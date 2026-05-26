from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def ilo_client(tmp_path: Path, monkeypatch):
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
    main.set_current_kit_name("iLO-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def test_ilo_page_wires_actions_and_visible_last_status(ilo_client):
    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    assert 'hx-post="/save-ilo-settings"' in response.text
    assert 'hx-post="/export-ilo-inventory"' in response.text
    assert 'href="/storage"' in response.text
    assert 'href="/execution"' in response.text
    assert '<details class="card identity-soft-card" open>' in response.text
    assert "What happened last" in response.text
    assert "No iLO run has finished for this kit yet." in response.text


def test_ilo_page_latest_receipt_open_log_uses_report_route(ilo_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO Log Kit"
    main.save_kit_config(cfg)
    summary_path = main.HISTORY_DIR / "ilo-open-log-summary.yml"
    summary_path.write_text("scope: ilo\nstatus: Completed\n", encoding="utf-8")
    main.save_history(
        "iLO-Log-Kit",
        [
            {
                "time": "2026-05-25 23:19:00",
                "scope": "ilo",
                "status": "Completed",
                "current_stage": "Finished",
                "run_summary_path": str(summary_path),
            }
        ],
    )

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    assert "Last iLO run" in response.text
    assert '<details class="card identity-soft-card" open>' in response.text
    assert 'hx-post="/view-report"' in response.text
    assert 'data-action-title="Opening iLO run log"' in response.text
    assert '<button class="btn action-button" type="submit">Open log</button>' in response.text
    assert 'name="return_page" value="ilo"' in response.text
    assert 'name="report_path"' in response.text
    assert str(summary_path) in response.text
    assert 'hx-post="/view-run-summary"' not in response.text

    open_response = ilo_client.post(
        "/view-report",
        data={"return_page": "ilo", "report_path": str(summary_path)},
    )
    assert open_response.status_code == 200
    assert "Report: ilo-open-log-summary.yml" in open_response.text
    assert "scope: ilo" in open_response.text
