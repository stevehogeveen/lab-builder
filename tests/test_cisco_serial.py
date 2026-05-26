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


def _speed_up_serial_client_reads(monkeypatch):
    def fast_read_for(self, _seconds):
        if self._conn is None:
            raise cisco.CiscoSerialError("Serial connection is not open.")
        waiting = int(getattr(self._conn, "in_waiting", 0) or 0)
        chunk = self._conn.read(waiting or 256)
        return chunk.decode("utf-8", errors="replace") if chunk else ""

    monkeypatch.setattr(cisco.CiscoSerialClient, "_read_for", fast_read_for)


def test_detect_cisco_prompt_patterns():
    assert cisco.detect_cisco_prompt("Switch#")[0] == "privileged"
    assert cisco.detect_cisco_prompt("Router>")[0] == "user_exec"
    assert cisco.detect_cisco_prompt("Switch(config)#")[0] == "config"
    assert cisco.detect_cisco_prompt("Switch(config-if)#")[0] == "interface_config"
    assert cisco.detect_cisco_prompt("Username:")[0] == "username"
    assert cisco.detect_cisco_prompt("Login:")[0] == "login"
    assert cisco.detect_cisco_prompt("Password:")[0] == "password"
    assert cisco.detect_cisco_prompt("rommon >")[0] == "rommon"
    assert cisco.detect_cisco_prompt("Enter enable secret:")[0] == "setup_enable_secret"
    assert cisco.detect_cisco_prompt("Confirm enable secret:")[0] == "setup_enable_secret"
    assert cisco.detect_cisco_prompt("Re-enter enable secret:")[0] == "setup_enable_secret"
    assert cisco.detect_cisco_prompt("Enter virtual terminal password:")[0] == "setup_password"
    assert cisco.detect_cisco_prompt("Re-enter password:")[0] == "setup_password"
    assert cisco.detect_cisco_prompt("% Password too short\nEnter enable secret:")[0] == "setup_password_policy_failure"
    assert cisco.detect_cisco_prompt("Would you like to enter the initial configuration dialog?")[0] == "initial_dialog"
    assert cisco.detect_cisco_prompt("Would you like to terminate autoinstall? [yes]:")[0] == "autoinstall_terminate"
    assert cisco.detect_cisco_prompt("Press RETURN to get started!")[0] == "press_return"
    assert cisco.detect_cisco_prompt("0  Go to the IOS command prompt without saving this config\nEnter your selection [2]:")[0] == "setup_final_menu"


def test_cisco_wizard_password_policy_validation():
    assert "at least 10 characters" in cisco.validate_cisco_wizard_password_policy("Short1")
    assert "one uppercase letter" in cisco.validate_cisco_wizard_password_policy("lowercase12")
    assert "one lowercase letter" in cisco.validate_cisco_wizard_password_policy("UPPERCASE12")
    assert "one digit" in cisco.validate_cisco_wizard_password_policy("NoDigitsHere")
    assert cisco.validate_cisco_wizard_password_policy("ValidSecret123") == []


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
    _speed_up_serial_client_reads(monkeypatch)
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
        b"",
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
    assert writes[2] == ""
    assert "configure terminal" in writes
    assert writes.index("no") < writes.index("configure terminal")
    assert "Secret123" not in result.output


def test_bootstrap_handles_forced_enable_secret_after_initial_dialog_no(monkeypatch):
    _speed_up_serial_client_reads(monkeypatch)
    commands = cisco.CiscoSerialClient.build_management_commands(
        {
            "hostname": "sw01",
            "management_vlan": 10,
            "management_ip": "192.168.1.2",
            "subnet_mask": "255.255.255.0",
            "gateway": "192.168.1.1",
            "domain_name": "example.local",
            "username": "admin",
            "password": "ValidSecret123",
            "enable_secret": "ValidSecret123",
        },
        mask=False,
    )
    outputs = [
        b"Would you like to enter the initial configuration dialog? [yes/no]: ",
        b"\r\nEnter enable secret: ",
        b"\r\nConfirm enable secret: ",
        b"\r\n0  Go to the IOS command prompt without saving this config\r\n1  Return back to the setup without saving this config\r\n2  Save this configuration to NVRAM and exit\r\nEnter your selection [2]: ",
        b"\r\nSwitch>",
        b"\r\nPassword: ",
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
                "management_ip": "192.168.1.2",
                "subnet_mask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "domain_name": "example.local",
                "username": "admin",
                "password": "ValidSecret123",
                "enable_secret": "ValidSecret123",
                "wizard_password": "ValidSecret123",
            }
        )

    writes = [item.decode("utf-8").strip() for item in connection.writes]

    assert result.ok is True
    assert writes[1] == "no"
    assert "0" in writes
    assert "2" not in writes
    assert writes.index("no") < writes.index("ValidSecret123") < writes.index("0") < writes.index("configure terminal")
    assert writes.index("configure terminal") < writes.index("write memory")
    assert any("initial configuration dialog" in step for step in result.steps)
    assert any("password prompt" in step for step in result.steps)
    assert any("final menu" in step for step in result.steps)
    assert "ValidSecret123" not in result.output


