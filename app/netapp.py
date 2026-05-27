from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
from pathlib import Path
import re
from typing import Any

import requests
import urllib3
from requests.auth import HTTPBasicAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class NetAppConfig:
    host: str
    username: str
    password: str
    verify_tls: bool = False
    timeout: int = 20


class NetAppError(Exception):
    pass


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_bytes(value: Any) -> str:
    number = _int_or_none(value)
    if number is None:
        return "unknown"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    rendered = float(number)
    for unit in units:
        if abs(rendered) < 1024 or unit == units[-1]:
            return f"{rendered:.1f} {unit}" if unit != "B" else f"{int(rendered)} B"
        rendered /= 1024
    return f"{number} B"


def _space_metric(record: dict[str, Any], names: tuple[str, ...]) -> int | None:
    space = record.get("space") if isinstance(record.get("space"), dict) else {}
    candidates: list[dict[str, Any]] = [record]
    if isinstance(space, dict):
        candidates.append(space)
        for nested_name in ("block_storage", "logical_space", "footprint", "performance_tier"):
            nested = space.get(nested_name)
            if isinstance(nested, dict):
                candidates.append(nested)
    for container in candidates:
        for name in names:
            number = _int_or_none(container.get(name))
            if number is not None:
                return number
    return None


def _sum_known(values: list[int | None]) -> int | None:
    known = [item for item in values if item is not None]
    return sum(known) if known else None


