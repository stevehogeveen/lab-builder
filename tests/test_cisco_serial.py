from types import SimpleNamespace

import app.cisco as cisco


class FakeSerialConnection:
    def __init__(self, output: bytes):
        self.output = output
        self.in_waiting = len(output)
        self.writes: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def reset_input_buffer(self):
        pass

    def write(self, payload: bytes):
        self.writes.append(payload)

    def flush(self):
        pass

    def read(self, size: int) -> bytes:
        if not self.output:
            self.in_waiting = 0
            return b""
        chunk = self.output[:size]
        self.output = self.output[size:]
        self.in_waiting = len(self.output)
        return chunk

    def close(self):
        pass


class CommandAwareSerialConnection:
    def __init__(self, initial_output: bytes, command_outputs: dict[str, bytes]):
        self.initial_output = initial_output
        self.command_outputs = dict(command_outputs)
        self.output = b""
        self.in_waiting = 0
        self.writes: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def reset_input_buffer(self):
        self.output = b""
        self.in_waiting = 0

    def write(self, payload: bytes):
        self.writes.append(payload)
        command = payload.decode("utf-8", errors="ignore").strip()
        self.output = self.command_outputs.get(command, self.initial_output if command == "" else b"")
        self.in_waiting = len(self.output)

    def flush(self):
        pass

    def read(self, size: int) -> bytes:
        if not self.output:
            self.in_waiting = 0
            return b""
        chunk = self.output[:size]
        self.output = self.output[size:]
        self.in_waiting = len(self.output)
        return chunk

    def close(self):
        pass


class CommandAwareSerialModule:
    def __init__(self, connections: dict[str, CommandAwareSerialConnection]):
        self.connections = connections

    def Serial(self, port, baudrate, timeout, write_timeout):
        return self.connections[port]


class FakeSerialModule:
    def __init__(self, outputs: dict[str, bytes]):
        self.outputs = outputs

    def Serial(self, port, baudrate, timeout, write_timeout):
        return FakeSerialConnection(self.outputs.get(port, b""))


class SequencedSerialConnection:
    def __init__(self, outputs: list[bytes]):
        self.outputs = list(outputs)
        self.output = b""
        self.in_waiting = 0
        self.writes: list[bytes] = []

    def write(self, payload: bytes):
        self.writes.append(payload)
        self.output = self.outputs.pop(0) if self.outputs else b"Switch#"
        self.in_waiting = len(self.output)

    def flush(self):
        pass

    def read(self, size: int) -> bytes:
        if not self.output:
            self.in_waiting = 0
            return b""
        chunk = self.output[:size]
        self.output = self.output[size:]
        self.in_waiting = len(self.output)
        return chunk

    def close(self):
        pass


class SequencedSerialModule:
    def __init__(self, connection: SequencedSerialConnection):
        self.connection = connection

    def Serial(self, port, baudrate, timeout, write_timeout):
        return self.connection


def _ports(*devices):
    return SimpleNamespace(
        comports=lambda: [
            SimpleNamespace(device=device, description=f"{device} adapter", hwid=f"hwid-{device}", manufacturer="Lab")
            for device in devices
        ]
    )


def test_detect_cisco_prompt_patterns():
    assert cisco.detect_cisco_prompt("Switch#")[0] == "privileged"
    assert cisco.detect_cisco_prompt("Router>")[0] == "user_exec"
    assert cisco.detect_cisco_prompt("Username:")[0] == "username"
    assert cisco.detect_cisco_prompt("Password:")[0] == "password"
    assert cisco.detect_cisco_prompt("rommon >")[0] == "rommon"
    assert cisco.detect_cisco_prompt("Enter enable secret:")[0] == "setup_enable_secret"
    assert cisco.detect_cisco_prompt("Confirm enable secret:")[0] == "setup_enable_secret"
    assert cisco.detect_cisco_prompt("Would you like to enter the initial configuration dialog?")[0] == "initial_dialog"


def test_serial_discovery_no_console_found(monkeypatch):
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(cisco, "glob", SimpleNamespace(glob=lambda pattern: ["/dev/ttyUSB0"] if pattern == "/dev/ttyUSB*" else []))
    monkeypatch.setattr(cisco, "serial", FakeSerialModule({"/dev/ttyUSB0": b"not a cisco prompt"}))

    candidates = cisco.CiscoSerialDiscovery(baud_rates=[9600], timeout=0.01, settle_seconds=0).scan()

    assert len(candidates) == 1
    assert candidates[0].score == 0
    assert candidates[0].prompt_type == ""


