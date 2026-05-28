from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

import app.overnight_finalize as overnight_finalize
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
    inspect_overnight_artifacts,
    normalize_overnight_mode,
    reconcile_overnight_needs_attention_reasons,
    request_hardware_stop,
    run_overnight_hardware,
    scan_text_for_secrets,
    should_stop_hardware_actions,
    write_cisco_skipped_artifacts,
    write_ilo_skipped_artifacts,
)


def write_complete_nonsecret_artifacts(writer: OvernightArtifactWriter) -> None:
    writer.write_config_snapshot({}, OvernightHardwareConfig.from_mapping({}, {}))
    for relative in [
        "ilo/discovery.json",
        "ilo/power-state-before.json",
        "ilo/boot-options.json",
        "ilo/virtual-media.json",
        "ilo/final-state.json",
    ]:
        writer.write_json(relative, {"ok": True, "status": "captured"})
    for relative in [
        "cisco/console-detect.txt",
        "cisco/initial-session.txt",
        "cisco/show-version.txt",
        "cisco/running-config-before.txt",
        "cisco/setup-transcript.txt",
        "cisco/running-config-after.txt",
    ]:
        writer.write_text(relative, "captured\n")


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

    evening_start = datetime(2026, 5, 27, 17, 57)
    assert should_stop_hardware_actions(datetime(2026, 5, 27, 20, 31), run_started_at=evening_start) is False
    assert finalization_deadline_ok(datetime(2026, 5, 27, 20, 31), run_started_at=evening_start) is True
    assert should_stop_hardware_actions(datetime(2026, 5, 28, 5, 30), run_started_at=evening_start) is True
    assert finalization_deadline_ok(datetime(2026, 5, 28, 5, 59, 59), run_started_at=evening_start) is True
    assert finalization_deadline_ok(datetime(2026, 5, 28, 6, 0), run_started_at=evening_start) is False

    morning_start = datetime(2026, 5, 27, 5, 20)
    assert should_stop_hardware_actions(datetime(2026, 5, 27, 5, 29, 59), run_started_at=morning_start) is False
    assert should_stop_hardware_actions(datetime(2026, 5, 27, 5, 30), run_started_at=morning_start) is True
    assert finalization_deadline_ok(datetime(2026, 5, 27, 5, 59, 59), run_started_at=morning_start) is True
    assert finalization_deadline_ok(datetime(2026, 5, 27, 6, 0), run_started_at=morning_start) is False


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
    assert "Git commit or push did not complete." not in result["needs_attention_reasons"]
    assert not any(command[:2] == ["git", "commit"] for command in calls)
    report = writer.morning_report_path.read_text(encoding="utf-8")
    assert "Possible secrets were found" in report
    assert "Git commit or push did not complete." not in report
    assert "verysecretvalue" not in report
    summary_text = writer.summary_path.read_text(encoding="utf-8")
    summary = yaml.safe_load(summary_text)
    finalization = summary["finalization"]
    assert finalization["secret_scan_result"].startswith("blocked")
    assert isinstance(finalization["secret_findings"], list)
    assert finalization["secret_findings"][0]["excerpt"] == "[redacted possible secret]"
    assert "verysecretvalue" not in summary_text


def test_secret_scan_ignores_code_variable_plumbing():
    assert scan_text_for_secrets("password = str(cfg.get('password') or '')\n") == []
    secretish_line = "api_key = '" + ("abcd1234" * 2) + "'\n"
    assert scan_text_for_secrets(secretish_line)


