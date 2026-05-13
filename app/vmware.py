from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import shlex
from typing import Any


@dataclass
class VMwareConfig:
    vcenter_ip: str
    username: str
    password: str
    datacenter_name: str
    cluster_name: str
    esxi_host_start_offset: int = 31
    esxi_host_end_offset: int = 39
    esxi_root_user: str = "root"
    esxi_root_password: str = ""
    vcenter_vm_name_match: str = "SVCNTR"
    ha_enabled: bool = True
    ha_isolation_response: str = "Shutdown"
    drs_enabled: bool = False
    startup_policy_enabled: bool = True


def build_esxi_host_range(subnet_cidr: str, start_offset: int, end_offset: int) -> list[str]:
    network = ipaddress.ip_network(str(subnet_cidr or ""), strict=False)
    lower = min(int(start_offset), int(end_offset))
    upper = max(int(start_offset), int(end_offset))
    hosts: list[str] = []
    for offset in range(lower, upper + 1):
        candidate = network.network_address + offset
        if candidate == network.network_address or candidate == network.broadcast_address or candidate not in network:
            continue
        hosts.append(str(candidate))
    return hosts


def _discovered_netapp_nfs_context(cfg: dict[str, Any], discovery: dict[str, Any] | None = None) -> dict[str, Any]:
    netapp_cfg = cfg.get("netapp") or {}
    desired = netapp_cfg.get("desired") or {}
    nfs_cfg = (netapp_cfg.get("nfs") or {}) if isinstance(netapp_cfg.get("nfs"), dict) else {}
    desired_nfs = (desired.get("nfs") or {}) if isinstance(desired.get("nfs"), dict) else {}
    discovery = discovery or {}

    discovered_lifs = [item for item in list(discovery.get("discovered_nfs_lifs") or []) if isinstance(item, dict)]
    config_lifs = [item for item in list(nfs_cfg.get("lifs") or desired_nfs.get("lifs") or []) if isinstance(item, dict)]
    lifs = discovered_lifs or config_lifs
    lif_ips = [str((item.get("address") if discovered_lifs else item.get("ip")) or "").strip() for item in lifs]
    lif_ips = [item for item in lif_ips if item]

    discovered_svm_names = [str(item).strip() for item in list(discovery.get("svms") or []) if str(item).strip()]
    svm_name = str(netapp_cfg.get("svm_name") or desired.get("svm_name") or "").strip()
    if not svm_name and len(discovered_svm_names) == 1:
        svm_name = discovered_svm_names[0]

    volume_name = str(desired_nfs.get("volume") or nfs_cfg.get("datastore_name") or "esxi_datastore_01").strip()
    datastore_name = str((((cfg.get("vmware") or {}).get("nfs") or {}).get("datastore_name") or volume_name)).strip()
    export_path = str(desired_nfs.get("mount_path") or f"/{volume_name}").strip()
    export_policy = str(nfs_cfg.get("export_policy") or desired_nfs.get("export_policy") or "").strip()
    allowed_subnet = str(nfs_cfg.get("allowed_subnet") or desired_nfs.get("allowed_subnet") or "").strip()
    mount_targets = [str(item).strip() for item in list(desired_nfs.get("esxi_mount_targets") or []) if str(item).strip()]

    return {
        "svm_name": svm_name,
        "lif_ips": lif_ips,
        "volume_name": volume_name,
        "datastore_name": datastore_name,
        "export_path": export_path,
        "export_policy": export_policy,
        "allowed_subnet": allowed_subnet,
        "mount_targets": mount_targets,
    }


def _build_nfs_mount_plan(esxi_hosts: list[str], server_ips: list[str], *, export_path: str, datastore_name: str, nfs_version: str) -> list[dict[str, Any]]:
    if not esxi_hosts or not server_ips:
        return []
    mount_plan: list[dict[str, Any]] = []
    total_servers = len(server_ips)
    for index, host in enumerate(esxi_hosts):
        primary = server_ips[index % total_servers]
        alternates = [item for item in server_ips if item != primary]
        nfs41_hosts = "'" + ",".join([primary] + alternates).replace("'", "'\"'\"'") + "'"
        mount_plan.append(
            {
                "host": host,
                "server": primary,
                "alternate_servers": alternates,
                "export_path": export_path,
                "datastore_name": datastore_name,
                "nfs_version": nfs_version,
                "esxcli_fallback_command": (
                    f"esxcli storage nfs add -H {shlex.quote(primary)} "
                    f"-s {shlex.quote(export_path)} -v {shlex.quote(datastore_name)}"
                    if str(nfs_version or "").strip() == "4.1"
                    else ""
                ),
                "esxcli_command": (
                    f"esxcli storage nfs41 add -H {nfs41_hosts} "
                    f"-s {shlex.quote(export_path)} -v {shlex.quote(datastore_name)}"
                    if str(nfs_version or "").strip() == "4.1"
                    else f"esxcli storage nfs add -H {shlex.quote(primary)} "
                    f"-s {shlex.quote(export_path)} -v {shlex.quote(datastore_name)}"
                ),
                "powercli_command": (
                    f"New-Datastore -Nfs -VMHost {host} -Name {datastore_name} "
                    f"-Path {export_path} -NfsHost {primary} -NfsVersion {nfs_version}"
                ),
            }
        )
    return mount_plan


