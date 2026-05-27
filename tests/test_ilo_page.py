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
    assert 'id="ilo-save-form"' in response.text
    assert 'data-action-title="Saving iLO setup"' in response.text
    assert 'data-action-complete="iLO setup saved."' in response.text
    assert 'hx-post="/export-ilo-inventory"' in response.text
    assert 'data-action-complete="Current iLO read finished."' in response.text
    assert 'hx-post="/save-upgrade-override"' in response.text
    assert 'hx-vals=\'{"return_page":"ilo","device_key":"ilo"}\'' in response.text
    assert 'data-action-title="Saving iLO upgrade override"' in response.text
    assert (
        'data-action-start="Updating whether iLO can continue setup while its upgrade gate is unresolved."'
        in response.text
    )
    assert 'data-action-complete="iLO upgrade override saved."' in response.text
    assert 'href="/storage"' in response.text
    assert 'href="/execution"' in response.text
    assert "iLO operator checkpoint" in response.text
    assert "Operator Mode" in response.text
    assert "Open Debug Mode/details" in response.text
    assert 'href="#ilo-debug-details"' in response.text
    assert 'id="ilo-debug-details"' in response.text
    assert "Debug Mode/details" in response.text
    assert "logs/status" in response.text
    assert "Next step" in response.text
    assert "Completion state" in response.text
    assert "Last result" in response.text
    assert "Saved Lab Builder kit config" in response.text
    assert "Discovered/current iLO state" in response.text
    assert "Planned/suggested values" in response.text
    debug_section_index = response.text.index('id="ilo-debug-details"')
    assert response.text.index("Standard iLO policy") > debug_section_index
    assert response.text.index("More local iLO users") > debug_section_index
    assert 'name="ilo_policy_apply_standard_policy" form="ilo-save-form"' in response.text
    assert "Allow iLO reset during manual apply" in response.text
    assert '<span class="status progress">manual only</span>' in response.text
    assert '<span class="status danger">disruptive</span>' in response.text
    assert 'name="ilo_extra_username" x-model="user.username" form="ilo-save-form"' in response.text
    assert "Discovery 192.168.1.21-29" in response.text
    assert "192.168.1.67" in response.text
    assert "192.168.1.68" in response.text
    assert '<details class="card identity-soft-card" open>' not in response.text
    assert '<details class="card identity-soft-card">' in response.text
    assert "What happened last" in response.text
    assert "No iLO run has finished for this kit yet." in response.text
    assert "Save access before reading iLO" in response.text


def test_ilo_page_upgrade_gate_override_route_saves_and_returns_feedback(ilo_client):
    response = ilo_client.post(
        "/save-upgrade-override",
        data={"return_page": "ilo", "device_key": "ilo", "override_upgrade_gate": "true"},
    )

    assert response.status_code == 200
    assert "Upgrade override saved" in response.text
    assert "Override: enabled" in response.text
    assert 'data-action-title="Saving iLO upgrade override"' in response.text
    saved = main.load_kit_config()
    assert saved["upgrade_helper"]["overrides"]["ilo"] is True


