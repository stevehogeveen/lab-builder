import pytest
import yaml
from pathlib import Path
from typing import Any
from fastapi.testclient import TestClient

from app.ilo import ILOClient, ILOConfig, ILOError
from app.esxi.kickstart import build_kickstart
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
    assert cfg["included"]["ilo"] is True


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
    assert "Open Storage setup" in response.text
    assert "Read current iLO" in response.text
    assert "This is filled in from Global Settings unless you replace it here." in response.text


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
    assert "Global Settings" in global_response.text
    assert "Save the shared defaults here once." in global_response.text
    assert "Default addresses" in global_response.text
    assert "Save shared defaults" in global_response.text

    esxi_response = client.get("/esxi")
    assert esxi_response.status_code == 200
    assert "ESXi setup" in esxi_response.text
    assert "The server address, gateway, and DNS come from Global Settings" in esxi_response.text
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
    assert "Latest Live Summary" in response.text
    assert "serial_number: ABC123" in response.text
    assert "Download current iLO summary" in response.text
    assert "Download raw iLO data" in response.text


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
    assert "Target server" in response.text
    assert "Current storage setup" in response.text
    assert "Current storage setup loaded" in response.text
    assert "The current storage layout is now ready to review." in response.text
    assert "Storage setup uses the final iLO address from the iLO page by default." in response.text
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
    assert "Current storage setup loaded" in response.text
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

    assert resolved["resolved"] == "10.10.8.89"
    assert resolved["source"] == "planned iLO target IP"
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
    assert "Reserved as the data-side hot spare" in response.text
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
    assert "Reserved as the data-side hot spare" in plan_text
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


