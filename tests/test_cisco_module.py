from app.cisco import (
    CiscoConsoleBootstrapStateMachine,
    CiscoSerialClient,
    cisco_wizard_password_policy_error,
    validate_cisco_wizard_password_policy,
)
from app.modules.cisco.service import CiscoModuleService, parse_cisco_show_version


def test_cisco_wizard_password_policy_requires_length_case_and_digit():
    assert validate_cisco_wizard_password_policy("ValidS1a") == ["at least 10 characters"]
    assert validate_cisco_wizard_password_policy("validsecret1") == ["one uppercase letter"]
    assert validate_cisco_wizard_password_policy("VALIDSECRET1") == ["one lowercase letter"]
    assert validate_cisco_wizard_password_policy("ValidSecret") == ["one digit"]
    assert validate_cisco_wizard_password_policy("ValidSec1A") == []

    error = cisco_wizard_password_policy_error("Cisco password", "ValidSecret")

    assert "Cisco password must satisfy the Cisco setup wizard password policy" in error
    assert "one digit" in error
    assert "ValidSecret" not in error


def test_cisco_status_operator_findings_use_full_password_policy_without_secrets():
    service = CiscoModuleService()

    result = service.status(
        {
            "cfg": {
                "cisco_switch": {
                    "password": "lowercase12",
                    "enable_secret": "UPPERCASE12",
                    "management_ip": "192.168.1.2",
                    "subnet_mask": "255.255.255.0",
                    "gateway": "192.168.1.1",
                }
            }
        }
    )

    findings = result["status"]["operator_findings"]
    titles = {finding["title"] for finding in findings}
    text = repr(findings)
    assert "Cisco login password may fail modern password policy" in titles
    assert "Enable credential may be rejected by IOS XE" in titles
    assert "one uppercase letter" in text
    assert "one lowercase letter" in text
    assert "lowercase12" not in text
    assert "UPPERCASE12" not in text


def test_parse_cisco_show_version_ios_xe():
    output = """
Cisco IOS XE Software, Version 17.09.04a
Cisco IOS Software [Cupertino], C9300 Software (C9300-UNIVERSALK9-M), Version 17.09.04a
sw01 uptime is 2 weeks, 4 days
cisco C9300-48P (X86) processor with 8388608K/6147K bytes of memory.
"""

    parsed = parse_cisco_show_version(output)

    assert parsed["version"] == "17.09.04a"
    assert parsed["hostname"] == "sw01"
    assert parsed["model"] == "C9300-48P"
    assert parsed["platform"] == "C9300-UNIVERSALK9-M"


def test_cisco_service_discover_reports_missing_target():
    service = CiscoModuleService()

    result = service.discover({"cfg": {"cisco_switch": {}}})

    assert result["ok"] is False
    assert "IP is not set" in result["error"]


def test_cisco_status_distinguishes_live_and_desired_ports():
    service = CiscoModuleService()

    result = service.status(
        {
            "cfg": {
                "cisco_switch": {
                    "ports": {"GigabitEthernet1/0/1": {"profile": "client_device"}},
                    "last_port_discovery": {"interfaces": {"GigabitEthernet1/0/24": {"status": "connected"}}},
                    "last_running_config_backup": "interface GigabitEthernet1/0/24",
                }
            }
        }
    )

    status = result["status"]
    assert status["desired_port_count"] == 1
    assert status["discovered_interface_count"] == 1
    assert status["last_running_config_backup"] == "interface GigabitEthernet1/0/24"


def test_cisco_status_distinguishes_discovered_saved_and_ready_values():
    service = CiscoModuleService()

    result = service.status(
        {
            "cfg": {
                "ip_plan": {"switch": "192.168.1.2", "gateway": "192.168.1.1", "netmask": "255.255.255.0"},
                "cisco_switch": {
                    "management_ip": "",
                    "ip": "",
                    "last_console_bootstrap_check": {
                        "management_vlan": 10,
                        "current_management_ip": "192.168.1.50",
                        "current_subnet_mask": "255.255.255.0",
                        "default_gateway": "192.168.1.1",
                        "ssh_enabled": True,
                        "scp_enabled": True,
                    },
                },
            }
        }
    )

    status = result["status"]

    assert status["discovered_current"]["management_ip"] == "192.168.1.50"
    assert status["discovered_current"]["not_saved"] is True
    assert status["discovered_current"]["note"] == "Discovered, not saved to this kit yet."
    assert status["saved_kit_config"]["state_label"] == "Not saved yet"
    assert status["ready_to_apply"]["management_ip"] == "192.168.1.2"
    assert status["ready_to_apply"]["source"] == "Generated kit IP plan until Cisco values are saved"