class NetAppClient:
    def __init__(self, config: NetAppConfig) -> None:
        self.config = config
        self.base = f"https://{config.host}"
        self.session = requests.Session()
        self.session.verify = config.verify_tls
        self.session.auth = HTTPBasicAuth(config.username, config.password)
        self.session.headers.update({"Accept": "application/json"})

    def _url(self, path: str) -> str:
        clean = "/" + str(path or "").lstrip("/")
        if not clean.startswith("/api/"):
            clean = "/api" + clean
        return f"{self.base}{clean}"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        try:
            response = self.session.request(
                method.upper(),
                self._url(path),
                params=params or {},
                json=json_body,
                timeout=self.config.timeout,
            )
        except requests.RequestException as exc:
            raise NetAppError(f"Connection failed: {exc}") from exc
        if response.status_code not in ok_statuses and response.status_code >= 400:
            text = response.text.strip()
            raise NetAppError(f"{method.upper()} {path} failed ({response.status_code}): {text[:300]}")
        if response.status_code not in ok_statuses:
            text = response.text.strip()
            raise NetAppError(f"{method.upper()} {path} returned unexpected status {response.status_code}: {text[:300]}")
        try:
            return response.json() if response.text else {}
        except ValueError as exc:
            if response.status_code in ok_statuses and not response.text:
                return {}
            raise NetAppError(f"{method.upper()} {path} returned non-JSON response.") from exc

    def post(self, path: str, body: dict[str, Any], *, ok_statuses: tuple[int, ...] = (200, 201, 202)) -> dict[str, Any]:
        return self._request("POST", path, json_body=body, ok_statuses=ok_statuses)

    def patch(self, path: str, body: dict[str, Any], *, ok_statuses: tuple[int, ...] = (200, 202)) -> dict[str, Any]:
        return self._request("PATCH", path, json_body=body, ok_statuses=ok_statuses)

    def get_job(self, uuid: str) -> dict[str, Any]:
        return self._get(f"/api/cluster/jobs/{uuid}")

    def get_cluster_software(self) -> dict[str, Any]:
        return self._get("/api/cluster/software")

    def validate_cluster_software(self, version: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            "/api/cluster/software",
            params={"validate_only": "true"},
            json_body={"version": version},
            ok_statuses=(200, 202),
        )

    def start_cluster_software_update(
        self,
        version: str,
        *,
        skip_warnings: bool = False,
        stabilize_minutes: int = 8,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"stabilize_minutes": int(stabilize_minutes)}
        if skip_warnings:
            params["skip_warnings"] = "true"
        return self._request(
            "PATCH",
            "/api/cluster/software",
            params=params,
            json_body={"version": version},
            ok_statuses=(200, 202),
        )

    def private_cli_cluster_image_update(
        self,
        version: str,
        *,
        ignore_validation_warning: bool = True,
        skip_confirmation: bool = True,
        stabilize_minutes: int = 8,
    ) -> dict[str, Any]:
        body = {
            "version": version,
            "ignore-validation-warning": "true" if ignore_validation_warning else "false",
            "skip-confirmation": "true" if skip_confirmation else "false",
            "stabilize-minutes": str(int(stabilize_minutes)),
        }
        try:
            response = self.session.post(
                self._url("/api/private/cli/cluster/image/update"),
                params={"return_timeout": "0"},
                json=body,
                timeout=self.config.timeout,
            )
        except requests.ReadTimeout:
            return {
                "status": "accepted_timeout",
                "message": "ONTAP held the private CLI update request open past the HTTP timeout; polling cluster software state.",
                "request": body,
            }
        except requests.RequestException as exc:
            raise NetAppError(f"Connection failed: {exc}") from exc
        if response.status_code >= 400:
            raise NetAppError(
                f"POST /api/private/cli/cluster/image/update failed ({response.status_code}): {response.text[:300]}"
            )
        try:
            payload = response.json() if response.text else {}
        except ValueError:
            payload = {}
        payload.setdefault("status", "submitted")
        return payload

    def control_cluster_software_update(self, action: str, version: str) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        if normalized not in {"pause", "resume", "cancel"}:
            raise NetAppError(f"Unsupported ONTAP upgrade action: {action}")
        return self._request(
            "PATCH",
            "/api/cluster/software",
            params={"action": normalized},
            json_body={"version": version},
            ok_statuses=(200, 202),
        )

    def upload_cluster_software(self, file_path: str | Path) -> dict[str, Any]:
        image = Path(file_path)
        if not image.is_file():
            raise NetAppError(f"ONTAP image not found: {image}")
        url = self._url("/api/cluster/software/upload")
        with image.open("rb") as handle:
            try:
                response = self.session.post(
                    url,
                    files={"file": (image.name, handle, "application/octet-stream")},
                    timeout=max(self.config.timeout, 900),
                )
            except requests.RequestException as exc:
                raise NetAppError(f"Connection failed: {exc}") from exc
        if response.status_code >= 400:
            raise NetAppError(f"POST /api/cluster/software/upload failed ({response.status_code}): {response.text[:300]}")
        try:
            return response.json() if response.text else {}
        except ValueError:
            return {}

    def get_cluster_software_package(self, version: str) -> dict[str, Any]:
        return self._get(f"/api/cluster/software/packages/{version}")

    def _records(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = self._get(path, params=params)
        records = payload.get("records")
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
        return [payload] if isinstance(payload, dict) and payload else []

    def _records_with_fallback(self, path: str, field_variants: list[str | None]) -> tuple[list[dict[str, Any]], str]:
        last_error: NetAppError | None = None
        for index, fields in enumerate(field_variants):
            params = {"fields": fields} if fields else None
            try:
                records = self._records(path, params=params)
                return records, "native" if index == 0 else "fallback"
            except NetAppError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return [], "missing"

    def get_cluster(self) -> dict[str, Any]:
        return self._get("/api/cluster")

    def get_nodes(self) -> list[dict[str, Any]]:
        return self._records(
            "/api/cluster/nodes",
            params={"fields": "name,model,serial_number,state,version,ha,uuid"},
        )

    def get_ports(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/network/ethernet/ports",
            [
                "name,node,broadcast_domain,broadcast_domain.ipspace.name,mtu,state,speed,type,enabled,mac_address",
                "name,node,broadcast_domain,mtu,state,speed,type,enabled,mac_address",
                "name,node,broadcast_domain,mtu,state,speed,type",
                None,
            ],
        )
        return records

    def get_aggregates(self) -> list[dict[str, Any]]:
        return self._records("/api/storage/aggregates", params={"fields": "name,node,space,state,uuid"})

    def get_disks(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/storage/disks",
            [
                "name,node,owner,vendor,model,class,type,state,firmware_revision,usable_size,physical_size,container_type,container_name,self_encrypting",
                "name,node,owner,vendor,model,type,state,firmware_revision,usable_size,physical_size",
                "name,node,type,state,vendor,model",
                None,
            ],
        )
        return records

    def get_svms(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/svm/svms",
            [
                "name,uuid,state,subtype,allowed_protocols",
                "name,uuid,state,subtype",
                "name,state,subtype,allowed_protocols",
                "name,state,subtype",
                "name,state",
                None,
            ],
        )
        return records

    def get_volumes(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/storage/volumes",
            [
                "name,svm,aggregates,size,space,state,type,nas.path,nas.export_policy",
                "name,svm,aggregate,size,space,state,type,nas.path,nas.export_policy",
                "name,svm,size,space,state,type,nas.path,nas.export_policy",
                "name,svm,aggregates,size,state,type,nas.path,nas.export_policy",
                "name,svm,aggregate,size,state,type,nas.path,nas.export_policy",
                "name,svm,size,state,type,nas.path,nas.export_policy",
                "name,size,state,type,nas.path",
                None,
            ],
        )
        return records

    def get_network_interfaces(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/network/ip/interfaces",
            [
                "name,uuid,scope,svm,ip,location,service_policy,enabled",
                "name,uuid,svm,ip,location,service_policy,enabled",
                "name,svm,ip,location,enabled",
                "name,svm,ip,location",
                None,
            ],
        )
        return records

    def get_licenses(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/cluster/licensing/licenses",
            [
                "name,state,scope",
                "name,state",
                "name",
                None,
            ],
        )
        return records

    def get_protocol_services(self) -> dict[str, list[dict[str, Any]]]:
        nfs_records, _ = self._records_with_fallback(
            "/api/protocols/nfs/services",
            [
                "svm,enabled,v3,v4_1,v4_2",
                "svm,enabled,v3,v4_1",
                "svm,enabled",
                None,
            ],
        )
        iscsi_records, _ = self._records_with_fallback(
            "/api/protocols/san/iscsi/services",
            [
                "svm,enabled,target",
                "svm,enabled",
                "enabled",
                None,
            ],
        )
        return {"nfs": nfs_records, "iscsi": iscsi_records}

    def get_fcp_services(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/protocols/san/fcp/services",
            [
                "svm,enabled,target",
                "svm,enabled",
                "enabled",
                None,
            ],
        )
        return records

    def get_fc_interfaces(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/network/fc/interfaces",
            [
                "name,uuid,svm,location,enabled,wwpn",
                "name,svm,location,enabled,wwpn",
                "name,svm,location,wwpn",
                "name,svm,wwpn",
                None,
            ],
        )
        return records

    def get_fc_ports(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/network/fc/ports",
            [
                "name,uuid,node,enabled,speed,state,wwpn,supported_protocols,fabric",
                "name,node,enabled,speed,state,wwpn,supported_protocols",
                "name,node,enabled,state,wwpn",
                "name,node,state",
                None,
            ],
        )
        return records

    def get_export_policies(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/protocols/nfs/export-policies",
            [
                "name,svm,rules",
                "name,svm",
                "name",
                None,
            ],
        )
        return records

    def get_igroups(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/protocols/san/igroups",
            [
                "name,svm,os_type,protocol,initiators",
                "name,svm,os_type,protocol",
                "name,protocol",
                None,
            ],
        )
        return records

    def get_portsets(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/protocols/san/portsets",
            [
                "name,svm,protocol,interfaces,igroups",
                "name,svm,protocol,interfaces",
                "name,protocol",
                None,
            ],
        )
        return records

    def get_luns(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/storage/luns",
            [
                "name,os_type,location,space,comment,svm,status.state,serial_number",
                "name,os_type,location,svm,status.state,comment",
                "name,os_type,svm,status.state",
                None,
            ],
        )
        return records

    def get_lun_maps(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/protocols/san/lun-maps",
            [
                "lun,igroup,logical_unit_number,svm",
                "lun,igroup,logical_unit_number",
                None,
            ],
        )
        return records

    def get_broadcast_domains(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/network/ethernet/broadcast-domains",
            [
                "name,ipspace,mtu,ports",
                "name,mtu,ports",
                "name,mtu",
                None,
            ],
        )
        return records

    def get_subnets(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/network/ip/subnets",
            [
                "name,ipspace,subnet,broadcast_domain,ranges,gateway",
                "name,subnet,broadcast_domain,gateway",
                "name,subnet,gateway",
                "name,subnet",
                None,
            ],
        )
        return records

    def get_ntp_servers(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/cluster/ntp/servers",
            [
                "server,is_preferred,version",
                "server,is_preferred",
                "server",
                None,
            ],
        )
        return records

    def get_autosupport(self) -> dict[str, Any]:
        return self._get("/api/support/autosupport")

    def get_users(self) -> list[dict[str, Any]]:
        records, _ = self._records_with_fallback(
            "/api/security/accounts",
            [
                "name,applications,authentication_methods,owner,role,locked",
                "name,owner,role,locked",
                "name,role,locked",
                "name,role",
                None,
            ],
        )
        return records

    def get_interface_groups(self) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for port in self.get_ports():
            port_type = str(port.get("type") or "").strip().lower()
            name = str(port.get("name") or "").strip()
            if port_type == "if_group" or re.match(r"^a\d+[a-z]?$", name):
                groups.append(port)
        return groups

    def build_discovery_summary(self) -> dict[str, Any]:
        warnings: list[str] = []
        capabilities: dict[str, bool] = {
            "cluster": True,
            "nodes": True,
            "ports": True,
            "broadcast_domains": True,
            "aggregates": True,
            "svms": True,
            "subnets": True,
            "licenses": True,
            "protocol_services": True,
            "fcp_services": True,
            "fc_interfaces": True,
            "fc_ports": True,
            "export_policies": True,
            "igroups": True,
            "portsets": True,
            "luns": True,
            "lun_maps": True,
            "disk_inventory": True,
            "autosupport": True,
            "ntp_servers": True,
            "users": True,
            "volumes": True,
            "network_interfaces": True,
        }
        capability_status: dict[str, str] = {key: "native" for key in capabilities}
        cluster = self.get_cluster()
        nodes = self.get_nodes()
        ports = self.get_ports()
        try:
            broadcast_domains, capability_status["broadcast_domains"] = self._records_with_fallback(
                "/api/network/ethernet/broadcast-domains",
                [
                    "name,ipspace,mtu,ports",
                    "name,mtu,ports",
                    "name,mtu",
                    None,
                ],
            )
        except NetAppError:
            broadcast_domains = []
            capabilities["broadcast_domains"] = False
            capability_status["broadcast_domains"] = "missing"
            warnings.append("Broadcast domains could not be read through REST API.")
        aggregates = self.get_aggregates()
        svms = self.get_svms()
        try:
            subnets, capability_status["subnets"] = self._records_with_fallback(
                "/api/network/ip/subnets",
                [
                    "name,ipspace,subnet,broadcast_domain,ranges,gateway",
                    "name,subnet,broadcast_domain,gateway",
                    "name,subnet,gateway",
                    "name,subnet",
                    None,
                ],
            )
        except NetAppError:
            subnets = []
            capabilities["subnets"] = False
            capability_status["subnets"] = "missing"
            warnings.append("Cluster subnet objects could not be read through REST API.")
        interface_groups = self.get_interface_groups()
        try:
            licenses, capability_status["licenses"] = self._records_with_fallback(
                "/api/cluster/licensing/licenses",
                ["name,state,scope", "name,state", "name", None],
            )
        except NetAppError:
            licenses = []
            capabilities["licenses"] = False
            capability_status["licenses"] = "missing"
            warnings.append("License records could not be read through REST API.")
        try:
            nfs_services, nfs_status = self._records_with_fallback(
                "/api/protocols/nfs/services",
                ["svm,enabled,v3,v4_1,v4_2", "svm,enabled,v3,v4_1", "svm,enabled", None],
            )
            iscsi_services, iscsi_status = self._records_with_fallback(
                "/api/protocols/san/iscsi/services",
                ["svm,enabled,target", "svm,enabled", "enabled", None],
            )
            protocol_services = {"nfs": nfs_services, "iscsi": iscsi_services}
            capability_status["protocol_services"] = (
                "native"
                if nfs_status == "native" and iscsi_status == "native"
                else "fallback"
            )
        except NetAppError:
            protocol_services = {"nfs": [], "iscsi": []}
            capabilities["protocol_services"] = False
            capability_status["protocol_services"] = "missing"
            warnings.append("Protocol service records could not be read through REST API.")
        try:
            fcp_services, capability_status["fcp_services"] = self._records_with_fallback(
                "/api/protocols/san/fcp/services",
                ["svm,enabled,target", "svm,enabled", "enabled", None],
            )
            protocol_services["fc"] = fcp_services
        except NetAppError:
            fcp_services = []
            protocol_services["fc"] = []
            capabilities["fcp_services"] = False
            capability_status["fcp_services"] = "missing"
            warnings.append("FC protocol service records could not be read through REST API.")
        try:
            fc_interfaces, capability_status["fc_interfaces"] = self._records_with_fallback(
                "/api/network/fc/interfaces",
                ["name,uuid,svm,location,enabled,wwpn", "name,svm,location,enabled,wwpn", "name,svm,wwpn", None],
            )
        except NetAppError:
            fc_interfaces = []
            capabilities["fc_interfaces"] = False
            capability_status["fc_interfaces"] = "missing"
            warnings.append("FC interfaces could not be read through REST API.")
        try:
            fc_ports, capability_status["fc_ports"] = self._records_with_fallback(
                "/api/network/fc/ports",
                [
                    "name,uuid,node,enabled,speed,state,wwpn,supported_protocols,fabric",
                    "name,node,enabled,speed,state,wwpn,supported_protocols",
                    "name,node,state",
                    None,
                ],
            )
        except NetAppError:
            fc_ports = []
            capabilities["fc_ports"] = False
            capability_status["fc_ports"] = "missing"
            warnings.append("FC port inventory could not be read through REST API.")
        try:
            export_policies, capability_status["export_policies"] = self._records_with_fallback(
                "/api/protocols/nfs/export-policies",
                ["name,svm,rules", "name,svm", "name", None],
            )
        except NetAppError:
            export_policies = []
            capabilities["export_policies"] = False
            capability_status["export_policies"] = "missing"
            warnings.append("NFS export policies could not be read through REST API.")
        try:
            igroups, capability_status["igroups"] = self._records_with_fallback(
                "/api/protocols/san/igroups",
                ["name,svm,os_type,protocol,initiators", "name,svm,os_type,protocol", "name,protocol", None],
            )
        except NetAppError:
            igroups = []
            capabilities["igroups"] = False
            capability_status["igroups"] = "missing"
            warnings.append("SAN initiator groups could not be read through REST API.")
        try:
            portsets, capability_status["portsets"] = self._records_with_fallback(
                "/api/protocols/san/portsets",
                ["name,svm,protocol,interfaces,igroups", "name,svm,protocol,interfaces", "name,protocol", None],
            )
        except NetAppError:
            portsets = []
            capabilities["portsets"] = False
            capability_status["portsets"] = "missing"
            warnings.append("SAN portsets could not be read through REST API.")
        try:
            luns, capability_status["luns"] = self._records_with_fallback(
                "/api/storage/luns",
                [
                    "name,os_type,location,space,comment,svm,status.state,serial_number",
                    "name,os_type,location,svm,status.state,comment",
                    "name,os_type,svm,status.state",
                    None,
                ],
            )
        except NetAppError:
            luns = []
            capabilities["luns"] = False
            capability_status["luns"] = "missing"
            warnings.append("LUN inventory could not be read through REST API.")
        try:
            lun_maps, capability_status["lun_maps"] = self._records_with_fallback(
                "/api/protocols/san/lun-maps",
                ["lun,igroup,logical_unit_number,svm", "lun,igroup,logical_unit_number", None],
            )
        except NetAppError:
            lun_maps = []
            capabilities["lun_maps"] = False
            capability_status["lun_maps"] = "missing"
            warnings.append("LUN mappings could not be read through REST API.")
        try:
            disks, capability_status["disk_inventory"] = self._records_with_fallback(
                "/api/storage/disks",
                [
                    "name,node,owner,vendor,model,class,type,state,firmware_revision,usable_size,physical_size,container_type,container_name,self_encrypting",
                    "name,node,owner,vendor,model,type,state,firmware_revision,usable_size,physical_size",
                    "name,node,type,state,vendor,model",
                    None,
                ],
            )
        except NetAppError:
            disks = []
            capabilities["disk_inventory"] = False
            capability_status["disk_inventory"] = "missing"
            warnings.append("Disk inventory could not be read through REST API.")

        node_names = [str(item.get("name") or "") for item in nodes if str(item.get("name") or "").strip()]
        node_models = [str(item.get("model") or "") for item in nodes if str(item.get("model") or "").strip()]
        if not nodes:
            warnings.append("No cluster nodes were returned by ONTAP.")
        node_details: list[dict[str, Any]] = []
        for item in nodes:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            version = item.get("version") or {}
            ha = item.get("ha") or {}
            node_details.append(
                {
                    "name": name,
                    "model": str(item.get("model") or "").strip(),
                    "serial_number": str(item.get("serial_number") or "").strip(),
                    "state": str(item.get("state") or "").strip(),
                    "ontap_version": str(version.get("full") or version.get("generation") or "").strip(),
                    "ha_enabled": bool(ha.get("enabled")) if isinstance(ha, dict) else False,
                    "ha_auto_giveback": bool(ha.get("auto_giveback")) if isinstance(ha, dict) else False,
                    "ha_takeover_state": str(((ha.get("takeover") or {}).get("state") or "")).strip() if isinstance(ha, dict) else "",
                    "ha_giveback_state": str(((ha.get("giveback") or {}).get("state") or "")).strip() if isinstance(ha, dict) else "",
                }
            )
        enabled_protocols: list[str] = []
        if any(bool(item.get("enabled")) for item in protocol_services.get("nfs", [])):
            enabled_protocols.append("nfs")
        if any(bool(item.get("enabled")) for item in protocol_services.get("iscsi", [])):
            enabled_protocols.append("iscsi")
        if any(bool(item.get("enabled")) for item in protocol_services.get("fc", [])):
            enabled_protocols.append("fc")
        if not enabled_protocols:
            allowed_set = {str(p).lower() for svm in svms for p in list(svm.get("allowed_protocols") or [])}
            if "nfs" in allowed_set:
                enabled_protocols.append("nfs")
            if "iscsi" in allowed_set:
                enabled_protocols.append("iscsi")
            if "fcp" in allowed_set or "fc" in allowed_set:
                enabled_protocols.append("fc")
        if "nfs" not in enabled_protocols and export_policies:
            enabled_protocols.append("nfs")
        if "iscsi" not in enabled_protocols and (igroups or portsets or luns or lun_maps):
            enabled_protocols.append("iscsi")
        if "fc" not in enabled_protocols and (fc_interfaces or fc_ports):
            enabled_protocols.append("fc")
        try:
            autosupport = self.get_autosupport()
        except NetAppError:
            autosupport = {}
            capabilities["autosupport"] = False
            capability_status["autosupport"] = "missing"
            warnings.append("AutoSupport settings could not be read through REST API.")
        try:
            ntp_servers, capability_status["ntp_servers"] = self._records_with_fallback(
                "/api/cluster/ntp/servers",
                ["server,is_preferred,version", "server,is_preferred", "server", None],
            )
        except NetAppError:
            ntp_servers = []
            capabilities["ntp_servers"] = False
            capability_status["ntp_servers"] = "missing"
            warnings.append("NTP server settings could not be read through REST API.")
        try:
            users, capability_status["users"] = self._records_with_fallback(
                "/api/security/accounts",
                [
                    "name,applications,authentication_methods,owner,role,locked",
                    "name,owner,role,locked",
                    "name,role,locked",
                    "name,role",
                    None,
                ],
            )
        except NetAppError:
            users = []
            capabilities["users"] = False
            capability_status["users"] = "missing"
            warnings.append("User account settings could not be read through REST API.")

        version = str(cluster.get("version", {}).get("full") or cluster.get("version", {}).get("generation") or "")
        cluster_name = str(cluster.get("name") or "")
        available_ports = sorted({f"{(p.get('node') or {}).get('name','')}:{p.get('name','')}".strip(":") for p in ports if p.get("name")})
        try:
            volumes, capability_status["volumes"] = self._records_with_fallback(
                "/api/storage/volumes",
                [
                    "name,svm,aggregate,aggregates,size,space,state,type,nas.path,nas.export_policy",
                    "name,svm,aggregate,size,space,state,type",
                    "name,svm,size,space,state,type",
                    "name,svm,size,state,type",
                    "name,size,state,type",
                    None,
                ],
            )
        except NetAppError:
            volumes = []
            capabilities["volumes"] = False
            capability_status["volumes"] = "missing"
            warnings.append("Volume inventory could not be read through REST API.")
        try:
            interfaces, capability_status["network_interfaces"] = self._records_with_fallback(
                "/api/network/ip/interfaces",
                [
                    "name,uuid,scope,svm,ip,location,service_policy,enabled",
                    "name,uuid,svm,ip,location,service_policy,enabled",
                    "name,svm,ip,location,enabled",
                    "name,svm,ip,location",
                    None,
                ],
            )
        except NetAppError:
            interfaces = []
            capabilities["network_interfaces"] = False
            capability_status["network_interfaces"] = "missing"
            warnings.append("Network interface records could not be read through REST API.")
        capabilities = {key: capability_status.get(key) != "missing" for key in capabilities}
        observed_networks: list[str] = []
        observed_network_set: set[str] = set()
        disk_inventory: list[dict[str, Any]] = []
        disk_counts: dict[str, int] = {}
        disk_type_counts: dict[str, int] = {}
        for disk in disks:
            disk_name = str(disk.get("name") or "").strip()
            if not disk_name:
                continue
            node_name = str(((disk.get("node") or {}).get("name") or "")).strip()
            disk_type = str(disk.get("type") or "").strip()
            disk_state = str(disk.get("state") or "").strip()
            disk_inventory.append(
                {
                    "name": disk_name,
                    "node": node_name,
                    "owner": str(((disk.get("owner") or {}).get("name") or "")).strip(),
                    "vendor": str(disk.get("vendor") or "").strip(),
                    "model": str(disk.get("model") or "").strip(),
                    "class": str(disk.get("class") or "").strip(),
                    "type": disk_type,
                    "state": disk_state,
                    "firmware_revision": str(disk.get("firmware_revision") or "").strip(),
                    "container_type": str(disk.get("container_type") or "").strip(),
                    "container_name": str(disk.get("container_name") or "").strip(),
                    "usable_size": disk.get("usable_size"),
                    "physical_size": disk.get("physical_size"),
                    "self_encrypting": bool(disk.get("self_encrypting")),
                }
            )
            if node_name:
                disk_counts[node_name] = disk_counts.get(node_name, 0) + 1
            if disk_type:
                disk_type_counts[disk_type] = disk_type_counts.get(disk_type, 0) + 1
        aggregate_details: list[dict[str, Any]] = []
        for aggregate in aggregates:
            name = str(aggregate.get("name") or "").strip()
            if not name:
                continue
            total = _space_metric(aggregate, ("size", "total", "total_size"))
            used = _space_metric(aggregate, ("used", "used_size"))
            available = _space_metric(aggregate, ("available", "available_size"))
            used_percent = round((used / total) * 100, 1) if used is not None and total else None
            aggregate_details.append(
                {
                    "name": name,
                    "node": str(((aggregate.get("node") or {}).get("name") or "")).strip(),
                    "state": str(aggregate.get("state") or "").strip(),
                    "size": total,
                    "size_label": _format_bytes(total),
                    "used": used,
                    "used_label": _format_bytes(used),
                    "available": available,
                    "available_label": _format_bytes(available),
                    "used_percent": used_percent,
                }
            )
        svm_details: list[dict[str, Any]] = []
        for svm in svms:
            name = str(svm.get("name") or "").strip()
            if not name:
                continue
            svm_details.append(
                {
                    "name": name,
                    "state": str(svm.get("state") or "").strip(),
                    "subtype": str(svm.get("subtype") or "").strip(),
                    "allowed_protocols": [str(item).strip().lower() for item in list(svm.get("allowed_protocols") or []) if str(item).strip()],
                }
            )
        lif_details: list[dict[str, Any]] = []
        for interface in interfaces:
            name = str(interface.get("name") or "").strip()
            if not name:
                continue
            location = interface.get("location") or {}
            ip = interface.get("ip") or {}
            svm = interface.get("svm") or {}
            address = str(ip.get("address") or "").strip()
            netmask = str(ip.get("netmask") or "").strip()
            if address and netmask:
                try:
                    network_text = str(ipaddress.IPv4Network(f"{address}/{netmask}", strict=False))
                except Exception:
                    network_text = ""
                if network_text and network_text not in observed_network_set:
                    observed_network_set.add(network_text)
                    observed_networks.append(network_text)
            lif_details.append(
                {
                    "name": name,
                    "uuid": str(interface.get("uuid") or "").strip(),
                    "scope": str(interface.get("scope") or "").strip(),
                    "svm": str(svm.get("name") or "").strip(),
                    "address": address,
                    "netmask": netmask,
                    "home_node": str(((location.get("home_node") or {}).get("name") or "")).strip(),
                    "home_port": str(((location.get("home_port") or {}).get("name") or "")).strip(),
                    "service_policy": str(((interface.get("service_policy") or {}).get("name") or "")).strip(),
                    "enabled": bool(interface.get("enabled")),
                }
            )
        discovered_cluster_mgmt_lif: dict[str, Any] = {}
        discovered_node_mgmt_lifs: list[dict[str, Any]] = []
        discovered_svm_management_lifs: list[dict[str, Any]] = []
        discovered_nfs_lifs: list[dict[str, Any]] = []
        discovered_iscsi_lifs: list[dict[str, Any]] = []
        node_mgmt_by_node: dict[str, str] = {}
        for lif in lif_details:
            name = str(lif.get("name") or "").strip()
            name_lower = name.lower()
            svm_name = str(lif.get("svm") or "").strip()
            svm_lower = svm_name.lower()
            home_node = str(lif.get("home_node") or "").strip()
            home_port = str(lif.get("home_port") or "").strip()
            service_policy = str(lif.get("service_policy") or "").strip()
            service_lower = service_policy.lower()
            is_management = any(token in service_lower for token in ("management", "mgmt")) or any(
                token in name_lower for token in ("mgmt", "admin")
            )
            is_cluster_mgmt = "cluster_mgmt" in name_lower or (
                is_management and not svm_name and "cluster" in name_lower
            )
            is_svm_mgmt = is_management and (
                "svm_admin" in name_lower
                or (svm_name and svm_lower not in {"cluster", "admin"})
            )
            is_node_mgmt = (
                is_management
                and home_port.lower() == "e0m"
                and not is_cluster_mgmt
                and not is_svm_mgmt
            )
            if is_cluster_mgmt and not discovered_cluster_mgmt_lif:
                discovered_cluster_mgmt_lif = dict(lif)
            if is_node_mgmt:
                discovered_node_mgmt_lifs.append(dict(lif))
                if home_node and str(lif.get("address") or "").strip():
                    node_mgmt_by_node[home_node] = str(lif.get("address") or "").strip()
            if is_svm_mgmt:
                discovered_svm_management_lifs.append(dict(lif))
            if not is_management and ("nfs" in name_lower or "files" in service_lower):
                discovered_nfs_lifs.append(dict(lif))
            if not is_management and ("iscsi" in name_lower or "block" in service_lower):
                discovered_iscsi_lifs.append(dict(lif))
        if not discovered_cluster_mgmt_lif:
            cluster_candidates = [
                lif
                for lif in lif_details
                if str(lif.get("name") or "").strip().lower() == "cluster_mgmt"
            ]
            if cluster_candidates:
                discovered_cluster_mgmt_lif = dict(cluster_candidates[0])
        discovered_node_mgmt_lifs.sort(key=lambda item: (str(item.get("home_node") or ""), str(item.get("name") or "")))
        volume_details: list[dict[str, Any]] = []
        for volume in volumes:
            name = str(volume.get("name") or "").strip()
            if not name:
                continue
            size = _space_metric(volume, ("size", "total", "total_size"))
            used = _space_metric(volume, ("used", "used_size"))
            available = _space_metric(volume, ("available", "available_size"))
            used_percent = round((used / size) * 100, 1) if used is not None and size else None
            volume_details.append(
                {
                    "name": name,
                    "uuid": str(volume.get("uuid") or "").strip(),
                    "svm": str(((volume.get("svm") or {}).get("name") or "")).strip(),
                    "aggregate": str(((volume.get("aggregate") or {}).get("name") or ((list(volume.get("aggregates") or [{}])[0]).get("name") if list(volume.get("aggregates") or []) else "")) or "").strip(),
                    "state": str(volume.get("state") or "").strip(),
                    "type": str(volume.get("type") or "").strip(),
                    "size": size,
                    "size_label": _format_bytes(size),
                    "used": used,
                    "used_label": _format_bytes(used),
                    "available": available,
                    "available_label": _format_bytes(available),
                    "used_percent": used_percent,
                    "nas_path": str(((volume.get("nas") or {}).get("path") or "")).strip(),
                    "export_policy": str((((volume.get("nas") or {}).get("export_policy") or {}).get("name") or "")).strip(),
                }
            )
        export_policy_details: list[dict[str, Any]] = []
        for policy in export_policies:
            name = str(policy.get("name") or "").strip()
            if not name:
                continue
            export_policy_details.append(
                {
                    "name": name,
                    "svm": str(((policy.get("svm") or {}).get("name") or "")).strip(),
                    "rule_count": len(list(policy.get("rules") or [])),
                }
            )
        igroup_details: list[dict[str, Any]] = []
        for igroup in igroups:
            name = str(igroup.get("name") or "").strip()
            if not name:
                continue
            igroup_details.append(
                {
                    "name": name,
                    "svm": str(((igroup.get("svm") or {}).get("name") or "")).strip(),
                    "os_type": str(igroup.get("os_type") or "").strip(),
                    "protocol": str(igroup.get("protocol") or "").strip().lower(),
                    "initiator_count": len(list(igroup.get("initiators") or [])),
                }
            )
        portset_details: list[dict[str, Any]] = []
        for portset in portsets:
            name = str(portset.get("name") or "").strip()
            if not name:
                continue
            portset_details.append(
                {
                    "name": name,
                    "svm": str(((portset.get("svm") or {}).get("name") or "")).strip(),
                    "protocol": str(portset.get("protocol") or "").strip().lower(),
                    "interface_count": len(list(portset.get("interfaces") or [])),
                    "igroup_count": len(list(portset.get("igroups") or [])),
                }
            )
        lun_details: list[dict[str, Any]] = []
        for lun in luns:
            name = str(lun.get("name") or "").strip()
            if not name:
                continue
            location = lun.get("location") or {}
            status = lun.get("status") or {}
            size = _space_metric(lun, ("size", "total", "total_size"))
            used = _space_metric(lun, ("used", "used_size"))
            lun_details.append(
                {
                    "name": name,
                    "svm": str(((lun.get("svm") or {}).get("name") or "")).strip(),
                    "os_type": str(lun.get("os_type") or "").strip(),
                    "state": str(lun.get("state") or (status.get("state") if isinstance(status, dict) else "") or "").strip(),
                    "comment": str(lun.get("comment") or "").strip(),
                    "volume": str(((location.get("volume") or {}).get("name") or "")).strip(),
                    "size": size,
                    "size_label": _format_bytes(size),
                    "used": used,
                    "used_label": _format_bytes(used),
                }
            )
        lun_map_details: list[dict[str, Any]] = []
        for lun_map in lun_maps:
            lun_name = str(((lun_map.get("lun") or {}).get("name") or "")).strip()
            igroup_name = str(((lun_map.get("igroup") or {}).get("name") or "")).strip()
            if not lun_name and not igroup_name:
                continue
            lun_map_details.append(
                {
                    "lun": lun_name,
                    "igroup": igroup_name,
                    "lun_id": lun_map.get("logical_unit_number"),
                    "svm": str(((lun_map.get("svm") or {}).get("name") or "")).strip(),
                }
            )
        fc_service_details: list[dict[str, Any]] = []
        for service in fcp_services:
            fc_service_details.append(
                {
                    "svm": str(((service.get("svm") or {}).get("name") or "")).strip(),
                    "enabled": bool(service.get("enabled")),
                    "target": str(((service.get("target") or {}).get("name") or service.get("target") or "")).strip(),
                }
            )
        fc_interface_details: list[dict[str, Any]] = []
        for interface in fc_interfaces:
            name = str(interface.get("name") or "").strip()
            location = interface.get("location") or {}
            if not name and not interface.get("wwpn"):
                continue
            fc_interface_details.append(
                {
                    "name": name,
                    "uuid": str(interface.get("uuid") or "").strip(),
                    "svm": str(((interface.get("svm") or {}).get("name") or "")).strip(),
                    "wwpn": str(interface.get("wwpn") or "").strip(),
                    "home_node": str(((location.get("home_node") or {}).get("name") or "")).strip(),
                    "home_port": str(((location.get("home_port") or {}).get("name") or "")).strip(),
                    "enabled": bool(interface.get("enabled")),
                }
            )
        fc_port_details: list[dict[str, Any]] = []
        for port in fc_ports:
            name = str(port.get("name") or "").strip()
            if not name:
                continue
            fabric = port.get("fabric") or {}
            fc_port_details.append(
                {
                    "name": name,
                    "uuid": str(port.get("uuid") or "").strip(),
                    "node": str(((port.get("node") or {}).get("name") or "")).strip(),
                    "enabled": bool(port.get("enabled")),
                    "state": str(port.get("state") or "").strip(),
                    "wwpn": str(port.get("wwpn") or "").strip(),
                    "configured_speed": str(((port.get("speed") or {}).get("configured") or "")).strip() if isinstance(port.get("speed"), dict) else str(port.get("speed") or "").strip(),
                    "fabric_connected": bool(fabric.get("connected")) if isinstance(fabric, dict) else False,
                    "supported_protocols": [str(item).strip().lower() for item in list(port.get("supported_protocols") or []) if str(item).strip()],
                }
            )
        volume_size = _sum_known([_int_or_none(item.get("size")) for item in volume_details])
        volume_used = _sum_known([_int_or_none(item.get("used")) for item in volume_details])
        aggregate_size = _sum_known([_int_or_none(item.get("size")) for item in aggregate_details])
        aggregate_used = _sum_known([_int_or_none(item.get("used")) for item in aggregate_details])
        aggregate_available = _sum_known([_int_or_none(item.get("available")) for item in aggregate_details])
        space_summary = {
            "volume_count": len(volume_details),
            "volume_size": volume_size,
            "volume_size_label": _format_bytes(volume_size),
            "volume_used": volume_used,
            "volume_used_label": _format_bytes(volume_used),
            "aggregate_count": len(aggregate_details),
            "aggregate_size": aggregate_size,
            "aggregate_size_label": _format_bytes(aggregate_size),
            "aggregate_used": aggregate_used,
            "aggregate_used_label": _format_bytes(aggregate_used),
            "aggregate_available": aggregate_available,
            "aggregate_available_label": _format_bytes(aggregate_available),
            "aggregate_used_percent": round((aggregate_used / aggregate_size) * 100, 1) if aggregate_used is not None and aggregate_size else None,
        }
        ip_summary = {
            "cluster_mgmt": discovered_cluster_mgmt_lif,
            "node_mgmt": discovered_node_mgmt_lifs,
            "svm_mgmt": discovered_svm_management_lifs,
            "nfs": discovered_nfs_lifs,
            "iscsi": discovered_iscsi_lifs,
            "fc": fc_interface_details,
        }
        return {
            "ontap_version": version,
            "cluster_name": cluster_name,
            "source": "Live read",
            "read_at": datetime.now(timezone.utc).isoformat(),
            "source_host": self.config.host,
            "node_count": len(nodes),
            "node_names": node_names,
            "node_models": node_models,
            "node_details": node_details,
            "available_ports": available_ports,
            "physical_ports": sorted({item for item in available_ports if item and ":" in item}),
            "nodes": node_names,
            "existing_broadcast_domains": [str(item.get("name") or "") for item in broadcast_domains if str(item.get("name") or "").strip()],
            "subnets": [str(item.get("name") or "") for item in subnets if str(item.get("name") or "").strip()],
            "observed_networks": observed_networks,
            "existing_interface_groups": sorted(
                {
                    f"{str((item.get('node') or {}).get('name') or '').strip()}:{str(item.get('name') or '').strip()}".strip(":")
                    for item in interface_groups
                    if str(item.get("name") or "").strip()
                }
            ),
            "aggregates": [str(item.get("name") or "") for item in aggregates if str(item.get("name") or "").strip()],
            "aggregate_details": aggregate_details,
            "svms": [str(item.get("name") or "") for item in svms if str(item.get("name") or "").strip()],
            "svm_details": svm_details,
            "lif_details": lif_details,
            "ip_summary": ip_summary,
            "discovered_cluster_mgmt_lif": discovered_cluster_mgmt_lif,
            "discovered_cluster_mgmt_ip": str(discovered_cluster_mgmt_lif.get("address") or "").strip(),
            "discovered_node_mgmt_lifs": discovered_node_mgmt_lifs,
            "discovered_node_mgmt_ips": node_mgmt_by_node,
            "discovered_node_mgmt_ip_list": [str(item.get("address") or "").strip() for item in discovered_node_mgmt_lifs if str(item.get("address") or "").strip()],
            "discovered_svm_management_lifs": discovered_svm_management_lifs,
            "discovered_nfs_lifs": discovered_nfs_lifs,
            "discovered_iscsi_lifs": discovered_iscsi_lifs,
            "volume_details": volume_details,
            "export_policy_details": export_policy_details,
            "igroup_details": igroup_details,
            "portset_details": portset_details,
            "lun_details": lun_details,
            "lun_map_details": lun_map_details,
            "fc_service_details": fc_service_details,
            "fc_interface_details": fc_interface_details,
            "fc_port_details": fc_port_details,
            "enabled_protocols": enabled_protocols,
            "space_summary": space_summary,
            "capabilities": capabilities,
            "capability_status": capability_status,
            "disk_count": len(disk_inventory),
            "disk_counts_by_node": disk_counts,
            "disk_types": disk_type_counts,
            "disk_inventory": disk_inventory,
            "warnings": list(dict.fromkeys(warnings)),
            "raw": {
                "cluster": cluster,
                "nodes": nodes,
                "ports": ports,
                "broadcast_domains": broadcast_domains,
                "subnets": subnets,
                "interface_groups": interface_groups,
                "aggregates": aggregates,
                "svms": svms,
                "volumes": volumes,
                "interfaces": interfaces,
                "disks": disks,
                "licenses": licenses,
                "protocol_services": protocol_services,
                "fc_interfaces": fc_interfaces,
                "fc_ports": fc_ports,
                "export_policies": export_policies,
                "igroups": igroups,
                "portsets": portsets,
                "luns": luns,
                "lun_maps": lun_maps,
                "autosupport": autosupport,
                "ntp_servers": ntp_servers,
                "users": users,
            },
        }
