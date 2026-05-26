from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def reports_client(tmp_path: Path, monkeypatch):
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
    main.set_current_kit_name("Reports-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def test_reports_page_wires_visible_controls_to_report_routes(reports_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Reports Control Kit"
    main.save_kit_config(cfg)
    report_path = main.export_current_kit_config_snapshot(cfg)
    run_summary_path = main.GENERATED_DIR / "run-summary-Reports-Control-Kit-ilo.yml"
    run_summary_path.write_text("scope: ilo\nstatus: Completed\n", encoding="utf-8")
    main.save_history(
        "Reports Control Kit",
        [
            {
                "time": "2026-05-25 22:50:00",
                "scope": "ilo",
                "status": "Completed",
                "current_stage": "Finished",
                "run_summary_path": str(run_summary_path),
                "config_summary": {"target_ip": "192.168.1.11"},
            }
        ],
    )

    response = reports_client.get("/configs?report_type=config")

    assert response.status_code == 200
    assert 'href="/history"' in response.text
    assert 'action="/configs" method="get"' in response.text
    assert 'name="report_query"' in response.text
    assert 'name="report_type"' in response.text
    assert '<button class="btn action-button w-full" type="submit">Search reports</button>' in response.text
    assert 'hx-post="/view-report"' in response.text
    assert 'action="/download-report" method="post"' in response.text
    assert 'name="return_page" value="configs"' in response.text
    assert 'data-action-title="Opening run bundle"' in response.text
    assert 'data-action-title="Opening saved report"' in response.text
    assert '<button class="btn action-button" type="submit">Open bundle</button>' in response.text
    assert '<button class="btn action-button" type="submit">View</button>' in response.text
    assert '<button class="btn action-button" type="submit">Download</button>' in response.text
    assert str(report_path) in response.text
    assert str(run_summary_path) in response.text
    assert '<a class="btn action-button" href="/configs?report_query=192.168.1.11">Related reports</a>' in response.text
    assert "Open bundle" in response.text
    assert "View" in response.text
    assert "Download" in response.text
    assert "Technical output" not in response.text

    view_response = reports_client.post(
        "/view-report",
        data={"return_page": "configs", "report_path": str(report_path)},
    )

    assert view_response.status_code == 200
    assert "Report opened" in view_response.text
    assert "Technical details" in view_response.text
    assert "Technical output" not in view_response.text
    assert f"Report: {report_path.name}" in view_response.text
    assert "Reports-Control-Kit" in view_response.text

    download_response = reports_client.post(
        "/download-report",
        data={"report_path": str(report_path)},
    )

    assert download_response.status_code == 200
    assert report_path.name in download_response.headers["content-disposition"]
    assert any(
        route.path == "/download-report" and "POST" in route.methods
        for route in main.app.routes
    )
