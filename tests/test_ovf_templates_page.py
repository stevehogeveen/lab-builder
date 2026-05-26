from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def ovf_templates_client(tmp_path, monkeypatch):
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
    main.set_current_kit_name("OVF-Templates-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def _write_minimal_ovf(directory: Path) -> None:
    (directory / "template-disk.vmdk").write_bytes(b"disk")
    (directory / "template.ovf").write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References><File ovf:id="file1" ovf:href="template-disk.vmdk"/></References>
          <NetworkSection><Network ovf:name="VM Network"/></NetworkSection>
          <VirtualSystem ovf:id="Reusable-Template"/>
        </Envelope>
        """,
        encoding="utf-8",
    )


def test_ovf_templates_page_wires_registration_and_empty_status(ovf_templates_client):
    response = ovf_templates_client.get("/modules/ovf-templates")

    assert response.status_code == 200
    assert 'hx-post="/modules/ovf-templates/register-directory"' in response.text
    assert 'data-action-complete="OVF directory registration finished."' in response.text
    assert 'name="ovf_template_directory"' in response.text
    assert '<button class="btn btn-primary action-button" type="submit">Register directory</button>' in response.text
    assert "What happened last" in response.text
    assert "No OVF template action recorded yet" in response.text
    assert "Register a full OVF directory once" in response.text


def test_ovf_templates_page_keeps_latest_registration_status(ovf_templates_client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates Receipt Kit"
    main.save_kit_config(cfg)
    _write_minimal_ovf(tmp_path)

    registered = ovf_templates_client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "Reusable Windows Template",
            "ovf_template_os_family": "windows",
            "ovf_source_location_type": "local",
        },
    )
    assert registered.status_code == 200

    response = ovf_templates_client.get("/modules/ovf-templates")

    assert response.status_code == 200
    assert "What happened last" in response.text
    assert "OVF template registered" in response.text
    assert "Reusable Windows Template is registered for VM setup workflows." in response.text
    assert "Local source | 2 files" in response.text
    assert 'href="/windows"' in response.text
    assert "Open Windows setup" in response.text
