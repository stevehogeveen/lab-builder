from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import ipaddress
import re
import time
import requests
import urllib3
from requests.auth import HTTPBasicAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class ILOConfig:
    host: str
    username: str
    password: str
    verify_tls: bool = False
    timeout: int = 15


class ILOError(Exception):
    pass


class ILOClient:
    SMART_STORAGE_PROBE_TIMEOUT = 3

    @staticmethod
    def _normalize_string_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _values_match_exact(actual: list[str], requested: list[str]) -> bool:
        return list(actual) == list(requested)

    @staticmethod
    def _first_present(block: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            if key in block:
                return block.get(key)
        return None

    def _safe_get(self, path: str | None, timeout: int | float | None = None) -> dict[str, Any]:
        if not path:
            return {}
        try:
            return self._get(path, timeout=timeout)
        except TypeError as e:
            if timeout is not None and "unexpected keyword argument 'timeout'" in str(e):
                try:
                    return self._get(path)
                except Exception as inner:
                    return {"@error": str(inner), "@path": path}
            return {"@error": str(e), "@path": path}
        except Exception as e:
            return {"@error": str(e), "@path": path}

    def _expand_collection(self, collection_path: str | None) -> list[dict[str, Any]]:
        if not collection_path:
            return []
        collection = self._safe_get(collection_path)
        members = collection.get("Members", [])
        items = []
        for member in members:
            path = member.get("@odata.id")
            if path:
                items.append(self._safe_get(path))
        return items

    def _build_processor_summary(self, processors: list[dict[str, Any]]) -> dict[str, Any]:
        models = []
        total_processors = 0
        total_cores = 0
        total_threads = 0

        for item in processors:
            total_processors += 1
            model = item.get("Model") or item.get("ProcessorType") or item.get("Name") or ""
            if model:
                models.append(model)
            total_cores += int(item.get("TotalCores") or 0)
            total_threads += int(item.get("TotalThreads") or 0)

        return {
            "model": models[0] if models else "",
            "count": total_processors,
            "total_cores": total_cores,
            "total_threads": total_threads,
            "items": [
                {
                    "id": item.get("Id", ""),
                    "name": item.get("Name", ""),
                    "model": item.get("Model", ""),
                    "manufacturer": item.get("Manufacturer", ""),
                    "socket": item.get("Socket", ""),
                    "cores": item.get("TotalCores", 0),
                    "threads": item.get("TotalThreads", 0),
                    "max_speed_mhz": item.get("MaxSpeedMHz", None),
                    "instruction_set": item.get("InstructionSet", ""),
                    "status": item.get("Status", {}),
                }
                for item in processors
            ],
        }

    def _build_memory_summary(self, memory: list[dict[str, Any]], system: dict[str, Any]) -> dict[str, Any]:
        total_gib = system.get("MemorySummary", {}).get("TotalSystemMemoryGiB")
        if total_gib in (None, ""):
            total_mib = 0
            for dimm in memory:
                total_mib += int(dimm.get("CapacityMiB") or 0)
            total_gib = round(total_mib / 1024, 2) if total_mib else 0

        return {
            "total_gib": total_gib,
            "dimm_count": len(memory),
            "dimms": [
                {
                    "id": item.get("Id", ""),
                    "name": item.get("Name", ""),
                    "device_locator": item.get("DeviceLocator", ""),
                    "capacity_mib": item.get("CapacityMiB", 0),
                    "memory_type": item.get("MemoryDeviceType", "") or item.get("MemoryType", ""),
                    "base_speed_mhz": item.get("OperatingSpeedMhz", None) or item.get("BaseSpeedMHz", None),
                    "manufacturer": item.get("Manufacturer", ""),
                    "part_number": item.get("PartNumber", ""),
                    "serial_number": item.get("SerialNumber", ""),
                    "status": item.get("Status", {}),
                }
                for item in memory
            ],
        }

    def _build_storage_summary(self, storage_subsystems: list[dict[str, Any]]) -> dict[str, Any]:
        controllers = []
        volumes = []
        drives = []

        for storage in storage_subsystems:
            for controller in storage.get("StorageControllers", []) or []:
                controllers.append(
                    {
                        "name": controller.get("Name", ""),
                        "model": controller.get("Model", ""),
                        "firmware_version": controller.get("FirmwareVersion", ""),
                        "manufacturer": controller.get("Manufacturer", ""),
                        "serial_number": controller.get("SerialNumber", ""),
                        "speed_gbps": controller.get("SpeedGbps", None),
                        "status": controller.get("Status", {}),
                    }
                )
            for volume in storage.get("VolumesExpanded", []) or []:
                volumes.append(
                    {
                        "id": volume.get("Id", ""),
                        "name": volume.get("Name", ""),
                        "raid_type": volume.get("RAIDType", ""),
                        "capacity_bytes": volume.get("CapacityBytes", 0),
                        "encrypted": volume.get("Encrypted", None),
                        "status": volume.get("Status", {}),
                    }
                )
            for drive in storage.get("DrivesExpanded", []) or []:
                drives.append(
                    {
                        "id": drive.get("Id", ""),
                        "name": drive.get("Name", ""),
                        "model": drive.get("Model", ""),
                        "manufacturer": drive.get("Manufacturer", ""),
                        "serial_number": drive.get("SerialNumber", ""),
                        "media_type": drive.get("MediaType", ""),
                        "protocol": drive.get("Protocol", ""),
                        "capacity_bytes": drive.get("CapacityBytes", 0),
                        "status": drive.get("Status", {}),
                    }
                )

        return {
            "controllers": controllers,
            "volumes": volumes,
            "drives": drives,
        }

    def _storage_capacity_gib(self, value: Any) -> float | None:
        try:
            capacity = int(value or 0)
        except Exception:
            return None
        if capacity <= 0:
            return None
        return round(capacity / 1024 / 1024 / 1024, 2)

    def _storage_capacity_mib_to_gib(self, value: Any) -> float | None:
        try:
            capacity = int(value or 0)
        except Exception:
            return None
        if capacity <= 0:
            return None
        return round(capacity / 1024, 2)

    def _infer_server_generation(self, model: str) -> str:
        text = (model or "").lower().replace(" ", "")
        if "gen11" in text or "g11" in text:
            return "Gen11"
        if "gen10plus" in text or "gen10+" in text or "g10plus" in text:
            return "Gen10+"
        if "gen10" in text or "g10" in text:
            return "Gen10"
        return ""

    def _infer_ilo_version(self, manager: dict[str, Any]) -> str:
        text = " ".join(str(manager.get(key, "")) for key in ("Model", "Name", "ManagerType"))
        for version in ("iLO 6", "iLO 5"):
            if version.lower() in text.lower():
                return version
        return str(manager.get("Model", ""))

    def _storage_status_text(self, item: dict[str, Any]) -> str:
        status = item.get("Status", {})
        if not isinstance(status, dict):
            return ""
        return " / ".join([x for x in (status.get("Health"), status.get("State")) if x])

    def _storage_drive_bay(self, item: dict[str, Any]) -> str:
        location = item.get("PhysicalLocation", {})
        part_location = location.get("PartLocation", {}) if isinstance(location, dict) else {}
        placement = part_location.get("LocationOrdinalValue") if isinstance(part_location, dict) else None
        return str(item.get("BayNumber") or item.get("Location") or placement or item.get("Id") or "")

    def _normalize_standard_storage(self, storage_subsystems: list[dict[str, Any]]) -> dict[str, Any]:
        controllers = []
        volumes = []
        drives = []

        for storage in storage_subsystems:
            storage_path = storage.get("@odata.id", "")
            for controller in storage.get("StorageControllers", []) or []:
                controllers.append(
                    {
                        "path": storage_path,
                        "name": controller.get("Name") or controller.get("MemberId") or "",
                        "model": controller.get("Model", ""),
                        "firmware_version": controller.get("FirmwareVersion", ""),
                        "manufacturer": controller.get("Manufacturer", ""),
                        "status": self._storage_status_text(controller),
                    }
                )

            for volume in storage.get("VolumesExpanded", []) or []:
                volumes.append(
                    {
                        "path": volume.get("@odata.id", ""),
                        "id": volume.get("Id", ""),
                        "name": volume.get("Name", ""),
                        "raid_type": volume.get("RAIDType") or volume.get("VolumeType") or "",
                        "capacity_gib": self._storage_capacity_gib(volume.get("CapacityBytes")),
                        "status": self._storage_status_text(volume),
                    }
                )

            for drive in storage.get("DrivesExpanded", []) or []:
                drives.append(
                    {
                        "path": drive.get("@odata.id", ""),
                        "id": drive.get("Id", ""),
                        "bay": self._storage_drive_bay(drive),
                        "name": drive.get("Name", ""),
                        "model": drive.get("Model", ""),
                        "serial_number": drive.get("SerialNumber", ""),
                        "size_gib": self._storage_capacity_gib(drive.get("CapacityBytes")),
                        "media_type": drive.get("MediaType", ""),
                        "protocol": drive.get("Protocol", ""),
                        "status": self._storage_status_text(drive),
                    }
                )

        return {"controllers": controllers, "volumes": volumes, "drives": drives}

    def _normalize_smart_storage_controller(self, controller: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": controller.get("@odata.id", ""),
            "id": controller.get("Id", ""),
            "name": controller.get("Name") or controller.get("Model") or "",
            "model": controller.get("Model") or controller.get("ControllerName") or "",
            "firmware_version": controller.get("FirmwareVersion") or controller.get("Firmware", ""),
            "manufacturer": controller.get("Manufacturer", "HPE" if controller else ""),
            "status": self._storage_status_text(controller),
        }

    def _normalize_smart_storage_volume(self, volume: dict[str, Any]) -> dict[str, Any]:
        data_drive_link = ((volume.get("Links") or {}).get("DataDrives") or {}).get("@odata.id", "")
        spare_drive_link = (
            ((volume.get("Links") or {}).get("StandbySpareDrives") or {}).get("@odata.id", "")
            or ((volume.get("Links") or {}).get("SpareDrives") or {}).get("@odata.id", "")
        )
        return {
            "path": volume.get("@odata.id", ""),
            "id": volume.get("Id", ""),
            "name": volume.get("LogicalDriveName") or volume.get("Name") or "",
            "logical_drive_name": volume.get("LogicalDriveName") or "",
            "raid_type": volume.get("RAIDType") or volume.get("Raid") or volume.get("LogicalDriveType") or "",
            "capacity_gib": self._storage_capacity_gib(volume.get("CapacityBytes")) or self._storage_capacity_mib_to_gib(volume.get("CapacityMiB")),
            "status": self._storage_status_text(volume) or str(volume.get("Status", "")),
            "data_drives_path": data_drive_link,
            "spare_drives_path": spare_drive_link,
            "drive_bays": [],
            "spare_bays": [],
        }

    def _normalize_smart_storage_drive(self, drive: dict[str, Any]) -> dict[str, Any]:
        smart_location = str(
            drive.get("Location")
            or drive.get("DriveLocation")
            or drive.get("LocationString")
            or drive.get("SlotLocation")
            or ""
        ).strip()
        bay = str(drive.get("Bay") or drive.get("BayNumber") or drive.get("Id") or "")
        if smart_location:
            match = re.search(r"(\d+)$", smart_location)
            if match:
                bay = match.group(1)
            elif not bay:
                bay = smart_location
        return {
            "path": drive.get("@odata.id", ""),
            "id": drive.get("Id", ""),
            "bay": bay,
            "name": drive.get("Name", ""),
            "model": drive.get("Model") or drive.get("ModelNumber") or "",
            "serial_number": drive.get("SerialNumber", ""),
            "size_gib": self._storage_capacity_gib(drive.get("CapacityBytes")) or self._storage_capacity_mib_to_gib(drive.get("CapacityMiB")),
            "media_type": drive.get("MediaType") or drive.get("DriveMediaType") or "",
            "protocol": drive.get("Protocol") or drive.get("InterfaceType") or "",
            "status": self._storage_status_text(drive) or str(drive.get("Status", "")),
            "smart_storage_location": smart_location,
            "smart_storage_location_format": str(drive.get("LocationFormat") or drive.get("DriveLocationFormat") or "").strip(),
        }

    def _expand_storage_collection_ref(self, owner: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = owner.get(key)
        if isinstance(value, dict):
            return self._expand_collection(value.get("@odata.id"))
        if isinstance(value, list):
            expanded = []
            for item in value:
                if isinstance(item, dict) and item.get("@odata.id"):
                    expanded.append(self._safe_get(item.get("@odata.id")))
            return expanded
        return []

    def _record_smart_storage_doc_probe(self, diagnostics: dict[str, Any], path: str, doc: dict[str, Any], phase: str) -> None:
        diagnostics["probed_paths"].append(
            {
                "phase": phase,
                "path": path,
                "status": "error" if doc.get("@error") else "ok",
                "exists": not bool(doc.get("@error")),
                "error": doc.get("@error", ""),
                "name": doc.get("Name", ""),
                "members": len(doc.get("Members", []) or []),
            }
        )

    def _record_smart_storage_found_path(self, diagnostics: dict[str, Any], path: str, source: str, key: str) -> None:
        if not path:
            return
        entry = {
            "path": path,
            "source": source,
            "key": key,
        }
        if entry not in diagnostics["found_paths"]:
            diagnostics["found_paths"].append(entry)

    def _record_smart_storage_followed_link(
        self,
        diagnostics: dict[str, Any],
        owner: str,
        key: str,
        path: str,
        phase: str,
        source: str,
    ) -> None:
        if not path:
            return
        entry = {
            "owner": owner,
            "key": key,
            "path": path,
            "phase": phase,
            "source": source,
        }
        if entry not in diagnostics["followed_links"]:
            diagnostics["followed_links"].append(entry)

    def _record_smart_storage_collection_result(self, diagnostics: dict[str, Any], key: str, status: str) -> None:
        counts = diagnostics["collection_counts"].setdefault(
            key,
            {"total": 0, "populated": 0, "empty": 0, "error": 0},
        )
        counts["total"] += 1
        counts[status] = counts.get(status, 0) + 1

    def _collect_smart_storage_candidates(
        self,
        value: Any,
        diagnostics: dict[str, Any] | None = None,
        source: str = "",
    ) -> list[str]:
        candidates = []
        target_keys = {"smartstorage", "smartstorageconfig", "arraycontrollers"}

        def walk(node: Any, parent_key: str = "") -> None:
            if isinstance(node, dict):
                lower_parent = parent_key.lower()
                if lower_parent in target_keys and node.get("@odata.id"):
                    candidate_path = node.get("@odata.id")
                    candidates.append(candidate_path)
                    if diagnostics is not None:
                        self._record_smart_storage_found_path(diagnostics, candidate_path, source, parent_key)
                for key, child in node.items():
                    walk(child, key)
            elif isinstance(node, list):
                for child in node:
                    walk(child, parent_key)

        walk(value)

        unique = []
        for item in candidates:
            if item and item not in unique:
                unique.append(item)
        return unique

    def _smart_storage_doc(self, path: str, seen_paths: set[str], diagnostics: dict[str, Any], phase: str) -> dict[str, Any]:
        if not path:
            return {}
        if path in diagnostics["_doc_cache"]:
            return diagnostics["_doc_cache"][path]
        doc = self._safe_get(path, timeout=self.SMART_STORAGE_PROBE_TIMEOUT)
        self._record_smart_storage_doc_probe(diagnostics, path, doc, phase)
        if not doc.get("@error"):
            seen_paths.add(path)
            diagnostics["_doc_cache"][path] = doc
        return doc

    def _smart_storage_collection_paths(self, owner: dict[str, Any], key: str, synthesize: bool) -> list[str]:
        paths = []
        value = owner.get(key)
        if isinstance(value, dict) and value.get("@odata.id"):
            paths.append(value.get("@odata.id"))

        owner_path = owner.get("@odata.id", "")
        if synthesize and owner_path and not owner_path.rstrip("/").endswith(f"/{key}"):
            paths.append(f"{owner_path.rstrip('/')}/{key}")

        unique = []
        for path in paths:
            if path and path not in unique:
                unique.append(path)
        return unique

    def _expand_smart_storage_collection(
        self,
        owner: dict[str, Any],
        key: str,
        seen_paths: set[str],
        diagnostics: dict[str, Any],
        phase: str,
        synthesize: bool = False,
    ) -> list[dict[str, Any]]:
        expanded = []
        owner_path = owner.get("@odata.id", "")
        value = owner.get(key)

        if isinstance(value, list):
            member_paths = [item.get("@odata.id") for item in value if isinstance(item, dict) and item.get("@odata.id")]
            diagnostics["collections"].append(
                {
                    "owner": owner_path,
                    "collection": key,
                    "phase": phase,
                    "path": "",
                    "status": "populated" if member_paths else "empty",
                    "members": len(member_paths),
                    "source": "inline",
                }
            )
            self._record_smart_storage_collection_result(
                diagnostics,
                key,
                "populated" if member_paths else "empty",
            )
            for member_path in member_paths:
                self._record_smart_storage_followed_link(diagnostics, owner_path, key, member_path, phase, "inline_member")
                if member_path in seen_paths:
                    expanded.append(self._smart_storage_doc(member_path, seen_paths, diagnostics, phase))
                else:
                    expanded.append(self._smart_storage_doc(member_path, seen_paths, diagnostics, phase))

        for collection_path in self._smart_storage_collection_paths(owner, key, synthesize=synthesize):
            probe_key = (owner_path, key, collection_path, phase)
            if probe_key in diagnostics["_collection_probe_cache"]:
                continue
            diagnostics["_collection_probe_cache"].add(probe_key)
            link_source = "synthetic_collection" if synthesize and (not isinstance(value, dict) or value.get("@odata.id") != collection_path) else "collection_link"
            self._record_smart_storage_followed_link(diagnostics, owner_path, key, collection_path, phase, link_source)
            collection = self._safe_get(collection_path, timeout=self.SMART_STORAGE_PROBE_TIMEOUT)
            members = collection.get("Members", []) if not collection.get("@error") else []
            status = "error" if collection.get("@error") else "populated" if members else "empty"
            diagnostics["collections"].append(
                {
                    "owner": owner_path,
                    "collection": key,
                    "phase": phase,
                    "path": collection_path,
                    "status": status,
                    "members": len(members),
                    "error": collection.get("@error", ""),
                    "source": "collection",
                }
            )
            self._record_smart_storage_collection_result(diagnostics, key, status)
            if collection.get("@error"):
                continue
            for member in members:
                member_path = member.get("@odata.id")
                if not member_path:
                    continue
                self._record_smart_storage_followed_link(diagnostics, collection_path, "Members", member_path, phase, "collection_member")
                if member_path in seen_paths:
                    expanded.append(self._smart_storage_doc(member_path, seen_paths, diagnostics, phase))
                else:
                    expanded.append(self._smart_storage_doc(member_path, seen_paths, diagnostics, phase))

        return [doc for doc in expanded if doc and not doc.get("@error")]

    def get_storage_discovery(self, deep_smart_storage_scan: bool = False) -> dict[str, Any]:
        service_root = self.get_service_root()
        manager_path = self.get_managers()[0]
        system_path = self.get_systems()[0]
        manager = self.get_manager(manager_path)
        system = self.get_system(system_path)

        storage_subsystems = []
        standard_storage_path = system.get("Storage", {}).get("@odata.id")
        if standard_storage_path:
            for storage_path in self._expand_collection(standard_storage_path):
                volumes = self._expand_collection(storage_path.get("Volumes", {}).get("@odata.id"))
                drives = []
                for drive_ref in storage_path.get("Drives", []) or []:
                    drives.append(self._safe_get(drive_ref.get("@odata.id")))
                storage_doc = dict(storage_path)
                storage_doc["VolumesExpanded"] = volumes
                storage_doc["DrivesExpanded"] = drives
                storage_subsystems.append(storage_doc)

        smart_storage_candidates = []
        smart_storage_diagnostics = {
            "probed_paths": [],
            "found_paths": [],
            "followed_links": [],
            "collections": [],
            "collection_counts": {},
            "warnings": [],
            "deep_scan_requested": deep_smart_storage_scan,
            "deep_fallback_ran": False,
            "_doc_cache": {},
            "_collection_probe_cache": set(),
        }
        smart_storage_candidates = []
        for source_name, source_value in (
            ("system", system),
            ("system_oem", system.get("Oem", {})),
            ("manager", manager),
            ("manager_oem", manager.get("Oem", {})),
            ("service_root", service_root),
        ):
            smart_storage_candidates.extend(
                self._collect_smart_storage_candidates(
                    source_value,
                    diagnostics=smart_storage_diagnostics,
                    source=source_name,
                )
            )
        for source_name, path in (
            ("guessed", f"{system_path}/SmartStorage"),
            ("guessed", f"{system_path}/SmartStorage/ArrayControllers"),
            ("guessed", f"{system_path}/SmartStorageConfig"),
            ("guessed", f"{system_path}/SmartStorageConfig/Settings"),
        ):
            smart_storage_candidates.append(path)
            self._record_smart_storage_found_path(smart_storage_diagnostics, path, source_name, "synthetic")
        unique_candidates = []
        for path in smart_storage_candidates:
            normalized = str(path or "").rstrip("/")
            if normalized and normalized not in unique_candidates:
                unique_candidates.append(normalized)
        smart_storage_candidates = unique_candidates

        smart_storage_docs = []
        seen_paths = set()
        for path in smart_storage_candidates:
            if not path or path in seen_paths:
                continue
            doc = self._smart_storage_doc(path, seen_paths, smart_storage_diagnostics, "fast_pass")
            if doc.get("@error"):
                continue
            smart_storage_docs.append(doc)
            for member in doc.get("Members", []) or []:
                member_path = member.get("@odata.id")
                if member_path and member_path not in seen_paths:
                    self._record_smart_storage_followed_link(smart_storage_diagnostics, path, "Members", member_path, "fast_pass", "root_member")
                    smart_storage_docs.append(self._smart_storage_doc(member_path, seen_paths, smart_storage_diagnostics, "fast_pass"))

        for doc in list(smart_storage_docs):
            if doc.get("@error"):
                continue
            for key in ("ArrayControllers", "Settings"):
                for child in self._expand_smart_storage_collection(doc, key, seen_paths, smart_storage_diagnostics, "fast_pass"):
                    smart_storage_docs.append(child)

        smart_controllers = []
        smart_volumes = []
        smart_drives = []
        controller_docs_seen = []
        for doc in smart_storage_docs:
            if doc.get("@error"):
                continue
            controller_docs = []
            if any(key in doc for key in ("ControllerName", "FirmwareVersion", "LogicalDrives", "DiskDrives")):
                controller_docs.append(doc)
            controller_docs.extend(self._expand_smart_storage_collection(doc, "ArrayControllers", seen_paths, smart_storage_diagnostics, "fast_pass"))

            for controller in controller_docs:
                controller_docs_seen.append(controller)
                smart_controllers.append(self._normalize_smart_storage_controller(controller))
                for volume in self._expand_smart_storage_collection(controller, "LogicalDrives", seen_paths, smart_storage_diagnostics, "fast_pass"):
                    smart_volumes.append(self._normalize_smart_storage_volume(volume))
                for volume in self._expand_smart_storage_collection(controller, "Volumes", seen_paths, smart_storage_diagnostics, "fast_pass"):
                    smart_volumes.append(self._normalize_smart_storage_volume(volume))
                for drive in self._expand_smart_storage_collection(controller, "DiskDrives", seen_paths, smart_storage_diagnostics, "fast_pass"):
                    smart_drives.append(self._normalize_smart_storage_drive(drive))
                for drive in self._expand_smart_storage_collection(controller, "Drives", seen_paths, smart_storage_diagnostics, "fast_pass"):
                    smart_drives.append(self._normalize_smart_storage_drive(drive))

            for volume in self._expand_smart_storage_collection(doc, "LogicalDrives", seen_paths, smart_storage_diagnostics, "fast_pass"):
                smart_volumes.append(self._normalize_smart_storage_volume(volume))
            for volume in self._expand_smart_storage_collection(doc, "Volumes", seen_paths, smart_storage_diagnostics, "fast_pass"):
                smart_volumes.append(self._normalize_smart_storage_volume(volume))
            for drive in self._expand_smart_storage_collection(doc, "DiskDrives", seen_paths, smart_storage_diagnostics, "fast_pass"):
                smart_drives.append(self._normalize_smart_storage_drive(drive))
            for drive in self._expand_smart_storage_collection(doc, "Drives", seen_paths, smart_storage_diagnostics, "fast_pass"):
                smart_drives.append(self._normalize_smart_storage_drive(drive))

        should_run_deep_fallback = deep_smart_storage_scan or (smart_controllers and (not smart_volumes or not smart_drives))
        if should_run_deep_fallback:
            smart_storage_diagnostics["deep_fallback_ran"] = True
            deep_docs = list(smart_storage_docs) + controller_docs_seen
            for doc in deep_docs:
                if doc.get("@error"):
                    continue
                for key in ("ArrayControllers", "LogicalDrives", "Volumes", "DiskDrives", "Drives", "Settings"):
                    children = self._expand_smart_storage_collection(
                        doc,
                        key,
                        seen_paths,
                        smart_storage_diagnostics,
                        "deep_fallback",
                        synthesize=True,
                    )
                    for child in children:
                        if child not in smart_storage_docs:
                            smart_storage_docs.append(child)
                        if key == "ArrayControllers":
                            controller_docs_seen.append(child)
                            normalized_controller = self._normalize_smart_storage_controller(child)
                            if normalized_controller not in smart_controllers:
                                smart_controllers.append(normalized_controller)
                        elif key in ("LogicalDrives", "Volumes"):
                            normalized_volume = self._normalize_smart_storage_volume(child)
                            if normalized_volume not in smart_volumes:
                                smart_volumes.append(normalized_volume)
                        elif key in ("DiskDrives", "Drives"):
                            normalized_drive = self._normalize_smart_storage_drive(child)
                            if normalized_drive not in smart_drives:
                                smart_drives.append(normalized_drive)

        drive_bay_by_path = {str(drive.get("path") or ""): str(drive.get("bay") or drive.get("id") or "") for drive in smart_drives}
        for volume in smart_volumes:
            for link_key, output_key in (("data_drives_path", "drive_bays"), ("spare_drives_path", "spare_bays")):
                linked_path = str(volume.get(link_key) or "").strip()
                if not linked_path:
                    continue
                linked_docs = self._expand_collection(linked_path)
                bays = []
                for linked_doc in linked_docs:
                    linked_doc_path = str(linked_doc.get("@odata.id") or "").strip()
                    bay = drive_bay_by_path.get(linked_doc_path)
                    if not bay:
                        bay = self._normalize_smart_storage_drive(linked_doc).get("bay", "")
                    if bay:
                        bays.append(str(bay))
                volume[output_key] = sorted(set(bays), key=lambda item: int(re.sub(r"\D+", "", item) or "999999"))

        if smart_controllers and not smart_volumes and not smart_drives:
            smart_storage_diagnostics["warnings"].append(
                "HPE Smart Storage controller detected, but no logical drives or physical drives were found in the probed child collections."
            )

        smart_storage_diagnostics.pop("_doc_cache", None)
        smart_storage_diagnostics.pop("_collection_probe_cache", None)

        standard = self._normalize_standard_storage(storage_subsystems)
        server_model = system.get("Model") or system.get("ProductName") or ""

        return {
            "summary": {
                "server": {
                    "model": server_model,
                    "product_name": system.get("ProductName", ""),
                    "generation": self._infer_server_generation(server_model),
                    "serial_number": system.get("SerialNumber", ""),
                },
                "ilo": {
                    "model": manager.get("Model", ""),
                    "version": self._infer_ilo_version(manager),
                    "firmware": manager.get("FirmwareVersion", ""),
                },
                "capabilities": {
                    "standard_redfish_storage": bool(standard_storage_path and storage_subsystems),
                    "hpe_smart_storage": bool(smart_storage_docs),
                    "standard_storage_path": standard_storage_path or "",
                    "hpe_smart_storage_paths": [doc.get("@odata.id", "") for doc in smart_storage_docs if doc.get("@odata.id")],
                    "hpe_smart_storage_diagnostics": smart_storage_diagnostics,
                },
                "standard_redfish_storage": standard,
                "hpe_smart_storage": {
                    "controllers": smart_controllers,
                    "volumes": smart_volumes,
                    "drives": smart_drives,
                    "diagnostics": smart_storage_diagnostics,
                },
            },
            "raw": {
                "service_root": service_root,
                "manager": manager,
                "system": system,
                "standard_storage": storage_subsystems,
                "hpe_smart_storage": smart_storage_docs,
                "hpe_smart_storage_diagnostics": smart_storage_diagnostics,
            },
        }

    def _build_account_summary(self, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "id": item.get("Id", ""),
                "username": item.get("UserName", ""),
                "role": item.get("RoleId", ""),
                "enabled": item.get("Enabled", None),
                "locked": item.get("Locked", None),
                "password_change_required": item.get("PasswordChangeRequired", None),
                "links": item.get("Links", {}),
            }
            for item in accounts
        ]

    def _build_ethernet_summary(self, interfaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "path": item.get("@odata.id", ""),
                "id": item.get("Id", ""),
                "name": item.get("Name", ""),
                "hostname": item.get("HostName", ""),
                "fqdn": item.get("FQDN", ""),
                "mac_address": item.get("MACAddress", ""),
                "interface_enabled": item.get("InterfaceEnabled", None),
                "link_status": item.get("LinkStatus", ""),
                "speed_mbps": item.get("SpeedMbps", None),
                "dhcpv4": item.get("DHCPv4", {}),
                "dhcpv6": item.get("DHCPv6", {}),
                "ipv4_addresses": item.get("IPv4Addresses", []),
                "ipv4_static_addresses": item.get("IPv4StaticAddresses", []),
                "ipv6_addresses": item.get("IPv6Addresses", []),
                "name_servers": item.get("NameServers", []),
                "static_name_servers": item.get("StaticNameServers", []),
                "status": item.get("Status", {}),
            }
            for item in interfaces
        ]

    def _prefix_length_from_netmask(self, subnet_mask: str) -> int:
        try:
            return ipaddress.IPv4Network(f"0.0.0.0/{subnet_mask}").prefixlen
        except Exception as e:
            raise ILOError(f"Invalid subnet mask '{subnet_mask}': {e}") from e

    def get_active_manager_interface(self) -> dict[str, Any]:
        manager_path = self.get_managers()[0]
        iface_paths = self.get_manager_ethernet_interface_paths(manager_path)

        candidates = []
        for path in iface_paths:
            item = self._get(path)
            candidates.append(item)

        # Prefer enabled + link up
        for item in candidates:
            if item.get("InterfaceEnabled") is True and str(item.get("LinkStatus", "")).lower() == "linkup":
                return item

        # Fallback to first enabled
        for item in candidates:
            if item.get("InterfaceEnabled") is True:
                return item

        # Fallback to first interface
        if candidates:
            return candidates[0]

        raise ILOError("No manager EthernetInterfaces found.")

    def get_current_config_snapshot(self) -> dict[str, Any]:
        summary = self.get_summary()
        service_root = self.get_service_root()
        manager_path = summary.get("manager_path", "")
        system_path = summary.get("system_path", "")
        manager = self.get_manager(manager_path or None)
        system = self.get_system(system_path or None)
        np_path, network_protocol = self.get_network_protocol()
        iface_paths = self.get_manager_ethernet_interface_paths(manager_path or None)
        interfaces = [self._get(path) for path in iface_paths]
        iface = self.get_active_manager_interface()
        capability_dump = self.get_capability_dump()
        processors = self._expand_collection(system.get("Processors", {}).get("@odata.id"))
        memory = self._expand_collection(system.get("Memory", {}).get("@odata.id"))
        system_ethernet = self._expand_collection(system.get("EthernetInterfaces", {}).get("@odata.id"))
        account_service = self._safe_get(service_root.get("AccountService", {}).get("@odata.id"))
        accounts = self._expand_collection(account_service.get("Accounts", {}).get("@odata.id"))

        storage_subsystems = []
        for storage_path in self._expand_collection(system.get("Storage", {}).get("@odata.id")):
            volumes = self._expand_collection(storage_path.get("Volumes", {}).get("@odata.id"))
            drives = []
            for drive_ref in storage_path.get("Drives", []) or []:
                drives.append(self._safe_get(drive_ref.get("@odata.id")))
            storage_doc = dict(storage_path)
            storage_doc["VolumesExpanded"] = volumes
            storage_doc["DrivesExpanded"] = drives
            storage_subsystems.append(storage_doc)

        processor_summary = self._build_processor_summary(processors)
        memory_summary = self._build_memory_summary(memory, system)
        storage_summary = self._build_storage_summary(storage_subsystems)
        manager_interfaces_summary = self._build_ethernet_summary(interfaces)
        system_interfaces_summary = self._build_ethernet_summary(system_ethernet)
        accounts_summary = self._build_account_summary(accounts)

        return {
            "summary": {
                "service_root": {
                    "name": summary.get("service_root_name", ""),
                    "redfish_version": summary.get("redfish_version", ""),
                },
                "manager": {
                    "path": summary.get("manager_path", ""),
                    "model": summary.get("manager_model", ""),
                    "firmware": summary.get("manager_firmware", ""),
                },
                "system": {
                    "path": summary.get("system_path", ""),
                    "manufacturer": summary.get("system_manufacturer", ""),
                    "model": summary.get("system_model", ""),
                    "product_name": system.get("ProductName", ""),
                    "serial_number": system.get("SerialNumber", ""),
                    "bios_version": system.get("BiosVersion", ""),
                    "power_state": summary.get("power_state", ""),
                },
                "network_protocol": {
                    "path": np_path,
                    "hostname": network_protocol.get("HostName", ""),
                    "fqdn": network_protocol.get("FQDN", ""),
                    "http": network_protocol.get("HTTP", {}),
                    "https": network_protocol.get("HTTPS", {}),
                    "snmp": network_protocol.get("SNMP", {}),
                },
                "active_interface": {
                    "path": iface.get("@odata.id", ""),
                    "name": iface.get("Name", ""),
                    "hostname": iface.get("HostName", ""),
                    "fqdn": iface.get("FQDN", ""),
                    "mac_address": iface.get("MACAddress", ""),
                    "interface_enabled": iface.get("InterfaceEnabled", None),
                    "link_status": iface.get("LinkStatus", ""),
                    "speed_mbps": iface.get("SpeedMbps", None),
                    "dhcpv4": iface.get("DHCPv4", {}),
                    "dhcpv6": iface.get("DHCPv6", {}),
                    "ipv4_addresses": iface.get("IPv4Addresses", []),
                    "ipv4_static_addresses": iface.get("IPv4StaticAddresses", []),
                    "ipv6_addresses": iface.get("IPv6Addresses", []),
                    "ipv6_static_addresses": iface.get("IPv6StaticAddresses", []),
                    "name_servers": iface.get("NameServers", []),
                    "static_name_servers": iface.get("StaticNameServers", []),
                    "vlan": iface.get("VLAN", {}),
                },
                "processors": processor_summary,
                "memory": memory_summary,
                "accounts": accounts_summary,
                "storage": storage_summary,
                "manager_ethernet_interfaces": manager_interfaces_summary,
                "system_ethernet_interfaces": system_interfaces_summary,
            },
            "raw": {
                "service_root": service_root,
                "manager": manager,
                "system": system,
                "network_protocol": network_protocol,
                "active_manager_interface": iface,
                "manager_ethernet_interfaces": interfaces,
                "system_ethernet_interfaces": system_ethernet,
                "processors": processors,
                "memory": memory,
                "account_service": account_service,
                "accounts": accounts,
                "storage": storage_subsystems,
                "virtual_media": summary.get("virtual_media", []),
                "capability_dump": capability_dump,
            },
        }

    def set_dns_servers_best_effort(self, dns_servers: list[str]) -> dict[str, Any]:
        dns_servers = [x.strip() for x in dns_servers if x and x.strip()]
        if not dns_servers:
            raise ILOError("No DNS servers provided.")

        iface = self.get_active_manager_interface()
        iface_path = iface.get("@odata.id")
        if not iface_path:
            raise ILOError("Active interface missing @odata.id")

        before_static = iface.get("StaticNameServers", [])
        before_names = iface.get("NameServers", [])

        patch_payload = {}

        # Most promising field on your iLO
        if "StaticNameServers" in iface:
            patch_payload["StaticNameServers"] = dns_servers

        # Some firmwares may also allow NameServers
        if not patch_payload and "NameServers" in iface:
            patch_payload["NameServers"] = dns_servers

        if not patch_payload:
            raise ILOError("No writable DNS server field found on active interface.")

        self._patch(iface_path, patch_payload)
        after = self._get(iface_path)
        before_static_normalized = self._normalize_string_list(before_static)
        before_names_normalized = self._normalize_string_list(before_names)
        after_static = self._normalize_string_list(after.get("StaticNameServers", []))
        after_names = self._normalize_string_list(after.get("NameServers", []))

        verified_field = ""
        if self._values_match_exact(after_static, dns_servers):
            verified_field = "StaticNameServers"
        elif self._values_match_exact(after_names, dns_servers):
            verified_field = "NameServers"

        verified = bool(verified_field)
        before_matched = self._values_match_exact(before_static_normalized, dns_servers) or self._values_match_exact(before_names_normalized, dns_servers)
        changed = verified and not before_matched

        mismatches = []
        if not verified:
            mismatches.append(
                {
                    "field": "dns_servers",
                    "requested": list(dns_servers),
                    "actual_static": after_static,
                    "actual_names": after_names,
                }
            )

        before = {
            "StaticNameServers": before_static_normalized,
            "NameServers": before_names_normalized,
        }
        after_result = {
            "StaticNameServers": after_static,
            "NameServers": after_names,
        }
        notes = [
            f"Wrote DNS values using {', '.join(sorted(list(patch_payload.keys())))}."
        ]
        if verified:
            notes.append(f"Requested DNS values matched {verified_field} after the write.")
        else:
            notes.append("PATCH succeeded but the requested DNS values did not read back exactly.")

        return {
            "action": "apply_dns",
            "requested": list(dns_servers),
            "path": iface_path,
            "before": before,
            "after": after_result,
            "before_static": before_static_normalized,
            "after_static": after_static,
            "before_names": before_names_normalized,
            "after_names": after_names,
            "applied_keys": sorted(list(patch_payload.keys())),
            "matched": verified,
            "mismatches": mismatches,
            "changed": changed,
            "reset_recommended": False,
            "notes": notes,
            "verified": verified,
            "verified_field": verified_field,
            "status": "Verified" if verified else "Mismatch",
            "details": notes[-1],
        }

    def set_static_ipv4_best_effort(self, address: str, subnet_mask: str, gateway: str) -> dict[str, Any]:
        if not address.strip():
            raise ILOError("Static IPv4 address is empty.")
        if not subnet_mask.strip():
            raise ILOError("Subnet mask is empty.")
        if not gateway.strip():
            raise ILOError("Gateway is empty.")

        iface = self.get_active_manager_interface()
        iface_path = iface.get("@odata.id")
        if not iface_path:
            raise ILOError("Active interface missing @odata.id")

        prefix_length = self._prefix_length_from_netmask(subnet_mask)
        before_ipv4_addresses = iface.get("IPv4Addresses", [])
        before_static_addresses = iface.get("IPv4StaticAddresses", [])
        before_dhcpv4 = iface.get("DHCPv4", {})

        static_entry = {
            "Address": address,
            "SubnetMask": subnet_mask,
            "Gateway": gateway,
        }
        static_entry_prefix = {
            "Address": address,
            "PrefixLength": prefix_length,
            "Gateway": gateway,
        }
        static_entry_full = {
            "Address": address,
            "SubnetMask": subnet_mask,
            "PrefixLength": prefix_length,
            "Gateway": gateway,
        }

        attempts = []
        payloads: list[dict[str, Any]] = []

        dhcp_block = iface.get("DHCPv4")
        if isinstance(dhcp_block, dict):
            dhcp_changes = {}
            for key in ("DHCPEnabled", "Enabled", "UseDHCP", "ProtocolEnabled"):
                if key in dhcp_block:
                    dhcp_changes[key] = False
            if dhcp_changes:
                payloads.append({"DHCPv4": dhcp_changes})

        if "IPv4StaticAddresses" in iface:
            payloads.extend([
                {"IPv4StaticAddresses": [static_entry]},
                {"IPv4StaticAddresses": [static_entry_prefix]},
                {"IPv4StaticAddresses": [static_entry_full]},
            ])

        if "IPv4Addresses" in iface:
            payloads.extend([
                {"IPv4Addresses": [static_entry]},
                {"IPv4Addresses": [static_entry_prefix]},
                {"IPv4Addresses": [static_entry_full]},
            ])

        if not payloads:
            raise ILOError("No writable DHCPv4 or static IPv4 fields found on active interface.")

        # Try DHCP disable first if supported.
        applied_keys: list[str] = []
        if isinstance(dhcp_block, dict):
            for payload in [p for p in payloads if "DHCPv4" in p]:
                try:
                    self._patch(iface_path, payload)
                    applied_keys.extend(payload.keys())
                    break
                except Exception as e:
                    attempts.append(f"DHCP patch failed: {e}")

        # Then try the static address variants until one sticks.
        for payload in [p for p in payloads if "DHCPv4" not in p]:
            try:
                self._patch(iface_path, payload)
                applied_keys.extend(payload.keys())
                after = self._get(iface_path)
                return {
                    "path": iface_path,
                    "before_ipv4_addresses": before_ipv4_addresses,
                    "after_ipv4_addresses": after.get("IPv4Addresses", []),
                    "before_static_addresses": before_static_addresses,
                    "after_static_addresses": after.get("IPv4StaticAddresses", []),
                    "before_dhcpv4": before_dhcpv4,
                    "after_dhcpv4": after.get("DHCPv4", {}),
                    "applied_keys": applied_keys,
                }
            except Exception as e:
                attempts.append(f"Static IPv4 patch failed for keys {', '.join(payload.keys())}: {e}")

        raise ILOError("Static IPv4 update failed. " + " | ".join(attempts))
    
    def __init__(self, cfg: ILOConfig):
        self.cfg = cfg
        self.base = f"https://{cfg.host}"
        self.redfish_root = f"{self.base}/redfish/v1"
        self.auth = HTTPBasicAuth(cfg.username, cfg.password)

    def get_capability_dump(self) -> dict[str, Any]:
        manager_path = self.get_managers()[0]
        np_path = self.get_network_protocol_path(manager_path)
        np = self._get(np_path)

        iface_paths = []
        iface_data = []
        try:
            iface_paths = self.get_manager_ethernet_interface_paths(manager_path)
            for p in iface_paths:
                iface_data.append(self._get(p))
        except Exception:
            pass

        def top_keys(obj: dict[str, Any]) -> list[str]:
            return sorted(list(obj.keys())) if isinstance(obj, dict) else []

        def nested_keys(obj: dict[str, Any], key: str) -> list[str]:
            val = obj.get(key, {})
            return sorted(list(val.keys())) if isinstance(val, dict) else []

        def hpe_oem_keys(obj: dict[str, Any]) -> list[str]:
            oem = obj.get("Oem", {})
            if not isinstance(oem, dict):
                return []
            hpe = oem.get("Hpe", {})
            return sorted(list(hpe.keys())) if isinstance(hpe, dict) else []

        def safe_value(obj: dict[str, Any], key: str):
            return obj.get(key)

        return {
            "manager_path": manager_path,
            "network_protocol_path": np_path,
            "network_protocol_keys": top_keys(np),
            "snmp_keys": nested_keys(np, "SNMP"),
            "snmp_object": np.get("SNMP", {}),
            "network_protocol_oem_keys": nested_keys(np, "Oem"),
            "network_protocol_oem_hpe_keys": hpe_oem_keys(np),
            "ethernet_interfaces": [
                {
                    "path": item.get("@odata.id", ""),
                    "keys": top_keys(item),
                    "ipv4_addresses": safe_value(item, "IPv4Addresses"),
                    "ipv4_static_addresses": safe_value(item, "IPv4StaticAddresses"),
                    "dhcpv4": safe_value(item, "DHCPv4"),
                    "ipv6_addresses": safe_value(item, "IPv6Addresses"),
                    "ipv6_static_addresses": safe_value(item, "IPv6StaticAddresses"),
                    "dhcpv6": safe_value(item, "DHCPv6"),
                    "name_servers": safe_value(item, "NameServers"),
                    "static_name_servers": safe_value(item, "StaticNameServers"),
                    "vlan": safe_value(item, "VLAN"),
                    "oem_keys": nested_keys(item, "Oem"),
                    "oem_hpe_keys": hpe_oem_keys(item),
                    "host_name": item.get("HostName", ""),
                    "fqdn": item.get("FQDN", ""),
                    "interface_enabled": item.get("InterfaceEnabled", None),
                    "link_status": item.get("LinkStatus", ""),
                }
                for item in iface_data
            ],
        }
        
    def _get(self, path: str, timeout: int | float | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.base}{path}"
        effective_timeout = self.cfg.timeout if timeout is None else timeout
        r = requests.get(
            url,
            auth=self.auth,
            verify=self.cfg.verify_tls,
            timeout=effective_timeout,
        )
        if r.status_code >= 400:
            raise ILOError(f"GET {url} failed with HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except Exception as e:
            raise ILOError(f"GET {url} returned non-JSON response: {e}") from e

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        url = path if path.startswith("http") else f"{self.base}{path}"
        r = requests.post(
            url,
            json=payload or {},
            auth=self.auth,
            verify=self.cfg.verify_tls,
            timeout=self.cfg.timeout,
        )
        if r.status_code >= 400:
            raise ILOError(f"POST {url} failed with HTTP {r.status_code}: {r.text[:300]}")
        if not r.text.strip():
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        url = path if path.startswith("http") else f"{self.base}{path}"
        r = requests.patch(
            url,
            json=payload,
            auth=self.auth,
            verify=self.cfg.verify_tls,
            timeout=self.cfg.timeout,
        )
        if r.status_code >= 400:
            raise ILOError(f"PATCH {url} failed with HTTP {r.status_code}: {r.text[:500]}")
        if not r.text.strip():
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _delete(self, path: str) -> dict[str, Any] | None:
        url = path if path.startswith("http") else f"{self.base}{path}"
        r = requests.delete(
            url,
            auth=self.auth,
            verify=self.cfg.verify_tls,
            timeout=self.cfg.timeout,
        )
        if r.status_code >= 400:
            raise ILOError(f"DELETE {url} failed with HTTP {r.status_code}: {r.text[:500]}")
        if not r.text.strip():
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _put(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        url = path if path.startswith("http") else f"{self.base}{path}"
        r = requests.put(
            url,
            json=payload,
            auth=self.auth,
            verify=self.cfg.verify_tls,
            timeout=self.cfg.timeout,
        )
        if r.status_code >= 400:
            raise ILOError(f"PUT {url} failed with HTTP {r.status_code}: {r.text[:500]}")
        if not r.text.strip():
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _smart_storage_settings_parent(self, settings_path: str) -> str:
        if not settings_path:
            return ""
        match = re.match(r"^(.*?)/settings/?$", settings_path, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        return settings_path.rstrip("/")

    def _smart_storage_get_pending_config(self, settings_path: str) -> dict[str, Any]:
        settings_doc = self._safe_get(settings_path)
        if settings_doc and not settings_doc.get("@error"):
            return settings_doc
        current_path = self._smart_storage_settings_parent(settings_path)
        current_doc = self._safe_get(current_path)
        if current_doc and not current_doc.get("@error"):
            return current_doc
        error = settings_doc.get("@error") or current_doc.get("@error") or "unknown settings lookup error"
        raise ILOError(f"SmartStorageConfig settings path could not be read: {settings_path} ({error})")

    def _smart_storage_reboot_required(self, response: dict[str, Any] | None) -> bool:
        if not response:
            return True
        if response.get("reboot_required") is not None:
            return bool(response.get("reboot_required"))
        messages = response.get("Messages") or response.get("messages") or []
        if isinstance(messages, list):
            for item in messages:
                text = str(item.get("MessageId") or item.get("Message") or item)
                if "reset" in text.lower() or "reboot" in text.lower():
                    return True
        return True

    def _required_drive_locations(self, intent: dict[str, Any], expected_count: int, kind: str) -> list[str]:
        drive_locations = []
        for drive in intent.get("drives", []) or []:
            location = str(drive.get("smart_storage_location") or drive.get("location") or "").strip()
            if not location:
                bay = str(drive.get("bay") or "").strip()
                raise ILOError(
                    f"{kind} drive mapping is incomplete. Missing Smart Storage location for planned bay {bay or 'unknown'}."
                )
            drive_locations.append(location)
        if len(drive_locations) != expected_count:
            raise ILOError(f"{kind} requires exactly {expected_count} mapped drives, found {len(drive_locations)}.")
        return drive_locations

    def _logical_drive_payload(self, logical_drive_kind: str, intent: dict[str, Any]) -> dict[str, Any]:
        if logical_drive_kind == "os_raid1":
            drive_locations = self._required_drive_locations(intent, 2, "OS RAID 1")
            return {
                "LogicalDriveName": "OS RAID 1",
                "Raid": "Raid1",
                "CapacityGiB": int(intent.get("target_size_gib") or 500),
                "DataDrives": drive_locations,
            }
        if logical_drive_kind == "data_raid6":
            drive_locations = self._required_drive_locations(
                intent,
                len(intent.get("drives", []) or []),
                "Data RAID 6",
            )
            if len(drive_locations) < 4:
                raise ILOError(f"Data RAID 6 requires at least 4 mapped drives, found {len(drive_locations)}.")
            return {
                "LogicalDriveName": "Data RAID 6",
                "Raid": "Raid6",
                "DataDrives": drive_locations,
            }
        raise ILOError(f"Unsupported Gen10 logical drive kind: {logical_drive_kind}")

    def _smart_storage_delete_payload_item(self, volume_path: str) -> dict[str, Any]:
        if not volume_path:
            raise ILOError("Logical drive delete requires a logical drive path.")
        volume = self._safe_get(volume_path)
        if volume.get("@error"):
            raise ILOError(f"Logical drive metadata could not be read before delete: {volume.get('@error')}")
        volume_uid = str(
            volume.get("VolumeUniqueIdentifier")
            or volume.get("VolumeUniqueID")
            or volume.get("LogicalDriveUniqueIdentifier")
            or ""
        ).strip()
        if not volume_uid:
            raise ILOError(f"Logical drive delete requires VolumeUniqueIdentifier at {volume_path}.")
        return {
            "VolumeUniqueIdentifier": volume_uid,
            "Actions": [{"Action": "LogicalDriveDelete"}],
        }

    def build_gen10_storage_config_payload(
        self,
        settings_path: str,
        apply_mode: str,
        existing_volume_paths: list[str],
        os_intent: dict[str, Any],
        data_intent: dict[str, Any],
        spare_intent: dict[str, Any],
    ) -> dict[str, Any]:
        if not settings_path:
            raise ILOError("Gen10 SmartStorageConfig payload generation requires the SmartStorageConfig settings path.")
        if apply_mode not in {"create_only", "wipe_rebuild"}:
            raise ILOError(f"Unsupported Gen10 apply mode: {apply_mode}")

        logical_drives: list[dict[str, Any]] = []
        if apply_mode == "create_only":
            pending = self._smart_storage_get_pending_config(settings_path)
            logical_drives.extend(list(pending.get("LogicalDrives") or []))
        else:
            for volume_path in existing_volume_paths:
                logical_drives.append(self._smart_storage_delete_payload_item(volume_path))

        os_payload = self._logical_drive_payload("os_raid1", os_intent)
        data_payload = self._logical_drive_payload("data_raid6", data_intent)
        spare_drive = (spare_intent.get("drive") or {})
        spare_location = str(spare_drive.get("smart_storage_location") or spare_drive.get("location") or "").strip()
        if not spare_location:
            raise ILOError(
                f"Hot-spare mapping is incomplete. Missing Smart Storage location for planned bay {spare_intent.get('bay') or 'unknown'}."
            )
        data_payload["SpareDrives"] = [spare_location]
        data_payload["SpareRebuildMode"] = "Dedicated"

        logical_drives.extend([os_payload, data_payload])
        return {
            "DataGuard": "Permissive" if apply_mode == "wipe_rebuild" else "Disabled",
            "LogicalDrives": logical_drives,
        }

    def apply_gen10_storage_layout(
        self,
        settings_path: str,
        apply_mode: str,
        existing_volume_paths: list[str],
        os_intent: dict[str, Any],
        data_intent: dict[str, Any],
        spare_intent: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = self.build_gen10_storage_config_payload(
            settings_path=settings_path,
            apply_mode=apply_mode,
            existing_volume_paths=existing_volume_paths,
            os_intent=os_intent,
            data_intent=data_intent,
            spare_intent=spare_intent,
        )
        response = self._put(settings_path, payload)
        return {
            "settings_path": settings_path,
            "apply_mode": apply_mode,
            "deleted_volume_paths": list(existing_volume_paths),
            "delete_count": len(existing_volume_paths),
            "create_count": 2,
            "hot_spare_location": ((spare_intent.get("drive") or {}).get("smart_storage_location") or ""),
            "payload": payload,
            "response": response,
            "reboot_required": self._smart_storage_reboot_required(response),
        }

    def delete_storage_logical_drive(self, volume_path: str, settings_path: str = "") -> dict[str, Any] | None:
        if not settings_path:
            raise ILOError("Logical drive delete requires the SmartStorageConfig settings path.")
        payload_item = self._smart_storage_delete_payload_item(volume_path)
        payload = {
            "DataGuard": "Permissive",
            "LogicalDrives": [payload_item],
        }
        response = self._put(settings_path, payload)
        return {
            "settings_path": settings_path,
            "deleted_path": volume_path,
            "volume_unique_identifier": payload_item["VolumeUniqueIdentifier"],
            "payload": payload,
            "response": response,
            "reboot_required": self._smart_storage_reboot_required(response),
        }

    def create_gen10_logical_drive(
        self,
        settings_path: str,
        logical_drive_kind: str,
        intent: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not settings_path:
            raise ILOError("Gen10 logical drive creation requires the SmartStorageConfig settings path.")
        pending = self._smart_storage_get_pending_config(settings_path)
        logical_drives = list(pending.get("LogicalDrives") or [])
        logical_drive_payload = self._logical_drive_payload(logical_drive_kind, intent)
        logical_drives.append(logical_drive_payload)
        payload = {
            "DataGuard": "Disabled",
            "LogicalDrives": logical_drives,
        }
        response = self._put(settings_path, payload)
        return {
            "settings_path": settings_path,
            "logical_drive_kind": logical_drive_kind,
            "payload": payload,
            "response": response,
            "reboot_required": self._smart_storage_reboot_required(response),
        }

    def assign_gen10_hot_spare(
        self,
        settings_path: str,
        intent: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not settings_path:
            raise ILOError("Gen10 hot-spare assignment requires the SmartStorageConfig settings path.")
        drive = (intent.get("drive") or {})
        location = str(drive.get("smart_storage_location") or drive.get("location") or "").strip()
        if not location:
            raise ILOError(
                f"Hot-spare mapping is incomplete. Missing Smart Storage location for planned bay {intent.get('bay') or 'unknown'}."
            )
        pending = self._smart_storage_get_pending_config(settings_path)
        pending_drives = list(pending.get("LogicalDrives") or [])
        target = None
        for item in pending_drives:
            raid = str(item.get("Raid") or item.get("RAIDType") or "").lower()
            name = str(item.get("LogicalDriveName") or item.get("Name") or "").lower()
            if raid == "raid6" or ("data" in name and "raid 6" in name):
                target = item
                break
        if target is None:
            raise ILOError("Hot-spare assignment could not find the pending Data RAID 6 logical drive in SmartStorageConfig.")
        payload_item = {
            "LogicalDriveName": target.get("LogicalDriveName") or "Data RAID 6",
            "SpareDrives": [location],
            "SpareRebuildMode": "Dedicated",
        }
        volume_uid = str(target.get("VolumeUniqueIdentifier") or "").strip()
        if volume_uid:
            payload_item["VolumeUniqueIdentifier"] = volume_uid
        payload = {"LogicalDrives": [payload_item]}
        response = self._patch(settings_path, payload)
        return {
            "settings_path": settings_path,
            "assigned_bay": intent.get("bay", ""),
            "payload": payload,
            "response": response,
            "reboot_required": self._smart_storage_reboot_required(response),
        }

    def get_service_root(self) -> dict[str, Any]:
        return self._get("/redfish/v1/")

    def get_managers(self) -> list[str]:
        data = self._get("/redfish/v1/Managers")
        return [m.get("@odata.id", "") for m in data.get("Members", [])]

    def get_systems(self) -> list[str]:
        data = self._get("/redfish/v1/Systems")
        return [m.get("@odata.id", "") for m in data.get("Members", [])]

    def get_manager(self, manager_path: str | None = None) -> dict[str, Any]:
        if not manager_path:
            managers = self.get_managers()
            if not managers:
                raise ILOError("No Redfish managers found.")
            manager_path = managers[0]
        return self._get(manager_path)

    def get_system(self, system_path: str | None = None) -> dict[str, Any]:
        if not system_path:
            systems = self.get_systems()
            if not systems:
                raise ILOError("No Redfish systems found.")
            system_path = systems[0]
        return self._get(system_path)

    def get_network_protocol_path(self, manager_path: str | None = None) -> str:
        mgr = self.get_manager(manager_path)
        path = mgr.get("NetworkProtocol", {}).get("@odata.id")
        if not path:
            raise ILOError("Manager NetworkProtocol path not found.")
        return path

    def get_manager_ethernet_interface_paths(self, manager_path: str | None = None) -> list[str]:
        if not manager_path:
            managers = self.get_managers()
            if not managers:
                raise ILOError("No Redfish managers found.")
            manager_path = managers[0]

        mgr = self.get_manager(manager_path)
        collection_path = mgr.get("EthernetInterfaces", {}).get("@odata.id")
        if not collection_path:
            raise ILOError("Manager EthernetInterfaces collection not found.")

        data = self._get(collection_path)
        return [m.get("@odata.id", "") for m in data.get("Members", []) if m.get("@odata.id")]

    def get_virtual_media(self, manager_path: str | None = None) -> list[dict[str, Any]]:
        if not manager_path:
            managers = self.get_managers()
            if not managers:
                raise ILOError("No Redfish managers found.")
            manager_path = managers[0]

        vm_collection = self._get(f"{manager_path}/VirtualMedia")
        items = []
        for member in vm_collection.get("Members", []):
            path = member.get("@odata.id")
            if path:
                items.append(self._get(path))
        return items

    def get_summary(self) -> dict[str, Any]:
        service_root = self.get_service_root()
        managers = self.get_managers()
        systems = self.get_systems()

        manager = self.get_manager(managers[0] if managers else None)
        system = self.get_system(systems[0] if systems else None)
        vm = self.get_virtual_media(managers[0] if managers else None)

        return {
            "service_root_name": service_root.get("Name", ""),
            "redfish_version": service_root.get("RedfishVersion", ""),
            "manager_path": managers[0] if managers else "",
            "system_path": systems[0] if systems else "",
            "manager_firmware": manager.get("FirmwareVersion", ""),
            "manager_model": manager.get("Model", ""),
            "system_name": system.get("Name", ""),
            "system_model": system.get("Model", ""),
            "system_manufacturer": system.get("Manufacturer", ""),
            "power_state": system.get("PowerState", ""),
            "virtual_media": [
                {
                    "id": item.get("Id", ""),
                    "name": item.get("Name", ""),
                    "inserted": item.get("Inserted", False),
                    "image": item.get("Image", ""),
                    "write_protected": item.get("WriteProtected", None),
                    "media_types": item.get("MediaTypes", []),
                    "path": item.get("@odata.id", ""),
                }
                for item in vm
            ],
        }

    def set_hostname_best_effort(self, desired_hostname: str) -> dict[str, Any]:
        if not desired_hostname.strip():
            raise ILOError("Desired hostname is empty.")

        manager_path = self.get_managers()[0]
        errors: list[str] = []

        try:
            np_path = self.get_network_protocol_path(manager_path)
            before = self._get(np_path)
            before_name = before.get("HostName", "")
            self._patch(np_path, {"HostName": desired_hostname})
            after = self._get(np_path)
            after_name = after.get("HostName", "")
            return {
                "method": "NetworkProtocol.HostName",
                "path": np_path,
                "before": before_name,
                "after": after_name,
                "matched": after_name == desired_hostname,
                "changed": before_name != after_name,
            }
        except Exception as e:
            errors.append(f"NetworkProtocol.HostName failed: {e}")

        try:
            iface_paths = self.get_manager_ethernet_interface_paths(manager_path)
            if not iface_paths:
                raise ILOError("No manager EthernetInterfaces found.")
            iface_path = iface_paths[0]
            before = self._get(iface_path)
            before_name = before.get("HostName", "")
            self._patch(iface_path, {"HostName": desired_hostname})
            after = self._get(iface_path)
            after_name = after.get("HostName", "")
            return {
                "method": "EthernetInterface.HostName",
                "path": iface_path,
                "before": before_name,
                "after": after_name,
                "matched": after_name == desired_hostname,
                "changed": before_name != after_name,
            }
        except Exception as e:
            errors.append(f"EthernetInterface.HostName failed: {e}")

        raise ILOError("Hostname update failed. " + " | ".join(errors))

    def get_network_protocol(self) -> tuple[str, dict[str, Any]]:
        np_path = self.get_network_protocol_path()
        return np_path, self._get(np_path)

    def _patch_nested_if_present(
        self,
        path: str,
        current: dict[str, Any],
        parent_key: str,
        desired_changes: dict[str, Any],
    ) -> dict[str, Any]:
        block = current.get(parent_key)
        if not isinstance(block, dict):
            raise ILOError(f"{parent_key} not present as a writable object at {path}")

        payload_changes = {k: v for k, v in desired_changes.items() if k in block}
        if not payload_changes:
            raise ILOError(f"No matching writable keys found under {parent_key} at {path}")

        payload = {parent_key: payload_changes}
        self._patch(path, payload)
        return self._get(path)

    def disable_ipv6_best_effort(self) -> dict[str, Any]:
        np_path, np = self.get_network_protocol()
        attempts = []
        candidates = [
            ("IPv6", {"ProtocolEnabled": False}),
            ("DHCPv6", {"ProtocolEnabled": False}),
        ]

        for parent, desired in candidates:
            try:
                before = np.get(parent, {})
                after_doc = self._patch_nested_if_present(np_path, np, parent, desired)
                after = after_doc.get(parent, {})
                return {
                    "method": parent,
                    "path": np_path,
                    "before": before,
                    "after": after,
                }
            except Exception as e:
                attempts.append(f"{parent}: {e}")

        raise ILOError("IPv6 disable failed. " + " | ".join(attempts))

    def harden_snmp_best_effort(
        self,
        v3_username: str,
        v3_auth_protocol: str,
        v3_auth_password: str,
        v3_priv_protocol: str,
        v3_priv_password: str,
    ) -> dict[str, Any]:
        np_path, np = self.get_network_protocol()
        snmp = np.get("SNMP")
        if not isinstance(snmp, dict):
            raise ILOError("SNMP block not present under ManagerNetworkProtocol.")

        before = dict(snmp)
        patch_block: dict[str, Any] = {}

        # Always try to enable SNMP.
        if "ProtocolEnabled" in snmp:
            patch_block["ProtocolEnabled"] = True

        # Try to disable SNMPv1 where the field exists.
        for key in (
            "SNMPv1Enabled",
            "EnableSNMPv1",
            "SNMPv1RequestsEnabled",
            "SNMPv1TrapEnabled",
            "SNMPv1GetEnabled",
            "SNMPv1SetEnabled",
        ):
            if key in snmp:
                patch_block[key] = False

        # Try to disable older SNMP variants where the field exists.
        for key in (
            "SNMPv2Enabled",
            "EnableSNMPv2",
            "SNMPv2RequestsEnabled",
            "SNMPv2TrapEnabled",
            "SNMPv2cEnabled",
            "EnableSNMPv2c",
            "SNMPv2cRequestsEnabled",
            "SNMPv2cTrapEnabled",
            "CommunityAccessEnabled",
        ):
            if key in snmp:
                patch_block[key] = False

        # Try to assert SNMPv3 where fields exist.
        for key in (
            "SNMPv3RequestsEnabled",
            "SNMPv3Enabled",
            "SNMPv3TrapEnabled",
        ):
            if key in snmp:
                patch_block[key] = True

        # Best-effort user/credential keys if exposed directly in the SNMP block.
        possible_user_map = {
            "UserName": v3_username,
            "Username": v3_username,
            "SNMPv3UserName": v3_username,
            "SNMPv3Username": v3_username,
            "AuthProtocol": v3_auth_protocol,
            "SNMPv3AuthProtocol": v3_auth_protocol,
            "AuthPassword": v3_auth_password,
            "SNMPv3AuthPassword": v3_auth_password,
            "PrivacyProtocol": v3_priv_protocol,
            "SNMPv3PrivacyProtocol": v3_priv_protocol,
            "PrivacyPassword": v3_priv_password,
            "SNMPv3PrivacyPassword": v3_priv_password,
        }
        for key, value in possible_user_map.items():
            if key in snmp and value:
                patch_block[key] = value

        if not patch_block:
            raise ILOError("No supported SNMP hardening keys found on this iLO.")

        self._patch(np_path, {"SNMP": patch_block})
        after_doc = self._get(np_path)
        after = after_doc.get("SNMP", {})
        verification_checks: list[dict[str, Any]] = []

        if any(key in patch_block for key in ("UserName", "Username", "SNMPv3UserName", "SNMPv3Username")):
            after_username = self._first_present(after, ("UserName", "Username", "SNMPv3UserName", "SNMPv3Username"))
            verification_checks.append({
                "label": "username",
                "requested": v3_username,
                "actual": after_username,
                "matched": after_username == v3_username,
            })

        if any(key in patch_block for key in ("AuthProtocol", "SNMPv3AuthProtocol")):
            after_auth_protocol = self._first_present(after, ("AuthProtocol", "SNMPv3AuthProtocol"))
            verification_checks.append({
                "label": "auth_protocol",
                "requested": v3_auth_protocol,
                "actual": after_auth_protocol,
                "matched": after_auth_protocol == v3_auth_protocol,
            })

        if any(key in patch_block for key in ("PrivacyProtocol", "SNMPv3PrivacyProtocol")):
            after_priv_protocol = self._first_present(after, ("PrivacyProtocol", "SNMPv3PrivacyProtocol"))
            verification_checks.append({
                "label": "privacy_protocol",
                "requested": v3_priv_protocol,
                "actual": after_priv_protocol,
                "matched": after_priv_protocol == v3_priv_protocol,
            })

        if "ProtocolEnabled" in patch_block:
            verification_checks.append({
                "label": "protocol_enabled",
                "requested": True,
                "actual": after.get("ProtocolEnabled"),
                "matched": after.get("ProtocolEnabled") is True,
            })

        for key in (
            "SNMPv1Enabled",
            "EnableSNMPv1",
            "SNMPv1RequestsEnabled",
            "SNMPv1TrapEnabled",
            "SNMPv1GetEnabled",
            "SNMPv1SetEnabled",
            "SNMPv2Enabled",
            "EnableSNMPv2",
            "SNMPv2RequestsEnabled",
            "SNMPv2TrapEnabled",
            "SNMPv2cEnabled",
            "EnableSNMPv2c",
            "SNMPv2cRequestsEnabled",
            "SNMPv2cTrapEnabled",
            "CommunityAccessEnabled",
        ):
            if key in patch_block:
                verification_checks.append({
                    "label": key,
                    "requested": False,
                    "actual": after.get(key),
                    "matched": after.get(key) is False,
                })

        for key in (
            "SNMPv3RequestsEnabled",
            "SNMPv3Enabled",
            "SNMPv3TrapEnabled",
        ):
            if key in patch_block:
                verification_checks.append({
                    "label": key,
                    "requested": True,
                    "actual": after.get(key),
                    "matched": after.get(key) is True,
                })

        matched = bool(verification_checks) and all(item["matched"] for item in verification_checks)
        mismatches = [item for item in verification_checks if not item["matched"]]
        before_changed_checks: list[dict[str, Any]] = []
        if any(key in patch_block for key in ("UserName", "Username", "SNMPv3UserName", "SNMPv3Username")):
            before_changed_checks.append({
                "requested": v3_username,
                "actual": self._first_present(before, ("UserName", "Username", "SNMPv3UserName", "SNMPv3Username")),
            })
        if any(key in patch_block for key in ("AuthProtocol", "SNMPv3AuthProtocol")):
            before_changed_checks.append({
                "requested": v3_auth_protocol,
                "actual": self._first_present(before, ("AuthProtocol", "SNMPv3AuthProtocol")),
            })
        if any(key in patch_block for key in ("PrivacyProtocol", "SNMPv3PrivacyProtocol")):
            before_changed_checks.append({
                "requested": v3_priv_protocol,
                "actual": self._first_present(before, ("PrivacyProtocol", "SNMPv3PrivacyProtocol")),
            })
        if "ProtocolEnabled" in patch_block:
            before_changed_checks.append({"requested": True, "actual": before.get("ProtocolEnabled")})
        for key in (
            "SNMPv1Enabled",
            "EnableSNMPv1",
            "SNMPv1RequestsEnabled",
            "SNMPv1TrapEnabled",
            "SNMPv1GetEnabled",
            "SNMPv1SetEnabled",
            "SNMPv2Enabled",
            "EnableSNMPv2",
            "SNMPv2RequestsEnabled",
            "SNMPv2TrapEnabled",
            "SNMPv2cEnabled",
            "EnableSNMPv2c",
            "SNMPv2cRequestsEnabled",
            "SNMPv2cTrapEnabled",
            "CommunityAccessEnabled",
        ):
            if key in patch_block:
                before_changed_checks.append({"requested": False, "actual": before.get(key)})
        for key in ("SNMPv3RequestsEnabled", "SNMPv3Enabled", "SNMPv3TrapEnabled"):
            if key in patch_block:
                before_changed_checks.append({"requested": True, "actual": before.get(key)})
        changed = any(item.get("actual") != item.get("requested") for item in before_changed_checks)
        notes = [
            f"Wrote SNMP values using {', '.join(sorted(list(patch_block.keys())))}."
        ]
        if matched:
            notes.append("Requested SNMP values were verified after the write.")
        else:
            notes.append("PATCH succeeded but one or more SNMP values did not read back as requested.")
        return {
            "action": "apply_snmp",
            "path": np_path,
            "before": before,
            "after": after,
            "applied_keys": sorted(list(patch_block.keys())),
            "requested": {
                "username": v3_username,
                "auth_protocol": v3_auth_protocol,
                "privacy_protocol": v3_priv_protocol,
                "auth_secret_present": bool(v3_auth_password),
                "privacy_secret_present": bool(v3_priv_password),
            },
            "verification": {
                "checks": verification_checks,
                "mismatches": mismatches,
            },
            "matched": matched,
            "mismatches": mismatches,
            "changed": changed,
            "reset_recommended": False,
            "notes": notes,
            "verified": matched,
            "status": "Verified" if matched else "Mismatch",
            "details": notes[-1],
        }

    def ensure_local_accounts_best_effort(self, desired_accounts: list[dict[str, Any]]) -> dict[str, Any]:
        sanitized_accounts = []
        for item in desired_accounts or []:
            username = str(item.get("username") or "").strip()
            password = str(item.get("password") or "")
            role = str(item.get("role") or "Administrator").strip() or "Administrator"
            if not username or not password:
                continue
            sanitized_accounts.append({
                "username": username,
                "password": password,
                "role": role,
            })

        if not sanitized_accounts:
            return {
                "action": "ensure_local_accounts",
                "path": "",
                "before": [],
                "after": [],
                "requested": [],
                "results": [],
                "matched": True,
                "mismatches": [],
                "status": "Skipped",
                "notes": ["No additional iLO users were requested."],
            }

        service_root = self.get_service_root()
        account_service_path = service_root.get("AccountService", {}).get("@odata.id")
        if not account_service_path:
            raise ILOError("AccountService path not found.")

        account_service = self._get(account_service_path)
        accounts_path = account_service.get("Accounts", {}).get("@odata.id")
        if not accounts_path:
            raise ILOError("AccountService Accounts collection not found.")

        before_accounts = self._expand_collection(accounts_path)
        before_summary = self._build_account_summary(before_accounts)
        existing_by_username = {
            str(item.get("UserName") or "").strip().lower(): item
            for item in before_accounts
            if str(item.get("UserName") or "").strip()
        }

        results: list[dict[str, Any]] = []
        notes: list[str] = []

        for desired in sanitized_accounts:
            username = desired["username"]
            password = desired["password"]
            role = desired["role"]
            existing = existing_by_username.get(username.lower())
            try:
                if existing and existing.get("@odata.id"):
                    account_path = str(existing.get("@odata.id") or "")
                    payload: dict[str, Any] = {"Password": password, "RoleId": role}
                    if "Enabled" in existing:
                        payload["Enabled"] = True
                    self._patch(account_path, payload)
                    after_account = self._get(account_path)
                    result_status = "Updated"
                else:
                    payload = {
                        "UserName": username,
                        "Password": password,
                        "RoleId": role,
                    }
                    try:
                        self._post(accounts_path, {**payload, "Enabled": True})
                    except Exception:
                        self._post(accounts_path, payload)

                    refreshed_accounts = self._expand_collection(accounts_path)
                    after_account = next(
                        (
                            item
                            for item in refreshed_accounts
                            if str(item.get("UserName") or "").strip().lower() == username.lower()
                        ),
                        {},
                    )
                    if not after_account:
                        raise ILOError(f"Account {username} did not appear in AccountService after create.")
                    result_status = "Created"

                after_role = str(after_account.get("RoleId") or "")
                after_enabled = after_account.get("Enabled")
                matched = bool(after_account) and (after_role == role if role else True) and (after_enabled is not False)
                result = {
                    "username": username,
                    "requested_role": role,
                    "actual_role": after_role,
                    "actual_enabled": after_enabled,
                    "status": result_status,
                    "matched": matched,
                    "password_requested": True,
                }
                results.append(result)
                if matched:
                    notes.append(f"{result_status} local iLO user {username} with role {role}.")
                else:
                    notes.append(
                        f"{result_status} local iLO user {username}, but readback did not match the requested role/enabled state."
                    )
            except Exception as e:
                results.append({
                    "username": username,
                    "requested_role": role,
                    "actual_role": "",
                    "actual_enabled": None,
                    "status": "Failed",
                    "matched": False,
                    "password_requested": True,
                    "error": str(e).splitlines()[0],
                })
                notes.append(f"Failed to ensure local iLO user {username}: {str(e).splitlines()[0]}")

        after_summary = self._build_account_summary(self._expand_collection(accounts_path))
        mismatches = [item for item in results if not item.get("matched")]
        matched = bool(results) and not mismatches
        return {
            "action": "ensure_local_accounts",
            "path": accounts_path,
            "before": before_summary,
            "after": after_summary,
            "requested": [
                {
                    "username": item["username"],
                    "role": item["role"],
                    "password_requested": True,
                }
                for item in sanitized_accounts
            ],
            "results": results,
            "matched": matched,
            "mismatches": mismatches,
            "status": "Verified" if matched else "Mismatch",
            "notes": notes,
        }

    def manager_reset_best_effort(self, reset_type: str = "GracefulRestart") -> dict[str, Any]:
        manager_path = self.get_managers()[0]
        manager = self.get_manager(manager_path)
        target = manager.get("Actions", {}).get("#Manager.Reset", {}).get("target")
        if not target:
            raise ILOError("Manager reset action not available on this iLO.")
        self._post(target, {"ResetType": reset_type})
        return {
            "action": "reset_ilo",
            "path": target,
            "reset_type": reset_type,
        }

    def reset_ilo(self, reset_type: str = "GracefulRestart") -> dict[str, Any]:
        return self.manager_reset_best_effort(reset_type=reset_type)

    def eject_virtual_media(self, vm_path: str) -> None:
        vm = self._get(vm_path)
        actions = vm.get("Actions", {})
        target = actions.get("#VirtualMedia.EjectMedia", {}).get("target")
        if not target:
            raise ILOError(f"No eject action found for virtual media {vm_path}")
        self._post(target, {})

    @staticmethod
    def _boot_override_snapshot(system: dict[str, Any]) -> dict[str, str]:
        boot = system.get("Boot", {}) if isinstance(system, dict) else {}
        if not isinstance(boot, dict):
            boot = {}
        return {
            "enabled": str(boot.get("BootSourceOverrideEnabled") or ""),
            "target": str(boot.get("BootSourceOverrideTarget") or ""),
            "uefi_target": str(boot.get("UefiTargetBootSourceOverride") or ""),
        }

    @staticmethod
    def _boot_target_matches_cd(value: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
        if not normalized:
            return False
        return normalized in {
            "cd",
            "cdrom",
            "dvd",
            "cddvd",
            "ueficd",
            "ueficddvd",
            "ueficdrom",
        } or ("cd" in normalized and "uefi" in normalized)

    @staticmethod
    def _boot_option_text(option: dict[str, Any]) -> str:
        fields = [
            option.get("BootOptionReference") or option.get("boot_option_reference"),
            option.get("DisplayName") or option.get("display_name"),
            option.get("Alias") or option.get("alias"),
            option.get("Name") or option.get("name"),
            option.get("Description") or option.get("description"),
            option.get("UefiDevicePath") or option.get("uefi_device_path"),
        ]
        return " ".join(str(x or "") for x in fields)

    @staticmethod
    def _boot_option_label_text(option: dict[str, Any]) -> str:
        fields = [
            option.get("BootOptionReference") or option.get("boot_option_reference"),
            option.get("DisplayName") or option.get("display_name"),
            option.get("Alias") or option.get("alias"),
            option.get("Name") or option.get("name"),
            option.get("Description") or option.get("description"),
        ]
        return " ".join(str(x or "") for x in fields)

    @staticmethod
    def _summarize_boot_option(option: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": str(option.get("@odata.id") or option.get("path") or ""),
            "boot_option_reference": str(option.get("BootOptionReference") or option.get("boot_option_reference") or ""),
            "display_name": str(option.get("DisplayName") or option.get("display_name") or ""),
            "alias": str(option.get("Alias") or option.get("alias") or ""),
            "name": str(option.get("Name") or option.get("name") or ""),
            "description": str(option.get("Description") or option.get("description") or ""),
            "uefi_device_path": str(option.get("UefiDevicePath") or option.get("uefi_device_path") or ""),
            "raw_error": str(option.get("@error") or option.get("raw_error") or ""),
        }

    @staticmethod
    def _summarize_oem_scalars(block: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key, value in (block or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                summary[key] = value
            elif isinstance(value, list) and all(isinstance(item, (str, int, float, bool)) or item is None for item in value):
                summary[key] = list(value)
        return summary

    def _find_virtual_cd_boot_option_in_options(self, options: list[dict[str, Any]]) -> dict[str, Any] | None:
        preferred_terms = (
            "virtual cd",
            "virtual dvd",
            "cd/dvd",
            "dvd/cd",
            "virtual media",
            "ilo virtual",
            "embedded virtual media",
        )
        for option in options:
            text = self._boot_option_label_text(option).lower()
            if any(term in text for term in preferred_terms):
                return option
        for option in options:
            text = self._boot_option_label_text(option).lower()
            if re.search(r"\b(cd|cd-rom|cdrom|dvd)\b", text):
                return option
        return None

    def _find_virtual_cd_boot_option(self, system: dict[str, Any]) -> dict[str, Any] | None:
        boot = system.get("Boot", {}) if isinstance(system, dict) else {}
        if not isinstance(boot, dict):
            boot = {}
        boot_options_path = (
            ((boot.get("BootOptions") or {}).get("@odata.id") or "")
            or ((system.get("BootOptions") or {}).get("@odata.id") or "")
        ).strip()
        if not boot_options_path:
            return None
        collection = self._safe_get(boot_options_path)
        members = collection.get("Members", []) if isinstance(collection, dict) else []
        options: list[dict[str, Any]] = []
        for member in members:
            path = (member.get("@odata.id") or "").strip() if isinstance(member, dict) else ""
            if not path:
                continue
            option = self._safe_get(path)
            if isinstance(option, dict) and not option.get("@error"):
                options.append(option)
        return self._find_virtual_cd_boot_option_in_options(options)

    def collect_boot_option_inventory(self, system_path: str | None = None) -> dict[str, Any]:
        if not system_path:
            systems = self.get_systems()
            if not systems:
                raise ILOError("No Redfish systems found.")
            system_path = systems[0]

        system = self.get_system(system_path)
        boot = dict(system.get("Boot") or {}) if isinstance(system, dict) else {}
        boot_options_path = (
            ((boot.get("BootOptions") or {}).get("@odata.id") or "")
            or ((system.get("BootOptions") or {}).get("@odata.id") or "")
        ).strip()
        collection = self._safe_get(boot_options_path) if boot_options_path else {}
        members = collection.get("Members", []) if isinstance(collection, dict) else []

        options: list[dict[str, Any]] = []
        for member in members:
            path = (member.get("@odata.id") or "").strip() if isinstance(member, dict) else ""
            if not path:
                continue
            option = self._safe_get(path)
            if not isinstance(option, dict):
                continue
            options.append(self._summarize_boot_option({"@odata.id": path, **option}))

        oem_hpe = dict(((system.get("Oem") or {}).get("Hpe") or {})) if isinstance(system, dict) else {}
        return {
            "system_path": str(system_path),
            "boot": {
                "enabled": str(boot.get("BootSourceOverrideEnabled") or ""),
                "target": str(boot.get("BootSourceOverrideTarget") or ""),
                "uefi_target": str(boot.get("UefiTargetBootSourceOverride") or ""),
                "boot_order": list(boot.get("BootOrder") or []),
                "boot_order_property_selection": str(boot.get("BootOrderPropertySelection") or ""),
            },
            "boot_options_path": boot_options_path,
            "boot_options_count": len(options),
            "boot_options": options,
            "oem_hpe_keys": sorted(oem_hpe.keys()),
            "oem_hpe_values": self._summarize_oem_scalars(oem_hpe),
        }

    def set_one_time_boot_cd(self, system_path: str | None = None) -> dict[str, Any]:
        if not system_path:
            systems = self.get_systems()
            if not systems:
                raise ILOError("No Redfish systems found.")
            system_path = systems[0]

        before = self.get_system(system_path)
        before_boot = self._boot_override_snapshot(before)
        boot_inventory = self.collect_boot_option_inventory(system_path)
        payload_boot: dict[str, Any] = {
            "BootSourceOverrideEnabled": "Once",
            "BootSourceOverrideTarget": "Cd",
        }
        selected_boot_option = self._find_virtual_cd_boot_option_in_options(
            list(boot_inventory.get("boot_options") or [])
        )
        selected_boot_option_ref = str(
            (selected_boot_option or {}).get("BootOptionReference")
            or (selected_boot_option or {}).get("boot_option_reference")
            or ""
        ).strip()
        selected_boot_option_target = str(
            (selected_boot_option or {}).get("UefiDevicePath")
            or (selected_boot_option or {}).get("uefi_device_path")
            or selected_boot_option_ref
        ).strip()
        selection_reason = ""
        if selected_boot_option_ref:
            payload_boot = {
                "BootSourceOverrideEnabled": "Once",
                "BootSourceOverrideTarget": "UefiTarget",
                "UefiTargetBootSourceOverride": selected_boot_option_target,
            }
            selection_reason = f"Matched virtual-media UEFI boot option {selected_boot_option_ref}."
        elif not str(boot_inventory.get("boot_options_path") or "").strip():
            selection_reason = "System did not expose a Redfish BootOptions collection."
        elif not int(boot_inventory.get("boot_options_count") or 0):
            selection_reason = "Redfish BootOptions collection was present but empty."
        else:
            selection_reason = "BootOptions were exposed, but none looked like a virtual CD/DVD boot option."
        self._patch(system_path, {"Boot": payload_boot})
        after = self.get_system(system_path)
        after_boot = self._boot_override_snapshot(after)
        matched = after_boot.get("enabled", "").lower() == "once" and (
            (
                after_boot.get("target", "").strip().lower() == "uefitarget"
                and selected_boot_option_ref
                and after_boot.get("uefi_target", "").strip() in {selected_boot_option_target, selected_boot_option_ref}
            )
            or self._boot_target_matches_cd(after_boot.get("target", ""))
        )
        notes = [
            f"Requested one-time boot override on {system_path} to CD/DVD.",
            f"Before: enabled={before_boot.get('enabled') or '(empty)'} target={before_boot.get('target') or '(empty)'}.",
            f"After: enabled={after_boot.get('enabled') or '(empty)'} target={after_boot.get('target') or '(empty)'}"
            + (
                f" uefi_target={after_boot.get('uefi_target') or '(empty)'}."
                if after_boot.get("uefi_target") or selected_boot_option_ref
                else "."
            ),
        ]
        if selected_boot_option_ref:
            notes.append(
                "Selected UEFI boot option "
                f"{selected_boot_option_ref} for virtual CD/DVD: "
                f"{self._boot_option_text(selected_boot_option or {}) or '(unnamed option)'}."
            )
            if selected_boot_option_target and selected_boot_option_target != selected_boot_option_ref:
                notes.append(f"Using UEFI target override value: {selected_boot_option_target}.")
        else:
            notes.append(selection_reason)
        if matched:
            if after_boot.get("target", "").strip().lower() == "uefitarget" and selected_boot_option_ref:
                notes.append("One-time boot override read back on the selected UEFI boot option.")
            elif after_boot.get("target", "").strip().lower() != "cd":
                notes.append(f"iLO returned equivalent boot target {after_boot.get('target')} and it was accepted as CD/DVD.")
            else:
                notes.append("One-time boot override read back exactly as requested.")
        else:
            notes.append("One-time boot override did not read back as the requested CD/DVD setting.")
        return {
            "system_path": system_path,
            "before_enabled": before_boot.get("enabled", ""),
            "before_target": before_boot.get("target", ""),
            "before_uefi_target": before_boot.get("uefi_target", ""),
            "after_enabled": after_boot.get("enabled", ""),
            "after_target": after_boot.get("target", ""),
            "after_uefi_target": after_boot.get("uefi_target", ""),
            "selected_boot_option_reference": selected_boot_option_ref,
            "selected_uefi_target": selected_boot_option_target,
            "boot_option_selection_reason": selection_reason,
            "boot_option_inventory": boot_inventory,
            "matched": matched,
            "notes": notes,
            "action": "set_one_time_boot_cd",
        }

    def power_reset(self, reset_type: str = "ForceRestart", system_path: str | None = None) -> dict[str, Any]:
        if not system_path:
            systems = self.get_systems()
            if not systems:
                raise ILOError("No Redfish systems found.")
            system_path = systems[0]

        system = self._get(system_path)
        target = system.get("Actions", {}).get("#ComputerSystem.Reset", {}).get("target")
        if not target:
            raise ILOError("Reset action not available on this system.")
        self._post(target, {"ResetType": reset_type})
        return {
            "system_path": system_path,
            "path": target,
            "reset_type": reset_type,
        }

    def reboot_server_and_wait(
        self,
        reset_type: str = "GracefulRestart",
        reboot_start_timeout: int = 120,
        return_timeout: int = 600,
        poll_interval: int = 10,
    ) -> dict[str, Any]:
        system_path = self.get_systems()[0]
        baseline = self.get_system(system_path)
        baseline_power = str(baseline.get("PowerState") or "")
        baseline_post = str(((baseline.get("Oem") or {}).get("Hpe") or {}).get("PostState") or "")
        reset_result = self.power_reset(reset_type=reset_type, system_path=system_path)

        reboot_started = False
        reboot_start_detail = "No observable reboot-start state change was detected before timeout."
        reboot_start_deadline = time.time() + max(reboot_start_timeout, 1)
        while time.time() < reboot_start_deadline:
            time.sleep(max(poll_interval, 1))
            try:
                current = self.get_system(system_path)
            except Exception as e:
                reboot_started = True
                reboot_start_detail = f"System read temporarily failed after reset request: {str(e).splitlines()[0]}"
                break

            current_power = str(current.get("PowerState") or "")
            current_post = str(((current.get("Oem") or {}).get("Hpe") or {}).get("PostState") or "")
            current_boot = str(((current.get("BootProgress") or {}).get("LastState") or ""))
            if current_power and baseline_power and current_power != baseline_power:
                reboot_started = True
                reboot_start_detail = f"Observed PowerState change from {baseline_power} to {current_power}."
                break
            if current_post and baseline_post and current_post != baseline_post:
                reboot_started = True
                reboot_start_detail = f"Observed HPE PostState change from {baseline_post} to {current_post}."
                break
            if current_boot:
                reboot_started = True
                reboot_start_detail = f"Observed BootProgress state after reset request: {current_boot}."
                break

        return_deadline = time.time() + max(return_timeout, 1)
        system_returned = False
        return_detail = "System did not return before timeout."
        service_root = None
        final_system = None
        while time.time() < return_deadline:
            time.sleep(max(poll_interval, 1))
            try:
                service_root = self.get_service_root()
                final_system = self.get_system(system_path)
                system_returned = True
                return_detail = f"System returned with PowerState={final_system.get('PowerState') or 'unknown'}."
                break
            except Exception as e:
                return_detail = f"Waiting for Redfish/system readiness: {str(e).splitlines()[0]}"

        if not system_returned:
            raise ILOError(return_detail)

        return {
            **reset_result,
            "action": "reboot_server",
            "reboot_start_observed": reboot_started,
            "reboot_start_detail": reboot_start_detail,
            "system_returned": system_returned,
            "return_detail": return_detail,
            "service_root": service_root or {},
            "final_system": final_system or {},
        }

    def reboot_server(
        self,
        reset_type: str = "GracefulRestart",
        reboot_start_timeout: int = 120,
        return_timeout: int = 600,
        poll_interval: int = 10,
    ) -> dict[str, Any]:
        return self.reboot_server_and_wait(
            reset_type=reset_type,
            reboot_start_timeout=reboot_start_timeout,
            return_timeout=return_timeout,
            poll_interval=poll_interval,
        )
