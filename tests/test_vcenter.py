import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.vcenter import build_vcenter_install_context, build_vcenter_install_spec


@pytest.fixture()
def vcenter_client(tmp_path: Path, monkeypatch):
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
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {"root": str(media_dir), "latest": {}, "counts": {}, "candidates": []})
    main.set_current_kit_name("Kit-01")

    with TestClient(main.app) as test_client:
        yield test_client


def _button_containing(html: str, marker: str) -> str:
    marker_index = html.index(marker)
    button_start = html.rfind("<button", 0, marker_index)
    button_end = html.find("</button>", marker_index) + len("</button>")
    return html[button_start:button_end]


def test_vcenter_context_defaults_to_lab_subnet_offset_and_caps_dns(tmp_path: Path):
    iso = tmp_path / "VMware-VCSA-all-8.0.3.iso"
    iso.write_text("placeholder", encoding="utf-8")
    cfg = {
        "site": {"name": "Lab-Test"},
        "ip_plan": {"subnet": "10.10.8.0/24", "gateway": "10.10.8.1"},
        "shared_network": {"subnet": "10.10.8.0/24", "dns_servers": ["bad", "10.10.8.99", "1.1.1.1", "8.8.8.8"]},
        "esxi": {"management_ip": "10.10.8.111", "root_password": "Password1!"},
        "vmware": {
            "password": "Password1!",
            "vcenter_install": {
                "iso_path": str(iso),
                "datastore": "NETAPP-NFS-01",
                "deployment_network": "VM Network",
            },
        },
    }

    context = build_vcenter_install_context(
        cfg,
        media_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        artifacts_dir=tmp_path / "artifacts",
    )

    assert context["target_ip"] == "10.10.8.50"
    assert context["dns_servers"] == ["10.10.8.99", "1.1.1.1"]
    assert context["installer_extract_dir"].startswith("/tmp/lab-builder-vcenter-installer/")
    assert context["ready"] is True


def test_vcenter_spec_redacts_passwords_and_keeps_network_identity(tmp_path: Path):
    iso = tmp_path / "VMware-VCSA-all-8.0.3.iso"
    iso.write_text("placeholder", encoding="utf-8")
    cfg = {
        "site": {"name": "Lab-Test"},
        "ip_plan": {"subnet": "10.10.8.0/24", "gateway": "10.10.8.1"},
        "shared_network": {"subnet": "10.10.8.0/24"},
        "esxi": {"management_ip": "10.10.8.111", "root_password": "Password1!"},
        "vmware": {
            "password": "Password1!",
            "vcenter_install": {
                "target_ip": "10.10.8.50",
                "system_name": "vcenter.lab.local",
                "iso_path": str(iso),
                "datastore": "NETAPP-NFS-01",
                "deployment_network": "VM Network",
                "dns_servers": ["10.10.8.99"],
                "ntp_servers": "10.10.8.99",
            },
        },
    }
    context = build_vcenter_install_context(
        cfg,
        media_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        artifacts_dir=tmp_path / "artifacts",
    )

    spec = build_vcenter_install_spec(context, redact=True)

    assert spec["new_vcsa"]["network"]["system_name"] == "vcenter.lab.local"
    assert spec["new_vcsa"]["network"]["dns_servers"] == ["10.10.8.99"]
    assert spec["new_vcsa"]["os"]["ntp_servers"] == "10.10.8.99"
    assert spec["new_vcsa"]["esxi"]["password"] == "********"
    assert spec["new_vcsa"]["os"]["password"] == "********"
    assert spec["new_vcsa"]["sso"]["password"] == "********"


