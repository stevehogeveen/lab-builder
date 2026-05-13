from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.modules.netapp.schemas import NetAppModuleContext
from app.modules.netapp.service import NetAppModuleService
from app.plan_renderer import build_token_map, render_command_preview, write_plan_artifacts
from app.storage_profiles import build_protocol_profile
from app.vmware import build_vmware_plan

router = APIRouter()

MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
template_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

service = NetAppModuleService()


def _lines(value: str) -> list[str]:
    return [line.strip() for line in str(value or "").replace(",", "\n").splitlines() if line.strip()]


def _parse_lifs(value: str) -> list[dict[str, str]]:
    lifs: list[dict[str, str]] = []
    for line in _lines(value):
        parts = [part.strip() for part in line.split(",")]
        if not parts or not parts[0]:
            continue
        while len(parts) < 4:
            parts.append("")
        lifs.append({"name": parts[0], "ip": parts[1], "node": parts[2], "port": parts[3]})
    return lifs


def _parse_iscsi_volumes(value: str) -> list[dict[str, str]]:
    volumes: list[dict[str, str]] = []
    for line in _lines(value):
        parts = [part.strip() for part in line.split(",")]
        if not parts or not parts[0]:
            continue
        while len(parts) < 6:
            parts.append("")
        volumes.append(
            {
                "volume_name": parts[0],
                "lun_name": parts[1] or parts[0],
                "aggregate": parts[2],
                "lun_id": parts[3],
                "lun_size": parts[4],
                "description": parts[5],
            }
        )
    return volumes


def _render_netapp_page(request: Request, context: dict[str, Any], payload: dict[str, Any], *, saved: bool = False):
    from app import main

    plan = payload.get("plan") or {}
    profile = plan.get("protocol_profile") or {}
    settings = service.settings_context(context)
    return main.render_page(
        request,
        context["cfg"],
        active_page="netapp",
        action_feedback=main.build_action_feedback(
            "NetApp settings saved" if saved else "NetApp dry-run review",
            "Saved NetApp desired settings and rebuilt the dry-run plan." if saved else "Read-only discovery and validation are loaded for NetApp base workflow and selected storage profile.",
            tone="ready" if payload.get("ok") or saved else "pending",
            status_label="Ready" if payload.get("ok") else "Needs attention",
            outcomes=[
                f"Protocol profile: {str(profile.get('selected_protocol') or 'nfs').upper()}",
                f"Cluster: {str((plan.get('adaptive_discovery') or {}).get('cluster_name') or (payload.get('discovery') or {}).get('cluster_name') or '(unknown)')}",
            ],
            details=([str(payload.get("error"))] if payload.get("error") else []) + list(payload.get("warnings") or []),
        ),
        extra_context={"netapp_payload": payload, "netapp_settings": settings, "netapp_export_paths": payload.get("export_paths") or {}},
    )


def _set_bootstrap_check(cfg: dict[str, Any], key: str, result: dict[str, Any]) -> None:
    cfg.setdefault("netapp", {})
    cfg["netapp"].setdefault("bootstrap_checks", {})
    cfg["netapp"]["bootstrap_checks"][key] = result


def _set_vmware_check(cfg: dict[str, Any], key: str, result: dict[str, Any]) -> None:
    cfg.setdefault("netapp", {})
    cfg["netapp"].setdefault("vmware_checks", {})
    cfg["netapp"]["vmware_checks"][key] = result


def _sync_discovered_values(cfg: dict[str, Any], discovery: dict[str, Any]) -> list[str]:
    cfg.setdefault("netapp", {})
    netapp_cfg = cfg["netapp"]
    management = netapp_cfg.setdefault("management", {})
    bootstrap_overrides = netapp_cfg.setdefault("bootstrap_overrides", {})
    desired = netapp_cfg.setdefault("desired", {})
    notes: list[str] = []

    cluster_mgmt_ip = str(discovery.get("discovered_cluster_mgmt_ip") or "").strip()
    if cluster_mgmt_ip:
        netapp_cfg["host"] = cluster_mgmt_ip
        management["cluster_mgmt_ip"] = cluster_mgmt_ip
        bootstrap_overrides["netapp_cluster_mgmt"] = cluster_mgmt_ip
        notes.append(f"ONTAP API target set to {cluster_mgmt_ip}.")

    node_mgmt_ips = dict(discovery.get("discovered_node_mgmt_ips") or {})
    node_names = list(discovery.get("node_names") or discovery.get("nodes") or [])
    if node_names:
        if len(node_names) >= 1:
            node_01_ip = str(node_mgmt_ips.get(str(node_names[0])) or "").strip()
            if node_01_ip:
                management["node_01_mgmt_ip"] = node_01_ip
                bootstrap_overrides["netapp_node_01_mgmt"] = node_01_ip
                notes.append(f"Node 1 management set to {node_01_ip}.")
        if len(node_names) >= 2:
            node_02_ip = str(node_mgmt_ips.get(str(node_names[1])) or "").strip()
            if node_02_ip:
                management["node_02_mgmt_ip"] = node_02_ip
                bootstrap_overrides["netapp_node_02_mgmt"] = node_02_ip
                notes.append(f"Node 2 management set to {node_02_ip}.")

    cluster_name = str(discovery.get("cluster_name") or "").strip()
    if cluster_name:
        netapp_cfg["cluster_name"] = cluster_name
        desired["cluster_name"] = cluster_name
        notes.append(f"Cluster name set to {cluster_name}.")

    discovered_nfs_lifs = [dict(item) for item in list(discovery.get("discovered_nfs_lifs") or []) if isinstance(item, dict)]
    if discovered_nfs_lifs:
        netapp_cfg.setdefault("nfs", {})
        netapp_cfg["nfs"]["lifs"] = discovered_nfs_lifs
        desired.setdefault("nfs", {})
        desired["nfs"]["lifs"] = discovered_nfs_lifs
        notes.append(f"Captured {len(discovered_nfs_lifs)} discovered NFS LIFs.")

    return notes