def test_cisco_status_marks_stale_initial_dialog_bootstrap_failed():
    service = CiscoModuleService()

    result = service.status(
        {
            "cfg": {
                "cisco_switch": {
                    "connection_method": "ssh",
                    "last_bootstrap": {"ok": True, "management_ip": "192.168.1.2"},
                    "last_ssh_test": {"ok": False},
                    "last_serial_output": "Would you like to enter the initial configuration dialog? [yes/no]: terminal length 0\n% Please answer 'yes' or 'no'.",
                }
            }
        }
    )

    status = result["status"]

    assert status["connection_method"] == "console"
    assert status["last_bootstrap"]["ok"] is False
    assert "initial setup dialog" in status["last_bootstrap"]["error"]


def test_cisco_status_redacts_legacy_saved_debug_output():
    service = CiscoModuleService()
    cfg = {
        "shared_snmp": {
            "v3_auth_password": "SharedSnmpAuthSecret1!",
            "users": [{"auth_password": "SharedUserAuthSecret1!", "priv_password": "SharedUserPrivSecret1!"}],
        },
        "cisco_switch": {
            "password": "CiscoLoginSecret1!",
            "console_password": "CiscoConsoleSecret1!",
            "enable_password": "CiscoEnableSecret1!",
            "snmp": {
                "v3_auth_password": "CiscoSnmpAuthSecret1!",
                "v3_priv_password": "CiscoSnmpPrivSecret1!",
            },
            "last_serial_output": "Password: CiscoLoginSecret1!\nenable secret CiscoEnableSecret1!",
            "last_bootstrap": {"ok": False, "error": "Bootstrap echoed CiscoEnableSecret1!"},
            "last_discovery_error": "Discovery echoed CiscoLoginSecret1!",
            "last_show_version": "Version command echoed CiscoEnableSecret1!",
            "last_console_candidates": [{"port": "/dev/ttyUSB0", "raw_output": "Candidate had CiscoConsoleSecret1!"}],
            "last_console_probe_results": [{"output": "Probe had CiscoConsoleSecret1!"}],
            "last_console_suggestions": ["Try CiscoConsoleSecret1!"],
            "last_ssh_test": {"ok": False, "error": "SSH failed with CiscoLoginSecret1!"},
            "last_console_diagnostics": {"raw": "Console asked for CiscoConsoleSecret1!"},
            "last_console_management_state": "Console used CiscoConsoleSecret1!",
            "last_console_bootstrap_check": {"warnings": ["Check saw CiscoSnmpAuthSecret1!"]},
            "last_raw_port_discovery": "Port discovery output had CiscoLoginSecret1!",
            "last_config_preview": "username admin privilege 15 secret CiscoLoginSecret1!\nenable secret CiscoEnableSecret1!\nsnmp auth CiscoSnmpAuthSecret1!",
            "last_cisco_action": {"ok": False, "error": "Action echoed CiscoEnableSecret1!", "log_excerpt": "SharedUserPrivSecret1!"},
            "last_running_config_backup": "enable secret CiscoEnableSecret1!\nsnmp priv CiscoSnmpPrivSecret1!",
            "last_host_fix": {"output": "Host command printed SharedSnmpAuthSecret1!"},
        },
    }

    result = service.status({"cfg": cfg})

    status = result["status"]
    rendered_status = repr(status)
    for secret in [
        "CiscoLoginSecret1!",
        "CiscoConsoleSecret1!",
        "CiscoEnableSecret1!",
        "CiscoSnmpAuthSecret1!",
        "CiscoSnmpPrivSecret1!",
        "SharedSnmpAuthSecret1!",
        "SharedUserAuthSecret1!",
        "SharedUserPrivSecret1!",
    ]:
        assert secret not in rendered_status
    assert "********" in status["last_serial_output"]
    assert "********" in status["last_bootstrap"]["error"]
    assert "********" in status["last_console_management_state"]
    assert "********" in status["last_config_preview"]
    assert "********" in status["last_cisco_action"]["log_excerpt"]
    assert "********" in status["last_running_config_backup"]
    assert "********" in status["last_action_result"]["summary"]
    assert "CiscoLoginSecret1!" in cfg["cisco_switch"]["last_serial_output"]


