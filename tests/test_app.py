import pytest
import yaml
from typing import Any
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
                    "controllers": [{"name": "Smart Array", "firmware_version": {"Current": {"VersionString": "1.98"}}}],
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

    def get_storage_discovery(self, deep_smart_storage_scan=False):
        del deep_smart_storage_scan
        return self.discovery

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
        return {
            "path": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            "system_path": "/redfish/v1/Systems/1",
            "reset_type": reset_type,
            "reboot_start_observed": True,
            "reboot_start_detail": "Observed BootProgress state after reset request: POST.",
            "system_returned": True,
            "return_detail": "System returned with PowerState=On.",
        }


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
            "ilo_gateway": "10.10.8.254",
            "ilo_hostname": "ilo-focused",
            "ilo_username": "Administrator",
            "ilo_password": "secret",
            "included_ilo": "on",
        },
    )

    assert response.status_code == 200
    cfg = main.load_kit_config("Ilo-Page-Kit")
    assert cfg["ilo"]["current_ip"] == "10.10.8.50"
    assert cfg["ilo"]["gateway"] == "10.10.8.254"
    assert cfg["ilo"]["hostname"] == "ilo-focused"
    assert cfg["included"]["ilo"] is True


def test_save_esxi_windows_and_qnap_page_settings(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Workflow Kit"
    main.save_kit_config(cfg)

    client.post("/save-esxi-settings", data={"return_page": "esxi", "esxi_hostname": "esxi-lab", "esxi_root_password": "secret", "included_esxi": "on"})
    client.post("/save-windows-settings", data={"return_page": "windows", "windows_vm_name": "win-lab", "windows_admin_password": "secret", "included_windows": "on"})
    client.post("/save-qnap-settings", data={"return_page": "qnap", "qnap_hostname": "qnap-lab", "qnap_username": "admin", "qnap_password": "secret", "included_qnap": "on"})

    cfg = main.load_kit_config("Workflow-Kit")
    assert cfg["esxi"]["hostname"] == "esxi-lab"
    assert cfg["windows"]["vm_name"] == "win-lab"
    assert cfg["qnap"]["hostname"] == "qnap-lab"


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
    assert "Review storage setup" in response.text
    assert "Gen11" in response.text
    assert "iLO 6" in response.text
    assert "MR416i-o" in response.text
    assert "1.98" in response.text
    assert "{&#39;Current&#39;:" not in response.text
    assert "OS Volume" in response.text
    assert "RAID1" in response.text
    assert "HPE SSD" in response.text
    assert "Review storage setup" in response.text
    assert "Using right now:" in response.text
    assert "10.10.8.60" in response.text
    assert "current kit iLO IP" in response.text
    assert "Sign-in user:" in response.text
    assert 'hx-indicator="#read-storage-progress"' in response.text
    assert "Checking current storage on the server." in response.text

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


def test_storage_target_host_prefers_current_kit_ip_over_artifact_host():
    cfg = main.default_config()
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
    cfg["storage"]["latest_host"] = "10.10.8.92"

    resolved = main.resolve_storage_target_host(cfg)

    assert resolved["resolved"] == "10.10.8.92"
    assert resolved["source"] == "latest discovery artifact"
    assert resolved["artifact_fallback"] is True


def test_storage_target_host_reports_clear_error_when_no_host_is_resolved():
    cfg = main.default_config()
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
    assert "Saved storage target settings." in response.text
    saved = main.load_kit_config("Storage-Target-Kit")
    assert saved["storage"]["target_host_override"] == "10.10.8.99"
    assert saved["storage"]["username"] == "StorageAdmin"
    assert saved["storage"]["password"] == "storage-secret"


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
    assert "What will happen" in response.text
    assert "Review storage setup" in response.text
    assert "Selected server:" in response.text
    assert "Server:" in response.text
    assert "10.10.8.80" in response.text
    assert "This server already has storage set up:" in response.text
    assert "wipe and rebuild" in response.text
    assert "SSD-480" in response.text
    assert "HDD-1200" in response.text
    assert "Oddball" in response.text
    assert "Hot spare" in response.text
    assert "Reserved as the data-side hot spare" in response.text
    assert "Planned layout" in response.text
    assert "target size 500 GiB on bays 1, 2" in response.text
    assert "3, 4, 5, 6" in response.text
    assert "remaining compatible eligible drives after reserving one hot spare" in response.text
    assert "bay 8" in response.text
    assert "What would change" in response.text
    assert "Existing storage that would be removed" in response.text
    assert "New layout that would be created" in response.text
    assert "Reserved hot spare:" in response.text
    assert "Storage setup status" in response.text
    assert "Approve this storage setup" in response.text
    assert "Approve for setup" in response.text
    assert "Include this approved storage plan in the later iLO run" in response.text
    plan_path = export_paths["directory"] / "raid-plan.yml"
    assert plan_path.exists()
    plan_text = plan_path.read_text(encoding="utf-8")
    assert "source_discovery:" in plan_text
    assert "default_recommendation: wipe and rebuild" in plan_text
    assert "hot_spare:" in plan_text
    assert "typed_confirmation: WIPE STORAGE" in plan_text
    assert "Reserved as the data-side hot spare" in plan_text
    assert "Not in the selected RAID 6 compatible media/protocol/capacity" in plan_text


def test_approve_storage_plan_saves_exact_artifact_paths_for_later_ilo_run(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Approval Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
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
    assert "Storage approved" in response.text
    assert "Included in iLO run: Yes" in response.text
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
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
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
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
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
    assert "Run review" in response.text
    assert "Included stages" in response.text
    assert "View full run details" in response.text
    assert "Pre-run review:" in response.text
    assert "Storage in the upcoming iLO run" in response.text
    assert "Storage approved" in response.text
    assert "Review approved storage" in response.text
    assert "/storage#storage-approval-actions" in response.text
    assert str(export_paths["raw"]) in response.text
    assert str(plan_paths["plan"]) in response.text
    assert "Storage included -&gt; Yes" in response.text or "Storage included -> Yes" in response.text


def test_execution_page_warns_when_storage_is_not_approved(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Exec Warn Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["included"]["storage"] = False
    main.save_kit_config(cfg)

    response = client.get("/execution")

    assert response.status_code == 200
    assert "Storage in the upcoming iLO run" in response.text
    assert "Storage not approved" in response.text
    assert "Storage and RAID will not be configured during the iLO run until they are reviewed and approved" in response.text
    assert "Open Storage / RAID" in response.text
    assert "/storage#storage-review-start" in response.text


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


def test_build_raid_plan_blocks_when_no_compatible_data_spare_remains():
    discovery = planner_discovery_without_data_spare()
    discovery_paths = {
        "directory": main.Path("/tmp/storage-plan-test"),
        "summary": main.Path("/tmp/storage-plan-test/summary.yml"),
        "raw": main.Path("/tmp/storage-plan-test/raw.json"),
    }

    plan = main.build_raid_plan(discovery, discovery_paths)

    assert plan["data_raid6"]["drive_count"] == 4
    assert plan["hot_spare"]["reserved"] is False
    assert plan["apply_readiness"]["wipe_rebuild_ready"] is False
    assert plan["planned_layout"]["hot_spare"]["bay"] == ""
    assert plan["pre_apply_summary"]["planned_layout"]["data_raid6"]["bays"] == "3, 4, 5, 6"
    assert any("hot spare" in blocker.lower() for blocker in plan["blockers"])


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
    resolved = main.resolve_storage_target_host(cfg)

    assert resolved["valid"] is False
    assert resolved["source"] == ""
    assert "current kit iLO IP/host" in resolved["error"]


def test_apply_storage_layout_blocks_create_only_when_not_ready(client, monkeypatch):
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
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
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
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
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
    assert "Apply attempt artifacts" in response.text
    assert "View Apply Log" in response.text
    assert "Storage setup progress" in response.text
    assert "Restart needed to finish" in response.text
    assert "Storage changes are staged, but they will not finish until the server restarts." in response.text
    assert "Reboot Required" in response.text
    assert "Reboot Machine Now" in response.text
    assert "This button sends a real Redfish reset request through iLO and waits for the server to return." in response.text
    assert "Reboot Now" in response.text
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
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "kit-password"
    main.save_kit_config(cfg)

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
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
    assert "Storage Workflow Progress" in response.text
    assert "Fully complete" in response.text
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
    ]
    assert plan["hot_spare"]["drive"]["smart_storage_location"] == "1I:1:8"

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
    ]
    assert exported_plan["hot_spare"]["drive"]["smart_storage_location"] == "1I:1:8"


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
