from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import subprocess
from typing import Any

from app.cisco import (
    CiscoSerialClient,
    CiscoSerialDiscovery,
    CiscoSSHClient,
    apply_serial_permission_fix,
    append_cisco_log,
    discovery_candidates_payload,
    mask_secrets,
    normalize_cisco_switch_config,
    parse_cisco_discovery_outputs,
    parse_show_interfaces_status,
    parse_show_ip_interface_brief,
    port_map_rows,
    render_cisco_baseline_config,
    render_cisco_diff_preview,
    render_cisco_full_config,
    render_cisco_port_config,
    render_management_config,
    serial_runtime_diagnostics,
    validate_cisco_config,
)


class CiscoModuleError(RuntimeError):
    pass


@dataclass
class CiscoDiscoveryResult:
    ok: bool
    target: str
    username: str
    version: str = ""
    hostname: str = ""
    model: str = ""
    platform: str = ""
    raw_excerpt: str = ""
    error: str = ""
    warnings: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target": self.target,
            "username": self.username,
            "version": self.version,
            "hostname": self.hostname,
            "model": self.model,
            "platform": self.platform,
            "raw_excerpt": self.raw_excerpt,
            "error": self.error,
            "warnings": list(self.warnings or []),
        }


def parse_cisco_show_version(output: str) -> dict[str, str]:
    text = str(output or "")
    version_patterns = [
        r"Cisco IOS XE Software,\s+Version\s+([^\s,]+)",
        r"Cisco IOS Software.*?,\s+Version\s+([^\s,]+)",
        r"\bVersion\s+([0-9][^\s,]+)",
        r"system:\s+version\s+([^\s,]+)",
    ]
    version = ""
    for pattern in version_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            version = match.group(1).strip()
            break

    hostname = ""
    hostname_match = re.search(r"^(\S+)\s+uptime is", text, flags=re.MULTILINE)
    if hostname_match:
        hostname = hostname_match.group(1).strip()

    model = ""
    for pattern in (
        r"cisco\s+(\S+)\s+\([^)]+\)\s+processor",
        r"[Mm]odel number\s*:\s*(\S+)",
        r"[Cc]isco\s+(\S+)\s+\(.+?\)\s+with",
    ):
        match = re.search(pattern, text)
        if match:
            model = match.group(1).strip()
            break

    platform = ""
    for pattern in (
        r"Cisco IOS Software\s+\[[^\]]+\],\s+\S+\s+Software\s+\(([^)]+)\)",
        r"Cisco IOS XE Software,\s+\S+\s+Software\s+\(([^)]+)\)",
        r"[Pp]latform:\s*([^\n,]+)",
    ):
        match = re.search(pattern, text)
        if match:
            platform = match.group(1).strip()
            break

    return {
        "version": version,
        "hostname": hostname,
        "model": model,
        "platform": platform,
        "raw_excerpt": "\n".join(text.splitlines()[:18]).strip(),
    }


