from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def windows_client(tmp_path, monkeypatch):
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
    main.set_current_kit_name("Windows-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def test_windows_page_wires_actions_and_empty_last_status(windows_client):
    response = windows_client.get("/windows")

    assert response.status_code == 200
    assert 'id="windows-settings-form"' in response.text
    assert 'hx-post="/save-windows-settings"' in response.text
    assert 'hx-post="/probe-windows-vsphere" hx-include="#windows-settings-form"' in response.text
    assert 'hx-post="/probe-windows-winrm" hx-include="#windows-settings-form"' in response.text
    assert 'hx-post="/select-windows-ovf-template"' in response.text
    assert 'hx-post="/plan-windows-install" hx-include="#windows-settings-form"' in response.text
    assert 'href="/global-settings"' in response.text
    assert 'href="/modules/ovf-templates"' in response.text
    assert 'href="/execution"' in response.text
    assert "What happened last" in response.text
    assert "No Windows action recorded yet" in response.text


def test_windows_page_keeps_last_action_after_save(windows_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Receipt Kit"
    cfg["windows"]["admin_password"] = "ExistingWindowsSecret1!"
    cfg["windows"]["vsphere_password"] = "ExistingVsphereSecret1!"
    cfg["windows"]["winrm_password"] = "ExistingWinRmSecret1!"
    main.save_kit_config(cfg)

    response = windows_client.post(
        "/save-windows-settings",
        data={
            "return_page": "windows",
            "windows_vm_name": "win-lab",
            "windows_admin_password": "",
            "windows_vsphere_host": "192.168.1.10",
            "windows_vsphere_username": "root",
            "windows_vsphere_password": "",
            "windows_vsphere_datacenter": "ha-datacenter",
            "windows_vsphere_datastore": "datastore1",
            "windows_vsphere_network": "VM Network",
            "windows_vsphere_folder": "",
            "windows_vsphere_resource_pool": "",
            "windows_winrm_username": "Administrator",
            "windows_winrm_password": "",
            "windows_winrm_port": "5986",
            "windows_winrm_use_https": "on",
            "included_windows": "on",
        },
    )

    assert response.status_code == 200
    assert "Windows setup saved" in response.text
    assert "ExistingWindowsSecret1!" not in response.text
    assert "ExistingVsphereSecret1!" not in response.text
    assert "ExistingWinRmSecret1!" not in response.text

    page_response = windows_client.get("/windows")

    assert page_response.status_code == 200
    assert "What happened last" in page_response.text
    assert "Windows setup saved" in page_response.text
    assert "Saved the Windows setup values for this kit." in page_response.text
    assert "ExistingWindowsSecret1!" not in page_response.text
    assert "ExistingVsphereSecret1!" not in page_response.text
    assert "ExistingWinRmSecret1!" not in page_response.text
