from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote
import xml.etree.ElementTree as ET


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1] if "}" in value else value


def _attr_by_local_name(element: ET.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return str(value)
    return ""


def _child_text_by_local_name(element: ET.Element, name: str) -> str:
    for child in list(element):
        if _local_name(child.tag) == name:
            return str(child.text or "").strip()
    return ""


def display_size(size_bytes: int) -> str:
    if size_bytes >= 1024**3:
        return f"{size_bytes / 1024**3:.1f} GB"
    if size_bytes >= 1024**2:
        return f"{size_bytes / 1024**2:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def inspect_ovf_source(image_path: str | Path) -> dict[str, Any]:
    path = Path(str(image_path)).expanduser()
    suffix = path.suffix.lower().lstrip(".")
    summary: dict[str, Any] = {
        "ok": False,
        "kind": suffix,
        "path": str(path),
        "name": path.name,
        "directory": str(path.parent) if path.name else "",
        "files": [],
        "total_size_bytes": 0,
        "total_size_display": "0 B",
        "warnings": [],
        "vm_name": "",
        "network_names": [],
        "os_description": "",
        "hardware_version": "",
        "cpu_count": "",
        "memory_mb": "",
        "disk_capacity": "",
    }
    if suffix not in {"ova", "ovf"}:
        summary["warnings"].append("OVF source must be OVA or OVF.")
        return summary
    if not path.exists() or not path.is_file():
        summary["warnings"].append("OVF source file is missing.")
        return summary

    def add_file(file_path: Path, role: str, expected_size: str = "") -> None:
        exists = file_path.exists() and file_path.is_file()
        actual_size = file_path.stat().st_size if exists else 0
        summary["files"].append(
            {
                "name": file_path.name,
                "path": str(file_path),
                "role": role,
                "exists": exists,
                "size_bytes": actual_size,
                "size_display": display_size(actual_size),
                "expected_size": expected_size,
            }
        )
        summary["total_size_bytes"] += actual_size

    add_file(path, "source")
    if suffix == "ova":
        summary["ok"] = True
        summary["total_size_display"] = display_size(int(summary["total_size_bytes"]))
        return summary

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        summary["warnings"].append(f"OVF descriptor could not be parsed: {str(exc).splitlines()[0]}")
        summary["total_size_display"] = display_size(int(summary["total_size_bytes"]))
        return summary

    base_dir = path.parent.resolve()
    for element in root.iter():
        name = _local_name(element.tag)
        if name == "File":
            href = _attr_by_local_name(element, "href").strip()
            if not href:
                continue
            candidate = (path.parent / unquote(href)).resolve()
            try:
                candidate.relative_to(base_dir)
            except ValueError:
                summary["warnings"].append(f"OVF referenced file is outside the template folder: {href}")
                continue
            add_file(candidate, "referenced", _attr_by_local_name(element, "size").strip())
            if not candidate.exists():
                summary["warnings"].append(f"OVF referenced file is missing: {href}")
        elif name == "VirtualSystem" and not summary["vm_name"]:
            summary["vm_name"] = _attr_by_local_name(element, "id").strip()
        elif name == "Network":
            network_name = _attr_by_local_name(element, "name").strip()
            if network_name and network_name not in summary["network_names"]:
                summary["network_names"].append(network_name)
        elif name == "Description" and not summary["os_description"]:
            text = str(element.text or "").strip()
            if text:
                summary["os_description"] = text
        elif name == "VirtualSystemType" and not summary["hardware_version"]:
            summary["hardware_version"] = str(element.text or "").strip()
        elif name == "Disk" and not summary["disk_capacity"]:
            capacity = _attr_by_local_name(element, "capacity").strip()
            allocation_units = _attr_by_local_name(element, "capacityAllocationUnits").strip()
            if capacity:
                summary["disk_capacity"] = f"{capacity} {allocation_units}".strip()
        elif name == "Item":
            resource_type = _child_text_by_local_name(element, "ResourceType")
            quantity = _child_text_by_local_name(element, "VirtualQuantity")
            allocation_units = _child_text_by_local_name(element, "AllocationUnits")
            if resource_type == "3" and quantity and not summary["cpu_count"]:
                summary["cpu_count"] = quantity
            elif resource_type == "4" and quantity and not summary["memory_mb"]:
                summary["memory_mb"] = f"{quantity} {allocation_units}".strip()

    summary["ok"] = not summary["warnings"]
    summary["total_size_display"] = display_size(int(summary["total_size_bytes"]))
    return summary


def inspect_ovf_directory(directory_path: str | Path, ovf_name: str = "") -> dict[str, Any]:
    directory = Path(str(directory_path)).expanduser()
    if not directory.exists() or not directory.is_dir():
        return {
            "ok": False,
            "directory": str(directory),
            "warnings": ["OVF template directory is missing."],
            "candidates": [],
        }
    ovf_candidates = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".ovf")
    ova_candidates = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".ova")
    candidates = ovf_candidates + ova_candidates
    if ovf_name:
        selected = directory / Path(ovf_name).name
        if selected not in candidates:
            return {
                "ok": False,
                "directory": str(directory),
                "warnings": [f"Requested OVF/OVA file was not found in the template directory: {Path(ovf_name).name}"],
                "candidates": [path.name for path in candidates],
            }
        return inspect_ovf_source(selected)
    if ovf_candidates:
        if len(ovf_candidates) > 1:
            return {
                "ok": False,
                "directory": str(directory),
                "warnings": ["Multiple .ovf descriptors were found. Enter the descriptor file name to use."],
                "candidates": [path.name for path in candidates],
            }
        return inspect_ovf_source(ovf_candidates[0])
    if ova_candidates:
        if len(ova_candidates) > 1:
            return {
                "ok": False,
                "directory": str(directory),
                "warnings": ["Multiple .ova files were found. Enter the OVA file name to use."],
                "candidates": [path.name for path in candidates],
            }
        return inspect_ovf_source(ova_candidates[0])
    if not candidates:
        return {
            "ok": False,
            "directory": str(directory),
            "warnings": ["No .ovf descriptor or .ova source was found in the template directory."],
            "candidates": [],
        }