def _apply_current_netapp_convention(cfg: dict[str, Any]) -> list[str]:
    from app.core.config import ip_at_offset

    shared = cfg.setdefault("shared_network", {})
    subnet = str(shared.get("subnet") or "10.10.8.0/24").strip()
    netapp_cfg = cfg.setdefault("netapp", {})
    management = netapp_cfg.setdefault("management", {})
    desired = netapp_cfg.setdefault("desired", {})
    overrides = dict(netapp_cfg.get("bootstrap_overrides") or {})

    current_values = {
        "netapp_sp_a": ip_at_offset(subnet, int(shared.get("netapp_sp_a_offset", 13) or 13)),
        "netapp_sp_b": ip_at_offset(subnet, int(shared.get("netapp_sp_b_offset", 14) or 14)),
        "netapp_cluster_mgmt": ip_at_offset(subnet, int(shared.get("netapp_cluster_mgmt_offset", 45) or 45)),
        "netapp_node_01_mgmt": ip_at_offset(subnet, int(shared.get("netapp_node_01_mgmt_offset", 46) or 46)),
        "netapp_node_02_mgmt": ip_at_offset(subnet, int(shared.get("netapp_node_02_mgmt_offset", 47) or 47)),
        "netapp_svm_mgmt": ip_at_offset(subnet, int(shared.get("netapp_svm_mgmt_offset", 48) or 48)),
    }
    for key, value in current_values.items():
        overrides[key] = value
    netapp_cfg["bootstrap_overrides"] = overrides
    netapp_cfg["host"] = current_values["netapp_cluster_mgmt"]
    management.update(
        {
            "cluster_mgmt_ip": current_values["netapp_cluster_mgmt"],
            "node_01_mgmt_ip": current_values["netapp_node_01_mgmt"],
            "node_02_mgmt_ip": current_values["netapp_node_02_mgmt"],
            "svm_mgmt_ip": current_values["netapp_svm_mgmt"],
        }
    )
    desired["svm_mgmt_ip"] = current_values["netapp_svm_mgmt"]
    return [
        f"ONTAP API target updated to {current_values['netapp_cluster_mgmt']}.",
        f"Node management updated to {current_values['netapp_node_01_mgmt']} and {current_values['netapp_node_02_mgmt']}.",
        f"SVM management updated to {current_values['netapp_svm_mgmt']}.",
    ]


def _apply_live_netapp_form_state(cfg: dict[str, Any], form: dict[str, Any]) -> None:
    cfg.setdefault("netapp", {})
    cfg["netapp"]["host"] = str(form.get("netapp_host") or cfg["netapp"].get("host") or "").strip()
    cfg["netapp"]["username"] = str(form.get("netapp_username") or cfg["netapp"].get("username") or "admin").strip()
    password = str(form.get("netapp_password") or "")
    if password:
        cfg["netapp"]["password"] = password
    protocol = str(form.get("netapp_storage_protocol") or cfg["netapp"].get("storage_protocol") or "nfs").strip().lower()
    cfg["netapp"]["storage_protocol"] = protocol if protocol in {"iscsi", "nfs"} else "nfs"
    cfg["netapp"]["bootstrap_overrides"] = {
        "netapp_sp_a": str(form.get("netapp_sp_a_ip") or ((cfg["netapp"].get("bootstrap_overrides") or {}).get("netapp_sp_a")) or "").strip(),
        "netapp_sp_b": str(form.get("netapp_sp_b_ip") or ((cfg["netapp"].get("bootstrap_overrides") or {}).get("netapp_sp_b")) or "").strip(),
        "netapp_cluster_mgmt": str(form.get("netapp_cluster_mgmt_ip") or ((cfg["netapp"].get("bootstrap_overrides") or {}).get("netapp_cluster_mgmt")) or "").strip(),
        "netapp_node_01_mgmt": str(form.get("netapp_node_01_mgmt_ip") or ((cfg["netapp"].get("bootstrap_overrides") or {}).get("netapp_node_01_mgmt")) or "").strip(),
        "netapp_node_02_mgmt": str(form.get("netapp_node_02_mgmt_ip") or ((cfg["netapp"].get("bootstrap_overrides") or {}).get("netapp_node_02_mgmt")) or "").strip(),
        "netapp_svm_mgmt": str(form.get("netapp_svm_mgmt_ip") or ((cfg["netapp"].get("bootstrap_overrides") or {}).get("netapp_svm_mgmt")) or "").strip(),
    }


