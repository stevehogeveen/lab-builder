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


def _write_minimal_ova(directory: Path) -> None:
    (directory / "appliance.ova").write_bytes(b"ova-package")


def _write_named_ovf(directory: Path, descriptor_name: str) -> None:
    (directory / "template-disk.vmdk").write_bytes(b"disk")
    (directory / descriptor_name).write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References><File ovf:id="file1" ovf:href="template-disk.vmdk"/></References>
          <VirtualSystem ovf:id="Reusable-Template"/>
        </Envelope>
        """,
        encoding="utf-8",
    )


def _write_missing_sidecar_ovf(directory: Path) -> None:
    (directory / "template.ovf").write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References><File ovf:id="file1" ovf:href="missing-disk.vmdk"/></References>
          <VirtualSystem ovf:id="Broken-Template"/>
        </Envelope>
        """,
        encoding="utf-8",
    )


def test_ovf_templates_page_wires_registration_and_empty_status(ovf_templates_client):
    response = ovf_templates_client.get("/modules/ovf-templates")

    assert response.status_code == 200
    assert "OVF/OVA Templates" in response.text
    assert 'hx-post="/modules/ovf-templates/register-directory"' in response.text
    assert 'data-action-complete="OVF directory registration finished."' in response.text
    assert 'name="ovf_template_directory"' in response.text
    assert "template.ovf or appliance.ova" in response.text
    assert '<button class="btn btn-primary action-button" type="submit">Register directory</button>' in response.text
    assert "OVF/OVA operator checkpoint" in response.text
    assert "Operator Mode" in response.text
    assert "Open Debug Mode/details" in response.text
    assert 'href="#ovf-debug-details"' in response.text
    assert 'id="ovf-debug-details"' in response.text
    assert "Selected template" in response.text
    assert "No template selected" in response.text
    assert "Next step" in response.text
    assert "Completion state" in response.text
    assert "Last result" in response.text
    assert "Logs/status" in response.text
    assert "No templates registered yet" in response.text
    assert "Saved Lab Builder template selection" in response.text
    assert "Discovered/current file state" in response.text
    assert "Planned/suggested values" in response.text
    assert "Default local source media/ovf/example-template" in response.text
    assert "Debug Mode/details" in response.text
    assert "No debug details yet" in response.text
    assert "Recovery suggestions" in response.text
    assert "register the full directory" in response.text
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
    assert "Selected template" in response.text
    assert "Reusable Windows Template" in response.text
    operator_section = response.text.split('id="ovf-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Open VM setup" in operator_section
    assert "Select template in VM setup" not in operator_section
    assert "Saved Lab Builder template selection" in response.text
    assert "Discovered/current file state" in response.text
    assert "template.ovf | 2 files" in response.text
    assert "Reusable Windows Template is selected for VM setup workflows." in response.text
    assert "Template ready" in response.text
    assert "Local server source does not require NetApp." in response.text
    assert "Template registration validates local OVF/OVA files only." in response.text
    assert "Discovered files" in response.text
    assert "NetApp VMware/NFS datastore probe is ready" in response.text
    assert "dry-run readiness evidence" in response.text
    assert "template.ovf" in response.text
    assert "template-disk.vmdk" in response.text
    assert 'href="/windows"' in response.text
    assert "Open Windows setup" in response.text


def test_ovf_operator_mode_hides_full_paths_while_debug_shows_files(ovf_templates_client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates Path Boundary Kit"
    main.save_kit_config(cfg)
    _write_minimal_ovf(tmp_path)

    registered = ovf_templates_client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "Reusable Path Boundary Template",
            "ovf_template_os_family": "windows",
            "ovf_source_location_type": "local",
        },
    )
    assert registered.status_code == 200

    response = ovf_templates_client.get("/modules/ovf-templates")

    assert response.status_code == 200
    descriptor_path = str(tmp_path / "template.ovf")
    disk_path = str(tmp_path / "template-disk.vmdk")
    operator_section = response.text.split('id="ovf-operator-mode"', 1)[1].split("</section>", 1)[0]
    debug_section = response.text.split('id="ovf-debug-details"', 1)[1]
    assert "Reusable Path Boundary Template" in operator_section
    assert "Template ready" in operator_section
    assert "template.ovf | 2 files" in operator_section
    assert str(tmp_path) not in operator_section
    assert descriptor_path not in operator_section
    assert disk_path not in operator_section
    assert "Directory:" not in operator_section
    assert "Discovered files" in debug_section
    assert f"Directory: {tmp_path}" in debug_section
    assert descriptor_path in debug_section
    assert "template-disk.vmdk" in debug_section
    assert "Deployment prep stays dry-run" in operator_section


