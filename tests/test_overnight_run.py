from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.overnight_run import (
    DESTRUCTIVE_FLAG_DEFAULTS,
    CommandCapture,
    OvernightArtifactWriter,
    OvernightHardwareConfig,
    collect_cisco_console_discovery,
    collect_ilo_discovery,
    decide_finalization_git_action,
    finalization_deadline_ok,
    finalize_overnight_run,
    hardware_stop_requested,
    normalize_overnight_mode,
    request_hardware_stop,
    run_overnight_hardware,
    scan_text_for_secrets,
    should_stop_hardware_actions,
)


def test_overnight_mode_validation_and_defaults():
    cfg = {"cisco_switch": {"console_port": "/dev/ttyUSB0", "console_baud": 115200}}
    run_config = OvernightHardwareConfig.from_mapping({}, cfg)

    assert run_config.mode == "discovery_only"
    assert run_config.ilo_host == "192.168.1.200"
    assert run_config.cisco_console_port == "/dev/ttyUSB0"
    assert run_config.cisco_console_baud == 115200
    assert run_config.requires_safety_confirmation is False
    assert run_config.destructive_flags == DESTRUCTIVE_FLAG_DEFAULTS
    assert all(value is False for value in run_config.destructive_flags.values())

    guided = OvernightHardwareConfig.from_mapping({"mode": "guided_setup"}, cfg)
    assert guided.requires_safety_confirmation is True
    flagged_discovery = OvernightHardwareConfig.from_mapping({"allow_power_cycle": "on"}, cfg)
    assert flagged_discovery.requires_safety_confirmation is True

    with pytest.raises(ValueError):
        normalize_overnight_mode("wipe_everything")


def test_overnight_hardware_stop_and_finalization_deadline():
    assert should_stop_hardware_actions(datetime(2026, 5, 27, 5, 30)) is True
    assert should_stop_hardware_actions(datetime(2026, 5, 27, 5, 29, 59)) is False
    assert finalization_deadline_ok(datetime(2026, 5, 27, 5, 59, 59)) is True
    assert finalization_deadline_ok(datetime(2026, 5, 27, 6, 0)) is False


def test_secret_scan_blocks_auto_commit(tmp_path):
    repo = tmp_path
    writer = OvernightArtifactWriter(repo / "artifacts" / "runs" / "overnight" / "20260527-010000-ilo-cisco")
    writer.initialize_placeholders()
    secret_config_line = "enable " + "secret 5 " + "verysecretvalue\n"
    writer.write_text("cisco/running-config-before.txt", secret_config_line)
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> CommandCapture:
        calls.append(command)
        if command[:2] == ["git", "status"]:
            return CommandCapture(command, 0, "## test-branch...origin/test-branch\n", "")
        return CommandCapture(command, 0, "", "")

    result = finalize_overnight_run(
        writer,
        repo_root=repo,
        run_tests=False,
        allow_git=True,
        command_runner=runner,
        now=datetime(2026, 5, 27, 5, 45),
    )

    assert result["status_label"] == "Needs attention"
    assert result["secret_findings"]
    assert not any(command[:2] == ["git", "commit"] for command in calls)
    assert "Possible secrets were found" in writer.morning_report_path.read_text(encoding="utf-8")


def test_secret_scan_ignores_code_variable_plumbing():
    assert scan_text_for_secrets("password = str(cfg.get('password') or '')\n") == []
    secretish_line = "api_key = '" + ("abcd1234" * 2) + "'\n"
    assert scan_text_for_secrets(secretish_line)


def test_finalization_decision_allows_git_only_when_clean():
    decision = decide_finalization_git_action(
        allow_git=True,
        tests_ok=True,
        secret_findings_count=0,
        deadline_ok=True,
    )

    assert decision.should_commit_push is True
    assert decision.notes == ()


@pytest.mark.parametrize(
    ("kwargs", "expected_note"),
    [
        ({"allow_git": False}, "Auto-commit/push disabled"),
        ({"tests_ok": False}, "Tests or compileall failed"),
        ({"secret_findings_count": 1}, "Possible secrets were found"),
        ({"deadline_ok": False}, "6:00 AM finalization deadline was missed"),
    ],
)
def test_finalization_decision_blocks_unsafe_git(kwargs, expected_note):
    values = {
        "allow_git": True,
        "tests_ok": True,
        "secret_findings_count": 0,
        "deadline_ok": True,
    }
    values.update(kwargs)

    decision = decide_finalization_git_action(**values)

    assert decision.should_commit_push is False
    assert any(expected_note in note for note in decision.notes)