def _apply_settings_to_cfg(
    cfg: dict[str, Any],
    *,
    netapp_host: str,
    netapp_username: str,
    netapp_password: str,
    netapp_storage_protocol: str,
    bootstrap_complete: bool,
    netapp_sp_a_ip: str,
    netapp_sp_b_ip: str,
    netapp_cluster_mgmt_ip: str,
    netapp_node_01_mgmt_ip: str,
    netapp_node_02_mgmt_ip: str,
    netapp_svm_mgmt_ip: str,
    cluster_name: str,
    svm_name: str,
    required_nodes: str,
    expected_ports: str,
    data_broadcast_domain: str,
    target_mtu: str,
    aggregate_node_01: str,
    aggregate_node_02: str,
    aggregate_diskcount: str,
    aggregate_raidtype: str,
    svm_mgmt_lif: str,
    svm_mgmt_ip: str,
    management_subnet: str,
    management_gateway: str,
    management_netmask: str,
    autosupport_from: str,
    autosupport_to: str,
    autosupport_mail_hosts: str,
    ntp_servers: str,
    required_users: str,
    esxi_hosts: str,
    iscsi_subnet: str,
    iscsi_subnet_cidr: str,
    iscsi_gateway: str,
    iscsi_ip_range: str,
    iscsi_iqns: str,
    iscsi_lifs: str,
    iscsi_volumes: str,
    iscsi_portset: str,
    iscsi_igroup: str,
    iscsi_lun: str,
    iscsi_vmfs_datastore: str,
    nfs_volume: str,
    nfs_export_policy: str,
    nfs_mount_path: str,
    nfs_esxi_mount_targets: str,
    nfs_lifs: str,
    netapp_iscsi_commands: str,
    netapp_nfs_commands: str,
) -> None:
    existing_password = str(((cfg.get("netapp") or {}).get("password") or ""))
    existing_desired = dict((((cfg.get("netapp") or {}).get("desired")) or {}))
    existing_iscsi = dict((existing_desired.get("iscsi") or {}))
    existing_nfs = dict((existing_desired.get("nfs") or {}))
    try:
        mtu_value = int(str(target_mtu or "9000").strip())
    except ValueError:
        mtu_value = 9000
    try:
        diskcount_value = int(str(aggregate_diskcount or "11").strip())
    except ValueError:
        diskcount_value = 11
    protocol = str(netapp_storage_protocol or "nfs").strip().lower()
    if protocol not in {"iscsi", "nfs"}:
        protocol = "nfs"
    cfg.setdefault("netapp", {})
    cfg["netapp"]["bootstrap_overrides"] = {
        "netapp_sp_a": netapp_sp_a_ip.strip(),
        "netapp_sp_b": netapp_sp_b_ip.strip(),
        "netapp_cluster_mgmt": netapp_cluster_mgmt_ip.strip(),
        "netapp_node_01_mgmt": netapp_node_01_mgmt_ip.strip(),
        "netapp_node_02_mgmt": netapp_node_02_mgmt_ip.strip(),
        "netapp_svm_mgmt": netapp_svm_mgmt_ip.strip(),
    }
    cfg["netapp"].update(
        {
            "host": netapp_host.strip(),
            "username": netapp_username.strip(),
            "password": netapp_password if netapp_password else existing_password,
            "storage_protocol": protocol,
            "bootstrap_complete": bool(bootstrap_complete),
            "command_templates": {
                "iscsi": netapp_iscsi_commands if netapp_iscsi_commands else str((((cfg.get("netapp") or {}).get("command_templates") or {}).get("iscsi")) or ""),
                "nfs": netapp_nfs_commands if netapp_nfs_commands else str((((cfg.get("netapp") or {}).get("command_templates") or {}).get("nfs")) or ""),
            },
            "desired": {
                "cluster_name": cluster_name.strip() or str(existing_desired.get("cluster_name") or ""),
                "svm_name": svm_name.strip() or str(existing_desired.get("svm_name") or ""),
                "required_nodes": _lines(required_nodes) or list(existing_desired.get("required_nodes") or []),
                "expected_ports": _lines(expected_ports) or list(existing_desired.get("expected_ports") or []),
                "data_broadcast_domain": data_broadcast_domain.strip() or str(existing_desired.get("data_broadcast_domain") or "Data"),
                "target_mtu": mtu_value,
                "aggregate_node_01": aggregate_node_01.strip() or str(existing_desired.get("aggregate_node_01") or "aggr_01"),
                "aggregate_node_02": aggregate_node_02.strip() or str(existing_desired.get("aggregate_node_02") or "aggr_02"),
                "aggregate_diskcount": diskcount_value,
                "aggregate_raidtype": aggregate_raidtype.strip() or "raid_dp",
                "svm_mgmt_lif": svm_mgmt_lif.strip() or str(existing_desired.get("svm_mgmt_lif") or ""),
                "svm_mgmt_ip": svm_mgmt_ip.strip() or str(existing_desired.get("svm_mgmt_ip") or ""),
                "management_subnet": management_subnet.strip() or str(existing_desired.get("management_subnet") or ""),
                "management_gateway": management_gateway.strip() or str(existing_desired.get("management_gateway") or ""),
                "management_netmask": management_netmask.strip() or str(existing_desired.get("management_netmask") or "255.255.255.0"),
                "autosupport_from": autosupport_from.strip() or str(existing_desired.get("autosupport_from") or ""),
                "autosupport_to": autosupport_to.strip() or str(existing_desired.get("autosupport_to") or ""),
                "autosupport_mail_hosts": _lines(autosupport_mail_hosts) or list(existing_desired.get("autosupport_mail_hosts") or []),
                "ntp_servers": _lines(ntp_servers) or list(existing_desired.get("ntp_servers") or []),
                "required_users": _lines(required_users) or list(existing_desired.get("required_users") or []),
                "esxi_hosts": _lines(esxi_hosts) or list(existing_desired.get("esxi_hosts") or []),
                "iscsi": {
                    "subnet": iscsi_subnet.strip() or str(existing_iscsi.get("subnet") or "192.168.1.0/24"),
                    "subnet_cidr": iscsi_subnet_cidr.strip() or str(existing_iscsi.get("subnet_cidr") or ""),
                    "gateway": iscsi_gateway.strip() or str(existing_iscsi.get("gateway") or ""),
                    "ip_range": iscsi_ip_range.strip() or str(existing_iscsi.get("ip_range") or ""),
                    "lifs": _parse_lifs(iscsi_lifs) or list(existing_iscsi.get("lifs") or []),
                    "volumes": _parse_iscsi_volumes(iscsi_volumes) or list(existing_iscsi.get("volumes") or []),
                    "portset": iscsi_portset.strip() or str(existing_iscsi.get("portset") or "iSCSI"),
                    "igroup": iscsi_igroup.strip() or str(existing_iscsi.get("igroup") or ""),
                    "lun": iscsi_lun.strip() or str(existing_iscsi.get("lun") or ""),
                    "vmfs_datastore": iscsi_vmfs_datastore.strip() or str(existing_iscsi.get("vmfs_datastore") or ""),
                    "iqns": _lines(iscsi_iqns) or list(existing_iscsi.get("iqns") or []),
                },
                "nfs": {
                    "volume": nfs_volume.strip() or str(existing_nfs.get("volume") or ""),
                    "export_policy": nfs_export_policy.strip() or str(existing_nfs.get("export_policy") or ""),
                    "mount_path": nfs_mount_path.strip() or str(existing_nfs.get("mount_path") or ""),
                    "esxi_mount_targets": _lines(nfs_esxi_mount_targets) or list(existing_nfs.get("esxi_mount_targets") or []),
                    "lifs": _parse_lifs(nfs_lifs) or list(existing_nfs.get("lifs") or []),
                },
            },
        }
    )


