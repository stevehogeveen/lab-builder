from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def storage_client(tmp_path: Path, monkeypatch):
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
    main.set_current_kit_name("Storage-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def _storage_artifact_discovery() -> dict:
    controller_path = "/redfish/v1/Systems/1/Storage/DE009000"
    drives = []
    for bay in range(1, 9):
        is_os_drive = bay <= 2
        drives.append(
            {
                "id": str(bay),
                "bay": str(bay),
                "model": "SSD-480" if is_os_drive else "HDD-1200",
                "serial_number": f"DRIVE{bay:02d}",
                "size_gib": 480 if is_os_drive else 1200,
                "media_type": "SSD" if is_os_drive else "HDD",
                "protocol": "SAS",
                "status": "OK / Enabled",
                "path": f"{controller_path}/Drives/{bay}",
                "controller_path": controller_path,
            }
        )
    return {
        "summary": {
            "server": {
                "model": "ProLiant DL380 Gen11",
                "product_name": "DL380",
                "generation": "Gen11",
                "serial_number": "STORAGEARTIFACT01",
            },
            "ilo": {"model": "iLO 6", "version": "iLO 6", "firmware": "3.00"},
            "capabilities": {
                "standard_redfish_storage": True,
                "hpe_smart_storage": False,
                "standard_storage_path": controller_path,
                "hpe_smart_storage_paths": [],
            },
            "standard_redfish_storage": {
                "controllers": [
                    {
                        "path": controller_path,
                        "name": "Smart Array MR416i-o",
                        "model": "MR416i-o",
                        "firmware_version": "1.98",
                        "manufacturer": "HPE",
                        "status": "OK / Enabled",
                    }
                ],
                "volumes": [],
                "drives": drives,
            },
            "hpe_smart_storage": {"controllers": [], "volumes": [], "drives": []},
        },
        "raw": {"source_host": "192.168.1.50"},
    }


def test_storage_page_latest_receipt_open_log_uses_report_route(storage_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Log Kit"
    main.save_kit_config(cfg)
    summary_path = main.HISTORY_DIR / "storage-open-log-summary.yml"
    summary_path.write_text("scope: storage-apply:create_only\nstatus: Completed\n", encoding="utf-8")
    main.save_history(
        "Storage-Log-Kit",
        [
            {
                "time": "2026-05-25 22:10:00",
                "scope": "storage-apply:create_only",
                "status": "Completed",
                "current_stage": "Finished",
                "run_summary_path": str(summary_path),
            }
        ],
    )

    response = storage_client.get("/storage")

    assert response.status_code == 200
    assert "Latest verified storage result" in response.text
    assert 'hx-post="/view-report"' in response.text
    assert 'data-action-title="Opening storage run log"' in response.text
    assert '<button class="btn action-button" type="submit">Open log</button>' in response.text
    assert 'name="return_page" value="storage"' in response.text
    assert 'name="report_path"' in response.text
    assert str(summary_path) in response.text
    assert 'hx-post="/view-run-summary"' not in response.text

    open_response = storage_client.post(
        "/view-report",
        data={"return_page": "storage", "report_path": str(summary_path)},
    )
    assert open_response.status_code == 200
    assert "Report: storage-open-log-summary.yml" in open_response.text
    assert "scope: storage-apply:create_only" in open_response.text


def test_storage_read_current_control_uses_specific_completion_feedback(storage_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Read Kit"
    cfg["ilo"]["current_ip"] = "192.168.1.50"
    cfg["ilo"]["host"] = "192.168.1.50"
    cfg["ilo"]["target_ip"] = "192.168.1.51"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = storage_client.get("/storage")

    assert response.status_code == 200
    assert 'hx-post="/read-current-storage"' in response.text
    assert 'data-action-title="Reading current storage"' in response.text
    assert (
        'data-action-start="Connecting to iLO and reading the current controller, volumes, and drives."'
        in response.text
    )
    assert 'data-action-complete="Current storage setup loaded."' in response.text
    assert '<button class="btn action-button" type="submit">Display current storage setup</button>' in response.text


def test_storage_target_save_controls_use_specific_completion_feedback(storage_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Target Kit"
    cfg["ilo"]["current_ip"] = "192.168.1.50"
    cfg["ilo"]["target_ip"] = "192.168.1.51"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = storage_client.get("/storage")

    assert response.status_code == 200
    assert 'hx-post="/save-storage-target"' in response.text
    assert 'data-action-title="Saving storage target"' in response.text
    assert 'data-action-start="Saving the storage target and sign-in details."' in response.text
    assert 'data-action-complete="Storage target saved."' in response.text
    assert (
        '<button class="btn btn-primary action-button" type="submit" name="storage_target_mode" value="override">Use entered IP</button>'
        in response.text
    )
    assert (
        '<button class="btn action-button" type="submit" name="storage_target_mode" value="defaults">Use iLO defaults</button>'
        in response.text
    )


def test_storage_repair_control_uses_specific_completion_feedback(storage_client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Repair Kit"
    cfg["ilo"]["current_ip"] = "192.168.1.50"
    cfg["ilo"]["host"] = "192.168.1.50"
    cfg["ilo"]["target_ip"] = "192.168.1.51"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = _storage_artifact_discovery()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="192.168.1.50")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    def raise_controller_mismatch(*_args, **_kwargs):
        raise ValueError("controller mismatch: selected drive resolved to the wrong controller")

    monkeypatch.setattr(main, "validate_storage_plan_drive_paths", raise_controller_mismatch)

    response = storage_client.post(
        "/approve-storage-plan",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "include_in_ilo_run": "on",
        },
    )

    assert response.status_code == 200
    assert "Repair invalid storage selections" in response.text
    assert 'hx-post="/repair-storage-selection"' in response.text
    assert 'data-action-title="Repairing storage selections"' in response.text
    assert 'data-action-start="Clearing invalid storage selections and loading fresh inventory."' in response.text
    assert 'data-action-complete="Invalid storage selections cleared."' in response.text
    assert (
        '<button class="btn btn-danger action-button" type="submit">Clear invalid selections and reload inventory</button>'
        in response.text
    )


def test_storage_clear_approval_control_uses_specific_completion_feedback(storage_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Clear Approval Kit"
    cfg["ilo"]["current_ip"] = "192.168.1.50"
    cfg["ilo"]["host"] = "192.168.1.50"
    cfg["ilo"]["target_ip"] = "192.168.1.51"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = _storage_artifact_discovery()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="192.168.1.50")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    main.approve_storage_plan_for_cfg(
        cfg,
        discovery=discovery,
        discovery_paths=export_paths,
        plan=plan,
        plan_paths=plan_paths,
        include_in_ilo_run=True,
    )
    main.save_kit_config(cfg)

    response = storage_client.get("/storage")

    assert response.status_code == 200
    assert 'hx-post="/clear-storage-approval"' in response.text
    assert 'data-action-title="Removing approval"' in response.text
    assert 'data-action-start="Removing approval from this storage plan."' in response.text
    assert 'data-action-complete="Storage approval removed."' in response.text
    assert '<button class="btn action-button" type="submit">Remove approval</button>' in response.text


def test_storage_artifact_view_controls_use_shared_action_feedback(storage_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Artifact Kit"
    cfg["ilo"]["current_ip"] = "192.168.1.50"
    cfg["ilo"]["host"] = "192.168.1.50"
    main.save_kit_config(cfg)
    discovery = _storage_artifact_discovery()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="192.168.1.50")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    apply_dir = export_paths["directory"] / "apply-attempt"
    apply_dir.mkdir()

    response = storage_client.post(
        "/view-storage-artifact",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "artifact_kind": "discovery_summary",
            "apply_artifact_dir": str(apply_dir),
        },
    )

    assert response.status_code == 200
    assert 'hx-post="/view-storage-artifact"' in response.text
    for title, button in [
        ("Opening storage apply log", '<button class="btn action-button" type="submit">View Apply Log</button>'),
        ("Opening storage apply results", '<button class="btn action-button" type="submit">View Apply Results</button>'),
        ("Opening storage plan details", '<button class="btn action-button" type="submit">View details</button>'),
        ("Opening storage discovery summary", '<button class="btn action-button" type="submit">View discovery summary</button>'),
        ("Opening raw storage discovery", '<button class="btn action-button" type="submit">View raw discovery</button>'),
    ]:
        assert f'data-action-title="{title}"' in response.text
        assert button in response.text
