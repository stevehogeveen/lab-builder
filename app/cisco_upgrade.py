from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
from typing import Any

from app.modules.cisco.service import CiscoModuleError
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


def execute_cisco_upgrade(cfg: dict[str, Any], media_scan: dict[str, Any]) -> dict[str, Any]:
    plan = build_cisco_upgrade_plan(cfg, media_scan)
    if not plan.get("ready"):
        raise CiscoModuleError("; ".join(list(plan.get("blockers") or []) or ["Cisco upgrade prechecks are not satisfied."]))

    host = str(plan.get("host") or "")
    username = str(plan.get("username") or "")
    password = str((cfg.get("cisco_switch") or {}).get("password") or "")
    image = Path(str(plan.get("media_path") or ""))
    remote_name = image.name

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
    install_cmd = f"install add file flash:{remote_name} activate commit prompt-level none"
    install = _run_command(
        [
            "sshpass",
            "-p",
            password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=15",
            f"{username}@{host}",
            install_cmd,
        ],
        timeout=7200,
    )
    result = {
        "status": "submitted",
        "host": host,
        "previous_version": str(plan.get("current_version") or ""),
        "target_version": str(plan.get("media_version") or ""),
        "media_path": str(image),
        "media_filename": remote_name,
        "transfer": transfer,
        "install": install,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    cfg.setdefault("cisco_switch", {})
    cfg["cisco_switch"].setdefault("upgrade", {})
    cfg["cisco_switch"]["upgrade"]["last_plan"] = plan
    cfg["cisco_switch"]["upgrade"]["last_result"] = result
    record_upgrade_inventory(
        cfg,
        "cisco_switch",
        current_version=str(plan.get("media_version") or ""),
        raw_version=str(plan.get("media_version") or ""),
        source="Post-upgrade Cisco submission",
        model=str(plan.get("model") or ""),
        platform=str(plan.get("platform") or ""),
    )
    return result


def _run_command(cmd: list[str], *, timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CiscoModuleError(f"Timed out running Cisco upgrade command: {' '.join(cmd[:4])} ...") from exc
    stdout = str(proc.stdout or "").strip()
    stderr = str(proc.stderr or "").strip()
    if proc.returncode != 0:
        raise CiscoModuleError((stderr or stdout or f"Cisco command failed ({proc.returncode}).").splitlines()[0])
    return {
        "command": " ".join(cmd),
        "stdout_excerpt": "\n".join(stdout.splitlines()[:20]),
        "stderr_excerpt": "\n".join(stderr.splitlines()[:20]),
        "returncode": proc.returncode,
    }