def _module_context(request: Request) -> dict[str, Any]:
    # Import from app at call time to avoid startup cycles.
    from app import main

    cfg = main.load_kit_config()
    return NetAppModuleContext(
        module_name="netapp",
        payload={"path": str(request.url.path), "query": str(request.url.query), "method": request.method},
        cfg=cfg,
    ).model_dump()


def _render_template(template_name: str, context: dict[str, Any]) -> str:
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        return (
            '<section class="panel space-y-6">\n'
            "    <h1>NetApp module</h1>\n"
            "    <p>NetApp preview template is not available.</p>\n"
            "</section>\n"
        )
    template = template_env.get_template(template_name)
    return template.render(**context)


@router.get("/modules/netapp", response_class=HTMLResponse)
async def netapp_module_page(request: Request):
    context = _module_context(request)
    payload = service.plan(context)
    return _render_netapp_page(request, context, payload)


@router.post("/modules/netapp/save-settings", response_class=HTMLResponse)
async def netapp_save_settings(
    request: Request,
    netapp_host: str = Form(""),
    netapp_username: str = Form("admin"),
    netapp_password: str = Form(""),
    netapp_storage_protocol: str = Form("nfs"),
    bootstrap_complete: bool = Form(False),
    netapp_sp_a_ip: str = Form(""),
    netapp_sp_b_ip: str = Form(""),
    netapp_cluster_mgmt_ip: str = Form(""),
    netapp_node_01_mgmt_ip: str = Form(""),
    netapp_node_02_mgmt_ip: str = Form(""),
    netapp_svm_mgmt_ip: str = Form(""),
    cluster_name: str = Form(""),
    svm_name: str = Form(""),
    required_nodes: str = Form(""),
    expected_ports: str = Form(""),
    data_broadcast_domain: str = Form("Data"),
    target_mtu: str = Form("9000"),
    aggregate_node_01: str = Form("aggr_01"),
    aggregate_node_02: str = Form("aggr_02"),
    aggregate_diskcount: str = Form("11"),
    aggregate_raidtype: str = Form("raid_dp"),
    svm_mgmt_lif: str = Form(""),
    svm_mgmt_ip: str = Form(""),
    management_subnet: str = Form(""),
    management_gateway: str = Form(""),
    management_netmask: str = Form("255.255.255.0"),
    autosupport_from: str = Form(""),
    autosupport_to: str = Form(""),
    autosupport_mail_hosts: str = Form(""),
    ntp_servers: str = Form(""),
    required_users: str = Form(""),
    esxi_hosts: str = Form(""),
    iscsi_subnet: str = Form("192.168.1.0/24"),
    iscsi_subnet_cidr: str = Form("192.168.1.0/24"),
    iscsi_gateway: str = Form("192.168.1.1"),
    iscsi_ip_range: str = Form("192.168.1.11-192.168.1.60"),
    iscsi_iqns: str = Form(""),
    iscsi_lifs: str = Form(""),
    iscsi_volumes: str = Form(""),
    iscsi_portset: str = Form("iSCSI"),
    iscsi_igroup: str = Form(""),
    iscsi_lun: str = Form("esxi_lun01"),
    iscsi_vmfs_datastore: str = Form("vmfs_ds01"),
    nfs_volume: str = Form("esxi_datastore_01"),
    nfs_export_policy: str = Form("esxi_nfs_policy"),
    nfs_mount_path: str = Form("/esxi_datastore_01"),
    nfs_esxi_mount_targets: str = Form(""),
    nfs_lifs: str = Form(""),
    netapp_iscsi_commands: str = Form(""),
    netapp_nfs_commands: str = Form(""),
):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    _apply_settings_to_cfg(
        cfg,
        netapp_host=netapp_host,
        netapp_username=netapp_username,
        netapp_password=netapp_password,
        netapp_storage_protocol=netapp_storage_protocol,
        bootstrap_complete=bootstrap_complete,
        netapp_sp_a_ip=netapp_sp_a_ip,
        netapp_sp_b_ip=netapp_sp_b_ip,
        netapp_cluster_mgmt_ip=netapp_cluster_mgmt_ip,
        netapp_node_01_mgmt_ip=netapp_node_01_mgmt_ip,
        netapp_node_02_mgmt_ip=netapp_node_02_mgmt_ip,
        netapp_svm_mgmt_ip=netapp_svm_mgmt_ip,
        cluster_name=cluster_name,
        svm_name=svm_name,
        required_nodes=required_nodes,
        expected_ports=expected_ports,
        data_broadcast_domain=data_broadcast_domain,
        target_mtu=target_mtu,
        aggregate_node_01=aggregate_node_01,
        aggregate_node_02=aggregate_node_02,
        aggregate_diskcount=aggregate_diskcount,
        aggregate_raidtype=aggregate_raidtype,
        svm_mgmt_lif=svm_mgmt_lif,
        svm_mgmt_ip=svm_mgmt_ip,
        management_subnet=management_subnet,
        management_gateway=management_gateway,
        management_netmask=management_netmask,
        autosupport_from=autosupport_from,
        autosupport_to=autosupport_to,
        autosupport_mail_hosts=autosupport_mail_hosts,
        ntp_servers=ntp_servers,
        required_users=required_users,
        esxi_hosts=esxi_hosts,
        iscsi_subnet=iscsi_subnet,
        iscsi_subnet_cidr=iscsi_subnet_cidr,
        iscsi_gateway=iscsi_gateway,
        iscsi_ip_range=iscsi_ip_range,
        iscsi_iqns=iscsi_iqns,
        iscsi_lifs=iscsi_lifs,
        iscsi_volumes=iscsi_volumes,
        iscsi_portset=iscsi_portset,
        iscsi_igroup=iscsi_igroup,
        iscsi_lun=iscsi_lun,
        iscsi_vmfs_datastore=iscsi_vmfs_datastore,
        nfs_volume=nfs_volume,
        nfs_export_policy=nfs_export_policy,
        nfs_mount_path=nfs_mount_path,
        nfs_esxi_mount_targets=nfs_esxi_mount_targets,
        nfs_lifs=nfs_lifs,
        netapp_iscsi_commands=netapp_iscsi_commands,
        netapp_nfs_commands=netapp_nfs_commands,
    )
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.plan(context)
    return _render_netapp_page(request, context, payload, saved=True)