def test_serial_discovery_multiple_ports_found(monkeypatch):
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0", "/dev/ttyACM0"))
    monkeypatch.setattr(
        cisco,
        "glob",
        SimpleNamespace(
            glob=lambda pattern: {
                "/dev/serial/by-id/*": [],
                "/dev/ttyUSB*": ["/dev/ttyUSB0"],
                "/dev/ttyACM*": ["/dev/ttyACM0"],
            }.get(pattern, [])
        ),
    )
    monkeypatch.setattr(cisco, "serial", FakeSerialModule({"/dev/ttyUSB0": b"\r\nSwitch#", "/dev/ttyACM0": b"\r\nRouter>"}))

    candidates = cisco.CiscoSerialDiscovery(baud_rates=[9600], timeout=0.01, settle_seconds=0).scan()
    matches = [item for item in candidates if item.score > 0]

    assert len(matches) == 2
    assert {item.port for item in matches} == {"/dev/ttyUSB0", "/dev/ttyACM0"}


def test_serial_discovery_downgrades_generic_prompt_without_cisco_identity(monkeypatch):
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(cisco, "glob", SimpleNamespace(glob=lambda pattern: ["/dev/ttyUSB0"] if pattern == "/dev/ttyUSB*" else []))
    monkeypatch.setattr(
        cisco,
        "serial",
        CommandAwareSerialModule(
            {
                "/dev/ttyUSB0": CommandAwareSerialConnection(
                    b"\r\nlinux-host#",
                    {"show version": b"bash: show: command not found\r\nlinux-host#"},
                )
            }
        ),
    )

    candidates = cisco.CiscoSerialDiscovery(baud_rates=[9600], timeout=0.01, settle_seconds=0).scan()

    assert candidates[0].prompt_type == "generic_privileged"
    assert candidates[0].score < 50


def test_serial_discovery_verifies_custom_named_cisco_prompt(monkeypatch):
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(cisco, "glob", SimpleNamespace(glob=lambda pattern: ["/dev/ttyUSB0"] if pattern == "/dev/ttyUSB*" else []))
    monkeypatch.setattr(
        cisco,
        "serial",
        CommandAwareSerialModule(
            {
                "/dev/ttyUSB0": CommandAwareSerialConnection(
                    b"\r\nLAB-SW01#",
                    {"show version": b"Cisco IOS XE Software, Version 17.09.05\r\nLAB-SW01#"},
                )
            }
        ),
    )

    candidates = cisco.CiscoSerialDiscovery(baud_rates=[9600], timeout=0.01, settle_seconds=0).scan()

    assert candidates[0].prompt_type == "privileged_verified"
    assert candidates[0].score >= 180


def test_serial_discovery_deduplicates_by_id_alias(monkeypatch):
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(
        cisco,
        "glob",
        SimpleNamespace(
            glob=lambda pattern: {
                "/dev/serial/by-id/*": ["/dev/serial/by-id/usb-lab"],
                "/dev/ttyUSB*": ["/dev/ttyUSB0"],
                "/dev/ttyACM*": [],
            }.get(pattern, [])
        ),
    )
    monkeypatch.setattr(cisco.os.path, "realpath", lambda path: "/dev/ttyUSB0" if path in {"/dev/serial/by-id/usb-lab", "/dev/ttyUSB0"} else path)
    monkeypatch.setattr(cisco, "serial", FakeSerialModule({"/dev/serial/by-id/usb-lab": b"\r\nSwitch#"}))

    candidates = cisco.CiscoSerialDiscovery(baud_rates=[9600], timeout=0.01, settle_seconds=0).scan()

    assert [item.port for item in candidates] == ["/dev/serial/by-id/usb-lab"]


def test_bootstrap_exits_initial_dialog_before_config(monkeypatch):
    commands = cisco.CiscoSerialClient.build_management_commands(
        {
            "hostname": "sw01",
            "management_vlan": 10,
            "management_ip": "10.10.8.2",
            "subnet_mask": "255.255.255.0",
            "gateway": "10.10.8.1",
            "domain_name": "example.local",
            "username": "admin",
            "password": "Secret123",
        },
        mask=False,
    )
    outputs = [
        b"Would you like to enter the initial configuration dialog? [yes/no]: ",
        b"\r\nSwitch#",
        b"\r\nSwitch#",
    ] + [b"\r\nSwitch#" for _ in commands]
    connection = SequencedSerialConnection(outputs)
    monkeypatch.setattr(cisco, "serial", SequencedSerialModule(connection))
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(cisco, "append_cisco_log", lambda *args, **kwargs: None)

    with cisco.CiscoSerialClient("/dev/ttyUSB0", 9600, timeout=0.01) as client:
        result = client.apply_management_config(
            {
                "hostname": "sw01",
                "management_vlan": 10,
                "management_ip": "10.10.8.2",
                "subnet_mask": "255.255.255.0",
                "gateway": "10.10.8.1",
                "domain_name": "example.local",
                "username": "admin",
                "password": "Secret123",
            }
        )

    writes = [item.decode("utf-8").strip() for item in connection.writes]

    assert result.ok is True
    assert writes[0] == ""
    assert writes[1] == "no"
    assert "configure terminal" in writes
    assert writes.index("no") < writes.index("configure terminal")
    assert "Secret123" not in result.output


def test_password_masking():
    text = "username admin privilege 15 secret SuperSecret\nPassword: SuperSecret\n"

    masked = cisco.mask_secrets(text, ["SuperSecret"])

    assert "SuperSecret" not in masked
    assert cisco.SECRET_MASK in masked


