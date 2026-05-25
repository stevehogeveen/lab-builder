from pathlib import Path

from app.vcenter import build_vcenter_install_context, build_vcenter_install_spec


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