@router.post("/modules/netapp/test-connection", response_class=HTMLResponse)
async def netapp_test_connection(
    request: Request,
    netapp_host: str = Form(""),
    netapp_username: str = Form("admin"),
    netapp_password: str = Form(""),
    netapp_storage_protocol: str = Form("nfs"),
    bootstrap_complete: bool = Form(False),
    netapp_sp_a_ip: str = Form(""),
    netapp_sp_b_ip: str = Form(""),
    netapp_cluster_mgmt_ip: str = Form(""),
    netapp_node_01_mgmt_ip: str = Form(""),
    netapp_node_02_mgmt_ip: str = Form(""),
    netapp_svm_mgmt_ip: str = Form(""),
    cluster_name: str = Form(""),
    svm_name: str = Form(""),
    required_nodes: str = Form(""),
    expected_ports: str = Form(""),
    data_broadcast_domain: str = Form("Data"),
    target_mtu: str = Form("9000"),
    aggregate_node_01: str = Form("aggr_01"),
    aggregate_node_02: str = Form("aggr_02"),
    aggregate_diskcount: str = Form("11"),
    aggregate_raidtype: str = Form("raid_dp"),
    svm_mgmt_lif: str = Form(""),
    svm_mgmt_ip: str = Form(""),
    management_subnet: str = Form(""),
    management_gateway: str = Form(""),
    management_netmask: str = Form("255.255.255.0"),
    autosupport_from: str = Form(""),
    autosupport_to: str = Form(""),
    autosupport_mail_hosts: str = Form(""),
    ntp_servers: str = Form(""),
    required_users: str = Form(""),
    esxi_hosts: str = Form(""),
    iscsi_subnet: str = Form("192.168.1.0/24"),
    iscsi_subnet_cidr: str = Form("192.168.1.0/24"),
    iscsi_gateway: str = Form("192.168.1.1"),
    iscsi_ip_range: str = Form("192.168.1.11-192.168.1.60"),
    iscsi_iqns: str = Form(""),
    iscsi_lifs: str = Form(""),
    iscsi_volumes: str = Form(""),
    iscsi_portset: str = Form("iSCSI"),
    iscsi_igroup: str = Form(""),
    iscsi_lun: str = Form("esxi_lun01"),
    iscsi_vmfs_datastore: str = Form("vmfs_ds01"),
    nfs_volume: str = Form("esxi_datastore_01"),
    nfs_export_policy: str = Form("esxi_nfs_policy"),
    nfs_mount_path: str = Form("/esxi_datastore_01"),
    nfs_esxi_mount_targets: str = Form(""),
    nfs_lifs: str = Form(""),
    netapp_iscsi_commands: str = Form(""),
    netapp_nfs_commands: str = Form(""),
):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    _apply_settings_to_cfg(
        cfg,
        netapp_host=netapp_host,
        netapp_username=netapp_username,
        netapp_password=netapp_password,
        netapp_storage_protocol=netapp_storage_protocol,
        bootstrap_complete=bootstrap_complete,
        netapp_sp_a_ip=netapp_sp_a_ip,
        netapp_sp_b_ip=netapp_sp_b_ip,
        netapp_cluster_mgmt_ip=netapp_cluster_mgmt_ip,
        netapp_node_01_mgmt_ip=netapp_node_01_mgmt_ip,
        netapp_node_02_mgmt_ip=netapp_node_02_mgmt_ip,
        netapp_svm_mgmt_ip=netapp_svm_mgmt_ip,
        cluster_name=cluster_name,
        svm_name=svm_name,
        required_nodes=required_nodes,
        expected_ports=expected_ports,
        data_broadcast_domain=data_broadcast_domain,
        target_mtu=target_mtu,
        aggregate_node_01=aggregate_node_01,
        aggregate_node_02=aggregate_node_02,
        aggregate_diskcount=aggregate_diskcount,
        aggregate_raidtype=aggregate_raidtype,
        svm_mgmt_lif=svm_mgmt_lif,
        svm_mgmt_ip=svm_mgmt_ip,
        management_subnet=management_subnet,
        management_gateway=management_gateway,
        management_netmask=management_netmask,
        autosupport_from=autosupport_from,
        autosupport_to=autosupport_to,
        autosupport_mail_hosts=autosupport_mail_hosts,
        ntp_servers=ntp_servers,
        required_users=required_users,
        esxi_hosts=esxi_hosts,
        iscsi_subnet=iscsi_subnet,
        iscsi_subnet_cidr=iscsi_subnet_cidr,
        iscsi_gateway=iscsi_gateway,
        iscsi_ip_range=iscsi_ip_range,
        iscsi_iqns=iscsi_iqns,
        iscsi_lifs=iscsi_lifs,
        iscsi_volumes=iscsi_volumes,
        iscsi_portset=iscsi_portset,
        iscsi_igroup=iscsi_igroup,
        iscsi_lun=iscsi_lun,
        iscsi_vmfs_datastore=iscsi_vmfs_datastore,
        nfs_volume=nfs_volume,
        nfs_export_policy=nfs_export_policy,
        nfs_mount_path=nfs_mount_path,
        nfs_esxi_mount_targets=nfs_esxi_mount_targets,
        nfs_lifs=nfs_lifs,
        netapp_iscsi_commands=netapp_iscsi_commands,
        netapp_nfs_commands=netapp_nfs_commands,
    )
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.test_connection(context)
    return _render_netapp_page(request, context, payload)


