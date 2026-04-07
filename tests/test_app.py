import pytest
from fastapi.testclient import TestClient

from app.ilo import ILOClient, ILOConfig, ILOError
import app.main as main


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
                    "controllers": [{"name": "Smart Array", "firmware_version": "1.23"}],
                    "volumes": [{"id": "1", "name": "Volume1"}],
                    "drives": [{"id": "1", "name": "Drive1"}],
                },
                "manager_ethernet_interfaces": [{"id": "1", "name": "Manager NIC"}],
                "system_ethernet_interfaces": [{"id": "NIC1", "name": "NIC 1"}],
            },
            "raw": {
                "service_root": {"RedfishVersion": "1.18.0"},
                "manager": {"FirmwareVersion": "3.00"},
                "system": {"Model": "ProLiant DL380 Gen11"},
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
                            "firmware_version": "1.23",
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


@pytest.fixture()
def client(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    kits_dir = config_dir / "kits"
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

    for path in (
        config_dir,
        kits_dir,
        generated_dir,
        jobs_dir,
        history_dir,
        ilo_export_dir,
        config_export_dir,
        live_ilo_config_dir,
        ilo_inventory_dir,
        ilo_live_export_dir,
        storage_raid_export_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(main, "KITS_DIR", kits_dir)
    monkeypatch.setattr(main, "CURRENT_KIT_FILE", config_dir / "current_kit.txt")
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
    main.set_current_kit_name("Kit-01")

    with TestClient(main.app) as test_client:
        yield test_client


def test_navigation_pages_render(client):
    for path in ["/", "/dashboard", "/execution", "/configuration", "/configs", "/storage", "/kits", "/history"]:
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
    assert "Live Inventory Status" in response.text
    assert "Summary file:" in response.text
    assert "Raw file:" in response.text
    assert "Host: 10.10.8.50" in response.text

    latest = main.latest_live_inventory_export()
    assert latest is not None
    assert latest["summary"].name == "summary.yml"
    assert latest["raw"].name == "raw.json"

    view_response = client.post("/view-latest-live-summary", data={"return_page": "configs"})
    assert view_response.status_code == 200
    assert "Latest Live Summary" in view_response.text
    assert "Live Inventory Status" in view_response.text
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
    assert "Storage / RAID" in response.text
    assert "Gen11" in response.text
    assert "iLO 6" in response.text
    assert "MR416i-o" in response.text
    assert "OS Volume" in response.text
    assert "RAID1" in response.text
    assert "HPE SSD" in response.text
    assert "Export path:" in response.text

    export_dir = main.STORAGE_RAID_EXPORT_DIR / "ABC123" / "20260407-150000"
    summary_path = export_dir / "summary.yml"
    raw_path = export_dir / "raw.json"
    assert summary_path.exists()
    assert raw_path.exists()
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "generation: Gen11" in summary_text
    assert "standard_redfish_storage: true" in summary_text


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
    assert "Smart Array P408i-a SR Gen10" in response.text
    assert "HPE Smart Storage controller detected" in response.text
    assert "Smart Storage traversal diagnostics" in response.text
    assert "Deep fallback ran: True" in response.text
    assert "LogicalDrives" in response.text
    assert "DiskDrives" in response.text


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