def test_ilo_page_render_does_not_touch_real_ilo_client(ilo_client, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("iLO page render must not open a real iLO client")

    monkeypatch.setattr(main, "ILOClient", fail_if_called)

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    assert "iLO operator checkpoint" in response.text


def test_ilo_page_latest_receipt_open_log_uses_report_route(ilo_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO Log Kit"
    cfg["ilo"]["password"] = "IloReceiptSecret1!"
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
                "current_stage": "Finished with IloReceiptSecret1!",
                "config_summary": {"dns_apply_status": "IloReceiptSecret1!"},
                "run_summary_path": str(summary_path),
            }
        ],
    )

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    assert "Last iLO run" in response.text
    assert '<details class="card identity-soft-card" open>' not in response.text
    assert '<details class="card identity-soft-card">' in response.text
    operator_section = response.text.split('id="ilo-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "ilo-open-log-summary.yml" in operator_section
    assert str(summary_path) not in operator_section
    assert "IloReceiptSecret1!" not in response.text
    assert "********" in response.text
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


def test_ilo_operator_completion_prioritizes_missing_credentials_over_cached_read(ilo_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO Missing Credentials Cached Read Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.110",
            "host": "192.168.1.110",
            "username": "Administrator",
            "password": "",
        }
    )
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 5", "firmware": "3.19"},
                "system": {"path": "/redfish/v1/Systems/1", "model": "ProLiant DL360", "power_state": "On"},
                "active_interface": {"ipv4_addresses": [{"Address": "192.168.1.110"}]},
            },
            "raw": {},
        },
    )

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    operator_section = response.text.split('id="ilo-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Add iLO credentials" in operator_section
    assert "Needs saved access" in operator_section
    assert "Current iLO read cached" in operator_section
    assert "Current state captured" not in operator_section


def test_ilo_page_does_not_render_saved_policy_or_extra_user_secrets(ilo_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO Secret Render Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.110",
            "target_ip": "192.168.1.111",
            "username": "Administrator",
            "password": "SavedIloLoginSecret1!",
            "additional_users": [
                {"username": "ExtraAdmin", "password": "ExtraUserSecret1!", "role": "Administrator"}
            ],
            "policy": {
                "kit_admin_password": "KitAdminSecret1!",
                "kit_operator_password": "KitOperatorSecret1!",
                "shared_admin_password": "SharedAdminSecret1!",
                "snmp_read_community": "ReadCommunitySecret1!",
                "snmpv3_auth_password": "SnmpAuthSecret1!",
                "snmpv3_priv_password": "SnmpPrivSecret1!",
            },
        }
    )
    cfg["shared_snmp"] = {
        "read_community": "SharedReadCommunitySecret1!",
        "v3_auth_password": "SharedSnmpAuthSecret1!",
        "users": [{"auth_password": "SharedUserAuthSecret1!", "priv_password": "SharedUserPrivSecret1!"}],
    }
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 5 SavedIloLoginSecret1!", "firmware": "3.19"},
                "system": {"path": "/redfish/v1/Systems/1", "model": "ProLiant DL360", "power_state": "On"},
                "network_protocol": {
                    "path": "/redfish/v1/Managers/1/NetworkProtocol",
                    "hostname": "ilo-SnmpAuthSecret1!",
                    "snmp": {"SNMPv3Enabled": True, "SNMPv3Username": "SharedUserAuthSecret1!"},
                },
                "active_interface": {
                    "hostname": "ilo-SharedReadCommunitySecret1!",
                    "fqdn": "ilo.example.local?token=UrlTokenSecret1!",
                    "ipv4_addresses": [{"Address": "192.168.1.110"}],
                    "static_name_servers": ["192.168.1.1"],
                },
                "accounts": [{"username": "ExtraAdmin", "role": "KitAdminSecret1!"}],
            },
            "raw": {
                "manager": {
                    "Model": "iLO 5 SavedIloLoginSecret1!",
                    "FirmwareVersion": "3.19",
                    "Actions": {"#Manager.Reset": {"target": "/redfish/v1/Managers/1/Actions/Manager.Reset?token=UrlTokenSecret1!"}},
                },
                "system": {
                    "@odata.id": "/redfish/v1/Systems/1",
                    "PowerState": "On",
                    "Actions": {
                        "#ComputerSystem.Reset": {
                            "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                            "ResetType@Redfish.AllowableValues": ["On", "ForceOff"],
                        }
                    },
                },
                "virtual_media": [
                    {
                        "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                        "Name": "Virtual CD/DVD",
                        "Inserted": True,
                        "Image": "https://repo:UrlPasswordSecret1!@192.168.1.10/ESXi.iso?token=UrlTokenSecret1!",
                    }
                ],
                "capability_dump": {
                    "network_protocol_path": "/redfish/v1/Managers/1/NetworkProtocol",
                    "network_protocol_keys": ["HostName", "SNMP"],
                    "snmp_keys": ["SNMPv3Enabled", "SNMPv3Username"],
                    "snmp_object": {"SNMPv3Enabled": True, "SNMPv3Username": "SharedUserPrivSecret1!"},
                },
            },
        },
    )

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    assert "ExtraAdmin" in response.text
    for secret in [
        "SavedIloLoginSecret1!",
        "ExtraUserSecret1!",
        "KitAdminSecret1!",
        "KitOperatorSecret1!",
        "SharedAdminSecret1!",
        "ReadCommunitySecret1!",
        "SnmpAuthSecret1!",
        "SnmpPrivSecret1!",
        "SharedReadCommunitySecret1!",
        "SharedSnmpAuthSecret1!",
        "SharedUserAuthSecret1!",
        "SharedUserPrivSecret1!",
        "UrlPasswordSecret1!",
        "UrlTokenSecret1!",
    ]:
        assert secret not in response.text
    assert '"password": ""' in response.text
    assert "Saved - leave blank to keep" in response.text
    assert "********" in response.text