@router.post("/modules/netapp/discover-page", response_class=HTMLResponse)
async def netapp_discover_page(
    request: Request,
    netapp_host: str = Form(""),
    netapp_username: str = Form("admin"),
    netapp_password: str = Form(""),
    netapp_storage_protocol: str = Form("nfs"),
):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    bootstrap_overrides = (((cfg.get("netapp") or {}).get("bootstrap_overrides")) or {})
    _apply_settings_to_cfg(
        cfg,
        netapp_host=netapp_host,
        netapp_username=netapp_username,
        netapp_password=netapp_password,
        netapp_storage_protocol=netapp_storage_protocol,
        bootstrap_complete=bool(((cfg.get("netapp") or {}).get("bootstrap_complete"))),
        netapp_sp_a_ip=str(bootstrap_overrides.get("netapp_sp_a") or ""),
        netapp_sp_b_ip=str(bootstrap_overrides.get("netapp_sp_b") or ""),
        netapp_cluster_mgmt_ip=str(bootstrap_overrides.get("netapp_cluster_mgmt") or ""),
        netapp_node_01_mgmt_ip=str(bootstrap_overrides.get("netapp_node_01_mgmt") or ""),
        netapp_node_02_mgmt_ip=str(bootstrap_overrides.get("netapp_node_02_mgmt") or ""),
        netapp_svm_mgmt_ip=str(bootstrap_overrides.get("netapp_svm_mgmt") or ""),
        cluster_name=str((cfg.get("netapp") or {}).get("cluster_name") or ""),
        svm_name=str((cfg.get("netapp") or {}).get("svm_name") or ""),
        required_nodes="",
        expected_ports="",
        data_broadcast_domain=str((cfg.get("netapp") or {}).get("data_broadcast_domain") or "Data"),
        target_mtu=str((cfg.get("netapp") or {}).get("mtu") or 9000),
        aggregate_node_01=str((cfg.get("netapp") or {}).get("aggregate_node_01") or "aggr_01"),
        aggregate_node_02=str((cfg.get("netapp") or {}).get("aggregate_node_02") or "aggr_02"),
        aggregate_diskcount="11",
        aggregate_raidtype="raid_dp",
        svm_mgmt_lif="",
        svm_mgmt_ip="",
        management_subnet="",
        management_gateway="",
        management_netmask="255.255.255.0",
        autosupport_from="",
        autosupport_to="",
        autosupport_mail_hosts="",
        ntp_servers="",
        required_users="",
        esxi_hosts="",
        iscsi_subnet="192.168.1.0/24",
        iscsi_subnet_cidr="192.168.1.0/24",
        iscsi_gateway="192.168.1.1",
        iscsi_ip_range="192.168.1.11-192.168.1.60",
        iscsi_iqns="",
        iscsi_lifs="",
        iscsi_volumes="",
        iscsi_portset="iSCSI",
        iscsi_igroup="",
        iscsi_lun="esxi_lun01",
        iscsi_vmfs_datastore="vmfs_ds01",
        nfs_volume="esxi_datastore_01",
        nfs_export_policy="",
        nfs_mount_path="/esxi_datastore_01",
        nfs_esxi_mount_targets="",
        nfs_lifs="",
        netapp_iscsi_commands=str((((cfg.get("netapp") or {}).get("command_templates") or {}).get("iscsi")) or ""),
        netapp_nfs_commands=str((((cfg.get("netapp") or {}).get("command_templates") or {}).get("nfs")) or ""),
    )
    main.save_kit_config(cfg)
    context = _module_context(request)
    return _render_netapp_page(request, context, service.discover(context))


