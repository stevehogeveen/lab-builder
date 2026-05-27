from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def physical_pages_client(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    media_dir = tmp_path / "media"
    artifacts_dir = tmp_path / "artifacts"
    exports_dir = artifacts_dir / "exports"
    paths = {
        "CONFIG_DIR": config_dir,
        "KITS_DIR": config_dir / "kits",
        "CURRENT_KIT_FILE": config_dir / "current_kit.txt",
        "MEDIA_DIR": media_dir,
        "FIRMWARE_UPLOAD_DIR": media_dir / "firmware",
        "ARTIFACTS_DIR": artifacts_dir,
        "GENERATED_DIR": artifacts_dir / "generated",
        "JOBS_DIR": artifacts_dir / "jobs",
        "HISTORY_DIR": artifacts_dir / "history",
        "RUNS_DIR": artifacts_dir / "runs",
        "EXPORTS_DIR": exports_dir,
        "BUILD_OUTPUT_DIR": exports_dir / "builds",
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
    monkeypatch.setenv("LAB_BUILDER_VALIDATE_ESXI_MEDIA_URL", "0")
    monkeypatch.setenv("LAB_BUILDER_LIVE_RUN_CENTER_CHECKS", "0")
    monkeypatch.setattr(
        main,
        "scan_upgrade_media",
        lambda: {"root": str(media_dir), "latest": {}, "counts": {}, "candidates": []},
    )
    main.set_current_kit_name("Physical-Pages-Operator-Contract-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("path", "page_marker", "operator_anchor"),
    [
        ("/cisco", "Cisco operator checkpoint", 'id="cisco-operator-mode"'),
        ("/ilo", "iLO operator checkpoint", 'id="ilo-operator-mode"'),
        ("/esxi", "ESXi operator checkpoint", 'id="esxi-operator-mode"'),
        ("/modules/ovf-templates", "OVF/OVA operator checkpoint", 'id="ovf-operator-mode"'),
    ],
)
def test_physical_pages_render_operator_and_debug_contract(physical_pages_client, path, page_marker, operator_anchor):
    response = physical_pages_client.get(path)

    assert response.status_code == 200
    assert page_marker in response.text
    assert operator_anchor in response.text
    operator_section = response.text.split(operator_anchor, 1)[1].split("</section>", 1)[0]
    for label in [
        "Operator Mode",
        "Next step",
        "Completion state",
        "Last result",
        "Logs/status",
        "Open Debug Mode/details",
    ]:
        assert label in operator_section
    for generic_status in ["No log yet", "No validation result yet"]:
        assert generic_status not in operator_section
    assert "Debug Mode/details" in response.text


@pytest.mark.parametrize(
    ("path", "purpose_text"),
    [
        ("/cisco", "Bootstrap console, then approve Access Settings."),
        ("/ilo", "Connect to HPE iLO, capture current state, and prepare safe setup actions."),
        ("/esxi", "Build media, mount through iLO, and start the physical install only from Run Center."),
        ("/modules/ovf-templates", "Register reusable OVF/OVA VM template folders for Windows, Ubuntu, and future VM workflows."),
    ],
)
def test_physical_pages_state_what_the_page_is_for(physical_pages_client, path, purpose_text):
    response = physical_pages_client.get(path)

    assert response.status_code == 200
    assert purpose_text in response.text


@pytest.mark.parametrize(
    ("path", "expected_labels"),
    [
        (
            "/cisco",
            [
                "Saved Lab Builder kit config",
                "Discovered/current switch state",
                "Planned/suggested values",
            ],
        ),
        (
            "/ilo",
            [
                "Saved Lab Builder kit config",
                "Discovered/current iLO state",
                "Planned/suggested values",
            ],
        ),
        (
            "/esxi",
            [
                "Saved Lab Builder kit config",
                "Discovered/current install state",
                "Planned/suggested values",
            ],
        ),
        (
            "/modules/ovf-templates",
            [
                "Saved Lab Builder template selection",
                "Discovered/current file state",
                "Planned/suggested values",
            ],
        ),
    ],
)
def test_physical_pages_separate_saved_current_and_suggested_values(
    physical_pages_client,
    path,
    expected_labels,
):
    response = physical_pages_client.get(path)

    assert response.status_code == 200
    for label in expected_labels:
        assert label in response.text


@pytest.mark.parametrize(
    ("path", "operator_anchor", "detail_only_labels"),
    [
        (
            "/cisco",
            'id="cisco-operator-mode"',
            [
                "Last action log excerpt",
                "Current Switch Config",
                "Raw status payload",
            ],
        ),
        (
            "/ilo",
            'id="ilo-operator-mode"',
            [
                "Detected Redfish capability keys",
                "Recovery suggestions",
                "Standard iLO policy",
                "More local iLO users",
            ],
        ),
        (
            "/esxi",
            'id="esxi-operator-mode"',
            [
                "Virtual media URL",
                "Installer details",
                "Post-install policy",
                "Recovery suggestions",
            ],
        ),
        (
            "/modules/ovf-templates",
            'id="ovf-operator-mode"',
            [
                "Discovered files",
                "Recovery suggestions",
                "Descriptor:",
                "Directory:",
            ],
        ),
    ],
)
def test_physical_operator_mode_keeps_detail_diagnostics_out_of_checkpoint(
    physical_pages_client,
    path,
    operator_anchor,
    detail_only_labels,
):
    response = physical_pages_client.get(path)

    assert response.status_code == 200
    operator_section = response.text.split(operator_anchor, 1)[1].split("</section>", 1)[0]
    assert "Open Debug Mode/details" in operator_section
    for label in detail_only_labels:
        assert label not in operator_section


def test_physical_page_get_renders_do_not_instantiate_hardware_clients(physical_pages_client, monkeypatch):
    import app.modules.cisco.service as cisco_service
    import app.stages.esxi.runtime as esxi_runtime

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Physical setup page GET must not instantiate hardware clients")

    monkeypatch.setattr(main, "ILOClient", fail_if_called)
    monkeypatch.setattr(main, "VsphereClient", fail_if_called)
    monkeypatch.setattr(cisco_service, "CiscoSerialClient", fail_if_called)
    monkeypatch.setattr(cisco_service, "CiscoSerialDiscovery", fail_if_called)
    monkeypatch.setattr(cisco_service, "CiscoSSHClient", fail_if_called)
    monkeypatch.setattr(esxi_runtime.requests, "get", fail_if_called)

    for path in ["/cisco", "/ilo", "/esxi", "/modules/ovf-templates"]:
        response = physical_pages_client.get(path)
        assert response.status_code == 200


def test_netapp_tomorrow_checklist_preserves_mock_boundary_and_flow_contract():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "netapp-tomorrow-manual-test-checklist.md").read_text(encoding="utf-8")

    assert "Do not run any real NetApp API, SSH, SP, serial, or console action" in text
    assert "mocks, dry-runs, route tests, template tests, and checklist review" in text
    assert "Do not overwrite a saved kit that intentionally uses another network." in text
    assert "192.168.1.0/24" in text
    for value in [
        "192.168.1.13",
        "192.168.1.14",
        "192.168.1.45",
        "192.168.1.46",
        "192.168.1.47",
        "192.168.1.48",
        "192.168.1.51",
        "192.168.1.52",
        "192.168.1.53",
        "192.168.1.54",
    ]:
        assert value in text
    for label in [
        "Context",
        "Targets",
        "Credentials",
        "Current State",
        "Preflight",
        "Plan",
        "Execute",
        "Monitor",
        "Evidence",
        "Next Step",
    ]:
        assert label in text


def test_netapp_tomorrow_checklist_preserves_plan_taxonomy_and_secret_boundary():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "netapp-tomorrow-manual-test-checklist.md").read_text(encoding="utf-8")

    for action_type in ["create", "update", "skip", "manual", "blocked", "destructive", "read-only"]:
        assert action_type in text
    for boundary in [
        "require an explicit operator action for every real state-changing or destructive operation",
        "no password, token, cookie, or private key appears in logs or artifacts",
        "no secrets appear in page logs, debug output, artifacts, or test output",
        "what changed, what was skipped, what was blocked, what was verified",
    ]:
        assert boundary in text