def test_config_render_does_not_expose_secrets():
    rendered = cisco.render_management_config(
        {
            "hostname": "sw01",
            "management_vlan": 1,
            "management_ip": "10.10.8.2",
            "subnet_mask": "255.255.255.0",
            "gateway": "10.10.8.1",
            "domain_name": "lab.local",
            "username": "admin",
            "password": "DoNotLeak",
        }
    )

    assert "DoNotLeak" not in rendered
    assert "username admin privilege 15 secret ********" in rendered
    assert "ip domain name lab.local" in rendered


def test_management_bootstrap_can_configure_minimal_network_port():
    commands = cisco.CiscoSerialClient.build_management_commands(
        {
            "hostname": "sw01",
            "management_vlan": 10,
            "management_ip": "10.10.8.2",
            "subnet_mask": "255.255.255.0",
            "gateway": "10.10.8.1",
            "domain_name": "lab.local",
            "username": "admin",
            "password": "DoNotLeak",
            "bootstrap_network_port": "Te1/1/1",
            "bootstrap_network_mode": "trunk",
        },
        mask=False,
    )

    assert "vlan 10" in commands
    assert "interface TenGigabitEthernet1/1/1" in commands
    assert "switchport mode trunk" in commands
    assert "switchport trunk allowed vlan add 10" in commands
    assert commands.index("interface TenGigabitEthernet1/1/1") < commands.index("interface vlan 10")


def test_append_cisco_log_creates_log_and_masks_secrets(tmp_path, monkeypatch):
    log_path = tmp_path / "artifacts" / "logs" / "cisco.log"
    monkeypatch.setattr(cisco, "CISCO_LOG_PATH", log_path)

    cisco.append_cisco_log(
        "serial.discovery.failed",
        error="Password: Secret123",
        raw_output="Password: Secret123",
        commands=["username admin privilege 15 secret Secret123"],
    )

    text = log_path.read_text(encoding="utf-8")

    assert "Secret123" not in text
    assert cisco.SECRET_MASK in text


def test_apply_serial_permission_fix_runs_usermod_and_acl(monkeypatch):
    calls: list[list[str]] = []
    diagnostics = [
        {
            "ordered_ports": ["/dev/ttyUSB0"],
            "group_names": ["administrator"],
            "device_access": [{"path": "/dev/ttyUSB0", "readable": False, "writable": False}],
        },
        {
            "ordered_ports": ["/dev/ttyUSB0"],
            "group_names": ["administrator"],
            "device_access": [{"path": "/dev/ttyUSB0", "readable": True, "writable": True}],
        },
    ]
    monkeypatch.setattr(cisco, "serial_runtime_diagnostics", lambda: diagnostics.pop(0))
    monkeypatch.setattr(cisco, "append_cisco_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(cisco, "grp", SimpleNamespace(getgrgid=lambda _gid: SimpleNamespace(gr_name="administrator")))
    monkeypatch.setattr(cisco, "pwd", SimpleNamespace(getpwuid=lambda _uid: SimpleNamespace(pw_name="administrator")))
    monkeypatch.setattr(cisco, "os", SimpleNamespace(getuid=lambda: 1000, getgroups=lambda: [1000], path=SimpleNamespace(realpath=lambda value: value)))
    monkeypatch.setattr(cisco.shutil, "which", lambda name: "/usr/bin/setfacl" if name == "setfacl" else None)

    def fake_run(command, password):
        calls.append(list(command))
        return True, ""

    monkeypatch.setattr(cisco, "_run_sudo_command", fake_run)

    result = cisco.apply_serial_permission_fix("pw")

    assert result["ok"] is True
    assert any(command[:3] == ["/usr/sbin/usermod", "-aG", "dialout"] for command in calls)
    assert any(command[:2] == ["/usr/bin/setfacl", "-m"] for command in calls)
    assert result["restart_required"] is False


def test_apply_serial_permission_fix_requires_password():
    result = cisco.apply_serial_permission_fix("")

    assert result["ok"] is False
    assert "password is required" in result["error"].lower()


def test_serial_diagnostics_handles_missing_posix_modules(monkeypatch):
    monkeypatch.setattr(cisco, "grp", None)
    monkeypatch.setattr(cisco, "pwd", None)
    monkeypatch.setattr(cisco, "_port_metadata", lambda: {})

    diagnostics = cisco.serial_runtime_diagnostics()

    assert diagnostics["posix_permissions_supported"] is False
    assert diagnostics["group_names"] == []
    assert "user" in diagnostics


def test_apply_serial_permission_fix_blocks_without_posix_support(monkeypatch):
    monkeypatch.setattr(cisco, "grp", None)
    monkeypatch.setattr(cisco, "pwd", None)
    monkeypatch.setattr(cisco, "_port_metadata", lambda: {})

    result = cisco.apply_serial_permission_fix("pw")

    assert result["ok"] is False
    assert "posix" in result["error"].lower()
    assert result["applied"] == []
