from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.stages.esxi.runtime as esxi_runtime


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
    assert 'id="esxi-save-form"' in response.text
    assert 'data-action-title="Saving ESXi setup"' in response.text
    assert 'data-action-start="Saving the ESXi installer and post-config settings."' in response.text
    assert 'data-action-complete="ESXi setup saved."' in response.text
    assert '<button class="btn btn-primary action-button" type="submit">Save ESXi setup</button>' in response.text
    assert "ESXi operator checkpoint" in response.text
    assert "Operator Mode" in response.text
    assert "Open Debug Mode/details" in response.text
    assert 'href="#esxi-debug-details"' in response.text
    assert 'id="esxi-debug-details"' in response.text
    assert "Debug Mode/details" in response.text
    assert "logs/status" in response.text
    assert "Next step" in response.text
    assert "Completion state" in response.text
    assert "Last result" in response.text
    assert "Saved Lab Builder kit config" in response.text
    assert "Discovered/current install state" in response.text
    assert "Planned/suggested values" in response.text
    operator_section = response.text.split('id="esxi-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Media Base ISO" in operator_section
    assert "Root password missing" in operator_section
    assert "http://" not in operator_section
    assert "Virtual media URL" in response.text
    assert "192.168.1.63@162/wutvpmonitor/priv/trap" in response.text
    debug_section_index = response.text.index('id="esxi-debug-details"')
    assert response.text.index("Installer details") > debug_section_index
    assert response.text.index("Post-install policy") > debug_section_index
    assert 'name="esxi_post_transport" class="input" form="esxi-save-form"' in response.text
    assert 'name="esxi_post_allow_datastore_create" form="esxi-save-form"' in response.text
    assert "Reboot approved after post-config apply" in response.text
    assert '<span class="status progress">manual only</span>' in response.text
    assert '<span class="status danger">disruptive</span>' in response.text


def test_esxi_operator_next_step_names_manual_run_center_install_plan(esxi_client, tmp_path):
    iso = tmp_path / "VMware-ESXi-7.iso"
    iso.write_bytes(b"0")
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Manual Next Step Kit"
    cfg["ip_plan"].update({"esxi": "192.168.1.202", "gateway": "192.168.1.1", "netmask": "255.255.255.0"})
    cfg["esxi"].update(
        {
            "version": "7",
            "base_iso_path": str(iso),
            "hostname": "esxi-manual",
            "root_password": "Valid1Pass!",
        }
    )
    main.save_kit_config(cfg)

    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    operator_section = response.text.split('id="esxi-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Review Run Center manual install plan" in operator_section
    assert "Ready for manual install review" in operator_section
    assert "Manual install plan ready" in operator_section
    assert "Review Run Center plan" not in operator_section
    assert "Virtual media URL" not in operator_section
    assert "Build artifacts" in response.text
    assert "Built ISO path" in response.text
    assert "Virtual media URL" in response.text
    assert "Manual test script defaults are not used by Run Center" in response.text


def test_esxi_operator_blocks_manual_install_when_root_password_policy_fails(esxi_client, tmp_path):
    iso = tmp_path / "VMware-ESXi-7.iso"
    iso.write_bytes(b"0")
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Invalid Password Kit"
    cfg["ip_plan"].update({"esxi": "192.168.1.202", "gateway": "192.168.1.1", "netmask": "255.255.255.0"})
    cfg["esxi"].update(
        {
            "version": "7",
            "base_iso_path": str(iso),
            "hostname": "esxi-password",
            "root_password": "abcdefg",
        }
    )
    main.save_kit_config(cfg)

    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    operator_section = response.text.split('id="esxi-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Fix saved ESXi values" in operator_section
    assert "Needs setup values" in operator_section
    assert "Manual install plan ready" not in operator_section
    assert "Ready for manual install review" not in operator_section
    assert "Use at least 3 character types: lowercase, uppercase, number, or special." in response.text
    assert "Password policy looks valid" in response.text
    assert "abcdefg" not in response.text


def test_esxi_page_render_does_not_touch_real_ilo_or_media_probe(esxi_client, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("ESXi page render must not open real hardware or network probes")

    monkeypatch.setattr(main, "ILOClient", fail_if_called)
    monkeypatch.setattr(esxi_runtime.requests, "get", fail_if_called)

    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    assert "ESXi operator checkpoint" in response.text


def test_esxi_page_latest_receipt_open_log_uses_report_route(esxi_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Log Kit"
    cfg["esxi"]["root_password"] = "EsxiReceiptSecret1!"
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
                "current_stage": "Finished with EsxiReceiptSecret1!",
                "config_summary": {"target_ip": "EsxiReceiptSecret1!", "gateway": "192.168.1.1"},
                "run_summary_path": str(summary_path),
            }
        ],
    )

    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    assert "Last ESXi run" in response.text
    operator_section = response.text.split('id="esxi-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "esxi-open-log-summary.yml" in operator_section
    assert str(summary_path) not in operator_section
    assert "EsxiReceiptSecret1!" not in response.text
    assert "********" in response.text
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


def test_esxi_page_does_not_render_saved_root_or_post_config_secrets(esxi_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Secret Render Kit"
    cfg["esxi"].update(
        {
            "hostname": "esxi-secret",
            "root_password": "SavedRootSecret1!",
            "post_config_secrets": {
                "wug_password": "WugSecret1!",
                "snmpv3_auth_password": "SnmpAuthSecret1!",
                "snmpv3_priv_password": "SnmpPrivSecret1!",
                "kit_root_password": "KitRootSecret1!",
                "svmservice_password": "SvmServiceSecret1!",
                "localtech_password": "LocalTechSecret1!",
            },
            "post_config_hostname_override": "esxi-LocalTechSecret1!",
            "post_config_inventory": {
                "datastores": [{"name": "LOCAL-WugSecret1!", "capacity_gb": 120}],
                "scsi_disks": [{"name": "naa.SnmpAuthSecret1!", "size_gb": 2048, "in_use": False}],
                "physical_nics": [{"name": "vmnic0-SnmpPrivSecret1!", "speed_mbps": 1000}],
            },
        }
    )
    cfg["ilo"].update({"current_ip": "192.168.1.110", "host": "192.168.1.110", "password": "IloSecretForEsxi1!"})
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "system": {"path": "/redfish/v1/Systems/1", "power_state": "Off"},
                "active_interface": {"ipv4_addresses": [{"Address": "192.168.1.110"}]},
            },
            "raw": {
                "system": {
                    "@odata.id": "/redfish/v1/Systems/1?token=EsxiUrlTokenSecret1!",
                    "PowerState": "Off",
                    "Boot": {
                        "BootSourceOverrideEnabled": "Once",
                        "BootSourceOverrideTarget": "Cd-SavedRootSecret1!",
                    },
                    "Actions": {
                        "#ComputerSystem.Reset": {
                            "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset?token=EsxiUrlTokenSecret1!",
                            "ResetType@Redfish.AllowableValues": ["On", "ForceOff"],
                        }
                    },
                },
                "virtual_media": [
                    {
                        "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2?token=EsxiUrlTokenSecret1!",
                        "Name": "Virtual CD/DVD",
                        "Inserted": False,
                        "Actions": {
                            "#VirtualMedia.InsertMedia": {
                                "target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"
                            }
                        },
                    }
                ],
            },
        },
    )

    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    assert "ESXi Secret Render Kit" in response.text or "ESXi setup" in response.text
    for secret in [
        "SavedRootSecret1!",
        "WugSecret1!",
        "SnmpAuthSecret1!",
        "SnmpPrivSecret1!",
        "KitRootSecret1!",
        "SvmServiceSecret1!",
        "LocalTechSecret1!",
        "IloSecretForEsxi1!",
        "EsxiUrlTokenSecret1!",
    ]:
        assert secret not in response.text
    assert "Leave blank to keep saved value" in response.text
    assert "Saved - leave blank to keep" in response.text
    assert "********" in response.text


def test_esxi_debug_mode_shows_fake_ilo_virtual_media_power_and_boot_details(esxi_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi-Test-Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.110",
            "host": "192.168.1.110",
            "username": "Administrator",
            "password": "SavedSecret123",
        }
    )
    cfg["esxi"].update({"hostname": "esxi01", "root_password": "Valid1Pass!"})
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "service_root": {"redfish_version": "1.11.0"},
                "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 5", "firmware": "3.19"},
                "system": {"path": "/redfish/v1/Systems/1", "model": "ProLiant DL360", "power_state": "Off"},
                "network_protocol": {"path": "/redfish/v1/Managers/1/NetworkProtocol", "hostname": "ilo01"},
                "active_interface": {"hostname": "ilo01", "ipv4_addresses": [{"Address": "192.168.1.110"}]},
            },
            "raw": {
                "system": {
                    "@odata.id": "/redfish/v1/Systems/1",
                    "PowerState": "Off",
                    "Boot": {
                        "BootSourceOverrideEnabled": "Once",
                        "BootSourceOverrideTarget": "Cd",
                    },
                    "Actions": {
                        "#ComputerSystem.Reset": {
                            "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                            "ResetType@Redfish.AllowableValues": ["On", "ForceOff", "GracefulRestart"],
                        }
                    },
                },
                "virtual_media": [
                    {
                        "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                        "Name": "Virtual CD/DVD",
                        "Inserted": False,
                        "Image": "",
                        "Actions": {
                            "#VirtualMedia.InsertMedia": {
                                "target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"
                            },
                            "#VirtualMedia.EjectMedia": {
                                "target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia"
                            },
                        },
                    }
                ],
            },
        },
    )

    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    assert "Discovered/current install state" in response.text
    assert "Source Saved kit values" in response.text
    operator_section = response.text.split('id="esxi-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Virtual media 1 device | Insert actions 1 | Eject actions 1" in operator_section
    assert "Boot override Once / Cd" in operator_section
    assert "/redfish/v1/Managers/1/VirtualMedia/2" not in operator_section
    assert "iLO virtual media and boot path" in response.text
    assert "PowerState" in response.text
    assert "Off" in response.text
    assert "Insert media actions" in response.text
    assert "Boot override" in response.text
    assert "Once / Cd" in response.text
    assert "Allowed reset types" in response.text
    assert "ForceOff" in response.text
    assert "ComputerSystem.Reset target" in response.text
    assert "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" in response.text
    assert "First virtual media path" in response.text
    assert "/redfish/v1/Managers/1/VirtualMedia/2" in response.text
    assert "Recovery suggestions" in response.text
    assert "virtual media insert/eject actions" in response.text
    assert "explicit operator action in Run Center" in response.text
    assert "SavedSecret123" not in response.text


def test_esxi_operator_mode_hides_missing_boot_override_readback(esxi_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Missing Boot Override Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.110",
            "host": "192.168.1.110",
            "username": "Administrator",
            "password": "SavedSecret123",
        }
    )
    cfg["esxi"].update({"hostname": "esxi01", "root_password": "Valid1Pass!"})
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "system": {"path": "/redfish/v1/Systems/1", "power_state": "On"},
                "active_interface": {"ipv4_addresses": [{"Address": "192.168.1.110"}]},
            },
            "raw": {
                "system": {
                    "@odata.id": "/redfish/v1/Systems/1",
                    "PowerState": "On",
                    "Actions": {
                        "#ComputerSystem.Reset": {
                            "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                            "ResetType@Redfish.AllowableValues": ["GracefulRestart", "ForceRestart"],
                        }
                    },
                },
                "virtual_media": [
                    {
                        "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                        "Name": "Virtual CD/DVD",
                        "Inserted": False,
                        "Actions": {
                            "#VirtualMedia.InsertMedia": {
                                "target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"
                            }
                        },
                    }
                ],
            },
        },
    )

    response = esxi_client.get("/esxi")

    assert response.status_code == 200
    operator_section = response.text.split('id="esxi-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "On" in operator_section
    assert "Virtual media 1 device | Insert actions 1 | Eject actions 0" in operator_section
    assert "Boot override Not captured" not in operator_section
    assert "ComputerSystem.Reset target" not in operator_section
    assert "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" not in operator_section
    assert "iLO virtual media and boot path" in response.text
    assert "Boot override" in response.text
    assert "Not captured" in response.text
    assert "ComputerSystem.Reset target" in response.text
    assert "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" in response.text
    assert "SavedSecret123" not in response.text
