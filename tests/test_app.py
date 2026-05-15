import asyncio
import copy
import pytest
import yaml
import requests
import shutil
from pathlib import Path
from typing import Any
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ilo import ILOClient, ILOConfig, ILOError
from app.windows import VsphereClient
from app.esxi.kickstart import build_kickstart, redact_kickstart_text
from app.core.config import build_default_ip_plan
from app.core.models import KitConfigModel
from app.core.registry import (
    apply_module_enable_overrides,
    discover_module_manifests,
    module_navigation,
    load_modules,
)
from app.core.stage_registry import StageRegistry, CallableStagePlugin
from app.stages.ilo.runtime import build_snmp_readback_checks, current_snmp_matches, verify_final_ilo_state
import app.ilo as ilo_module
import app.main as main
import app.modules.cisco.routes as cisco_module_routes
import app.modules.cisco.service as cisco_service
from app.debug_bundle import redact_value


def fake_esxi_base_iso(tmp_path: Path) -> Path:
    path = tmp_path / "base-esxi.iso"
    path.write_text("iso", encoding="utf-8")
    return path


def test_validate_execution_scope_blocks_on_upgrade_gate(monkeypatch):
    cfg = main.default_config()
    cfg["upgrade_inventory"] = {
        "netapp": {"current_version": "9.9.1P2", "source": "Last NetApp discovery", "last_checked_at": "2026-05-13T00:00:00+00:00"},
    }
    cfg["upgrade_helper"] = {"policies": {"netapp": "block"}}
    cfg["included"]["netapp"] = True
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {
        "root": "/repo/media",
        "latest": {"netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}},
        "counts": {"netapp": 1},
        "candidates": [],
    })

    with pytest.raises(ValueError, match="Upgrade ONTAP"):
        main.validate_execution_scope(cfg, "netapp")


def test_validate_execution_scope_allows_warn_only_upgrade_gap(monkeypatch):
    cfg = main.default_config()
    cfg["upgrade_inventory"] = {
        "netapp": {"current_version": "9.9.1P2", "source": "Last NetApp discovery", "last_checked_at": "2026-05-13T00:00:00+00:00"},
    }
    cfg["upgrade_helper"] = {"policies": {"netapp": "warn"}}
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {
        "root": "/repo/media",
        "latest": {"netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}},
        "counts": {"netapp": 1},
        "candidates": [],
    })

    main.validate_execution_scope(cfg, "netapp")


def test_upgrade_helper_runs_ilo_upgrade_while_ilo_page_shows_gate_only(client, monkeypatch):
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [],
    })
    cfg = main.default_config()
    cfg["upgrade_inventory"] = {
        "ilo": {"current_version": "1.50", "source": "Latest live iLO inventory", "manager_model": "iLO 6"},
    }
    main.save_kit_config(cfg)

    response = client.get("/upgrade-helper")
    assert response.status_code == 200
    assert "Plan iLO upgrade" in response.text
    assert "Run iLO upgrade" in response.text
    assert "iLO upgrade status" in response.text

    response = client.get("/ilo")
    assert response.status_code == 200
    assert "iLO firmware upgrade" in response.text
    assert "1.50" in response.text
    assert "1.76" in response.text
    assert "Run iLO upgrade" not in response.text
    assert "Plan iLO upgrade" not in response.text
    assert 'hx-post="/run-ilo-upgrade"' not in response.text
    assert 'hx-post="/plan-ilo-upgrade"' not in response.text
    assert "iLO upgrade status" not in response.text

    response = client.get("/ilo-upgrade-activity")
    assert response.status_code == 200
    assert "iLO upgrade status" in response.text
    assert "progress-bar" in response.text


def test_ilo_page_gate_can_read_current_ilo_when_version_unknown(client, monkeypatch):
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [],
    })
    cfg = main.default_config()
    cfg["upgrade_inventory"] = {
        "ilo": {"current_version": "", "source": "", "manager_model": "iLO 6"},
    }
    main.save_kit_config(cfg)

    response = client.get("/ilo")

    assert response.status_code == 200
    assert "iLO firmware upgrade" in response.text
    assert "Unknown" in response.text
    assert "1.76" in response.text
    assert "Read the current iLO version before prebuild." in response.text
    assert 'hx-post="/export-ilo-inventory"' in response.text
    assert 'data-action-start="Connecting to iLO and reading the current firmware version."' in response.text
    assert "Run iLO upgrade" not in response.text
    assert "Plan iLO upgrade" not in response.text


def test_ilo_upgrade_plan_uses_latest_live_read_when_cached_inventory_is_stale(client, monkeypatch):
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "3.20", "filename": "ilo6_320.fwpkg", "path": "/repo/media/ilo6_320.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [
            {"device": "ilo", "version": "3.20", "filename": "ilo6_320.fwpkg", "path": "/repo/media/ilo6_320.fwpkg"},
        ],
    })
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Upgrade Sync Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.50"
    cfg["ilo"]["host"] = "10.10.8.50"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    cfg["ilo"]["upgrade"] = {
        "activity": {"status": "blocked", "message": "Read current iLO first so the app can identify the iLO family."},
        "last_result": {"status": "blocked", "error": "Read current iLO first so the app can identify the iLO family."},
    }
    cfg["upgrade_inventory"] = {"ilo": {"current_version": "", "source": "", "manager_model": ""}}
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(cfg, FakeILOClient(None).get_current_config_snapshot(), source_host="10.10.8.50")

    response = client.post("/plan-ilo-upgrade", data={"return_page": "upgrade_helper"})

    assert response.status_code == 200
    assert "iLO upgrade plan ready" in response.text
    assert "Read current iLO first" not in response.text
    saved = main.load_kit_config("Ilo-Upgrade-Sync-Kit")
    assert saved["upgrade_inventory"]["ilo"]["current_version"] == "3.00"
    assert saved["upgrade_inventory"]["ilo"]["manager_model"] == "iLO 6"
    assert saved["ilo"]["upgrade"]["last_plan"]["ready"] is True
    assert saved["ilo"]["upgrade"]["last_plan"]["media_filename"] == "ilo6_320.fwpkg"
    assert saved["ilo"]["upgrade"]["activity"] == {}
    assert saved["ilo"]["upgrade"]["last_result"] == {}


def test_run_ilo_upgrade_uses_latest_live_read_when_cached_inventory_is_stale(client, monkeypatch):
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "3.20", "filename": "ilo6_320.fwpkg", "path": "/repo/media/ilo6_320.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [
            {"device": "ilo", "version": "3.20", "filename": "ilo6_320.fwpkg", "path": "/repo/media/ilo6_320.fwpkg"},
        ],
    })
    monkeypatch.setattr(main, "_start_ilo_upgrade_worker", lambda cfg, media_scan: None)
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Upgrade Run Sync Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.50"
    cfg["ilo"]["host"] = "10.10.8.50"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    cfg["upgrade_inventory"] = {"ilo": {"current_version": "", "source": "", "manager_model": ""}}
    main.save_kit_config(cfg)
    main.export_ilo_inventory_snapshot(cfg, FakeILOClient(None).get_current_config_snapshot(), source_host="10.10.8.50")

    response = client.post("/run-ilo-upgrade", data={"return_page": "upgrade_helper"})

    assert response.status_code == 200
    assert "iLO upgrade started" in response.text
    assert "iLO upgrade blocked" not in response.text
    saved = main.load_kit_config("Ilo-Upgrade-Run-Sync-Kit")
    assert saved["upgrade_inventory"]["ilo"]["current_version"] == "3.00"
    assert saved["ilo"]["upgrade"]["activity"]["status"] == "running"
    assert saved["ilo"]["upgrade"]["last_plan"]["ready"] is True


def test_upgrade_helper_shows_netapp_and_cisco_upgrade_actions(client):
    cfg = main.default_config()
    cfg["upgrade_inventory"] = {
        "netapp": {"current_version": "9.9.1P2", "source": "Last NetApp discovery"},
        "cisco_switch": {"current_version": "17.03.01", "source": "Last Cisco discovery"},
    }
    main.save_kit_config(cfg)

    response = client.get("/upgrade-helper")
    assert response.status_code == 200
    assert "Review ONTAP upgrade plan" in response.text
    assert "Run ONTAP upgrade" in response.text
    assert "Review Cisco upgrade plan" in response.text
    assert "Run Cisco upgrade" in response.text
    assert "Upgrade Helper" in response.text


def test_cisco_discover_console_surfaces_permission_denied(client, monkeypatch):
    monkeypatch.setattr(
        cisco_module_routes.service,
        "discover_console",
        lambda _context: {
            "ok": False,
            "error": "No Cisco console prompt was detected.",
            "warnings": [],
            "candidates": [],
            "probe_results": [
                {
                    "port": "/dev/ttyUSB0",
                    "baud": 9600,
                    "description": "USB serial",
                    "hardware_id": "USB VID:PID=067B:2303",
                    "manufacturer": "Prolific",
                    "prompt_type": "",
                    "score": 0,
                    "error": "[Errno 13] Permission denied: '/dev/ttyUSB0'",
                }
            ],
            "diagnostics": {
                "serial_imported": True,
                "ordered_ports": ["/dev/ttyUSB0"],
                "group_names": ["administrator", "plugdev"],
                "permission_denied": True,
                "error_summary": "The Lab Builder process can see the serial adapter but cannot open it. The server user needs access to the serial device group.",
            },
        },
    )

    response = client.post("/modules/cisco/discover-console")

    assert response.status_code == 200
    assert "Host permission needed" in response.text
    assert "cannot open it" in response.text
    assert "Permission denied" in response.text

    cfg = main.load_kit_config()
    assert "cannot open it" in cfg["cisco_switch"]["last_discovery_error"]


def test_cisco_fix_serial_permissions_renders_password_prompt_and_result(client, monkeypatch):
    monkeypatch.setattr(
        cisco_module_routes.service,
        "fix_serial_permissions",
        lambda _context, _password: {
            "ok": True,
            "error": "",
            "applied": ["Added administrator to dialout."],
            "warnings": ["Lab Builder must be restarted from a fresh login or service session before the dialout group membership applies to the running process."],
            "restart_required": True,
            "diagnostics": {
                "permission_denied": True,
                "group_names": ["administrator"],
                "ordered_ports": ["/dev/ttyUSB0"],
            },
        },
    )

    response = client.post("/modules/cisco/fix-serial-permissions", data={"cisco_host_sudo_password": "pw"})

    assert response.status_code == 200
    assert "Serial permissions updated" in response.text
    assert "Fix serial access" in response.text

    cfg = main.load_kit_config()
    assert cfg["cisco_switch"]["last_host_fix"]["ok"] is True


def test_upgrade_helper_preserves_post_upgrade_ilo_version_over_stale_live_snapshot(monkeypatch):
    cfg = main.default_config()
    cfg["upgrade_inventory"] = {
        "ilo": {
            "current_version": "3.19",
            "source": "Post-upgrade iLO verification",
            "last_checked_at": "2026-05-13T17:09:38+00:00",
            "manager_model": "iLO 5",
        }
    }
    monkeypatch.setattr(
        main,
        "load_latest_live_inventory_snapshot_for_cfg",
        lambda _cfg: {
            "summary": {"ilo_firmware_version": "iLO 5 v3.03"},
            "raw": {"inventory": {"summary": {"manager": {"model": "iLO 5"}}}},
        },
    )
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "3.19", "filename": "ilo5_319.fwpkg", "path": "/repo/media/ilo5_319.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [{"device": "ilo", "version": "3.19", "filename": "ilo5_319.fwpkg", "path": "/repo/media/ilo5_319.fwpkg"}],
    })

    card = main.build_upgrade_helper_card(cfg)
    entry = next(item for item in card["planner"]["entries"] if item["key"] == "ilo")

    assert entry["current_version"] == "3.19"
    assert entry["comparison"] == "current_enough"


@pytest.fixture(autouse=True)
def isolate_runtime_paths(tmp_path, monkeypatch):
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
    monkeypatch.setattr(main, "scan_upgrade_media", lambda: {"root": str(tmp_path / "media"), "latest": {}, "counts": {}, "candidates": []})
    main.set_current_kit_name("Kit-01")


class FakeILOClient:
    def __init__(self, cfg):
        self.cfg = cfg

    def get_current_config_snapshot(self):
        return {
            "summary": {
                "service_root": {"name": "Root", "redfish_version": "1.18.0"},
                "manager": {"path": "/redfish/v1/Managers/1", "model": "iLO 6", "firmware": "3.00"},
                "system": {
                    "path": "/redfish/v1/Systems/1",
                    "manufacturer": "HPE",
                    "model": "ProLiant DL380 Gen11",
                    "product_name": "DL380",
                    "serial_number": "ABC123",
                    "bios_version": "U32 2.10",
                    "power_state": "On",
                },
                "network_protocol": {
                    "path": "/redfish/v1/Managers/1/NetworkProtocol",
                    "hostname": "ilo-live",
                    "fqdn": "ilo-live.example.test",
                    "http": {},
                    "https": {},
                    "snmp": {"ProtocolEnabled": True},
                },
                "active_interface": {
                    "path": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                    "name": "Manager NIC",
                    "hostname": "ilo-live",
                    "fqdn": "ilo-live.example.test",
                    "mac_address": "00:11:22:33:44:55",
                    "interface_enabled": True,
                    "link_status": "LinkUp",
                    "speed_mbps": 1000,
                    "dhcpv4": {"DHCPEnabled": False},
                    "dhcpv6": {},
                    "ipv4_addresses": [{"Address": "10.10.8.50", "SubnetMask": "255.255.255.0", "Gateway": "10.10.8.1"}],
                    "ipv4_static_addresses": [{"Address": "10.10.8.50", "SubnetMask": "255.255.255.0", "Gateway": "10.10.8.1"}],
                    "ipv6_addresses": [],
                    "ipv6_static_addresses": [],
                    "name_servers": ["1.1.1.1", "8.8.8.8"],
                    "static_name_servers": ["1.1.1.1", "8.8.8.8"],
                    "vlan": {},
                },
                "processors": {
                    "model": "Intel Xeon Gold",
                    "count": 2,
                    "total_cores": 32,
                    "total_threads": 64,
                    "items": [{"id": "CPU1", "model": "Intel Xeon Gold", "cores": 16, "threads": 32}],
                },
                "memory": {
                    "total_gib": 256,
                    "dimm_count": 8,
                    "dimms": [{"id": "DIMM1", "capacity_mib": 32768}],
                },
                "accounts": [{"id": "1", "username": "Administrator", "role": "Administrator"}],
                "storage": {
                    "controllers": [{"name": "Smart Array", "firmware_version": {"Current": {"VersionString": "1.98"}}}],
                    "volumes": [{"id": "1", "name": "Volume1"}],
                    "drives": [{"id": "1", "name": "Drive1"}],
                },
                "manager_ethernet_interfaces": [{"id": "1", "name": "Manager NIC"}],
                "system_ethernet_interfaces": [{"id": "NIC1", "name": "NIC 1"}],
            },
            "raw": {
                "service_root": {"RedfishVersion": "1.18.0"},
                "manager": {
                    "Model": "iLO 6",
                    "FirmwareVersion": "3.00",
                    "Actions": {
                        "#Manager.Reset": {"target": "/redfish/v1/Managers/1/Actions/Manager.Reset"}
                    },
                },
                "system": {"Model": "ProLiant DL380 Gen11"},
                "account_service": {"Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}},
                "virtual_media": [
                    {
                        "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                        "Id": "2",
                        "Name": "Virtual CD/DVD",
                        "Actions": {
                            "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                            "#VirtualMedia.EjectMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia"},
                        },
                    }
                ],
                "capability_dump": {
                    "manager_path": "/redfish/v1/Managers/1",
                    "network_protocol_path": "/redfish/v1/Managers/1/NetworkProtocol",
                    "network_protocol_keys": ["FQDN", "HostName", "HTTP", "HTTPS", "SNMP"],
                    "snmp_keys": ["ProtocolEnabled", "SNMPv1Enabled", "SNMPv2cEnabled", "SNMPv3Enabled", "SNMPv3Username"],
                    "snmp_object": {
                        "ProtocolEnabled": True,
                        "SNMPv1Enabled": False,
                        "SNMPv2cEnabled": False,
                        "SNMPv3Enabled": True,
                        "SNMPv3Username": "ops-user",
                    },
                    "network_protocol_oem_keys": ["Hpe"],
                    "network_protocol_oem_hpe_keys": ["AlertMail", "RemoteSyslog"],
                    "ethernet_interfaces": [
                        {
                            "path": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                            "keys": ["DHCPv4", "DHCPv6", "FQDN", "HostName", "IPv4Addresses", "IPv6Addresses", "NameServers", "StaticNameServers", "VLAN"],
                            "oem_keys": ["Hpe"],
                            "oem_hpe_keys": ["DomainName"],
                            "host_name": "ilo-live",
                            "fqdn": "ilo-live.example.test",
                            "interface_enabled": True,
                            "link_status": "LinkUp",
                            "name_servers": ["1.1.1.1", "8.8.8.8"],
                            "static_name_servers": ["1.1.1.1", "8.8.8.8"],
                            "vlan": {},
                        }
                    ],
                },
            },
        }

    def get_storage_discovery(self, deep_smart_storage_scan=False):
        return {
            "summary": {
                "server": {
                    "model": "ProLiant DL380 Gen11",
                    "product_name": "DL380",
                    "generation": "Gen11",
                    "serial_number": "ABC123",
                },
                "ilo": {
                    "model": "iLO 6",
                    "version": "iLO 6",
                    "firmware": "3.00",
                },
                "capabilities": {
                    "standard_redfish_storage": True,
                    "hpe_smart_storage": False,
                    "standard_storage_path": "/redfish/v1/Systems/1/Storage",
                    "hpe_smart_storage_paths": [],
                    "hpe_smart_storage_diagnostics": {
                        "probed_paths": [],
                        "collections": [],
                        "warnings": [],
                        "deep_scan_requested": deep_smart_storage_scan,
                        "deep_fallback_ran": False,
                    },
                },
                "standard_redfish_storage": {
                    "controllers": [
                        {
                            "path": "/redfish/v1/Systems/1/Storage/1",
                            "name": "Smart Array",
                            "model": "MR416i-o",
                            "firmware_version": {"Current": {"VersionString": "1.98"}},
                            "manufacturer": "HPE",
                            "status": "OK / Enabled",
                        }
                    ],
                    "volumes": [
                        {
                            "path": "/redfish/v1/Systems/1/Storage/1/Volumes/1",
                            "id": "1",
                            "name": "OS Volume",
                            "raid_type": "RAID1",
                            "capacity_gib": 480,
                            "drive_bays": ["1"],
                            "spare_bays": ["2"],
                            "status": "OK / Enabled",
                        }
                    ],
                    "drives": [
                        {
                            "path": "/redfish/v1/Systems/1/Storage/1/Drives/1",
                            "id": "1",
                            "bay": "1",
                            "name": "Drive 1",
                            "model": "HPE SSD",
                            "serial_number": "SER1",
                            "size_gib": 480,
                            "media_type": "SSD",
                            "protocol": "SAS",
                            "status": "OK / Enabled",
                        },
                        {
                            "path": "/redfish/v1/Systems/1/Storage/1/Drives/2",
                            "id": "2",
                            "bay": "2",
                            "name": "Drive 2",
                            "model": "HPE SSD",
                            "serial_number": "SER2",
                            "size_gib": 480,
                            "media_type": "SSD",
                            "protocol": "SAS",
                            "status": "OK / Enabled",
                        },
                    ],
                },
            },
            "raw": {
                "source_host": getattr(self.cfg, "host", ""),
                "deep_scan_requested": deep_smart_storage_scan,
            },
        }


def test_ilo_client_retries_get_after_no_valid_session(monkeypatch):
    class FakeCookies:
        def clear(self):
            return None

    class FakeResponse:
        def __init__(self, status_code, text="", json_data=None, headers=None):
            self.status_code = status_code
            self.text = text
            self._json_data = json_data
            self.headers = headers or {}

        def json(self):
            if self._json_data is None:
                raise ValueError("no json")
            return self._json_data

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.cookies = FakeCookies()
            self.first_get = True

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if method == "GET" and url.endswith("/redfish/v1/Managers") and self.first_get:
                self.first_get = False
                return FakeResponse(
                    401,
                    '{"error":{"@Message.ExtendedInfo":[{"MessageId":"Base.1.18.NoValidSession"}]}}',
                    json_data={"error": "NoValidSession"},
                )
            if method == "GET" and url.endswith("/redfish/v1/Managers"):
                return FakeResponse(
                    200,
                    '{"Members":[{"@odata.id":"/redfish/v1/Managers/1"}]}',
                    json_data={"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
                )
            raise AssertionError(f"unexpected request: {method} {url}")

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            assert url.endswith("/redfish/v1/SessionService/Sessions")
            return FakeResponse(
                201,
                "",
                json_data={},
                headers={
                    "X-Auth-Token": "token-123",
                    "Location": "/redfish/v1/SessionService/Sessions/1",
                },
            )

    created = {}

    def build_session():
        session = FakeSession()
        created["session"] = session
        return session

    monkeypatch.setattr(ilo_module.requests, "Session", build_session)

    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="secret"))

    assert client.get_managers() == ["/redfish/v1/Managers/1"]
    session = created["session"]
    assert session.calls[0][0] == "GET"
    assert session.calls[1][0] == "POST"
    assert session.calls[2][0] == "GET"
    assert "auth" in session.calls[0][2]
    assert session.calls[2][2]["headers"]["X-Auth-Token"] == "token-123"

    def get_storage_discovery(self, deep_smart_storage_scan=False):
        return {
            "summary": {
                "server": {
                    "model": "ProLiant DL380 Gen11",
                    "product_name": "DL380",
                    "generation": "Gen11",
                    "serial_number": "ABC123",
                },
                "ilo": {
                    "model": "iLO 6",
                    "version": "iLO 6",
                    "firmware": "3.00",
                },
                "capabilities": {
                    "standard_redfish_storage": True,
                    "hpe_smart_storage": False,
                    "standard_storage_path": "/redfish/v1/Systems/1/Storage",
                    "hpe_smart_storage_paths": [],
                    "hpe_smart_storage_diagnostics": {
                        "probed_paths": [],
                        "collections": [],
                        "warnings": [],
                        "deep_scan_requested": deep_smart_storage_scan,
                        "deep_fallback_ran": False,
                    },
                },
                "standard_redfish_storage": {
                    "controllers": [
                        {
                            "path": "/redfish/v1/Systems/1/Storage/1",
                            "name": "Smart Array",
                            "model": "MR416i-o",
                            "firmware_version": {"Current": {"VersionString": "1.98"}},
                            "manufacturer": "HPE",
                            "status": "OK / Enabled",
                        }
                    ],
                    "volumes": [
                        {
                            "path": "/redfish/v1/Systems/1/Storage/1/Volumes/1",
                            "id": "1",
                            "name": "OS Volume",
                            "raid_type": "RAID1",
                            "capacity_gib": 480,
                            "drive_bays": ["1"],
                            "spare_bays": ["2"],
                            "status": "OK / Enabled",
                        }
                    ],
                    "drives": [
                        {
                            "path": "/redfish/v1/Systems/1/Storage/1/Drives/1",
                            "id": "1",
                            "bay": "1",
                            "name": "Drive 1",
                            "model": "HPE SSD",
                            "serial_number": "DRIVE123",
                            "size_gib": 480,
                            "media_type": "SSD",
                            "protocol": "SAS",
                            "status": "OK / Enabled",
                        },
                        {
                            "path": "/redfish/v1/Systems/1/Storage/1/Drives/2",
                            "id": "2",
                            "bay": "2",
                            "name": "Drive 2",
                            "model": "HPE SSD",
                            "serial_number": "SPARE123",
                            "size_gib": 480,
                            "media_type": "SSD",
                            "protocol": "SAS",
                            "status": "OK / Enabled",
                        }
                    ],
                },
                "hpe_smart_storage": {
                    "controllers": [],
                    "volumes": [],
                    "drives": [],
                    "diagnostics": {
                        "probed_paths": [],
                        "collections": [],
                        "warnings": [],
                        "deep_scan_requested": deep_smart_storage_scan,
                        "deep_fallback_ran": False,
                    },
                },
            },
            "raw": {
                "system": {"Model": "ProLiant DL380 Gen11"},
                "standard_storage": [],
                "hpe_smart_storage": [],
                "hpe_smart_storage_diagnostics": {
                    "probed_paths": [],
                    "collections": [],
                    "warnings": [],
                    "deep_scan_requested": deep_smart_storage_scan,
                    "deep_fallback_ran": False,
                },
            },
        }


class FakeSmartStorageWarningClient(FakeILOClient):
    def get_storage_discovery(self, deep_smart_storage_scan=False):
        discovery = super().get_storage_discovery()
        diagnostics = {
            "probed_paths": [
                {"path": "/redfish/v1/Systems/1/SmartStorage", "status": "ok", "members": 0},
                {"path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers", "status": "ok", "members": 1},
            ],
            "collections": [
                {
                    "owner": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0",
                    "collection": "LogicalDrives",
                    "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives",
                    "status": "empty",
                    "members": 0,
                    "source": "collection",
                },
                {
                    "owner": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0",
                    "collection": "DiskDrives",
                    "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives",
                    "status": "empty",
                    "members": 0,
                    "source": "collection",
                },
            ],
            "warnings": [
                "HPE Smart Storage controller detected, but no logical drives or physical drives were found in the probed child collections."
            ],
            "deep_scan_requested": deep_smart_storage_scan,
            "deep_fallback_ran": True,
        }
        discovery["summary"]["capabilities"]["hpe_smart_storage"] = True
        discovery["summary"]["capabilities"]["hpe_smart_storage_paths"] = ["/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0"]
        discovery["summary"]["capabilities"]["hpe_smart_storage_diagnostics"] = diagnostics
        discovery["summary"]["hpe_smart_storage"] = {
            "controllers": [
                {
                    "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0",
                    "name": "Smart Array P408i-a SR Gen10",
                    "model": "P408i-a",
                    "firmware_version": "4.11",
                    "manufacturer": "HPE",
                    "status": "OK",
                }
            ],
            "volumes": [],
            "drives": [],
            "diagnostics": diagnostics,
        }
        discovery["raw"]["hpe_smart_storage_diagnostics"] = diagnostics
        return discovery


def test_standard_redfish_storage_falls_back_to_storage_member_as_controller():
    client = ILOClient(ILOConfig(host="ilo-gen11.example.test", username="Administrator", password="secret"))
    storage_subsystems = [
        {
            "@odata.id": "/redfish/v1/Systems/1/Storage/DE00C000",
            "Id": "DE00C000",
            "Name": "DE00C000 Controller",
            "Model": "MR408i-o Gen11",
            "FirmwareVersion": "5.10",
            "Manufacturer": "HPE",
            "Status": {"Health": "OK", "State": "Enabled"},
            "StorageControllers": [],
            "VolumesExpanded": [
                {
                    "@odata.id": "/redfish/v1/Systems/1/Storage/DE00C000/Volumes/1",
                    "Id": "1",
                    "Name": "OS Volume",
                    "RAIDType": "RAID1",
                    "CapacityBytes": 480 * 1024 * 1024 * 1024,
                    "Status": {"Health": "OK", "State": "Enabled"},
                }
            ],
            "DrivesExpanded": [
                {
                    "@odata.id": "/redfish/v1/Systems/1/Storage/DE00C000/Drives/1",
                    "Id": "1",
                    "Name": "Drive 1",
                    "Model": "HPE SSD",
                    "SerialNumber": "DRIVE123",
                    "CapacityBytes": 480 * 1024 * 1024 * 1024,
                    "MediaType": "SSD",
                    "Protocol": "SAS",
                    "PhysicalLocation": {"PartLocation": {"LocationOrdinalValue": 1}},
                    "Status": {"Health": "OK", "State": "Enabled"},
                }
            ],
        }
    ]

    normalized = client._normalize_standard_storage(storage_subsystems)
    summary = client._build_storage_summary(storage_subsystems)

    assert normalized["controllers"] == [
        {
            "path": "/redfish/v1/Systems/1/Storage/DE00C000",
            "name": "DE00C000 Controller",
            "model": "MR408i-o Gen11",
            "firmware_version": "5.10",
            "manufacturer": "HPE",
            "serial_number": "",
            "speed_gbps": None,
            "status": "OK / Enabled",
        }
    ]
    assert summary["controllers"][0]["name"] == "DE00C000 Controller"
    assert summary["controllers"][0]["model"] == "MR408i-o Gen11"
    assert summary["controllers"][0]["firmware_version"] == "5.10"


class FakeGen10SmartStorageILOClient(ILOClient):
    def __init__(self):
        super().__init__(ILOConfig(host="ilo-gen10.example.test", username="Administrator", password="secret"))
        self.docs = {
            "/redfish/v1/": {"Name": "Root", "RedfishVersion": "1.6.0"},
            "/redfish/v1/Managers": {"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
            "/redfish/v1/Managers/1": {"@odata.id": "/redfish/v1/Managers/1", "Model": "iLO 5", "FirmwareVersion": "2.99"},
            "/redfish/v1/Systems": {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            "/redfish/v1/Systems/1": {
                "@odata.id": "/redfish/v1/Systems/1",
                "Model": "ProLiant DL360 Gen10",
                "ProductName": "DL360",
                "SerialNumber": "GEN10SERIAL",
                "Oem": {"Hpe": {"SmartStorage": {"@odata.id": "/redfish/v1/Systems/1/SmartStorage"}}},
            },
            "/redfish/v1/Systems/1/SmartStorage": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage",
                "ArrayControllers": {"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers"},
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers",
                "Members": [{"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0"}],
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0",
                "Name": "Smart Array P408i-a SR Gen10",
                "Model": "P408i-a",
                "FirmwareVersion": "4.11",
                "Status": {"Health": "OK", "State": "Enabled"},
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives",
                "Members": [{"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1"}],
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1",
                "Id": "1",
                "Name": "Logical Drive 1",
                "Raid": "RAID1",
                "CapacityMiB": 102400,
                "Status": {"Health": "OK", "State": "Enabled"},
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives",
                "Members": [{"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives/1"}],
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives/1": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives/1",
                "Id": "1",
                "Name": "Drive 1",
                "Model": "HPE SAS SSD",
                "CapacityMiB": 102400,
                "Location": "1I:1:1",
                "LocationFormat": "ControllerPort:Box:Bay",
                "DriveMediaType": "SSD",
                "InterfaceType": "SAS",
                "Status": {"Health": "OK", "State": "Enabled"},
            },
        }

    def _get(self, path, timeout=None):
        if path in self.docs:
            return self.docs[path]
        raise ILOError(f"GET {path} failed with HTTP 404")


class FakeGen10FastSmartStorageILOClient(FakeGen10SmartStorageILOClient):
    def __init__(self):
        super().__init__()
        controller = self.docs["/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0"]
        controller["LogicalDrives"] = {"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives"}
        controller["DiskDrives"] = {"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives"}


class FakeGen10NestedOemSmartStorageILOClient(FakeGen10SmartStorageILOClient):
    def __init__(self):
        super().__init__()
        self.docs["/redfish/v1/Systems/1"]["Oem"] = {
            "Hp": {
                "Links": {
                    "SmartStorage": {"@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage"}
                }
            }
        }
        self.docs["/redfish/v1/Systems/1/Oem/Hp/SmartStorage"] = {
            "@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage",
            "ArrayControllers": {"@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers"},
        }
        self.docs["/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers"] = {
            "@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers",
            "Members": [{"@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0"}],
        }
        self.docs["/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0"] = {
            "@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0",
            "Name": "Smart Array P408i-a SR Gen10",
            "Model": "P408i-a",
            "FirmwareVersion": "4.11",
            "LogicalDrives": {"@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/LogicalDrives"},
            "DiskDrives": {"@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/DiskDrives"},
            "Status": {"Health": "OK", "State": "Enabled"},
        }
        self.docs["/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/LogicalDrives"] = {
            "@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/LogicalDrives",
            "Members": [{"@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/LogicalDrives/1"}],
        }
        self.docs["/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/LogicalDrives/1"] = {
            "@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/LogicalDrives/1",
            "Id": "1",
            "Name": "Logical Drive 1",
            "Raid": "RAID1",
            "CapacityMiB": 102400,
            "Status": {"Health": "OK", "State": "Enabled"},
        }
        self.docs["/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/DiskDrives"] = {
            "@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/DiskDrives",
            "Members": [{"@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/DiskDrives/1"}],
        }
        self.docs["/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/DiskDrives/1"] = {
            "@odata.id": "/redfish/v1/Systems/1/Oem/Hp/SmartStorage/ArrayControllers/0/DiskDrives/1",
            "Id": "1",
            "Name": "Drive 1",
            "Model": "HPE SAS SSD",
            "CapacityMiB": 102400,
            "DriveMediaType": "SSD",
            "InterfaceType": "SAS",
            "Status": {"Health": "OK", "State": "Enabled"},
        }

        for legacy_path in (
            "/redfish/v1/Systems/1/SmartStorage",
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers",
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0",
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives",
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1",
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives",
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives/1",
        ):
            self.docs.pop(legacy_path, None)


class FakeGen10RealBoxSmartStorageILOClient(ILOClient):
    def __init__(self):
        super().__init__(ILOConfig(host="ilo-realbox.example.test", username="Administrator", password="secret"))
        self.docs = {
            "/redfish/v1/": {
                "Name": "Root",
                "RedfishVersion": "1.20.0",
                "Systems": {"@odata.id": "/redfish/v1/Systems/"},
                "Managers": {"@odata.id": "/redfish/v1/Managers/"},
            },
            "/redfish/v1/Managers": {"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
            "/redfish/v1/Managers/": {"Members": [{"@odata.id": "/redfish/v1/Managers/1"}]},
            "/redfish/v1/Managers/1": {"@odata.id": "/redfish/v1/Managers/1", "Model": "iLO 5", "FirmwareVersion": "2.99"},
            "/redfish/v1/Systems": {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            "/redfish/v1/Systems/": {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            "/redfish/v1/Systems/1": {
                "@odata.id": "/redfish/v1/Systems/1",
                "Model": "ProLiant DL360 Gen10",
                "ProductName": "DL360",
                "SerialNumber": "MXQ85103SX",
                "Oem": {
                    "Hpe": {
                        "Links": {
                            "SmartStorage": {"@odata.id": "/redfish/v1/Systems/1/SmartStorage"}
                        },
                        "SmartStorageConfig": [
                            {"@odata.id": "/redfish/v1/systems/1/smartstorageconfig"}
                        ],
                    }
                },
            },
            "/redfish/v1/Systems/1/SmartStorage": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage",
                "ArrayControllers": {"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers"},
                "Name": "Smart Storage",
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers",
                "Members": [{"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0"}],
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0",
                "Name": "Smart Array P408i-a SR Gen10",
                "Model": "P408i-a",
                "FirmwareVersion": "4.11",
                "LogicalDrives": {"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives"},
                "DiskDrives": {"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives"},
                "Status": {"Health": "OK", "State": "Enabled"},
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives",
                "Members": [{"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1"}],
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1",
                "Id": "1",
                "Name": "Logical Drive 1",
                "Raid": "RAID1",
                "CapacityMiB": 102400,
                "Status": {"Health": "OK", "State": "Enabled"},
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives",
                "Members": [{"@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives/1"}],
            },
            "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives/1": {
                "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/DiskDrives/1",
                "Id": "1",
                "Name": "Drive 1",
                "Model": "HPE SAS SSD",
                "CapacityMiB": 102400,
                "Location": "1I:1:1",
                "LocationFormat": "ControllerPort:Box:Bay",
                "DriveMediaType": "SSD",
                "InterfaceType": "SAS",
                "Status": {"Health": "OK", "State": "Enabled"},
            },
            "/redfish/v1/systems/1/smartstorageconfig": {
                "@odata.id": "/redfish/v1/systems/1/smartstorageconfig",
                "Name": "Smart Storage Config",
                "Settings": {"@odata.id": "/redfish/v1/systems/1/smartstorageconfig/settings"},
            },
            "/redfish/v1/systems/1/smartstorageconfig/settings": {
                "@odata.id": "/redfish/v1/systems/1/smartstorageconfig/settings",
                "Name": "Smart Storage Config Settings",
            },
        }

    # Match the real failure mode: a subclass _get implementation with no timeout kwarg.
    def _get(self, path):
        if path in self.docs:
            return self.docs[path]
        raise ILOError(f"GET {path} failed with HTTP 404")


class FakeGen10StorageApplyClient:
    def __init__(self, cfg, fail_on: str = ""):
        self.cfg = cfg
        self.fail_on = fail_on
        self.discovery = planner_gen10_apply_discovery(existing_volumes=True)
        self.calls = []
        self.system_power_state = "On"

    def get_storage_discovery(self, deep_smart_storage_scan=False):
        del deep_smart_storage_scan
        return self.discovery

    def get_systems(self):
        return ["/redfish/v1/Systems/1"]

    def get_system(self, system_path):
        assert system_path == "/redfish/v1/Systems/1"
        return {"PowerState": self.system_power_state}

    def power_reset(self, reset_type="ForceRestart", system_path=None):
        self.calls.append(("POWER_RESET", reset_type, system_path))
        if reset_type == "On":
            self.system_power_state = "On"
        if reset_type in {"ForceOff", "GracefulShutdown"}:
            self.system_power_state = "Off"
        return {
            "reset_type": reset_type,
            "system_path": system_path,
            "path": f"{system_path}/Actions/ComputerSystem.Reset" if system_path else "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        }

    def delete_storage_logical_drive(self, volume_path: str, settings_path: str = ""):
        if self.fail_on == "delete":
            raise ILOError(f"simulated delete failure for {volume_path}")
        hpe = self.discovery["summary"]["hpe_smart_storage"]
        hpe["volumes"] = [item for item in hpe["volumes"] if item.get("path") != volume_path]
        return {"deleted_path": volume_path, "settings_path": settings_path, "reboot_required": True}

    def create_gen10_logical_drive(self, settings_path: str, logical_drive_kind: str, intent: dict):
        if self.fail_on == logical_drive_kind:
            raise ILOError(f"simulated {logical_drive_kind} create failure")
        hpe = self.discovery["summary"]["hpe_smart_storage"]
        if logical_drive_kind == "os_raid1":
            hpe["volumes"].append(
                {
                    "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/10",
                    "id": "10",
                    "name": "OS RAID 1",
                    "raid_type": "RAID1",
                    "capacity_gib": 500,
                    "status": "OK / Enabled",
                }
            )
        elif logical_drive_kind == "data_raid6":
            hpe["volumes"].append(
                {
                    "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/20",
                    "id": "20",
                    "name": "Data RAID 6",
                    "raid_type": "RAID6",
                    "capacity_gib": 3600,
                    "status": "OK / Enabled",
                }
            )
        return {
            "settings_path": settings_path,
            "logical_drive_kind": logical_drive_kind,
            "intent": intent,
            "reboot_required": True,
        }

    def assign_gen10_hot_spare(self, settings_path: str, intent: dict):
        if self.fail_on == "hot_spare":
            raise ILOError("simulated hot spare failure")
        return {
            "settings_path": settings_path,
            "assigned_bay": intent.get("bay", ""),
            "reboot_required": True,
        }

    def apply_gen10_storage_layout(
        self,
        settings_path: str,
        apply_mode: str,
        existing_volume_paths: list[str],
        os_intent: dict[str, Any],
        data_intent: dict[str, Any],
        spare_intent: dict[str, Any],
    ):
        self.calls.append(
            (
                "PUT",
                settings_path,
                {
                    "apply_mode": apply_mode,
                    "existing_volume_paths": list(existing_volume_paths),
                    "os_intent": os_intent,
                    "data_intent": data_intent,
                    "spare_intent": spare_intent,
                },
            )
        )
        if self.fail_on == "data_raid6":
            raise ILOError("simulated data_raid6 create failure")
        if self.fail_on == "apply":
            raise ILOError("simulated consolidated storage apply failure")
        hpe = self.discovery["summary"]["hpe_smart_storage"]
        if apply_mode == "wipe_rebuild":
            hpe["volumes"] = []
        hpe["volumes"].append(
            {
                "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/10",
                "id": "10",
                "name": "OS RAID 1",
                "raid_type": "RAID1",
                "capacity_gib": 500,
                "status": "OK / Enabled",
            }
        )
        hpe["volumes"].append(
            {
                "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/20",
                "id": "20",
                "name": "Data RAID 6",
                "raid_type": "RAID6",
                "capacity_gib": 3600,
                "status": "OK / Enabled",
            }
        )
        return {
            "settings_path": settings_path,
            "apply_mode": apply_mode,
            "deleted_volume_paths": list(existing_volume_paths),
            "delete_count": len(existing_volume_paths),
            "create_count": 2,
            "hot_spare_location": ((spare_intent.get("drive") or {}).get("smart_storage_location") or ""),
            "reboot_required": True,
        }

    def reboot_server_and_wait(self, reset_type: str = "GracefulRestart", reboot_start_timeout: int = 120, return_timeout: int = 600, poll_interval: int = 10):
        del reboot_start_timeout, return_timeout, poll_interval
        if self.fail_on == "reboot":
            raise ILOError("simulated reboot failure")
        recovered = self.fail_on == "reboot_disconnect_success"
        return {
            "path": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            "system_path": "/redfish/v1/Systems/1",
            "reset_type": reset_type,
            "allowed_reset_types": ["GracefulRestart", "ForceRestart", "On"],
            "expected_final_power_state": "On",
            "first_observed_power_state": "On",
            "final_power_state": "On",
            "final_state_matched_expected": True,
            "recovered_after_transport_disconnect": recovered,
            "reboot_start_observed": True,
            "reboot_start_detail": "Observed BootProgress state after reset request: POST.",
            "system_returned": True,
            "return_detail": "System returned with PowerState=On.",
        }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    kits_dir = config_dir / "kits"
    media_dir = tmp_path / "media"
    firmware_upload_dir = media_dir / "firmware"
    artifacts_dir = tmp_path / "artifacts"
    generated_dir = artifacts_dir / "generated"
    jobs_dir = artifacts_dir / "jobs"
    history_dir = artifacts_dir / "history"
    ilo_export_dir = history_dir / "ilo-configs"
    config_export_dir = history_dir / "configs"
    live_ilo_config_dir = history_dir / "ilo-live-configs"
    ilo_inventory_dir = history_dir / "ilo-inventory"
    exports_dir = artifacts_dir / "exports"
    ilo_live_export_dir = exports_dir / "ilo" / "live"
    storage_raid_export_dir = exports_dir / "storage-raid"
    debug_bundles_dir = artifacts_dir / "debug-bundles"

    for path in (
        config_dir,
        kits_dir,
        media_dir,
        firmware_upload_dir,
        generated_dir,
        jobs_dir,
        history_dir,
        ilo_export_dir,
        config_export_dir,
        live_ilo_config_dir,
        ilo_inventory_dir,
        ilo_live_export_dir,
        storage_raid_export_dir,
        debug_bundles_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(main, "KITS_DIR", kits_dir)
    monkeypatch.setattr(main, "CURRENT_KIT_FILE", config_dir / "current_kit.txt")
    monkeypatch.setattr(main, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(main, "FIRMWARE_UPLOAD_DIR", firmware_upload_dir)
    monkeypatch.setattr(main, "ARTIFACTS_DIR", artifacts_dir)
    monkeypatch.setattr(main, "GENERATED_DIR", generated_dir)
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(main, "ILO_CONFIG_EXPORT_DIR", ilo_export_dir)
    monkeypatch.setattr(main, "CONFIG_EXPORT_DIR", config_export_dir)
    monkeypatch.setattr(main, "LIVE_ILO_CONFIG_DIR", live_ilo_config_dir)
    monkeypatch.setattr(main, "ILO_INVENTORY_DIR", ilo_inventory_dir)
    monkeypatch.setattr(main, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(main, "ILO_LIVE_EXPORT_DIR", ilo_live_export_dir)
    monkeypatch.setattr(main, "STORAGE_RAID_EXPORT_DIR", storage_raid_export_dir)
    monkeypatch.setattr(main, "DEBUG_BUNDLES_DIR", debug_bundles_dir)
    main.set_current_kit_name("Kit-01")

    with TestClient(main.app) as test_client:
        yield test_client


def test_health_reports_app_name_version_and_status(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "app_name": "Lab Builder",
        "version": main.app_version(),
        "status": "ok",
    }


def test_upgrade_media_upload_writes_to_persistent_media_folder(client):
    response = client.post(
        "/upload-upgrade-media",
        files={"media_file": ("ilo6_319.fwpkg", b"firmware-bytes", "application/octet-stream")},
    )

    assert response.status_code == 200
    assert "Firmware media uploaded" in response.text
    assert (main.FIRMWARE_UPLOAD_DIR / "ilo6_319.fwpkg").read_bytes() == b"firmware-bytes"


def test_navigation_pages_render(client):
    for path in ["/", "/dashboard", "/global-settings", "/ilo", "/storage", "/esxi", "/windows", "/qnap", "/execution", "/configuration", "/configs", "/kits", "/history"]:
        response = client.get(path)
        assert response.status_code == 200


def test_save_config_persists_manual_completion_and_ilo_ips(client):
    response = client.post(
        "/save-config",
        data={
            "return_page": "configuration",
            "site_name": "Test Kit",
            "shared_subnet": "10.10.8.0/24",
            "gateway_ip": "10.10.8.1",
            "switch_ip": "10.10.8.2",
            "esxi_ip": "10.10.8.10",
            "ilo_ip": "",
            "ilo_target_ip": "10.10.8.11",
            "windows_ip": "10.10.8.20",
            "qnap_ip": "10.10.8.30",
            "iosafe_ip": "10.10.8.31",
            "dns1": "1.1.1.1",
            "dns2": "8.8.8.8",
            "dns3": "",
            "dns4": "",
            "included_ilo": "on",
            "included_esxi": "on",
            "included_windows": "on",
            "section_basics_complete": "false",
            "section_network_complete": "true",
            "section_included_complete": "false",
            "section_credentials_complete": "true",
            "ilo_current_ip": "10.10.8.50",
            "ilo_subnet_mask": "255.255.255.0",
            "ilo_gateway": "10.10.8.1",
            "ilo_dns1": "9.9.9.9",
            "ilo_dns2": "8.8.4.4",
            "ilo_dns3": "",
            "ilo_dns4": "",
            "ilo_hostname": "ilo-test",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
            "esxi_hostname": "esxi01",
            "esxi_root_password": "secret",
            "windows_vm_name": "win2022-01",
            "windows_admin_password": "secret",
            "qnap_hostname": "qnap01",
            "qnap_username": "admin",
            "qnap_password": "secret",
            "iosafe_hostname": "iosafe01",
            "iosafe_username": "admin",
            "iosafe_password": "secret",
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "secret",
            "snmp_v3_username": "snmpuser",
            "snmp_v3_auth_protocol": "SHA",
            "snmp_v3_auth_password": "authsecret",
            "snmp_v3_priv_protocol": "AES",
            "snmp_v3_priv_password": "privsecret",
        },
    )

    assert response.status_code == 200

    cfg = main.load_kit_config("Test-Kit")
    assert cfg["section_completion"] == {
        "basics": False,
        "network": True,
        "included": False,
        "credentials": True,
    }
    assert cfg["ilo"]["current_ip"] == "10.10.8.50"
    assert cfg["ilo"]["target_ip"] == "10.10.8.11"
    assert cfg["ilo"]["host"] == "10.10.8.50"
    assert cfg["ilo"]["dns_servers"][:2] == ["9.9.9.9", "8.8.4.4"]


def test_apply_ip_plan_allows_netapp_cluster_mgmt_alias_to_match_netapp_host():
    cfg = main.default_config()

    plan = main.apply_ip_plan(cfg)["ip_plan"]

    assert plan["netapp"] == "10.10.8.45"
    assert plan["netapp_cluster_mgmt"] == "10.10.8.45"
    assert plan["cluster_mgmt_ip"] == "10.10.8.45"


def test_export_ilo_config_writes_dated_yaml_snapshot(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260327-123456"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-03-27 12:34:56"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "My Kit"
    cfg["ilo"]["hostname"] = "ilo-prod"
    cfg["ilo"]["current_ip"] = "10.10.8.50"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    main.save_kit_config(cfg)

    response = client.post("/export-ilo-config", data={"return_page": "configs"})

    assert response.status_code == 200
    snapshot_path = main.ILO_CONFIG_EXPORT_DIR / "ilo-prod-20260327-123456.yml"
    assert snapshot_path.exists()

    content = snapshot_path.read_text(encoding="utf-8")
    assert "kit_name: My-Kit" in content
    assert "current_ip: 10.10.8.50" in content
    assert "target_ip: 10.10.8.11" in content


def test_save_config_rebuilds_ip_plan_when_subnet_changes(client):
    response = client.post(
        "/save-config",
        data={
            "return_page": "configuration",
            "site_name": "Subnet Kit",
            "shared_subnet": "10.20.30.0/24",
            "gateway_ip": "10.10.8.1",
            "switch_ip": "10.10.8.2",
            "esxi_ip": "10.10.8.10",
            "ilo_ip": "",
            "ilo_target_ip": "10.10.8.11",
            "windows_ip": "10.10.8.20",
            "qnap_ip": "10.10.8.30",
            "iosafe_ip": "10.10.8.31",
            "dns1": "",
            "dns2": "",
            "dns3": "",
            "dns4": "",
            "section_basics_complete": "false",
            "section_network_complete": "false",
            "section_included_complete": "false",
            "section_credentials_complete": "false",
            "ilo_current_ip": "10.20.30.11",
            "ilo_subnet_mask": "",
            "ilo_gateway": "",
            "ilo_hostname": "ilo01",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
            "esxi_hostname": "esxi01",
            "esxi_root_password": "secret",
            "windows_vm_name": "win2022-01",
            "windows_admin_password": "secret",
            "qnap_hostname": "qnap01",
            "qnap_username": "admin",
            "qnap_password": "secret",
            "iosafe_hostname": "iosafe01",
            "iosafe_username": "admin",
            "iosafe_password": "secret",
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "secret",
            "snmp_v3_username": "",
            "snmp_v3_auth_protocol": "SHA",
            "snmp_v3_auth_password": "",
            "snmp_v3_priv_protocol": "AES",
            "snmp_v3_priv_password": "",
        },
    )

    assert response.status_code == 200
    cfg = main.load_kit_config("Subnet-Kit")
    assert cfg["shared_network"]["subnet"] == "10.20.30.0/24"
    assert cfg["ip_plan"]["gateway"] == "10.20.30.1"
    assert cfg["ip_plan"]["switch"] == "10.20.30.2"
    assert cfg["ip_plan"]["esxi"] == "10.20.30.10"
    assert cfg["ip_plan"]["ilo"] == "10.20.30.11"


def test_save_global_settings_updates_shared_defaults(client):
    response = client.post(
        "/save-global-settings",
        data={
            "return_page": "global_settings",
            "site_name": "Global Kit",
            "shared_subnet": "10.30.40.0/24",
            "gateway_ip": "10.30.40.1",
            "switch_ip": "10.30.40.2",
            "esxi_ip": "10.30.40.10",
            "ilo_target_ip": "10.30.40.11",
            "windows_ip": "10.30.40.20",
            "qnap_ip": "10.30.40.30",
            "iosafe_ip": "10.30.40.31",
            "dns1": "1.1.1.1",
            "dns2": "8.8.8.8",
            "dns3": "",
            "dns4": "",
            "snmp_v3_username": "snmpuser",
            "snmp_v3_auth_protocol": "SHA",
            "snmp_v3_auth_password": "authsecret",
            "snmp_v3_priv_protocol": "AES",
            "snmp_v3_priv_password": "privsecret",
            "included_ilo": "on",
            "included_esxi": "on",
            "included_windows": "on",
            "included_storage": "on",
        },
    )

    assert response.status_code == 200
    cfg = main.load_kit_config("Global-Kit")
    assert cfg["shared_network"]["subnet"] == "10.30.40.0/24"
    assert cfg["ip_plan"]["ilo"] == "10.30.40.11"
    assert cfg["included"]["storage"] is True


def test_save_global_settings_preserves_module_fields_when_form_is_shared_only(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Shared Only Kit"
    cfg["ip_plan"].update({"ilo": "10.10.8.11", "esxi": "10.10.8.10", "netapp": "10.10.8.45"})
    cfg["included"].update({"ilo": True, "storage": True, "netapp": True})
    cfg["netapp"].update({"host": "10.10.8.45", "username": "admin", "password": "secret", "storage_protocol": "iscsi"})
    cfg["cisco_switch"].update({"hostname": "sw01", "username": "admin", "password": "switchsecret"})
    main.save_kit_config(cfg)

    response = client.post(
        "/save-global-settings",
        data={
            "return_page": "global_settings",
            "site_name": "Shared Only Kit",
            "shared_subnet": "10.10.8.0/24",
            "gateway_ip": "10.10.8.1",
            "dns1": "1.1.1.1",
            "dns2": "8.8.8.8",
            "dns3": "",
            "dns4": "",
            "snmp_v3_username": "snmpuser",
            "snmp_v3_auth_protocol": "SHA",
            "snmp_v3_auth_password": "authsecret",
            "snmp_v3_priv_protocol": "AES",
            "snmp_v3_priv_password": "privsecret",
        },
    )

    assert response.status_code == 200
    saved = main.load_kit_config("Shared-Only-Kit")
    assert saved["shared_network"]["subnet"] == "10.10.8.0/24"
    assert saved["ip_plan"]["gateway"] == "10.10.8.1"
    assert saved["ip_plan"]["ilo"] == "10.10.8.11"
    assert saved["ip_plan"]["netapp"] == "10.10.8.45"
    assert saved["included"]["netapp"] is True
    assert saved["netapp"]["host"] == "10.10.8.45"
    assert saved["netapp"]["password"] == "secret"
    assert saved["cisco_switch"]["password"] == "switchsecret"


def test_save_global_settings_persists_additional_snmp_users(client):
    response = client.post(
        "/save-global-settings",
        data={
            "return_page": "global_settings",
            "site_name": "SNMP Kit",
            "shared_subnet": "10.30.40.0/24",
            "gateway_ip": "10.30.40.1",
            "switch_ip": "10.30.40.2",
            "esxi_ip": "10.30.40.10",
            "ilo_target_ip": "10.30.40.11",
            "windows_ip": "10.30.40.20",
            "qnap_ip": "10.30.40.30",
            "iosafe_ip": "10.30.40.31",
            "dns1": "1.1.1.1",
            "dns2": "",
            "dns3": "",
            "dns4": "",
            "snmp_v3_username": "primary-snmp",
            "snmp_v3_auth_protocol": "SHA",
            "snmp_v3_auth_password": "primary-auth",
            "snmp_v3_priv_protocol": "AES",
            "snmp_v3_priv_password": "primary-priv",
            "snmp_extra_username": ["backup-snmp"],
            "snmp_extra_auth_protocol": ["MD5"],
            "snmp_extra_auth_password": ["backup-auth"],
            "snmp_extra_priv_protocol": ["DES"],
            "snmp_extra_priv_password": ["backup-priv"],
        },
    )

    assert response.status_code == 200
    cfg = main.load_kit_config("SNMP-Kit")
    assert cfg["shared_snmp"]["users"] == [
        {
            "username": "primary-snmp",
            "auth_protocol": "SHA",
            "auth_password": "primary-auth",
            "priv_protocol": "AES",
            "priv_password": "primary-priv",
        },
        {
            "username": "backup-snmp",
            "auth_protocol": "MD5",
            "auth_password": "backup-auth",
            "priv_protocol": "DES",
            "priv_password": "backup-priv",
        },
    ]


def test_save_ilo_settings_updates_only_ilo_page_fields(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Page Kit"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-ilo-settings",
        data={
            "return_page": "ilo",
            "ilo_current_ip": "10.10.8.50",
            "ilo_target_ip": "10.10.8.11",
            "ilo_gateway": "",
            "ilo_hostname": "ilo-focused",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
        },
    )

    assert response.status_code == 200
    cfg = main.load_kit_config("Ilo-Page-Kit")
    assert cfg["ilo"]["current_ip"] == "10.10.8.50"
    assert cfg["ilo"]["gateway"] == "10.10.8.1"
    assert cfg["ilo"]["hostname"] == "ilo-focused"
    assert cfg["ilo"]["password"] == "secret"
    assert cfg["included"]["ilo"] is True


def test_save_config_preserves_existing_secret_fields_when_blank(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Secret Preserve Kit"
    cfg["ilo"]["password"] = "IloSecret1!"
    cfg["esxi"]["root_password"] = "EsxiSecret1!"
    cfg["windows"]["admin_password"] = "WindowsSecret1!"
    cfg["qnap"]["password"] = "QnapSecret1!"
    cfg["netapp"]["password"] = "NetAppSecret1!"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-config",
        data={
            "return_page": "configuration",
            "site_name": "Secret Preserve Kit",
            "shared_subnet": "10.10.8.0/24",
            "gateway_ip": "10.10.8.1",
            "switch_ip": "10.10.8.2",
            "esxi_ip": "10.10.8.10",
            "ilo_ip": "10.10.8.11",
            "ilo_target_ip": "10.10.8.11",
            "windows_ip": "10.10.8.20",
            "qnap_ip": "10.10.8.30",
            "iosafe_ip": "10.10.8.31",
            "netapp_ip": "10.10.8.40",
            "dns1": "",
            "dns2": "",
            "dns3": "",
            "dns4": "",
            "snmp_v3_username": "",
            "snmp_v3_auth_protocol": "SHA",
            "snmp_v3_auth_password": "",
            "snmp_v3_priv_protocol": "AES",
            "snmp_v3_priv_password": "",
            "section_basics_complete": "true",
            "section_network_complete": "true",
            "section_included_complete": "true",
            "section_credentials_complete": "true",
            "ilo_current_ip": "10.10.8.11",
            "ilo_subnet_mask": "255.255.255.0",
            "ilo_gateway": "10.10.8.1",
            "ilo_dns1": "",
            "ilo_dns2": "",
            "ilo_dns3": "",
            "ilo_dns4": "",
            "ilo_hostname": "ilo-secret-preserve",
            "ilo_username": "Administrator",
            "ilo_password": "",
            "esxi_hostname": "esxi01",
            "esxi_root_password": "",
            "windows_vm_name": "win2022-01",
            "windows_admin_password": "",
            "qnap_hostname": "qnap01",
            "qnap_username": "admin",
            "qnap_password": "",
            "iosafe_hostname": "iosafe01",
            "iosafe_username": "admin",
            "iosafe_password": "",
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "",
            "netapp_host": "",
            "netapp_username": "admin",
            "netapp_password": "",
            "netapp_storage_protocol": "nfs",
            "netapp_iscsi_commands": "",
            "netapp_nfs_commands": "",
        },
    )

    assert response.status_code == 200
    saved = main.load_kit_config("Secret-Preserve-Kit")
    assert saved["ilo"]["password"] == "IloSecret1!"
    assert saved["esxi"]["root_password"] == "EsxiSecret1!"
    assert saved["windows"]["admin_password"] == "WindowsSecret1!"
    assert saved["qnap"]["password"] == "QnapSecret1!"
    assert saved["netapp"]["password"] == "NetAppSecret1!"


def test_save_ilo_settings_persists_standard_policy_fields(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Policy Kit"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-ilo-settings",
        data={
            "return_page": "ilo",
            "ilo_current_ip": "10.10.8.50",
            "ilo_target_ip": "10.10.8.11",
            "ilo_gateway": "10.10.8.1",
            "ilo_hostname": "ilo-policy",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
            "ilo_discover_start_octet": "24",
            "ilo_discover_end_octet": "27",
            "ilo_policy_apply_standard_policy": "on",
            "ilo_policy_enable_standard_accounts": "on",
            "ilo_policy_enable_license_check": "on",
            "ilo_policy_enable_snmp_policy": "on",
            "ilo_policy_enable_alert_destinations": "on",
            "ilo_policy_enable_ipv6_disable": "on",
            "ilo_policy_enable_time_policy": "on",
            "ilo_policy_enable_auto_reset": "on",
            "ilo_policy_kit_admin_password": "KitAdminPass1!",
            "ilo_policy_kit_operator_password": "KitOperatorPass1!",
            "ilo_policy_shared_admin_username": "765CS",
            "ilo_policy_shared_admin_password": "SharedAdminPass1!",
            "ilo_policy_snmp_read_community": "ReadCommunity1!",
            "ilo_policy_snmpv3_username": "765CS",
            "ilo_policy_snmpv3_auth_protocol": "SHA",
            "ilo_policy_snmpv3_auth_password": "SnmpAuthPass1!",
            "ilo_policy_snmpv3_priv_protocol": "AES",
            "ilo_policy_snmpv3_priv_password": "SnmpPrivPass1!",
            "ilo_policy_alert_destinations": "10.245.190.67, 10.245.190.68",
        },
    )

    assert response.status_code == 200
    saved = main.load_kit_config("Ilo-Policy-Kit")
    policy = saved["ilo"]["policy"]
    assert policy["discover_start_octet"] == 24
    assert policy["discover_end_octet"] == 27
    assert policy["kit_admin_password"] == "KitAdminPass1!"
    assert policy["kit_operator_password"] == "KitOperatorPass1!"
    assert policy["shared_admin_password"] == "SharedAdminPass1!"
    assert policy["snmp_read_community"] == "ReadCommunity1!"
    assert policy["snmpv3_auth_password"] == "SnmpAuthPass1!"
    assert policy["snmpv3_priv_password"] == "SnmpPrivPass1!"
    assert policy["alert_destinations"] == ["10.245.190.67", "10.245.190.68"]


def test_save_ilo_settings_normalizes_invalid_hostname(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Hostname Kit"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-ilo-settings",
        data={
            "return_page": "ilo",
            "ilo_current_ip": "10.10.8.50",
            "ilo_target_ip": "10.10.8.11",
            "ilo_gateway": "",
            "ilo_hostname": "GEN 11 TEST",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
        },
    )

    assert response.status_code == 200
    assert 'name="ilo_hostname" value="GEN-11-TEST"' in response.text
    cfg = main.load_kit_config("Ilo-Hostname-Kit")
    assert cfg["ilo"]["hostname"] == "GEN-11-TEST"


def test_save_ilo_settings_returns_validation_error_for_duplicate_ip(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Duplicate Kit"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    cfg["ip_plan"]["esxi"] = "10.10.8.15"
    cfg["ip_plan"]["ilo"] = "10.10.8.11"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-ilo-settings",
        data={
            "return_page": "ilo",
            "ilo_current_ip": "10.10.8.50",
            "ilo_target_ip": "10.10.8.15",
            "ilo_gateway": "",
            "ilo_hostname": "ilo-duplicate",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
        },
    )

    assert response.status_code == 200
    assert "global-warning-popup" in response.text
    assert "Warning: something needs attention" in response.text
    assert "Could not save iLO setup" in response.text
    assert "Each device IP must be unique within the kit" in response.text
    assert "10.10.8.15" in response.text
    assert "esxi, ilo" in response.text
    saved = main.load_kit_config("Ilo-Duplicate-Kit")
    assert saved["ip_plan"]["ilo"] == "10.10.8.11"


def test_save_ilo_settings_persists_additional_users(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo User Kit"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-ilo-settings",
        data={
            "return_page": "ilo",
            "ilo_current_ip": "10.10.8.50",
            "ilo_target_ip": "10.10.8.11",
            "ilo_gateway": "",
            "ilo_hostname": "ilo-users",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
            "ilo_extra_username": ["opsadmin", "auditor"],
            "ilo_extra_password": ["ops-pass", "audit-pass"],
            "ilo_extra_role": ["Administrator", "ReadOnly"],
        },
    )

    assert response.status_code == 200
    cfg = main.load_kit_config("Ilo-User-Kit")
    assert cfg["ilo"]["additional_users"] == [
        {"username": "opsadmin", "password": "ops-pass", "role": "Administrator"},
        {"username": "auditor", "password": "audit-pass", "role": "ReadOnly"},
    ]


def test_esxi_validation_checks_do_not_depend_on_storage_page_state():
    cfg = main.default_config()
    cfg["included"]["storage"] = True
    cfg["storage"]["include_in_ilo_run"] = True
    cfg["storage"]["approval"] = {}
    cfg["storage"]["state"] = "idle"
    cfg["ilo"]["current_ip"] = "10.10.8.50"
    cfg["ilo"]["host"] = "10.10.8.50"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["hostname"] = "esxi01"
    cfg["esxi"]["root_password"] = "Secret123!"
    cfg["shared_network"]["subnet"] = "10.10.8.0/24"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"

    checks = main.build_validation_checks(cfg, "esxi")

    assert all(item["label"] != "Storage readiness" for item in checks)


def test_page_precheck_summary_uses_state_instead_of_duplicate_target_card():
    cfg = main.default_config()
    cfg["ilo"]["current_ip"] = "10.10.8.50"
    cfg["ilo"]["host"] = "10.10.8.50"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "Secret123!"
    cfg["shared_network"]["subnet"] = "10.10.8.0/24"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"

    summary = main.build_page_precheck_summary("ilo", cfg, main.build_workflow_contexts(cfg, {"scope": "", "status": ""}, []))

    assert summary is not None
    assert summary["show_target"] is False
    assert summary["summary_value_label"] == "State"


def test_cisco_header_uses_cisco_context_not_global_netapp_focus(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Header Kit"
    cfg["included"]["netapp"] = True
    cfg["included"]["cisco_switch"] = True
    cfg["netapp"]["storage_protocol"] = "iscsi"
    cfg["cisco_switch"]["management_ip"] = ""
    main.save_kit_config(cfg)

    response = client.get("/cisco")

    assert response.status_code == 200
    hero = response.text.split('<section class="hero">', 1)[1].split("</section>", 1)[0]
    assert "Cisco Switch" in hero
    assert "SSH credentials" in hero
    assert "ONTAP policy blocks the build" not in hero
    assert "Protocol" not in hero
    assert "ISCSI" not in hero


def test_save_ilo_settings_rejects_invalid_primary_credentials(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Invalid Credentials Kit"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-ilo-settings",
        data={
            "return_page": "ilo",
            "ilo_current_ip": "10.10.8.50",
            "ilo_target_ip": "10.10.8.11",
            "ilo_gateway": "",
            "ilo_hostname": "ilo-invalid",
            "ilo_username": "bad user",
            "ilo_password": "secret",
        },
    )

    assert response.status_code == 200
    assert "iLO setup needs attention" in response.text
    assert "iLO username cannot contain spaces." in response.text
    assert 'name="ilo_username"' in response.text
    assert "field-error" in response.text
    assert "input-invalid" in response.text
    saved = main.load_kit_config("Ilo-Invalid-Credentials-Kit")
    assert saved["ilo"]["username"] == "Administrator"


def test_save_ilo_settings_rejects_invalid_additional_user_credentials(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Invalid Extra User Kit"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-ilo-settings",
        data={
            "return_page": "ilo",
            "ilo_current_ip": "10.10.8.50",
            "ilo_target_ip": "10.10.8.11",
            "ilo_gateway": "",
            "ilo_hostname": "ilo-users",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
            "ilo_extra_username": ["ops user"],
            "ilo_extra_password": ["extra-pass"],
            "ilo_extra_role": ["Administrator"],
        },
    )

    assert response.status_code == 200
    assert "Extra iLO user 1 username cannot contain spaces." in response.text
    saved = main.load_kit_config("Ilo-Invalid-Extra-User-Kit")
    assert saved["ilo"]["additional_users"] == []


def test_ilo_page_removes_old_controls_and_points_to_storage(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo UI Kit"
    cfg["ip_plan"]["ilo"] = "10.10.8.11"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    main.save_kit_config(cfg)

    response = client.get("/ilo")

    assert response.status_code == 200
    assert "Open global settings" not in response.text
    assert "Include iLO setup in this kit" not in response.text
    assert "Open storage setup" in response.text
    assert "Read current iLO" in response.text
    assert "Address and sign-in." in response.text
    assert "Use a single printable login name, 39 characters or less." in response.text


def test_save_esxi_windows_and_qnap_page_settings(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Workflow Kit"
    main.save_kit_config(cfg)

    client.post("/save-esxi-settings", data={"return_page": "esxi", "esxi_hostname": "esxi-lab", "esxi_root_password": "Valid1Pass!"})
    client.post(
        "/save-windows-settings",
        data={
            "return_page": "windows",
            "windows_vm_name": "win-lab",
            "windows_admin_password": "secret",
            "windows_vsphere_host": "esxi.local",
            "windows_vsphere_username": "root",
            "windows_vsphere_password": "vmware-secret",
            "windows_vsphere_datastore": "datastore1",
            "windows_vsphere_network": "VM Network",
            "windows_winrm_username": "Administrator",
            "windows_winrm_password": "winrm-secret",
            "windows_winrm_port": "5986",
            "windows_winrm_use_https": "on",
            "included_windows": "on",
        },
    )
    client.post("/save-qnap-settings", data={"return_page": "qnap", "qnap_hostname": "qnap-lab", "qnap_username": "admin", "qnap_password": "secret", "included_qnap": "on"})

    cfg = main.load_kit_config("Workflow-Kit")
    assert cfg["esxi"]["hostname"] == "esxi-lab"
    assert cfg["included"]["esxi"] is True
    assert cfg["windows"]["vm_name"] == "win-lab"
    assert cfg["windows"]["vsphere_host"] == "esxi.local"
    assert cfg["windows"]["vsphere_datastore"] == "datastore1"
    assert cfg["windows"]["winrm_username"] == "Administrator"
    assert cfg["windows"]["winrm_use_https"] is True
    assert cfg["qnap"]["hostname"] == "qnap-lab"


def test_default_esxi_version_is_7():
    cfg = main.default_config()
    assert cfg["esxi"]["version"] == "7"
    assert main.get_esxi_effective_values({})["version"] == "7"


def test_save_esxi_settings_persists_version_and_base_iso(client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Version Kit"
    main.save_kit_config(cfg)
    iso = tmp_path / "VMware-ESXi-8.iso"
    iso.write_text("iso", encoding="utf-8")

    response = client.post(
        "/save-esxi-settings",
        data={
            "return_page": "esxi",
            "esxi_version": "8",
            "esxi_base_iso_path": str(iso),
            "esxi_hostname": "esxi8-lab",
            "esxi_root_password": "Valid1Pass!",
        },
    )

    saved = main.load_kit_config("ESXi-Version-Kit")
    assert response.status_code == 200
    assert saved["esxi"]["version"] == "8"
    assert saved["esxi"]["base_iso_path"] == str(iso)
    assert "ESXi version: 8" in response.text


def test_discover_esxi_base_isos_finds_version_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    base = tmp_path / "media" / "esxi" / "base"
    (base / "esxi7").mkdir(parents=True)
    (base / "esxi8").mkdir(parents=True)
    (base / "esxi7" / "esxi7.iso").write_text("iso7", encoding="utf-8")
    (base / "esxi8" / "esxi8.iso").write_text("iso8", encoding="utf-8")

    all_isos = main.discover_esxi_base_isos()
    esxi8 = main.discover_esxi_base_isos(version="8")

    assert {item["version"] for item in all_isos} == {"7", "8"}
    assert [item["name"] for item in esxi8] == ["esxi8.iso"]


def test_build_esxi_install_review_fails_missing_selected_iso(tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Missing ESXi ISO Kit"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    cfg["esxi"]["version"] = "8"
    cfg["esxi"]["base_iso_path"] = str(tmp_path / "missing.iso")

    with pytest.raises(FileNotFoundError, match="Configured ESXi base ISO was not found"):
        main.build_esxi_install_review(cfg, run_stamp="20260418-191000")


def test_verify_esxi_virtual_media_url_reports_unreachable(monkeypatch, tmp_path):
    iso = tmp_path / "built.iso"
    iso.write_bytes(b"iso")
    monkeypatch.setenv("LAB_BUILDER_VALIDATE_ESXI_MEDIA_URL", "1")

    def fake_get(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(main.requests, "get", fake_get)

    result = main.verify_esxi_virtual_media_url("http://192.168.1.51:8000/esxi.iso", iso)

    assert result["status"] == "failed"
    assert "connection refused" in result["error"]
    assert "LAB_BUILDER_PUBLIC_BASE_URL" in result["recommended_fix"]


def test_detect_public_base_url_details_reports_env_source(monkeypatch):
    monkeypatch.setenv("LAB_BUILDER_PUBLIC_BASE_URL", "http://lab-builder.example.test:9000")

    result = main.detect_public_base_url_details("10.10.8.90")

    assert result["url"] == "http://lab-builder.example.test:9000"
    assert result["source"] == "LAB_BUILDER_PUBLIC_BASE_URL"
    assert result["host"] == "lab-builder.example.test"
    assert result["port"] == "9000"
    assert result["probe_target"] == "10.10.8.90"


def test_public_base_url_from_request_uses_lan_host():
    class FakeRequest:
        url = "http://192.168.1.26:8001/execution"

    class LocalRequest:
        url = "http://127.0.0.1:8001/execution"

    assert main.public_base_url_from_request(FakeRequest()) == "http://192.168.1.26:8001"
    assert main.public_base_url_from_request(LocalRequest()) == ""


def test_build_esxi_iso_url_prefers_runtime_request_url(monkeypatch, tmp_path):
    monkeypatch.delenv("LAB_BUILDER_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("LAB_BUILDER_PORT", "8000")
    cfg = main.default_config()
    cfg["site"]["name"] = "Runtime URL Kit"
    cfg["_runtime"] = {"public_base_url": "http://192.168.1.26:8001"}

    url = main.build_esxi_iso_url(cfg, tmp_path / "esxi-runtime.iso", "10.10.8.90")

    assert url == "http://192.168.1.26:8001/esxi-built-iso/Runtime-URL-Kit/esxi-runtime.iso"


def test_esxi_runtime_status_explains_powered_off_server(monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Runtime ESXi Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    main.save_kit_config(cfg)
    main.save_job(
        "Runtime ESXi Kit",
        {
            "status": "Completed",
            "scope": "esxi",
            "current_stage": "Finished",
            "esxi_iso_url": "http://10.10.8.20:8000/esxi-built-iso/Runtime-ESXi-Kit/esxi-old.iso",
            "esxi_management_network": {"host": "10.10.8.10", "port": 443, "attempts": 12},
        },
    )
    monkeypatch.setenv("LAB_BUILDER_LIVE_RUN_CENTER_CHECKS", "1")
    monkeypatch.setattr(main, "probe_tcp_port", lambda host, port, timeout_seconds=0.75: {"host": host, "port": port, "reachable": False, "error": "timed out"})

    class FakeRuntimeILOClient:
        def __init__(self, cfg_obj):
            self.cfg = cfg_obj

        def get_system_path(self):
            return "/redfish/v1/Systems/1"

        def get_system(self, system_path):
            return {
                "PowerState": "Off",
                "Oem": {"Hpe": {"PostState": "PowerOff"}},
                "BootProgress": {"LastState": "None"},
            }

        def get_virtual_media(self):
            return [{"@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2", "Inserted": False, "Image": ""}]

    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeRuntimeILOClient(cfg_obj))
    review = {
        "management_ip": "10.10.8.10",
        "virtual_media_url": "http://10.10.8.30:8000/esxi-built-iso/Runtime-ESXi-Kit/esxi-new.iso",
    }

    status = main.build_esxi_runtime_status(cfg, review)

    assert status["management_reachable"] is False
    assert status["ilo_power_state"] == "Off"
    assert status["stale_media_host"] is True
    assert "currently Off" in status["summary"]
    assert "Last run used media host 10.10.8.20:8000" in status["recommended_action"]


def test_run_esxi_real_blocks_when_virtual_media_url_is_not_served(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi URL Check Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    built_iso = tmp_path / "esxi-url-check.iso"
    built_iso.write_bytes(b"iso")

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://192.168.1.51:8000")
    monkeypatch.setattr(
        main,
        "verify_esxi_virtual_media_url",
        lambda iso_url, output_iso: {
            "status": "failed",
            "url": iso_url,
            "output_iso_path": str(output_iso),
            "error": "connection refused",
            "recommended_fix": "Set LAB_BUILDER_PUBLIC_BASE_URL to the reachable app URL.",
        },
    )

    main.run_esxi_real(cfg, run_stamp="20260418-191500")
    job = main.load_job("Real ESXi URL Check Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Failed"
    assert "Virtual media URL check failed" in joined_logs
    assert job["esxi_virtual_media_url_check"]["status"] == "failed"
    assert job["diagnosis"]["selected_action"] == "Block ESXi virtual media mount because the generated ISO URL was not reachable."


def test_global_settings_and_workflow_pages_show_defaults_and_dependencies(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Workspace Kit"
    cfg["shared_network"]["subnet"] = "10.55.66.0/24"
    cfg["shared_network"]["dns_servers"] = ["1.1.1.1", "8.8.8.8"]
    cfg["ip_plan"]["gateway"] = "10.55.66.1"
    cfg["ip_plan"]["esxi"] = "10.55.66.10"
    cfg["ip_plan"]["windows"] = "10.55.66.20"
    cfg["ip_plan"]["qnap"] = "10.55.66.30"
    cfg["esxi"]["hostname"] = "esxi-workspace"
    main.save_kit_config(cfg)

    global_response = client.get("/global-settings")
    assert global_response.status_code == 200
    assert "Use a single printable name, 32 characters or less." in global_response.text
    assert "Global Settings" in global_response.text
    assert "Shared DNS and alerts" in global_response.text
    assert "Advanced SNMPv3 users" in global_response.text
    assert "Save shared defaults" in global_response.text
    assert "Default addresses" not in global_response.text
    assert "Advanced kit pages" not in global_response.text
    assert 'name="esxi_ip"' not in global_response.text
    assert 'name="netapp_host"' not in global_response.text
    assert 'name="cisco_switch_hostname"' not in global_response.text
    assert "Open reports &amp; technical details" not in global_response.text

    esxi_response = client.get("/esxi")
    assert esxi_response.status_code == 200
    assert "ESXi setup" in esxi_response.text
    assert "Installer version, ISO, name, and password." in esxi_response.text
    assert "Save ESXi setup" in esxi_response.text
    assert "Advanced ESXi installer view" in esxi_response.text
    assert "What happened last" in esxi_response.text
    assert "Address" in esxi_response.text
    assert "Gateway / DNS" in esxi_response.text
    assert "Installer details" in esxi_response.text
    assert "Save ESXi setup" in esxi_response.text
    assert "Generate KS.CFG" not in esxi_response.text


def test_autofill_ip_plan_uses_entered_subnet(client):
    response = client.post(
        "/autofill-ip-plan",
        data={
            "return_page": "configuration",
            "shared_subnet": "10.44.55.0/24",
        },
    )

    assert response.status_code == 200
    cfg = main.load_kit_config("Kit-01")
    assert cfg["shared_network"]["subnet"] == "10.44.55.0/24"
    assert cfg["ip_plan"]["gateway"] == "10.44.55.1"
    assert cfg["ip_plan"]["switch"] == "10.44.55.2"
    assert cfg["ip_plan"]["esxi"] == "10.44.55.10"
    assert cfg["ip_plan"]["ilo"] == "10.44.55.11"


def test_ad_hoc_inventory_export_uses_label_and_does_not_persist_password(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260402-160000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-02 16:00:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    monkeypatch.setattr(main, "ILOClient", FakeILOClient)

    cfg = main.default_config()
    cfg["site"]["name"] = "Primary Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "kit-admin"
    cfg["ilo"]["password"] = "kit-secret"
    main.save_kit_config(cfg)

    response = client.post(
        "/export-ad-hoc-ilo-inventory",
        data={
            "return_page": "configs",
            "ad_hoc_ilo_host": "10.99.1.15",
            "ad_hoc_ilo_username": "temp-admin",
            "ad_hoc_ilo_password": "super-secret-password",
            "ad_hoc_ilo_label": "spare-node-01",
        },
    )

    assert response.status_code == 200
    export_dir = main.ILO_LIVE_EXPORT_DIR / "spare-node-01" / "20260402-160000"
    summary_path = export_dir / "summary.yml"
    raw_path = export_dir / "raw.json"
    assert summary_path.exists()
    assert raw_path.exists()
    assert "super-secret-password" not in summary_path.read_text(encoding="utf-8")
    assert "super-secret-password" not in raw_path.read_text(encoding="utf-8")

    cfg_after = main.load_kit_config("Primary-Kit")
    assert cfg_after["ilo"]["current_ip"] == "10.10.8.11"
    assert cfg_after["ilo"]["username"] == "kit-admin"
    assert cfg_after["ilo"]["password"] == "kit-secret"


def test_ad_hoc_inventory_export_can_save_values_to_current_kit(client, monkeypatch):
    monkeypatch.setattr(main, "ILOClient", FakeILOClient)

    cfg = main.default_config()
    cfg["site"]["name"] = "Save Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "old-user"
    cfg["ilo"]["password"] = "old-password"
    main.save_kit_config(cfg)

    response = client.post(
        "/export-ad-hoc-ilo-inventory",
        data={
            "return_page": "configs",
            "ad_hoc_ilo_host": "ilo-temp.lab.local",
            "ad_hoc_ilo_username": "new-user",
            "ad_hoc_ilo_password": "new-password",
            "ad_hoc_ilo_label": "",
            "save_to_current_kit": "on",
        },
    )

    assert response.status_code == 200
    cfg_after = main.load_kit_config("Save-Kit")
    assert cfg_after["ilo"]["current_ip"] == "ilo-temp.lab.local"
    assert cfg_after["ilo"]["host"] == "ilo-temp.lab.local"
    assert cfg_after["ilo"]["username"] == "new-user"
    assert cfg_after["ilo"]["password"] == "new-password"
    assert cfg_after["upgrade_inventory"]["ilo"]["current_version"] == "3.00"
    assert cfg_after["upgrade_inventory"]["ilo"]["manager_model"] == "iLO 6"


def test_latest_live_summary_and_raw_downloads_use_new_export_layout(client, monkeypatch):
    monkeypatch.setattr(main, "ILOClient", FakeILOClient)

    cfg = main.default_config()
    cfg["site"]["name"] = "Latest Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.50"
    cfg["ilo"]["host"] = "10.10.8.50"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = client.post("/export-ilo-inventory", data={"return_page": "configs"})
    assert response.status_code == 200
    assert "Current iLO inventory captured" in response.text
    assert "Target: 10.10.8.50" in response.text
    assert "Open artifacts page" in response.text

    latest = main.latest_live_inventory_export()
    assert latest is not None
    assert latest["summary"].name == "summary.yml"
    assert latest["raw"].name == "raw.json"

    view_response = client.post("/view-latest-live-summary", data={"return_page": "configs"})
    assert view_response.status_code == 200
    assert "Latest Live Summary" in view_response.text
    assert "serial_number: ABC123" in view_response.text

    summary_download = client.post("/download-latest-live-summary")
    assert summary_download.status_code == 200
    assert summary_download.headers["content-type"].startswith("application/x-yaml")
    assert summary_download.headers["x-live-inventory-summary-path"].endswith("summary.yml")
    assert summary_download.headers["x-live-inventory-raw-path"].endswith("raw.json")
    assert summary_download.headers["x-live-inventory-label"] == "ilo01"
    assert summary_download.headers["x-live-inventory-host"] == "10.10.8.50"

    raw_download = client.post("/download-latest-live-raw")
    assert raw_download.status_code == 200
    assert raw_download.headers["content-type"].startswith("application/json")
    assert raw_download.headers["x-live-inventory-summary-path"].endswith("summary.yml")
    assert raw_download.headers["x-live-inventory-raw-path"].endswith("raw.json")


def test_debug_bundle_redaction_masks_sensitive_fields():
    value = {
        "password": "abc",
        "nested": {"Authorization": "Bearer token", "ok": "value"},
        "list": [{"session_id": "123"}, {"note": "safe"}],
    }
    redacted = redact_value(value)
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["Authorization"] == "[REDACTED]"
    assert redacted["nested"]["ok"] == "value"
    assert redacted["list"][0]["session_id"] == "[REDACTED]"


def test_debug_bundle_latest_route_404_when_missing(client):
    response = client.get("/debug-bundles/latest")
    assert response.status_code == 404


def test_save_job_failed_real_generates_redacted_debug_bundle(client):
    kit_name = "Debug Bundle Kit"
    main.save_job(
        kit_name,
        {
            "status": "Failed",
            "scope": "multi__ilo__storage__esxi",
            "execution_mode": "real",
            "execution_mode_label": "Real execution",
            "current_stage": "Storage apply",
            "progress_percent": 50,
            "completed_steps": 5,
            "total_steps": 10,
            "logs": [
                "[RUNNING] storage stage",
                "Authorization: Bearer topsecret",
                "[FAILED] simulated failure",
            ],
            "diagnosis": {
                "status": "blocked",
                "desired_state": {"controller_path": "/old"},
                "discovered_state": {"controller_path": "/new"},
                "options_discovered": {"writable_volume_paths": ["/new/Volumes"]},
                "safe_corrections_attempted": ["checked live storage"],
                "rejection_reasons": ["Bay 3 drive serial changed"],
                "recommended_fix": "Run storage discovery again and re-approve storage.",
                "user_action_required": True,
            },
        },
    )
    latest = main.DEBUG_BUNDLES_DIR / "latest-failure.txt"
    assert latest.exists()
    text = latest.read_text(encoding="utf-8")
    assert "topsecret" not in text
    assert "Authorization=[REDACTED]" in text
    assert "recommended_next_steps" in text
    assert "Run storage discovery again and re-approve storage." in text
    assert "Bay 3 drive serial changed" in text
    response = client.get("/debug-bundles/latest")
    assert response.status_code == 200


def test_load_latest_live_inventory_snapshot_for_cfg_does_not_leak_other_kits(monkeypatch):
    monkeypatch.setattr(main, "ILOClient", FakeILOClient)

    other_cfg = main.default_config()
    other_cfg["site"]["name"] = "Other-Kit"
    other_cfg["ilo"]["current_ip"] = "10.10.8.50"
    other_cfg["ilo"]["host"] = "10.10.8.50"
    main.save_kit_config(other_cfg)
    inventory = FakeILOClient(None).get_current_config_snapshot()
    main.export_ilo_inventory_snapshot(other_cfg, inventory, label="other-kit", source_host="10.10.8.50")

    current_cfg = main.default_config()
    current_cfg["site"]["name"] = "Current-Kit"
    current_cfg["ilo"]["current_ip"] = "10.10.8.110"
    current_cfg["ilo"]["host"] = "10.10.8.110"

    assert main.load_latest_live_inventory_snapshot_for_cfg(current_cfg) == {}


def test_load_latest_storage_discovery_snapshot_does_not_fallback_to_other_server():
    cfg = main.default_config()
    cfg["site"]["name"] = "Current-Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.110"
    cfg["ilo"]["host"] = "10.10.8.110"
    cfg["storage"]["latest_discovery_raw_path"] = ""

    assert main.load_latest_storage_discovery_snapshot(cfg) == {}


def test_export_ilo_inventory_renders_summary_and_download_actions_on_ilo_page(client, monkeypatch):
    monkeypatch.setattr(main, "ILOClient", FakeILOClient)

    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Read Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.50"
    cfg["ilo"]["host"] = "10.10.8.50"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = client.post("/export-ilo-inventory", data={"return_page": "ilo"})

    assert response.status_code == 200
    assert "Save iLO setup" in response.text
    assert "What happened last" in response.text
    assert "Advanced iLO options" in response.text
    assert "Detected from the latest live iLO read" in response.text
    assert "SNMP and alerts" in response.text
    assert "Manager reset" in response.text
    assert "Virtual media and remote install" in response.text
    assert "Show detected details" in response.text
    assert "SNMP protocol enabled" in response.text
    assert "Virtual media 1" in response.text
    assert "Interface 1 keys" in response.text
    assert "Detected Redfish capability keys" in response.text
    assert "Latest Live Summary" in response.text
    assert "serial_number: ABC123" in response.text
    assert "Download current iLO summary" in response.text
    assert "Download raw iLO data" in response.text
    saved = main.load_kit_config("Ilo-Read-Kit")
    assert saved["upgrade_inventory"]["ilo"]["current_version"] == "3.00"
    assert saved["upgrade_inventory"]["ilo"]["manager_model"] == "iLO 6"
    assert saved["upgrade_inventory"]["ilo"]["source"] == "Latest live iLO inventory"


def test_ilo_page_shows_advanced_tab_empty_state_without_live_read(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Ilo Advanced Empty Kit"
    main.save_kit_config(cfg)

    response = client.get("/ilo")

    assert response.status_code == 200
    assert "Advanced" in response.text
    assert "Advanced iLO options" in response.text
    assert "Read current iLO to load version-specific advanced options." in response.text


def test_read_current_storage_saves_discovery_export_and_renders_summary(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260407-150000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-07 15:00:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    monkeypatch.setattr(main, "ILOClient", FakeILOClient)

    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.60"
    cfg["ilo"]["host"] = "10.10.8.60"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = client.post("/read-current-storage", data={"return_page": "storage"})

    assert response.status_code == 200
    assert "Storage setup" in response.text
    assert "Readiness at a glance" in response.text
    assert "Hardware identity" in response.text
    assert "Before and after storage" in response.text
    assert "Latest verified storage result" in response.text
    assert "Target server" in response.text
    assert "Current storage setup" in response.text
    assert "Warning: something needs attention" not in response.text
    assert "Storage discovery failed" not in response.text
    assert "ProLiant DL380 Gen11" in response.text
    assert "MR416i-o" in response.text
    assert "1.98" in response.text
    assert "OS Volume / RAID RAID1" in response.text
    assert "Spare for OS Volume / RAID RAID1" in response.text
    assert "storage-discovery-details" in response.text
    assert "Deep Smart Storage Scan" not in response.text
    assert "Build storage plan" in response.text
    assert "Open reports" in response.text
    assert "See detailed storage information" in response.text

    export_dir = main.STORAGE_RAID_EXPORT_DIR / "ABC123" / "20260407-150000"
    summary_path = export_dir / "summary.yml"
    raw_path = export_dir / "raw.json"
    assert summary_path.exists()
    assert raw_path.exists()
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "generation: Gen11" in summary_text
    assert "standard_redfish_storage: true" in summary_text
    raw_text = raw_path.read_text(encoding="utf-8")
    assert '"deep_scan_requested": true' in raw_text


def test_storage_page_restores_latest_discovery_and_shows_capability_table(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260505-111500"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-05-05 11:15:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    class FakeProbeILOClient(FakeILOClient):
        def get_storage_discovery(self, deep_smart_storage_scan=False):
            discovery = super().get_storage_discovery(deep_smart_storage_scan=deep_smart_storage_scan)
            discovery["summary"]["standard_redfish_storage"]["controllers"][0]["path"] = "/redfish/v1/Systems/1/Storage/DE009000"
            discovery["raw"]["standard_storage"] = [
                {
                    "@odata.id": "/redfish/v1/Systems/1/Storage/DE009000",
                    "Volumes": {"@odata.id": "/redfish/v1/Systems/1/Storage/DE009000/Volumes"},
                    "Actions": {
                        "#Storage.ResetToDefaults": {
                            "target": "/redfish/v1/Systems/1/Storage/DE009000/Actions/Storage.ResetToDefaults",
                            "@Redfish.ActionInfo": "/redfish/v1/Systems/1/Storage/DE009000/ResetActionInfo",
                        }
                    },
                }
            ]
            return discovery

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    monkeypatch.setattr(main, "ILOClient", FakeProbeILOClient)

    cfg = main.default_config()
    cfg["site"]["name"] = "Capability Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.60"
    cfg["ilo"]["host"] = "10.10.8.60"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = client.post("/read-current-storage", data={"return_page": "storage"})
    assert response.status_code == 200

    restored = client.get("/storage")

    assert restored.status_code == 200
    assert "Controller apply capabilities" in restored.text
    assert "A writable-looking /Volumes path exists, but that alone does not verify create support." in restored.text
    assert "ResetActionInfo" in restored.text


def test_probe_storage_capabilities_is_non_destructive(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260505-111700"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-05-05 11:17:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    class FakeProbeILOClient(FakeILOClient):
        calls: list[tuple[str, bool]] = []

        def get_storage_discovery(self, deep_smart_storage_scan=False):
            self.__class__.calls.append(("get_storage_discovery", deep_smart_storage_scan))
            discovery = super().get_storage_discovery(deep_smart_storage_scan=deep_smart_storage_scan)
            discovery["summary"]["standard_redfish_storage"]["controllers"][0]["path"] = "/redfish/v1/Systems/1/Storage/DE009000"
            discovery["raw"]["standard_storage"] = [
                {
                    "@odata.id": "/redfish/v1/Systems/1/Storage/DE009000",
                    "Volumes": {"@odata.id": "/redfish/v1/Systems/1/Storage/DE009000/Volumes"},
                    "Actions": {"#Storage.ResetToDefaults": {"target": "/redfish/v1/Systems/1/Storage/DE009000/Actions/Storage.ResetToDefaults"}},
                }
            ]
            return discovery

        def delete_storage_logical_drive(self, *args, **kwargs):
            raise AssertionError("capability probe must not delete volumes")

        def create_storage_logical_drive(self, *args, **kwargs):
            raise AssertionError("capability probe must not create volumes")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    monkeypatch.setattr(main, "ILOClient", FakeProbeILOClient)

    cfg = main.default_config()
    cfg["site"]["name"] = "Probe Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.60"
    cfg["ilo"]["host"] = "10.10.8.60"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = client.post("/probe-storage-capabilities", data={"return_page": "storage"})

    assert response.status_code == 200
    assert "Storage capability probe complete" in response.text
    assert "No delete or create requests were issued." in response.text
    assert "Controller apply capabilities" in response.text
    assert FakeProbeILOClient.calls == [("get_storage_discovery", True)]


def test_storage_page_requires_manual_current_storage_read(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Manual Read Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.60"
    cfg["ilo"]["host"] = "10.10.8.60"
    cfg["ilo"]["target_ip"] = "10.10.8.61"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = client.get("/storage")

    assert response.status_code == 200
    assert "Display current storage setup" in response.text
    assert "No storage read has been run on this page yet." in response.text
    assert 'hx-post="/read-current-storage"' in response.text
    assert 'hx-trigger="load"' not in response.text
    assert "storage-autoload-form" not in response.text


def test_read_current_storage_warns_when_smart_storage_controller_has_no_children(client, monkeypatch):
    monkeypatch.setattr(main, "ILOClient", FakeSmartStorageWarningClient)

    cfg = main.default_config()
    cfg["site"]["name"] = "Gen10 Storage Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.70"
    cfg["ilo"]["host"] = "10.10.8.70"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = client.post("/read-current-storage", data={"return_page": "storage"})

    assert response.status_code == 200
    assert "Warning: something needs attention" not in response.text
    assert "Storage discovery failed" not in response.text
    assert "Build storage plan" in response.text
    assert "Open reports" in response.text


def test_storage_target_host_prefers_planned_ilo_ip_over_current_and_artifact_host():
    cfg = main.default_config()
    cfg["ilo"]["target_ip"] = "10.10.8.89"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.91"
    cfg["storage"]["latest_host"] = "10.10.8.92"
    cfg["storage"]["approval"]["host"] = "10.10.8.93"

    resolved = main.resolve_storage_target_host(cfg)

    assert resolved["resolved"] == "10.10.8.90"
    assert resolved["source"] == "current kit iLO IP"
    assert resolved["artifact_fallback"] is False


def test_storage_target_host_can_fallback_to_latest_artifact_when_kit_host_is_missing():
    cfg = main.default_config()
    cfg["ilo"]["target_ip"] = ""
    cfg["ip_plan"]["ilo"] = ""
    cfg["ilo"]["current_ip"] = ""
    cfg["ilo"]["host"] = ""
    cfg["storage"]["latest_host"] = "10.10.8.92"

    resolved = main.resolve_storage_target_host(cfg)

    assert resolved["resolved"] == "10.10.8.92"
    assert resolved["source"] == "latest discovery artifact"
    assert resolved["artifact_fallback"] is True


def test_storage_target_host_reports_clear_error_when_no_host_is_resolved():
    cfg = main.default_config()
    cfg["ilo"]["target_ip"] = ""
    cfg["ip_plan"]["ilo"] = ""
    cfg["ilo"]["current_ip"] = ""
    cfg["ilo"]["host"] = ""
    resolved = main.resolve_storage_target_host(cfg)

    assert resolved["valid"] is False
    assert resolved["resolved"] == ""
    assert "No storage target host is resolved." in resolved["error"]


def test_save_storage_target_persists_explicit_storage_credentials(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Target Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.60"
    cfg["ilo"]["host"] = "10.10.8.60"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-storage-target",
        data={
            "return_page": "storage",
            "storage_target_host": "10.10.8.99",
            "storage_username": "StorageAdmin",
            "storage_password": "storage-secret",
        },
    )

    assert response.status_code == 200
    assert "Storage target updated" in response.text
    saved = main.load_kit_config("Storage-Target-Kit")
    assert saved["storage"]["target_host_override"] == "10.10.8.99"
    assert saved["storage"]["username"] == "StorageAdmin"
    assert saved["storage"]["password"] == "storage-secret"


def test_save_storage_target_can_clear_override_and_use_ilo_defaults(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Defaults Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.61"
    cfg["ilo"]["current_ip"] = "10.10.8.60"
    cfg["ilo"]["host"] = "10.10.8.60"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    cfg["storage"]["target_host_override"] = "10.10.8.99"
    cfg["storage"]["username"] = "StorageAdmin"
    cfg["storage"]["password"] = "storage-secret"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-storage-target",
        data={
            "return_page": "storage",
            "storage_target_mode": "defaults",
        },
    )

    assert response.status_code == 200
    assert "Using iLO defaults." in response.text
    saved = main.load_kit_config("Storage-Defaults-Kit")
    assert saved["storage"]["target_host_override"] == ""
    assert saved["storage"]["username"] == ""
    assert saved["storage"]["password"] == ""


def test_gen10_smart_storage_traversal_follows_controller_child_collections():
    discovery = FakeGen10SmartStorageILOClient().get_storage_discovery()
    hpe = discovery["summary"]["hpe_smart_storage"]
    diagnostics = discovery["raw"]["hpe_smart_storage_diagnostics"]

    assert hpe["controllers"][0]["name"] == "Smart Array P408i-a SR Gen10"
    assert diagnostics["deep_fallback_ran"] is True
    assert hpe["volumes"][0]["name"] == "Logical Drive 1"
    assert hpe["volumes"][0]["raid_type"] == "RAID1"
    assert hpe["drives"][0]["model"] == "HPE SAS SSD"
    assert any(
        item["collection"] == "LogicalDrives" and item["status"] == "populated"
        for item in diagnostics["collections"]
    )


def test_gen10_smart_storage_fast_pass_stops_when_children_are_explicitly_linked():
    discovery = FakeGen10FastSmartStorageILOClient().get_storage_discovery()
    hpe = discovery["summary"]["hpe_smart_storage"]
    diagnostics = discovery["raw"]["hpe_smart_storage_diagnostics"]

    assert hpe["volumes"][0]["name"] == "Logical Drive 1"
    assert hpe["drives"][0]["model"] == "HPE SAS SSD"
    assert diagnostics["deep_fallback_ran"] is False
    assert any(
        item["collection"] == "LogicalDrives" and item["phase"] == "fast_pass" and item["status"] == "populated"
        for item in diagnostics["collections"]
    )


def test_gen10_smart_storage_deep_scan_can_be_forced_even_after_fast_pass_success():
    discovery = FakeGen10FastSmartStorageILOClient().get_storage_discovery(deep_smart_storage_scan=True)
    diagnostics = discovery["raw"]["hpe_smart_storage_diagnostics"]

    assert diagnostics["deep_scan_requested"] is True
    assert diagnostics["deep_fallback_ran"] is True
    assert any(item["phase"] == "deep_fallback" for item in diagnostics["collections"])
    assert any(
        item["collection"] == "DiskDrives" and item["status"] == "populated"
        for item in diagnostics["collections"]
    )


def test_gen10_smart_storage_nested_oem_root_is_detected():
    discovery = FakeGen10NestedOemSmartStorageILOClient().get_storage_discovery()
    hpe = discovery["summary"]["hpe_smart_storage"]
    diagnostics = discovery["raw"]["hpe_smart_storage_diagnostics"]

    assert discovery["summary"]["capabilities"]["hpe_smart_storage"] is True
    assert hpe["controllers"][0]["name"] == "Smart Array P408i-a SR Gen10"
    assert hpe["volumes"][0]["name"] == "Logical Drive 1"
    assert hpe["drives"][0]["model"] == "HPE SAS SSD"
    assert any(item["path"] == "/redfish/v1/Systems/1/Oem/Hp/SmartStorage" and item["exists"] is True for item in diagnostics["probed_paths"])


def test_gen10_real_box_shape_is_discovered_even_when_subclass_get_has_no_timeout_kwarg():
    discovery = FakeGen10RealBoxSmartStorageILOClient().get_storage_discovery()
    hpe = discovery["summary"]["hpe_smart_storage"]
    diagnostics = discovery["raw"]["hpe_smart_storage_diagnostics"]

    assert discovery["summary"]["capabilities"]["hpe_smart_storage"] is True
    assert hpe["controllers"][0]["name"] == "Smart Array P408i-a SR Gen10"
    assert hpe["volumes"][0]["name"] == "Logical Drive 1"
    assert hpe["drives"][0]["model"] == "HPE SAS SSD"
    assert any(item["path"] == "/redfish/v1/Systems/1/SmartStorage" and item["source"] in {"system", "system_oem"} for item in diagnostics["found_paths"])
    assert any(item["path"] == "/redfish/v1/systems/1/smartstorageconfig" for item in diagnostics["found_paths"])
    assert any(item["path"] == "/redfish/v1/Systems/1/SmartStorage/ArrayControllers" for item in diagnostics["followed_links"])
    assert diagnostics["collection_counts"]["ArrayControllers"]["populated"] >= 1
    assert diagnostics["collection_counts"]["LogicalDrives"]["populated"] >= 1
    assert diagnostics["collection_counts"]["DiskDrives"]["populated"] >= 1
    assert not any("unexpected keyword argument 'timeout'" in (item.get("error") or "") for item in diagnostics["probed_paths"])


def test_storage_artifact_view_and_download_use_current_discovery_and_plan(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260409-121500"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-09 12:15:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Artifact Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.83"
    cfg["ilo"]["host"] = "10.10.8.83"
    main.save_kit_config(cfg)
    discovery = planner_discovery_with_mixed_drives()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.83")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    view_response = client.post(
        "/view-storage-artifact",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "artifact_kind": "discovery_raw",
        },
    )

    assert view_response.status_code == 200
    assert "Storage Discovery Raw JSON" in view_response.text
    assert str(export_paths["raw"]) in view_response.text
    assert "10.10.8.83" in view_response.text
    assert "source_host" in view_response.text

    plan_view_response = client.post(
        "/view-storage-artifact",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "artifact_kind": "raid_plan",
        },
    )

    assert plan_view_response.status_code == 200
    assert "RAID Plan:" in plan_view_response.text
    assert "default_recommendation: wipe and rebuild" in plan_view_response.text

    download_response = client.post(
        "/download-storage-artifact",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "artifact_kind": "discovery_summary",
        },
    )

    assert download_response.status_code == 200
    assert "summary.yml" in download_response.headers.get("content-disposition", "")


def planner_discovery_with_mixed_drives() -> dict:
    discovery = FakeILOClient(None).get_storage_discovery()
    standard = discovery["summary"]["standard_redfish_storage"]
    standard["volumes"] = [{"id": "1", "name": "Existing OS", "raid_type": "RAID1", "capacity_gib": 480}]
    standard["drives"] = [
        {"id": "1", "bay": "1", "model": "SSD-480", "size_gib": 480, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled", "path": "/drives/1"},
        {"id": "2", "bay": "2", "model": "SSD-480", "size_gib": 480, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled", "path": "/drives/2"},
        {"id": "3", "bay": "3", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "path": "/drives/3"},
        {"id": "4", "bay": "4", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "path": "/drives/4"},
        {"id": "5", "bay": "5", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "path": "/drives/5"},
        {"id": "6", "bay": "6", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "path": "/drives/6"},
        {"id": "7", "bay": "7", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "path": "/drives/7"},
        {"id": "8", "bay": "8", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "path": "/drives/8"},
        {"id": "9", "bay": "9", "model": "Oddball", "size_gib": 960, "media_type": "SSD", "protocol": "SATA", "status": "OK / Enabled", "path": "/drives/9"},
    ]
    return discovery


def planner_gen10_apply_discovery(existing_volumes: bool = True) -> dict:
    volumes = []
    if existing_volumes:
        volumes.append(
            {
                "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1",
                "id": "1",
                "name": "Existing OS",
                "raid_type": "RAID1",
                "capacity_gib": 480,
                "status": "OK / Enabled",
            }
        )

    diagnostics = {
        "found_paths": [
            {"path": "/redfish/v1/Systems/1/SmartStorage", "source": "system_oem", "key": "SmartStorage"},
            {"path": "/redfish/v1/systems/1/smartstorageconfig", "source": "system_oem", "key": "SmartStorageConfig"},
        ],
        "followed_links": [
            {"owner": "/redfish/v1/Systems/1/SmartStorage", "key": "ArrayControllers", "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers", "phase": "fast_pass", "source": "collection_link"}
        ],
        "collection_counts": {
            "ArrayControllers": {"total": 1, "populated": 1, "empty": 0, "error": 0},
            "LogicalDrives": {"total": 1, "populated": 1 if existing_volumes else 0, "empty": 0 if existing_volumes else 1, "error": 0},
            "DiskDrives": {"total": 1, "populated": 1, "empty": 0, "error": 0},
        },
        "probed_paths": [],
        "collections": [],
        "warnings": [],
        "deep_scan_requested": False,
        "deep_fallback_ran": False,
    }
    return {
        "summary": {
            "server": {
                "model": "ProLiant DL360 Gen10",
                "product_name": "DL360",
                "generation": "Gen10",
                "serial_number": "MXQ85103SX",
            },
            "ilo": {
                "model": "iLO 5",
                "version": "iLO 5",
                "firmware": "2.99",
            },
            "capabilities": {
                "standard_redfish_storage": False,
                "hpe_smart_storage": True,
                "standard_storage_path": "",
                "hpe_smart_storage_paths": ["/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0"],
                "hpe_smart_storage_diagnostics": diagnostics,
            },
            "standard_redfish_storage": {"controllers": [], "volumes": [], "drives": []},
            "hpe_smart_storage": {
                "controllers": [
                    {
                        "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0",
                        "name": "Smart Array P408i-a SR Gen10",
                        "model": "P408i-a",
                        "firmware_version": "4.11",
                        "manufacturer": "HPE",
                        "status": "OK / Enabled",
                    }
                ],
                "volumes": volumes,
                "drives": [
                    {"path": "/hpe/drives/1", "id": "1", "bay": "1", "name": "Drive 1", "model": "SSD-480", "size_gib": 480, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled", "smart_storage_location": "1I:1:1"},
                    {"path": "/hpe/drives/2", "id": "2", "bay": "2", "name": "Drive 2", "model": "SSD-480", "size_gib": 480, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled", "smart_storage_location": "1I:1:2"},
                    {"path": "/hpe/drives/3", "id": "3", "bay": "3", "name": "Drive 3", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "smart_storage_location": "1I:1:3"},
                    {"path": "/hpe/drives/4", "id": "4", "bay": "4", "name": "Drive 4", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "smart_storage_location": "1I:1:4"},
                    {"path": "/hpe/drives/5", "id": "5", "bay": "5", "name": "Drive 5", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "smart_storage_location": "1I:1:5"},
                    {"path": "/hpe/drives/6", "id": "6", "bay": "6", "name": "Drive 6", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "smart_storage_location": "1I:1:6"},
                    {"path": "/hpe/drives/7", "id": "7", "bay": "7", "name": "Drive 7", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "smart_storage_location": "1I:1:7"},
                    {"path": "/hpe/drives/8", "id": "8", "bay": "8", "name": "Drive 8", "model": "HDD-1200", "size_gib": 1200, "media_type": "HDD", "protocol": "SAS", "status": "OK / Enabled", "smart_storage_location": "1I:1:8"},
                ],
                "diagnostics": diagnostics,
            },
        },
        "raw": {
            "source_host": "10.10.8.90",
            "hpe_smart_storage_diagnostics": diagnostics,
        },
    }


def planner_standard_redfish_apply_discovery(
    existing_volumes: bool = True,
    generation: str = "Gen11",
    ilo_version: str = "iLO 6",
    include_second_controller: bool = False,
) -> dict:
    controller_path = "/redfish/v1/Systems/1/Storage/DE009000"
    second_controller_path = "/redfish/v1/Systems/1/Storage/DE009001"
    volumes = []
    if existing_volumes:
        volumes.append(
            {
                "path": f"{controller_path}/Volumes/1",
                "controller_path": controller_path,
                "id": "1",
                "name": "Existing OS",
                "raid_type": "RAID1",
                "capacity_gib": 480,
                "status": "OK / Enabled",
            }
        )

    controllers = [
        {
            "path": controller_path,
            "name": "HPE MR416i-o Gen11" if generation == "Gen11" else "HPE MR416i-a Gen10+",
            "model": "MR416i-o" if generation == "Gen11" else "MR416i-a",
            "firmware_version": "1.71" if generation == "Gen11" else "3.03",
            "manufacturer": "HPE",
            "status": "OK / Enabled",
        }
    ]
    if include_second_controller:
        controllers.append(
            {
                "path": second_controller_path,
                "name": "HPE NS204i-u Gen11 Boot Controller",
                "model": "NS204i-u",
                "firmware_version": "1.00",
                "manufacturer": "HPE",
                "status": "OK / Enabled",
            }
        )

    drives = [
        {"path": "/redfish/v1/Chassis/DE009000/Drives/0", "controller_path": controller_path, "id": "0", "bay": "1", "name": "Drive 1", "model": "SSD-480", "size_gib": 480, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled"},
        {"path": "/redfish/v1/Chassis/DE009000/Drives/1", "controller_path": controller_path, "id": "1", "bay": "2", "name": "Drive 2", "model": "SSD-480", "size_gib": 480, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled"},
        {"path": "/redfish/v1/Chassis/DE009000/Drives/2", "controller_path": controller_path, "id": "2", "bay": "3", "name": "Drive 3", "model": "SSD-960", "size_gib": 960, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled"},
        {"path": "/redfish/v1/Chassis/DE009000/Drives/3", "controller_path": controller_path, "id": "3", "bay": "4", "name": "Drive 4", "model": "SSD-960", "size_gib": 960, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled"},
        {"path": "/redfish/v1/Chassis/DE009000/Drives/4", "controller_path": controller_path, "id": "4", "bay": "5", "name": "Drive 5", "model": "SSD-960", "size_gib": 960, "media_type": "SSD", "protocol": "SAS", "status": "OK / Enabled"},
    ]
    if include_second_controller:
        drives.extend(
            [
                {"path": "/redfish/v1/Systems/1/Storage/DE009001/Drives/1", "controller_path": second_controller_path, "id": "5", "bay": "6", "name": "Boot Drive 1", "model": "NVMe-480", "size_gib": 480, "media_type": "SSD", "protocol": "NVMe", "status": "OK / Enabled"},
                {"path": "/redfish/v1/Systems/1/Storage/DE009001/Drives/2", "controller_path": second_controller_path, "id": "6", "bay": "7", "name": "Boot Drive 2", "model": "NVMe-480", "size_gib": 480, "media_type": "SSD", "protocol": "NVMe", "status": "OK / Enabled"},
            ]
        )

    raw_storage = [
        {
            "@odata.id": controller_path,
            "Id": "DE009000",
            "Name": controllers[0]["name"],
            "Status": {"Health": "OK", "State": "Enabled"},
            "Controllers": {"@odata.id": f"{controller_path}/Controllers"},
            "Volumes": {"@odata.id": f"{controller_path}/Volumes"},
            "Actions": {
                "#Storage.ResetToDefaults": {
                    "target": f"{controller_path}/Actions/Storage.ResetToDefaults",
                    "ResetType@Redfish.AllowableValues": ["ResetAll", "PreserveVolumes"],
                }
            },
            "DrivesExpanded": [],
            "VolumesExpanded": [],
        }
    ]
    if include_second_controller:
        raw_storage.append(
            {
                "@odata.id": second_controller_path,
                "Id": "DE009001",
                "Name": "HPE NS204i-u Gen11 Boot Controller",
                "Status": {"Health": "OK", "State": "Enabled"},
                "Controllers": {"@odata.id": f"{second_controller_path}/Controllers"},
                "Volumes": {"@odata.id": f"{second_controller_path}/Volumes"},
                "Actions": {"#Storage.ResetToDefaults": {"target": f"{second_controller_path}/Actions/Storage.ResetToDefaults"}},
                "DrivesExpanded": [],
                "VolumesExpanded": [],
            }
        )

    return {
        "summary": {
            "server": {
                "model": "ProLiant DL360 Gen11" if generation == "Gen11" else "ProLiant DL360 Gen10 Plus",
                "product_name": "DL360",
                "generation": generation,
                "serial_number": "3M1D3V105V" if generation == "Gen11" else "3M1D1Y11Z2",
            },
            "ilo": {
                "model": ilo_version,
                "version": ilo_version,
                "firmware": "1.71" if generation == "Gen11" else "3.03",
            },
            "capabilities": {
                "standard_redfish_storage": True,
                "standard_redfish_volume_create_verified": True,
                "hpe_smart_storage": generation == "Gen10+",
                "standard_storage_path": "/redfish/v1/Systems/1/Storage",
                "hpe_smart_storage_paths": ["/redfish/v1/Systems/1/SmartStorage"] if generation == "Gen10+" else [],
                "hpe_smart_storage_diagnostics": {"probed_paths": [], "collections": [], "warnings": [], "deep_scan_requested": False, "deep_fallback_ran": False},
            },
            "standard_redfish_storage": {
                "controllers": controllers,
                "volumes": volumes,
                "drives": drives,
            },
            "hpe_smart_storage": {"controllers": [], "volumes": [], "drives": [], "diagnostics": {"probed_paths": [], "collections": [], "warnings": [], "deep_scan_requested": False, "deep_fallback_ran": False}},
        },
        "raw": {
            "source_host": "10.10.8.90",
            "standard_storage": raw_storage,
            "system": {"Oem": {"Hpe": {"DeviceDiscoveryComplete": {"DeviceDiscovery": "vMainDeviceDiscoveryComplete"}}}},
            "hpe_smart_storage_diagnostics": {"probed_paths": [], "collections": [], "warnings": [], "deep_scan_requested": False, "deep_fallback_ran": False},
        },
    }


def remap_standard_redfish_discovery_path(discovery: dict, old_path: str, new_path: str) -> dict:
    remapped = copy.deepcopy(discovery)

    def replace_value(value):
        if isinstance(value, str):
            return value.replace(old_path, new_path).replace("DE009000", "DE00A000")
        if isinstance(value, dict):
            return {key: replace_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_value(item) for item in value]
        return value

    return replace_value(remapped)


def planner_gen10_plus_hpe_inventory_without_settings_path() -> dict:
    diagnostics = {
        "found_paths": [
            {"path": "/redfish/v1/Systems/1/SmartStorage", "source": "system", "key": "SmartStorage"},
            {"path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers", "source": "guessed", "key": "synthetic"},
            {"path": "/redfish/v1/Systems/1/SmartStorageConfig", "source": "guessed", "key": "synthetic"},
            {"path": "/redfish/v1/Systems/1/SmartStorageConfig/Settings", "source": "guessed", "key": "synthetic"},
        ],
        "followed_links": [],
        "collection_counts": {},
        "collections": [],
        "warnings": [],
        "deep_scan_requested": False,
        "deep_fallback_ran": False,
        "probed_paths": [
            {
                "phase": "fast_pass",
                "path": "/redfish/v1/Systems/1/SmartStorage",
                "status": "ok",
                "exists": True,
                "error": "",
                "name": "HpeSmartStorage",
                "members": 0,
            },
            {
                "phase": "fast_pass",
                "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers",
                "status": "ok",
                "exists": True,
                "error": "",
                "name": "HpeSmartStorageArrayControllers",
                "members": 0,
            },
            {
                "phase": "fast_pass",
                "path": "/redfish/v1/Systems/1/SmartStorageConfig",
                "status": "error",
                "exists": False,
                "error": "404 ResourceMissingAtURI",
                "name": "",
                "members": 0,
            },
            {
                "phase": "fast_pass",
                "path": "/redfish/v1/Systems/1/SmartStorageConfig/Settings",
                "status": "error",
                "exists": False,
                "error": "404 ResourceMissingAtURI",
                "name": "",
                "members": 0,
            },
        ],
    }
    return {
        "summary": {
            "server": {
                "model": "ProLiant DL360 Gen10 Plus",
                "product_name": "",
                "generation": "Gen10+",
                "serial_number": "3M1D1Y11Z2",
            },
            "ilo": {
                "model": "iLO 5",
                "version": "iLO 5",
                "firmware": "iLO 5 v3.03",
            },
            "capabilities": {
                "standard_redfish_storage": False,
                "hpe_smart_storage": True,
                "standard_storage_path": "/redfish/v1/Systems/1/Storage",
                "hpe_smart_storage_paths": [
                    "/redfish/v1/Systems/1/SmartStorage",
                    "/redfish/v1/Systems/1/SmartStorage/ArrayControllers",
                ],
                "hpe_smart_storage_diagnostics": diagnostics,
            },
            "standard_redfish_storage": {"controllers": [], "volumes": [], "drives": []},
            "hpe_smart_storage": {
                "controllers": [
                    {
                        "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0",
                        "name": "HPE MR416i-a Gen10+",
                        "model": "MR416i-a Gen10+",
                        "firmware_version": "1.00",
                        "manufacturer": "HPE",
                        "status": "OK / Enabled",
                    }
                ],
                "volumes": [],
                "drives": [],
                "diagnostics": diagnostics,
            },
        },
        "raw": {
            "source_host": "10.10.8.110",
            "hpe_smart_storage_diagnostics": diagnostics,
        },
    }


def planner_discovery_without_data_spare() -> dict:
    discovery = planner_discovery_with_mixed_drives()
    standard = discovery["summary"]["standard_redfish_storage"]
    standard["drives"] = [drive for drive in standard["drives"] if drive["bay"] not in {"7", "8"}]
    return discovery


def test_plan_raid_layout_uses_displayed_discovery_artifact_and_saves_plan(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260407-170000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-07 17:00:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Plan Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.80"
    cfg["ip_plan"]["ilo"] = "10.10.8.80"
    cfg["ilo"]["current_ip"] = "10.10.8.80"
    cfg["ilo"]["host"] = "10.10.8.80"
    main.save_kit_config(cfg)
    discovery = planner_discovery_with_mixed_drives()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.80")

    response = client.post(
        "/plan-raid-layout",
        data={"return_page": "storage", "discovery_raw_path": str(export_paths["raw"])},
    )

    assert response.status_code == 200
    assert "Storage plan ready" in response.text
    assert "Build storage plan" in response.text
    assert "Approve this plan" in response.text
    assert "Run for real" in response.text
    assert "SSD-480" in response.text
    assert "HDD-1200" in response.text
    assert "Oddball" in response.text
    assert "Hot spare" in response.text
    assert "No dedicated hot spare is selected for this plan." in response.text
    assert "Apply it during the real run" in response.text
    assert "Open reports" in response.text
    assert "Open build files" in response.text
    plan_path = export_paths["directory"] / "raid-plan.yml"
    assert plan_path.exists()
    plan_text = plan_path.read_text(encoding="utf-8")
    assert "source_discovery:" in plan_text
    assert "default_recommendation: wipe and rebuild" in plan_text
    assert "hot_spare:" in plan_text
    assert "typed_confirmation: WIPE STORAGE" in plan_text
    assert "required: false" in plan_text
    assert "Not in the selected RAID 6 compatible media/protocol/capacity" in plan_text


def test_plan_raid_layout_accepts_custom_drive_selection(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260407-170100"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-07 17:01:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Custom Plan Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.80"
    cfg["ip_plan"]["ilo"] = "10.10.8.80"
    cfg["ilo"]["current_ip"] = "10.10.8.80"
    cfg["ilo"]["host"] = "10.10.8.80"
    main.save_kit_config(cfg)
    discovery = planner_discovery_with_mixed_drives()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.80")

    response = client.post(
        "/plan-raid-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_bays": ["1", "2"],
            "data_bays": ["4", "5", "6", "7"],
            "hot_spare_bay": "8",
        },
    )

    assert response.status_code == 200
    assert "This plan was customized from the default drive selection." in response.text
    plan_payload = yaml.safe_load((export_paths["directory"] / "raid-plan.yml").read_text(encoding="utf-8"))
    plan = plan_payload["plan"]
    assert plan["customization"]["active"] is True
    assert plan["planned_layout"]["os_raid1"]["bays"] == "1, 2"
    assert plan["planned_layout"]["data_raid6"]["bays"] == "4, 5, 6, 7"
    assert plan["planned_layout"]["hot_spare"]["bay"] == "8"


def test_plan_raid_layout_accepts_custom_raid_levels(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260407-170200"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-07 17:02:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Custom Raid Level Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.80"
    cfg["ip_plan"]["ilo"] = "10.10.8.80"
    cfg["ilo"]["current_ip"] = "10.10.8.80"
    cfg["ilo"]["host"] = "10.10.8.80"
    main.save_kit_config(cfg)
    discovery = planner_discovery_with_mixed_drives()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.80")

    response = client.post(
        "/plan-raid-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "os_raid_level": "RAID10",
            "data_raid_level": "RAID5",
            "os_bays": ["1", "2", "3", "4"],
            "data_bays": ["5", "6", "7"],
            "hot_spare_bay": "8",
        },
    )

    assert response.status_code == 200
    assert "RAID 10" in response.text
    assert "RAID 5" in response.text
    plan_payload = yaml.safe_load((export_paths["directory"] / "raid-plan.yml").read_text(encoding="utf-8"))
    plan = plan_payload["plan"]
    assert plan["customization"]["selected_os_raid_level"] == "RAID10"
    assert plan["customization"]["selected_data_raid_level"] == "RAID5"
    assert plan["planned_layout"]["os_raid1"]["raid"] == "RAID 10"
    assert plan["planned_layout"]["data_raid6"]["raid"] == "RAID 5"
    assert plan["os_raid1"]["raid"] == "RAID10"
    assert plan["data_raid6"]["raid"] == "RAID5"


def test_storage_page_only_shows_controller_selector_when_multiple_exist(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260428-110000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-28 11:00:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Multi Controller Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.80"
    cfg["ip_plan"]["ilo"] = "10.10.8.80"
    cfg["ilo"]["current_ip"] = "10.10.8.80"
    cfg["ilo"]["host"] = "10.10.8.80"
    main.save_kit_config(cfg)

    discovery = planner_standard_redfish_apply_discovery(existing_volumes=False, generation="Gen11", ilo_version="iLO 6", include_second_controller=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.80")

    response = client.post(
        "/plan-raid-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "controller_path": "/redfish/v1/Systems/1/Storage/DE009001",
            "os_raid_level": "RAID1",
            "data_raid_level": "",
            "os_bays": ["6", "7"],
            "data_bays": [],
            "hot_spare_bay": "",
        },
    )

    assert response.status_code == 200
    assert "OS array controller" in response.text
    assert "Data array controller" in response.text
    plan_payload = yaml.safe_load((export_paths["directory"] / "raid-plan.yml").read_text(encoding="utf-8"))
    plan = plan_payload["plan"]
    assert plan["source_discovery"]["controller"]["path"] == "/redfish/v1/Systems/1/Storage/DE009001"
    assert plan["customization"]["selected_controller_path"] == "/redfish/v1/Systems/1/Storage/DE009001"


def test_build_storage_apply_intent_uses_selected_raid_levels():
    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }

    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "os_raid_level": "RAID10",
            "data_raid_level": "RAID5",
            "os_bays": ["1", "2", "3", "4"],
            "data_bays": ["5", "6", "7"],
            "hot_spare_bay": "8",
        },
    )
    intent = main.build_storage_apply_intent(plan, "wipe_rebuild")

    assert intent["os_raid1"]["raid"] == "RAID10"
    assert intent["data_raid6"]["raid"] == "RAID5"


def test_approve_storage_plan_saves_exact_artifact_paths_for_later_ilo_run(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Approval Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.90"
    cfg["ip_plan"]["ilo"] = "10.10.8.90"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    response = client.post(
        "/approve-storage-plan",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "include_in_ilo_run": "on",
        },
    )

    assert response.status_code == 200
    assert "The current storage plan is approved for the real run." in response.text
    assert "Apply it during the real run: Yes" in response.text
    assert "Run for real" in response.text
    assert "Remove approval" in response.text
    cfg_after = main.load_kit_config("Approval-Kit")
    storage_cfg = cfg_after["storage"]
    assert storage_cfg["state"] == "approved"
    assert storage_cfg["include_in_ilo_run"] is True
    assert storage_cfg["approval"]["discovery_raw_path"] == str(export_paths["raw"])
    assert storage_cfg["approval"]["plan_path"] == str(plan_paths["plan"])
    assert cfg_after["included"]["storage"] is True


def test_storage_approval_becomes_stale_when_latest_discovery_changes():
    cfg = main.default_config()
    cfg["site"]["name"] = "Stale Kit"
    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = {
        "directory": main.Path("/tmp/storage-approval"),
        "summary": main.Path("/tmp/storage-approval/summary.yml"),
        "raw": main.Path("/tmp/storage-approval/raw.json"),
    }
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = {"directory": export_paths["directory"], "plan": main.Path("/tmp/storage-approval/raid-plan.yml")}

    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    changed = planner_gen10_apply_discovery(existing_volumes=False)
    changed["summary"]["hpe_smart_storage"]["drives"][0]["status"] = "Predictive Failure"
    main.update_storage_latest_state(cfg, discovery=changed, discovery_paths=export_paths)

    assert cfg["storage"]["state"] == "stale"
    assert cfg["storage"]["approval"]["state"] == "stale"
    assert "approved discovery basis" in cfg["storage"]["status_reason"]


def test_storage_approval_becomes_stale_when_storage_target_host_changes():
    cfg = main.default_config()
    cfg["site"]["name"] = "Stale Host Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = {
        "directory": main.Path("/tmp/storage-approval-host"),
        "summary": main.Path("/tmp/storage-approval-host/summary.yml"),
        "raw": main.Path("/tmp/storage-approval-host/raw.json"),
    }
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = {"directory": export_paths["directory"], "plan": main.Path("/tmp/storage-approval-host/raid-plan.yml")}

    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    cfg["storage"]["target_host_override"] = "10.10.8.91"
    main.refresh_storage_approval_from_saved_state(cfg)

    assert cfg["storage"]["state"] == "stale"
    assert "differs from the approved storage host" in cfg["storage"]["status_reason"]


def test_prepare_execute_shows_combined_storage_review_using_exact_approved_artifact(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Exec Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    cfg = main.load_kit_config("Exec-Kit")
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    cfg["included"]["storage"] = True
    main.save_kit_config(cfg)

    response = client.post(
        "/prepare-execute",
        data={"scope": "ilo", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "Ready to run" in response.text
    assert "Apply mode" in response.text
    assert "Stages that will run" in response.text
    assert "Approved discovery path" in response.text
    assert "Technical details" in response.text
    assert "Settings that will be used" in response.text
    assert "Storage run values" in response.text
    assert "Approved plan path:" in response.text
    assert "Open summary" in response.text
    assert "Open Reports" in response.text


def test_execution_page_warns_when_storage_is_not_approved(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Exec Warn Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["included"]["storage"] = False
    main.save_kit_config(cfg)

    response = client.get("/execution")

    assert response.status_code == 200
    assert "Run Center" in response.text
    assert "Choose run" in response.text
    assert "Current focus" in response.text
    assert "Still waiting on" in response.text
    assert "Review one part" not in response.text


def test_prepare_execute_shows_blocked_stage_guidance(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Exec Blocked Guidance Kit"
    cfg["included"]["esxi"] = True
    cfg["ip_plan"]["esxi"] = "10.10.8.10"
    main.save_kit_config(cfg)

    response = client.post(
        "/prepare-execute",
        data={"scope": "included", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "Blocked" in response.text
    assert "Needs attention:" in response.text
    assert "Fix on iLO" in response.text
    assert "Open setup page" in response.text


def test_prepare_execute_blocks_windows_when_saved_credentials_are_missing(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Exec Windows Block Kit"
    cfg["included"]["windows"] = True
    cfg["windows"]["admin_password"] = ""
    main.save_kit_config(cfg)

    response = client.post(
        "/prepare-execute",
        data={"scope": "windows", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "Saved credentials" in response.text
    assert "Administrator password is missing." in response.text
    assert "Open the Windows page and save the administrator password." in response.text
    assert "/windows" in response.text


def test_ilo_page_warns_clearly_when_storage_is_not_approved(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ILO Warn Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    main.save_kit_config(cfg)

    response = client.get("/ilo")

    assert response.status_code == 200
    assert "Open storage setup" in response.text


def test_execute_is_blocked_when_included_storage_plan_is_stale(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Exec Block Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = {
        "directory": main.Path("/tmp/storage-stale"),
        "summary": main.Path("/tmp/storage-stale/summary.yml"),
        "raw": main.Path("/tmp/storage-stale/raw.json"),
    }
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = {"directory": export_paths["directory"], "plan": main.Path("/tmp/storage-stale/raid-plan.yml")}
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    cfg["included"]["storage"] = True
    changed = planner_gen10_apply_discovery(existing_volumes=False)
    main.update_storage_latest_state(cfg, discovery=changed, discovery_paths=export_paths)
    main.save_kit_config(cfg)

    response = client.post(
        "/execute",
        data={
            "scope": "ilo",
            "confirm_checkbox": "on",
            "confirm_phrase": "EXECUTE",
            "return_page": "execution",
        },
    )

    assert response.status_code == 200
    assert "Execution blocked:" in response.text
    assert "stale" in response.text.lower()


def test_prepare_execute_marks_included_scope_as_preview_only(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Preview Mode Kit"
    cfg["included"]["windows"] = True
    main.save_kit_config(cfg)

    response = client.post(
        "/prepare-execute",
        data={"scope": "included", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "Preview only" in response.text
    assert "Mode" in response.text
    assert "Start preview run" in response.text
    assert "Run for real" in response.text
    assert "/execute-preview" in response.text
    assert "/execute" in response.text
    assert "Ready to run" in response.text


def test_execute_preview_scope_reports_preview_started(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Preview Execute Kit"
    cfg["included"]["windows"] = True
    cfg["windows"]["admin_password"] = "secret"
    main.save_kit_config(cfg)

    response = client.post(
        "/execute-preview",
        data={
            "scope": "windows",
            "return_page": "execution",
        },
    )

    assert response.status_code == 200
    assert "Preview started for scope: windows. No real changes will be made." in response.text


def test_execute_real_scope_starts_existing_ilo_path(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real Execute Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    main.save_kit_config(cfg)

    started: dict[str, object] = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    response = client.post(
        "/execute",
        data={
            "scope": "ilo",
            "confirm_checkbox": "on",
            "confirm_phrase": "EXECUTE",
            "return_page": "execution",
        },
    )

    assert response.status_code == 200
    assert "Real iLO automation started in the background. Check Job Monitor for live progress and logs." in response.text
    assert started["target"] is main.execute_real_job_in_background
    assert started["args"][1] == "ilo"
    assert started["args"][0]["site"]["name"] == "Real-Execute-Kit"
    assert started["daemon"] is True
    assert started["started"] is True


def test_prepare_execute_windows_offers_safe_real_run(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Safe Execute Kit"
    cfg["included"]["windows"] = True
    cfg["windows"]["admin_password"] = "secret"
    cfg["windows"]["source_image_path"] = "/tmp/windows-template.ova"
    cfg["windows"]["source_image_kind"] = "ova"
    cfg["windows"]["install_plan"] = {"ready": True, "warnings": []}
    main.save_kit_config(cfg)

    response = client.post(
        "/prepare-execute",
        data={"scope": "windows", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "Dry-run apply" in response.text


def test_execute_real_scope_starts_windows_safe_path(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Safe Real Run Kit"
    cfg["included"]["windows"] = True
    cfg["windows"]["admin_password"] = "secret"
    cfg["windows"]["source_image_path"] = "/tmp/windows-template.ova"
    cfg["windows"]["source_image_kind"] = "ova"
    cfg["windows"]["install_plan"] = {"ready": True, "warnings": []}
    main.save_kit_config(cfg)

    started: dict[str, object] = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    response = client.post(
        "/execute",
        data={
            "scope": "windows",
            "confirm_checkbox": "on",
            "confirm_phrase": "EXECUTE",
            "return_page": "execution",
        },
    )

    assert response.status_code == 200
    assert "Windows safe execution started in the background." in response.text
    assert started["target"] is main.execute_real_job_in_background
    assert started["args"][1] == "windows"
    assert started["daemon"] is True
    assert started["started"] is True


def test_execute_real_scope_starts_esxi_path(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Execute Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    cfg["included"]["esxi"] = True
    main.save_kit_config(cfg)

    started: dict[str, object] = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    response = client.post(
        "/execute",
        data={
            "scope": "esxi",
            "confirm_checkbox": "on",
            "confirm_phrase": "EXECUTE",
            "return_page": "execution",
        },
    )

    assert response.status_code == 200
    assert "Real ESXi automation started in the background. Check Job Monitor for live progress and logs." in response.text
    assert started["target"] is main.execute_real_job_in_background
    assert started["args"][1] == "esxi"
    assert started["daemon"] is True
    assert started["started"] is True


def test_execute_real_scope_starts_storage_path(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Real Run Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    main.save_kit_config(cfg)
    main.set_current_kit_name(cfg["site"]["name"])

    started: dict[str, object] = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    response = client.post(
        "/execute",
        data={
            "scope": "storage",
            "confirm_checkbox": "on",
            "confirm_phrase": "EXECUTE",
            "return_page": "execution",
        },
    )

    assert response.status_code == 200
    assert "Real storage automation started in the background. Check Job Monitor for live progress and logs." in response.text
    assert started["target"] is main.execute_real_job_in_background
    assert started["args"][1] == "storage"
    assert started["daemon"] is True
    assert started["started"] is True


def test_execute_real_storage_starts_manual_reboot_watch_when_staged(client, monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Watch Kit"
    main.save_kit_config(cfg)

    apply_dir = tmp_path / "storage-apply"
    apply_dir.mkdir(parents=True, exist_ok=True)
    apply_paths = {
        "directory": apply_dir,
        "apply_results": apply_dir / "apply-results.json",
        "apply_log": apply_dir / "apply-log.yml",
        "pre_change_summary": apply_dir / "pre-change-summary.yml",
        "pre_change_raw": apply_dir / "pre-change-raw.json",
        "post_change_summary": apply_dir / "post-change-summary.yml",
        "post_change_raw": apply_dir / "post-change-raw.json",
        "post_reboot_summary": apply_dir / "post-reboot-summary.yml",
        "post_reboot_raw": apply_dir / "post-reboot-raw.json",
        "reboot_results": apply_dir / "reboot-results.json",
    }

    monkeypatch.setattr(
        main,
        "validate_storage_ready_for_ilo_run",
        lambda cfg_obj: {
            "approved_host": "10.10.8.90",
            "discovery_raw_path": "/tmp/discovery.json",
            "plan_path": "/tmp/plan.yml",
        },
    )
    monkeypatch.setattr(
        main,
        "restore_storage_page_state",
        lambda **kwargs: ({}, {}, {"layout": {}}, {"plan": main.Path("/tmp/plan.yml")}),
    )
    monkeypatch.setattr(main, "storage_apply_mode_for_plan", lambda plan: "wipe_rebuild")
    monkeypatch.setattr(main, "initialize_storage_apply_artifacts", lambda cfg_obj, plan, plan_paths: apply_paths)

    def fake_run_storage_apply(cfg_obj, discovery_raw_path, raid_plan_path, apply_mode, actual_apply_paths):
        del cfg_obj, discovery_raw_path, raid_plan_path, apply_mode
        main.save_storage_apply_state(
            {
                "workflow_state": "staged_reboot_required",
                "reboot_requested": False,
                "reboot_required": True,
                "status": "Staged",
            },
            actual_apply_paths,
        )

    started = {}

    monkeypatch.setattr(main, "run_storage_apply", fake_run_storage_apply)
    monkeypatch.setattr(
        main,
        "start_storage_manual_reboot_watch_background",
        lambda cfg_obj, discovery_raw_path, raid_plan_path, actual_apply_paths: started.update(
            {
                "kit": cfg_obj["site"]["name"],
                "discovery_raw_path": discovery_raw_path,
                "raid_plan_path": raid_plan_path,
                "directory": str(actual_apply_paths["directory"]),
            }
        ),
    )

    main.execute_real_job_in_background(cfg, "storage")

    assert started["kit"] == "Storage-Watch-Kit"
    assert started["discovery_raw_path"] == "/tmp/discovery.json"
    assert started["raid_plan_path"] == "/tmp/plan.yml"
    assert started["directory"] == str(apply_dir)
    final_job = main.load_job(cfg["site"]["name"])
    assert final_job.get("current_stage") == "Queued for manual reboot"
    assert final_job.get("stage_statuses", {}).get("storage") == "running"


def test_prepare_execute_enables_real_launch_for_esxi_scope(client, monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Launch Review Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    cfg["included"]["esxi"] = True
    main.save_kit_config(cfg)

    class FakeNow:
        def strftime(self, fmt):
            assert fmt == "%Y%m%d-%H%M%S"
            return "20260416-121500"

    class FakeDateTime:
        @staticmethod
        def now():
            return FakeNow()

        @staticmethod
        def fromtimestamp(ts):
            from datetime import datetime as real_datetime
            return real_datetime.fromtimestamp(ts)

    monkeypatch.setattr(main, "datetime", FakeDateTime)
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(
        main,
        "detect_public_base_url_details",
        lambda target_host="": {
            "url": "http://lab-builder.local:8000",
            "source": "LAB_BUILDER_PUBLIC_BASE_URL",
            "host": "lab-builder.local",
            "port": "8000",
            "probe_target": target_host,
        },
    )
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))

    response = client.post(
        "/prepare-execute",
        data={"scope": "esxi", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "Builds the custom ESXi installer ISO, mounts it through virtual media, sets one-time boot, and starts the real ESXi boot sequence." in response.text
    assert 'name="confirm_checkbox"' in response.text
    assert 'name="confirm_checkbox" disabled' not in response.text
    assert 'name="confirm_phrase"' in response.text
    assert 'name="confirm_phrase" placeholder="EXECUTE" class="input" disabled' not in response.text
    assert "Review one part" not in response.text
    assert "Review run" in response.text
    assert "Saved kit values from the ESXi Setup page and shared defaults" in response.text
    assert "Management IP: 10.10.8.10" in response.text
    assert "Root password: Saved" in response.text
    assert "Built ISO path:" in response.text
    assert "esxi-20260416-121500/esxi-20260416-121500.iso" in response.text
    assert "Virtual media URL:" in response.text
    assert "http://lab-builder.local:8000/esxi-built-iso/ESXi-Launch-Review-Kit/esxi-20260416-121500.iso" in response.text
    assert "Virtual media URL source: LAB_BUILDER_PUBLIC_BASE_URL" in response.text
    assert "host=lab-builder.local port=8000" in response.text
    assert "Manual test defaults: Manual test script defaults are not used by Run Center" in response.text
    assert 'name="esxi_run_stamp" value="20260416-121500"' in response.text
    assert "Management IP" in response.text
    assert "10.10.8.10" in response.text
    assert "Base ISO path" in response.text
    stage_section = response.text.split("Stages that will run", 1)[1].split("Run for real", 1)[0]
    assert "iLO" not in stage_section


def test_prepare_execute_shows_exact_missing_esxi_fields(client, monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Missing Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = ""
    main.save_kit_config(cfg)

    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))

    response = client.post(
        "/prepare-execute",
        data={"scope": "esxi", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "ESXi setup is missing: root password." in response.text
    assert "Missing required values: root password" in response.text
    assert "Root password: Missing" in response.text


def test_download_built_esxi_iso_serves_nested_output_path(client, monkeypatch, tmp_path):
    exports_dir = tmp_path / "exports"
    iso_dir = exports_dir / "esxi-isos" / "Home-Kit-Test" / "esxi-20260420-200440"
    iso_dir.mkdir(parents=True, exist_ok=True)
    iso_path = iso_dir / "esxi-20260420-200440.iso"
    iso_path.write_text("fake iso", encoding="utf-8")

    monkeypatch.setattr(main, "EXPORTS_DIR", exports_dir)

    response = client.get("/esxi-built-iso/Home-Kit-Test/esxi-20260420-200440.iso")

    assert response.status_code == 200
    assert response.content == b"fake iso"
    access_log = iso_dir / "iso-access.log"
    assert access_log.exists()
    assert "method=GET" in access_log.read_text(encoding="utf-8")

    response = client.head("/esxi-built-iso/Home-Kit-Test/esxi-20260420-200440.iso")

    assert response.status_code == 200
    assert "method=HEAD" in access_log.read_text(encoding="utf-8")


def test_prepare_execute_enables_real_launch_for_storage_scope(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Launch Review Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    main.save_kit_config(cfg)
    main.set_current_kit_name(cfg["site"]["name"])

    response = client.post(
        "/prepare-execute",
        data={"scope": "storage", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert 'value="storage"' in response.text
    assert "Applies the approved storage plan to the current server using the exact approved discovery and plan artifacts." in response.text
    assert "Storage run values" in response.text
    assert "Apply mode:" in response.text
    assert "Approved plan path:" in response.text
    assert str(plan_paths["plan"]) in response.text
    assert "Approved discovery path:" in response.text
    assert str(export_paths["raw"]) in response.text
    assert 'name="confirm_checkbox"' in response.text
    assert 'name="confirm_checkbox" disabled' not in response.text
    assert "/execute" in response.text


def test_watch_storage_manual_reboot_completion_finishes_staged_job(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Storage Manual Reboot Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    apply_paths = main.initialize_storage_apply_artifacts(cfg, plan, plan_paths)

    class FakeManualRebootWatchClient(FakeGen10StorageApplyClient):
        def __init__(self, cfg_obj):
            super().__init__(cfg_obj)

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            del system_path
            raise ILOError("simulated manual reboot interruption")

        def get_summary(self):
            return {"Managers": 1}

    clients = [FakeGen10StorageApplyClient(None), FakeManualRebootWatchClient(None)]
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: clients.pop(0))

    main.run_storage_apply(cfg, str(export_paths["raw"]), str(plan_paths["plan"]), "wipe_rebuild", apply_paths)
    staged_job = main.load_job("Storage Manual Reboot Kit")
    assert staged_job["status"] == "Staged"
    assert staged_job["progress_percent"] == 68
    assert staged_job["completed_steps"] == 10
    assert staged_job["total_steps"] == 15

    main.watch_storage_manual_reboot_completion(
        cfg,
        str(export_paths["raw"]),
        str(plan_paths["plan"]),
        apply_paths,
        reboot_start_timeout=1,
        return_timeout=1,
        poll_interval=0,
    )

    job = main.load_job("Storage Manual Reboot Kit")
    assert job["status"] == "Completed"
    assert job["current_stage"] == "Finished"
    assert job["completed_steps"] == 15
    assert job["total_steps"] == 15
    assert job["progress_percent"] == 100
    assert any("Manual reboot detected" in line for line in job["logs"])
    assert any("post-reboot storage discovery" in line for line in job["logs"])
    assert any("post-reboot storage validation completed" in line for line in job["logs"])

    workflow = main.load_storage_workflow_state(apply_paths)
    assert workflow["apply"]["workflow_state"] == "post_reboot_validation_complete"
    assert workflow["apply"]["reboot_status"] == "Completed"


def test_prepare_execute_accepts_multiple_selected_runs(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Multi Review Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    cfg["windows"]["vm_name"] = "lab-win"
    cfg["windows"]["admin_password"] = "windowssecret"
    main.save_kit_config(cfg)

    response = client.post(
        "/prepare-execute",
        data={"selected_scopes": ["esxi", "windows"], "return_page": "execution"},
    )

    assert response.status_code == 200
    stage_section = response.text.split("Stages that will run", 1)[1].split("Run for real", 1)[0]
    assert "ESXi" in stage_section
    assert "Windows" in stage_section
    assert "QNAP" not in stage_section
    assert "Fix the blocked items above first." in response.text


def test_prepare_execute_whole_run_launches_supported_included_stages(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Whole Real Multi Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["target_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.20"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    cfg["included"]["ilo"] = True
    cfg["included"]["storage"] = True
    cfg["included"]["esxi"] = True
    cfg["included"]["windows"] = False
    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    main.save_kit_config(cfg)
    main.set_current_kit_name(cfg["site"]["name"])

    response = client.post(
        "/prepare-execute",
        data={"selected_scopes": ["included"], "return_page": "execution"},
    )

    assert response.status_code == 200
    assert 'name="scope" value="multi__ilo__storage__esxi"' in response.text
    assert "Runs the included live stages in order." in response.text
    assert "ESXi installer values" in response.text


def test_execute_whole_run_starts_multi_stage_path(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Whole Execute Multi Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["target_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.20"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    cfg["included"]["ilo"] = True
    cfg["included"]["storage"] = True
    cfg["included"]["esxi"] = True
    cfg["included"]["windows"] = False
    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    main.save_kit_config(cfg)
    main.set_current_kit_name(cfg["site"]["name"])

    started: dict[str, object] = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    response = client.post(
        "/execute",
        data={
            "scope": "included",
            "confirm_checkbox": "on",
            "confirm_phrase": "EXECUTE",
            "return_page": "execution",
        },
    )

    assert response.status_code == 200
    assert "Real selected-stage automation started in the background. Check Job Monitor for live progress and logs." in response.text
    assert started["target"] is main.execute_real_job_in_background
    assert started["args"][1] == "multi__ilo__storage__esxi"
    assert started["daemon"] is True
    assert started["started"] is True


def test_multi_real_run_promotes_final_ilo_ip_before_esxi(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Multi Real Endpoint Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["target_ip"] = "10.10.8.91"
    cfg["ip_plan"]["ilo"] = "10.10.8.91"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    main.save_kit_config(cfg)

    calls = []

    def fake_run_ilo_real(run_cfg):
        calls.append(("ilo", run_cfg["ilo"]["current_ip"], run_cfg["ilo"]["target_ip"]))
        main.promote_final_ilo_endpoint(run_cfg, run_cfg["ilo"]["target_ip"])
        main.save_kit_config(run_cfg)
        main.save_job(
            run_cfg["site"]["name"],
            {
                "status": "Completed",
                "scope": "ilo",
                "logs": ["[OK] iLO finished"],
                "storage_run_directory": "",
            },
        )

    def fake_run_esxi_real(run_cfg, run_stamp=None):
        calls.append(("esxi", run_cfg["ilo"]["current_ip"], run_cfg["ilo"]["host"], run_stamp))

    monkeypatch.setattr(main, "run_ilo_real", fake_run_ilo_real)
    monkeypatch.setattr(main, "run_esxi_real", fake_run_esxi_real)

    main.execute_real_job_in_background(cfg, "multi__ilo__esxi")

    assert calls == [
        ("ilo", "10.10.8.90", "10.10.8.91"),
        ("esxi", "10.10.8.91", "10.10.8.91", None),
    ]
    final_job = main.load_job(cfg["site"]["name"])
    assert final_job.get("root_scope") == "multi__ilo__esxi"
    assert final_job.get("stage_statuses", {}).get("ilo") == "completed"
    assert final_job.get("stage_statuses", {}).get("esxi") == "completed"


def test_execute_real_single_ilo_scope_uses_registry_executor(monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Single ILO Scope Kit"
    main.save_kit_config(cfg)
    kit_name = main.sanitize_kit_name(cfg["site"]["name"])
    main.initialize_background_job(kit_name, "ilo")

    calls = []

    def fake_run_ilo_real(run_cfg):
        calls.append(run_cfg["site"]["name"])
        main.save_job(
            run_cfg["site"]["name"],
            {
                "status": "Completed",
                "scope": "ilo",
                "root_scope": "ilo",
                "current_stage": "iLO finished",
                "logs": ["[OK] iLO finished"],
            },
        )

    monkeypatch.setattr(main, "run_ilo_real", fake_run_ilo_real)

    main.execute_real_job_in_background(cfg, "ilo")

    assert calls == [kit_name]
    final_job = main.load_job(kit_name)
    assert final_job.get("stage_statuses", {}).get("ilo") == "completed"


def test_execute_real_single_esxi_scope_uses_registry_executor(monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Single ESXi Scope Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["_runtime"] = {"esxi_run_stamp": "20260505-120000"}
    main.save_kit_config(cfg)
    kit_name = main.sanitize_kit_name(cfg["site"]["name"])
    main.initialize_background_job(kit_name, "esxi")

    calls = []

    def fake_promote_final_ilo_endpoint(run_cfg, final_ip=None):
        del final_ip
        calls.append(("promote", run_cfg["site"]["name"]))

    def fake_run_esxi_real(run_cfg, run_stamp=None):
        calls.append(("esxi", run_cfg["site"]["name"], run_stamp))
        main.save_job(
            run_cfg["site"]["name"],
            {
                "status": "Completed",
                "scope": "esxi",
                "root_scope": "esxi",
                "current_stage": "ESXi finished",
                "logs": ["[OK] ESXi finished"],
            },
        )

    monkeypatch.setattr(main, "promote_final_ilo_endpoint", fake_promote_final_ilo_endpoint)
    monkeypatch.setattr(main, "run_esxi_real", fake_run_esxi_real)

    main.execute_real_job_in_background(cfg, "esxi")

    assert calls == [
        ("promote", kit_name),
        ("esxi", kit_name, "20260505-120000"),
    ]
    final_job = main.load_job(kit_name)
    assert final_job.get("stage_statuses", {}).get("esxi") == "completed"


def test_execute_real_single_cisco_scope_applies_approved_plan(monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Single Cisco Scope Kit"
    cfg["cisco_switch"].update(
        {
            "management_ip": "10.10.8.2",
            "ip": "10.10.8.2",
            "username": "admin",
            "password": "secret",
            "last_ssh_test": {"ok": True},
            "config_approval": {"state": "approved", "mode": "full"},
        }
    )
    main.save_kit_config(cfg)
    kit_name = main.sanitize_kit_name(cfg["site"]["name"])
    main.initialize_background_job(kit_name, "cisco_switch")
    calls = []
    launch_options = main.build_execution_launch_options(cfg, "cisco_switch")
    assert launch_options["real"]["scope"] == "cisco_switch"
    assert main.execution_mode_for_scope("cisco_switch")["key"] == "real"

    class FakeCiscoService:
        def preview_config(self, context, mode="full", selected_ports=None):
            calls.append(("preview", mode, (context.get("cfg") or {}).get("cisco_switch", {}).get("management_ip")))
            return {"ok": True, "config": "hostname sw01", "validation": {"ok": True, "errors": [], "warnings": []}}

        def apply_config(self, context, mode="full", selected_ports=None):
            calls.append(("apply", mode, (context.get("cfg") or {}).get("cisco_switch", {}).get("management_ip")))
            return {"ok": True, "applied": True, "config": "hostname sw01", "validation": {"ok": True, "errors": [], "warnings": []}}

    monkeypatch.setattr(cisco_service, "CiscoModuleService", FakeCiscoService)

    main.execute_real_job_in_background(cfg, "cisco_switch")

    assert calls == [("preview", "full", "10.10.8.2"), ("apply", "full", "10.10.8.2")]
    final_job = main.load_job(kit_name)
    assert final_job.get("stage_statuses", {}).get("cisco_switch") == "completed"
    saved = main.load_kit_config()
    assert saved["cisco_switch"]["last_cisco_action"]["applied"] is True


def test_initialize_background_job_sets_stage_statuses_for_selected_scope():
    cfg = main.default_config()
    cfg["site"]["name"] = "Stage Status Init Kit"
    cfg["included"]["ilo"] = True
    cfg["included"]["storage"] = True
    cfg["included"]["esxi"] = False
    main.save_kit_config(cfg)

    main.initialize_background_job("Stage Status Init Kit", "multi__ilo__storage")
    job = main.load_job("Stage Status Init Kit")

    assert job.get("root_scope") == "multi__ilo__storage"
    assert job.get("stage_statuses") == {"ilo": "pending", "storage": "pending"}


def test_execute_real_scope_blocks_with_exact_missing_esxi_fields(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Execute Block Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = ""
    cfg["esxi"]["root_password"] = ""
    main.save_kit_config(cfg)

    started: dict[str, object] = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    response = client.post(
        "/execute",
        data={
            "scope": "esxi",
            "confirm_checkbox": "on",
            "confirm_phrase": "EXECUTE",
            "return_page": "execution",
        },
    )

    assert response.status_code == 200
    assert "Execution blocked: ESXi setup is missing: hostname, root password." in response.text
    assert "started" not in started


def test_run_esxi_real_builds_iso_and_starts_virtual_media_boot(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Run Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["target_ip"] = "10.10.8.91"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["dns_servers"] = ["1.1.1.1", ""]
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-20260416-120000.iso"
    built_iso.write_text("iso", encoding="utf-8")
    built: dict[str, object] = {}

    def fake_build_custom_iso(spec):
        built["spec"] = spec
        (built_iso.parent / "build-summary.yml").write_text(
            yaml.safe_dump(
                {
                    "generation": {
                        "ks_cfg": {
                            "generated": True,
                            "iso_path": "/KS.CFG",
                            "inspection_path": str(built_iso.parent / "inspection" / "KS.CFG"),
                            "redacted_preview_path": str(built_iso.parent / "KS.CFG.redacted.txt"),
                            "preview_redacted": "rootpw [REDACTED]\nreboot\n",
                            "debug_no_reboot": False,
                        },
                        "boot_cfg": {"patched": True},
                        "efi_boot_cfg": {"present": True, "patched": True},
                    },
                    "self_check": {
                        "output_boot_report": {
                            "bios_entry_present": True,
                            "uefi_entry_present": True,
                        },
                        "output_files_present": {
                            "ks_cfg": True,
                            "boot_cfg": True,
                            "efi_boot_cfg": True,
                        },
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return built_iso

    initial_power_state = {"value": "On"}

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = initial_power_state["value"]
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": True,
                "Image": "http://old.example/old.iso",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }
            self.calls = []

        def get_virtual_media(self):
            return [dict(self.virtual_media)]

        def eject_virtual_media(self, vm_path):
            self.calls.append(("eject", vm_path))
            self.virtual_media["Inserted"] = False
            self.virtual_media["Image"] = ""

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            assert system_path == "/redfish/v1/Systems/1"
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": "OSBootStarted" if self.power_state == "On" else "None"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            self.calls.append(("power_reset", reset_type, system_path))
            if reset_type in {"GracefulShutdown", "ForceOff"}:
                self.power_state = "Off"
            elif reset_type == "On":
                self.power_state = "On"
            return {
                "reset_type": reset_type,
                "system_path": system_path,
                "path": f"{system_path}/Actions/ComputerSystem.Reset" if system_path else "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            }

        def _post(self, target, payload):
            self.calls.append(("post", target, payload))
            if target.endswith("VirtualMedia.InsertMedia"):
                self.virtual_media["Inserted"] = True
                self.virtual_media["Image"] = payload["Image"]

        def set_one_time_boot_cd(self, system_path=None):
            self.calls.append(("set_one_time_boot_cd", system_path))
            before = {
                "BootSourceOverrideEnabled": self.boot_state["Boot"]["BootSourceOverrideEnabled"],
                "BootSourceOverrideTarget": self.boot_state["Boot"]["BootSourceOverrideTarget"],
            }
            self.boot_state["Boot"] = {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "Cd",
            }
            after = dict(self.boot_state["Boot"])
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": before["BootSourceOverrideEnabled"],
                "before_target": before["BootSourceOverrideTarget"],
                "before_uefi_target": "",
                "after_enabled": after["BootSourceOverrideEnabled"],
                "after_target": "UefiTarget",
                "after_uefi_target": "Boot0009",
                "selected_boot_option_reference": "Boot0009",
                "boot_option_selection_reason": "Matched virtual-media UEFI boot option Boot0009.",
                "boot_option_inventory": {
                    "system_path": system_path or "/redfish/v1/Systems/1",
                    "boot": {
                        "enabled": "Disabled",
                        "target": "None",
                        "uefi_target": "",
                        "boot_order": [],
                        "boot_order_property_selection": "",
                    },
                    "boot_options_path": "/redfish/v1/Systems/1/BootOptions",
                    "boot_options_count": 2,
                    "boot_options": [
                        {
                            "path": "/redfish/v1/Systems/1/BootOptions/1",
                            "boot_option_reference": "Boot0001",
                            "display_name": "UEFI Hard Disk",
                            "alias": "",
                            "name": "",
                            "description": "",
                            "uefi_device_path": "",
                            "raw_error": "",
                        },
                        {
                            "path": "/redfish/v1/Systems/1/BootOptions/2",
                            "boot_option_reference": "Boot0009",
                            "display_name": "iLO Virtual CD/DVD ROM",
                            "alias": "",
                            "name": "",
                            "description": "",
                            "uefi_device_path": "",
                            "raw_error": "",
                        },
                    ],
                    "oem_hpe_keys": ["PostState"],
                    "oem_hpe_values": {"PostState": "Off"},
                },
                "matched": True,
                "notes": ["Verified one-time boot override."],
            }

    created_clients = []

    def build_client(cfg_obj):
        client = FakeEsxiILOClient(cfg_obj)
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "build_custom_iso", fake_build_custom_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 2})
    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_esxi_real(cfg, run_stamp="20260416-120000")

    job = main.load_job("Real ESXi Run Kit")
    joined_logs = "\n".join(job["logs"])
    client = created_clients[-1]
    spec = built["spec"]

    assert created_clients
    assert all(item.cfg.host == "10.10.8.91" for item in created_clients)
    assert "[RUNNING] Building custom ESXi ISO" in joined_logs
    assert "[RUNNING] Generating KS.CFG" in joined_logs
    assert "[OK] KS.CFG generated" in joined_logs
    assert "[INFO] ESXi install values: hostname=esxi-lab, management_ip=10.10.8.10, subnet_mask=255.255.255.0, gateway=10.10.8.1, dns=1.1.1.1" in joined_logs
    assert "[INFO] root_password=SET (policy-valid=" in joined_logs
    assert "[INFO] Optional settings: vlan=(none), ntp=(none), ssh=yes, disable_ipv6=yes, debug_no_reboot=no" in joined_logs
    assert f"[INFO] Base ISO: {spec.base_iso_path}" in joined_logs
    assert "[INFO] KS.CFG install target: install --firstdisk --overwritevmfs" in joined_logs
    assert "[INFO] KS.CFG path: iso=/KS.CFG" in joined_logs
    assert "[OK] BOOT.CFG patched" in joined_logs
    assert "[OK] EFI/BOOT/BOOT.CFG patched" in joined_logs
    assert "[INFO] ISO self-check: bios_boot=yes, uefi_boot=yes, ks_cfg=yes, boot_cfg=yes, efi_boot_cfg=yes" in joined_logs
    assert f"[OK] Built ESXi ISO: {built_iso}" in joined_logs
    assert "[INFO] Virtual media URL: http://lab-builder.local:8000/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso" in joined_logs
    assert "[RUNNING] Ejecting previous virtual media" in joined_logs
    assert "[RUNNING] Powering server off before setting one-time boot" in joined_logs
    assert "[OK] Server is off" in joined_logs
    assert "[RUNNING] Mounting custom ESXi ISO" in joined_logs
    assert "[INFO] Virtual media readback: inserted=yes image=http://lab-builder.local:8000/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso" in joined_logs
    assert "[OK] Virtual media mounted" in joined_logs
    assert "[RUNNING] Setting one-time boot to CD/DVD" in joined_logs
    assert "[INFO] Boot override before: enabled=Disabled target=None" in joined_logs
    assert "[OK] One-time boot set to CD/DVD" in joined_logs
    assert "[INFO] Boot override after: enabled=Once target=UefiTarget" in joined_logs
    assert "[INFO] Boot override decision: selected UEFI virtual-media option Boot0009 target=Boot0009." in joined_logs
    assert "HPE OEM boot values:" not in joined_logs
    assert "[INFO] Boot override note: Verified one-time boot override." in joined_logs
    assert "[RUNNING] Powering server on" in joined_logs
    assert "[RUNNING] Waiting for ESXi management network on 10.10.8.10" in joined_logs
    assert "[OK] ESXi responded on configured IP 10.10.8.10:443 after 2 checks. ESXi boot sequence started." in joined_logs
    assert "Valid1Pass!" not in joined_logs
    assert job["status"] == "Completed"
    assert len(created_clients) == 1
    assert job["esxi_iso_path"] == str(built_iso)
    assert job["esxi_iso_url"].endswith("/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso")
    assert job["esxi_expected_ip"] == "10.10.8.10"
    assert job["esxi_trace_path"].endswith("/esxi-run-trace.yml")
    assert spec.hostname == "esxi-lab"
    assert spec.management_ip == "10.10.8.10"
    assert spec.subnet_mask == "255.255.255.0"
    assert spec.gateway == "10.10.8.1"
    assert spec.dns_servers == ["1.1.1.1"]
    assert spec.root_password == "Valid1Pass!"
    assert spec.output_name == "esxi-20260416-120000"
    assert spec.esxi_version == "7"
    assert spec.debug_no_reboot is False
    assert "[INFO] Selected ESXi version: 7" in joined_logs
    assert "[OK] KS.CFG generated for ESXi 7" in joined_logs
    trace_path = main.Path(job["esxi_trace_path"])
    assert trace_path.exists()
    trace = yaml.safe_load(trace_path.read_text(encoding="utf-8"))
    summary = yaml.safe_load(main.Path(job["run_summary_path"]).read_text(encoding="utf-8"))
    assert trace["install_values"]["hostname"] == "esxi-lab"
    assert trace["install_values"]["root_password_saved"] is True
    assert trace["install_values"]["root_password_policy_valid"] is True
    assert trace["install_values"]["debug_no_reboot"] is False
    assert trace["artifacts"]["base_iso_path"] == str(spec.base_iso_path)
    assert trace["artifacts"]["output_iso_path"] == str(built_iso)
    assert trace["artifacts"]["virtual_media_url"].endswith("/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso")
    assert trace["builder_summary"]["generation"]["boot_cfg"]["patched"] is True
    assert summary["esxi_run_summary"]["install_values"]["hostname"] == "esxi-lab"
    assert summary["esxi_run_summary"]["artifacts"]["base_iso_path"] == str(spec.base_iso_path)
    assert summary["esxi_run_summary"]["artifacts"]["built_iso_path"] == str(built_iso)
    assert summary["esxi_run_summary"]["artifacts"]["virtual_media_url"].endswith("/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso")
    assert summary["esxi_run_summary"]["builder_generation"]["boot_cfg"]["patched"] is True
    assert summary["esxi_run_summary"]["ks_cfg"]["preview_redacted"] == "rootpw [REDACTED]\nreboot\n"
    assert summary["esxi_run_summary"]["install_target"]["kickstart_line"] == "install --firstdisk --overwritevmfs"
    assert summary["esxi_run_summary"]["builder_self_check"]["output_boot_report"]["uefi_entry_present"] is True
    assert summary["esxi_run_summary"]["boot_override"]["matched"] is True
    assert summary["esxi_run_summary"]["boot_override"]["selected_boot_option_reference"] == "Boot0009"
    assert summary["esxi_run_summary"]["boot_override"]["boot_option_selection_reason"] == "Matched virtual-media UEFI boot option Boot0009."
    assert summary["esxi_run_summary"]["boot_override"]["boot_option_inventory"]["boot_options_path"] == "/redfish/v1/Systems/1/BootOptions"
    assert summary["esxi_run_summary"]["boot_override"]["boot_option_inventory"]["boot_options_count"] == 2
    assert summary["esxi_run_summary"]["boot_override"]["boot_option_inventory"]["oem_hpe_keys"] == ["PostState"]
    assert summary["esxi_run_summary"]["boot_override"]["boot_option_inventory"]["oem_hpe_values"]["PostState"] == "Off"
    assert summary["esxi_run_summary"]["boot_evidence"]["power_state"] == "On"
    assert summary["esxi_run_summary"]["boot_evidence"]["boot_progress_state"] == "OSBootStarted"
    assert summary["esxi_run_summary"]["virtual_media"]["insert_target"].endswith("VirtualMedia.InsertMedia")
    assert summary["esxi_run_summary"]["virtual_media"]["post_mount_inserted"] is True
    assert summary["esxi_run_summary"]["virtual_media"]["post_mount_image_matches"] is True
    assert summary["esxi_run_summary"]["management_network"]["host"] == "10.10.8.10"
    assert summary["esxi_run_summary"]["management_network"]["attempts"] == 2
    assert ("eject", "/redfish/v1/Managers/1/VirtualMedia/2") in client.calls
    assert ("power_reset", "ForceOff", "/redfish/v1/Systems/1") in client.calls
    assert "[INFO] Power reset request: ResetType=ForceOff endpoint=/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" in joined_logs
    assert "allowed=" in joined_logs
    assert (
        "post",
        "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia",
        {
            "Image": "http://lab-builder.local:8000/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso",
            "Inserted": True,
            "WriteProtected": True,
        },
    ) in client.calls
    assert ("set_one_time_boot_cd", "/redfish/v1/Systems/1") in client.calls
    assert ("power_reset", "On", "/redfish/v1/Systems/1") in client.calls

    initial_power_state["value"] = "Off"
    created_clients.clear()
    cfg["site"]["name"] = "Real ESXi Already Off Kit"
    main.run_esxi_real(cfg, run_stamp="20260416-120001")
    off_job = main.load_job("Real ESXi Already Off Kit")
    off_logs = "\n".join(off_job["logs"])
    off_client = created_clients[-1]

    assert "[SKIP] Server already Off before ESXi boot preparation." in off_logs
    assert ("power_reset", "ForceOff", "/redfish/v1/Systems/1") not in off_client.calls
    assert ("power_reset", "On", "/redfish/v1/Systems/1") in off_client.calls


def test_run_esxi_real_passes_selected_esxi8_iso_to_builder(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi 8 Run Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["version"] = "8"
    cfg["esxi"]["base_iso_path"] = str(tmp_path / "VMware-ESXi-8.iso")
    cfg["esxi"]["debug_no_reboot"] = True
    cfg["esxi"]["hostname"] = "esxi8-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    Path(cfg["esxi"]["base_iso_path"]).write_text("iso8", encoding="utf-8")
    built_iso = tmp_path / "esxi8-built.iso"
    built_iso.write_text("iso", encoding="utf-8")
    built = {}

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "Off"
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {"#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"}},
            }
            self.boot_state = {"Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Cd"}}

        def get_virtual_media(self):
            return [dict(self.virtual_media)]

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {"PowerState": self.power_state, "BootProgress": {"LastState": "OSBootStarted"}, "Oem": {"Hpe": {"PostState": "FinishedPost"}}, **self.boot_state}

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "path": f"{system_path}/Actions/ComputerSystem.Reset"}

        def _post(self, target, payload):
            self.virtual_media["Inserted"] = True
            self.virtual_media["Image"] = payload["Image"]

        def set_one_time_boot_cd(self, system_path=None):
            return {"system_path": system_path or "/redfish/v1/Systems/1", "before_enabled": "Disabled", "before_target": "None", "after_enabled": "Once", "after_target": "Cd", "matched": True, "notes": ["Verified."]}

    def fake_build(spec):
        built["spec"] = spec
        (built_iso.parent / "build-summary.yml").write_text(
            yaml.safe_dump(
                {
                    "generation": {
                        "ks_cfg": {
                            "generated": True,
                            "iso_path": "/KS.CFG",
                            "redacted_preview_path": str(built_iso.parent / "KS.CFG.redacted.txt"),
                            "preview_redacted": "rootpw [REDACTED]\n# debug_no_reboot enabled\n",
                            "debug_no_reboot": True,
                        }
                    },
                    "self_check": {"output_boot_report": {}, "output_files_present": {}},
                }
            ),
            encoding="utf-8",
        )
        return built_iso

    monkeypatch.setattr(main, "build_custom_iso", fake_build)
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 1})
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg, run_stamp="20260418-200000")
    job = main.load_job("Real ESXi 8 Run Kit")
    logs = "\n".join(job["logs"])

    assert built["spec"].esxi_version == "8"
    assert built["spec"].base_iso_path == Path(cfg["esxi"]["base_iso_path"])
    assert built["spec"].debug_no_reboot is True
    assert "[INFO] Selected ESXi version: 8" in logs
    assert "ESXi debug_no_reboot is enabled" in logs
    assert "KS.CFG debug_no_reboot confirmed" in logs


def test_run_esxi_real_reconnects_after_build_when_ilo_session_has_expired(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Session Refresh Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-refresh.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg, *, expire_on_first_media=False):
            self.cfg = cfg
            self.expire_on_first_media = expire_on_first_media
            self.media_checked = False
            self.power_state = "Off"
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }
            self.calls = []

        def get_virtual_media(self):
            self.calls.append(("get_virtual_media",))
            if self.expire_on_first_media and not self.media_checked:
                self.media_checked = True
                raise main.ILOError('GET https://10.10.8.90/redfish/v1/Managers failed with HTTP 401: {"error":{"@Message.ExtendedInfo":[{"MessageId":"Base.1.18.NoValidSession"}]}}')
            return [dict(self.virtual_media)]

        def eject_virtual_media(self, vm_path):
            self.calls.append(("eject", vm_path))

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": "FirmwareReady"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            self.calls.append(("power_reset", reset_type, system_path))
            if reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.calls.append(("post", target, payload))
            self.virtual_media["Inserted"] = True
            self.virtual_media["Image"] = payload["Image"]
            self.virtual_media["WriteProtected"] = True

        def set_one_time_boot_cd(self, system_path=None):
            self.calls.append(("set_one_time_boot_cd", system_path))
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["Verified one-time boot override."],
            }

    def fake_build(spec):
        (built_iso.parent / "build-summary.yml").write_text(
            yaml.safe_dump(
                {
                    "generation": {
                        "ks_cfg": {
                            "generated": True,
                            "iso_path": "/KS.CFG",
                            "inspection_path": str(built_iso.parent / "inspection" / "KS.CFG"),
                            "redacted_preview_path": str(built_iso.parent / "KS.CFG.redacted.txt"),
                            "preview_redacted": "rootpw [REDACTED]\nreboot\n",
                            "debug_no_reboot": False,
                        }
                    },
                    "install_target": {"kickstart_line": "install --firstdisk --overwritevmfs", "mode": "firstdisk"},
                    "self_check": {"output_boot_report": {}, "output_files_present": {"ks_cfg": True}},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return built_iso

    monkeypatch.setattr(main, "build_custom_iso", fake_build)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 1})
    created_clients = []

    def build_client(cfg_obj):
        client = FakeEsxiILOClient(cfg_obj, expire_on_first_media=(len(created_clients) == 0))
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_esxi_real(cfg, run_stamp="20260418-120000")

    job = main.load_job("Real ESXi Session Refresh Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Completed"
    assert len(created_clients) == 2
    assert "[INFO] Reconnected to iLO after ISO build" in joined_logs
    assert "[INFO] iLO session expired during ESXi orchestration. Reconnecting and retrying once." in joined_logs


def test_run_esxi_real_uses_push_power_button_fallback_when_forceoff_does_not_power_off(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi ForceOff Fallback Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-fallback.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "On"
            self.boot_state = {"Boot": {"BootSourceOverrideEnabled": "Disabled", "BootSourceOverrideTarget": "None"}}
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {"#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"}},
            }
            self.calls = []

        def get_virtual_media(self):
            return [dict(self.virtual_media)]

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            assert system_path == "/redfish/v1/Systems/1"
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": "OSBootStarted" if self.power_state == "On" else "None"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            self.calls.append(("power_reset", reset_type, system_path))
            if reset_type == "PushPowerButton":
                self.power_state = "Off"
            elif reset_type == "On":
                self.power_state = "On"
            return {
                "reset_type": reset_type,
                "system_path": system_path,
                "path": f"{system_path}/Actions/ComputerSystem.Reset",
                "allowed_reset_types": ["On", "ForceOff", "PushPowerButton"],
            }

        def ensure_power_state(self, expected_state, *, system_path=None, timeout_seconds=300, poll_interval=5):
            del timeout_seconds, poll_interval
            if expected_state == "Off":
                self.power_reset("ForceOff", system_path=system_path)
                self.power_reset("PushPowerButton", system_path=system_path)
                return {
                    "action": "PushPowerButton",
                    "reset_target": f"{system_path}/Actions/ComputerSystem.Reset",
                    "allowed_reset_types": ["On", "ForceOff", "PushPowerButton"],
                }
            self.power_reset("On", system_path=system_path)
            return {
                "action": "On",
                "reset_target": f"{system_path}/Actions/ComputerSystem.Reset",
                "allowed_reset_types": ["On", "ForceOff", "PushPowerButton"],
            }

        def _post(self, target, payload):
            self.calls.append(("post", target, payload))
            if target.endswith("VirtualMedia.InsertMedia"):
                self.virtual_media["Inserted"] = True
                self.virtual_media["Image"] = payload["Image"]

        def set_one_time_boot_cd(self, system_path=None):
            self.calls.append(("set_one_time_boot_cd", system_path))
            return {"system_path": system_path or "/redfish/v1/Systems/1", "before_enabled": "Disabled", "before_target": "None", "after_enabled": "Once", "after_target": "Cd", "matched": True, "notes": ["Verified one-time boot override."]}

    def fake_build_power_failure(spec):
        (built_iso.parent / "build-summary.yml").write_text(
            yaml.safe_dump(
                {
                    "generation": {
                        "ks_cfg": {
                            "generated": True,
                            "iso_path": "/KS.CFG",
                            "inspection_path": str(built_iso.parent / "inspection" / "KS.CFG"),
                            "redacted_preview_path": str(built_iso.parent / "KS.CFG.redacted.txt"),
                            "preview_redacted": "rootpw [REDACTED]\nreboot\n",
                            "debug_no_reboot": False,
                        }
                    },
                    "install_target": {"kickstart_line": "install --firstdisk --overwritevmfs", "mode": "firstdisk"},
                    "self_check": {"output_boot_report": {}, "output_files_present": {"ks_cfg": True}},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return built_iso

    monkeypatch.setattr(main, "build_custom_iso", fake_build_power_failure)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 2})
    created_clients = []

    def fake_wait_for_power_state(client, expected_state, **kwargs):
        if expected_state == "Off":
            has_push = ("power_reset", "PushPowerButton", "/redfish/v1/Systems/1") in getattr(client, "calls", [])
            if not has_push:
                raise ILOError("Timed out waiting for server power state Off. Last observed state: On.")
            return {"PowerState": "Off"}
        return {"PowerState": "On"}

    monkeypatch.setattr(main, "wait_for_power_state", fake_wait_for_power_state)
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: created_clients.append(FakeEsxiILOClient(cfg_obj)) or created_clients[-1])

    main.run_esxi_real(cfg, run_stamp="20260416-120100")
    job = main.load_job("Real ESXi ForceOff Fallback Kit")
    joined_logs = "\n".join(job["logs"])
    client = created_clients[0]
    assert ("power_reset", "ForceOff", "/redfish/v1/Systems/1") in client.calls
    assert ("power_reset", "PushPowerButton", "/redfish/v1/Systems/1") in client.calls
    assert "PushPowerButton fallback was used" in joined_logs


def test_run_esxi_real_blocks_power_on_when_boot_override_does_not_stick(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Boot Failure Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-20260416-120000.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "Off"
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "WriteProtected": True,
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }
            self.calls = []

        def get_virtual_media(self):
            return [dict(self.virtual_media)]

        def eject_virtual_media(self, vm_path):
            self.calls.append(("eject", vm_path))

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": "FirmwareReady"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            self.calls.append(("power_reset", reset_type, system_path))
            if reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.calls.append(("post", target, payload))
            self.virtual_media["Inserted"] = True
            self.virtual_media["Image"] = payload["Image"]

        def set_one_time_boot_cd(self, system_path=None):
            self.calls.append(("set_one_time_boot_cd", system_path))
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Hdd",
                "matched": False,
                "notes": ["Boot override did not read back as CD/DVD."],
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 2})
    created_clients = []

    def build_client(cfg_obj):
        client = FakeEsxiILOClient(cfg_obj)
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_esxi_real(cfg, run_stamp="20260416-120000")
    job = main.load_job("Real ESXi Boot Failure Kit")
    joined_logs = "\n".join(job["logs"])
    client = created_clients[0]

    assert job["status"] == "Completed"
    assert "[RUNNING] Setting one-time boot to CD/DVD" in joined_logs
    assert "[INFO] Boot override before: enabled=Disabled target=None" in joined_logs
    assert "[WARN] One-time boot did not stick cleanly; got enabled=Once target=Hdd. Continuing because mounted virtual media is verified on this hardware." in joined_logs
    assert ("power_reset", "On", "/redfish/v1/Systems/1") in client.calls


def test_run_esxi_real_continues_when_eject_media_is_unsupported(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Eject Unsupported Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-20260420-200000.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "Off"
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": True,
                "Image": "http://lab-builder.local:8000/old.iso",
                "WriteProtected": True,
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }
            self.calls = []

        def get_virtual_media(self):
            return [dict(self.virtual_media)]

        def eject_virtual_media(self, vm_path):
            self.calls.append(("eject_virtual_media", vm_path))
            raise RuntimeError('HTTP 400: {"error":{"@Message.ExtendedInfo":[{"MessageId":"iLO.2.25.UnsupportedOperation"}]}}')

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": "OSBootStarted" if self.power_state == "On" else "None"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            self.calls.append(("power_reset", reset_type, system_path))
            if reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.calls.append(("post", target, payload))
            self.virtual_media["Inserted"] = True
            self.virtual_media["Image"] = payload["Image"]

        def set_one_time_boot_cd(self, system_path=None):
            self.calls.append(("set_one_time_boot_cd", system_path))
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["One-time boot override read back exactly as requested."],
                "boot_option_inventory": {},
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 1})
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg, run_stamp="20260420-200000")
    job = main.load_job("Real ESXi Eject Unsupported Kit")
    joined_logs = "\n".join(job["logs"])

    assert "[WARN] iLO did not support ejecting the current virtual media. Continuing with best-effort media replacement." in joined_logs
    assert "[OK] Server is off" in joined_logs
    assert "[OK] Virtual media mounted" in joined_logs
    assert job["status"] == "Completed"


def test_run_esxi_real_recovers_when_eject_media_connection_drops(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Eject Disconnect Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-eject-disconnect.iso"
    built_iso.write_text("iso", encoding="utf-8")
    state = {
        "power": "Off",
        "virtual_media": {
            "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
            "Inserted": True,
            "Image": "http://lab-builder.local:8000/old.iso",
            "WriteProtected": True,
            "MediaTypes": ["CD", "DVD"],
            "Actions": {
                "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                "#VirtualMedia.EjectMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia"},
            },
        },
        "eject_calls": 0,
    }

    class FakeEsxiILOClient:
        def __init__(self, cfg_obj):
            self.cfg = cfg_obj
            self.boot_state = {"Boot": {"BootSourceOverrideEnabled": "Disabled", "BootSourceOverrideTarget": "None"}}

        def get_virtual_media(self):
            return [dict(state["virtual_media"])]

        def eject_virtual_media(self, vm_path):
            state["eject_calls"] += 1
            state["virtual_media"]["Inserted"] = False
            state["virtual_media"]["Image"] = ""
            raise main.ILOError("POST https://10.10.8.90/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia failed: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": state["power"],
                "BootProgress": {"LastState": "OSBootStarted" if state["power"] == "On" else "None"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if state["power"] == "On" else "Off"}},
                **self.boot_state,
            }

        def get_power_state(self, system_path=None):
            return state["power"]

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type == "On":
                state["power"] = "On"
            elif reset_type in {"ForceOff", "GracefulShutdown"}:
                state["power"] = "Off"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            state["virtual_media"]["Inserted"] = bool(payload.get("Inserted"))
            state["virtual_media"]["Image"] = payload.get("Image", "")
            state["virtual_media"]["WriteProtected"] = payload.get("WriteProtected")

        def set_one_time_boot_cd(self, system_path=None):
            self.boot_state["Boot"] = {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Cd"}
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["One-time boot override read back exactly as requested."],
                "boot_option_inventory": {},
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 1})
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg, run_stamp="20260420-201000")
    job = main.load_job("Real ESXi Eject Disconnect Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Completed"
    assert state["eject_calls"] == 1
    assert "iLO closed the Eject media connection without a response" in joined_logs
    assert "iLO closed EjectMedia without a response, but virtual media readback shows it ejected; treating eject as successful." in joined_logs
    assert "[OK] Virtual media mounted" in joined_logs


def test_run_esxi_real_recovers_when_insert_media_connection_drops(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Insert Disconnect Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-insert-disconnect.iso"
    built_iso.write_text("iso", encoding="utf-8")
    state = {
        "power": "Off",
        "virtual_media": {
            "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
            "Inserted": False,
            "Image": "",
            "WriteProtected": True,
            "MediaTypes": ["CD", "DVD"],
            "Actions": {
                "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                "#VirtualMedia.EjectMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia"},
            },
        },
        "insert_calls": 0,
    }

    class FakeEsxiILOClient:
        def __init__(self, cfg_obj):
            self.cfg = cfg_obj
            self.boot_state = {"Boot": {"BootSourceOverrideEnabled": "Disabled", "BootSourceOverrideTarget": "None"}}

        def get_virtual_media(self):
            return [dict(state["virtual_media"])]

        def eject_virtual_media(self, vm_path):
            state["virtual_media"]["Inserted"] = False
            state["virtual_media"]["Image"] = ""

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": state["power"],
                "BootProgress": {"LastState": "OSBootStarted" if state["power"] == "On" else "None"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if state["power"] == "On" else "Off"}},
                **self.boot_state,
            }

        def get_power_state(self, system_path=None):
            return state["power"]

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type == "On":
                state["power"] = "On"
            elif reset_type in {"ForceOff", "GracefulShutdown"}:
                state["power"] = "Off"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            state["insert_calls"] += 1
            state["virtual_media"]["Inserted"] = bool(payload.get("Inserted"))
            state["virtual_media"]["Image"] = payload.get("Image", "")
            state["virtual_media"]["WriteProtected"] = payload.get("WriteProtected")
            raise main.ILOError("POST https://10.10.8.90/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia failed: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")

        def set_one_time_boot_cd(self, system_path=None):
            self.boot_state["Boot"] = {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Cd"}
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["One-time boot override read back exactly as requested."],
                "boot_option_inventory": {},
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 1})
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg, run_stamp="20260420-201500")
    job = main.load_job("Real ESXi Insert Disconnect Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Completed"
    assert state["insert_calls"] == 1
    assert "iLO closed the Mount ISO connection without a response" in joined_logs
    assert "iLO closed InsertMedia without a response, but virtual media readback matches the generated ISO; treating mount as successful." in joined_logs
    assert "[OK] Virtual media mounted" in joined_logs


def test_run_esxi_real_recovers_from_virtual_media_max_connection(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Max Virtual Media Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-max-virtual-media.iso"
    built_iso.write_text("iso", encoding="utf-8")
    state = {
        "power": "Off",
        "virtual_media": {
            "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
            "Inserted": False,
            "Image": "",
            "WriteProtected": True,
            "MediaTypes": ["CD", "DVD"],
            "Actions": {
                "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                "#VirtualMedia.EjectMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia"},
            },
        },
        "insert_calls": 0,
        "eject_calls": 0,
    }

    class FakeEsxiILOClient:
        def __init__(self, cfg_obj):
            self.cfg = cfg_obj
            self.boot_state = {"Boot": {"BootSourceOverrideEnabled": "Disabled", "BootSourceOverrideTarget": "None"}}

        def get_virtual_media(self):
            return [dict(state["virtual_media"])]

        def eject_virtual_media(self, vm_path):
            state["eject_calls"] += 1
            state["virtual_media"]["Inserted"] = False
            state["virtual_media"]["Image"] = ""

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": state["power"],
                "BootProgress": {"LastState": "OSBootStarted" if state["power"] == "On" else "None"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if state["power"] == "On" else "Off"}},
                **self.boot_state,
            }

        def get_power_state(self, system_path=None):
            return state["power"]

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type == "On":
                state["power"] = "On"
            elif reset_type in {"ForceOff", "GracefulShutdown"}:
                state["power"] = "Off"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            state["insert_calls"] += 1
            if state["insert_calls"] == 1:
                state["virtual_media"]["Inserted"] = True
                state["virtual_media"]["Image"] = "http://lab-builder.local:8000/old.iso"
                raise main.ILOError('POST https://10.10.8.90/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia failed with HTTP 400: {"error":{"@Message.ExtendedInfo":[{"MessageId":"iLO.2.25.MaxVirtualMediaConnectionEstablished"}]}}')
            state["virtual_media"]["Inserted"] = bool(payload.get("Inserted"))
            state["virtual_media"]["Image"] = payload.get("Image", "")
            state["virtual_media"]["WriteProtected"] = payload.get("WriteProtected")

        def set_one_time_boot_cd(self, system_path=None):
            self.boot_state["Boot"] = {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Cd"}
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["One-time boot override read back exactly as requested."],
                "boot_option_inventory": {},
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 1})
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg, run_stamp="20260420-202000")
    job = main.load_job("Real ESXi Max Virtual Media Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Completed"
    assert state["insert_calls"] == 2
    assert state["eject_calls"] == 1
    assert "maximum virtual media connection is already established" in joined_logs
    assert "[OK] Previous virtual media ejected:" in joined_logs
    assert "[OK] Virtual media mounted" in joined_logs


def test_run_esxi_real_clears_stuck_virtual_media_with_patch_fallback(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Patch Clear Media Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-patch-clear.iso"
    built_iso.write_text("iso", encoding="utf-8")
    state = {
        "power": "Off",
        "virtual_media": {
            "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
            "Inserted": True,
            "Image": "http://lab-builder.local:8000/old.iso",
            "WriteProtected": True,
            "MediaTypes": ["CD", "DVD"],
            "Actions": {
                "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                "#VirtualMedia.EjectMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia"},
            },
        },
        "eject_calls": 0,
        "patch_calls": 0,
    }

    class FakeEsxiILOClient:
        def __init__(self, cfg_obj):
            self.cfg = cfg_obj
            self.boot_state = {"Boot": {"BootSourceOverrideEnabled": "Disabled", "BootSourceOverrideTarget": "None"}}

        def get_virtual_media(self):
            return [dict(state["virtual_media"])]

        def eject_virtual_media(self, vm_path):
            state["eject_calls"] += 1

        def _patch(self, path, payload):
            state["patch_calls"] += 1
            if payload == {"Image": None, "Inserted": False}:
                state["virtual_media"]["Inserted"] = False
                state["virtual_media"]["Image"] = ""

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": state["power"],
                "BootProgress": {"LastState": "OSBootStarted" if state["power"] == "On" else "None"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if state["power"] == "On" else "Off"}},
                **self.boot_state,
            }

        def get_power_state(self, system_path=None):
            return state["power"]

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type == "On":
                state["power"] = "On"
            elif reset_type in {"ForceOff", "GracefulShutdown"}:
                state["power"] = "Off"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            state["virtual_media"]["Inserted"] = bool(payload.get("Inserted"))
            state["virtual_media"]["Image"] = payload.get("Image", "")
            state["virtual_media"]["WriteProtected"] = payload.get("WriteProtected")

        def set_one_time_boot_cd(self, system_path=None):
            self.boot_state["Boot"] = {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Cd"}
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["One-time boot override read back exactly as requested."],
                "boot_option_inventory": {},
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 1})
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg, run_stamp="20260420-202500")
    job = main.load_job("Real ESXi Patch Clear Media Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Completed"
    assert state["eject_calls"] == 2
    assert state["patch_calls"] == 1
    assert "trying Redfish PATCH Image=null, Inserted=false fallback" in joined_logs
    assert "[OK] Previous virtual media cleared with Redfish PATCH fallback." in joined_logs
    assert "[OK] Virtual media mounted" in joined_logs


def test_run_esxi_real_fails_when_virtual_media_readback_does_not_match(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Mount Readback Failure Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-20260416-120000.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "Off"
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }
            self.calls = []

        def get_virtual_media(self):
            return [dict(self.virtual_media)]

        def eject_virtual_media(self, vm_path):
            self.calls.append(("eject", vm_path))

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": "FirmwareReady"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            self.calls.append(("power_reset", reset_type, system_path))
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.calls.append(("post", target, payload))
            self.virtual_media["Inserted"] = False
            self.virtual_media["Image"] = "http://wrong.example/wrong.iso"

        def set_one_time_boot_cd(self, system_path=None):
            self.calls.append(("set_one_time_boot_cd", system_path))
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["Verified one-time boot override."],
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 2})
    created_clients = []

    def build_client(cfg_obj):
        client = FakeEsxiILOClient(cfg_obj)
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_esxi_real(cfg, run_stamp="20260416-120000")
    job = main.load_job("Real ESXi Mount Readback Failure Kit")
    joined_logs = "\n".join(job["logs"])
    client = created_clients[0]

    assert job["status"] == "Failed"
    assert job["current_stage"] == "Mount ISO"
    assert "[FAILED] Virtual media mount readback did not match the built ESXi ISO URL." in joined_logs
    assert ("set_one_time_boot_cd", "/redfish/v1/Systems/1") not in client.calls
    assert ("power_reset", "On", "/redfish/v1/Systems/1") not in client.calls


def test_run_esxi_real_fails_when_expected_management_ip_never_comes_up(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Failure Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-failure.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "On"
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.vm = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }

        def get_virtual_media(self):
            return [dict(self.vm)]

        def eject_virtual_media(self, vm_path):
            return None

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": "FirmwareReady"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type in {"GracefulShutdown", "ForceOff"}:
                self.power_state = "Off"
            elif reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.vm = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": True,
                "Image": payload["Image"],
                "WriteProtected": True,
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }
            return None

        def set_one_time_boot_cd(self, system_path=None):
            self.boot_state["Boot"] = {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "Cd",
            }
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["Verified one-time boot override."],
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: (_ for _ in ()).throw(main.ILOError(f"ESXi did not answer on configured IP {host}:443 before timeout. Last error: timed out")))
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg)
    job = main.load_job("Real ESXi Failure Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Failed"
    assert "[INFO] Final boot evidence before timeout: power=On, post_state=FinishedPost, boot_progress=FirmwareReady, boot_override=Once/Cd" in joined_logs
    assert "[INFO] Final virtual media state before timeout: inserted=yes" in joined_logs
    assert "ESXi did not answer on configured IP 10.10.8.10:443 before timeout." in joined_logs
    assert "This usually means the kickstart network settings did not apply or the installer did not finish." in joined_logs


def test_run_esxi_real_fails_early_when_stuck_in_post(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Stuck Post Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-stuck.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "On"
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.vm = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }

        def get_virtual_media(self):
            return [dict(self.vm)]

        def eject_virtual_media(self, vm_path):
            return None

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": ""},
                "Oem": {"Hpe": {"PostState": "InPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type in {"GracefulShutdown", "ForceOff"}:
                self.power_state = "Off"
            elif reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.vm = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": True,
                "Image": payload["Image"],
                "WriteProtected": True,
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }
            return None

        def set_one_time_boot_cd(self, system_path=None):
            self.boot_state["Boot"] = {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "Cd",
            }
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["Verified one-time boot override."],
            }

    def fake_wait(host, **kwargs):
        on_poll = kwargs["on_poll"]
        for attempt in range(1, 9):
            on_poll(
                {
                    "attempts": attempt,
                    "host": host,
                    "port": 443,
                    "last_error": "timed out",
                    "remaining_seconds": 600,
                }
            )
        raise AssertionError("wait loop should have failed from stuck POST detection before timeout")

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", fake_wait)
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg)
    job = main.load_job("Real ESXi Stuck Post Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Failed"
    assert "[INFO] ESXi wait poll " in joined_logs
    assert "post_state=InPost" in joined_logs
    assert "boot_override=Once/Cd" in joined_logs
    assert "Server appears stuck in firmware/POST with the virtual CD/DVD still mounted and the one-time CD/DVD boot override still pending." in joined_logs


def test_run_esxi_real_does_not_rearm_virtual_media_after_override_is_consumed(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi No Rearm Boot Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-rearm.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "On"
            self.boot_set_count = 0
            self.eject_count = 0
            self.wait_evidence_count = 0
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.vm = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }

        def get_virtual_media(self):
            return [dict(self.vm)]

        def eject_virtual_media(self, vm_path):
            self.eject_count += 1
            self.vm["Inserted"] = False
            self.vm["Image"] = ""
            return None

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            post_state = "InPost" if self.power_state == "On" else "Off"
            if self.wait_evidence_count == 4:
                post_state = "InPostDiscoveryComplete"
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": ""},
                "Oem": {"Hpe": {"PostState": post_state}},
                **self.boot_state,
            }

        def get_power_state(self, system_path=None):
            return self.power_state

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type in {"GracefulShutdown", "ForceOff"}:
                self.power_state = "Off"
            elif reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.vm = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": True,
                "Image": payload["Image"],
                "WriteProtected": True,
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }
            return None

        def set_one_time_boot_cd(self, system_path=None):
            self.boot_set_count += 1
            self.boot_state["Boot"] = {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "Cd",
            }
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "matched": True,
                "notes": ["Verified one-time boot override."],
            }

    created_clients = []

    def make_client(cfg_obj):
        client = FakeEsxiILOClient(cfg_obj)
        created_clients.append(client)
        return client

    def fake_collect(client, *, system_path=None):
        client.wait_evidence_count += 1
        if client.wait_evidence_count >= 3 and client.boot_set_count == 1:
            client.boot_state["Boot"] = {
                "BootSourceOverrideEnabled": "Disabled",
                "BootSourceOverrideTarget": "None",
            }
        system = client.get_system(system_path or "/redfish/v1/Systems/1")
        boot = system.get("Boot", {})
        return {
            "power_state": system.get("PowerState", ""),
            "post_state": ((system.get("Oem") or {}).get("Hpe") or {}).get("PostState", ""),
            "boot_progress_state": ((system.get("BootProgress") or {}).get("LastState") or ""),
            "boot_override_enabled": boot.get("BootSourceOverrideEnabled", ""),
            "boot_override_target": boot.get("BootSourceOverrideTarget", ""),
            "mounted_virtual_media": {
                "device_path": client.vm.get("@odata.id"),
                "inserted": client.vm.get("Inserted"),
                "image": client.vm.get("Image"),
            },
        }

    def fake_wait(host, **kwargs):
        on_poll = kwargs["on_poll"]
        for attempt in (1, 2, 4, 6):
            on_poll(
                {
                    "attempts": attempt,
                    "host": host,
                    "port": 443,
                    "last_error": "timed out",
                    "remaining_seconds": 600,
                }
            )
        raise main.ILOError(f"ESXi did not answer on configured IP {host}:443 before timeout. Last error: timed out")

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", fake_wait)
    monkeypatch.setattr(main, "collect_esxi_boot_evidence", fake_collect)
    monkeypatch.setattr(main, "ILOClient", make_client)

    main.run_esxi_real(cfg)
    job = main.load_job("Real ESXi No Rearm Boot Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Failed"
    assert "[INFO] One-time CD/DVD boot override was consumed; treating ESXi installer boot as started and not re-arming virtual media automatically." in joined_logs
    assert "Retry boot power-off" not in joined_logs
    assert "Retry boot override after" not in joined_logs
    assert "Retry boot power-on" not in joined_logs
    assert created_clients[0].boot_set_count == 1
    assert created_clients[0].eject_count == 1
    assert job["esxi_installer_boot_observed"] is True
    assert job["esxi_installer_reboot_detected"] is True
    assert job["esxi_post_install_boot_guard"]["eject_status"] == "ejected"
    assert "Post-install boot guard ejected virtual media" in joined_logs
    assert any("Possible kickstart failure" in item for item in job["diagnosis"]["rejection_reasons"])


def test_build_kickstart_uses_explicit_management_network_fields():
    spec = main.EsxiBuildSpec(
        kit_name="Test-Kit",
        base_iso_path=main.Path("/tmp/base.iso"),
        output_name="esxi-test",
        hostname="esxi-lab",
        management_ip="10.10.8.10",
        subnet_mask="255.255.255.0",
        gateway="10.10.8.1",
        dns_servers=["1.1.1.1", "8.8.8.8"],
        root_password="Valid1Pass!",
        vlan_id="123",
    )

    kickstart = build_kickstart(spec)

    assert "--device=vmnic0" in kickstart
    assert "--ip=10.10.8.10" in kickstart
    assert "--netmask=255.255.255.0" in kickstart
    assert "--gateway=10.10.8.1" in kickstart
    assert "--nameserver=1.1.1.1,8.8.8.8" in kickstart
    assert "--hostname=esxi-lab" in kickstart
    assert "--addvmportgroup=0" in kickstart
    assert "--vlanid=123" in kickstart
    assert "Lab Builder first boot network check" in kickstart
    assert "UPLINK=$(esxcli network nic list | awk 'NR>2 && $5 == \"Up\" {print $1; exit}')" in kickstart
    assert "esxcli network vswitch standard uplink add --uplink-name=\"$UPLINK\" --vswitch-name=vSwitch0" in kickstart
    assert "esxcli network ip interface ipv4 set --interface-name=vmk0 --ipv4=10.10.8.10 --netmask=255.255.255.0 --type=static" in kickstart


def test_build_kickstart_debug_no_reboot_omits_reboot_and_redacts_preview():
    spec = main.EsxiBuildSpec(
        kit_name="Test-Kit",
        base_iso_path=main.Path("/tmp/base.iso"),
        output_name="esxi-test",
        hostname="esxi-lab",
        management_ip="10.10.8.10",
        subnet_mask="255.255.255.0",
        gateway="10.10.8.1",
        dns_servers=["1.1.1.1"],
        root_password="Valid1Pass!",
        debug_no_reboot=True,
    )

    kickstart = build_kickstart(spec)
    redacted = redact_kickstart_text(kickstart)

    assert "\nreboot\n" not in kickstart
    assert "debug_no_reboot enabled" in kickstart
    assert "rootpw [REDACTED]" in redacted
    assert "Valid1Pass!" not in redacted


def test_run_ilo_real_executes_storage_when_included(monkeypatch):
    fake_clock = {"now": 0.0}

    monkeypatch.setattr(main.time, "sleep", lambda seconds: fake_clock.__setitem__("now", fake_clock["now"] + seconds))
    monkeypatch.setattr(main.time, "time", lambda: fake_clock["now"])

    cfg = main.default_config()
    cfg["site"]["name"] = "Real Storage Review Verify Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["target_ip"] = "10.10.8.91"
    cfg["ilo"]["gateway"] = "10.10.8.1"
    cfg["ilo"]["hostname"] = "Home-Test-01"
    cfg["shared_network"]["dns_servers"] = ["1.1.1.1", "", "", ""]
    cfg["shared_snmp"]["v3_username"] = "snmpuser"
    cfg["shared_snmp"]["v3_auth_password"] = "authpass"
    cfg["shared_snmp"]["v3_priv_password"] = "privpass"

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)

    reset_state = {"requested": False, "polls_after_reset": 0}

    class FakeRunILOClient(RecordingGen10SmartStorageWriteClient):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg
            self.discovery = discovery
            self.dns_calls = []
            self.snmp_calls = []
            self.manager_reset_calls = []

        def get_summary(self):
            if self.cfg.host == "10.10.8.91" and reset_state["requested"]:
                reset_state["polls_after_reset"] += 1
                if reset_state["polls_after_reset"] == 1:
                    raise ILOError("iLO reset in progress")
            return {"redfish_version": "1.16.0", "system_manufacturer": "HPE", "system_model": "DL360 Gen10", "power_state": "On"}

        def get_active_manager_interface(self):
            return {
                "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                "DHCPv4": {"DHCPEnabled": False},
                "IPv4Addresses": [{"Address": self.cfg.host}],
                "StaticNameServers": ["1.1.1.1"],
                "NameServers": ["1.1.1.1"],
                "HostName": "Home-Test-01",
            }

        def get_network_protocol(self):
            return (
                "/redfish/v1/Managers/1/NetworkProtocol",
                {
                    "HostName": "Home-Test-01",
                    "SNMP": {
                        "ProtocolEnabled": True,
                        "UserName": "snmpuser",
                        "AuthProtocol": "SHA",
                        "PrivacyProtocol": "AES",
                    },
                },
            )

        def set_static_ipv4_best_effort(self, address, subnet_mask, gateway):
            return {
                "applied_keys": ["DHCPv4", "IPv4StaticAddresses"],
                "before_dhcpv4": {"DHCPEnabled": True},
                "after_dhcpv4": {"DHCPEnabled": False},
                "before_ipv4_addresses": [{"Address": "10.10.8.90"}],
                "before_static_addresses": [],
                "after_ipv4_addresses": [{"Address": address}],
                "after_static_addresses": [{"Address": address, "SubnetMask": subnet_mask, "Gateway": gateway}],
            }

        def set_hostname_best_effort(self, desired_hostname):
            return {"method": "patch", "before": "old-ilo", "after": desired_hostname, "matched": True}

        def set_dns_servers_best_effort(self, dns_servers):
            self.dns_calls.append(list(dns_servers))
            return {
                "applied_keys": ["NameServers"],
                "before_static": ["8.8.8.8"],
                "before_names": ["8.8.8.8"],
                "after_static": dns_servers,
                "after_names": dns_servers,
                "requested": dns_servers,
                "verified": True,
                "verified_field": "StaticNameServers",
                "status": "Verified",
                "details": "Requested DNS values matched StaticNameServers after the write.",
            }

        def disable_ipv6_best_effort(self):
            return {"method": "patch", "path": "/redfish/v1/Managers/1/EthernetInterfaces/1"}

        def harden_snmp_best_effort(self, **kwargs):
            self.snmp_calls.append(dict(kwargs))
            return {
                "applied_keys": ["SNMP.ProtocolEnabled", "SNMPv3RequestsEnabled"],
                "verification": {
                    "checks": [
                        {"label": "protocol_enabled", "requested": True, "actual": True, "matched": True},
                        {"label": "username", "requested": kwargs["v3_username"], "actual": kwargs["v3_username"], "matched": True},
                    ]
                },
                "verified": True,
                "status": "Verified",
                "details": "Requested SNMP values were verified after the write.",
            }

        def get_storage_discovery(self, deep_smart_storage_scan=False):
            del deep_smart_storage_scan
            return self.discovery

        def reset_ilo(self):
            self.manager_reset_calls.append({"reset_type": "GracefulRestart"})
            reset_state["requested"] = True
            return {"path": "/redfish/v1/Managers/1/Actions/Manager.Reset", "reset_type": "GracefulRestart"}

        def reboot_server_and_wait(self, reset_type: str = "GracefulRestart", reboot_start_timeout: int = 120, return_timeout: int = 600, poll_interval: int = 10):
            del reboot_start_timeout, return_timeout, poll_interval
            return {
                "path": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                "system_path": "/redfish/v1/Systems/1",
                "reset_type": reset_type,
                "reboot_start_observed": True,
                "reboot_start_detail": "Observed BootProgress state after reset request: POST.",
                "system_returned": True,
                "return_detail": "System returned with PowerState=On.",
            }

    created_clients = []

    def build_client(cfg_obj):
        client = FakeRunILOClient(cfg_obj)
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_ilo_real(cfg)
    job = main.load_job("Real Storage Review Verify Kit")
    joined_logs = "\n".join(job["logs"])
    client = created_clients[0]

    assert "[RUNNING] Starting the approved storage stage after the iLO stage finished." in joined_logs
    assert "Storage plan was approved from the previous iLO address 10.10.8.11; applying it through the verified active iLO endpoint 10.10.8.91." in joined_logs
    assert "Submitted the consolidated SmartStorageConfig pending payload" in joined_logs
    assert ("DNS apply attempt" in joined_logs) or ("DNS already correct; no change needed" in joined_logs)
    assert "DNS verified" in joined_logs
    assert ("SNMP apply attempt" in joined_logs) or ("SNMP already correct; no change needed" in joined_logs)
    assert ("SNMP verified" in joined_logs) or ("SNMP already correct; no change needed" in joined_logs)
    assert "iLO reset requested" in joined_logs
    assert "iLO reset completed and the final iLO endpoint is reachable on 10.10.8.91" in joined_logs
    assert "auth_password=set | priv_password=set" in joined_logs
    assert job["dns_apply_status"] == "Already correct"
    assert job["dns_applied_values"] == ["1.1.1.1"]
    assert job["dns_before_values"] == ["1.1.1.1"]
    assert job["snmp_apply_status"] == "Already correct"
    assert job["snmp_username"] == "snmpuser"
    assert job["snmp_auth_secret_present"] is True
    assert job["snmp_priv_secret_present"] is True
    assert job["snmp_verified_checks"] == []
    assert job["storage_server_reboot_status"] in {"Completed", "Not required"}
    assert "Final hostname verified" in joined_logs
    assert "Final DNS verified" in joined_logs
    assert "Final SNMP verified" in joined_logs
    assert "Post-reset verification complete" in joined_logs
    assert client.dns_calls == []
    assert client.snmp_calls == []
    assert client.manager_reset_calls == [{"reset_type": "GracefulRestart"}]
    assert joined_logs.index("iLO reset completed and the final iLO endpoint is reachable") < joined_logs.index("Starting the approved storage stage after the iLO stage finished.")
    assert job["run_bundle_dir"]
    assert Path(job["run_bundle_dir"]).is_dir()
    assert Path(job["run_live_log_path"]).is_file()
    assert Path(job["run_trace_path"]).is_file()
    assert Path(job["run_config_snapshot_path"]).is_file()
    assert "iLO reset completed and the final iLO endpoint is reachable" in Path(job["run_live_log_path"]).read_text(encoding="utf-8")
    trace_text = Path(job["run_trace_path"]).read_text(encoding="utf-8")
    summary_text = Path(job["run_summary_path"]).read_text(encoding="utf-8")
    assert "trace_events:" in trace_text or "events:" in trace_text
    assert str(Path(job["run_bundle_dir"])) in trace_text
    assert "ilo_change_summary:" in summary_text
    assert "ipv4: changed" in summary_text
    assert "dns: already-correct" in summary_text
    assert "ilo_reset_decision:" in summary_text
    assert "reason: iLO IP changed" in summary_text


def test_run_ilo_real_marks_storage_failures_as_storage_error(monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real Storage Error Label Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["target_ip"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["hostname"] = "Home-Test-01"
    cfg["shared_network"]["dns_servers"] = ["1.1.1.1", "", "", ""]
    cfg["shared_snmp"]["v3_username"] = "snmpuser"
    cfg["shared_snmp"]["v3_auth_password"] = "authpass"
    cfg["shared_snmp"]["v3_priv_password"] = "privpass"

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)

    class FakeRunILOClient(RecordingGen10SmartStorageWriteClient):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg

        def get_summary(self):
            return {"redfish_version": "1.16.0", "system_manufacturer": "HPE", "system_model": "DL360 Gen10", "power_state": "On"}

        def get_active_manager_interface(self):
            return {
                "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                "DHCPv4": {"DHCPEnabled": False},
                "IPv4Addresses": [{"Address": self.cfg.host}],
                "StaticNameServers": ["1.1.1.1"],
                "NameServers": ["1.1.1.1"],
                "HostName": "Home-Test-01",
            }

        def get_network_protocol(self):
            return (
                "/redfish/v1/Managers/1/NetworkProtocol",
                {
                    "HostName": "Home-Test-01",
                    "SNMP": {
                        "ProtocolEnabled": True,
                        "UserName": "snmpuser",
                        "AuthProtocol": "SHA",
                        "PrivacyProtocol": "AES",
                    },
                },
            )

        def disable_ipv6_best_effort(self):
            return {"method": "patch", "path": "/redfish/v1/Managers/1/EthernetInterfaces/1"}

        def get_storage_discovery(self, deep_smart_storage_scan=False):
            del deep_smart_storage_scan
            return discovery

    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeRunILOClient(cfg_obj))
    monkeypatch.setattr(main, "run_storage_as_part_of_real_run", lambda *args, **kwargs: (_ for _ in ()).throw(ILOError("simulated storage apply failure")))

    main.run_ilo_real(cfg)
    job = main.load_job(cfg["site"]["name"])
    assert job["status"] == "Failed"
    assert job["current_stage"] == "Storage error"


def test_run_ilo_real_marks_storage_reboot_failures_with_specific_stage(monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real Storage Reboot Error Label Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["target_ip"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["hostname"] = "Home-Test-01"
    cfg["shared_network"]["dns_servers"] = ["1.1.1.1", "", "", ""]
    cfg["shared_snmp"]["v3_username"] = "snmpuser"
    cfg["shared_snmp"]["v3_auth_password"] = "authpass"
    cfg["shared_snmp"]["v3_priv_password"] = "privpass"

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)

    class FakeRunILOClient(RecordingGen10SmartStorageWriteClient):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg

        def get_summary(self):
            return {"redfish_version": "1.16.0", "system_manufacturer": "HPE", "system_model": "DL360 Gen10", "power_state": "On"}

        def get_active_manager_interface(self):
            return {
                "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                "DHCPv4": {"DHCPEnabled": False},
                "IPv4Addresses": [{"Address": self.cfg.host}],
                "StaticNameServers": ["1.1.1.1"],
                "NameServers": ["1.1.1.1"],
                "HostName": "Home-Test-01",
            }

        def get_network_protocol(self):
            return (
                "/redfish/v1/Managers/1/NetworkProtocol",
                {
                    "HostName": "Home-Test-01",
                    "SNMP": {
                        "ProtocolEnabled": True,
                        "UserName": "snmpuser",
                        "AuthProtocol": "SHA",
                        "PrivacyProtocol": "AES",
                    },
                },
            )

        def disable_ipv6_best_effort(self):
            return {"method": "patch", "path": "/redfish/v1/Managers/1/EthernetInterfaces/1"}

        def get_storage_discovery(self, deep_smart_storage_scan=False):
            del deep_smart_storage_scan
            return discovery

    def fake_storage_run(*args, **kwargs):
        del kwargs
        kit_name = args[5]
        job = args[6]
        job["storage_run_directory"] = "/tmp/storage-run"
        main.update_job(kit_name, job, "Running", "Request server reboot", 15, 20, "[RUNNING] Request server reboot")
        error = ILOError("Power reset connection dropped and expected power state was not reached within timeout. Expected=On last_observed=Off.")
        error.power_reset_details = {"expected_power_state": "On"}
        raise error

    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeRunILOClient(cfg_obj))
    monkeypatch.setattr(main, "run_storage_as_part_of_real_run", fake_storage_run)

    main.run_ilo_real(cfg)
    job = main.load_job(cfg["site"]["name"])
    assert job["status"] == "Failed"
    assert job["current_stage"] == "Storage reboot wait failed"


def test_run_ilo_real_fails_when_ilo_reset_cannot_be_verified(monkeypatch):
    fake_clock = {"now": 0.0}

    monkeypatch.setattr(main.time, "sleep", lambda seconds: fake_clock.__setitem__("now", fake_clock["now"] + seconds))
    monkeypatch.setattr(main.time, "time", lambda: fake_clock["now"])

    cfg = main.default_config()
    cfg["site"]["name"] = "Real Reset Verify Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["target_ip"] = "10.10.8.91"
    cfg["ilo"]["gateway"] = "10.10.8.1"
    cfg["ilo"]["hostname"] = "ilo-reset-test"

    class FakeResetVerifyClient(RecordingGen10SmartStorageWriteClient):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg

        def get_summary(self):
            return {"redfish_version": "1.16.0", "system_manufacturer": "HPE", "system_model": "DL360 Gen10", "power_state": "On"}

        def get_active_manager_interface(self):
            return {"@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1", "DHCPv4": {"DHCPEnabled": False}, "IPv4Addresses": [{"Address": self.cfg.host}]}

        def set_static_ipv4_best_effort(self, address, subnet_mask, gateway):
            return {
                "applied_keys": ["DHCPv4", "IPv4StaticAddresses"],
                "before_dhcpv4": {"DHCPEnabled": True},
                "after_dhcpv4": {"DHCPEnabled": False},
                "before_ipv4_addresses": [{"Address": "10.10.8.90"}],
                "before_static_addresses": [],
                "after_ipv4_addresses": [{"Address": address}],
                "after_static_addresses": [{"Address": address, "SubnetMask": subnet_mask, "Gateway": gateway}],
            }

        def set_hostname_best_effort(self, desired_hostname):
            return {"method": "patch", "before": "old-ilo", "after": desired_hostname, "matched": True}

        def disable_ipv6_best_effort(self):
            return {"method": "patch", "path": "/redfish/v1/Managers/1/EthernetInterfaces/1"}

        def harden_snmp_best_effort(self, **kwargs):
            del kwargs
            return {
                "applied_keys": ["SNMP.ProtocolEnabled", "SNMPv3Enabled"],
                "verification": {"checks": [{"label": "protocol_enabled", "requested": True, "actual": True, "matched": True}]},
                "matched": True,
                "verified": True,
                "status": "Verified",
                "reset_recommended": True,
                "notes": ["Requested SNMP values were verified after the write."],
            }

        def reset_ilo(self):
            return {"path": "/redfish/v1/Managers/1/Actions/Manager.Reset", "reset_type": "GracefulRestart"}

    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeResetVerifyClient(cfg_obj))

    main.run_ilo_real(cfg)
    job = main.load_job("Real Reset Verify Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Failed"
    assert job["ilo_reset_status"] == "Failed"
    assert job["ilo_stage_finished"] is False
    assert "iLO reset was requested but completion was not verified" in joined_logs
    assert "Storage and later stages were blocked because the iLO stage did not finish." in joined_logs


def test_run_job_simulation_writes_run_bundle_files():
    cfg = main.default_config()
    cfg["site"]["name"] = "Preview Bundle Kit"
    main.initialize_background_job(cfg["site"]["name"], "esxi")

    main.run_job_simulation(cfg, "esxi")

    job = main.load_job("Preview Bundle Kit")
    assert job["status"] == "Preview complete"
    assert Path(job["run_bundle_dir"]).is_dir()
    live_log = Path(job["run_live_log_path"]).read_text(encoding="utf-8")
    assert "[PREVIEW] Preview ESXi configuration" in live_log
    assert "[DONE] Preview complete. No real changes were made." in live_log
    summary_text = Path(job["run_summary_path"]).read_text(encoding="utf-8")
    assert "Preview-Bundle-Kit" in summary_text
    config_snapshot_text = Path(job["run_config_snapshot_path"]).read_text(encoding="utf-8")
    assert "site:" in config_snapshot_text


def test_run_ilo_real_continues_on_existing_session_when_target_ip_already_reads_back(monkeypatch):
    fake_clock = {"now": 0.0}

    monkeypatch.setattr(main.time, "sleep", lambda seconds: fake_clock.__setitem__("now", fake_clock["now"] + seconds))
    monkeypatch.setattr(main.time, "time", lambda: fake_clock["now"])

    cfg = main.default_config()
    cfg["site"]["name"] = "Real ILO Session Carry Kit"
    cfg["ilo"]["current_ip"] = "192.168.1.201"
    cfg["ilo"]["host"] = "192.168.1.201"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["target_ip"] = "192.168.1.200"
    cfg["ilo"]["gateway"] = "192.168.1.1"
    cfg["ilo"]["subnet_mask"] = "255.255.255.0"
    cfg["ilo"]["hostname"] = "Home-Test-01"
    cfg["shared_network"]["dns_servers"] = ["8.8.8.8", "2.2.2.2", "", ""]
    cfg["shared_snmp"]["v3_username"] = "PrivateUser"
    cfg["shared_snmp"]["v3_auth_password"] = "SnmpAuth123!"
    cfg["shared_snmp"]["v3_priv_password"] = "SnmpPriv123!"

    class FakeCarrySessionClient(RecordingGen10SmartStorageWriteClient):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg
            self.dns_calls = []
            self.snmp_calls = []
            self.manager_reset_calls = []

        def get_summary(self):
            if self.cfg.host == "192.168.1.200":
                raise ILOError("No route to host")
            return {"redfish_version": "1.20.0", "system_manufacturer": "HPE", "system_model": "ProLiant DL360 Gen10", "power_state": "On"}

        def get_active_manager_interface(self):
            return {
                "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                "DHCPv4": {"DHCPEnabled": False},
                "IPv4Addresses": [{"Address": "192.168.1.200"}],
            }

        def set_static_ipv4_best_effort(self, address, subnet_mask, gateway):
            return {
                "applied_keys": ["DHCPv4", "IPv4StaticAddresses"],
                "before_dhcpv4": {"DHCPEnabled": False},
                "after_dhcpv4": {"DHCPEnabled": False},
                "before_ipv4_addresses": [{"Address": "192.168.1.200", "SubnetMask": subnet_mask, "Gateway": gateway}],
                "before_static_addresses": [],
                "after_ipv4_addresses": [{"Address": address, "SubnetMask": subnet_mask, "Gateway": gateway}],
                "after_static_addresses": [{"Address": address, "SubnetMask": subnet_mask, "Gateway": gateway}],
            }

        def set_hostname_best_effort(self, desired_hostname):
            return {"method": "patch", "before": "old-ilo", "after": desired_hostname, "matched": True}

        def set_dns_servers_best_effort(self, dns_servers):
            self.dns_calls.append(list(dns_servers))
            return {
                "applied_keys": ["NameServers"],
                "before_static": ["1.1.1.1"],
                "before_names": ["1.1.1.1"],
                "after_static": dns_servers,
                "after_names": dns_servers,
                "requested": dns_servers,
                "verified": True,
                "status": "Verified",
            }

        def disable_ipv6_best_effort(self):
            return {"method": "patch", "path": "/redfish/v1/Managers/1/EthernetInterfaces/1"}

        def harden_snmp_best_effort(self, **kwargs):
            self.snmp_calls.append(dict(kwargs))
            return {
                "applied_keys": ["SNMP.ProtocolEnabled", "SNMPv3Enabled"],
                "verification": {"checks": [{"label": "protocol_enabled", "requested": True, "actual": True, "matched": True}]},
                "matched": True,
                "verified": True,
                "status": "Verified",
                "reset_recommended": True,
                "notes": ["Requested SNMP values were verified after the write."],
            }

        def reset_ilo(self):
            self.manager_reset_calls.append({"reset_type": "GracefulRestart"})
            return {"path": "/redfish/v1/Managers/1/Actions/Manager.Reset", "reset_type": "GracefulRestart"}

    created_clients = []

    def build_client(cfg_obj):
        client = FakeCarrySessionClient(cfg_obj)
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_ilo_real(cfg)
    job = main.load_job("Real ILO Session Carry Kit")
    joined_logs = "\n".join(job["logs"])
    client = created_clients[0]

    assert "Target iLO IP already appeared in interface readback" in joined_logs
    assert "DNS apply attempt" in joined_logs
    assert "SNMP apply attempt" in joined_logs
    assert "iLO reset requested" in joined_logs
    assert client.dns_calls == [["8.8.8.8", "2.2.2.2"]]
    assert client.snmp_calls
    assert client.manager_reset_calls == [{"reset_type": "GracefulRestart"}]
    assert job["status"] == "Failed"
    assert job["ilo_reset_status"] == "Failed"


def test_run_ilo_real_does_not_reset_when_only_dns_changes(monkeypatch):
    fake_clock = {"now": 0.0}

    monkeypatch.setattr(main.time, "sleep", lambda seconds: fake_clock.__setitem__("now", fake_clock["now"] + seconds))
    monkeypatch.setattr(main.time, "time", lambda: fake_clock["now"])

    cfg = main.default_config()
    cfg["site"]["name"] = "Real DNS Only Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["target_ip"] = "10.10.8.90"
    cfg["ilo"]["gateway"] = "10.10.8.1"
    cfg["ilo"]["subnet_mask"] = "255.255.255.0"
    cfg["ilo"]["hostname"] = "dns-only-ilo"
    cfg["shared_network"]["dns_servers"] = ["1.1.1.1", "9.9.9.9", "", ""]
    cfg["shared_snmp"]["v3_username"] = "snmpuser"
    cfg["shared_snmp"]["v3_auth_password"] = "authpass"
    cfg["shared_snmp"]["v3_priv_password"] = "privpass"

    class FakeDnsOnlyClient(RecordingGen10SmartStorageWriteClient):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg
            self.manager_reset_calls = []
            self.dns_values = ["8.8.8.8"]

        def get_summary(self):
            return {"redfish_version": "1.16.0", "system_manufacturer": "HPE", "system_model": "DL360 Gen10", "power_state": "On"}

        def get_active_manager_interface(self):
            return {
                "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                    "DHCPv4": {"DHCPEnabled": False},
                    "IPv4Addresses": [{"Address": "10.10.8.90", "SubnetMask": "255.255.255.0", "Gateway": "10.10.8.1"}],
                    "StaticNameServers": list(self.dns_values),
                    "NameServers": list(self.dns_values),
                    "HostName": "dns-only-ilo",
                }

        def get_network_protocol(self):
            return (
                "/redfish/v1/Managers/1/NetworkProtocol",
                {
                    "HostName": "dns-only-ilo",
                    "SNMP": {
                        "ProtocolEnabled": True,
                        "UserName": "snmpuser",
                        "AuthProtocol": "SHA",
                        "PrivacyProtocol": "AES",
                        "SNMPv3Enabled": True,
                    },
                },
            )

        def set_dns_servers_best_effort(self, dns_servers):
            before_values = list(self.dns_values)
            self.dns_values = list(dns_servers)
            return {
                "applied_keys": ["StaticNameServers"],
                "before": {"StaticNameServers": before_values, "NameServers": before_values},
                "after": {"StaticNameServers": dns_servers, "NameServers": dns_servers},
                "before_static": before_values,
                "before_names": before_values,
                "after_static": dns_servers,
                "after_names": dns_servers,
                "requested": dns_servers,
                "matched": True,
                "changed": True,
                "status": "Verified",
                "notes": ["Requested DNS values matched StaticNameServers after the write."],
            }

        def disable_ipv6_best_effort(self):
            return {"method": "patch", "path": "/redfish/v1/Managers/1/EthernetInterfaces/1"}

        def reset_ilo(self):
            self.manager_reset_calls.append({"reset_type": "GracefulRestart"})
            return {"path": "/redfish/v1/Managers/1/Actions/Manager.Reset", "reset_type": "GracefulRestart"}

    created_clients = []

    def build_client(cfg_obj):
        client = FakeDnsOnlyClient(cfg_obj)
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_ilo_real(cfg)
    job = main.load_job("Real DNS Only Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Completed"
    assert job["ilo_reset_required"] is False
    assert job["ilo_reset_status"] == "Not required"
    assert "DNS apply attempt" in joined_logs
    assert "Hostname already correct; no change needed" in joined_logs
    assert "SNMP already correct; no change needed" in joined_logs
    assert "Reset decision | required=no" in joined_logs
    assert "iLO stage finished and no separate iLO reset was needed" in joined_logs
    assert created_clients[0].manager_reset_calls == []


def test_run_ilo_real_resets_when_only_ip_changes(monkeypatch):
    fake_clock = {"now": 0.0}

    monkeypatch.setattr(main.time, "sleep", lambda seconds: fake_clock.__setitem__("now", fake_clock["now"] + seconds))
    monkeypatch.setattr(main.time, "time", lambda: fake_clock["now"])

    cfg = main.default_config()
    cfg["site"]["name"] = "Real IP Only Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["target_ip"] = "10.10.8.91"
    cfg["ilo"]["gateway"] = "10.10.8.1"
    cfg["ilo"]["subnet_mask"] = "255.255.255.0"
    cfg["ilo"]["hostname"] = "ip-only-ilo"
    cfg["shared_network"]["dns_servers"] = ["1.1.1.1", "", "", ""]
    cfg["shared_snmp"]["v3_username"] = "snmpuser"
    cfg["shared_snmp"]["v3_auth_password"] = "authpass"
    cfg["shared_snmp"]["v3_priv_password"] = "privpass"

    reset_state = {"requested": False, "polls_after_reset": 0}

    class FakeIpOnlyClient(RecordingGen10SmartStorageWriteClient):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg
            self.manager_reset_calls = []

        def get_summary(self):
            if self.cfg.host == "10.10.8.91" and reset_state["requested"]:
                reset_state["polls_after_reset"] += 1
                if reset_state["polls_after_reset"] == 1:
                    raise ILOError("iLO reset in progress")
            return {"redfish_version": "1.16.0", "system_manufacturer": "HPE", "system_model": "DL360 Gen10", "power_state": "On"}

        def get_active_manager_interface(self):
            return {
                "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                "DHCPv4": {"DHCPEnabled": False},
                "IPv4Addresses": [{"Address": self.cfg.host, "SubnetMask": "255.255.255.0", "Gateway": "10.10.8.1"}],
                "StaticNameServers": ["1.1.1.1"],
                "NameServers": ["1.1.1.1"],
                "HostName": "ip-only-ilo",
            }

        def get_network_protocol(self):
            return (
                "/redfish/v1/Managers/1/NetworkProtocol",
                {
                    "HostName": "ip-only-ilo",
                    "SNMP": {
                        "ProtocolEnabled": True,
                        "UserName": "snmpuser",
                        "AuthProtocol": "SHA",
                        "PrivacyProtocol": "AES",
                        "SNMPv3Enabled": True,
                    },
                },
            )

        def set_static_ipv4_best_effort(self, address, subnet_mask, gateway):
            return {
                "applied_keys": ["DHCPv4", "IPv4StaticAddresses"],
                "before_dhcpv4": {"DHCPEnabled": False},
                "after_dhcpv4": {"DHCPEnabled": False},
                "before_ipv4_addresses": [{"Address": "10.10.8.90", "SubnetMask": subnet_mask, "Gateway": gateway}],
                "before_static_addresses": [],
                "after_ipv4_addresses": [{"Address": address, "SubnetMask": subnet_mask, "Gateway": gateway}],
                "after_static_addresses": [{"Address": address, "SubnetMask": subnet_mask, "Gateway": gateway}],
            }

        def disable_ipv6_best_effort(self):
            return {"method": "patch", "path": "/redfish/v1/Managers/1/EthernetInterfaces/1"}

        def reset_ilo(self):
            self.manager_reset_calls.append({"reset_type": "GracefulRestart"})
            reset_state["requested"] = True
            return {"path": "/redfish/v1/Managers/1/Actions/Manager.Reset", "reset_type": "GracefulRestart"}

    created_clients = []

    def build_client(cfg_obj):
        client = FakeIpOnlyClient(cfg_obj)
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_ilo_real(cfg)
    job = main.load_job("Real IP Only Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Completed"
    assert job["ilo_reset_required"] is True
    assert job["ilo_reset_status"] == "Completed"
    assert "Hostname already correct; no change needed" in joined_logs
    assert "DNS already correct; no change needed" in joined_logs
    assert "SNMP already correct; no change needed" in joined_logs
    assert "Reset decision | required=yes | reason=iLO IP changed" in joined_logs
    assert created_clients[0].manager_reset_calls == [{"reset_type": "GracefulRestart"}]


def test_run_ilo_real_accepts_protocol_only_final_snmp_readback(monkeypatch):
    fake_clock = {"now": 0.0}

    monkeypatch.setattr(main.time, "sleep", lambda seconds: fake_clock.__setitem__("now", fake_clock["now"] + seconds))
    monkeypatch.setattr(main.time, "time", lambda: fake_clock["now"])

    cfg = main.default_config()
    cfg["site"]["name"] = "Real Final SNMP Readback Kit"
    cfg["ilo"]["current_ip"] = "192.168.1.200"
    cfg["ilo"]["host"] = "192.168.1.200"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["target_ip"] = "192.168.1.200"
    cfg["ilo"]["gateway"] = "192.168.1.1"
    cfg["ilo"]["subnet_mask"] = "255.255.255.0"
    cfg["ilo"]["hostname"] = "Home-Test-01"
    cfg["shared_network"]["dns_servers"] = ["8.8.8.8", "2.2.2.2", "", ""]
    cfg["shared_snmp"]["v3_username"] = "PrivateUser"
    cfg["shared_snmp"]["v3_auth_password"] = "SnmpAuth123!"
    cfg["shared_snmp"]["v3_priv_password"] = "SnmpPriv123!"

    class FakeProtocolOnlySnmpClient(RecordingGen10SmartStorageWriteClient):
        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg

        def get_summary(self):
            return {
                "redfish_version": "1.20.0",
                "system_manufacturer": "HPE",
                "system_model": "ProLiant DL360 Gen10",
                "power_state": "On",
            }

        def get_active_manager_interface(self):
            return {
                "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
                "DHCPv4": {"DHCPEnabled": False},
                "IPv4Addresses": [{"Address": "192.168.1.200", "SubnetMask": "255.255.255.0", "Gateway": "192.168.1.1"}],
                "StaticNameServers": ["8.8.8.8", "2.2.2.2"],
                "NameServers": ["8.8.8.8", "2.2.2.2"],
                "HostName": "Home-Test-01",
            }

        def get_network_protocol(self):
            return (
                "/redfish/v1/Managers/1/NetworkProtocol",
                {
                    "HostName": "Home-Test-01",
                    "SNMP": {
                        "ProtocolEnabled": True,
                    },
                },
            )

        def disable_ipv6_best_effort(self):
            return {"method": "patch", "path": "/redfish/v1/Managers/1/EthernetInterfaces/1"}

        def harden_snmp_best_effort(self, **kwargs):
            return {
                "applied_keys": ["ProtocolEnabled"],
                "verification": {
                    "checks": [
                        {"label": "protocol_enabled", "requested": True, "actual": True, "matched": True},
                    ],
                },
                "matched": True,
                "verified": True,
                "status": "Verified",
                "changed": True,
                "notes": ["Requested SNMP values were verified after the write."],
            }

    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeProtocolOnlySnmpClient(cfg_obj))

    main.run_ilo_real(cfg)
    job = main.load_job("Real Final SNMP Readback Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Completed"
    assert job["ilo_stage_finished"] is True
    assert "Final SNMP verified" in joined_logs
    assert "checks=[{'label': 'protocol_enabled'" in joined_logs


def test_set_dns_servers_best_effort_reports_verified_readback(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    state = {
        "@odata.id": "/redfish/v1/Managers/1/EthernetInterfaces/1",
        "StaticNameServers": ["8.8.8.8"],
        "NameServers": ["8.8.8.8"],
    }

    monkeypatch.setattr(client, "get_active_manager_interface", lambda: dict(state))
    monkeypatch.setattr(client, "_patch", lambda path, payload: state.update(payload))
    monkeypatch.setattr(client, "_get", lambda path: dict(state))

    result = client.set_dns_servers_best_effort(["1.1.1.1"])

    assert result["status"] == "Verified"
    assert result["verified"] is True
    assert result["after_static"] == ["1.1.1.1"]


def test_set_one_time_boot_cd_reports_verified_readback(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    state = {
        "Boot": {
            "BootSourceOverrideEnabled": "Disabled",
            "BootSourceOverrideTarget": "None",
        }
    }

    monkeypatch.setattr(client, "get_system", lambda system_path=None: dict(state))

    def fake_patch(path, payload):
        state["Boot"].update(payload["Boot"])

    monkeypatch.setattr(client, "_patch", fake_patch)

    result = client.set_one_time_boot_cd("/redfish/v1/Systems/1")

    assert result["before_enabled"] == "Disabled"
    assert result["before_target"] == "None"
    assert result["after_enabled"] == "Once"
    assert result["after_target"] == "Cd"
    assert result["matched"] is True


def test_set_one_time_boot_cd_accepts_equivalent_target(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    state = {
        "Boot": {
            "BootSourceOverrideEnabled": "Disabled",
            "BootSourceOverrideTarget": "None",
        }
    }

    monkeypatch.setattr(client, "get_system", lambda system_path=None: dict(state))

    def fake_patch(path, payload):
        state["Boot"]["BootSourceOverrideEnabled"] = payload["Boot"]["BootSourceOverrideEnabled"]
        state["Boot"]["BootSourceOverrideTarget"] = "UefiCd"

    monkeypatch.setattr(client, "_patch", fake_patch)

    result = client.set_one_time_boot_cd("/redfish/v1/Systems/1")

    assert result["after_enabled"] == "Once"
    assert result["after_target"] == "UefiCd"
    assert result["matched"] is True


def test_set_one_time_boot_cd_prefers_matching_uefi_boot_option(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    state = {
        "Boot": {
            "BootSourceOverrideEnabled": "Disabled",
            "BootSourceOverrideTarget": "None",
            "UefiTargetBootSourceOverride": "",
            "BootOptions": {"@odata.id": "/redfish/v1/Systems/1/BootOptions"},
        },
    }
    boot_options = {
        "/redfish/v1/Systems/1/BootOptions": {
            "Members": [
                {"@odata.id": "/redfish/v1/Systems/1/BootOptions/1"},
                {"@odata.id": "/redfish/v1/Systems/1/BootOptions/2"},
            ]
        },
        "/redfish/v1/Systems/1/BootOptions/1": {
            "BootOptionReference": "Boot0001",
            "DisplayName": "UEFI Hard Disk",
        },
        "/redfish/v1/Systems/1/BootOptions/2": {
            "BootOptionReference": "Boot0009",
            "DisplayName": "iLO Virtual CD/DVD ROM",
            "UefiDevicePath": "PciRoot(0x0)/Pci(0x1C,0x4)/Pci(0x0,0x4)/USB(0x1,0x0)",
        },
    }

    def fake_get(path):
        if path in boot_options:
            return dict(boot_options[path])
        return dict(state)

    monkeypatch.setattr(client, "get_system", lambda system_path=None: dict(state))
    monkeypatch.setattr(client, "_safe_get", fake_get)

    def fake_patch(path, payload):
        state["Boot"].update(payload["Boot"])

    monkeypatch.setattr(client, "_patch", fake_patch)

    result = client.set_one_time_boot_cd("/redfish/v1/Systems/1")

    assert result["after_target"] == "UefiTarget"
    assert result["after_uefi_target"] == "PciRoot(0x0)/Pci(0x1C,0x4)/Pci(0x0,0x4)/USB(0x1,0x0)"
    assert result["selected_boot_option_reference"] == "Boot0009"
    assert result["selected_uefi_target"] == "PciRoot(0x0)/Pci(0x1C,0x4)/Pci(0x0,0x4)/USB(0x1,0x0)"
    assert result["matched"] is True


def test_set_one_time_boot_cd_does_not_confuse_mac_hex_with_cd_media(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    state = {
        "Boot": {
            "BootSourceOverrideEnabled": "Disabled",
            "BootSourceOverrideTarget": "None",
            "UefiTargetBootSourceOverride": "",
            "BootOptions": {"@odata.id": "/redfish/v1/Systems/1/BootOptions"},
        },
    }
    boot_options = {
        "/redfish/v1/Systems/1/BootOptions": {
            "Members": [
                {"@odata.id": "/redfish/v1/Systems/1/BootOptions/5"},
                {"@odata.id": "/redfish/v1/Systems/1/BootOptions/9"},
            ]
        },
        "/redfish/v1/Systems/1/BootOptions/5": {
            "BootOptionReference": "Boot000E",
            "DisplayName": "Embedded LOM 1 Port 1 : HPE Ethernet 1Gb 4-port 331i Adapter - NIC (HTTP(S) IPv4)",
            "UefiDevicePath": "PciRoot(0x0)/Pci(0x1C,0x0)/Pci(0x0,0x0)/MAC(20677CD582A4,0x1)/IPv4(0.0.0.0)/Uri()",
        },
        "/redfish/v1/Systems/1/BootOptions/9": {
            "BootOptionReference": "Boot0012",
            "DisplayName": "iLO Virtual USB 3 : iLO Virtual CD-ROM",
            "UefiDevicePath": "PciRoot(0x0)/Pci(0x1C,0x4)/Pci(0x0,0x4)/USB(0x1,0x0)",
        },
    }

    def fake_get(path):
        if path in boot_options:
            return dict(boot_options[path])
        return dict(state)

    monkeypatch.setattr(client, "get_system", lambda system_path=None: dict(state))
    monkeypatch.setattr(client, "_safe_get", fake_get)

    def fake_patch(path, payload):
        state["Boot"].update(payload["Boot"])

    monkeypatch.setattr(client, "_patch", fake_patch)

    result = client.set_one_time_boot_cd("/redfish/v1/Systems/1")

    assert result["selected_boot_option_reference"] == "Boot0012"
    assert result["after_target"] == "UefiTarget"
    assert result["after_uefi_target"] == "PciRoot(0x0)/Pci(0x1C,0x4)/Pci(0x0,0x4)/USB(0x1,0x0)"


def test_collect_boot_option_inventory_reads_boot_scoped_bootoptions(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    system = {
        "Boot": {
            "BootSourceOverrideEnabled": "Disabled",
            "BootSourceOverrideTarget": "None",
            "UefiTargetBootSourceOverride": "None",
            "BootOptions": {"@odata.id": "/redfish/v1/Systems/1/BootOptions"},
        },
        "Oem": {"Hpe": {"PostState": "PowerOff"}},
    }
    boot_options = {
        "/redfish/v1/Systems/1/BootOptions": {
            "Members": [{"@odata.id": "/redfish/v1/Systems/1/BootOptions/9"}],
        },
        "/redfish/v1/Systems/1/BootOptions/9": {
            "BootOptionReference": "Boot0012",
            "DisplayName": "iLO Virtual USB 3 : iLO Virtual CD-ROM",
            "UefiDevicePath": "PciRoot(0x0)/Pci(0x1C,0x4)/Pci(0x0,0x4)/USB(0x1,0x0)",
        },
    }

    monkeypatch.setattr(client, "get_system", lambda system_path=None: dict(system))
    monkeypatch.setattr(client, "_safe_get", lambda path: dict(boot_options.get(path, {})))

    result = client.collect_boot_option_inventory("/redfish/v1/Systems/1")

    assert result["boot_options_path"] == "/redfish/v1/Systems/1/BootOptions"
    assert result["boot_options_count"] == 1
    assert result["boot_options"][0]["boot_option_reference"] == "Boot0012"
    assert result["boot_options"][0]["uefi_device_path"] == "PciRoot(0x0)/Pci(0x1C,0x4)/Pci(0x0,0x4)/USB(0x1,0x0)"


def test_set_one_time_boot_cd_records_empty_boot_option_inventory(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    state = {
        "Boot": {
            "BootSourceOverrideEnabled": "Disabled",
            "BootSourceOverrideTarget": "None",
        }
    }

    monkeypatch.setattr(client, "get_system", lambda system_path=None: dict(state))
    monkeypatch.setattr(
        client,
        "collect_boot_option_inventory",
        lambda system_path=None: {
            "system_path": system_path or "/redfish/v1/Systems/1",
            "boot": {"enabled": "Disabled", "target": "None", "uefi_target": "", "boot_order": [], "boot_order_property_selection": ""},
            "boot_options_path": "",
            "boot_options_count": 0,
            "boot_options": [],
            "oem_hpe_keys": [],
        },
    )

    def fake_patch(path, payload):
        state["Boot"]["BootSourceOverrideEnabled"] = payload["Boot"]["BootSourceOverrideEnabled"]
        state["Boot"]["BootSourceOverrideTarget"] = payload["Boot"]["BootSourceOverrideTarget"]

    monkeypatch.setattr(client, "_patch", fake_patch)

    result = client.set_one_time_boot_cd("/redfish/v1/Systems/1")

    assert result["selected_boot_option_reference"] == ""
    assert result["boot_option_inventory"]["boot_options_count"] == 0
    assert result["boot_option_selection_reason"] == "System did not expose a Redfish BootOptions collection."
    assert result["after_target"] == "Cd"


def test_run_esxi_real_persists_boot_option_fallback_reason(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Boot Fallback Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-fallback.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "Off"
            self.boot_state = {
                "Boot": {
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget": "None",
                }
            }
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }

        def get_virtual_media(self):
            return [dict(self.virtual_media)]

        def eject_virtual_media(self, vm_path):
            return None

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": self.power_state,
                "BootProgress": {"LastState": "OSBootStarted" if self.power_state == "On" else "None"},
                "Oem": {"Hpe": {"PostState": "FinishedPost" if self.power_state == "On" else "Off"}},
                **self.boot_state,
            }

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.virtual_media["Inserted"] = True
            self.virtual_media["Image"] = payload["Image"]
            self.virtual_media["WriteProtected"] = True

        def set_one_time_boot_cd(self, system_path=None):
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "before_uefi_target": "",
                "after_enabled": "Once",
                "after_target": "Cd",
                "after_uefi_target": "",
                "selected_boot_option_reference": "",
                "boot_option_selection_reason": "System did not expose a Redfish BootOptions collection.",
                "boot_option_inventory": {
                    "system_path": system_path or "/redfish/v1/Systems/1",
                    "boot": {
                        "enabled": "Disabled",
                        "target": "None",
                        "uefi_target": "",
                        "boot_order": [],
                        "boot_order_property_selection": "",
                    },
                    "boot_options_path": "",
                    "boot_options_count": 0,
                    "boot_options": [],
                    "oem_hpe_keys": [],
                    "oem_hpe_values": {},
                },
                "matched": True,
                "notes": ["One-time boot override read back exactly as requested."],
            }

    monkeypatch.setattr(main, "build_custom_iso", lambda spec: built_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 1})
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg, run_stamp="20260418-180000")
    job = main.load_job("Real ESXi Boot Fallback Kit")
    joined_logs = "\n".join(job["logs"])
    summary = yaml.safe_load(main.Path(job["run_summary_path"]).read_text(encoding="utf-8"))

    assert "No concrete UEFI virtual CD option found. Generic Cd override read back successfully, continuing." in joined_logs
    assert "BootOptions path:" not in joined_logs
    assert "HPE OEM boot values:" not in joined_logs
    assert summary["esxi_run_summary"]["boot_override"]["boot_option_selection_reason"] == "System did not expose a Redfish BootOptions collection."
    assert summary["esxi_run_summary"]["boot_override"]["boot_option_inventory"]["boot_options_count"] == 0
    assert job["status"] == "Completed"


def test_run_esxi_real_power_on_failure_populates_debug_diagnosis(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Power Failure Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["root_password"] = "Valid1Pass!"

    built_iso = tmp_path / "esxi-power-failure.iso"
    built_iso.write_text("iso", encoding="utf-8")

    class FakeEsxiILOClient:
        def __init__(self, cfg):
            self.cfg = cfg
            self.power_state = "Off"
            self.virtual_media = {
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "Image": "",
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }

        def get_virtual_media(self):
            return [dict(self.virtual_media)]

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {
                "PowerState": self.power_state,
                "Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Cd"},
                "BootProgress": {"LastState": "None"},
                "Oem": {"Hpe": {"PostState": "Off"}},
            }

        def _post(self, target, payload):
            self.virtual_media["Inserted"] = True
            self.virtual_media["Image"] = payload["Image"]
            self.virtual_media["WriteProtected"] = True

        def set_one_time_boot_cd(self, system_path=None):
            return {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "before_enabled": "Disabled",
                "before_target": "None",
                "after_enabled": "Once",
                "after_target": "Cd",
                "selected_boot_option_reference": "",
                "boot_option_selection_reason": "BootOptions were exposed, but none looked like a virtual CD/DVD boot option.",
                "boot_option_inventory": {
                    "system_path": system_path or "/redfish/v1/Systems/1",
                    "boot_options_path": "/redfish/v1/Systems/1/BootOptions",
                    "boot_options_count": 1,
                    "boot_options": [{"boot_option_reference": "Boot0001", "display_name": "UEFI Hard Disk"}],
                    "oem_hpe_keys": ["PostState", "VirtualInstallDisk"],
                    "oem_hpe_values": {"PostState": "Off", "VirtualInstallDisk": "Disabled"},
                },
                "matched": True,
                "notes": ["One-time boot override read back exactly as requested."],
            }

        def ensure_power_state(self, expected_state, *, system_path=None, timeout_seconds=300, poll_interval=5):
            if expected_state == "Off":
                return {"action": "skip", "changed": False, "final_power_state": "Off"}
            details = {
                "system_path": system_path or "/redfish/v1/Systems/1",
                "reset_target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                "allowed_reset_types": ["On", "ForceOff", "PushPowerButton"],
                "initial_power_state": "Off",
                "expected_power_state": "On",
                "final_power_state": "Off",
                "action": "On",
                "result": {"http_status_code": None, "message_ids": [], "connection_dropped": True, "attempt": "retry"},
                "first_observed_power_state": "Off",
                "last_observed_power_state": "Off",
                "poll_timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval,
                "retry_attempted": True,
                "fallback_attempted": True,
                "attempts": [
                    {
                        "reset_type": "On",
                        "result": {"http_status_code": None, "message_ids": [], "connection_dropped": True, "attempt": "first"},
                        "poll": {"matched": False, "first_observed": "Off", "last_observed": "Off"},
                    },
                    {
                        "reset_type": "On",
                        "result": {"http_status_code": None, "message_ids": [], "connection_dropped": True, "attempt": "retry"},
                        "poll": {"matched": False, "first_observed": "Off", "last_observed": "Off"},
                        "retry": True,
                    },
                    {
                        "reset_type": "PushPowerButton",
                        "result": {"http_status_code": 200, "message_ids": ["Base.1.18.Success"], "connection_dropped": False},
                        "poll": {"matched": False, "first_observed": "Off", "last_observed": "Off"},
                        "fallback": True,
                    },
                ],
            }
            error = ILOError("Power reset connection dropped and expected PowerState was not reached after retry.")
            error.power_reset_details = details
            raise error

    def fake_build_power_failure(spec):
        (built_iso.parent / "build-summary.yml").write_text(
            yaml.safe_dump(
                {
                    "generation": {
                        "ks_cfg": {
                            "generated": True,
                            "iso_path": "/KS.CFG",
                            "inspection_path": str(built_iso.parent / "inspection" / "KS.CFG"),
                            "redacted_preview_path": str(built_iso.parent / "KS.CFG.redacted.txt"),
                            "preview_redacted": "rootpw [REDACTED]\nreboot\n",
                            "debug_no_reboot": False,
                        }
                    },
                    "install_target": {"kickstart_line": "install --firstdisk --overwritevmfs", "mode": "firstdisk"},
                    "self_check": {"output_boot_report": {}, "output_files_present": {"ks_cfg": True}},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return built_iso

    monkeypatch.setattr(main, "build_custom_iso", fake_build_power_failure)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: fake_esxi_base_iso(tmp_path))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg, run_stamp="20260418-190000")
    job = main.load_job("Real ESXi Power Failure Kit")
    joined_logs = "\n".join(job["logs"])
    bundle_text = (main.DEBUG_BUNDLES_DIR / "latest-failure.txt").read_text(encoding="utf-8")

    assert job["status"] == "Failed"
    assert "endpoint=/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" in joined_logs
    assert "ResetType=On" in joined_logs
    assert "allowed=On,ForceOff,PushPowerButton" in joined_logs
    assert "message_ids=Base.1.18.Success" in joined_logs
    assert "connection_dropped=yes" in joined_logs
    assert "retry=yes" in joined_logs
    assert "push_button_fallback=yes" in joined_logs
    assert "No concrete UEFI virtual CD option found. Generic Cd override read back successfully, continuing." in joined_logs
    assert "HPE OEM boot values:" not in joined_logs
    assert job["diagnosis"]["status"] == "failed"
    assert job["diagnosis"]["discovered_state"]["virtual_media_image_matches"] is True
    assert job["diagnosis"]["discovered_state"]["boot_override_enabled"] == "Once"
    assert job["diagnosis"]["discovered_state"]["boot_override_target"] == "Cd"
    assert job["diagnosis"]["discovered_state"]["power_reset"]["retry_attempted"] is True
    assert job["diagnosis"]["discovered_state"]["power_reset"]["fallback_attempted"] is True
    assert "Retry ResetType=On" in job["diagnosis"]["recommended_fix"]
    assert "diagnosis:" in bundle_text
    assert "recommended_next_steps" in bundle_text
    assert "VirtualInstallDisk" in bundle_text
    assert "rootpw [REDACTED]" in bundle_text
    assert "install --firstdisk --overwritevmfs" in bundle_text
    assert "Valid1Pass!" not in bundle_text
    assert "secret" not in bundle_text


def test_harden_snmp_best_effort_reports_mismatch_when_readback_differs(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    before = {
        "SNMP": {
            "ProtocolEnabled": False,
            "SNMPv1Enabled": True,
            "SNMPv2cEnabled": True,
            "SNMPv3Enabled": False,
            "SNMPv3Username": "olduser",
            "SNMPv3AuthProtocol": "MD5",
            "SNMPv3PrivacyProtocol": "DES",
        }
    }
    after = {
        "SNMP": {
            "ProtocolEnabled": True,
            "SNMPv1Enabled": False,
            "SNMPv2cEnabled": False,
            "SNMPv3Enabled": True,
            "SNMPv3Username": "olduser",
            "SNMPv3AuthProtocol": "MD5",
            "SNMPv3PrivacyProtocol": "DES",
        }
    }

    monkeypatch.setattr(client, "get_network_protocol", lambda: ("/redfish/v1/Managers/1/NetworkProtocol", dict(before)))
    monkeypatch.setattr(client, "_patch", lambda path, payload: None)
    monkeypatch.setattr(client, "_get", lambda path: dict(after))

    result = client.harden_snmp_best_effort(
        v3_username="newuser",
        v3_auth_protocol="SHA",
        v3_auth_password="authpass",
        v3_priv_protocol="AES",
        v3_priv_password="privpass",
    )

    assert result["status"] == "Mismatch"
    assert result["verified"] is False
    assert result["verification"]["mismatches"]


def test_ensure_local_accounts_best_effort_creates_and_updates_accounts(monkeypatch):
    client = ILOClient(ILOConfig(host="10.0.0.1", username="Administrator", password="secret"))
    state = {
        "/redfish/v1": {"AccountService": {"@odata.id": "/redfish/v1/AccountService"}},
        "/redfish/v1/AccountService": {"Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}},
        "/redfish/v1/AccountService/Accounts": {
            "Members": [
                {"@odata.id": "/redfish/v1/AccountService/Accounts/1"},
            ]
        },
        "/redfish/v1/AccountService/Accounts/1": {
            "@odata.id": "/redfish/v1/AccountService/Accounts/1",
            "Id": "1",
            "UserName": "Administrator",
            "RoleId": "Administrator",
            "Enabled": True,
        },
    }

    def fake_get(path, timeout=None):
        del timeout
        path = path.rstrip("/") or "/"
        return dict(state[path])

    def fake_patch(path, payload):
        state[path].update(payload)

    def fake_post(path, payload=None):
        payload = payload or {}
        if path == "/redfish/v1/AccountService/Accounts":
            new_path = "/redfish/v1/AccountService/Accounts/2"
            state[new_path] = {
                "@odata.id": new_path,
                "Id": "2",
                "UserName": payload["UserName"],
                "RoleId": payload.get("RoleId", "Administrator"),
                "Enabled": payload.get("Enabled", True),
            }
            state[path]["Members"].append({"@odata.id": new_path})
        return {}

    monkeypatch.setattr(client, "_get", fake_get)
    monkeypatch.setattr(client, "_patch", fake_patch)
    monkeypatch.setattr(client, "_post", fake_post)

    result = client.ensure_local_accounts_best_effort([
        {"username": "Administrator", "password": "new-secret", "role": "Administrator"},
        {"username": "opsadmin", "password": "ops-secret", "role": "ReadOnly"},
    ])

    assert result["status"] == "Verified"
    assert result["matched"] is True
    assert [item["status"] for item in result["results"]] == ["Updated", "Created"]
    assert any(item["username"] == "opsadmin" for item in result["after"])


def test_prepare_execute_shows_storage_will_be_applied_in_real_run(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Exec Storage Real Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    cfg = main.load_kit_config("Exec-Storage-Real-Kit")
    main.approve_storage_plan_for_cfg(cfg, discovery, export_paths, plan, plan_paths, include_in_ilo_run=True)
    cfg["included"]["storage"] = True
    main.save_kit_config(cfg)

    response = client.post(
        "/prepare-execute",
        data={"scope": "included", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "Storage will be applied during the real run using the approved layout." in response.text
    assert "The approved storage plan will also be applied." in response.text


def test_execution_page_no_longer_shows_view_live_log(client):
    response = client.get("/execution")

    assert response.status_code == 200
    assert "View live log" not in response.text
    assert "Run checklist" in response.text
    assert "Technical log" in response.text
    assert 'id="execution-stage-checklist"' in response.text


def test_execution_page_shows_live_stage_details_from_job_state(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Live Stage Kit"
    main.save_kit_config(cfg)
    main.save_job(
        "Live Stage Kit",
        {
            "scope": "multi__ilo__storage__esxi",
            "status": "Running",
            "current_stage": "Applying iLO settings",
            "execution_mode": "real",
            "execution_mode_label": "Real execution",
            "progress_percent": 62,
            "completed_steps": 5,
            "total_steps": 8,
            "dns_apply_status": "Verified",
            "snmp_apply_status": "Verified",
            "ilo_reset_status": "Completed",
            "ilo_final_ip_verified": True,
            "target_ip": "10.10.8.30",
            "storage_server_reboot_status": "Completed",
            "workflow_state": "post_reboot_validation_complete",
            "esxi_iso_path": "/tmp/esxi.iso",
            "esxi_iso_url": "http://127.0.0.1:8000/esxi.iso",
            "esxi_expected_ip": "10.10.8.50",
            "esxi_trace_path": "/tmp/esxi-trace.yml",
            "logs": ["[OK] DNS saved on active iLO interface"],
        },
    )

    response = client.get("/execution")

    assert response.status_code == 200
    assert "Live stage details" in response.text
    assert "Show last confirmed checks" in response.text
    assert "DNS status" in response.text
    assert "SNMP status" in response.text
    assert "Final iLO IP" in response.text
    assert "Server reboot status" in response.text
    assert "Built ISO path" in response.text
    assert "Expected ESXi IP" in response.text


def test_ilo_page_shows_last_run_dns_snmp_and_reset_states(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "ILO Result Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    main.save_kit_config(cfg)
    main.append_history_entry(
        "ILO Result Kit",
        {
            "time": "2026-04-10 12:00:00",
            "scope": "ilo",
            "status": "Completed",
            "current_stage": "Finished",
            "progress_percent": 100,
            "completed_steps": 10,
            "total_steps": 10,
            "config_summary": {
                "dns_apply_status": "Applied",
                "dns_applied_values": ["1.1.1.1", "8.8.8.8"],
                "snmp_username": "snmpuser",
                "snmp_auth_protocol": "SHA",
                "snmp_priv_protocol": "AES",
                "snmp_auth_secret_present": True,
                "snmp_priv_secret_present": True,
                "snmp_apply_status": "Verified",
                "storage_server_reboot_status": "Completed",
                "ilo_reset_status": "Not requested separately",
            },
        },
    )

    response = client.get("/ilo")

    assert response.status_code == 200
    assert "iLO setup" in response.text
    assert "What happened last" in response.text
    assert "This run handled DNS applied, SNMP verified, iLO reset not requested separately, server reboot completed." in response.text
    assert "Open Run Center" in response.text


def test_history_page_shows_applied_dns_snmp_and_reset_states(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "History Result Kit"
    main.save_kit_config(cfg)
    main.append_history_entry(
        "History Result Kit",
        {
            "time": "2026-04-10 12:15:00",
            "scope": "ilo",
            "status": "Completed",
            "current_stage": "Finished",
            "progress_percent": 100,
            "completed_steps": 10,
            "total_steps": 10,
            "config_summary": {
                "dns_apply_status": "Verified",
                "dns_applied_values": ["1.1.1.1"],
                "snmp_apply_status": "Verified",
                "snmp_username": "snmpuser",
                "snmp_auth_protocol": "SHA",
                "snmp_priv_protocol": "AES",
                "snmp_auth_secret_present": True,
                "snmp_priv_secret_present": False,
                "storage_server_reboot_status": "Completed",
                "ilo_reset_status": "Completed",
            },
        },
    )

    response = client.get("/history")

    assert response.status_code == 200
    assert "Applied results" in response.text
    assert "DNS:" in response.text
    assert "SNMP:" in response.text
    assert "auth secret Yes" in response.text
    assert "privacy secret No" in response.text
    assert "Storage server reboot:" in response.text
    assert "iLO reset:" in response.text


def test_append_history_entry_marks_older_failed_storage_runs_as_superseded(tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Supersede Kit"
    main.save_kit_config(cfg)

    old_bundle = tmp_path / "old-run"
    old_bundle.mkdir(parents=True, exist_ok=True)
    old_bundle_summary = old_bundle / "summary.yml"
    old_bundle_summary.write_text(yaml.safe_dump({"status": "Failed"}), encoding="utf-8")
    old_report = tmp_path / "old-report.yml"
    old_report.write_text(yaml.safe_dump({"status": "Failed"}), encoding="utf-8")

    main.save_history(
        "Supersede-Kit",
        [
            {
                "time": "2026-05-05 10:10:00",
                "scope": "storage",
                "status": "Approved",
            },
            {
                "time": "2026-05-05 10:00:00",
                "scope": "storage-apply:wipe_rebuild",
                "status": "Failed",
                "current_stage": "Storage apply failed",
                "run_bundle_dir": str(old_bundle),
                "run_summary_path": str(old_report),
            }
        ],
    )

    main.append_history_entry(
        "Supersede-Kit",
        {
            "time": "2026-05-05 11:00:00",
            "scope": "storage-apply:wipe_rebuild",
            "status": "Completed",
            "current_stage": "Finished",
            "run_bundle_dir": str(tmp_path / "new-run"),
            "run_summary_path": str(tmp_path / "new-report.yml"),
        },
    )

    history = main.load_history("Supersede-Kit")
    assert history[0]["status"] == "Completed"
    assert history[1]["status"] == "Approved"
    assert history[2]["status"] == "Superseded"
    assert history[2]["original_status"] == "Failed"
    assert history[2]["superseded_by"]["by_time"] == "2026-05-05 11:00:00"

    old_bundle_payload = yaml.safe_load(old_bundle_summary.read_text(encoding="utf-8"))
    old_report_payload = yaml.safe_load(old_report.read_text(encoding="utf-8"))
    assert old_bundle_payload["status"] == "Superseded"
    assert old_report_payload["status"] == "Superseded"


def test_run_job_simulation_finishes_as_preview_complete():
    cfg = main.default_config()
    cfg["site"]["name"] = "Preview Job Kit"

    main.run_job_simulation(cfg, "windows")
    job = main.load_job("Preview Job Kit")

    assert job["execution_mode"] == "preview"
    assert job["execution_mode_label"] == "Preview / safety mode"
    assert job["status"] == "Preview complete"
    assert job["current_stage"] == "Ready for real execution"
    assert "[DONE] Preview complete. No real changes were made." in job["logs"]


def test_build_raid_plan_allows_data_layout_without_hot_spare():
    discovery = planner_discovery_without_data_spare()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }

    plan = main.build_raid_plan(discovery, discovery_paths)

    assert plan["data_raid6"]["drive_count"] == 4
    assert plan["hot_spare"]["reserved"] is False
    assert plan["apply_readiness"]["wipe_rebuild_ready"] is True
    assert plan["planned_layout"]["hot_spare"]["bay"] == ""
    assert plan["pre_apply_summary"]["planned_layout"]["data_raid6"]["bays"] == "3, 4, 5, 6"
    assert not any("hot spare" in blocker.lower() for blocker in plan["blockers"])


def test_plan_raid_layout_allows_five_drive_split_without_hot_spare(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260407-170300"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-07 17:03:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Five Drive Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.80"
    cfg["ip_plan"]["ilo"] = "10.10.8.80"
    cfg["ilo"]["current_ip"] = "10.10.8.80"
    cfg["ilo"]["host"] = "10.10.8.80"
    main.save_kit_config(cfg)
    discovery = planner_discovery_with_mixed_drives()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.80")

    response = client.post(
        "/plan-raid-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID5",
            "os_bays": ["1", "2"],
            "data_bays": ["3", "4", "5"],
            "hot_spare_bay": "",
        },
    )

    assert response.status_code == 200
    assert "No dedicated hot spare is selected for this plan." in response.text
    plan_payload = yaml.safe_load((export_paths["directory"] / "raid-plan.yml").read_text(encoding="utf-8"))
    plan = plan_payload["plan"]
    assert plan["valid"] is True
    assert plan["hot_spare"]["reserved"] is False
    assert plan["apply_readiness"]["wipe_rebuild_ready"] is True
    assert plan["planned_layout"]["data_raid6"]["raid"] == "RAID 5"


def test_build_raid_plan_allows_single_os_array_only():
    discovery = planner_discovery_with_mixed_drives()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }
    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "os_raid_level": "RAID1",
            "data_raid_level": "",
            "os_bays": ["1", "2"],
            "data_bays": [],
            "hot_spare_bay": "",
        },
    )

    assert plan["valid"] is True
    assert plan["planned_layout"]["os_raid1"]["raid"] == "RAID 1"
    assert plan["planned_layout"]["data_raid6"]["raid"] == "Not used"
    assert plan["planned_layout"]["data_raid6"]["bays"] == ""


def test_storage_drive_identity_falls_back_when_path_and_serial_missing():
    drive = {
        "source": "standard_redfish_storage",
        "controller_path": "/redfish/v1/Systems/1/Storage/DE009001",
        "id": "Disk.Bay.1",
        "bay": "1",
        "model": "VO001920RXUKC",
        "size_gib": 1788.5,
        "smart_storage_location": "",
    }
    identity = main.storage_drive_identity(drive)
    assert identity
    assert "id:Disk.Bay.1" in identity


def test_normalized_plan_drive_derives_controller_path_from_drive_path():
    drive = {
        "path": "/redfish/v1/Chassis/DE009001/Drives/4",
        "controller_path": "",
        "id": "4",
        "bay": "1",
        "name": "VK000240GXNWU",
        "model": "VK000240GXNWU",
        "serial_number": "254854A5AC8C",
        "size_gib": 223.57,
        "media_type": "SSD",
        "protocol": "NVMe",
        "status": "OK / Enabled",
    }
    normalized = main.normalized_plan_drive(drive, "standard_redfish_storage")
    assert normalized["controller_path"] == "/redfish/v1/Chassis/DE009001"
    assert normalized["drive_identity"] == "/redfish/v1/Chassis/DE009001/Drives/4"


def duplicate_bay_gen11_storage_discovery() -> dict:
    controller_path = "/redfish/v1/Systems/1/Storage/DE009001"
    drives = [
        ("4", "1", "VK000240GXNWU", "254854A5AC8C", 223.57),
        ("1", "1", "VO001920RXUKC", "LARGE-BAY1", 1788.5),
        ("3", "2", "VK000240GXNWU", "254854A5AB49", 223.57),
        ("2", "2", "VO001920RXUKC", "LARGE-BAY2", 1788.5),
        ("5", "3", "VO001920RXUKC", "LARGE-BAY3", 1788.5),
        ("6", "4", "VO001920RXUKC", "LARGE-BAY4", 1788.5),
        ("7", "5", "VO001920RXUKC", "LARGE-BAY5", 1788.5),
        ("8", "6", "VO001920RXUKC", "LARGE-BAY6", 1788.5),
        ("9", "7", "VO001920RXUKC", "LARGE-BAY7", 1788.5),
        ("10", "8", "VO001920RXUKC", "LARGE-BAY8", 1788.5),
    ]
    return {
        "summary": {
            "server": {"serial_number": "ABC123"},
            "standard_redfish_storage": {
                "controllers": [
                    {"path": controller_path, "name": "MR416i-o", "model": "MR416i-o"},
                ],
                "volumes": [],
                "drives": [
                    {
                        "path": f"/redfish/v1/Chassis/DE009001/Drives/{drive_id}",
                        "controller_path": controller_path,
                        "bay": bay,
                        "name": model,
                        "model": model,
                        "serial_number": serial,
                        "size_gib": size_gib,
                        "media_type": "SSD",
                        "protocol": "NVMe",
                        "status": "OK / Enabled",
                    }
                    for drive_id, bay, model, serial, size_gib in drives
                ],
            },
            "hpe_smart_storage": {"controllers": [], "volumes": [], "drives": []},
        },
        "raw": {"source_host": "10.10.8.80"},
    }


def two_controller_gen11_storage_discovery() -> dict:
    os_controller = "/redfish/v1/Systems/1/Storage/BOOT"
    data_controller = "/redfish/v1/Systems/1/Storage/DATA"
    return {
        "summary": {
            "server": {
                "serial_number": "ABC123",
                "model": "ProLiant DL380 Gen11",
                "generation": "Gen11",
            },
            "standard_redfish_storage": {
                "controllers": [
                    {"path": os_controller, "name": "HPE MR416i-p Gen11", "model": "MR416i-p Gen11"},
                    {"path": data_controller, "name": "HPE MR416i-o Gen11", "model": "MR416i-o Gen11"},
                ],
                "volumes": [],
                "drives": [
                    {
                        "path": f"/redfish/v1/Chassis/BOOT/Drives/{drive_id}",
                        "controller_path": os_controller,
                        "bay": bay,
                        "name": "VK000240GXNWU",
                        "model": "VK000240GXNWU",
                        "serial_number": serial,
                        "size_gib": 223.57,
                        "media_type": "SSD",
                        "protocol": "NVMe",
                        "status": "OK / Enabled",
                    }
                    for drive_id, bay, serial in [("1", "1", "OS-SERIAL-1"), ("2", "2", "OS-SERIAL-2")]
                ] + [
                    {
                        "path": f"/redfish/v1/Chassis/DATA/Drives/{drive_id}",
                        "controller_path": data_controller,
                        "bay": bay,
                        "name": "VO001920RXUKC",
                        "model": "VO001920RXUKC",
                        "serial_number": f"DATA-SERIAL-{bay}",
                        "size_gib": 1788.5,
                        "media_type": "SSD",
                        "protocol": "SAS",
                        "status": "OK / Enabled",
                    }
                    for drive_id, bay in [("3", "3"), ("4", "4"), ("5", "5"), ("6", "6"), ("7", "7"), ("8", "8")]
                ],
            },
            "hpe_smart_storage": {"controllers": [], "volumes": [], "drives": []},
        },
        "raw": {"source_host": "10.10.8.80"},
    }


def test_build_raid_plan_supports_os_and_data_on_different_controllers():
    discovery = two_controller_gen11_storage_discovery()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }

    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "os_controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "data_controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_ids": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "data_drive_ids": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
            "hot_spare_drive_id": "",
        },
    )

    assert plan["valid"] is True
    assert plan["customization"]["selected_os_controller_path"] == "/redfish/v1/Systems/1/Storage/BOOT"
    assert plan["customization"]["selected_data_controller_path"] == "/redfish/v1/Systems/1/Storage/DATA"
    assert {drive["controller_path"] for drive in plan["os_raid1"]["drives"]} == {"/redfish/v1/Systems/1/Storage/BOOT"}
    assert {drive["controller_path"] for drive in plan["data_raid6"]["drives"]} == {"/redfish/v1/Systems/1/Storage/DATA"}
    assert [array["role"] for array in plan["arrays"]] == ["os", "data"]
    assert plan["arrays"][0]["selected_drive_ids"] == [
        "/redfish/v1/Chassis/BOOT/Drives/1",
        "/redfish/v1/Chassis/BOOT/Drives/2",
    ]
    assert plan["arrays"][1]["selected_drive_ids"] == [
        "/redfish/v1/Chassis/DATA/Drives/3",
        "/redfish/v1/Chassis/DATA/Drives/4",
        "/redfish/v1/Chassis/DATA/Drives/5",
        "/redfish/v1/Chassis/DATA/Drives/6",
        "/redfish/v1/Chassis/DATA/Drives/7",
        "/redfish/v1/Chassis/DATA/Drives/8",
    ]
    assert any("multi-controller layout" in item for item in plan["profile_advisories"])
    assert plan["hot_spare"]["reserved"] is False


def test_choose_storage_apply_platform_blocks_unverified_multi_controller_standard_redfish_create_before_delete():
    discovery = two_controller_gen11_storage_discovery()
    discovery["summary"]["capabilities"] = {
        "standard_redfish_storage": True,
        "standard_redfish_volume_create_verified": False,
        "hpe_smart_storage": False,
        "standard_storage_path": "/redfish/v1/Systems/1/Storage",
        "hpe_smart_storage_paths": [],
        "hpe_smart_storage_diagnostics": {"probed_paths": [], "collections": [], "warnings": [], "deep_scan_requested": False, "deep_fallback_ran": False},
    }
    discovery["raw"] = {
        "source_host": "10.10.8.80",
        "standard_storage": [
            {
                "@odata.id": "/redfish/v1/Systems/1/Storage/BOOT",
                "Volumes": {"@odata.id": "/redfish/v1/Systems/1/Storage/BOOT/Volumes"},
                "Actions": {"#Storage.ResetToDefaults": {"target": "/redfish/v1/Systems/1/Storage/BOOT/Actions/Storage.ResetToDefaults"}},
            },
            {
                "@odata.id": "/redfish/v1/Systems/1/Storage/DATA",
                "Volumes": {"@odata.id": "/redfish/v1/Systems/1/Storage/DATA/Volumes"},
                "Actions": {"#Storage.ResetToDefaults": {"target": "/redfish/v1/Systems/1/Storage/DATA/Actions/Storage.ResetToDefaults"}},
            },
        ],
    }
    export_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }
    plan = main.build_raid_plan(
        discovery,
        export_paths,
        overrides={
            "os_controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "data_controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "data_drive_paths": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
        },
    )

    platform = main.choose_storage_apply_platform(discovery, plan)

    assert platform["supported"] is False
    assert platform["id"] == "standard_redfish_create_unverified"
    assert "Blocking before delete" in platform["reason"]


def test_build_raid_plan_blocks_array_spanning_multiple_controllers():
    discovery = two_controller_gen11_storage_discovery()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }

    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "os_raid_level": "RAID1",
            "data_raid_level": "",
            "os_drive_ids": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/DATA/Drives/3",
            ],
        },
    )

    assert plan["valid"] is False
    assert any("OS array cannot span multiple storage controllers" in blocker for blocker in plan["blockers"])


def test_build_raid_plan_does_not_auto_select_standby_spare_or_absent_drives():
    discovery = two_controller_gen11_storage_discovery()
    discovery["summary"]["standard_redfish_storage"]["drives"][2]["status"] = "OK / StandbySpare"
    discovery["summary"]["standard_redfish_storage"]["drives"][3]["status"] = "Absent"
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }

    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "os_controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "data_controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
        },
    )

    selected_paths = {drive["path"] for drive in plan["os_raid1"]["drives"] + plan["data_raid6"]["drives"]}
    assert "/redfish/v1/Chassis/DATA/Drives/3" not in selected_paths
    assert "/redfish/v1/Chassis/DATA/Drives/4" not in selected_paths
    assert plan["hot_spare"]["drive"] == {}
    assert any("standby spare" in item.lower() for item in [drive["exclude_reason"] for drive in plan["excluded_drives"]])


def test_validate_storage_apply_request_allows_multi_array_multi_controller_plan():
    discovery = two_controller_gen11_storage_discovery()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }
    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "os_controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "data_controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "data_drive_paths": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
        },
    )

    main.validate_storage_apply_request(plan, "wipe_rebuild", main.STORAGE_APPLY_CONFIRM_WIPE, True)


def test_validate_storage_apply_request_rejects_single_array_mixed_controller_drives():
    discovery = two_controller_gen11_storage_discovery()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }
    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "os_controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "data_raid_level": "",
            "os_drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/DATA/Drives/3",
            ],
        },
    )

    with pytest.raises(ValueError, match="cannot span multiple storage controllers|but OS array is set to"):
        main.validate_storage_apply_request(plan, "wipe_rebuild", main.STORAGE_APPLY_CONFIRM_WIPE, True)


def test_storage_page_hides_controller_selectors_for_single_controller(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260430-121000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-30 12:10:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Single Controller Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.80"
    cfg["ip_plan"]["ilo"] = "10.10.8.80"
    cfg["ilo"]["current_ip"] = "10.10.8.80"
    cfg["ilo"]["host"] = "10.10.8.80"
    main.save_kit_config(cfg)
    discovery = duplicate_bay_gen11_storage_discovery()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.80")

    response = client.post(
        "/plan-raid-layout",
        data={"return_page": "storage", "discovery_raw_path": str(export_paths["raw"])},
    )

    assert response.status_code == 200
    assert "OS array controller" not in response.text
    assert "Data array controller" not in response.text
    assert 'name="os_controller_path"' in response.text
    assert 'name="data_controller_path"' in response.text


def test_build_raid_plan_uses_drive_identities_when_bays_are_duplicated():
    discovery = duplicate_bay_gen11_storage_discovery()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }

    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "controller_path": "/redfish/v1/Systems/1/Storage/DE009001",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_ids": [
                "/redfish/v1/Chassis/DE009001/Drives/4",
                "/redfish/v1/Chassis/DE009001/Drives/3",
            ],
            "data_drive_ids": [
                "/redfish/v1/Chassis/DE009001/Drives/5",
                "/redfish/v1/Chassis/DE009001/Drives/6",
                "/redfish/v1/Chassis/DE009001/Drives/7",
                "/redfish/v1/Chassis/DE009001/Drives/8",
                "/redfish/v1/Chassis/DE009001/Drives/9",
                "/redfish/v1/Chassis/DE009001/Drives/10",
            ],
            "hot_spare_drive_id": "",
        },
    )

    assert plan["valid"] is True
    assert plan["customization"]["selected_controller_path"] == "/redfish/v1/Systems/1/Storage/DE009001"
    assert plan["customization"]["selected_os_drive_ids"] == [
        "/redfish/v1/Chassis/DE009001/Drives/4",
        "/redfish/v1/Chassis/DE009001/Drives/3",
    ]
    assert plan["planned_layout"]["os_raid1"]["bays"] == "1, 2"
    assert plan["os_raid1"]["drives"][0]["model"] == "VK000240GXNWU"
    assert plan["os_raid1"]["drives"][1]["model"] == "VK000240GXNWU"
    assert plan["planned_layout"]["data_raid6"]["bays"] == "3, 4, 5, 6, 7, 8"
    assert plan["hot_spare"]["reserved"] is False
    assert any("Duplicate bay numbers detected" in warning for warning in plan["warnings"])


def test_build_raid_plan_blocks_reused_drive_identity():
    discovery = duplicate_bay_gen11_storage_discovery()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }

    plan = main.build_raid_plan(
        discovery,
        discovery_paths,
        overrides={
            "controller_path": "/redfish/v1/Systems/1/Storage/DE009001",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_ids": [
                "/redfish/v1/Chassis/DE009001/Drives/4",
                "/redfish/v1/Chassis/DE009001/Drives/3",
            ],
            "data_drive_ids": [
                "/redfish/v1/Chassis/DE009001/Drives/3",
                "/redfish/v1/Chassis/DE009001/Drives/5",
                "/redfish/v1/Chassis/DE009001/Drives/6",
                "/redfish/v1/Chassis/DE009001/Drives/7",
            ],
        },
    )

    assert plan["valid"] is False
    assert any("same drive" in blocker.lower() or "same drive identity" in blocker.lower() for blocker in plan["blockers"])


def test_storage_approval_preserves_selected_drive_identities(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260430-120000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-30 12:00:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Duplicate Bay Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.80"
    cfg["ip_plan"]["ilo"] = "10.10.8.80"
    cfg["ilo"]["current_ip"] = "10.10.8.80"
    cfg["ilo"]["host"] = "10.10.8.80"
    main.save_kit_config(cfg)
    discovery = duplicate_bay_gen11_storage_discovery()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.80")

    plan_response = client.post(
        "/plan-raid-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "controller_path": "/redfish/v1/Systems/1/Storage/DE009001",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_ids": [
                "/redfish/v1/Chassis/DE009001/Drives/4",
                "/redfish/v1/Chassis/DE009001/Drives/3",
            ],
            "data_drive_ids": [
                "/redfish/v1/Chassis/DE009001/Drives/5",
                "/redfish/v1/Chassis/DE009001/Drives/6",
                "/redfish/v1/Chassis/DE009001/Drives/7",
                "/redfish/v1/Chassis/DE009001/Drives/8",
                "/redfish/v1/Chassis/DE009001/Drives/9",
                "/redfish/v1/Chassis/DE009001/Drives/10",
            ],
            "hot_spare_drive_id": "",
        },
    )

    assert plan_response.status_code == 200
    plan_path = export_paths["directory"] / "raid-plan.yml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))["plan"]
    assert plan["customization"]["selected_os_drive_ids"] == [
        "/redfish/v1/Chassis/DE009001/Drives/4",
        "/redfish/v1/Chassis/DE009001/Drives/3",
    ]

    approve_response = client.post(
        "/approve-storage-plan",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_path),
            "include_in_ilo_run": "on",
        },
    )

    assert approve_response.status_code == 200
    assert "Storage approved" in approve_response.text
    approved_cfg = main.load_kit_config("Duplicate-Bay-Kit")
    approved_plan_path = main.Path(approved_cfg["storage"]["approval"]["plan_path"])
    approved_plan = yaml.safe_load(approved_plan_path.read_text(encoding="utf-8"))["plan"]
    assert approved_plan["customization"]["selected_os_drive_ids"] == [
        "/redfish/v1/Chassis/DE009001/Drives/4",
        "/redfish/v1/Chassis/DE009001/Drives/3",
    ]
    assert approved_plan["arrays"][0]["selected_drive_ids"] == [
        "/redfish/v1/Chassis/DE009001/Drives/4",
        "/redfish/v1/Chassis/DE009001/Drives/3",
    ]
    assert approved_plan["customization"]["selected_data_drive_ids"] == [
        "/redfish/v1/Chassis/DE009001/Drives/5",
        "/redfish/v1/Chassis/DE009001/Drives/6",
        "/redfish/v1/Chassis/DE009001/Drives/7",
        "/redfish/v1/Chassis/DE009001/Drives/8",
        "/redfish/v1/Chassis/DE009001/Drives/9",
        "/redfish/v1/Chassis/DE009001/Drives/10",
    ]
    assert approved_plan["arrays"][1]["selected_drive_ids"] == [
        "/redfish/v1/Chassis/DE009001/Drives/5",
        "/redfish/v1/Chassis/DE009001/Drives/6",
        "/redfish/v1/Chassis/DE009001/Drives/7",
        "/redfish/v1/Chassis/DE009001/Drives/8",
        "/redfish/v1/Chassis/DE009001/Drives/9",
        "/redfish/v1/Chassis/DE009001/Drives/10",
    ]
    assert approved_plan["hot_spare"]["drive"] == {}


def test_plan_raid_layout_rejects_discovery_from_different_host(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Plan Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.81"
    cfg["ilo"]["host"] = "10.10.8.81"
    main.save_kit_config(cfg)
    discovery = planner_discovery_with_mixed_drives()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.82")

    response = client.post(
        "/plan-raid-layout",
        data={"return_page": "storage", "discovery_raw_path": str(export_paths["raw"])},
    )

    assert response.status_code == 200
    assert "RAID planning failed" in response.text


def test_plan_raid_layout_blocks_when_storage_host_is_missing():
    cfg = main.default_config()
    cfg["ilo"]["target_ip"] = ""
    cfg["ip_plan"]["ilo"] = ""
    cfg["ilo"]["current_ip"] = ""
    cfg["ilo"]["host"] = ""
    resolved = main.resolve_storage_target_host(cfg)

    assert resolved["valid"] is False
    assert resolved["source"] == ""
    assert "planned iLO IP" in resolved["error"]


def test_apply_storage_layout_blocks_create_only_when_not_ready(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    response = client.post(
        "/apply-storage-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_mode": "create_only",
            "acknowledge_apply": "on",
            "typed_confirmation": "CREATE STORAGE",
        },
    )

    assert response.status_code == 200
    assert "Storage apply failed" in response.text
    assert "Create-only apply is not ready" in response.text


def test_apply_storage_layout_blocks_wipe_rebuild_without_confirmation(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    response = client.post(
        "/apply-storage-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_mode": "wipe_rebuild",
            "acknowledge_apply": "on",
            "typed_confirmation": "WRONG VALUE",
        },
    )

    assert response.status_code == 200
    assert "Storage apply failed" in response.text
    assert "requires the exact confirmation string" in response.text


def test_apply_storage_layout_creates_artifacts_and_logs_success(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260409-130000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-09 13:00:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    monkeypatch.setattr(main, "ILOClient", lambda cfg: FakeGen10StorageApplyClient(cfg))
    monkeypatch.setattr(
        main,
        "start_storage_apply_background",
        lambda cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths: main.execute_storage_apply_in_background(
            cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths
        ),
    )

    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    response = client.post(
        "/apply-storage-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_mode": "wipe_rebuild",
            "acknowledge_apply": "on",
            "typed_confirmation": "WIPE STORAGE",
        },
    )

    assert response.status_code == 200
    apply_dir = main.STORAGE_RAID_EXPORT_DIR / "MXQ85103SX" / "20260409-130000"
    assert (apply_dir / "pre-change-summary.yml").exists()
    assert (apply_dir / "pre-change-raw.json").exists()
    assert (apply_dir / "raid-plan.yml").exists()
    assert (apply_dir / "apply-log.yml").exists()
    assert (apply_dir / "apply-results.json").exists()
    assert (apply_dir / "post-change-summary.yml").exists()
    assert (apply_dir / "post-change-raw.json").exists()
    apply_log_text = (apply_dir / "apply-log.yml").read_text(encoding="utf-8")
    apply_results_text = (apply_dir / "apply-results.json").read_text(encoding="utf-8")
    job = main.load_job("Apply-Kit")
    assert job["scope"] == "storage-apply:wipe_rebuild"
    assert job["status"] == "Staged"
    assert job["current_stage"] == "Reboot required"
    assert job["completed_steps"] == 10
    assert job["total_steps"] == 15
    assert job["progress_percent"] < 100
    assert job["progress_percent"] == 68
    assert any("Validate controller and plan" in line for line in job["logs"])
    assert any("Export pre-change storage" in line for line in job["logs"])
    assert any("Create OS RAID 1 logical drive" in line for line in job["logs"])
    assert any("Create Data RAID 6 logical drive" in line for line in job["logs"])
    assert any("Assign hot spare" in line for line in job["logs"])
    assert any("Poll controller/apply status" in line for line in job["logs"])
    assert any("Determine whether reboot is required" in line for line in job["logs"])
    assert any("Export post-change storage" in line for line in job["logs"])
    assert "Delete existing logical volume" in apply_log_text
    assert "Create OS RAID 1 logical drive" in apply_log_text
    assert "Assign hot spare" in apply_log_text
    assert "\"status\": \"Staged\"" in apply_results_text
    assert "\"workflow_state\": \"staged_reboot_required\"" in apply_results_text


def test_choose_storage_apply_platform_rejects_guessed_smartstorageconfig_settings_path():
    discovery = planner_gen10_plus_hpe_inventory_without_settings_path()
    export_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }
    plan = main.build_raid_plan(discovery, export_paths)

    platform = main.choose_storage_apply_platform(discovery, plan)

    assert platform["supported"] is False
    assert platform["id"] == "hpe_smart_storage_read_only"
    assert platform["settings_path"] == ""
    assert "no writable SmartStorageConfig settings URI was verified" in platform["reason"]


def test_run_storage_as_part_of_real_run_fails_early_when_only_read_only_hpe_inventory_exists(monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Lab-Uplands-G10"
    cfg["ilo"]["current_ip"] = "10.10.8.110"
    cfg["ilo"]["target_ip"] = "10.10.8.110"
    cfg["ilo"]["host"] = "10.10.8.110"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=False)
    discovery["summary"]["server"]["model"] = "ProLiant DL360 Gen10 Plus"
    discovery["summary"]["server"]["generation"] = "Gen10+"
    diagnostics = discovery["summary"]["capabilities"]["hpe_smart_storage_diagnostics"]
    diagnostics["probed_paths"] = [
        {
            "phase": "fast_pass",
            "path": "/redfish/v1/Systems/1/SmartStorage",
            "status": "ok",
            "exists": True,
            "error": "",
            "name": "HpeSmartStorage",
            "members": 0,
        },
        {
            "phase": "fast_pass",
            "path": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers",
            "status": "ok",
            "exists": True,
            "error": "",
            "name": "HpeSmartStorageArrayControllers",
            "members": 0,
        },
        {
            "phase": "fast_pass",
            "path": "/redfish/v1/Systems/1/SmartStorageConfig",
            "status": "error",
            "exists": False,
            "error": "404 ResourceMissingAtURI",
            "name": "",
            "members": 0,
        },
        {
            "phase": "fast_pass",
            "path": "/redfish/v1/Systems/1/SmartStorageConfig/Settings",
            "status": "error",
            "exists": False,
            "error": "404 ResourceMissingAtURI",
            "name": "",
            "members": 0,
        },
    ]
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.110")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    storage_execution = {
        "discovery_raw_path": str(export_paths["raw"]),
        "plan_path": str(plan_paths["plan"]),
        "approved_host": "10.10.8.110",
    }
    job = {
        "status": "Running",
        "scope": "multi__ilo__storage__esxi",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": 32,
        "logs": [],
    }
    main.save_job(cfg["site"]["name"], job)

    class FakeClient:
        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            assert system_path == "/redfish/v1/Systems/1"
            return {"PowerState": "On"}

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            return {"reset_type": reset_type, "system_path": system_path, "path": f"{system_path}/Actions/ComputerSystem.Reset"}

        def get_storage_discovery(self, deep_smart_storage_scan=False):
            return discovery

    with pytest.raises(ILOError) as exc:
        main.run_storage_as_part_of_real_run(
            cfg,
            FakeClient(),
            "10.10.8.110",
            "10.10.8.110",
            storage_execution,
            cfg["site"]["name"],
            job,
            17,
            32,
        )

    text = str(exc.value)
    assert "Storage apply requires server power On and a writable Redfish Volumes path. Current path is inventory-only." in text
    assert "Recommended fix" in text
    failed_job = main.load_job(cfg["site"]["name"])
    assert failed_job["status"] == "Failed"
    assert failed_job["current_stage"] == "Choose storage apply path"
    assert any("[FAILED] Choose storage apply path" in line for line in failed_job["logs"])
    assert any("[DISCOVER] Storage preflight options discovered" in line for line in failed_job["logs"])
    assert any("[BLOCKED] Storage preflight rejected" in line for line in failed_job["logs"])


def test_load_job_normalizes_stale_running_complete_state():
    kit_name = "Stale Complete Kit"
    main.save_job(
        kit_name,
        {
            "status": "Running",
            "scope": "multi__ilo__storage__esxi",
            "current_stage": "Post-reboot validation",
            "progress_percent": 100,
            "completed_steps": 32,
            "total_steps": 32,
            "logs": ["[SKIP] Post-reboot validation | No storage reboot was required."],
        },
    )

    job = main.load_job(kit_name)

    assert job["status"] == "Completed"
    assert job["current_stage"] == "Post-reboot validation"
    assert any("marking stale running state as completed" in line for line in job["logs"])


def test_ilo_get_retries_once_after_connection_abort():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    calls = []

    class Response:
        status_code = 200
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    def fake_request(method, url, *, timeout=None, json_payload=None):
        del timeout, json_payload
        calls.append((method, url))
        if len(calls) == 1:
            raise requests.ConnectionError("connection aborted")
        return Response()

    client._request = fake_request

    assert client._get("/redfish/v1/Systems/1") == {"ok": True}
    assert len(calls) == 2


def test_ilo_delete_retries_once_after_connection_abort():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    calls = []

    class Response:
        status_code = 200
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    def fake_request(method, url, *, timeout=None, json_payload=None):
        del timeout, json_payload
        calls.append((method, url))
        if len(calls) == 1:
            raise requests.ConnectionError("connection aborted")
        return Response()

    client._request = fake_request

    assert client._delete("/redfish/v1/Systems/1/Storage/DE00A000/Volumes/1") == {"ok": True}
    assert len(calls) == 2


def test_ilo_post_does_not_retry_after_connection_abort():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    calls = []

    def fake_request(method, url, *, timeout=None, json_payload=None):
        del timeout, json_payload
        calls.append((method, url))
        raise requests.ConnectionError("connection aborted")

    client._request = fake_request

    with pytest.raises(ILOError) as exc:
        client._post("/redfish/v1/Systems/1/Storage/DE00A000/Volumes", payload={"RAIDType": "RAID1"})
    assert "failed:" in str(exc.value)
    assert len(calls) == 1


def test_ilo_patch_retries_once_after_connection_abort():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    calls = []

    class Response:
        status_code = 200
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    def fake_request(method, url, *, timeout=None, json_payload=None):
        del timeout
        calls.append((method, url, json_payload))
        if len(calls) == 1:
            raise requests.ConnectionError("connection aborted")
        return Response()

    client._request = fake_request

    assert client._patch("/redfish/v1/Managers/1/NetworkProtocol", {"HostName": "ilo01"}) == {"ok": True}
    assert len(calls) == 2


def test_ilo_put_retries_once_after_connection_abort():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    calls = []

    class Response:
        status_code = 200
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    def fake_request(method, url, *, timeout=None, json_payload=None):
        del timeout
        calls.append((method, url, json_payload))
        if len(calls) == 1:
            raise requests.ConnectionError("connection aborted")
        return Response()

    client._request = fake_request

    assert client._put("/redfish/v1/Systems/1/Bios/Settings", {"BootMode": "Uefi"}) == {"ok": True}
    assert len(calls) == 2


def test_ilo_power_reset_recovers_after_disconnect_when_power_reaches_expected_state(monkeypatch):
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    monkeypatch.setattr(ilo_module.time, "sleep", lambda _: None)
    system_states = iter(
        [
            {"PowerState": "On", "Actions": {"#ComputerSystem.Reset": {"target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"}}},
            {"PowerState": "On"},
            {"PowerState": "Off"},
        ]
    )

    def fake_get(path: str, timeout=None):
        del timeout
        if path == "/redfish/v1/Systems":
            return {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]}
        if path == "/redfish/v1/Systems/1":
            return next(system_states)
        raise AssertionError(path)

    def fake_post(path: str, payload: dict | None = None):
        del payload
        raise ILOError(f"POST {path} failed: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")

    client._get = fake_get
    client._post = fake_post

    result = client.power_reset(reset_type="GracefulShutdown", system_path="/redfish/v1/Systems/1")
    assert result["recovered_after_transport_disconnect"] is True
    assert result["recovery_power_state"] == "Off"


def test_ilo_power_reset_disconnect_fails_when_expected_state_not_reached(monkeypatch):
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    monkeypatch.setattr(ilo_module.time, "sleep", lambda _: None)
    ticks = {"now": 0}
    monkeypatch.setattr(ilo_module.time, "time", lambda: ticks.__setitem__("now", ticks["now"] + 30) or ticks["now"])
    system_states = iter(
        [
            {"PowerState": "On", "Actions": {"#ComputerSystem.Reset": {"target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"}}},
            {"PowerState": "On"},
            {"PowerState": "On"},
            {"PowerState": "On"},
        ]
    )

    def fake_get(path: str, timeout=None):
        del timeout
        if path == "/redfish/v1/Systems":
            return {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]}
        if path == "/redfish/v1/Systems/1":
            return next(system_states, {"PowerState": "On"})
        raise AssertionError(path)

    def fake_post(path: str, payload: dict | None = None):
        del payload
        raise ILOError(f"POST {path} failed: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")

    client._get = fake_get
    client._post = fake_post

    with pytest.raises(ILOError, match="expected power state was not reached"):
        client.power_reset(reset_type="GracefulShutdown", system_path="/redfish/v1/Systems/1")


def test_ilo_power_reset_does_not_hide_http_401_errors():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))

    def fake_get(path: str, timeout=None):
        del timeout
        if path == "/redfish/v1/Systems":
            return {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]}
        if path == "/redfish/v1/Systems/1":
            return {"PowerState": "On", "Actions": {"#ComputerSystem.Reset": {"target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"}}}
        raise AssertionError(path)

    def fake_post(path: str, payload: dict | None = None):
        del payload
        raise ILOError(f"POST {path} failed with HTTP 401: Base.1.18.NoValidSession")

    client._get = fake_get
    client._post = fake_post

    with pytest.raises(ILOError, match="HTTP 401"):
        client.power_reset(reset_type="GracefulShutdown", system_path="/redfish/v1/Systems/1")


def test_ilo_power_reset_uses_action_metadata_and_returns_allowed_types():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))

    def fake_get(path: str, timeout=None):
        del timeout
        if path == "/redfish/v1/Systems/1":
            return {
                "PowerState": "On",
                "Actions": {
                    "#ComputerSystem.Reset": {
                        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                        "ResetType@Redfish.AllowableValues": ["On", "ForceOff", "PushPowerButton"],
                    }
                },
            }
        raise AssertionError(path)

    post_calls = []

    def fake_post(path: str, payload: dict | None = None):
        post_calls.append((path, payload))
        return {"ok": True}

    client._get = fake_get
    client._post = fake_post

    result = client.power_reset(reset_type="ForceOff", system_path="/redfish/v1/Systems/1")
    assert post_calls == [("/redfish/v1/Systems/1/Actions/ComputerSystem.Reset", {"ResetType": "ForceOff"})]
    assert result["allowed_reset_types"] == ["On", "ForceOff", "PushPowerButton"]


def test_ilo_power_reset_rejects_disallowed_reset_type():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))

    def fake_get(path: str, timeout=None):
        del timeout
        if path == "/redfish/v1/Systems/1":
            return {
                "PowerState": "On",
                "Actions": {
                    "#ComputerSystem.Reset": {
                        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                        "ResetType@Redfish.AllowableValues": ["On", "ForceOff"],
                    }
                },
            }
        raise AssertionError(path)

    client._get = fake_get
    client._post = lambda path, payload=None: {"ok": True}

    with pytest.raises(ILOError, match="not allowed"):
        client.power_reset(reset_type="GracefulShutdown", system_path="/redfish/v1/Systems/1")


def test_ilo_power_reset_graceful_restart_disconnect_recovers_when_final_on(monkeypatch):
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    calls = {"get": 0}

    def fake_get(path: str, timeout=None):
        del timeout
        if path == "/redfish/v1/Systems/1":
            calls["get"] += 1
            return {
                "PowerState": "On",
                "Actions": {
                    "#ComputerSystem.Reset": {
                        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                        "ResetType@Redfish.AllowableValues": ["GracefulRestart", "ForceRestart", "On"],
                    }
                },
            }
        raise AssertionError(path)

    def fake_post(path: str, payload: dict | None = None):
        assert path == "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
        assert payload == {"ResetType": "GracefulRestart"}
        raise ILOError("Connection aborted: RemoteDisconnected('Remote end closed connection without response')")

    client._get = fake_get
    client._post = fake_post
    monkeypatch.setattr(ilo_module.time, "sleep", lambda _: None)

    result = client.power_reset(reset_type="GracefulRestart", system_path="/redfish/v1/Systems/1")

    assert result["expected_final_power_state"] == "On"
    assert result["recovered_after_transport_disconnect"] is True
    assert result["first_observed_power_state"] == "On"
    assert result["final_power_state"] == "On"
    assert result["final_state_matched_expected"] is True


def test_ilo_power_reset_graceful_restart_expected_state_is_never_unknown():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))

    def fake_get(path: str, timeout=None):
        del timeout
        if path == "/redfish/v1/Systems/1":
            return {
                "PowerState": "On",
                "Actions": {
                    "#ComputerSystem.Reset": {
                        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                        "ResetType@Redfish.AllowableValues": ["GracefulRestart"],
                    }
                },
            }
        raise AssertionError(path)

    client._get = fake_get
    client._post = lambda path, payload=None: {"ok": True}

    result = client.power_reset(reset_type="GracefulRestart", system_path="/redfish/v1/Systems/1")
    assert result["expected_final_power_state"] == "On"
    assert "unknown" not in str(result.get("expected_final_power_state", "")).lower()


def test_ensure_power_state_off_skips_when_already_off():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))

    client.get_reset_action_metadata = lambda system_path=None: {
        "system_path": "/redfish/v1/Systems/1",
        "power_state": "Off",
        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        "allowed_reset_types": ["On", "ForceOff", "PushPowerButton"],
    }
    result = client.ensure_power_state("Off", system_path="/redfish/v1/Systems/1")
    assert result["changed"] is False
    assert result["action"] == "skip"


def test_ensure_power_state_on_powers_on_when_off(monkeypatch):
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    state = {"value": "Off"}
    client.get_reset_action_metadata = lambda system_path=None: {
        "system_path": "/redfish/v1/Systems/1",
        "power_state": state["value"],
        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        "allowed_reset_types": ["On", "ForceOff", "PushPowerButton"],
    }
    client._submit_reset_action = lambda system_path, target, reset_type: state.__setitem__("value", "On") or {"reset_type": reset_type, "http_status_code": 200, "message_ids": ["Base.1.18.Success"], "connection_dropped": False}
    client.get_power_state = lambda system_path=None: state["value"]
    monkeypatch.setattr(ilo_module.time, "sleep", lambda _: None)
    result = client.ensure_power_state("On", system_path="/redfish/v1/Systems/1", timeout_seconds=5, poll_interval=1)
    assert result["action"] == "On"
    assert result["final_power_state"] == "On"
    assert result["result"]["http_status_code"] == 200
    assert "Base.1.18.Success" in result["result"]["message_ids"]


def test_ensure_power_state_on_connection_drop_then_poll_success(monkeypatch):
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    state = {"value": "Off"}
    client.get_reset_action_metadata = lambda system_path=None: {
        "system_path": "/redfish/v1/Systems/1",
        "power_state": state["value"],
        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        "allowed_reset_types": ["On", "PushPowerButton"],
    }

    def fake_submit(system_path, target, reset_type):
        state["value"] = "On"
        return {"reset_type": reset_type, "http_status_code": None, "message_ids": [], "connection_dropped": True}

    client._submit_reset_action = fake_submit
    client.get_power_state = lambda system_path=None: state["value"]
    monkeypatch.setattr(ilo_module.time, "sleep", lambda _: None)
    result = client.ensure_power_state("On", system_path="/redfish/v1/Systems/1", timeout_seconds=5, poll_interval=1)
    assert result["action"] == "On"
    assert result["final_power_state"] == "On"
    assert result["result"]["connection_dropped"] is True


def test_ensure_power_state_on_uses_push_power_button_fallback(monkeypatch):
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    state = {"value": "Off"}
    calls = []
    client.get_reset_action_metadata = lambda system_path=None: {
        "system_path": "/redfish/v1/Systems/1",
        "power_state": state["value"],
        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        "allowed_reset_types": ["On", "PushPowerButton"],
    }

    def fake_submit(system_path, target, reset_type):
        del system_path, target
        calls.append(reset_type)
        if reset_type == "PushPowerButton":
            state["value"] = "On"
        return {"reset_type": reset_type, "http_status_code": 200, "message_ids": [], "connection_dropped": False}

    client._submit_reset_action = fake_submit
    ticks = {"n": 0}

    def fake_get_power_state(system_path=None):
        ticks["n"] += 1
        if ticks["n"] < 3:
            return "Off"
        return state["value"]

    client.get_power_state = fake_get_power_state
    monkeypatch.setattr(ilo_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(ilo_module.time, "time", lambda: ticks["n"])
    result = client.ensure_power_state("On", system_path="/redfish/v1/Systems/1", timeout_seconds=10, poll_interval=1)
    assert calls[0] == "On"
    assert "PushPowerButton" in calls
    assert result["action"] == "PushPowerButton"


def test_ensure_power_state_on_http_400_not_masked():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    client.get_reset_action_metadata = lambda system_path=None: {
        "system_path": "/redfish/v1/Systems/1",
        "power_state": "Off",
        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        "allowed_reset_types": ["On"],
    }
    client._submit_reset_action = lambda system_path, target, reset_type: (_ for _ in ()).throw(ILOError("POST /redfish/v1/Systems/1/Actions/ComputerSystem.Reset failed with HTTP 400: bad request"))
    with pytest.raises(ILOError, match="HTTP 400"):
        client.ensure_power_state("On", system_path="/redfish/v1/Systems/1")


def test_ensure_power_state_on_http_401_not_masked():
    client = ILOClient(ILOConfig(host="10.10.8.110", username="Administrator", password="pw"))
    client.get_reset_action_metadata = lambda system_path=None: {
        "system_path": "/redfish/v1/Systems/1",
        "power_state": "Off",
        "target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        "allowed_reset_types": ["On"],
    }
    client._submit_reset_action = lambda system_path, target, reset_type: (_ for _ in ()).throw(ILOError("POST /redfish/v1/Systems/1/Actions/ComputerSystem.Reset failed with HTTP 401: unauthorized"))
    with pytest.raises(ILOError, match="HTTP 401"):
        client.ensure_power_state("On", system_path="/redfish/v1/Systems/1")


def test_apply_storage_layout_failure_logs_are_saved(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260409-131500"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-09 13:15:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    monkeypatch.setattr(main, "ILOClient", lambda cfg: FakeGen10StorageApplyClient(cfg, fail_on="data_raid6"))
    monkeypatch.setattr(
        main,
        "start_storage_apply_background",
        lambda cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths: main.execute_storage_apply_in_background(
            cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths
        ),
    )

    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    response = client.post(
        "/apply-storage-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_mode": "wipe_rebuild",
            "acknowledge_apply": "on",
            "typed_confirmation": "WIPE STORAGE",
        },
    )

    assert response.status_code == 200
    apply_dir = main.STORAGE_RAID_EXPORT_DIR / "MXQ85103SX" / "20260409-131500"
    assert (apply_dir / "apply-log.yml").exists()
    assert (apply_dir / "apply-results.json").exists()
    apply_log_text = (apply_dir / "apply-log.yml").read_text(encoding="utf-8")
    apply_results_text = (apply_dir / "apply-results.json").read_text(encoding="utf-8")
    job = main.load_job("Apply-Kit")
    assert job["scope"] == "storage-apply:wipe_rebuild"
    assert job["status"] == "Failed"
    assert any("Create Data RAID 6 logical drive" in line for line in job["logs"])
    assert any("simulated data_raid6 create failure" in line for line in job["logs"])
    assert "simulated data_raid6 create failure" in apply_log_text
    assert "\"status\": \"Failed\"" in apply_results_text
    assert (apply_dir / "post-change-summary.yml").exists()


def test_reboot_storage_now_creates_reboot_artifacts_and_logs_success(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260409-150000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-09 15:00:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    monkeypatch.setattr(main, "ILOClient", lambda cfg: FakeGen10StorageApplyClient(cfg))
    monkeypatch.setattr(
        main,
        "start_storage_apply_background",
        lambda cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths: main.execute_storage_apply_in_background(
            cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths
        ),
    )
    monkeypatch.setattr(
        main,
        "start_storage_reboot_background",
        lambda cfg, discovery_raw_path, raid_plan_path, apply_paths: main.execute_storage_reboot_in_background(
            cfg, discovery_raw_path, raid_plan_path, apply_paths
        ),
    )

    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.11"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["host"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.11")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    client.post(
        "/apply-storage-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_mode": "wipe_rebuild",
            "acknowledge_apply": "on",
            "typed_confirmation": "WIPE STORAGE",
        },
    )

    apply_dir = main.STORAGE_RAID_EXPORT_DIR / "MXQ85103SX" / "20260409-150000"
    response = client.post(
        "/reboot-storage-now",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_artifact_dir": str(apply_dir),
        },
    )

    assert response.status_code == 200
    assert (apply_dir / "reboot-results.json").exists()
    assert (apply_dir / "post-reboot-summary.yml").exists()
    assert (apply_dir / "post-reboot-raw.json").exists()
    reboot_results_text = (apply_dir / "reboot-results.json").read_text(encoding="utf-8")
    apply_results_text = (apply_dir / "apply-results.json").read_text(encoding="utf-8")
    job = main.load_job("Apply-Kit")
    assert job["scope"] == "storage-reboot"
    assert job["status"] == "Completed"
    assert job["progress_percent"] == 100
    assert any("Request server reboot" in line for line in job["logs"])
    assert any("Wait for reboot start" in line for line in job["logs"])
    assert any("Wait for server to return" in line for line in job["logs"])
    assert any("ResetType=GracefulRestart" in line for line in job["logs"])
    assert any("allowed=GracefulRestart, ForceRestart, On" in line for line in job["logs"])
    assert any("first PowerState=On" in line for line in job["logs"])
    assert any("connection_dropped=no" in line for line in job["logs"])
    assert any("final PowerState=On" in line for line in job["logs"])
    assert any("matched=yes" in line for line in job["logs"])
    assert any("Export post-reboot storage" in line for line in job["logs"])
    assert "\"status\": \"Completed\"" in reboot_results_text
    assert "\"workflow_state\": \"post_reboot_validation_complete\"" in apply_results_text


def test_reboot_storage_now_failure_is_logged(client, monkeypatch):
    monkeypatch.setattr(main, "ILOClient", lambda cfg: FakeGen10StorageApplyClient(cfg, fail_on="reboot"))
    monkeypatch.setattr(
        main,
        "start_storage_apply_background",
        lambda cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths: main.execute_storage_apply_in_background(
            cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths
        ),
    )
    monkeypatch.setattr(
        main,
        "start_storage_reboot_background",
        lambda cfg, discovery_raw_path, raid_plan_path, apply_paths: main.execute_storage_reboot_in_background(
            cfg, discovery_raw_path, raid_plan_path, apply_paths
        ),
    )

    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    client.post(
        "/apply-storage-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_mode": "wipe_rebuild",
            "acknowledge_apply": "on",
            "typed_confirmation": "WIPE STORAGE",
        },
    )

    apply_dir = export_paths["directory"]
    response = client.post(
        "/reboot-storage-now",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_artifact_dir": str(apply_dir),
        },
    )

    assert response.status_code == 200
    assert "Retry Reboot Now" in response.text
    reboot_results_text = (apply_dir / "reboot-results.json").read_text(encoding="utf-8")
    apply_results_text = (apply_dir / "apply-results.json").read_text(encoding="utf-8")
    job = main.load_job("Apply-Kit")
    assert job["scope"] == "storage-reboot"
    assert job["status"] == "Failed"
    assert any("simulated reboot failure" in line for line in job["logs"])
    assert "\"status\": \"Failed\"" in reboot_results_text
    assert "\"workflow_state\": \"reboot_failed\"" in apply_results_text


def test_storage_workflow_progress_percent_stays_below_complete_until_validation_finishes():
    assert main.storage_workflow_progress_percent("running_apply", 5, 10) < 68
    assert main.storage_workflow_progress_percent("staged_reboot_required", 10, 10) == 68
    assert main.storage_workflow_progress_percent("reboot_requested", 0, 5) == 72
    assert main.storage_workflow_progress_percent("waiting_for_reboot_start", 1, 5) == 78
    assert main.storage_workflow_progress_percent("waiting_for_server_return", 2, 5) == 86
    assert main.storage_workflow_progress_percent("post_reboot_validation_pending", 4, 5) == 94
    assert main.storage_workflow_progress_percent("post_reboot_validation_complete", 5, 5) == 100


def test_apply_storage_layout_blocks_host_mismatch(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.91")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    response = client.post(
        "/apply-storage-layout",
        data={
            "return_page": "storage",
            "discovery_raw_path": str(export_paths["raw"]),
            "raid_plan_path": str(plan_paths["plan"]),
            "apply_mode": "wipe_rebuild",
            "acknowledge_apply": "on",
            "typed_confirmation": "WIPE STORAGE",
        },
    )

    assert response.status_code == 200
    assert "Storage apply failed" in response.text
    assert "host mismatch" in response.text


def test_realbox_hpe_disk_location_is_preserved_in_normalized_drive():
    discovery = FakeGen10RealBoxSmartStorageILOClient().get_storage_discovery()
    drives = discovery["summary"]["hpe_smart_storage"]["drives"]

    assert drives
    assert drives[0]["smart_storage_location"] == "1I:1:1"
    assert drives[0]["smart_storage_location_format"] == "ControllerPort:Box:Bay"


def test_saved_plan_artifact_preserves_smart_storage_location_for_os_data_and_spare_drives(client, monkeypatch):
    def fake_strftime(fmt):
        if fmt == "%Y%m%d-%H%M%S":
            return "20260409-140000"
        if fmt == "%Y-%m-%d %H:%M:%S":
            return "2026-04-09 14:00:00"
        raise AssertionError(f"unexpected strftime format: {fmt}")

    monkeypatch.setattr(main.time, "strftime", fake_strftime)
    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    for drive in discovery["summary"]["hpe_smart_storage"]["drives"]:
        drive["bay"] = drive["smart_storage_location"]
        drive.pop("smart_storage_location", None)
        drive.pop("smart_storage_location_format", None)

    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
    loaded_discovery, _ = main.load_storage_discovery_artifact(str(export_paths["raw"]), expected_host="10.10.8.90")
    plan = main.build_raid_plan(loaded_discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)

    assert plan["os_raid1"]["drives"][0]["smart_storage_location"] == "1I:1:1"
    assert plan["os_raid1"]["drives"][1]["smart_storage_location"] == "1I:1:2"
    assert [drive["smart_storage_location"] for drive in plan["data_raid6"]["drives"]] == [
        "1I:1:3",
        "1I:1:4",
        "1I:1:5",
        "1I:1:6",
        "1I:1:7",
        "1I:1:8",
    ]
    assert plan["hot_spare"]["drive"] == {}

    plan_payload = yaml.safe_load(plan_paths["plan"].read_text(encoding="utf-8"))
    exported_plan = plan_payload["plan"]
    assert exported_plan["os_raid1"]["drives"][0]["smart_storage_location"] == "1I:1:1"
    assert exported_plan["os_raid1"]["drives"][1]["smart_storage_location"] == "1I:1:2"
    assert [drive["smart_storage_location"] for drive in exported_plan["data_raid6"]["drives"]] == [
        "1I:1:3",
        "1I:1:4",
        "1I:1:5",
        "1I:1:6",
        "1I:1:7",
        "1I:1:8",
    ]
    assert exported_plan["hot_spare"]["drive"] == {}


def test_gen10_preflight_accepts_plan_loaded_from_saved_artifact_with_location_only_in_bay(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Apply Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    for drive in discovery["summary"]["hpe_smart_storage"]["drives"]:
        drive["bay"] = drive["smart_storage_location"]
        drive.pop("smart_storage_location", None)
        drive.pop("smart_storage_location_format", None)

    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
    loaded_discovery, loaded_paths = main.load_storage_discovery_artifact(str(export_paths["raw"]), expected_host="10.10.8.90")
    plan = main.build_raid_plan(loaded_discovery, loaded_paths)
    intent = main.build_storage_apply_intent(plan, "wipe_rebuild")
    client = RecordingGen10SmartStorageWriteClient()

    os_response = client.create_gen10_logical_drive(
        "/redfish/v1/Systems/1/SmartStorageConfig/Settings",
        "os_raid1",
        intent["os_raid1"],
    )

    assert os_response["reboot_required"] is True
    assert client.calls[0][2]["LogicalDrives"][0]["DataDrives"] == ["1I:1:1", "1I:1:2"]


class RecordingGen10SmartStorageWriteClient(ILOClient):
    def __init__(self):
        super().__init__(ILOConfig(host="ilo-gen10.example.test", username="Administrator", password="secret"))
        self.calls = []
        self.system_power_state = "On"
        self.settings_doc = {"@odata.id": "/redfish/v1/Systems/1/SmartStorageConfig/Settings", "LogicalDrives": []}
        self.volume_doc = {
            "@odata.id": "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1",
            "VolumeUniqueIdentifier": "600508B1001C1F3A0B7D1A6F0E93C001",
            "Name": "Existing OS",
            "Raid": "RAID1",
        }
        self.fail_method = ""
        self.reboot_required = True

    def _get(self, path: str, timeout=None):
        if path == "/redfish/v1/Systems/1/SmartStorageConfig/Settings":
            return self.settings_doc
        if path == "/redfish/v1/Systems/1/SmartStorageConfig":
            return {"@odata.id": "/redfish/v1/Systems/1/SmartStorageConfig", "LogicalDrives": self.settings_doc["LogicalDrives"]}
        if path == "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1":
            return self.volume_doc
        raise ILOError(f"GET {path} failed with HTTP 404")

    def _put(self, path: str, payload: dict):
        self.calls.append(("PUT", path, payload))
        if self.fail_method == "PUT":
            raise ILOError(f"PUT {path} failed with HTTP 400: simulated write failure")
        self.settings_doc = {"@odata.id": path, "LogicalDrives": list(payload.get("LogicalDrives") or [])}
        return {"Messages": [{"MessageId": "SmartStorage.ResetRequired"}], "reboot_required": self.reboot_required}

    def _patch(self, path: str, payload: dict):
        self.calls.append(("PATCH", path, payload))
        if self.fail_method == "PATCH":
            raise ILOError(f"PATCH {path} failed with HTTP 400: simulated write failure")
        return {"Messages": [{"MessageId": "SmartStorage.ResetRequired"}], "reboot_required": self.reboot_required}

    def get_systems(self):
        return ["/redfish/v1/Systems/1"]

    def get_system(self, system_path):
        assert system_path == "/redfish/v1/Systems/1"
        return {"PowerState": self.system_power_state}

    def power_reset(self, reset_type="ForceRestart", system_path=None):
        self.calls.append(("POWER_RESET", reset_type, system_path))
        if reset_type == "On":
            self.system_power_state = "On"
        if reset_type in {"ForceOff", "GracefulShutdown"}:
            self.system_power_state = "Off"
        return {
            "reset_type": reset_type,
            "system_path": system_path,
            "path": f"{system_path}/Actions/ComputerSystem.Reset" if system_path else "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        }


class RecordingStandardRedfishStorageWriteClient(ILOClient):
    def __init__(self):
        super().__init__(ILOConfig(host="ilo-gen11.example.test", username="Administrator", password="secret"))
        self.calls = []
        self.fail_delete = False
        self.fail_delete_missing = False
        self.fail_post = False
        self.fail_post_http_once = False
        self.fail_post_connection_once = False
        self.fail_post_connection_always = False
        self.fail_post_timeout_once = False
        self.simulate_create_side_effect_on_connection_abort = False
        self.volume_collection = [
            {
                "@odata.id": "/redfish/v1/Systems/1/Storage/DE009000/Volumes/1",
                "RAIDType": "RAID0",
                "Links": {
                    "Drives": [
                        {"@odata.id": "/redfish/v1/Chassis/DE009000/Drives/9"},
                    ]
                },
            }
        ]

    def _get(self, path: str, timeout=None):
        del timeout
        if path == "/redfish/v1/Systems/1":
            return {"Oem": {"Hpe": {"DeviceDiscoveryComplete": {"DeviceDiscovery": "vMainDeviceDiscoveryComplete"}}}}
        if path == "/redfish/v1/Systems/1/Storage/DE009000/Volumes":
            return {
                "@odata.id": "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
                "Members": [{"@odata.id": item["@odata.id"]} for item in self.volume_collection],
                "Members@odata.count": len(self.volume_collection),
            }
        if path.startswith("/redfish/v1/Systems/1/Storage/DE009000/Volumes/"):
            for item in self.volume_collection:
                if item.get("@odata.id") == path:
                    return item
            raise ILOError(f"GET {path} failed with HTTP 404")
        if path == "/redfish/v1/Systems/1/Storage/DE009000/Volumes/Capabilities":
            return {
                "@odata.id": "/redfish/v1/Systems/1/Storage/DE009000/Volumes/Capabilities",
                "Links": {
                    "Drives@Redfish.RequiredOnCreate": True,
                    "DedicatedSpareDrives@Redfish.OptionalOnCreate": True,
                },
                "RAIDType@Redfish.AllowableValues": ["RAID0", "RAID1", "RAID5", "RAID6", "RAID10"],
            }
        raise ILOError(f"GET {path} failed with HTTP 404")

    def _post(self, path: str, payload: dict | None = None):
        self.calls.append(("POST", path, payload or {}))
        if self.fail_post_connection_once:
            self.fail_post_connection_once = False
            if self.simulate_create_side_effect_on_connection_abort:
                self.volume_collection.append(
                    {
                        "@odata.id": f"{path}/recover-created",
                        "RAIDType": str((payload or {}).get("RAIDType") or ""),
                        "Links": dict((payload or {}).get("Links") or {}),
                    }
                )
            raise ILOError(f"POST {path} failed: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")
        if self.fail_post_connection_always:
            raise ILOError(f"POST {path} failed: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")
        if self.fail_post_timeout_once:
            self.fail_post_timeout_once = False
            if self.simulate_create_side_effect_on_connection_abort:
                self.volume_collection.append(
                    {
                        "@odata.id": f"{path}/recover-created-timeout",
                        "RAIDType": str((payload or {}).get("RAIDType") or ""),
                        "Links": dict((payload or {}).get("Links") or {}),
                    }
                )
            raise ILOError(f"POST {path} failed: HTTPSConnectionPool(host='10.10.8.110', port=443): Read timed out. (read timeout=15)")
        if self.fail_post_http_once:
            self.fail_post_http_once = False
            raise ILOError(
                'POST '
                f'{path} failed with HTTP 500: {{"error":{{"code":"iLO.0.10.ExtendedInfo","message":"See @Message.ExtendedInfo for more information.","@Message.ExtendedInfo":[{{"MessageArgs":["Unexpected error (84,01,34)"],"MessageId":"iLO.2.37.InternalErrorWithParam"}}]}}}}'
            )
        if self.fail_post:
            raise ILOError(f"POST {path} failed with HTTP 400: simulated write failure")
        if path.endswith("/Volumes"):
            self.volume_collection.append(
                {
                    "@odata.id": f"{path}/{len(self.volume_collection) + 1}",
                    "RAIDType": str((payload or {}).get("RAIDType") or ""),
                    "Links": dict((payload or {}).get("Links") or {}),
                }
            )
        return {"Id": "Task1", "Messages": []}

    def _delete(self, path: str):
        self.calls.append(("DELETE", path, None))
        if self.fail_delete_missing:
            raise ILOError(
                f'DELETE {path} failed with HTTP 404: {{"error":{{"code":"iLO.0.10.ExtendedInfo","message":"See @Message.ExtendedInfo for more information.","@Message.ExtendedInfo":[{{"MessageArgs":["{path}"],"MessageId":"Base.1.18.ResourceMissingAtURI"}}]}}}}'
            )
        if self.fail_delete:
            raise ILOError(f"DELETE {path} failed with HTTP 400: simulated delete failure")
        return {"Messages": []}


class RecordingStandardRedfishApplyClient(RecordingStandardRedfishStorageWriteClient):
    def __init__(self, discovery: dict[str, Any]):
        super().__init__()
        self.discovery = discovery
        self.system_power_state = "On"

    def get_storage_discovery(self, deep_smart_storage_scan=False):
        return self.discovery

    def get_systems(self):
        return ["/redfish/v1/Systems/1"]

    def get_system(self, system_path):
        assert system_path == "/redfish/v1/Systems/1"
        return {"PowerState": self.system_power_state}

    def power_reset(self, reset_type="ForceRestart", system_path=None):
        self.calls.append(("POWER_RESET", reset_type, system_path))
        if reset_type == "On":
            self.system_power_state = "On"
        if reset_type in {"ForceOff", "GracefulShutdown"}:
            self.system_power_state = "Off"
        return {
            "reset_type": reset_type,
            "system_path": system_path,
            "path": f"{system_path}/Actions/ComputerSystem.Reset" if system_path else "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        }

    def ensure_power_state(self, expected_state, *, system_path=None, timeout_seconds=300, poll_interval=5):
        del timeout_seconds, poll_interval
        expected = str(expected_state or "").strip().lower()
        if expected == "on":
            if self.system_power_state.lower() != "on":
                result = self.power_reset("On", system_path=system_path)
                return {
                    "action": "On",
                    "reset_target": result.get("path") or "",
                    "allowed_reset_types": ["On", "ForceOff", "PushPowerButton"],
                    "result": {"http_status_code": 200, "message_ids": ["Base.1.18.Success"], "connection_dropped": False},
                    "first_observed_power_state": "Off",
                    "last_observed_power_state": "On",
                    "changed": True,
                }
            return {"action": "skip", "changed": False}
        if self.system_power_state.lower() != "off":
            result = self.power_reset("ForceOff", system_path=system_path)
            return {"action": "ForceOff", "reset_target": result.get("path") or "", "allowed_reset_types": ["On", "ForceOff", "PushPowerButton"], "changed": True}
        return {"action": "skip", "changed": False}


def test_delete_storage_logical_drive_uses_settings_put_payload():
    client = RecordingGen10SmartStorageWriteClient()

    response = client.delete_storage_logical_drive(
        "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1",
        settings_path="/redfish/v1/Systems/1/SmartStorageConfig/Settings",
    )

    assert client.calls == [
        (
            "PUT",
            "/redfish/v1/Systems/1/SmartStorageConfig/Settings",
            {
                "DataGuard": "Permissive",
                "LogicalDrives": [
                    {
                        "VolumeUniqueIdentifier": "600508B1001C1F3A0B7D1A6F0E93C001",
                        "Actions": [{"Action": "LogicalDriveDelete"}],
                    }
                ],
            },
        )
    ]
    assert response["deleted_path"].endswith("/LogicalDrives/1")
    assert response["reboot_required"] is True


def test_build_gen10_storage_config_payload_for_create_only_uses_single_pending_config_put_shape():
    client = RecordingGen10SmartStorageWriteClient()
    payload = client.build_gen10_storage_config_payload(
        "/redfish/v1/Systems/1/SmartStorageConfig/Settings",
        "create_only",
        [],
        {
            "target_size_gib": 500,
            "drives": [
                {"bay": "1", "smart_storage_location": "1I:1:1"},
                {"bay": "2", "smart_storage_location": "1I:1:2"},
            ],
        },
        {
            "drives": [
                {"bay": "3", "smart_storage_location": "1I:1:3"},
                {"bay": "4", "smart_storage_location": "1I:1:4"},
                {"bay": "5", "smart_storage_location": "1I:1:5"},
                {"bay": "6", "smart_storage_location": "1I:1:6"},
            ]
        },
        {"bay": "8", "drive": {"smart_storage_location": "1I:1:8"}},
    )

    assert payload == {
        "DataGuard": "Disabled",
        "LogicalDrives": [
            {
                "LogicalDriveName": "OS RAID 1",
                "Raid": "Raid1",
                "CapacityGiB": 500,
                "DataDrives": ["1I:1:1", "1I:1:2"],
            },
            {
                "LogicalDriveName": "Data RAID 6",
                "Raid": "Raid6",
                "DataDrives": ["1I:1:3", "1I:1:4", "1I:1:5", "1I:1:6"],
                "SpareDrives": ["1I:1:8"],
                "SpareRebuildMode": "Dedicated",
            },
        ],
    }


def test_apply_gen10_storage_layout_submits_single_consolidated_wipe_rebuild_payload():
    client = RecordingGen10SmartStorageWriteClient()
    response = client.apply_gen10_storage_layout(
        "/redfish/v1/Systems/1/SmartStorageConfig/Settings",
        "wipe_rebuild",
        ["/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1"],
        {
            "target_size_gib": 500,
            "drives": [
                {"bay": "1", "smart_storage_location": "1I:1:1"},
                {"bay": "2", "smart_storage_location": "1I:1:2"},
            ],
        },
        {
            "drives": [
                {"bay": "3", "smart_storage_location": "1I:1:3"},
                {"bay": "4", "smart_storage_location": "1I:1:4"},
                {"bay": "5", "smart_storage_location": "1I:1:5"},
                {"bay": "6", "smart_storage_location": "1I:1:6"},
            ]
        },
        {"bay": "8", "drive": {"smart_storage_location": "1I:1:8"}},
    )

    assert client.calls == [
        (
            "PUT",
            "/redfish/v1/Systems/1/SmartStorageConfig/Settings",
            {
                "DataGuard": "Permissive",
                "LogicalDrives": [
                    {
                        "VolumeUniqueIdentifier": "600508B1001C1F3A0B7D1A6F0E93C001",
                        "Actions": [{"Action": "LogicalDriveDelete"}],
                    },
                    {
                        "LogicalDriveName": "OS RAID 1",
                        "Raid": "Raid1",
                        "CapacityGiB": 500,
                        "DataDrives": ["1I:1:1", "1I:1:2"],
                    },
                    {
                        "LogicalDriveName": "Data RAID 6",
                        "Raid": "Raid6",
                        "DataDrives": ["1I:1:3", "1I:1:4", "1I:1:5", "1I:1:6"],
                        "SpareDrives": ["1I:1:8"],
                        "SpareRebuildMode": "Dedicated",
                    },
                ],
            },
        )
    ]
    assert response["delete_count"] == 1
    assert response["reboot_required"] is True


def test_gen10_helper_write_failures_surface_cleanly():
    client = RecordingGen10SmartStorageWriteClient()
    client.fail_method = "PUT"

    with pytest.raises(ILOError, match="simulated write failure"):
        client.apply_gen10_storage_layout(
            "/redfish/v1/Systems/1/SmartStorageConfig/Settings",
            "wipe_rebuild",
            ["/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1"],
            {
                "target_size_gib": 500,
                "drives": [
                    {"bay": "1", "smart_storage_location": "1I:1:1"},
                    {"bay": "2", "smart_storage_location": "1I:1:2"},
                ],
            },
            {
                "drives": [
                    {"bay": "3", "smart_storage_location": "1I:1:3"},
                    {"bay": "4", "smart_storage_location": "1I:1:4"},
                    {"bay": "5", "smart_storage_location": "1I:1:5"},
                    {"bay": "6", "smart_storage_location": "1I:1:6"},
                ]
            },
            {"bay": "8", "drive": {"smart_storage_location": "1I:1:8"}},
        )


def test_gen10_helper_reboot_required_respects_explicit_controller_response():
    client = RecordingGen10SmartStorageWriteClient()
    client.reboot_required = False

    response = client.delete_storage_logical_drive(
        "/redfish/v1/Systems/1/SmartStorage/ArrayControllers/0/LogicalDrives/1",
        settings_path="/redfish/v1/Systems/1/SmartStorageConfig/Settings",
    )

    assert response["reboot_required"] is False


def test_choose_storage_apply_platform_supports_standard_redfish_volumes_backend():
    discovery = planner_standard_redfish_apply_discovery(existing_volumes=False, generation="Gen11", ilo_version="iLO 6")
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }
    plan = main.build_raid_plan(discovery, export_paths)

    platform = main.choose_storage_apply_platform(discovery, plan)

    assert platform["supported"] is True
    assert platform["id"] == "standard_redfish_volumes"
    assert platform["controller_path"] == "/redfish/v1/Systems/1/Storage/DE009000"
    assert platform["volumes_path"] == "/redfish/v1/Systems/1/Storage/DE009000/Volumes"
    assert platform["reset_target"] == "/redfish/v1/Systems/1/Storage/DE009000/Actions/Storage.ResetToDefaults"


def test_choose_storage_apply_platform_accepts_inferred_verified_paths_from_volume_capabilities():
    discovery = planner_standard_redfish_apply_discovery(existing_volumes=False, generation="Gen11", ilo_version="iLO 6")
    discovery["summary"]["capabilities"]["standard_redfish_volume_create_verified"] = False
    discovery["summary"]["capabilities"]["standard_redfish_volume_create_verified_paths"] = []
    discovery["raw"]["standard_storage"][0]["VolumeCapabilities"] = {
        "Links": {"Drives@Redfish.RequiredOnCreate": True},
        "RAIDType@Redfish.AllowableValues": ["RAID1", "RAID5", "RAID6"],
        "VolumeUsage@Redfish.AllowableValues": ["Data"],
    }
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }
    plan = main.build_raid_plan(discovery, export_paths)

    platform = main.choose_storage_apply_platform(discovery, plan)

    assert platform["supported"] is True
    assert platform["id"] == "standard_redfish_volumes"


def test_choose_storage_apply_platform_blocks_unverified_standard_redfish_create_before_delete():
    discovery = planner_standard_redfish_apply_discovery(existing_volumes=True, generation="Gen11", ilo_version="iLO 6")
    discovery["summary"]["capabilities"]["standard_redfish_volume_create_verified"] = False
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }
    plan = main.build_raid_plan(discovery, export_paths)

    platform = main.choose_storage_apply_platform(discovery, plan)

    assert platform["supported"] is False
    assert platform["id"] == "standard_redfish_create_unverified"
    assert "Blocking before delete" in platform["reason"]


def test_build_storage_controller_capabilities_marks_unverified_standard_redfish_inventory_only():
    discovery = planner_standard_redfish_apply_discovery(existing_volumes=True, generation="Gen11", ilo_version="iLO 6")
    discovery["summary"]["capabilities"]["standard_redfish_volume_create_verified"] = False

    rows = main.build_storage_controller_capabilities(discovery)

    assert len(rows) == 1
    assert rows[0]["controller_path"] == "/redfish/v1/Systems/1/Storage/DE009000"
    assert rows[0]["can_delete_volumes"] is True
    assert rows[0]["can_create_volumes"] is False
    assert rows[0]["create_method"] == "inventory_only"
    assert rows[0]["verified"] is False
    assert "not verified" in rows[0]["reason"].lower()


def test_build_storage_controller_capabilities_marks_verified_when_volume_capabilities_allow_create():
    discovery = planner_standard_redfish_apply_discovery(existing_volumes=True, generation="Gen11", ilo_version="iLO 6")
    discovery["summary"]["capabilities"]["standard_redfish_volume_create_verified"] = False
    discovery["summary"]["capabilities"]["standard_redfish_volume_create_verified_paths"] = []
    discovery["raw"]["standard_storage"][0]["VolumeCapabilities"] = {
        "Links": {"Drives@Redfish.RequiredOnCreate": True},
        "RAIDType@Redfish.AllowableValues": ["RAID0", "RAID1", "RAID5", "RAID6", "RAID10"],
        "VolumeUsage@Redfish.AllowableValues": ["Data"],
    }

    rows = main.build_storage_controller_capabilities(discovery)

    assert len(rows) == 1
    assert rows[0]["verified"] is True
    assert rows[0]["can_create_volumes"] is True
    assert rows[0]["create_method"] == "standard_redfish"


def test_execute_standard_redfish_wipe_rebuild_skips_reset_when_controller_is_empty(tmp_path):
    discovery = planner_standard_redfish_apply_discovery(existing_volumes=False, generation="Gen11", ilo_version="iLO 6")
    discovery["summary"]["capabilities"]["standard_redfish_volume_create_verified"] = False
    discovery["summary"]["capabilities"]["standard_redfish_volume_create_verified_paths"] = []
    discovery["raw"]["standard_storage"][0]["VolumeCapabilities"] = {
        "Links": {"Drives@Redfish.RequiredOnCreate": True},
        "RAIDType@Redfish.AllowableValues": ["RAID0", "RAID1", "RAID5", "RAID6", "RAID10"],
        "VolumeUsage@Redfish.AllowableValues": ["Data"],
    }
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }
    plan = main.build_raid_plan(discovery, export_paths)
    platform = main.choose_storage_apply_platform(discovery, plan)

    class FakeStandardRedfishApplyClient:
        def __init__(self):
            self.reset_calls: list[tuple[str, str]] = []
            self.create_calls: list[tuple[str, str, list[str]]] = []

        def wait_for_storage_device_discovery(self):
            return {"ready": True, "state": "Idle"}

        def get_standard_storage_volume_capabilities(self, _volumes_path: str):
            return {
                "Links": {"Drives@Redfish.RequiredOnCreate": True},
                "RAIDType@Redfish.AllowableValues": ["RAID1", "RAID6"],
                "VolumeUsage@Redfish.AllowableValues": ["Data"],
            }

        def delete_standard_storage_volume(self, volume_path: str):
            raise AssertionError(f"delete should not be called for empty controller: {volume_path}")

        def reset_standard_storage_to_defaults(self, target: str, reset_type: str = "ResetAll"):
            self.reset_calls.append((target, reset_type))
            return {"target": target, "reset_type": reset_type}

        def create_standard_storage_volume(
            self,
            volumes_path: str,
            intent: dict[str, Any],
            spare_intent: dict[str, Any] | None = None,
            capabilities: dict[str, Any] | None = None,
        ):
            self.create_calls.append((volumes_path, str(intent.get("raid_level") or ""), list(intent.get("drive_paths") or [])))
            return {
                "path": f"{volumes_path}/created",
                "capabilities": capabilities or {},
                "spare_intent": spare_intent or {},
            }

    client = FakeStandardRedfishApplyClient()
    apply_dir = tmp_path / "apply"
    apply_dir.mkdir(parents=True, exist_ok=True)
    apply_paths = {
        "directory": apply_dir,
        "apply_log": apply_dir / "apply-log.yml",
        "apply_results": apply_dir / "apply-results.json",
    }
    apply_state = {
        "apply_path": "standard_redfish_volumes",
        "controller": {"name": "HPE MR416i-o Gen11"},
        "steps": [],
        "errors": [],
        "responses": [],
    }
    job = {"logs": []}

    _current_step, responses = main.execute_storage_apply_standard_redfish(
        client,
        plan,
        "wipe_rebuild",
        platform,
        "Kit-01",
        job,
        apply_state,
        apply_paths,
        1,
        6,
    )

    assert client.reset_calls == []
    assert len(client.create_calls) == 2
    assert responses[0]["device_discovery"]["ready"] is True
    delete_steps = [step for step in apply_state["steps"] if step["step"] == "Delete existing logical volumes"]
    assert any("skipped delete/reset" in str(step.get("details") or "").lower() for step in delete_steps)


def test_storage_preflight_remaps_controller_path_when_hardware_intent_matches():
    approved = planner_standard_redfish_apply_discovery(existing_volumes=True, generation="Gen11", ilo_version="iLO 6")
    live = remap_standard_redfish_discovery_path(
        approved,
        "/redfish/v1/Systems/1/Storage/DE009000",
        "/redfish/v1/Systems/1/Storage/DE00A000",
    )
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }
    plan = main.build_raid_plan(approved, export_paths)

    remapped_plan, diagnosis = main.storage_preflight_compare_and_remap(plan, live, "wipe_rebuild")

    assert diagnosis["status"] == "remapped"
    assert remapped_plan["source_discovery"]["controller"]["path"] == "/redfish/v1/Systems/1/Storage/DE00A000"
    assert remapped_plan["existing_logical_volumes"][0]["path"] == "/redfish/v1/Systems/1/Storage/DE00A000/Volumes/1"
    assert any("Controller Redfish path changed" in item for item in diagnosis["differences"])
    assert any("Remapped controller path" in item for item in diagnosis["safe_corrections_attempted"])


def test_gitignore_covers_local_kits_artifacts_jobs_media_and_secrets():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "config/kits/*.yml" in gitignore
    assert "artifacts/jobs/" in gitignore
    assert "artifacts/runs/" in gitignore
    assert "artifacts/history/" in gitignore
    assert "artifacts/lab-builder.sqlite3" in gitignore
    assert "media/" in gitignore
    assert "secrets/" in gitignore
    assert ".env" in gitignore


def test_storage_preflight_blocks_when_approved_drive_serial_changes():
    approved = planner_standard_redfish_apply_discovery(existing_volumes=False, generation="Gen11", ilo_version="iLO 6")
    for drive in approved["summary"]["standard_redfish_storage"]["drives"]:
        drive["serial_number"] = f"SERIAL-{drive['bay']}"
    live = copy.deepcopy(approved)
    live["summary"]["standard_redfish_storage"]["drives"][2]["serial_number"] = "DIFFERENT-SERIAL"
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }
    plan = main.build_raid_plan(approved, export_paths)

    _remapped_plan, diagnosis = main.storage_preflight_compare_and_remap(plan, live, "wipe_rebuild")

    assert diagnosis["status"] == "blocked"
    assert diagnosis["user_action_required"] is True
    assert any("drive serial changed" in item for item in diagnosis["rejection_reasons"])
    assert "re-approve storage" in diagnosis["recommended_fix"]


def test_storage_live_layout_matches_plan_for_multi_controller_layout():
    discovery = two_controller_gen11_storage_discovery()
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }
    plan = main.build_raid_plan(
        discovery,
        export_paths,
        overrides={
            "os_controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "data_controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "data_drive_paths": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
            "hot_spare_path": "",
        },
    )
    live = copy.deepcopy(discovery)
    live["summary"]["standard_redfish_storage"]["volumes"] = [
        {
            "path": "/redfish/v1/Systems/1/Storage/BOOT/Volumes/1",
            "controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "id": "1",
            "name": "OS",
            "raid_type": "RAID1",
            "drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "spare_paths": [],
            "status": "OK / Enabled",
        },
        {
            "path": "/redfish/v1/Systems/1/Storage/DATA/Volumes/2",
            "controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "id": "2",
            "name": "DATA",
            "raid_type": "RAID6",
            "drive_paths": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
            "spare_paths": [],
            "status": "OK / Enabled",
        },
    ]

    matched, notes = main.storage_live_layout_matches_plan(plan, live)

    assert matched is True
    assert any("RAID1" in note for note in notes)
    assert any("RAID6" in note for note in notes)


def test_storage_preflight_marks_already_applied_when_live_layout_matches_plan():
    discovery = two_controller_gen11_storage_discovery()
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }
    plan = main.build_raid_plan(
        discovery,
        export_paths,
        overrides={
            "os_controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "data_controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "data_drive_paths": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
            "hot_spare_path": "",
        },
    )
    live = copy.deepcopy(discovery)
    live["summary"]["standard_redfish_storage"]["volumes"] = [
        {
            "path": "/redfish/v1/Systems/1/Storage/BOOT/Volumes/1",
            "controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "id": "1",
            "name": "OS",
            "raid_type": "RAID1",
            "drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "spare_paths": [],
            "status": "OK / Enabled",
        },
        {
            "path": "/redfish/v1/Systems/1/Storage/DATA/Volumes/2",
            "controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "id": "2",
            "name": "DATA",
            "raid_type": "RAID6",
            "drive_paths": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
            "spare_paths": [],
            "status": "OK / Enabled",
        },
    ]

    remapped_plan, diagnosis = main.storage_preflight_compare_and_remap(plan, live, "wipe_rebuild")

    assert remapped_plan["existing_logical_volumes"]
    assert diagnosis["status"] == "already_applied"
    assert "already matches the approved plan" in diagnosis["selected_action"]


def test_build_raid_plan_scopes_drives_to_selected_controller_when_multiple_are_detected():
    discovery = planner_standard_redfish_apply_discovery(existing_volumes=False, generation="Gen11", ilo_version="iLO 6", include_second_controller=True)
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }

    plan = main.build_raid_plan(discovery, export_paths)

    assert "More than one storage controller was detected" in " ".join(plan["warnings"])
    assert all(drive.get("controller_path") == "/redfish/v1/Systems/1/Storage/DE009000" for drive in plan["os_raid1"]["drives"])
    assert all(drive.get("controller_path") == "/redfish/v1/Systems/1/Storage/DE009000" for drive in plan["data_raid6"]["drives"])


def test_build_raid_plan_uses_selected_controller_when_multiple_are_detected():
    discovery = planner_standard_redfish_apply_discovery(existing_volumes=False, generation="Gen11", ilo_version="iLO 6", include_second_controller=True)
    export_paths = {
        "directory": Path("/tmp"),
        "summary": Path("/tmp/summary.yml"),
        "raw": Path("/tmp/raw.json"),
    }

    plan = main.build_raid_plan(
        discovery,
        export_paths,
        overrides={"controller_path": "/redfish/v1/Systems/1/Storage/DE009001"},
    )

    assert plan["source_discovery"]["controller"]["path"] == "/redfish/v1/Systems/1/Storage/DE009001"
    assert plan["customization"]["selected_controller_path"] == "/redfish/v1/Systems/1/Storage/DE009001"
    all_drive_paths = [drive.get("controller_path") for drive in plan["os_raid1"]["drives"] + plan["data_raid6"]["drives"]]
    assert all(path == "/redfish/v1/Systems/1/Storage/DE009001" for path in all_drive_paths)


def test_standard_redfish_storage_layout_uses_delete_and_volume_posts():
    client = RecordingStandardRedfishStorageWriteClient()

    response = client.delete_standard_storage_volume("/redfish/v1/Systems/1/Storage/DE009000/Volumes/1")
    create_response = client.create_standard_storage_volume(
        "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
        {
            "raid": "RAID1",
            "label": "OS RAID 1 logical drive",
            "target_size_gib": 500,
            "drives": [
                {"path": "/redfish/v1/Chassis/DE009000/Drives/0"},
                {"path": "/redfish/v1/Chassis/DE009000/Drives/1"},
            ],
        },
        capabilities=client.get_standard_storage_volume_capabilities("/redfish/v1/Systems/1/Storage/DE009000/Volumes"),
    )

    assert response["reboot_required"] is False
    assert create_response["reboot_required"] is False
    assert client.calls == [
        ("DELETE", "/redfish/v1/Systems/1/Storage/DE009000/Volumes/1", None),
        (
            "POST",
            "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
            {
                "RAIDType": "RAID1",
                "Links": {
                    "Drives": [
                        {"@odata.id": "/redfish/v1/Chassis/DE009000/Drives/0"},
                        {"@odata.id": "/redfish/v1/Chassis/DE009000/Drives/1"},
                    ]
                },
                "DisplayName": "OS RAID 1 logic",
                "VolumeUsage": "Data",
                "CapacityBytes": 536870912000,
            },
        ),
    ]


def test_standard_redfish_volume_payload_omits_out_of_range_capacity_bytes_and_sets_volume_usage():
    client = RecordingStandardRedfishStorageWriteClient()

    payload = client._standard_volume_payload(
        {
            "raid": "RAID1",
            "label": "OS RAID 1 logical drive",
            "target_size_gib": 500,
            "drives": [
                {"path": "/redfish/v1/Chassis/DE009000/Drives/0"},
                {"path": "/redfish/v1/Chassis/DE009000/Drives/1"},
            ],
        },
        capabilities={
            "payload": {
                "Links": {"Drives@Redfish.RequiredOnCreate": True},
                "RAIDType@Redfish.AllowableValues": ["RAID1"],
                "VolumeUsage@Redfish.AllowableValues": ["Data"],
                "CapacityBytes@Redfish.AllowableNumbers": ["1073741824:479023071232"],
            }
        },
    )

    assert payload["RAIDType"] == "RAID1"
    assert payload["DisplayName"] == "OS RAID 1 logic"
    assert payload["VolumeUsage"] == "Data"
    assert "CapacityBytes" not in payload


def test_standard_redfish_delete_treats_resource_missing_as_idempotent_success():
    client = RecordingStandardRedfishStorageWriteClient()
    client.fail_delete_missing = True

    response = client.delete_standard_storage_volume("/redfish/v1/Systems/1/Storage/DE009000/Volumes/1")

    assert response["already_missing"] is True
    assert response["reboot_required"] is False
    assert response["response"]["already_missing"] is True


def test_standard_redfish_volume_create_recovers_if_post_response_drops_but_volume_exists():
    client = RecordingStandardRedfishStorageWriteClient()
    client.fail_post_connection_once = True
    client.simulate_create_side_effect_on_connection_abort = True

    create_response = client.create_standard_storage_volume(
        "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
        {
            "raid": "RAID1",
            "label": "OS RAID 1 logical drive",
            "target_size_gib": 500,
            "drives": [
                {"path": "/redfish/v1/Chassis/DE009000/Drives/0"},
                {"path": "/redfish/v1/Chassis/DE009000/Drives/1"},
            ],
        },
        capabilities=client.get_standard_storage_volume_capabilities("/redfish/v1/Systems/1/Storage/DE009000/Volumes"),
    )

    post_calls = [item for item in client.calls if item[0] == "POST"]
    assert len(post_calls) == 1
    assert create_response["recovered_after_transport_error"] is True
    assert create_response["response"]["recovered_after_transport_error"] is True


def test_standard_redfish_volume_create_retries_once_if_post_response_drops_and_no_volume_visible():
    client = RecordingStandardRedfishStorageWriteClient()
    client.fail_post_connection_once = True

    create_response = client.create_standard_storage_volume(
        "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
        {
            "raid": "RAID1",
            "label": "OS RAID 1 logical drive",
            "target_size_gib": 500,
            "drives": [
                {"path": "/redfish/v1/Chassis/DE009000/Drives/0"},
                {"path": "/redfish/v1/Chassis/DE009000/Drives/1"},
            ],
        },
        capabilities=client.get_standard_storage_volume_capabilities("/redfish/v1/Systems/1/Storage/DE009000/Volumes"),
    )

    post_calls = [item for item in client.calls if item[0] == "POST"]
    assert len(post_calls) == 2
    assert create_response["recovered_after_transport_error"] is False
    assert create_response["response"]["Id"] == "Task1"


def test_standard_redfish_volume_create_recovers_if_post_times_out_but_volume_exists():
    client = RecordingStandardRedfishStorageWriteClient()
    client.fail_post_timeout_once = True
    client.simulate_create_side_effect_on_connection_abort = True

    create_response = client.create_standard_storage_volume(
        "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
        {
            "raid": "RAID1",
            "label": "Data RAID 1 logical drive",
            "drives": [
                {"path": "/redfish/v1/Chassis/DE009000/Drives/2"},
                {"path": "/redfish/v1/Chassis/DE009000/Drives/3"},
            ],
        },
        capabilities=client.get_standard_storage_volume_capabilities("/redfish/v1/Systems/1/Storage/DE009000/Volumes"),
    )

    post_calls = [item for item in client.calls if item[0] == "POST"]
    assert len(post_calls) == 1
    assert create_response["recovered_after_transport_error"] is True
    assert create_response["response"]["recovered_after_transport_error"] is True


def test_standard_redfish_volume_create_recovers_if_retry_returns_internal_error_but_volume_exists():
    client = RecordingStandardRedfishStorageWriteClient()
    client.fail_post_connection_once = True
    client.fail_post_http_once = True
    client.simulate_create_side_effect_on_connection_abort = True
    readback_calls = {"count": 0}

    def delayed_readback(_volumes_path: str, _payload: dict[str, Any]):
        readback_calls["count"] += 1
        if readback_calls["count"] <= 5:
            return {}
        return client.volume_collection[-1]

    client._find_matching_standard_volume = delayed_readback  # type: ignore[method-assign]

    create_response = client.create_standard_storage_volume(
        "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
        {
            "raid": "RAID6",
            "label": "Data RAID 6 logical drive",
            "drives": [
                {"path": "/redfish/v1/Chassis/DE009000/Drives/0"},
                {"path": "/redfish/v1/Chassis/DE009000/Drives/1"},
            ],
        },
        capabilities=client.get_standard_storage_volume_capabilities("/redfish/v1/Systems/1/Storage/DE009000/Volumes"),
    )

    post_calls = [item for item in client.calls if item[0] == "POST"]
    assert len(post_calls) == 2
    assert create_response["recovered_after_transport_error"] is True
    assert create_response["response"]["recovered_after_readback"] is True
    assert "retry_error" in create_response["response"]


def test_run_storage_as_part_of_real_run_supports_standard_redfish_volumes_backend():
    cfg = main.default_config()
    cfg["site"]["name"] = "Std-Storage-Kit"
    main.save_kit_config(cfg)

    discovery = planner_standard_redfish_apply_discovery(existing_volumes=True, generation="Gen11", ilo_version="iLO 6")
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.122.142.13")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    storage_execution = {
        "discovery_raw_path": str(export_paths["raw"]),
        "plan_path": str(plan_paths["plan"]),
        "approved_host": "10.122.142.13",
    }
    job = {
        "status": "Running",
        "scope": "multi__ilo__storage__esxi",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": 32,
        "logs": [],
    }
    main.save_job(cfg["site"]["name"], job)
    client = RecordingStandardRedfishApplyClient(discovery)

    result = main.run_storage_as_part_of_real_run(
        cfg,
        client,
        "10.122.142.13",
        "10.122.142.13",
        storage_execution,
        cfg["site"]["name"],
        job,
        17,
        32,
    )

    assert result["apply_state"]["apply_path"] == "Standard Redfish Storage Volumes"
    assert result["apply_state"]["reboot_required"] is False
    assert client.calls[:4] == [
        ("DELETE", "/redfish/v1/Systems/1/Storage/DE009000/Volumes/1", None),
        (
            "POST",
            "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
                {
                    "RAIDType": "RAID1",
                    "Links": {
                        "Drives": [
                            {"@odata.id": "/redfish/v1/Chassis/DE009000/Drives/0"},
                            {"@odata.id": "/redfish/v1/Chassis/DE009000/Drives/1"},
                        ]
                    },
                    "DisplayName": "OS RAID 1 logic",
                    "VolumeUsage": "Data",
                    "CapacityBytes": 536870912000,
                },
            ),
            (
                "POST",
            "/redfish/v1/Systems/1/Storage/DE009000/Volumes",
            {
                "RAIDType": "RAID5",
                    "Links": {
                        "Drives": [
                            {"@odata.id": "/redfish/v1/Chassis/DE009000/Drives/2"},
                            {"@odata.id": "/redfish/v1/Chassis/DE009000/Drives/3"},
                            {"@odata.id": "/redfish/v1/Chassis/DE009000/Drives/4"},
                        ]
                    },
                    "DisplayName": "Data RAID 5 log",
                    "VolumeUsage": "Data",
                },
            ),
        ]


def test_run_storage_as_part_of_real_run_remaps_stale_controller_path_before_apply():
    cfg = main.default_config()
    cfg["site"]["name"] = "Std-Storage-Remap-Kit"
    main.save_kit_config(cfg)

    approved = planner_standard_redfish_apply_discovery(existing_volumes=True, generation="Gen11", ilo_version="iLO 6")
    live = remap_standard_redfish_discovery_path(
        approved,
        "/redfish/v1/Systems/1/Storage/DE009000",
        "/redfish/v1/Systems/1/Storage/DE00A000",
    )
    export_paths = main.export_storage_discovery_snapshot(cfg, approved, host="10.122.142.13")
    plan = main.build_raid_plan(approved, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    storage_execution = {
        "discovery_raw_path": str(export_paths["raw"]),
        "plan_path": str(plan_paths["plan"]),
        "approved_host": "10.122.142.13",
    }
    job = {
        "status": "Running",
        "scope": "multi__ilo__storage__esxi",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": 32,
        "logs": [],
    }
    main.save_job(cfg["site"]["name"], job)
    client = RecordingStandardRedfishApplyClient(live)

    result = main.run_storage_as_part_of_real_run(
        cfg,
        client,
        "10.122.142.13",
        "10.122.142.13",
        storage_execution,
        cfg["site"]["name"],
        job,
        17,
        32,
    )

    assert result["apply_state"]["apply_path"] == "Standard Redfish Storage Volumes"
    assert client.calls[0] == ("DELETE", "/redfish/v1/Systems/1/Storage/DE00A000/Volumes/1", None)
    assert client.calls[1][1] == "/redfish/v1/Systems/1/Storage/DE00A000/Volumes"
    finished_job = main.load_job(cfg["site"]["name"])
    assert any("[REMAP] Storage preflight" in line for line in finished_job["logs"])
    assert finished_job["storage_preflight"]["status"] == "remapped"


def test_run_storage_as_part_of_real_run_marks_job_completed_when_layout_is_already_applied():
    cfg = main.default_config()
    cfg["site"]["name"] = "Std-Storage-Already-Applied"
    main.save_kit_config(cfg)

    discovery = two_controller_gen11_storage_discovery()
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.122.142.13")
    plan = main.build_raid_plan(
        discovery,
        export_paths,
        overrides={
            "os_controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "data_controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "os_raid_level": "RAID1",
            "data_raid_level": "RAID6",
            "os_drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "data_drive_paths": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
            "hot_spare_path": "",
        },
    )
    live = copy.deepcopy(discovery)
    live["summary"]["standard_redfish_storage"]["volumes"] = [
        {
            "path": "/redfish/v1/Systems/1/Storage/BOOT/Volumes/1",
            "controller_path": "/redfish/v1/Systems/1/Storage/BOOT",
            "id": "1",
            "name": "OS",
            "raid_type": "RAID1",
            "drive_paths": [
                "/redfish/v1/Chassis/BOOT/Drives/1",
                "/redfish/v1/Chassis/BOOT/Drives/2",
            ],
            "spare_paths": [],
            "status": "OK / Enabled",
        },
        {
            "path": "/redfish/v1/Systems/1/Storage/DATA/Volumes/2",
            "controller_path": "/redfish/v1/Systems/1/Storage/DATA",
            "id": "2",
            "name": "DATA",
            "raid_type": "RAID6",
            "drive_paths": [
                "/redfish/v1/Chassis/DATA/Drives/3",
                "/redfish/v1/Chassis/DATA/Drives/4",
                "/redfish/v1/Chassis/DATA/Drives/5",
                "/redfish/v1/Chassis/DATA/Drives/6",
                "/redfish/v1/Chassis/DATA/Drives/7",
                "/redfish/v1/Chassis/DATA/Drives/8",
            ],
            "spare_paths": [],
            "status": "OK / Enabled",
        },
    ]
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    storage_execution = {
        "discovery_raw_path": str(export_paths["raw"]),
        "plan_path": str(plan_paths["plan"]),
        "approved_host": "10.122.142.13",
    }
    job = {
        "status": "Running",
        "scope": "multi__ilo__storage__esxi",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": 32,
        "logs": [],
    }
    main.save_job(cfg["site"]["name"], job)
    client = RecordingStandardRedfishApplyClient(live)

    result = main.run_storage_as_part_of_real_run(
        cfg,
        client,
        "10.122.142.13",
        "10.122.142.13",
        storage_execution,
        cfg["site"]["name"],
        job,
        17,
        32,
    )

    assert result["apply_state"]["status"] == "Completed"
    assert client.calls == []
    finished_job = main.load_job(cfg["site"]["name"])
    assert finished_job["status"] == "Completed"
    assert finished_job["current_stage"] == "Finished"
    assert finished_job["progress_percent"] == 100
    assert any("already matches the approved plan" in line for line in finished_job["logs"])


def test_run_storage_as_part_of_real_run_powers_on_when_server_starts_off():
    cfg = main.default_config()
    cfg["site"]["name"] = "Std-Storage-Off-Boot"
    main.save_kit_config(cfg)

    discovery = planner_standard_redfish_apply_discovery(existing_volumes=True, generation="Gen11", ilo_version="iLO 6")
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.122.142.13")
    plan = main.build_raid_plan(discovery, export_paths)
    plan_paths = main.export_raid_plan_snapshot(cfg, plan, export_paths)
    storage_execution = {
        "discovery_raw_path": str(export_paths["raw"]),
        "plan_path": str(plan_paths["plan"]),
        "approved_host": "10.122.142.13",
    }
    job = {
        "status": "Running",
        "scope": "multi__ilo__storage__esxi",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": 32,
        "logs": [],
    }
    main.save_job(cfg["site"]["name"], job)
    client = RecordingStandardRedfishApplyClient(discovery)
    client.system_power_state = "Off"

    main.run_storage_as_part_of_real_run(
        cfg,
        client,
        "10.122.142.13",
        "10.122.142.13",
        storage_execution,
        cfg["site"]["name"],
        job,
        17,
        32,
    )

    assert ("POWER_RESET", "On", "/redfish/v1/Systems/1") in client.calls
    finished_job = main.load_job(cfg["site"]["name"])
    joined_logs = "\n".join(finished_job["logs"])
    assert "Storage stage initial PowerState=Off" in joined_logs
    assert "Storage stage confirmed server PowerState=On" in joined_logs


def test_dashboard_shows_recommended_next_step_and_workflow_cards(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Dash Kit"
    cfg["ilo"]["current_ip"] = ""
    cfg["ilo"]["host"] = ""
    cfg["ilo"]["target_ip"] = ""
    cfg["ip_plan"]["ilo"] = ""
    main.save_kit_config(cfg)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Active kit" in response.text
    assert response.text.count("Active kit") == 1
    assert "Choose a kit" in response.text
    active_section = response.text.split("Active kit", 1)[1]
    assert "Choose a kit" in active_section
    assert "Create a new kit" in response.text
    assert "Use an existing kit" in response.text
    assert "Open current config" in response.text
    assert "Download current config" in response.text
    assert "Recommended next step" in response.text
    assert "Open next step" in response.text
    assert "Continue setup" in response.text
    assert "Run Center" in response.text
    assert "Review ESXi setup" not in response.text
    assert "Open run history" not in response.text
    assert "Open reports &amp; technical details" not in response.text
    assert 'name="selected_kit"' in response.text
    assert 'name="new_kit_name"' in response.text
    assert 'type="file"' not in response.text
    assert "Generic readiness cockpit for the active deployment workspace." in response.text
    assert "Job status" in response.text
    assert "No runs have completed for this kit yet." in response.text
    assert "Last update" not in response.text
    assert "What happened last" not in response.text
    assert 'id="theme-toggle"' not in response.text
    assert "Use this page to move through the setup one step at a time." not in response.text


def test_dashboard_load_previous_kit_uses_saved_kit_dropdown(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Primary Dash Kit"
    main.save_kit_config(cfg)
    older = main.default_config()
    older["site"]["name"] = "Older Dash Kit"
    main.save_kit_config(older)
    main.set_current_kit_name("Primary-Dash-Kit")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'name="selected_kit"' in response.text
    assert "Switch active kit" in response.text
    selector_section = response.text.split('name="selected_kit"', 1)[1]
    assert ">Older-Dash-Kit<" in selector_section
    assert ">Primary-Dash-Kit<" not in selector_section


def test_cisco_page_separates_live_state_from_desired_template(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco State Kit"
    cfg["cisco_switch"]["last_discovered_version"] = "17.09.04a"
    cfg["cisco_switch"]["last_discovered_at"] = "2026-05-14T12:00:00+00:00"
    cfg["cisco_switch"]["last_port_discovery"] = {
        "interfaces": {
            "GigabitEthernet1/0/24": {"status": "connected", "vlan": "10", "description": "Live Uplink"},
        }
    }
    cfg["cisco_switch"]["last_running_config_backup"] = "interface GigabitEthernet1/0/24"
    main.save_kit_config(cfg)

    response = client.get("/cisco")

    assert response.status_code == 200
    assert "Upgrade, access, then approve" in response.text
    assert "Console access" in response.text
    assert "Current console config" in response.text
    assert "Test console access" in response.text
    assert "Apply Access Configs" in response.text
    assert "Switch Config and Run Approval" in response.text
    assert "Current Switch Config" in response.text
    assert "Save to config" in response.text
    assert "Approve config" in response.text
    assert "Run Cisco upgrade" not in response.text
    assert "Port map" in response.text
    assert "GigabitEthernet1/0/24" in response.text


def test_cisco_approve_config_plan_includes_switch_for_run_center(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Approval Kit"
    cfg["included"]["cisco_switch"] = False
    cfg["cisco_switch"].update(
        {
            "last_ssh_test": {"ok": True},
            "last_discovered_version": "17.09.04a",
            "upgrade": {"last_plan": {"comparison": "already_current"}},
            "ports": {"GigabitEthernet1/0/1": {"profile": "client_device"}},
        }
    )
    main.save_kit_config(cfg)

    monkeypatch.setattr(
        cisco_module_routes.service,
        "preview_config",
        lambda _context, mode="full", selected_ports=None: {
            "ok": True,
            "config": "hostname sw01\nusername admin secret ********",
            "validation": {"ok": True, "errors": [], "warnings": []},
        },
    )

    response = client.post("/modules/cisco/approve-config-plan", data={"mode": "full"})

    assert response.status_code == 200
    assert "Cisco config plan approved" in response.text
    saved = main.load_kit_config()
    assert saved["included"]["cisco_switch"] is True
    assert saved["cisco_switch"]["config_approval"]["state"] == "approved"


def test_cisco_approve_config_plan_uses_nonblocking_upgrade_helper_gate(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Nonblocking Gate Kit"
    cfg["included"]["cisco_switch"] = False
    cfg["cisco_switch"].update(
        {
            "last_ssh_test": {"ok": True},
            "last_discovered_version": "17.09.04a",
            "ports": {"GigabitEthernet1/0/1": {"profile": "client_device"}},
        }
    )
    main.save_kit_config(cfg)

    monkeypatch.setattr(
        cisco_module_routes.service,
        "preview_config",
        lambda _context, mode="full", selected_ports=None: {
            "ok": True,
            "config": "hostname sw01",
            "validation": {"ok": True, "errors": [], "warnings": []},
        },
    )
    monkeypatch.setattr(
        main,
        "upgrade_gate_entry",
        lambda _cfg, key: {"key": key, "label": "Cisco", "blocks_run": False, "comparison": "warning_only"},
    )

    response = client.post("/modules/cisco/approve-config-plan", data={"mode": "full"})

    assert response.status_code == 200
    assert "Cisco config plan approved" in response.text
    assert "Review the Cisco upgrade gate" not in response.text
    saved = main.load_kit_config()
    assert saved["included"]["cisco_switch"] is True
    assert saved["cisco_switch"]["config_approval"]["state"] == "approved"


def test_cisco_approve_config_plan_shows_blocked_result_inline(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Blocked Approval Kit"
    cfg["cisco_switch"].update(
        {
            "management_ip": "10.10.8.5",
            "username": "admin",
            "password": "Secret123!",
            "last_ssh_test": {"ok": False},
            "upgrade": {"last_plan": {"comparison": "already_current"}},
        }
    )
    main.save_kit_config(cfg)

    monkeypatch.setattr(
        cisco_module_routes.service,
        "preview_config",
        lambda _context, mode="full", selected_ports=None: {
            "ok": True,
            "config": "hostname sw01",
            "validation": {"ok": True, "errors": [], "warnings": []},
        },
    )

    response = client.post("/modules/cisco/approve-config-plan", data={"mode": "full"})

    assert response.status_code == 200
    assert "Cisco config plan not approved" in response.text
    assert "Approval result: blocked" in response.text
    assert "SSH must pass before the Cisco config plan can be approved for Run Center." in response.text
    saved = main.load_kit_config()
    assert saved["cisco_switch"]["config_approval"]["state"] == "blocked"


def test_cisco_discover_version_uses_posted_management_fields(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Posted State Kit"
    cfg["cisco_switch"]["ip"] = ""
    cfg["cisco_switch"]["management_ip"] = ""
    cfg["cisco_switch"]["username"] = ""
    cfg["cisco_switch"]["password"] = ""
    main.save_kit_config(cfg)

    def fake_discover(context):
        cisco_cfg = (context.get("cfg") or {}).get("cisco_switch") or {}
        assert cisco_cfg.get("ip") == "10.10.8.2"
        assert cisco_cfg.get("username") == "admin"
        assert cisco_cfg.get("password") == "secret"
        return {
            "ok": True,
            "target": "10.10.8.2",
            "version": "17.09.04a",
            "raw_excerpt": "Cisco IOS XE Software, Version 17.09.04a",
            "model": "C9300-48P",
            "platform": "C9300-UNIVERSALK9-M",
            "hostname": "sw01",
            "warnings": [],
        }

    monkeypatch.setattr(cisco_module_routes.service, "discover", fake_discover)

    response = client.post(
        "/modules/cisco/discover-version",
        data={
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "secret",
            "cisco_management_ip": "10.10.8.2",
        },
    )

    assert response.status_code == 200
    assert "17.09.04a" in response.text


def test_dashboard_job_status_lists_passed_and_failed_with_dates(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Dash Status Kit"
    main.save_kit_config(cfg)
    main.save_history(
        "Dash-Status-Kit",
        [
            {"time": "2026-04-17 10:30:00", "scope": "esxi", "status": "Failed", "run_summary_path": "/tmp/esxi-summary.yml"},
            {"time": "2026-04-17 09:15:00", "scope": "ilo", "status": "Completed", "run_summary_path": "/tmp/ilo-summary.yml"},
        ],
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Job status" in response.text
    assert "iLO run passed" in response.text
    assert ">Passed<" in response.text
    assert "2026-04-17 09:15:00" in response.text
    assert "ESXi run failed" in response.text
    assert ">Failed<" in response.text
    assert "2026-04-17 10:30:00" in response.text
    assert response.text.count("Open log") >= 2
    assert "/tmp/ilo-summary.yml" in response.text
    assert "/tmp/esxi-summary.yml" in response.text


def test_dashboard_job_status_ignores_superseded_entries(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Dash Superseded Kit"
    main.save_kit_config(cfg)
    history = [
        {"time": "2026-04-17 10:45:00", "scope": "storage-apply:wipe_rebuild", "status": "Completed", "run_summary_path": "/tmp/storage-ok.yml"},
        {"time": "2026-04-17 10:30:00", "scope": "storage-apply:wipe_rebuild", "status": "Superseded", "original_status": "Failed", "run_summary_path": "/tmp/storage-failed.yml"},
        {"time": "2026-04-17 09:15:00", "scope": "ilo", "status": "Completed", "run_summary_path": "/tmp/ilo-summary.yml"},
    ]

    status = main.build_dashboard_job_status(history)

    assert status["failed"] == []
    assert any(item["name"] == "Storage" and item["status"] == "Completed" for item in status["passed"])


def test_dashboard_keeps_focus_on_kit_status_and_next_steps(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Identity Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.50"
    cfg["ilo"]["host"] = "10.10.8.50"
    cfg["ilo"]["target_ip"] = "10.10.8.11"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["shared_network"]["subnet"] = "10.10.8.0/24"
    cfg["ip_plan"]["gateway"] = "10.10.8.1"

    ilo_export_dir = main.ILO_LIVE_EXPORT_DIR / "Identity-Server" / "20260424-101500"
    ilo_export_dir.mkdir(parents=True, exist_ok=True)
    (ilo_export_dir / "summary.yml").write_text(
        yaml.safe_dump(
            {
                "server_model": "ProLiant DL380 Gen11",
                "product_name": "DL380",
                "serial_number": "ABC123",
                "current_ilo_ip": "10.10.8.50",
                "target_ilo_ip": "10.10.8.11",
                "ilo_firmware_version": "3.00",
                "storage": {"controllers": [{"name": "Smart Array", "firmware_version": {"Current": {"VersionString": "1.98"}}}]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (ilo_export_dir / "raw.json").write_text(
        main.json.dumps({"inventory": {"summary": {"manager": {"model": "iLO 6"}}}}, indent=2),
        encoding="utf-8",
    )

    storage_export_dir = main.STORAGE_RAID_EXPORT_DIR / "ABC123" / "20260424-101700"
    storage_export_dir.mkdir(parents=True, exist_ok=True)
    storage_raw_path = storage_export_dir / "raw.json"
    (storage_export_dir / "summary.yml").write_text(
        yaml.safe_dump(
            {
                "server": {
                    "model": "ProLiant DL380 Gen11",
                    "product_name": "DL380",
                    "generation": "Gen11",
                    "serial_number": "ABC123",
                },
                "ilo": {"model": "iLO 6", "version": "iLO 6", "firmware": "3.00"},
                "standard_redfish_storage": {
                    "controllers": [{"name": "Smart Array", "model": "MR416i-o", "firmware_version": {"Current": {"VersionString": "1.98"}}}]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    storage_raw_path.write_text(main.json.dumps({"discovery": {}}, indent=2), encoding="utf-8")
    cfg["storage"]["latest_discovery_raw_path"] = str(storage_raw_path)
    main.save_kit_config(cfg)

    main.save_history(
        "Identity-Kit",
        [
            {
                "time": "2026-04-24 10:30:00",
                "scope": "ilo",
                "status": "Completed",
                "current_stage": "Finished",
                "run_summary_path": "/tmp/ilo-summary.yml",
                "config_summary": {
                    "login_ip": "10.10.8.50",
                    "target_ip": "10.10.8.11",
                    "dns_apply_status": "Verified",
                    "snmp_apply_status": "Verified",
                    "ilo_reset_status": "Completed",
                    "ilo_final_ip_verified": True,
                },
            }
        ],
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Active kit" in response.text
    assert "Choose a kit" in response.text
    assert "Job status" in response.text
    assert "iLO run passed" in response.text
    assert "Open log" in response.text
    assert "Continue setup" in response.text
    assert "Hardware identity" not in response.text
    assert "Latest build receipt" not in response.text
    assert "Build timeline" not in response.text


def test_dashboard_uses_simplified_primary_navigation(client):
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Mission control" in response.text
    assert "Shared defaults" in response.text
    assert "Reports" in response.text
    assert "Technical details" in response.text
    assert 'href="/execution"' in response.text
    assert "Run Center" in response.text
    assert ".sidebar .nav-group:last-of-type" not in response.text
    assert "Run History" not in response.text
    assert "Reset dashboard layout" not in response.text


def test_dashboard_and_windows_page_show_precheck_summary(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Precheck Kit"
    cfg["included"]["windows"] = True
    main.save_kit_config(cfg)

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Operations summary" in dashboard.text
    assert "Ready workflows" in dashboard.text
    assert "Next fix" in dashboard.text
    assert "Mission control overview" in dashboard.text

    windows = client.get("/windows")
    assert windows.status_code == 200
    assert "Windows pre-check" in windows.text
    assert "Checks ready" in windows.text
    assert "Target" in windows.text
    assert "Dry-run before execution" in windows.text


def test_sidebar_shows_optional_setup_pages_when_included(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "More Setup Kit"
    cfg["included"]["windows"] = True
    cfg["included"]["qnap"] = True
    main.save_kit_config(cfg)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'href="/windows"' in response.text
    assert 'href="/qnap"' in response.text
    assert "Windows" in response.text
    assert "QNAP" in response.text
    assert '.sidebar .nav-link[href="/windows"]' not in response.text
    assert '.sidebar .nav-link[href="/qnap"]' not in response.text


def test_sidebar_keeps_windows_setup_link_when_not_included(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Nav Kit"
    cfg["included"]["windows"] = False
    main.save_kit_config(cfg)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'href="/windows"' in response.text
    assert "Windows" in response.text
    assert 'nav-dot nav-dot-muted' in response.text


def test_sidebar_groups_windows_cisco_and_netapp_under_setup(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Setup Group Kit"
    cfg["included"]["windows"] = True
    main.save_kit_config(cfg)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert '<div class="nav-label">Setup Modules</div>' in response.text
    assert 'href="/windows"' in response.text
    assert 'href="/modules/cisco"' in response.text
    assert 'href="/modules/netapp"' in response.text
    assert 'href="/modules/ovf-templates"' in response.text
    assert response.text.count('href="/modules/cisco"') == 1
    assert response.text.count('href="/modules/netapp"') == 1
    assert response.text.count('href="/modules/ovf-templates"') == 1
    assert response.text.index('href="/windows"') < response.text.index('<div class="nav-label">Run</div>')
    assert response.text.index('href="/modules/cisco"') < response.text.index('<div class="nav-label">Run</div>')
    assert response.text.index('href="/modules/netapp"') < response.text.index('<div class="nav-label">Run</div>')
    assert response.text.index('href="/modules/ovf-templates"') < response.text.index('<div class="nav-label">Run</div>')
    assert response.text.count('<div class="nav-label">Setup Modules</div>') == 1


def test_ovf_templates_page_renders_from_sidebar_route(client):
    response = client.get("/modules/ovf-templates")

    assert response.status_code == 200
    assert "OVF Templates" in response.text
    assert "Register local template directory" in response.text
    assert "Dashboard mission control" not in response.text


def test_kits_route_falls_back_to_dashboard_workflow(client):
    response = client.get("/kits")

    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Active kit" in response.text
    assert "Choose a kit" in response.text
    assert "Continue setup" in response.text


def test_create_new_kit_updates_active_kit_on_dashboard(client):
    response = client.post(
        "/new-kit",
        data={"new_kit_name": "Fresh Kit", "return_page": "dashboard"},
    )

    assert response.status_code == 200
    assert "Active kit" in response.text
    assert "Fresh-Kit" in response.text


def test_websocket_job_stream_exits_cleanly_on_cancelled_sleep(monkeypatch):
    class FakeWebSocket:
        def __init__(self):
            self.accepted = False
            self.sent = []

        async def accept(self):
            self.accepted = True

        async def send_text(self, payload):
            self.sent.append(payload)

    async def cancelled_sleep(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(main, "load_job", lambda kit_name: {"status": "Idle", "kit": kit_name})
    monkeypatch.setattr(main.asyncio, "sleep", cancelled_sleep)

    websocket = FakeWebSocket()
    asyncio.run(main.websocket_job_stream(websocket, "Shutdown Kit"))

    assert websocket.accepted is True
    assert websocket.sent
    assert "Shutdown-Kit" in websocket.sent[0]


def test_reports_page_hides_live_jobs_and_config_capture_blocks(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Reports Kit"
    main.save_kit_config(cfg)

    response = client.get("/configs")

    assert response.status_code == 200
    assert "Reports" in response.text
    assert "Technical details" in response.text
    assert "Live job and logs" not in response.text
    assert "Capture current iLO" not in response.text
    assert "Saved intended config" not in response.text


def test_esxi_page_removes_duplicate_include_and_global_settings_prompt(client):
    response = client.get("/esxi")

    assert response.status_code == 200
    assert "Installer version, ISO, name, and password." in response.text
    assert "Installer details" in response.text
    assert "Include ESXi setup in this kit" not in response.text
    assert "Open global settings" not in response.text


def test_save_global_settings_rejects_invalid_snmp_values(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "SNMP Invalid Kit"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-global-settings",
        data={
            "return_page": "global_settings",
            "site_name": "SNMP Invalid Kit",
            "shared_subnet": "10.10.8.0/24",
            "gateway_ip": "10.10.8.1",
            "switch_ip": "10.10.8.2",
            "esxi_ip": "10.10.8.10",
            "ilo_target_ip": "10.10.8.11",
            "windows_ip": "10.10.8.20",
            "qnap_ip": "10.10.8.30",
            "iosafe_ip": "10.10.8.31",
            "dns1": "1.1.1.1",
            "dns2": "",
            "dns3": "",
            "dns4": "",
            "snmp_v3_username": "bad user",
            "snmp_v3_auth_protocol": "SHA",
            "snmp_v3_auth_password": "short",
            "snmp_v3_priv_protocol": "AES",
            "snmp_v3_priv_password": "tiny",
        },
    )

    assert response.status_code == 200
    assert "Shared defaults need attention" in response.text
    assert "SNMPv3 user cannot contain spaces." in response.text
    assert "SNMPv3 auth password must be at least 8 characters." in response.text
    assert 'name="snmp_v3_username"' in response.text
    assert "field-error" in response.text
    assert "input-invalid" in response.text
    saved = main.load_kit_config("SNMP-Invalid-Kit")
    assert saved["shared_snmp"]["v3_username"] == ""


def test_save_esxi_settings_preserves_disabled_inclusion_when_page_has_no_toggle(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Preserve Kit"
    cfg["included"]["esxi"] = False
    main.save_kit_config(cfg)

    response = client.post(
        "/save-esxi-settings",
        data={"return_page": "esxi", "esxi_hostname": "esxi-preserve", "esxi_root_password": "Valid1Pass!"},
    )

    assert response.status_code == 200
    cfg = main.load_kit_config("ESXi-Preserve-Kit")
    assert cfg["esxi"]["hostname"] == "esxi-preserve"
    assert cfg["included"]["esxi"] is False
    assert cfg["esxi"]["debug_no_reboot"] is False


def test_save_esxi_settings_persists_debug_no_reboot(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Debug Mode Kit"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-esxi-settings",
        data={
            "return_page": "esxi",
            "esxi_hostname": "esxi-debug",
            "esxi_root_password": "Valid1Pass!",
            "esxi_debug_no_reboot": "on",
        },
    )

    assert response.status_code == 200
    saved = main.load_kit_config("ESXi-Debug-Mode-Kit")
    assert saved["esxi"]["debug_no_reboot"] is True
    assert "Debug no reboot: Yes" in response.text


def test_save_esxi_settings_rejects_invalid_hostname_and_password(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Invalid Kit"
    cfg["esxi"]["hostname"] = "esxi-good"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-esxi-settings",
        data={"return_page": "esxi", "esxi_hostname": "bad_host!", "esxi_root_password": "secret"},
    )

    assert response.status_code == 200
    assert "ESXi setup needs attention" in response.text
    assert "Use only letters, numbers, hyphens, and dots in the ESXi server name." in response.text
    assert "Use at least 3 character types" in response.text
    assert 'name="esxi_hostname"' in response.text
    assert "field-error" in response.text
    assert "input-invalid" in response.text

    saved = main.load_kit_config("ESXi-Invalid-Kit")
    assert saved["esxi"]["hostname"] == "esxi-good"
    assert saved["esxi"]["root_password"] == "Valid1Pass!"


def test_esxi_post_config_preview_builds_targets_and_templates():
    cfg = main.default_config()
    cfg["site"]["name"] = "Kit-77"
    cfg.setdefault("site", {})["support_unit"] = "NSWAN"
    cfg.setdefault("site", {})["host_bay"] = "2"
    cfg["shared_network"]["subnet"] = "10.10.8.0/24"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["ip_plan"]["domestic_dc_ip"] = "10.10.8.200"
    cfg["esxi"]["post_config_inventory"] = {
        "datastores": [{"name": "datastore1", "capacity_gb": 200}],
        "scsi_disks": [{"name": "naa.5001", "size_gb": 2000, "in_use": False}],
        "physical_nics": [{"name": "vmnic0", "speed_mbps": 1000}, {"name": "vmnic1", "speed_mbps": 1000}],
    }
    main.ensure_esxi_post_config_policy(cfg)["allow_datastore_create"] = True

    preview = main.build_esxi_post_config_preview(cfg)

    assert preview["plan"]["connection_targets"][0] == "10.10.8.10"
    assert "10.10.8.31" in preview["plan"]["connection_targets"]
    assert preview["plan"]["identity"]["hostname"] == "NSWAN-Kit-77-VP00002"
    assert preview["plan"]["identity"]["domain"] == "Kit-77.forces.mil.ca"
    assert preview["plan"]["datastore_plan"]["create_local_s2_allowed"] is True


def test_esxi_post_config_validation_flags_missing_ntp_and_uplink_policy():
    preview = {
        "warnings": [],
        "plan": {
            "connection_targets": ["10.10.8.10"],
            "ntp": {"server": ""},
            "identity": {"dns_servers": ["", "10.10.8.200"]},
            "network_plan": {"preferred_mgmt_uplinks": ["vmnic0"], "single_uplink_override_enabled": False},
            "datastore_plan": {"create_local_s2_allowed": False},
        },
    }
    result = main.validate_esxi_post_config_preview(preview)
    assert result["ok"] is False
    assert any("NTP server is empty" in item for item in result["errors"])
    assert any("Primary DNS server is empty" in item for item in result["errors"])
    assert any("At least two management uplinks are required" in item for item in result["errors"])


def test_save_esxi_settings_persists_post_config_policy_fields(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Post Policy Kit"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-esxi-settings",
        data={
            "return_page": "esxi",
            "esxi_hostname": "esxi-post",
            "esxi_root_password": "Valid1Pass!",
            "esxi_post_discovery_start_octet": "41",
            "esxi_post_discovery_end_octet": "44",
            "esxi_post_allow_datastore_create": "on",
            "esxi_post_allow_single_mgmt_uplink_override": "on",
            "esxi_post_wug_snmp_target": "10.0.0.5@162/wug/priv/trap",
            "esxi_post_hostname_override": "manual-host",
        },
    )

    assert response.status_code == 200
    saved = main.load_kit_config("ESXi-Post-Policy-Kit")
    policy = saved["esxi"]["post_config_policy"]
    assert policy["discovery_start_octet"] == 41
    assert policy["discovery_end_octet"] == 44
    assert policy["allow_datastore_create"] is True
    assert policy["allow_single_mgmt_uplink_override"] is True
    assert policy["wug_snmp_target"] == "10.0.0.5@162/wug/priv/trap"
    assert saved["esxi"]["post_config_hostname_override"] == "manual-host"


def test_esxi_post_config_executor_dry_run_marks_actions_planned():
    cfg = main.default_config()
    cfg["site"]["name"] = "Kit-99"
    cfg["shared_network"]["subnet"] = "10.10.8.0/24"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["post_config_inventory"] = {
        "datastores": [{"name": "datastore1", "capacity_gb": 200}],
        "scsi_disks": [{"name": "naa.5001", "size_gb": 1800, "in_use": False}],
        "physical_nics": [{"name": "vmnic0", "speed_mbps": 1000}, {"name": "vmnic1", "speed_mbps": 1000}],
    }
    main.ensure_esxi_post_config_policy(cfg)["allow_datastore_create"] = True
    preview = main.build_esxi_post_config_preview(cfg)
    validation = main.validate_esxi_post_config_preview(preview)

    result = main.execute_esxi_post_config_actions(cfg, preview=preview, validation=validation, run_action_fn=None)

    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert result["reboot_required"] is True
    assert all(item["status"] in {"planned", "skipped"} for item in result["results"])


def test_esxi_post_config_executor_blocks_on_validation_errors():
    cfg = main.default_config()
    preview = {"policy": {}, "plan": {}, "warnings": []}
    validation = {"ok": False, "errors": ["missing values"], "warnings": []}

    result = main.execute_esxi_post_config_actions(cfg, preview=preview, validation=validation, run_action_fn=None)

    assert result["ok"] is False
    assert result["results"] == []
    assert result["errors"] == ["missing values"]


def test_save_esxi_settings_persists_reboot_confirmed_flag(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Reboot Confirm Kit"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-esxi-settings",
        data={
            "return_page": "esxi",
            "esxi_hostname": "esxi-reboot",
            "esxi_root_password": "Valid1Pass!",
            "esxi_post_reboot_confirmed": "on",
        },
    )

    assert response.status_code == 200
    saved = main.load_kit_config("ESXi-Reboot-Confirm-Kit")
    assert saved["esxi"]["post_config_policy"]["reboot_confirmed"] is True


def test_build_esxi_post_config_ssh_run_action_requires_transport_prerequisites():
    cfg = main.default_config()
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    preview = main.build_esxi_post_config_preview(cfg)
    if shutil.which("sshpass"):
        run_action = main.build_esxi_post_config_ssh_run_action(cfg, preview)
        assert callable(run_action)
    else:
        with pytest.raises(RuntimeError):
            main.build_esxi_post_config_ssh_run_action(cfg, preview)


def test_esxi_post_config_ssh_run_action_dispatches_with_custom_runner():
    cfg = main.default_config()
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["root_password"] = "Valid1Pass!"
    cfg["shared_network"]["subnet"] = "10.10.8.0/24"
    cfg["esxi"]["post_config_inventory"] = {
        "datastores": [{"name": "datastore1", "capacity_gb": 200}],
        "scsi_disks": [{"name": "naa.5001", "size_gb": 1800, "in_use": False}],
        "physical_nics": [{"name": "vmnic0", "speed_mbps": 1000}, {"name": "vmnic1", "speed_mbps": 1000}],
    }
    preview = main.build_esxi_post_config_preview(cfg)
    seen = []

    def fake_runner(cmd: list[str]) -> tuple[int, str, str]:
        seen.append(cmd)
        return 0, "ok\n", ""

    run_action = main.build_esxi_post_config_ssh_run_action(cfg, preview, command_runner=fake_runner)
    result = run_action("ceip", {"UserVars.HostClientCEIPOptIn": 2})
    assert result["status"] == "applied"
    assert seen
    assert seen[0][0] == "sshpass"


def test_save_esxi_settings_persists_post_config_transport_and_secrets(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Transport Kit"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-esxi-settings",
        data={
            "return_page": "esxi",
            "esxi_hostname": "esxi-transport",
            "esxi_root_password": "Valid1Pass!",
            "esxi_post_transport": "ssh",
            "esxi_post_secret_wug_password": "WUGSecret1!",
        },
    )
    assert response.status_code == 200
    saved = main.load_kit_config("ESXi-Transport-Kit")
    assert saved["esxi"]["post_config_transport"] == "ssh"
    assert saved["esxi"]["post_config_secrets"]["wug_password"] == "WUGSecret1!"


def test_report_center_lists_storage_reports_and_view_report(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Report Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")

    response = client.get("/configs?report_type=storage")

    assert response.status_code == 200
    assert "Recent history" in response.text
    assert "Latest run bundles" in response.text
    assert "Find saved files" in response.text
    assert "Recent matching files" in response.text
    assert "View" in response.text

    view_response = client.post(
        "/view-report",
        data={"return_page": "configs", "report_path": str(export_paths["summary"])},
    )
    assert view_response.status_code == 200
    assert "Report: summary.yml" in view_response.text
    assert "source_host" in view_response.text


def test_view_run_summary_builds_exportable_review(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Summary Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["included"]["ilo"] = True
    main.save_kit_config(cfg)

    response = client.post(
        "/view-run-summary",
        data={"scope": "ilo", "return_page": "execution"},
    )

    assert response.status_code == 200
    assert "Run Summary: ilo" in response.text
    assert "Run summary ready" in response.text
    assert "validation_checks" in response.text
    assert "recoverability" in response.text
    assert "readiness_matrix" in response.text
    assert "final_summary" in response.text
    assert "artifacts" in response.text


def test_history_and_report_center_show_run_bundle_links(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Bundle Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["included"]["ilo"] = True
    main.save_kit_config(cfg)

    main.save_job(
        "Bundle Kit",
        {
            "status": "Completed",
            "scope": "ilo",
            "current_stage": "Finished",
            "progress_percent": 100,
            "completed_steps": 3,
            "total_steps": 3,
            "logs": ["[DONE] iLO run finished."],
        },
    )
    main.append_job_history_snapshot(cfg, "ilo")

    history_response = client.get("/history")
    assert history_response.status_code == 200
    assert "Run bundles" in history_response.text
    assert "Recent runs" in history_response.text
    assert "Open run summary" in history_response.text
    assert "Related reports" in history_response.text

    configs_response = client.get("/configs")
    assert configs_response.status_code == 200
    assert "Recent history" in configs_response.text
    assert "Latest run bundles" in configs_response.text
    assert "Open bundle" in configs_response.text
    assert "Find saved files" in configs_response.text


def test_report_center_collapses_large_raw_file_list_by_default(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Large Reports Kit"
    main.save_kit_config(cfg)

    for index in range(15):
        folder = main.CONFIG_EXPORT_DIR / "Large-Reports-Kit" / f"set-{index:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"config-{index:02d}.yml").write_text(f"item: {index}\n", encoding="utf-8")

    response = client.get("/configs?report_type=config")

    assert response.status_code == 200
    assert "Recent matching files" in response.text
    assert "Browse all matching files" in response.text
    assert "15 matching files" in response.text


def test_report_center_collapses_older_bundles_by_stage(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Bundle Summary Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    main.save_kit_config(cfg)

    main.save_history(
        "Bundle Summary Kit",
        [
            {"time": "2026-04-24 10:30:00", "scope": "ilo", "status": "Completed", "current_stage": "Finished", "run_summary_path": "/tmp/ilo-new.yml", "config_summary": {"target_ip": "10.10.8.90"}},
            {"time": "2026-04-24 09:00:00", "scope": "ilo", "status": "Failed", "current_stage": "Retry needed", "run_summary_path": "/tmp/ilo-old.yml", "config_summary": {"target_ip": "10.10.8.90"}},
            {"time": "2026-04-24 08:00:00", "scope": "esxi", "status": "Completed", "current_stage": "Installed", "run_summary_path": "/tmp/esxi.yml", "config_summary": {"target_ip": "10.10.8.20"}},
        ],
    )

    response = client.get("/configs")

    assert response.status_code == 200
    assert "Latest run bundles" in response.text
    assert "Older runs" in response.text
    assert response.text.count("iLO run") >= 2
    assert "ESXi run" in response.text


def test_report_center_shows_human_friendly_bundle_summary(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Friendly Bundle Kit"
    main.save_kit_config(cfg)
    main.save_history(
        "Friendly Bundle Kit",
        [
            {
                "time": "2026-04-24 10:30:00",
                "scope": "ilo",
                "status": "Completed",
                "current_stage": "Finished",
                "run_summary_path": "/tmp/ilo-friendly.yml",
                "config_summary": {
                    "login_ip": "10.10.8.90",
                    "target_ip": "10.10.8.30",
                    "dns_apply_status": "Verified",
                    "snmp_apply_status": "Verified",
                    "ilo_reset_status": "Completed",
                    "ilo_final_ip_verified": True,
                },
            }
        ],
    )

    response = client.get("/configs")

    assert response.status_code == 200
    assert "This run handled DNS verified, SNMP verified, iLO reset completed, final iLO IP verified." in response.text
    assert "iLO IP 10.10.8.90 -&gt; 10.10.8.30" in response.text
    assert "DNS Verified" in response.text
    assert "SNMP Verified" in response.text
    assert "iLO reset Completed" in response.text
    assert "Full story:" in response.text
    assert "Finished" in response.text


def test_load_job_handles_partial_yaml_without_crashing():
    path = main.job_path("Partial YAML Kit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("status: Running\nlogs:\n- 'unterminated", encoding="utf-8")

    job = main.load_job("Partial YAML Kit")

    assert job["status"] == "Updating"
    assert job["current_stage"] == "Refreshing live status"
    assert "[WARN] Live job state was mid-write. Refreshing." in job["logs"][0]


def test_load_kit_config_bootstraps_default_kit_when_none_exist(tmp_path, monkeypatch):
    kits_dir = tmp_path / "kits"
    kits_dir.mkdir(parents=True, exist_ok=True)
    current_kit_file = tmp_path / "current_kit.txt"

    monkeypatch.setattr(main, "KITS_DIR", kits_dir)
    monkeypatch.setattr(main, "CURRENT_KIT_FILE", current_kit_file)

    cfg = main.load_kit_config()

    assert (kits_dir / "Kit-01.yml").exists()
    assert cfg["site"]["name"] == "Kit-01"
    assert cfg["ilo"]["username"] == "Administrator"


def test_get_current_kit_name_skips_missing_pointer_and_uses_available_kit(tmp_path, monkeypatch):
    kits_dir = tmp_path / "kits"
    kits_dir.mkdir(parents=True, exist_ok=True)
    current_kit_file = tmp_path / "current_kit.txt"
    current_kit_file.write_text("Missing-Kit", encoding="utf-8")
    (kits_dir / "Available-Kit.yml").write_text(
        yaml.safe_dump({"site": {"name": "Available-Kit"}}, sort_keys=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(main, "KITS_DIR", kits_dir)
    monkeypatch.setattr(main, "CURRENT_KIT_FILE", current_kit_file)

    assert main.get_current_kit_name() == "Available-Kit"


def test_history_page_renders_boolean_config_summary_values(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "History Bool Kit"
    main.save_kit_config(cfg)
    main.append_history_entry(
        "History Bool Kit",
        {
            "time": "2026-04-10 10:00:00",
            "scope": "ilo",
            "status": "Completed",
            "current_stage": "Finished",
            "progress_percent": 100,
            "completed_steps": 2,
            "total_steps": 2,
            "config_summary": {
                "login_ip": "10.10.8.90",
                "storage_included": True,
                "dns_servers": ["1.1.1.1", "8.8.8.8"],
                "gateway": "10.10.8.1",
            },
        },
    )

    response = client.get("/history")

    assert response.status_code == 200
    assert "Storage Included:" in response.text
    assert "Yes" in response.text
    assert "1.1.1.1, 8.8.8.8" in response.text


def test_history_page_shows_human_friendly_run_summary(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "History Friendly Kit"
    main.save_kit_config(cfg)
    main.append_history_entry(
        "History Friendly Kit",
        {
            "time": "2026-04-24 10:30:00",
            "scope": "ilo",
            "status": "Completed",
            "current_stage": "Finished",
            "progress_percent": 100,
            "completed_steps": 10,
            "total_steps": 10,
            "config_summary": {
                "login_ip": "10.10.8.90",
                "target_ip": "10.10.8.30",
                "dns_apply_status": "Verified",
                "snmp_apply_status": "Verified",
                "ilo_reset_status": "Completed",
                "ilo_final_ip_verified": True,
            },
        },
    )

    response = client.get("/history")

    assert response.status_code == 200
    assert "This run handled DNS verified, SNMP verified, iLO reset completed, final iLO IP verified." in response.text
    assert "iLO IP 10.10.8.90 -&gt; 10.10.8.30" in response.text
    assert "Full story:" in response.text
    assert "Finished" in response.text


def test_import_kit_config_loads_uploaded_config(client):
    payload = yaml.safe_dump(
        {
            "site": {"name": "Imported Kit"},
            "ip_plan": {"gateway": "10.44.55.1", "ilo": "10.44.55.11"},
            "ilo": {"current_ip": "10.44.55.90", "host": "10.44.55.90", "username": "Administrator"},
        },
        sort_keys=False,
    ).encode("utf-8")

    response = client.post(
        "/import-kit-config",
        data={"return_page": "configs"},
        files={"import_file": ("imported-kit.yml", payload, "application/x-yaml")},
    )

    assert response.status_code == 200
    assert "Config imported" in response.text
    assert "Current kit: Imported-Kit" in response.text
    cfg = main.load_kit_config("Imported-Kit")
    assert cfg["ilo"]["current_ip"] == "10.44.55.90"


def test_kit_config_model_accepts_existing_yaml_shape():
    cfg = KitConfigModel(**main.default_config())
    dumped = cfg.model_dump()
    assert dumped["site"]["name"] == "Kit-01"
    assert dumped["ilo"]["policy"]["discover_start_octet"] == 21


def test_build_default_ip_plan_uses_expected_offsets():
    plan = build_default_ip_plan("10.55.66.0/24")
    assert plan == {
        "gateway": "10.55.66.1",
        "switch": "10.55.66.2",
        "esxi": "10.55.66.10",
        "ilo": "10.55.66.11",
        "windows": "10.55.66.20",
        "qnap": "10.55.66.30",
        "iosafe": "10.55.66.31",
            "netapp": "10.55.66.45",
    }


def test_global_settings_omits_module_specific_fields_and_execution_includes_netapp_scope(client):
    global_settings = client.get("/global-settings")
    assert global_settings.status_code == 200
    assert 'name="netapp_host"' not in global_settings.text
    assert 'name="netapp_storage_protocol"' not in global_settings.text
    assert 'name="included_netapp"' not in global_settings.text

    execution = client.get("/execution")
    assert execution.status_code == 200
    assert 'value="netapp"' in execution.text


def test_save_global_settings_persists_netapp_configuration(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "NetApp Save Kit"
    main.save_kit_config(cfg)

    response = client.post(
        "/save-global-settings",
        data={
            "return_page": "global_settings",
            "site_name": "NetApp Save Kit",
            "shared_subnet": "10.10.8.0/24",
            "gateway_ip": "10.10.8.1",
            "switch_ip": "10.10.8.2",
            "esxi_ip": "10.10.8.10",
            "ilo_target_ip": "10.10.8.11",
            "windows_ip": "10.10.8.20",
            "qnap_ip": "10.10.8.30",
            "iosafe_ip": "10.10.8.31",
            "netapp_ip": "10.10.8.40",
            "netapp_host": "10.10.8.40",
            "netapp_username": "admin",
            "netapp_password": "secret",
            "netapp_storage_protocol": "iscsi",
            "included_netapp": "on",
        },
    )
    assert response.status_code == 200

    saved = main.load_kit_config("NetApp-Save-Kit")
    assert saved["included"]["netapp"] is True
    assert saved["netapp"]["host"] == "10.10.8.40"
    assert saved["netapp"]["username"] == "admin"
    assert saved["netapp"]["password"] == "secret"
    assert saved["netapp"]["storage_protocol"] == "iscsi"


def test_stage_registry_registers_and_filters_enabled_stages():
    registry = StageRegistry()
    registry.register(
        CallableStagePlugin(
            name="alpha",
            title="Alpha",
            enabled_fn=lambda context: bool(context.get("enabled")),
            plan_fn=lambda context: {"planned": True},
            validate_fn=lambda context: {"ok": True},
            execute_fn=lambda context, job: job.update({"ran": True}),
        )
    )
    registry.register(
        CallableStagePlugin(
            name="beta",
            title="Beta",
            enabled_fn=lambda context: False,
            plan_fn=lambda context: {},
            validate_fn=lambda context: {},
            execute_fn=lambda context, job: None,
        )
    )
    enabled = registry.enabled({"enabled": True})
    assert [stage.name for stage in enabled] == ["alpha"]
    assert registry.get("alpha").plan({}) == {"planned": True}


def test_build_stage_registry_contains_core_stages():
    registry = main.build_stage_registry(main.default_config())
    assert registry.get("ilo") is not None
    assert registry.get("storage") is not None
    assert registry.get("esxi") is not None


def test_module_manifest_discovery_and_navigation():
    modules_dir = main.BASE_DIR / "app" / "modules"
    manifests = discover_module_manifests(modules_dir)
    names = {item["name"] for item in manifests}
    assert "ilo" in names
    assert "storage" in names
    assert "netapp" in names
    nav = module_navigation(manifests)
    assert any(item["name"] == "netapp" and item["href"] == "/modules/netapp" for item in nav)


def test_netapp_module_route_renders_without_breaking_legacy_routes(client):
    response = client.get("/modules/netapp")
    assert response.status_code == 200
    assert "NetApp setup" in response.text
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Lab Builder Dashboard" in dashboard.text


def test_cisco_module_route_renders_without_breaking_legacy_routes(client):
    response = client.get("/modules/cisco")
    assert response.status_code == 200
    assert "Cisco setup" in response.text
    legacy = client.get("/cisco")
    assert legacy.status_code == 200
    assert "Cisco setup" in legacy.text
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200


def test_apply_module_enable_overrides_env(monkeypatch):
    manifests = [
        {"name": "ilo", "enabled": True},
        {"name": "netapp", "enabled": True},
        {"name": "cisco", "enabled": True},
    ]
    monkeypatch.setenv("LAB_BUILDER_DISABLED_MODULES", "netapp")
    monkeypatch.setenv("LAB_BUILDER_ENABLED_MODULES", "ilo,cisco")
    updated = apply_module_enable_overrides(manifests)
    enabled = {item["name"]: item["enabled"] for item in updated}
    assert enabled["ilo"] is True
    assert enabled["cisco"] is True
    assert enabled["netapp"] is False


def test_load_modules_skips_disabled_module(tmp_path):
    app = FastAPI()
    modules_dir = tmp_path / "modules"
    disabled = modules_dir / "disabledmod"
    disabled.mkdir(parents=True, exist_ok=True)
    (disabled / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "name": "disabledmod",
                "title": "Disabled",
                "enabled": False,
                "routes": {"prefix": "/modules/disabledmod"},
                "navigation": {"label": "Disabled", "href": "/modules/disabledmod", "active_page": "disabledmod"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (disabled / "routes.py").write_text(
        "from fastapi import FastAPI\n"
        "def register_module_routes(app: FastAPI) -> None:\n"
        "    app.state.disabled_route_registered = True\n",
        encoding="utf-8",
    )
    (modules_dir / "__init__.py").write_text("", encoding="utf-8")
    (disabled / "__init__.py").write_text("", encoding="utf-8")

    manifests = load_modules(app, modules_dir=modules_dir, package_root="modules")
    assert len(manifests) == 1
    assert getattr(app.state, "disabled_route_registered", False) is False


def test_get_steps_for_scope_uses_registry_titles():
    steps = main.get_steps_for_scope(main.default_config(), "ilo")
    assert steps[0] == "Preview iLO target and sign-in"
    assert "policy and account changes" in steps[2]


def test_build_snmp_readback_checks_captures_expected_v3_fields():
    checks = build_snmp_readback_checks(
        {
            "SNMP": {
                "ProtocolEnabled": True,
                "SNMPv3Username": "ops-user",
                "SNMPv3AuthProtocol": "SHA",
                "SNMPv3PrivacyProtocol": "AES",
                "SNMPv1Enabled": False,
                "SNMPv3Enabled": True,
            }
        },
        requested_username="ops-user",
        desired_auth_protocol="SHA",
        desired_priv_protocol="AES",
    )

    labels = {item["label"]: item for item in checks}
    assert labels["protocol_enabled"]["matched"] is True
    assert labels["username"]["matched"] is True
    assert labels["auth_protocol"]["matched"] is True
    assert labels["privacy_protocol"]["matched"] is True
    assert labels["SNMPv1Enabled"]["matched"] is True
    assert labels["SNMPv3Enabled"]["matched"] is True


def test_current_snmp_matches_requires_matching_checks_when_policy_enabled():
    network_protocol = {
        "SNMP": {
            "ProtocolEnabled": True,
            "SNMPv3Username": "ops-user",
            "SNMPv3AuthProtocol": "SHA",
            "SNMPv3PrivacyProtocol": "AES",
            "SNMPv3Enabled": True,
        }
    }

    assert current_snmp_matches(
        network_protocol,
        snmp_policy_enabled=True,
        requested_username="ops-user",
        desired_auth_protocol="SHA",
        desired_priv_protocol="AES",
    ) is True
    assert current_snmp_matches(
        network_protocol,
        snmp_policy_enabled=True,
        requested_username="ops-user",
        desired_auth_protocol="SHA",
        desired_priv_protocol="DES",
    ) is False


def test_verify_final_ilo_state_reports_hostname_dns_and_snmp_mismatches():
    result = verify_final_ilo_state(
        network_protocol_doc={
            "HostName": "ilo-a",
            "SNMP": {
                "ProtocolEnabled": True,
                "SNMPv3Username": "ops-user",
                "SNMPv3AuthProtocol": "SHA",
                "SNMPv3PrivacyProtocol": "AES",
                "SNMPv3Enabled": True,
            },
        },
        iface_doc={
            "StaticNameServers": ["1.1.1.1", "8.8.8.8"],
        },
        desired_hostname="ilo-b",
        shared_dns=["9.9.9.9"],
        snmp_policy_enabled=True,
        requested_username="ops-user",
        desired_auth_protocol="SHA",
        desired_priv_protocol="DES",
    )

    assert result["hostname_matched"] is False
    assert result["dns_matched"] is False
    assert result["snmp_matched"] is False
    assert result["matched"] is False


def test_upload_windows_image_rejects_non_ova_ovf(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Upload Kit"
    main.save_kit_config(cfg)
    response = client.post(
        "/upload-windows-image",
        data={"return_page": "windows"},
        files={"windows_image": ("bad.iso", b"fake", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert "Only .ova or .ovf uploads are supported." in response.text


def test_upload_windows_image_and_plan_dry_run(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Plan Kit"
    cfg["windows"]["vm_name"] = "win-plan"
    cfg["windows"]["admin_password"] = "Secret123!"
    cfg["windows"]["vsphere_host"] = "esxi.local"
    cfg["windows"]["vsphere_username"] = "root"
    cfg["windows"]["vsphere_datastore"] = "datastore1"
    cfg["windows"]["vsphere_network"] = "VM Network"
    main.save_kit_config(cfg)
    upload = client.post(
        "/upload-windows-image",
        data={"return_page": "windows"},
        files={"windows_image": ("template.ova", b"ova-bytes", "application/octet-stream")},
    )
    assert upload.status_code == 200
    assert "Windows image uploaded" in upload.text
    plan = client.post("/plan-windows-install", data={"return_page": "windows"})
    assert plan.status_code == 200
    assert "Windows install plan preview" in plan.text
    saved = main.load_kit_config("Windows-Plan-Kit")
    install_plan = saved["windows"].get("install_plan") or {}
    assert install_plan.get("mode") == "dry_run"
    assert install_plan.get("ready") is True
    assert install_plan.get("vsphere_host") == "esxi.local"
    assert install_plan.get("datastore") == "datastore1"
    assert install_plan.get("network") == "VM Network"
    deployment_preview = install_plan.get("deployment_preview") or {}
    assert deployment_preview.get("mode") == "dry_run"
    assert deployment_preview.get("target", {}).get("datastore") == "datastore1"


def test_register_windows_local_ovf_path_validates_sidecars_and_plans(client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Local OVF Kit"
    cfg["windows"]["vm_name"] = "win-local-ovf"
    cfg["windows"]["admin_password"] = "Secret123!"
    cfg["windows"]["vsphere_host"] = "esxi.local"
    cfg["windows"]["vsphere_username"] = "root"
    cfg["windows"]["vsphere_datastore"] = "datastore1"
    cfg["windows"]["vsphere_network"] = "VM Network"
    main.save_kit_config(cfg)
    ovf_path = tmp_path / "template.ovf"
    disk_path = tmp_path / "template-disk.vmdk"
    nvram_path = tmp_path / "template.nvram"
    disk_path.write_bytes(b"disk")
    nvram_path.write_bytes(b"nvram")
    ovf_path.write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References>
            <File ovf:id="file1" ovf:href="template-disk.vmdk"/>
            <File ovf:id="file2" ovf:href="template.nvram"/>
          </References>
            <NetworkSection><Network ovf:name="VM Network"/></NetworkSection>
          <DiskSection><Disk ovf:capacity="100" ovf:capacityAllocationUnits="byte * 2^30"/></DiskSection>
          <VirtualSystem ovf:id="Windows-Template">
            <OperatingSystemSection><Description>Windows Server 2022</Description></OperatingSystemSection>
            <VirtualHardwareSection>
              <System><VirtualSystemType>vmx-19</VirtualSystemType></System>
              <Item><ResourceType>3</ResourceType><VirtualQuantity>2</VirtualQuantity></Item>
              <Item><ResourceType>4</ResourceType><VirtualQuantity>4096</VirtualQuantity><AllocationUnits>MegaBytes</AllocationUnits></Item>
            </VirtualHardwareSection>
          </VirtualSystem>
        </Envelope>
        """,
        encoding="utf-8",
    )

    response = client.post(
        "/register-windows-ovf-path",
        data={"return_page": "windows", "windows_ovf_path": str(ovf_path)},
    )
    assert response.status_code == 200
    assert "Windows OVF source registered" in response.text
    saved = main.load_kit_config("Windows-Local-OVF-Kit")
    assert saved["windows"]["source_image_origin"] == "local_path"
    assert saved["windows"]["source_image_kind"] == "ovf"
    assert len(saved["windows"]["source_image_files"]) == 3

    plan = client.post("/plan-windows-install", data={"return_page": "windows"})
    assert plan.status_code == 200
    saved = main.load_kit_config("Windows-Local-OVF-Kit")
    install_plan = saved["windows"].get("install_plan") or {}
    assert install_plan.get("ready") is True
    source_summary = install_plan.get("source_summary") or {}
    assert source_summary.get("vm_name") == "Windows-Template"
    assert source_summary.get("network_names") == ["VM Network"]
    assert source_summary.get("cpu_count") == "2"
    assert source_summary.get("memory_mb") == "4096 MegaBytes"
    assert source_summary.get("disk_capacity") == "100 byte * 2^30"
    deployment_preview = install_plan.get("deployment_preview") or {}
    assert deployment_preview.get("source", {}).get("file_count") == 3
    assert deployment_preview.get("template", {}).get("hardware_version") == "vmx-19"
    assert deployment_preview.get("target", {}).get("vm_name") == "win-local-ovf"


def test_ovf_templates_register_directory_and_windows_selects_template(client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Template Kit"
    cfg["windows"]["vm_name"] = "win-from-template"
    cfg["windows"]["admin_password"] = "Secret123!"
    cfg["windows"]["vsphere_host"] = "esxi.local"
    cfg["windows"]["vsphere_username"] = "root"
    cfg["windows"]["vsphere_datastore"] = "datastore1"
    cfg["windows"]["vsphere_network"] = "VM Network"
    main.save_kit_config(cfg)
    ovf_path = tmp_path / "template.ovf"
    disk_path = tmp_path / "template-disk.vmdk"
    nvram_path = tmp_path / "template.nvram"
    disk_path.write_bytes(b"disk")
    nvram_path.write_bytes(b"nvram")
    ovf_path.write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References>
            <File ovf:id="file1" ovf:href="template-disk.vmdk"/>
            <File ovf:id="file2" ovf:href="template.nvram"/>
          </References>
          <NetworkSection><Network ovf:name="VM Network"/></NetworkSection>
          <VirtualSystem ovf:id="Reusable-Template"/>
        </Envelope>
        """,
        encoding="utf-8",
    )

    registered = client.post(
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
    assert "OVF template registered" in registered.text
    saved = main.load_kit_config("OVF-Template-Kit")
    templates = (saved.get("ovf_templates") or {}).get("templates") or {}
    template_id = next(iter(templates))
    assert templates[template_id]["file_count"] == 3
    assert templates[template_id]["directory"] == str(tmp_path)
    assert templates[template_id]["source_location_type"] == "local"
    assert templates[template_id]["readiness"]["ready"] is True

    selected = client.post(
        "/select-windows-ovf-template",
        data={"return_page": "windows", "windows_ovf_template_id": template_id},
    )
    assert selected.status_code == 200
    assert "Windows OVF template selected" in selected.text
    saved = main.load_kit_config("OVF-Template-Kit")
    assert saved["windows"]["source_image_origin"] == "ovf_template"
    assert saved["windows"]["source_image_folder"] == str(tmp_path)

    plan = client.post("/plan-windows-install", data={"return_page": "windows"})
    assert plan.status_code == 200
    saved = main.load_kit_config("OVF-Template-Kit")
    assert saved["windows"]["install_plan"]["ready"] is True
    assert saved["windows"]["install_plan"]["deployment_preview"]["source"]["file_count"] == 3


def test_ovf_template_directory_requires_descriptor_choice_when_multiple_ovfs(client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF Multi Kit"
    main.save_kit_config(cfg)
    (tmp_path / "one.ovf").write_text("<Envelope/>", encoding="utf-8")
    (tmp_path / "two.ovf").write_text("<Envelope/>", encoding="utf-8")

    response = client.post(
        "/modules/ovf-templates/register-directory",
        data={"return_page": "ovf_templates", "ovf_template_directory": str(tmp_path)},
    )

    assert response.status_code == 200
    assert "Multiple .ovf descriptors were found." in response.text
    assert "one.ovf" in response.text
    assert "two.ovf" in response.text


def test_netapp_backed_ovf_template_blocks_windows_until_storage_ready(client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF NetApp Blocked Kit"
    cfg["windows"]["vm_name"] = "win-netapp"
    cfg["windows"]["admin_password"] = "Secret123!"
    cfg["windows"]["vsphere_host"] = "esxi.local"
    cfg["windows"]["vsphere_username"] = "root"
    cfg["windows"]["vsphere_datastore"] = "netapp_nfs"
    cfg["windows"]["vsphere_network"] = "VM Network"
    main.save_kit_config(cfg)
    ovf_path = tmp_path / "template.ovf"
    disk_path = tmp_path / "template-disk.vmdk"
    disk_path.write_bytes(b"disk")
    ovf_path.write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References><File ovf:id="file1" ovf:href="template-disk.vmdk"/></References>
          <NetworkSection><Network ovf:name="VM Network"/></NetworkSection>
          <VirtualSystem ovf:id="NetApp-Template"/>
        </Envelope>
        """,
        encoding="utf-8",
    )

    response = client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "NetApp Template",
            "ovf_source_location_type": "netapp",
        },
    )
    assert response.status_code == 200
    saved = main.load_kit_config("OVF-NetApp-Blocked-Kit")
    template_id = next(iter((saved.get("ovf_templates") or {}).get("templates") or {}))
    template = saved["ovf_templates"]["templates"][template_id]
    assert template["source_location_type"] == "netapp"
    assert template["readiness"]["ready"] is False

    selected = client.post(
        "/select-windows-ovf-template",
        data={"return_page": "windows", "windows_ovf_template_id": template_id},
    )
    assert selected.status_code == 200
    assert "NetApp-backed OVF source needs a ready NetApp VMware/NFS datastore probe first." in selected.text


def test_netapp_backed_ovf_template_allows_windows_when_probe_ready(client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "OVF NetApp Ready Kit"
    cfg["windows"]["vm_name"] = "win-netapp"
    cfg["windows"]["admin_password"] = "Secret123!"
    cfg["windows"]["vsphere_host"] = "esxi.local"
    cfg["windows"]["vsphere_username"] = "root"
    cfg["windows"]["vsphere_datastore"] = "netapp_nfs"
    cfg["windows"]["vsphere_network"] = "VM Network"
    cfg["netapp"]["vmware_checks"] = {"nfs_mount": {"ready": True, "datastore_name": "netapp_nfs"}}
    main.save_kit_config(cfg)
    ovf_path = tmp_path / "template.ovf"
    disk_path = tmp_path / "template-disk.vmdk"
    disk_path.write_bytes(b"disk")
    ovf_path.write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References><File ovf:id="file1" ovf:href="template-disk.vmdk"/></References>
          <NetworkSection><Network ovf:name="VM Network"/></NetworkSection>
          <VirtualSystem ovf:id="NetApp-Template"/>
        </Envelope>
        """,
        encoding="utf-8",
    )

    response = client.post(
        "/modules/ovf-templates/register-directory",
        data={
            "return_page": "ovf_templates",
            "ovf_template_directory": str(tmp_path),
            "ovf_template_name": "NetApp Template",
            "ovf_source_location_type": "netapp",
        },
    )
    assert response.status_code == 200
    saved = main.load_kit_config("OVF-NetApp-Ready-Kit")
    template_id = next(iter((saved.get("ovf_templates") or {}).get("templates") or {}))

    selected = client.post(
        "/select-windows-ovf-template",
        data={"return_page": "windows", "windows_ovf_template_id": template_id},
    )
    assert selected.status_code == 200
    assert "Windows OVF template selected" in selected.text
    plan = client.post("/plan-windows-install", data={"return_page": "windows"})
    assert plan.status_code == 200
    saved = main.load_kit_config("OVF-NetApp-Ready-Kit")
    assert saved["windows"]["install_plan"]["ready"] is True


def test_windows_install_plan_warns_on_ovf_network_mismatch(client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Network Mismatch Kit"
    cfg["windows"]["vm_name"] = "win-network"
    cfg["windows"]["admin_password"] = "Secret123!"
    cfg["windows"]["vsphere_host"] = "esxi.local"
    cfg["windows"]["vsphere_username"] = "root"
    cfg["windows"]["vsphere_datastore"] = "datastore1"
    cfg["windows"]["vsphere_network"] = "Prod Network"
    ovf_path = tmp_path / "template.ovf"
    disk_path = tmp_path / "disk.vmdk"
    disk_path.write_bytes(b"disk")
    ovf_path.write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References><File ovf:id="file1" ovf:href="disk.vmdk"/></References>
          <NetworkSection><Network ovf:name="VM Network"/></NetworkSection>
          <VirtualSystem ovf:id="Windows-Template"/>
        </Envelope>
        """,
        encoding="utf-8",
    )
    cfg["windows"]["source_image_path"] = str(ovf_path)
    cfg["windows"]["source_image_name"] = "template.ovf"
    cfg["windows"]["source_image_kind"] = "ovf"
    main.save_kit_config(cfg)

    response = client.post("/plan-windows-install", data={"return_page": "windows"})
    assert response.status_code == 200
    saved = main.load_kit_config("Windows-Network-Mismatch-Kit")
    install_plan = saved["windows"].get("install_plan") or {}
    assert install_plan.get("ready") is False
    assert "Saved VM network 'Prod Network' does not match OVF network(s): VM Network." in install_plan.get("warnings", [])
    assert install_plan.get("deployment_preview", {}).get("warnings")


def test_register_windows_local_ovf_path_rejects_missing_sidecar(client, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Broken OVF Kit"
    main.save_kit_config(cfg)
    ovf_path = tmp_path / "broken.ovf"
    ovf_path.write_text(
        """
        <Envelope xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
          <References><File ovf:id="file1" ovf:href="missing.vmdk"/></References>
        </Envelope>
        """,
        encoding="utf-8",
    )

    response = client.post(
        "/register-windows-ovf-path",
        data={"return_page": "windows", "windows_ovf_path": str(ovf_path)},
    )
    assert response.status_code == 200
    assert "OVF referenced file is missing: missing.vmdk" in response.text
    saved = main.load_kit_config("Windows-Broken-OVF-Kit")
    assert not saved["windows"].get("source_image_path")


def test_windows_install_plan_warns_when_vsphere_target_is_missing(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Missing Target Kit"
    cfg["windows"]["vm_name"] = "win-plan"
    cfg["windows"]["admin_password"] = "Secret123!"
    image_path = main.EXPORTS_DIR / "windows-images" / "template.ova"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"ova-bytes")
    cfg["windows"]["source_image_path"] = str(image_path)
    cfg["windows"]["source_image_name"] = "template.ova"
    cfg["windows"]["source_image_kind"] = "ova"
    main.save_kit_config(cfg)

    response = client.post("/plan-windows-install", data={"return_page": "windows"})
    assert response.status_code == 200
    saved = main.load_kit_config("Windows-Missing-Target-Kit")
    install_plan = saved["windows"].get("install_plan") or {}
    assert install_plan.get("ready") is False
    assert "vSphere host is missing." in install_plan.get("warnings", [])
    assert "Datastore is missing." in install_plan.get("warnings", [])


def test_windows_probe_routes_require_saved_credentials(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Windows Probe Kit"
    cfg["windows"]["vsphere_host"] = "esxi.local"
    cfg["windows"]["vsphere_username"] = "root"
    cfg["windows"]["winrm_username"] = "Administrator"
    main.save_kit_config(cfg)

    vsphere = client.post("/probe-windows-vsphere", data={"return_page": "windows"})
    assert vsphere.status_code == 200
    assert "vSphere probe failed: host, username, and password are required." in vsphere.text

    winrm = client.post("/probe-windows-winrm", data={"return_page": "windows"})
    assert winrm.status_code == 200
    assert "WinRM probe failed: host, username, and password are required." in winrm.text


def test_vsphere_ovf_validation_reports_missing_runtime_inputs(tmp_path):
    missing = VsphereClient.validate_ovf_inputs(
        {
            "image_path": str(tmp_path / "missing.ova"),
            "image_kind": "ova",
            "vsphere_host": "",
            "vsphere_username": "",
            "datastore": "",
            "network": "",
        }
    )
    assert missing["ready"] is False
    assert "Windows source image file is missing." in missing["warnings"]
    assert "vSphere host is missing." in missing["warnings"]

    image_path = tmp_path / "template.ovf"
    image_path.write_text("<ovf/>", encoding="utf-8")
    ready = VsphereClient.validate_ovf_inputs(
        {
            "image_path": str(image_path),
            "image_kind": "ovf",
            "vsphere_host": "esxi.local",
            "vsphere_username": "root",
            "datastore": "datastore1",
            "network": "VM Network",
        }
    )
    assert ready["ready"] is True
    assert ready["warnings"] == []


def test_netapp_execution_launch_options_offer_safe_apply():
    cfg = main.default_config()
    cfg["included"]["netapp"] = True

    launch = main.build_execution_launch_options(cfg, "netapp")
    mode = main.execution_mode_for_scope("netapp")

    assert launch["real"] is not None
    assert launch["real"]["scope"] == "netapp"
    assert "safe apply" in launch["real"]["label"].lower()
    assert mode["key"] == "safe_apply"
    assert mode["real_changes"] == "Yes"


def test_netapp_validation_checks_require_bootstrap_complete():
    cfg = main.default_config()
    cfg["netapp"]["host"] = "10.10.8.45"
    cfg["netapp"]["username"] = "admin"
    cfg["netapp"]["password"] = "secret"
    cfg["netapp"]["bootstrap_complete"] = False

    checks = main.build_validation_checks(cfg, "netapp")
    bootstrap_check = next(item for item in checks if item["label"] == "Bootstrap complete")

    assert bootstrap_check["ok"] is False
    assert "bootstrap" in bootstrap_check["details"].lower()
