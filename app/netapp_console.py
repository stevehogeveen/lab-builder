from __future__ import annotations

from dataclasses import dataclass
import glob
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any

try:  # pyserial is optional at import time so tests can monkeypatch it.
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised only when pyserial is absent.
    serial = None
    list_ports = None


NETAPP_CONSOLE_BAUD_RATES = [115200, 9600]
NETAPP_CONSOLE_LOG_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "logs" / "netapp-console.log"
SECRET_MASK = "********"


class NetAppConsoleError(RuntimeError):
    pass


@dataclass
class NetAppConsoleCandidate:
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


def append_netapp_console_log(event: str, **fields: Any) -> None:
    NETAPP_CONSOLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"event": event, "ts": time.time(), **fields}
    try:
        with NETAPP_CONSOLE_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        pass


def mask_secrets(value: str, secrets: list[str] | None = None) -> str:
    text = str(value or "")
    for secret in [item for item in list(secrets or []) if item]:
        text = text.replace(secret, SECRET_MASK)
    return text


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


def _device_access_details(path: str) -> dict[str, Any]:
    realpath = os.path.realpath(path)
    details = {
        "path": path,
        "realpath": realpath,
        "exists": os.path.exists(realpath),
        "readable": os.access(realpath, os.R_OK),
        "writable": os.access(realpath, os.W_OK),
        "mode": "",
    }
    try:
        details["mode"] = stat.filemode(os.stat(realpath).st_mode)
    except OSError:
        pass
    return details


def serial_runtime_diagnostics() -> dict[str, Any]:
    serial_module, port_tools = _load_serial_modules()
    ordered = _ordered_port_paths(_port_metadata())
    return {
        "serial_imported": serial_module is not None,
        "list_ports_imported": port_tools is not None,
        "log_path": str(NETAPP_CONSOLE_LOG_PATH),
        "user": str(os.environ.get("USER") or ""),
        "ordered_ports": ordered,
        "by_id_ports": sorted(glob.glob("/dev/serial/by-id/*")),
        "ttyusb_ports": sorted(glob.glob("/dev/ttyUSB*")),
        "ttyacm_ports": sorted(glob.glob("/dev/ttyACM*")),
        "device_access": [_device_access_details(path) for path in ordered],
    }


def detect_netapp_console_prompt(output: str) -> tuple[str, int]:
    text = str(output or "")
    lowered = text.lower()
    score_bonus = 40 if re.search(r"(?i)\b(netapp|ontap|data ontap|clustered data ontap)\b", text) else 0

    if (
        re.search(r"(?im)(?:^|\n)\s*boot menu\b", text)
        or "selection:" in lowered and "boot menu" in lowered
        or re.search(r"(?is)Selection\s*\(1-\d+\)\?\s*$", text)
        and re.search(r"(?i)Clean configuration and initialize all disks|Normal Boot", text)
    ):
        return "boot_menu", 150 + score_bonus
    if re.search(r"(?im)\bpress ctrl-c .*boot menu\b", text):
        return "boot_interrupt", 110 + score_bonus
    if re.search(r"(?im)(?:^|\n)\s*loader[a-z0-9_.-]*>\s*$", text):
        return "loader", 95 + score_bonus
    if re.search(r"(?is)Welcome to the cluster setup wizard|Type yes to confirm and continue", text):
        return "cluster_setup", 140 + score_bonus
    if re.search(r"(?m)::>\s*$", text):
        return "cluster_cli", 130 + score_bonus
    if re.search(r"(?m)\*>\s*$", text):
        return "advanced_cli", 130 + score_bonus
    if re.search(r"(?im)(?:login|username):\s*$", text):
        return "login", 65 + score_bonus
    if re.search(r"(?im)password:\s*$", text):
        return "password", 55 + score_bonus
    if re.search(r"(?m)[\w.-]+>\s*$", text):
        return "generic_prompt", 25 + score_bonus
    return "", score_bonus


