from __future__ import annotations

import ipaddress
from typing import Any


def normalize_protocol(value: str) -> str:
    protocol = str(value or "iscsi").strip().lower()
    return protocol if protocol in {"iscsi", "nfs"} else "iscsi"


def build_naming(kit_id: str) -> dict[str, str]:
    clean = str(kit_id or "Kit-01").strip() or "Kit-01"
    return {
        "cluster_name": clean,
        "svm_name": f"{clean}-SVM",
        "svm_root_volume": f"{clean}_SVM_root",
        "node_01": f"{clean}-01",
        "node_02": f"{clean}-02",
        "svm_mgmt_lif": f"{clean}-SVM_admin1",
        "iscsi_portset": "iSCSI",
        "iscsi_igroup": f"{clean}_ESXi_Servers",
        "nfs_export_policy": f"{clean}_ESXi_NFS",
        "vmware_datacenter": clean,
        "vmware_cluster": f"{clean}-Cluster",
        "vmware_role": f"{clean}VirtualManagers",
    }


def subnet_metadata(cidr: str) -> dict[str, Any]:
    network = ipaddress.ip_network(str(cidr or ""), strict=False)
    hosts = list(network.hosts())
    return {
        "cidr": str(network),
        "netmask": str(network.netmask),
        "prefixlen": int(network.prefixlen),
        "broadcast": str(network.broadcast_address),
        "first_usable": str(hosts[0]) if hosts else "",
        "last_usable": str(hosts[-1]) if hosts else "",
        "usable_range": f"{hosts[0]} - {hosts[-1]}" if hosts else "",
    }


def default_iscsi_lifs(kit_id: str) -> list[dict[str, str]]:
    names = build_naming(kit_id)
    return [
        {"name": f"{names['node_01']}_iscsi_lif_1", "ip": "192.168.1.51", "node": names["node_01"], "port": "a0a"},
        {"name": f"{names['node_01']}_iscsi_lif_2", "ip": "192.168.1.52", "node": names["node_01"], "port": "a0a"},
        {"name": f"{names['node_02']}_iscsi_lif_1", "ip": "192.168.1.53", "node": names["node_02"], "port": "a0a"},
        {"name": f"{names['node_02']}_iscsi_lif_2", "ip": "192.168.1.54", "node": names["node_02"], "port": "a0a"},
    ]


def default_nfs_lifs(kit_id: str, preferred_ips: list[str] | None = None) -> list[dict[str, str]]:
    names = build_naming(kit_id)
    ips = list(preferred_ips or ["", ""])
    while len(ips) < 2:
        ips.append("")
    return [
        {"name": f"{names['node_01']}_nfs_lif_1", "ip": ips[0], "node": names["node_01"], "port": "a0a"},
        {"name": f"{names['node_02']}_nfs_lif_1", "ip": ips[1], "node": names["node_02"], "port": "a0a"},
    ]


def normalize_lifs(items: Any) -> list[dict[str, str]]:
    lifs: list[dict[str, str]] = []
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        ip = str(item.get("ip") or item.get("address") or "").strip()
        node = str(item.get("node") or item.get("home_node") or "").strip()
        port = str(item.get("port") or item.get("home_port") or "").strip()
        if not any([name, ip, node, port]):
            continue
        lifs.append({"name": name, "ip": ip, "node": node, "port": port})
    return lifs


