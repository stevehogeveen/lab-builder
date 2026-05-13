from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote
import xml.etree.ElementTree as ET


class WindowsInterfaceError(Exception):
    pass


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1] if "}" in value else value


def _attr_by_local_name(element: ET.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return str(value)
    return ""


def _display_size(size_bytes: int) -> str:
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
        "files": [],
        "total_size_bytes": 0,
        "total_size_display": "0 B",
        "warnings": [],
        "vm_name": "",
        "network_names": [],
        "os_description": "",
        "hardware_version": "",
    }
    if suffix not in {"ova", "ovf"}:
        summary["warnings"].append("Windows source image must be OVA or OVF.")
        return summary
    if not path.exists() or not path.is_file():
        summary["warnings"].append("Windows source image file is missing.")
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
                "size_display": _display_size(actual_size),
                "expected_size": expected_size,
            }
        )
        summary["total_size_bytes"] += actual_size

    add_file(path, "source")
    if suffix == "ova":
        summary["ok"] = True
        summary["total_size_display"] = _display_size(int(summary["total_size_bytes"]))
        return summary

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        summary["warnings"].append(f"OVF descriptor could not be parsed: {str(exc).splitlines()[0]}")
        summary["total_size_display"] = _display_size(int(summary["total_size_bytes"]))
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

    summary["ok"] = not summary["warnings"]
    summary["total_size_display"] = _display_size(int(summary["total_size_bytes"]))
    return summary


@dataclass
class VsphereConfig:
    host: str
    username: str
    password: str
    port: int = 443
    verify_tls: bool = False


@dataclass
class WinRMConfig:
    host: str
    username: str
    password: str
    port: int = 5986
    use_https: bool = True
    transport: str = "ntlm"
    server_cert_validation: str = "ignore"


class VsphereClient:
    def __init__(self, config: VsphereConfig) -> None:
        self.config = config
        self._service_instance: Any = None

    def connect(self) -> Any:
        try:
            from pyVim.connect import SmartConnect, SmartConnectNoSSL
        except Exception as exc:
            raise WindowsInterfaceError("pyvmomi is not installed in this environment.") from exc
        connector = SmartConnect if self.config.verify_tls else SmartConnectNoSSL
        try:
            self._service_instance = connector(
                host=self.config.host,
                user=self.config.username,
                pwd=self.config.password,
                port=self.config.port,
            )
            return self._service_instance
        except Exception as exc:
            raise WindowsInterfaceError(f"vSphere connection failed: {str(exc).splitlines()[0]}") from exc

    def disconnect(self) -> None:
        if not self._service_instance:
            return
        try:
            from pyVim.connect import Disconnect

            Disconnect(self._service_instance)
        except Exception:
            pass
        finally:
            self._service_instance = None

    def inventory_summary(self) -> dict[str, Any]:
        si = self.connect()
        try:
            content = si.RetrieveContent()
            about = getattr(content, "about", None)
            root = getattr(content, "rootFolder", None)
            datacenters = [child for child in getattr(root, "childEntity", []) or [] if getattr(child, "name", "")]
            return {
                "connected": True,
                "product": getattr(about, "fullName", "") if about else "",
                "api_version": getattr(about, "apiVersion", "") if about else "",
                "datacenters": [getattr(item, "name", "") for item in datacenters],
            }
        finally:
            self.disconnect()

    @staticmethod
    def validate_ovf_inputs(plan: dict[str, Any]) -> dict[str, Any]:
        warnings: list[str] = []
        image_path = str(plan.get("image_path") or "").strip()
        image_kind = str(plan.get("image_kind") or "").strip().lower()
        source_summary: dict[str, Any] = {}
        if image_kind not in {"ova", "ovf"}:
            warnings.append("Windows source image must be OVA or OVF.")
        if not image_path or not Path(image_path).exists():
            warnings.append("Windows source image file is missing.")
        else:
            source_summary = inspect_ovf_source(image_path)
            warnings.extend([item for item in source_summary.get("warnings", []) if item not in warnings])
        labels = {
            "vsphere_host": "vSphere host",
            "vsphere_username": "vSphere username",
            "datastore": "Datastore",
            "network": "VM network",
        }
        for field, label in labels.items():
            if not str(plan.get(field) or "").strip():
                warnings.append(f"{label} is missing.")
        return {"ready": not warnings, "warnings": warnings, "source_summary": source_summary}


class WinRMClient:
    def __init__(self, config: WinRMConfig) -> None:
        self.config = config

    def _endpoint(self) -> str:
        scheme = "https" if self.config.use_https else "http"
        return f"{scheme}://{self.config.host}:{self.config.port}/wsman"

    def probe(self) -> dict[str, Any]:
        try:
            import winrm
        except Exception as exc:
            raise WindowsInterfaceError("pywinrm is not installed in this environment.") from exc
        try:
            session = winrm.Session(
                self._endpoint(),
                auth=(self.config.username, self.config.password),
                transport=self.config.transport,
                server_cert_validation=self.config.server_cert_validation,
            )
            result = session.run_cmd("hostname")
        except Exception as exc:
            raise WindowsInterfaceError(f"WinRM probe failed: {str(exc).splitlines()[0]}") from exc
        stdout = bytes(result.std_out or b"").decode("utf-8", errors="replace").strip()
        stderr = bytes(result.std_err or b"").decode("utf-8", errors="replace").strip()
        return {
            "connected": int(result.status_code) == 0,
            "status_code": int(result.status_code),
            "endpoint": self._endpoint(),
            "stdout": stdout,
            "stderr": stderr,
        }