def test_finalize_records_git_statuses_and_push_result(tmp_path):
    repo = tmp_path
    tracked_file = repo / "app" / "overnight_run.py"
    tracked_file.parent.mkdir(parents=True)
    tracked_file.write_text("print('safe scheduler path')\n", encoding="utf-8")
    writer = OvernightArtifactWriter(repo / "artifacts" / "runs" / "overnight" / "20260527-052000-ilo-cisco")
    writer.initialize_placeholders()
    calls: list[list[str]] = []
    status_calls = 0

    def runner(command: list[str], cwd: Path) -> CommandCapture:
        nonlocal status_calls
        calls.append(command)
        if command == ["git", "status", "--short", "--branch"]:
            status_calls += 1
            stdout = "## feature/finalizer...origin/feature/finalizer\n M app/overnight_run.py\n" if status_calls == 1 else "## feature/finalizer...origin/feature/finalizer\n"
            return CommandCapture(command, 0, stdout, "")
        if command[:2] == ["git", "add"]:
            return CommandCapture(command, 0, "", "")
        if command[:3] == ["git", "diff", "--cached"]:
            return CommandCapture(command, 0, "app/overnight_run.py\n", "")
        if command == ["git", "branch", "--show-current"]:
            return CommandCapture(command, 0, "feature/finalizer\n", "")
        if command[:2] == ["git", "commit"]:
            return CommandCapture(command, 0, "[feature/finalizer abc123] Finalize\n", "")
        if command == ["git", "rev-parse", "HEAD"]:
            return CommandCapture(command, 0, "abc123def456\n", "")
        if command == ["git", "push", "origin", "feature/finalizer"]:
            return CommandCapture(command, 0, "pushed\n", "")
        return CommandCapture(command, 0, "", "")

    result = finalize_overnight_run(
        writer,
        repo_root=repo,
        run_tests=False,
        allow_git=True,
        commit_paths=["app/overnight_run.py"],
        command_runner=runner,
        now=datetime(2026, 5, 27, 5, 20),
    )

    report = writer.morning_report_path.read_text(encoding="utf-8")
    assert result["status_label"] == "Ready for review"
    assert result["branch"] == "feature/finalizer"
    assert result["commit_sha"] == "abc123def456"
    assert result["push_result"] == "pushed"
    assert result["artifact_folder"] == str(writer.run_dir)
    assert result["git_status_before"].startswith("## feature/finalizer")
    assert result["git_status_after"].strip() == "## feature/finalizer...origin/feature/finalizer"
    assert hardware_stop_requested(writer.run_dir)
    assert "## Git Status Before" in report
    assert "Artifact folder:" in report
    assert any(command[:2] == ["git", "commit"] for command in calls)


def test_hardware_stop_marker_prevents_hardware_collectors(tmp_path):
    writer = OvernightArtifactWriter(tmp_path / "run")
    writer.initialize_placeholders()
    request_hardware_stop(writer, now=datetime(2026, 5, 27, 5, 25))
    calls: list[str] = []

    def blocked_ilo_factory(**kwargs):
        calls.append("ilo")
        raise AssertionError("iLO discovery should not start after stop marker")

    def blocked_cisco_client(port: str, baud: int):
        calls.append("cisco")
        raise AssertionError("Cisco discovery should not start after stop marker")

    result = run_overnight_hardware(
        {},
        OvernightHardwareConfig.from_mapping({}, {}),
        writer,
        repo_root=tmp_path,
        ilo_client_factory=blocked_ilo_factory,
        cisco_diagnostics_fn=lambda: {"ordered_ports": []},
        cisco_discovery_factory=lambda: SimpleNamespace(scan=lambda: []),
        cisco_client_factory=blocked_cisco_client,
        finalizer=lambda *args, **kwargs: {"status_label": "Ready for review"},
        now_fn=lambda: datetime(2026, 5, 27, 5, 25),
    )

    assert calls == []
    assert result["finalization"]["status_label"] == "Ready for review"


class FakeIloClient:
    def get_service_root(self):
        return {"Name": "Service Root", "RedfishVersion": "1.18.0"}

    def get_managers(self):
        return ["/redfish/v1/Managers/1"]

    def get_systems(self):
        return ["/redfish/v1/Systems/1"]

    def get_manager(self, manager_path="/redfish/v1/Managers/1"):
        return {"@odata.id": manager_path, "Model": "iLO 6", "FirmwareVersion": "3.00"}

    def get_system(self, system_path="/redfish/v1/Systems/1"):
        return {
            "@odata.id": system_path,
            "Name": "Server",
            "Model": "ProLiant",
            "PowerState": "On",
            "Boot": {"BootSourceOverrideEnabled": "Disabled", "BootSourceOverrideTarget": "None"},
        }

    def get_virtual_media(self, manager_path="/redfish/v1/Managers/1"):
        return [{"@odata.id": f"{manager_path}/VirtualMedia/2", "Inserted": False, "Image": ""}]

    def collect_boot_option_inventory(self, system_path=None):
        return {"system_path": system_path, "boot": {"enabled": "Disabled"}, "boot_options": []}

    def get_summary(self):
        return {"manager_model": "iLO 6", "power_state": "On"}