def test_bootstrap_terminates_autoinstall_after_initial_dialog(monkeypatch):
    _speed_up_serial_client_reads(monkeypatch)
    commands = cisco.CiscoSerialClient.build_management_commands(
        {
            "hostname": "sw01",
            "management_vlan": 10,
            "management_ip": "192.168.1.2",
            "subnet_mask": "255.255.255.0",
            "gateway": "192.168.1.1",
            "domain_name": "example.local",
            "username": "admin",
            "password": "Secret123",
            "enable_secret": "Enable123!",
        },
        mask=False,
    )
    outputs = [
        b"Would you like to enter the initial configuration dialog? [yes/no]: ",
        b"\r\nWould you like to terminate autoinstall? [yes]: ",
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
                "management_ip": "192.168.1.2",
                "subnet_mask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "domain_name": "example.local",
                "username": "admin",
                "password": "Secret123",
                "enable_secret": "Enable123!",
            }
        )

    writes = [item.decode("utf-8").strip() for item in connection.writes]

    assert result.ok is True
    assert writes[1] == "no"
    assert writes[2] == "yes"
    assert writes.index("no") < writes.index("yes") < writes.index("configure terminal")
    assert any("initial configuration dialog" in step for step in result.steps)
    assert any("autoinstall" in step for step in result.steps)


def test_bootstrap_returns_from_config_prompt_before_apply(monkeypatch):
    _speed_up_serial_client_reads(monkeypatch)
    commands = cisco.CiscoSerialClient.build_management_commands(
        {
            "hostname": "sw01",
            "management_vlan": 10,
            "management_ip": "192.168.1.2",
            "subnet_mask": "255.255.255.0",
            "gateway": "192.168.1.1",
            "domain_name": "example.local",
            "username": "admin",
            "password": "Secret123",
            "enable_secret": "Enable123!",
        },
        mask=False,
    )
    outputs = [b"\r\nSwitch(config-if)#", b"\r\nSwitch#"] + [b"\r\nSwitch#" for _ in commands]
    connection = SequencedSerialConnection(outputs)
    monkeypatch.setattr(cisco, "serial", SequencedSerialModule(connection))
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(cisco, "append_cisco_log", lambda *args, **kwargs: None)

    with cisco.CiscoSerialClient("/dev/ttyUSB0", 9600, timeout=0.01) as client:
        result = client.apply_management_config(
            {
                "hostname": "sw01",
                "management_vlan": 10,
                "management_ip": "192.168.1.2",
                "subnet_mask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "domain_name": "example.local",
                "username": "admin",
                "password": "Secret123",
                "enable_secret": "Enable123!",
            }
        )

    writes = [item.decode("utf-8").strip() for item in connection.writes]

    assert result.ok is True
    assert writes[1] == "end"
    assert writes.index("end") < writes.index("configure terminal")
    assert any("configuration prompt" in step for step in result.steps)