def test_bootstrap_password_policy_blocks_before_serial(monkeypatch):
    service = CiscoModuleService()

    class FailIfOpened:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("CiscoSerialClient must not open when password policy fails")

    monkeypatch.setattr("app.modules.cisco.service.CiscoSerialClient", FailIfOpened)

    result = service.bootstrap_management(
        {
            "cfg": {
                "ip_plan": {"gateway": "192.168.1.1"},
                "cisco_switch": {
                    "console_port": "/dev/ttyUSB0",
                    "console_baud": 9600,
                    "management_ip": "192.168.1.2",
                    "subnet_mask": "255.255.255.0",
                    "gateway": "192.168.1.1",
                    "username": "admin",
                    "password": "short1A",
                    "enable_secret": "ValidSecret123",
                },
            }
        }
    )

    assert result["ok"] is False
    assert "Cisco password must satisfy the Cisco setup wizard password policy" in result["error"]
    assert "short1A" not in result["error"]


def test_console_state_machine_handles_forced_setup_wizard_and_selects_zero():
    class ScriptedConsole:
        def __init__(self):
            self.responses = iter(
                [
                    "Would you like to enter the initial configuration dialog? [yes/no]:",
                    "Enter enable secret:",
                    "Confirm enable secret:",
                    "0. Go to the IOS command prompt without saving this config\n1. Return back\n2. Save this configuration\nEnter your selection [2]:",
                    "Switch>",
                    "Password:",
                    "Switch#",
                ]
            )
            self.commands: list[tuple[str, bool]] = []

        def read_prompt(self):
            return next(self.responses)

        def run_command(self, command, redact=False, wait_seconds=3.0):
            self.commands.append((command, redact))
            return next(self.responses)

    console = ScriptedConsole()
    state_machine = CiscoConsoleBootstrapStateMachine(console)

    prompt_type, output, steps = state_machine.reach_privileged_exec(
        {
            "username": "admin",
            "password": "ValidSecret123",
            "enable_secret": "ValidSecret123",
            "wizard_password": "ValidSecret123",
        }
    )
    diagnostics = state_machine.diagnostics(prompt_type)

    assert prompt_type == "privileged"
    assert output.endswith("Switch#")
    assert [command for command, _redact in console.commands] == [
        "no",
        "ValidSecret123",
        "ValidSecret123",
        "0",
        "enable",
        "ValidSecret123",
    ]
    assert "2" not in [command for command, _redact in console.commands]
    assert console.commands[1][1] is True
    assert console.commands[2][1] is True
    assert console.commands[-1][1] is True
    assert any("initial configuration dialog; answered no" in step for step in steps)
    assert any("setup wizard password prompt; sent fallback wizard secret" in step for step in steps)
    assert any("setup wizard final menu; selected IOS command prompt" in step for step in steps)
    assert diagnostics["initial_dialog_answered_no"] is True
    assert diagnostics["forced_setup_wizard_detected"] is True
    assert diagnostics["final_menu_seen"] is True
    assert diagnostics["switch_user_exec_reached"] is True
    assert diagnostics["enable_password_prompt_reached"] is True
    assert "setup final menu appears again choose 0" in diagnostics["next_manual_recovery_step"]
    assert "never choose 2" in diagnostics["next_manual_recovery_step"]