class CiscoModuleService:
    def _target(self, context: dict[str, Any]) -> str:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return str(cisco_cfg.get("ip") or cfg.get("ip_plan", {}).get("switch") or "").strip()

    def _username(self, context: dict[str, Any]) -> str:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return str(cisco_cfg.get("username") or "admin").strip()

    def _password(self, context: dict[str, Any]) -> str:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return str(cisco_cfg.get("password") or "")

    def _management_ip(self, context: dict[str, Any]) -> str:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return str(cisco_cfg.get("management_ip") or cisco_cfg.get("ip") or cfg.get("ip_plan", {}).get("switch") or "").strip()

    def _bootstrap_network_path(self, cisco_cfg: dict[str, Any]) -> tuple[str, str]:
        configured_port = str(cisco_cfg.get("bootstrap_network_port") or "").strip()
        configured_mode = str(cisco_cfg.get("bootstrap_network_mode") or "").strip().lower()
        if configured_port:
            return configured_port, configured_mode or "trunk"
        raw_console_check = str(cisco_cfg.get("last_raw_console_bootstrap_check") or "")
        if raw_console_check:
            interface_status = parse_show_interfaces_status(_extract_command_output(raw_console_check, "show interfaces status", "show ip ssh"))
            for port, data in interface_status.items():
                if not str(port).lower().startswith(("gigabitethernet", "tengigabitethernet", "twentyfivegige", "fortygigabitethernet")):
                    continue
                if str(data.get("status") or "").lower() != "connected":
                    continue
                vlan = str(data.get("vlan") or "").strip().lower()
                if vlan == "trunk":
                    return str(port), "trunk"
                return str(port), "access"
        normalized = normalize_cisco_switch_config(cisco_cfg)
        profiles = dict(normalized.get("port_profiles") or {})
        for port, settings in dict(normalized.get("ports") or {}).items():
            profile_name = str(dict(settings or {}).get("profile") or "")
            profile = dict(profiles.get(profile_name) or {})
            mode = str(profile.get("mode") or dict(settings or {}).get("mode") or "").strip().lower()
            if "uplink" in profile_name or mode == "trunk":
                return str(port), mode or "trunk"
        return "", configured_mode or "trunk"

    def _status_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        last_bootstrap = dict(cisco_cfg.get("last_bootstrap") or {})
        last_serial_output = str(cisco_cfg.get("last_serial_output") or "").strip()
        stale_initial_dialog_bootstrap = bool(
            last_bootstrap.get("ok")
            and re.search(r"(?is)Would you like to enter the initial configuration dialog|Please answer 'yes' or 'no'", last_serial_output)
        )
        if stale_initial_dialog_bootstrap:
            last_bootstrap["ok"] = False
            last_bootstrap["error"] = "Previous console bootstrap ran while the switch was still in the Cisco initial setup dialog. Run Bootstrap management IP over console again."
        connection_method = str(cisco_cfg.get("connection_method") or "auto").strip()
        if stale_initial_dialog_bootstrap and not bool(dict(cisco_cfg.get("last_ssh_test") or {}).get("ok")):
            connection_method = "console"
        effective_bootstrap_port, effective_bootstrap_mode = self._bootstrap_network_path(cisco_cfg)
        return {
            "target": self._target(context),
            "management_ip": self._management_ip(context),
            "username": self._username(context),
            "hostname": str(cisco_cfg.get("hostname") or "").strip(),
            "connection_method": connection_method,
            "console_port": str(cisco_cfg.get("console_port") or "").strip(),
            "console_baud": int(cisco_cfg.get("console_baud") or 9600),
            "management_vlan": int(cisco_cfg.get("management_vlan") or 10),
            "subnet_mask": str(cisco_cfg.get("subnet_mask") or "").strip(),
            "gateway": str(cisco_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip(),
            "bootstrap_network_port": str(cisco_cfg.get("bootstrap_network_port") or "").strip(),
            "bootstrap_network_mode": str(cisco_cfg.get("bootstrap_network_mode") or "trunk").strip(),
            "effective_bootstrap_network_port": effective_bootstrap_port,
            "effective_bootstrap_network_mode": effective_bootstrap_mode,
            "last_discovered_version": str(cisco_cfg.get("last_discovered_version") or "").strip(),
            "last_discovered_at": str(cisco_cfg.get("last_discovered_at") or "").strip(),
            "last_discovery_error": str(cisco_cfg.get("last_discovery_error") or "").strip(),
            "last_show_version": str(cisco_cfg.get("last_show_version") or "").strip(),
            "last_console_candidates": list(cisco_cfg.get("last_console_candidates") or []),
            "last_console_probe_results": list(cisco_cfg.get("last_console_probe_results") or []),
            "last_console_suggestions": list(cisco_cfg.get("last_console_suggestions") or []),
            "last_serial_output": last_serial_output,
            "last_bootstrap": last_bootstrap,
            "last_ssh_test": dict(cisco_cfg.get("last_ssh_test") or {}),
            "last_console_diagnostics": dict(cisco_cfg.get("last_console_diagnostics") or {}),
            "port_map": port_map_rows(cisco_cfg),
            "port_profiles": dict(normalize_cisco_switch_config(cisco_cfg).get("port_profiles") or {}),
            "vlans": list(normalize_cisco_switch_config(cisco_cfg).get("vlans") or []),
            "apply_mode": str(cisco_cfg.get("apply_mode") or "initial_install"),
            "last_port_discovery": dict(cisco_cfg.get("last_port_discovery") or {}),
            "last_raw_port_discovery": str(cisco_cfg.get("last_raw_port_discovery") or ""),
            "discovered_interfaces": list(
                {
                    "name": name,
                    "short_name": name,
                    "status": str(data.get("status") or data.get("admin_status") or ""),
                    "vlan": str(data.get("vlan") or ""),
                    "ip_address": str(data.get("ip_address") or ""),
                    "description": str(data.get("description") or data.get("name") or ""),
                    "shutdown": bool(data.get("shutdown")),
                }
                for name, data in sorted(dict((cisco_cfg.get("last_port_discovery") or {}).get("interfaces") or {}).items())
            ),
            "discovered_interface_count": len(dict((cisco_cfg.get("last_port_discovery") or {}).get("interfaces") or {})),
            "desired_port_count": len(dict(cisco_cfg.get("ports") or {})),
            "last_config_preview": str(cisco_cfg.get("last_config_preview") or ""),
            "last_cisco_action": dict(cisco_cfg.get("last_cisco_action") or {}),
            "last_running_config_backup": str(cisco_cfg.get("last_running_config_backup") or ""),
            "last_host_fix": dict(cisco_cfg.get("last_host_fix") or {}),
            "last_console_bootstrap_check": dict(cisco_cfg.get("last_console_bootstrap_check") or {}),
            "config_approval": dict(cisco_cfg.get("config_approval") or {}),
        }

    def _console_probe_results(self, candidates: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "port": item.port,
                "baud": item.baud,
                "description": item.description,
                "hardware_id": item.hardware_id,
                "manufacturer": item.manufacturer,
                "prompt_type": item.prompt_type,
                "score": item.score,
                "error": item.error,
            }
            for item in candidates
        ]

    def _console_failure_summary(
        self,
        diagnostics: dict[str, Any],
        candidates: list[Any],
        *,
        exception: str = "",
    ) -> tuple[str, list[str]]:
        suggestions: list[str] = []
        probe_errors = [str(item.error or "").strip() for item in candidates if str(item.error or "").strip()]
        ordered_ports = [str(item).strip() for item in list(diagnostics.get("ordered_ports") or []) if str(item).strip()]
        device_access = list(diagnostics.get("device_access") or [])
        permission_denied = any("permission denied" in error.lower() for error in probe_errors)
        permission_denied = permission_denied or any(
            item.get("path") and (item.get("readable") is False or item.get("writable") is False)
            for item in device_access
        )

        if exception and "pyserial" in exception.lower():
            summary = "Cisco console access cannot start because pyserial is not installed in the app environment."
            suggestions.append("Install the Python dependency set, then restart Lab Builder so serial support loads.")
        elif not diagnostics.get("serial_imported"):
            summary = "Cisco console access cannot start because pyserial is not installed in the app environment."
            suggestions.append("Install pyserial from the project requirements and restart Lab Builder.")
        elif not ordered_ports:
            summary = "No USB serial console adapter was detected by the Lab Builder server."
            suggestions.extend(
                [
                    "Plug in the Cisco console USB/serial adapter and confirm the host sees /dev/ttyUSB* or /dev/ttyACM*.",
                    "If the adapter was just connected, wait a few seconds and test console access again.",
                ]
            )
        elif permission_denied:
            diagnostics["permission_denied"] = True
            user = str(diagnostics.get("user") or "the Lab Builder service user")
            summary = f"The server can see the serial adapter, but {user} cannot open it."
            suggestions.extend(
                [
                    "Use Fix serial access, then restart the app or service session if group membership changed.",
                    "The Linux user running Lab Builder needs read/write access to the serial device, usually through the dialout group.",
                ]
            )
        elif probe_errors and len(probe_errors) >= max(1, len(candidates)):
            summary = "Every detected serial adapter probe failed before Lab Builder could read a Cisco prompt."
            suggestions.append("Review the probe errors below; the first failure usually identifies the bad device path, lock, or driver issue.")
        elif candidates:
            saw_output = any(str(getattr(item, "raw_output", "") or "").strip() for item in candidates)
            if saw_output:
                summary = "A serial adapter responded, but the output did not look like a Cisco console prompt."
            else:
                summary = "The serial adapter opened successfully, but no Cisco console output was received."
            suggestions.extend(
                [
                    "Press Enter on the console or power-cycle the switch, then test again.",
                    "Check the rollover/console cable and try both 9600 and 115200 baud.",
                    "Confirm the selected USB adapter is connected to the switch console port, not a data port.",
                ]
            )
        else:
            summary = str(exception or "No Cisco console prompt was detected.").strip()
            suggestions.append("Check the console cable, switch power state, and serial adapter mapping, then test again.")

        diagnostics["error_summary"] = summary
        diagnostics["suggestions"] = suggestions
        return summary, suggestions

    def _run_show_version(self, context: dict[str, Any]) -> CiscoDiscoveryResult:
        target = self._target(context)
        username = self._username(context)
        password = self._password(context)
        warnings: list[str] = []
        if not target:
            raise CiscoModuleError("Cisco switch IP is not set.")
        if not username:
            raise CiscoModuleError("Cisco switch username is not set.")
        if not password:
            raise CiscoModuleError("Cisco switch password is not set.")
        if not shutil.which("sshpass"):
            raise CiscoModuleError("sshpass is required for Cisco SSH discovery.")

        cmd = [
            "sshpass",
            "-p",
            password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            f"{username}@{target}",
            "show version",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=25)
        except subprocess.TimeoutExpired as exc:
            raise CiscoModuleError(f"Cisco SSH discovery timed out for {target}.") from exc
        stdout = str(proc.stdout or "")
        stderr = str(proc.stderr or "")
        if proc.returncode != 0:
            message = (stderr or stdout or f"Cisco SSH command failed ({proc.returncode}).").splitlines()[0]
            raise CiscoModuleError(message)
        parsed = parse_cisco_show_version(stdout)
        if not parsed.get("version"):
            warnings.append("Cisco version could not be parsed from show version output.")
        return CiscoDiscoveryResult(
            ok=True,
            target=target,
            username=username,
            version=str(parsed.get("version") or ""),
            hostname=str(parsed.get("hostname") or ""),
            model=str(parsed.get("model") or ""),
            platform=str(parsed.get("platform") or ""),
            raw_excerpt=str(parsed.get("raw_excerpt") or ""),
            warnings=warnings,
        )

    def discover(self, context: dict[str, Any]) -> dict[str, Any]:
        status = self._status_payload(context)
        try:
            result = self._run_show_version(context)
            payload = result.as_dict()
            payload["module"] = "cisco"
            payload["action"] = "discover"
            payload["status"] = status
            return payload
        except CiscoModuleError as exc:
            return {
                "module": "cisco",
                "action": "discover",
                "ok": False,
                "target": status["target"],
                "username": status["username"],
                "error": str(exc),
                "warnings": [],
                "status": status,
            }

    def discover_console(self, context: dict[str, Any]) -> dict[str, Any]:
        status = self._status_payload(context)
        diagnostics = serial_runtime_diagnostics()
        try:
            candidates = CiscoSerialDiscovery().scan()
        except Exception as exc:
            append_cisco_log("serial.discovery.exception", error=str(exc))
            summary, suggestions = self._console_failure_summary(diagnostics, [], exception=str(exc))
            return {
                "module": "cisco",
                "action": "discover_console",
                "ok": False,
                "error": summary,
                "warnings": [],
                "suggestions": suggestions,
                "status": status,
                "candidates": [],
                "probe_results": [],
                "diagnostics": diagnostics,
            }
        matches = [item for item in candidates if item.score >= 50]
        diagnostics["probe_results"] = self._console_probe_results(candidates)
        summary = ""
        suggestions: list[str] = []
        if not matches:
            summary, suggestions = self._console_failure_summary(diagnostics, candidates)
        return {
            "module": "cisco",
            "action": "discover_console",
            "ok": bool(matches),
            "error": "" if matches else summary,
            "warnings": ["Multiple Cisco console candidates were detected. Select the intended console port before configuring management IP."] if len(matches) > 1 else [],
            "suggestions": suggestions,
            "status": status,
            "candidates": discovery_candidates_payload(matches, include_raw=True),
            "probe_results": diagnostics["probe_results"],
            "diagnostics": diagnostics,
        }

    def fix_serial_permissions(self, context: dict[str, Any], sudo_password: str) -> dict[str, Any]:
        status = self._status_payload(context)
        result = apply_serial_permission_fix(sudo_password)
        result.update({"module": "cisco", "action": "fix_serial_permissions", "status": status})
        return result

    def bootstrap_management(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        port = str(cisco_cfg.get("console_port") or "").strip()
        baud = int(cisco_cfg.get("console_baud") or 9600)
        if not port:
            return {"module": "cisco", "action": "bootstrap_management", "ok": False, "error": "Cisco console port is not selected.", "status": self._status_payload(context)}
        if not self._management_ip(context):
            return {"module": "cisco", "action": "bootstrap_management", "ok": False, "error": "Cisco management IP is not set.", "status": self._status_payload(context)}
        if not str(cisco_cfg.get("subnet_mask") or "").strip():
            return {"module": "cisco", "action": "bootstrap_management", "ok": False, "error": "Cisco subnet mask is not set.", "status": self._status_payload(context)}
        if not str(cisco_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip():
            return {"module": "cisco", "action": "bootstrap_management", "ok": False, "error": "Cisco management gateway is not set.", "status": self._status_payload(context)}
        if not self._username(context):
            return {"module": "cisco", "action": "bootstrap_management", "ok": False, "error": "Cisco username is not set.", "status": self._status_payload(context)}
        if not self._password(context):
            return {"module": "cisco", "action": "bootstrap_management", "ok": False, "error": "Cisco password is not set.", "status": self._status_payload(context)}
        management_config = {
            "hostname": str(cisco_cfg.get("hostname") or "sw01").strip(),
            "management_vlan": int(cisco_cfg.get("management_vlan") or 10),
            "management_ip": self._management_ip(context),
            "subnet_mask": str(cisco_cfg.get("subnet_mask") or "").strip(),
            "gateway": str(cisco_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip(),
            "domain_name": str(cisco_cfg.get("domain_name") or "lab.local").strip(),
            "username": self._username(context),
            "password": self._password(context),
            "enable_password": str(cisco_cfg.get("enable_password") or ""),
        }
        bootstrap_port, bootstrap_mode = self._bootstrap_network_path(cisco_cfg)
        management_config["bootstrap_network_port"] = bootstrap_port
        management_config["bootstrap_network_mode"] = bootstrap_mode
        try:
            with CiscoSerialClient(port, baud) as client:
                result = client.apply_management_config(management_config)
            payload = result.as_dict(include_raw=True)
            payload.update({"module": "cisco", "action": "bootstrap_management", "status": self._status_payload(context)})
            return payload
        except Exception as exc:
            return {"module": "cisco", "action": "bootstrap_management", "ok": False, "error": str(exc), "status": self._status_payload(context)}

    def verify_console_bootstrap(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        port = str(cisco_cfg.get("console_port") or "").strip()
        baud = int(cisco_cfg.get("console_baud") or 9600)
        management_vlan = int(cisco_cfg.get("management_vlan") or 1)
        if not port:
            return {"module": "cisco", "action": "verify_console_bootstrap", "ok": False, "error": "Cisco console port is not selected.", "status": self._status_payload(context)}
        commands = [
            "terminal length 0",
            "show ip interface brief",
            "show vlan brief",
            "show interfaces status",
            "show ip ssh",
        ]
        try:
            with CiscoSerialClient(port, baud) as client:
                output = client.read_prompt()
                for command in commands:
                    output += f"\n--- {command} ---\n"
                    output += client.run_command(command, wait_seconds=3.0)
        except Exception as exc:
            return {"module": "cisco", "action": "verify_console_bootstrap", "ok": False, "error": str(exc), "status": self._status_payload(context)}

        ip_interfaces = parse_show_ip_interface_brief(_extract_command_output(output, "show ip interface brief", "show vlan brief"))
        interface_status = parse_show_interfaces_status(_extract_command_output(output, "show interfaces status", "show ip ssh"))
        svi_name = f"Vlan{management_vlan}"
        svi = dict(ip_interfaces.get(svi_name) or {})
        vlan_exists = bool(re.search(rf"(?im)^\s*{management_vlan}\s+\S+\s+active\b", output))
        ssh_enabled = "SSH Enabled" in output
        connected_ports = [
            name
            for name, data in interface_status.items()
            if str(data.get("status") or "").lower() == "connected"
            and str(data.get("vlan") or "").lower() in {str(management_vlan), "trunk"}
        ]
        warnings: list[str] = []
        if not vlan_exists:
            warnings.append(f"VLAN {management_vlan} does not exist in the VLAN database.")
        svi_admin_status = str(svi.get("admin_status") or svi.get("status") or "").lower()
        svi_protocol = str(svi.get("protocol") or "").lower()
        if svi_admin_status != "up" or svi_protocol != "up":
            warnings.append(f"{svi_name} is not up/up. Connect or configure at least one active port carrying VLAN {management_vlan}.")
        if not connected_ports:
            warnings.append(f"No connected switchport is currently carrying VLAN {management_vlan}.")
        if not ssh_enabled:
            warnings.append("SSH is not enabled on the switch.")
        ok = bool(vlan_exists and ssh_enabled and svi_admin_status == "up" and svi_protocol == "up")
        return {
            "module": "cisco",
            "action": "verify_console_bootstrap",
            "ok": ok,
            "error": "" if ok else "Console bootstrap is configured, but network reachability is not ready.",
            "management_vlan": management_vlan,
            "management_svi": svi,
            "vlan_exists": vlan_exists,
            "ssh_enabled": ssh_enabled,
            "connected_management_ports": connected_ports,
            "warnings": warnings,
            "raw_output": mask_secrets(output, [self._password(context), str(cisco_cfg.get("enable_password") or "")]),
            "status": self._status_payload(context),
        }

    def test_ssh(self, context: dict[str, Any]) -> dict[str, Any]:
        status = self._status_payload(context)
        try:
            result = CiscoSSHClient(self._management_ip(context), self._username(context), self._password(context)).test_reachability()
            return {
                "module": "cisco",
                "action": "test_ssh",
                "ok": True,
                "host": self._management_ip(context),
                "raw_excerpt": "\n".join(str(result.get("output") or "").splitlines()[:18]),
                "status": status,
            }
        except Exception as exc:
            return {"module": "cisco", "action": "test_ssh", "ok": False, "host": self._management_ip(context), "error": str(exc), "status": status}

    def discover_ports(self, context: dict[str, Any]) -> dict[str, Any]:
        host = self._management_ip(context)
        username = self._username(context)
        password = self._password(context)
        try:
            client = CiscoSSHClient(host, username, password)
            status = client.run_commands(
                [
                    "terminal length 0",
                    "show interfaces status",
                    "show ip interface brief",
                    "show running-config | section ^interface",
                ]
            )
            output = str(status.get("output") or "")
            discovery = parse_cisco_discovery_outputs(
                _extract_command_output(output, "show interfaces status", "show ip interface brief"),
                _extract_command_output(output, "show ip interface brief", "show running-config | section ^interface"),
                _extract_command_output(output, "show running-config | section ^interface", ""),
            )
            return {"module": "cisco", "action": "discover_ports", "ok": True, "host": host, "discovery": discovery, "raw_output": mask_secrets(output, [password]), "status": self._status_payload(context)}
        except Exception as exc:
            return {"module": "cisco", "action": "discover_ports", "ok": False, "host": host, "error": str(exc), "status": self._status_payload(context)}

    def preview_config(self, context: dict[str, Any], *, mode: str = "full", selected_ports: list[str] | None = None, existing_config: str = "") -> dict[str, Any]:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        validation = validate_cisco_config(cisco_cfg)
        if mode == "baseline":
            rendered = render_cisco_baseline_config(cisco_cfg)
        elif mode == "ports":
            rendered = render_cisco_port_config(cisco_cfg, selected_ports=selected_ports)
        else:
            rendered = render_cisco_full_config(cisco_cfg)
        return {
            "module": "cisco",
            "action": "preview",
            "ok": validation.get("ok", False),
            "mode": mode,
            "config": rendered,
            "diff": render_cisco_diff_preview(existing_config, rendered) if existing_config else "",
            "validation": validation,
            "status": self._status_payload(context),
        }

    def apply_config(self, context: dict[str, Any], *, mode: str = "full", selected_ports: list[str] | None = None) -> dict[str, Any]:
        preview = self.preview_config(context, mode=mode, selected_ports=selected_ports)
        if not preview.get("ok"):
            return {**preview, "action": f"apply_{mode}", "applied": False}
        cisco_cfg = dict((dict(context.get("cfg") or {}).get("cisco_switch")) or {})
        if mode == "baseline":
            actual_config = render_cisco_baseline_config(cisco_cfg, mask=False)
        elif mode == "ports":
            actual_config = render_cisco_port_config(cisco_cfg, selected_ports=selected_ports)
        else:
            actual_config = render_cisco_full_config(cisco_cfg, mask=False)
        commands = [line.strip() for line in str(actual_config or "").splitlines() if line.strip() and line.strip() != "!"]
        try:
            result = CiscoSSHClient(self._management_ip(context), self._username(context), self._password(context), timeout=30).run_commands(commands)
            return {**preview, "action": f"apply_{mode}", "applied": True, "raw_output": result.get("output", "")}
        except Exception as exc:
            return {**preview, "action": f"apply_{mode}", "ok": False, "applied": False, "error": str(exc)}

    def backup_config(self, context: dict[str, Any]) -> dict[str, Any]:
        try:
            result = CiscoSSHClient(self._management_ip(context), self._username(context), self._password(context), timeout=30).run_commands(["terminal length 0", "show running-config"])
            return {"module": "cisco", "action": "backup_config", "ok": True, "host": self._management_ip(context), "running_config": result.get("output", ""), "status": self._status_payload(context)}
        except Exception as exc:
            return {"module": "cisco", "action": "backup_config", "ok": False, "host": self._management_ip(context), "error": str(exc), "status": self._status_payload(context)}

    def management_config_preview(self, context: dict[str, Any]) -> str:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return render_management_config(
            {
                "hostname": str(cisco_cfg.get("hostname") or "sw01").strip(),
                "management_vlan": int(cisco_cfg.get("management_vlan") or 10),
                "management_ip": self._management_ip(context),
                "subnet_mask": str(cisco_cfg.get("subnet_mask") or "").strip(),
                "gateway": str(cisco_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip(),
                "domain_name": str(cisco_cfg.get("domain_name") or "lab.local").strip(),
                "username": self._username(context),
                "password": self._password(context),
            }
        )

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "cisco", "action": "plan", "ok": True, "status": self._status_payload(context)}

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "cisco", "action": "validate", "ok": True, "status": self._status_payload(context)}

    def preview(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.preview_config(context)

    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        mode = str((job or {}).get("mode") or "full").strip()
        selected_ports = list((job or {}).get("selected_ports") or [])
        return self.apply_config(context, mode=mode, selected_ports=selected_ports)

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "cisco", "action": "status", "ok": True, "status": self._status_payload(context)}

    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]:
        return {"module": "cisco", "action": "repair", "issue_id": issue_id, "ok": True, "status": self._status_payload(context)}


def _extract_command_output(output: str, start_command: str, end_command: str) -> str:
    text = str(output or "")
    if not start_command:
        return text
    start = text.find(start_command)
    if start < 0:
        return text
    start += len(start_command)
    end = text.find(end_command, start) if end_command else -1
    return text[start:end if end >= 0 else None]