def test_vcenter_page_wires_visible_form_actions(vcenter_client):
    response = vcenter_client.get("/vcenter")

    assert response.status_code == 200
    assert 'id="vcenter-settings-form"' in response.text
    assert 'hx-post="/save-vcenter-settings"' in response.text
    assert 'hx-post="/plan-vcenter-install" hx-include="#vcenter-settings-form"' in response.text
    assert 'hx-post="/run-vcenter-install" hx-include="#vcenter-settings-form"' in response.text
    assert "VMware vCenter Server Appliance (VCSA)" in response.text
    assert "Single Sign-On (SSO) domain" in response.text
    assert "Network Time Protocol (NTP) servers" in response.text
    assert "Domain Name System (DNS) servers" in response.text
    assert "Enable Secure Shell (SSH) on the appliance" in response.text
    assert 'data-action-complete="vCenter setup saved."' in response.text
    assert 'data-action-complete="vCenter deployment spec generated."' in response.text
    assert 'data-action-complete="vCenter appliance deployment request submitted."' in response.text
    start_button = _button_containing(response.text, 'hx-post="/run-vcenter-install"')
    assert "disabled" in start_button
    assert "Generate deployment spec" in response.text
    assert "Start vCenter appliance deployment" in response.text


def test_vcenter_start_deployment_button_enables_when_ready(vcenter_client):
    iso = main.MEDIA_DIR / "VMware-VCSA-all-8.0.3.iso"
    iso.write_text("placeholder", encoding="utf-8")
    cfg = main.default_config()
    cfg["site"]["name"] = "VCenter Ready Kit"
    cfg["shared_network"]["subnet"] = "192.168.1.0/24"
    cfg["shared_network"]["dns_servers"] = ["192.168.1.1"]
    cfg["ip_plan"]["subnet"] = "192.168.1.0/24"
    cfg["ip_plan"]["gateway"] = "192.168.1.1"
    cfg["esxi"]["management_ip"] = "192.168.1.10"
    cfg["esxi"]["root_password"] = "ValidPass1!"
    cfg["vmware"]["vcenter_install"].update(
        {
            "target_ip": "192.168.1.50",
            "system_name": "vcenter.lab.local",
            "vm_name": "SVCNTR-LAB",
            "iso_path": str(iso),
            "esxi_host": "192.168.1.10",
            "esxi_username": "root",
            "esxi_password": "ValidPass1!",
            "datastore": "datastore1",
            "deployment_network": "VM Network",
            "root_password": "ValidPass1!",
            "sso_password": "ValidPass1!",
            "dns_servers": ["192.168.1.1"],
            "ntp_servers": "192.168.1.1",
        }
    )
    main.save_kit_config(cfg)

    response = vcenter_client.get("/vcenter")

    assert response.status_code == 200
    start_button = _button_containing(response.text, 'hx-post="/run-vcenter-install"')
    assert "disabled" not in start_button