def test_console_state_machine_stops_after_setup_password_policy_failure():
    class PolicyFailureConsole:
        def __init__(self):
            self.responses = iter(
                [
                    "Would you like to enter the initial configuration dialog? [yes/no]:",
                    "Enter enable secret:",
                    "% Password too weak; must contain upper case, lower case, and digits.",
                ]
            )
            self.commands: list[tuple[str, bool]] = []

        def read_prompt(self):
            return next(self.responses)

        def run_command(self, command, redact=False, wait_seconds=3.0):
            self.commands.append((command, redact))
            return next(self.responses)

    console = PolicyFailureConsole()
    state_machine = CiscoConsoleBootstrapStateMachine(console)

    prompt_type, output, steps = state_machine.reach_privileged_exec(
        {
            "username": "admin",
            "password": "ValidSecret123",
            "enable_secret": "ValidSecret123",
            "wizard_password": "ValidSecret123",
        }
    )
    diagnostics = state_machine.diagnostics(prompt_type)

    assert prompt_type == "setup_password_policy_failure"
    assert "Password too weak" in output
    assert [command for command, _redact in console.commands] == ["no", "ValidSecret123"]
    assert console.commands[-1][1] is True
    assert any("password policy failure; stopped before retrying" in step for step in steps)
    assert diagnostics["initial_dialog_answered_no"] is True
    assert diagnostics["forced_setup_wizard_detected"] is True
    assert diagnostics["password_policy_failure_detected"] is True
    assert diagnostics["next_manual_recovery_step"] == (
        "Set a policy-compliant Cisco password and enable secret in Access settings, then rerun Setup Console."
    )


def test_serial_management_config_saves_only_after_successful_config(monkeypatch):
    def make_client(rejected_command: str = ""):
        client = object.__new__(CiscoSerialClient)
        client.port = "/dev/ttyFAKE"
        client.baud = 9600
        commands: list[str] = []

        def fake_reach_privileged_exec(_config):
            return (
                "privileged",
                "Switch#",
                ["Reached privileged EXEC in fake console."],
                {"last_detected_state": "privileged", "last_safe_action": "none"},
            )

        def fake_run_command(command, redact=False, wait_seconds=2.0):
            commands.append(command)
            if command == rejected_command:
                return "% Invalid input detected at '^' marker.\nSwitch#"
            return "\nSwitch#"

        client._reach_privileged_exec = fake_reach_privileged_exec
        client.run_command = fake_run_command
        return client, commands

    config = {
        "hostname": "sw01",
        "management_vlan": 10,
        "management_ip": "192.168.1.2",
        "subnet_mask": "255.255.255.0",
        "gateway": "192.168.1.1",
        "domain_name": "lab.local",
        "username": "admin",
        "password": "ValidSecret123",
        "enable_secret": "ValidSecret123",
        "management_port": "GigabitEthernet1/0/1",
        "management_port_mode": "do_not_touch",
    }

    success_client, success_commands = make_client()
    success = success_client.apply_management_config(config)

    assert success.ok is True
    assert success_commands[-1] == "write memory"
    assert success_commands.count("write memory") == 1
    assert success_commands.index("write memory") > success_commands.index("end")
    serialized = success.as_dict(include_raw=True)
    assert "ValidSecret123" not in repr(serialized)
    assert "enable secret ********" in repr(serialized)
    assert "username admin privilege 15 secret ********" in repr(serialized)

    failed_client, failed_commands = make_client(rejected_command="ip ssh version 2")
    failed = failed_client.apply_management_config(config)

    assert failed.ok is False
    assert "write memory" not in failed_commands
    assert "rejected one or more bootstrap commands" in failed.error


def test_verify_console_bootstrap_reports_down_management_svi(monkeypatch):
    service = CiscoModuleService()

    class FakeConsole:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def read_prompt(self):
            return "Switch#"

        def run_command(self, command, wait_seconds=3.0):
            if command == "show ip interface brief":
                return """
Interface              IP-Address      OK? Method Status                Protocol
Vlan10                 192.168.1.2       YES manual down                  down
"""
            if command == "show vlan brief":
                return """
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
10   MANAGEMENT                       active
"""
            if command == "show interfaces status":
                return """
Port         Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1                         notconnect   10           auto   auto 10/100/1000BaseTX
"""
            if command == "show ip ssh":
                return "SSH Enabled - version 2.0"
            return "Switch#"

    monkeypatch.setattr("app.modules.cisco.service.CiscoSerialClient", FakeConsole)

    result = service.verify_console_bootstrap(
        {
            "cfg": {
                "cisco_switch": {
                    "console_port": "/dev/ttyUSB0",
                    "console_baud": 9600,
                    "management_vlan": 10,
                    "password": "secret",
                }
            }
        }
    )

    assert result["ok"] is False
    assert result["vlan_exists"] is True
    assert result["ssh_enabled"] is True
    assert any("Vlan10 is not up/up" in item for item in result["warnings"])
