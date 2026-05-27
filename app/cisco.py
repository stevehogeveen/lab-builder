from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import grp
import json
from pathlib import Path
import glob
import ipaddress
import os
import pwd
import re
import stat
import shutil
import subprocess
import time
from typing import Any

import paramiko

try:  # pyserial is optional at import time so tests can monkeypatch it cleanly.
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised only when pyserial is absent.
    serial = None
    list_ports = None


CISCO_BAUD_RATES = [9600, 115200]
SECRET_MASK = "********"
CISCO_LOG_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "logs" / "cisco.log"
CISCO_PROFILE_FIELDS = {
    "mode",
    "access_vlan",
    "allowed_vlans",
    "native_vlan",
    "description",
    "enabled",
    "cdp",
    "lldp",
    "snmp_trap_link_status",
    "spanning_tree_portfast",
    "bpduguard",
    "extra_commands",
}


DEFAULT_CISCO_VLANS: list[dict[str, Any]] = [
    {"id": 10, "name": "MANAGEMENT", "svi_enabled": True, "description": "Management VLAN"},
]


DEFAULT_CISCO_PORT_PROFILES: dict[str, dict[str, Any]] = {
    "client_device": {
        "mode": "access",
        "access_vlan": 10,
        "allowed_vlans": [],
        "native_vlan": "",
        "description": "Client Device",
        "enabled": True,
        "cdp": False,
        "lldp": True,
        "snmp_trap_link_status": True,
        "spanning_tree_portfast": True,
        "bpduguard": True,
        "extra_commands": [],
    },
    "unused_blackhole": {
        "mode": "access",
        "access_vlan": 10,
        "allowed_vlans": [],
        "native_vlan": "",
        "description": "UNUSED",
        "enabled": False,
        "cdp": False,
        "lldp": True,
        "snmp_trap_link_status": False,
        "spanning_tree_portfast": True,
        "bpduguard": True,
        "extra_commands": [],
    },
    "uplink_trunk": {
        "mode": "trunk",
        "access_vlan": "",
        "allowed_vlans": [10],
        "native_vlan": "",
        "description": "Uplink",
        "enabled": True,
        "cdp": True,
        "lldp": True,
        "snmp_trap_link_status": True,
        "spanning_tree_portfast": False,
        "bpduguard": False,
        "extra_commands": [],
    },
    "server_esxi": {
        "mode": "trunk",
        "access_vlan": "",
        "allowed_vlans": [10],
        "native_vlan": "",
        "description": "ESXi Server",
        "enabled": True,
        "cdp": True,
        "lldp": True,
        "snmp_trap_link_status": True,
        "spanning_tree_portfast": True,
        "bpduguard": False,
        "extra_commands": [],
    },
    "netapp_storage": {
        "mode": "trunk",
        "access_vlan": "",
        "allowed_vlans": [10],
        "native_vlan": "",
        "description": "NetApp Storage",
        "enabled": True,
        "cdp": True,
        "lldp": True,
        "snmp_trap_link_status": True,
        "spanning_tree_portfast": True,
        "bpduguard": False,
        "extra_commands": [],
    },
    "access_point": {
        "mode": "trunk",
        "access_vlan": "",
        "allowed_vlans": [10],
        "native_vlan": 10,
        "description": "Access Point",
        "enabled": True,
        "cdp": False,
        "lldp": True,
        "snmp_trap_link_status": True,
        "spanning_tree_portfast": True,
        "bpduguard": True,
        "extra_commands": [],
    },
    "printer": {
        "mode": "access",
        "access_vlan": 10,
        "allowed_vlans": [],
        "native_vlan": "",
        "description": "Printer",
        "enabled": True,
        "cdp": False,
        "lldp": True,
        "snmp_trap_link_status": True,
        "spanning_tree_portfast": True,
        "bpduguard": True,
        "extra_commands": [],
    },
    "custom": {
        "mode": "access",
        "access_vlan": 10,
        "allowed_vlans": [],
        "native_vlan": "",
        "description": "Custom",
        "enabled": True,
        "cdp": False,
        "lldp": True,
        "snmp_trap_link_status": True,
        "spanning_tree_portfast": False,
        "bpduguard": False,
        "extra_commands": [],
    },
}


INTERFACE_PREFIXES = {
    "gi": "GigabitEthernet",
    "gigabitethernet": "GigabitEthernet",
    "te": "TenGigabitEthernet",
    "tengigabitethernet": "TenGigabitEthernet",
    "fo": "FortyGigabitEthernet",
    "fortygigabitethernet": "FortyGigabitEthernet",
    "tw": "TwentyFiveGigE",
    "twentyfivegige": "TwentyFiveGigE",
    "hu": "HundredGigE",
    "hundredgige": "HundredGigE",
    "fa": "FastEthernet",
    "fastethernet": "FastEthernet",
    "po": "Port-channel",
    "port-channel": "Port-channel",
    "vlan": "Vlan",
}

INTERFACE_SHORT_PREFIXES = {
    "GigabitEthernet": "Gi",
    "TenGigabitEthernet": "Te",
    "FortyGigabitEthernet": "Fo",
    "TwentyFiveGigE": "Tw",
    "HundredGigE": "Hu",
    "FastEthernet": "Fa",
    "Port-channel": "Po",
    "Vlan": "Vlan",
}


class CiscoError(RuntimeError):
    pass


class CiscoSerialError(CiscoError):
    pass


class CiscoSSHError(CiscoError):
    pass


@dataclass
class CiscoSerialCandidate:
    port: str
    baud: int
    description: str = ""
    hardware_id: str = ""
    manufacturer: str = ""
    prompt_type: str = ""
    score: int = 0
    raw_output: str = ""
    error: str = ""

    def as_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        payload = {
            "port": self.port,
            "baud": self.baud,
            "description": self.description,
            "hardware_id": self.hardware_id,
            "manufacturer": self.manufacturer,
            "prompt_type": self.prompt_type,
            "score": self.score,
            "error": self.error,
        }
        if include_raw:
            payload["raw_output"] = self.raw_output
        return payload


@dataclass
class CiscoBootstrapResult:
    ok: bool
    port: str = ""
    baud: int = 9600
    management_ip: str = ""
    ssh_reachable: bool = False
    commands: list[str] = field(default_factory=list)
    output: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    def as_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "port": self.port,
            "baud": self.baud,
            "management_ip": self.management_ip,
            "ssh_reachable": self.ssh_reachable,
            "commands": [mask_secrets(item) for item in self.commands],
            "error": mask_secrets(self.error),
            "warnings": [mask_secrets(item) for item in self.warnings],
            "steps": [mask_secrets(item) for item in self.steps],
        }
        if include_raw:
            payload["output"] = mask_secrets(self.output)
        return payload


PROMPT_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("interface_config", re.compile(r"(?im)(?:^|\r|\n)\s*(?:Switch|Router|[\w.-]+)\(config-[^)]+\)#\s*$"), 98),
    ("config", re.compile(r"(?im)(?:^|\r|\n)\s*(?:Switch|Router|[\w.-]+)\(config\)#\s*$"), 97),
    ("privileged", re.compile(r"(?im)(?:^|\r|\n)\s*(?:Switch|Router|[\w.-]+)#\s*$"), 100),
    ("user_exec", re.compile(r"(?im)(?:^|\r|\n)\s*(?:Switch|Router|[\w.-]+)>\s*$"), 90),
    ("username", re.compile(r"(?im)(?:^|\r|\n)\s*Username:\s*$"), 80),
    ("login", re.compile(r"(?im)(?:^|\r|\n)\s*Login:\s*$"), 80),
    (
        "setup_password_policy_failure",
        re.compile(
            r"(?is)(?:password|secret).{0,120}(?:too short|too weak|not strong|must contain|must be|"
            r"does not (?:satisfy|meet|pass)|invalid|rejected|failed|failure)"
        ),
        79,
    ),
    ("setup_final_menu", re.compile(r"(?is)0\s*[.)-]?\s*Go to the IOS command prompt without saving this config|Enter your selection\s*(?:\[[0-2]\])?\s*:\s*$"), 78),
    ("setup_enable_secret", re.compile(r"(?im)(?:^|\r|\n)\s*(?:(?:Enter|Confirm|Re-enter|Reenter|Verify|Please enter)\s+(?:the\s+)?)?enable\s+(?:secret|password):\s*$"), 76),
    (
        "setup_password",
        re.compile(
            r"(?im)(?:^|\r|\n)\s*(?:"
            r"(?:(?:Enter|Confirm|Re-enter|Reenter|Verify|Please enter)\s+(?:the\s+)?)?"
            r"(?:virtual terminal|console|user|login|line vty)\s+password|"
            r"(?:Enter|Confirm|Re-enter|Reenter|Verify|Please enter)\s+(?:the\s+)?password"
            r"):\s*$"
        ),
        76,
    ),
    ("password", re.compile(r"(?im)(?:^|\r|\n)\s*Password:\s*$"), 75),
    ("rommon", re.compile(r"(?im)(?:^|\r|\n)\s*rommon\s*>\s*$"), 70),
    ("initial_dialog", re.compile(r"(?is)initial configuration dialog|would you like to enter the initial configuration dialog|continue with configuration dialog"), 65),
    ("autoinstall_terminate", re.compile(r"(?is)would you like to terminate autoinstall|terminate autoinstall\?\s*\[yes\]"), 64),
    ("setup_yes_no", re.compile(r"(?is)(?:would you like|do you want|configure|continue).+\[(?:yes/no|no|yes)\]\s*:\s*$"), 62),
    ("press_return", re.compile(r"(?is)press\s+return\s+to\s+get\s+started!?"), 60),
]

