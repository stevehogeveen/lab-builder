from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def global_settings_client(tmp_path: Path, monkeypatch):
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
    main.set_current_kit_name("Global-Settings-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def _button_containing(html: str, marker: str) -> str:
    marker_index = html.index(marker)
    button_start = html.rfind("<button", 0, marker_index)
    button_end = html.find("</button>", marker_index)
    assert button_start != -1
    assert button_end != -1
    return html[button_start:button_end]


def test_global_settings_aliases_wire_visible_controls(global_settings_client):
    for path in ("/global-settings", "/configuration"):
        response = global_settings_client.get(path)

        assert response.status_code == 200
        assert "Global Settings" in response.text
        assert 'id="global-settings-form"' in response.text
        assert 'name="return_page" value="global_settings"' in response.text
        assert 'hx-post="/save-global-settings"' in response.text
        assert 'data-action-complete="Shared defaults saved."' in response.text
        assert 'type="submit">Save shared defaults' in response.text

        populate_button = _button_containing(response.text, 'hx-post="/populate-setup-sections"')
        assert 'type="button"' in populate_button
        assert 'hx-include="#global-settings-form"' in populate_button
        assert 'hx-target="#main-content"' in populate_button
        assert 'data-action-complete="Setup sections populated."' in populate_button
        assert "Populate setup sections from IP plan" in populate_button

        add_user_button = _button_containing(response.text, "snmpUsers.push")
        assert 'type="button"' in add_user_button
        assert "+ Add user" in add_user_button

        remove_user_button = _button_containing(response.text, "snmpUsers.splice(index, 1)")
        assert 'type="button"' in remove_user_button
        assert "Remove user" in remove_user_button
