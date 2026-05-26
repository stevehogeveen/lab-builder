from app.modules.cisco.service import CiscoModuleService, parse_cisco_show_version


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
                    "last_bootstrap": {"ok": True, "management_ip": "10.10.8.2"},
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
Vlan10                 10.10.8.2       YES manual down                  down
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
