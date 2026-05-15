from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable

import paramiko

from app.modules.cisco.service import CiscoModuleError
from app.cisco import append_cisco_log, mask_secrets
from app.upgrade_helper import build_upgrade_inventory, compare_versions, record_upgrade_inventory, select_upgrade_candidate


def build_cisco_upgrade_plan(cfg: dict[str, Any], media_scan: dict[str, Any]) -> dict[str, Any]:
    inventory = build_upgrade_inventory(cfg)
    item = dict(inventory.get("cisco_switch") or {})
    cisco_cfg = dict(cfg.get("cisco_switch") or {})
    current_version = str(item.get("current_version") or "").strip()
    current_source = str(item.get("source") or "").strip()
    host = str(cisco_cfg.get("ip") or (cfg.get("ip_plan") or {}).get("switch") or "").strip()
    username = str(cisco_cfg.get("username") or "").strip()
    password_present = bool(str(cisco_cfg.get("password") or ""))
    ssh_confirmed = bool((cisco_cfg.get("last_ssh_test") or {}).get("ok"))
    model = str(item.get("model") or "").strip()
    platform = str(item.get("platform") or "").strip()
    selected = select_upgrade_candidate(media_scan, "cisco_switch", {"model": model, "platform": platform})
    media_version = str(selected.get("version") or "").strip()
    media_filename = str(selected.get("filename") or "").strip()
    media_path = str(selected.get("path") or "").strip()

    blockers: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    if model:
        notes.append(f"Detected model: {model}")
    if platform:
        notes.append(f"Detected platform: {platform}")
    if current_version:
        notes.append(f"Current Cisco version: {current_version}")
    if media_version:
        notes.append(f"Matched image version: {media_version}")
    if media_path:
        notes.append(f"Matched image file: {media_path}")
    if current_source:
        notes.append(f"Current version source: {current_source}")

    if not host:
        blockers.append("Cisco target IP is not set.")
    if not username or not password_present:
        blockers.append("Saved Cisco credentials are incomplete.")
    if not ssh_confirmed:
        blockers.append("Confirmed Cisco SSH reachability is required before firmware upgrade.")
    if not current_version:
        blockers.append("Current Cisco version is unknown. Read Cisco version first.")
    if not media_version or not media_path:
        blockers.append("No approved Cisco image was found under the media directory.")
    if not shutil.which("sshpass"):
        blockers.append("sshpass is required for Cisco upgrade automation.")
    if not shutil.which("scp"):
        blockers.append("scp is required for Cisco image transfer automation.")

    comparison = compare_versions(current_version, media_version) if current_version and media_version else None
    if comparison is not None and comparison >= 0:
        warnings.append("Current Cisco version is already equal to or newer than the matched image.")

    ready = not blockers and comparison is not None and comparison < 0
    if ready:
        notes.append("Upgrade can proceed through SSH copy/install commands when the switch is available.")

    return {
        "ready": ready,
        "host": host,
        "username": username,
        "password_present": password_present,
        "ssh_confirmed": ssh_confirmed,
        "current_version": current_version,
        "current_source": current_source,
        "model": model,
        "platform": platform,
        "media_version": media_version,
        "media_filename": media_filename,
        "media_path": media_path,
        "comparison": comparison,
        "blockers": blockers,
        "warnings": warnings,
        "notes": notes,
        "planned_at": datetime.now(timezone.utc).isoformat(),
    }