def test_clean_secret_scan_status_is_not_redacted_in_reports(tmp_path):
    repo = tmp_path
    writer = OvernightArtifactWriter(repo / "artifacts" / "runs" / "overnight" / "20260527-052000-ilo-cisco")
    writer.initialize_placeholders()
    write_complete_nonsecret_artifacts(writer)

    def runner(command: list[str], cwd: Path) -> CommandCapture:
        if command == ["git", "status", "--short", "--branch"]:
            return CommandCapture(command, 0, "## feature/finalizer...origin/feature/finalizer\n", "")
        return CommandCapture(command, 0, "", "")

    result = finalize_overnight_run(
        writer,
        repo_root=repo,
        run_tests=False,
        allow_git=False,
        command_runner=runner,
        now=datetime(2026, 5, 27, 5, 20),
    )

    report = writer.morning_report_path.read_text(encoding="utf-8")
    summary = yaml.safe_load(writer.summary_path.read_text(encoding="utf-8"))

    assert result["secret_scan_result"] == "clean"
    assert "- Secret scan: clean" in report
    assert summary["finalization"]["secret_scan_result"] == "clean"
    assert summary["finalization"]["secret_findings"] == []


def test_missing_and_pending_artifacts_are_reported_clearly(tmp_path):
    writer = OvernightArtifactWriter(tmp_path / "run")
    writer.initialize_placeholders()
    (writer.run_dir / "ilo" / "discovery.json").unlink()

    health = inspect_overnight_artifacts(writer.run_dir)

    assert health["ok"] is False
    assert "ilo/discovery.json" in health["missing"]
    assert "cisco/initial-session.txt" in health["pending"]


def test_skipped_artifacts_are_reported_clearly(tmp_path):
    writer = OvernightArtifactWriter(tmp_path / "run")
    writer.initialize_placeholders()
    writer.write_config_snapshot({}, OvernightHardwareConfig.from_mapping({}, {}))
    writer.write_text("live-job.log", "hardware stopped\n")
    writer.write_yaml("trace.yml", {"events": []})
    writer.write_yaml("summary.yml", {"status": "Needs attention"})
    writer.write_text("MORNING_READY.md", "# Morning Ready\n\nStatus: Needs attention\n")
    reason = "Hardware stop marker is present; no additional hardware actions will start."

    write_ilo_skipped_artifacts(writer, reason, now=datetime(2026, 5, 27, 5, 25))
    write_cisco_skipped_artifacts(writer, reason, now=datetime(2026, 5, 27, 5, 25))
    health = inspect_overnight_artifacts(writer.run_dir)

    assert health["ok"] is False
    assert not health["pending"]
    assert len(health["skipped"]) == 11
    assert "ilo/discovery.json" in health["skipped"]
    assert "cisco/initial-session.txt" in health["skipped"]


def test_finalize_records_skipped_artifacts_as_needs_attention(tmp_path):
    repo = tmp_path
    writer = OvernightArtifactWriter(repo / "artifacts" / "runs" / "overnight" / "20260527-052500-ilo-cisco")
    writer.initialize_placeholders()
    writer.write_config_snapshot({}, OvernightHardwareConfig.from_mapping({}, {}))
    writer.write_text("live-job.log", "hardware stopped\n")
    writer.write_yaml("trace.yml", {"events": []})
    reason = "Hardware stop marker is present; no additional hardware actions will start."
    write_ilo_skipped_artifacts(writer, reason, now=datetime(2026, 5, 27, 5, 25))
    write_cisco_skipped_artifacts(writer, reason, now=datetime(2026, 5, 27, 5, 25))

    def runner(command: list[str], cwd: Path) -> CommandCapture:
        if command == ["git", "status", "--short", "--branch"]:
            return CommandCapture(command, 0, "## feature/skipped\n", "")
        return CommandCapture(command, 0, "", "")

    result = finalize_overnight_run(
        writer,
        repo_root=repo,
        run_tests=False,
        allow_git=False,
        command_runner=runner,
        now=datetime(2026, 5, 27, 5, 25),
    )

    report = writer.morning_report_path.read_text(encoding="utf-8")

    assert result["status_label"] == "Needs attention"
    assert any(reason.startswith("Expected artifacts were skipped:") for reason in result["needs_attention_reasons"])
    assert len(result["artifact_health"]["skipped"]) == 11
    assert "- Skipped: ilo/discovery.json" in report
    assert "Expected artifacts still contain placeholders" not in report


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
    write_complete_nonsecret_artifacts(writer)
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
    assert result["compile_result"] == "not run"
    assert result["artifact_folder"] == str(writer.run_dir)
    assert result["git_status_before"].startswith("## feature/finalizer")
    assert result["git_status_after"].strip() == "## feature/finalizer...origin/feature/finalizer"
    assert hardware_stop_requested(writer.run_dir)
    assert "## Git Status Before" in report
    assert "Artifact folder:" in report
    assert "- Compile: not run" in report
    assert any(command[:2] == ["git", "commit"] for command in calls)


