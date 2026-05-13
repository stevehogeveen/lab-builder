from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ovf import inspect_ovf_source


class WindowsInterfaceError(Exception):
    pass


def build_deployment_preview(plan: dict[str, Any], source_summary: dict[str, Any]) -> dict[str, Any]:
    source_files = list(source_summary.get("files") or [])
    sidecar_files = [item for item in source_files if item.get("role") == "referenced"]
    vm_name = str(plan.get("vm_name") or source_summary.get("vm_name") or "").strip()
    target_network = str(plan.get("network") or "").strip()
    ovf_networks = [str(item) for item in source_summary.get("network_names") or [] if str(item).strip()]
    warnings: list[str] = []
    if target_network and ovf_networks and target_network not in ovf_networks:
        warnings.append(f"Saved VM network '{target_network}' does not match OVF network(s): {', '.join(ovf_networks)}.")
    preview = {
        "action": "Deploy OVF/OVA template",
        "mode": "dry_run",
        "creates_vm": False,
        "source": {
            "name": source_summary.get("name") or Path(str(plan.get("image_path") or "")).name,
            "path": source_summary.get("path") or plan.get("image_path") or "",
            "kind": source_summary.get("kind") or plan.get("image_kind") or "",
            "total_size_display": source_summary.get("total_size_display") or "",
            "file_count": len(source_files),
            "sidecar_count": len(sidecar_files),
        },
        "template": {
            "vm_name": source_summary.get("vm_name") or "",
            "os_description": source_summary.get("os_description") or "",
            "hardware_version": source_summary.get("hardware_version") or "",
            "cpu_count": source_summary.get("cpu_count") or "",
            "memory_mb": source_summary.get("memory_mb") or "",
            "disk_capacity": source_summary.get("disk_capacity") or "",
            "network_names": ovf_networks,
        },
        "target": {
            "vm_name": vm_name,
            "vsphere_host": str(plan.get("vsphere_host") or ""),
            "datacenter": str(plan.get("datacenter") or ""),
            "datastore": str(plan.get("datastore") or ""),
            "network": target_network,
            "folder": str(plan.get("folder") or ""),
            "resource_pool": str(plan.get("resource_pool") or ""),
            "guest_ip": str(plan.get("target_ip") or ""),
            "gateway": str(plan.get("gateway") or ""),
            "dns_servers": list(plan.get("dns_servers") or []),
        },
        "steps": [
            "Validate OVA/OVF source and sidecar files.",
            "Connect to the saved vSphere or standalone ESXi endpoint.",
            "Map the OVF network to the saved VM network.",
            "Import the VM to the selected datastore and inventory location.",
            "Leave the VM powered off until an explicit apply flow is added.",
        ],
        "warnings": warnings,
    }
    return preview


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
        deployment_preview = build_deployment_preview(plan, source_summary) if source_summary else {}
        warnings.extend([item for item in deployment_preview.get("warnings", []) if item not in warnings])
        return {
            "ready": not warnings,
            "warnings": warnings,
            "source_summary": source_summary,
            "deployment_preview": deployment_preview,
        }


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