def test_bootstrap_setup_wizard_final_menu_chooses_ios_prompt_without_saving(monkeypatch):
    _speed_up_serial_client_reads(monkeypatch)
    commands = cisco.CiscoSerialClient.build_management_commands(
        {
            "hostname": "sw01",
            "management_vlan": 10,
            "management_ip": "192.168.1.2",
            "subnet_mask": "255.255.255.0",
            "gateway": "192.168.1.1",
            "domain_name": "example.local",
            "username": "admin",
            "password": "ValidSecret123",
            "enable_secret": "ValidSecret123",
        },
        mask=False,
    )
    outputs = [
        b"Enter enable secret: ",
        b"\r\nConfirm enable secret: ",
        b"\r\n0  Go to the IOS command prompt without saving this config\r\n1  Return back to the setup without saving this config\r\n2  Save this configuration to NVRAM and exit\r\nEnter your selection [2]: ",
        b"\r\nSwitch>",
        b"\r\nPassword: ",
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
                "management_ip": "192.168.1.2",
                "subnet_mask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "domain_name": "example.local",
                "username": "admin",
                "password": "ValidSecret123",
                "enable_secret": "ValidSecret123",
                "wizard_password": "ValidSecret123",
            }
        )

    writes = [item.decode("utf-8").strip() for item in connection.writes]

    assert result.ok is True
    assert "0" in writes
    assert "2" not in writes
    assert writes.index("0") < writes.index("configure terminal")
    assert writes.index("configure terminal") < writes.index("write memory")
    assert any("final menu" in step for step in result.steps)
    assert "ValidSecret123" not in result.output


def test_bootstrap_user_exec_sends_enable_and_enable_secret(monkeypatch):
    _speed_up_serial_client_reads(monkeypatch)
    commands = cisco.CiscoSerialClient.build_management_commands(
        {
            "hostname": "sw01",
            "management_vlan": 10,
            "management_ip": "192.168.1.2",
            "subnet_mask": "255.255.255.0",
            "gateway": "192.168.1.1",
            "domain_name": "example.local",
            "username": "admin",
            "password": "ValidSecret123",
            "enable_secret": "ValidSecret123",
        },
        mask=False,
    )
    outputs = [
        b"\r\nSwitch>",
        b"\r\nPassword: ",
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
                "management_ip": "192.168.1.2",
                "subnet_mask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "domain_name": "example.local",
                "username": "admin",
                "password": "ValidSecret123",
                "enable_secret": "ValidSecret123",
            }
        )

    writes = [item.decode("utf-8").strip() for item in connection.writes]

    assert result.ok is True
    assert writes[1] == "enable"
    assert writes[2] == "ValidSecret123"
    assert writes.index("enable") < writes.index("ValidSecret123") < writes.index("configure terminal")
    assert any("user EXEC" in step for step in result.steps)
    assert any("enable password prompt" in step for step in result.steps)
    assert "ValidSecret123" not in result.output


def test_bootstrap_failure_message_includes_prompt_diagnostics(monkeypatch):
    _speed_up_serial_client_reads(monkeypatch)
    outputs = [
        b"Would you like to enter the initial configuration dialog? [yes/no]: ",
        b"\r\nEnter enable secret: ",
        b"\r\n% Password too short\r\nEnter enable secret: ",
    ]
    connection = SequencedSerialConnection(outputs)
    monkeypatch.setattr(cisco, "serial", SequencedSerialModule(connection))
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(cisco, "append_cisco_log", lambda *args, **kwargs: None)

    with cisco.CiscoSerialClient("/dev/ttyUSB0", 9600, timeout=0.01) as client:
        result = client.apply_management_config(
            {
                "hostname": "sw01",
                "management_vlan": 10,
                "management_ip": "192.168.1.2",
                "subnet_mask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "domain_name": "example.local",
                "username": "admin",
                "password": "short1A",
                "enable_secret": "short1A",
                "wizard_password": "short1A",
            }
        )

    assert result.ok is False
    assert "Last detected state: setup_password_policy_failure" in result.error
    assert "Last safe action: stopped after Cisco reported a password policy failure" in result.error
    assert "Initial dialog answered no: yes" in result.error
    assert "Forced setup wizard detected: yes" in result.error
    assert "Final wizard menu seen: no" in result.error
    assert "Switch> reached: no" in result.error
    assert "Password after enable reached: no" in result.error
    assert "Password policy failure detected: yes" in result.error
    assert "Next manual recovery step:" in result.error
    assert "short1A" not in result.error
    assert "short1A" not in result.output