def test_evening_finalize_uses_next_morning_deadline(tmp_path):
    repo = tmp_path
    tracked_file = repo / "app" / "overnight_run.py"
    tracked_file.parent.mkdir(parents=True)
    tracked_file.write_text("print('safe scheduler path')\n", encoding="utf-8")
    writer = OvernightArtifactWriter(repo / "artifacts" / "runs" / "overnight" / "20260527-175700-ilo-cisco")
    writer.initialize_placeholders()
    write_complete_nonsecret_artifacts(writer)
    calls: list[list[str]] = []
    status_calls = 0

    def runner(command: list[str], cwd: Path) -> CommandCapture:
        nonlocal status_calls
        calls.append(command)
        if command == ["git", "status", "--short", "--branch"]:
            status_calls += 1
            stdout = "## feature/evening...origin/feature/evening\n M app/overnight_run.py\n" if status_calls == 1 else "## feature/evening...origin/feature/evening\n"
            return CommandCapture(command, 0, stdout, "")
        if command[:2] == ["git", "add"]:
            return CommandCapture(command, 0, "", "")
        if command[:3] == ["git", "diff", "--cached"]:
            return CommandCapture(command, 0, "app/overnight_run.py\n", "")
        if command == ["git", "branch", "--show-current"]:
            return CommandCapture(command, 0, "feature/evening\n", "")
        if command[:2] == ["git", "commit"]:
            return CommandCapture(command, 0, "[feature/evening abc123] Finalize\n", "")
        if command == ["git", "rev-parse", "HEAD"]:
            return CommandCapture(command, 0, "abc123def456\n", "")
        if command == ["git", "push", "origin", "feature/evening"]:
            return CommandCapture(command, 0, "pushed\n", "")
        return CommandCapture(command, 0, "", "")

    result = finalize_overnight_run(
        writer,
        repo_root=repo,
        run_tests=False,
        allow_git=True,
        commit_paths=["app/overnight_run.py"],
        command_runner=runner,
        now=datetime(2026, 5, 27, 20, 31),
    )

    assert result["status_label"] == "Ready for review"
    assert result["push_result"] == "pushed"
    assert result["finalization_deadline"] == "2026-05-28 06:00 local"
    assert result["finalization_timing"] == "before deadline"
    assert not any("deadline was missed" in reason for reason in result["needs_attention_reasons"])
    assert not any("at or after 5:30 AM" in note for note in result["notes"])
    assert any(command[:2] == ["git", "commit"] for command in calls)
    report = writer.morning_report_path.read_text(encoding="utf-8")
    assert "- Finalization deadline: 2026-05-28 06:00 local" in report
    assert "- Finalization timing: before deadline" in report


