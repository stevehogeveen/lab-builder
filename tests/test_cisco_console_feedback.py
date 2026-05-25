from __future__ import annotations

from app.cisco import CiscoSerialCandidate
import app.modules.cisco.service as cisco_service


class FakeDiscovery:
    candidates: list[CiscoSerialCandidate] = []

    def scan(self) -> list[CiscoSerialCandidate]:
        return list(self.candidates)


def _diagnostics(**overrides):
    data = {
        "serial_imported": True,
        "list_ports_imported": True,
        "user": "labuser",
        "group_names": ["labuser"],
        "ordered_ports": ["/dev/ttyUSB0"],
        "by_id_ports": [],
        "ttyusb_ports": ["/dev/ttyUSB0"],
        "ttyacm_ports": [],
        "device_access": [{"path": "/dev/ttyUSB0", "readable": True, "writable": True}],
    }
    data.update(overrides)
    return data


def test_console_discovery_reports_no_serial_adapters(monkeypatch):
    monkeypatch.setattr(cisco_service, "CiscoSerialDiscovery", FakeDiscovery)
    monkeypatch.setattr(cisco_service, "serial_runtime_diagnostics", lambda: _diagnostics(ordered_ports=[], ttyusb_ports=[]))
    FakeDiscovery.candidates = []

    result = cisco_service.CiscoModuleService().discover_console({"cfg": {}})

    assert result["ok"] is False
    assert "No USB serial console adapter" in result["error"]
    assert any("/dev/ttyUSB" in suggestion for suggestion in result["suggestions"])
    assert result["diagnostics"]["error_summary"] == result["error"]


def test_console_discovery_reports_permission_denied(monkeypatch):
    monkeypatch.setattr(cisco_service, "CiscoSerialDiscovery", FakeDiscovery)
    monkeypatch.setattr(
        cisco_service,
        "serial_runtime_diagnostics",
        lambda: _diagnostics(device_access=[{"path": "/dev/ttyUSB0", "readable": False, "writable": False}]),
    )
    FakeDiscovery.candidates = [
        CiscoSerialCandidate(port="/dev/ttyUSB0", baud=9600, error="Permission denied: '/dev/ttyUSB0'")
    ]

    result = cisco_service.CiscoModuleService().discover_console({"cfg": {}})

    assert result["ok"] is False
    assert "cannot open" in result["error"]
    assert result["diagnostics"]["permission_denied"] is True
    assert any("dialout" in suggestion for suggestion in result["suggestions"])


def test_console_discovery_reports_open_adapter_without_prompt(monkeypatch):
    monkeypatch.setattr(cisco_service, "CiscoSerialDiscovery", FakeDiscovery)
    monkeypatch.setattr(cisco_service, "serial_runtime_diagnostics", lambda: _diagnostics())
    FakeDiscovery.candidates = [
        CiscoSerialCandidate(port="/dev/ttyUSB0", baud=9600, raw_output="", score=0)
    ]

    result = cisco_service.CiscoModuleService().discover_console({"cfg": {}})

    assert result["ok"] is False
    assert "opened successfully" in result["error"]
    assert any("console cable" in suggestion for suggestion in result["suggestions"])


def test_console_discovery_allows_responding_adapter_without_confirmed_prompt(monkeypatch):
    monkeypatch.setattr(cisco_service, "CiscoSerialDiscovery", FakeDiscovery)
    monkeypatch.setattr(cisco_service, "serial_runtime_diagnostics", lambda: _diagnostics())
    FakeDiscovery.candidates = [
        CiscoSerialCandidate(port="/dev/ttyUSB0", baud=9600, raw_output="unrecognized console text", score=0)
    ]

    result = cisco_service.CiscoModuleService().discover_console({"cfg": {}})

    assert result["ok"] is True
    assert result["error"] == "Console responded but Cisco prompt was not confirmed"
    assert result["candidates"][0]["port"] == "/dev/ttyUSB0"
    assert result["probe_results"][0]["prompt_unconfirmed"] is True
    assert any("Trust selected adapter" in suggestion for suggestion in result["suggestions"])