CISCO_PROMPT_LABELS = {
    "autoinstall_terminate": "autoinstall termination prompt",
    "config": "global configuration prompt",
    "enable_password_required": "Password prompt after enable",
    "initial_dialog": "initial configuration dialog",
    "interface_config": "interface configuration prompt",
    "login": "login prompt",
    "password": "password prompt",
    "press_return": "Press RETURN prompt",
    "privileged": "privileged EXEC prompt",
    "rommon": "ROMMON prompt",
    "setup_enable_secret": "setup enable secret/password prompt",
    "setup_final_menu": "setup wizard final menu",
    "setup_password": "setup wizard password prompt",
    "setup_password_policy_failure": "setup wizard password policy failure",
    "setup_yes_no": "setup wizard yes/no prompt",
    "timeout": "console read timeout",
    "unknown": "unrecognized console output",
    "user_exec": "user EXEC prompt",
    "username": "username prompt",
}

CISCO_IDENTITY_PATTERN = re.compile(
    r"(?is)\b(?:Cisco IOS|Cisco IOS XE|Cisco NX-OS|Cisco Adaptive Security Appliance|Catalyst|Nexus|"
    r"cisco\s+\S+\s+\([^)]+\)\s+processor|Model number\s*:|System serial number\s*:)\b"
)
GENERIC_PROMPT_PATTERN = re.compile(r"(?im)(?:^|\r|\n)\s*([\w.-]+)([#>])\s*$")


def detect_cisco_prompt(output: str) -> tuple[str, int]:
    text = str(output or "")
    for prompt_type, pattern, score in PROMPT_PATTERNS:
        if pattern.search(text):
            return prompt_type, score
    if re.search(r"(?is)\bCisco\b.+(?:IOS|rommon|bootstrap)", text):
        return "cisco_output", 50
    return "", 0


def has_cisco_identity(output: str) -> bool:
    return bool(CISCO_IDENTITY_PATTERN.search(str(output or "")))


def is_generic_console_prompt(output: str) -> bool:
    match = GENERIC_PROMPT_PATTERN.search(str(output or ""))
    if not match:
        return False
    hostname = match.group(1).strip().lower()
    return hostname not in {"switch", "router"}


def validate_cisco_wizard_password_policy(password: str) -> list[str]:
    secret = str(password or "")
    failures: list[str] = []
    if len(secret) < 10:
        failures.append("at least 10 characters")
    if not re.search(r"[A-Z]", secret):
        failures.append("one uppercase letter")
    if not re.search(r"[a-z]", secret):
        failures.append("one lowercase letter")
    if not re.search(r"\d", secret):
        failures.append("one digit")
    return failures


def cisco_wizard_password_policy_error(label: str, password: str) -> str:
    failures = validate_cisco_wizard_password_policy(password)
    if not failures:
        return ""
    return f"{label} must satisfy the Cisco setup wizard password policy: use " + ", ".join(failures) + "."


def mask_secrets(value: str, secrets: list[str] | None = None) -> str:
    masked = str(value or "")
    for secret in [item for item in (secrets or []) if item]:
        masked = masked.replace(str(secret), SECRET_MASK)
    masked = re.sub(r"(?im)^(\s*username\s+\S+\s+privilege\s+\d+\s+(?:algorithm-type\s+\S+\s+)?secret\s+).*$", rf"\1{SECRET_MASK}", masked)
    masked = re.sub(r"(?im)^(\s*enable\s+secret\s+).*$", rf"\1{SECRET_MASK}", masked)
    masked = re.sub(r"(?im)^(\s*enable\s+password\s+).*$", rf"\1{SECRET_MASK}", masked)
    masked = re.sub(r"(?im)^(\s*Password:\s*).*$", rf"\1{SECRET_MASK}", masked)
    return masked


def _device_access_details(path: str) -> dict[str, Any]:
    details: dict[str, Any] = {"path": str(path or "")}
    try:
        device_stat = os.stat(path)
    except OSError as exc:
        details["error"] = str(exc).splitlines()[0]
        return details
    try:
        owner = pwd.getpwuid(device_stat.st_uid).pw_name
    except KeyError:
        owner = str(device_stat.st_uid)
    try:
        group = grp.getgrgid(device_stat.st_gid).gr_name
    except KeyError:
        group = str(device_stat.st_gid)
    details.update(
        {
            "mode": stat.filemode(device_stat.st_mode),
            "owner": owner,
            "group": group,
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
        }
    )
    return details


