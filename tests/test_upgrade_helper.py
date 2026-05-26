from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.upgrade_helper import MEDIA_SCAN_ROOT, REPO_ROOT, build_upgrade_helper_context, build_upgrade_helper_summary, build_upgrade_inventory, build_upgrade_planner, build_upgrade_planner_with_policies, compare_versions, normalize_upgrade_policies, record_upgrade_inventory, scan_upgrade_media, select_upgrade_candidate


@pytest.fixture()
def upgrade_helper_client(tmp_path: Path, monkeypatch):
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
    main.set_current_kit_name("Upgrade-Helper-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def test_upgrade_helper_media_and_policy_controls_have_feedback_metadata(upgrade_helper_client):
    response = upgrade_helper_client.get("/upgrade-helper?tab=ontap")

    assert response.status_code == 200
    assert 'data-active-upgrade-tab="ontap"' in response.text
    assert 'hx-post="/upload-upgrade-media?upgrade_tab=ontap"' in response.text
    assert 'hx-encoding="multipart/form-data"' in response.text
    assert 'data-action-title="Uploading upgrade media"' in response.text
    assert 'data-action-start="Saving the selected firmware or install media file."' in response.text
    assert 'class="btn btn-primary action-button" type="submit">Upload</button>' in response.text
    assert 'hx-post="/save-upgrade-policies?upgrade_tab=ontap"' in response.text
    assert 'data-action-title="Saving upgrade policies"' in response.text
    assert 'name="policy_ilo"' in response.text
    assert 'name="policy_netapp"' in response.text
    assert 'name="policy_cisco_switch"' in response.text
    assert 'class="btn btn-primary action-button" type="submit">Save policies</button>' in response.text


def test_upgrade_helper_generated_tab_actions_use_shared_action_button_class(upgrade_helper_client):
    response = upgrade_helper_client.get("/upgrade-helper?tab=cisco")

    assert response.status_code == 200
    for label, route, expected_class in [
        ("Review Cisco upgrade plan", "/modules/cisco/plan-upgrade?upgrade_tab=cisco", 'class="btn action-button"'),
        ("Run Cisco upgrade", "/modules/cisco/run-upgrade?upgrade_tab=cisco", 'class="btn btn-primary action-button"'),
        ("Read Cisco version", "/modules/cisco/discover-version?upgrade_tab=cisco", 'class="btn action-button"'),
    ]:
        label_pos = response.text.index(f">{label}</button>")
        start = response.text.rfind("<button", 0, label_pos)
        end = response.text.find("</button>", label_pos) + len("</button>")
        markup = response.text[start:end]
        assert expected_class in markup
        assert f'hx-post="{route}"' in markup


def test_upgrade_helper_override_toggle_has_specific_action_feedback(upgrade_helper_client):
    response = upgrade_helper_client.get("/upgrade-helper?tab=ilo")

    assert response.status_code == 200
    assert 'hx-post="/save-upgrade-override?upgrade_tab=ilo"' in response.text
    assert 'hx-trigger="change"' in response.text
    assert "Override gate for config setup" in response.text
    assert 'data-action-title="Saving iLO upgrade override"' in response.text
    assert (
        'data-action-start="Updating whether iLO can continue setup while its upgrade gate is unresolved."'
        in response.text
    )
    assert 'data-action-complete="iLO upgrade override saved."' in response.text


def test_save_upgrade_policies_keeps_selected_helper_tab(upgrade_helper_client):
    response = upgrade_helper_client.post(
        "/save-upgrade-policies?upgrade_tab=cisco",
        data={
            "return_page": "upgrade_helper",
            "policy_ilo": "warn",
            "policy_netapp": "ignore",
            "policy_cisco_switch": "block",
        },
    )

    assert response.status_code == 200
    assert "Upgrade policies saved" in response.text
    assert 'data-active-upgrade-tab="cisco"' in response.text
    assert "Review Cisco upgrade plan" in response.text
    saved = main.load_kit_config()
    assert saved["upgrade_helper"]["policies"] == {
        "ilo": "warn",
        "netapp": "ignore",
        "cisco_switch": "block",
    }


def test_upload_upgrade_media_saves_file_without_hardware(upgrade_helper_client):
    response = upgrade_helper_client.post(
        "/upload-upgrade-media?upgrade_tab=firmware",
        data={"return_page": "upgrade_helper"},
        files={"media_file": ("ilo6_176.fwpkg", b"fake firmware", "application/octet-stream")},
    )

    assert response.status_code == 200
    assert "Firmware media uploaded" in response.text
    assert 'data-active-upgrade-tab="firmware"' in response.text
    assert (main.FIRMWARE_UPLOAD_DIR / "ilo6_176.fwpkg").read_bytes() == b"fake firmware"


def test_compare_versions_handles_ontap_patch_versions():
    assert compare_versions("9.9.1P2", "9.12.1") == -1
    assert compare_versions("9.12.1", "9.12.1") == 0
    assert compare_versions("3.03", "2.99") == 1


def test_scan_upgrade_media_detects_supported_upgrade_files(tmp_path: Path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "ilo5_3.10.fwpkg").write_text("x", encoding="utf-8")
    (media / "ontap-9.12.1P5.tgz").write_text("x", encoding="utf-8")
    (media / "cat9k_lite_iosxe.17.09.04a.SPA.bin").write_text("x", encoding="utf-8")

    summary = scan_upgrade_media(media)

    assert summary["latest"]["ilo"]["version"] == "3.10"
    assert summary["latest"]["netapp"]["version"] == "9.12.1P5"
    assert summary["latest"]["cisco_switch"]["version"] == "17.09.04"


def test_scan_upgrade_media_detects_real_repo_style_files(tmp_path: Path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "9171_q_image.tgz").write_text("x", encoding="utf-8")
    (media / "9131P17_q_image.tgz").write_text("x", encoding="utf-8")
    (media / "ilo5_319.fwpkg").write_text("x", encoding="utf-8")
    (media / "ilo6_176.fwpkg").write_text("x", encoding="utf-8")

    summary = scan_upgrade_media(media)

    assert summary["latest"]["netapp"]["version"] == "9.17.1"
    netapp_versions = {item["version"] for item in summary["candidates"] if item["device"] == "netapp"}
    assert "9.13.1P17" in netapp_versions
    assert summary["latest"]["ilo"]["version"] == "3.19"


def test_build_upgrade_helper_summary_flags_upgrade_and_unknown_versions():
    media_scan = {
        "root": "/media",
        "latest": {
            "ilo": {"version": "3.10", "filename": "ilo5_3.10.fwpkg", "path": "/media/ilo5_3.10.fwpkg"},
            "netapp": {"version": "9.12.1", "filename": "ontap-9.12.1.tgz", "path": "/media/ontap-9.12.1.tgz"},
        },
        "counts": {"ilo": 1, "netapp": 1},
        "candidates": [],
    }

    card = build_upgrade_helper_summary(media_scan, {"ilo": "2.99", "netapp": ""})

    assert card["blockers"] == 2
    assert any(item["label"] == "iLO: upgrade available" for item in card["items"])
    assert any(item["label"] == "ONTAP: current version unknown" for item in card["items"])


def test_build_upgrade_helper_context_includes_device_rows():
    media_scan = {
        "root": "/media",
        "latest": {
            "ilo": {"version": "3.10", "filename": "ilo5_3.10.fwpkg", "path": "/media/ilo5_3.10.fwpkg"},
            "netapp": {"version": "9.12.1", "filename": "ontap-9.12.1.tgz", "path": "/media/ontap-9.12.1.tgz"},
        },
        "counts": {"ilo": 1, "netapp": 1},
        "candidates": [],
    }

    context = build_upgrade_helper_context(
        media_scan,
        {"ilo": "2.99", "netapp": "9.12.1"},
        {"ilo": "Latest live iLO inventory", "netapp": "Last NetApp discovery"},
    )

    ilo = next(item for item in context["devices"] if item["key"] == "ilo")
    netapp = next(item for item in context["devices"] if item["key"] == "netapp")
    assert ilo["status"] == "upgrade_available"
    assert ilo["current_source"] == "Latest live iLO inventory"
    assert netapp["status"] == "current_enough"


def test_build_upgrade_inventory_uses_cached_and_legacy_values():
    cfg = {
        "upgrade_inventory": {
            "ilo": {"current_version": "3.03", "source": "Latest live iLO inventory", "last_checked_at": "2026-05-13T00:00:00+00:00"},
        },
        "netapp": {"last_discovered_ontap_version": "9.9.1P2"},
        "cisco_switch": {"last_discovered_version": "17.09.04"},
    }

    inventory = build_upgrade_inventory(cfg)

    assert inventory["ilo"]["current_version"] == "3.03"
    assert inventory["netapp"]["current_version"] == "9.9.1P2"
    assert inventory["netapp"]["source"] == "Last NetApp discovery"
    assert inventory["cisco_switch"]["current_version"] == "17.09.04"
    assert inventory["cisco_switch"]["source"] == "Last Cisco discovery"


def test_record_upgrade_inventory_normalizes_versions():
    cfg = {}

    record_upgrade_inventory(cfg, "cisco_switch", current_version="Cisco IOS XE Software, Version 17.09.04a", source="Last Cisco discovery")

    assert cfg["upgrade_inventory"]["cisco_switch"]["current_version"] == "17.09.04"
    assert cfg["upgrade_inventory"]["cisco_switch"]["source"] == "Last Cisco discovery"


def test_upgrade_media_root_defaults_to_repo_media():
    assert MEDIA_SCAN_ROOT == REPO_ROOT / "media"


def test_build_upgrade_planner_marks_prebuild_gates():
    media_scan = {
        "root": "/repo/media",
        "latest": {
            "ilo": {"version": "3.10", "filename": "ilo5_3.10.fwpkg", "path": "/repo/media/ilo5_3.10.fwpkg"},
            "netapp": {"version": "9.12.1", "filename": "ontap-9.12.1.tgz", "path": "/repo/media/ontap-9.12.1.tgz"},
        },
        "counts": {"ilo": 1, "netapp": 1},
        "candidates": [],
    }

    planner = build_upgrade_planner(
        media_scan,
        {"ilo": "2.99", "netapp": "", "cisco_switch": ""},
        {"ilo": "Latest live iLO inventory", "netapp": "", "cisco_switch": ""},
    )

    ilo = next(item for item in planner["entries"] if item["key"] == "ilo")
    netapp = next(item for item in planner["entries"] if item["key"] == "netapp")
    assert ilo["comparison"] == "upgrade_available"
    assert ilo["prebuild_gate"] is True
    assert netapp["comparison"] == "current_unknown"
    assert netapp["prebuild_gate"] is True


def test_build_upgrade_planner_with_warn_policy_downgrades_gate():
    media_scan = {
        "root": "/repo/media",
        "latest": {
            "netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"},
        },
        "counts": {"netapp": 1},
        "candidates": [],
    }

    planner = build_upgrade_planner_with_policies(
        media_scan,
        {"netapp": "9.9.1P2"},
        {"netapp": "Last NetApp discovery"},
        {"netapp": "warn"},
    )

    entry = next(item for item in planner["entries"] if item["key"] == "netapp")
    assert planner["blockers"] == 0
    assert planner["warnings"] == 1
    assert entry["warn_only"] is True
    assert entry["blocks_run"] is False
    assert entry["policy"] == "warn"