def build_protocol_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    kit_id = str(((cfg.get("site") or {}).get("name") or "Kit-01")).strip()
    names = build_naming(kit_id)
    netapp_cfg = cfg.get("netapp") or {}
    desired = netapp_cfg.get("desired") or {}
    protocol = normalize_protocol(netapp_cfg.get("storage_protocol") or "iscsi")
    ip_plan = cfg.get("ip_plan") or {}
    management_cidr = str(((cfg.get("shared_network") or {}).get("subnet") or ip_plan.get("subnet") or "192.168.1.0/24")).strip()
    management = subnet_metadata(management_cidr)
    nfs_lifs = normalize_lifs(((netapp_cfg.get("nfs") or {}).get("lifs")) or ((desired.get("nfs") or {}).get("lifs")))
    iscsi_lifs = normalize_lifs(((netapp_cfg.get("iscsi") or {}).get("lifs")) or ((desired.get("iscsi") or {}).get("lifs")))
    profile = {
        "protocol": protocol,
        "base": {
            "cluster_name": str(netapp_cfg.get("cluster_name") or desired.get("cluster_name") or names["cluster_name"]).strip(),
            "svm_name": str(netapp_cfg.get("svm_name") or desired.get("svm_name") or names["svm_name"]).strip(),
            "node_01": names["node_01"],
            "node_02": names["node_02"],
            "aggregate_node_01": str(netapp_cfg.get("aggregate_node_01") or desired.get("aggregate_node_01") or "aggr_01").strip(),
            "aggregate_node_02": str(netapp_cfg.get("aggregate_node_02") or desired.get("aggregate_node_02") or "aggr_02").strip(),
            "data_broadcast_domain": str(netapp_cfg.get("data_broadcast_domain") or desired.get("data_broadcast_domain") or "Data").strip(),
            "management_subnet": management,
        },
        "iscsi": {
            "subnet": str((((netapp_cfg.get("iscsi") or {}).get("subnet")) or ((desired.get("iscsi") or {}).get("subnet")) or "192.168.1.0/24")).strip(),
            "gateway": str((((netapp_cfg.get("iscsi") or {}).get("gateway")) or ((desired.get("iscsi") or {}).get("gateway")) or "192.168.1.1")).strip(),
            "ip_range": str((((netapp_cfg.get("iscsi") or {}).get("ip_range")) or ((desired.get("iscsi") or {}).get("ip_range")) or "192.168.1.11-192.168.1.60")).strip(),
            "portset_name": str((((netapp_cfg.get("iscsi") or {}).get("portset_name")) or ((desired.get("iscsi") or {}).get("portset")) or names["iscsi_portset"])).strip(),
            "igroup_name": str((((netapp_cfg.get("iscsi") or {}).get("igroup_name")) or ((desired.get("iscsi") or {}).get("igroup")) or names["iscsi_igroup"])).strip(),
            "lifs": iscsi_lifs or default_iscsi_lifs(kit_id),
            "volumes": list(((netapp_cfg.get("iscsi") or {}).get("volumes")) or ((desired.get("iscsi") or {}).get("volumes")) or []),
        },
        "nfs": {
            "allowed_subnet": str((((netapp_cfg.get("nfs") or {}).get("allowed_subnet")) or ((desired.get("nfs") or {}).get("allowed_subnet")) or management["cidr"])).strip(),
            "export_policy": str((((netapp_cfg.get("nfs") or {}).get("export_policy")) or ((desired.get("nfs") or {}).get("export_policy")) or names["nfs_export_policy"])).strip(),
            "lifs": nfs_lifs or default_nfs_lifs(kit_id),
            "volumes": list(((netapp_cfg.get("nfs") or {}).get("volumes")) or ((desired.get("nfs") or {}).get("volumes")) or []),
        },
        "vmware": {
            "datacenter_name": str(((cfg.get("vmware") or {}).get("datacenter_name") or names["vmware_datacenter"])).strip(),
            "cluster_name": str(((cfg.get("vmware") or {}).get("cluster_name") or names["vmware_cluster"])).strip(),
            "role_name": names["vmware_role"],
        },
    }
    return profile


def validate_protocol_networks(profile: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    management_cidr = str((((profile.get("base") or {}).get("management_subnet") or {}).get("cidr") or "")).strip()
    if management_cidr:
        mgmt_network = ipaddress.ip_network(management_cidr, strict=False)
        iscsi_subnet = str(((profile.get("iscsi") or {}).get("subnet") or "")).strip()
        if iscsi_subnet:
            try:
                iscsi_network = ipaddress.ip_network(iscsi_subnet, strict=False)
                if iscsi_network.overlaps(mgmt_network):
                    warnings.append("The iSCSI subnet overlaps the management subnet.")
            except ValueError:
                warnings.append("The iSCSI subnet is not a valid CIDR.")
        for lif in list((profile.get("nfs") or {}).get("lifs") or []):
            ip_value = str((lif or {}).get("ip") or "").strip()
            if not ip_value:
                continue
            try:
                if ipaddress.ip_address(ip_value) not in mgmt_network:
                    warnings.append(f"NFS LIF {lif.get('name')} is outside the management/data subnet.")
            except ValueError:
                warnings.append(f"NFS LIF {lif.get('name')} has an invalid IP address.")
    return warnings
