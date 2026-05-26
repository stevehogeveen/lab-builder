from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def esxi_client(tmp_path: Path, monkeypatch):
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
    main.set_current_kit_name("ESXi-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def test_esxi_page_save_form_uses_shared_completion_feedback(esxi_client):
    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    assert 'hx-post="/save-esxi-settings"' in response.text
    assert 'data-action-title="Saving ESXi setup"' in response.text
    assert 'data-action-start="Saving the ESXi installer and post-config settings."' in response.text
    assert 'data-action-complete="ESXi setup saved."' in response.text
    assert '<button class="btn btn-primary action-button" type="submit">Save ESXi setup</button>' in response.text


def test_esxi_page_latest_receipt_open_log_uses_report_route(esxi_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Log Kit"
    main.save_kit_config(cfg)
    summary_path = main.HISTORY_DIR / "esxi-open-log-summary.yml"
    summary_path.write_text("scope: esxi\nstatus: Completed\n", encoding="utf-8")
    main.save_history(
        "ESXi-Log-Kit",
        [
            {
                "time": "2026-05-25 22:18:00",
                "scope": "esxi",
                "status": "Completed",
                "current_stage": "Finished",
                "run_summary_path": str(summary_path),
            }
        ],
    )

    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    assert "Last ESXi run" in response.text
    assert 'hx-post="/view-report"' in response.text
    assert 'data-action-title="Opening ESXi run log"' in response.text
    assert '<button class="btn action-button" type="submit">Open log</button>' in response.text
    assert 'name="return_page" value="esxi"' in response.text
    assert 'name="report_path"' in response.text
    assert str(summary_path) in response.text
    assert 'hx-post="/view-run-summary"' not in response.text

    open_response = esxi_client.post(
        "/view-report",
        data={"return_page": "esxi", "report_path": str(summary_path)},
    )
    assert open_response.status_code == 200
    assert "Report: esxi-open-log-summary.yml" in open_response.text
    assert "scope: esxi" in open_response.text