def test_approve_storage_plan_saves_exact_artifact_paths_for_later_ilo_run(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Approval Kit"
    cfg["ilo"]["target_ip"] = "10.10.8.90"
    cfg["ip_plan"]["ilo"] = "10.10.8.90"
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
    assert "Run summary" in response.text
    assert "Stages that will run" in response.text
    assert "View details" in response.text
    assert "Open summary" in response.text
    assert "Open reports & technical details" in response.text
    assert "Storage will be applied during the real run" in response.text


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
    assert "Review run before execution" in response.text
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
    assert "The Windows workflow needs the saved administrator password before a real run." in response.text
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
    assert "Storage will stay out of the run until it is reviewed and approved." in response.text
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
    assert "Preview / safety mode" in response.text
    assert "Preview only" in response.text
    assert "Mode" in response.text
    assert "What this does" in response.text
    assert "Checks the run and prepares a preview." in response.text
    assert "Real changes made" in response.text
    assert "No" in response.text
    assert "Next step" in response.text
    assert "Run for real when everything looks ready." in response.text
    assert "Start preview run" in response.text
    assert "Run for real" in response.text
    assert "/execute-preview" in response.text
    assert "/execute" in response.text
    assert "execution-layout" in response.text
    assert "execution-matrix-item" in response.text


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


def test_execute_real_scope_starts_esxi_path(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Execute Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = "esxisecret"
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


def test_prepare_execute_enables_real_launch_for_esxi_scope(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "ESXi Launch Review Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = "esxisecret"
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
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: main.Path("/tmp/base-esxi.iso"))

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
    assert "Review run before execution" in response.text
    assert "Saved kit values from the ESXi Setup page and shared defaults" in response.text
    assert "Management IP: 10.10.8.10" in response.text
    assert "Root password: Saved" in response.text
    assert "Built ISO path:" in response.text
    assert "esxi-20260416-121500/esxi-20260416-121500.iso" in response.text
    assert "Virtual media URL:" in response.text
    assert "http://lab-builder.local:8000/esxi-built-iso/ESXi-Launch-Review-Kit/esxi-20260416-121500.iso" in response.text
    assert "Manual test defaults: Manual test script defaults are not used by Run Center" in response.text
    assert 'name="esxi_run_stamp" value="20260416-121500"' in response.text
    stage_section = response.text.split("Stages that will run", 1)[1].split("Review before you start", 1)[0]
    assert "iLO" not in stage_section


def test_prepare_execute_accepts_multiple_selected_runs(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Multi Review Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["root_password"] = "esxisecret"
    cfg["windows"]["vm_name"] = "lab-win"
    cfg["windows"]["admin_password"] = "windowssecret"
    main.save_kit_config(cfg)

    response = client.post(
        "/prepare-execute",
        data={"selected_scopes": ["esxi", "windows"], "return_page": "execution"},
    )

    assert response.status_code == 200
    stage_section = response.text.split("Stages that will run", 1)[1].split("Review before you start", 1)[0]
    assert "ESXi" in stage_section
    assert "Windows" in stage_section
    assert "QNAP" not in stage_section
    assert "A real run is not available for this review yet." in response.text


def test_run_esxi_real_builds_iso_and_starts_virtual_media_boot(monkeypatch, tmp_path):
    cfg = main.default_config()
    cfg["site"]["name"] = "Real ESXi Run Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["esxi"]["hostname"] = "esxi-lab"
    cfg["esxi"]["management_ip"] = "10.10.8.10"
    cfg["esxi"]["subnet_mask"] = "255.255.255.0"
    cfg["esxi"]["gateway"] = "10.10.8.1"
    cfg["esxi"]["dns_servers"] = ["1.1.1.1", ""]
    cfg["esxi"]["root_password"] = "esxisecret"

    built_iso = tmp_path / "esxi-20260416-120000.iso"
    built_iso.write_text("iso", encoding="utf-8")
    built: dict[str, object] = {}

    def fake_build_custom_iso(spec):
        built["spec"] = spec
        (built_iso.parent / "build-summary.yml").write_text(
            yaml.safe_dump(
                {
                    "generation": {
                        "ks_cfg": {"generated": True},
                        "boot_cfg": {"patched": True},
                        "efi_boot_cfg": {"present": True, "patched": True},
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return built_iso

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
            return {"PowerState": self.power_state, **self.boot_state}

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            self.calls.append(("power_reset", reset_type, system_path))
            if reset_type in {"GracefulShutdown", "ForceOff"}:
                self.power_state = "Off"
            elif reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

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
                "after_enabled": after["BootSourceOverrideEnabled"],
                "after_target": after["BootSourceOverrideTarget"],
                "matched": True,
                "notes": ["Verified one-time boot override."],
            }

    created_clients = []

    def build_client(cfg_obj):
        client = FakeEsxiILOClient(cfg_obj)
        created_clients.append(client)
        return client

    monkeypatch.setattr(main, "build_custom_iso", fake_build_custom_iso)
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: main.Path("/tmp/base-esxi.iso"))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: {"host": host, "port": 443, "attempts": 2})
    monkeypatch.setattr(main, "ILOClient", build_client)

    main.run_esxi_real(cfg, run_stamp="20260416-120000")

    job = main.load_job("Real ESXi Run Kit")
    joined_logs = "\n".join(job["logs"])
    client = created_clients[0]
    spec = built["spec"]

    assert "[RUNNING] Building custom ESXi ISO" in joined_logs
    assert "[RUNNING] Generating KS.CFG" in joined_logs
    assert "[OK] KS.CFG generated" in joined_logs
    assert "[INFO] ESXi install values: hostname=esxi-lab, management_ip=10.10.8.10, subnet_mask=255.255.255.0, gateway=10.10.8.1, dns=1.1.1.1" in joined_logs
    assert "[INFO] root_password=SET (policy-valid=no)" in joined_logs
    assert "[INFO] Optional settings: vlan=(none), ntp=(none), ssh=yes, disable_ipv6=yes" in joined_logs
    assert "[INFO] Base ISO: /tmp/base-esxi.iso" in joined_logs
    assert "[OK] BOOT.CFG patched" in joined_logs
    assert "[OK] EFI/BOOT/BOOT.CFG patched" in joined_logs
    assert f"[OK] Built ESXi ISO: {built_iso}" in joined_logs
    assert "[INFO] Virtual media URL: http://lab-builder.local:8000/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso" in joined_logs
    assert "[RUNNING] Ejecting previous virtual media" in joined_logs
    assert "[RUNNING] Powering server off before setting one-time boot" in joined_logs
    assert "[OK] Server is off" in joined_logs
    assert "[RUNNING] Mounting custom ESXi ISO" in joined_logs
    assert "[OK] Virtual media mounted" in joined_logs
    assert "[RUNNING] Setting one-time boot to CD/DVD" in joined_logs
    assert "[INFO] Boot override before: enabled=Disabled target=None" in joined_logs
    assert "[OK] One-time boot set to CD/DVD" in joined_logs
    assert "[INFO] Boot override after: enabled=Once target=Cd" in joined_logs
    assert "[RUNNING] Powering server on" in joined_logs
    assert "[RUNNING] Waiting for ESXi management network on 10.10.8.10" in joined_logs
    assert "[OK] ESXi responded on configured IP 10.10.8.10:443 after 2 checks. ESXi boot sequence started." in joined_logs
    assert "esxisecret" not in joined_logs
    assert job["status"] == "Completed"
    assert job["esxi_iso_path"] == str(built_iso)
    assert job["esxi_iso_url"].endswith("/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso")
    assert job["esxi_expected_ip"] == "10.10.8.10"
    assert job["esxi_trace_path"].endswith("/esxi-run-trace.yml")
    assert spec.hostname == "esxi-lab"
    assert spec.management_ip == "10.10.8.10"
    assert spec.subnet_mask == "255.255.255.0"
    assert spec.gateway == "10.10.8.1"
    assert spec.dns_servers == ["1.1.1.1"]
    assert spec.root_password == "esxisecret"
    assert spec.output_name == "esxi-20260416-120000"
    trace_path = main.Path(job["esxi_trace_path"])
    assert trace_path.exists()
    trace = yaml.safe_load(trace_path.read_text(encoding="utf-8"))
    assert trace["install_values"]["hostname"] == "esxi-lab"
    assert trace["install_values"]["root_password_saved"] is True
    assert trace["install_values"]["root_password_policy_valid"] is False
    assert trace["artifacts"]["base_iso_path"] == "/tmp/base-esxi.iso"
    assert trace["artifacts"]["output_iso_path"] == str(built_iso)
    assert trace["artifacts"]["virtual_media_url"].endswith("/esxi-built-iso/Real-ESXi-Run-Kit/esxi-20260416-120000.iso")
    assert trace["builder_summary"]["generation"]["boot_cfg"]["patched"] is True
    assert ("eject", "/redfish/v1/Managers/1/VirtualMedia/2") in client.calls
    assert ("power_reset", "GracefulShutdown", "/redfish/v1/Systems/1") in client.calls
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
    cfg["esxi"]["root_password"] = "esxisecret"

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
            self.calls = []

        def get_virtual_media(self):
            return [{
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }]
            self.calls = []

        def eject_virtual_media(self, vm_path):
            self.calls.append(("eject", vm_path))

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {"PowerState": self.power_state, **self.boot_state}

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            self.calls.append(("power_reset", reset_type, system_path))
            if reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
            self.calls.append(("post", target, payload))

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
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: main.Path("/tmp/base-esxi.iso"))
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

    assert job["status"] == "Failed"
    assert "[RUNNING] Setting one-time boot to CD/DVD" in joined_logs
    assert "[INFO] Boot override before: enabled=Disabled target=None" in joined_logs
    assert "[FAILED] One-time boot did not stick; expected Once/Cd but got enabled=Once target=Hdd." in joined_logs
    assert "[SKIP] Server power-on blocked because one-time boot was not verified" in joined_logs
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
    cfg["esxi"]["root_password"] = "esxisecret"

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

        def get_virtual_media(self):
            return [{
                "@odata.id": "/redfish/v1/Managers/1/VirtualMedia/2",
                "Inserted": False,
                "MediaTypes": ["CD", "DVD"],
                "Actions": {
                    "#VirtualMedia.InsertMedia": {"target": "/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia"},
                },
            }]

        def eject_virtual_media(self, vm_path):
            return None

        def get_systems(self):
            return ["/redfish/v1/Systems/1"]

        def get_system(self, system_path):
            return {"PowerState": self.power_state, **self.boot_state}

        def power_reset(self, reset_type="ForceRestart", system_path=None):
            if reset_type in {"GracefulShutdown", "ForceOff"}:
                self.power_state = "Off"
            elif reset_type == "On":
                self.power_state = "On"
            return {"reset_type": reset_type, "system_path": system_path}

        def _post(self, target, payload):
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
    monkeypatch.setattr(main, "resolve_esxi_base_iso_path", lambda cfg_obj: main.Path("/tmp/base-esxi.iso"))
    monkeypatch.setattr(main, "detect_public_base_url", lambda target_host="": "http://lab-builder.local:8000")
    monkeypatch.setattr(main, "wait_for_esxi_management_ready", lambda host, **kwargs: (_ for _ in ()).throw(main.ILOError(f"ESXi did not answer on configured IP {host}:443 before timeout. Last error: timed out")))
    monkeypatch.setattr(main, "ILOClient", lambda cfg_obj: FakeEsxiILOClient(cfg_obj))

    main.run_esxi_real(cfg)
    job = main.load_job("Real ESXi Failure Kit")
    joined_logs = "\n".join(job["logs"])

    assert job["status"] == "Failed"
    assert "ESXi did not answer on configured IP 10.10.8.10:443 before timeout." in joined_logs
    assert "This usually means the kickstart network settings did not apply or the installer did not finish." in joined_logs


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
        root_password="esxisecret",
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


def test_run_ilo_real_executes_storage_when_included(monkeypatch):
    fake_clock = {"now": 0.0}

    monkeypatch.setattr(main.time, "sleep", lambda seconds: fake_clock.__setitem__("now", fake_clock["now"] + seconds))
    monkeypatch.setattr(main.time, "time", lambda: fake_clock["now"])

    cfg = main.default_config()
    cfg["site"]["name"] = "Real Storage Review Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
    cfg["ilo"]["username"] = "Administrator"
    cfg["ilo"]["password"] = "secret"
    cfg["ilo"]["target_ip"] = "10.10.8.91"
    cfg["ilo"]["gateway"] = "10.10.8.1"
    cfg["shared_network"]["dns_servers"] = ["1.1.1.1", "", "", ""]
    cfg["shared_snmp"]["v3_username"] = "snmpuser"
    cfg["shared_snmp"]["v3_auth_password"] = "authpass"
    cfg["shared_snmp"]["v3_priv_password"] = "privpass"

    discovery = planner_gen10_apply_discovery(existing_volumes=True)
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
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
    job = main.load_job("Real Storage Review Kit")
    joined_logs = "\n".join(job["logs"])
    client = created_clients[0]

    assert "[RUNNING] Starting the approved storage stage after the iLO stage finished." in joined_logs
    assert "Submitted the consolidated SmartStorageConfig pending payload" in joined_logs
    assert "DNS apply attempt" in joined_logs
    assert "DNS verified" in joined_logs
    assert "SNMP apply attempt" in joined_logs
    assert "SNMP verified" in joined_logs
    assert "iLO reset requested" in joined_logs
    assert "iLO reset completed and the final iLO endpoint is reachable on 10.10.8.91" in joined_logs
    assert "auth_password=set | priv_password=set" in joined_logs
    assert job["storage_run_directory"]
    assert job["dns_apply_status"] == "Verified"
    assert job["dns_applied_values"] == ["1.1.1.1"]
    assert job["dns_before_values"] == ["8.8.8.8"]
    assert job["snmp_apply_status"] == "Verified"
    assert job["snmp_username"] == "snmpuser"
    assert job["snmp_auth_secret_present"] is True
    assert job["snmp_priv_secret_present"] is True
    assert job["snmp_verified_checks"]
    assert job["storage_server_reboot_status"] == "Completed"
    assert job["ilo_reset_status"] == "Completed"
    assert job["ilo_stage_finished"] is True
    assert job["ilo_final_ip_verified"] is True
    assert client.dns_calls == [["1.1.1.1"]]
    assert client.snmp_calls == [{
        "v3_username": "snmpuser",
        "v3_auth_protocol": "SHA",
        "v3_auth_password": "authpass",
        "v3_priv_protocol": "AES",
        "v3_priv_password": "privpass",
    }]
    assert client.manager_reset_calls == [{"reset_type": "GracefulRestart"}]
    assert joined_logs.index("iLO reset completed and the final iLO endpoint is reachable") < joined_logs.index("Starting the approved storage stage after the iLO stage finished.")
    assert job["run_bundle_dir"]
    assert Path(job["run_bundle_dir"]).is_dir()
    assert Path(job["run_live_log_path"]).is_file()
    assert Path(job["run_trace_path"]).is_file()
    assert Path(job["run_config_snapshot_path"]).is_file()
    assert "iLO reset completed and the final iLO endpoint is reachable" in Path(job["run_live_log_path"]).read_text(encoding="utf-8")
    trace_text = Path(job["run_trace_path"]).read_text(encoding="utf-8")
    assert "trace_events:" in trace_text or "events:" in trace_text
    assert str(Path(job["run_bundle_dir"])) in trace_text


def test_run_ilo_real_fails_when_ilo_reset_cannot_be_verified(monkeypatch):
    fake_clock = {"now": 0.0}

    monkeypatch.setattr(main.time, "sleep", lambda seconds: fake_clock.__setitem__("now", fake_clock["now"] + seconds))
    monkeypatch.setattr(main.time, "time", lambda: fake_clock["now"])

    cfg = main.default_config()
    cfg["site"]["name"] = "Real Reset Verify Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
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
    export_paths = main.export_storage_discovery_snapshot(cfg, discovery, host="10.10.8.90")
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
    assert "Detailed execution logs are saved with the run" in response.text


def test_ilo_page_shows_last_run_dns_snmp_and_reset_states(client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "ILO Result Kit"
    cfg["ilo"]["current_ip"] = "10.10.8.90"
    cfg["ilo"]["host"] = "10.10.8.90"
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
    assert "Choose a kit" in response.text
    assert "Create a new kit" in response.text
    assert "Use an existing kit" in response.text
    assert "Open current config" in response.text
    assert "Download current config" in response.text
    assert "Start with one step" in response.text
    assert "Review ESXi setup" not in response.text
    assert "Open run history" in response.text
    assert 'name="selected_kit"' in response.text
    assert 'name="new_kit_name"' in response.text
    assert 'type="file"' not in response.text
    assert "Per-kit deployment dashboard for offline builds." in response.text
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
    selector_section = response.text.split('name="selected_kit"', 1)[1]
    assert ">Older-Dash-Kit<" in selector_section
    assert ">Primary-Dash-Kit<" not in selector_section


def test_dashboard_job_status_lists_passed_and_failed_with_dates(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Dash Status Kit"
    main.save_kit_config(cfg)
    main.save_history(
        "Dash-Status-Kit",
        [
            {"time": "2026-04-17 10:30:00", "scope": "esxi", "status": "Failed"},
            {"time": "2026-04-17 09:15:00", "scope": "ilo", "status": "Completed"},
        ],
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Job status" in response.text
    assert "iLO run passed" in response.text
    assert "2026-04-17 09:15:00" in response.text
    assert "ESXi run failed" in response.text
    assert "2026-04-17 10:30:00" in response.text


def test_create_new_kit_updates_active_kit_on_dashboard(client):
    response = client.post(
        "/new-kit",
        data={"new_kit_name": "Fresh Kit", "return_page": "dashboard"},
    )

    assert response.status_code == 200
    assert "Active kit" in response.text
    assert "Fresh-Kit" in response.text


def test_reports_page_hides_live_jobs_and_config_capture_blocks(client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Reports Kit"
    main.save_kit_config(cfg)

    response = client.get("/configs")

    assert response.status_code == 200
    assert "Reports & technical details" in response.text
    assert "Live job and logs" not in response.text
    assert "Capture current iLO" not in response.text
    assert "Saved intended config" not in response.text


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
    assert "Run bundles" in response.text
    assert "Search reports" in response.text
    assert "Report browser" in response.text
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
    assert "Open run summary" in history_response.text
    assert "Related reports" in history_response.text

    configs_response = client.get("/configs")
    assert configs_response.status_code == 200
    assert "Live job and logs" in configs_response.text
    assert "Recent history" in configs_response.text
    assert "Run bundles" in configs_response.text
    assert "Open bundle" in configs_response.text
    assert "Load a saved kit config" in configs_response.text


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