def test_mocked_ilo_discovery_writes_raw_json_artifacts(tmp_path):
    cfg = {"ilo": {"username": "Administrator", "password": "secret"}}
    run_config = OvernightHardwareConfig.from_mapping({}, {})
    writer = OvernightArtifactWriter(tmp_path / "run")
    writer.initialize_placeholders()

    result = collect_ilo_discovery(
        cfg,
        run_config,
        writer,
        client_factory=lambda **_: FakeIloClient(),
    )

    assert result["ok"] is True
    assert "https://192.168.1.200/redfish/v1/" in (writer.run_dir / "ilo" / "discovery.json").read_text(encoding="utf-8")
    assert '"PowerState": "On"' in (writer.run_dir / "ilo" / "power-state-before.json").read_text(encoding="utf-8")
    assert "boot_options" in (writer.run_dir / "ilo" / "boot-options.json").read_text(encoding="utf-8")


class FakeCiscoClient:
    def __init__(self, port: str, baud: int):
        self.port = port
        self.baud = baud
        self.commands: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read_prompt(self):
        return "Switch#"

    def run_command(self, command: str, wait_seconds: float = 2.0):
        self.commands.append(command)
        if command == "show version":
            return "Cisco IOS XE Software, Version 17.09.05\nSwitch#"
        if command == "show running-config":
            return "version 17.9\nhostname Switch\nSwitch#"
        return "Switch#"


def test_mocked_cisco_console_discovery_is_read_only(tmp_path):
    cfg = {"cisco_switch": {"console_port": "/dev/ttyUSB0", "console_baud": 9600}}
    run_config = OvernightHardwareConfig.from_mapping({}, cfg)
    writer = OvernightArtifactWriter(tmp_path / "run")
    writer.initialize_placeholders()
    fake_client = FakeCiscoClient("/dev/ttyUSB0", 9600)

    result = collect_cisco_console_discovery(
        cfg,
        run_config,
        writer,
        diagnostics_fn=lambda: {"serial_imported": True, "ordered_ports": ["/dev/ttyUSB0"]},
        discovery_factory=lambda: SimpleNamespace(scan=lambda: []),
        client_factory=lambda port, baud: fake_client,
    )

    assert result["ok"] is True
    assert fake_client.commands == ["terminal length 0", "show version", "show running-config"]
    assert "no write memory command executed" in (writer.run_dir / "cisco" / "setup-transcript.txt").read_text(encoding="utf-8").lower()
    assert "Version 17.09.05" in (writer.run_dir / "cisco" / "show-version.txt").read_text(encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    artifacts_dir = tmp_path / "artifacts"
    media_dir = tmp_path / "media"
    exports_dir = artifacts_dir / "exports"
    paths = {
        "CONFIG_DIR": config_dir,
        "KITS_DIR": config_dir / "kits",
        "CURRENT_KIT_FILE": config_dir / "current_kit.txt",
        "ARTIFACTS_DIR": artifacts_dir,
        "GENERATED_DIR": artifacts_dir / "generated",
        "JOBS_DIR": artifacts_dir / "jobs",
        "HISTORY_DIR": artifacts_dir / "history",
        "RUNS_DIR": artifacts_dir / "runs",
        "EXPORTS_DIR": exports_dir,
        "BUILD_OUTPUT_DIR": exports_dir / "builds",
        "MEDIA_DIR": media_dir,
        "FIRMWARE_UPLOAD_DIR": media_dir / "firmware",
        "ILO_CONFIG_EXPORT_DIR": artifacts_dir / "history" / "ilo-configs",
        "CONFIG_EXPORT_DIR": artifacts_dir / "history" / "configs",
        "LIVE_ILO_CONFIG_DIR": artifacts_dir / "history" / "ilo-live-configs",
        "ILO_INVENTORY_DIR": artifacts_dir / "history" / "ilo-inventory",
        "ILO_LIVE_EXPORT_DIR": exports_dir / "ilo" / "live",
        "STORAGE_RAID_EXPORT_DIR": exports_dir / "storage-raid",
        "DEBUG_BUNDLES_DIR": artifacts_dir / "debug-bundles",
    }
    for value in paths.values():
        if isinstance(value, Path) and value.suffix == "":
            value.mkdir(parents=True, exist_ok=True)
    for name, value in paths.items():
        monkeypatch.setattr(main, name, value)
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {"root": str(media_dir), "latest": {}, "counts": {}, "candidates": []})
    main.save_kit_config(main.default_config())
    with TestClient(main.app) as test_client:
        yield test_client


def test_overnight_ui_exposes_operator_and_debug_modes(client):
    response = client.get("/overnight-hardware")

    assert response.status_code == 200
    assert "Operator Mode" in response.text
    assert "Debug Mode" in response.text
    assert "Start Overnight Hardware Run" in response.text
    assert "discovery_only" in response.text
    assert "allow_esxi_install" in response.text
    assert "Safety confirmation sheet" in response.text
    assert "Raw paths, logs, traces, API output, and transcripts" in response.text


def test_overnight_react_api_exposes_safe_defaults(client):
    response = client.get("/api/ui/overnight-hardware")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_mode"] == "discovery_only"
    assert payload["targets"]["ilo"] == "192.168.1.200"
    assert all(value is False for value in payload["destructive_flags"].values())