def test_save_vcenter_settings_persists_visible_form_values(vcenter_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "VCenter Save Kit"
    main.save_kit_config(cfg)

    response = vcenter_client.post(
        "/save-vcenter-settings",
        data={
            "return_page": "vcenter",
            "vcenter_target_ip": "192.168.1.55",
            "vcenter_system_name": "vcenter-save.lab.local",
            "vcenter_vm_name": "SVCNTR-SAVE",
            "vcenter_iso_path": "/media/VMware-VCSA-all-8.0.3.iso",
            "vcenter_esxi_host": "192.168.1.20",
            "vcenter_esxi_username": "root",
            "vcenter_esxi_password": "ValidPass1!",
            "vcenter_datastore": "datastore1",
            "vcenter_deployment_network": "VM Network",
            "vcenter_deployment_option": "tiny",
            "vcenter_root_password": "ValidPass1!",
            "vcenter_sso_domain": "vsphere.local",
            "vcenter_sso_password": "ValidPass1!",
            "vcenter_ntp_servers": "192.168.1.1",
            "vcenter_dns_servers": "192.168.1.1, 1.1.1.1",
            "vcenter_ssh_enable": "on",
        },
    )

    assert response.status_code == 200
    assert "vCenter settings saved" in response.text
    assert "ValidPass1!" not in response.text
    saved = main.load_kit_config("VCenter-Save-Kit")
    install = saved["vmware"]["vcenter_install"]
    assert saved["included"]["vcenter"] is True
    assert install["target_ip"] == "192.168.1.55"
    assert install["system_name"] == "vcenter-save.lab.local"
    assert install["vm_name"] == "SVCNTR-SAVE"
    assert install["esxi_host"] == "192.168.1.20"
    assert install["datastore"] == "datastore1"
    assert install["dns_servers"] == ["192.168.1.1", "1.1.1.1"]
    assert install["ssh_enable"] is True


def test_plan_vcenter_install_uses_posted_form_values(vcenter_client):
    iso = main.MEDIA_DIR / "VMware-VCSA-all-8.0.3.iso"
    iso.write_text("placeholder", encoding="utf-8")
    cfg = main.default_config()
    cfg["site"]["name"] = "VCenter Posted Kit"
    cfg["shared_network"]["subnet"] = "192.168.1.0/24"
    cfg["shared_network"]["dns_servers"] = ["192.168.1.1"]
    cfg["ip_plan"]["subnet"] = "192.168.1.0/24"
    cfg["ip_plan"]["gateway"] = "192.168.1.1"
    cfg["esxi"]["management_ip"] = "192.168.1.10"
    main.save_kit_config(cfg)

    response = vcenter_client.post(
        "/plan-vcenter-install",
        data={
            "return_page": "vcenter",
            "vcenter_target_ip": "192.168.1.50",
            "vcenter_system_name": "vcenter.lab.local",
            "vcenter_vm_name": "SVCNTR-LAB",
            "vcenter_iso_path": str(iso),
            "vcenter_esxi_host": "192.168.1.10",
            "vcenter_esxi_username": "root",
            "vcenter_esxi_password": "ValidPass1!",
            "vcenter_datastore": "NETAPP-NFS-01",
            "vcenter_deployment_network": "VM Network",
            "vcenter_deployment_option": "tiny",
            "vcenter_root_password": "ValidPass1!",
            "vcenter_sso_domain": "vsphere.local",
            "vcenter_sso_password": "ValidPass1!",
            "vcenter_ntp_servers": "192.168.1.1",
            "vcenter_dns_servers": "192.168.1.1",
            "vcenter_ssh_enable": "on",
        },
    )

    assert response.status_code == 200
    assert "vCenter install plan generated" in response.text
    assert "ValidPass1!" not in response.text
    saved = main.load_kit_config("VCenter-Posted-Kit")
    install = saved["vmware"]["vcenter_install"]
    assert saved["included"]["vcenter"] is True
    assert install["target_ip"] == "192.168.1.50"
    assert install["system_name"] == "vcenter.lab.local"
    assert install["esxi_host"] == "192.168.1.10"
    assert install["datastore"] == "NETAPP-NFS-01"
    assert install["dns_servers"] == ["192.168.1.1"]
    redacted_spec = json.loads(Path(install["last_plan"]["spec_path"]).read_text(encoding="utf-8"))
    assert redacted_spec["new_vcsa"]["network"]["ip"] == "192.168.1.50"
    assert redacted_spec["new_vcsa"]["esxi"]["datastore"] == "NETAPP-NFS-01"
    assert redacted_spec["new_vcsa"]["esxi"]["password"] == "********"


def test_run_vcenter_install_blocked_route_applies_posted_form_without_hardware(vcenter_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "VCenter Blocked Kit"
    main.save_kit_config(cfg)

    response = vcenter_client.post(
        "/run-vcenter-install",
        data={
            "return_page": "vcenter",
            "vcenter_target_ip": "192.168.1.60",
            "vcenter_system_name": "192.168.1.60",
            "vcenter_esxi_host": "",
            "vcenter_datastore": "",
            "vcenter_dns_servers": "",
        },
    )

    assert response.status_code == 200
    assert "vCenter install blocked" in response.text
    saved = main.load_kit_config("VCenter-Blocked-Kit")
    assert saved["vmware"]["vcenter_install"]["target_ip"] == "192.168.1.60"
    assert saved["vmware"]["vcenter_install"]["activity"]["status"] == "blocked"