@router.post("/modules/netapp/bootstrap-complete", response_class=HTMLResponse)
async def netapp_bootstrap_complete(request: Request, bootstrap_complete: str = Form("false")):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    cfg.setdefault("netapp", {})
    cfg["netapp"]["bootstrap_complete"] = str(bootstrap_complete or "").strip().lower() in {"1", "true", "yes", "on"}
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.plan(context) if cfg["netapp"]["bootstrap_complete"] else service._response(context, "bootstrap")
    return _render_netapp_page(request, context, payload, saved=True)


@router.post("/modules/netapp/bootstrap-test/{target}", response_class=HTMLResponse)
async def netapp_bootstrap_test(request: Request, target: str):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.test_bootstrap_target(context, target)
    if payload.get("bootstrap_test"):
        _set_bootstrap_check(cfg, target, payload["bootstrap_test"])
        main.save_kit_config(cfg)
        context = _module_context(request)
        payload = service._response(context, f"bootstrap_{target}")
        payload["bootstrap_test"] = cfg["netapp"]["bootstrap_checks"].get(target)
    return _render_netapp_page(request, context, payload)


@router.post("/modules/netapp/use-discovered-values", response_class=HTMLResponse)
async def netapp_use_discovered_values(request: Request):
    from app import main

    context = _module_context(request)
    discover_payload = service.discover(context)
    if not discover_payload.get("ok") or not discover_payload.get("discovery"):
        return _render_netapp_page(request, context, discover_payload)

    cfg = context["cfg"]
    sync_notes = _sync_discovered_values(cfg, discover_payload["discovery"])
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.plan(context)
    payload.setdefault("suggestions", [])
    payload["suggestions"] = list(sync_notes) + list(payload.get("suggestions") or [])
    return _render_netapp_page(request, context, payload, saved=True)