def execute_cisco_upgrade(cfg: dict[str, Any], media_scan: dict[str, Any], *, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    plan = build_cisco_upgrade_plan(cfg, media_scan)
    if not plan.get("ready"):
        raise CiscoModuleError("; ".join(list(plan.get("blockers") or []) or ["Cisco upgrade prechecks are not satisfied."]))

    host = str(plan.get("host") or "")
    username = str(plan.get("username") or "")
    password = str((cfg.get("cisco_switch") or {}).get("password") or "")
    image = Path(str(plan.get("media_path") or ""))
    remote_name = image.name
    target_version = str(plan.get("media_version") or "")
    previous_version = str(plan.get("current_version") or "")
    local_size = _local_file_size(image)

    _emit_progress(progress, "precheck", "Checking whether the Cisco image is already on flash.", 8, host=host, media_filename=remote_name)
    remote_size = _remote_flash_file_size(host, username, password, remote_name)
    transfer: dict[str, Any]
    scp_prep: dict[str, Any] = {"status": "not_needed", "reason": "Existing flash image matched local image size."}
    scp_was_enabled = False
    if remote_size == local_size:
        _emit_progress(progress, "transfer", f"Image already exists on flash with matching size ({local_size} bytes); skipping upload.", 45, host=host, media_filename=remote_name)
        transfer = {
            "status": "skipped",
            "reason": "Existing flash image matched local image size.",
            "remote_size": remote_size,
            "local_size": local_size,
            "remote_filename": remote_name,
        }
    else:
        if remote_size:
            _emit_progress(progress, "transfer", f"Flash image is incomplete or different ({remote_size}/{local_size} bytes); uploading again.", 15, host=host, media_filename=remote_name)
        _emit_progress(progress, "precheck", "Checking Cisco SCP service state.", 10, host=host, media_filename=remote_name)
        scp_was_enabled = _is_scp_server_enabled(host, username, password)
        _emit_progress(progress, "precheck", "Temporarily enabling Cisco SCP service for image transfer.", 12, host=host, media_filename=remote_name)
        scp_prep = _run_interactive_ssh_commands(
            host,
            username,
            password,
            [
                "terminal length 0",
                "configure terminal",
                "ip scp server enable",
                "end",
                "write memory",
            ],
            timeout=45,
        )
        _emit_progress(progress, "transfer", "Copying Cisco image to flash.", 20, host=host, media_filename=remote_name)
        try:
            transfer = _run_command(
                [
                    "sshpass",
                    "-p",
                    password,
                    "scp",
                    "-O",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "ConnectTimeout=15",
                    str(image),
                    f"{username}@{host}:flash:{remote_name}",
                ],
                timeout=1800,
            )
        finally:
            if not scp_was_enabled:
                _emit_progress(progress, "transfer", "Disabling Cisco SCP service after image transfer.", 45, host=host, media_filename=remote_name)
                _run_interactive_ssh_commands(
                    host,
                    username,
                    password,
                    [
                        "terminal length 0",
                        "configure terminal",
                        "no ip scp server enable",
                        "end",
                        "write memory",
                    ],
                    timeout=45,
                )
    _emit_progress(progress, "install", "Image copied. Starting Cisco install activate commit.", 55, host=host, media_filename=remote_name)
    install_cmd = f"install add file flash:{remote_name} activate commit prompt-level none"
    install = _run_interactive_ssh_commands(
        host,
        username,
        password,
        ["terminal length 0", install_cmd],
        timeout=7200,
        command_timeout=7200,
        allow_disconnect=True,
        label="interactive ssh: install activate commit",
    )
    install_error = ""
    try:
        _validate_install_output(install.get("output_excerpt", ""))
    except CiscoModuleError as exc:
        install_error = str(exc).strip()
    prepared_for_reload = _install_prepared_for_reload(host, username, password, target_version)
    if install_error and not prepared_for_reload:
        raise CiscoModuleError(install_error)
    if prepared_for_reload:
        _emit_progress(
            progress,
            "reload",
            "Cisco install prepared packages.conf for the target version. Reloading switch to boot the new image.",
            75,
            host=host,
            media_filename=remote_name,
        )
        _reload_switch(host, username, password)
    _emit_progress(progress, "verify", "Install command submitted. Waiting for switch to report the target version.", 80, host=host, media_filename=remote_name)
    verification = _wait_for_cisco_version(
        host,
        username,
        password,
        expected_version=target_version,
        previous_version=previous_version,
        progress=progress,
        media_filename=remote_name,
    )
    install_summary = _verify_install_committed(host, username, password, target_version)
    _emit_progress(progress, "complete", f"Cisco upgrade verified at {verification.get('version') or target_version}.", 100, host=host, media_filename=remote_name)
    result = {
        "status": "completed",
        "host": host,
        "previous_version": previous_version,
        "target_version": target_version,
        "media_path": str(image),
        "media_filename": remote_name,
        "scp_was_enabled": scp_was_enabled,
        "scp_prep": scp_prep,
        "transfer": transfer,
        "install": install,
        "install_summary": install_summary,
        "verification": verification,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    cfg.setdefault("cisco_switch", {})
    cfg["cisco_switch"].setdefault("upgrade", {})
    cfg["cisco_switch"]["upgrade"]["last_plan"] = plan
    cfg["cisco_switch"]["upgrade"]["last_result"] = result
    record_upgrade_inventory(
        cfg,
        "cisco_switch",
        current_version=str(verification.get("version") or target_version),
        raw_version=str(verification.get("raw_version") or verification.get("version") or target_version),
        source="Post-upgrade Cisco verification",
        model=str(plan.get("model") or ""),
        platform=str(plan.get("platform") or ""),
    )
    return result


def _emit_progress(progress: Callable[[dict[str, Any]], None] | None, phase: str, message: str, progress_percent: int, **extra: Any) -> None:
    if not progress:
        return
    payload = {
        "phase": phase,
        "message": message,
        "progress_percent": progress_percent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    progress(payload)


def _is_scp_server_enabled(host: str, username: str, password: str) -> bool:
    result = _run_interactive_ssh_commands(
        host,
        username,
        password,
        ["terminal length 0", "show running-config | include ^ip scp server enable"],
        timeout=30,
        label="interactive ssh: check scp server",
    )
    for line in str(result.get("output_excerpt") or "").splitlines():
        if line.strip() == "ip scp server enable":
            return True
    return False


def _remote_flash_file_size(host: str, username: str, password: str, filename: str) -> int | None:
    result = _run_interactive_ssh_commands(
        host,
        username,
        password,
        ["terminal length 0", f"dir flash:{filename}"],
        timeout=30,
        label="interactive ssh: check flash image",
    )
    return _parse_flash_file_size(str(result.get("output_excerpt") or ""), filename)


def _install_prepared_for_reload(host: str, username: str, password: str, target_version: str) -> bool:
    if not target_version:
        return False
    try:
        version_result = _run_interactive_ssh_commands(
            host,
            username,
            password,
            ["terminal length 0", "show version"],
            timeout=30,
            label="interactive ssh: inspect running Cisco version",
        )
        from app.modules.cisco.service import parse_cisco_show_version

        running = str(parse_cisco_show_version(str(version_result.get("output_excerpt") or "")).get("version") or "").strip()
        if running == target_version:
            return False
        packages_result = _run_interactive_ssh_commands(
            host,
            username,
            password,
            ["terminal length 0", f"more flash:packages.conf | include {target_version}"],
            timeout=30,
            label="interactive ssh: inspect packages.conf",
        )
        return target_version in str(packages_result.get("output_excerpt") or "")
    except Exception:
        return False


def _verify_install_committed(host: str, username: str, password: str, target_version: str) -> dict[str, Any]:
    result = _run_interactive_ssh_commands(
        host,
        username,
        password,
        ["terminal length 0", "show install summary"],
        timeout=30,
        label="interactive ssh: verify install summary",
    )
    output = str(result.get("output_excerpt") or "")
    if target_version and not re.search(rf"(?m)\bC\s+{re.escape(target_version)}(?:\.|\b)", output):
        raise CiscoModuleError(f"Cisco reports version {target_version}, but show install summary does not show it as committed.")
    return {"status": "committed", "output_excerpt": output}


def _reload_switch(host: str, username: str, password: str) -> dict[str, Any]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    output_chunks: list[str] = []
    try:
        client.connect(
            host,
            username=username,
            password=password,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        shell = client.invoke_shell()
        shell.settimeout(1.0)
        time.sleep(0.5)
        if shell.recv_ready():
            output_chunks.append(shell.recv(65535).decode("utf-8", errors="replace"))
        for command in ("terminal length 0", "write memory"):
            shell.send(command + "\n")
            output_chunks.append(_read_shell_until_prompt(shell, timeout=60))
        shell.send("reload\n")
        reload_output = _read_shell_until_patterns(shell, patterns=(r"\[confirm\]", r"\byes/no\b"), timeout=30)
        output_chunks.append(reload_output)
        if re.search(r"(?i)\byes/no\b", reload_output):
            shell.send("yes\n")
            confirm_output = _read_shell_until_patterns(shell, patterns=(r"\[confirm\]",), timeout=15)
            output_chunks.append(confirm_output)
        if re.search(r"(?i)\[confirm\]|confirm", "\n".join(output_chunks)):
            shell.send("\n")
            output_chunks.append(_read_shell_until_disconnect_or_timeout(shell, timeout=20))
        output = "\n".join(output_chunks)
        append_cisco_log("upgrade.reload.issued", host=host, output=mask_secrets(output, [password]))
        return {"command": "interactive ssh: reload", "output_excerpt": mask_secrets("\n".join(output.splitlines()[-80:]), [password])}
    except Exception as exc:
        raise CiscoModuleError(f"Cisco reload failed: {str(exc).splitlines()[0]}") from exc
    finally:
        client.close()


def execute_cisco_factory_reset(host: str, username: str, password: str) -> dict[str, Any]:
    if not host or not username or not password:
        raise CiscoModuleError("Cisco host, username, and password are required for factory reset.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    output_chunks: list[str] = []
    try:
        client.connect(
            host,
            username=username,
            password=password,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        shell = client.invoke_shell()
        shell.settimeout(1.0)
        time.sleep(0.5)
        if shell.recv_ready():
            output_chunks.append(shell.recv(65535).decode("utf-8", errors="replace"))
        shell.send("terminal length 0\n")
        output_chunks.append(_read_shell_until_prompt(shell, timeout=20))
        shell.send("write erase\n")
        erase_output = _read_shell_until_patterns(shell, patterns=(r"\[confirm\]", r"confirm"), timeout=30)
        output_chunks.append(erase_output)
        if re.search(r"(?i)\[confirm\]|confirm", erase_output):
            shell.send("\n")
            output_chunks.append(_read_shell_until_prompt(shell, timeout=60))
        shell.send("delete /force flash:vlan.dat\n")
        output_chunks.append(_read_shell_until_prompt(shell, timeout=30))
        shell.send("reload\n")
        reload_output = _read_shell_until_patterns(shell, patterns=(r"\[confirm\]", r"\byes/no\b"), timeout=30)
        output_chunks.append(reload_output)
        if re.search(r"(?i)\byes/no\b", reload_output):
            shell.send("yes\n")
            output_chunks.append(_read_shell_until_patterns(shell, patterns=(r"\[confirm\]",), timeout=15))
        if re.search(r"(?i)\[confirm\]|confirm", "\n".join(output_chunks)):
            shell.send("\n")
            output_chunks.append(_read_shell_until_disconnect_or_timeout(shell, timeout=20))
        output = "\n".join(output_chunks)
        append_cisco_log("factory_reset.issued", host=host, output=mask_secrets(output, [password]))
        return {
            "status": "reload_issued",
            "host": host,
            "commands": ["write erase", "delete /force flash:vlan.dat", "reload"],
            "output_excerpt": mask_secrets("\n".join(output.splitlines()[-80:]), [password]),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        raise CiscoModuleError(f"Cisco factory reset failed: {str(exc).splitlines()[0]}") from exc
    finally:
        client.close()


def _local_file_size(path: Path) -> int:
    return path.stat().st_size


def _parse_flash_file_size(output: str, filename: str) -> int | None:
    escaped = re.escape(str(filename or "").strip())
    if not escaped:
        return None
    pattern = re.compile(rf"(?m)^\s*\d+\s+-\S+\s+(\d+)\s+.*\s{escaped}\s*$")
    match = pattern.search(str(output or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _run_interactive_ssh_commands(
    host: str,
    username: str,
    password: str,
    commands: list[str],
    *,
    timeout: int,
    command_timeout: int | None = None,
    allow_disconnect: bool = False,
    label: str = "interactive ssh: commands",
) -> dict[str, Any]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    output_chunks: list[str] = []
    try:
        client.connect(
            host,
            username=username,
            password=password,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        shell = client.invoke_shell()
        shell.settimeout(1.0)
        time.sleep(0.5)
        if shell.recv_ready():
            output_chunks.append(shell.recv(65535).decode("utf-8", errors="replace"))
        for command in commands:
            shell.send(command + "\n")
            command_output = _read_shell_until_prompt(shell, timeout=command_timeout or max(5, min(timeout, 20)), allow_disconnect=allow_disconnect)
            output_chunks.append(command_output)
            if _cisco_command_failed(command_output):
                raise CiscoModuleError(_first_useful_error_line(command_output) or f"Cisco SSH command failed while running {command}.")
        output = "\n".join(output_chunks)
        append_cisco_log("upgrade.ssh_commands.complete", host=host, command=label, output=mask_secrets(output, [password]))
        return {"command": label, "output_excerpt": mask_secrets("\n".join(output.splitlines()[-80:]), [password])}
    except CiscoModuleError:
        raise
    except Exception as exc:
        raise CiscoModuleError(f"Cisco SCP prep failed: {str(exc).splitlines()[0]}") from exc
    finally:
        client.close()


def _read_shell_until_prompt(shell: Any, *, timeout: int, allow_disconnect: bool = False) -> str:
    deadline = time.time() + timeout
    output = ""
    last_data_at = time.time()
    while time.time() < deadline:
        try:
            if shell.recv_ready():
                chunk = shell.recv(65535).decode("utf-8", errors="replace")
                output += chunk
                last_data_at = time.time()
                if re.search(r"(?m)[\r\n][A-Za-z0-9_.-]+(?:\([^)]*\))?[#>] ?$", output):
                    break
            elif allow_disconnect and getattr(shell, "closed", False):
                break
        except Exception:
            if allow_disconnect:
                output += "\n[SSH session closed during command]\n"
                break
            raise
        time.sleep(0.5 if allow_disconnect else 0.2)
    return output


def _read_shell_until_patterns(shell: Any, *, patterns: tuple[str, ...], timeout: int) -> str:
    deadline = time.time() + timeout
    output = ""
    while time.time() < deadline:
        try:
            if shell.recv_ready():
                output += shell.recv(65535).decode("utf-8", errors="replace")
                if any(re.search(pattern, output, flags=re.IGNORECASE) for pattern in patterns):
                    break
                if re.search(r"(?m)[\r\n][A-Za-z0-9_.-]+(?:\([^)]*\))?[#>] ?$", output):
                    break
        except Exception:
            break
        time.sleep(0.2)
    return output


def _read_shell_until_disconnect_or_timeout(shell: Any, *, timeout: int) -> str:
    deadline = time.time() + timeout
    output = ""
    while time.time() < deadline:
        try:
            if shell.recv_ready():
                output += shell.recv(65535).decode("utf-8", errors="replace")
            elif getattr(shell, "closed", False):
                break
        except Exception:
            output += "\n[SSH session closed during reload]\n"
            break
        time.sleep(0.5)
    return output


def _validate_install_output(output: str) -> None:
    text = str(output or "")
    lowered = text.lower()
    error_lines = []
    for line in text.splitlines():
        cleaned = line.strip()
        line_lower = cleaned.lower()
        if "updatepcr8d unavailable" in line_lower:
            continue
        if re.search(r"(?i)(aborted|failed|not enough space|no such file|invalid|error)", cleaned):
            error_lines.append(cleaned)
    if error_lines:
        raise CiscoModuleError(_first_useful_error_line(text) or "Cisco install command reported an error.")
    evidence = any(term in lowered for term in ("install_add", "finished", "success", "reload", "software install", "commit: passed"))
    if not evidence:
        raise CiscoModuleError("Cisco install command returned without clear install evidence. The image was copied, but the switch did not confirm activation.")


def _wait_for_cisco_version(
    host: str,
    username: str,
    password: str,
    *,
    expected_version: str,
    previous_version: str,
    progress: Callable[[dict[str, Any]], None] | None,
    media_filename: str,
    timeout: int = 3600,
    poll_interval: int = 30,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = ""
    last_version = ""
    from app.modules.cisco.service import parse_cisco_show_version

    while time.time() < deadline:
        try:
            result = _run_interactive_ssh_commands(
                host,
                username,
                password,
                ["terminal length 0", "show version"],
                timeout=30,
                label="interactive ssh: verify show version",
            )
            parsed = parse_cisco_show_version(str(result.get("output_excerpt") or ""))
            last_version = str(parsed.get("version") or "").strip()
            if last_version:
                _emit_progress(progress, "verify", f"Cisco reports version {last_version}; waiting for {expected_version}.", 90, host=host, media_filename=media_filename)
            if expected_version and last_version == expected_version:
                return {"status": "verified", "version": last_version, "raw_version": last_version, "previous_version": previous_version}
        except Exception as exc:
            last_error = str(exc).splitlines()[0]
            _emit_progress(progress, "verify", f"Waiting for switch SSH to return after install: {last_error}.", 85, host=host, media_filename=media_filename)
        time.sleep(poll_interval)
    raise CiscoModuleError(
        f"Timed out waiting for Cisco version {expected_version}. "
        f"Last seen version: {last_version or 'unknown'}. Last error: {last_error or 'none'}."
    )


def _cisco_command_failed(output: str) -> bool:
    text = str(output or "")
    return bool(re.search(r"(?im)(% ?invalid input|% ?incomplete command|% ?ambiguous command|authorization failed|authentication failed|error:)", text))


def _first_useful_error_line(output: str) -> str:
    for line in str(output or "").splitlines():
        cleaned = line.strip()
        if re.search(r"(?i)(invalid input|incomplete command|ambiguous command|authorization failed|authentication failed|error:)", cleaned):
            return cleaned
    return ""


def _run_command(cmd: list[str], *, timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CiscoModuleError(f"Timed out running Cisco upgrade command: {' '.join(cmd[:4])} ...") from exc
    stdout = str(proc.stdout or "").strip()
    stderr = str(proc.stderr or "").strip()
    if proc.returncode != 0:
        combined_lines = [line.strip() for line in (stderr + "\n" + stdout).splitlines() if line.strip()]
        useful_lines = [line for line in combined_lines if "Permanently added" not in line and "Warning:" not in line]
        message = "\n".join((useful_lines or combined_lines or [f"Cisco command failed ({proc.returncode})."])[:8])
        raise CiscoModuleError(message)
    command = " ".join(cmd)
    secrets = [cmd[index + 1] for index, token in enumerate(cmd[:-1]) if token == "-p"]
    return {
        "command": mask_secrets(command, secrets),
        "stdout_excerpt": mask_secrets("\n".join(stdout.splitlines()[:20]), secrets),
        "stderr_excerpt": mask_secrets("\n".join(stderr.splitlines()[:20]), secrets),
        "returncode": proc.returncode,
    }
