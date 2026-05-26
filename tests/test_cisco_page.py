from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def cisco_client(tmp_path: Path, monkeypatch):
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
    main.set_current_kit_name("Cisco-Page-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def test_cisco_console_and_current_config_actions_use_shared_feedback_metadata(cisco_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Page Test Kit"
    cfg["cisco_switch"].update(
        {
            "console_port": "/dev/ttyUSB0",
            "console_baud": 9600,
            "username": "admin",
            "password": "CiscoSecret123!",
        }
    )
    main.save_kit_config(cfg)

    response = cisco_client.get("/cisco")

    assert response.status_code == 200
    assert 'hx-post="/modules/cisco/test-console-access"' in response.text
    assert 'hx-post="/modules/cisco/trust-console-adapter"' in response.text
    assert 'hx-post="/modules/cisco/check-current-config"' in response.text
    assert 'hx-post="/modules/cisco/test-ssh"' in response.text
    assert 'class="btn action-button" type="button" hx-post="/modules/cisco/test-console-access"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/trust-console-adapter"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/check-current-config"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/test-ssh"' in response.text
    assert 'data-action-title="Testing Cisco console access"' in response.text
    assert "Checking the selected serial console adapter without changing switch configuration." in response.text
    assert 'data-action-title="Trusting Cisco console adapter"' in response.text
    assert "Saving the selected serial console adapter for this kit." in response.text
    assert 'data-action-title="Checking Cisco current config"' in response.text
    assert "Reading VLAN, management IP, gateway, SSH, and SCP from the selected console path." in response.text
    assert 'data-action-complete="Cisco current config check finished."' in response.text
    assert 'data-action-title="Testing Cisco SSH"' in response.text
    assert "Connecting to the saved Cisco management IP with the saved switch credentials." in response.text
    assert 'data-action-complete="Cisco SSH test finished."' in response.text
    assert "CiscoSecret123!" not in response.text

    assert any(
        route.path == "/modules/cisco/test-console-access" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/trust-console-adapter" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/check-current-config" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/test-ssh" and "POST" in route.methods
        for route in main.app.routes
    )