def test_ovf_templates_existing_templates_without_selection_show_selection_state(ovf_templates_client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates Unselected Kit"
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
    saved = main.load_kit_config()
    saved["ovf_templates"].pop("last_selected_template_id", None)
    main.save_kit_config(saved)

    response = ovf_templates_client.get("/modules/ovf-templates")

    assert response.status_code == 200
    operator_section = response.text.split('id="ovf-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "No template selected" in operator_section
    assert "Select template in VM setup" in operator_section
    assert "Needs template selection" in operator_section
    assert "Template registered; select one in VM setup" in operator_section
    assert "Needs readiness review" not in operator_section


def test_ovf_templates_page_registers_local_ova_package(ovf_templates_client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates OVA Kit"
    main.save_kit_config(cfg)
    _write_minimal_ova(tmp_path)

    registered = ovf_templates_client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "Reusable Appliance OVA",
            "ovf_template_os_family": "appliance",
            "ovf_source_location_type": "local",
        },
    )
    assert registered.status_code == 200

    response = ovf_templates_client.get("/modules/ovf-templates")

    assert response.status_code == 200
    assert "Reusable Appliance OVA" in response.text
    assert "appliance.ova | 1 file" in response.text
    assert "Local server source does not require NetApp." in response.text
    saved = main.load_kit_config()
    template = saved["ovf_templates"]["templates"]["reusable-appliance-ova"]
    assert template["kind"] == "ova"
    assert template["descriptor_name"] == "appliance.ova"
    assert template["files"][0]["role"] == "source"
    assert template["readiness"]["ready"] is True


def test_ovf_templates_page_redacts_display_paths_without_mutating_saved_template(ovf_templates_client, tmp_path):
    from app.modules.ovf_templates.service import OvfTemplateService

    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates Path Redaction Kit"
    main.save_kit_config(cfg)
    secret_dir = tmp_path / "source-TokenSecret1"
    secret_dir.mkdir()
    _write_minimal_ovf(secret_dir)

    response = ovf_templates_client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(secret_dir),
            "ovf_template_name": "Path Redaction Template",
            "ovf_template_os_family": "windows",
            "ovf_source_location_type": "local",
        },
    )

    assert response.status_code == 200
    assert "TokenSecret1" not in response.text
    assert "********" in response.text
    saved = main.load_kit_config()
    template = saved["ovf_templates"]["templates"]["path-redaction-template"]
    assert "TokenSecret1" in template["directory"]
    assert "TokenSecret1" in template["descriptor_path"]
    raw_template = OvfTemplateService().get_template(saved, "path-redaction-template")
    assert raw_template is not None
    assert "TokenSecret1" in raw_template["descriptor_path"]


def test_ovf_templates_registration_feedback_redacts_secret_like_values(ovf_templates_client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates Feedback Redaction Kit"
    main.save_kit_config(cfg)
    _write_named_ovf(tmp_path, "TokenSecret1.ovf")

    response = ovf_templates_client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "TokenSecret1 Template",
            "ovf_template_os_family": "windows",
            "ovf_source_location_type": "local",
            "ovf_descriptor_name": "TokenSecret1.ovf",
        },
    )

    assert response.status_code == 200
    assert "OVF template registered" in response.text
    assert "TokenSecret1" not in response.text
    assert "Template: ******** Template" in response.text
    assert "Descriptor: ********" in response.text
    saved = main.load_kit_config()
    template = saved["ovf_templates"]["templates"]["tokensecret1-template"]
    assert template["name"] == "TokenSecret1 Template"
    assert template["descriptor_name"] == "TokenSecret1.ovf"


def test_ovf_templates_failure_feedback_redacts_secret_like_candidates(ovf_templates_client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates Failure Redaction Kit"
    main.save_kit_config(cfg)
    _write_named_ovf(tmp_path, "CandidateTokenSecret1.ovf")
    _write_named_ovf(tmp_path, "plain-template.ovf")

    response = ovf_templates_client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "Candidate Redaction Template",
            "ovf_template_os_family": "windows",
            "ovf_source_location_type": "local",
        },
    )

    assert response.status_code == 200
    assert "OVF template not registered" in response.text
    operator_section = response.text.split('id="ovf-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "OVF template not registered" in operator_section
    assert "Fix the directory or descriptor choice, then submit the form again." in operator_section
    assert "Registration needs attention" in operator_section
    assert "CandidateTokenSecret1" not in response.text
    assert "Candidates: ********, plain-template.ovf" in response.text
    saved = main.load_kit_config()
    assert "ovf_templates" not in saved


def test_ovf_templates_missing_referenced_file_stays_unsaved_and_visible(ovf_templates_client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates Missing Sidecar Kit"
    main.save_kit_config(cfg)
    _write_missing_sidecar_ovf(tmp_path)

    response = ovf_templates_client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "Broken Sidecar Template",
            "ovf_template_os_family": "windows",
            "ovf_source_location_type": "local",
        },
    )

    assert response.status_code == 200
    assert "OVF template not registered" in response.text
    assert "OVF referenced file is missing: missing-disk.vmdk" in response.text
    operator_section = response.text.split('id="ovf-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "OVF template not registered" in operator_section
    assert "Fix the directory or descriptor choice, then submit the form again." in operator_section
    assert "Registration needs attention" in operator_section
    saved = main.load_kit_config()
    assert "ovf_templates" not in saved


def test_ovf_templates_netapp_source_registers_as_blocked_without_probe(ovf_templates_client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Templates NetApp Boundary Kit"
    main.save_kit_config(cfg)
    _write_minimal_ovf(tmp_path)

    response = ovf_templates_client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "NetApp Backed Template",
            "ovf_template_os_family": "windows",
            "ovf_source_location_type": "netapp",
        },
    )

    assert response.status_code == 200
    assert "OVF template registered" in response.text
    assert "NetApp source" in response.text
    assert "Source: NetApp" in response.text
    assert "Netapp source" not in response.text
    assert "NetApp-backed OVF source needs a ready NetApp VMware/NFS datastore probe first." in response.text
    assert "Run NetApp discovery and the ESXi/NFS probe before using this template." in response.text
    assert "dry-run readiness evidence" in response.text
    operator_section = response.text.split('id="ovf-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "NetApp Backed Template" in operator_section
    assert "Resolve template readiness blockers" in operator_section
    assert "Needs readiness review" in operator_section
    assert "NetApp-backed OVF source needs a ready NetApp VMware/NFS datastore probe first." in operator_section

    saved = main.load_kit_config()
    template = saved["ovf_templates"]["templates"]["netapp-backed-template"]
    assert template["source_location_type"] == "netapp"
    assert template["required_components"] == ["netapp"]
    assert template["readiness"]["ready"] is False
    assert template["readiness"]["label"] == "Blocked"