class NetAppConsoleDiscovery:
    def __init__(self, *, baud_rates: list[int] | None = None, timeout: float = 0.8, settle_seconds: float = 0.2):
        self.baud_rates = list(baud_rates or NETAPP_CONSOLE_BAUD_RATES)
        self.timeout = timeout
        self.settle_seconds = settle_seconds

    def scan(self) -> list[NetAppConsoleCandidate]:
        serial_module, _port_tools = _load_serial_modules()
        if serial_module is None:
            append_netapp_console_log("serial.discovery.unavailable", error="pyserial is required for NetApp serial discovery.")
            raise NetAppConsoleError("pyserial is required for NetApp serial discovery.")

        metadata = _port_metadata()
        candidates: list[NetAppConsoleCandidate] = []
        append_netapp_console_log("serial.discovery.start", ports=_ordered_port_paths(metadata), baud_rates=self.baud_rates)
        for port in _ordered_port_paths(metadata):
            info = metadata.get(port)
            for baud in self.baud_rates:
                candidates.append(self._probe_port(port, baud, info))
        candidates.sort(key=lambda item: (item.score, item.prompt_type in {"cluster_cli", "advanced_cli", "boot_menu"}, item.port.startswith("/dev/serial/by-id/")), reverse=True)
        append_netapp_console_log(
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

    def _probe_port(self, port: str, baud: int, info: Any) -> NetAppConsoleCandidate:
        candidate = NetAppConsoleCandidate(
            port=port,
            baud=baud,
            description=str(getattr(info, "description", "") or ""),
            hardware_id=str(getattr(info, "hwid", "") or ""),
            manufacturer=str(getattr(info, "manufacturer", "") or ""),
        )
        try:
            serial_module, _port_tools = _load_serial_modules()
            if serial_module is None:
                raise NetAppConsoleError("pyserial is required for NetApp serial discovery.")
            with serial_module.Serial(port=port, baudrate=baud, timeout=self.timeout, write_timeout=self.timeout) as conn:
                time.sleep(self.settle_seconds)
                conn.reset_input_buffer()
                conn.write(b"\r\n")
                conn.flush()
                candidate.raw_output = self._read_available_for(conn, self.timeout)
                candidate.prompt_type, candidate.score = detect_netapp_console_prompt(candidate.raw_output)
        except Exception as exc:
            candidate.error = str(exc).splitlines()[0]
            append_netapp_console_log("serial.discovery.probe_failed", port=port, baud=baud, error=candidate.error)
            return candidate

        append_netapp_console_log(
            "serial.discovery.probe_complete",
            port=port,
            baud=baud,
            prompt_type=candidate.prompt_type,
            score=candidate.score,
            raw_output=mask_secrets(candidate.raw_output),
        )
        return candidate

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


class NetAppConsoleClient:
    def __init__(self, port: str, baud: int = 115200, *, timeout: float = 1.0):
        serial_module, _port_tools = _load_serial_modules()
        if serial_module is None:
            raise NetAppConsoleError("pyserial is required for NetApp serial access.")
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._conn: Any = None

    def __enter__(self) -> NetAppConsoleClient:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def open(self) -> None:
        serial_module, _port_tools = _load_serial_modules()
        if serial_module is None:
            raise NetAppConsoleError("pyserial is required for NetApp serial access.")
        self._conn = serial_module.Serial(port=self.port, baudrate=self.baud, timeout=self.timeout, write_timeout=self.timeout)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def read_prompt(self, *, wait_seconds: float = 1.5) -> str:
        self._write("\r\n")
        return self._read_for(wait_seconds)

    def run_command(self, command: str, *, redact: bool = False, wait_seconds: float = 2.0) -> str:
        self._write(str(command) + "\r\n")
        output = self._read_for(wait_seconds)
        return mask_secrets(output, [command]) if redact else output

    def send_ctrl_c(self) -> None:
        self._write_bytes(b"\x03")

    def login_to_cli(self, username: str, password: str) -> dict[str, Any]:
        output = self.read_prompt()
        prompt_type, _score = detect_netapp_console_prompt(output)
        for _attempt in range(5):
            if prompt_type in {"cluster_cli", "advanced_cli", "boot_menu", "loader", "boot_interrupt"}:
                return {"ok": True, "prompt_type": prompt_type, "output": mask_secrets(output, [password])}
            if prompt_type == "login" and username:
                output += self.run_command(username, wait_seconds=1.0)
            elif prompt_type == "password" and password:
                output += self.run_command(password, redact=True, wait_seconds=1.5)
            else:
                output += self.read_prompt(wait_seconds=1.0)
            prompt_type, _score = detect_netapp_console_prompt(output)
        return {
            "ok": False,
            "prompt_type": prompt_type,
            "error": "NetApp console did not reach an ONTAP CLI or boot menu prompt.",
            "output": mask_secrets(output, [password]),
        }

    def read_status_probe(self, username: str, password: str) -> dict[str, Any]:
        login = self.login_to_cli(username, password)
        output = str(login.get("output") or "")
        prompt_type = str(login.get("prompt_type") or "")
        if login.get("ok") and prompt_type in {"cluster_cli", "advanced_cli"}:
            for command in ("system node show", "network interface show -role cluster-mgmt,node-mgmt"):
                output += f"\n--- {command} ---\n"
                output += self.run_command(command, wait_seconds=2.0)
        return {
            "ok": bool(login.get("ok")),
            "port": self.port,
            "baud": self.baud,
            "prompt_type": prompt_type,
            "error": str(login.get("error") or ""),
            "raw_output": mask_secrets(output, [password]),
        }

    def _write(self, text: str) -> None:
        self._write_bytes(text.encode("utf-8"))

    def _write_bytes(self, value: bytes) -> None:
        if self._conn is None:
            raise NetAppConsoleError("Serial connection is not open.")
        self._conn.write(value)
        self._conn.flush()

    def _read_for(self, seconds: float) -> str:
        if self._conn is None:
            raise NetAppConsoleError("Serial connection is not open.")
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


def discovery_candidates_payload(candidates: list[NetAppConsoleCandidate], *, include_raw: bool = False) -> list[dict[str, Any]]:
    return [item.as_dict(include_raw=include_raw) for item in candidates]


def probe_netapp_console_login(*, port: str, baud: int, username: str, password: str) -> dict[str, Any]:
    if not str(port or "").strip():
        return {"ok": False, "error": "NetApp console port is not selected.", "raw_output": ""}
    if not str(username or "").strip():
        return {"ok": False, "error": "NetApp console username is not set.", "raw_output": ""}
    with NetAppConsoleClient(str(port).strip(), int(baud or 115200)) as client:
        return client.read_status_probe(str(username or "").strip(), str(password or ""))


def execute_netapp_console_factory_reset(
    *,
    port: str,
    baud: int,
    username: str,
    password: str,
    reboot_command: str = "",
    boot_menu_option: str = "4",
    boot_wait_seconds: int = 240,
    wipe_wait_seconds: int = 1800,
    reset_node_name: str = "",
    partner_node_name: str = "",
    disable_storage_failover: bool = False,
    disable_partner_storage_failover: bool = False,
    halt_partner_before_reset: bool = False,
    normal_boot_after_wipe: bool = True,
) -> dict[str, Any]:
    port = str(port or "").strip()
    username = str(username or "").strip()
    password = str(password or "")
    reboot_command = str(reboot_command or "").strip()
    boot_menu_option = str(boot_menu_option or "4").strip()
    reset_node_name = str(reset_node_name or "").strip()
    partner_node_name = str(partner_node_name or "").strip()
    if not port:
        return {"ok": False, "status": "failed", "error": "NetApp console port is not selected.", "raw_output": ""}
    if boot_menu_option != "4":
        return {"ok": False, "status": "blocked", "error": "Only ONTAP boot-menu option 4 is supported for this factory-reset helper.", "raw_output": ""}

    with NetAppConsoleClient(port, int(baud or 115200), timeout=1.0) as client:
        login = client.login_to_cli(username, password)
        output = str(login.get("output") or "")
        prompt_type = str(login.get("prompt_type") or "")

        if prompt_type in {"cluster_cli", "advanced_cli"}:
            if disable_storage_failover and reset_node_name:
                command = f"storage failover modify -node {reset_node_name} -enabled false"
                output += f"\n--- {command} ---\n"
                output += client.run_command(command, wait_seconds=2.0)
            if disable_partner_storage_failover and partner_node_name:
                command = f"storage failover modify -node {partner_node_name} -enabled false"
                output += f"\n--- {command} ---\n"
                output += client.run_command(command, wait_seconds=2.0)
            if halt_partner_before_reset and partner_node_name:
                command = f"system node halt -node {partner_node_name} -ignore-quorum-warnings true"
                output += f"\n--- {command} ---\n"
                output += client.run_command(command, wait_seconds=3.0)
                if re.search(r"(?is)(are you sure|continue|confirm|do you want).*[\?:]\s*$", output):
                    output += client.run_command("yes", wait_seconds=8.0)
            if not reboot_command and reset_node_name:
                reboot_command = f"system node reboot -node {reset_node_name} -ignore-quorum-warnings true"
            if not reboot_command:
                return {
                    "ok": False,
                    "status": "blocked",
                    "error": "Console is at the ONTAP CLI. Enter a node reboot command or manually reboot the node to the ONTAP boot menu, then run the console reset again.",
                    "prompt_type": prompt_type,
                    "raw_output": mask_secrets(output, [password]),
                }
            output += f"\n--- {reboot_command} ---\n"
            output += client.run_command(reboot_command, wait_seconds=2.0)
            if re.search(r"(?is)(are you sure|continue|confirm|do you want).*[\?:]\s*$", output):
                output += client.run_command("yes", wait_seconds=1.0)
        elif prompt_type == "loader":
            output += "\n--- boot_ontap menu ---\n"
            output += client.run_command("boot_ontap menu", wait_seconds=5.0)
            prompt_type, _score = detect_netapp_console_prompt(output)

        boot_seen = prompt_type == "boot_menu" or "boot menu" in output.lower()
        deadline = time.monotonic() + max(15, int(boot_wait_seconds or 240))
        last_ctrl_c = 0.0
        loader_menu_sent = prompt_type == "loader"
        while not boot_seen and time.monotonic() < deadline:
            chunk = client._read_for(1.0)
            output += chunk
            prompt_type, _score = detect_netapp_console_prompt(output)
            if prompt_type == "boot_menu":
                boot_seen = True
                break
            if prompt_type == "loader" and not loader_menu_sent:
                output += "\n--- boot_ontap menu ---\n"
                output += client.run_command("boot_ontap menu", wait_seconds=5.0)
                prompt_type, _score = detect_netapp_console_prompt(output)
                loader_menu_sent = True
                if prompt_type == "boot_menu":
                    boot_seen = True
                    break
            if prompt_type == "boot_interrupt" or re.search(r"(?i)press ctrl-c .*boot menu", chunk):
                client.send_ctrl_c()
                last_ctrl_c = time.monotonic()
            elif time.monotonic() - last_ctrl_c > 8:
                client.send_ctrl_c()
                last_ctrl_c = time.monotonic()

        if not boot_seen:
            return {
                "ok": False,
                "status": "failed",
                "error": "ONTAP boot menu was not detected before the reset timeout.",
                "prompt_type": prompt_type,
                "raw_output": mask_secrets(output, [password]),
            }

        output += f"\n--- boot menu option {boot_menu_option} ---\n"
        output += client.run_command(boot_menu_option, wait_seconds=3.0)
        wipe_started = False
        yes_responses = 0
        partner_responses = 0
        normal_boot_sent = False
        wipe_deadline = time.monotonic() + max(30, int(wipe_wait_seconds or 1800))
        while time.monotonic() < wipe_deadline:
            tail = output[-3000:]
            if re.search(r"(?is)boot menu selection.*\"?4\"?.*cancelled|selection.*\"?4\"?.*cancelled|reservation conflict found", tail):
                return {
                    "ok": False,
                    "status": "failed",
                    "error": "ONTAP cancelled boot-menu option 4 because of a disk reservation or giveback conflict. Disable storage failover, wait for giveback/reservations to clear, then rerun the reset.",
                    "port": port,
                    "baud": int(baud or 115200),
                    "prompt_type": prompt_type,
                    "raw_output": mask_secrets(output, [password]),
                }
            if re.search(r"(?is)(partner node name|name of the partner|enter.*partner)", tail) and re.search(r"(?m)[:?]\s*$", tail):
                if not partner_node_name:
                    return {
                        "ok": False,
                        "status": "blocked",
                        "error": "ONTAP is asking for the HA partner node name. Save the partner node name in the NetApp console reset section and rerun the reset.",
                        "prompt_type": prompt_type,
                        "raw_output": mask_secrets(output, [password]),
                    }
                if partner_responses < 2:
                    output += client.run_command(partner_node_name, wait_seconds=3.0)
                    partner_responses += 1
                    continue
            if re.search(
                r"(?is)(zero disks|initialize|erase|destroy|delete|are you sure|continue|wipeconfig|this will erase all the data|type yes to confirm).*?[\?:]\s*$",
                tail,
            ):
                if yes_responses < 4:
                    output += client.run_command("yes", wait_seconds=3.0)
                    yes_responses += 1
                    wipe_started = True
                    continue
            if re.search(r"(?is)(rebooting to finish wipeconfig|wipeconfig request|initializ(?:e|ing) all disks|zeroing disks|clean configuration)", tail):
                wipe_started = True
            prompt_type, _score = detect_netapp_console_prompt(output)
            if wipe_started and prompt_type == "boot_menu":
                if normal_boot_after_wipe and not normal_boot_sent:
                    output += "\n--- normal boot after wipe ---\n"
                    output += client.run_command("1", wait_seconds=5.0)
                    normal_boot_sent = True
                    continue
                return {
                    "ok": True,
                    "status": "completed",
                    "message": "ONTAP wipe completed and the node returned to the boot menu.",
                    "port": port,
                    "baud": int(baud or 115200),
                    "prompt_type": prompt_type,
                    "raw_output": mask_secrets(output, [password]),
                }
            if wipe_started and re.search(r"(?is)(cluster setup|Type yes to confirm and continue|login:\s*$|::>\s*$|\*>\s*$)", tail):
                return {
                    "ok": True,
                    "status": "completed",
                    "message": "ONTAP factory reset completed and the node is booted far enough for setup/login.",
                    "port": port,
                    "baud": int(baud or 115200),
                    "prompt_type": prompt_type,
                    "raw_output": mask_secrets(output, [password]),
                }
            output += client._read_for(5.0)

    return {
        "ok": True,
        "status": "submitted",
        "message": "ONTAP boot-menu wipe option 4 was sent over the selected console. Completion was not detected before the monitor timeout; review raw output before touching the partner node.",
        "port": port,
        "baud": int(baud or 115200),
        "prompt_type": prompt_type,
        "raw_output": mask_secrets(output, [password]),
    }