def append_cisco_log(event: str, **fields: Any) -> None:
    def _sanitize(value: Any) -> Any:
        if isinstance(value, str):
            return mask_secrets(value)
        if isinstance(value, list):
            return [_sanitize(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _sanitize(item) for key, item in value.items()}
        return value

    payload = {"event": str(event or ""), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for key, value in fields.items():
        payload[key] = _sanitize(value)
    try:
        CISCO_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CISCO_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        pass


def _run_sudo_command(command: list[str], password: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["sudo", "-S", "-p", ""] + list(command),
            input=str(password or "") + "\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception as exc:
        return False, str(exc).splitlines()[0]
    output = str(proc.stderr or proc.stdout or "").strip()
    if proc.returncode != 0:
        return False, output.splitlines()[0] if output else f"sudo command failed ({proc.returncode})."
    return True, output


def _port_metadata() -> dict[str, Any]:
    serial_module, port_tools = _load_serial_modules()
    ports: dict[str, Any] = {}
    if serial_module is None or port_tools is None:
        return ports
    for info in port_tools.comports():
        device = str(getattr(info, "device", "") or "")
        if device:
            ports[device] = info
    return ports


def _load_serial_modules() -> tuple[Any, Any]:
    global serial, list_ports
    if serial is not None and list_ports is not None:
        return serial, list_ports
    try:
        import serial as serial_module
        from serial.tools import list_ports as port_tools
    except ImportError:
        serial = None
        list_ports = None
        return None, None
    serial = serial_module
    list_ports = port_tools
    return serial, list_ports


def serial_runtime_diagnostics() -> dict[str, Any]:
    serial_module, port_tools = _load_serial_modules()
    ordered = _ordered_port_paths(_port_metadata())
    group_names = sorted({grp.getgrgid(group_id).gr_name for group_id in os.getgroups()})
    return {
        "serial_imported": serial_module is not None,
        "list_ports_imported": port_tools is not None,
        "log_path": str(CISCO_LOG_PATH),
        "user": pwd.getpwuid(os.getuid()).pw_name,
        "group_names": group_names,
        "ordered_ports": ordered,
        "by_id_ports": sorted(glob.glob("/dev/serial/by-id/*")),
        "ttyusb_ports": sorted(glob.glob("/dev/ttyUSB*")),
        "ttyacm_ports": sorted(glob.glob("/dev/ttyACM*")),
        "device_access": [_device_access_details(path) for path in ordered],
    }


def apply_serial_permission_fix(sudo_password: str, *, username: str = "") -> dict[str, Any]:
    password = str(sudo_password or "")
    if not password:
        return {"ok": False, "error": "Sudo password is required.", "applied": [], "warnings": [], "diagnostics": serial_runtime_diagnostics()}

    effective_user = str(username or pwd.getpwuid(os.getuid()).pw_name).strip()
    diagnostics_before = serial_runtime_diagnostics()
    real_devices = sorted({os.path.realpath(path) for path in list(diagnostics_before.get("ordered_ports") or []) if str(path).strip()})
    applied: list[str] = []
    warnings: list[str] = []

    append_cisco_log("serial.permissions.fix.start", username=effective_user, ports=real_devices)

    ok, output = _run_sudo_command(["/usr/sbin/usermod", "-aG", "dialout", effective_user], password)
    if not ok:
        append_cisco_log("serial.permissions.fix.failed", username=effective_user, error=output)
        return {"ok": False, "error": output, "applied": applied, "warnings": warnings, "diagnostics": diagnostics_before}
    applied.append(f"Added {effective_user} to dialout.")

    setfacl_path = shutil.which("setfacl")
    if setfacl_path and real_devices:
        for device in real_devices:
            acl_ok, acl_output = _run_sudo_command([setfacl_path, "-m", f"u:{effective_user}:rw", device], password)
            if acl_ok:
                applied.append(f"Granted rw ACL on {device}.")
            else:
                warnings.append(f"Could not grant immediate ACL on {device}: {acl_output}")
    else:
        warnings.append("Immediate ACL grant was skipped because setfacl is unavailable or no serial device is present.")

    diagnostics_after = serial_runtime_diagnostics()
    append_cisco_log(
        "serial.permissions.fix.complete",
        username=effective_user,
        applied=applied,
        warnings=warnings,
        diagnostics=diagnostics_after,
    )
    restart_required = "dialout" not in list(diagnostics_after.get("group_names") or [])
    if restart_required:
        warnings.append("Lab Builder must be restarted from a fresh login or service session before the dialout group membership applies to the running process.")
    if real_devices and any(item.get("readable") and item.get("writable") for item in list(diagnostics_after.get("device_access") or [])):
        restart_required = False
    return {
        "ok": True,
        "error": "",
        "applied": applied,
        "warnings": warnings,
        "restart_required": restart_required,
        "diagnostics": diagnostics_after,
    }


def _ordered_port_paths(metadata: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    seen_realpaths: set[str] = set()
    for pattern in ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        for path in sorted(glob.glob(pattern)):
            realpath = os.path.realpath(path)
            if realpath not in seen_realpaths and path not in ordered:
                ordered.append(path)
                seen_realpaths.add(realpath)
    return ordered


class CiscoSerialDiscovery:
    def __init__(self, *, baud_rates: list[int] | None = None, timeout: float = 0.8, settle_seconds: float = 0.2):
        self.baud_rates = list(baud_rates or CISCO_BAUD_RATES)
        self.timeout = timeout
        self.settle_seconds = settle_seconds

    def scan(self) -> list[CiscoSerialCandidate]:
        serial_module, _port_tools = _load_serial_modules()
        if serial_module is None:
            append_cisco_log("serial.discovery.unavailable", error="pyserial is required for Cisco serial discovery.")
            raise CiscoSerialError("pyserial is required for Cisco serial discovery.")

        metadata = _port_metadata()
        candidates: list[CiscoSerialCandidate] = []
        append_cisco_log("serial.discovery.start", ports=_ordered_port_paths(metadata), baud_rates=self.baud_rates)
        for port in _ordered_port_paths(metadata):
            info = metadata.get(port)
            for baud in self.baud_rates:
                candidates.append(self._probe_port(port, baud, info))
        candidates.sort(key=lambda item: (item.score, item.prompt_type == "privileged", item.port.startswith("/dev/serial/by-id/")), reverse=True)
        append_cisco_log(
            "serial.discovery.complete",
            results=[
                {
                    "port": item.port,
                    "baud": item.baud,
                    "prompt_type": item.prompt_type,
                    "score": item.score,
                    "error": item.error,
                }
                for item in candidates
            ],
        )
        return candidates

    def _probe_port(self, port: str, baud: int, info: Any) -> CiscoSerialCandidate:
        candidate = CiscoSerialCandidate(
            port=port,
            baud=baud,
            description=str(getattr(info, "description", "") or ""),
            hardware_id=str(getattr(info, "hwid", "") or ""),
            manufacturer=str(getattr(info, "manufacturer", "") or ""),
        )
        try:
            serial_module, _port_tools = _load_serial_modules()
            if serial_module is None:
                raise CiscoSerialError("pyserial is required for Cisco serial discovery.")
            with serial_module.Serial(port=port, baudrate=baud, timeout=self.timeout, write_timeout=self.timeout) as conn:
                time.sleep(self.settle_seconds)
                conn.reset_input_buffer()
                conn.write(b"\r\n")
                conn.flush()
                candidate.raw_output = self._read_available(conn)
                prompt_type, score = detect_cisco_prompt(candidate.raw_output)
                if prompt_type in {"privileged", "user_exec"}:
                    identity_output = self._probe_identity(conn)
                    if identity_output:
                        candidate.raw_output = f"{candidate.raw_output}\n{identity_output}".strip()
                        if has_cisco_identity(identity_output):
                            prompt_type = f"{prompt_type}_verified"
                            score = max(score + 120, 180)
                    if prompt_type in {"privileged", "user_exec"} and is_generic_console_prompt(candidate.raw_output):
                        prompt_type = f"generic_{prompt_type}"
                        score = min(score, 35)
                candidate.prompt_type = prompt_type
                candidate.score = score
        except Exception as exc:
            candidate.error = str(exc).splitlines()[0]
            append_cisco_log("serial.discovery.probe_failed", port=port, baud=baud, error=candidate.error)
            return candidate

        append_cisco_log(
            "serial.discovery.probe_complete",
            port=port,
            baud=baud,
            prompt_type=candidate.prompt_type,
            score=candidate.score,
            raw_output=mask_secrets(candidate.raw_output),
        )
        return candidate

    def _probe_identity(self, conn: Any) -> str:
        output = ""
        for command, wait_seconds in (("terminal length 0", 0.25), ("show version", max(1.0, self.timeout))):
            try:
                conn.write((command + "\r\n").encode("utf-8"))
                conn.flush()
                output += self._read_available_for(conn, wait_seconds)
            except Exception as exc:
                append_cisco_log("serial.discovery.identity_probe_failed", error=str(exc).splitlines()[0])
                break
        return output

    def _read_available(self, conn: Any) -> str:
        return self._read_available_for(conn, self.timeout)

    def _read_available_for(self, conn: Any, seconds: float) -> str:
        deadline = time.monotonic() + seconds
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            waiting = int(getattr(conn, "in_waiting", 0) or 0)
            chunk = conn.read(waiting or 256)
            if chunk:
                chunks.append(chunk)
            else:
                time.sleep(0.05)
        return b"".join(chunks).decode("utf-8", errors="replace")


class CiscoConsoleBootstrapStateMachine:
    """Advance common Cisco console states to privileged EXEC without applying config."""

    max_transitions = 12
    forced_setup_prompt_types = {
        "setup_enable_secret",
        "setup_final_menu",
        "setup_password",
        "setup_password_policy_failure",
        "setup_yes_no",
    }

    def __init__(self, client: Any):
        self.client = client
        self.steps: list[str] = []
        self.last_prompt_type = ""
        self.last_safe_action = "pressed RETURN to read console output"
        self.initial_dialog_answered_no = False
        self.forced_setup_wizard_detected = False
        self.final_menu_seen = False
        self.user_exec_reached = False
        self.enable_password_prompt_reached = False
        self.password_policy_failure_detected = False

    def _record_prompt(self, prompt_type: str) -> None:
        if not prompt_type:
            return
        self.last_prompt_type = prompt_type
        if prompt_type in self.forced_setup_prompt_types:
            self.forced_setup_wizard_detected = True
        if prompt_type == "setup_final_menu":
            self.final_menu_seen = True
        elif prompt_type == "user_exec":
            self.user_exec_reached = True
        elif prompt_type in {"enable_password_required", "password_after_enable"}:
            self.enable_password_prompt_reached = True
        elif prompt_type == "setup_password_policy_failure":
            self.password_policy_failure_detected = True

    def _set_action(self, action: str, step: str) -> None:
        self.last_safe_action = action
        self.steps.append(step)

    def diagnostics(self, prompt_type: str) -> dict[str, Any]:
        state = prompt_type or self.last_prompt_type or "unknown"
        label = CISCO_PROMPT_LABELS.get(state, state)
        return {
            "last_detected_state": f"{state} ({label})",
            "last_prompt_type": state,
            "last_safe_action": self.last_safe_action,
            "initial_dialog_answered_no": self.initial_dialog_answered_no,
            "forced_setup_wizard_detected": self.forced_setup_wizard_detected,
            "final_menu_seen": self.final_menu_seen,
            "switch_user_exec_reached": self.user_exec_reached,
            "enable_password_prompt_reached": self.enable_password_prompt_reached,
            "password_policy_failure_detected": self.password_policy_failure_detected,
            "next_manual_recovery_step": self._manual_recovery_step(state),
        }

    def _manual_recovery_step(self, prompt_type: str) -> str:
        if self.password_policy_failure_detected:
            return "Set a policy-compliant Cisco password and enable secret in Access settings, then rerun Setup Console."
        if self.enable_password_prompt_reached:
            if self.final_menu_seen:
                return "Verify the saved enable secret, manually confirm enable reaches Switch#, and if the setup final menu appears again choose 0 and never choose 2, then rerun Setup Console."
            return "Verify the saved enable secret, manually confirm enable reaches Switch#, then rerun Setup Console."
        if self.final_menu_seen:
            return "On the console, choose 0 at the setup final menu, never choose 2, wait for an IOS CLI prompt, then rerun Setup Console."
        if self.forced_setup_wizard_detected:
            return "On the console, complete the remaining setup prompts without saving the wizard config, choose 0 at the final menu, never choose 2, then rerun Setup Console."
        if self.initial_dialog_answered_no:
            return "Press RETURN on the console until the IOS CLI prompt appears; if setup prompts continue, use a policy-compliant enable secret, choose 0 at the final menu, and never choose 2."
        if prompt_type == "rommon":
            return "Boot IOS from ROMMON or recover the switch image, then rerun Setup Console after an IOS prompt appears."
        return "Connect to the console, press RETURN, note the visible prompt, and rerun Setup Console after the switch shows Switch#."

    def reach_privileged_exec(self, config: dict[str, Any]) -> tuple[str, str, list[str]]:
        latest = self.client.read_prompt()
        output = latest
        prompt_type, _score = detect_cisco_prompt(latest)
        self._record_prompt(prompt_type)
        if not output.strip():
            self.steps.append("No console output was received after pressing RETURN.")

        username = str(config.get("username") or "admin").strip()
        password = str(config.get("password") or "")
        console_password = str(config.get("console_password") or password)
        enable_password = str(config.get("enable_secret") or config.get("enable_password") or "")
        wizard_password = str(config.get("wizard_password") or enable_password or password)

        for _ in range(self.max_transitions):
            self._record_prompt(prompt_type)
            if prompt_type == "privileged":
                break
            if prompt_type == "press_return":
                self._set_action("pressed RETURN at Cisco start prompt", "Detected Cisco start prompt; pressed RETURN.")
                latest = self.client.run_command("")
            elif prompt_type == "initial_dialog":
                self.initial_dialog_answered_no = True
                self._set_action("answered initial configuration dialog no", "Detected Cisco initial configuration dialog; answered no.")
                latest = self.client.run_command("no")
            elif prompt_type == "autoinstall_terminate":
                self._set_action("accepted autoinstall termination", "Detected Cisco autoinstall prompt; accepted termination.")
                latest = self.client.run_command("yes")
            elif prompt_type == "setup_yes_no":
                self._set_action("answered setup wizard yes/no prompt no", "Detected Cisco setup wizard yes/no prompt; answered no.")
                latest = self.client.run_command("no")
            elif prompt_type == "setup_password_policy_failure":
                self._set_action(
                    "stopped after Cisco reported a password policy failure",
                    "Detected Cisco setup wizard password policy failure; stopped before retrying the same secret.",
                )
                break
            elif prompt_type in {"setup_enable_secret", "setup_password"} and wizard_password:
                self._set_action("sent fallback wizard secret", "Detected Cisco setup wizard password prompt; sent fallback wizard secret.")
                latest = self.client.run_command(wizard_password, redact=True)
            elif prompt_type == "setup_final_menu":
                self._set_action(
                    "selected 0 at setup wizard final menu",
                    "Detected Cisco setup wizard final menu; selected IOS command prompt without saving wizard config.",
                )
                latest = self.client.run_command("0")
            elif prompt_type in {"config", "interface_config"}:
                self._set_action("sent end from configuration prompt", "Detected Cisco configuration prompt; returned to privileged EXEC.")
                latest = self.client.run_command("end")
            elif prompt_type in {"username", "login"} and username:
                self._set_action("sent configured username", "Detected Cisco login prompt; sent configured username.")
                latest = self.client.run_command(username, redact=False)
            elif prompt_type == "password" and console_password:
                self._set_action("sent configured console password", "Detected Cisco password prompt; sent configured console password.")
                latest = self.client.run_command(console_password, redact=True)
            elif prompt_type == "user_exec":
                self.user_exec_reached = True
                self._set_action("sent enable from user EXEC prompt", "Detected Cisco user EXEC prompt; entered enable mode.")
                latest = self.client.run_command("enable")
                if re.search(r"(?im)Password:\s*$", latest) and enable_password:
                    self.enable_password_prompt_reached = True
                    self._set_action("sent configured enable secret", "Detected enable password prompt; sent configured enable secret.")
                    output += latest
                    latest = self.client.run_command(enable_password, redact=True)
                elif re.search(r"(?im)Password:\s*$", latest):
                    self.enable_password_prompt_reached = True
                    self.last_prompt_type = "enable_password_required"
                    self._set_action(
                        "stopped at enable password prompt",
                        "Detected enable password prompt, but no enable secret was available.",
                    )
                    output += latest
                    prompt_type = "enable_password_required"
                    break
            else:
                break
            output += latest
            prompt_type, _score = detect_cisco_prompt(latest)
            if not prompt_type and not latest.strip():
                self._set_action(
                    "pressed RETURN to continue reading console output",
                    "No console output followed the last action; pressed RETURN to continue reading.",
                )
                latest = self.client.run_command("")
                output += latest
                prompt_type, _score = detect_cisco_prompt(latest)
            self._record_prompt(prompt_type)
        else:
            self.steps.append("Console bootstrap reached the state transition limit before privileged EXEC.")

        if not prompt_type:
            prompt_type = "timeout" if not output.strip() else "unknown"
            if prompt_type == "timeout":
                self.steps.append("Console state is timeout: no prompt was readable.")
            else:
                self.steps.append("Console state is unknown: output did not match a supported Cisco prompt.")
        self._record_prompt(prompt_type)
        return prompt_type, output, list(self.steps)


def format_cisco_privileged_exec_failure(diagnostics: dict[str, Any]) -> str:
    def yes_no(value: Any) -> str:
        return "yes" if bool(value) else "no"

    return (
        "Cisco console did not reach privileged EXEC mode. "
        f"Last detected state: {diagnostics.get('last_detected_state') or 'unknown'}. "
        f"Last safe action: {diagnostics.get('last_safe_action') or 'none'}. "
        f"Initial dialog answered no: {yes_no(diagnostics.get('initial_dialog_answered_no'))}. "
        f"Forced setup wizard detected: {yes_no(diagnostics.get('forced_setup_wizard_detected'))}. "
        f"Final wizard menu seen: {yes_no(diagnostics.get('final_menu_seen'))}. "
        f"Switch> reached: {yes_no(diagnostics.get('switch_user_exec_reached'))}. "
        f"Password after enable reached: {yes_no(diagnostics.get('enable_password_prompt_reached'))}. "
        f"Password policy failure detected: {yes_no(diagnostics.get('password_policy_failure_detected'))}. "
        f"Next manual recovery step: {diagnostics.get('next_manual_recovery_step') or 'Connect to the console and recover the switch prompt manually.'}"
    )


class CiscoSerialClient:
    def __init__(self, port: str, baud: int = 9600, *, timeout: float = 1.0):
        serial_module, _port_tools = _load_serial_modules()
        if serial_module is None:
            raise CiscoSerialError("pyserial is required for Cisco serial access.")
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._conn: Any = None

    def __enter__(self) -> CiscoSerialClient:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def open(self) -> None:
        serial_module, _port_tools = _load_serial_modules()
        if serial_module is None:
            raise CiscoSerialError("pyserial is required for Cisco serial access.")
        self._conn = serial_module.Serial(port=self.port, baudrate=self.baud, timeout=self.timeout, write_timeout=self.timeout)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def read_prompt(self) -> str:
        self._write("\r\n")
        return self._read_for(1.5)

    def _reach_privileged_exec(self, config: dict[str, Any]) -> tuple[str, str, list[str], dict[str, Any]]:
        state_machine = CiscoConsoleBootstrapStateMachine(self)
        prompt_type, output, steps = state_machine.reach_privileged_exec(config)
        return prompt_type, output, steps, state_machine.diagnostics(prompt_type)

    def apply_management_config(self, config: dict[str, Any]) -> CiscoBootstrapResult:
        append_cisco_log("serial.bootstrap.start", port=self.port, baud=self.baud, management_ip=str(config.get("management_ip") or ""))
        prompt_type, output, steps, diagnostics = self._reach_privileged_exec(config)
        password = str(config.get("password") or "")
        console_password = str(config.get("console_password") or password)
        enable_password = str(config.get("enable_secret") or config.get("enable_password") or "")
        if prompt_type != "privileged":
            safe_output = mask_secrets(output, [password, console_password, enable_password])
            error = format_cisco_privileged_exec_failure(diagnostics)
            append_cisco_log("serial.bootstrap.no_prompt", port=self.port, baud=self.baud, output=safe_output, diagnostics=diagnostics)
            return CiscoBootstrapResult(
                ok=False,
                port=self.port,
                baud=self.baud,
                error=error,
                output=safe_output,
                steps=steps,
            )

        commands = self.build_management_commands(config, mask=False)
        attempted_commands: list[str] = []
        command_error = ""
        ok = True
        save_commands = [command for command in commands if command == "write memory"]
        config_commands = [command for command in commands if command != "write memory"]
        for command in config_commands:
            wait_seconds = 8.0 if command.startswith("crypto key generate") else 2.0
            command_output = self.run_command(command, redact="secret" in command.lower() or command == password, wait_seconds=wait_seconds)
            output += command_output
            attempted_commands.append(command)
            if re.search(r"(?im)^% ?(?:Invalid|Incomplete|Ambiguous|Error)", command_output):
                ok = False
                command_error = "Cisco rejected one or more bootstrap commands. Review raw serial output in technical details."
                break
            if re.search(r"(?im)^% Please define a domain-name first", command_output):
                ok = False
                command_error = "Cisco SSH key generation failed because the domain name was not accepted before crypto key generation."
                break
        if ok:
            for command in save_commands:
                output += self.run_command(command, redact=False, wait_seconds=8.0)
                attempted_commands.append(command)
        prompt_type, _score = detect_cisco_prompt(output)
        ok = ok and (prompt_type == "privileged" or bool(re.search(r"(?im)(?:^|\r|\n)\s*(?:Switch|Router|[\w.-]+)#\s*$", output)))
        append_cisco_log("serial.bootstrap.complete", port=self.port, baud=self.baud, ok=ok, output=output)
        return CiscoBootstrapResult(
            ok=ok,
            port=self.port,
            baud=self.baud,
            management_ip=str(config.get("management_ip") or ""),
            commands=attempted_commands,
            error="" if ok else command_error or "Cisco management bootstrap did not complete cleanly. Review raw serial output in technical details.",
            output=mask_secrets(output, [password, console_password, enable_password]),
            steps=steps,
        )

    def factory_reset(self, config: dict[str, Any]) -> CiscoBootstrapResult:
        append_cisco_log("serial.factory_reset.start", port=self.port, baud=self.baud)
        prompt_type, output, steps, diagnostics = self._reach_privileged_exec(config)
        password = str(config.get("password") or "")
        console_password = str(config.get("console_password") or password)
        enable_password = str(config.get("enable_secret") or config.get("enable_password") or "")
        if prompt_type != "privileged":
            safe_output = mask_secrets(output, [password, console_password, enable_password])
            error = format_cisco_privileged_exec_failure(diagnostics)
            append_cisco_log("serial.factory_reset.not_privileged", port=self.port, baud=self.baud, prompt_type=prompt_type, output=safe_output, diagnostics=diagnostics)
            return CiscoBootstrapResult(
                ok=False,
                port=self.port,
                baud=self.baud,
                error=error,
                output=safe_output,
                steps=steps,
            )

        commands = ["terminal length 0", "write erase", "delete /force flash:vlan.dat", "reload"]
        output += self.run_command("terminal length 0", wait_seconds=1.0)
        erase_output = self.run_command("write erase", wait_seconds=5.0)
        output += erase_output
        if re.search(r"(?i)\[confirm\]|confirm", erase_output):
            output += self.run_command("", wait_seconds=5.0)
        output += self.run_command("delete /force flash:vlan.dat", wait_seconds=4.0)
        reload_output = self.run_command("reload", wait_seconds=5.0)
        output += reload_output
        followup_output = ""
        if re.search(r"(?is)\bsave\b.*?\b(?:yes/no|y/n)\b", reload_output):
            followup_output = self.run_command("no", wait_seconds=3.0)
            output += followup_output
        elif re.search(r"(?i)\byes/no\b", reload_output):
            followup_output = self.run_command("yes", wait_seconds=3.0)
            output += followup_output
        if re.search(r"(?i)\[confirm\]|confirm", reload_output + followup_output):
            output += self.run_command("", wait_seconds=8.0)

        fatal_output = "\n".join(
            line
            for line in output.splitlines()
            if not re.search(r"(?i)(no such file|not found|unable to delete.*vlan\.dat)", line)
        )
        command_error = ""
        ok = True
        if re.search(r"(?im)^% ?(?:Invalid|Incomplete|Ambiguous|Error)", fatal_output):
            ok = False
            command_error = "Cisco rejected one or more factory reset commands. Review raw serial output in technical details."
        append_cisco_log("serial.factory_reset.complete", port=self.port, baud=self.baud, ok=ok, output=output)
        return CiscoBootstrapResult(
            ok=ok,
            port=self.port,
            baud=self.baud,
            commands=commands,
            error="" if ok else command_error or "Cisco factory reset did not complete cleanly. Review raw serial output in technical details.",
            output=mask_secrets(output, [password, console_password, enable_password]),
            steps=steps,
        )

    def run_command(self, command: str, *, redact: bool = False, wait_seconds: float = 2.0) -> str:
        self._write(str(command) + "\r\n")
        output = self._read_for(wait_seconds)
        return mask_secrets(output, [command]) if redact else output

    def _write(self, text: str) -> None:
        if self._conn is None:
            raise CiscoSerialError("Serial connection is not open.")
        self._conn.write(text.encode("utf-8"))
        self._conn.flush()

    def _read_for(self, seconds: float) -> str:
        if self._conn is None:
            raise CiscoSerialError("Serial connection is not open.")
        deadline = time.monotonic() + seconds
        chunks: list[bytes] = []
        while time.monotonic() < deadline:
            waiting = int(getattr(self._conn, "in_waiting", 0) or 0)
            chunk = self._conn.read(waiting or 256)
            if chunk:
                chunks.append(chunk)
            else:
                time.sleep(0.05)
        return b"".join(chunks).decode("utf-8", errors="replace")

    @staticmethod
    def build_management_commands(config: dict[str, Any], *, mask: bool = True) -> list[str]:
        hostname = str(config.get("hostname") or "Switch").strip()
        management_vlan = str(config.get("management_vlan") or "1").strip()
        management_ip = str(config.get("management_ip") or "").strip()
        subnet_mask = str(config.get("subnet_mask") or "").strip()
        gateway = str(config.get("gateway") or "").strip()
        domain_name = str(config.get("domain_name") or "lab.local").strip()
        username = str(config.get("username") or "admin").strip()
        password = str(config.get("password") or "").strip()
        enable_secret = str(config.get("enable_secret") or config.get("enable_password") or "").strip()
        dns_servers = [str(item).strip() for item in list(config.get("dns_servers") or []) if str(item).strip()]
        dns1 = str(config.get("dns1") or (dns_servers[0] if dns_servers else "")).strip()
        management_port = normalize_interface_name(
            str(config.get("management_port") or config.get("bootstrap_network_port") or "").strip()
        )
        management_port_mode = str(
            config.get("management_port_mode") or config.get("bootstrap_network_mode") or "do_not_touch"
        ).strip().lower()
        if management_port_mode not in {"access", "trunk", "do_not_touch"}:
            management_port_mode = "do_not_touch"
        secret = SECRET_MASK if mask and password else password
        enable_secret_value = SECRET_MASK if mask and enable_secret else enable_secret
        commands = [
            "enable",
            "configure terminal",
            f"hostname {hostname}",
            f"vlan {management_vlan}",
            "name MGMT",
            "exit",
        ]
        if management_port and management_port_mode == "access":
            commands.extend(
                [
                    f"interface {management_port}",
                    "description LAB BUILDER MANAGEMENT",
                    "switchport mode access",
                    f"switchport access vlan {management_vlan}",
                    "spanning-tree portfast",
                    "no shutdown",
                    "exit",
                ]
            )
        elif management_port and management_port_mode == "trunk" and bool(config.get("trunk_review_ack")):
            commands.extend(
                [
                    f"interface {management_port}",
                    "description LAB BUILDER MANAGEMENT",
                    "switchport mode trunk",
                    f"switchport trunk allowed vlan add {management_vlan}",
                    "no shutdown",
                    "exit",
                ]
            )
        commands.extend(
            [
                f"interface vlan {management_vlan}",
                f"ip address {management_ip} {subnet_mask}",
                "no shutdown",
                "exit",
                f"ip default-gateway {gateway}",
                f"ip domain name {domain_name}",
            ]
        )
        if dns1:
            commands.append(f"ip name-server {dns1}")
        commands.extend(
            [
                "ip ssh version 2",
                "crypto key generate rsa modulus 4096",
            ]
        )
        if enable_secret:
            commands.append(f"enable secret {enable_secret_value}")
        commands.extend(
            [
                f"username {username} privilege 15 secret {secret}",
                "line con 0",
                "logging synchronous",
                "login local",
                "exit",
                "line vty 0 15",
                "logging synchronous",
                "login local",
                "transport input ssh",
                "exit",
                "ip scp server enable",
                "end",
                "write memory",
            ]
        )
        return commands


class CiscoSSHClient:
    def __init__(self, host: str, username: str, password: str, *, timeout: int = 12):
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout

    def test_reachability(self) -> dict[str, Any]:
        if not self.host:
            raise CiscoSSHError("Cisco management IP is not set.")
        if not self.username or not self.password:
            raise CiscoSSHError("Cisco SSH credentials are incomplete.")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                self.host,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            shell = client.invoke_shell()
            shell.settimeout(1.0)
            output_chunks: list[str] = []
            time.sleep(0.5)
            if shell.recv_ready():
                output_chunks.append(shell.recv(65535).decode("utf-8", errors="replace"))
            for command in ("terminal length 0", "show version"):
                shell.send(command + "\n")
                deadline = time.time() + self.timeout
                command_output = ""
                while time.time() < deadline:
                    if shell.recv_ready():
                        chunk = shell.recv(65535).decode("utf-8", errors="replace")
                        command_output += chunk
                        if re.search(r"(?m)[\r\n][A-Za-z0-9_.-]+[#>] ?$", command_output):
                            break
                    time.sleep(0.2)
                output_chunks.append(command_output)
            return {"ok": True, "host": self.host, "output": mask_secrets("\n".join(output_chunks), [self.password]), "error": ""}
        except Exception as exc:
            raise CiscoSSHError(str(exc).splitlines()[0]) from exc
        finally:
            client.close()

    def run_commands(self, commands: list[str]) -> dict[str, Any]:
        if not self.host:
            raise CiscoSSHError("Cisco management IP is not set.")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        output_chunks: list[str] = []
        try:
            client.connect(
                self.host,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            shell = client.invoke_shell()
            time.sleep(0.5)
            if shell.recv_ready():
                output_chunks.append(shell.recv(65535).decode("utf-8", errors="replace"))
            for command in commands:
                shell.send(str(command).rstrip() + "\n")
                time.sleep(0.2)
                if shell.recv_ready():
                    output_chunks.append(shell.recv(65535).decode("utf-8", errors="replace"))
                if str(command).strip().lower() in {"copy run start", "copy running-config startup-config"}:
                    shell.send("\n")
                    time.sleep(0.5)
                    if shell.recv_ready():
                        output_chunks.append(shell.recv(65535).decode("utf-8", errors="replace"))
            return {"ok": True, "host": self.host, "output": mask_secrets("\n".join(output_chunks), [self.password])}
        except Exception as exc:
            raise CiscoSSHError(str(exc).splitlines()[0]) from exc
        finally:
            client.close()


def render_management_config(config: dict[str, Any]) -> str:
    return "\n".join(CiscoSerialClient.build_management_commands(config, mask=True))


def discovery_candidates_payload(candidates: list[CiscoSerialCandidate], *, include_raw: bool = False) -> list[dict[str, Any]]:
    return [candidate.as_dict(include_raw=include_raw) for candidate in candidates]


def serial_ports_available() -> bool:
    return bool(_ordered_port_paths(_port_metadata()) or any(Path("/dev").glob("ttyUSB*")) or any(Path("/dev").glob("ttyACM*")))


def default_cisco_switch_config() -> dict[str, Any]:
    return {
        "hostname": "sw01",
        "ip": "",
        "username": "admin",
        "password": "",
        "console_password": "",
        "enable_password": "",
        "connection_method": "auto",
        "console_port": "",
        "console_baud": 9600,
        "trusted_console_adapter": False,
        "domain_name": "example.local",
        "dns_servers": ["192.168.1.1"],
        "ntp_servers": ["192.168.1.1"],
        "management_vlan": 10,
        "management_ip": "",
        "subnet_mask": "255.255.255.0",
        "gateway": "",
        "management_port": "GigabitEthernet1/0/1",
        "management_port_mode": "do_not_touch",
        "bootstrap_network_port": "",
        "bootstrap_network_mode": "trunk",
        "vlans": [dict(item) for item in DEFAULT_CISCO_VLANS],
        "port_profiles": {key: dict(value) for key, value in DEFAULT_CISCO_PORT_PROFILES.items()},
        "ports": {},
        "custom_global_commands": [],
        "custom_port_commands": {},
        "apply_mode": "initial_install",
        "discovery": {"prefer_console": True, "allow_network_scan": True},
    }


def normalize_cisco_switch_config(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = dict((cfg.get("cisco_switch") if "cisco_switch" in cfg else cfg) or {})
    base = default_cisco_switch_config()
    for key, value in raw.items():
        if value not in (None,):
            base[key] = value
    base["management_ip"] = str(base.get("management_ip") or base.get("ip") or "").strip()
    base["gateway"] = str(base.get("gateway") or "").strip()
    base["enable_secret"] = str(base.get("enable_secret") or base.get("enable_password") or "")
    base["enable_password"] = str(base.get("enable_password") or base.get("enable_secret") or "")
    base["console_password"] = str(base.get("console_password") or "")
    base["trusted_console_adapter"] = bool(base.get("trusted_console_adapter"))
    if "management_port" not in raw and str(raw.get("bootstrap_network_port") or "").strip():
        base["management_port"] = str(raw.get("bootstrap_network_port") or "").strip()
    base["management_port"] = normalize_interface_name(str(base.get("management_port") or "GigabitEthernet1/0/1").strip())
    if "management_port_mode" not in raw and str(raw.get("bootstrap_network_mode") or "").strip():
        base["management_port_mode"] = str(raw.get("bootstrap_network_mode") or "").strip()
    base["management_port_mode"] = str(base.get("management_port_mode") or "do_not_touch").strip().lower()
    if base["management_port_mode"] not in {"access", "trunk", "do_not_touch"}:
        base["management_port_mode"] = "do_not_touch"
    base["bootstrap_network_port"] = str(base.get("bootstrap_network_port") or "").strip()
    base["bootstrap_network_mode"] = str(base.get("bootstrap_network_mode") or base.get("management_port_mode") or "trunk").strip()
    profiles = {key: dict(value) for key, value in DEFAULT_CISCO_PORT_PROFILES.items()}
    for name, profile in dict(raw.get("port_profiles") or {}).items():
        merged = dict(profiles.get(str(name), DEFAULT_CISCO_PORT_PROFILES["custom"]))
        merged.update(dict(profile or {}))
        profiles[str(name)] = merged
    base["port_profiles"] = profiles
    base["ports"] = {normalize_interface_name(name): dict(value or {}) for name, value in dict(raw.get("ports") or {}).items()}
    base["custom_port_commands"] = {normalize_interface_name(name): dict(value or {}) for name, value in dict(raw.get("custom_port_commands") or {}).items()}
    base["dns_servers"] = [str(item).strip() for item in list(base.get("dns_servers") or []) if str(item).strip()]
    base["ntp_servers"] = [str(item).strip() for item in list(base.get("ntp_servers") or []) if str(item).strip()]
    base["custom_global_commands"] = [str(item).strip() for item in list(base.get("custom_global_commands") or []) if str(item).strip()]
    if not raw.get("ports"):
        base["ports"] = {f"GigabitEthernet1/0/{index}": {"profile": "client_device"} for index in range(1, 25)}
    return base


def normalize_interface_name(name: str) -> str:
    text = str(name or "").strip()
    match = re.match(r"^([A-Za-z][A-Za-z-]*)(.+)$", text)
    if not match:
        return text
    prefix, suffix = match.groups()
    full_prefix = INTERFACE_PREFIXES.get(prefix.lower(), prefix)
    return f"{full_prefix}{suffix}"


def short_interface_name(name: str) -> str:
    full = normalize_interface_name(name)
    for prefix, short in INTERFACE_SHORT_PREFIXES.items():
        if full.startswith(prefix):
            return short + full[len(prefix) :]
    return full


def interface_sort_key(name: str) -> tuple[str, list[int], str]:
    full = normalize_interface_name(name)
    match = re.match(r"^([A-Za-z][A-Za-z-]*)(.*)$", full)
    if not match:
        return (full, [], full)
    prefix, suffix = match.groups()
    numbers = [int(item) for item in re.findall(r"\d+", suffix)]
    return (prefix, numbers, full)


def expand_interface_key(key: str) -> list[str]:
    interfaces: list[str] = []
    for token in [item.strip() for item in str(key or "").split(",") if item.strip()]:
        match = re.match(r"^([A-Za-z][A-Za-z-]*[\d/]*?/)(\d+)-(\d+)$", token)
        if match:
            prefix, start, end = match.groups()
            for index in range(int(start), int(end) + 1):
                interfaces.append(normalize_interface_name(f"{prefix}{index}"))
        else:
            interfaces.append(normalize_interface_name(token))
    return interfaces


def expand_port_definitions(ports: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    expanded: dict[str, dict[str, Any]] = {}
    overlaps: list[str] = []
    for raw_name, settings in dict(ports or {}).items():
        for interface in expand_interface_key(str(raw_name)):
            if interface in expanded:
                overlaps.append(interface)
            expanded[interface] = dict(settings or {})
    return expanded, sorted(set(overlaps), key=interface_sort_key)


def resolve_port_config(cisco_cfg: dict[str, Any], interface: str) -> dict[str, Any]:
    cfg = normalize_cisco_switch_config(cisco_cfg)
    port = dict(cfg.get("ports", {}).get(normalize_interface_name(interface)) or {})
    profile_name = str(port.get("profile") or "custom")
    profile = dict(cfg.get("port_profiles", {}).get(profile_name) or cfg.get("port_profiles", {}).get("custom") or {})
    resolved = dict(profile)
    for key in CISCO_PROFILE_FIELDS:
        if key in port:
            resolved[key] = port[key]
    resolved["profile"] = profile_name
    resolved["interface"] = normalize_interface_name(interface)
    resolved["has_overrides"] = any(key in port for key in CISCO_PROFILE_FIELDS)
    return resolved


def port_map_rows(cisco_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = normalize_cisco_switch_config(cisco_cfg)
    rows: list[dict[str, Any]] = []
    for interface in sorted(cfg.get("ports", {}), key=interface_sort_key):
        resolved = resolve_port_config(cfg, interface)
        mode = str(resolved.get("mode") or "access")
        vlan = ",".join(str(item) for item in list(resolved.get("allowed_vlans") or [])) if mode == "trunk" else str(resolved.get("access_vlan") or "")
        rows.append(
            {
                "port": interface,
                "short_port": short_interface_name(interface),
                "profile": str(resolved.get("profile") or ""),
                "profile_label": str(resolved.get("profile") or "").replace("_", " ").title(),
                "vlan": "Trunk" if mode == "trunk" else vlan,
                "enabled": bool(resolved.get("enabled", True)),
                "description": str(resolved.get("description") or ""),
                "has_overrides": bool(resolved.get("has_overrides")),
            }
        )
    return rows


def parse_show_interfaces_status(output: str) -> dict[str, dict[str, Any]]:
    interfaces: dict[str, dict[str, Any]] = {}
    for line in str(output or "").splitlines():
        if not line.strip() or line.lower().startswith("port ") or line.startswith("-"):
            continue
        match = re.match(r"^(\S+)\s+(.+?)\s+(connected|notconnect|disabled|err-disabled|inactive)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        port, name, status, vlan, duplex, speed, iface_type = match.groups()
        full = normalize_interface_name(port)
        interfaces.setdefault(full, {})
        interfaces[full].update({"name": name.strip(), "status": status.lower(), "vlan": vlan, "duplex": duplex, "speed": speed, "type": iface_type.strip()})
    return interfaces


def parse_show_ip_interface_brief(output: str) -> dict[str, dict[str, Any]]:
    interfaces: dict[str, dict[str, Any]] = {}
    for line in str(output or "").splitlines():
        if not line.strip() or line.lower().startswith("interface "):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        interface = normalize_interface_name(parts[0])
        interfaces.setdefault(interface, {})
        interfaces[interface].update({"ip_address": parts[1], "method": parts[2], "protocol": parts[-1], "admin_status": " ".join(parts[4:-1])})
    return interfaces


def parse_running_config_interfaces(output: str) -> dict[str, dict[str, Any]]:
    interfaces: dict[str, dict[str, Any]] = {}
    current = ""
    commands: list[str] = []
    for line in str(output or "").splitlines():
        interface_match = re.match(r"^interface\s+(\S+)", line.strip(), flags=re.IGNORECASE)
        if interface_match:
            if current:
                interfaces[current] = {"commands": commands}
            current = normalize_interface_name(interface_match.group(1))
            commands = []
            continue
        if current and line.strip() and line.strip() != "!":
            commands.append(line.strip())
    if current:
        interfaces[current] = {"commands": commands}
    for name, data in interfaces.items():
        commands = list(data.get("commands") or [])
        data["shutdown"] = "shutdown" in commands and "no shutdown" not in commands
        ip_cmd = next((item for item in commands if item.startswith("ip address ")), "")
        if ip_cmd:
            parts = ip_cmd.split()
            if len(parts) >= 4:
                data["ip_address"] = parts[2]
                data["subnet_mask"] = parts[3]
        description = next((item.removeprefix("description ").strip() for item in commands if item.startswith("description ")), "")
        if description:
            data["description"] = description
    return interfaces


def parse_cisco_discovery_outputs(show_interfaces_status: str = "", show_ip_interface_brief: str = "", running_config_interfaces: str = "") -> dict[str, Any]:
    interfaces: dict[str, dict[str, Any]] = {}
    for parsed in (parse_show_interfaces_status(show_interfaces_status), parse_show_ip_interface_brief(show_ip_interface_brief), parse_running_config_interfaces(running_config_interfaces)):
        for name, data in parsed.items():
            interfaces.setdefault(name, {}).update(data)
    return {"interfaces": dict(sorted(interfaces.items(), key=lambda item: interface_sort_key(item[0])))}


def _bool_command(enabled: bool, positive: str, negative: str) -> str:
    return positive if bool(enabled) else negative


def _vlan_list(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ",".join(str(item) for item in list(value or []) if str(item).strip())


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text:
                    return text
        else:
            text = str(value or "").strip()
            if text:
                return text
    return ""


def _primary_snmp_user(cisco_cfg: dict[str, Any]) -> dict[str, Any]:
    snmp_cfg = dict(cisco_cfg.get("snmp") or {})
    users = list(snmp_cfg.get("users") or [])
    if users and isinstance(users[0], dict):
        primary = dict(users[0])
        primary.setdefault("v3_username", primary.get("username"))
        primary.setdefault("v3_auth_protocol", primary.get("auth_protocol"))
        primary.setdefault("v3_auth_password", primary.get("auth_password"))
        primary.setdefault("v3_priv_protocol", primary.get("priv_protocol"))
        primary.setdefault("v3_priv_password", primary.get("priv_password"))
        return {**snmp_cfg, **primary}
    return snmp_cfg


def _snmp_priv_keyword(protocol: str) -> str:
    text = str(protocol or "AES").strip().lower()
    return "aes 128" if text == "aes" else text


def _management_vlan_name(cisco_cfg: dict[str, Any]) -> str:
    management_vlan = int(cisco_cfg.get("management_vlan") or 0)
    for vlan in list(cisco_cfg.get("vlans") or []):
        try:
            if int(dict(vlan).get("id") or 0) == management_vlan:
                return str(dict(vlan).get("name") or "DWAN").strip() or "DWAN"
        except (TypeError, ValueError):
            continue
    return "DWAN"


def _port_body_commands(cisco_cfg: dict[str, Any], interface: str) -> list[str]:
    cfg = normalize_cisco_switch_config(cisco_cfg)
    resolved = resolve_port_config(cfg, interface)
    commands: list[str] = []
    description = str(resolved.get("description") or "").strip()
    if description:
        commands.append(f"description {description}")
    mode = str(resolved.get("mode") or "access").strip().lower()
    commands.append("switchport")
    if mode == "trunk":
        commands.append("switchport mode trunk")
        allowed = _vlan_list(resolved.get("allowed_vlans"))
        if allowed:
            commands.append(f"switchport trunk allowed vlan {allowed}")
        native_vlan = str(resolved.get("native_vlan") or "").strip()
        if native_vlan:
            commands.append(f"switchport trunk native vlan {native_vlan}")
    else:
        commands.append("switchport mode access")
        commands.append(f"switchport access vlan {resolved.get('access_vlan') or 1}")
    commands.append(_bool_command(resolved.get("cdp", False), "cdp enable", "no cdp enable"))
    if bool(resolved.get("lldp", True)):
        commands.extend(["lldp transmit", "lldp receive"])
    else:
        commands.extend(["no lldp transmit", "no lldp receive"])
    commands.append(_bool_command(resolved.get("snmp_trap_link_status", True), "snmp trap link-status", "no snmp trap link-status"))
    if bool(resolved.get("spanning_tree_portfast", False)):
        commands.append("spanning-tree portfast trunk" if mode == "trunk" else "spanning-tree portfast")
    else:
        commands.append("no spanning-tree portfast")
    commands.append(_bool_command(resolved.get("bpduguard", False), "spanning-tree bpduguard enable", "spanning-tree bpduguard disable"))
    commands.extend([str(item).strip() for item in list(resolved.get("extra_commands") or []) if str(item).strip()])
    after_profile = list((cfg.get("custom_port_commands", {}).get(normalize_interface_name(interface)) or {}).get("after_profile") or [])
    commands.extend([str(item).strip() for item in after_profile if str(item).strip()])
    commands.append("no shutdown" if bool(resolved.get("enabled", True)) else "shutdown")
    return commands


def render_cisco_baseline_config(cfg: dict[str, Any], *, mask: bool = True) -> str:
    cisco_cfg = normalize_cisco_switch_config(cfg)
    hostname = str(cisco_cfg.get("hostname") or "sw01").strip()
    domain_name = str(cisco_cfg.get("domain_name") or "example.local").strip()
    management_vlan = int(cisco_cfg.get("management_vlan") or 10)
    username = str(cisco_cfg.get("username") or "admin").strip()
    password = str(cisco_cfg.get("password") or "").strip()
    secret_value = SECRET_MASK if mask and password else password
    lines = [
        "terminal length 0",
        "configure terminal",
        f"hostname {hostname}",
        "lldp run",
        "no ip domain lookup",
    ]
    for server in cisco_cfg.get("dns_servers") or []:
        lines.append(f"ip name-server {server}")
    lines.extend(
        [
            f"ip domain name {domain_name}",
            "crypto key generate rsa modulus 4096",
            "ip ssh version 2",
        ]
    )
    if username and password:
        lines.append(f"username {username} privilege 15 algorithm-type sha256 secret {secret_value}")
    for extra_user in list(cisco_cfg.get("additional_local_users") or ["ESBAccess", "LocalTech"]):
        extra_username = str(dict(extra_user).get("username") if isinstance(extra_user, dict) else extra_user or "").strip()
        extra_password = str(dict(extra_user).get("password") if isinstance(extra_user, dict) else password or "").strip()
        if extra_username and extra_password:
            lines.append(f"username {extra_username} privilege 15 algorithm-type sha256 secret {SECRET_MASK if mask else extra_password}")
    if cisco_cfg.get("gateway"):
        lines.append(f"ip default-gateway {cisco_cfg.get('gateway')}")
    lines.extend(
        [
            "no ip http server",
            "no ip http secure-server",
            "line con 0",
            "logging synchronous",
            "login local",
            "length 100",
            "stopbits 1",
            "line vty 0 15",
            "logging synchronous",
            "login local",
            "length 100",
            "transport preferred ssh",
            "transport input ssh",
        ]
    )
    for server in cisco_cfg.get("ntp_servers") or []:
        lines.append(f"ntp server {server}")
    lines.extend(
        [
            "service sequence-numbers",
            "service timestamps debug datetime localtime show-timezone msec",
            "service timestamps log datetime localtime show-timezone msec",
        ]
    )
    logging_host = _first_nonempty(cisco_cfg.get("logging_hosts"), cisco_cfg.get("logging_host"), cisco_cfg.get("syslog_host"), cisco_cfg.get("gateway"))
    if logging_host:
        lines.append(f"logging host {logging_host}")
    lines.extend(
        [
            "logging trap informational",
            "logging history informational",
            "logging monitor informational",
            "logging origin-id hostname",
            "logging origin-id ip",
            f"logging source-interface vlan {management_vlan}",
            "logging userinfo",
            "logging on",
        ]
    )
    snmp_cfg = _primary_snmp_user(cisco_cfg)
    snmp_user = str(snmp_cfg.get("v3_username") or "").strip()
    snmp_auth_protocol = str(snmp_cfg.get("v3_auth_protocol") or "SHA").strip().lower() or "sha"
    snmp_auth_password = str(snmp_cfg.get("v3_auth_password") or "").strip()
    snmp_priv_keyword = _snmp_priv_keyword(str(snmp_cfg.get("v3_priv_protocol") or "AES"))
    snmp_priv_password = str(snmp_cfg.get("v3_priv_password") or "").strip()
    snmp_view = str(snmp_cfg.get("view") or snmp_cfg.get("v3_view") or snmp_user or "").strip()
    snmp_group = str(snmp_cfg.get("group") or snmp_cfg.get("v3_group") or snmp_user or "").strip()
    snmp_host = _first_nonempty(snmp_cfg.get("host"), snmp_cfg.get("hosts"), snmp_cfg.get("alert_destinations"), cisco_cfg.get("snmp_hosts"), logging_host)
    if snmp_user:
        lines.append(f"snmp-server view {snmp_view} iso included")
        lines.append(f"snmp-server group {snmp_group} v3 priv write {snmp_view}")
        if snmp_auth_password and snmp_priv_password:
            lines.append(
                "snmp-server user "
                f"{snmp_user} {snmp_group} v3 auth {snmp_auth_protocol} "
                f"{SECRET_MASK if mask else snmp_auth_password} priv {snmp_priv_keyword} "
                f"{SECRET_MASK if mask else snmp_priv_password}"
            )
        if snmp_host:
            lines.append(f"snmp-server host {snmp_host} informs version 3 priv {snmp_user}")
        lines.extend(["snmp-server inform retries 3", "snmp-server inform timeout 180 pending 100"])
    banner_description = str(cisco_cfg.get("banner_description") or cisco_cfg.get("description") or "").strip()
    banner_location = str(cisco_cfg.get("banner_location") or cisco_cfg.get("location") or "Deployed").strip()
    banner_serial = str(cisco_cfg.get("serial_number") or cisco_cfg.get("last_discovered_serial") or "Serial number of device").strip()
    banner_asset = str(cisco_cfg.get("asset_number") or "N/A").strip()
    lines.extend(
        [
            "banner motd $",
            "",
            f"    Hostname: {hostname}",
            f"    Description: {banner_description}",
            f"    Location: {banner_location}",
            f"    Serial Number:  {banner_serial}",
            f"    Asset Number: {banner_asset}",
            "",
            "$",
            "banner login $",
            "#########################################################################",
            "#                                                                       #",
            "#     This equipment is the property of the Canadian Government         #",
            "#                   Department of National Defence                      #",
            "#     Cette equipement est la propriete du Gouvernement Canadien        #",
            "#                 Departement de la Defense Nationale                   #",
            "#                                                                       #",
            "#               Unauthorized access is NOT permitted.                   #",
            "#            Utilisation NON permise n'est pas permise.                #",
            "#                                                                       #",
            "#        Any questions or problems / Questions ou problemes:            #",
            "#        Network Admin/Admin Reseau          tel-613-943-6345           #",
            "#        DEFSOC Service Desk/Centre d'Aide    tel-613-945-7777           #",
            "#                                                                       #",
            "#########################################################################",
            "$",
        ]
    )
    for command in cisco_cfg.get("custom_global_commands") or []:
        lines.append(str(command))
    rendered = "\n".join(lines).strip()
    secrets = [password, snmp_auth_password, snmp_priv_password]
    for extra_user in list(cisco_cfg.get("additional_local_users") or []):
        if isinstance(extra_user, dict):
            secrets.append(str(extra_user.get("password") or ""))
    return mask_secrets(rendered, secrets) if mask else rendered


def render_cisco_vlan_config(cfg: dict[str, Any]) -> str:
    cisco_cfg = normalize_cisco_switch_config(cfg)
    management_vlan = int(cisco_cfg.get("management_vlan") or 0)
    management_name = _management_vlan_name(cisco_cfg)
    lines: list[str] = [
        "interface vlan1",
        "description VLAN1 - DO NOT USE",
        "shutdown",
        "exit",
    ]
    rendered_vlans: set[int] = set()
    for vlan in sorted(list(cisco_cfg.get("vlans") or []), key=lambda item: int(item.get("id") or 0)):
        vlan_id = int(vlan.get("id") or 0)
        if not vlan_id or vlan_id in {1, 999} or vlan_id == management_vlan:
            continue
        lines.extend([f"vlan {vlan_id}", f" name {vlan.get('name') or f'VLAN{vlan_id}'}"])
        rendered_vlans.add(vlan_id)
    if management_vlan:
        lines.extend([f"vlan {management_vlan}", f"name {management_name}", "exit", f"interface vlan{management_vlan}"])
        description = str(cisco_cfg.get("management_svi_description") or f"VLAN{management_vlan} - {management_name}").strip()
        if description:
            lines.append(f"description {description}")
        if cisco_cfg.get("management_ip") and cisco_cfg.get("subnet_mask"):
            lines.append(f"ip address {cisco_cfg.get('management_ip')} {cisco_cfg.get('subnet_mask')}")
        lines.extend(["no shutdown", "exit"])
    if 999 not in rendered_vlans:
        lines.extend(["vlan 999", "name BLACK-HOLE", "shutdown", "exit"])
    lines.extend(["interface vlan999", "description VLAN999 - Black Hole VLAN", "shutdown"])
    return "\n".join(lines).strip()


def _is_overridden(cisco_cfg: dict[str, Any], interface: str) -> bool:
    port = dict(normalize_cisco_switch_config(cisco_cfg).get("ports", {}).get(normalize_interface_name(interface)) or {})
    return any(key in port for key in CISCO_PROFILE_FIELDS)


def _render_interface_block(header: str, body: list[str]) -> str:
    return "\n".join([header] + [f" {command}" for command in body])


def render_cisco_port_config(cfg: dict[str, Any], *, selected_ports: list[str] | None = None) -> str:
    cisco_cfg = normalize_cisco_switch_config(cfg)
    ports = dict(cisco_cfg.get("ports") or {})
    names = [normalize_interface_name(item) for item in (selected_ports or ports.keys()) if normalize_interface_name(item) in ports]
    names = sorted(names, key=interface_sort_key)
    groups: dict[tuple[str, ...], list[str]] = {}
    blocks: list[str] = []
    for interface in names:
        body = _port_body_commands(cisco_cfg, interface)
        if _is_overridden(cisco_cfg, interface):
            blocks.append(_render_interface_block(f"interface {interface}", body))
        else:
            groups.setdefault(tuple(body), []).append(interface)
    for body, interfaces in groups.items():
        if len(interfaces) > 1:
            blocks.append(_render_interface_block("interface range " + ", ".join(interfaces), list(body)))
        elif interfaces:
            blocks.append(_render_interface_block(f"interface {interfaces[0]}", list(body)))
    return "\n!\n".join(blocks).strip()


def render_cisco_standard_access_config(cfg: dict[str, Any]) -> str:
    cisco_cfg = normalize_cisco_switch_config(cfg)
    access_vlan = int(cisco_cfg.get("management_vlan") or 10)
    return "\n!\n".join(
        [
            _render_interface_block(
                "interface range GigabitEthernet1/0/2 - 24",
                [
                    "description UNUSED",
                    f"switchport access vlan {access_vlan}",
                    "switchport mode access",
                    "no snmp trap link-status",
                    "no cdp enable",
                    "spanning-tree portfast",
                    "spanning-tree bpduguard enable",
                    "shutdown",
                    "exit",
                ],
            ),
            _render_interface_block(
                "interface range GigabitEthernet1/1/1 - 4",
                [
                    "description UNUSED",
                    "switchport access vlan 999",
                    "switchport mode access",
                    "no snmp trap link-status",
                    "no cdp enable",
                    "spanning-tree portfast",
                    "spanning-tree bpduguard enable",
                    "shutdown",
                    "exit",
                ],
            ),
            _render_interface_block(
                "interface range TenGigabitEthernet1/1/1 - 4",
                [
                    "description UNUSED",
                    "switchport access vlan 999",
                    "switchport mode access",
                    "no snmp trap link-status",
                    "no cdp enable",
                    "spanning-tree portfast",
                    "spanning-tree bpduguard enable",
                    "shutdown",
                    "exit",
                ],
            ),
            _render_interface_block(
                "interface GigabitEthernet1/0/1",
                [
                    "switchport",
                    "switchport mode access",
                    f"switchport access vlan {access_vlan}",
                    "description Client Device",
                    "snmp trap link-status",
                    "cdp enable",
                    "no spanning-tree portfast",
                    "no spanning-tree bpduguard enable",
                    "no shutdown",
                    "exit",
                ],
            ),
            _render_interface_block(
                "interface range GigabitEthernet1/0/2-24",
                [
                    "switchport",
                    "switchport mode access",
                    f"switchport access vlan {access_vlan}",
                    "description Client Device",
                    "snmp trap link-status",
                    "no cdp enable",
                    "spanning-tree portfast",
                    "spanning-tree bpduguard enable",
                    "no shutdown",
                    "exit",
                ],
            ),
        ]
    )


def render_cisco_full_config(cfg: dict[str, Any], *, mask: bool = True) -> str:
    sections = [
        render_cisco_baseline_config(cfg, mask=mask),
        render_cisco_vlan_config(cfg),
        render_cisco_standard_access_config(cfg),
        render_cisco_port_config(cfg) if str(normalize_cisco_switch_config(cfg).get("apply_mode") or "").strip() != "initial_install" else "",
        "end",
        "copy run start",
    ]
    rendered = "\n!\n".join(section for section in sections if section.strip())
    return mask_secrets(rendered) if mask else rendered


def render_cisco_diff_preview(existing_config: str, desired_config: str) -> str:
    diff = difflib.unified_diff(
        mask_secrets(existing_config).splitlines(),
        mask_secrets(desired_config).splitlines(),
        fromfile="running-config",
        tofile="desired-config",
        lineterm="",
    )
    return "\n".join(diff)


def validate_cisco_config(cfg: dict[str, Any], *, discovery: dict[str, Any] | None = None, connection_method: str = "", ssh_uplink_port: str = "") -> dict[str, Any]:
    cisco_cfg = normalize_cisco_switch_config(cfg)
    warnings: list[str] = []
    errors: list[str] = []
    expanded, overlaps = expand_port_definitions(dict((cfg.get("cisco_switch") if "cisco_switch" in cfg else cfg).get("ports") or {}))
    if overlaps:
        errors.append("Overlapping port definitions: " + ", ".join(overlaps))

    management_vlan = int(cisco_cfg.get("management_vlan") or 0)
    management_svi = f"Vlan{management_vlan}" if management_vlan else ""
    discovered = dict(discovery or cisco_cfg.get("last_port_discovery") or {})
    interfaces = dict(discovered.get("interfaces") or {})
    svi = dict(interfaces.get(management_svi) or interfaces.get(normalize_interface_name(management_svi)) or {})
    if management_vlan and bool(svi.get("shutdown")):
        errors.append(f"Management VLAN interface {management_svi} is shutdown.")
    if not cisco_cfg.get("management_ip"):
        errors.append("Management SVI has no IP configured.")
    if not cisco_cfg.get("gateway"):
        errors.append("Management gateway is missing.")

    for interface, settings in expanded.items():
        profile_name = str((settings or {}).get("profile") or "")
        resolved = resolve_port_config(cisco_cfg, interface)
        discovered_status = str((interfaces.get(interface) or {}).get("status") or "").lower()
        if profile_name.startswith("unused") and bool(resolved.get("enabled", False)):
            errors.append(f"{interface} uses an unused profile but is enabled.")
        if profile_name.startswith("unused") and discovered_status == "connected":
            warnings.append(f"{interface} is connected but assigned to an unused profile.")

    if str(connection_method or cisco_cfg.get("connection_method") or "").lower() == "ssh" and ssh_uplink_port:
        uplink = normalize_interface_name(ssh_uplink_port)
        if uplink in expanded and not bool(resolve_port_config(cisco_cfg, uplink).get("enabled", True)):
            errors.append(f"Current SSH uplink port {uplink} would be shut down.")
    return {"ok": not errors, "errors": errors, "warnings": warnings}