def test_finalize_morning_report_records_needs_attention_reason_and_compile(tmp_path):
    repo = tmp_path
    (repo / "tests").mkdir()
    (repo / "tests" / "test_overnight_run.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    (repo / "app").mkdir()
    writer = OvernightArtifactWriter(repo / "artifacts" / "runs" / "overnight" / "20260527-052500-ilo-cisco")
    writer.initialize_placeholders()
    write_complete_nonsecret_artifacts(writer)

    def runner(command: list[str], cwd: Path) -> CommandCapture:
        if command == ["git", "status", "--short", "--branch"]:
            return CommandCapture(command, 0, "## feature/finalizer\n", "")
        if command[1:3] == ["-m", "pytest"]:
            return CommandCapture(command, 1, "failed\n", "")
        if command[1:3] == ["-m", "compileall"]:
            return CommandCapture(command, 0, "compiled\n", "")
        return CommandCapture(command, 0, "", "")

    result = finalize_overnight_run(
        writer,
        repo_root=repo,
        run_tests=True,
        allow_git=False,
        command_runner=runner,
        python_executable="/venv/bin/python",
        now=datetime(2026, 5, 27, 5, 40),
    )

    report = writer.morning_report_path.read_text(encoding="utf-8")
    assert result["status_label"] == "Needs attention"
    assert result["compile_result"] == "passed"
    assert "## Needs Attention Reasons" in report
    assert "Pytest did not pass" in report
    assert "- Compile: passed" in report


def test_deadline_reconciliation_keeps_real_missed_deadline(tmp_path):
    run_dir = tmp_path / "20260527-052500-ilo-cisco"
    run_dir.mkdir()
    reasons = ["The 6:00 AM finalization deadline was missed."]

    filtered, info = reconcile_overnight_needs_attention_reasons(
        reasons,
        run_dir=run_dir,
        generated_at="2026-05-27T06:10:00-04:00",
    )

    assert filtered == reasons
    assert info["deadline"] == "2026-05-27 06:00 local"
    assert info["status"] == "missed_deadline"


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
    assert '"status": "skipped"' in (writer.run_dir / "ilo" / "discovery.json").read_text(encoding="utf-8")
    assert "Cisco console discovery skipped" in (writer.run_dir / "cisco" / "initial-session.txt").read_text(encoding="utf-8")


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


def test_cisco_no_console_port_writes_guidance_without_connecting(tmp_path):
    writer = OvernightArtifactWriter(tmp_path / "run")
    writer.initialize_placeholders()

    result = collect_cisco_console_discovery(
        {"cisco_switch": {"console_port": "", "console_baud": 9600}},
        OvernightHardwareConfig.from_mapping({}, {}),
        writer,
        diagnostics_fn=lambda: {"serial_imported": True, "ordered_ports": []},
        discovery_factory=lambda: SimpleNamespace(scan=lambda: []),
        client_factory=lambda port, baud: (_ for _ in ()).throw(AssertionError("should not connect")),
    )

    transcript = (writer.run_dir / "cisco" / "initial-session.txt").read_text(encoding="utf-8")
    assert result["ok"] is False
    assert "No saved or detected Cisco console port" in transcript
    assert "next_steps:" in transcript


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


def test_overnight_latest_run_status_appears_in_api_and_ui(client):
    run_dir = main.ARTIFACTS_DIR / "runs" / "overnight" / "20260527-052500-ilo-cisco"
    writer = OvernightArtifactWriter(run_dir)
    writer.initialize_placeholders()
    write_complete_nonsecret_artifacts(writer)
    writer.write_summary(
        {
            "status": "Needs attention",
            "finalization": {
                "status_label": "Needs attention",
                "needs_attention_reasons": ["Pytest did not pass; review command output in MORNING_READY.md."],
            },
        }
    )
    writer.morning_report_path.write_text(
        "# Morning Ready\n\nStatus: Needs attention\n\n## Needs Attention Reasons\n- Pytest did not pass; review command output in MORNING_READY.md.\n",
        encoding="utf-8",
    )

    api_response = client.get("/api/ui/overnight-hardware")
    payload = api_response.json()
    assert payload["operator"]["latest_run_status"] == "Needs attention"
    assert payload["operator"]["latest_run_folder"] == str(run_dir)
    assert "Pytest did not pass" in payload["operator"]["needs_attention"]

    page_response = client.get("/overnight-hardware")
    assert str(run_dir) in page_response.text
    assert "Pytest did not pass" in page_response.text


def test_overnight_operator_mode_summarizes_pending_artifact_details(client):
    run_dir = main.ARTIFACTS_DIR / "runs" / "overnight" / "20260527-060000-ilo-cisco"
    writer = OvernightArtifactWriter(run_dir)
    writer.initialize_placeholders()
    raw_reason = (
        "Expected artifacts still contain placeholders: "
        "ilo/discovery.json, cisco/initial-session.txt, cisco/show-version.txt"
    )
    writer.write_summary(
        {
            "status": "Needs attention",
            "finalization": {
                "status_label": "Needs attention",
                "needs_attention_reasons": [raw_reason],
            },
        }
    )
    writer.morning_report_path.write_text(
        f"# Morning Ready\n\nStatus: Needs attention\n\n## Needs Attention Reasons\n- {raw_reason}\n",
        encoding="utf-8",
    )

    response = client.get("/api/ui/overnight-hardware")
    payload = response.json()

    assert response.status_code == 200
    assert payload["operator"]["needs_attention"] == "Hardware evidence is still pending (3 artifacts)."
    assert "ilo/discovery.json" not in payload["operator"]["needs_attention"]
    assert payload["operator"]["needs_attention_reasons"] == ["Hardware evidence is still pending (3 artifacts)."]
    assert payload["debug"]["latest_run"]["needs_attention_reasons"] == [raw_reason]


def test_overnight_operator_mode_summarizes_skipped_artifact_details(client):
    run_dir = main.ARTIFACTS_DIR / "runs" / "overnight" / "20260527-061500-ilo-cisco"
    writer = OvernightArtifactWriter(run_dir)
    writer.initialize_placeholders()
    writer.write_config_snapshot({}, OvernightHardwareConfig.from_mapping({}, {}))
    writer.write_text("live-job.log", "hardware stopped\n")
    writer.write_yaml("trace.yml", {"events": []})
    reason = "Hardware stop marker is present; no additional hardware actions will start."
    write_ilo_skipped_artifacts(writer, reason, now=datetime(2026, 5, 27, 5, 35))
    write_cisco_skipped_artifacts(writer, reason, now=datetime(2026, 5, 27, 5, 35))
    writer.write_summary(
        {
            "status": "Needs attention",
            "finalization": {
                "status_label": "Needs attention",
                "needs_attention_reasons": [],
            },
        }
    )
    writer.morning_report_path.write_text("# Morning Ready\n\nStatus: Needs attention\n", encoding="utf-8")

    response = client.get("/api/ui/overnight-hardware")
    payload = response.json()

    assert response.status_code == 200
    assert payload["operator"]["needs_attention"] == "Hardware evidence was skipped (11 artifacts)."
    assert payload["operator"]["next"] == "Start a new discovery_only run before the hardware stop window to collect the skipped hardware evidence."
    assert len(payload["debug"]["latest_run"]["artifact_health"]["skipped"]) == 11
    assert "ilo/discovery.json" in payload["debug"]["latest_run"]["artifact_health"]["skipped"]


def test_overnight_operator_mode_reconciles_stale_running_job(client):
    run_dir = main.ARTIFACTS_DIR / "runs" / "overnight" / "20260527-175700-ilo-cisco"
    writer = OvernightArtifactWriter(run_dir)
    writer.initialize_placeholders()
    writer.write_yaml(
        "summary.yml",
        {
            "run_folder": str(run_dir),
            "generated_at": "2026-05-27T20:35:11-04:00",
            "status": "Needs attention",
            "finalization": {
                "status_label": "Needs attention",
                "needs_attention_reasons": [
                    "The 6:00 AM finalization deadline was missed.",
                    "Expected artifacts still contain placeholders: ilo/discovery.json",
                ],
            },
        }
    )
    writer.morning_report_path.write_text(
        "# Morning Ready\n\nStatus: Needs attention\n\n## Needs Attention Reasons\n- Expected artifacts still contain placeholders: ilo/discovery.json\n",
        encoding="utf-8",
    )
    cfg = main.load_kit_config()
    main.save_job(
        cfg["site"]["name"],
        {
            "status": "Running",
            "scope": "overnight_hardware",
            "root_scope": "overnight_hardware",
            "overnight_mode": "discovery_only",
            "current_stage": "Finalization",
            "progress_percent": 72,
            "logs": ["[OVERNIGHT] finalization: running - Stopping hardware work and starting morning finalization."],
            "run_id": run_dir.name,
            "run_bundle_dir": str(run_dir),
        },
    )

    response = client.get("/api/ui/overnight-hardware")
    payload = response.json()

    assert response.status_code == 200
    assert payload["operator"]["status"] == "Needs attention"
    assert payload["operator"]["current_stage"] == "Finalization complete"
    assert payload["operator"]["completion"] == 100
    assert payload["operator"]["last"] == "Latest run finalized as Needs attention."
    assert payload["operator"]["next"] == "Start a new discovery_only run before the hardware stop window to collect the pending artifacts."
    assert payload["operator"]["needs_attention"] == "Hardware evidence is still pending (1 artifact)."
    assert "ilo/discovery.json" not in payload["operator"]["needs_attention"]
    assert "The 6:00 AM finalization deadline was missed." not in payload["operator"]["needs_attention_reasons"]
    assert payload["debug"]["latest_run"]["needs_attention_reasons"] == ["Expected artifacts still contain placeholders: ilo/discovery.json"]
    assert payload["debug"]["latest_run"]["deadline_reconciliation"]["removed_stale_deadline_reason"] is True


def test_cli_finalizer_syncs_matching_overnight_job_state(tmp_path):
    repo = tmp_path
    artifacts_root = repo / "artifacts"
    run_dir = artifacts_root / "runs" / "overnight" / "20260527-175700-ilo-cisco"
    run_dir.mkdir(parents=True)
    (run_dir / "config-snapshot.yml").write_text(
        yaml.safe_dump({"kit_config": {"site": {"name": "Kit-01"}}}, sort_keys=False),
        encoding="utf-8",
    )
    jobs_dir = artifacts_root / "jobs"
    jobs_dir.mkdir(parents=True)
    job_path = jobs_dir / "Kit-01_job.yml"
    job_path.write_text(
        yaml.safe_dump(
            {
                "status": "Running",
                "execution_mode": "overnight_hardware",
                "scope": "overnight_hardware",
                "root_scope": "overnight_hardware",
                "current_stage": "Finalization",
                "progress_percent": 72,
                "completed_steps": 72,
                "total_steps": 100,
                "logs": ["[OVERNIGHT] finalization: running - Stopping hardware work."],
                "trace_events": [],
                "run_id": run_dir.name,
                "run_bundle_dir": str(run_dir),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    synced = overnight_finalize.sync_finalized_job_state(
        repo_root=repo,
        artifacts_root=artifacts_root,
        run_dir=run_dir,
        result={
            "status_label": "Needs attention",
            "test_result": "passed",
            "compile_result": "passed",
            "push_result": "not run",
            "secret_scan_result": "blocked (1 finding(s))",
            "secret_findings": [{"path": "example.txt", "line": 1, "excerpt": "do not persist this"}],
            "needs_attention_reasons": ["Possible secrets were found; auto-commit and push were blocked."],
        },
    )

    updated = yaml.safe_load(job_path.read_text(encoding="utf-8"))
    job_state_text = (run_dir / "job-state.yml").read_text(encoding="utf-8")
    job_state = yaml.safe_load(job_state_text)

    assert synced == job_path
    assert updated["status"] == "Needs attention"
    assert updated["current_stage"] == "Finalization complete"
    assert updated["progress_percent"] == 100
    assert updated["completed_steps"] == 100
    assert "Finalization result: Needs attention." in updated["logs"][-1]
    assert job_state["status"] == "Needs attention"
    assert job_state["finalization"]["secret_findings_count"] == 1
    assert "secret_findings" not in job_state["finalization"]
    assert "do not persist this" not in job_state_text


def test_cli_finalizer_sync_refreshes_existing_finalization_event(tmp_path):
    repo = tmp_path
    artifacts_root = repo / "artifacts"
    run_dir = artifacts_root / "runs" / "overnight" / "20260527-175700-ilo-cisco"
    run_dir.mkdir(parents=True)
    current_timestamp = "2026-05-28T05:25:55.226624-04:00"
    stale_timestamp = "2026-05-28T01:21:43.328871-04:00"
    final_message = "Finalization result: Needs attention."
    (run_dir / "config-snapshot.yml").write_text(
        yaml.safe_dump({"kit_config": {"site": {"name": "Kit-01"}}}, sort_keys=False),
        encoding="utf-8",
    )
    (run_dir / "summary.yml").write_text(
        yaml.safe_dump(
            {"generated_at": current_timestamp, "status": "Needs attention", "finalization": {}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "trace.yml").write_text(
        yaml.safe_dump(
            {
                "events": [
                    {
                        "timestamp": current_timestamp,
                        "stage": "finalization",
                        "status": "needs_attention",
                        "progress": 100,
                        "message": final_message,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    jobs_dir = artifacts_root / "jobs"
    jobs_dir.mkdir(parents=True)
    job_path = jobs_dir / "Kit-01_job.yml"
    job_path.write_text(
        yaml.safe_dump(
            {
                "status": "Needs attention",
                "scope": "overnight_hardware",
                "root_scope": "overnight_hardware",
                "current_stage": "Finalization complete",
                "progress_percent": 100,
                "logs": [],
                "trace_events": [
                    {
                        "timestamp": stale_timestamp,
                        "stage": "finalization",
                        "status": "needs_attention",
                        "progress": 100,
                        "message": final_message,
                        "source": "overnight_finalize_cli",
                    }
                ],
                "run_id": run_dir.name,
                "run_bundle_dir": str(run_dir),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    overnight_finalize.sync_finalized_job_state(
        repo_root=repo,
        artifacts_root=artifacts_root,
        run_dir=run_dir,
        result={
            "status_label": "Needs attention",
            "test_result": "passed",
            "compile_result": "passed",
            "push_result": "pushed",
            "secret_scan_result": "clean",
            "needs_attention_reasons": ["Expected artifacts were skipped: ilo/discovery.json"],
        },
    )

    updated = yaml.safe_load(job_path.read_text(encoding="utf-8"))
    job_state = yaml.safe_load((run_dir / "job-state.yml").read_text(encoding="utf-8"))
    matching_events = [
        event
        for event in updated["trace_events"]
        if event.get("stage") == "finalization" and event.get("message") == final_message
    ]

    assert len(matching_events) == 1
    assert matching_events[0]["timestamp"] == current_timestamp
    assert matching_events[0]["source"] == "overnight_finalize_cli"
    assert job_state["events"] == updated["trace_events"]


def test_cli_finalizer_sync_reconciles_stale_deadline_snapshot(tmp_path):
    repo = tmp_path
    artifacts_root = repo / "artifacts"
    run_dir = artifacts_root / "runs" / "overnight" / "20260527-175700-ilo-cisco"
    run_dir.mkdir(parents=True)
    (run_dir / "config-snapshot.yml").write_text(
        yaml.safe_dump({"kit_config": {"site": {"name": "Kit-01"}}}, sort_keys=False),
        encoding="utf-8",
    )
    (run_dir / "summary.yml").write_text(
        yaml.safe_dump(
            {
                "generated_at": "2026-05-27T20:35:11-04:00",
                "status": "Needs attention",
                "finalization": {
                    "needs_attention_reasons": ["The 6:00 AM finalization deadline was missed."],
                    "notes": [
                        "Hardware stop marker was written at or after 5:30 AM local time; verify no hardware action overran the stop window.",
                        "Auto-commit/push skipped because the 6:00 AM finalization deadline was missed.",
                    ],
                    "secret_findings": [{"path": "example.txt", "line": 1, "excerpt": "do not persist this"}],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "STOP_HARDWARE_WORK").write_text(
        yaml.safe_dump(
            {
                "requested_at": "2026-05-27T20:31:11-04:00",
                "reason": "finalization scheduler",
                "deadline": "05:30",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "MORNING_READY.md").write_text(
        "# Morning Ready\n\n"
        "Status: Needs attention\n\n"
        "## Needs Attention Reasons\n"
        "- The 6:00 AM finalization deadline was missed.\n",
        encoding="utf-8",
    )
    jobs_dir = artifacts_root / "jobs"
    jobs_dir.mkdir(parents=True)
    job_path = jobs_dir / "Kit-01_job.yml"
    job_path.write_text(
        yaml.safe_dump(
            {
                "status": "Running",
                "scope": "overnight_hardware",
                "root_scope": "overnight_hardware",
                "current_stage": "Finalization",
                "progress_percent": 72,
                "logs": [],
                "trace_events": [],
                "run_id": run_dir.name,
                "run_bundle_dir": str(run_dir),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    overnight_finalize.sync_finalized_job_state(
        repo_root=repo,
        artifacts_root=artifacts_root,
        run_dir=run_dir,
        result={
            "status_label": "Needs attention",
            "test_result": "passed",
            "compile_result": "passed",
            "push_result": "not run",
            "secret_scan_result": "clean",
            "needs_attention_reasons": [
                "The 6:00 AM finalization deadline was missed.",
                "Expected artifacts still contain placeholders: ilo/discovery.json",
                "Git commit or push did not complete.",
            ],
        },
    )

    updated = yaml.safe_load(job_path.read_text(encoding="utf-8"))
    job_state = yaml.safe_load((run_dir / "job-state.yml").read_text(encoding="utf-8"))
    summary = yaml.safe_load((run_dir / "summary.yml").read_text(encoding="utf-8"))
    morning_ready = (run_dir / "MORNING_READY.md").read_text(encoding="utf-8")

    assert "The 6:00 AM finalization deadline was missed." not in updated["overnight_finalization"]["needs_attention_reasons"]
    assert updated["overnight_finalization"]["needs_attention_reasons"] == ["Expected artifacts still contain placeholders: ilo/discovery.json"]
    assert updated["overnight_finalization"]["finalization_completed_at"] == "2026-05-27 20:35 local"
    assert updated["overnight_finalization"]["finalization_deadline"] == "2026-05-28 06:00 local"
    assert updated["overnight_finalization"]["finalization_timing"] == "before deadline"
    assert job_state["finalization"] == updated["overnight_finalization"]
    assert summary["finalization"]["needs_attention_reasons"] == ["Expected artifacts still contain placeholders: ilo/discovery.json"]
    assert summary["finalization"]["notes"] == []
    assert "do not persist this" not in (run_dir / "summary.yml").read_text(encoding="utf-8")
    assert "The 6:00 AM finalization deadline was missed." not in morning_ready
    assert "Git commit or push did not complete." not in morning_ready
    assert "Hardware stop marker was written at or after 5:30 AM" not in morning_ready
    assert "Finalization timing: before deadline" in morning_ready
    assert "do not persist this" not in morning_ready


def test_overnight_start_blocks_when_existing_run_is_active(client, monkeypatch):
    cfg = main.load_kit_config()
    main.save_job(
        cfg["site"]["name"],
        {
            "status": "Running",
            "scope": "overnight_hardware",
            "root_scope": "overnight_hardware",
            "execution_mode": "overnight_hardware",
            "overnight_mode": "discovery_only",
            "current_stage": "iLO Discovery",
            "progress_percent": 18,
            "completed_steps": 18,
            "total_steps": 100,
            "logs": ["[OVERNIGHT] ilo_discovery: running - Connecting to Redfish service root."],
        },
    )

    def fail_initialize(*args, **kwargs):
        raise AssertionError("a second overnight hardware run should not be initialized")

    monkeypatch.setattr(main, "initialize_overnight_artifacts", fail_initialize)

    response = client.post("/overnight-hardware/start", data={"mode": "discovery_only"})

    assert response.status_code == 200
    assert "another overnight hardware run is still active" in response.text