def test_ilo_inventory_export_redacts_rendered_summary_and_artifacts(ilo_client, monkeypatch):
    class SecretInventoryClient:
        def __init__(self, _config):
            pass

        def get_current_config_snapshot(self):
            return {
                "summary": {
                    "service_root": {"redfish_version": "1.11.0"},
                    "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 5 SavedIloExportSecret1!", "firmware": "3.19"},
                    "system": {"path": "/redfish/v1/Systems/1", "model": "ProLiant DL360", "power_state": "On"},
                    "network_protocol": {
                        "path": "/redfish/v1/Managers/1/NetworkProtocol?token=UrlTokenExportSecret1!",
                        "hostname": "ilo-SnmpAuthExportSecret1!",
                        "snmp": {
                            "SNMPv3Enabled": True,
                            "SNMPv3Username": "SharedUserAuthExportSecret1!",
                            "ReadCommunity": "ReadCommunityExportSecret1!",
                        },
                    },
                    "active_interface": {
                        "hostname": "ilo-SharedReadCommunityExportSecret1!",
                        "fqdn": "ilo.example.local?token=UrlTokenExportSecret1!",
                        "ipv4_addresses": [{"Address": "192.168.1.110"}],
                        "static_name_servers": ["192.168.1.1"],
                    },
                    "accounts": [{"username": "ExtraAdmin", "role": "KitAdminExportSecret1!"}],
                },
                "raw": {
                    "manager": {
                        "Model": "iLO 5 SavedIloExportSecret1!",
                        "Actions": {
                            "#Manager.Reset": {
                                "target": "/redfish/v1/Managers/1/Actions/Manager.Reset?token=UrlTokenExportSecret1!"
                            }
                        },
                    },
                    "virtual_media": [
                        {
                            "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                            "Image": "https://repo:UrlPasswordExportSecret1!@192.168.1.10/ESXi.iso?token=UrlTokenExportSecret1!",
                        }
                    ],
                    "capability_dump": {
                        "snmp_object": {"SNMPv3Username": "SharedUserPrivExportSecret1!"},
                    },
                },
            }

    monkeypatch.setattr(main, "ILOClient", SecretInventoryClient)
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO Export Redaction Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.110",
            "host": "192.168.1.110",
            "target_ip": "192.168.1.111",
            "hostname": "ilo-export",
            "username": "Administrator",
            "password": "SavedIloExportSecret1!",
            "additional_users": [
                {"username": "ExtraAdmin", "password": "ExtraUserExportSecret1!", "role": "Administrator"}
            ],
            "policy": {
                "kit_admin_password": "KitAdminExportSecret1!",
                "snmp_read_community": "ReadCommunityExportSecret1!",
                "snmpv3_auth_password": "SnmpAuthExportSecret1!",
                "snmpv3_priv_password": "SnmpPrivExportSecret1!",
            },
        }
    )
    cfg["shared_snmp"] = {
        "read_community": "SharedReadCommunityExportSecret1!",
        "v3_auth_password": "SharedSnmpAuthExportSecret1!",
        "users": [{"auth_password": "SharedUserAuthExportSecret1!", "priv_password": "SharedUserPrivExportSecret1!"}],
    }
    main.save_kit_config(cfg)

    response = ilo_client.post("/export-ilo-inventory", data={"return_page": "ilo"})

    assert response.status_code == 200
    assert "Current iLO inventory captured" in response.text
    assert "Latest Live Summary" in response.text
    latest = main.latest_live_inventory_export()
    assert latest is not None
    summary_text = latest["summary"].read_text(encoding="utf-8")
    raw_text = latest["raw"].read_text(encoding="utf-8")
    for secret in [
        "SavedIloExportSecret1!",
        "ExtraUserExportSecret1!",
        "KitAdminExportSecret1!",
        "ReadCommunityExportSecret1!",
        "SnmpAuthExportSecret1!",
        "SnmpPrivExportSecret1!",
        "SharedReadCommunityExportSecret1!",
        "SharedSnmpAuthExportSecret1!",
        "SharedUserAuthExportSecret1!",
        "SharedUserPrivExportSecret1!",
        "UrlPasswordExportSecret1!",
        "UrlTokenExportSecret1!",
    ]:
        assert secret not in response.text
        assert secret not in summary_text
        assert secret not in raw_text
    assert "********" in response.text
    assert "********" in summary_text
    assert "********" in raw_text


