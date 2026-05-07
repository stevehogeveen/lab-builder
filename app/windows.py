from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WindowsInterfaceError(Exception):
    pass


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
        if image_kind not in {"ova", "ovf"}:
            warnings.append("Windows source image must be OVA or OVF.")
        if not image_path or not Path(image_path).exists():
            warnings.append("Windows source image file is missing.")
        labels = {
            "vsphere_host": "vSphere host",
            "vsphere_username": "vSphere username",
            "datastore": "Datastore",
            "network": "VM network",
        }
        for field, label in labels.items():
            if not str(plan.get(field) or "").strip():
                warnings.append(f"{label} is missing.")
        return {"ready": not warnings, "warnings": warnings}


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