def _resolved_esxi_targets(cfg: dict[str, Any], config: VMwareConfig, subnet_cidr: str) -> list[str]:
    vmware_cfg = cfg.get("vmware") or {}
    direct_host = str(((cfg.get("esxi") or {}).get("management_ip") or "")).strip()
    discovered_hosts = [str(item).strip() for item in list(vmware_cfg.get("discovered_host_ips") or []) if str(item).strip()]
    if not str(config.vcenter_ip or "").strip():
        if direct_host:
            return [direct_host]
        if discovered_hosts:
            return discovered_hosts
    return build_esxi_host_range(subnet_cidr, config.esxi_host_start_offset, config.esxi_host_end_offset)


def build_vmware_plan(cfg: dict[str, Any], *, storage_protocol: str, discovery: dict[str, Any] | None = None) -> dict[str, Any]:
    vmware_cfg = cfg.get("vmware") or {}
    esxi_cfg = cfg.get("esxi") or {}
    subnet = str(((cfg.get("shared_network") or {}).get("subnet") or (cfg.get("ip_plan") or {}).get("subnet") or "10.10.8.0/24")).strip()
    site_name = str(((cfg.get("site") or {}).get("name") or "Kit-01")).strip()
    config = VMwareConfig(
        vcenter_ip=str(vmware_cfg.get("vcenter_ip") or "").strip(),
        username=str(vmware_cfg.get("username") or "vsphere.local\\administrator").strip(),
        password=str(vmware_cfg.get("password") or ""),
        datacenter_name=str(vmware_cfg.get("datacenter_name") or site_name).strip(),
        cluster_name=str(vmware_cfg.get("cluster_name") or f"{site_name}-Cluster").strip(),
        esxi_host_start_offset=int(vmware_cfg.get("esxi_host_start_offset") or 31),
        esxi_host_end_offset=int(vmware_cfg.get("esxi_host_end_offset") or 39),
        esxi_root_user=str(vmware_cfg.get("esxi_root_user") or "root").strip(),
        esxi_root_password=str(vmware_cfg.get("esxi_root_password") or esxi_cfg.get("root_password") or ""),
        vcenter_vm_name_match=str(vmware_cfg.get("vcenter_vm_name_match") or "SVCNTR").strip(),
        ha_enabled=bool(vmware_cfg.get("ha_enabled", True)),
        ha_isolation_response=str(vmware_cfg.get("ha_isolation_response") or "Shutdown").strip(),
        drs_enabled=bool(vmware_cfg.get("drs_enabled", False)),
        startup_policy_enabled=bool(vmware_cfg.get("startup_policy_enabled", True)),
    )
    esxi_hosts = _resolved_esxi_targets(cfg, config, subnet)
    nfs_context = _discovered_netapp_nfs_context(cfg, discovery)
    nfs_version = str((((vmware_cfg.get("nfs") or {}).get("nfs_version")) or "4.1")).strip()
    connection_mode = "vcenter" if str(config.vcenter_ip or "").strip() else "standalone_esxi"
    steps = [
        {"name": "ensure_datacenter", "status": "create" if connection_mode == "vcenter" else "skip", "details": {"datacenter": config.datacenter_name}},
        {"name": "ensure_cluster", "status": "create" if connection_mode == "vcenter" else "skip", "details": {"cluster": config.cluster_name}},
        {"name": "ensure_ha_policy", "status": "update" if connection_mode == "vcenter" else "skip", "details": {"enabled": config.ha_enabled, "isolation_response": config.ha_isolation_response}},
        {"name": "ensure_drs_policy", "status": "update" if connection_mode == "vcenter" else "skip", "details": {"enabled": config.drs_enabled}},
        {"name": "discover_esxi_hosts", "status": "ok", "details": {"hosts": esxi_hosts}},
        {"name": "ensure_cluster_membership", "status": "manual" if connection_mode == "vcenter" else "skip", "details": {"hosts": esxi_hosts}},
        {"name": "ensure_vmware_role", "status": "manual" if connection_mode == "vcenter" else "skip", "details": {"role_name": f"{site_name}VirtualManagers"}},
        {"name": "ensure_startup_policy", "status": "update" if connection_mode == "vcenter" else "skip", "details": {"enabled": config.startup_policy_enabled, "vm_name_match": config.vcenter_vm_name_match}},
    ]
    if str(storage_protocol or "").strip().lower() == "iscsi":
        steps.extend(
            [
                {"name": "rescan_iscsi_hbas", "status": "manual", "details": {"hosts": esxi_hosts}},
                {"name": "rescan_vmfs", "status": "manual", "details": {"hosts": esxi_hosts}},
                {"name": "set_round_robin", "status": "manual", "details": {"policy": str((((vmware_cfg.get('iscsi') or {}).get('multipath_policy')) or 'RoundRobin')).strip()}},
                {"name": "plan_vmfs_datastores", "status": "manual", "details": {}},
            ]
        )
    else:
        mount_sources = list(nfs_context.get("mount_targets") or []) or list(nfs_context.get("lif_ips") or [])
        mount_plan = _build_nfs_mount_plan(
            esxi_hosts,
            mount_sources,
            export_path=str(nfs_context.get("export_path") or "").strip(),
            datastore_name=str(nfs_context.get("datastore_name") or "").strip(),
            nfs_version=nfs_version,
        )
        mount_ready = bool(
            mount_plan
            and nfs_context.get("export_path")
            and (
                (connection_mode == "vcenter" and config.vcenter_ip and config.password)
                or (connection_mode == "standalone_esxi" and config.esxi_root_user and config.esxi_root_password)
            )
        )
        steps.extend(
            [
                {
                    "name": "validate_nfs_mount_inputs",
                    "status": "ok" if mount_ready else "blocked",
                    "details": {
                        "connection_mode": connection_mode,
                        "vcenter_ip": config.vcenter_ip,
                        "esxi_root_user": config.esxi_root_user,
                        "has_esxi_root_password": bool(config.esxi_root_password),
                        "has_password": bool(config.password),
                        "datastore_name": nfs_context.get("datastore_name") or "",
                        "svm_name": nfs_context.get("svm_name") or "",
                        "export_path": nfs_context.get("export_path") or "",
                        "server_ips": list(nfs_context.get("lif_ips") or []),
                        "esxi_hosts": esxi_hosts,
                    },
                },
                {
                    "name": "plan_nfs_datastore_mounts",
                    "status": "create" if connection_mode == "standalone_esxi" and mount_ready else "manual",
                    "details": {
                        "hosts": esxi_hosts,
                        "nfs_version": nfs_version,
                        "datastore_name": nfs_context.get("datastore_name") or "",
                        "svm_name": nfs_context.get("svm_name") or "",
                        "export_path": nfs_context.get("export_path") or "",
                        "server_ips": list(nfs_context.get("lif_ips") or []),
                        "mount_plan": mount_plan,
                    },
                },
                {
                    "name": "validate_nfs_accessibility",
                    "status": "manual",
                    "details": {
                        "hosts": esxi_hosts,
                        "server_ips": list(nfs_context.get("lif_ips") or []),
                        "export_path": nfs_context.get("export_path") or "",
                    },
                },
            ]
        )
    warnings: list[str] = []
    if connection_mode == "vcenter":
        if not config.vcenter_ip:
            warnings.append("vCenter IP is not configured.")
        if not config.password:
            warnings.append("vCenter password is not configured.")
    else:
        if not esxi_hosts:
            warnings.append("No standalone ESXi host target is configured.")
        if not config.esxi_root_password:
            warnings.append("Standalone ESXi root password is not configured.")
    if str(storage_protocol or "").strip().lower() == "nfs":
        if not nfs_context.get("lif_ips"):
            warnings.append("No NFS LIF IPs are available for VMware datastore mount planning.")
        if not nfs_context.get("export_path"):
            warnings.append("NFS export path is not configured.")
    return {
        "scope": "vmware",
        "dry_run_only": True,
        "connection_mode": connection_mode,
        "config": config.__dict__,
        "esxi_hosts": esxi_hosts,
        "nfs_context": nfs_context,
        "steps": steps,
        "warnings": warnings,
    }