def test_normalize_upgrade_policies_defaults_invalid_values():
    policies = normalize_upgrade_policies(policies={"ilo": "bad", "netapp": "ignore", "cisco_switch": "warn"})

    assert policies == {"ilo": "block", "netapp": "ignore", "cisco_switch": "warn"}


def test_build_upgrade_planner_adds_netapp_baseline_details():
    media_scan = {
        "root": "/repo/media",
        "latest": {
            "netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"},
        },
        "counts": {"netapp": 1},
        "candidates": [],
    }

    planner = build_upgrade_planner_with_policies(
        media_scan,
        {"netapp": "9.9.1P2"},
        {"netapp": "Last NetApp discovery"},
        {"netapp": "block"},
        {"netapp": {"baseline_target": "9.12.1", "minimum_version": "9.9.1"}},
    )

    entry = next(item for item in planner["entries"] if item["key"] == "netapp")
    assert "below the target baseline 9.12.1" in entry["compatibility_summary"]
    assert "Minimum supported: 9.9.1." in entry["detail_lines"]


def test_build_upgrade_planner_adds_cisco_platform_hint():
    media_scan = {
        "root": "/repo/media",
        "latest": {
            "cisco_switch": {"version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"},
        },
        "counts": {"cisco_switch": 1},
        "candidates": [],
    }

    planner = build_upgrade_planner_with_policies(
        media_scan,
        {"cisco_switch": "17.03.01"},
        {"cisco_switch": "Last Cisco discovery"},
        {"cisco_switch": "warn"},
        {"cisco_switch": {"model": "C9300-48P", "platform": "C9300-UNIVERSALK9-M"}},
    )

    entry = next(item for item in planner["entries"] if item["key"] == "cisco_switch")
    assert "Detected model: C9300-48P." in entry["detail_lines"]
    assert "does not explicitly mention detected model C9300-48P" in entry["compatibility_summary"]


def test_select_upgrade_candidate_prefers_matching_ilo_family():
    media_scan = {
        "root": "/repo/media",
        "latest": {
            "ilo": {"version": "3.19", "filename": "ilo5_319.fwpkg", "path": "/repo/media/ilo5_319.fwpkg"},
        },
        "counts": {"ilo": 2},
        "candidates": [
            {"device": "ilo", "version": "3.19", "filename": "ilo5_319.fwpkg", "path": "/repo/media/ilo5_319.fwpkg"},
            {"device": "ilo", "version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"},
        ],
    }

    selected = select_upgrade_candidate(media_scan, "ilo", {"manager_model": "iLO 6"})

    assert selected["filename"] == "ilo6_176.fwpkg"
    assert selected["version"] == "1.76"