def test_bootstrap_does_not_save_when_cli_config_is_rejected(monkeypatch):
    _speed_up_serial_client_reads(monkeypatch)
    connection = CommandAwareSerialConnection(
        b"\r\nSwitch#",
        {
            "": b"\r\nSwitch#",
            "ip address 192.168.1.2 255.255.255.0": b"\r\n% Invalid input detected at '^' marker.\r\nSwitch(config-if)#",
        },
    )
    monkeypatch.setattr(cisco, "serial", CommandAwareSerialModule({"/dev/ttyUSB0": connection}))
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(cisco, "append_cisco_log", lambda *args, **kwargs: None)

    with cisco.CiscoSerialClient("/dev/ttyUSB0", 9600, timeout=0.01) as client:
        result = client.apply_management_config(
            {
                "hostname": "sw01",
                "management_vlan": 10,
                "management_ip": "192.168.1.2",
                "subnet_mask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "domain_name": "example.local",
                "username": "admin",
                "password": "ValidSecret123",
                "enable_secret": "ValidSecret123",
            }
        )

    writes = [item.decode("utf-8").strip() for item in connection.writes]

    assert result.ok is False
    assert "write memory" not in writes
    assert "write memory" not in result.commands
    assert "rejected" in result.error


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
            "enable_secret": "Enable123!",
            "management_port": "Te1/1/1",
            "management_port_mode": "access",
        },
        mask=False,
    )

    assert "vlan 10" in commands
    assert "interface TenGigabitEthernet1/1/1" in commands
    assert "switchport mode access" in commands
    assert "switchport access vlan 10" in commands
    assert "spanning-tree portfast" in commands
    assert "switchport mode trunk" not in commands
    assert "crypto key generate rsa modulus 4096" in commands
    assert "line con 0" in commands
    assert "line vty 0 15" in commands
    assert "ip scp server enable" in commands
    assert commands.index("interface TenGigabitEthernet1/1/1") < commands.index("interface vlan 10")


def test_management_bootstrap_requires_trunk_review_ack_for_trunk_port():
    config = {
        "hostname": "sw01",
        "management_vlan": 10,
        "management_ip": "10.10.8.2",
        "subnet_mask": "255.255.255.0",
        "gateway": "10.10.8.1",
        "domain_name": "lab.local",
        "username": "admin",
        "password": "DoNotLeak",
        "management_port": "Te1/1/1",
        "management_port_mode": "trunk",
    }

    unreviewed = cisco.CiscoSerialClient.build_management_commands(config, mask=False)
    reviewed = cisco.CiscoSerialClient.build_management_commands({**config, "trunk_review_ack": True}, mask=False)

    assert "interface TenGigabitEthernet1/1/1" not in unreviewed
    assert "switchport mode trunk" in reviewed
    assert "switchport trunk allowed vlan add 10" in reviewed


def test_console_factory_reset_declines_save_prompt(monkeypatch):
    _speed_up_serial_client_reads(monkeypatch)
    outputs = [
        b"\r\nSwitch#",
        b"\r\nSwitch#",
        b"Erasing the nvram filesystem will remove all configuration files! Continue? [confirm]",
        b"\r\nErase of nvram: complete\r\nSwitch#",
        b"\r\nSwitch#",
        b"System configuration has been modified. Save? [yes/no]: ",
        b"\r\nProceed with reload? [confirm]",
        b"\r\nReloading\r\n",
    ]
    connection = SequencedSerialConnection(outputs)
    monkeypatch.setattr(cisco, "serial", SequencedSerialModule(connection))
    monkeypatch.setattr(cisco, "list_ports", _ports("/dev/ttyUSB0"))
    monkeypatch.setattr(cisco, "append_cisco_log", lambda *args, **kwargs: None)

    with cisco.CiscoSerialClient("/dev/ttyUSB0", 9600, timeout=0.01) as client:
        result = client.factory_reset({"username": "admin", "password": "Secret123", "enable_password": "Enable123!"})

    writes = [item.decode("utf-8").strip() for item in connection.writes]

    assert result.ok is True
    assert "write erase" in writes
    assert "delete /force flash:vlan.dat" in writes
    assert "reload" in writes
    assert "no" in writes
    assert "yes" not in writes
    assert "Secret123" not in result.output


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
    monkeypatch.setattr(cisco, "pwd", SimpleNamespace(getpwuid=lambda _uid: SimpleNamespace(pw_name="administrator")))
    monkeypatch.setattr(cisco, "os", SimpleNamespace(getuid=lambda: 1000, path=SimpleNamespace(realpath=lambda value: value)))
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
