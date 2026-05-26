from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.modules.netapp.schemas import NetAppModuleContext
from app.modules.netapp.service import NetAppModuleService
from app.netapp import NetAppClient, NetAppConfig
from app.netapp_console import (
    NetAppConsoleDiscovery,
    discovery_candidates_payload,
    execute_netapp_console_factory_reset,
    probe_netapp_console_login,
    serial_runtime_diagnostics as netapp_serial_runtime_diagnostics,
)
from app.netapp_upgrade import (
    _software_update_is_running,
    _software_update_reached_target,
    build_ontap_upgrade_status,
    build_netapp_upgrade_plan,
    execute_netapp_upgrade,
)
from app.plan_renderer import build_token_map, render_command_preview, write_plan_artifacts
from app.storage_profiles import build_naming, build_protocol_profile, normalize_lifs
from app.upgrade_helper import record_upgrade_inventory
from app.upgrade_panels import build_netapp_upgrade_panel
from app.vmware import build_vmware_plan

router = APIRouter()

MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
template_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

service = NetAppModuleService()
NETAPP_FACTORY_RESET_CONFIRM = "FACTORY RESET NETAPP"
NETAPP_CONSOLE_FACTORY_RESET_CONFIRM = "FACTORY RESET NETAPP CONSOLE"
NETAPP_FACTORY_RESET_DOC_URL = "https://docs.netapp.com/us-en/ontap/system-admin/manage-node-boot-menu-task.html"


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


