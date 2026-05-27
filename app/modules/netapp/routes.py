from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time
from typing import Any

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.modules.netapp.schemas import NetAppModuleContext
from app.modules.netapp.service import NetAppModuleService
from app.netapp import NetAppClient, NetAppConfig
from app.netapp_upgrade import build_netapp_upgrade_plan, execute_netapp_upgrade
from app.plan_renderer import build_token_map, render_command_preview, write_plan_artifacts
from app.storage_profiles import build_protocol_profile
from app.upgrade_helper import record_upgrade_inventory
from app.vmware import build_vmware_plan

router = APIRouter()

MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
template_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

service = NetAppModuleService()

BOOTSTRAP_CHECK_TARGETS = (
    "sp_a",
    "sp_b",
    "cluster_mgmt",
    "node_01_mgmt",
    "node_02_mgmt",
    "svm_mgmt",
)
BOOTSTRAP_CHECK_LABELS = {
    "sp_a": "Controller A SP",
    "sp_b": "Controller B SP",
    "cluster_mgmt": "Cluster management",
    "node_01_mgmt": "Controller A management",
    "node_02_mgmt": "Controller B management",
    "svm_mgmt": "SVM management",
}
CONSOLE_PORT_PATTERNS = ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*")
NETAPP_FACTORY_RESET_ENV = "LAB_BUILDER_ENABLE_NETAPP_FACTORY_RESET"


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

    plan = payload.get("plan") or {}
    profile = plan.get("protocol_profile") or {}
    settings = service.settings_context(context)
    return main.render_page(
        request,
        context["cfg"],
        active_page="netapp",
        action_feedback=action_feedback
        or main.build_action_feedback(
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
    checked_at = str(result.get("checked_at") or datetime.now(timezone.utc).isoformat())
    host = str(result.get("host") or result.get("ip") or "").strip()
    cfg["netapp"]["bootstrap_checks"][key] = {
        **result,
        "label": str(result.get("label") or BOOTSTRAP_CHECK_LABELS.get(key) or key.replace("_", " ").title()),
        "target": key,
        "host": host,
        "ip": host,
        "reachable": bool(result.get("reachable")),
        "checked_at": checked_at,
        "error": str(result.get("error") or ""),
    }


def _set_vmware_check(cfg: dict[str, Any], key: str, result: dict[str, Any]) -> None:
    cfg.setdefault("netapp", {})
    cfg["netapp"].setdefault("vmware_checks", {})
    cfg["netapp"]["vmware_checks"][key] = result


def _cache_discovery_summary(cfg: dict[str, Any], discovery: dict[str, Any]) -> None:
    cfg.setdefault("netapp", {})
    version = str(discovery.get("ontap_version") or "").strip()
    read_at = str(discovery.get("read_at") or datetime.now(timezone.utc).isoformat()).strip()
    source = str(discovery.get("source") or "Live read").strip()
    cfg["netapp"]["last_discovered_ontap_version"] = version
    cfg["netapp"]["last_discovered_cluster_name"] = str(discovery.get("cluster_name") or "").strip()
    cfg["netapp"]["last_discovered_at"] = read_at
    cfg["netapp"]["last_discovered_source"] = source
    if version:
        record_upgrade_inventory(cfg, "netapp", current_version=version, source="Live NetApp discovery", raw_version=version, checked_at=read_at)


def _store_live_read(cfg: dict[str, Any], read: dict[str, Any]) -> dict[str, str]:
    cfg.setdefault("netapp", {})
    netapp_cfg = cfg["netapp"]
    host = str(read.get("target_host") or read.get("source_host") or netapp_cfg.get("host") or "").strip()
    live_read = {
        "ontap_version": str(read.get("ontap_version") or "").strip(),
        "cluster_name": str(read.get("cluster_name") or "").strip(),
        "target_host": host,
        "read_at": str(read.get("read_at") or datetime.now(timezone.utc).isoformat()).strip(),
        "source": "Live read",
    }
    netapp_cfg["last_live_read"] = live_read
    if live_read["ontap_version"]:
        netapp_cfg["last_discovered_ontap_version"] = live_read["ontap_version"]
    if live_read["cluster_name"]:
        netapp_cfg["last_discovered_cluster_name"] = live_read["cluster_name"]
    netapp_cfg["last_discovered_at"] = live_read["read_at"]
    netapp_cfg["last_discovered_source"] = "Live read"
    return live_read


def _scan_console_ports() -> dict[str, Any]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for pattern in CONSOLE_PORT_PATTERNS:
        parent = Path(pattern).parent
        name_pattern = Path(pattern).name
        if not parent.exists():
            continue
        for path in sorted(parent.glob(name_pattern)):
            path_text = str(path)
            if path_text in seen:
                continue
            seen.add(path_text)
            resolved = ""
            try:
                resolved = str(path.resolve())
            except OSError:
                resolved = ""
            candidates.append(
                {
                    "path": path_text,
                    "resolved": resolved,
                    "name": path.name,
                }
            )
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "patterns": list(CONSOLE_PORT_PATTERNS),
        "candidates": candidates,
    }


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _netapp_factory_reset_steps(targets: list[str]) -> list[str]:
    target_text = ", ".join(targets) if targets else "each controller that must be reset"
    return [
        f"Use the selected serial console to access {target_text}.",
        "Interrupt boot to reach the ONTAP boot menu.",
        "At the boot menu, run wipeconfig.",
        "Answer yes only after confirming this is the intended lab NetApp.",
        "Let the controller reboot back to factory setup state.",
        "Repeat on the partner controller when rebuilding a pair from scratch.",
    ]


def _probe_netapp_reset_console(port: str, baud: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "port": str(port or "").strip(),
        "baud": str(baud or "115200").strip() or "115200",
        "sent": ["newline only"],
        "output_excerpt": "",
        "error": "",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if not result["port"]:
        result["error"] = "Console port is not selected."
        return result
    try:
        baud_int = int(result["baud"])
    except ValueError:
        result["error"] = f"Invalid baud rate: {result['baud']}"
        return result
    try:
        import serial  # type: ignore
    except ImportError:
        result["error"] = "pyserial is not installed, so Lab Builder cannot probe the NetApp console."
        return result
    chunks: list[bytes] = []
    try:
        with serial.Serial(port=result["port"], baudrate=baud_int, timeout=0.5, write_timeout=0.5) as conn:
            conn.write(b"\r\n")
            conn.flush()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                waiting = int(getattr(conn, "in_waiting", 0) or 0)
                chunk = conn.read(waiting or 256)
                if chunk:
                    chunks.append(chunk)
                else:
                    time.sleep(0.05)
        output = b"".join(chunks).decode("utf-8", errors="replace")
        result["output_excerpt"] = "\n".join(output.splitlines()[-40:]).strip()
        result["ok"] = True
        if not result["output_excerpt"]:
            result["error"] = "Console opened, but no output was received after sending Enter."
    except Exception as exc:
        result["error"] = str(exc).splitlines()[0]
    return result


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
    read_at = datetime.now(timezone.utc).isoformat()
    if version:
        netapp_cfg["last_discovered_ontap_version"] = version
        netapp_cfg["last_discovered_at"] = read_at
        netapp_cfg["last_discovered_source"] = "Live ONTAP upgrade readiness check"
        record_upgrade_inventory(cfg, "netapp", current_version=version, source="Live ONTAP upgrade readiness check", raw_version=version, checked_at=read_at)
        notes.append(f"Current ONTAP version read from {host}: {version}")
    else:
        notes.append(f"Connected to {host}, but /api/cluster did not return an ONTAP version.")
    if cluster_name:
        netapp_cfg["last_discovered_cluster_name"] = cluster_name
        notes.append(f"Cluster: {cluster_name}")
    return version, notes


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
    cfg["netapp"]["host"] = str(form.get("netapp_host") or form.get("netapp_cluster_mgmt_ip") or cfg["netapp"].get("host") or "").strip()
    cfg["netapp"]["username"] = str(form.get("netapp_username") or cfg["netapp"].get("username") or "admin").strip()
    password = str(form.get("netapp_password") or "")
    if password:
        cfg["netapp"]["password"] = password
    console_port = str(form.get("netapp_console_port_quick") or form.get("netapp_console_port") or "").strip()
    if console_port:
        cfg["netapp"]["console_port"] = console_port
    console_baud = str(form.get("netapp_console_baud_quick") or form.get("netapp_console_baud") or "").strip()
    if console_baud:
        cfg["netapp"]["console_baud"] = console_baud
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
    desired = cfg["netapp"].setdefault("desired", {})
    scalar_fields = (
        "cluster_name",
        "svm_name",
        "data_broadcast_domain",
        "aggregate_node_01",
        "aggregate_node_02",
        "aggregate_raidtype",
        "svm_mgmt_lif",
        "svm_mgmt_ip",
        "management_subnet",
        "management_gateway",
        "management_netmask",
        "autosupport_from",
        "autosupport_to",
    )
    for key in scalar_fields:
        if key in form:
            desired[key] = str(form.get(key) or "").strip()
    int_fields = ("target_mtu", "aggregate_diskcount")
    for key in int_fields:
        if key in form:
            try:
                desired[key] = int(str(form.get(key) or "").strip())
            except ValueError:
                pass
    list_fields = ("required_nodes", "expected_ports", "autosupport_mail_hosts", "ntp_servers", "required_users", "esxi_hosts")
    for key in list_fields:
        if key in form:
            desired[key] = _lines(str(form.get(key) or ""))
    if "svm_mgmt_ip" in form:
        cfg["netapp"].setdefault("management", {})["svm_mgmt_ip"] = str(form.get("svm_mgmt_ip") or "").strip()
    desired.setdefault("nfs", {})
    desired.setdefault("iscsi", {})
    if "nfs_volume" in form:
        desired["nfs"]["volume"] = str(form.get("nfs_volume") or "").strip()
    if "nfs_export_policy" in form:
        desired["nfs"]["export_policy"] = str(form.get("nfs_export_policy") or "").strip()
    if "nfs_mount_path" in form:
        desired["nfs"]["mount_path"] = str(form.get("nfs_mount_path") or "").strip()
    if "nfs_esxi_mount_targets" in form:
        desired["nfs"]["esxi_mount_targets"] = _lines(str(form.get("nfs_esxi_mount_targets") or ""))
    if "nfs_lifs" in form:
        desired["nfs"]["lifs"] = _parse_lifs(str(form.get("nfs_lifs") or ""))
    for key in ("subnet", "subnet_cidr", "gateway", "ip_range", "portset", "igroup", "lun", "vmfs_datastore"):
        form_key = f"iscsi_{key}"
        if form_key in form:
            desired["iscsi"][key] = str(form.get(form_key) or "").strip()
    if "iscsi_iqns" in form:
        desired["iscsi"]["iqns"] = _lines(str(form.get("iscsi_iqns") or ""))
    if "iscsi_lifs" in form:
        desired["iscsi"]["lifs"] = _parse_lifs(str(form.get("iscsi_lifs") or ""))
    if "iscsi_volumes" in form:
        desired["iscsi"]["volumes"] = _parse_iscsi_volumes(str(form.get("iscsi_volumes") or ""))
    templates = cfg["netapp"].setdefault("command_templates", {})
    if "netapp_iscsi_commands" in form:
        templates["iscsi"] = str(form.get("netapp_iscsi_commands") or "")
    if "netapp_nfs_commands" in form:
        templates["nfs"] = str(form.get("netapp_nfs_commands") or "")


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
    existing_netapp = cfg.get("netapp") or {}
    existing_password = str((existing_netapp.get("password") or ""))
    existing_host = str((existing_netapp.get("host") or "")).strip()
    resolved_host = netapp_host.strip() or netapp_cluster_mgmt_ip.strip() or existing_host
    existing_desired = dict(((existing_netapp.get("desired")) or {}))
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
    protocol = str(netapp_storage_protocol or cfg.get("netapp", {}).get("storage_protocol") or "nfs").strip().lower()
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
            "host": resolved_host,
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
    netapp_console_port: str = Form(""),
    netapp_console_baud: str = Form("9600"),
    netapp_storage_protocol: str = Form(""),
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
    form = {key: value for key, value in dict(await request.form()).items()}
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
    if netapp_console_port.strip():
        cfg["netapp"]["console_port"] = netapp_console_port.strip()
    if netapp_console_baud.strip():
        cfg["netapp"]["console_baud"] = netapp_console_baud.strip()
    _apply_live_netapp_form_state(cfg, form)
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
    netapp_storage_protocol: str = Form(""),
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
    form = {key: value for key, value in dict(await request.form()).items()}
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
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.test_connection(context)
    if payload.get("connection_test"):
        cfg.setdefault("netapp", {})["last_api_check"] = payload["connection_test"]
        if payload["connection_test"].get("api_auth_ok"):
            _store_live_read(cfg, payload["connection_test"])
        main.save_kit_config(cfg)
        context = _module_context(request)
    test_result = payload.get("connection_test") or {}
    feedback = main.build_action_feedback(
        "ONTAP API reachable" if test_result.get("api_auth_ok") else "ONTAP API check failed",
        f"Target: {test_result.get('target_host') or 'not set'}",
        tone="ready" if test_result.get("api_auth_ok") else "danger",
        outcomes=[
            f"Cluster: {test_result.get('cluster_name') or 'unknown'}",
            f"ONTAP: {test_result.get('ontap_version') or 'unknown'}",
        ],
        details=([str(payload.get("error"))] if payload.get("error") else []),
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


@router.post("/modules/netapp/discover-page", response_class=HTMLResponse)
async def netapp_discover_page(
    request: Request,
    netapp_host: str = Form(""),
    netapp_username: str = Form("admin"),
    netapp_password: str = Form(""),
    netapp_storage_protocol: str = Form(""),
):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    if netapp_host or netapp_username or netapp_password or netapp_storage_protocol:
        form.setdefault("netapp_host", netapp_host)
        form.setdefault("netapp_username", netapp_username)
        form.setdefault("netapp_password", netapp_password)
        form.setdefault("netapp_storage_protocol", netapp_storage_protocol)
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service.discover(context)
    if payload.get("ok") and payload.get("discovery"):
        _cache_discovery_summary(cfg, payload["discovery"])
        _store_live_read(cfg, payload["discovery"])
        main.save_kit_config(cfg)
        context = _module_context(request)
    discovery_result = payload.get("discovery") or {}
    feedback = main.build_action_feedback(
        "Current ONTAP read complete" if payload.get("ok") and discovery_result else "Current ONTAP read failed",
        f"Target: {discovery_result.get('source_host') or (cfg.get('netapp') or {}).get('host') or 'not set'}",
        tone="ready" if payload.get("ok") and discovery_result else "danger",
        outcomes=[
            f"Cluster: {discovery_result.get('cluster_name') or 'unknown'}",
            f"ONTAP: {discovery_result.get('ontap_version') or 'unknown'}",
        ],
        details=([str(payload.get("error"))] if payload.get("error") else []),
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
    return _render_netapp_page(request, context, payload, saved=True)


@router.post("/modules/netapp/discover-console", response_class=HTMLResponse)
@router.post("/modules/netapp/check-console-ports", response_class=HTMLResponse)
async def netapp_check_console_ports(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    cfg.setdefault("netapp", {})
    scan = _scan_console_ports()
    candidates = list(scan.get("candidates") or [])
    cfg["netapp"]["console_ports"] = scan
    cfg["netapp"]["last_console_candidates"] = candidates
    cfg["netapp"]["last_console_probe_results"] = scan
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service._response(context, "check_console_ports")
    payload["console_ports"] = scan
    feedback = main.build_action_feedback(
        "Console ports checked",
        f"Found {len(candidates)} likely Ubuntu console device{'s' if len(candidates) != 1 else ''}.",
        tone="ready" if candidates else "pending",
        outcomes=[str(item.get("path")) for item in candidates[:4]],
        details=["No commands were sent to NetApp."],
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


@router.post("/modules/netapp/save-console", response_class=HTMLResponse)
async def netapp_save_console(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    cfg.setdefault("netapp", {})
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service._response(context, "save_console")
    selected = str((cfg.get("netapp") or {}).get("console_port") or "").strip()
    feedback = main.build_action_feedback(
        "Console selection saved" if selected else "Console selection not saved",
        f"Selected port: {selected or 'none'}",
        tone="ready" if selected else "pending",
        outcomes=[f"Baud: {str((cfg.get('netapp') or {}).get('console_baud') or '9600')}"],
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


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


@router.post("/modules/netapp/bootstrap-test-all", response_class=HTMLResponse)
async def netapp_bootstrap_test_all(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    main.save_kit_config(cfg)
    context = _module_context(request)
    results: dict[str, Any] = {}
    for target in BOOTSTRAP_CHECK_TARGETS:
        target_payload = service.test_bootstrap_target(context, target)
        if target_payload.get("bootstrap_test"):
            result = target_payload["bootstrap_test"]
            _set_bootstrap_check(cfg, target, result)
            results[target] = cfg["netapp"]["bootstrap_checks"][target]
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service._response(context, "bootstrap_all")
    payload["bootstrap_test_all"] = results
    payload["bootstrap_test"] = {
        "target": "all",
        "results": results,
        "reachable": any(bool(result.get("reachable")) for result in results.values()),
    }
    up_count = sum(1 for result in results.values() if result.get("reachable"))
    feedback = main.build_action_feedback(
        "NetApp IP ping complete",
        f"{up_count} of {len(results)} NetApp IPs responded.",
        tone="ready" if up_count == len(results) and results else "pending",
        outcomes=[f"{result.get('label')}: {'UP' if result.get('reachable') else 'DOWN'}" for result in results.values()],
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


@router.post("/modules/netapp/apply-ip-setup", response_class=HTMLResponse)
async def netapp_apply_ip_setup(request: Request):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    result = {
        "status": "blocked",
        "attempted_at": datetime.now(timezone.utc).isoformat(),
        "attempted_action": "apply_ip_setup",
        "message": "NetApp IP setup apply backend is not implemented yet. Saved setup values only.",
        "target": str((cfg.get("netapp") or {}).get("host") or ""),
        "attempted": ["Saved form values.", "Did not send NetApp configuration commands."],
    }
    cfg.setdefault("netapp", {})["last_ip_setup_apply"] = result
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service._response(context, "apply_ip_setup")
    payload["ip_setup_apply"] = result
    feedback = main.build_action_feedback(
        "NetApp IP setup not applied",
        result["message"],
        tone="pending",
        outcomes=["No NetApp commands were sent.", "Setup values were saved."],
    )
    return _render_netapp_page(request, context, payload, action_feedback=feedback)


@router.post("/modules/netapp/factory-reset", response_class=HTMLResponse)
async def netapp_factory_reset(request: Request, netapp_factory_reset_confirmation: str = Form("")):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    netapp_cfg = cfg.setdefault("netapp", {})
    confirmation = str(netapp_factory_reset_confirmation or "").strip()
    mode = str(form.get("netapp_factory_reset_mode") or form.get("netapp_factory_reset_mode_select") or "preflight").strip()
    if mode not in {"preflight", "live_console"}:
        mode = "preflight"
    targets = _lines(str(form.get("netapp_factory_reset_targets") or ""))
    planned_steps = _netapp_factory_reset_steps(targets)
    console_port = str(netapp_cfg.get("console_port") or "").strip()
    console_baud = str(netapp_cfg.get("console_baud") or "115200").strip() or "115200"
    live_enabled = _truthy_env(NETAPP_FACTORY_RESET_ENV)
    attempted_at = datetime.now(timezone.utc).isoformat()

    base_result: dict[str, Any] = {
        "attempted_at": attempted_at,
        "attempted_action": "factory_reset",
        "confirmation": confirmation,
        "mode": mode,
        "env_gate": NETAPP_FACTORY_RESET_ENV,
        "live_enabled": live_enabled,
        "target": str(netapp_cfg.get("host") or ""),
        "targets": targets,
        "console_port": console_port,
        "console_baud": console_baud,
        "planned_steps": planned_steps,
        "documentation_basis": "ONTAP boot-menu wipeconfig reset path",
    }

    if confirmation != "FACTORY RESET":
        result = {
            **base_result,
            "status": "refused",
            "message": "Factory reset was not attempted because confirmation did not match FACTORY RESET.",
            "attempted": ["Checked typed confirmation.", "No NetApp commands were sent."],
        }
        tone = "danger"
        title = "Factory reset not attempted"
        outcomes = ["Typed confirmation did not match.", "No NetApp commands were sent."]
    else:
        console_probe = _probe_netapp_reset_console(console_port, console_baud)
        console_ok = bool(console_probe.get("ok"))
        if mode == "preflight":
            result = {
                **base_result,
                "status": "preflight_ready" if console_ok else "preflight_blocked",
                "message": "Factory reset readiness check completed. No destructive commands were sent."
                if console_ok
                else "Factory reset readiness check could not confirm console access. No destructive commands were sent.",
                "console_probe": console_probe,
                "attempted": [
                    "Accepted exact typed confirmation.",
                    "Opened the selected console and sent Enter only." if console_ok else "Tried to open the selected console for a newline-only probe.",
                    "No wipeconfig, reset, reboot, or ONTAP configuration commands were sent.",
                ],
            }
            tone = "ready" if console_ok else "pending"
            title = "Factory reset readiness checked"
            outcomes = [
                f"Console: {'ready' if console_ok else 'not ready'}",
                "No destructive commands were sent.",
            ]
            if console_probe.get("error"):
                outcomes.append(str(console_probe.get("error")))
        elif not live_enabled:
            result = {
                **base_result,
                "status": "blocked",
                "message": f"Live NetApp factory reset is disabled. Set {NETAPP_FACTORY_RESET_ENV}=1 and restart Lab Builder before live testing destructive reset automation.",
                "console_probe": console_probe,
                "attempted": [
                    "Accepted exact typed confirmation.",
                    "Probed the selected console with Enter only." if console_ok else "Tried to probe the selected console with Enter only.",
                    f"Stopped because {NETAPP_FACTORY_RESET_ENV} is not enabled.",
                    "No wipeconfig, reset, reboot, or ONTAP configuration commands were sent.",
                ],
            }
            tone = "danger"
            title = "Factory reset blocked"
            outcomes = [f"{NETAPP_FACTORY_RESET_ENV} is not enabled.", "No destructive commands were sent."]
            if console_probe.get("error"):
                outcomes.append(str(console_probe.get("error")))
        else:
            result = {
                **base_result,
                "status": "not_implemented",
                "message": "Factory reset backend is not implemented yet for NetApp.",
                "console_probe": console_probe,
                "attempted": [
                    "Accepted exact typed confirmation.",
                    f"Verified {NETAPP_FACTORY_RESET_ENV} is enabled.",
                    "Stopped because no guarded NetApp factory-reset executor exists.",
                    "No wipeconfig, reset, reboot, or ONTAP configuration commands were sent.",
                ],
            }
            tone = "pending"
            title = "Factory reset backend unavailable"
            outcomes = ["Confirmation accepted.", "Executor is not implemented.", "No destructive commands were sent."]
            if console_probe.get("error"):
                outcomes.append(str(console_probe.get("error")))
    netapp_cfg["last_factory_reset"] = result
    main.save_kit_config(cfg)
    context = _module_context(request)
    payload = service._response(context, "factory_reset")
    payload["factory_reset"] = result
    feedback = main.build_action_feedback(title, result["message"], tone=tone, outcomes=outcomes)
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
    return _render_netapp_page(request, context, payload)


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


@router.post("/modules/netapp/plan-upgrade", response_class=HTMLResponse)
async def netapp_plan_upgrade(request: Request, return_page: str = Form("netapp")):
    from app import main

    context = _module_context(request)
    cfg = context["cfg"]
    form = {key: value for key, value in dict(await request.form()).items()}
    _apply_live_netapp_form_state(cfg, form)
    live_version_notes: list[str] = []
    current_inventory = (((cfg.get("upgrade_inventory") or {}).get("netapp")) or {})
    if not str(current_inventory.get("current_version") or "").strip():
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
    if not str((((cfg.get("upgrade_inventory") or {}).get("netapp") or {}).get("current_version")) or "").strip():
        _read_current_ontap_version_for_upgrade(cfg)
    plan = build_netapp_upgrade_plan(cfg, main.scan_upgrade_media())
    _store_netapp_upgrade_plan(cfg, plan)
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
    return main.templates.TemplateResponse(
        request,
        "partials/components/netapp_upgrade_activity.html",
        {"cfg": cfg},
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
