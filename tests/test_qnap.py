from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def qnap_client(tmp_path, monkeypatch):
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
    main.set_current_kit_name("Qnap-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def test_qnap_page_wires_save_form_and_navigation(qnap_client):
    response = qnap_client.get("/qnap")

    assert response.status_code == 200
    assert 'hx-post="/save-qnap-settings"' in response.text
    assert 'name="qnap_hostname"' in response.text
    assert 'name="qnap_username"' in response.text
    assert 'name="qnap_password"' in response.text
    assert 'name="included_qnap"' in response.text
    assert 'href="/global-settings"' in response.text
    assert 'href="/execution"' in response.text
    assert "What happened last" in response.text
    assert "No QNAP action recorded yet" in response.text


def test_save_qnap_settings_receipt_reports_inclusion_and_preserves_secret(qnap_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "QNAP Receipt Kit"
    cfg["qnap"]["password"] = "ExistingQnapSecret1!"
    main.save_kit_config(cfg)

    response = qnap_client.post(
        "/save-qnap-settings",
        data={
            "return_page": "qnap",
            "qnap_hostname": "qnap-lab",
            "qnap_username": "admin",
            "qnap_password": "",
            "included_qnap": "on",
        },
    )

    saved = main.load_kit_config("QNAP-Receipt-Kit")
    assert response.status_code == 200
    assert saved["qnap"]["password"] == "ExistingQnapSecret1!"
    assert saved["included"]["qnap"] is True
    assert "QNAP setup saved" in response.text
    assert "Included in kit: Yes" in response.text
    assert "ExistingQnapSecret1!" not in response.text

    response = qnap_client.post(
        "/save-qnap-settings",
        data={
            "return_page": "qnap",
            "qnap_hostname": "qnap-lab",
            "qnap_username": "admin",
            "qnap_password": "",
        },
    )

    saved = main.load_kit_config("QNAP-Receipt-Kit")
    assert response.status_code == 200
    assert saved["included"]["qnap"] is False
    assert saved["qnap"]["password"] == "ExistingQnapSecret1!"
    assert "Included in kit: No" in response.text
    assert "ExistingQnapSecret1!" not in response.text

    page_response = qnap_client.get("/qnap")

    assert page_response.status_code == 200
    assert "What happened last" in page_response.text
    assert "QNAP setup saved" in page_response.text
    assert "Saved the QNAP setup values for this kit." in page_response.text
    assert "ExistingQnapSecret1!" not in page_response.text


def test_save_qnap_settings_handles_partial_kit_config(qnap_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "QNAP Partial Kit"
    cfg["qnap"] = None
    cfg["included"] = None
    main.save_kit_config(cfg)

    response = qnap_client.post(
        "/save-qnap-settings",
        data={
            "return_page": "qnap",
            "qnap_hostname": "qnap-partial",
            "qnap_username": "admin",
            "qnap_password": "NewQnapSecret1!",
            "included_qnap": "on",
        },
    )

    saved = main.load_kit_config("QNAP-Partial-Kit")
    assert response.status_code == 200
    assert saved["qnap"]["hostname"] == "qnap-partial"
    assert saved["qnap"]["username"] == "admin"
    assert saved["qnap"]["password"] == "NewQnapSecret1!"
    assert saved["included"]["qnap"] is True
    assert "QNAP setup saved" in response.text
    assert "NewQnapSecret1!" not in response.text