def _render_netapp_page(
    request: Request,
    context: dict[str, Any],
    payload: dict[str, Any],
    *,
    saved: bool = False,
    action_feedback: dict[str, Any] | None = None,
):
    from app import main

    if _cache_payload_discovery(context, payload):
        main.save_kit_config(context["cfg"])
    plan = payload.get("plan") or {}
    profile = plan.get("protocol_profile") or {}
    settings = service.settings_context(context)
    default_feedback = main.build_action_feedback(
        "NetApp page loaded" if payload.get("action") == "overview" else ("NetApp settings saved" if saved else "NetApp dry-run review"),
        "Showing saved NetApp settings and cached upgrade state. Click Read current NetApp when you want a live ONTAP refresh." if payload.get("action") == "overview" else ("Saved NetApp desired settings and rebuilt the dry-run plan." if saved else "Read-only discovery and validation are loaded for NetApp base workflow and selected storage profile."),
        tone="ready" if payload.get("ok") or saved else "pending",
        status_label="Ready" if payload.get("ok") else "Needs attention",
        outcomes=[
            f"Protocol profile: {str(profile.get('selected_protocol') or 'nfs').upper()}",
            f"Cluster: {str((plan.get('adaptive_discovery') or {}).get('cluster_name') or (payload.get('discovery') or {}).get('cluster_name') or '(unknown)')}",
        ],
        details=([str(payload.get("error"))] if payload.get("error") else []) + list(payload.get("warnings") or []),
    )
    return main.render_page(
        request,
        context["cfg"],
        active_page="netapp",
        action_feedback=action_feedback or default_feedback,
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


def _cache_discovery_summary(cfg: dict[str, Any], discovery: dict[str, Any]) -> None:
    cfg.setdefault("netapp", {})
    version = str(discovery.get("ontap_version") or "").strip()
    if version:
        cfg["netapp"]["last_discovered_ontap_version"] = version
        record_upgrade_inventory(cfg, "netapp", current_version=version, source="Last NetApp discovery", raw_version=version)
    cluster_name = str(discovery.get("cluster_name") or "").strip()
    if cluster_name:
        cfg["netapp"]["last_discovered_cluster_name"] = cluster_name


def _cache_payload_discovery(context: dict[str, Any], payload: dict[str, Any]) -> bool:
    discovery = payload.get("discovery") or {}
    if not payload.get("ok") or not discovery or not discovery.get("ontap_version"):
        return False
    _cache_discovery_summary(context["cfg"], discovery)
    context["cfg"].setdefault("netapp", {})["nfs_capacity"] = service.nfs_capacity_context(context, discovery)
    return True


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
        discovered_nfs_svms = sorted({str(item.get("svm") or "").strip() for item in discovered_nfs_lifs if str(item.get("svm") or "").strip()})
        if len(discovered_nfs_svms) == 1 and not str(desired.get("svm_name") or netapp_cfg.get("svm_name") or "").strip():
            desired["svm_name"] = discovered_nfs_svms[0]
            notes.append(f"SVM name set to {discovered_nfs_svms[0]} from discovered NFS LIFs.")
        normalized_nfs_lifs = normalize_lifs(discovered_nfs_lifs)
        netapp_cfg.setdefault("nfs", {})
        netapp_cfg["nfs"]["lifs"] = normalized_nfs_lifs
        desired.setdefault("nfs", {})
        desired["nfs"]["lifs"] = normalized_nfs_lifs
        notes.append(f"Captured {len(normalized_nfs_lifs)} discovered NFS LIFs.")

    return notes


def _prepare_nfs_storage_defaults(cfg: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    site_name = str(((cfg.get("site") or {}).get("name") or "Kit-01")).strip()
    names = build_naming(site_name)
    shared_subnet = str(((cfg.get("shared_network") or {}).get("subnet") or (cfg.get("ip_plan") or {}).get("subnet") or "10.10.8.0/24")).strip()
    esxi_ip = str(((cfg.get("esxi") or {}).get("management_ip") or (cfg.get("ip_plan") or {}).get("esxi") or "")).strip()
    cfg.setdefault("netapp", {})
    netapp_cfg = cfg["netapp"]
    if str(netapp_cfg.get("storage_protocol") or "").strip().lower() != "nfs":
        notes.append("Selected NFS as the NetApp storage protocol.")
    netapp_cfg["storage_protocol"] = "nfs"

    desired = netapp_cfg.setdefault("desired", {})
    existing_nfs = dict(netapp_cfg.get("nfs") or {})
    desired_nfs = dict(desired.get("nfs") or {})
    saved_lif_items = list(existing_nfs.get("lifs") or desired_nfs.get("lifs") or [])
    discovered_svm = next((str((item or {}).get("svm") or "").strip() for item in saved_lif_items if isinstance(item, dict) and str((item or {}).get("svm") or "").strip()), "")
    if not str(desired.get("svm_name") or netapp_cfg.get("svm_name") or "").strip():
        desired["svm_name"] = discovered_svm or names["svm_name"]
        notes.append(f"Set SVM name to {desired['svm_name']}.")

    normalized_lifs = normalize_lifs(saved_lif_items)
    current_export_policy = str(desired_nfs.get("export_policy") or existing_nfs.get("export_policy") or "").strip()
    if not current_export_policy:
        current_export_policy = names["nfs_export_policy"]
        notes.append(f"Set NFS export policy to {current_export_policy}.")
    cached_capacity = netapp_cfg.get("nfs_capacity") if isinstance(netapp_cfg.get("nfs_capacity"), dict) else {}
    recommended_size = str((cached_capacity or {}).get("recommended_size") or "").strip()
    current_size = str(desired_nfs.get("size") or existing_nfs.get("size") or "").strip()
    if not current_size and recommended_size:
        current_size = recommended_size
        notes.append(f"Defaulted NFS datastore size to half of discovered free aggregate space: {current_size}.")
    current_volume = str(desired_nfs.get("volume") or existing_nfs.get("volume") or "esxi_datastore_01").strip()
    current_datastore_name = str(
        desired_nfs.get("datastore_name")
        or existing_nfs.get("datastore_name")
        or (((cfg.get("vmware") or {}).get("nfs") or {}).get("datastore_name"))
        or current_volume
    ).strip()
    nfs_defaults = {
        "volume": current_volume,
        "size": current_size,
        "datastore_name": current_datastore_name,
        "export_policy": current_export_policy,
        "mount_path": str(desired_nfs.get("mount_path") or existing_nfs.get("mount_path") or f"/{current_volume}").strip(),
        "allowed_subnet": str(desired_nfs.get("allowed_subnet") or existing_nfs.get("allowed_subnet") or shared_subnet).strip(),
        "esxi_mount_targets": list(desired_nfs.get("esxi_mount_targets") or existing_nfs.get("esxi_mount_targets") or ([esxi_ip] if esxi_ip else [])),
        "lifs": [dict(item) for item in normalized_lifs],
    }
    if esxi_ip and esxi_ip not in nfs_defaults["esxi_mount_targets"]:
        nfs_defaults["esxi_mount_targets"].append(esxi_ip)
        notes.append(f"Added ESXi {esxi_ip} as an NFS mount target.")
    desired["nfs"] = nfs_defaults
    netapp_cfg["nfs"] = {
        **existing_nfs,
        "export_policy": nfs_defaults["export_policy"],
        "allowed_subnet": nfs_defaults["allowed_subnet"],
        "size": nfs_defaults["size"],
        "datastore_name": nfs_defaults["datastore_name"],
        "lifs": [dict(item) for item in normalized_lifs],
    }
    cfg.setdefault("vmware", {})
    if not isinstance(cfg["vmware"].get("nfs"), dict):
        cfg["vmware"]["nfs"] = {}
    cfg["vmware"]["nfs"]["datastore_name"] = current_datastore_name
    if normalized_lifs:
        notes.append(f"Prepared {len(normalized_lifs)} NFS data LIFs for planning.")
    else:
        notes.append("No NFS data LIFs are saved yet. Use Read current NetApp or edit desired NFS LIFs before applying.")
    return notes


def _build_netapp_factory_reset_plan(cfg: dict[str, Any]) -> dict[str, Any]:
    netapp_cfg = cfg.get("netapp") or {}
    management = netapp_cfg.get("management") or {}
    bootstrap = netapp_cfg.get("bootstrap_overrides") or {}
    node_names = list(((netapp_cfg.get("discovery") or {}).get("node_names")) or [])
    if not node_names:
        node_names = ["node-1", "node-2"]
    node_01_ip = str(management.get("node_01_mgmt_ip") or bootstrap.get("netapp_node_01_mgmt") or "").strip()
    node_02_ip = str(management.get("node_02_mgmt_ip") or bootstrap.get("netapp_node_02_mgmt") or "").strip()
    sp_a = str(bootstrap.get("netapp_sp_a") or "").strip()
    sp_b = str(bootstrap.get("netapp_sp_b") or "").strip()
    cluster_name = str(netapp_cfg.get("last_discovered_cluster_name") or netapp_cfg.get("cluster_name") or ((netapp_cfg.get("desired") or {}).get("cluster_name")) or "").strip()
    steps = [
        "Disconnect or unmount ESXi/vCenter datastores that use this NetApp before wiping.",
        "Use the SP/BMC or serial console for each controller. Do not rely on the cluster API for the wipe itself.",
        "Reboot one node to the ONTAP boot menu, press Ctrl-C when prompted, then select option 4 to clean configuration and initialize all disks.",
        "Repeat the boot-menu wipe on the partner node only when it is safe and not taken over.",
        "If the system uses root-data partitioning or encrypted drives, follow NetApp boot-menu guidance before selecting the wipe option.",
        "After both nodes are initialized, return to this page and rerun the NetApp bootstrap plan.",
    ]
    return {
        "status": "planned",
        "mode": "manual_console_required",
        "target_host": str(netapp_cfg.get("host") or "").strip(),
        "cluster_name": cluster_name,
        "nodes": node_names,
        "node_management_ips": [item for item in [node_01_ip, node_02_ip] if item],
        "sp_targets": [item for item in [sp_a, sp_b] if item],
        "confirmation_phrase": NETAPP_FACTORY_RESET_CONFIRM,
        "warnings": [
            "This is destructive and erases ONTAP configuration and data on the selected node disks.",
            "The app does not automatically select ONTAP boot-menu wipe options; a human must perform the console step.",
            "Do not run a boot-menu wipe on an HA node that has been taken over.",
            "SED/NSE/FIPS drives need the NetApp protected-drive procedure before initialization.",
        ],
        "manual_steps": steps,
        "reference_url": NETAPP_FACTORY_RESET_DOC_URL,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _clear_netapp_runtime_state_after_factory_reset(cfg: dict[str, Any]) -> list[str]:
    netapp_cfg = cfg.setdefault("netapp", {})
    cleared: list[str] = []
    for key in (
        "bootstrap_checks",
        "discovery",
        "last_factory_reset",
        "nfs_capacity",
        "upgrade",
        "validation",
        "vmware_checks",
    ):
        if key in netapp_cfg:
            netapp_cfg.pop(key, None)
            cleared.append(key)
    for key in ("last_discovered_ontap_version", "last_discovered_cluster_name"):
        if netapp_cfg.get(key):
            netapp_cfg[key] = ""
            cleared.append(key)
    netapp_cfg["bootstrap_complete"] = False
    if "upgrade_inventory" in cfg and isinstance(cfg["upgrade_inventory"], dict):
        cfg["upgrade_inventory"]["netapp"] = {"current_version": "", "source": "", "last_checked_at": ""}
        cleared.append("upgrade_inventory.netapp")
    return cleared


def _netapp_console_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    netapp_cfg = cfg.setdefault("netapp", {})
    console = netapp_cfg.setdefault("console", {})
    console.setdefault("port", "")
    console.setdefault("baud", 115200)
    console.setdefault("username", str(netapp_cfg.get("username") or "admin").strip() or "admin")
    console.setdefault("password", "")
    console.setdefault("node_reboot_command", "")
    console.setdefault("boot_menu_option", "4")
    console.setdefault("last_candidates", [])
    console.setdefault("last_probe", {})
    console.setdefault("last_diagnostics", {})
    console.setdefault("last_raw_output", "")
    console.setdefault("last_reset", {})
    return console


def _apply_netapp_console_form_state(cfg: dict[str, Any], form: dict[str, Any]) -> None:
    console = _netapp_console_cfg(cfg)
    netapp_cfg = cfg.setdefault("netapp", {})
    if "netapp_console_port" in form:
        console["port"] = str(form.get("netapp_console_port") or "").strip()
    if "netapp_console_baud" in form:
        try:
            console["baud"] = int(str(form.get("netapp_console_baud") or console.get("baud") or 115200).strip())
        except ValueError:
            console["baud"] = 115200
    if "netapp_console_username" in form:
        console["username"] = str(form.get("netapp_console_username") or console.get("username") or netapp_cfg.get("username") or "admin").strip()
    console_password = str(form.get("netapp_console_password") or "")
    if console_password:
        console["password"] = console_password
    if "netapp_console_reboot_command" in form:
        console["node_reboot_command"] = str(form.get("netapp_console_reboot_command") or "").strip()
    if "netapp_console_boot_menu_option" in form:
        console["boot_menu_option"] = str(form.get("netapp_console_boot_menu_option") or console.get("boot_menu_option") or "4").strip()
    if "netapp_console_reset_node_name" in form:
        console["reset_node_name"] = str(form.get("netapp_console_reset_node_name") or "").strip()
    if "netapp_console_partner_node_name" in form:
        console["partner_node_name"] = str(form.get("netapp_console_partner_node_name") or "").strip()
    if "netapp_console_disable_storage_failover" in form:
        console["disable_storage_failover"] = str(form.get("netapp_console_disable_storage_failover") or "").strip().lower() in {"1", "true", "yes", "on"}
    if "netapp_console_disable_partner_storage_failover" in form:
        console["disable_partner_storage_failover"] = str(form.get("netapp_console_disable_partner_storage_failover") or "").strip().lower() in {"1", "true", "yes", "on"}
    if "netapp_console_halt_partner_before_reset" in form:
        console["halt_partner_before_reset"] = str(form.get("netapp_console_halt_partner_before_reset") or "").strip().lower() in {"1", "true", "yes", "on"}
    if "netapp_console_normal_boot_after_wipe" in form:
        console["normal_boot_after_wipe"] = str(form.get("netapp_console_normal_boot_after_wipe") or "").strip().lower() in {"1", "true", "yes", "on"}


def _netapp_console_failure_summary(diagnostics: dict[str, Any], probe_results: list[dict[str, Any]], *, exception: str = "") -> tuple[str, list[str]]:
    suggestions: list[str] = []
    ordered_ports = [str(item).strip() for item in list(diagnostics.get("ordered_ports") or []) if str(item).strip()]
    device_access = list(diagnostics.get("device_access") or [])
    probe_errors = [str(item.get("error") or "").strip() for item in probe_results if str(item.get("error") or "").strip()]
    permission_denied = any("permission denied" in error.lower() for error in probe_errors)
    permission_denied = permission_denied or any(
        item.get("path") and (item.get("readable") is False or item.get("writable") is False)
        for item in device_access
    )

    if exception and "pyserial" in exception.lower():
        summary = "NetApp console access cannot start because pyserial is not installed in the app environment."
        suggestions.append("Install the Python dependency set, then restart Lab Builder so serial support loads.")
    elif not diagnostics.get("serial_imported"):
        summary = "NetApp console access cannot start because pyserial is not installed in the app environment."
        suggestions.append("Install pyserial from the project requirements and restart Lab Builder.")
    elif not ordered_ports:
        summary = "No USB serial console adapter was detected by the Lab Builder server."
        suggestions.extend(
            [
                "Connect the NetApp console adapter and confirm the host sees /dev/ttyUSB* or /dev/ttyACM*.",
                "If the adapter was just connected, wait a few seconds and detect the console again.",
            ]
        )
    elif permission_denied:
        diagnostics["permission_denied"] = True
        user = str(diagnostics.get("user") or "the Lab Builder service user")
        summary = f"The server can see the serial adapter, but {user} cannot open it."
        suggestions.append("Grant the Lab Builder server user read/write access to the serial device, usually through the dialout group.")
    elif probe_errors and len(probe_errors) >= max(1, len(probe_results)):
        summary = "Every detected serial adapter probe failed before Lab Builder could read a NetApp prompt."
        suggestions.append("Review the probe errors below; the first failure usually identifies the bad device path, lock, or driver issue.")
    elif probe_results:
        saw_output = any(str(item.get("raw_output") or "").strip() for item in probe_results)
        summary = "A serial adapter responded, but the output did not look like a NetApp console prompt." if saw_output else "The serial adapter opened successfully, but no NetApp console output was received."
        suggestions.extend(
            [
                "Press Enter on the console or power-cycle the controller, then detect again.",
                "Try both 115200 and 9600 baud if the output looks blank or garbled.",
                "Confirm the selected adapter is connected to the NetApp serial console port.",
            ]
        )
    else:
        summary = str(exception or "No NetApp console prompt was detected.").strip()
        suggestions.append("Check the console cable, controller power state, and serial adapter mapping, then detect again.")

    diagnostics["error_summary"] = summary
    diagnostics["suggestions"] = suggestions
    return summary, suggestions


def _discover_netapp_console(cfg: dict[str, Any]) -> dict[str, Any]:
    diagnostics = netapp_serial_runtime_diagnostics()
    try:
        candidates = NetAppConsoleDiscovery().scan()
    except Exception as exc:
        summary, suggestions = _netapp_console_failure_summary(diagnostics, [], exception=str(exc))
        return {
            "ok": False,
            "error": summary,
            "warnings": [],
            "suggestions": suggestions,
            "candidates": [],
            "probe_results": [],
            "diagnostics": diagnostics,
        }
    matches = [item for item in candidates if item.score >= 50]
    probe_results = discovery_candidates_payload(candidates, include_raw=True)
    diagnostics["probe_results"] = [{key: value for key, value in item.items() if key != "raw_output"} for item in probe_results]
    summary = ""
    suggestions: list[str] = []
    if not matches:
        summary, suggestions = _netapp_console_failure_summary(diagnostics, probe_results)
    return {
        "ok": bool(matches),
        "error": "" if matches else summary,
        "warnings": ["Multiple NetApp console candidates were detected. Select the intended console port before running a reset."] if len(matches) > 1 else [],
        "suggestions": suggestions,
        "candidates": discovery_candidates_payload(matches, include_raw=True),
        "probe_results": probe_results,
        "diagnostics": diagnostics,
    }


def _probe_selected_netapp_console(cfg: dict[str, Any]) -> dict[str, Any]:
    console = _netapp_console_cfg(cfg)
    return probe_netapp_console_login(
        port=str(console.get("port") or "").strip(),
        baud=int(console.get("baud") or 115200),
        username=str(console.get("username") or cfg.get("netapp", {}).get("username") or "admin").strip(),
        password=str(console.get("password") or cfg.get("netapp", {}).get("password") or ""),
    )


def _start_netapp_console_factory_reset_worker(cfg: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    from app import main

    kit_name = str((cfg.get("site") or {}).get("name") or main.get_current_kit_name())
    operation_id = f"netapp-console-reset-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    started_at = datetime.now(timezone.utc).isoformat()
    console = _netapp_console_cfg(cfg)
    running = {
        "operation_id": operation_id,
        "status": "running",
        "ok": False,
        "started_at": started_at,
        "port": str(options.get("port") or console.get("port") or ""),
        "baud": int(options.get("baud") or console.get("baud") or 115200),
        "message": "NetApp console factory reset sequence started.",
    }
    console["last_reset"] = running
    main.save_kit_config(cfg)

    def worker() -> None:
        worker_cfg = main.load_kit_config(kit_name)
        worker_console = _netapp_console_cfg(worker_cfg)
        try:
            result = execute_netapp_console_factory_reset(
                port=str(options.get("port") or ""),
                baud=int(options.get("baud") or 115200),
                username=str(options.get("username") or ""),
                password=str(options.get("password") or ""),
                reboot_command=str(options.get("reboot_command") or ""),
                boot_menu_option=str(options.get("boot_menu_option") or "4"),
                boot_wait_seconds=int(options.get("boot_wait_seconds") or 240),
                wipe_wait_seconds=int(options.get("wipe_wait_seconds") or 1800),
                reset_node_name=str(options.get("reset_node_name") or ""),
                partner_node_name=str(options.get("partner_node_name") or ""),
                disable_storage_failover=bool(options.get("disable_storage_failover", False)),
                disable_partner_storage_failover=bool(options.get("disable_partner_storage_failover", False)),
                halt_partner_before_reset=bool(options.get("halt_partner_before_reset", False)),
                normal_boot_after_wipe=bool(options.get("normal_boot_after_wipe", True)),
            )
        except Exception as exc:
            result = {"ok": False, "status": "failed", "error": str(exc).splitlines()[0], "raw_output": ""}
        finished = {
            **{key: value for key, value in result.items() if key != "raw_output"},
            "operation_id": operation_id,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if result.get("raw_output"):
            worker_console["last_raw_output"] = str(result.get("raw_output") or "")
        if result.get("ok") and options.get("clear_saved_state"):
            finished["cleared_local_state"] = _clear_netapp_runtime_state_after_factory_reset(worker_cfg)
        worker_console["last_reset"] = finished
        main.save_kit_config(worker_cfg)

    thread = threading.Thread(target=worker, name=operation_id, daemon=True)
    thread.start()
    return running


def _store_netapp_upgrade_plan(cfg: dict[str, Any], plan: dict) -> None:
    cfg.setdefault("netapp", {})
    cfg["netapp"].setdefault("upgrade", {})
    cfg["netapp"]["upgrade"]["last_plan"] = plan


def _compact_error(exc: Exception) -> str:
    return " ".join(str(exc).split())


def _record_netapp_upgrade_activity(cfg: dict[str, Any], event: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    from app import main

    progress_by_phase = {
        "queued": 5,
        "precheck": 10,
        "connect": 20,
        "upload": 35,
        "validate": 55,
        "start": 70,
        "upgrade": 85,
        "blocked": 100,
        "failed": 100,
        "complete": 100,
    }
    cfg.setdefault("netapp", {}).setdefault("upgrade", {})
    activity = cfg["netapp"]["upgrade"].setdefault("activity", {})
    events = list(activity.get("events") or [])
    events.append(event)
    phase = str(event.get("phase") or activity.get("phase") or "")
    event_progress = event.get("progress_percent")
    try:
        progress_percent = int(event_progress) if event_progress is not None else progress_by_phase.get(phase, int(activity.get("progress_percent") or 0))
    except (TypeError, ValueError):
        progress_percent = progress_by_phase.get(phase, int(activity.get("progress_percent") or 0))
    activity.update(
        {
            "status": status or activity.get("status") or "running",
            "phase": phase,
            "message": event.get("message") or activity.get("message") or "",
            "updated_at": event.get("timestamp") or activity.get("updated_at") or "",
            "events": events[-80:],
            "progress_percent": max(0, min(100, progress_percent)),
        }
    )
    if event.get("job_uuid"):
        activity["job_uuid"] = event.get("job_uuid")
    if not activity.get("started_at"):
        activity["started_at"] = event.get("timestamp") or ""
    main.save_kit_config(cfg)
    return activity


def _start_netapp_upgrade_worker(cfg: dict[str, Any]) -> None:
    from app import main

    def progress(event: dict[str, Any]) -> None:
        _record_netapp_upgrade_activity(cfg, event, status="running")

    def worker() -> None:
        try:
            result = execute_netapp_upgrade(
                cfg,
                main.scan_upgrade_media(),
                build_client=lambda *, host, username, password: NetAppClient(
                    NetAppConfig(host=host, username=username, password=password, verify_tls=False, timeout=30)
                ),
                progress=progress,
                skip_warnings=True,
            )
            cfg.setdefault("netapp", {}).setdefault("upgrade", {})["last_result"] = result
            _record_netapp_upgrade_activity(
                cfg,
                {"phase": "complete", "message": "ONTAP upgrade completed.", "timestamp": result.get("completed_at") or ""},
                status="completed",
            )
            main.save_kit_config(cfg)
        except Exception as exc:
            error = _compact_error(exc)
            cfg.setdefault("netapp", {}).setdefault("upgrade", {})["last_result"] = {"status": "failed", "error": error}
            _record_netapp_upgrade_activity(
                cfg,
                {"phase": "failed", "message": error, "timestamp": datetime.now(timezone.utc).isoformat()},
                status="failed",
            )
            main.save_kit_config(cfg)

    thread = threading.Thread(target=worker, name="netapp-upgrade-worker", daemon=True)
    thread.start()


def _read_current_ontap_version_for_upgrade(cfg: dict[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    netapp_cfg = cfg.setdefault("netapp", {})
    host = str(netapp_cfg.get("host") or "").strip()
    username = str(netapp_cfg.get("username") or "admin").strip()
    password = str(netapp_cfg.get("password") or "")
    if not host:
        return "", ["ONTAP API target is not set."]
    if not username or not password:
        return "", ["Saved ONTAP credentials are incomplete."]
    try:
        client = NetAppClient(NetAppConfig(host=host, username=username, password=password, verify_tls=False, timeout=20))
        cluster = client.get_cluster()
    except Exception as exc:
        return "", [f"Could not read ONTAP version from {host}: {str(exc).splitlines()[0]}"]

    version_payload = cluster.get("version") if isinstance(cluster, dict) else {}
    version = ""
    if isinstance(version_payload, dict):
        version = str(version_payload.get("full") or version_payload.get("generation") or "").strip()
    cluster_name = str(cluster.get("name") or "").strip() if isinstance(cluster, dict) else ""
    if version:
        netapp_cfg["last_discovered_ontap_version"] = version
        record_upgrade_inventory(cfg, "netapp", current_version=version, source="Live ONTAP upgrade readiness check", raw_version=version)
        notes.append(f"Current ONTAP version read from {host}: {version}")
    else:
        notes.append(f"Connected to {host}, but /api/cluster did not return an ONTAP version.")
    if cluster_name:
        netapp_cfg["last_discovered_cluster_name"] = cluster_name
        notes.append(f"Cluster: {cluster_name}")
    return version, notes


def _netapp_upgrade_target_version(cfg: dict[str, Any]) -> str:
    upgrade = ((cfg.get("netapp") or {}).get("upgrade") or {})
    for source in (upgrade.get("last_plan") or {}, upgrade.get("last_result") or {}, upgrade.get("activity") or {}):
        for key in ("media_version", "target_version", "pending_version"):
            value = str((source or {}).get(key) or "").strip()
            if value:
                return value
    return ""


def _software_update_progress_percent(payload: dict[str, Any]) -> int:
    try:
        elapsed = int(str(payload.get("elapsed_duration") or ""))
        estimated = int(str(payload.get("estimated_duration") or ""))
    except (TypeError, ValueError):
        return 85
    if estimated <= 0:
        return 85
    return min(99, max(70, 70 + int((elapsed / estimated) * 29)))


def _reconcile_running_netapp_upgrade(cfg: dict[str, Any]) -> bool:
    upgrade = cfg.setdefault("netapp", {}).setdefault("upgrade", {})
    activity = dict(upgrade.get("activity") or {})
    if str(activity.get("status") or "").lower() != "running":
        return False

    target_version = _netapp_upgrade_target_version(cfg)
    netapp_cfg = cfg.setdefault("netapp", {})
    host = str(netapp_cfg.get("host") or "").strip()
    username = str(netapp_cfg.get("username") or "admin").strip()
    password = str(netapp_cfg.get("password") or "")
    if not target_version or not host or not username or not password:
        return False

    try:
        client = NetAppClient(NetAppConfig(host=host, username=username, password=password, verify_tls=False, timeout=5))
        software = client.get_cluster_software()
    except Exception:
        return False

    current_version = str(software.get("version") or "").strip()
    pending_version = str(software.get("pending_version") or "").strip()
    software_state = str(software.get("state") or "").strip().lower()
    now = datetime.now(timezone.utc).isoformat()

    if _software_update_reached_target(software, target_version):
        result = {
            "status": "completed",
            "host": host,
            "previous_version": str((upgrade.get("last_plan") or {}).get("current_version") or ""),
            "target_version": target_version,
            "current_version": current_version or target_version,
            "raw": software,
            "completed_at": now,
            "source": "Live ONTAP activity reconciliation",
        }
        upgrade["last_result"] = result
        record_upgrade_inventory(
            cfg,
            "netapp",
            current_version=current_version or target_version,
            raw_version=current_version or target_version,
            source="Live ONTAP activity reconciliation",
        )
        _record_netapp_upgrade_activity(
            cfg,
            {
                "phase": "complete",
                "message": f"ONTAP upgrade completed to {current_version or target_version}.",
                "timestamp": now,
                "progress_percent": 100,
                "software_state": software_state or "completed",
                "pending_version": pending_version,
                "current_version": current_version,
                "status_details": software.get("status_details") or [],
                "update_details": software.get("update_details") or [],
                "result": result,
            },
            status="completed",
        )
        return True

    if software_state in {"failed", "failure", "canceled", "cancelled"}:
        message = f"ONTAP software update state is {software_state or 'failed'} while targeting {target_version}."
        upgrade["last_result"] = {"status": "failed", "error": message, "raw": software}
        _record_netapp_upgrade_activity(
            cfg,
            {
                "phase": "failed",
                "message": message,
                "timestamp": now,
                "progress_percent": 100,
                "software_state": software_state,
                "pending_version": pending_version,
                "current_version": current_version,
                "status_details": software.get("status_details") or [],
                "update_details": software.get("update_details") or [],
            },
            status="failed",
        )
        return True

    if software_state in {"paused", "pause"}:
        message = f"ONTAP software update is paused while targeting {target_version}."
        upgrade["last_result"] = {"status": "blocked", "error": message, "raw": software}
        _record_netapp_upgrade_activity(
            cfg,
            {
                "phase": "blocked",
                "message": message,
                "timestamp": now,
                "progress_percent": 100,
                "software_state": software_state,
                "pending_version": pending_version,
                "current_version": current_version,
                "status_details": software.get("status_details") or [],
                "update_details": software.get("update_details") or [],
            },
            status="blocked",
        )
        return True

    if _software_update_is_running(software):
        message = f"ONTAP software state: {software_state or 'unknown'}; current {current_version or 'unknown'}; pending {pending_version or target_version}."
        if str(activity.get("message") or "") == message:
            return False
        upgrade["last_result"] = {
            "status": "running",
            "message": message,
            "target_version": target_version,
            "current_version": current_version,
            "raw": software,
        }
        _record_netapp_upgrade_activity(
            cfg,
            {
                "phase": "upgrade",
                "message": message,
                "timestamp": now,
                "progress_percent": _software_update_progress_percent(software),
                "software_state": software_state,
                "pending_version": pending_version,
                "current_version": current_version,
                "elapsed_duration": software.get("elapsed_duration"),
                "estimated_duration": software.get("estimated_duration"),
                "status_details": software.get("status_details") or [],
                "update_details": software.get("update_details") or [],
            },
            status="running",
        )
        return True

    return False


def _apply_current_netapp_convention(cfg: dict[str, Any]) -> list[str]:
    from app.core.config import ip_at_offset

    shared = cfg.setdefault("shared_network", {})
    subnet = str(shared.get("subnet") or "10.10.8.0/24").strip()
    shared.update(
        {
            "netapp_sp_a_offset": 13,
            "netapp_sp_b_offset": 14,
            "netapp_cluster_mgmt_offset": 45,
            "netapp_node_01_mgmt_offset": 46,
            "netapp_node_02_mgmt_offset": 47,
            "netapp_svm_mgmt_offset": 48,
        }
    )
    netapp_cfg = cfg.setdefault("netapp", {})
    management = netapp_cfg.setdefault("management", {})
    desired = netapp_cfg.setdefault("desired", {})
    overrides = dict(netapp_cfg.get("bootstrap_overrides") or {})

    current_values = {
        "netapp_sp_a": ip_at_offset(subnet, 13),
        "netapp_sp_b": ip_at_offset(subnet, 14),
        "netapp_cluster_mgmt": ip_at_offset(subnet, 45),
        "netapp_node_01_mgmt": ip_at_offset(subnet, 46),
        "netapp_node_02_mgmt": ip_at_offset(subnet, 47),
        "netapp_svm_mgmt": ip_at_offset(subnet, 48),
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
    _apply_netapp_console_form_state(cfg, form)
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
    if any(key in form for key in {"nfs_volume", "nfs_size", "nfs_datastore_name", "nfs_export_policy", "nfs_mount_path", "nfs_esxi_mount_targets", "nfs_lifs"}):
        desired = cfg["netapp"].setdefault("desired", {})
        existing_nfs = dict(desired.get("nfs") or {})
        nfs_volume = str(form.get("nfs_volume") or existing_nfs.get("volume") or "esxi_datastore_01").strip()
        datastore_name = str(form.get("nfs_datastore_name") or existing_nfs.get("datastore_name") or nfs_volume).strip()
        desired["nfs"] = {
            **existing_nfs,
            "volume": nfs_volume,
            "size": str(form.get("nfs_size") or existing_nfs.get("size") or "").strip(),
            "datastore_name": datastore_name,
            "export_policy": str(form.get("nfs_export_policy") or existing_nfs.get("export_policy") or "").strip(),
            "mount_path": str(form.get("nfs_mount_path") or existing_nfs.get("mount_path") or f"/{nfs_volume}").strip(),
            "esxi_mount_targets": _lines(str(form.get("nfs_esxi_mount_targets") or "")) or list(existing_nfs.get("esxi_mount_targets") or []),
            "lifs": _parse_lifs(str(form.get("nfs_lifs") or "")) or list(existing_nfs.get("lifs") or []),
        }
        cfg.setdefault("vmware", {})
        if not isinstance(cfg["vmware"].get("nfs"), dict):
            cfg["vmware"]["nfs"] = {}
        cfg["vmware"]["nfs"]["datastore_name"] = datastore_name


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
    nfs_size: str,
    nfs_datastore_name: str,
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
        mtu_value = int(str(target_mtu or "1500").strip())
    except ValueError:
        mtu_value = 1500
    try:
        diskcount_value = int(str(aggregate_diskcount or "11").strip())
    except ValueError:
        diskcount_value = 11
    protocol = str(netapp_storage_protocol or "nfs").strip().lower()
    if protocol not in {"iscsi", "nfs"}:
        protocol = "nfs"
    nfs_datastore_label = nfs_datastore_name.strip() or str(existing_nfs.get("datastore_name") or nfs_volume.strip() or "esxi_datastore_01")
    cfg.setdefault("netapp", {})
    cfg.setdefault("vmware", {})
    if not isinstance(cfg["vmware"].get("nfs"), dict):
        cfg["vmware"]["nfs"] = {}
    if nfs_datastore_label:
        cfg["vmware"]["nfs"]["datastore_name"] = nfs_datastore_label
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
            "mtu": mtu_value,
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
                    "size": nfs_size.strip() or str(existing_nfs.get("size") or ""),
                    "datastore_name": nfs_datastore_label,
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
    payload = service.page_overview(context)
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
    target_mtu: str = Form("1500"),
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
    nfs_size: str = Form(""),
    nfs_datastore_name: str = Form(""),
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
        nfs_size=nfs_size,
        nfs_datastore_name=nfs_datastore_name,
        nfs_export_policy=nfs_export_policy,
        nfs_mount_path=nfs_mount_path,
        nfs_esxi_mount_targets=nfs_esxi_mount_targets,
        nfs_lifs=nfs_lifs,
        netapp_iscsi_commands=netapp_iscsi_commands,
        netapp_nfs_commands=netapp_nfs_commands,
    )
    _apply_netapp_console_form_state(cfg, {key: value for key, value in dict(await request.form()).items()})
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.plan(context)
    return _render_netapp_page(request, context, payload, saved=True)


@router.post("/modules/netapp/prepare-nfs-storage", response_class=HTMLResponse)
async def netapp_prepare_nfs_storage(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    notes = _prepare_nfs_storage_defaults(cfg)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.page_overview(context)
    payload.setdefault("suggestions", [])
    payload["suggestions"] = notes + list(payload.get("suggestions") or [])
    feedback = main.build_action_feedback(
        "NFS storage defaults prepared",
        "NetApp is set to NFS and the datastore, export policy, ESXi target, and saved NFS LIF values are ready for discovery and validation.",
        tone="ready",
        outcomes=notes,
    )
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


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
    target_mtu: str = Form("1500"),
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
    nfs_size: str = Form(""),
    nfs_datastore_name: str = Form(""),
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
        nfs_size=nfs_size,
        nfs_datastore_name=nfs_datastore_name,
        nfs_export_policy=nfs_export_policy,
        nfs_mount_path=nfs_mount_path,
        nfs_esxi_mount_targets=nfs_esxi_mount_targets,
        nfs_lifs=nfs_lifs,
        netapp_iscsi_commands=netapp_iscsi_commands,
        netapp_nfs_commands=netapp_nfs_commands,
    )
    _apply_netapp_console_form_state(cfg, {key: value for key, value in dict(await request.form()).items()})
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.test_connection(context)
    if payload.get("connection_test"):
        _set_bootstrap_check(cfg, "api_readiness", payload["connection_test"])
        main.save_kit_config(cfg)
    test = dict(payload.get("connection_test") or {})
    feedback = main.build_action_feedback(
        "NetApp API connection verified" if test.get("api_auth_ok") else "NetApp API connection failed",
        "The app reached ONTAP with the saved cluster management target and credentials." if test.get("api_auth_ok") else str(payload.get("error") or "The app could not authenticate to ONTAP."),
        tone="ready" if test.get("api_auth_ok") else "danger",
        status_label="Ready" if test.get("api_auth_ok") else "Needs attention",
        outcomes=[
            f"Target: {test.get('target_host') or netapp_host or 'Not set'}",
            f"Cluster: {test.get('cluster_name') or 'Unknown'}",
        ],
        details=list(payload.get("warnings") or []),
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


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
        target_mtu=str((cfg.get("netapp") or {}).get("mtu") or 1500),
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
        nfs_volume=str(((((cfg.get("netapp") or {}).get("desired") or {}).get("nfs") or {}).get("volume")) or "esxi_datastore_01"),
        nfs_size=str(((((cfg.get("netapp") or {}).get("desired") or {}).get("nfs") or {}).get("size")) or ""),
        nfs_datastore_name=str(((((cfg.get("netapp") or {}).get("desired") or {}).get("nfs") or {}).get("datastore_name")) or ((((cfg.get("vmware") or {}).get("nfs") or {}).get("datastore_name")) or "")),
        nfs_export_policy=str(((((cfg.get("netapp") or {}).get("desired") or {}).get("nfs") or {}).get("export_policy")) or ""),
        nfs_mount_path=str(((((cfg.get("netapp") or {}).get("desired") or {}).get("nfs") or {}).get("mount_path")) or "/esxi_datastore_01"),
        nfs_esxi_mount_targets="",
        nfs_lifs="",
        netapp_iscsi_commands=str((((cfg.get("netapp") or {}).get("command_templates") or {}).get("iscsi")) or ""),
        netapp_nfs_commands=str((((cfg.get("netapp") or {}).get("command_templates") or {}).get("nfs")) or ""),
    )
    _apply_netapp_console_form_state(cfg, {key: value for key, value in dict(await request.form()).items()})
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.discover(context)
    if payload.get("ok") and payload.get("discovery"):
        _cache_discovery_summary(cfg, payload["discovery"])
        cfg.setdefault("netapp", {})["nfs_capacity"] = service.nfs_capacity_context(context, payload["discovery"])
        main.save_kit_config(cfg)
        context = _module_context(request)
    discovery = dict(payload.get("discovery") or {})
    feedback = main.build_action_feedback(
        "Current NetApp state read" if payload.get("ok") and discovery else "Current NetApp read failed",
        "Read ONTAP cluster, node, disk, LIF, and protocol state from the saved API target." if payload.get("ok") and discovery else str(payload.get("error") or "Discovery did not return usable ONTAP state."),
        tone="ready" if payload.get("ok") and discovery else "danger",
        status_label="Discovered" if payload.get("ok") and discovery else "Needs attention",
        outcomes=[
            f"Cluster: {discovery.get('cluster_name') or 'Unknown'}",
            f"ONTAP: {discovery.get('ontap_version') or 'Unknown'}",
        ],
        details=list(payload.get("warnings") or []),
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


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
    complete = bool(cfg["netapp"]["bootstrap_complete"])
    feedback = main.build_action_feedback(
        "NetApp bootstrap marked complete" if complete else "NetApp bootstrap marked incomplete",
        "ONTAP API actions are now available after bootstrap completion." if complete else "NetApp API actions are held until bootstrap is marked complete again.",
        tone="ready" if complete else "pending",
        outcomes=[f"Cluster management IP: {((cfg.get('netapp') or {}).get('host') or 'Not set')}"],
    )
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


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
    test = dict(payload.get("bootstrap_test") or {})
    feedback = main.build_action_feedback(
        "NetApp bootstrap target reachable" if test.get("reachable") else "NetApp bootstrap target did not respond",
        "The planned bootstrap address responded on one of the tested management ports." if test.get("reachable") else f"{target.replace('_', ' ').title()} did not respond on the tested ports.",
        tone="ready" if test.get("reachable") else "danger",
        outcomes=[
            f"Target: {test.get('host') or 'Not set'}",
            f"Ports: {', '.join(str(item) for item in list(test.get('ports_tested') or [])) or 'not tested'}",
        ],
        details=list(payload.get("warnings") or []),
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


@router.post("/modules/netapp/use-discovered-values", response_class=HTMLResponse)
async def netapp_use_discovered_values(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
    context = _module_context(request)
    discover_payload = service.discover(context)
    if not discover_payload.get("ok") or not discover_payload.get("discovery"):
        feedback = main.build_action_feedback(
            "No discovered NetApp values were applied",
            "The app tried to read ONTAP first, but discovery did not return usable values to copy into the kit settings.",
            tone="danger",
            outcomes=[f"Target: {str((cfg.get('netapp') or {}).get('host') or 'Not set')}"],
            details=([str(discover_payload.get("error"))] if discover_payload.get("error") else []) + list(discover_payload.get("warnings") or []),
        )
        return _render_netapp_page(request, context, discover_payload, action_feedback=feedback)

    _cache_discovery_summary(cfg, discover_payload["discovery"])
    cfg.setdefault("netapp", {})["nfs_capacity"] = service.nfs_capacity_context(context, discover_payload["discovery"])
    sync_notes = _sync_discovered_values(cfg, discover_payload["discovery"])
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.plan(context)
    payload.setdefault("suggestions", [])
    payload["suggestions"] = list(sync_notes) + list(payload.get("suggestions") or [])
    feedback = main.build_action_feedback(
        "Discovered NetApp values applied" if sync_notes else "Discovery succeeded, no settings changed",
        "Copied the discovered cluster management, node management, cluster name, and NFS LIF values that ONTAP returned.",
        tone="ready" if sync_notes else "pending",
        outcomes=sync_notes or ["Discovered values already match the saved kit settings."],
    )
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


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
    probe = dict(payload.get("vmware_probe") or cfg.get("netapp", {}).get("vmware_checks", {}).get("nfs_mount") or {})
    feedback = main.build_action_feedback(
        "NetApp NFS probe ready" if probe.get("ready") else "NetApp NFS probe needs review",
        "ESXi management and discovered NFS target reachability checks passed." if probe.get("ready") else "Review the ESXi and NFS reachability results before mounting the datastore.",
        tone="ready" if probe.get("ready") else "pending",
        outcomes=[
            f"Datastore: {probe.get('datastore_name') or 'Not set'}",
            f"NFS servers: {', '.join(list(probe.get('server_ips') or [])) or 'None'}",
        ],
        details=list(payload.get("warnings") or []),
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


@router.post("/modules/netapp/update-convention", response_class=HTMLResponse)
async def netapp_update_convention(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    update_notes = _apply_current_netapp_convention(cfg)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.plan(context)
    payload.setdefault("suggestions", [])
    payload["suggestions"] = list(update_notes) + list(payload.get("suggestions") or [])
    feedback = main.build_action_feedback(
        "NetApp IP convention updated",
        "Updated the planned bootstrap values and ONTAP API target to the current NetApp build convention.",
        tone="ready",
        outcomes=update_notes,
    )
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


@router.post("/modules/netapp/console-discover", response_class=HTMLResponse)
async def netapp_console_discover(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    result = _discover_netapp_console(cfg)
    console = _netapp_console_cfg(cfg)
    candidates = list(result.get("candidates") or [])
    probe_results = list(result.get("probe_results") or [])
    diagnostics = dict(result.get("diagnostics") or {})
    suggestions = list(result.get("suggestions") or diagnostics.get("suggestions") or [])
    console["last_candidates"] = [{key: value for key, value in item.items() if key != "raw_output"} for item in candidates]
    console["last_probe_results"] = [{key: value for key, value in item.items() if key != "raw_output"} for item in probe_results]
    console["last_diagnostics"] = diagnostics
    console["last_raw_output"] = "\n\n".join(str(item.get("raw_output") or "") for item in candidates if item.get("raw_output"))
    if len(candidates) == 1:
        console["port"] = str(candidates[0].get("port") or "")
        console["baud"] = int(candidates[0].get("baud") or 115200)
    main.save_kit_config(cfg)

    details = list(result.get("warnings") or []) + suggestions
    if diagnostics:
        details.extend(
            [
                f"pyserial import status: {'ready' if diagnostics.get('serial_imported') else 'missing'}",
                f"Visible serial ports: {', '.join(list(diagnostics.get('ordered_ports') or [])) or 'none'}",
            ]
        )
    port_errors = [str(item.get("error") or "").strip() for item in probe_results if str(item.get("error") or "").strip()]
    if port_errors:
        details.append(f"Probe error: {port_errors[0]}")
    if result.get("ok"):
        if len(candidates) == 1:
            title = "NetApp console found"
            message = "Serial discovery found one NetApp console candidate and selected it."
            outcomes = [f"Console: {console.get('port')} @ {console.get('baud')}"]
        else:
            title = "Choose NetApp console"
            message = "Multiple NetApp console candidates matched. Select the intended USB port before running a reset."
            outcomes = [f"{len(candidates)} console candidates found"]
        feedback = main.build_action_feedback(title, message, tone="ready", outcomes=outcomes, details=details)
    else:
        feedback = main.build_action_feedback(
            "NetApp console not found",
            str(result.get("error") or "No NetApp console prompt was detected."),
            tone="pending",
            details=details,
        )
    context = _module_context(request)
    payload = service.page_overview(context)
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


@router.post("/modules/netapp/console-probe", response_class=HTMLResponse)
async def netapp_console_probe(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    result = _probe_selected_netapp_console(cfg)
    console = _netapp_console_cfg(cfg)
    console["last_probe"] = {key: value for key, value in result.items() if key != "raw_output"}
    if result.get("raw_output"):
        console["last_raw_output"] = str(result.get("raw_output") or "")
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "NetApp console login verified" if result.get("ok") else "NetApp console probe failed",
        "The selected USB console reached an ONTAP CLI or boot menu prompt." if result.get("ok") else str(result.get("error") or "The selected console did not reach an ONTAP prompt."),
        tone="ready" if result.get("ok") else "danger",
        outcomes=[
            f"Console: {console.get('port') or 'Not selected'} @ {console.get('baud') or 115200}",
            f"Prompt: {result.get('prompt_type') or 'unknown'}",
        ],
    )
    context = _module_context(request)
    payload = service.page_overview(context)
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


@router.post("/modules/netapp/console-factory-reset", response_class=HTMLResponse)
async def netapp_console_factory_reset(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    console = _netapp_console_cfg(cfg)
    phrase = str(form.get("netapp_console_factory_reset_confirm") or "").strip().upper()
    acknowledged = str(form.get("netapp_console_factory_reset_ack") or "").strip().lower() in {"1", "true", "yes", "on"}
    send_wipe = str(form.get("netapp_console_reset_send_boot_menu_wipe") or "").strip().lower() in {"1", "true", "yes", "on"}
    clear_saved_state = str(form.get("netapp_console_factory_reset_clear_saved_state") or "").strip().lower() in {"1", "true", "yes", "on"}

    if phrase != NETAPP_CONSOLE_FACTORY_RESET_CONFIRM or not acknowledged or not send_wipe:
        result = {
            "status": "blocked",
            "ok": False,
            "error": f"Check both confirmation boxes and type {NETAPP_CONSOLE_FACTORY_RESET_CONFIRM} before the app sends ONTAP boot-menu option 4 over the console.",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "port": str(console.get("port") or ""),
            "baud": int(console.get("baud") or 115200),
        }
        console["last_reset"] = result
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback("NetApp console factory reset blocked", result["error"], tone="danger", status_label="Blocked")
        context = _module_context(request)
        payload = service.page_overview(context)
        return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)

    port = str(console.get("port") or "").strip()
    username = str(console.get("username") or cfg.get("netapp", {}).get("username") or "admin").strip()
    boot_menu_option = str(console.get("boot_menu_option") or "4").strip()
    if not port or not username or boot_menu_option != "4":
        missing = []
        if not port:
            missing.append("select a NetApp console USB port")
        if not username:
            missing.append("enter a NetApp console username")
        if boot_menu_option != "4":
            missing.append("use ONTAP boot-menu option 4")
        result = {
            "status": "blocked",
            "ok": False,
            "error": "Before starting console reset, " + ", ".join(missing) + ".",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "port": port,
            "baud": int(console.get("baud") or 115200),
        }
        console["last_reset"] = result
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback("NetApp console factory reset blocked", result["error"], tone="danger", status_label="Blocked")
        context = _module_context(request)
        payload = service.page_overview(context)
        return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)

    options = {
        "port": port,
        "baud": int(console.get("baud") or 115200),
        "username": username,
        "password": str(console.get("password") or cfg.get("netapp", {}).get("password") or ""),
        "reboot_command": str(console.get("node_reboot_command") or "").strip(),
        "boot_menu_option": boot_menu_option,
        "boot_wait_seconds": 240,
        "wipe_wait_seconds": 1800,
        "reset_node_name": str(console.get("reset_node_name") or "").strip(),
        "partner_node_name": str(console.get("partner_node_name") or "").strip(),
        "disable_storage_failover": bool(console.get("disable_storage_failover", True)),
        "disable_partner_storage_failover": bool(console.get("disable_partner_storage_failover", False)),
        "halt_partner_before_reset": bool(console.get("halt_partner_before_reset", False)),
        "normal_boot_after_wipe": bool(console.get("normal_boot_after_wipe", True)),
        "clear_saved_state": clear_saved_state,
    }
    result = _start_netapp_console_factory_reset_worker(cfg, options)
    console = _netapp_console_cfg(cfg)
    console["last_reset"] = result
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "NetApp console factory reset started",
        "The app started the serial reset worker. Monitor the raw console output and do not touch the partner node until this controller is stable.",
        tone="danger",
        status_label="Running",
        outcomes=[
            f"Console: {result.get('port') or 'Not selected'} @ {result.get('baud') or 115200}",
            "Boot-menu option: 4",
        ],
        details=[
            "This sends a destructive ONTAP boot-menu wipe selection only because the console-specific confirmation was provided.",
            "Leave force giveback/manual HA operations outside automation.",
        ],
        links=[{"label": "NetApp boot menu docs", "href": NETAPP_FACTORY_RESET_DOC_URL}],
    )
    context = _module_context(request)
    payload = service.page_overview(context)
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


@router.post("/modules/netapp/factory-reset-plan", response_class=HTMLResponse)
async def netapp_factory_reset_plan(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    plan = _build_netapp_factory_reset_plan(cfg)
    cfg.setdefault("netapp", {})["last_factory_reset"] = plan
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.page_overview(context)
    payload["factory_reset"] = plan
    feedback = main.build_action_feedback(
        "NetApp factory reset runbook prepared",
        "Review the manual console wipe steps. No ONTAP wipe command was executed by the app.",
        tone="pending",
        status_label="Manual action",
        outcomes=[
            f"Target: {plan.get('target_host') or 'Not set'}",
            f"Cluster: {plan.get('cluster_name') or 'Unknown'}",
            f"Nodes: {', '.join(plan.get('nodes') or [])}",
        ],
        details=list(plan.get("warnings") or []) + list(plan.get("manual_steps") or []),
        links=[{"label": "NetApp boot menu docs", "href": NETAPP_FACTORY_RESET_DOC_URL}],
    )
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


@router.post("/modules/netapp/factory-reset-record", response_class=HTMLResponse)
async def netapp_factory_reset_record(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    phrase = str(form.get("netapp_factory_reset_confirm") or "").strip().upper()
    acknowledged = str(form.get("netapp_factory_reset_ack") or "").strip().lower() in {"1", "true", "yes", "on"}
    clear_saved_state = str(form.get("netapp_factory_reset_clear_saved_state") or "").strip().lower() in {"1", "true", "yes", "on"}
    plan = _build_netapp_factory_reset_plan(cfg)

    if phrase != NETAPP_FACTORY_RESET_CONFIRM or not acknowledged:
        result = {
            **plan,
            "status": "blocked",
            "error": f"Check the acknowledgement box and type {NETAPP_FACTORY_RESET_CONFIRM} to record manual wipe approval.",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        cfg.setdefault("netapp", {})["last_factory_reset"] = result
        main.save_kit_config(cfg)
        context = _module_context(request)
        payload = service.page_overview(context)
        payload["factory_reset"] = result
        feedback = main.build_action_feedback(
            "NetApp factory reset blocked",
            result["error"],
            tone="danger",
            status_label="Blocked",
            details=list(plan.get("warnings") or []),
        )
        return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)

    cleared: list[str] = []
    if clear_saved_state:
        cleared = _clear_netapp_runtime_state_after_factory_reset(cfg)
    result = {
        **plan,
        "status": "manual_approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "clear_saved_state": clear_saved_state,
        "cleared_local_state": cleared,
        "note": "Manual console reset approval was recorded. The app did not choose the ONTAP boot-menu wipe option automatically.",
    }
    cfg.setdefault("netapp", {})["last_factory_reset"] = result
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.page_overview(context)
    payload["factory_reset"] = result
    feedback = main.build_action_feedback(
        "NetApp manual factory reset approval recorded",
        "Use the SP/BMC or serial console to perform the ONTAP boot-menu wipe on each controller.",
        tone="danger",
        status_label="Manual destructive action",
        outcomes=[
            "No automated ONTAP wipe was executed.",
            f"Local cached NetApp state cleared: {'yes' if clear_saved_state else 'no'}",
        ],
        details=list(plan.get("manual_steps") or []) + ([f"Cleared local state: {', '.join(cleared)}"] if cleared else []),
        links=[{"label": "NetApp boot menu docs", "href": NETAPP_FACTORY_RESET_DOC_URL}],
    )
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


@router.post("/modules/netapp/api-readiness", response_class=HTMLResponse)
async def netapp_api_readiness(request: Request):
    from app import main

    context = _module_context(request)
    payload = service.test_connection(context)
    cfg = context["cfg"]
    if payload.get("connection_test"):
        _set_bootstrap_check(cfg, "api_readiness", payload["connection_test"])
        main.save_kit_config(cfg)
    test = dict(payload.get("connection_test") or {})
    feedback = main.build_action_feedback(
        "NetApp API connection verified" if test.get("api_auth_ok") else "NetApp API connection failed",
        "The app reached ONTAP with the saved cluster management target and credentials." if test.get("api_auth_ok") else str(payload.get("error") or "The app could not authenticate to ONTAP."),
        tone="ready" if test.get("api_auth_ok") else "danger",
        status_label="Ready" if test.get("api_auth_ok") else "Needs attention",
        outcomes=[
            f"Target: {test.get('target_host') or 'Not set'}",
            f"Cluster: {test.get('cluster_name') or 'Unknown'}",
        ],
        details=list(payload.get("warnings") or []),
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


@router.post("/modules/netapp/validate-page", response_class=HTMLResponse)
async def netapp_validate_page(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.validate(context)
    feedback = main.build_action_feedback(
        "NetApp plan validated" if payload.get("ok") else "NetApp plan needs review",
        "Saved intent was compared with discovered ONTAP state." if payload.get("ok") else str(payload.get("error") or "Resolve validation findings before safe apply."),
        tone="ready" if payload.get("ok") else "pending",
        outcomes=[f"Checks: {len(list(payload.get('validation_checks') or []))}"],
        details=list(payload.get("warnings") or []),
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


@router.post("/modules/netapp/export-plan", response_class=HTMLResponse)
async def netapp_export_plan(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
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
    feedback = main.build_action_feedback(
        "NetApp plan exported",
        "Generated the NetApp plan artifacts for review and evidence.",
        tone="ready",
        outcomes=[f"{label.upper()}: {path}" for label, path in payload["export_paths"].items()],
    )
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


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
    apply_stage = dict(payload.get("apply") or {})
    feedback = main.build_action_feedback(
        "NetApp safe apply completed" if payload.get("ok") else "NetApp safe apply needs review",
        "Supported NetApp create/update/skip actions were applied or verified." if payload.get("ok") else str(payload.get("error") or "Safe apply stopped before all planned actions completed."),
        tone="ready" if payload.get("ok") else "danger",
        status_label=str(payload.get("result") or apply_stage.get("result") or ("complete" if payload.get("ok") else "Needs attention")).replace("_", " ").title(),
        outcomes=[
            f"Execution mode: {apply_stage.get('execution_mode') or 'safe_apply'}",
            f"Job: {payload.get('job_id') or 'job-netapp-safe-apply-001'}",
        ],
        details=list(payload.get("warnings") or []),
    )
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


@router.post("/modules/netapp/plan-upgrade", response_class=HTMLResponse)
async def netapp_plan_upgrade(request: Request, return_page: str = Form("netapp")):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    _, live_version_notes = _read_current_ontap_version_for_upgrade(cfg)
    plan = build_netapp_upgrade_plan(cfg, main.scan_upgrade_media())
    _store_netapp_upgrade_plan(cfg, plan)
    main.save_kit_config(cfg)
    details = list(live_version_notes) + list(plan.get("blockers") or []) + list(plan.get("warnings") or []) + list(plan.get("notes") or [])
    feedback = main.build_action_feedback(
        "ONTAP upgrade readiness checked" if plan.get("ready") else "ONTAP upgrade is not ready yet",
        "This reviews the ONTAP upgrade path only. It does not upload an image or start an upgrade.",
        tone="ready" if plan.get("ready") else "pending",
        outcomes=[
            "Action: readiness review only",
            f"Target: {plan.get('host') or 'Not set'}",
            f"Current version: {plan.get('current_version') or 'Unknown'}",
            f"Matched image: {plan.get('media_version') or 'Not found'}",
            f"Ready to run: {'yes' if plan.get('ready') else 'no'}",
        ],
        details=details,
    )
    page = str(return_page or "").strip().lower()
    if page in {"upgrade_helper", "global_settings"}:
        return main.render_page(request, cfg, active_page=page, action_feedback=feedback)
    context = _module_context(request)
    payload = service.plan(context)
    return _render_netapp_page(request, context, payload | {"upgrade_plan": plan}, saved=True, action_feedback=feedback)


@router.post("/modules/netapp/run-upgrade", response_class=HTMLResponse)
async def netapp_run_upgrade(request: Request, return_page: str = Form("netapp")):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    _read_current_ontap_version_for_upgrade(cfg)
    plan = build_netapp_upgrade_plan(cfg, main.scan_upgrade_media())
    _store_netapp_upgrade_plan(cfg, plan)
    activity = (((cfg.get("netapp") or {}).get("upgrade") or {}).get("activity") or {})
    if str(activity.get("status") or "").lower() == "running" and _reconcile_running_netapp_upgrade(cfg):
        activity = (((cfg.get("netapp") or {}).get("upgrade") or {}).get("activity") or {})
    if str(activity.get("status") or "").lower() == "running":
        feedback = main.build_action_feedback(
            "ONTAP upgrade already running",
            "The existing ONTAP upgrade activity is still active. Watch the activity panel for the latest job state.",
            tone="pending",
            outcomes=[f"Phase: {activity.get('phase') or 'unknown'}", f"Last message: {activity.get('message') or 'waiting'}"],
        )
    elif not plan.get("ready"):
        cfg.setdefault("netapp", {}).setdefault("upgrade", {})["last_result"] = {"status": "blocked", "error": "; ".join(plan.get("blockers") or [])}
        _record_netapp_upgrade_activity(
            cfg,
            {"phase": "blocked", "message": "; ".join(plan.get("blockers") or ["ONTAP upgrade prechecks are not satisfied."]), "timestamp": datetime.now(timezone.utc).isoformat()},
            status="blocked",
        )
        feedback = main.build_action_feedback(
            "ONTAP upgrade blocked",
            "The app did not start the upgrade because readiness checks did not pass.",
            tone="danger",
            outcomes=[
                f"Target: {plan.get('host') or 'Not set'}",
                f"Current version: {plan.get('current_version') or 'Unknown'}",
                f"Matched image: {plan.get('media_version') or 'Not found'}",
            ],
            details=list(plan.get("blockers") or []) + list(plan.get("warnings") or []),
        )
    else:
        cfg.setdefault("netapp", {}).setdefault("upgrade", {})["activity"] = {
            "status": "running",
            "phase": "queued",
            "message": "ONTAP upgrade worker queued.",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "events": [
                {
                    "phase": "queued",
                    "message": "ONTAP upgrade worker queued.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }
        main.save_kit_config(cfg)
        _start_netapp_upgrade_worker(cfg)
        feedback = main.build_action_feedback(
            "ONTAP upgrade started",
            "The upgrade is running in the background. The activity panel will show upload, validation, job IDs, and errors.",
            tone="pending",
            outcomes=[
                f"Target: {plan.get('host') or 'Not set'}",
                f"Current version: {plan.get('current_version') or 'Unknown'}",
                f"Target version: {plan.get('media_version') or 'Unknown'}",
            ],
        )
    page = str(return_page or "").strip().lower()
    if page in {"upgrade_helper", "global_settings"}:
        return main.render_page(request, cfg, active_page=page, action_feedback=feedback)
    context = _module_context(request)
    payload = service.plan(context)
    return _render_netapp_page(request, context, payload, saved=True, action_feedback=feedback)


@router.get("/modules/netapp/upgrade-activity", response_class=HTMLResponse)
async def netapp_upgrade_activity(request: Request):
    from app import main

    cfg = main.load_kit_config()
    _reconcile_running_netapp_upgrade(cfg)
    ontap_upgrade_status = build_ontap_upgrade_status(cfg)
    return main.templates.TemplateResponse(
        request,
        "partials/components/netapp_upgrade_activity.html",
        {"cfg": cfg, "ontap_upgrade_status": ontap_upgrade_status, "netapp_upgrade_panel": build_netapp_upgrade_panel(cfg, ontap_upgrade_status)},
    )


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