@router.post("/modules/netapp/probe-vmware-nfs", response_class=HTMLResponse)
async def netapp_probe_vmware_nfs(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.test_vmware_nfs_targets(context)
    if payload.get("vmware_probe"):
        _set_vmware_check(cfg, "nfs_mount", payload["vmware_probe"])
        main.save_kit_config(cfg)
        context = _module_context(request)
        payload = service.plan(context)
        payload["vmware_probe"] = cfg["netapp"]["vmware_checks"].get("nfs_mount")
    return _render_netapp_page(request, context, payload)


@router.post("/modules/netapp/update-convention", response_class=HTMLResponse)
async def netapp_update_convention(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    update_notes = _apply_current_netapp_convention(cfg)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.plan(context)
    payload.setdefault("suggestions", [])
    payload["suggestions"] = list(update_notes) + list(payload.get("suggestions") or [])
    return _render_netapp_page(request, context, payload, saved=True)


@router.post("/modules/netapp/api-readiness", response_class=HTMLResponse)
async def netapp_api_readiness(request: Request):
    from app import main

    context = _module_context(request)
    payload = service.test_connection(context)
    cfg = context["cfg"]
    if payload.get("connection_test"):
        _set_bootstrap_check(cfg, "api_readiness", payload["connection_test"])
        main.save_kit_config(cfg)
    return _render_netapp_page(request, context, payload)


@router.post("/modules/netapp/validate-page", response_class=HTMLResponse)
async def netapp_validate_page(request: Request):
    context = _module_context(request)
    return _render_netapp_page(request, context, service.validate(context))


@router.post("/modules/netapp/export-plan", response_class=HTMLResponse)
async def netapp_export_plan(request: Request):
    from app import main

    context = _module_context(request)
    payload = service.plan(context)
    cfg = context["cfg"]
    profile = build_protocol_profile(cfg)
    vmware_plan = build_vmware_plan(cfg, storage_protocol=str(((payload.get("plan") or {}).get("storage_protocol")) or "nfs"))
    protocol = str(profile.get("protocol") or "iscsi")
    templates = ((cfg.get("netapp") or {}).get("command_templates") or {})
    template_text = str(templates.get(protocol) or "")
    if not template_text:
        template_text = service._default_iscsi_template() if protocol == "iscsi" else service._default_nfs_template()
    command_preview = render_command_preview(template_text, build_token_map(cfg, profile))
    artifact_dir = main.ARTIFACTS_DIR / "generated" / "netapp"
    prefix = f"{str(((cfg.get('site') or {}).get('name') or 'Kit-01')).strip()}-{protocol}-plan"
    export_payload = {
        "netapp_plan": payload.get("plan") or {},
        "vmware_plan": vmware_plan,
        "command_preview": command_preview,
    }
    payload["export_paths"] = write_plan_artifacts(artifact_dir, prefix, export_payload)
    return _render_netapp_page(request, context, payload, saved=True)


@router.post("/modules/netapp/apply-page", response_class=HTMLResponse)
async def netapp_apply_page(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.apply(context, {"job_id": "job-netapp-safe-apply-001", "scope": "netapp.apply", "confirm": True})
    return _render_netapp_page(request, context, payload, saved=True)


@router.get("/modules/netapp/preview", response_class=HTMLResponse)
async def netapp_module_preview(request: Request):
    context = _module_context(request)
    payload = service.preview(context)
    html = _render_template("netapp_preview.html", {"payload": payload, "config": context["cfg"]})
    return HTMLResponse(html)


@router.post("/modules/netapp/discover")
async def netapp_module_discover(request: Request):
    return service.discover(_module_context(request))


@router.post("/modules/netapp/plan")
async def netapp_module_plan(request: Request):
    return service.plan(_module_context(request))


@router.post("/modules/netapp/validate")
async def netapp_module_validate(request: Request):
    return service.validate(_module_context(request))


@router.post("/modules/netapp/apply")
async def netapp_module_apply(request: Request):
    context = _module_context(request)
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    return service.apply(context, dict(body.get("job", {}) if body else {}))


@router.get("/modules/netapp/status")
async def netapp_module_status(request: Request):
    return service.status(_module_context(request))


@router.post("/modules/netapp/repair/{issue_id}")
async def netapp_module_repair(request: Request, issue_id: str):
    return service.repair(_module_context(request), issue_id)


def register_module_routes(app: FastAPI) -> None:
    app.include_router(router)
