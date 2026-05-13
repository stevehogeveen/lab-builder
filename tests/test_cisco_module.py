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
