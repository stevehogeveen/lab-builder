from __future__ import annotations

from dataclasses import dataclass
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
        try:
            response = self.session.get(self._url(path), params=params or {}, timeout=self.config.timeout)
        except requests.RequestException as exc:
            raise NetAppError(f"Connection failed: {exc}") from exc
        if response.status_code >= 400:
            text = response.text.strip()
            raise NetAppError(f"GET {path} failed ({response.status_code}): {text[:300]}")
        try:
            return response.json() if response.text else {}
        except ValueError as exc:
            raise NetAppError(f"GET {path} returned non-JSON response.") from exc

    def _records(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = self._get(path, params=params)
        records = payload.get("records")
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
        return [payload] if isinstance(payload, dict) and payload else []

    def get_cluster(self) -> dict[str, Any]:
        return self._get("/api/cluster")

    def get_nodes(self) -> list[dict[str, Any]]:
        return self._records("/api/cluster/nodes", params={"fields": "name,model,version,uuid"})

    def get_ports(self) -> list[dict[str, Any]]:
        return self._records("/api/network/ethernet/ports", params={"fields": "name,node,ipspace,broadcast_domain,mtu,state,speed"})

    def get_aggregates(self) -> list[dict[str, Any]]:
        return self._records("/api/storage/aggregates", params={"fields": "name,node,space,state,uuid"})

    def get_svms(self) -> list[dict[str, Any]]:
        return self._records("/api/svm/svms", params={"fields": "name,uuid,state,subtype,allowed_protocols"})

    def get_volumes(self) -> list[dict[str, Any]]:
        return self._records("/api/storage/volumes", params={"fields": "name,svm,aggregate,size,state,type"})

    def get_network_interfaces(self) -> list[dict[str, Any]]:
        return self._records("/api/network/ip/interfaces", params={"fields": "name,svm,ip,location,service_policy,enabled"})

    def get_licenses(self) -> list[dict[str, Any]]:
        return self._records("/api/cluster/licensing/licenses", params={"fields": "name,state,scope"})

    def get_protocol_services(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "nfs": self._records("/api/protocols/nfs/services", params={"fields": "svm,enabled,v3,v4_1,v4_2"}),
            "iscsi": self._records("/api/protocols/san/iscsi/services", params={"fields": "svm,enabled,target"}),
        }

    def get_broadcast_domains(self) -> list[dict[str, Any]]:
        return self._records("/api/network/ethernet/broadcast-domains", params={"fields": "name,ipspace,mtu,ports"})

    def build_discovery_summary(self) -> dict[str, Any]:
        warnings: list[str] = []
        cluster = self.get_cluster()
        nodes = self.get_nodes()
        ports = self.get_ports()
        broadcast_domains = self.get_broadcast_domains()
        aggregates = self.get_aggregates()
        svms = self.get_svms()
        licenses = self.get_licenses()
        protocol_services = self.get_protocol_services()

        node_names = [str(item.get("name") or "") for item in nodes if str(item.get("name") or "").strip()]
        node_models = [str(item.get("model") or "") for item in nodes if str(item.get("model") or "").strip()]
        if not nodes:
            warnings.append("No cluster nodes were returned by ONTAP.")
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
        if not licenses:
            warnings.append("No license records were returned; protocol availability may be incomplete.")

        version = str(cluster.get("version", {}).get("full") or cluster.get("version", {}).get("generation") or "")
        cluster_name = str(cluster.get("name") or "")
        available_ports = sorted({f"{(p.get('node') or {}).get('name','')}:{p.get('name','')}".strip(":") for p in ports if p.get("name")})
        return {
            "ontap_version": version,
            "cluster_name": cluster_name,
            "node_count": len(nodes),
            "node_names": node_names,
            "node_models": node_models,
            "available_ports": available_ports,
            "existing_broadcast_domains": [str(item.get("name") or "") for item in broadcast_domains if str(item.get("name") or "").strip()],
            "aggregates": [str(item.get("name") or "") for item in aggregates if str(item.get("name") or "").strip()],
            "svms": [str(item.get("name") or "") for item in svms if str(item.get("name") or "").strip()],
            "enabled_protocols": enabled_protocols,
            "warnings": warnings,
            "raw": {
                "cluster": cluster,
                "nodes": nodes,
                "ports": ports,
                "broadcast_domains": broadcast_domains,
                "aggregates": aggregates,
                "svms": svms,
                "volumes": self.get_volumes(),
                "interfaces": self.get_network_interfaces(),
                "licenses": licenses,
                "protocol_services": protocol_services,
            },
        }
