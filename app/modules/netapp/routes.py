from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.modules.netapp.schemas import NetAppModuleContext
from app.modules.netapp.service import NetAppModuleService

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
            status_label="Dry-run only",
            outcomes=[
                f"Protocol profile: {str(profile.get('selected_protocol') or 'nfs').upper()}",
                f"Cluster: {str((plan.get('adaptive_discovery') or {}).get('cluster_name') or (payload.get('discovery') or {}).get('cluster_name') or '(unknown)')}",
            ],
            details=list(payload.get("warnings") or []),
        ),
        extra_context={"netapp_payload": payload, "netapp_settings": settings},
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
    iscsi_subnet: str = Form("iSCSI"),
    iscsi_subnet_cidr: str = Form("192.168.1.0/24"),
    iscsi_gateway: str = Form("192.168.1.1"),
    iscsi_ip_range: str = Form("192.168.1.11-192.168.1.60"),
    iscsi_iqns: str = Form(""),
    iscsi_portset: str = Form("iSCSI"),
    iscsi_igroup: str = Form(""),
    iscsi_lun: str = Form("esxi_lun01"),
    iscsi_vmfs_datastore: str = Form("vmfs_ds01"),
    nfs_volume: str = Form("esxi_datastore_01"),
    nfs_export_policy: str = Form("esxi_nfs_policy"),
    nfs_mount_path: str = Form("/esxi_datastore_01"),
    nfs_esxi_mount_targets: str = Form(""),
    netapp_iscsi_commands: str = Form(""),
    netapp_nfs_commands: str = Form(""),
):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
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
    cfg["netapp"].update(
        {
            "host": netapp_host.strip(),
            "username": netapp_username.strip(),
            "password": netapp_password,
            "storage_protocol": protocol,
            "command_templates": {
                "iscsi": netapp_iscsi_commands,
                "nfs": netapp_nfs_commands,
            },
            "desired": {
                "cluster_name": cluster_name.strip(),
                "svm_name": svm_name.strip(),
                "required_nodes": _lines(required_nodes),
                "expected_ports": _lines(expected_ports),
                "data_broadcast_domain": data_broadcast_domain.strip() or "Data",
                "target_mtu": mtu_value,
                "aggregate_node_01": aggregate_node_01.strip() or "aggr_01",
                "aggregate_node_02": aggregate_node_02.strip() or "aggr_02",
                "aggregate_diskcount": diskcount_value,
                "aggregate_raidtype": aggregate_raidtype.strip() or "raid_dp",
                "svm_mgmt_lif": svm_mgmt_lif.strip(),
                "svm_mgmt_ip": svm_mgmt_ip.strip(),
                "management_subnet": management_subnet.strip(),
                "management_gateway": management_gateway.strip(),
                "management_netmask": management_netmask.strip() or "255.255.255.0",
                "autosupport_from": autosupport_from.strip(),
                "autosupport_to": autosupport_to.strip(),
                "autosupport_mail_hosts": _lines(autosupport_mail_hosts),
                "ntp_servers": _lines(ntp_servers),
                "required_users": _lines(required_users),
                "esxi_hosts": _lines(esxi_hosts),
                "iscsi": {
                    "subnet": iscsi_subnet.strip() or "iSCSI",
                    "subnet_cidr": iscsi_subnet_cidr.strip(),
                    "gateway": iscsi_gateway.strip(),
                    "ip_range": iscsi_ip_range.strip(),
                    "portset": iscsi_portset.strip() or "iSCSI",
                    "igroup": iscsi_igroup.strip(),
                    "lun": iscsi_lun.strip(),
                    "vmfs_datastore": iscsi_vmfs_datastore.strip(),
                    "iqns": _lines(iscsi_iqns),
                },
                "nfs": {
                    "volume": nfs_volume.strip(),
                    "export_policy": nfs_export_policy.strip(),
                    "mount_path": nfs_mount_path.strip(),
                    "esxi_mount_targets": _lines(nfs_esxi_mount_targets),
                },
            },
        }
    )
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.plan(context)
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