def test_ilo_debug_mode_shows_fake_live_power_reset_and_virtual_media_details(ilo_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO-Test-Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.110",
            "host": "192.168.1.110",
            "target_ip": "192.168.1.110",
            "username": "Administrator",
            "password": "SavedSecret123",
        }
    )
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "service_root": {"redfish_version": "1.11.0"},
                "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 5", "firmware": "3.19"},
                "system": {"path": "/redfish/v1/Systems/1", "model": "ProLiant DL360", "power_state": "On"},
                "network_protocol": {
                    "path": "/redfish/v1/Managers/1/NetworkProtocol",
                    "hostname": "ilo01",
                    "snmp": {"SNMPv3Enabled": True},
                },
                "active_interface": {
                    "hostname": "ilo01",
                    "ipv4_addresses": [{"Address": "192.168.1.110"}],
                    "static_name_servers": ["192.168.1.1"],
                },
                "accounts": [{"username": "Administrator", "role": "Administrator"}],
            },
            "raw": {
                "manager": {
                    "Model": "iLO 5",
                    "FirmwareVersion": "3.19",
                    "Actions": {
                        "#Manager.Reset": {"target": "/redfish/v1/Managers/1/Actions/Manager.Reset"}
                    },
                },
                "system": {
                    "@odata.id": "/redfish/v1/Systems/1",
                    "PowerState": "On",
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
                "capability_dump": {
                    "network_protocol_path": "/redfish/v1/Managers/1/NetworkProtocol",
                    "network_protocol_keys": ["HostName", "SNMP"],
                    "snmp_keys": ["SNMPv3Enabled"],
                    "snmp_object": {"SNMPv3Enabled": True},
                    "ethernet_interfaces": [
                        {
                            "path": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                            "keys": ["IPv4Addresses", "StaticNameServers", "VLAN"],
                            "host_name": "ilo01",
                            "link_status": "LinkUp",
                            "static_name_servers": ["192.168.1.1"],
                        }
                    ],
                },
            },
        },
    )

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    assert "Current state captured" in response.text
    assert "Discovered/current iLO state" in response.text
    assert "192.168.1.110" in response.text
    assert "iLO 5 / Firmware 3.19 / Power On" in response.text
    assert "Redfish version" in response.text
    assert "1.11.0" in response.text
    operator_section = response.text.split('id="ilo-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Virtual media 1 device | Insert actions 1 | Eject actions 1" in operator_section
    assert "/redfish/v1/Managers/1/VirtualMedia/2" not in operator_section
    assert "Power and safe reset" in response.text
    assert "Current PowerState" in response.text
    assert "On" in response.text
    assert "ComputerSystem.Reset target" in response.text
    assert "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" in response.text
    assert "Allowed reset types" in response.text
    assert "ForceOff" in response.text
    assert "Virtual media and remote install" in response.text
    assert "Insert media actions" in response.text
    assert "/redfish/v1/Managers/1/VirtualMedia/2" in response.text
    assert "Recovery suggestions" in response.text
    assert "allowed ComputerSystem.Reset values" in response.text
    assert "clear stale media before mounting the ESXi installer" in response.text
    assert "SavedSecret123" not in response.text


def test_ilo_operator_mode_summarizes_power_state_without_reset_endpoint(ilo_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO-Power-State-Boundary-Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.115",
            "host": "192.168.1.115",
            "target_ip": "192.168.1.115",
            "username": "Administrator",
            "password": "SavedSecret123",
        }
    )
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "service_root": {"redfish_version": "1.11.0"},
                "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 6", "firmware": "3.20"},
                "system": {"path": "/redfish/v1/Systems/1", "model": "ProLiant DL380", "power_state": "Off"},
                "active_interface": {
                    "hostname": "ilo-power",
                    "ipv4_addresses": [{"Address": "192.168.1.115"}],
                },
            },
            "raw": {
                "system": {
                    "@odata.id": "/redfish/v1/Systems/1",
                    "PowerState": "Off",
                    "Actions": {
                        "#ComputerSystem.Reset": {
                            "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                            "ResetType@Redfish.AllowableValues": ["On", "ForceOff"],
                        }
                    },
                },
                "virtual_media": [],
            },
        },
    )

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    operator_section = response.text.split('id="ilo-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Current state captured" in operator_section
    assert "iLO 6 / Firmware 3.20 / Power Off" in operator_section
    assert "ComputerSystem.Reset target" not in operator_section
    assert "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" not in operator_section
    assert "Expected safe power workflow" not in operator_section
    assert "Power and safe reset" in response.text
    assert "Current PowerState" in response.text
    assert "Off" in response.text
    assert "ComputerSystem.Reset target" in response.text
    assert "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" in response.text
    assert "Allowed reset types" in response.text
    assert "On, ForceOff" in response.text
    assert "Expected safe power workflow" in response.text
    assert "SavedSecret123" not in response.text


def test_ilo_operator_mode_summarizes_stale_virtual_media_without_image_path(ilo_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO-Stale-Virtual-Media-Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.116",
            "host": "192.168.1.116",
            "target_ip": "192.168.1.116",
            "username": "Administrator",
            "password": "SavedSecret123",
        }
    )
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "service_root": {"redfish_version": "1.11.0"},
                "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 5", "firmware": "3.19"},
                "system": {"path": "/redfish/v1/Systems/1", "model": "ProLiant DL360", "power_state": "On"},
                "active_interface": {
                    "hostname": "ilo-stale-media",
                    "ipv4_addresses": [{"Address": "192.168.1.116"}],
                },
            },
            "raw": {
                "virtual_media": [
                    {
                        "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                        "Name": "Virtual CD/DVD",
                        "Inserted": True,
                        "Image": "https://repo:SavedSecret123@192.168.1.50/old-esxi.iso",
                        "Actions": {
                            "#VirtualMedia.EjectMedia": {
                                "target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia"
                            }
                        },
                    }
                ],
            },
        },
    )

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    operator_section = response.text.split('id="ilo-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Current state captured" in operator_section
    assert "Virtual media 1 device | Insert actions 0 | Eject actions 1" in operator_section
    assert "old-esxi.iso" not in operator_section
    assert "/redfish/v1/Managers/1/VirtualMedia/2" not in operator_section
    assert "Virtual media and remote install" in response.text
    assert "Virtual media 1" in response.text
    assert "inserted=True" in response.text
    assert "image=https://repo:********@192.168.1.50/old-esxi.iso" in response.text
    assert "clear stale media before mounting the ESXi installer" in response.text
    assert "SavedSecret123" not in response.text


def test_ilo_operator_mode_shows_empty_virtual_media_state_without_paths(ilo_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "iLO-Empty-Virtual-Media-Kit"
    cfg["ilo"].update(
        {
            "current_ip": "192.168.1.110",
            "host": "192.168.1.110",
            "target_ip": "192.168.1.110",
            "username": "Administrator",
            "password": "SavedSecret123",
        }
    )
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(
        cfg,
        {
            "summary": {
                "service_root": {"redfish_version": "1.11.0"},
                "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 5", "firmware": "3.19"},
                "system": {"path": "/redfish/v1/Systems/1", "model": "ProLiant DL360", "power_state": "On"},
                "network_protocol": {
                    "path": "/redfish/v1/Managers/1/NetworkProtocol",
                    "hostname": "ilo01",
                },
                "active_interface": {
                    "hostname": "ilo01",
                    "ipv4_addresses": [{"Address": "192.168.1.110"}],
                    "static_name_servers": ["192.168.1.1"],
                },
            },
            "raw": {"virtual_media": []},
        },
    )

    response = ilo_client.get("/ilo")

    assert response.status_code == 200
    operator_section = response.text.split('id="ilo-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Current state captured" in operator_section
    assert "Virtual media none detected" in operator_section
    assert "/redfish/v1/Managers/1/VirtualMedia" not in operator_section
    assert "Virtual media and remote install" in response.text
    assert "Virtual media devices" in response.text
    assert "None detected" in response.text
    assert "SavedSecret123" not in response.text
