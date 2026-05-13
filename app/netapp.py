from __future__ import annotations

from dataclasses import dataclass
import ipaddress
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
                "name,uuid,state,subtype",
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
                "name,svm,ip,location,service_policy,enabled",
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
                "name,os_type,location,space,comment,svm,state,serial_number",
                "name,os_type,location,svm,state,comment",
                "name,os_type,svm,state",
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
                ["name,os_type,location,space,comment,svm,state,serial_number", "name,os_type,location,svm,state,comment", "name,os_type,svm,state", None],
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
        if not enabled_protocols:
            allowed_set = {str(p).lower() for svm in svms for p in list(svm.get("allowed_protocols") or [])}
            if "nfs" in allowed_set:
                enabled_protocols.append("nfs")
            if "iscsi" in allowed_set:
                enabled_protocols.append("iscsi")
        if "nfs" not in enabled_protocols and export_policies:
            enabled_protocols.append("nfs")
        if "iscsi" not in enabled_protocols and (igroups or portsets or luns or lun_maps):
            enabled_protocols.append("iscsi")
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
                ["name,svm,aggregate,size,state,type", "name,svm,size,state,type", "name,size,state,type", None],
            )
        except NetAppError:
            volumes = []
            capabilities["volumes"] = False
            capability_status["volumes"] = "missing"
            warnings.append("Volume inventory could not be read through REST API.")
        try:
            interfaces, capability_status["network_interfaces"] = self._records_with_fallback(
                "/api/network/ip/interfaces",
                ["name,svm,ip,location,service_policy,enabled", "name,svm,ip,location,enabled", "name,svm,ip,location", None],
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
            volume_details.append(
                {
                    "name": name,
                    "svm": str(((volume.get("svm") or {}).get("name") or "")).strip(),
                    "aggregate": str(((volume.get("aggregate") or {}).get("name") or ((list(volume.get("aggregates") or [{}])[0]).get("name") if list(volume.get("aggregates") or []) else "")) or "").strip(),
                    "state": str(volume.get("state") or "").strip(),
                    "type": str(volume.get("type") or "").strip(),
                    "size": volume.get("size"),
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
            lun_details.append(
                {
                    "name": name,
                    "svm": str(((lun.get("svm") or {}).get("name") or "")).strip(),
                    "os_type": str(lun.get("os_type") or "").strip(),
                    "state": str(lun.get("state") or "").strip(),
                    "comment": str(lun.get("comment") or "").strip(),
                    "volume": str(((location.get("volume") or {}).get("name") or "")).strip(),
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
        return {
            "ontap_version": version,
            "cluster_name": cluster_name,
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
            "svms": [str(item.get("name") or "") for item in svms if str(item.get("name") or "").strip()],
            "svm_details": svm_details,
            "lif_details": lif_details,
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
            "enabled_protocols": enabled_protocols,
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
