from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import platform
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
            "last_console_management_state": str(cisco_cfg.get("last_console_management_state") or ""),
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
            "operator_findings": self._operator_findings(cfg, cisco_cfg),
        }

    def _operator_findings(self, cfg: dict[str, Any], cisco_cfg: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        ip_plan = dict(cfg.get("ip_plan") or {})
        check = dict(cisco_cfg.get("last_console_bootstrap_check") or {})
        action = dict(cisco_cfg.get("last_cisco_action") or {})
        diagnostics = dict(cisco_cfg.get("last_console_diagnostics") or {})
        management_vlan = int(cisco_cfg.get("management_vlan") or 10)
        management_ip = str(cisco_cfg.get("management_ip") or cisco_cfg.get("ip") or "").strip()
        planned_switch_ip = str(ip_plan.get("switch") or "").strip()
        gateway = str(cisco_cfg.get("gateway") or ip_plan.get("gateway") or "").strip()
        netmask = str(cisco_cfg.get("subnet_mask") or ip_plan.get("netmask") or "").strip()

        def add(severity: str, title: str, detail: str, *, actions: list[str] | None = None) -> None:
            findings.append({"severity": severity, "title": title, "detail": detail, "actions": list(actions or [])})

        if diagnostics.get("permission_denied"):
            add(
                "danger",
                "Serial adapter permission is blocked",
                str(diagnostics.get("error_summary") or "The host can see the console adapter but the app cannot open it."),
                actions=["Run Fix serial access.", "Restart the app service if Linux group membership changed."],
            )
        elif diagnostics and not diagnostics.get("serial_imported"):
            add(
                "danger",
                "Python serial support is missing",
                "The Cisco console workflow cannot use USB serial until pyserial is available.",
                actions=["Install project requirements.", "Restart Lab Builder."],
            )

        if action.get("mode") == "discover_console" and action.get("error"):
            add(
                "warning",
                "Console discovery needs attention",
                str(action.get("error") or ""),
                actions=list(action.get("suggestions") or diagnostics.get("suggestions") or ["Check the console adapter and retry Test console access."]),
            )

        password = str(cisco_cfg.get("password") or "")
        enable_password = str(cisco_cfg.get("enable_password") or "")
        if password and len(password) < 10:
            add(
                "warning",
                "Cisco login password may fail modern password policy",
                "The saved Cisco password is shorter than 10 characters. Newer IOS XE setup dialogs often reject weak secrets.",
                actions=["Use a stronger Cisco password before bootstrap.", "Keep a separate strong enable secret configured."],
            )
        if not enable_password:
            add(
                "warning",
                "Enable secret is missing",
                "Console bootstrap may stop at the Cisco setup dialog until a valid enable secret is supplied.",
                actions=["Enter an enable secret in More settings.", "Use 10-32 characters with upper/lower case and a digit."],
            )
        elif len(enable_password) < 10 or not (re.search(r"[A-Z]", enable_password) and re.search(r"[a-z]", enable_password) and re.search(r"\d", enable_password)):
            add(
                "warning",
                "Enable secret may be rejected by IOS XE",
                "The saved enable secret does not appear to satisfy the common Cisco strength rule.",
                actions=["Set a stronger enable secret before running console bootstrap."],
            )

        if planned_switch_ip and management_ip and management_ip != planned_switch_ip:
            add(
                "warning",
                "Cisco management IP differs from generated IP plan",
                f"Configured Cisco IP is {management_ip}, but the kit IP plan suggests {planned_switch_ip}.",
                actions=["Keep the configured override if intentional.", "Change Cisco management IP to the generated switch address before bootstrap."],
            )
        if management_ip and gateway and netmask:
            try:
                network = ipaddress.ip_network(f"{management_ip}/{netmask}", strict=False)
                if ipaddress.ip_address(gateway) not in network:
                    add(
                        "danger",
                        "Cisco gateway is outside the management subnet",
                        f"Gateway {gateway} is not inside {network}. SSH will likely fail after bootstrap.",
                        actions=["Fix the gateway or management IP before applying console bootstrap."],
                    )
            except ValueError:
                add(
                    "danger",
                    "Cisco management network is invalid",
                    "The configured management IP, subnet mask, or gateway could not be parsed.",
                    actions=["Correct the Cisco network fields before applying bootstrap."],
                )

        if check:
            if check.get("ok") and check.get("host_reachable") is False:
                route = dict(check.get("host_route") or {})
                route_detail = str(route.get("summary") or "").strip()
                add(
                    "danger",
                    "No route from this machine to Cisco management IP",
                    (
                        f"The switch reports VLAN {management_vlan} and {management_ip} are up, but this host cannot reach {management_ip}. "
                        + (route_detail or "The host route check did not find a usable local path.")
                    ),
                    actions=[
                        "Make the local interface connected to the switch use an IP in the Cisco management subnet.",
                        "If using Wi-Fi for 192.168.1.x, connect VLAN 10 to that same LAN or route to it.",
                        "If using the wired port to the switch, change either the wired IP or the Cisco management IP/mask so they are in the same subnet.",
                    ],
                )
            if check.get("gateway_reachable") is False:
                add(
                    "danger",
                    "Configured Cisco gateway is not reachable from VLAN 10",
                    f"The Cisco default gateway is set to {gateway}, but the switch cannot ping that address from the management VLAN.",
                    actions=[
                        "Connect VLAN 10 to the network where that gateway exists.",
                        "Use a gateway that actually exists on the Cisco management VLAN.",
                        "If this is an isolated lab VLAN, leave gateway blank or use a lab router on VLAN 10.",
                    ],
                )
            if check.get("ssh_enabled") and not check.get("scp_enabled"):
                add(
                    "warning",
                    "Cisco SSH is enabled but SCP transfer is not ready",
                    "The switch accepts SSH, but the SCP server is not enabled. Firmware image transfer needs SCP.",
                    actions=["Run Configure IP using console again to enable SCP.", "Or let the upgrade workflow temporarily enable SCP during transfer."],
                )
            if not check.get("vlan_exists"):
                add(
                    "warning",
                    f"Management VLAN {management_vlan} is missing",
                    "The switch currently does not have the desired management VLAN in the VLAN database.",
                    actions=["Run Configure IP using console to create the VLAN/SVI.", "Or change Management VLAN if this switch should use an existing VLAN."],
                )
            if check.get("vlan_exists") and not check.get("connected_management_ports"):
                add(
                    "warning",
                    f"No connected port carries VLAN {management_vlan}",
                    "The management SVI will stay down until an active port carries the management VLAN.",
                    actions=["Select a bootstrap network port.", "Use trunk mode if the upstream path carries multiple VLANs.", "Use access mode for a single management VLAN uplink."],
                )
            connected = list(check.get("all_connected_ports") or [])
            access_vlans = sorted({str(item.get("vlan") or "") for item in connected if str(item.get("vlan") or "").strip()})
            if connected and access_vlans and str(management_vlan) not in access_vlans and "trunk" not in {item.lower() for item in access_vlans}:
                add(
                    "info",
                    "Connected ports are on a different VLAN",
                    f"Connected ports currently show VLAN(s): {', '.join(access_vlans)}. Desired management VLAN is {management_vlan}.",
                    actions=["Choose the connected port that leads back to Lab Builder.", "Bootstrap can move that port to trunk or access mode."],
                )
            if check.get("unexpected_svis"):
                add(
                    "warning",
                    "Unexpected switch management interfaces are present",
                    "The switch has other SVI addresses besides the desired management VLAN: " + ", ".join(check.get("unexpected_svis")),
                    actions=["Review whether these are intentional.", "Remove or ignore legacy/default SVIs before final config approval."],
                )
            suggested = list(check.get("suggested_bootstrap_ports") or [])
            if suggested and not str(cisco_cfg.get("bootstrap_network_port") or "").strip():
                add(
                    "info",
                    "Bootstrap port should be selected explicitly",
                    "Connected switchports were detected, but no bootstrap network port is configured.",
                    actions=[f"Candidate ports: {', '.join(suggested[:4])}", "Enter the intended port in Network port before bootstrap."],
                )

        override_keys = [
            key
            for key in ("management_ip", "ip", "gateway", "subnet_mask", "management_vlan", "bootstrap_network_port", "bootstrap_network_mode")
            if cisco_cfg.get(key) not in (None, "", ip_plan.get("switch") if key in {"management_ip", "ip"} else None)
        ]
        if override_keys:
            add(
                "info",
                "Cisco has explicit configuration overrides",
                "The Cisco module is using saved values instead of only generated kit defaults: " + ", ".join(sorted(set(override_keys))),
                actions=["Review these values before applying config.", "Keep them if they reflect the physical lab."],
            )

        return findings

    def _interface_kind(self, name: str) -> str:
        iface = str(name or "").lower()
        if iface.startswith(("wl", "wifi", "wlan")):
            return "wifi"
        if iface.startswith(("en", "eth")):
            return "wired"
        if iface.startswith(("br", "docker", "veth")):
            return "virtual"
        return "interface"

    def _host_ipv4_addresses(self) -> list[dict[str, Any]]:
        try:
            proc = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True, check=False, timeout=3)
        except Exception:
            return []
        if proc.returncode != 0:
            return []
        try:
            data = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return []
        addresses: list[dict[str, Any]] = []
        for item in list(data or []):
            iface = str(item.get("ifname") or "")
            for addr in list(item.get("addr_info") or []):
                ip = str(addr.get("local") or "")
                prefix = addr.get("prefixlen")
                if not ip or prefix is None:
                    continue
                addresses.append(
                    {
                        "interface": iface,
                        "kind": self._interface_kind(iface),
                        "ip": ip,
                        "prefixlen": int(prefix),
                    }
                )
        return addresses

    def _host_route_to(self, host: str) -> dict[str, str]:
        try:
            proc = subprocess.run(["ip", "-4", "route", "get", str(host)], capture_output=True, text=True, check=False, timeout=3)
        except Exception as exc:
            return {"error": str(exc).splitlines()[0]}
        text = " ".join(str(proc.stdout or proc.stderr or "").split())
        if proc.returncode != 0:
            return {"error": text or f"ip route get failed ({proc.returncode})"}
        route: dict[str, str] = {"raw": text}
        parts = text.split()
        for key in ("dev", "src", "via"):
            if key in parts:
                index = parts.index(key)
                if index + 1 < len(parts):
                    route[key] = parts[index + 1]
        return route

    def _host_network_diagnostics(self, host: str, netmask: str = "", gateway: str = "") -> dict[str, Any]:
        target = str(host or "").strip()
        diagnostics: dict[str, Any] = {"target": target}
        try:
            target_ip = ipaddress.ip_address(target)
        except ValueError:
            diagnostics["summary"] = "Cisco management IP is not a valid IPv4 address."
            return diagnostics

        route = self._host_route_to(target)
        addresses = self._host_ipv4_addresses()
        diagnostics["route"] = route
        diagnostics["addresses"] = addresses

        cisco_network = None
        if netmask:
            try:
                cisco_network = ipaddress.ip_network(f"{target}/{netmask}", strict=False)
                diagnostics["cisco_network"] = str(cisco_network)
            except ValueError:
                diagnostics["summary"] = f"Subnet mask {netmask} is invalid for Cisco management IP {target}."
                return diagnostics

        same_subnet = []
        off_subnet = []
        for address in addresses:
            ip_text = str(address.get("ip") or "")
            try:
                local_ip = ipaddress.ip_address(ip_text)
            except ValueError:
                continue
            if cisco_network and local_ip in cisco_network:
                same_subnet.append(address)
            elif address.get("kind") != "virtual":
                off_subnet.append(address)
        diagnostics["same_subnet_addresses"] = same_subnet
        diagnostics["off_subnet_addresses"] = off_subnet

        route_dev = str(route.get("dev") or "")
        route_src = str(route.get("src") or "")
        route_kind = self._interface_kind(route_dev)
        if route.get("error"):
            diagnostics["summary"] = f"The host has no route to {target}: {route.get('error')}."
        elif cisco_network and route_src:
            try:
                route_src_ip = ipaddress.ip_address(route_src)
            except ValueError:
                route_src_ip = None
            if route_src_ip and route_src_ip not in cisco_network:
                diagnostics["summary"] = (
                    f"This host would send traffic to {target} through {route_dev or 'an unknown interface'} "
                    f"using source {route_src}, but that source is outside Cisco subnet {cisco_network}."
                )
            elif route_src_ip:
                detail = (
                    f"This host routes to {target} through {route_dev} ({route_kind}) using source {route_src}, "
                    f"which is inside Cisco subnet {cisco_network}, but the switch still does not answer."
                )
                wired_off_subnet = [item for item in off_subnet if item.get("kind") == "wired"]
                if route_kind == "wifi" and wired_off_subnet:
                    detail += " A wired adapter is also active outside that subnet: " + ", ".join(
                        f"{item.get('interface')}={item.get('ip')}/{item.get('prefixlen')}" for item in wired_off_subnet
                    ) + ". If the switch is connected to that wired adapter, the wired IP/subnet does not match the Cisco management network."
                detail += " This usually means the selected switch VLAN is not connected to the same Layer-2 network as that local interface."
                diagnostics["summary"] = detail
        if not diagnostics.get("summary"):
            if same_subnet:
                diagnostics["summary"] = f"This host has local address(es) in the Cisco subnet, but {target} is still unreachable; check VLAN membership, cabling, and ARP."
            else:
                diagnostics["summary"] = f"No non-virtual local interface address is in the Cisco management subnet for {target}."

        if gateway and cisco_network:
            try:
                gateway_ip = ipaddress.ip_address(gateway)
                diagnostics["gateway_in_cisco_subnet"] = gateway_ip in cisco_network
            except ValueError:
                diagnostics["gateway_in_cisco_subnet"] = False
        return diagnostics

    def _host_can_reach(self, host: str) -> bool | None:
        target = str(host or "").strip()
        if not target:
            return None
        try:
            ipaddress.ip_address(target)
        except ValueError:
            return None
        count_arg = "-n" if platform.system().lower().startswith("win") else "-c"
        wait_arg = "-w" if platform.system().lower().startswith("win") else "-W"
        try:
            proc = subprocess.run(["ping", count_arg, "1", wait_arg, "1", target], capture_output=True, text=True, check=False, timeout=3)
        except Exception:
            return None
        return proc.returncode == 0

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

    def discover_version_any(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        port = str(cisco_cfg.get("console_port") or "").strip()
        baud = int(cisco_cfg.get("console_baud") or 9600)

        console_warning = ""
        if port:
            try:
                with CiscoSerialClient(port, baud) as client:
                    output = client.read_prompt()
                    output += client.run_command("terminal length 0", wait_seconds=1.0)
                    output += client.run_command("show version", wait_seconds=5.0)
                parsed = parse_cisco_show_version(output)
                if parsed.get("version"):
                    return {
                        "module": "cisco",
                        "action": "discover",
                        "ok": True,
                        "target": port,
                        "username": self._username(context),
                        "source": "console",
                        "version": str(parsed.get("version") or ""),
                        "hostname": str(parsed.get("hostname") or ""),
                        "model": str(parsed.get("model") or ""),
                        "platform": str(parsed.get("platform") or ""),
                        "raw_excerpt": mask_secrets(str(parsed.get("raw_excerpt") or ""), [self._password(context), str(cisco_cfg.get("enable_password") or "")]),
                        "warnings": [],
                        "status": self._status_payload(context),
                    }
                console_warning = "Console responded, but Cisco version could not be parsed from show version output."
            except Exception as exc:
                console_warning = f"Console version read failed: {str(exc).splitlines()[0]}"

        ssh_result = self.discover(context)
        if ssh_result.get("ok"):
            ssh_result["source"] = "ssh"
            warnings = list(ssh_result.get("warnings") or [])
            if not port:
                warnings.append("Console version read skipped because no console port is selected.")
            elif console_warning:
                warnings.append(console_warning)
            ssh_result["warnings"] = warnings
            return ssh_result

        if port:
            return {
                **ssh_result,
                "source": "console_ssh_failed",
                "warnings": list(ssh_result.get("warnings") or []) + ([console_warning] if console_warning else []),
            }

        return {
            **ssh_result,
            "source": "ssh",
            "warnings": list(ssh_result.get("warnings") or []) + ["Console version read skipped because no console port is selected."],
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
        management_ip = self._management_ip(context)
        subnet_mask = str(cisco_cfg.get("subnet_mask") or "").strip()
        gateway = str(cisco_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip()
        if not port:
            return {"module": "cisco", "action": "verify_console_bootstrap", "ok": False, "error": "Cisco console port is not selected.", "status": self._status_payload(context)}
        commands = [
            "terminal length 0",
            "show ip interface brief",
            "show vlan brief",
            f"show run interface Vlan{management_vlan}",
            "show run | include ^ip default-gateway|^ip domain-name|^ip ssh|^ip scp|^username",
            "show run | section ^line vty",
            "show interfaces status",
            "show ip ssh",
            "show run | include ^ip scp server enable",
            "show run",
            f"ping {gateway} repeat 2 timeout 1" if gateway else "",
        ]
        commands = [command for command in commands if command]
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
        unexpected_svis = [
            f"{name} {data.get('ip_address')}"
            for name, data in sorted(ip_interfaces.items())
            if name.lower().startswith("vlan")
            and name != svi_name
            and str(data.get("ip_address") or "").strip().lower() not in {"", "unassigned"}
        ]
        vlan_exists = bool(re.search(rf"(?im)^\s*{management_vlan}\s+\S+\s+active\b", output))
        ssh_enabled = "SSH Enabled" in output
        scp_enabled = bool(re.search(r"(?im)^ip scp server enable\s*$", output))
        host_reachable = self._host_can_reach(management_ip) if vlan_exists else None
        host_route = self._host_network_diagnostics(management_ip, subnet_mask, gateway) if vlan_exists else {}
        gateway_reachable = None
        if gateway:
            gateway_output = _extract_command_output(output, f"ping {gateway} repeat 2 timeout 1", "")
            if "Success rate is" in gateway_output:
                gateway_reachable = not re.search(r"Success rate is\s+0\s+percent", gateway_output, flags=re.IGNORECASE)
        all_connected_ports = [
            {
                "name": name,
                "vlan": str(data.get("vlan") or ""),
                "speed": str(data.get("speed") or ""),
                "type": str(data.get("type") or ""),
                "description": str(data.get("name") or data.get("description") or ""),
            }
            for name, data in sorted(interface_status.items(), key=lambda item: item[0])
            if str(data.get("status") or "").lower() == "connected"
        ]
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
        if not scp_enabled:
            warnings.append("Cisco SCP server is not enabled; image transfer for upgrade will need to enable it before upload.")
        if vlan_exists and svi_admin_status == "up" and svi_protocol == "up" and host_reachable is False:
            warnings.append(f"Cisco management IP {management_ip} is configured and up on the switch, but this Lab Builder host cannot reach it. {host_route.get('summary') or ''}".strip())
        if gateway and gateway_reachable is False:
            warnings.append(f"Cisco default gateway {gateway} is configured, but the switch cannot ping it from the management VLAN.")
        if unexpected_svis:
            warnings.append("Unexpected SVI address exists: " + ", ".join(unexpected_svis))
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
            "scp_enabled": scp_enabled,
            "host_reachable": host_reachable,
            "host_route": host_route,
            "gateway_reachable": gateway_reachable,
            "unexpected_svis": unexpected_svis,
            "connected_management_ports": connected_ports,
            "all_connected_ports": all_connected_ports,
            "suggested_bootstrap_ports": [item["name"] for item in all_connected_ports if not item["name"].lower().startswith("ap")],
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
                    "show run | section ^interface",
                ]
            )
            output = str(status.get("output") or "")
            discovery = parse_cisco_discovery_outputs(
                _extract_command_output(output, "show interfaces status", "show ip interface brief"),
                _extract_command_output(output, "show ip interface brief", "show run | section ^interface"),
                _extract_command_output(output, "show run | section ^interface", ""),
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
            result = CiscoSSHClient(self._management_ip(context), self._username(context), self._password(context), timeout=30).run_commands(["terminal length 0", "show run"])
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
