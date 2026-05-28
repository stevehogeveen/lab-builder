from pathlib import Path
import asyncio
import copy
from datetime import datetime
import ipaddress
import json
import os
import re
import requests
import socket
import sqlite3
import threading
import time
import yaml
from typing import Any, Callable
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.ilo import ILOClient, ILOConfig, ILOError
from app.ilo_upgrade import build_ilo_upgrade_plan, execute_ilo_upgrade
from app.upgrade_helper import build_upgrade_helper_context, build_upgrade_inventory, build_upgrade_planner_with_policies, normalize_upgrade_policies, record_upgrade_inventory, scan_upgrade_media
from app.windows import VsphereClient, VsphereConfig, WinRMClient, WinRMConfig
from app.esxi.builder import build_custom_iso
from app.esxi.models import EsxiBuildSpec
from app.debug_bundle import create_debug_bundle
from app.diagnostics import diagnostic_log_lines, diagnostic_result
from app.core.config import (
    apply_ip_plan as core_apply_ip_plan,
    build_default_ip_plan as core_build_default_ip_plan,
    build_esxi_password_policy_check as core_build_esxi_password_policy_check,
    build_ilo_discovery_targets as core_build_ilo_discovery_targets,
    build_ilo_field_errors as core_build_ilo_field_errors,
    build_ilo_input_review as core_build_ilo_input_review,
    build_legacy_offset_plan as core_build_legacy_offset_plan,
    build_policy_ilo_username as core_build_policy_ilo_username,
    build_snmp_field_errors as core_build_snmp_field_errors,
    build_snmp_input_review as core_build_snmp_input_review,
    build_standard_ilo_policy as core_build_standard_ilo_policy,
    calc_ip_plan as core_calc_ip_plan,
    count_password_classes as core_count_password_classes,
    default_config as core_default_config,
    extract_ilo_additional_users_from_form as core_extract_ilo_additional_users_from_form,
    extract_snmp_users_from_form as core_extract_snmp_users_from_form,
    has_non_printable_chars as core_has_non_printable_chars,
    ip_at_offset as core_ip_at_offset,
    merge_defaults as core_merge_defaults,
    normalize_ilo_additional_users as core_normalize_ilo_additional_users,
    normalize_ilo_config as core_normalize_ilo_config,
    normalize_ilo_hostname as core_normalize_ilo_hostname,
    normalize_ilo_policy as core_normalize_ilo_policy,
    normalize_ip_plan as core_normalize_ip_plan,
    normalize_snmp_users as core_normalize_snmp_users,
    policy_enabled as core_policy_enabled,
    sanitize_kit_name as core_sanitize_kit_name,
    standard_ilo_policy_accounts as core_standard_ilo_policy_accounts,
    standard_ilo_policy_defaults as core_standard_ilo_policy_defaults,
    standard_ilo_policy_kit_id as core_standard_ilo_policy_kit_id,
    subnet_details as core_subnet_details,
    validate_esxi_hostname as core_validate_esxi_hostname,
    validate_ilo_login_name as core_validate_ilo_login_name,
    validate_ilo_password as core_validate_ilo_password,
    validate_ip_for_subnet as core_validate_ip_for_subnet,
    validate_snmpv3_password as core_validate_snmpv3_password,
    validate_snmpv3_username as core_validate_snmpv3_username,
)
from app.core.jobs import JobStepRunner
from app.core.database import DatabaseStore, SQLiteRuntime
from app.core.registry import load_modules
from app.core.stage_registry import StageRegistry
from app.stages.ilo.adapter import HpeIloRedfishAdapter
from app.modules.ilo.routes import (
    ilo_page_handler,
    save_ilo_settings_handler,
)
from app.modules.ilo.service import default_ilo_module_service
from app.modules.configs.routes import (
    autofill_ip_plan_handler,
    download_current_kit_config_handler,
    download_ilo_config_snapshot_handler,
    download_latest_live_raw_handler,
    download_latest_live_summary_handler,
    download_report_handler,
    export_ad_hoc_ilo_inventory_handler,
    export_ilo_config_handler,
    export_ilo_inventory_handler,
    import_kit_config_handler,
    load_kit_handler,
    new_kit_handler,
    save_config_handler,
    save_global_settings_handler,
    view_current_kit_config_handler,
    view_ilo_config_snapshot_handler,
    view_latest_live_summary_handler,
    view_report_handler,
)
from app.modules.qnap.routes import save_qnap_settings_handler
from app.modules.windows.routes import (
    plan_windows_install_handler,
    probe_windows_vsphere_handler,
    probe_windows_winrm_handler,
    save_windows_settings_handler,
    upload_windows_image_handler,
)
from app.modules.execution.routes import (
    download_built_esxi_iso_handler,
    download_latest_debug_bundle_handler,
    download_run_summary_handler,
    execute_preview_scope_handler,
    execute_scope_handler,
    prepare_execute_handler,
    retry_storage_stage_handler,
    view_run_summary_handler,
)
from app.modules.esxi_config.routes import save_esxi_settings_handler
from app.modules.storage.routes import (
    approve_storage_plan_handler,
    apply_storage_layout_handler,
    clear_storage_approval_handler,
    download_storage_artifact_handler,
    plan_raid_layout_handler,
    probe_storage_capabilities_handler,
    read_current_storage_handler,
    reboot_storage_now_handler,
    repair_storage_selection_handler,
    save_storage_target_handler,
    storage_page_handler,
    view_storage_artifact_handler,
)
from app.stages.ilo.runtime import (
    build_snmp_readback_checks as ilo_build_snmp_readback_checks,
    current_snmp_matches as ilo_current_snmp_matches,
    verify_final_ilo_state as ilo_verify_final_state,
)
from app.stages.esxi.runtime import (
    build_esxi_post_config_ssh_run_action as esxi_build_post_config_ssh_run_action,
    build_esxi_post_config_actions as esxi_build_post_config_actions,
    build_esxi_post_config_preview as esxi_build_post_config_preview,
    build_esxi_install_review as esxi_build_install_review,
    build_esxi_iso_url as esxi_build_iso_url,
    build_esxi_runtime_status as esxi_build_runtime_status,
    execute_esxi_post_config_actions as esxi_execute_post_config_actions,
    ensure_esxi_post_config_policy as esxi_ensure_post_config_policy,
    detect_public_base_url as esxi_detect_public_base_url,
    detect_public_base_url_details as esxi_detect_public_base_url_details,
    discover_esxi_base_isos as esxi_discover_base_isos,
    esxi_password_policy_valid as esxi_password_valid,
    esxi_virtual_media_url_check_summary as esxi_url_check_summary,
    get_esxi_effective_values as esxi_get_effective_values,
    infer_esxi_version_from_iso_path as esxi_infer_version_from_iso_path,
    normalize_esxi_version as esxi_normalize_version,
    probe_tcp_port as esxi_probe_tcp_port,
    resolve_esxi_base_iso_path as esxi_resolve_base_iso_path,
    url_host_port as esxi_url_host_port,
    validate_esxi_base_iso as esxi_validate_base_iso,
    validate_esxi_post_config_preview as esxi_validate_post_config_preview,
    verify_esxi_virtual_media_url as esxi_verify_virtual_media_url,
)
from app.stages.ilo.plugin import create_ilo_stage
from app.stages.esxi.plugin import create_esxi_stage
from app.stages.netapp.plugin import create_netapp_stage
from app.stages.storage.plugin import create_storage_stage
from app.stages.windows.plugin import create_windows_stage
from app.stages.storage.runtime import (
    approve_storage_plan_for_cfg as storage_approve_plan_for_cfg,
    build_storage_change_summary as storage_build_change_summary,
    build_storage_execution_status as storage_build_execution_status,
    build_storage_page_readiness as storage_build_page_readiness,
    build_storage_review_context as storage_build_review_context,
    clear_storage_approval_for_cfg as storage_clear_approval_for_cfg,
    clear_storage_plan_selection_state as storage_clear_plan_selection_state,
    ensure_storage_config as storage_ensure_config,
    is_storage_drive_controller_mismatch_error as storage_is_drive_controller_mismatch_error,
    promote_final_ilo_endpoint as storage_promote_final_ilo_endpoint,
    refresh_storage_approval_from_saved_state as storage_refresh_approval_from_saved_state,
    resolve_ilo_control_host as storage_resolve_ilo_control_host,
    resolve_storage_target_credentials as storage_resolve_target_credentials,
    resolve_storage_target_host as storage_resolve_target_host,
    storage_item_display_name as storage_runtime_item_display_name,
    update_storage_latest_state as storage_update_latest_state,
)

ILO_CLIENT_BASE = ILOClient

BASE_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = BASE_DIR / "VERSION"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = BASE_DIR / "config"
KITS_DIR = CONFIG_DIR / "kits"
CURRENT_KIT_FILE = CONFIG_DIR / "current_kit.txt"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
MEDIA_DIR = BASE_DIR / "media"
FIRMWARE_UPLOAD_DIR = MEDIA_DIR / "firmware"
GENERATED_DIR = ARTIFACTS_DIR / "generated"
JOBS_DIR = ARTIFACTS_DIR / "jobs"
HISTORY_DIR = ARTIFACTS_DIR / "history"
RUNS_DIR = ARTIFACTS_DIR / "runs"
ILO_CONFIG_EXPORT_DIR = HISTORY_DIR / "ilo-configs"
CONFIG_EXPORT_DIR = HISTORY_DIR / "configs"
LIVE_ILO_CONFIG_DIR = HISTORY_DIR / "ilo-live-configs"
ILO_INVENTORY_DIR = HISTORY_DIR / "ilo-inventory"
EXPORTS_DIR = ARTIFACTS_DIR / "exports"
ILO_LIVE_EXPORT_DIR = EXPORTS_DIR / "ilo" / "live"
STORAGE_RAID_EXPORT_DIR = EXPORTS_DIR / "storage-raid"
BUILD_OUTPUT_DIR = EXPORTS_DIR / "builds"
DEBUG_BUNDLES_DIR = ARTIFACTS_DIR / "debug-bundles"
MODULES_DIR = BASE_DIR / "app" / "modules"
STORAGE_APPLY_CONFIRM_CREATE = "CREATE STORAGE"
STORAGE_APPLY_CONFIRM_WIPE = "WIPE STORAGE"
KNOWN_ISSUE_STORAGE_DRIVE_CONTROLLER_MISMATCH = "storage_drive_controller_mismatch"
APP_NAME = "Lab Builder"
ALLOWED_MEDIA_UPLOAD_SUFFIXES = {
    ".bin",
    ".fw",
    ".fwpkg",
    ".gz",
    ".img",
    ".iso",
    ".tar",
    ".tgz",
    ".zip",
}

app = FastAPI(title=APP_NAME)

STATIC_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
KITS_DIR.mkdir(parents=True, exist_ok=True)
FIRMWARE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
RUNS_DIR.mkdir(parents=True, exist_ok=True)
ILO_CONFIG_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
LIVE_ILO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
ILO_INVENTORY_DIR.mkdir(parents=True, exist_ok=True)
ILO_LIVE_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_RAID_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
BUILD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

SQLITE_RUNTIME = SQLiteRuntime(path_fn=lambda: ARTIFACTS_DIR / "lab-builder.sqlite3")
DB_STORE: DatabaseStore | None = None


def sqlite_db_path() -> Path:
    return SQLITE_RUNTIME.db_path()


def sqlite_connect() -> sqlite3.Connection:
    return SQLITE_RUNTIME.connect()


def ensure_sqlite_db() -> None:
    SQLITE_RUNTIME.ensure_ready()


def _json_text(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True)


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _db_store() -> DatabaseStore:
    global DB_STORE
    if DB_STORE is None:
        DB_STORE = DatabaseStore(
            runtime=SQLITE_RUNTIME,
            sanitize_kit_name=sanitize_kit_name,
            kit_path=kit_path,
            default_kit_name=DEFAULT_KIT_NAME,
            storage_plan_summary=storage_plan_summary,
            storage_plan_arrays=storage_plan_arrays,
        )
    return DB_STORE


def db_upsert_kit(cfg: dict[str, Any], conn: sqlite3.Connection | None = None) -> int:
    return _db_store().upsert_kit(cfg, conn=conn)


def db_find_host_id(
    conn: sqlite3.Connection,
    *,
    kit_id: int,
    system_serial: str = "",
    ilo_host: str = "",
) -> int | None:
    return _db_store().find_host_id(conn, kit_id=kit_id, system_serial=system_serial, ilo_host=ilo_host)


def db_lookup_drive_rows(
    *,
    cfg: dict[str, Any],
    system_serial: str = "",
    ilo_host: str = "",
) -> dict[str, dict[str, Any]]:
    return _db_store().lookup_drive_rows(cfg=cfg, system_serial=system_serial, ilo_host=ilo_host)


def db_record_run_history(cfg: dict[str, Any], entry: dict[str, Any]) -> None:
    _db_store().record_run_history(cfg, entry)


def db_record_known_issue_observation(
    cfg: dict[str, Any],
    *,
    fingerprint: str,
    title: str,
    description: str,
    message: str,
    discovery: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    status: str = "open",
) -> None:
    _db_store().record_known_issue_observation(
        cfg,
        fingerprint=fingerprint,
        title=title,
        description=description,
        message=message,
        discovery=discovery,
        plan=plan,
        status=status,
    )


def db_persist_storage_plan(
    cfg: dict[str, Any],
    *,
    discovery: dict[str, Any],
    discovery_paths: dict[str, Path],
    plan: dict[str, Any],
    plan_paths: dict[str, Path],
    approved: bool,
) -> None:
    _db_store().persist_storage_plan(
        cfg,
        discovery=discovery,
        discovery_paths=discovery_paths,
        plan=plan,
        plan_paths=plan_paths,
        approved=approved,
    )


def _storage_drive_bay_from_raw(item: dict[str, Any]) -> str:
    location = item.get("PhysicalLocation", {}) or {}
    part_location = location.get("PartLocation", {}) if isinstance(location, dict) else {}
    placement = part_location.get("LocationOrdinalValue") if isinstance(part_location, dict) else None
    return str(item.get("BayNumber") or item.get("Location") or placement or item.get("Id") or "")


def _storage_status_text_from_raw(item: dict[str, Any]) -> str:
    status = item.get("Status", {}) or {}
    if not isinstance(status, dict):
        return ""
    return " / ".join([bit for bit in (status.get("Health"), status.get("State")) if bit])


def ilo_inventory_components(inventory: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    controllers: list[dict[str, Any]] = []
    drives: list[dict[str, Any]] = []
    for storage in list(((inventory.get("raw") or {}).get("storage") or [])):
        storage_path = str(storage.get("@odata.id") or "").strip()
        storage_controllers = list(storage.get("StorageControllers", []) or [])
        for controller in storage_controllers:
            controllers.append(
                {
                    "path": storage_path,
                    "name": controller.get("Name") or controller.get("MemberId") or "",
                    "model": controller.get("Model", ""),
                    "firmware_version": controller.get("FirmwareVersion", ""),
                    "source": "standard_redfish_storage",
                    "status": _storage_status_text_from_raw(controller),
                }
            )
        if not storage_controllers and storage_path:
            controllers.append(
                {
                    "path": storage_path,
                    "name": storage.get("Name") or storage.get("Id") or storage_path.rsplit("/", 1)[-1],
                    "model": storage.get("Model", "") or storage.get("Description", ""),
                    "firmware_version": storage.get("FirmwareVersion", ""),
                    "source": "standard_redfish_storage",
                    "status": _storage_status_text_from_raw(storage),
                }
            )
        for drive in list(storage.get("DrivesExpanded", []) or []):
            drives.append(
                {
                    "path": str(drive.get("@odata.id") or "").strip(),
                    "controller_path": storage_path,
                    "id": drive.get("Id", ""),
                    "bay": _storage_drive_bay_from_raw(drive),
                    "name": drive.get("Name", ""),
                    "model": drive.get("Model", ""),
                    "serial_number": drive.get("SerialNumber", ""),
                    "size_gib": round((int(drive.get("CapacityBytes") or 0) / (1024 ** 3)), 2) if int(drive.get("CapacityBytes") or 0) > 0 else 0,
                    "media_type": drive.get("MediaType", ""),
                    "protocol": drive.get("Protocol", ""),
                    "status": _storage_status_text_from_raw(drive),
                    "source": "standard_redfish_storage",
                }
            )
    return controllers, drives


def db_persist_inventory(
    cfg: dict[str, Any],
    *,
    source_host: str,
    server_summary: dict[str, Any],
    manager_summary: dict[str, Any],
    controllers: list[dict[str, Any]],
    drives: list[dict[str, Any]],
    inventory_kind: str,
    raw_summary: dict[str, Any] | None = None,
) -> None:
    _db_store().persist_inventory(
        cfg,
        source_host=source_host,
        server_summary=server_summary,
        manager_summary=manager_summary,
        controllers=controllers,
        drives=drives,
        inventory_kind=inventory_kind,
        raw_summary=raw_summary,
    )


def db_persist_ilo_inventory(cfg: dict[str, Any], inventory: dict[str, Any], source_host: str = "") -> None:
    summary = inventory.get("summary", {}) or {}
    controllers, drives = ilo_inventory_components(inventory)
    for drive in drives:
        if not drive.get("controller_name"):
            drive["controller_name"] = next(
                (str(item.get("name") or item.get("model") or "") for item in controllers if str(item.get("path") or "") == str(drive.get("controller_path") or "")),
                "",
            )
    db_persist_inventory(
        cfg,
        source_host=source_host or str((summary.get("active_interface", {}) or {}).get("ipv4_addresses", [{}])[0].get("Address") or cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host") or "").strip(),
        server_summary=summary.get("system", {}) or {},
        manager_summary=summary.get("manager", {}) or {},
        controllers=controllers,
        drives=drives,
        inventory_kind="ilo_inventory",
        raw_summary=summary,
    )


def db_persist_storage_inventory(cfg: dict[str, Any], discovery: dict[str, Any], host: str = "") -> None:
    summary = discovery.get("summary", {}) or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    controllers = []
    drives = []
    for source, items in (("hpe_smart_storage", hpe.get("controllers", [])), ("standard_redfish_storage", standard.get("controllers", []))):
        for item in items or []:
            controllers.append({**item, "source": source})
    controller_name_by_path = {str(item.get("path") or ""): storage_controller_label(item) for item in controllers}
    for source, items in (("hpe_smart_storage", hpe.get("drives", [])), ("standard_redfish_storage", standard.get("drives", []))):
        for item in items or []:
            drives.append({**item, "source": source, "controller_name": controller_name_by_path.get(str(item.get("controller_path") or ""), "")})
    db_persist_inventory(
        cfg,
        source_host=host or str((discovery.get("raw", {}) or {}).get("source_host") or ""),
        server_summary=summary.get("server", {}) or {},
        manager_summary=summary.get("ilo", {}) or {},
        controllers=controllers,
        drives=drives,
        inventory_kind="storage_inventory",
        raw_summary=summary,
    )


def app_version() -> str:
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return version or "unknown"


@app.get("/health")
def health() -> dict[str, str]:
    return {"app_name": APP_NAME, "version": app_version(), "status": "ok"}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
load_modules(app, modules_dir=MODULES_DIR)

# Keep template names centralized so full-page and HTMX responses stay aligned.
PAGE_TEMPLATE = "index.html"
MAIN_CONTENT_TEMPLATE = "partials/main_content.html"
DEFAULT_IP_OFFSETS = {
    "gateway": 1,
    "switch": 2,
    "esxi": 10,
    "ilo": 11,
    "windows": 20,
    "qnap": 30,
    "iosafe": 31,
    "netapp": 45,
}
PAGE_META = {
    "dashboard": {
        "title": "Lab Builder Dashboard",
        "subtitle": "Generic readiness cockpit for the active deployment workspace.",
    },
    "global_settings": {
        "title": "Global Settings",
        "subtitle": "Shared defaults for network, inclusion, and kit-wide behavior.",
    },
    "upgrade_helper": {
        "title": "Upgrade Helper",
        "subtitle": "Compare discovered device versions against approved media before prebuild execution.",
    },
    "ovf_templates": {
        "title": "OVF Templates",
        "subtitle": "Register full local OVF/OVA template directories for VM workflows.",
    },
    "ilo": {
        "title": "iLO",
        "subtitle": "Target the controller, review readiness, and run the iLO workflow.",
    },
    "execution": {
        "title": "Execution",
        "subtitle": "Run staged actions and monitor live job progress.",
    },
    "esxi": {
        "title": "ESXi",
        "subtitle": "Review inherited network settings and local ESXi overrides.",
    },
    "windows": {
        "title": "Windows",
        "subtitle": "Review inherited network settings and local Windows overrides.",
    },
    "qnap": {
        "title": "QNAP",
        "subtitle": "Review inherited network settings and local QNAP overrides.",
    },
    "netapp": {
        "title": "NetApp",
        "subtitle": "Bootstrap, discover, validate, plan, and safely apply ONTAP setup.",
    },
    "cisco": {
        "title": "Cisco",
        "subtitle": "Cisco switch setup workspace.",
    },
    "configuration": {
        "title": "Global Settings",
        "subtitle": "Shared defaults for network, inclusion, and kit-wide behavior.",
    },
    "configs": {
        "title": "Reports",
        "subtitle": "Open logs, reports, raw output, and saved troubleshooting details in one place.",
    },
    "storage": {
        "title": "Storage setup",
        "subtitle": "Read what is on the server, build the new layout, approve it, and send it into the real run.",
    },
    "history": {
        "title": "History",
        "subtitle": "Review recent execution runs for the active kit.",
    },
}

STORAGE_APPROVAL_CONFIRM = "APPROVE STORAGE"
RUN_CENTER_STAGE_KEYS = ["ilo", "storage", "esxi", "windows", "qnap", "iosafe", "cisco_switch", "netapp"]
DEFAULT_KIT_NAME = "Kit-01"


def build_stage_registry(cfg: dict[str, Any] | None = None) -> StageRegistry:
    context_cfg = cfg or default_config()
    registry = StageRegistry([
        create_ilo_stage(),
        create_storage_stage(),
        create_esxi_stage(),
        create_netapp_stage(),
        create_windows_stage(),
    ])
    # Touch the plugins through their enabled hooks so tests can validate registry wiring
    registry.enabled({"cfg": context_cfg})
    return registry


sanitize_kit_name = core_sanitize_kit_name
normalize_ilo_hostname = core_normalize_ilo_hostname
has_non_printable_chars = core_has_non_printable_chars
count_password_classes = core_count_password_classes
validate_ilo_login_name = core_validate_ilo_login_name
validate_ilo_password = core_validate_ilo_password
validate_snmpv3_username = core_validate_snmpv3_username
validate_snmpv3_password = core_validate_snmpv3_password
build_ilo_input_review = core_build_ilo_input_review
build_snmp_input_review = core_build_snmp_input_review


def build_esxi_field_errors(cfg: dict[str, Any]) -> dict[str, list[str]]:
    values = get_esxi_effective_values(cfg)
    return {
        "hostname": list(values.get("hostname_errors") or []),
        "root_password": list(values.get("root_password_errors") or []),
    }


build_ilo_field_errors = core_build_ilo_field_errors
build_snmp_field_errors = core_build_snmp_field_errors
validate_esxi_hostname = core_validate_esxi_hostname
build_esxi_password_policy_check = core_build_esxi_password_policy_check


def normalize_page_name(name: str | None) -> str:
    page = (name or "dashboard").strip().lower()
    return page if page in PAGE_META else "dashboard"


def kit_path(kit_name: str) -> Path:
    return KITS_DIR / f"{sanitize_kit_name(kit_name)}.yml"


def list_kits():
    return sorted([p.stem for p in KITS_DIR.glob("*.yml")])


def ensure_bootstrap_kit() -> str:
    kits = list_kits()
    if kits:
        return kits[0]
    kit_name = DEFAULT_KIT_NAME
    cfg = merge_defaults({"site": {"name": kit_name}})
    path = kit_path(kit_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return kit_name


def normalize_run_center_scope(scope: str | None, selected_scopes: list[str] | None = None) -> str:
    normalized_scope = str(scope or "included").strip().lower() or "included"
    picks: list[str] = []
    includes_whole_run = False
    for item in selected_scopes or []:
        clean = str(item or "").strip().lower()
        if not clean:
            continue
        if clean == "included":
            includes_whole_run = True
            continue
        if clean in RUN_CENTER_STAGE_KEYS and clean not in picks:
            picks.append(clean)
    if picks:
        if len(picks) == 1:
            return picks[0]
        return "multi__" + "__".join(picks)
    if includes_whole_run:
        return "included"
    return normalized_scope


def run_center_scope_keys(scope: str, cfg: dict | None = None) -> list[str]:
    normalized = str(scope or "included").strip().lower()
    if normalized == "included":
        included = (cfg or {}).get("included", {}) or {}
        return [key for key in RUN_CENTER_STAGE_KEYS if included.get(key)]
    if normalized.startswith("multi__"):
        return [item for item in normalized.split("__")[1:] if item in RUN_CENTER_STAGE_KEYS]
    if normalized in RUN_CENTER_STAGE_KEYS:
        return [normalized]
    return []


def initialize_stage_statuses(scope: str, cfg: dict | None = None) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for key in run_center_scope_keys(scope, cfg):
        statuses[key] = "pending"
    return statuses


def _normalized_stage_status(value: Any) -> str:
    allowed = {"pending", "running", "completed", "failed", "skipped"}
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in allowed else "pending"


def merge_stage_statuses(existing: Any, incoming: Any) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in (existing, incoming):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            token = str(key or "").strip().lower()
            if token not in RUN_CENTER_STAGE_KEYS:
                continue
            merged[token] = _normalized_stage_status(value)
    return merged


def set_stage_status(job: dict[str, Any], token: str, status: str) -> None:
    token_clean = str(token or "").strip().lower()
    if token_clean not in RUN_CENTER_STAGE_KEYS:
        return
    statuses = merge_stage_statuses(job.get("stage_statuses"), {})
    statuses[token_clean] = _normalized_stage_status(status)
    job["stage_statuses"] = statuses


def get_current_kit_name():
    if CURRENT_KIT_FILE.exists():
        selected = sanitize_kit_name(CURRENT_KIT_FILE.read_text(encoding="utf-8").strip())
        if kit_path(selected).exists():
            return selected
    kits = list_kits()
    return kits[0] if kits else ensure_bootstrap_kit()


def set_current_kit_name(name: str):
    CURRENT_KIT_FILE.write_text(sanitize_kit_name(name), encoding="utf-8")


def load_kit_config(kit_name: str | None = None):
    name = sanitize_kit_name(kit_name or get_current_kit_name())
    path = kit_path(name)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        if not list_kits() and name == DEFAULT_KIT_NAME:
            ensure_bootstrap_kit()
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
    data = merge_defaults(data)
    try:
        data = apply_ip_plan(data)
    except Exception:
        pass
    refresh_storage_approval_from_saved_state(data)
    data["site"]["name"] = name
    return data


def save_kit_config(cfg: dict):
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    cfg["site"]["name"] = kit_name
    with open(kit_path(kit_name), "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    set_current_kit_name(kit_name)
    try:
        db_upsert_kit(cfg)
    except Exception:
        pass


def job_path(kit_name: str) -> Path:
    return JOBS_DIR / f"{sanitize_kit_name(kit_name)}_job.yml"


def run_bundle_root(kit_name: str) -> Path:
    path = RUNS_DIR / sanitize_kit_name(kit_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def scope_slug(scope: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(scope or "run").strip()).strip("-")
    return value or "run"


def write_run_bundle_files(kit_name: str, job: dict[str, Any]) -> None:
    run_bundle_dir = str(job.get("run_bundle_dir") or "").strip()
    if not run_bundle_dir:
        return

    bundle_dir = Path(run_bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    live_log_path = Path(str(job.get("run_live_log_path") or bundle_dir / "live-job.log"))
    trace_path = Path(str(job.get("run_trace_path") or bundle_dir / "trace.yml"))
    summary_path = Path(str(job.get("run_summary_path") or bundle_dir / "summary.yml"))

    logs = list(job.get("logs") or [])
    live_log_text = "\n".join(str(line) for line in logs)
    if live_log_text:
        live_log_text += "\n"
    live_log_path.write_text(live_log_text, encoding="utf-8")

    trace_payload = {
        "kit_name": sanitize_kit_name(kit_name),
        "run_id": str(job.get("run_id") or ""),
        "scope": str(job.get("scope") or ""),
        "execution_mode": str(job.get("execution_mode") or ""),
        "execution_mode_label": str(job.get("execution_mode_label") or ""),
        "status": str(job.get("status") or ""),
        "current_stage": str(job.get("current_stage") or ""),
        "progress_percent": int(job.get("progress_percent") or 0),
        "completed_steps": int(job.get("completed_steps") or 0),
        "total_steps": int(job.get("total_steps") or 0),
        "started_at": str(job.get("started_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
        "paths": {
            "job_yaml": str(job_path(kit_name)),
            "live_log": str(live_log_path),
            "config_snapshot": str(job.get("run_config_snapshot_path") or ""),
            "summary": str(summary_path),
        },
        "job_fields": {
            key: value
            for key, value in job.items()
            if key
            not in {
                "logs",
                "trace_events",
            }
        },
        "events": list(job.get("trace_events") or []),
    }
    trace_path.write_text(yaml.safe_dump(trace_payload, sort_keys=False), encoding="utf-8")

    summary_payload = {
        "kit_name": sanitize_kit_name(kit_name),
        "run_id": str(job.get("run_id") or ""),
        "scope": str(job.get("scope") or ""),
        "mode": str(job.get("execution_mode_label") or job.get("execution_mode") or ""),
        "status": str(job.get("status") or ""),
        "current_stage": str(job.get("current_stage") or ""),
        "progress_percent": int(job.get("progress_percent") or 0),
        "completed_steps": int(job.get("completed_steps") or 0),
        "total_steps": int(job.get("total_steps") or 0),
        "started_at": str(job.get("started_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
        "latest_log": str(logs[-1] if logs else ""),
        "artifacts": {
            "job_yaml": str(job_path(kit_name)),
            "live_log": str(live_log_path),
            "trace": str(trace_path),
            "config_snapshot": str(job.get("run_config_snapshot_path") or ""),
            "run_summary": str(job.get("run_summary_artifact") or ""),
            "esxi_iso_path": str(job.get("esxi_iso_path") or ""),
            "esxi_iso_url": str(job.get("esxi_iso_url") or ""),
            "esxi_trace_path": str(job.get("esxi_trace_path") or ""),
            "storage_run_directory": str(job.get("storage_run_directory") or ""),
        },
    }
    if job.get("ilo_change_summary"):
        summary_payload["ilo_change_summary"] = dict(job.get("ilo_change_summary") or {})
    if "ilo_reset_required" in job or job.get("ilo_reset_reason"):
        summary_payload["ilo_reset_decision"] = {
            "required": bool(job.get("ilo_reset_required")),
            "status": str(job.get("ilo_reset_status") or ""),
            "reason": str(job.get("ilo_reset_reason") or ""),
        }
    if "ilo_stage_finished" in job or "ilo_final_ip_verified" in job:
        summary_payload["ilo_final_state"] = {
            "stage_finished": bool(job.get("ilo_stage_finished")),
            "final_ip_verified": bool(job.get("ilo_final_ip_verified")),
            "dns_apply_status": str(job.get("dns_apply_status") or ""),
            "snmp_apply_status": str(job.get("snmp_apply_status") or ""),
            "local_account_status": str(job.get("local_account_status") or ""),
        }
    if job.get("esxi_install_values") or job.get("esxi_iso_path") or job.get("esxi_boot_override"):
        summary_payload["esxi_run_summary"] = {
            "install_values": dict(job.get("esxi_install_values") or {}),
            "artifacts": {
                "selected_esxi_version": str((job.get("esxi_install_values") or {}).get("esxi_version") or ""),
                "base_iso_path": str(job.get("esxi_base_iso_path") or ""),
                "built_iso_path": str(job.get("esxi_iso_path") or ""),
                "virtual_media_url": str(job.get("esxi_iso_url") or ""),
                "trace_path": str(job.get("esxi_trace_path") or ""),
                "builder_summary_path": str(job.get("esxi_builder_summary_path") or ""),
            },
            "builder_generation": dict(job.get("esxi_builder_generation") or {}),
            "builder_self_check": dict(job.get("esxi_builder_self_check") or {}),
            "ks_cfg": dict(job.get("esxi_ks_cfg") or {}),
            "install_target": dict(job.get("esxi_install_target") or {}),
            "virtual_media": dict(job.get("esxi_virtual_media") or {}),
            "boot_override": dict(job.get("esxi_boot_override") or {}),
            "boot_evidence": dict(job.get("esxi_boot_evidence") or {}),
            "boot_evidence_samples": list(job.get("esxi_boot_evidence_samples") or []),
            "installer_boot_observed": bool(job.get("esxi_installer_boot_observed")),
            "installer_reboot_detected": bool(job.get("esxi_installer_reboot_detected")),
            "post_install_boot_guard": dict(job.get("esxi_post_install_boot_guard") or {}),
            "power_transitions": dict(job.get("esxi_power_transitions") or {}),
            "management_network": dict(job.get("esxi_management_network") or {}),
        }
    summary_path.write_text(yaml.safe_dump(summary_payload, sort_keys=False), encoding="utf-8")


def ensure_run_bundle_for_job(kit_name: str, job: dict[str, Any]) -> dict[str, Any]:
    if job.get("run_bundle_dir"):
        return job

    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}-{scope_slug(str(job.get('scope') or 'run'))}"
    bundle_dir = run_bundle_root(kit_name) / run_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    cfg_snapshot = load_kit_config(kit_name)
    runtime = dict(cfg_snapshot.get("_runtime", {}) or {})
    if runtime:
        cfg_snapshot["_runtime"] = runtime
    config_snapshot_path = bundle_dir / "config-snapshot.yml"
    config_snapshot_path.write_text(yaml.safe_dump(cfg_snapshot, sort_keys=False), encoding="utf-8")

    job["run_id"] = run_id
    job["run_bundle_dir"] = str(bundle_dir)
    job["run_live_log_path"] = str(bundle_dir / "live-job.log")
    job["run_trace_path"] = str(bundle_dir / "trace.yml")
    job["run_summary_path"] = str(bundle_dir / "summary.yml")
    job["run_config_snapshot_path"] = str(config_snapshot_path)
    job["started_at"] = str(job.get("started_at") or time.strftime("%Y-%m-%d %H:%M:%S"))
    job["updated_at"] = str(time.strftime("%Y-%m-%d %H:%M:%S"))
    job["trace_events"] = list(job.get("trace_events") or [])
    write_run_bundle_files(kit_name, job)
    return job


def load_job(kit_name: str):
    path = job_path(kit_name)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return normalize_loaded_job_state(yaml.safe_load(f) or {})
        except yaml.YAMLError:
            # The websocket poller can race with a background writer.
            # Treat a partially-written job file as transient instead of crashing.
            return {
                "status": "Updating",
                "scope": "",
                "root_scope": "",
                "stage_statuses": {},
                "current_stage": "Refreshing live status",
                "progress_percent": 0,
                "completed_steps": 0,
                "total_steps": 0,
                "logs": ["[WARN] Live job state was mid-write. Refreshing."],
            }
    return {
        "status": "Idle",
        "scope": "",
        "root_scope": "",
        "stage_statuses": {},
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": 0,
        "logs": [],
    }


def normalize_loaded_job_state(job: dict[str, Any]) -> dict[str, Any]:
    def as_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    normalized_job = dict(job)
    normalized_job["stage_statuses"] = merge_stage_statuses(normalized_job.get("stage_statuses"), {})
    status = str(normalized_job.get("status") or "")
    completed = as_int(job.get("completed_steps"))
    total = as_int(job.get("total_steps"))
    progress = as_int(job.get("progress_percent"))
    if status == "Running" and total > 0 and completed >= total and progress >= 100:
        normalized = dict(normalized_job)
        normalized["status"] = "Completed"
        normalized["current_stage"] = str(normalized.get("current_stage") or "Finished")
        logs = list(normalized.get("logs") or [])
        if not any(str(line).startswith("[DONE]") for line in logs):
            logs.append("[DONE] Run reached all recorded steps; marking stale running state as completed.")
        normalized["logs"] = logs
        return normalized
    return normalized_job


def save_job(kit_name: str, job: dict):
    ensure_run_bundle_for_job(kit_name, job)
    job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = job_path(kit_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(job, f, sort_keys=False)
    os.replace(tmp_path, path)
    write_run_bundle_files(kit_name, job)
    if str(job.get("status") or "").strip() == "Failed" and str(job.get("execution_mode") or "").strip() == "real":
        try:
            logs = list(job.get("logs") or [])
            create_debug_bundle(
                base_dir=BASE_DIR,
                artifacts_dir=ARTIFACTS_DIR,
                config_dir=CONFIG_DIR,
                jobs_dir=JOBS_DIR,
                runs_dir=RUNS_DIR,
                generated_dir=GENERATED_DIR,
                exports_dir=EXPORTS_DIR,
                kit_name=sanitize_kit_name(kit_name),
                failure_context={
                    "job_status": job.get("status"),
                    "job_scope": job.get("scope"),
                    "current_stage": job.get("current_stage"),
                    "job_logs": logs,
                    "error_message": str(logs[-1] if logs else ""),
                    "diagnosis": job.get("diagnosis") or job.get("storage_preflight") or {},
                    "kit_config": load_kit_config(kit_name),
                },
            )
        except Exception:
            pass

def history_path(kit_name: str) -> Path:
    return HISTORY_DIR / f"{sanitize_kit_name(kit_name)}_history.yml"


def load_history(kit_name: str):
    path = history_path(kit_name)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
            return data if isinstance(data, list) else []
    return []


def save_history(kit_name: str, entries: list[dict]):
    path = history_path(kit_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(entries, f, sort_keys=False)


def history_scope_family(scope: str) -> str:
    value = str(scope or "").strip()
    if value.startswith("storage-apply") or value.startswith("storage-reboot"):
        return "storage"
    return value


def is_storage_run_scope(scope: str) -> bool:
    value = str(scope or "").strip()
    return value.startswith("storage-apply") or value.startswith("storage-reboot")


def history_status_tone(status: str) -> str:
    lowered = str(status or "").lower()
    if "supersed" in lowered:
        return "progress"
    if "complete" in lowered:
        return "ready"
    if "fail" in lowered or "block" in lowered:
        return "pending"
    return "progress"


def _write_superseded_run_summary(path_text: str, superseded_payload: dict[str, Any]) -> None:
    path_value = str(path_text or "").strip()
    if not path_value:
        return
    path = Path(path_value)
    if not path.exists():
        return
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    payload["status"] = "Superseded"
    payload["superseded"] = dict(superseded_payload)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def reconcile_superseded_history_entries(kit_name: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not entries:
        return entries
    latest = dict(entries[0] or {})
    latest_scope = str(latest.get("scope") or "")
    latest_status = str(latest.get("status") or "")
    if not is_storage_run_scope(latest_scope) or "complete" not in latest_status.lower():
        return entries

    superseded_payload = {
        "by_time": str(latest.get("time") or ""),
        "by_scope": str(latest.get("scope") or ""),
        "by_status": latest_status,
        "by_run_bundle_dir": str(latest.get("run_bundle_dir") or ""),
        "by_run_summary_path": str(latest.get("run_summary_path") or ""),
        "reason": "A newer storage run completed and replaced this earlier attempt.",
    }

    changed = False
    updated_entries: list[dict[str, Any]] = [latest]
    for item in entries[1:]:
        current = dict(item or {})
        if not is_storage_run_scope(str(current.get("scope") or "")):
            updated_entries.append(current)
            continue
        current_status = str(current.get("status") or "")
        if "complete" in current_status.lower() or "supersed" in current_status.lower():
            updated_entries.append(current)
            continue
        original_status = str(current.get("original_status") or current_status or "Recorded")
        current["original_status"] = original_status
        current["status"] = "Superseded"
        current["superseded_by"] = dict(superseded_payload)
        changed = True
        _write_superseded_run_summary(str(current.get("run_summary_path") or ""), current["superseded_by"])
        run_bundle_dir = str(current.get("run_bundle_dir") or "").strip()
        if run_bundle_dir:
            _write_superseded_run_summary(str(Path(run_bundle_dir) / "summary.yml"), current["superseded_by"])
        updated_entries.append(current)

    if changed:
        save_history(kit_name, updated_entries)
    return updated_entries


def append_history_entry(kit_name: str, entry: dict):
    history = load_history(kit_name)
    history.insert(0, entry)
    history = history[:25]
    history = reconcile_superseded_history_entries(kit_name, history)
    save_history(kit_name, history)
    try:
        db_record_run_history(load_kit_config(kit_name), entry)
    except Exception:
        pass


def build_history_config_summary(cfg: dict, scope: str) -> dict:
    if scope == "ilo":
        ilo_cfg = cfg.get("ilo", {})
        shared_dns = [x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x and x.strip()]
        snmp_cfg = cfg.get("shared_snmp", {})
        storage_review = build_storage_review_context(cfg)
        return {
            "login_ip": (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip(),
            "target_ip": (ilo_cfg.get("target_ip") or "").strip(),
            "hostname": (ilo_cfg.get("hostname") or "").strip(),
            "gateway": (ilo_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip(),
            "dns_servers": shared_dns,
            "snmp_v3_username": (snmp_cfg.get("v3_username") or "").strip(),
            "snmp_v3_auth_protocol": snmp_cfg.get("v3_auth_protocol", "SHA"),
            "snmp_v3_priv_protocol": snmp_cfg.get("v3_priv_protocol", "AES"),
            "snmp_v3_auth_secret_present": bool(snmp_cfg.get("v3_auth_password")),
            "snmp_v3_priv_secret_present": bool(snmp_cfg.get("v3_priv_password")),
            "storage_included": bool(storage_review.get("include_in_ilo_run")),
            "storage_plan_path": (storage_review.get("approval", {}) or {}).get("plan_path", ""),
        }

    return {
        "target_ip": (cfg.get("ip_plan", {}).get(scope) or "").strip(),
        "gateway": (cfg.get("ip_plan", {}).get("gateway") or "").strip(),
        "dns_servers": [x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x and x.strip()],
    }


def build_run_summary_artifacts(cfg: dict[str, Any], review: dict[str, Any], scope: str) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    storage_review = build_storage_review_context(cfg)
    ilo_cfg = cfg.get("ilo", {}) or {}
    target_server = (ilo_cfg.get("target_ip") or cfg.get("ip_plan", {}).get("ilo") or ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()

    artifacts.append(
        {
            "label": "Reports root",
            "path": str(EXPORTS_DIR),
            "summary": "Saved exports, snapshots, and bundle files live here.",
        }
    )
    if target_server:
        artifacts.append(
            {
                "label": "Target server",
                "path": target_server,
                "summary": "The iLO address used for this review.",
            }
        )
    if any(stage.get("key") == "storage" and stage.get("included") for stage in review.get("stages", [])):
        approval = storage_review.get("approval", {}) or {}
        if approval.get("discovery_raw_path"):
            artifacts.append(
                {
                    "label": "Approved storage snapshot",
                    "path": str(approval.get("discovery_raw_path")),
                    "summary": "Exact raw discovery used for the approved storage plan.",
                }
            )
        if approval.get("plan_path"):
            artifacts.append(
                {
                    "label": "Approved storage plan",
                    "path": str(approval.get("plan_path")),
                    "summary": "Exact storage plan approved for the run.",
                }
            )
    if scope in {"ilo", "included"}:
        artifacts.append(
            {
                "label": "Current kit config",
                "path": str(kit_path(cfg.get("site", {}).get("name", ""))),
                "summary": "Saved kit settings used as the source for this run review.",
            }
        )
    return artifacts


def current_build_output_dir(cfg: dict[str, Any]) -> Path:
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    path = BUILD_OUTPUT_DIR / kit_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_ilo_config_snapshot(cfg: dict) -> Path:
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    ilo_cfg = cfg.get("ilo", {})
    shared_snmp = cfg.get("shared_snmp", {})
    included = cfg.get("included", {})

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_name = sanitize_kit_name(ilo_cfg.get("hostname") or kit_name)
    snapshot_path = ILO_CONFIG_EXPORT_DIR / f"{base_name}-{timestamp}.yml"

    snapshot = {
        "kit_name": kit_name,
        "export_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ilo": {
            "current_ip": ilo_cfg.get("current_ip", ""),
            "target_ip": ilo_cfg.get("target_ip", ""),
            "hostname": ilo_cfg.get("hostname", ""),
            "username": ilo_cfg.get("username", ""),
            "subnet_mask": ilo_cfg.get("subnet_mask", ""),
            "gateway": ilo_cfg.get("gateway", ""),
            "dns_servers": [x for x in ilo_cfg.get("dns_servers", []) if x and x.strip()],
        },
        "included": {
            "ilo": included.get("ilo", False),
            "esxi": included.get("esxi", False),
            "windows": included.get("windows", False),
            "qnap": included.get("qnap", False),
            "iosafe": included.get("iosafe", False),
            "cisco_switch": included.get("cisco_switch", False),
        },
        "shared_snmp": {
            "v3_username": shared_snmp.get("v3_username", ""),
            "v3_auth_protocol": shared_snmp.get("v3_auth_protocol", ""),
            "v3_priv_protocol": shared_snmp.get("v3_priv_protocol", ""),
        },
    }

    with open(snapshot_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(snapshot, f, sort_keys=False)
    build_copy = current_build_output_dir(cfg) / f"ilo-config-{timestamp}.yml"
    build_copy.write_text(yaml.safe_dump(snapshot, sort_keys=False), encoding="utf-8")

    return snapshot_path


def export_current_kit_config_snapshot(cfg: dict) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    snapshot_path = CONFIG_EXPORT_DIR / f"{base_name}-{timestamp}.yml"

    with open(snapshot_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    build_copy = current_build_output_dir(cfg) / f"kit-config-{timestamp}.yml"
    build_copy.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    return snapshot_path


def export_live_ilo_config_snapshot(cfg: dict, live_config: dict) -> Path:
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    ilo_cfg = cfg.get("ilo", {})
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_name = sanitize_kit_name(ilo_cfg.get("hostname") or ilo_cfg.get("current_ip") or kit_name)
    snapshot_path = LIVE_ILO_CONFIG_DIR / f"{base_name}-live-{timestamp}.yml"

    payload = {
        "kit_name": kit_name,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_host": (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip(),
        "live_ilo_config": live_config,
    }

    with open(snapshot_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)

    return snapshot_path


def export_ilo_inventory_snapshot(
    cfg: dict,
    inventory: dict,
    label: str = "",
    source_host: str = "",
    target_ip: str = "",
    subnet_mask: str = "",
    gateway: str = "",
    dns_servers: list[str] | None = None,
) -> dict[str, Path]:
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    ilo_cfg = cfg.get("ilo", {})
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    effective_source_host = (source_host or ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    base_name = sanitize_kit_name(label or ilo_cfg.get("hostname") or effective_source_host or kit_name)

    export_dir = ILO_LIVE_EXPORT_DIR / base_name / timestamp
    export_dir.mkdir(parents=True, exist_ok=True)
    raw_path = export_dir / "raw.json"
    summary_path = export_dir / "summary.yml"

    summary_data = inventory.get("summary", {})
    active_interface = summary_data.get("active_interface", {})
    network_protocol = summary_data.get("network_protocol", {})
    effective_dns_servers = [
        x for x in (
            dns_servers
            or active_interface.get("static_name_servers")
            or active_interface.get("name_servers")
            or cfg.get("shared_network", {}).get("dns_servers", [])
        ) if x
    ]

    clean_summary = {
        "export_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kit_name": kit_name,
        "label": label.strip(),
        "ilo_hostname": (
            network_protocol.get("hostname")
            or active_interface.get("hostname")
            or ilo_cfg.get("hostname", "")
        ),
        "current_ilo_ip": effective_source_host,
        "target_ilo_ip": (target_ip or ilo_cfg.get("target_ip") or "").strip(),
        "subnet_mask": (subnet_mask or ilo_cfg.get("subnet_mask") or "").strip(),
        "gateway": (gateway or ilo_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip(),
        "dns_servers": effective_dns_servers,
        "ilo_firmware_version": summary_data.get("manager", {}).get("firmware", ""),
        "redfish_version": summary_data.get("service_root", {}).get("redfish_version", ""),
        "server_model": summary_data.get("system", {}).get("model", ""),
        "product_name": summary_data.get("system", {}).get("product_name", ""),
        "serial_number": summary_data.get("system", {}).get("serial_number", ""),
        "bios_version": summary_data.get("system", {}).get("bios_version", ""),
        "cpu": {
            "model": summary_data.get("processors", {}).get("model", ""),
            "count": summary_data.get("processors", {}).get("count", 0),
            "total_cores": summary_data.get("processors", {}).get("total_cores", 0),
            "total_threads": summary_data.get("processors", {}).get("total_threads", 0),
            "details": summary_data.get("processors", {}).get("items", []),
        },
        "memory": {
            "total_gib": summary_data.get("memory", {}).get("total_gib", 0),
            "dimm_count": summary_data.get("memory", {}).get("dimm_count", 0),
            "dimms": summary_data.get("memory", {}).get("dimms", []),
        },
        "ilo_network_settings": {
            "network_protocol": network_protocol,
            "active_interface": active_interface,
            "manager_ethernet_interfaces": summary_data.get("manager_ethernet_interfaces", []),
            "system_ethernet_interfaces": summary_data.get("system_ethernet_interfaces", []),
        },
        "accounts": summary_data.get("accounts", []),
        "storage": summary_data.get("storage", {}),
    }

    raw_payload = {
        "export_timestamp": clean_summary["export_timestamp"],
        "kit_name": kit_name,
        "label": label.strip(),
        "source_host": clean_summary["current_ilo_ip"],
        "inventory": inventory,
    }

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_payload, f, indent=2, sort_keys=False)

    with open(summary_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(clean_summary, f, sort_keys=False)

    return {
        "raw": raw_path,
        "summary": summary_path,
    }


def latest_live_inventory_export() -> dict[str, Path] | None:
    latest_dir = None
    latest_mtime = -1.0

    for path in ILO_LIVE_EXPORT_DIR.glob("*/*"):
        if not path.is_dir():
            continue
        summary_path = path / "summary.yml"
        raw_path = path / "raw.json"
        if not summary_path.exists() or not raw_path.exists():
            continue
        mtime = max(summary_path.stat().st_mtime, raw_path.stat().st_mtime)
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_dir = path

    if not latest_dir:
        return None

    return {
        "directory": latest_dir,
        "summary": latest_dir / "summary.yml",
        "raw": latest_dir / "raw.json",
    }


def live_inventory_export_metadata(export_paths: dict[str, Path]) -> dict[str, str]:
    summary_path = export_paths.get("summary")
    raw_path = export_paths.get("raw")
    export_dir = export_paths.get("directory") or (summary_path.parent if summary_path else None)
    label = export_dir.parent.name if export_dir else ""
    host = ""

    if summary_path and summary_path.exists():
        try:
            summary = yaml.safe_load(summary_path.read_text(encoding="utf-8")) or {}
            label = (summary.get("label") or label or "").strip()
            host = (summary.get("current_ilo_ip") or "").strip()
        except Exception:
            host = ""

    return {
        "summary_path": str(summary_path) if summary_path else "",
        "raw_path": str(raw_path) if raw_path else "",
        "label": label,
        "host": host,
    }


def live_inventory_download_headers(latest: dict[str, Path]) -> dict[str, str]:
    metadata = live_inventory_export_metadata(latest)
    return {
        "X-Live-Inventory-Summary-Path": metadata["summary_path"],
        "X-Live-Inventory-Raw-Path": metadata["raw_path"],
        "X-Live-Inventory-Label": metadata["label"],
        "X-Live-Inventory-Host": metadata["host"],
    }


def storage_discovery_export_payloads(cfg: dict, discovery: dict, host: str = "") -> tuple[dict, dict]:
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    summary = discovery.get("summary", {})
    summary_payload = {
        "export_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kit_name": kit_name,
        "source_host": host,
        **summary,
    }
    raw_payload = {
        "export_timestamp": summary_payload["export_timestamp"],
        "kit_name": kit_name,
        "source_host": host,
        "discovery": discovery,
    }
    return summary_payload, raw_payload


def write_storage_discovery_snapshot_files(summary_path: Path, raw_path: Path, cfg: dict, discovery: dict, host: str = "") -> None:
    summary_payload, raw_payload = storage_discovery_export_payloads(cfg, discovery, host=host)
    with open(summary_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(summary_payload, f, sort_keys=False)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_payload, f, indent=2, sort_keys=False)


def export_storage_discovery_snapshot(cfg: dict, discovery: dict, host: str = "") -> dict[str, Path]:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    summary = discovery.get("summary", {})
    server = summary.get("server", {})
    base_name = sanitize_kit_name(
        server.get("serial_number")
        or server.get("model")
        or cfg.get("ilo", {}).get("hostname")
        or host
        or kit_name
    )

    export_dir = STORAGE_RAID_EXPORT_DIR / base_name / timestamp
    export_dir.mkdir(parents=True, exist_ok=True)
    summary_path = export_dir / "summary.yml"
    raw_path = export_dir / "raw.json"
    write_storage_discovery_snapshot_files(summary_path, raw_path, cfg, discovery, host=host)

    return {
        "directory": export_dir,
        "summary": summary_path,
        "raw": raw_path,
    }


def load_storage_discovery_artifact(raw_path_text: str, expected_host: str = "") -> tuple[dict, dict[str, Path]]:
    raw_path = Path(raw_path_text).expanduser().resolve()
    export_root = STORAGE_RAID_EXPORT_DIR.resolve()
    if not raw_path.is_relative_to(export_root):
        raise ValueError("Storage discovery artifact must be under the storage export folder.")
    if raw_path.name != "raw.json" or not raw_path.exists():
        raise ValueError("Storage discovery raw artifact was not found.")

    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    source_host = (payload.get("source_host") or "").strip()
    if expected_host and source_host and source_host != expected_host:
        raise ValueError(f"Storage discovery host mismatch: artifact is for {source_host}, current kit points to {expected_host}.")

    discovery = payload.get("discovery", {})
    discovery.setdefault("raw", {})["source_host"] = source_host
    return discovery, {
        "directory": raw_path.parent,
        "summary": raw_path.with_name("summary.yml"),
        "raw": raw_path,
    }

def load_storage_plan_artifact(plan_path_text: str) -> tuple[dict, dict[str, Path]]:
    plan_path = Path(plan_path_text).expanduser().resolve()
    export_root = STORAGE_RAID_EXPORT_DIR.resolve()
    if not plan_path.is_relative_to(export_root):
        raise ValueError("RAID plan artifact must be under the storage export folder.")
    if not plan_path.exists() or plan_path.name not in {"raid-plan.yml"} or plan_path.suffix.lower() not in (".yml", ".yaml"):
        raise ValueError("RAID plan artifact was not found.")

    payload = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
    plan = payload.get("plan", {}) or {}
    source_discovery = payload.get("source_discovery", {}) or {}
    if source_discovery and not plan.get("source_discovery"):
        plan["source_discovery"] = source_discovery
    return plan, {
        "directory": plan_path.parent,
        "plan": plan_path,
    }


def restore_storage_page_state(
    discovery_raw_path: str = "",
    raid_plan_path: str = "",
    expected_host: str = "",
) -> tuple[dict | None, dict[str, Path] | None, dict | None, dict[str, Path] | None]:
    discovery = None
    discovery_paths = None
    plan = None
    plan_paths = None

    if discovery_raw_path:
        discovery, discovery_paths = load_storage_discovery_artifact(discovery_raw_path, expected_host=expected_host)

    if raid_plan_path:
        plan, plan_paths = load_storage_plan_artifact(raid_plan_path)
        if discovery_paths:
            plan_raw = str((plan.get("source_discovery", {}) or {}).get("raw", "") or "").strip()
            if plan_raw and Path(plan_raw).expanduser().resolve() != discovery_paths["raw"].resolve():
                raise ValueError("RAID plan artifact does not belong to the currently displayed storage discovery.")

    return discovery, discovery_paths, plan, plan_paths


def storage_fingerprint_payload(summary: dict[str, Any]) -> dict[str, Any]:
    hpe = summary.get("hpe_smart_storage", {}) or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    server = summary.get("server", {}) or {}
    return {
        "server": {
            "model": server.get("model", ""),
            "serial_number": server.get("serial_number", ""),
            "generation": server.get("generation", ""),
        },
        "controllers": sorted(
            [
                {
                    "path": item.get("path", ""),
                    "name": item.get("name", ""),
                    "model": item.get("model", ""),
                    "firmware_version": storage_firmware_display(item.get("firmware_version", "")),
                }
                for item in list(hpe.get("controllers", []) or []) + list(standard.get("controllers", []) or [])
            ],
            key=lambda item: (item.get("path", ""), item.get("name", ""), item.get("model", "")),
        ),
        "volumes": sorted(
            [
                {
                    "path": item.get("path", ""),
                    "name": item.get("name", ""),
                    "raid_type": item.get("raid_type", ""),
                    "capacity_gib": item.get("capacity_gib", ""),
                }
                for item in list(hpe.get("volumes", []) or []) + list(standard.get("volumes", []) or [])
            ],
            key=lambda item: (item.get("path", ""), item.get("name", "")),
        ),
        "drives": sorted(
            [
                {
                    "path": item.get("path", ""),
                    "bay": str(item.get("bay", "")),
                    "model": item.get("model", ""),
                    "serial_number": item.get("serial_number", ""),
                    "size_gib": item.get("size_gib", ""),
                    "status": item.get("status", ""),
                    "smart_storage_location": item.get("smart_storage_location", ""),
                }
                for item in list(hpe.get("drives", []) or []) + list(standard.get("drives", []) or [])
            ],
            key=lambda item: (item.get("path", ""), item.get("bay", ""), item.get("serial_number", "")),
        ),
    }


def storage_discovery_fingerprint(discovery: dict[str, Any]) -> str:
    summary = discovery.get("summary", discovery) or {}
    payload = storage_fingerprint_payload(summary)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def storage_item_display_name(item: dict[str, Any]) -> str:
    return storage_runtime_item_display_name(item)


def ensure_storage_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return storage_ensure_config(cfg)


def storage_array_summary_entries(plan: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for array in storage_plan_arrays(plan):
        drives = list(array.get("drives") or [])
        entries.append(
            {
                "role": str(array.get("role") or ""),
                "name": str(array.get("name") or ""),
                "controller": str(array.get("controller_name") or array.get("controller_path") or ""),
                "controller_path": str(array.get("controller_path") or ""),
                "raid_level": str(array.get("raid_level") or array.get("raid") or ""),
                "bays": plan_drive_bays(drives),
                "drive_count": len(drives),
                "selected_drive_paths": [str(drive.get("path") or drive.get("drive_path") or "") for drive in drives if str(drive.get("path") or drive.get("drive_path") or "").strip()],
                "selected_drive_serials": [str(drive.get("serial_number") or drive.get("serial") or "") for drive in drives if str(drive.get("serial_number") or drive.get("serial") or "").strip()],
                "drives": [
                    {
                        "path": str(drive.get("path") or drive.get("drive_path") or ""),
                        "serial_number": str(drive.get("serial_number") or drive.get("serial") or ""),
                        "bay": str(drive.get("bay") or ""),
                        "model": str(drive.get("model") or drive.get("name") or ""),
                        "controller_path": str(drive.get("controller_path") or ""),
                    }
                    for drive in drives
                ],
            }
        )
    return entries


def storage_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    arrays = storage_array_summary_entries(plan)
    hot_spare = (plan.get("hot_spare", {}) or {}).get("drive", {}) or {}
    controllers = [entry.get("controller") for entry in arrays if entry.get("controller")]
    return {
        "controller": " | ".join(dict.fromkeys([str(item) for item in controllers if str(item).strip()])),
        "mode": (plan.get("apply_readiness", {}) or {}).get("next_action", ""),
        "arrays": arrays,
        "hot_spare": {
            "bay": str(hot_spare.get("bay") or ""),
            "path": str(hot_spare.get("path") or hot_spare.get("drive_path") or ""),
            "serial_number": str(hot_spare.get("serial_number") or hot_spare.get("serial") or ""),
            "controller": str(hot_spare.get("controller_name") or hot_spare.get("controller_path") or ""),
        },
        "os_bays": next((entry.get("bays") for entry in arrays if entry.get("role") == "os"), ""),
        "data_bays": next((entry.get("bays") for entry in arrays if entry.get("role") == "data"), ""),
        "spare_bay": str(hot_spare.get("bay") or ""),
    }


def refresh_storage_approval_from_saved_state(cfg: dict[str, Any]) -> None:
    storage_refresh_approval_from_saved_state(cfg)


def update_storage_latest_state(
    cfg: dict[str, Any],
    discovery: dict[str, Any] | None = None,
    discovery_paths: dict[str, Path] | None = None,
    plan: dict[str, Any] | None = None,
    plan_paths: dict[str, Path] | None = None,
) -> None:
    storage_update_latest_state(
        cfg,
        discovery=discovery,
        discovery_paths=discovery_paths,
        plan=plan,
        plan_paths=plan_paths,
        storage_discovery_fingerprint_fn=storage_discovery_fingerprint,
        storage_plan_summary_fn=storage_plan_summary,
    )


def clear_storage_plan_selection_state(cfg: dict[str, Any]) -> None:
    storage_clear_plan_selection_state(cfg)


def is_storage_drive_controller_mismatch_error(message: str) -> bool:
    return storage_is_drive_controller_mismatch_error(message)


def approve_storage_plan_for_cfg(
    cfg: dict[str, Any],
    discovery: dict[str, Any],
    discovery_paths: dict[str, Path],
    plan: dict[str, Any],
    plan_paths: dict[str, Path],
    include_in_ilo_run: bool,
) -> None:
    storage_approve_plan_for_cfg(
        cfg,
        discovery=discovery,
        discovery_paths=discovery_paths,
        plan=plan,
        plan_paths=plan_paths,
        include_in_ilo_run=include_in_ilo_run,
        storage_discovery_fingerprint_fn=storage_discovery_fingerprint,
        storage_plan_summary_fn=storage_plan_summary,
        update_storage_latest_state_fn=update_storage_latest_state,
        db_persist_storage_plan_fn=db_persist_storage_plan,
    )


def clear_storage_approval_for_cfg(cfg: dict[str, Any]) -> None:
    storage_clear_approval_for_cfg(cfg)


def build_storage_review_context(cfg: dict[str, Any]) -> dict[str, Any]:
    return storage_build_review_context(cfg)


def resolve_storage_target_host(cfg: dict[str, Any]) -> dict[str, Any]:
    return storage_resolve_target_host(
        cfg,
        ensure_storage_config_fn=ensure_storage_config,
    )


def resolve_ilo_control_host(cfg: dict[str, Any]) -> str:
    return storage_resolve_ilo_control_host(cfg)


def promote_final_ilo_endpoint(cfg: dict[str, Any], final_ip: str | None = None) -> dict[str, Any]:
    return storage_promote_final_ilo_endpoint(
        cfg,
        resolve_ilo_control_host_fn=resolve_ilo_control_host,
        final_ip=final_ip,
    )


def propagate_active_ilo_endpoint(cfg: dict[str, Any], active_ip: str | None = None) -> dict[str, Any]:
    cfg.setdefault("ilo", {})
    endpoint = str(
        active_ip
        or cfg["ilo"].get("current_ip")
        or cfg["ilo"].get("host")
        or cfg["ilo"].get("target_ip")
        or cfg.get("ip_plan", {}).get("ilo")
        or ""
    ).strip()
    if not endpoint:
        return cfg
    cfg["ilo"]["current_ip"] = endpoint
    cfg["ilo"]["host"] = endpoint
    cfg.setdefault("storage", {})
    storage_override = str(cfg["storage"].get("target_host_override") or "").strip()
    if storage_override and storage_override != endpoint:
        cfg["storage"].setdefault("previous_target_host_overrides", [])
        previous = list(cfg["storage"].get("previous_target_host_overrides") or [])
        if storage_override not in previous:
            previous.append(storage_override)
        cfg["storage"]["previous_target_host_overrides"] = previous[-5:]
        cfg["storage"]["target_host_override"] = ""
    return cfg


def resolve_storage_target_credentials(cfg: dict[str, Any]) -> dict[str, Any]:
    return storage_resolve_target_credentials(
        cfg,
        ensure_storage_config_fn=ensure_storage_config,
    )


def build_storage_execution_status(cfg: dict[str, Any]) -> dict[str, Any]:
    return storage_build_execution_status(cfg)


def component_inclusion_status(cfg: dict[str, Any], component: str) -> dict[str, str]:
    enabled = bool(cfg.get("included", {}).get(component))
    return {
        "label": "Included" if enabled else "Not included",
        "tone": "ready" if enabled else "pending",
    }


def component_source_label(value: str, inherited: str) -> tuple[str, str]:
    if value and inherited and value != inherited:
        return value, "Local override"
    if value:
        return value, "Using global default"
    return inherited, "Using global default"


WORKFLOW_STATE_UI = {
    "not_started": {"label": "Not started", "tone": "pending"},
    "discovered": {"label": "Current state captured", "tone": "progress"},
    "planned": {"label": "Plan ready", "tone": "progress"},
    "approved": {"label": "Approved", "tone": "ready"},
    "running": {"label": "Running", "tone": "progress"},
    "waiting_for_restart": {"label": "Waiting for restart", "tone": "pending"},
    "validating": {"label": "Validating", "tone": "progress"},
    "complete": {"label": "Complete", "tone": "ready"},
    "failed": {"label": "Needs attention", "tone": "pending"},
    "stale": {"label": "Needs review again", "tone": "pending"},
}


def workflow_state_ui(state: str) -> dict[str, str]:
    return WORKFLOW_STATE_UI.get(state, {"label": state.replace("_", " ").title(), "tone": "pending"})


def latest_history_entry_for_scope(history: list[dict[str, Any]], scopes: list[str]) -> dict[str, Any] | None:
    scope_set = set(scopes)
    for item in history:
        item_scope = str(item.get("scope") or "")
        if item_scope in scope_set or any(item_scope.startswith(f"{scope}:") for scope in scope_set):
            return item
    return None


def append_activity_event(
    kit_name: str,
    event: str,
    *,
    workflow: str,
    state: str = "complete",
    summary: str = "",
    target: str = "",
    details: list[str] | None = None,
):
    entry = {
        "kind": "event",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": workflow,
        "status": workflow_state_ui(state)["label"],
        "state": state,
        "event": event,
        "summary": summary,
        "target": target,
        "details": details or [],
        "progress_percent": 100 if state == "complete" else 0,
    }
    append_history_entry(kit_name, entry)


def build_activity_feed(history: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    feed = []
    for item in history[:limit]:
        if item.get("kind") == "event":
            ui = workflow_state_ui(item.get("state", "complete"))
            feed.append(
                {
                    "time": item.get("time", ""),
                    "title": item.get("event", item.get("scope", "")).replace("_", " ").title(),
                    "summary": item.get("summary", ""),
                    "target": item.get("target", ""),
                    "label": ui["label"],
                    "tone": ui["tone"],
                    "details": item.get("details", []),
                }
            )
            continue
        status = str(item.get("status", ""))
        tone = history_status_tone(status)
        feed.append(
            {
                "time": item.get("time", ""),
                "title": f"{str(item.get('scope', '')).replace('_', ' ').title()} run",
                "summary": status or "Run recorded",
                "target": item.get("config_summary", {}).get("target_ip") or item.get("config_summary", {}).get("login_ip") or "",
                "label": status or "Run recorded",
                "tone": tone,
                "details": item.get("issues", [])[:3] if item.get("issues") else [],
            }
        )
    return feed


def build_history_display_entries(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    display_entries: list[dict[str, Any]] = []
    for item in history:
        if item.get("kind") == "event":
            state = str(item.get("state") or "complete")
            ui = workflow_state_ui(state)
            display_entries.append(
                {
                    **item,
                    "display_title": str(item.get("event") or item.get("scope") or "Event").replace("_", " ").title(),
                    "display_target": str(item.get("target") or "").strip(),
                    "display_status": ui["label"],
                    "display_tone": ui["tone"],
                    "display_summary": str(item.get("summary") or "Recorded event."),
                    "display_highlights": list(item.get("details") or [])[:4],
                }
            )
            continue

        scope = str(item.get("scope") or "")
        status = str(item.get("status") or "Recorded")
        current_stage = str(item.get("current_stage") or item.get("summary") or "Run recorded")
        config_summary = item.get("config_summary", {}) or {}
        tone = history_status_tone(status)
        display_entries.append(
            {
                **item,
                "display_title": f"{scope.replace('_', ' ').title()} run",
                "display_target": build_bundle_target_summary(config_summary),
                "display_status": status,
                "display_tone": tone,
                "display_summary": build_bundle_human_summary(scope, status, current_stage, config_summary),
                "display_highlights": build_bundle_highlights(scope, status, current_stage, config_summary),
            }
        )
    return display_entries


def build_dashboard_job_status(history: list[dict[str, Any]]) -> dict[str, Any]:
    workflow_defs = [
        ("ilo", "iLO", ["ilo"]),
        ("storage", "Storage", ["storage-apply", "storage-reboot"]),
        ("esxi", "ESXi", ["esxi"]),
        ("windows", "Windows", ["windows"]),
        ("qnap", "QNAP", ["qnap"]),
        ("netapp", "NetApp", ["netapp"]),
    ]
    passed: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    for _, label, scopes in workflow_defs:
        latest = latest_history_entry_for_scope(history, scopes) or {}
        if not latest:
            continue
        status = str(latest.get("status") or "")
        item = {
            "name": label,
            "status": status or "Run recorded",
            "time": str(latest.get("time") or ""),
            "run_summary_path": str(latest.get("run_summary_path") or ""),
        }
        lowered = status.lower()
        if "supersed" in lowered:
            continue
        if "fail" in lowered:
            failed.append(item)
        elif "complete" in lowered:
            passed.append(item)
    return {
        "passed": passed,
        "failed": failed,
    }


def latest_storage_discovery_export(cfg: dict[str, Any], allow_global_fallback: bool = False) -> dict[str, Path] | None:
    storage_cfg = cfg.get("storage", {}) or {}
    latest_raw = str(storage_cfg.get("latest_discovery_raw_path") or "").strip()
    if latest_raw:
        raw_path = Path(latest_raw).expanduser().resolve()
        if raw_path.exists():
            return {
                "directory": raw_path.parent,
                "summary": raw_path.with_name("summary.yml"),
                "raw": raw_path,
            }

    if not allow_global_fallback:
        return None

    latest_raw_path = None
    latest_mtime = -1.0
    for raw_path in STORAGE_RAID_EXPORT_DIR.glob("*/*/raw.json"):
        if not raw_path.is_file():
            continue
        try:
            mtime = raw_path.stat().st_mtime
        except OSError:
            continue
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_raw_path = raw_path

    if not latest_raw_path:
        return None

    return {
        "directory": latest_raw_path.parent,
        "summary": latest_raw_path.with_name("summary.yml"),
        "raw": latest_raw_path,
    }


def load_latest_live_inventory_snapshot() -> dict[str, Any]:
    export_paths = latest_live_inventory_export()
    if not export_paths:
        return {}

    snapshot: dict[str, Any] = {"paths": export_paths}
    try:
        snapshot["summary"] = yaml.safe_load(export_paths["summary"].read_text(encoding="utf-8")) or {}
    except Exception:
        snapshot["summary"] = {}
    try:
        snapshot["raw"] = json.loads(export_paths["raw"].read_text(encoding="utf-8"))
    except Exception:
        snapshot["raw"] = {}
    return snapshot


def load_latest_live_inventory_snapshot_for_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    snapshot = load_latest_live_inventory_snapshot()
    if not snapshot:
        return {}

    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    summary = snapshot.get("summary", {}) or {}
    raw = snapshot.get("raw", {}) or {}
    snapshot_kit = str(summary.get("kit_name") or raw.get("kit_name") or "").strip()
    if snapshot_kit and snapshot_kit == kit_name:
        return snapshot

    current_host = str((cfg.get("ilo", {}) or {}).get("current_ip") or (cfg.get("ilo", {}) or {}).get("host") or "").strip()
    snapshot_host = str(summary.get("current_ilo_ip") or raw.get("source_host") or "").strip()
    if current_host and snapshot_host and current_host == snapshot_host:
        return snapshot

    return {}


def sync_ilo_upgrade_inventory_from_latest_live(cfg: dict[str, Any]) -> bool:
    live_snapshot = load_latest_live_inventory_snapshot_for_cfg(cfg)
    live_summary = dict(live_snapshot.get("summary") or {})
    live_raw_summary = (((live_snapshot.get("raw") or {}).get("inventory") or {}).get("summary") or {})
    live_manager = dict((live_raw_summary.get("manager") or {}))
    ilo_version = _first_non_empty(live_summary.get("ilo_firmware_version"), live_manager.get("firmware"))
    manager_model = str(live_manager.get("model") or "").strip()
    if not ilo_version:
        return False

    existing_ilo_inventory = dict((build_upgrade_inventory(cfg).get("ilo") or {}))
    existing_ilo_source = str(existing_ilo_inventory.get("source") or "").strip().lower()
    preserve_verified_ilo = existing_ilo_source == "post-upgrade ilo verification" and str(existing_ilo_inventory.get("current_version") or "").strip()
    if preserve_verified_ilo:
        return False

    record_upgrade_inventory(
        cfg,
        "ilo",
        current_version=ilo_version,
        source="Latest live iLO inventory",
        raw_version=ilo_version,
        manager_model=manager_model,
    )
    return True


def load_latest_storage_discovery_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    export_paths = latest_storage_discovery_export(cfg, allow_global_fallback=False)
    if not export_paths:
        return {}

    snapshot: dict[str, Any] = {"paths": export_paths}
    try:
        snapshot["summary"] = yaml.safe_load(export_paths["summary"].read_text(encoding="utf-8")) or {}
    except Exception:
        snapshot["summary"] = {}
    try:
        snapshot["raw"] = json.loads(export_paths["raw"].read_text(encoding="utf-8"))
    except Exception:
        snapshot["raw"] = {}
    return snapshot


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def build_hardware_identity(cfg: dict[str, Any]) -> dict[str, Any]:
    live_snapshot = load_latest_live_inventory_snapshot_for_cfg(cfg)
    storage_snapshot = load_latest_storage_discovery_snapshot(cfg)

    live_summary = live_snapshot.get("summary", {}) or {}
    live_raw_summary = (((live_snapshot.get("raw", {}) or {}).get("inventory", {}) or {}).get("summary", {}) or {})
    storage_summary = storage_snapshot.get("summary", {}) or {}
    storage_server = (storage_summary.get("server", {}) or {})
    storage_ilo = (storage_summary.get("ilo", {}) or {})

    live_storage = live_summary.get("storage", {}) or {}
    standard_storage = storage_summary.get("standard_redfish_storage", {}) or {}
    hpe_storage = storage_summary.get("hpe_smart_storage", {}) or {}
    controllers = (
        standard_storage.get("controllers")
        or hpe_storage.get("controllers")
        or live_storage.get("controllers")
        or []
    )
    controller = controllers[0] if controllers else {}

    server_model = _first_non_empty(live_summary.get("server_model"), storage_server.get("model"))
    product_name = _first_non_empty(live_summary.get("product_name"), storage_server.get("product_name"))
    server_label = _first_non_empty(server_model, product_name)
    generation = _first_non_empty(storage_server.get("generation"))
    serial_number = _first_non_empty(live_summary.get("serial_number"), storage_server.get("serial_number"))
    manager_model = _first_non_empty(
        (live_raw_summary.get("manager", {}) or {}).get("model"),
        storage_ilo.get("version"),
        storage_ilo.get("model"),
    )
    ilo_firmware = _first_non_empty(live_summary.get("ilo_firmware_version"), storage_ilo.get("firmware"))
    current_ilo_ip = _first_non_empty(
        live_summary.get("current_ilo_ip"),
        cfg.get("ilo", {}).get("current_ip"),
        cfg.get("ilo", {}).get("host"),
    )
    target_ilo_ip = _first_non_empty(
        live_summary.get("target_ilo_ip"),
        cfg.get("ilo", {}).get("target_ip"),
        cfg.get("ip_plan", {}).get("ilo"),
    )
    controller_name = " / ".join(
        [
            value
            for value in [
                str(controller.get("name") or "").strip(),
                str(controller.get("model") or "").strip(),
            ]
            if value
        ]
    )
    controller_fw = storage_firmware_display(controller.get("firmware_version"))

    sources: list[str] = []
    if live_summary:
        sources.append("Latest live iLO inventory")
    if storage_summary:
        sources.append("Latest storage discovery")
    if not sources:
        sources.append("Saved kit values")

    discovered = bool(server_label or serial_number or manager_model or controller_name)
    fields = [
        {"label": "Server", "value": server_label or "Not identified yet"},
        {"label": "Serial", "value": serial_number or "Not identified yet"},
        {"label": "Generation", "value": generation or "Not identified yet"},
        {"label": "iLO", "value": manager_model or "Not identified yet"},
        {"label": "iLO firmware", "value": ilo_firmware or "Not identified yet"},
        {"label": "Current iLO IP", "value": current_ilo_ip or "Not set"},
        {"label": "Final iLO IP", "value": target_ilo_ip or "Not set"},
        {"label": "Controller", "value": controller_name or "Not identified yet"},
        {"label": "Controller firmware", "value": controller_fw or "Not identified yet"},
        {"label": "Source", "value": " + ".join(sources)},
    ]
    return {
        "discovered": discovered,
        "title": server_label or "Hardware identity not captured yet",
        "subtitle": (
            f"Serial {serial_number}"
            if serial_number
            else "Read current iLO or current storage to capture the live server identity."
        ),
        "fields": fields,
    }


def build_ilo_advanced_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    live_snapshot = load_latest_live_inventory_snapshot_for_cfg(cfg)
    if not live_snapshot:
        return {
            "available": False,
            "detected_label": "No live iLO read yet",
            "subtitle": "Read current iLO to load version-specific advanced options.",
            "summary_fields": [],
            "areas": [],
            "raw_key_groups": [],
        }

    summary = live_snapshot.get("summary", {}) or {}
    inventory = ((live_snapshot.get("raw", {}) or {}).get("inventory", {}) or {})
    inventory_summary = inventory.get("summary", {}) or {}
    inventory_raw = inventory.get("raw", {}) or {}
    manager_summary = inventory_summary.get("manager", {}) or {}
    manager_raw = inventory_raw.get("manager", {}) or {}
    network_protocol = inventory_summary.get("network_protocol", {}) or {}
    active_interface = inventory_summary.get("active_interface", {}) or {}
    accounts = inventory_summary.get("accounts", []) or []
    virtual_media = inventory_raw.get("virtual_media", []) or []
    capability_dump = inventory_raw.get("capability_dump", {}) or {}
    manager_interfaces = capability_dump.get("ethernet_interfaces") or []
    snmp_obj = capability_dump.get("snmp_object") or network_protocol.get("snmp") or {}
    snmp_keys = list(capability_dump.get("snmp_keys") or (sorted(snmp_obj.keys()) if isinstance(snmp_obj, dict) else []))
    network_protocol_keys = list(capability_dump.get("network_protocol_keys") or [])
    network_protocol_oem_hpe_keys = list(capability_dump.get("network_protocol_oem_hpe_keys") or [])
    reset_target = str((((manager_raw.get("Actions") or {}).get("#Manager.Reset") or {}).get("target")) or "").strip()

    def bool_label(value: bool) -> str:
        return "Available" if value else "Not exposed"

    def add_area(
        name: str,
        available: bool,
        summary_text: str,
        items: list[dict[str, str]],
        detail_rows: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "status_label": "Detected" if available else "Not detected",
            "status_tone": "ready" if available else "progress",
            "summary": summary_text,
            "items": [item for item in items if str(item.get("value") or "").strip()],
            "detail_rows": [item for item in (detail_rows or []) if str(item.get("value") or "").strip()],
        }

    static_ipv4 = active_interface.get("ipv4_static_addresses") or active_interface.get("ipv4_addresses") or []
    current_ips = ", ".join([str(item.get("Address") or "").strip() for item in static_ipv4 if str(item.get("Address") or "").strip()]) or "Not identified"
    dns_keys_present = any(
        key in network_protocol_keys
        for key in ("NameServers", "StaticNameServers")
    ) or any(
        key in (manager_interfaces[0].get("keys", []) if manager_interfaces else [])
        for key in ("NameServers", "StaticNameServers")
    )
    dns_values = ", ".join(active_interface.get("static_name_servers") or active_interface.get("name_servers") or []) or "Not identified"
    snmp_v3_keys = [key for key in snmp_keys if "v3" in key.lower()]
    snmp_legacy_keys = [key for key in snmp_keys if "v1" in key.lower() or "v2" in key.lower()]
    vm_insert = sum(1 for item in virtual_media if ((item.get("Actions") or {}).get("#VirtualMedia.InsertMedia") or {}).get("target"))
    vm_eject = sum(1 for item in virtual_media if ((item.get("Actions") or {}).get("#VirtualMedia.EjectMedia") or {}).get("target"))
    account_names = ", ".join([str(item.get("username") or "").strip() for item in accounts if str(item.get("username") or "").strip()][:5]) or "Not identified"
    vlan_supported = any("VLAN" in (item.get("keys") or []) or item.get("vlan") for item in manager_interfaces)
    ipv6_supported = any("IPv6Addresses" in (item.get("keys") or []) for item in manager_interfaces)

    areas = [
        add_area(
            "Network identity and DNS",
            bool(network_protocol or active_interface),
            f"Hostname, address, gateway, and DNS options detected on the active iLO interface at {current_ips}.",
            [
                {"label": "Current hostname", "value": str(active_interface.get("hostname") or network_protocol.get("hostname") or "Not identified")},
                {"label": "Current IP addresses", "value": current_ips},
                {"label": "Current DNS servers", "value": dns_values},
                {"label": "Static DNS keys", "value": bool_label(dns_keys_present)},
                {"label": "ManagerNetworkProtocol path", "value": str(capability_dump.get("network_protocol_path") or manager_summary.get("path") or "")},
            ],
            detail_rows=[
                {"label": "Current FQDN", "value": str(active_interface.get("fqdn") or network_protocol.get("fqdn") or "Not identified")},
                {"label": "IPv4 static entries", "value": str(len(static_ipv4))},
                {"label": "DHCPv4", "value": str((active_interface.get("dhcpv4") or {}).get("DHCPEnabled", "Not identified"))},
                {"label": "Network protocol keys", "value": ", ".join(network_protocol_keys) or "Not detected"},
                {"label": "Network OEM HPE keys", "value": ", ".join(network_protocol_oem_hpe_keys) or "Not detected"},
            ],
        ),
        add_area(
            "SNMP and alerts",
            bool(snmp_obj or snmp_keys),
            "These are the SNMP controls the last live iLO read exposed for this firmware version.",
            [
                {"label": "Detected SNMP keys", "value": ", ".join(snmp_keys) or "Not exposed"},
                {"label": "SNMPv3 controls", "value": bool_label(bool(snmp_v3_keys))},
                {"label": "Legacy SNMP controls", "value": bool_label(bool(snmp_legacy_keys))},
                {"label": "Current SNMPv3 username", "value": str(snmp_obj.get("SNMPv3Username") or snmp_obj.get("SNMPv3UserName") or snmp_obj.get("Username") or snmp_obj.get("UserName") or "Not identified")},
            ],
            detail_rows=[
                {"label": "SNMP protocol enabled", "value": str(snmp_obj.get("ProtocolEnabled", "Not identified"))},
                {"label": "SNMPv1 enabled", "value": str(snmp_obj.get("SNMPv1Enabled", "Not identified"))},
                {"label": "SNMPv2c enabled", "value": str(snmp_obj.get("SNMPv2cEnabled", "Not identified"))},
                {"label": "SNMPv3 enabled", "value": str(snmp_obj.get("SNMPv3Enabled", "Not identified"))},
                {"label": "SNMP object path", "value": str(capability_dump.get("network_protocol_path") or "")},
            ],
        ),
        add_area(
            "Local accounts and roles",
            bool(accounts),
            f"The last live iLO read found {len(accounts)} local account(s) on this controller.",
            [
                {"label": "Detected local accounts", "value": str(len(accounts))},
                {"label": "Account names", "value": account_names},
                {"label": "Account collection", "value": bool_label(bool(inventory_raw.get("account_service")))},
            ],
            detail_rows=[
                {"label": f"Account {index + 1}", "value": f"{item.get('username') or 'Unknown'} | role {item.get('role') or 'Unknown'}"}
                for index, item in enumerate(accounts[:8])
            ],
        ),
        add_area(
            "Manager reset",
            bool(reset_target),
            "This shows whether the current iLO firmware exposed a direct Manager.Reset action.",
            [
                {"label": "Manager reset action", "value": reset_target or "Not exposed"},
            ],
            detail_rows=[
                {"label": "Detected manager model", "value": str(manager_summary.get("model") or manager_raw.get("Model") or "Not identified")},
                {"label": "Detected manager firmware", "value": str(manager_summary.get("firmware") or manager_raw.get("FirmwareVersion") or "Not identified")},
            ],
        ),
        add_area(
            "Virtual media and remote install",
            bool(virtual_media),
            f"The last live iLO read found {len(virtual_media)} virtual media device(s).",
            [
                {"label": "Virtual media devices", "value": str(len(virtual_media)) if virtual_media else "None detected"},
                {"label": "Insert media actions", "value": str(vm_insert) if virtual_media else ""},
                {"label": "Eject media actions", "value": str(vm_eject) if virtual_media else ""},
            ],
            detail_rows=[
                {
                    "label": f"Virtual media {index + 1}",
                    "value": (
                        f"{item.get('Name') or item.get('Id') or 'Unknown'}"
                        f" | inserted={item.get('Inserted', 'Not identified')}"
                        f" | image={item.get('Image') or '(none)'}"
                    ),
                }
                for index, item in enumerate(virtual_media[:8])
            ],
        ),
        add_area(
            "Interface features",
            bool(manager_interfaces),
            f"The last live iLO read found {len(manager_interfaces)} manager interface capability set(s).",
            [
                {"label": "Manager interfaces", "value": str(len(manager_interfaces)) if manager_interfaces else ""},
                {"label": "VLAN controls", "value": bool_label(vlan_supported)},
                {"label": "IPv6 controls", "value": bool_label(ipv6_supported)},
                {"label": "Network OEM HPE keys", "value": ", ".join(network_protocol_oem_hpe_keys) or "None detected"},
            ],
            detail_rows=[
                {
                    "label": f"Interface {index + 1}",
                    "value": (
                        f"{item.get('path') or 'Unknown path'}"
                        f" | host={item.get('host_name') or 'Unknown'}"
                        f" | link={item.get('link_status') or 'Unknown'}"
                    ),
                }
                for index, item in enumerate(manager_interfaces[:8])
            ] + [
                {
                    "label": f"Interface {index + 1} keys",
                    "value": ", ".join(item.get("keys") or []) or "Not detected",
                }
                for index, item in enumerate(manager_interfaces[:4])
            ],
        ),
    ]

    raw_key_groups: list[dict[str, Any]] = []
    if network_protocol_keys:
        raw_key_groups.append({"label": "ManagerNetworkProtocol keys", "values": network_protocol_keys})
    if snmp_keys:
        raw_key_groups.append({"label": "SNMP keys", "values": snmp_keys})
    if network_protocol_oem_hpe_keys:
        raw_key_groups.append({"label": "ManagerNetworkProtocol OEM HPE keys", "values": network_protocol_oem_hpe_keys})
    for index, item in enumerate(manager_interfaces, start=1):
        interface_keys = list(item.get("keys") or [])
        if interface_keys:
            raw_key_groups.append(
                {
                    "label": f"Manager interface {index} keys",
                    "values": interface_keys,
                }
            )
        interface_oem_keys = list(item.get("oem_hpe_keys") or [])
        if interface_oem_keys:
            raw_key_groups.append(
                {
                    "label": f"Manager interface {index} OEM HPE keys",
                    "values": interface_oem_keys,
                }
            )

    detected_label = _first_non_empty(
        manager_summary.get("model"),
        inventory_raw.get("manager", {}).get("Model"),
        summary.get("ilo_hostname"),
    ) or "Detected iLO"

    summary_fields = [
        {"label": "Detected iLO", "value": detected_label},
        {"label": "Firmware", "value": _first_non_empty(summary.get("ilo_firmware_version"), manager_summary.get("firmware"), inventory_raw.get("manager", {}).get("FirmwareVersion")) or "Not identified"},
        {"label": "Source", "value": _first_non_empty(summary.get("current_ilo_ip"), cfg.get("ilo", {}).get("current_ip"), cfg.get("ilo", {}).get("host")) or "Not set"},
        {"label": "Last live read", "value": str(summary.get("export_timestamp") or "Not recorded")},
    ]

    return {
        "available": True,
        "detected_label": detected_label,
        "subtitle": "Detected from the latest live iLO read for this app. Read current iLO again after a firmware change to refresh this list.",
        "summary_fields": summary_fields,
        "areas": areas,
        "raw_key_groups": raw_key_groups,
    }


def build_live_job_story(job: dict[str, Any]) -> dict[str, str]:
    status = str(job.get("status") or "Idle")
    current_step = str(job.get("current_stage") or "").strip()
    logs = [str(line).strip() for line in (job.get("logs") or []) if str(line).strip()]
    completed_steps = int(job.get("completed_steps") or 0)
    total_steps = int(job.get("total_steps") or 0)
    last_confirmed = logs[-1] if logs else ""

    if status in {"Idle", ""}:
        return {
            "headline": "Nothing is running right now.",
            "summary": "Pick a run below and review it before you start.",
            "last_confirmed": "No live job has started yet.",
            "waiting_on": "The app is waiting for you to start a preview or a real run.",
        }

    if status in {"Complete", "Completed", "Preview complete"}:
        return {
            "headline": current_step or "Run finished.",
            "summary": "The run reached its terminal state. Use the live log and bundle to confirm the final verification details.",
            "last_confirmed": last_confirmed or "The final log lines were recorded.",
            "waiting_on": "Nothing else is running. Open the run summary if you need the full trace.",
        }

    if total_steps > 0 and completed_steps >= total_steps:
        waiting_on = "The app is waiting for the final verification and wrap-up before it can mark the run complete."
    elif total_steps > 0:
        waiting_on = f"The app is working toward step {completed_steps + 1} of {total_steps}."
    else:
        waiting_on = "The app is still working through the live run."

    return {
        "headline": current_step or "Run in progress",
        "summary": "The live log below is the source of truth while the run is active.",
        "last_confirmed": last_confirmed or "No confirmed log line has been written yet.",
        "waiting_on": waiting_on,
    }


def build_run_checklist(job: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, str]]:
    labels = {
        "ilo": "iLO",
        "storage": "Storage",
        "esxi": "ESXi",
        "windows": "Windows",
        "qnap": "QNAP",
        "iosafe": "ioSafe",
        "cisco_switch": "Cisco Switch",
        "netapp": "NetApp",
    }
    scope = str(job.get("root_scope") or job.get("scope") or "").strip().lower()
    selected = run_center_scope_keys(scope, cfg) if scope else []
    statuses = merge_stage_statuses(job.get("stage_statuses"), {})
    overall = str(job.get("status") or "").strip().lower()
    checklist: list[dict[str, str]] = []
    for token in selected:
        state = _normalized_stage_status(statuses.get(token))
        if overall in {"completed", "complete", "preview complete"} and state == "pending":
            state = "completed"
        checklist.append({"token": token, "label": labels.get(token, token), "state": state})
    return checklist


def build_live_stage_cards(job: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    scope = str(job.get("scope") or "")
    stage_text = str(job.get("current_stage") or "").lower()

    def tone_for(value: str, *, positive: set[str] | None = None, negative: set[str] | None = None) -> str:
        value_norm = str(value or "").strip().lower()
        positive = positive or {"verified", "completed", "already correct", "not required", "requested", "mounted"}
        negative = negative or {"failed", "mismatch", "blocked", "skipped"}
        if value_norm in positive:
            return "ready"
        if value_norm in negative:
            return "pending"
        return "progress"

    def add_card(name: str, status_label: str, status_tone: str, summary: str, detail_rows: list[dict[str, str]]) -> None:
        if not detail_rows:
            return
        cards.append(
            {
                "name": name,
                "status_label": status_label,
                "status_tone": status_tone,
                "summary": summary,
                "detail_rows": detail_rows,
            }
        )

    dns_status = str(job.get("dns_apply_status") or "").strip()
    snmp_status = str(job.get("snmp_apply_status") or "").strip()
    reset_status = str(job.get("ilo_reset_status") or "").strip()
    local_account_status = str(job.get("local_account_status") or "").strip()
    final_ip_verified = bool(job.get("ilo_final_ip_verified"))
    ilo_rows: list[dict[str, str]] = []
    if dns_status:
        ilo_rows.append({"label": "DNS status", "value": dns_status})
    if snmp_status:
        ilo_rows.append({"label": "SNMP status", "value": snmp_status})
    if local_account_status:
        ilo_rows.append({"label": "Local accounts", "value": local_account_status})
    if reset_status:
        ilo_rows.append({"label": "iLO reset", "value": reset_status})
    if "ilo_final_ip_verified" in job:
        ilo_rows.append({"label": "Final iLO IP", "value": "Verified" if final_ip_verified else "Waiting for verification"})
    if job.get("target_ip"):
        ilo_rows.append({"label": "Target iLO IP", "value": str(job.get("target_ip"))})
    if job.get("login_ip"):
        ilo_rows.append({"label": "Login iLO IP", "value": str(job.get("login_ip"))})
    if ilo_rows or "ilo" in scope or "ilo" in stage_text:
        if reset_status:
            ilo_status = reset_status
        elif snmp_status:
            ilo_status = snmp_status
        elif dns_status:
            ilo_status = dns_status
        else:
            ilo_status = "In progress" if job.get("status") not in {"Idle", "Complete", "Completed", "Preview complete"} else "Waiting"
        ilo_summary_parts = []
        if dns_status:
            ilo_summary_parts.append(f"DNS {dns_status.lower()}")
        if snmp_status:
            ilo_summary_parts.append(f"SNMP {snmp_status.lower()}")
        if reset_status:
            ilo_summary_parts.append(f"iLO reset {reset_status.lower()}")
        if final_ip_verified:
            ilo_summary_parts.append("final IP verified")
        ilo_summary = ", ".join(ilo_summary_parts) if ilo_summary_parts else "iLO checks will appear here while the run is active."
        add_card("iLO", ilo_status, tone_for(ilo_status), ilo_summary, ilo_rows)

    storage_reboot_status = str(job.get("storage_server_reboot_status") or "").strip()
    storage_rows: list[dict[str, str]] = []
    if job.get("apply_path"):
        storage_rows.append({"label": "Apply artifact", "value": str(job.get("apply_path"))})
    if job.get("workflow_state"):
        storage_rows.append({"label": "Workflow state", "value": str(job.get("workflow_state"))})
    if "reboot_required" in job:
        storage_rows.append({"label": "Server reboot required", "value": "Yes" if bool(job.get("reboot_required")) else "No"})
    if storage_reboot_status:
        storage_rows.append({"label": "Server reboot status", "value": storage_reboot_status})
    if storage_rows or "storage" in scope or "storage" in stage_text:
        storage_status = storage_reboot_status or workflow_state_ui(str(job.get("workflow_state") or "idle")).get("label", "Waiting")
        storage_summary = (
            f"Storage workflow is at {str(job.get('workflow_state') or 'idle').replace('_', ' ')}."
            if job.get("workflow_state")
            else "Storage progress and restart checks will appear here while the run is active."
        )
        add_card("Storage", storage_status, tone_for(storage_status), storage_summary, storage_rows)

    esxi_rows: list[dict[str, str]] = []
    if job.get("esxi_iso_path"):
        esxi_rows.append({"label": "Built ISO path", "value": str(job.get("esxi_iso_path"))})
    if job.get("esxi_iso_url"):
        esxi_rows.append({"label": "Virtual media URL", "value": str(job.get("esxi_iso_url"))})
    if job.get("esxi_expected_ip"):
        esxi_rows.append({"label": "Expected ESXi IP", "value": str(job.get("esxi_expected_ip"))})
    if job.get("esxi_trace_path"):
        esxi_rows.append({"label": "Technical trace", "value": str(job.get("esxi_trace_path"))})
    boot_override = job.get("esxi_boot_override") or {}
    if isinstance(boot_override, dict):
        after_enabled = str(boot_override.get("after_enabled") or "")
        after_target = str(boot_override.get("after_target") or "")
        matched = boot_override.get("matched")
        if after_enabled or after_target or matched is not None:
            boot_value = f"{after_enabled or '(unknown)'} / {after_target or '(unknown)'}"
            if matched is False:
                boot_value += " (best effort only)"
            esxi_rows.append({"label": "Boot override readback", "value": boot_value})
    mgmt_network = job.get("esxi_management_network") or {}
    if isinstance(mgmt_network, dict) and mgmt_network:
        reachability = str(mgmt_network.get("status") or mgmt_network.get("result") or "")
        if reachability:
            esxi_rows.append({"label": "Management reachability", "value": reachability})
    if esxi_rows or "esxi" in scope or "esxi" in stage_text:
        esxi_status = "In progress"
        if isinstance(mgmt_network, dict):
            reachability = str(mgmt_network.get("status") or mgmt_network.get("result") or "").strip()
            if reachability:
                esxi_status = reachability
        elif job.get("esxi_iso_path"):
            esxi_status = "Built"
        esxi_summary_parts = []
        if job.get("esxi_iso_path"):
            esxi_summary_parts.append("custom ISO built")
        if job.get("esxi_iso_url"):
            esxi_summary_parts.append("virtual media prepared")
        if isinstance(mgmt_network, dict):
            reachability = str(mgmt_network.get("status") or mgmt_network.get("result") or "").strip()
            if reachability:
                esxi_summary_parts.append(f"management reachability {reachability.lower()}")
        esxi_summary = ", ".join(esxi_summary_parts) if esxi_summary_parts else "ESXi build and boot checks will appear here while the run is active."
        add_card("ESXi", esxi_status, tone_for(esxi_status, positive={"reachable", "connected", "built", "verified", "mounted"}, negative={"failed", "timeout", "unreachable"}), esxi_summary, esxi_rows)

    return cards


def latest_scope_receipt(cfg: dict[str, Any], history: list[dict[str, Any]], scopes: list[str]) -> dict[str, Any] | None:
    scope_set = set(scopes)
    for bundle in build_run_bundles(cfg, history):
        if str(bundle.get("scope") or "") in scope_set:
            return {
                **bundle,
                "cta_label": "Open log" if bundle.get("run_summary_path") else "",
            }
    return None
def build_storage_page_readiness(
    storage_review: dict[str, Any],
    storage_target: dict[str, Any],
    storage_credentials: dict[str, Any],
    storage_execution_status: dict[str, Any],
    storage_export_paths: dict[str, Path] | None,
) -> list[dict[str, str]]:
    return storage_build_page_readiness(
        storage_review,
        storage_target,
        storage_credentials,
        storage_execution_status,
        storage_export_paths,
    )


def build_storage_change_summary(storage_review: dict[str, Any], storage_plan: dict[str, Any] | None) -> list[dict[str, str]]:
    return storage_build_change_summary(
        storage_review,
        storage_plan,
        storage_plan_summary_fn=storage_plan_summary,
        raid_label_fn=raid_label,
    )


def build_esxi_install_target_review(cfg: dict[str, Any]) -> dict[str, Any]:
    storage_cfg = cfg.get("storage", {}) or {}
    approval = storage_cfg.get("approval", {}) or {}
    plan_summary = approval.get("plan_summary") or storage_cfg.get("latest_plan_summary") or {}
    plan_path = str(approval.get("plan_path") or storage_cfg.get("latest_plan_path") or "").strip()
    os_drives: list[dict[str, Any]] = []
    data_drives: list[dict[str, Any]] = []
    if plan_path:
        try:
            path = Path(plan_path)
            if path.exists():
                plan = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                arrays = storage_array_summary_entries((plan.get("plan") or plan) if isinstance(plan, dict) else {})
                os_drives = next((list(item.get("drives") or []) for item in arrays if item.get("role") == "os"), [])
                data_drives = next((list(item.get("drives") or []) for item in arrays if item.get("role") == "data"), [])
        except Exception:
            os_drives = []
            data_drives = []

    arrays = list(plan_summary.get("arrays") or [])
    os_array = next((item for item in arrays if item.get("role") == "os"), {})
    data_array = next((item for item in arrays if item.get("role") == "data"), {})
    os_bays = str(os_array.get("bays") or ", ".join(str(d.get("bay") or "") for d in os_drives if d.get("bay")) or "unknown")
    data_bays = str(data_array.get("bays") or ", ".join(str(d.get("bay") or "") for d in data_drives if d.get("bay")) or "unknown")
    approved = str(approval.get("state") or storage_cfg.get("state") or "").lower() == "approved"
    preferred_target = (
        f"Approved OS RAID logical drive ({os_array.get('controller') or plan_summary.get('controller') or 'selected controller'}, bays {os_bays})"
        if approved and os_bays != "unknown"
        else "First eligible local disk selected by the ESXi installer"
    )
    safety_note = (
        "KS.CFG currently uses install --firstdisk --overwritevmfs. Lab Builder cannot yet bind a Redfish "
        "logical drive to an ESXi disk identifier, so verify the approved OS RAID logical drive is presented "
        "before any data RAID volume."
    )
    return {
        "mode": "firstdisk",
        "kickstart_line": "install --firstdisk --overwritevmfs",
        "preferred_target": preferred_target,
        "storage_approval_state": approval.get("state") or storage_cfg.get("state") or "not approved",
        "os_bays": os_bays,
        "data_bays": data_bays,
        "os_drive_count": len(os_drives),
        "data_drive_count": len(data_drives),
        "plan_path": plan_path,
        "safety_note": safety_note,
        "recommended_fix": "If the installer still misses the OS volume, set controller/boot order so the OS RAID logical drive is first, then rerun ESXi only.",
    }


def build_esxi_page_review(cfg: dict[str, Any]) -> dict[str, Any]:
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    login_ip = resolve_ilo_control_host(cfg)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_name = f"esxi-{stamp}"
    output_iso = EXPORTS_DIR / "esxi-isos" / kit_name / output_name / f"{output_name}.iso"
    values = get_esxi_effective_values(cfg)
    post_config_preview = build_esxi_post_config_preview(cfg)
    post_config_validation = validate_esxi_post_config_preview(post_config_preview)
    review = {
        "run_stamp": stamp,
        "source_label": "Saved kit values from the ESXi Setup page and shared defaults",
        "manual_defaults_label": "Manual test script defaults are not used by Run Center",
        "version": values["version"],
        "base_iso_choices": discover_esxi_base_isos(),
        "selected_base_iso_path": values["base_iso_path"],
        "base_iso_path": "",
        "base_iso_ready": False,
        "base_iso_error": "",
        "output_iso_path": str(output_iso),
        "virtual_media_url": build_esxi_iso_url(cfg, output_iso, login_ip),
        "hostname": values["hostname"],
        "hostname_valid": values["hostname_valid"],
        "hostname_errors": values["hostname_errors"],
        "hostname_warnings": values["hostname_warnings"],
        "management_ip": values["management_ip"],
        "subnet_mask": values["subnet_mask"],
        "gateway": values["gateway"],
        "dns_servers": values["dns_servers"],
        "root_password_saved": bool(values["root_password"]),
        "root_password_policy_valid": values["root_password_policy_valid"],
        "root_password_errors": values["root_password_errors"],
        "root_password_notes": values["root_password_notes"],
        "vlan_id": values["vlan_id"],
        "ntp_server": values["ntp_server"],
        "enable_ssh": values["enable_ssh"],
        "disable_ipv6": values["disable_ipv6"],
        "debug_no_reboot": values["debug_no_reboot"],
        "install_target": build_esxi_install_target_review(cfg),
        "missing_fields": list(values["missing_fields"]),
        "validation_errors": list(values["validation_errors"]),
        "validation_notes": list(values["validation_notes"]),
        "post_config_preview": post_config_preview,
        "post_config_validation": post_config_validation,
    }
    try:
        base_iso = resolve_esxi_base_iso_path(cfg)
        review["base_iso_path"] = str(base_iso)
        validate_esxi_base_iso(base_iso, values["version"])
        review["base_iso_ready"] = True
    except Exception as e:
        review["base_iso_error"] = str(e).splitlines()[0]
    return review


def build_esxi_advanced_profile(cfg: dict[str, Any], review: dict[str, Any] | None = None) -> dict[str, Any]:
    review = dict(review or build_esxi_page_review(cfg) or {})

    def yes_no(value: bool) -> str:
        return "Yes" if value else "No"

    missing_fields = list(review.get("missing_fields") or [])
    summary_fields = [
        {"label": "Source", "value": str(review.get("source_label") or "Saved kit values")},
        {"label": "Base ISO ready", "value": "Ready" if review.get("base_iso_ready") else "Missing"},
        {"label": "Root password saved", "value": yes_no(bool(review.get("root_password_saved")))},
        {"label": "Run stamp", "value": str(review.get("run_stamp") or "Not set")},
    ]

    def area(name: str, status_label: str, tone: str, summary: str, items: list[dict[str, str]], detail_rows: list[dict[str, str]] | None = None) -> dict[str, Any]:
        return {
            "name": name,
            "status_label": status_label,
            "status_tone": tone,
            "summary": summary,
            "items": [item for item in items if str(item.get("value") or "").strip()],
            "detail_rows": [item for item in (detail_rows or []) if str(item.get("value") or "").strip()],
        }

    areas = [
        area(
            "Installer identity",
            "Ready" if review.get("hostname") and review.get("management_ip") and review.get("hostname_valid") else "Needs values",
            "ready" if review.get("hostname") and review.get("management_ip") and review.get("hostname_valid") else "pending",
            "These are the main ESXi identity values the app will bake into the installer.",
            [
                {"label": "Hostname", "value": str(review.get("hostname") or "Not set")},
                {"label": "Management IP", "value": str(review.get("management_ip") or "Not set")},
                {"label": "Subnet mask", "value": str(review.get("subnet_mask") or "Not set")},
                {"label": "Gateway", "value": str(review.get("gateway") or "Not set")},
                {"label": "DNS servers", "value": ", ".join(review.get("dns_servers") or []) or "Not set"},
            ],
            detail_rows=[
                {"label": "Hostname checks", "value": "; ".join(review.get("hostname_errors") or []) or "Passed"},
                {"label": "Hostname notes", "value": "; ".join(review.get("hostname_warnings") or []) or "None"},
            ],
        ),
        area(
            "Install sign-in",
            "Ready" if review.get("root_password_saved") and review.get("root_password_policy_valid") else "Needs values",
            "ready" if review.get("root_password_saved") and review.get("root_password_policy_valid") else "pending",
            "These are the service and policy choices the builder will include for this ESXi install.",
            [
                {"label": "Root password saved", "value": yes_no(bool(review.get("root_password_saved")))},
                {"label": "Password policy looks valid", "value": yes_no(bool(review.get("root_password_policy_valid"))) if review.get("root_password_saved") else "Not checked"},
                {"label": "VLAN ID", "value": str(review.get("vlan_id") or "Not set")},
                {"label": "NTP server", "value": str(review.get("ntp_server") or "Not set")},
                {"label": "Enable SSH", "value": yes_no(bool(review.get("enable_ssh")))},
                {"label": "Disable IPv6", "value": yes_no(bool(review.get("disable_ipv6")))},
                {"label": "Debug no reboot", "value": yes_no(bool(review.get("debug_no_reboot")))},
            ],
            detail_rows=[
                {"label": "Password checks", "value": "; ".join(review.get("root_password_errors") or []) or "Passed"},
                {"label": "Password notes", "value": "; ".join(review.get("root_password_notes") or []) or "None"},
            ],
        ),
        area(
            "Build artifacts",
            "Ready" if review.get("base_iso_ready") else "Blocked",
            "ready" if review.get("base_iso_ready") else "pending",
            "These are the file paths and mount source the app will use during the real ESXi run.",
            [
                {"label": "ESXi version", "value": str(review.get("version") or "7")},
                {"label": "Base ISO path", "value": str(review.get("base_iso_path") or review.get("base_iso_error") or "Not set")},
                {"label": "Built ISO path", "value": str(review.get("output_iso_path") or "Not set")},
                {"label": "Virtual media URL", "value": str(review.get("virtual_media_url") or "Not set")},
                {"label": "Manual test defaults", "value": str(review.get("manual_defaults_label") or "Not set")},
            ],
        ),
        area(
            "Readiness",
            "Ready" if not missing_fields and review.get("base_iso_ready") and not review.get("validation_errors") else "Needs attention",
            "ready" if not missing_fields and review.get("base_iso_ready") and not review.get("validation_errors") else "pending",
            "This shows whether the saved values are complete enough for Run Center to build and launch the installer.",
            [
                {"label": "Missing required values", "value": ", ".join(missing_fields) or "None"},
                {"label": "Saved-value checks", "value": "; ".join(review.get("validation_errors") or []) or "Passed"},
                {"label": "Base ISO status", "value": "Ready" if review.get("base_iso_ready") else str(review.get("base_iso_error") or "Missing")},
            ],
            detail_rows=[
                {"label": "Source label", "value": str(review.get("source_label") or "Not set")},
                {"label": "Run stamp", "value": str(review.get("run_stamp") or "Not set")},
            ],
        ),
    ]

    return {
        "available": True,
        "detected_label": "Saved install profile",
        "subtitle": "These values come from the current kit and the ESXi builder path the app will use in Run Center.",
        "summary_fields": summary_fields,
        "areas": areas,
    }


def validation_check(
    label: str,
    ok: bool,
    details: str,
    *,
    why: str = "",
    fix: str = "",
    href: str = "",
) -> dict[str, Any]:
    return {
        "label": label,
        "ok": ok,
        "details": details,
        "why": why,
        "fix": fix,
        "href": href,
    }


def build_validation_checks(cfg: dict[str, Any], workflow: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    shared_subnet = (cfg.get("shared_network", {}).get("subnet") or "").strip()
    shared_gateway = (cfg.get("ip_plan", {}).get("gateway") or "").strip()
    if workflow in {"ilo", "storage"}:
        ilo_cfg = cfg.get("ilo", {}) or {}
        host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
        username = (ilo_cfg.get("username") or "").strip()
        password = str(ilo_cfg.get("password") or "")
        checks.append(
            validation_check(
                "Target address",
                bool(host),
                host or "Current iLO address is missing.",
                why="The app cannot connect to the server without the current iLO address.",
                fix="Open the iLO page and save the current iLO address.",
                href="/ilo",
            )
        )
        checks.append(
            validation_check(
                "Credentials",
                bool(username and password),
                "Ready" if (username and password) else "Username or password is missing.",
                why="Run Center cannot talk to iLO without saved sign-in details.",
                fix="Open the iLO page and save the username and password.",
                href="/ilo",
            )
        )
    if workflow in {"ilo", "esxi", "windows", "qnap", "netapp", "cisco_switch"}:
        checks.append(
            validation_check(
                "Shared defaults",
                bool(shared_subnet and shared_gateway),
                "Ready" if (shared_subnet and shared_gateway) else "Shared subnet or gateway is missing.",
                why="Shared network defaults feed the workflow pages and the final run review.",
                fix="Open Global Settings and save the shared subnet and gateway.",
                href="/global-settings",
            )
        )
    if workflow == "cisco_switch":
        cisco_cfg = cfg.get("cisco_switch", {}) or {}
        target = str(cisco_cfg.get("management_ip") or cisco_cfg.get("ip") or cfg.get("ip_plan", {}).get("switch") or "").strip()
        username = str(cisco_cfg.get("username") or "").strip()
        password = str(cisco_cfg.get("password") or "")
        approval = dict(cisco_cfg.get("config_approval") or {})
        checks.extend(
            [
                validation_check(
                    "Management IP",
                    bool(target),
                    target or "Cisco management IP is missing.",
                    why="Run Center applies Cisco config over SSH after console bootstrap.",
                    fix="Open Cisco and use Setup Cisco IP.",
                    href="/cisco",
                ),
                validation_check(
                    "SSH credentials",
                    bool(username and password and dict(cisco_cfg.get("last_ssh_test") or {}).get("ok")),
                    "Ready" if (username and password and dict(cisco_cfg.get("last_ssh_test") or {}).get("ok")) else "Save credentials and run Test SSH.",
                    why="Firmware and config actions must run over SSH, not serial.",
                    fix="Open Cisco, save the username/password, then run Test SSH.",
                    href="/cisco",
                ),
                validation_check(
                    "Approved config plan",
                    approval.get("state") == "approved",
                    "Ready" if approval.get("state") == "approved" else "Cisco config plan is not approved.",
                    why="Run Center should only apply the config plan after preview and approval.",
                    fix="Open Cisco, save the switch config, then approve it for Run Center.",
                    href="/cisco",
                ),
            ]
        )
    if workflow == "storage":
        storage_review = build_storage_review_context(cfg)
        approval = storage_review.get("approval", {}) or {}
        resolved_host = resolve_storage_target_host(cfg)
        checks.append(
            validation_check(
                "Approved plan",
                storage_review.get("approved"),
                "Ready" if storage_review.get("approved") else "No approved storage plan is saved yet.",
                why="Storage cannot be included safely unless the exact plan has been reviewed and approved.",
                fix="Open Storage / RAID, review the plan, and approve it.",
                href="/storage#storage-review-start",
            )
        )
        checks.append(
            validation_check(
                "Latest review match",
                not storage_review.get("stale"),
                "Ready" if not storage_review.get("stale") else "Storage approval no longer matches the latest discovery.",
                why="A stale storage plan could target the wrong hardware state.",
                fix="Display current storage setup again and approve the refreshed plan.",
                href="/storage#storage-review-start",
            )
        )
        checks.append(
            validation_check(
                "Approved host match",
                not approval.get("host") or approval.get("host") == resolved_host.get("resolved"),
                "Ready" if (not approval.get("host") or approval.get("host") == resolved_host.get("resolved")) else "The approved storage plan was saved for a different server address.",
                why="The approved plan must still point at the same server before it can be trusted.",
                fix="Open Storage / RAID and confirm the current storage target before approving again.",
                href="/storage#storage-review-start",
            )
        )
    if workflow == "ilo":
        try:
            storage_ready = validate_storage_ready_for_ilo_run(cfg)
            checks.append(
                validation_check(
                    "Storage dependency",
                    True,
                    "Ready" if storage_ready.get("included") else "Storage is not included in this run.",
                    why="Storage must be approved before it can safely join an iLO run.",
                    fix="Open Storage / RAID if you want storage included in this run.",
                    href="/storage#storage-review-start",
                )
            )
        except Exception as e:
            checks.append(
                validation_check(
                    "Storage dependency",
                    False,
                    str(e).splitlines()[0],
                    why="The iLO run would otherwise use a storage plan that is missing or no longer trusted.",
                    fix="Open Storage / RAID, refresh the storage review, and approve the latest plan.",
                    href="/storage#storage-review-start",
                )
            )
    if workflow in {"esxi", "windows", "qnap", "netapp"}:
        target_map = {
            "esxi": cfg.get("esxi", {}).get("management_ip") or cfg.get("ip_plan", {}).get("esxi", ""),
            "windows": cfg.get("windows", {}).get("ip_address") or cfg.get("ip_plan", {}).get("windows", ""),
            "qnap": cfg.get("qnap", {}).get("ip") or cfg.get("ip_plan", {}).get("qnap", ""),
            "netapp": cfg.get("netapp", {}).get("host") or cfg.get("ip_plan", {}).get("netapp", ""),
        }
        checks.append(
            validation_check(
                "Target address",
                bool(target_map.get(workflow)),
                target_map.get(workflow) or "Target address is missing.",
                why="This stage needs a target address before it can be reviewed or run safely.",
                fix=f"Open the {workflow.upper() if workflow == 'esxi' else workflow.title()} page and save the target settings.",
                href=f"/{workflow}",
            )
        )
        if workflow == "esxi":
            esxi_values = get_esxi_effective_values(cfg)
            checks.append(
                validation_check(
                    "Required ESXi values",
                    not esxi_values["missing_fields"],
                    "Ready" if not esxi_values["missing_fields"] else f"Missing: {', '.join(esxi_values['missing_fields'])}.",
                    why="The ESXi installer cannot be built or launched until the required install values are saved.",
                    fix="Open the ESXi page and save the missing setup values.",
                    href="/esxi",
                )
            )
            checks.append(
                validation_check(
                    "Saved-value rules",
                    not esxi_values["validation_errors"],
                    "Ready" if not esxi_values["validation_errors"] else "; ".join(esxi_values["validation_errors"]),
                    why="The ESXi installer can still fail later if the saved name or password breaks ESXi input rules.",
                    fix="Open the ESXi page and fix the saved server name or root password.",
                    href="/esxi",
                )
            )
            checks.append(
                validation_check(
                    "Depends on iLO setup",
                    bool((cfg.get("ilo", {}).get("current_ip") or "").strip()),
                    "Ready" if (cfg.get("ilo", {}).get("current_ip") or "").strip() else "Set the current iLO address first.",
                    why="ESXi orchestration depends on the hardware workflow pointing at the right server first.",
                    fix="Open the iLO page and save the current iLO address.",
                    href="/ilo",
                )
            )
        if workflow == "esxi":
            esxi_cfg = cfg.get("esxi", {}) or {}
            checks.append(
                validation_check(
                    "Saved credentials",
                    bool(str(esxi_cfg.get("root_password") or "")),
                    "Ready" if str(esxi_cfg.get("root_password") or "") else "Root password is missing.",
                    why="The ESXi workflow needs the saved install credentials before a real run.",
                    fix="Open the ESXi page and save the root password.",
                    href="/esxi",
                )
            )
        if workflow == "windows":
            windows_cfg = cfg.get("windows", {}) or {}
            checks.append(
                validation_check(
                    "Saved credentials",
                    bool(str(windows_cfg.get("admin_password") or "")),
                    "Ready" if str(windows_cfg.get("admin_password") or "") else "Administrator password is missing.",
                    why="The Windows workflow needs the saved administrator password before a real run.",
                    fix="Open the Windows page and save the administrator password.",
                    href="/windows",
                )
            )
            image_path = str(windows_cfg.get("source_image_path") or "").strip()
            image_kind = str(windows_cfg.get("source_image_kind") or "").strip().lower()
            image_ready = bool(image_path and image_kind in {"ova", "ovf"} and Path(image_path).exists())
            checks.append(
                validation_check(
                    "Windows source image",
                    image_ready,
                    "Ready" if image_ready else "Upload an OVA/OVF image first.",
                    why="Windows VM install planning needs a local OVA/OVF source artifact.",
                    fix="Open the Windows page and upload an OVA/OVF image.",
                    href="/windows",
                )
            )
            install_plan = windows_cfg.get("install_plan", {}) or {}
            plan_ready = bool(install_plan.get("ready"))
            checks.append(
                validation_check(
                    "Install plan preview",
                    plan_ready,
                    "Ready" if plan_ready else "Run the dry-run install planner and resolve warnings.",
                    why="Dry-run planning validates saved VM/image inputs before execution.",
                    fix="Open the Windows page and run Plan Windows install (dry-run).",
                    href="/windows",
                )
            )
        if workflow == "qnap":
            qnap_cfg = cfg.get("qnap", {}) or {}
            checks.append(
                validation_check(
                    "Saved credentials",
                    bool(str(qnap_cfg.get("username") or "").strip() and str(qnap_cfg.get("password") or "")),
                    "Ready" if (str(qnap_cfg.get("username") or "").strip() and str(qnap_cfg.get("password") or "")) else "QNAP username or password is missing.",
                    why="The QNAP workflow needs saved sign-in details before a real run.",
                    fix="Open the QNAP page and save the username and password.",
                    href="/qnap",
                )
            )
        if workflow == "netapp":
            netapp_cfg = cfg.get("netapp", {}) or {}
            protocol = str(netapp_cfg.get("storage_protocol") or "nfs").strip().lower()
            checks.append(
                validation_check(
                    "Bootstrap complete",
                    bool(netapp_cfg.get("bootstrap_complete")),
                    "Ready" if netapp_cfg.get("bootstrap_complete") else "Complete the NetApp bootstrap checklist first.",
                    why="The safe-apply path assumes the cluster management endpoint is already online.",
                    fix="Open the NetApp page, finish bootstrap, and mark it complete.",
                    href="/modules/netapp",
                )
            )
            checks.append(
                validation_check(
                    "ONTAP API target",
                    bool(str(netapp_cfg.get("host") or cfg.get("ip_plan", {}).get("netapp") or "").strip()),
                    "Ready" if str(netapp_cfg.get("host") or cfg.get("ip_plan", {}).get("netapp") or "").strip() else "NetApp target host is missing.",
                    why="The NetApp workflow needs the cluster management API endpoint before it can connect.",
                    fix="Open the NetApp page and save the ONTAP API / Cluster Management IP.",
                    href="/modules/netapp",
                )
            )
            checks.append(
                validation_check(
                    "Saved credentials",
                    bool(str(netapp_cfg.get("username") or "").strip() and str(netapp_cfg.get("password") or "")),
                    "Ready" if (str(netapp_cfg.get("username") or "").strip() and str(netapp_cfg.get("password") or "")) else "NetApp username or password is missing.",
                    why="Read-only discovery still needs credentials to query ONTAP REST APIs.",
                    fix="Open Global Settings and save NetApp credentials.",
                    href="/global-settings",
                )
            )
            checks.append(
                validation_check(
                    "Storage protocol",
                    protocol in {"nfs", "iscsi"},
                    protocol.upper() if protocol in {"nfs", "iscsi"} else "Storage protocol must be nfs or iscsi.",
                    why="Adaptive validation depends on the desired storage protocol.",
                    fix="Set NetApp storage protocol to NFS or iSCSI in Global Settings.",
                    href="/global-settings",
                )
            )
    return checks


def checks_status(checks: list[dict[str, Any]]) -> tuple[str, str, str]:
    if any(not item.get("ok") for item in checks):
        return "failed", "Needs attention", "pending"
    if checks:
        return "complete", "Ready", "ready"
    return "not_started", "Not started", "pending"


def summarize_validation_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(checks)
    ready = sum(1 for item in checks if item.get("ok"))
    blockers = sum(1 for item in checks if not item.get("ok"))
    next_blocker = next((item for item in checks if not item.get("ok")), None)
    tone = "pending" if blockers else ("ready" if total else "progress")
    label = "Needs attention" if blockers else ("Ready" if total else "Not started")
    return {
        "total": total,
        "ready": ready,
        "blockers": blockers,
        "next_blocker": next_blocker,
        "tone": tone,
        "label": label,
    }


def build_workflow_precheck_card(
    workflow_key: str,
    cfg: dict[str, Any],
    workflow_contexts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    context = workflow_contexts.get(workflow_key, {}) or {}
    checks = list(context.get("checks") or build_validation_checks(cfg, workflow_key))
    summary = summarize_validation_checks(checks)
    target = str(context.get("target") or "Not set")
    review_href = str(context.get("review_href") or f"/{workflow_key}")
    return {
        "key": workflow_key,
        "name": str(context.get("name") or workflow_key.replace("_", " ").title()),
        "label": summary["label"],
        "tone": summary["tone"],
        "target": target,
        "href": review_href,
        "checks_ready": summary["ready"],
        "total_checks": summary["total"],
        "blockers": summary["blockers"],
        "state_label": str(context.get("state_label") or summary["label"]),
        "next_blocker": summary["next_blocker"],
        "items": [
            {
                "label": str(item.get("label") or ""),
                "status": "Ready" if item.get("ok") else "Blocked",
                "tone": "ready" if item.get("ok") else "pending",
                "details": str(item.get("details") or ""),
                "fix": str(item.get("fix") or ""),
                "href": str(item.get("href") or review_href),
            }
            for item in checks
        ],
    }


def build_setup_precheck_summary(
    cfg: dict[str, Any],
    workflow_contexts: dict[str, dict[str, Any]],
    recommended_next_step: dict[str, str],
) -> dict[str, Any]:
    included = cfg.get("included", {}) or {}
    workflow_keys = ["ilo"]
    if included.get("storage"):
        workflow_keys.append("storage")
    for key in ["esxi", "windows", "qnap", "netapp", "cisco_switch"]:
        if included.get(key):
            workflow_keys.append(key)
    cards = [build_workflow_precheck_card(key, cfg, workflow_contexts) for key in workflow_keys]
    cards.append(build_upgrade_helper_card(cfg))
    total_workflows = len(cards)
    ready_workflows = sum(1 for item in cards if item.get("blockers") == 0 and item.get("total_checks"))
    total_blockers = sum(int(item.get("blockers") or 0) for item in cards)
    next_blocker = next((item.get("next_blocker") for item in cards if item.get("next_blocker")), None)
    tone = "pending" if total_blockers else "ready"
    label = "Needs attention" if total_blockers else "Ready"
    return {
        "title": "Operations summary",
        "subtitle": "The same pre-check state should stay visible while you move through setup, preview, and final run review.",
        "tone": tone,
        "label": label,
        "ready_workflows": ready_workflows,
        "total_workflows": total_workflows,
        "total_blockers": total_blockers,
        "next_step": recommended_next_step,
        "next_blocker": next_blocker,
        "items": cards,
    }


def build_dashboard_overview(
    cfg: dict[str, Any],
    setup_precheck_summary: dict[str, Any],
    workflow_contexts: dict[str, dict[str, Any]],
    dashboard_job_status: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    cards = list(setup_precheck_summary.get("items") or [])
    total_checks = sum(int(item.get("total_checks") or 0) for item in cards)
    ready_checks = sum(int(item.get("checks_ready") or 0) for item in cards)
    total_blockers = int(setup_precheck_summary.get("total_blockers") or 0)
    readiness_percent = int(round((ready_checks / total_checks) * 100)) if total_checks else 0
    ready_workflows = int(setup_precheck_summary.get("ready_workflows") or 0)
    total_workflows = int(setup_precheck_summary.get("total_workflows") or len(cards))
    running = str(job.get("status") or "").lower() not in {"", "idle", "complete", "completed", "preview complete"}
    failed_runs = list(dashboard_job_status.get("failed") or [])
    passed_runs = list(dashboard_job_status.get("passed") or [])

    if running:
        headline = "Run in progress"
        summary = str(job.get("current_stage") or "A run is active. Watch Run Center for live status.")
        tone = "progress"
    elif total_blockers:
        headline = "Needs attention"
        summary = f"{total_blockers} blocker{'s' if total_blockers != 1 else ''} need review before this kit is ready."
        tone = "pending"
    elif total_workflows:
        headline = "Ready for review"
        summary = "Included setup modules are ready for Run Center review."
        tone = "ready"
    else:
        headline = "No included setup modules"
        summary = "Choose the modules for this kit in Global Settings."
        tone = "muted"

    if failed_runs:
        latest_result = {
            "label": f"{failed_runs[0].get('name', 'Stage')} failed",
            "summary": failed_runs[0].get("time") or "Review the latest failed run.",
            "tone": "pending",
        }
    elif passed_runs:
        latest_result = {
            "label": f"{passed_runs[0].get('name', 'Stage')} passed",
            "summary": passed_runs[0].get("time") or "Latest run completed.",
            "tone": "ready",
        }
    else:
        latest_result = {
            "label": "No completed runs yet",
            "summary": "Run results will appear here after preview or execution.",
            "tone": "progress",
        }

    module_rows = []
    card_by_key = {str(item.get("key") or ""): item for item in cards}

    def setup_row_from_card(
        key: str,
        *,
        name: str = "",
        href: str = "",
        included: bool = True,
        configure_href: str = "",
        configure_label: str = "",
        target: str = "",
        summary: str = "",
    ) -> dict[str, Any]:
        item = dict(card_by_key.get(key) or {})
        context = workflow_contexts.get(key, {}) or {}
        row_name = name or str(item.get("name") or context.get("name") or key.replace("_", " ").title())
        row_href = href or str(item.get("href") or context.get("review_href") or f"/{key}")
        row_target = target or str(item.get("target") or context.get("target") or "")
        if not included:
            module_rows.append({
                "key": key,
                "name": row_name,
                "label": "Not included",
                "tone": "muted",
                "href": configure_href or row_href,
                "checks_ready": 0,
                "total_checks": 0,
                "blockers": 0,
                "ready": True,
                "included": False,
                "summary": summary or (f"Target {row_target}." if row_target and row_target != "Not set" else "Available to configure when this kit needs it."),
                "configure_href": configure_href or row_href,
                "configure_label": configure_label or "Configure",
            })
            return

        blockers = int(item.get("blockers") or 0)
        checks_ready = int(item.get("checks_ready") or 0)
        total = int(item.get("total_checks") or 0)
        module_rows.append(
            {
                "key": key,
                "name": row_name,
                "label": item.get("state_label") or item.get("label") or "Review",
                "tone": item.get("tone") or "progress",
                "href": row_href,
                "checks_ready": checks_ready,
                "total_checks": total,
                "blockers": blockers,
                "ready": blockers == 0 and (total == 0 or checks_ready == total),
                "included": True,
                "summary": (
                    item.get("next_blocker", {}).get("label")
                    if blockers and isinstance(item.get("next_blocker"), dict)
                    else summary or context.get("planned_summary", "Review saved setup.")
                ),
                "configure_href": configure_href or row_href,
                "configure_label": configure_label or "Configure",
            }
        )

    def append_static_setup_row(
        *,
        key: str,
        name: str,
        label: str,
        tone: str,
        href: str,
        summary: str,
        configure_href: str = "",
        configure_label: str = "",
        ready: bool = True,
    ) -> None:
        module_rows.append(
            {
                "key": key,
                "name": name,
                "label": label,
                "tone": tone,
                "href": href,
                "checks_ready": 1 if ready else 0,
                "total_checks": 1,
                "blockers": 0 if ready else 1,
                "ready": ready,
                "included": True,
                "summary": summary,
                "configure_href": configure_href or href,
                "configure_label": configure_label or "Open",
            }
        )

    included = cfg.get("included", {}) or {}
    ip_plan = cfg.get("ip_plan", {}) or {}
    shared_network = cfg.get("shared_network", {}) or {}
    shared_ready = bool(str(shared_network.get("subnet") or "").strip() and str(ip_plan.get("gateway") or "").strip())
    append_static_setup_row(
        key="global_settings",
        name="Global Settings",
        label="Shared defaults" if shared_ready else "Needs defaults",
        tone="ready" if shared_ready else "pending",
        href="/global-settings",
        summary="Subnet, gateway, DNS, module IP assignments, and shared SNMP defaults.",
        configure_href="/global-settings#address-plan",
        configure_label="Configure IPs",
        ready=shared_ready,
    )

    setup_row_from_card(
        "ilo",
        name="iLO",
        href="/ilo",
        included=True,
        configure_href="/ilo",
        configure_label="Configure IP",
        target=str((cfg.get("ilo") or {}).get("target_ip") or ip_plan.get("ilo") or (cfg.get("ilo") or {}).get("current_ip") or "Not set"),
    )
    setup_row_from_card(
        "storage",
        name="Storage setup",
        href="/storage",
        included=bool(included.get("storage")),
        configure_href="/storage",
        configure_label="Configure target",
        target=str(resolve_storage_target_host(cfg).get("resolved") or "Not set"),
        summary="Storage / RAID discovery, target access, current layout, and approval.",
    )
    setup_row_from_card(
        "esxi",
        name="ESXi setup",
        href="/esxi",
        included=bool(included.get("esxi", True)),
        configure_href="/global-settings#address-plan",
        configure_label="Configure IP",
        target=str((cfg.get("esxi") or {}).get("management_ip") or ip_plan.get("esxi") or "Not set"),
    )

    windows_cfg = cfg.get("windows", {}) or {}
    vcenter_host = str(windows_cfg.get("vsphere_host") or "").strip()
    append_static_setup_row(
        key="vcenter",
        name="vCenter / vSphere",
        label="Target saved" if vcenter_host else "Host not set",
        tone="ready" if vcenter_host else "pending",
        href="/windows#vcenter-settings",
        summary=f"VMware endpoint used for template deployment: {vcenter_host or 'not set'}.",
        configure_href="/windows#vcenter-settings",
        configure_label="Configure host",
        ready=bool(vcenter_host),
    )
    setup_row_from_card(
        "windows",
        name="Windows",
        href="/windows",
        included=bool(included.get("windows")),
        configure_href="/global-settings#address-plan",
        configure_label="Configure IP",
        target=str(windows_cfg.get("ip_address") or ip_plan.get("windows") or "Not set"),
        summary="Windows VM identity, guest IP, vSphere target, WinRM, and install plan.",
    )

    registered_ovfs = len((((cfg.get("ovf_templates") or {}).get("templates") or {})))
    append_static_setup_row(
        key="ovf_templates",
        name="OVF Templates",
        label=f"{registered_ovfs} registered" if registered_ovfs else "None registered",
        tone="ready" if registered_ovfs else "progress",
        href="/modules/ovf-templates#registered-templates",
        summary="Register reusable OVF/OVA directories before VM workflows select them.",
        configure_href="/modules/ovf-templates#register-ovf",
        configure_label="Register OVF",
        ready=registered_ovfs > 0,
    )

    if included.get("qnap"):
        setup_row_from_card(
            "qnap",
            name="QNAP",
            href="/qnap",
            included=True,
            configure_href="/global-settings#address-plan",
            configure_label="Configure IP",
            target=str((cfg.get("qnap") or {}).get("ip") or ip_plan.get("qnap") or "Not set"),
        )
    setup_row_from_card(
        "netapp",
        name="NetApp",
        href="/modules/netapp",
        included=bool(included.get("netapp")),
        configure_href="/global-settings#address-plan",
        configure_label="Configure IP",
        target=str((cfg.get("netapp") or {}).get("host") or ip_plan.get("netapp") or "Not set"),
        summary="ONTAP bootstrap, protocol target, VMware datastore planning, and safe apply.",
    )
    setup_row_from_card(
        "cisco_switch",
        name="Cisco",
        href="/cisco",
        included=bool(included.get("cisco_switch")),
        configure_href="/global-settings#address-plan",
        configure_label="Configure IP",
        target=str((cfg.get("cisco_switch") or {}).get("management_ip") or (cfg.get("cisco_switch") or {}).get("ip") or ip_plan.get("switch") or "Not set"),
        summary="Console access, management IP, SSH proof, and approved switch config.",
    )
    upgrade_card = build_upgrade_helper_card(cfg)
    card_by_key["upgrade_helper"] = upgrade_card
    setup_row_from_card(
        "upgrade_helper",
        name="Upgrade Helper",
        href="/upgrade-helper",
        included=True,
        configure_href="/upgrade-helper",
        configure_label="Review gates",
        summary="Firmware/media gates before execution.",
    )

    return {
        "headline": headline,
        "summary": summary,
        "tone": tone,
        "readiness_percent": readiness_percent,
        "ready_checks": ready_checks,
        "total_checks": total_checks,
        "ready_workflows": ready_workflows,
        "total_workflows": total_workflows,
        "total_blockers": total_blockers,
        "next_step": setup_precheck_summary.get("next_step") or {},
        "next_blocker": setup_precheck_summary.get("next_blocker"),
        "latest_result": latest_result,
        "module_rows": module_rows,
        "workspace_label": cfg.get("site", {}).get("name", "") or "Current kit",
    }


def build_page_precheck_summary(
    active_page: str,
    cfg: dict[str, Any],
    workflow_contexts: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if active_page == "upgrade_helper":
        card = build_upgrade_helper_card(cfg)
        card["title"] = "Upgrade pre-check"
        card["subtitle"] = "Keep upgrade readiness visible before you proceed with the rest of the build."
        return card
    workflow_map = {
        "ilo": "ilo",
        "storage": "storage",
        "esxi": "esxi",
        "windows": "windows",
        "qnap": "qnap",
        "netapp": "netapp",
        "cisco": "cisco_switch",
    }
    workflow_key = workflow_map.get(active_page)
    if not workflow_key:
        return None
    card = build_workflow_precheck_card(workflow_key, cfg, workflow_contexts)
    card["title"] = f"{card['name']} pre-check"
    card["subtitle"] = "Keep the target, blockers, and next fix visible while you work on this setup page."
    card["summary_value_label"] = "State"
    card["summary_value"] = card.get("state_label") or card.get("label") or "Review"
    card["show_target"] = False
    return card


def build_upgrade_helper_card(cfg: dict[str, Any]) -> dict[str, Any]:
    live_snapshot = load_latest_live_inventory_snapshot_for_cfg(cfg)
    live_summary = dict(live_snapshot.get("summary") or {})
    live_raw_summary = (((live_snapshot.get("raw") or {}).get("inventory") or {}).get("summary") or {})
    manager_model = str(((live_raw_summary.get("manager") or {}).get("model")) or "").strip()
    sync_ilo_upgrade_inventory_from_latest_live(cfg)
    inventory = build_upgrade_inventory(cfg)
    current_versions = {key: str((inventory.get(key) or {}).get("current_version") or "").strip() for key in ("ilo", "netapp", "cisco_switch")}
    current_sources = {key: str((inventory.get(key) or {}).get("source") or "").strip() for key in ("ilo", "netapp", "cisco_switch")}
    policies = normalize_upgrade_policies(cfg)
    device_details = {
        "ilo": {
            "manager_model": str((inventory.get("ilo") or {}).get("manager_model") or manager_model).strip(),
        },
        "netapp": {
            "baseline_target": str(((((cfg.get("netapp") or {}).get("desired") or {}).get("baseline") or {}).get("target_ontap_version") or "9.12.1")).strip(),
            "minimum_version": str(((((cfg.get("netapp") or {}).get("desired") or {}).get("baseline") or {}).get("minimum_ontap_version") or "")).strip(),
        },
        "cisco_switch": {
            "model": str((inventory.get("cisco_switch") or {}).get("model") or "").strip(),
            "platform": str((inventory.get("cisco_switch") or {}).get("platform") or "").strip(),
            "hostname": str((inventory.get("cisco_switch") or {}).get("hostname") or "").strip(),
        },
    }
    card = build_upgrade_helper_context(
        scan_upgrade_media(),
        current_versions,
        current_sources,
        device_details=device_details,
    )
    planner = build_upgrade_planner_with_policies(
        scan_upgrade_media(),
        current_versions,
        current_sources=current_sources,
        policies=policies,
        device_details=device_details,
    )
    overrides = {
        key: bool(value)
        for key, value in dict(((cfg.get("upgrade_helper") or {}).get("overrides") or {})).items()
        if key in {"ilo", "netapp", "cisco_switch"}
    }
    for entry in list(planner.get("entries") or []):
        key = str(entry.get("key") or "")
        if overrides.get(key) and entry.get("blocks_run"):
            entry["override_enabled"] = True
            entry["blocks_run"] = False
            entry["prebuild_gate"] = False
            entry["warn_only"] = True
            entry["severity"] = "progress"
            entry["recommended_action"] = f"Override enabled. {entry.get('recommended_action') or 'Review the version before continuing.'}"
    planner["blockers"] = sum(1 for item in list(planner.get("entries") or []) if item.get("blocks_run"))
    planner["warnings"] = sum(1 for item in list(planner.get("entries") or []) if item.get("warn_only"))
    planner["ready"] = len([item for item in list(planner.get("entries") or []) if not item.get("blocks_run")])
    card["planner"] = planner
    card["policies"] = policies
    card["overrides"] = overrides
    blocker_count = int(planner.get("blockers") or card.get("blockers") or 0)
    warning_count = int(planner.get("warnings") or 0)
    unknown_count = sum(1 for item in list(planner.get("entries") or []) if str(item.get("comparison") or "") == "current_unknown" and str(item.get("policy") or "") == "block")
    next_blocker = next((dict(item) for item in list(planner.get("entries") or []) if item.get("blocks_run")), None)
    next_warning = next((dict(item) for item in list(planner.get("entries") or []) if item.get("warn_only")), None)
    if blocker_count:
        state = "Upgrade first" if blocker_count > unknown_count else "Read versions"
    elif warning_count:
        state = "Warnings only"
    else:
        state = "Ready"
    card["blockers"] = blocker_count
    card["tone"] = "pending" if blocker_count else ("progress" if warning_count else "ready")
    card["label"] = "Needs attention" if blocker_count else ("Review warnings" if warning_count else "Ready")
    if next_blocker:
        blocker_details = ". ".join(
            [part for part in [str(next_blocker.get("compatibility_summary") or "").strip(), str(next_blocker.get("recommended_action") or "").strip()] if part]
        ).strip()
        card["next_blocker"] = {
            "label": f"{next_blocker.get('label', 'Device')} policy blocks the build",
            "details": blocker_details or "Open Upgrade Helper to review this device.",
            "fix": blocker_details or "Open Upgrade Helper to review this device.",
            "href": "/upgrade-helper",
        }
    elif next_warning:
        warning_details = ". ".join(
            [part for part in [str(next_warning.get("compatibility_summary") or "").strip(), str(next_warning.get("recommended_action") or "").strip()] if part]
        ).strip()
        card["next_blocker"] = {
            "label": f"{next_warning.get('label', 'Device')} has upgrade warnings",
            "details": warning_details or "Open Upgrade Helper to review the warning policy.",
            "fix": warning_details or "Open Upgrade Helper to review the warning policy.",
            "href": "/upgrade-helper",
        }
    else:
        card["next_blocker"] = None
    card["href"] = "/upgrade-helper"
    card["summary_value_label"] = "Decision"
    card["summary_value"] = state
    card["show_target"] = False
    return card


def upgrade_gate_blockers(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    planner = dict((build_upgrade_helper_card(cfg).get("planner") or {}))
    return [dict(item) for item in list(planner.get("entries") or []) if item.get("blocks_run")]


def upgrade_gate_entry(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    planner = dict((build_upgrade_helper_card(cfg).get("planner") or {}))
    for entry in list(planner.get("entries") or []):
        if str(entry.get("key") or "") == key:
            return dict(entry)
    return {}


def build_workflow_contexts(cfg: dict[str, Any], job: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    storage_review = build_storage_review_context(cfg)
    storage_target = resolve_storage_target_host(cfg)
    current_scope = str(job.get("scope") or "")
    current_status = str(job.get("status") or "")
    contexts: dict[str, dict[str, Any]] = {}

    storage_state = "not_started"
    if current_scope.startswith("storage-apply:") or current_scope == "storage-reboot":
        storage_state = "running" if "Running" in current_status else "validating"
    elif storage_review.get("stale"):
        storage_state = "stale"
    elif storage_review.get("approved"):
        storage_state = "approved"
    elif storage_review.get("state") == "planned":
        storage_state = "planned"
    elif storage_review.get("state") == "discovered":
        storage_state = "discovered"

    ui = workflow_state_ui(storage_state)
    contexts["storage"] = {
        "key": "storage",
        "name": "Storage / RAID",
        "state": storage_state,
        "state_label": ui["label"],
        "tone": ui["tone"],
        "target": storage_target.get("resolved") or "Not set",
        "approved": storage_review.get("approved"),
        "stale": storage_review.get("stale"),
        "current_summary": storage_review.get("status_reason") or "Display current storage setup to see what the server has today.",
        "planned_summary": storage_review.get("approval", {}).get("plan_summary", {}).get("mode") or "Build a storage plan to see the proposed layout.",
        "approved_summary": "Approved for a later iLO run." if storage_review.get("approved") else "No approved storage plan yet.",
        "result_summary": "Recent storage activity appears in Run History and the storage reports." if latest_history_entry_for_scope(history, ["storage-apply", "storage-reboot"]) else "No storage run has been recorded yet.",
        "checks": build_validation_checks(cfg, "storage"),
        "review_href": "/storage",
    }

    for key, name, target, config_summary in [
        ("ilo", "iLO", (cfg.get("ilo", {}).get("target_ip") or cfg.get("ip_plan", {}).get("ilo") or cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host") or "").strip(), (cfg.get("ilo", {}).get("hostname") or "").strip()),
        ("esxi", "ESXi", (cfg.get("esxi", {}).get("management_ip") or cfg.get("ip_plan", {}).get("esxi") or "").strip(), (cfg.get("esxi", {}).get("hostname") or "").strip()),
        ("windows", "Windows", (cfg.get("windows", {}).get("ip_address") or cfg.get("ip_plan", {}).get("windows") or "").strip(), (cfg.get("windows", {}).get("vm_name") or "").strip()),
        ("qnap", "QNAP", (cfg.get("qnap", {}).get("ip") or cfg.get("ip_plan", {}).get("qnap") or "").strip(), (cfg.get("qnap", {}).get("hostname") or "").strip()),
        ("netapp", "NetApp", (cfg.get("netapp", {}).get("host") or cfg.get("ip_plan", {}).get("netapp") or "").strip(), (cfg.get("netapp", {}).get("storage_protocol") or "nfs").strip().upper()),
        ("cisco_switch", "Cisco Switch", (cfg.get("cisco_switch", {}).get("management_ip") or cfg.get("cisco_switch", {}).get("ip") or cfg.get("ip_plan", {}).get("switch") or "").strip(), f"Approval {dict(cfg.get('cisco_switch', {}).get('config_approval') or {}).get('state') or 'not approved'}"),
    ]:
        checks = build_validation_checks(cfg, key)
        state, label, tone = checks_status(checks)
        if str(job.get("scope") or "") == key and str(job.get("status") or "") == "Running":
            state, label, tone = "running", workflow_state_ui("running")["label"], workflow_state_ui("running")["tone"]
        latest = latest_history_entry_for_scope(history, [key])
        contexts[key] = {
            "key": key,
            "name": name,
            "state": state,
            "state_label": label,
            "tone": tone,
            "target": target or "Not set",
            "current_summary": f"Current target is {target or 'not set'}.",
            "planned_summary": config_summary or "Finish the saved setup on this page.",
            "approved_summary": "This workflow uses saved settings directly." if cfg.get("included", {}).get(key) else "This workflow is currently not included in the kit.",
            "result_summary": (latest.get("status") or "No run has been recorded yet.") if latest else "No run has been recorded yet.",
            "checks": checks,
            "review_href": "/cisco" if key == "cisco_switch" else f"/{key}",
        }

    return contexts


def build_recommended_next_step(cfg: dict[str, Any], workflow_contexts: dict[str, dict[str, Any]]) -> dict[str, str]:
    upgrade_helper = build_upgrade_helper_card(cfg)
    if int(upgrade_helper.get("blockers") or 0) > 0:
        return {
            "title": "Review upgrade gates",
            "summary": "One or more devices need version review or upgrade before you continue the prebuild sequence.",
            "href": "/upgrade-helper",
        }
    ilo_host = (cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host") or "").strip()
    if not ilo_host:
        return {"title": "Set the iLO target", "summary": "Start on the iLO page and save the current iLO address and credentials first.", "href": "/ilo"}
    if cfg.get("included", {}).get("storage") and workflow_contexts["storage"]["state"] in {"not_started", "discovered", "planned", "stale"}:
        return {"title": "Finish storage review", "summary": "Go to Storage / RAID, confirm the current server, and approve the exact storage plan before the final run.", "href": "/storage"}
    for key in ["esxi", "windows", "qnap", "netapp", "cisco_switch"]:
        if cfg.get("included", {}).get(key) and workflow_contexts[key]["state"] in {"not_started", "failed"}:
            return {"title": f"Open {workflow_contexts[key]['name']} page", "summary": f"Open the {workflow_contexts[key]['name']} page and finish the saved setup values.", "href": workflow_contexts[key]["review_href"]}
    return {"title": "Review the run", "summary": "Open Run Center to review the included stages, checks, and warnings before starting.", "href": "/execution"}


def collect_report_entries(cfg: dict[str, Any], query: str = "", report_type: str = "all", limit: int = 120) -> list[dict[str, str]]:
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    entries: list[dict[str, str]] = []
    roots = [
        ("ilo-live", ILO_LIVE_EXPORT_DIR),
        ("storage", STORAGE_RAID_EXPORT_DIR),
        ("config", CONFIG_EXPORT_DIR),
        ("ilo-config", ILO_CONFIG_EXPORT_DIR),
    ]
    needle = query.strip().lower()
    for kind, root in roots:
        if report_type != "all" and report_type != kind:
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            text = f"{kind} {path.name} {path.parent.name} {path.parent.parent.name if path.parent.parent else ''}".lower()
            if needle and needle not in text:
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            entries.append(
                {
                    "kind": kind,
                    "label": path.name,
                    "path": str(path),
                    "parent": str(path.parent),
                    "server": path.parent.parent.name if path.parent.parent != path.parent else "",
                    "mtime": f"{modified.year:04d}-{modified.month:02d}-{modified.day:02d} {modified.hour:02d}:{modified.minute:02d}:{modified.second:02d}",
                    "kit_match": "Yes" if kit_name in str(path) else "",
                }
            )
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return entries[:limit]


def history_scope_bundle_name(scope: str) -> str:
    mapping = {
        "included": "Full kit run",
        "ilo": "iLO run",
        "storage-apply": "Storage apply run",
        "storage-reboot": "Storage restart follow-up",
        "esxi": "ESXi run",
        "windows": "Windows run",
        "qnap": "QNAP run",
    }
    return mapping.get(scope, scope.replace("_", " ").title())


def related_reports_query_for_history_item(item: dict[str, Any]) -> str:
    config_summary = item.get("config_summary", {}) or {}
    if config_summary.get("storage_plan_path"):
        return Path(str(config_summary["storage_plan_path"])).parent.name
    scope = str(item.get("scope") or "")
    target = str(config_summary.get("target_ip") or config_summary.get("login_ip") or "")
    if target:
        return target
    return scope


def build_bundle_target_summary(config_summary: dict[str, Any]) -> str:
    login_ip = str(config_summary.get("login_ip") or "").strip()
    target_ip = str(config_summary.get("target_ip") or "").strip()
    if login_ip and target_ip and login_ip != target_ip:
        return f"{login_ip} -> {target_ip}"
    return target_ip or login_ip or "Not set"


def build_bundle_highlights(scope: str, result: str, current_stage: str, config_summary: dict[str, Any]) -> list[str]:
    highlights: list[str] = []

    if scope == "ilo":
        login_ip = str(config_summary.get("login_ip") or "").strip()
        target_ip = str(config_summary.get("target_ip") or "").strip()
        if login_ip and target_ip and login_ip != target_ip:
            highlights.append(f"iLO IP {login_ip} -> {target_ip}")
        elif target_ip or login_ip:
            highlights.append(f"iLO target {target_ip or login_ip}")
        if config_summary.get("dns_apply_status"):
            highlights.append(f"DNS {config_summary.get('dns_apply_status')}")
        if config_summary.get("snmp_apply_status"):
            highlights.append(f"SNMP {config_summary.get('snmp_apply_status')}")
        if config_summary.get("ilo_reset_status"):
            highlights.append(f"iLO reset {config_summary.get('ilo_reset_status')}")
        if config_summary.get("storage_server_reboot_status"):
            highlights.append(f"Server reboot {config_summary.get('storage_server_reboot_status')}")
        if config_summary.get("ilo_final_ip_verified"):
            highlights.append("Final iLO IP verified")
    elif scope in {"storage", "storage-apply", "storage-reboot"}:
        if config_summary.get("storage_plan_path"):
            highlights.append("Approved storage plan used")
        if config_summary.get("storage_server_reboot_status"):
            highlights.append(f"Server reboot {config_summary.get('storage_server_reboot_status')}")
        if config_summary.get("reboot_required"):
            highlights.append("Restart was required")
    elif scope == "esxi":
        target_ip = str(config_summary.get("target_ip") or "").strip()
        if target_ip:
            highlights.append(f"Target IP {target_ip}")
        gateway = str(config_summary.get("gateway") or "").strip()
        if gateway:
            highlights.append(f"Gateway {gateway}")
        dns_servers = config_summary.get("dns_servers") or []
        if dns_servers:
            highlights.append(f"DNS {', '.join(dns_servers[:2])}")
    else:
        target_ip = str(config_summary.get("target_ip") or "").strip()
        if target_ip:
            highlights.append(f"Target IP {target_ip}")

    if current_stage and current_stage not in {"Finished", "Installed"}:
        highlights.append(f"Last step {current_stage}")
    elif result and result not in {"Recorded"}:
        highlights.append(f"Result {result}")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in highlights:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped[:5]


def build_bundle_human_summary(scope: str, result: str, current_stage: str, config_summary: dict[str, Any]) -> str:
    if scope == "ilo":
        parts: list[str] = []
        if config_summary.get("dns_apply_status"):
            parts.append(f"DNS {str(config_summary.get('dns_apply_status')).lower()}")
        if config_summary.get("snmp_apply_status"):
            parts.append(f"SNMP {str(config_summary.get('snmp_apply_status')).lower()}")
        if config_summary.get("ilo_reset_status"):
            parts.append(f"iLO reset {str(config_summary.get('ilo_reset_status')).lower()}")
        if config_summary.get("storage_server_reboot_status"):
            parts.append(f"server reboot {str(config_summary.get('storage_server_reboot_status')).lower()}")
        if config_summary.get("ilo_final_ip_verified"):
            parts.append("final iLO IP verified")
        if parts:
            return "This run handled " + ", ".join(parts) + "."
    if scope in {"storage", "storage-apply", "storage-reboot"} and config_summary.get("storage_plan_path"):
        return "This run used the approved storage plan and kept the full apply details in the bundle."
    if scope == "esxi":
        target_ip = str(config_summary.get("target_ip") or "").strip()
        if target_ip:
            return f"This run targeted the ESXi host at {target_ip}."
    if current_stage:
        return f"This run ended at: {current_stage}."
    if result:
        return f"This run finished with status: {result}."
    return "Open the bundle for the full technical detail."


def build_run_bundles(cfg: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    for item in history:
        if item.get("kind") == "event":
            continue
        scope = str(item.get("scope") or "")
        config_summary = item.get("config_summary", {}) or {}
        current_stage = str(item.get("current_stage") or item.get("summary") or "Run recorded")
        target = build_bundle_target_summary(config_summary)
        result = str(item.get("status") or "Recorded")
        run_summary_path = str(item.get("run_summary_path") or "")
        related_reports_query = related_reports_query_for_history_item(item)
        related_reports = []
        if run_summary_path:
            related_reports.append({"label": "Run summary", "path": run_summary_path})
        if config_summary.get("storage_plan_path"):
            related_reports.append({"label": "Storage plan used", "path": str(config_summary["storage_plan_path"])})
        bundles.append(
            {
                "name": history_scope_bundle_name(scope),
                "scope": scope,
                "target": target,
                "time": item.get("time", ""),
                "result": result,
                "tone": history_status_tone(result),
                "summary": current_stage,
                "human_summary": build_bundle_human_summary(scope, result, current_stage, config_summary),
                "highlights": build_bundle_highlights(scope, result, current_stage, config_summary),
                "run_summary_path": run_summary_path,
                "related_reports_query": related_reports_query,
                "related_reports": related_reports,
                "config_summary": config_summary,
            }
        )
    return bundles[:25]


def split_report_bundles_for_summary(bundles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    latest_by_scope: list[dict[str, Any]] = []
    older_runs: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for bundle in bundles:
        key = str(bundle.get("name") or bundle.get("scope") or "run")
        if key in seen_keys:
            older_runs.append(bundle)
            continue
        seen_keys.add(key)
        latest_by_scope.append(bundle)

    return latest_by_scope, older_runs


def build_report_center(cfg: dict[str, Any], query: str = "", report_type: str = "all") -> dict[str, Any]:
    history = load_history(cfg.get("site", {}).get("name", ""))
    entries = collect_report_entries(cfg, query=query, report_type=report_type)
    bundles = build_run_bundles(cfg, history)
    latest_bundles, older_bundles = split_report_bundles_for_summary(bundles)
    entry_counts: dict[str, int] = {}
    for item in entries:
        kind = str(item.get("kind") or "other")
        entry_counts[kind] = entry_counts.get(kind, 0) + 1
    preview_limit = 12
    return {
        "query": query,
        "report_type": report_type,
        "entries": entries,
        "entries_preview": entries[:preview_limit],
        "entries_total": len(entries),
        "entries_has_more": len(entries) > preview_limit,
        "entries_preview_limit": preview_limit,
        "entry_counts": entry_counts,
        "bundles": bundles,
        "latest_bundles": latest_bundles,
        "older_bundles": older_bundles,
    }


def safe_report_path(path_text: str) -> Path:
    candidate = Path(path_text).expanduser().resolve()
    roots = [EXPORTS_DIR.resolve(), HISTORY_DIR.resolve(), CONFIG_DIR.resolve(), GENERATED_DIR.resolve()]
    if not any(candidate.is_relative_to(root) for root in roots):
        raise ValueError("Report path must stay inside the app artifacts or config folders.")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError("The requested report file was not found.")
    return candidate


def build_execution_validation_overview(cfg: dict[str, Any], scope: str, stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for stage in stages:
        if not stage.get("included"):
            continue
        key = stage.get("key", "")
        if key == "storage":
            checks.extend(build_validation_checks(cfg, "storage"))
        elif key in {"ilo", "esxi", "windows", "qnap", "netapp"}:
            checks.extend(build_validation_checks(cfg, key))
    deduped: list[dict[str, Any]] = []
    seen = set()
    for item in checks:
        marker = (item.get("label"), item.get("details"), item.get("href"))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    if scope == "included" and not deduped:
        deduped.append(validation_check("Included stages", False, "No included stages are ready yet."))
    return deduped


def build_recoverability_notes(cfg: dict[str, Any], scope: str, stages: list[dict[str, Any]]) -> list[str]:
    notes = ["Run reviews can be exported before execution starts, and finished runs are kept in Run History."]
    if any(stage.get("key") == "storage" and stage.get("included") for stage in stages):
        notes.append("Storage discovery and approved-plan artifacts are saved so the exact pre-change view is easy to reopen.")
        notes.append("Storage changes can remove data. The pre-change snapshot is preserved, but data removal is not automatically reversible.")
    if scope in {"ilo", "included"}:
        notes.append("iLO settings can usually be changed again later, but network changes may temporarily move access to a new address.")
    return notes


def execution_mode_for_scope(scope: str) -> dict[str, str]:
    if str(scope or "").startswith("multi__"):
        return {
            "key": "real",
            "label": "Real execution",
            "badge": "Real run",
            "summary": "This path performs the selected real stages in order.",
            "what_this_does": "Runs the selected live stages in sequence.",
            "real_changes": "Yes",
            "next_step": "Start the run when the review looks correct.",
            "run_button": "Start selected real run",
            "run_note": "This is the live path. Real changes may be made.",
            "live_intro": "Live progress below is tracking a real run.",
        }
    if scope == "ilo":
        return {
            "key": "real",
            "label": "Real execution",
            "badge": "Real run",
            "summary": "This path performs real iLO changes when you start it.",
            "what_this_does": "Applies the saved iLO setup to the live target.",
            "real_changes": "Yes",
            "next_step": "Start the run when the review looks correct.",
            "run_button": "Start real iLO run",
            "run_note": "This is the live path. Real changes may be made.",
            "live_intro": "Live progress below is tracking a real run.",
        }
    if scope == "esxi":
        return {
            "key": "real",
            "label": "Real execution",
            "badge": "Real run",
            "summary": "This path performs the real ESXi install launch when you start it.",
            "what_this_does": "Builds the custom ESXi installer ISO and boots the server from it.",
            "real_changes": "Yes",
            "next_step": "Start the run when the review looks correct.",
            "run_button": "Start real ESXi run",
            "run_note": "This is the live path. Real changes may be made.",
            "live_intro": "Live progress below is tracking a real run.",
        }
    if scope == "windows":
        return {
            "key": "real",
            "label": "Safe execution",
            "badge": "Dry-run apply",
            "summary": "This path validates the uploaded OVA/OVF install inputs and records an execution log without deploying a VM yet.",
            "what_this_does": "Runs the Windows stage validation/apply simulation from the saved install plan.",
            "real_changes": "No",
            "next_step": "Use this to verify run behavior before enabling hypervisor-side deployment.",
            "run_button": "Start Windows safe execution",
            "run_note": "This stage currently performs validation only and does not deploy a VM.",
            "live_intro": "Live progress below is tracking a safe Windows stage execution.",
        }
    if scope == "netapp":
        return {
            "key": "safe_apply",
            "label": "Safe execution",
            "badge": "Safe apply",
            "summary": "This path runs the currently supported NetApp API create/update actions and blocks the rest.",
            "what_this_does": "Applies supported NetApp actions such as SVM, LIF, subnet, and protocol service setup.",
            "real_changes": "Yes",
            "next_step": "Use this after discovery and review confirm the current NetApp state.",
            "run_button": "Start NetApp safe apply",
            "run_note": "This is a live NetApp path for supported actions only. Manual and blocked steps are logged but not executed.",
            "live_intro": "Live progress below is tracking a NetApp safe-apply run.",
        }
    if scope == "cisco_switch":
        return {
            "key": "real",
            "label": "Real execution",
            "badge": "Real run",
            "summary": "This path applies the approved Cisco switch config over SSH.",
            "what_this_does": "Applies the saved and approved Cisco baseline and port configuration.",
            "real_changes": "Yes",
            "next_step": "Start the run when the Cisco review looks correct.",
            "run_button": "Start real Cisco run",
            "run_note": "This is the live Cisco path. Real switch changes may be made.",
            "live_intro": "Live progress below is tracking a real Cisco run.",
        }
    return {
        "key": "preview",
        "label": "Preview / safety mode",
        "badge": "Preview only",
        "summary": "This path validates and stages a preview only. No real changes are made.",
        "what_this_does": "Checks the run and prepares a preview.",
        "real_changes": "No",
        "next_step": "Run for real when everything looks ready.",
        "run_button": "Start preview run",
        "run_note": "This is a safety-mode preview. No real changes will be made.",
        "live_intro": "Live progress below is tracking a preview only.",
    }


def build_execution_launch_options(cfg: dict[str, Any], scope: str) -> dict[str, Any]:
    preview_option = {
        "scope": scope,
        "label": "Preview only",
        "summary": "Checks the run and prepares a preview. No real changes are made.",
    }
    storage_review = build_storage_review_context(cfg)
    storage_real = bool(storage_review.get("include_in_ilo_run") and storage_review.get("approved") and not storage_review.get("stale"))
    if scope == "ilo":
        return {
            "preview": preview_option,
            "real": {
                "scope": "ilo",
                "label": "Run for real",
                "summary": "Starts the live iLO run and may apply real changes to the server." + (" The approved storage plan will also be applied." if storage_real else ""),
            },
        }
    if scope == "included":
        selected = run_center_scope_keys(scope, cfg)
        supported_real = [item for item in selected if item in {"ilo", "storage", "esxi", "netapp", "cisco_switch"}]
        unsupported_real = [item for item in selected if item not in {"ilo", "storage", "esxi", "netapp", "cisco_switch"}]
        if unsupported_real:
            return {"preview": preview_option, "real": None}
        if len(supported_real) > 1:
            real_scope = "multi__" + "__".join(supported_real)
            multi_stage_summary = "Runs the included live stages in order."
            if "storage" in supported_real:
                multi_stage_summary += " The approved storage plan will also be applied."
            multi_stage_summary += " Later stages use the final iLO IP after the iLO stage finishes."
            return {
                "preview": preview_option,
                "real": {
                    "scope": real_scope,
                    "label": "Run whole kit for real",
                    "summary": multi_stage_summary,
                },
            }
        if supported_real == ["ilo"]:
            return {
                "preview": preview_option,
                "real": {
                    "scope": "ilo",
                    "label": "Run for real",
                    "summary": "Starts the live iLO run for this kit." + (" The approved storage plan will also be applied." if storage_real else ""),
                },
            }
        if supported_real == ["storage"]:
            return {
                "preview": preview_option,
                "real": {
                    "scope": "storage",
                    "label": "Run for real",
                    "summary": "Applies the approved storage plan to the current server using the exact approved discovery and plan artifacts.",
                },
            }
        if supported_real == ["esxi"]:
            return {
                "preview": preview_option,
                "real": {
                    "scope": "esxi",
                    "label": "Run for real",
                    "summary": "Builds the custom ESXi installer ISO, mounts it through virtual media, sets one-time boot, and starts the real ESXi boot sequence.",
                },
            }
        if supported_real == ["netapp"]:
            return {
                "preview": preview_option,
                "real": {
                    "scope": "netapp",
                    "label": "Run safe apply",
                    "summary": "Runs the supported NetApp API actions and logs blocked/manual steps for anything not yet automated.",
                },
            }
        if supported_real == ["cisco_switch"]:
            return {
                "preview": preview_option,
                "real": {
                    "scope": "cisco_switch",
                    "label": "Run for real",
                    "summary": "Applies the approved Cisco switch configuration over SSH.",
                },
            }
    if scope == "esxi":
        return {
            "preview": preview_option,
            "real": {
                "scope": "esxi",
                "label": "Run for real",
                "summary": "Builds the custom ESXi installer ISO, mounts it through virtual media, sets one-time boot, and starts the real ESXi boot sequence.",
            },
        }
    if scope == "windows":
        return {
            "preview": preview_option,
            "real": {
                "scope": "windows",
                "label": "Run safe Windows stage",
                "summary": "Validates uploaded OVA/OVF source and saved install plan, then records the stage run without deploying a VM.",
            },
        }
    if scope == "netapp":
        return {
            "preview": preview_option,
            "real": {
                "scope": "netapp",
                "label": "Run safe apply",
                "summary": "Runs the supported NetApp API actions and logs blocked/manual steps for anything not yet automated.",
            },
        }
    if scope == "cisco_switch":
        return {
            "preview": preview_option,
            "real": {
                "scope": "cisco_switch",
                "label": "Run for real",
                "summary": "Applies the approved Cisco switch configuration over SSH.",
            },
        }
    if scope == "storage":
        return {
            "preview": preview_option,
            "real": {
                "scope": "storage",
                "label": "Run for real",
                "summary": "Applies the approved storage plan to the current server using the exact approved discovery and plan artifacts.",
            },
        }
    if scope.startswith("multi__"):
        selected = run_center_scope_keys(scope, cfg)
        if selected and all(item in {"ilo", "storage", "esxi", "netapp", "cisco_switch"} for item in selected):
            return {
                "preview": preview_option,
                "real": {
                    "scope": scope,
                    "label": "Run selected for real",
                    "summary": "Runs the selected live stages in order. Later stages use the final iLO IP after the iLO stage finishes.",
                },
            }
    return {"preview": preview_option, "real": None}


def build_run_center_readiness_matrix(cfg: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    included_cfg = cfg.get("included", {}) or {}
    storage_review = build_storage_review_context(cfg)
    selected_keys = run_center_scope_keys(scope, cfg)
    upgrade_card = build_upgrade_helper_card(cfg)
    upgrade_planner = dict(upgrade_card.get("planner") or {})
    upgrade_blockers = [dict(item) for item in list(upgrade_planner.get("entries") or []) if item.get("blocks_run")]
    upgrade_warns = [dict(item) for item in list(upgrade_planner.get("entries") or []) if item.get("warn_only")]

    def stage_in_scope(key: str) -> bool:
        if scope == "included":
            if key == "storage":
                return bool(included_cfg.get("storage"))
            return bool(included_cfg.get(key))
        if scope == "ilo":
            return key in {"ilo", "storage"}
        if scope.startswith("multi__"):
            return key in selected_keys
        return key == scope

    shared_subnet = (cfg.get("shared_network", {}).get("subnet") or "").strip()
    shared_gateway = (cfg.get("ip_plan", {}).get("gateway") or "").strip()
    matrix: list[dict[str, Any]] = [
        {
            "name": "Global Settings",
            "label": "Ready" if (shared_subnet and shared_gateway) else "Blocked",
            "tone": "ready" if (shared_subnet and shared_gateway) else "pending",
            "summary": "Shared defaults are saved." if (shared_subnet and shared_gateway) else "Shared subnet or gateway is missing.",
            "action": "Finish shared defaults.",
            "href": "/global-settings",
        }
    ]
    if upgrade_blockers:
        primary = upgrade_blockers[0]
        matrix.append(
            {
                "name": "Upgrade gates",
                "label": "Blocked",
                "tone": "pending",
                "summary": primary.get("recommended_action") or "A device version gate is blocking this run.",
                "action": "Open Upgrade Helper and either upgrade the device or lower the policy intentionally.",
                "href": "/upgrade-helper",
            }
        )
    elif upgrade_warns:
        primary = upgrade_warns[0]
        matrix.append(
            {
                "name": "Upgrade gates",
                "label": "Needs review",
                "tone": "progress",
                "summary": primary.get("recommended_action") or "Upgrade review warnings are present.",
                "action": "Open Upgrade Helper and confirm the warning policies are intentional.",
                "href": "/upgrade-helper",
            }
        )
    else:
        matrix.append(
            {
                "name": "Upgrade gates",
                "label": "Ready",
                "tone": "ready",
                "summary": "No enforced upgrade blockers are active for this kit.",
                "action": "Open Upgrade Helper if you want to review media and device versions again.",
                "href": "/upgrade-helper",
            }
        )

    for key, name in [("ilo", "iLO"), ("storage", "Storage"), ("esxi", "ESXi"), ("windows", "Windows"), ("qnap", "QNAP"), ("netapp", "NetApp")]:
        included = stage_in_scope(key)
        if not included:
            continue

        if key == "storage":
            if storage_review.get("stale"):
                matrix.append(
                    {
                        "name": name,
                        "label": "Needs review",
                        "tone": "pending",
                        "summary": storage_review.get("status_reason") or "The approved storage plan no longer matches the latest storage view.",
                        "action": "Re-read storage and approve the plan again.",
                        "href": "/storage#storage-review-start",
                    }
                )
                continue
            if not storage_review.get("approved"):
                matrix.append(
                    {
                        "name": name,
                        "label": "Blocked",
                        "tone": "pending",
                        "summary": "No approved storage plan is available for this run.",
                        "action": "Open Storage / RAID and approve the current plan.",
                        "href": "/storage#storage-review-start",
                    }
                )
                continue
            matrix.append(
                {
                    "name": name,
                    "label": "Ready",
                    "tone": "ready",
                    "summary": "The approved storage plan is ready to use.",
                    "action": "Review the approved storage plan if you want another pass.",
                    "href": "/storage#storage-approval-actions",
                }
            )
            continue

        checks = build_validation_checks(cfg, key)
        blocked_check = next((item for item in checks if not item.get("ok")), None)
        if blocked_check:
            matrix.append(
                {
                    "name": name,
                    "label": "Blocked",
                    "tone": "pending",
                    "summary": blocked_check.get("details") or "This stage still needs setup.",
                    "action": "Open the workspace and fix the missing setup.",
                    "href": f"/{key}",
                }
            )
        else:
            runtime_status = (build_esxi_install_review(cfg, include_runtime=True).get("runtime_status") or {}) if key == "esxi" else {}
            summary = runtime_status.get("summary") if key == "esxi" and runtime_status else "Saved settings are ready for this run."
            action = runtime_status.get("recommended_action") if key == "esxi" and runtime_status else "Open the workspace if you want to review it again."
            label = "Ready"
            tone = "ready"
            if key == "esxi" and runtime_status and not runtime_status.get("management_reachable") and runtime_status.get("ilo_power_state"):
                label = "Ready, host offline"
                tone = "pending"
            matrix.append(
                {
                    "name": name,
                    "label": label,
                    "tone": tone,
                    "summary": summary,
                    "action": action,
                    "href": f"/{key}",
                }
            )
    return matrix


def build_run_summary(cfg: dict[str, Any], scope: str) -> dict[str, Any]:
    review = build_execution_review(cfg, scope)
    latest = latest_history_entry_for_scope(load_history(cfg.get("site", {}).get("name", "")), [scope, "included", "ilo", "storage-apply", "storage-reboot"]) or {}
    artifacts = build_run_summary_artifacts(cfg, review, scope)
    ilo_cfg = cfg.get("ilo", {}) or {}
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kit": cfg.get("site", {}).get("name", ""),
        "scope": scope,
        "target_server": (ilo_cfg.get("target_ip") or cfg.get("ip_plan", {}).get("ilo") or ilo_cfg.get("current_ip") or ilo_cfg.get("host") or ""),
        "summary_items": review.get("summary_items", []),
        "stages": review.get("stages", []),
        "readiness_matrix": review.get("readiness_matrix", []),
        "final_summary": review.get("final_summary", {}),
        "result": latest.get("status", "No run recorded yet."),
        "result_details": latest.get("config_summary", {}) or {},
        "restart_occurred": "Yes" if review.get("restart_expected") else "No",
        "detail_text": review.get("detail_text", ""),
        "validation_checks": review.get("validation_checks", []),
        "recoverability": review.get("recoverability", []),
        "artifacts": artifacts,
        "reports_root": str(EXPORTS_DIR),
    }


def write_run_summary_artifact(cfg: dict[str, Any], scope: str, *, timestamp: str | None = None) -> Path:
    summary = build_run_summary(cfg, scope)
    stamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    path = GENERATED_DIR / f"run-summary-{sanitize_kit_name(cfg.get('site', {}).get('name', 'kit'))}-{scope}-{stamp}.yml"
    path.write_text(yaml.safe_dump(summary, sort_keys=False), encoding="utf-8")
    build_copy = current_build_output_dir(cfg) / f"run-summary-{scope}-{stamp}.yml"
    build_copy.write_text(yaml.safe_dump(summary, sort_keys=False), encoding="utf-8")
    return path


def validate_storage_ready_for_ilo_run(cfg: dict[str, Any]) -> dict[str, Any]:
    storage_review = build_storage_review_context(cfg)
    if not storage_review.get("include_in_ilo_run"):
        return {"included": False}
    if not storage_review.get("approved"):
        if storage_review.get("stale"):
            raise ValueError("Storage is included in the iLO run, but the approved storage plan is stale and must be re-approved.")
        raise ValueError("Storage is included in the iLO run, but no approved storage plan is saved for this kit.")
    approval = storage_review.get("approval", {}) or {}
    discovery_raw_path = str(approval.get("discovery_raw_path") or "")
    approved_host = str(approval.get("host") or "").strip()
    if discovery_raw_path:
        try:
            raw_path = Path(discovery_raw_path).expanduser().resolve()
            if raw_path.is_relative_to(STORAGE_RAID_EXPORT_DIR.resolve()) and raw_path.exists():
                raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
                approved_host = str(raw_payload.get("source_host") or approved_host).strip()
        except Exception:
            pass
    return {
        "included": True,
        "approved_host": approved_host,
        "approved_serial_number": approval.get("serial_number", ""),
        "discovery_raw_path": discovery_raw_path,
        "plan_path": approval.get("plan_path", ""),
        "reboot_expected": bool(approval.get("reboot_expected")),
        "plan_summary": approval.get("plan_summary", {}),
    }


def safe_storage_artifact_path(path_text: str) -> Path:
    candidate = Path(path_text).expanduser().resolve()
    export_root = STORAGE_RAID_EXPORT_DIR.resolve()
    if not candidate.is_relative_to(export_root):
        raise ValueError("Storage artifact must be under the storage export folder.")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError("Storage artifact was not found.")
    return candidate


def storage_apply_paths_from_directory(apply_dir_text: str) -> dict[str, Path]:
    apply_dir = Path(apply_dir_text).expanduser().resolve()
    export_root = STORAGE_RAID_EXPORT_DIR.resolve()
    if not apply_dir.is_relative_to(export_root):
        raise ValueError("Storage apply artifact directory must be under the storage export folder.")
    if not apply_dir.exists() or not apply_dir.is_dir():
        raise ValueError("Storage apply artifact directory was not found.")

    return {
        "directory": apply_dir,
        "pre_change_summary": apply_dir / "pre-change-summary.yml",
        "pre_change_raw": apply_dir / "pre-change-raw.json",
        "plan": apply_dir / "raid-plan.yml",
        "apply_log": apply_dir / "apply-log.yml",
        "apply_results": apply_dir / "apply-results.json",
        "reboot_results": apply_dir / "reboot-results.json",
        "post_change_summary": apply_dir / "post-change-summary.yml",
        "post_change_raw": apply_dir / "post-change-raw.json",
        "post_reboot_summary": apply_dir / "post-reboot-summary.yml",
        "post_reboot_raw": apply_dir / "post-reboot-raw.json",
    }


def storage_artifact_target(
    artifact_kind: str,
    discovery_paths: dict[str, Path] | None,
    plan_paths: dict[str, Path] | None,
    artifact_path_text: str = "",
    artifact_title: str = "",
) -> tuple[Path, str]:
    if artifact_path_text:
        artifact_path = safe_storage_artifact_path(artifact_path_text)
        return artifact_path, artifact_title or f"Storage Artifact: {artifact_path.name}"
    if artifact_kind == "discovery_summary":
        if not discovery_paths:
            raise ValueError("No current storage discovery summary is available.")
        return discovery_paths["summary"], f"Storage Discovery Summary: {discovery_paths['summary'].name}"
    if artifact_kind == "discovery_raw":
        if not discovery_paths:
            raise ValueError("No current storage discovery raw export is available.")
        return discovery_paths["raw"], f"Storage Discovery Raw JSON: {discovery_paths['raw'].name}"
    if artifact_kind == "raid_plan":
        if not plan_paths:
            raise ValueError("No current RAID plan artifact is available.")
        return plan_paths["plan"], f"RAID Plan: {plan_paths['plan'].name}"
    raise ValueError("Unknown storage artifact request.")


def storage_apply_confirmation_for_mode(apply_mode: str) -> str:
    if apply_mode == "create_only":
        return STORAGE_APPLY_CONFIRM_CREATE
    if apply_mode == "wipe_rebuild":
        return STORAGE_APPLY_CONFIRM_WIPE
    raise ValueError("Unknown storage apply mode.")


def storage_apply_target_base(plan: dict) -> str:
    source = plan.get("source_discovery", {}) or {}
    return sanitize_kit_name(
        source.get("host")
        or source.get("serial_number")
        or source.get("server_model")
        or "storage-apply"
    )


def initialize_storage_apply_artifacts(
    cfg: dict,
    plan: dict,
    plan_paths: dict[str, Path],
) -> dict[str, Path]:
    del cfg, plan
    apply_dir = plan_paths["directory"]
    apply_dir.mkdir(parents=True, exist_ok=True)

    apply_paths = {
        "directory": apply_dir,
        "pre_change_summary": apply_dir / "pre-change-summary.yml",
        "pre_change_raw": apply_dir / "pre-change-raw.json",
        "plan": plan_paths["plan"],
        "apply_log": apply_dir / "apply-log.yml",
        "apply_results": apply_dir / "apply-results.json",
        "reboot_results": apply_dir / "reboot-results.json",
        "post_change_summary": apply_dir / "post-change-summary.yml",
        "post_change_raw": apply_dir / "post-change-raw.json",
        "post_reboot_summary": apply_dir / "post-reboot-summary.yml",
        "post_reboot_raw": apply_dir / "post-reboot-raw.json",
    }

    apply_log_payload = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "",
        "status": "Queued",
        "steps": [],
    }
    with open(apply_paths["apply_log"], "w", encoding="utf-8") as f:
        yaml.safe_dump(apply_log_payload, f, sort_keys=False)
    with open(apply_paths["apply_results"], "w", encoding="utf-8") as f:
        json.dump(
            {
                "started_at": apply_log_payload["started_at"],
                "mode": "",
                "status": "Queued",
                "paths": {key: str(value) for key, value in apply_paths.items()},
                "steps": [],
                "responses": [],
                "errors": [],
                "workflow_state": "queued",
                "reboot_status": "Not requested",
                "reboot_requested": False,
                "reboot_required": False,
            },
            f,
            indent=2,
            sort_keys=False,
        )
    with open(apply_paths["reboot_results"], "w", encoding="utf-8") as f:
        json.dump(
            {
                "status": "Not requested",
                "steps": [],
                "errors": [],
                "paths": {
                    "reboot_results": str(apply_paths["reboot_results"]),
                    "post_reboot_summary": str(apply_paths["post_reboot_summary"]),
                    "post_reboot_raw": str(apply_paths["post_reboot_raw"]),
                },
            },
            f,
            indent=2,
            sort_keys=False,
        )
    return apply_paths


def load_storage_workflow_state(storage_apply_paths: dict[str, Path] | None) -> dict[str, Any] | None:
    if not storage_apply_paths:
        return None

    apply_state = {}
    reboot_state = {}
    try:
        if storage_apply_paths["apply_results"].exists():
            apply_state = json.loads(storage_apply_paths["apply_results"].read_text(encoding="utf-8")) or {}
    except Exception:
        apply_state = {}
    try:
        if storage_apply_paths.get("reboot_results") and storage_apply_paths["reboot_results"].exists():
            reboot_state = json.loads(storage_apply_paths["reboot_results"].read_text(encoding="utf-8")) or {}
    except Exception:
        reboot_state = {}

    workflow_state = str(apply_state.get("workflow_state") or "")
    if not workflow_state:
        if apply_state.get("status") in {"Completed", "Staged"} and apply_state.get("reboot_required"):
            workflow_state = "staged_reboot_required"
        elif apply_state.get("status") == "Completed":
            workflow_state = "apply_complete"
        elif apply_state.get("status") == "Failed":
            workflow_state = "apply_failed"
        else:
            workflow_state = "idle"

    workflow_label, workflow_summary = storage_workflow_presentation(
        workflow_state,
        apply_state,
        reboot_state,
    )

    return {
        "apply": apply_state,
        "reboot": reboot_state,
        "workflow_state": workflow_state,
        "workflow_label": workflow_label,
        "workflow_summary": workflow_summary,
        "apply_path": apply_state.get("apply_path", ""),
        "reboot_required": bool(apply_state.get("reboot_required")),
        "reboot_status": reboot_state.get("status") or apply_state.get("reboot_status") or "Not requested",
        "reboot_requested": bool(apply_state.get("reboot_requested") or reboot_state.get("requested")),
        "post_reboot_validation": apply_state.get("post_reboot_validation", ""),
    }


def storage_status_is_eligible(status: str) -> bool:
    text = str(status or "").lower()
    if not text:
        return True
    return not any(term in text for term in ("critical", "failed", "failure", "disabled", "missing", "predictive", "warning"))


def storage_status_is_absent(status: str) -> bool:
    text = str(status or "").lower()
    return any(term in text for term in ("absent", "notpresent", "not present", "missing"))


def storage_status_is_standby_spare(status: str) -> bool:
    text = str(status or "").lower().replace(" ", "")
    return "standbyspare" in text


def storage_drive_is_array_selectable(drive: dict[str, Any]) -> bool:
    return bool(
        float(drive.get("size_gib") or drive.get("capacity") or 0) > 0
        and storage_status_is_eligible(str(drive.get("status") or ""))
        and not storage_status_is_absent(str(drive.get("status") or ""))
        and not storage_status_is_standby_spare(str(drive.get("status") or ""))
    )


def storage_drive_is_spare_selectable(drive: dict[str, Any]) -> bool:
    return bool(
        float(drive.get("size_gib") or drive.get("capacity") or 0) > 0
        and storage_status_is_eligible(str(drive.get("status") or ""))
        and not storage_status_is_absent(str(drive.get("status") or ""))
    )


def storage_drive_sort_key(drive: dict) -> tuple:
    bay = str(drive.get("bay") or drive.get("id") or "")
    try:
        bay_num = int(re.sub(r"\D+", "", bay) or "999999")
    except Exception:
        bay_num = 999999
    return (bay_num, bay, drive.get("serial_number", ""), drive.get("model", ""), drive.get("path", ""))


def storage_drive_identity(drive: dict[str, Any]) -> str:
    path = str(drive.get("path") or "").strip()
    if path:
        return path
    serial = str(drive.get("serial_number") or "").strip()
    if serial:
        return serial
    source = str(drive.get("source") or "").strip()
    controller_path = str(drive.get("controller_path") or "").strip()
    smart_location = str(drive.get("smart_storage_location") or drive.get("location") or "").strip()
    if smart_location:
        return f"{source}|{controller_path}|loc:{smart_location}"
    drive_id = str(drive.get("id") or "").strip()
    if drive_id:
        return f"{source}|{controller_path}|id:{drive_id}"
    bay = str(drive.get("bay") or "").strip()
    model = str(drive.get("model") or drive.get("name") or "").strip()
    size = str(drive.get("size_gib") or "").strip()
    if source or controller_path or bay or model or size:
        return f"{source}|{controller_path}|bay:{bay}|model:{model}|size:{size}"
    return ""


def infer_smart_storage_location(drive: dict, source: str) -> tuple[str, str]:
    location = str(drive.get("smart_storage_location") or drive.get("location") or "").strip()
    location_format = str(drive.get("smart_storage_location_format") or drive.get("location_format") or "").strip()
    if location:
        return location, location_format

    bay = str(drive.get("bay") or drive.get("id") or "").strip()
    if source == "hpe_smart_storage" and re.match(r"^[A-Za-z0-9]+:[A-Za-z0-9]+:[A-Za-z0-9]+$", bay):
        return bay, location_format or "ControllerPort:Box:Bay"
    return "", location_format


def normalized_plan_drive(drive: dict, source: str) -> dict:
    try:
        size_gib = float(drive.get("size_gib") or 0)
    except Exception:
        size_gib = 0.0
    smart_storage_location, smart_storage_location_format = infer_smart_storage_location(drive, source)
    path = str(drive.get("path") or "")
    controller_path = str(drive.get("controller_path") or "").strip()
    if not controller_path and path and "/Drives/" in path:
        controller_path = path.split("/Drives/", 1)[0]
    normalized = {
        "source": source,
        "path": path,
        "drive_path": path,
        "controller_path": controller_path,
        "id": str(drive.get("id") or ""),
        "bay": str(drive.get("bay") or drive.get("id") or ""),
        "name": drive.get("name", ""),
        "model": drive.get("model", ""),
        "serial_number": drive.get("serial_number", ""),
        "serial": str(drive.get("serial_number") or ""),
        "size_gib": size_gib,
        "capacity": size_gib,
        "media_type": drive.get("media_type", "") or "Unknown",
        "protocol": drive.get("protocol", "") or "Unknown",
        "status": drive.get("status", ""),
        "smart_storage_location": smart_storage_location,
        "smart_storage_location_format": smart_storage_location_format,
    }
    normalized["drive_identity"] = storage_drive_identity(normalized)
    return normalized


def storage_item_matches_controller(item: dict[str, Any], controller: dict[str, Any]) -> bool:
    if not controller:
        return True
    controller_source = str(controller.get("source") or "").strip()
    item_source = str(item.get("source") or "").strip()
    if controller_source and item_source and controller_source != item_source:
        return False

    controller_path = str(controller.get("path") or "").rstrip("/")
    if not controller_path:
        return True

    item_controller_path = str(item.get("controller_path") or "").rstrip("/")
    item_path = str(item.get("path") or "").rstrip("/")
    if item_controller_path:
        return item_controller_path == controller_path
    if item_path and item_path.startswith(f"{controller_path}/"):
        return True
    return True


def drive_group_key(drive: dict) -> tuple:
    return (
        str(drive.get("media_type") or "Unknown").lower(),
        str(drive.get("protocol") or "Unknown").lower(),
        int(round(float(drive.get("size_gib") or 0))),
    )


def choose_os_drive_pair(eligible_drives: list[dict]) -> tuple[list[dict], str]:
    candidates = []
    for idx, left in enumerate(eligible_drives):
        for right in eligible_drives[idx + 1:]:
            same_media = left["media_type"].lower() == right["media_type"].lower()
            same_protocol = left["protocol"].lower() == right["protocol"].lower()
            same_controller = str(left.get("controller_path") or "") == str(right.get("controller_path") or "")
            capacity_delta = abs(left["size_gib"] - right["size_gib"])
            usable_size = min(left["size_gib"], right["size_gib"])
            pair = sorted([left, right], key=storage_drive_sort_key)
            candidates.append((
                (
                    0 if same_controller else 1,
                    0 if same_media else 1,
                    0 if same_protocol else 1,
                    capacity_delta,
                    usable_size,
                    storage_drive_sort_key(pair[0]),
                    storage_drive_sort_key(pair[1]),
                ),
                pair,
            ))

    if not candidates:
        return [], "No eligible pair was available."

    _, pair = sorted(candidates, key=lambda item: item[0])[0]
    return pair, (
        "Selected the smallest healthy matched pair by controller, media type, protocol, capacity, and deterministic bay/order. "
        f"Usable mirror size is about {min(d['size_gib'] for d in pair):.0f} GiB."
    )


def choose_data_layout(remaining_drives: list[dict], raid_level: str) -> tuple[list[dict], dict, list[dict], str, list[str]]:
    selected_raid = normalize_raid_choice("data", raid_level, allow_empty=True)
    if not selected_raid:
        return [], {}, list(remaining_drives), "No default data array is selected for the remaining drives.", []
    if not remaining_drives:
        return [], {}, [], f"No remaining eligible drives were available for {raid_label(selected_raid)}.", [f"No remaining eligible drives are available for the {raid_label(selected_raid)} set."]

    groups: dict[tuple, list[dict]] = {}
    for drive in remaining_drives:
        groups.setdefault(drive_group_key(drive), []).append(drive)

    ranked_groups = sorted(groups.items(), key=lambda item: (-len(item[1]), -item[0][2], item[0][0], item[0][1]))
    selected_key, selected_group = ranked_groups[0]
    compatible_group = sorted(selected_group, key=storage_drive_sort_key)
    excluded = [
        {**drive, "exclude_reason": f"Not in the selected {raid_label(selected_raid)} compatible media/protocol/capacity group."}
        for drive in remaining_drives
        if drive not in selected_group
    ]
    blockers = validate_raid_drive_count(selected_raid, compatible_group, section="data")
    explanation = (
        f"Selected the largest compatible remaining group for {raid_label(selected_raid)}: "
        f"media={selected_key[0]}, protocol={selected_key[1]}, capacity≈{selected_key[2]} GiB, drives={len(compatible_group)}."
    )
    if blockers:
        explanation = (
            f"Best remaining compatible group for {raid_label(selected_raid)} was too small: "
            f"media={selected_key[0]}, protocol={selected_key[1]}, capacity≈{selected_key[2]} GiB, drives={len(compatible_group)}."
        )
    return compatible_group, {}, excluded, explanation, blockers


def choose_default_data_raid(remaining_drives: list[dict]) -> str:
    if len(remaining_drives) >= 4:
        return "RAID6"
    if len(remaining_drives) >= 3:
        return "RAID5"
    if len(remaining_drives) >= 2:
        return "RAID1"
    return ""


def storage_firmware_display(value: object) -> str:
    if isinstance(value, dict):
        current = value.get("Current")
        if isinstance(current, dict):
            version = current.get("VersionString")
            if version:
                return str(version)
        for key in ("VersionString", "version", "current", "firmware_version"):
            if value.get(key):
                return str(value.get(key))
        return ""
    return str(value or "")


def plan_drive_bays(drives: list[dict]) -> str:
    bays = [str(drive.get("bay") or drive.get("id") or "").strip() for drive in drives or []]
    bays = [bay for bay in bays if bay]
    return ", ".join(bays)


def storage_drive_metadata(drive: dict[str, Any]) -> dict[str, Any]:
    return {
        "bay": str(drive.get("bay") or ""),
        "size_gib": float(drive.get("size_gib") or drive.get("capacity") or 0),
        "model": str(drive.get("model") or drive.get("name") or ""),
        "serial_number": str(drive.get("serial_number") or drive.get("serial") or ""),
        "status": str(drive.get("status") or ""),
        "controller_path": str(drive.get("controller_path") or ""),
    }


def storage_plan_array(
    *,
    role: str,
    name: str,
    raid_level: str,
    controller: dict[str, Any],
    drives: list[dict[str, Any]],
    target_size_gib: int | None = None,
) -> dict[str, Any]:
    normalized_raid = normalize_raid_choice("os" if role == "os" else "data", raid_level, allow_empty=True)
    controller_path = str(controller.get("path") or "").strip()
    controller_name = storage_controller_label(controller) or controller_path
    payload = {
        "role": role,
        "name": name,
        "raid_level": normalized_raid,
        "raid_label": raid_label(normalized_raid),
        "controller_path": controller_path,
        "controller_name": controller_name,
        "selected_drive_ids": [str(drive.get("path") or drive.get("drive_path") or "") for drive in drives if str(drive.get("path") or drive.get("drive_path") or "").strip()],
        "selected_drive_metadata": [storage_drive_metadata(drive) for drive in drives],
        "drives": list(drives or []),
        "bays": plan_drive_bays(drives),
    }
    if target_size_gib is not None:
        payload["target_size_gib"] = target_size_gib
    return payload


def storage_plan_arrays(plan: dict[str, Any]) -> list[dict[str, Any]]:
    arrays = list(plan.get("arrays") or [])
    if arrays:
        return arrays
    fallback: list[dict[str, Any]] = []
    os_section = plan.get("os_raid1") or {}
    data_section = plan.get("data_raid6") or {}
    planned = plan.get("planned_layout") or {}
    os_controller = ((planned.get("os_raid1") or {}).get("controller")) or ((plan.get("source_discovery") or {}).get("os_controller")) or {}
    data_controller = ((planned.get("data_raid6") or {}).get("controller")) or ((plan.get("source_discovery") or {}).get("data_controller")) or {}
    if os_section.get("drives"):
        fallback.append(storage_plan_array(role="os", name="OS array", raid_level=str(os_section.get("raid") or "RAID1"), controller=os_controller, drives=list(os_section.get("drives") or []), target_size_gib=int(os_section.get("target_size_gib") or 500)))
    if data_section.get("drives"):
        fallback.append(storage_plan_array(role="data", name="Data array", raid_level=str(data_section.get("raid") or "RAID6"), controller=data_controller, drives=list(data_section.get("drives") or [])))
    return fallback


COMMON_RAID_OPTIONS: list[dict[str, Any]] = [
    {"value": "RAID0", "label": "RAID 0", "min_drives": 2},
    {"value": "RAID1", "label": "RAID 1", "min_drives": 2, "exact_drives": 2},
    {"value": "RAID5", "label": "RAID 5", "min_drives": 3},
    {"value": "RAID6", "label": "RAID 6", "min_drives": 4},
    {"value": "RAID10", "label": "RAID 10", "min_drives": 4, "even_drives": True},
]


def storage_raid_options(section: str) -> list[dict[str, Any]]:
    del section
    return COMMON_RAID_OPTIONS


def normalize_raid_choice(section: str, value: str, *, allow_empty: bool = False) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]", "", str(value or "").strip()).upper()
    if allow_empty and not normalized:
        return ""
    allowed = {item["value"] for item in storage_raid_options(section)}
    if normalized in allowed:
        return normalized
    return "RAID1" if section == "os" else "RAID6"


def raid_label(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z]", "", str(value or "").strip()).upper()
    if not normalized:
        return "Not used"
    if normalized.startswith("RAID") and len(normalized) > 4:
        return f"RAID {normalized[4:]}"
    return str(value or "").strip() or "RAID"


def validate_raid_drive_count(raid: str, drives: list[dict[str, Any]], *, section: str) -> list[str]:
    count = len(drives)
    if count == 0:
        return []
    normalized = normalize_raid_choice(section, raid)
    rules = {item["value"]: item for item in storage_raid_options(section)}
    rule = rules[normalized]
    issues: list[str] = []
    exact_drives = rule.get("exact_drives")
    min_drives = int(rule.get("min_drives") or 0)
    section_label = "OS" if section == "os" else "data"
    if exact_drives is not None and count != int(exact_drives):
        issues.append(f"Choose exactly {exact_drives} drives for {raid_label(normalized)} in the {section_label} section.")
    elif min_drives and count < min_drives:
        issues.append(f"Choose at least {min_drives} drives for {raid_label(normalized)} in the {section_label} section.")
    if rule.get("even_drives") and count % 2:
        issues.append(f"{raid_label(normalized)} in the {section_label} section requires an even number of drives.")
    return issues


def build_storage_planning_drives(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    summary = summary or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    controller_by_path = {
        str(controller.get("path") or "").strip(): controller
        for controller in list(hpe.get("controllers", []) or []) + list(standard.get("controllers", []) or [])
        if str(controller.get("path") or "").strip()
    }
    planning_drives = []
    for source, items in (("hpe_smart_storage", hpe.get("drives", [])), ("standard_redfish_storage", standard.get("drives", []))):
        for item in items or []:
            drive = normalized_plan_drive(item, source)
            if not str(drive.get("controller_path") or "").strip() and len(controller_by_path) == 1:
                drive["controller_path"] = next(iter(controller_by_path.keys()))
            controller = controller_by_path.get(str(drive.get("controller_path") or "").strip(), {})
            drive["controller_name"] = storage_controller_label(controller) if controller else str(drive.get("controller_path") or "")
            drive["drive_path"] = str(drive.get("path") or "")
            drive["serial"] = str(drive.get("serial_number") or "")
            drive["capacity"] = float(drive.get("size_gib") or 0)
            drive["standby_spare"] = storage_status_is_standby_spare(str(drive.get("status") or ""))
            drive["absent"] = storage_status_is_absent(str(drive.get("status") or ""))
            drive["eligible"] = storage_drive_is_array_selectable(drive)
            drive["spare_eligible"] = storage_drive_is_spare_selectable(drive)
            planning_drives.append(drive)
    return sorted(planning_drives, key=storage_drive_sort_key)


def select_primary_storage_controller(summary: dict[str, Any] | None) -> dict[str, Any]:
    summary = summary or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    controllers = list(hpe.get("controllers", []) or []) + list(standard.get("controllers", []) or [])
    for controller in controllers:
        if controller.get("model") or controller.get("name"):
            return {**controller, "firmware_version": storage_firmware_display(controller.get("firmware_version"))}
    if controllers:
        return {**controllers[0], "firmware_version": storage_firmware_display(controllers[0].get("firmware_version"))}
    return {}


def build_storage_controller_choices(summary: dict[str, Any] | None) -> list[dict[str, str]]:
    summary = summary or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    controllers = []
    for source, items in (("hpe_smart_storage", hpe.get("controllers", [])), ("standard_redfish_storage", standard.get("controllers", []))):
        for item in items or []:
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            name = str(item.get("name") or "").strip()
            model = str(item.get("model") or "").strip()
            firmware = storage_firmware_display(item.get("firmware_version"))
            label_bits = [bit for bit in [name, model] if bit]
            label = " / ".join(dict.fromkeys(label_bits)) or path.rsplit("/", 1)[-1]
            detail_bits = [bit for bit in [source.replace("_", " ").title(), firmware] if bit]
            controllers.append(
                {
                    "path": path,
                    "source": source,
                    "label": label,
                    "details": " | ".join(detail_bits),
                }
            )
    return controllers


def storage_controller_label(controller: dict[str, Any]) -> str:
    if not controller:
        return ""
    explicit_label = str(controller.get("label") or "").strip()
    if explicit_label:
        return explicit_label
    name = str(controller.get("name") or "").strip()
    model = str(controller.get("model") or "").strip()
    label_bits = [bit for bit in [name, model] if bit]
    return " / ".join(dict.fromkeys(label_bits)) or str(controller.get("path") or "").rsplit("/", 1)[-1]


def storage_drive_label(drive: dict[str, Any]) -> str:
    bay = str(drive.get("bay") or drive.get("id") or "").strip()
    model = str(drive.get("model") or drive.get("name") or "").strip()
    size = str(int(round(float(drive.get("size_gib") or drive.get("capacity") or 0)))) if (drive.get("size_gib") or drive.get("capacity")) else ""
    serial = str(drive.get("serial_number") or drive.get("serial") or "").strip()
    bits = []
    if bay:
        bits.append(f"Bay {bay}")
    if model:
        bits.append(model)
    if size:
        bits.append(f"{size} GiB")
    if serial:
        bits.append(serial)
    return " | ".join(bits) or str(drive.get("path") or drive.get("drive_path") or "(unknown drive)")


HARDWARE_PROFILES: list[dict[str, Any]] = [
    {
        "match": {"model_contains": "DL360", "generation_contains": "Gen10"},
        "profile_name": "HPE ProLiant DL360 Gen10",
        "expected_storage_layout": "single_controller",
        "known_good_apply_path": "SmartStorageConfig",
        "controller_examples": ["HPE Smart Array P408i-a SR Gen10"],
        "warnings": [],
    },
    {
        "match": {"model_contains": "DL380", "generation_contains": "Gen11"},
        "profile_name": "HPE ProLiant DL380 Gen11",
        "expected_storage_layout": "multi_controller",
        "known_good_apply_path": "inventory_only",
        "controller_roles": {
            "os_candidates": ["MR416i-p"],
            "data_candidates": ["MR416i-o"],
        },
        "warnings": [
            "Bay numbers are not globally unique.",
            "Use drive path or serial identity only.",
            "Standard Redfish Volumes may be inventory-only for creation.",
        ],
    },
]


def detect_hardware_profile(server: dict[str, Any], controllers: list[dict[str, Any]]) -> dict[str, Any]:
    del controllers
    model = str(server.get("model") or "").strip()
    generation = str(server.get("generation") or "").strip()
    for profile in HARDWARE_PROFILES:
        match = profile.get("match") or {}
        if match.get("model_contains") and match["model_contains"] not in model:
            continue
        if match.get("generation_contains") and match["generation_contains"] not in generation:
            continue
        return {k: copy.deepcopy(v) for k, v in profile.items() if k != "match"}
    return {
        "profile_name": model or "Unknown hardware",
        "expected_storage_layout": "unknown",
        "known_good_apply_path": "",
        "warnings": [],
    }


def storage_profile_advisories(profile: dict[str, Any], controllers: list[dict[str, Any]], drives: list[dict[str, Any]]) -> list[str]:
    advisories: list[str] = []
    if profile.get("expected_storage_layout") == "multi_controller" and len(controllers) > 1:
        advisories.append(f"Detected {profile.get('profile_name')} multi-controller layout.")
    role_hints = profile.get("controller_roles") or {}
    os_candidates = [token for token in role_hints.get("os_candidates") or [] if token]
    data_candidates = [token for token in role_hints.get("data_candidates") or [] if token]
    matched_os = next((controller for controller in controllers if any(token in storage_controller_label(controller) for token in os_candidates)), {})
    matched_data = next((controller for controller in controllers if any(token in storage_controller_label(controller) for token in data_candidates)), {})
    if matched_os:
        advisories.append(f"OS drives appear to be on {storage_controller_label(matched_os)}.")
    if matched_data:
        advisories.append(f"Data drives appear to be on {storage_controller_label(matched_data)}.")
    bay_counts: dict[str, int] = {}
    for drive in drives:
        bay = str(drive.get("bay") or "").strip()
        if bay:
            bay_counts[bay] = bay_counts.get(bay, 0) + 1
    if any(count > 1 for count in bay_counts.values()):
        advisories.append("Bay labels are duplicated, so drive identity will use Redfish path.")
    for warning in profile.get("warnings") or []:
        if warning not in advisories:
            advisories.append(str(warning))
    return advisories


def validate_storage_plan_drive_paths(plan: dict[str, Any], discovery: dict[str, Any]) -> None:
    summary = discovery.get("summary", {}) or {}
    planning_drives = build_storage_planning_drives(summary)
    discovery_source_host = str((discovery.get("raw", {}) or {}).get("source_host") or summary.get("source_host") or "").strip()
    discovery_serial = str((summary.get("server", {}) or {}).get("serial_number") or "").strip()
    db_drives = db_lookup_drive_rows(cfg=load_kit_config(), system_serial=discovery_serial, ilo_host=discovery_source_host)
    drive_by_path = {
        str(drive.get("path") or drive.get("drive_path") or "").strip(): drive
        for drive in planning_drives
        if str(drive.get("path") or drive.get("drive_path") or "").strip()
    }
    for path, drive in db_drives.items():
        drive_by_path.setdefault(path, drive)
    controller_choices = build_storage_controller_choices(summary)
    controller_by_path = {str(item.get("path") or "").strip(): item for item in controller_choices}

    arrays = storage_plan_arrays(plan)
    by_role = {str(array.get("role") or ""): array for array in arrays}
    problems: list[str] = []
    used_paths: dict[str, str] = {}
    for array in arrays:
        role = str(array.get("role") or "custom")
        role_name = str(array.get("name") or role.upper())
        controller_path = str(array.get("controller_path") or "").strip()
        controller_name = storage_controller_label(controller_by_path.get(controller_path, {})) or str(array.get("controller_name") or controller_path or "(none)")
        live_controller_paths: set[str] = set()
        live_sizes: set[int] = set()
        for drive_path in list(array.get("selected_drive_ids") or []):
            drive_path = str(drive_path or "").strip()
            if not drive_path:
                problems.append(f"{role_name} includes a drive without a Redfish drive path.")
                continue
            live = drive_by_path.get(drive_path)
            if not live:
                problems.append(f"{role_name} drive {drive_path} was not found in the current inventory.")
                continue
            if storage_status_is_absent(str(live.get("status") or "")):
                problems.append(f"{storage_drive_label(live)} is absent and cannot be selected.")
            live_controller_path = str(live.get("controller_path") or "").strip()
            live_controller_paths.add(live_controller_path)
            actual_name = storage_controller_label(controller_by_path.get(live_controller_path, {})) or live.get("controller_name") or live_controller_path or "(unknown controller)"
            if controller_path and live_controller_path != controller_path:
                problems.append(f"{storage_drive_label(live)} is on {actual_name}, but {role_name} is set to {controller_name}.")
            prior_role = used_paths.get(drive_path)
            if prior_role and prior_role != role:
                problems.append(f"Drive path {drive_path} is reused by both {prior_role} and {role}.")
            used_paths[drive_path] = role
            try:
                live_sizes.add(int(round(float(live.get("size_gib") or live.get("capacity") or 0))))
            except Exception:
                pass
        if len(live_controller_paths) > 1:
            problems.append(f"{role_name} cannot span multiple storage controllers.")
        if len(live_sizes) > 1:
            problems.append(f"{role_name} uses mixed drive sizes. Review the array before applying.")

    spare = ((plan.get("hot_spare") or {}).get("drive") or {})
    if spare:
        spare_path = str(spare.get("path") or spare.get("drive_path") or "").strip()
        live = drive_by_path.get(spare_path) if spare_path else None
        data_array = by_role.get("data") or {}
        data_controller_path = str(data_array.get("controller_path") or "").strip()
        data_controller_name = storage_controller_label(controller_by_path.get(data_controller_path, {})) or str(data_array.get("controller_name") or data_controller_path or "(none)")
        if not live:
            problems.append(f"Hot spare drive {spare_path or '(missing path)'} was not found in the current inventory.")
        else:
            actual_path = str(live.get("controller_path") or "").strip()
            actual_name = storage_controller_label(controller_by_path.get(actual_path, {})) or live.get("controller_name") or actual_path or "(unknown controller)"
            if data_controller_path and actual_path != data_controller_path:
                problems.append(f"{storage_drive_label(live)} is on {actual_name}, but the data array is set to {data_controller_name}.")
            if used_paths.get(spare_path):
                problems.append(f"Drive path {spare_path} cannot be reused as both {used_paths[spare_path]} and hot spare.")
    if problems:
        raise ValueError(" ".join(problems))


def build_storage_display_drives(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    summary = summary or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    volumes = list(hpe.get("volumes", []) or []) + list(standard.get("volumes", []) or [])
    membership_by_bay: dict[str, list[str]] = {}
    spare_by_bay: dict[str, list[str]] = {}
    for volume in volumes:
        volume_label = storage_item_display_name(volume) or "Logical volume"
        if volume.get("raid_type"):
            volume_label = f"{volume_label} / RAID {volume.get('raid_type')}"
        for bay in volume.get("drive_bays", []) or []:
            membership_by_bay.setdefault(str(bay), []).append(volume_label)
        for bay in volume.get("spare_bays", []) or []:
            spare_by_bay.setdefault(str(bay), []).append(volume_label)

    display_drives = []
    for drive in build_storage_planning_drives(summary):
        bay = str(drive.get("bay") or drive.get("id") or "")
        memberships = membership_by_bay.get(bay, [])
        spare_for = spare_by_bay.get(bay, [])
        role = "Unassigned"
        if memberships and spare_for:
            role = f"{'; '.join(memberships)}; spare for {'; '.join(spare_for)}"
        elif memberships:
            role = "; ".join(memberships)
        elif spare_for:
            role = f"Spare for {'; '.join(spare_for)}"
        display_drives.append({**drive, "volume_membership": role})
    return display_drives


def build_raid_plan(discovery: dict, discovery_paths: dict[str, Path], overrides: dict[str, Any] | None = None) -> dict:
    summary = discovery.get("summary", {})
    standard = summary.get("standard_redfish_storage", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    server = summary.get("server", {}) or {}
    warnings = []
    blockers = []
    overrides = overrides or {}

    controllers = []
    for source, items in (("hpe_smart_storage", hpe.get("controllers", [])), ("standard_redfish_storage", standard.get("controllers", []))):
        for item in items or []:
            controller_item = {**item, "source": source}
            if controller_item.get("firmware_version") is not None:
                controller_item = {**controller_item, "firmware_version": storage_firmware_display(controller_item.get("firmware_version"))}
            controller_item["label"] = storage_controller_label(controller_item)
            controllers.append(controller_item)
    controller_by_path = {str(item.get("path") or "").strip(): item for item in controllers if str(item.get("path") or "").strip()}
    requested_controller_path = str(overrides.get("controller_path") or "").strip()
    requested_os_controller_path = str(overrides.get("os_controller_path") or requested_controller_path or "").strip()
    requested_data_controller_path = str(overrides.get("data_controller_path") or requested_controller_path or "").strip()
    if not controllers:
        blockers.append("No detected storage controller is available for planning.")
    if requested_os_controller_path and requested_os_controller_path not in controller_by_path:
        warnings.append("The previously selected OS storage controller is no longer available. Using the best detected controller instead.")
        requested_os_controller_path = ""
    if requested_data_controller_path and requested_data_controller_path not in controller_by_path:
        warnings.append("The previously selected data storage controller is no longer available. Using the best detected controller instead.")
        requested_data_controller_path = ""
    controller = controller_by_path.get(requested_data_controller_path or requested_os_controller_path, controllers[0] if controllers else {})
    if len(controllers) > 1:
        warnings.append(
            "More than one storage controller was detected. OS and data arrays can be planned on different controllers."
        )

    hardware_profile = detect_hardware_profile(server, controllers)

    existing_volumes = []
    for source, items in (("hpe_smart_storage", hpe.get("volumes", [])), ("standard_redfish_storage", standard.get("volumes", []))):
        for item in items or []:
            existing_volumes.append({**item, "source": source})
    if existing_volumes:
        warnings.append("Existing logical volumes detected; default recommendation is wipe and rebuild before applying this target layout.")

    eligible_drives = []
    spare_candidate_drives = []
    excluded_drives = []
    for source, items in (("hpe_smart_storage", hpe.get("drives", [])), ("standard_redfish_storage", standard.get("drives", []))):
        for item in items or []:
            drive = normalized_plan_drive(item, source)
            if not str(drive.get("controller_path") or "").strip() and len(controller_by_path) == 1:
                drive["controller_path"] = next(iter(controller_by_path.keys()))
            drive_controller = controller_by_path.get(str(drive.get("controller_path") or "").strip(), {})
            drive["controller_name"] = storage_controller_label(drive_controller) if drive_controller else str(drive.get("controller_path") or "")
            drive["standby_spare"] = storage_status_is_standby_spare(str(drive.get("status") or ""))
            drive["absent"] = storage_status_is_absent(str(drive.get("status") or ""))
            if drive["size_gib"] <= 0:
                excluded_drives.append({**drive, "exclude_reason": "Missing or zero drive size."})
            elif drive["absent"]:
                excluded_drives.append({**drive, "exclude_reason": f"Drive is not present: {drive['status'] or 'absent'}."})
            elif not storage_status_is_eligible(drive["status"]):
                excluded_drives.append({**drive, "exclude_reason": f"Drive status is not eligible: {drive['status'] or 'unknown'}."})
            elif drive["standby_spare"]:
                spare_candidate_drives.append(drive)
                excluded_drives.append({**drive, "exclude_reason": "Already marked as a standby spare; shown separately and never auto-selected."})
            else:
                eligible_drives.append(drive)

    all_selectable_drives = list(eligible_drives) + list(spare_candidate_drives)
    warnings.extend(storage_profile_advisories(hardware_profile, controllers, all_selectable_drives))

    os_default_pool = [
        drive for drive in eligible_drives
        if not requested_os_controller_path or str(drive.get("controller_path") or "").strip() == requested_os_controller_path
    ]
    default_os_pair, default_os_explanation = choose_os_drive_pair(sorted(os_default_pool, key=storage_drive_sort_key))
    if requested_os_controller_path and not default_os_pair:
        warnings.append("No eligible OS drive pair was found on the selected OS controller.")

    default_os_ids = {storage_drive_identity(drive) for drive in default_os_pair}
    default_data_pool = [drive for drive in eligible_drives if storage_drive_identity(drive) not in default_os_ids]
    if requested_data_controller_path:
        default_data_pool = [
            drive for drive in default_data_pool
            if str(drive.get("controller_path") or "").strip() == requested_data_controller_path
        ]
    default_data_raid = choose_default_data_raid(default_data_pool)
    default_data_set, default_hot_spare, default_raid_excluded, default_data_explanation, default_data_blockers = choose_data_layout(
        sorted(default_data_pool, key=storage_drive_sort_key),
        default_data_raid,
    )

    eligible_by_identity = {storage_drive_identity(drive): drive for drive in all_selectable_drives if storage_drive_identity(drive)}
    eligible_by_bay = {str(drive.get("bay") or ""): drive for drive in all_selectable_drives}
    eligible_by_bay_by_controller: dict[str, dict[str, dict[str, Any]]] = {}
    for drive in all_selectable_drives:
        controller_path = str(drive.get("controller_path") or "").strip()
        bay = str(drive.get("bay") or "")
        eligible_by_bay_by_controller.setdefault(controller_path, {})[bay] = drive
    bay_counts: dict[str, int] = {}
    for drive in all_selectable_drives:
        bay = str(drive.get("bay") or "").strip()
        if bay:
            bay_counts[bay] = bay_counts.get(bay, 0) + 1
    duplicate_bays = sorted([bay for bay, count in bay_counts.items() if count > 1], key=lambda item: (int(item) if item.isdigit() else 999999, item))
    if duplicate_bays:
        warnings.append(
            "Duplicate bay numbers detected in this storage discovery: "
            f"{', '.join(duplicate_bays)}. Drive selections use Redfish path or serial number, not bay number."
        )
    selected_os_ids = [str(item).strip() for item in list(overrides.get("os_drive_ids") or []) if str(item).strip()]
    selected_data_ids = [str(item).strip() for item in list(overrides.get("data_drive_ids") or []) if str(item).strip()]
    selected_spare_id = str(overrides.get("hot_spare_drive_id") or "").strip()
    selected_os_paths = [str(item).strip() for item in list(overrides.get("os_drive_paths") or []) if str(item).strip()]
    selected_data_paths = [str(item).strip() for item in list(overrides.get("data_drive_paths") or []) if str(item).strip()]
    selected_spare_path = str(overrides.get("hot_spare_path") or "").strip()
    if not selected_os_ids:
        selected_os_ids = selected_os_paths
    if not selected_data_ids:
        selected_data_ids = selected_data_paths
    if not selected_spare_id:
        selected_spare_id = selected_spare_path
    # Path-first selection is the canonical model now. Keep legacy ids only as fallback.
    if selected_os_paths:
        selected_os_ids = list(selected_os_paths)
    if selected_data_paths:
        selected_data_ids = list(selected_data_paths)
    if selected_spare_path:
        selected_spare_id = selected_spare_path
    selected_os_controller_path = requested_os_controller_path
    selected_data_controller_path = requested_data_controller_path
    selected_os_bays = [str(item).strip() for item in list(overrides.get("os_bays") or []) if str(item).strip()]
    selected_data_bays = [str(item).strip() for item in list(overrides.get("data_bays") or []) if str(item).strip()]
    selected_spare_bay = str(overrides.get("hot_spare_bay") or "").strip()
    raw_os_raid = overrides["os_raid_level"] if "os_raid_level" in overrides else ""
    selected_os_raid = normalize_raid_choice("os", str(raw_os_raid or ""), allow_empty=True)
    if not selected_os_raid:
        selected_os_raid = "RAID1"
    raw_data_raid = overrides["data_raid_level"] if "data_raid_level" in overrides else default_data_raid
    selected_data_raid = normalize_raid_choice("data", str(raw_data_raid or ""), allow_empty=True)

    customization_active = bool(
        selected_os_ids
        or selected_data_ids
        or selected_spare_id
        or selected_os_bays
        or selected_data_bays
        or selected_spare_bay
        or selected_os_raid != "RAID1"
        or selected_data_raid != "RAID6"
    )
    os_pair = list(default_os_pair)
    os_explanation = default_os_explanation
    data_set = list(default_data_set)
    hot_spare = dict(default_hot_spare) if default_hot_spare else {}
    raid_excluded = list(default_raid_excluded)
    data_explanation = default_data_explanation

    if customization_active:
        custom_blockers = []
        overlap_blockers = []
        if not selected_data_raid and not selected_data_ids and not selected_data_bays:
            data_set = []
            hot_spare = {}
            data_explanation = "This section is not used in the current plan."
        if selected_os_ids:
            os_pair = [eligible_by_identity[drive_id] for drive_id in selected_os_ids if drive_id in eligible_by_identity and storage_drive_is_array_selectable(eligible_by_identity[drive_id])]
            missing_os = [drive_id for drive_id in selected_os_ids if drive_id not in eligible_by_identity or not storage_drive_is_array_selectable(eligible_by_identity[drive_id])]
            if missing_os:
                custom_blockers.append(f"Selected OS drives are not eligible or were not found by drive path: {', '.join(missing_os)}.")
            os_explanation = "Using the drives chosen below for the OS mirror."
        elif selected_os_bays:
            os_bay_lookup = eligible_by_bay_by_controller.get(selected_os_controller_path, eligible_by_bay) if selected_os_controller_path else eligible_by_bay
            os_pair = [os_bay_lookup[bay] for bay in selected_os_bays if bay in os_bay_lookup]
            missing_os = [bay for bay in selected_os_bays if bay not in os_bay_lookup]
            if missing_os:
                custom_blockers.append(f"Selected OS drives are not eligible or were not found: {', '.join(missing_os)}.")
            os_explanation = "Using the drives chosen below for the OS mirror."
        if selected_data_ids:
            data_set = [eligible_by_identity[drive_id] for drive_id in selected_data_ids if drive_id in eligible_by_identity and storage_drive_is_array_selectable(eligible_by_identity[drive_id])]
            missing_data = [drive_id for drive_id in selected_data_ids if drive_id not in eligible_by_identity or not storage_drive_is_array_selectable(eligible_by_identity[drive_id])]
            if missing_data:
                custom_blockers.append(f"Selected data drives are not eligible or were not found by drive path: {', '.join(missing_data)}.")
            data_explanation = "Using the drives chosen below for the data array."
        elif selected_data_bays:
            data_bay_lookup = eligible_by_bay_by_controller.get(selected_data_controller_path, eligible_by_bay) if selected_data_controller_path else eligible_by_bay
            data_set = [data_bay_lookup[bay] for bay in selected_data_bays if bay in data_bay_lookup]
            missing_data = [bay for bay in selected_data_bays if bay not in data_bay_lookup]
            if missing_data:
                custom_blockers.append(f"Selected data drives are not eligible or were not found: {', '.join(missing_data)}.")
            data_explanation = "Using the drives chosen below for the data array."
        if selected_spare_id:
            hot_spare = dict(eligible_by_identity.get(selected_spare_id) or {})
            if hot_spare and not storage_drive_is_spare_selectable(hot_spare):
                hot_spare = {}
            if not hot_spare:
                custom_blockers.append(f"Selected hot spare drive path was not eligible or was not found: {selected_spare_id}.")
        elif selected_spare_bay:
            spare_bay_lookup = eligible_by_bay_by_controller.get(selected_data_controller_path, eligible_by_bay) if selected_data_controller_path else eligible_by_bay
            hot_spare = dict(spare_bay_lookup.get(selected_spare_bay) or {})
            if not hot_spare:
                custom_blockers.append(f"Selected hot spare bay was not eligible or was not found: {selected_spare_bay}.")
        os_identity_set = {storage_drive_identity(drive) for drive in os_pair if storage_drive_identity(drive)}
        data_identity_set = {storage_drive_identity(drive) for drive in data_set if storage_drive_identity(drive)}
        spare_identity = storage_drive_identity(hot_spare) if hot_spare else ""
        if len(os_identity_set) != len(os_pair) or len(data_identity_set) != len(data_set) or (hot_spare and not spare_identity):
            custom_blockers.append("Every selected drive must have a stable drive path before it can be approved.")
        selected_identities = [storage_drive_identity(drive) for drive in os_pair + data_set + ([hot_spare] if hot_spare else []) if storage_drive_identity(drive)]
        if len(selected_identities) != len(set(selected_identities)):
            overlap_blockers.append("The same drive path cannot be reused in the OS, data, or hot spare selections.")
        os_controller_set = {str(drive.get("controller_path") or "").strip() for drive in os_pair if str(drive.get("controller_path") or "").strip()}
        data_controller_set = {str(drive.get("controller_path") or "").strip() for drive in data_set if str(drive.get("controller_path") or "").strip()}
        if len(os_controller_set) > 1:
            custom_blockers.append("The OS array cannot span multiple storage controllers.")
        if len(data_controller_set) > 1:
            custom_blockers.append("The data array cannot span multiple storage controllers.")
        if selected_os_controller_path and os_controller_set and os_controller_set != {selected_os_controller_path}:
            for drive in os_pair:
                drive_controller_path = str(drive.get("controller_path") or "").strip()
                if drive_controller_path == selected_os_controller_path:
                    continue
                drive_controller = controller_by_path.get(drive_controller_path, {})
                selected_controller = controller_by_path.get(selected_os_controller_path, {})
                custom_blockers.append(
                    f"{storage_drive_label(drive)} is on "
                    f"{storage_controller_label(drive_controller) or drive_controller_path}, "
                    f"not {storage_controller_label(selected_controller) or selected_os_controller_path}."
                )
        if selected_data_controller_path and data_controller_set and data_controller_set != {selected_data_controller_path}:
            for drive in data_set:
                drive_controller_path = str(drive.get("controller_path") or "").strip()
                if drive_controller_path == selected_data_controller_path:
                    continue
                drive_controller = controller_by_path.get(drive_controller_path, {})
                selected_controller = controller_by_path.get(selected_data_controller_path, {})
                custom_blockers.append(
                    f"{storage_drive_label(drive)} is on "
                    f"{storage_controller_label(drive_controller) or drive_controller_path}, "
                    f"not {storage_controller_label(selected_controller) or selected_data_controller_path}."
                )
        if os_identity_set and data_identity_set and (os_identity_set & data_identity_set):
            overlap_blockers.append("The same drive cannot be used for both the OS mirror and the data array.")
        if spare_identity and spare_identity in (os_identity_set | data_identity_set):
            overlap_blockers.append("The hot spare must be different from the OS and data drives.")
        if (selected_spare_id or selected_spare_bay) and not data_set:
            custom_blockers.append("Choose data drives before assigning a dedicated hot spare.")
        custom_blockers.extend(validate_raid_drive_count(selected_os_raid, os_pair, section="os"))
        custom_blockers.extend(validate_raid_drive_count(selected_data_raid, data_set, section="data"))
        if hot_spare:
            compatibility_group = {drive_group_key(drive) for drive in data_set + [hot_spare]}
        else:
            compatibility_group = {drive_group_key(drive) for drive in data_set}
        if hot_spare and selected_data_controller_path:
            spare_controller_path = str(hot_spare.get("controller_path") or "").strip()
            if spare_controller_path and spare_controller_path != selected_data_controller_path:
                spare_controller = controller_by_path.get(spare_controller_path, {})
                data_controller = controller_by_path.get(selected_data_controller_path, {})
                custom_blockers.append(
                    "Selected hot spare belongs to a different controller. "
                    f"spare={storage_controller_label(spare_controller) or spare_controller_path} "
                    f"data={storage_controller_label(data_controller) or selected_data_controller_path}"
                )
        if data_set and len(compatibility_group) > 1:
            warnings.append("The selected data drives and hot spare differ by media type, protocol, or size. Review the layout before approving.")
        blockers.extend(custom_blockers + overlap_blockers)
        selected_identity_set = set(selected_identities)
        raid_excluded = [
            {**drive, "exclude_reason": "Not selected for the custom data layout."}
            for drive in all_selectable_drives
            if storage_drive_identity(drive) not in selected_identity_set
        ]
        warnings.append("This plan was customized from the default drive selection.")

    excluded_drives.extend({**drive, "exclude_reason": "Reserved for OS RAID 1 pair."} for drive in os_pair)
    if hot_spare:
        excluded_drives.append({**hot_spare, "exclude_reason": "Reserved as the data-side hot spare."})
    excluded_drives.extend(raid_excluded)
    if not customization_active:
        blockers.extend(default_data_blockers)
    blockers.extend(validate_raid_drive_count(selected_os_raid, os_pair, section="os"))
    blockers.extend(validate_raid_drive_count(selected_data_raid, data_set, section="data"))
    os_controller_paths = sorted({str(drive.get("controller_path") or "").strip() for drive in os_pair if str(drive.get("controller_path") or "").strip()})
    data_controller_paths = sorted({str(drive.get("controller_path") or "").strip() for drive in data_set if str(drive.get("controller_path") or "").strip()})
    if len(os_controller_paths) > 1:
        blockers.append("The OS array cannot span multiple storage controllers.")
    if len(data_controller_paths) > 1:
        blockers.append("The data array cannot span multiple storage controllers.")
    if os_pair and len({drive_group_key(drive) for drive in os_pair}) > 1:
        warnings.append("The selected OS drives differ by media type, protocol, or size. Review the layout before approving.")
    if data_set and len({drive_group_key(drive) for drive in data_set}) > 1:
        warnings.append("The selected data drives differ by media type, protocol, or size. Review the layout before approving.")
    selected_os_controller_path = selected_os_controller_path or (os_controller_paths[0] if os_controller_paths else "")
    selected_data_controller_path = selected_data_controller_path or (data_controller_paths[0] if data_controller_paths else "")
    if not selected_os_controller_path and len(controllers) == 1:
        selected_os_controller_path = str((controllers[0] or {}).get("path") or "")
    if not selected_data_controller_path and len(controllers) == 1:
        selected_data_controller_path = str((controllers[0] or {}).get("path") or "")
    os_controller = controller_by_path.get(selected_os_controller_path, {})
    data_controller = controller_by_path.get(selected_data_controller_path, {})
    controller = data_controller or os_controller or controller
    if not os_pair and not data_set:
        blockers.append("Choose drives for at least one array.")

    apply_readiness = {
        "next_action": "wipe and rebuild" if existing_volumes else "create only",
        "create_only_ready": not existing_volumes and not validate_raid_drive_count(selected_os_raid, os_pair, section="os") and not validate_raid_drive_count(selected_data_raid, data_set, section="data"),
        "wipe_rebuild_ready": not validate_raid_drive_count(selected_os_raid, os_pair, section="os") and not validate_raid_drive_count(selected_data_raid, data_set, section="data"),
        # Future apply split:
        # - Gen10 / iLO 5 typically uses HPE SmartStorageConfig-style delete/create/apply semantics.
        # - Gen11 / iLO 6 typically uses the standard Redfish Storage/Volume model.
        # This pass only prepares the confirmation and action selection surface; it does not issue writes.
        "typed_confirmation": STORAGE_APPLY_CONFIRM_WIPE,
        "create_only_confirmation": STORAGE_APPLY_CONFIRM_CREATE,
        "wipe_rebuild_confirmation": STORAGE_APPLY_CONFIRM_WIPE,
    }
    create_only_blockers = []
    if existing_volumes:
        create_only_blockers.append("Existing logical volumes are present. Create-only is disabled until the controller is empty.")
    create_only_blockers.extend(validate_raid_drive_count(selected_os_raid, os_pair, section="os"))
    create_only_blockers.extend(validate_raid_drive_count(selected_data_raid, data_set, section="data"))
    wipe_rebuild_blockers = []
    wipe_rebuild_blockers.extend(validate_raid_drive_count(selected_os_raid, os_pair, section="os"))
    wipe_rebuild_blockers.extend(validate_raid_drive_count(selected_data_raid, data_set, section="data"))
    apply_readiness["create_only_blockers"] = create_only_blockers
    apply_readiness["wipe_rebuild_blockers"] = wipe_rebuild_blockers
    planned_layout = {
        "os_raid1": {
            "raid": raid_label(selected_os_raid),
            "target_size_gib": 500,
            "controller_path": selected_os_controller_path,
            "controller": os_controller,
            "bays": plan_drive_bays(os_pair),
            "drives": os_pair,
        },
        "data_raid6": {
            "raid": raid_label(selected_data_raid),
            "controller_path": selected_data_controller_path,
            "controller": data_controller,
            "bays": plan_drive_bays(data_set),
            "capacity_intent": "Use the selected compatible eligible drives for the data array.",
            "drives": data_set,
        },
        "hot_spare": {
            "required": False,
            "bay": str(hot_spare.get("bay") or hot_spare.get("id") or "") if hot_spare else "",
            "drive": hot_spare,
        },
    }
    arrays = []
    if os_pair:
        arrays.append(
            storage_plan_array(
                role="os",
                name="OS array",
                raid_level=selected_os_raid,
                controller=os_controller,
                drives=os_pair,
                target_size_gib=500,
            )
        )
    if data_set:
        arrays.append(
            storage_plan_array(
                role="data",
                name="Data array",
                raid_level=selected_data_raid,
                controller=data_controller,
                drives=data_set,
            )
        )
    hot_spare_summary = {
        "required": False,
        "drive": hot_spare,
        "reserved": bool(hot_spare),
        "drive_path": str((hot_spare or {}).get("path") or ""),
        "controller_path": str((hot_spare or {}).get("controller_path") or ""),
        "controller_name": str((hot_spare or {}).get("controller_name") or ""),
        "selected_drive_metadata": storage_drive_metadata(hot_spare) if hot_spare else {},
    }
    pre_apply_summary = {
        "mode": apply_readiness["next_action"],
        "volumes_to_remove": existing_volumes,
        "planned_layout": planned_layout,
        "reserved_hot_spare": hot_spare,
        "arrays": arrays,
    }

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "read_only": True,
        "source_discovery": {
            "host": discovery.get("raw", {}).get("source_host", ""),
            "serial_number": server.get("serial_number", ""),
            "server_model": server.get("model", ""),
            "controller": controller,
            "controllers": controllers,
            "os_controller": os_controller,
            "data_controller": data_controller,
            "directory": str(discovery_paths["directory"]),
            "summary": str(discovery_paths["summary"]),
            "raw": str(discovery_paths["raw"]),
        },
        "existing_logical_volumes_detected": bool(existing_volumes),
        "default_recommendation": "wipe and rebuild" if existing_volumes else "create only",
        "existing_logical_volumes": existing_volumes,
        "desired_layout": {
            "os_volume": {"raid": raid_label(selected_os_raid), "target_size_gib": 500},
            "data_volume": {"raid": raid_label(selected_data_raid), "capacity": "selected compatible eligible drives"},
            "hot_spare": {"required": False, "scope": "optional dedicated spare if selected"},
        },
        "hardware_profile": hardware_profile,
        "profile_advisories": storage_profile_advisories(hardware_profile, controllers, all_selectable_drives),
        "customization": {
            "active": customization_active,
            "selected_controller_path": str(controller.get("path") or ""),
            "selected_os_controller_path": selected_os_controller_path,
            "selected_data_controller_path": selected_data_controller_path,
            "selected_os_raid_level": selected_os_raid,
            "selected_data_raid_level": selected_data_raid,
            "selected_os_drive_ids": [storage_drive_identity(drive) for drive in os_pair if storage_drive_identity(drive)],
            "selected_data_drive_ids": [storage_drive_identity(drive) for drive in data_set if storage_drive_identity(drive)],
            "selected_hot_spare_drive_id": storage_drive_identity(hot_spare) if hot_spare else "",
            "selected_os_drive_paths": [str(drive.get("path") or "") for drive in os_pair if str(drive.get("path") or "")],
            "selected_data_drive_paths": [str(drive.get("path") or "") for drive in data_set if str(drive.get("path") or "")],
            "selected_hot_spare_path": str((hot_spare or {}).get("path") or ""),
            "selected_os_bays": [str(drive.get("bay") or "") for drive in os_pair],
            "selected_data_bays": [str(drive.get("bay") or "") for drive in data_set],
            "selected_hot_spare_bay": str((hot_spare or {}).get("bay") or ""),
        },
        "arrays": arrays,
        "planned_layout": planned_layout,
        "os_raid1": {"raid": selected_os_raid, "label": raid_label(selected_os_raid), "target_size_gib": 500, "drives": os_pair, "explanation": os_explanation},
        "data_raid6": {"raid": selected_data_raid, "label": raid_label(selected_data_raid), "feasible": not validate_raid_drive_count(selected_data_raid, data_set, section="data"), "drives": data_set, "drive_count": len(data_set), "explanation": data_explanation},
        "hot_spare": hot_spare_summary,
        "apply_readiness": apply_readiness,
        "pre_apply_summary": pre_apply_summary,
        "excluded_drives": sorted(excluded_drives, key=storage_drive_sort_key),
        "warnings": warnings,
        "blockers": blockers,
        "valid": not blockers,
    }


def export_raid_plan_snapshot(cfg: dict, plan: dict, discovery_paths: dict[str, Path]) -> dict[str, Path]:
    plan_path = discovery_paths["directory"] / "raid-plan.yml"
    payload = {
        "export_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kit_name": sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01")),
        "source_discovery": plan.get("source_discovery", {}),
        "plan": plan,
    }
    with open(plan_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)

    return {
        "directory": discovery_paths["directory"],
        "plan": plan_path,
    }


def append_storage_apply_history_snapshot(cfg: dict):
    append_job_history_snapshot(cfg, "storage-apply")


def storage_apply_step_targets_text(targets: dict[str, Any] | None) -> str:
    if not targets:
        return ""
    bits = []
    controller = (targets.get("controller") or "").strip()
    if controller:
        bits.append(f"controller={controller}")
    bays = targets.get("bays")
    if isinstance(bays, list) and bays:
        bits.append(f"bays={', '.join(str(item) for item in bays)}")
    elif bays:
        bits.append(f"bays={bays}")
    volume = (targets.get("volume") or "").strip()
    if volume:
        bits.append(f"volume={volume}")
    path = (targets.get("path") or "").strip()
    if path:
        bits.append(f"path={path}")
    return " | ".join(bits)


def storage_reboot_result_summary(result: dict[str, Any]) -> str:
    allowed = ", ".join(str(item) for item in list(result.get("allowed_reset_types") or [])) or "unknown"
    expected = str(result.get("expected_final_power_state") or "On")
    final_state = str(result.get("final_power_state") or result.get("recovery_power_state") or "unknown")
    first_state = str(result.get("first_observed_power_state") or result.get("initial_power_state") or "unknown")
    connection_dropped = bool(result.get("recovered_after_transport_disconnect") or result.get("connection_dropped"))
    matched = bool(result.get("final_state_matched_expected") or final_state.lower() == expected.lower())
    return (
        f"ResetType={result.get('reset_type') or 'GracefulRestart'}; "
        f"allowed={allowed}; first PowerState={first_state}; "
        f"connection_dropped={'yes' if connection_dropped else 'no'}; "
        f"final PowerState={final_state}; expected={expected}; matched={'yes' if matched else 'no'}"
    )


def storage_apply_response_excerpt(response: Any) -> Any:
    if response is None:
        return None
    if not isinstance(response, dict):
        return response
    excerpt = {}
    for key in (
        "@odata.id",
        "Id",
        "Name",
        "Status",
        "Messages",
        "error",
        "apply_mode",
        "delete_count",
        "create_count",
        "hot_spare_location",
        "deleted_volume_paths",
        "volume_path",
        "volumes_path",
        "reset_target",
        "reset_type",
        "reboot_required",
        "settings_path",
        "device_discovery",
        "volume_capabilities",
        "logical_drive_kind",
        "assigned_bay",
    ):
        if key in response:
            excerpt[key] = response.get(key)
    return excerpt or response


def storage_workflow_presentation(
    workflow_state: str,
    apply_state: dict[str, Any] | None = None,
    reboot_state: dict[str, Any] | None = None,
) -> tuple[str, str]:
    del reboot_state
    apply_state = apply_state or {}
    post_validation = str(apply_state.get("post_reboot_validation") or "").strip()
    mapping = {
        "queued": ("Ready to start", "Storage setup is queued and waiting to start."),
        "running_apply": ("Working on storage", "Storage changes are being prepared now. You can follow along below."),
        "staged_reboot_required": ("Restart needed to finish", "Storage changes are staged, but they will not finish until the server restarts."),
        "reboot_requested": ("Reboot requested", "Reboot has been requested. Waiting for the server reboot workflow to begin."),
        "waiting_for_reboot_start": ("Waiting for reboot start", "The reboot request was sent. Waiting for the server to leave its current running state."),
        "waiting_for_server_return": ("Waiting for server to return", "The server has started rebooting. Waiting for Redfish and the system inventory to come back."),
        "post_reboot_validation_pending": ("Post-reboot validation pending", "The server is back. Capturing post-reboot storage discovery and validation now."),
        "post_reboot_validation_complete": ("Ready to run", "Storage setup finished and the post-restart check is complete."),
        "apply_complete": ("Ready to run", "Storage setup finished and no restart is needed."),
        "reboot_failed": ("Reboot failed", "The reboot workflow failed. Review the live log and reboot artifacts, then retry if appropriate."),
        "apply_failed": ("Setup failed", "Storage setup stopped before it could finish."),
        "idle": ("Start here", "Read the current storage first, then review the proposed setup."),
    }
    label, summary = mapping.get(workflow_state or "idle", ("Idle", "Storage workflow state will update here while the run is active."))
    if workflow_state == "post_reboot_validation_pending" and post_validation:
        summary = f"{summary} Current validation state: {post_validation}."
    return label, summary


def storage_workflow_progress_percent(workflow_state: str, completed: int = 0, total: int = 0) -> int:
    workflow_state = str(workflow_state or "").strip() or "idle"
    if workflow_state in {"post_reboot_validation_complete", "apply_complete"}:
        return 100
    if workflow_state == "queued":
        return 0
    if workflow_state == "running_apply":
        if total <= 0:
            return 5
        return max(1, min(64, int((completed / total) * 64)))
    fixed = {
        "staged_reboot_required": 68,
        "reboot_requested": 72,
        "waiting_for_reboot_start": 78,
        "waiting_for_server_return": 86,
        "post_reboot_validation_pending": 94,
    }
    if workflow_state in fixed:
        return fixed[workflow_state]
    if workflow_state in {"apply_failed", "reboot_failed"}:
        if total <= 0:
            return 1
        return max(1, min(99, int((completed / total) * 99)))
    return max(0, min(99, int((completed / total) * 99))) if total else 0


def save_storage_apply_state(apply_state: dict[str, Any], apply_paths: dict[str, Path]) -> None:
    log_payload = {
        "started_at": apply_state.get("started_at", ""),
        "finished_at": apply_state.get("finished_at", ""),
        "mode": apply_state.get("mode", ""),
        "status": apply_state.get("status", ""),
        "apply_path": apply_state.get("apply_path", ""),
        "controller": apply_state.get("controller", {}),
        "steps": apply_state.get("steps", []),
        "errors": apply_state.get("errors", []),
        "workflow_state": apply_state.get("workflow_state", ""),
        "reboot_status": apply_state.get("reboot_status", ""),
        "reboot_requested": apply_state.get("reboot_requested", False),
        "reboot_required": apply_state.get("reboot_required", False),
        "post_reboot_validation": apply_state.get("post_reboot_validation", ""),
    }
    with open(apply_paths["apply_log"], "w", encoding="utf-8") as f:
        yaml.safe_dump(log_payload, f, sort_keys=False)
    with open(apply_paths["apply_results"], "w", encoding="utf-8") as f:
        json.dump(apply_state, f, indent=2, sort_keys=False)


def save_storage_reboot_state(reboot_state: dict[str, Any], apply_paths: dict[str, Path]) -> None:
    with open(apply_paths["reboot_results"], "w", encoding="utf-8") as f:
        json.dump(reboot_state, f, indent=2, sort_keys=False)


def record_storage_apply_step(
    kit_name: str,
    job: dict[str, Any],
    apply_state: dict[str, Any],
    apply_paths: dict[str, Path],
    step_name: str,
    completed: int,
    total: int,
    status: str,
    current_stage: str,
    targets: dict[str, Any] | None = None,
    details: str = "",
    error: str = "",
    response: Any = None,
    progress_percent: int | None = None,
) -> None:
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "step": step_name,
        "status": status,
        "controller": apply_state.get("controller", {}),
        "targets": targets or {},
        "details": details,
        "error": error,
        "response": storage_apply_response_excerpt(response),
    }
    apply_state.setdefault("steps", []).append(entry)
    if error:
        apply_state.setdefault("errors", []).append({"step": step_name, "error": error})
    if response is not None:
        apply_state.setdefault("responses", []).append({"step": step_name, "response": storage_apply_response_excerpt(response)})
    save_storage_apply_state(apply_state, apply_paths)
    job["apply_path"] = apply_state.get("apply_path", "")
    job["reboot_required"] = bool(apply_state.get("reboot_required"))
    job["workflow_state"] = apply_state.get("workflow_state", "")
    job["reboot_status"] = apply_state.get("reboot_status", "")
    job["storage_run_directory"] = str(apply_paths.get("directory", ""))

    status_prefix = {
        "running": "[RUNNING]",
        "ok": "[OK]",
        "skip": "[SKIP]",
        "failed": "[FAILED]",
    }.get(status.lower(), "[INFO]")
    target_text = storage_apply_step_targets_text(targets)
    line = f"{status_prefix} {step_name}"
    if target_text:
        line += f" | {target_text}"
    if details:
        line += f" | {details}"
    if error:
        line += f" | error={error}"
    workflow_state = apply_state.get("workflow_state", "")
    if status.lower() == "failed":
        workflow_state = workflow_state or "apply_failed"
    update_job(
        kit_name,
        job,
        "Failed" if status.lower() == "failed" else "Running",
        current_stage,
        completed,
        total,
        line,
        progress_percent=progress_percent if progress_percent is not None else storage_workflow_progress_percent(workflow_state, completed, total),
    )


def build_storage_apply_intent(plan: dict, apply_mode: str) -> dict[str, Any]:
    arrays = storage_plan_arrays(plan)
    arrays_intent = []
    for array in arrays:
        role = str(array.get("role") or "custom")
        raid_level = normalize_raid_choice("os" if role == "os" else "data", str(array.get("raid_level") or array.get("raid") or ""), allow_empty=True)
        label_prefix = "OS" if role == "os" else "Data" if role == "data" else str(array.get("name") or role.title())
        arrays_intent.append(
            {
                "role": role,
                "name": str(array.get("name") or f"{label_prefix} array"),
                "raid": raid_level,
                "label": f"{label_prefix} {raid_label(raid_level)} logical drive",
                "target_size_gib": array.get("target_size_gib", 500 if role == "os" else None),
                "controller_path": str(array.get("controller_path") or ""),
                "controller_name": str(array.get("controller_name") or ""),
                "bays": [drive.get("bay") for drive in array.get("drives", [])],
                "drive_paths": [drive.get("path") for drive in array.get("drives", [])],
                "drives": list(array.get("drives", []) or []),
                "selected_drive_metadata": list(array.get("selected_drive_metadata") or []),
            }
        )
    by_role = {str(item.get("role") or ""): item for item in arrays_intent}
    controller = plan.get("source_discovery", {}).get("controller", {}) or {}
    return {
        "mode": apply_mode,
        "controller": controller,
        "arrays": arrays_intent,
        "os_raid1": by_role.get("os", {"raid": "", "label": "OS logical drive", "target_size_gib": 500, "bays": [], "drive_paths": [], "drives": []}),
        "data_raid6": by_role.get("data", {"raid": "", "label": "Data logical drive", "bays": [], "drive_paths": [], "drives": []}),
        "hot_spare": {
            "bay": (plan.get("hot_spare", {}).get("drive", {}) or {}).get("bay", ""),
            "drive_path": (plan.get("hot_spare", {}).get("drive", {}) or {}).get("path", ""),
            "drive": dict((plan.get("hot_spare", {}).get("drive", {}) or {})),
        },
        "volumes_to_remove": [
            {
                "name": volume.get("name") or volume.get("id") or "",
                "path": volume.get("path") or "",
                "raid_type": volume.get("raid_type") or "",
            }
            for volume in plan.get("existing_logical_volumes", [])
        ],
    }


def storage_discovery_sources(discovery: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    summary = discovery.get("summary", {}) or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    controllers = []
    drives = []
    volumes = []
    for source, section in (("hpe_smart_storage", hpe), ("standard_redfish_storage", standard)):
        for controller in section.get("controllers", []) or []:
            controllers.append({**controller, "source": source})
        for drive in section.get("drives", []) or []:
            drives.append({**drive, "source": source})
        for volume in section.get("volumes", []) or []:
            volumes.append({**volume, "source": source})
    return {"controllers": controllers, "drives": drives, "volumes": volumes}


def storage_discovered_options(discovery: dict[str, Any]) -> dict[str, Any]:
    sources = storage_discovery_sources(discovery)
    raw_storage = ((discovery.get("raw") or {}).get("standard_storage") or [])
    writable_volume_paths = [
        str(((item.get("Volumes") or {}).get("@odata.id")) or "").rstrip("/")
        for item in raw_storage
        if str(((item.get("Volumes") or {}).get("@odata.id")) or "").strip()
    ]
    capabilities = ((discovery.get("summary") or {}).get("capabilities") or {})
    settings_path, settings_reason = _verified_hpe_smartstorage_settings_path(capabilities)
    return {
        "controllers": [
            {
                "path": item.get("path", ""),
                "name": item.get("name", ""),
                "model": item.get("model", ""),
                "source": item.get("source", ""),
            }
            for item in sources["controllers"]
        ],
        "writable_volume_paths": writable_volume_paths,
        "hpe_smartstorage_settings_path": settings_path,
        "inventory_only_reason": settings_reason if capabilities.get("hpe_smart_storage") and not settings_path else "",
    }


def storage_controller_signature(controller: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(controller.get("source") or "").strip().lower(),
        str(controller.get("model") or "").strip().lower(),
        str(controller.get("name") or "").strip().lower(),
        str(controller.get("manufacturer") or "").strip().lower(),
    )


def storage_selected_plan_drives(plan: dict[str, Any]) -> list[dict[str, Any]]:
    drives: list[dict[str, Any]] = []
    for array in storage_plan_arrays(plan):
        for drive in list(array.get("drives") or []):
            if drive:
                drives.append({**drive, "_section": str(array.get("role") or "custom")})
    spare = (plan.get("hot_spare", {}) or {}).get("drive", {}) or {}
    if spare:
        drives.append({**spare, "_section": "hot_spare"})
    return drives


def _normalized_drive_path_set(items: list[Any]) -> set[str]:
    values: set[str] = set()
    for item in items or []:
        text = str(item or "").strip()
        if text:
            values.add(text)
    return values


def storage_live_layout_matches_plan(plan: dict[str, Any], live_discovery: dict[str, Any]) -> tuple[bool, list[str]]:
    arrays = storage_plan_arrays(plan)
    live_volumes = list(storage_discovery_sources(live_discovery)["volumes"] or [])
    notes: list[str] = []
    if not arrays:
        return False, ["No planned arrays were found."]

    expected_controller_paths = {str(array.get("controller_path") or "").strip() for array in arrays if str(array.get("controller_path") or "").strip()}
    live_selected = [volume for volume in live_volumes if str(volume.get("controller_path") or "").strip() in expected_controller_paths]
    if len(live_selected) != len(arrays):
        return False, [f"Expected {len(arrays)} live volume(s) on the selected controllers, found {len(live_selected)}."]

    live_by_controller = {str(volume.get("controller_path") or "").strip(): volume for volume in live_selected}
    for array in arrays:
        controller_path = str(array.get("controller_path") or "").strip()
        role = str(array.get("role") or "custom")
        live_volume = live_by_controller.get(controller_path)
        if not live_volume:
            return False, [f"No live volume was found on controller {controller_path} for the {role} array."]
        planned_raid = str(array.get("raid_level") or array.get("raid") or "").strip().upper()
        live_raid = str(live_volume.get("raid_type") or "").strip().upper()
        if planned_raid and live_raid != planned_raid:
            return False, [f"Controller {controller_path} has {live_raid or 'no RAID type'} instead of {planned_raid} for the {role} array."]
        planned_drives = _normalized_drive_path_set([drive.get("path") or drive.get("drive_path") for drive in list(array.get("drives") or [])])
        live_drives = _normalized_drive_path_set(list(live_volume.get("drive_paths") or []))
        if planned_drives and live_drives != planned_drives:
            return False, [f"Controller {controller_path} drive membership does not match the approved {role} array."]
        if role == "data":
            planned_spare = str((((plan.get("hot_spare") or {}).get("drive") or {}).get("path")) or "").strip()
            live_spares = _normalized_drive_path_set(list(live_volume.get("spare_paths") or []))
            if planned_spare:
                if live_spares != {planned_spare}:
                    return False, [f"Controller {controller_path} dedicated spare does not match the approved data spare."]
            elif live_spares:
                return False, [f"Controller {controller_path} has a dedicated spare in live discovery, but the approved plan does not."]
        notes.append(
            f"{controller_path}: {live_volume.get('name') or live_volume.get('display_name') or live_volume.get('id') or 'volume'} {live_raid}"
        )
    return True, notes


def _drive_identity_matches(saved: dict[str, Any], live: dict[str, Any]) -> tuple[bool, str]:
    bay = str(saved.get("bay") or "").strip()
    live_bay = str(live.get("bay") or "").strip()
    if bay and live_bay and bay != live_bay:
        return False, f"Bay changed from {bay} to {live_bay}."

    saved_serial = str(saved.get("serial_number") or "").strip()
    live_serial = str(live.get("serial_number") or "").strip()
    if saved_serial and live_serial and saved_serial != live_serial:
        return False, f"Bay {bay or live_bay or '?'} drive serial changed from {saved_serial} to {live_serial}."
    if saved_serial and not live_serial:
        return False, f"Bay {bay or live_bay or '?'} approved drive serial {saved_serial} is not visible in live discovery."

    saved_model = str(saved.get("model") or "").strip()
    live_model = str(live.get("model") or "").strip()
    if saved_model and live_model and saved_model != live_model:
        return False, f"Bay {bay or live_bay or '?'} drive model changed from {saved_model} to {live_model}."

    try:
        saved_size = float(saved.get("size_gib") or 0)
        live_size = float(live.get("size_gib") or 0)
    except Exception:
        saved_size = 0
        live_size = 0
    if saved_size and live_size and abs(saved_size - live_size) > 1:
        return False, f"Bay {bay or live_bay or '?'} drive size changed from {saved_size:g} GiB to {live_size:g} GiB."
    return True, ""


def _candidate_drives_for_controller(discovery: dict[str, Any], controller: dict[str, Any]) -> list[dict[str, Any]]:
    path = str(controller.get("path") or "").rstrip("/")
    source = str(controller.get("source") or "").strip()
    drives = []
    for drive in storage_discovery_sources(discovery)["drives"]:
        if source and str(drive.get("source") or "").strip() != source:
            continue
        if path and not storage_item_matches_controller(drive, {"path": path, "source": source}):
            continue
        drives.append(drive)
    return drives


def _find_live_drive(saved_drive: dict[str, Any], live_drives: list[dict[str, Any]]) -> dict[str, Any]:
    saved_path = str(saved_drive.get("path") or saved_drive.get("drive_path") or "").strip()
    if saved_path:
        exact = next((drive for drive in live_drives if str(drive.get("path") or drive.get("drive_path") or "").strip() == saved_path), {})
        if exact:
            return exact
    saved_bay = str(saved_drive.get("bay") or "").strip()
    saved_serial = str(saved_drive.get("serial_number") or "").strip()
    bay_match = {}
    for drive in live_drives:
        if saved_bay and str(drive.get("bay") or "").strip() != saved_bay:
            continue
        if not bay_match:
            bay_match = drive
        if not saved_serial or str(drive.get("serial_number") or "").strip() == saved_serial:
            return drive
    return bay_match


def _storage_controller_candidates(plan: dict[str, Any], discovery: dict[str, Any]) -> list[dict[str, Any]]:
    saved_controller = (plan.get("source_discovery", {}) or {}).get("controller", {}) or {}
    saved_path = str(saved_controller.get("path") or "").rstrip("/")
    controllers = storage_discovery_sources(discovery)["controllers"]
    for controller in controllers:
        if saved_path and str(controller.get("path") or "").rstrip("/") == saved_path:
            return [controller]

    saved_signature = storage_controller_signature(saved_controller)
    model_matches = [
        controller
        for controller in controllers
        if storage_controller_signature(controller)[0] == saved_signature[0]
        and storage_controller_signature(controller)[1]
        and storage_controller_signature(controller)[1] == saved_signature[1]
    ]
    if model_matches:
        return model_matches

    source = str(saved_controller.get("source") or "").strip()
    same_source = [controller for controller in controllers if not source or str(controller.get("source") or "").strip() == source]
    return same_source or controllers


def storage_preflight_compare_and_remap(plan: dict[str, Any], live_discovery: dict[str, Any], apply_mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    remapped_plan = copy.deepcopy(plan)
    saved_controller = (plan.get("source_discovery", {}) or {}).get("controller", {}) or {}
    saved_controller_path = str(saved_controller.get("path") or "").rstrip("/")
    selected_drives = storage_selected_plan_drives(plan)
    differences: list[str] = []
    corrections: list[str] = []
    rejection_reasons: list[str] = []
    saved_server = plan.get("source_discovery", {}) or {}
    live_server = ((live_discovery.get("summary") or {}).get("server") or {})
    saved_serial = str(saved_server.get("serial_number") or "").strip()
    live_serial = str(live_server.get("serial_number") or "").strip()
    saved_model = str(saved_server.get("server_model") or "").strip()
    live_model = str(live_server.get("model") or "").strip()
    if saved_serial and live_serial and saved_serial != live_serial:
        rejection_reasons.append(f"Server serial changed from {saved_serial} to {live_serial}.")
    if saved_model and live_model and saved_model != live_model:
        rejection_reasons.append(f"Server model changed from {saved_model} to {live_model}.")
    if rejection_reasons:
        result = diagnostic_result(
            status="blocked",
            desired_state={
                "server_serial": saved_serial,
                "server_model": saved_model,
                "controller_path": saved_controller_path,
                "selected_bays": [drive.get("bay") for drive in selected_drives],
                "mode": apply_mode,
            },
            discovered_state={
                "server_serial": live_serial,
                "server_model": live_model,
                "controllers": storage_discovered_options(live_discovery).get("controllers", []),
            },
            differences=rejection_reasons,
            options_discovered=storage_discovered_options(live_discovery),
            selected_action="Block destructive storage apply",
            rejection_reasons=rejection_reasons,
            recommended_fix="Run storage discovery against the intended server, review the detected hardware identity, and re-approve storage before applying.",
            user_action_required=True,
        )
        return remapped_plan, result

    live_controllers = {str(controller.get("path") or "").rstrip("/"): controller for controller in storage_discovery_sources(live_discovery)["controllers"]}

    def remap_drive_for_controller(drive: dict[str, Any], live_controller: dict[str, Any]) -> dict[str, Any]:
        live_drive = _find_live_drive(drive, _candidate_drives_for_controller(live_discovery, live_controller))
        if not live_drive:
            rejection_reasons.append(
                f"Approved drive {storage_drive_label(drive)} was not found on live controller {live_controller.get('path') or '(unknown)'}."
            )
            return drive
        matched, reason = _drive_identity_matches(drive, live_drive)
        if not matched:
            rejection_reasons.append(reason)
            return drive
        normalized = normalized_plan_drive(live_drive, str(live_controller.get("source") or live_drive.get("source") or ""))
        if str(drive.get("path") or "").strip() and normalized.get("path") and str(drive.get("path")) != str(normalized.get("path")):
            corrections.append(f"Remapped bay {drive.get('bay') or normalized.get('bay')} drive path to {normalized.get('path')}.")
        return {**drive, **normalized}

    remapped_arrays: list[dict[str, Any]] = []
    for array in storage_plan_arrays(plan):
        saved_array_controller_path = str(array.get("controller_path") or "").rstrip("/")
        saved_array_controller = live_controllers.get(saved_array_controller_path)
        if not saved_array_controller:
            candidate_list = _storage_controller_candidates({"source_discovery": {"controller": {"path": saved_array_controller_path}}}, live_discovery)
            saved_array_controller = candidate_list[0] if candidate_list else {}
        if not saved_array_controller:
            rejection_reasons.append(f"The approved {array.get('name') or array.get('role') or 'array'} controller was not found in live discovery.")
            continue
        live_controller_path = str(saved_array_controller.get("path") or "").rstrip("/")
        if saved_array_controller_path and live_controller_path and saved_array_controller_path != live_controller_path:
            differences.append(f"Controller Redfish path changed from {saved_array_controller_path} to {live_controller_path}.")
            corrections.append(f"Remapped controller path to {live_controller_path}.")
        remapped_drives = [remap_drive_for_controller(drive, saved_array_controller) for drive in list(array.get("drives") or [])]
        remapped_arrays.append(
            {
                **array,
                "controller_path": live_controller_path,
                "controller_name": storage_controller_label(saved_array_controller) or live_controller_path,
                "selected_drive_ids": [str(drive.get("path") or drive.get("drive_path") or "") for drive in remapped_drives if str(drive.get("path") or drive.get("drive_path") or "").strip()],
                "selected_drive_metadata": [storage_drive_metadata(drive) for drive in remapped_drives],
                "drives": remapped_drives,
            }
        )

    if rejection_reasons:
        result = diagnostic_result(
            status="blocked",
            desired_state={
                "controller_path": saved_controller_path,
                "selected_bays": [drive.get("bay") for drive in selected_drives],
                "mode": apply_mode,
            },
            discovered_state={"controllers": storage_discovered_options(live_discovery).get("controllers", [])},
            differences=rejection_reasons,
            options_discovered=storage_discovered_options(live_discovery),
            selected_action="Block destructive storage apply",
            rejection_reasons=rejection_reasons,
            recommended_fix="Run storage discovery again, review the new controller/drive layout, and re-approve storage before applying.",
            user_action_required=True,
        )
        return remapped_plan, result

    remapped_plan["arrays"] = remapped_arrays
    by_role = {str(array.get("role") or ""): array for array in remapped_arrays}
    if by_role.get("os"):
        remapped_plan.setdefault("os_raid1", {})["drives"] = list(by_role["os"].get("drives") or [])
        remapped_plan.setdefault("source_discovery", {})["os_controller"] = {"path": by_role["os"].get("controller_path", ""), "name": by_role["os"].get("controller_name", "")}
    if by_role.get("data"):
        remapped_plan.setdefault("data_raid6", {})["drives"] = list(by_role["data"].get("drives") or [])
        remapped_plan.setdefault("source_discovery", {})["data_controller"] = {"path": by_role["data"].get("controller_path", ""), "name": by_role["data"].get("controller_name", "")}
    primary_controller_path = str((by_role.get("data") or by_role.get("os") or {}).get("controller_path") or "").strip()
    if primary_controller_path:
        remapped_plan.setdefault("source_discovery", {})["controller"] = dict(live_controllers.get(primary_controller_path, {}))
    spare = (remapped_plan.get("hot_spare", {}) or {}).get("drive", {}) or {}
    if spare:
        spare_controller_path = str(spare.get("controller_path") or ((by_role.get("data") or {}).get("controller_path")) or "").strip()
        live_spare_controller = live_controllers.get(spare_controller_path, {})
        remapped_plan.setdefault("hot_spare", {})["drive"] = remap_drive_for_controller(spare, live_spare_controller) if live_spare_controller else spare

    selected_controller_paths = {str(array.get("controller_path") or "").strip() for array in remapped_arrays if str(array.get("controller_path") or "").strip()}
    live_volumes = [volume for volume in storage_discovery_sources(live_discovery)["volumes"] if str(volume.get("controller_path") or "").strip() in selected_controller_paths]
    layout_matches, match_notes = storage_live_layout_matches_plan(remapped_plan, live_discovery)
    if layout_matches:
        remapped_plan["existing_logical_volumes"] = live_volumes
        result = diagnostic_result(
            status="already_applied",
            desired_state={
                "server_serial": (plan.get("source_discovery", {}) or {}).get("serial_number", ""),
                "server_model": (plan.get("source_discovery", {}) or {}).get("server_model", ""),
                "controller_path": saved_controller_path,
                "selected_bays": [drive.get("bay") for drive in selected_drives],
                "mode": apply_mode,
            },
            discovered_state={
                "controller_paths": sorted(selected_controller_paths),
                "selected_bays": [drive.get("bay") for drive in selected_drives],
            },
            differences=[],
            safe_corrections_attempted=corrections,
            options_discovered=storage_discovered_options(live_discovery),
            selected_action="Skip storage apply because the live controller layout already matches the approved plan.",
            recommended_fix="No storage rewrite is needed. Continue with the next stage.",
            user_action_required=False,
        )
        if match_notes:
            result["safe_corrections_attempted"] = list(result.get("safe_corrections_attempted") or []) + match_notes
        return remapped_plan, result
    if apply_mode == "wipe_rebuild":
        remapped_plan["existing_logical_volumes"] = live_volumes
        if live_volumes:
            corrections.append("Refreshed destructive volume targets from live discovery.")

    result_status = "remapped" if corrections else "pass"
    result = diagnostic_result(
        status=result_status,
        desired_state={
            "server_serial": (plan.get("source_discovery", {}) or {}).get("serial_number", ""),
            "server_model": (plan.get("source_discovery", {}) or {}).get("server_model", ""),
            "controller_path": saved_controller_path,
            "controller_model": saved_controller.get("model") or saved_controller.get("name") or "",
            "selected_bays": [drive.get("bay") for drive in selected_drives],
            "mode": apply_mode,
        },
        discovered_state={
            "controller_paths": sorted(selected_controller_paths),
            "selected_bays": [drive.get("bay") for drive in selected_drives],
        },
        differences=differences,
        safe_corrections_attempted=corrections,
        options_discovered=storage_discovered_options(live_discovery),
        selected_action=f"Use live controller path set {', '.join(sorted(selected_controller_paths)) or '(unknown)'} for storage apply.",
        recommended_fix="" if result_status != "blocked" else "Run storage discovery again and re-approve storage.",
        user_action_required=False,
    )
    return remapped_plan, result


def attach_storage_diagnosis(job: dict[str, Any], apply_state: dict[str, Any] | None, diagnosis: dict[str, Any]) -> None:
    job["storage_preflight"] = diagnosis
    job["diagnosis"] = diagnosis
    if apply_state is not None:
        apply_state["diagnosis"] = diagnosis


def _power_attempt_message_ids(result: dict[str, Any]) -> list[str]:
    message_ids: list[str] = []
    for attempt in result.get("attempts") or []:
        item_result = attempt.get("result") if isinstance(attempt, dict) else {}
        if not isinstance(item_result, dict):
            continue
        for message_id in item_result.get("message_ids") or []:
            text = str(message_id or "").strip()
            if text and text not in message_ids:
                message_ids.append(text)
    direct_result = result.get("result") if isinstance(result, dict) else {}
    if isinstance(direct_result, dict):
        for message_id in direct_result.get("message_ids") or []:
            text = str(message_id or "").strip()
            if text and text not in message_ids:
                message_ids.append(text)
    return message_ids


def _power_connection_dropped(result: dict[str, Any]) -> bool:
    direct_result = result.get("result") if isinstance(result, dict) else {}
    if isinstance(direct_result, dict) and direct_result.get("connection_dropped"):
        return True
    for attempt in result.get("attempts") or []:
        item_result = attempt.get("result") if isinstance(attempt, dict) else {}
        if isinstance(item_result, dict) and item_result.get("connection_dropped"):
            return True
    return False


def is_transport_disconnect_error(exc: Exception | str) -> bool:
    text = str(exc or "")
    return any(
        marker in text
        for marker in (
            "Connection aborted",
            "Remote end closed connection",
            "RemoteDisconnected",
            "ConnectionError",
            "connection reset",
            "Connection reset",
            "Broken pipe",
        )
    )


def _power_http_status(result: dict[str, Any]) -> str:
    direct_result = result.get("result") if isinstance(result, dict) else {}
    if isinstance(direct_result, dict) and direct_result.get("http_status_code") is not None:
        return str(direct_result.get("http_status_code"))
    for attempt in result.get("attempts") or []:
        item_result = attempt.get("result") if isinstance(attempt, dict) else {}
        if isinstance(item_result, dict) and item_result.get("http_status_code") is not None:
            return str(item_result.get("http_status_code"))
    return "(none)"


def power_reset_log_summary(result: dict[str, Any], *, default_action: str = "") -> str:
    result = result or {}
    message_ids = _power_attempt_message_ids(result)
    return (
        "Power reset request: "
        f"ResetType={result.get('action') or default_action or '(unknown)'} "
        f"endpoint={result.get('reset_target') or '(unknown)'} "
        f"allowed={','.join(result.get('allowed_reset_types') or []) or '(unknown)'} "
        f"http={_power_http_status(result)} "
        f"message_ids={','.join(message_ids) or '(none)'} "
        f"connection_dropped={'yes' if _power_connection_dropped(result) else 'no'} "
        f"retry={'yes' if result.get('retry_attempted') else 'no'} "
        f"push_button_fallback={'yes' if result.get('fallback_attempted') or result.get('action') == 'PushPowerButton' else 'no'} "
        f"first_observed={result.get('first_observed_power_state') or '(unknown)'} "
        f"last_observed={result.get('last_observed_power_state') or result.get('final_power_state') or '(unknown)'} "
        f"timeout={result.get('poll_timeout_seconds') or '(unknown)'}s "
        f"interval={result.get('poll_interval_seconds') or '(unknown)'}s"
    )


def ensure_client_power_state(
    client: ILOClient,
    expected_state: str,
    *,
    system_path: str | None = None,
    timeout_seconds: int = 180,
    poll_interval: int = 5,
) -> dict[str, Any]:
    if hasattr(client, "ensure_power_state"):
        return client.ensure_power_state(
            expected_state,
            system_path=system_path,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    path = system_path or (client.get_system_path() if hasattr(client, "get_system_path") else client.get_systems()[0])
    current = client.get_power_state(system_path=path) if hasattr(client, "get_power_state") else str(client.get_system(path).get("PowerState") or "")
    expected = str(expected_state or "").strip()
    if current.lower() == expected.lower():
        return {
            "system_path": path,
            "reset_target": "",
            "allowed_reset_types": [],
            "initial_power_state": current,
            "final_power_state": current,
            "changed": False,
            "action": "skip",
            "first_observed_power_state": current,
            "last_observed_power_state": current,
            "poll_timeout_seconds": timeout_seconds,
            "poll_interval_seconds": poll_interval,
            "retry_attempted": False,
            "fallback_attempted": False,
            "attempts": [],
        }
    reset_type = "On" if expected.lower() == "on" else "ForceOff"
    result = client.power_reset(reset_type=reset_type, system_path=path) or {}
    wait_for_power_state(client, expected, timeout_seconds=timeout_seconds, poll_interval=poll_interval)
    return {
        "system_path": path,
        "reset_target": str(result.get("path") or ""),
        "allowed_reset_types": list(result.get("allowed_reset_types") or []),
        "initial_power_state": current,
        "final_power_state": expected,
        "changed": True,
        "action": reset_type,
        "result": result,
        "first_observed_power_state": expected,
        "last_observed_power_state": expected,
        "poll_timeout_seconds": timeout_seconds,
        "poll_interval_seconds": poll_interval,
        "retry_attempted": False,
        "fallback_attempted": False,
        "attempts": [{"reset_type": reset_type, "result": result}],
    }


def power_manual_curl_command(details: dict[str, Any]) -> str:
    target = str(details.get("reset_target") or "").strip()
    reset_type = str(details.get("action") or details.get("selected_reset_type") or "On").strip() or "On"
    if not target:
        return ""
    return f"curl -k -u '<user>:<password>' -H 'Content-Type: application/json' -X POST 'https://<ilo-host>{target}' -d '{{\"ResetType\":\"{reset_type}\"}}'"


def power_failure_diagnosis(stage: str, expected_state: str, exc: Exception) -> dict[str, Any]:
    details = dict(getattr(exc, "power_reset_details", {}) or {})
    recommended = "Review live Redfish power state and retry the power action from the app."
    curl = power_manual_curl_command(details)
    if curl:
        recommended = f"Verify iLO reachability, then retry from the app. Manual equivalent: {curl}"
    return diagnostic_result(
        status="failed",
        desired_state={"stage": stage, "power_state": expected_state},
        discovered_state={
            "initial_power_state": details.get("initial_power_state", ""),
            "final_power_state": details.get("last_observed_power_state") or details.get("final_power_state") or "",
            "power_reset": details,
        },
        differences=[str(exc).splitlines()[0]],
        safe_corrections_attempted=[
            f"Attempted ResetType={details.get('action') or expected_state}.",
            f"Retry attempted={'yes' if details.get('retry_attempted') else 'no'}.",
            f"PushPowerButton fallback attempted={'yes' if details.get('fallback_attempted') else 'no'}.",
        ],
        options_discovered={
            "reset_target": details.get("reset_target", ""),
            "allowed_reset_types": details.get("allowed_reset_types", []),
            "manual_curl_command": curl,
        },
        selected_action=str(details.get("action") or ""),
        rejection_reasons=[str(exc).splitlines()[0]],
        recommended_fix=recommended,
        user_action_required=True,
    )


def attach_esxi_diagnosis(job: dict[str, Any], diagnosis: dict[str, Any]) -> None:
    job["esxi_diagnosis"] = diagnosis
    job["diagnosis"] = diagnosis


def esxi_failure_diagnosis(job: dict[str, Any], detail: str, exc: Exception | None = None) -> dict[str, Any]:
    power_details = dict(getattr(exc, "power_reset_details", {}) or {})
    if not power_details:
        power_details = dict((job.get("esxi_power_transitions") or {}).get("power_on_result") or {})
    virtual_media = dict(job.get("esxi_virtual_media") or {})
    boot_override = dict(job.get("esxi_boot_override") or {})
    boot_inventory = dict(boot_override.get("boot_option_inventory") or {})
    desired_iso = str(job.get("esxi_iso_url") or "")
    media_matches = bool(virtual_media.get("post_mount_image_matches")) or (
        desired_iso and str(virtual_media.get("post_mount_image") or "") == desired_iso
    )
    after_enabled = str(boot_override.get("after_enabled") or "")
    after_target = str(boot_override.get("after_target") or "")
    boot_matched = bool(boot_override.get("matched")) or (after_enabled.lower() == "once" and after_target.lower() in {"cd", "uefitarget"})
    ks_cfg = dict(job.get("esxi_ks_cfg") or {})
    install_target = dict(job.get("esxi_install_target") or {})
    install_values = dict(job.get("esxi_install_values") or {})
    boot_samples = list(job.get("esxi_boot_evidence_samples") or [])
    final_boot_evidence = dict(job.get("esxi_boot_evidence") or {})
    media_url_check = dict(job.get("esxi_virtual_media_url_check") or {})
    installer_boot_observed = bool(job.get("esxi_installer_boot_observed"))
    installer_reboot_detected = bool(job.get("esxi_installer_reboot_detected"))
    post_install_guard = dict(job.get("esxi_post_install_boot_guard") or {})
    management_timeout = "ESXi did not answer on configured IP" in str(detail or "")
    rejection_reasons = [str(detail or "ESXi stage failed.").strip()]
    if power_details:
        rejection_reasons.append(
            "Power-on failed after server was Off. "
            f"Expected={power_details.get('expected_power_state') or 'On'} "
            f"last_observed={power_details.get('last_observed_power_state') or power_details.get('final_power_state') or 'unknown'}."
        )
    if management_timeout:
        rejection_reasons.extend(
            [
                "ESXi installer boot was observed but the configured management IP never answered." if installer_boot_observed else "ESXi management IP never answered after boot preparation.",
                "Possible kickstart failure.",
                "Possible install target failure.",
                "Possible management NIC mismatch.",
            ]
        )
        if installer_reboot_detected:
            rejection_reasons.append("Possible early installer reboot before the management network became reachable.")
    attempted = [
        "Built custom ESXi ISO.",
        "Mounted virtual media and verified readback." if media_matches else "Mounted virtual media but readback did not fully match expected ISO.",
        (
            f"Set one-time boot override and read back Enabled={after_enabled or '(empty)'} Target={after_target or '(empty)'}."
            if boot_override
            else "Boot override was not completed."
        ),
    ]
    if power_details:
        attempted.append(
            "Submitted power-on reset "
            f"ResetType={power_details.get('action') or 'On'} "
            f"retry={'yes' if power_details.get('retry_attempted') else 'no'} "
            f"push_button_fallback={'yes' if power_details.get('fallback_attempted') else 'no'}."
        )
    if installer_boot_observed:
        attempted.append("Observed the one-time virtual CD boot override being consumed; treated the ESXi installer as started.")
    if post_install_guard:
        attempted.append(
            f"Post-install boot guard action={post_install_guard.get('action') or 'none'} "
            f"eject_status={post_install_guard.get('eject_status') or 'unknown'}."
        )
    if ks_cfg:
        attempted.append(f"Generated KS.CFG and saved a redacted preview at {ks_cfg.get('redacted_preview_path') or '(not written)'}.")
    if media_url_check:
        attempted.append(f"Checked virtual media URL serving status={media_url_check.get('status') or 'unknown'}.")
    manual_curl = power_manual_curl_command({**power_details, "action": power_details.get("action") or "On"})
    if str(media_url_check.get("status") or "") == "failed" or "Virtual media URL check failed" in str(detail or ""):
        selected_action = "Block ESXi virtual media mount because the generated ISO URL was not reachable."
        recommended_fix = (
            media_url_check.get("recommended_fix")
            or "Set LAB_BUILDER_PUBLIC_BASE_URL to the Lab Builder URL reachable by iLO, then rerun ESXi only."
        )
    elif power_details:
        selected_action = "ESXi boot preparation succeeded through ISO mount and boot override; power-on did not reach On."
        recommended_fix = (
            "Retry ResetType=On using a fresh connection or Connection: close. "
            "If ResetType=On still does not reach PowerState=On and PushPowerButton is allowed, try PushPowerButton fallback."
            + (f" Manual equivalent: {manual_curl}" if manual_curl else "")
        )
    elif management_timeout:
        selected_action = (
            "ESXi installer booted or the one-time CD/DVD boot override was consumed, but the management IP never became reachable."
            if installer_boot_observed
            else "ESXi boot preparation completed, but the management IP never became reachable."
        )
        recommended_fix = (
            "Rerun ESXi with esxi.debug_no_reboot=true so the iLO console keeps the installer screen visible. "
            "Check KS.CFG syntax, confirm the firstdisk target is the OS RAID logical drive, and verify the management cable/NIC mapping. "
            "If the installer rebooted, let the post-install boot guard leave virtual media unarmed so local disk can boot."
        )
    else:
        selected_action = "ESXi stage failed after collecting boot/install diagnostics."
        recommended_fix = "Review the ESXi boot evidence, KS.CFG preview, install target summary, and iLO console before rerunning ESXi only."
    return diagnostic_result(
        status="failed",
        desired_state={
            "stage": "ESXi install boot",
            "iso_url": desired_iso,
            "power_state_before_boot": "Off",
            "boot_override": {"enabled": "Once", "target": "Cd"},
            "management_ip": install_values.get("management_ip") or job.get("esxi_expected_ip") or "",
            "debug_no_reboot": bool(install_values.get("debug_no_reboot")),
            "install_target": install_target,
        },
        discovered_state={
            "virtual_media_inserted": bool(virtual_media.get("post_mount_inserted")),
            "virtual_media_image": str(virtual_media.get("post_mount_image") or ""),
            "virtual_media_image_matches": bool(media_matches),
            "boot_override_enabled": after_enabled,
            "boot_override_target": after_target,
            "boot_override_matched": bool(boot_matched),
            "power_reset": power_details,
            "installer_boot_observed": installer_boot_observed,
            "installer_reboot_detected": installer_reboot_detected,
            "final_boot_evidence": final_boot_evidence,
            "boot_evidence_samples": boot_samples[-12:],
            "post_install_boot_guard": post_install_guard,
            "virtual_media_url_check": media_url_check,
        },
        differences=rejection_reasons,
        safe_corrections_attempted=attempted,
        options_discovered={
            "reset_target": power_details.get("reset_target", ""),
            "allowed_reset_types": power_details.get("allowed_reset_types", []),
            "boot_option_selection_reason": boot_override.get("boot_option_selection_reason", ""),
            "boot_option_inventory": boot_inventory,
            "virtual_media_device": virtual_media.get("device_path", ""),
            "virtual_media_insert_target": virtual_media.get("insert_target", ""),
            "virtual_media_url_check": media_url_check,
            "manual_curl_command": manual_curl,
            "ks_cfg": ks_cfg,
            "install_target": install_target,
        },
        selected_action=selected_action,
        rejection_reasons=rejection_reasons,
        recommended_fix=recommended_fix,
        user_action_required=True,
    )


def storage_blocked_diagnosis(plan: dict[str, Any], discovery: dict[str, Any], apply_mode: str, platform: dict[str, Any], error: str) -> dict[str, Any]:
    controller = (plan.get("source_discovery", {}) or {}).get("controller", {}) or {}
    selected_drives = storage_selected_plan_drives(plan)
    reason = str(error or platform.get("reason") or "No writable storage apply path is available.").strip()
    return diagnostic_result(
        status="blocked",
        desired_state={
            "controller_path": controller.get("path", ""),
            "controller_model": controller.get("model") or controller.get("name") or "",
            "selected_bays": [drive.get("bay") for drive in selected_drives],
            "mode": apply_mode,
        },
        discovered_state={
            "selected_platform": platform.get("label", ""),
            "platform_id": platform.get("id", ""),
            "controller_path": platform.get("controller_path", ""),
        },
        differences=[reason],
        options_discovered=storage_discovered_options(discovery),
        selected_action="Block destructive storage apply",
        rejection_reasons=[reason],
        recommended_fix="Run storage discovery again while the server is powered On, review the writable Redfish Volumes options, and re-approve storage before applying.",
        user_action_required=True,
    )


def build_storage_failure_fields(
    error_text: str,
    diagnosis: dict[str, Any] | None = None,
    *,
    stage: str = "Storage apply",
) -> dict[str, str]:
    diagnosis = diagnosis or {}
    rejected = [str(item).strip() for item in (diagnosis.get("rejection_reasons") or []) if str(item).strip()]
    recommended = str(diagnosis.get("recommended_fix") or "").strip()
    selected_action = str(diagnosis.get("selected_action") or "").strip()
    status = str(diagnosis.get("status") or "failed").strip() or "failed"
    detail = str(error_text or "").strip() or "Unknown storage error."

    explanation_parts = [f"{stage} failed: {detail}."]
    if rejected:
        explanation_parts.append(f"Detected issue: {rejected[0]}.")
    if selected_action:
        explanation_parts.append(f"System decision: {selected_action}.")
    explanation = " ".join(explanation_parts)
    if not recommended:
        recommended = "Run storage discovery again, verify controller/drive visibility, then re-approve storage before applying."
    codex_handoff = (
        f"[STORAGE FAILURE HANDOFF] stage={stage}; status={status}; error={detail}; "
        f"likely_cause={rejected[0] if rejected else detail}; recommended_fix={recommended}"
    )
    return {
        "area": "storage",
        "reason": detail,
        "explanation": explanation,
        "recommended_fix": recommended,
        "codex_handoff": codex_handoff,
    }


def storage_apply_mode_for_plan(plan: dict[str, Any]) -> str:
    next_action = str((plan.get("apply_readiness", {}) or {}).get("next_action") or "").strip().lower()
    default_recommendation = str(plan.get("default_recommendation") or "").strip().lower()
    mode = next_action or default_recommendation
    if "wipe" in mode or "rebuild" in mode:
        return "wipe_rebuild"
    return "create_only"


def _verified_hpe_smartstorage_settings_path(capabilities: dict[str, Any]) -> tuple[str, str]:
    diagnostics = capabilities.get("hpe_smart_storage_diagnostics", {}) or {}
    probed_paths = list(diagnostics.get("probed_paths") or [])
    found_paths = [str(item.get("path") or "").rstrip("/") for item in diagnostics.get("found_paths", []) if item.get("path")]
    saw_explicit_smartstorage_probe = False

    for item in probed_paths:
        path = str(item.get("path") or "").rstrip("/")
        if "smartstorageconfig" in path.lower():
            saw_explicit_smartstorage_probe = True
        if "smartstorageconfig" in path.lower() and path.lower().endswith("/settings") and bool(item.get("exists")):
            return path, ""

    for item in probed_paths:
        path = str(item.get("path") or "").rstrip("/")
        if "smartstorageconfig" in path.lower() and bool(item.get("exists")):
            candidate = path if path.lower().endswith("/settings") else f"{path}/Settings"
            return candidate, ""

    if not saw_explicit_smartstorage_probe:
        for path in found_paths:
            lower = path.lower()
            if "smartstorageconfig" in lower and lower.endswith("/settings"):
                return path, ""
        for path in found_paths:
            lower = path.lower()
            if "smartstorageconfig" in lower:
                return f"{path}/Settings" if not lower.endswith("/settings") else path, ""

    if any("smartstorageconfig" in path.lower() for path in found_paths):
        return "", "Discovery found SmartStorageConfig references, but no writable SmartStorageConfig settings URI was verified."
    if capabilities.get("hpe_smart_storage"):
        return "", "HPE Smart Storage inventory was present, but no writable SmartStorageConfig settings URI was exposed."
    return "", "No HPE SmartStorageConfig settings URI was discovered."


def _standard_redfish_storage_apply_surface(discovery: dict[str, Any], controller_path: str) -> dict[str, str]:
    controller_path = str(controller_path or "").rstrip("/")
    if not controller_path:
        return {
            "storage_path": "",
            "volumes_path": "",
            "reset_target": "",
            "reason": "The selected storage controller is missing its Redfish storage path.",
        }

    standard_storage = ((discovery.get("raw") or {}).get("standard_storage") or [])
    for item in standard_storage:
        storage_path = str(item.get("@odata.id") or "").rstrip("/")
        if storage_path != controller_path:
            continue
        volumes_path = str(((item.get("Volumes") or {}).get("@odata.id")) or "").rstrip("/")
        reset_target = str((((item.get("Actions") or {}).get("#Storage.ResetToDefaults") or {}).get("target")) or "").rstrip("/")
        if volumes_path:
            return {
                "storage_path": storage_path,
                "volumes_path": volumes_path,
                "reset_target": reset_target,
                "reason": "",
            }
        return {
            "storage_path": storage_path,
            "volumes_path": "",
            "reset_target": reset_target,
            "reason": "The selected standard Redfish storage controller did not expose a writable Volumes collection.",
        }

    if (discovery.get("summary", {}) or {}).get("capabilities", {}).get("standard_redfish_storage"):
        return {
            "storage_path": controller_path,
            "volumes_path": "",
            "reset_target": "",
            "reason": "Standard Redfish storage was detected, but the selected controller path was not present in live discovery.",
        }
    return {
        "storage_path": controller_path,
        "volumes_path": "",
        "reset_target": "",
        "reason": "Standard Redfish storage inventory was not available for the selected controller.",
    }


def standard_redfish_controller_surfaces(discovery: dict[str, Any], controller_paths: list[str]) -> dict[str, dict[str, str]]:
    surfaces: dict[str, dict[str, str]] = {}
    for controller_path in controller_paths:
        clean = str(controller_path or "").strip()
        if not clean:
            continue
        surfaces[clean] = _standard_redfish_storage_apply_surface(discovery, clean)
    return surfaces


def standard_redfish_verified_create_paths(discovery: dict[str, Any]) -> set[str]:
    capabilities = ((discovery.get("summary") or {}).get("capabilities") or {})
    explicit_paths = capabilities.get("standard_redfish_volume_create_verified_paths")
    if isinstance(explicit_paths, list):
        normalized = {str(item or "").strip() for item in explicit_paths if str(item or "").strip()}
        if normalized:
            return normalized
    raw_storage = ((discovery.get("raw") or {}).get("standard_storage") or [])
    inferred = {
        str(item.get("@odata.id") or "").strip()
        for item in raw_storage
        if str(item.get("@odata.id") or "").strip()
        and isinstance(item.get("VolumeCapabilities"), dict)
        and bool((item.get("VolumeCapabilities") or {}).get("Links", {}).get("Drives@Redfish.RequiredOnCreate") is True)
        and bool((item.get("VolumeCapabilities") or {}).get("RAIDType@Redfish.AllowableValues"))
    }
    if inferred:
        return inferred
    if capabilities.get("standard_redfish_volume_create_verified") is True:
        controllers = ((discovery.get("summary") or {}).get("standard_redfish_storage") or {}).get("controllers") or []
        return {str(item.get("path") or "").strip() for item in controllers if str(item.get("path") or "").strip()}
    return set()


def storage_allow_unverified_standard_redfish_create(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_kit_config()
    storage_cfg = (cfg.get("storage") or {}) if isinstance(cfg, dict) else {}
    return bool(storage_cfg.get("allow_unverified_standard_redfish_create"))


def build_storage_controller_capabilities(discovery: dict[str, Any], cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    summary = discovery.get("summary", discovery) or {}
    capabilities = (summary.get("capabilities") or {}) if isinstance(summary, dict) else {}
    server = (summary.get("server") or {}) if isinstance(summary, dict) else {}
    server_gen = str(server.get("generation") or "")
    controllers = []
    override_enabled = storage_allow_unverified_standard_redfish_create(cfg)
    verified_redfish_paths = standard_redfish_verified_create_paths(discovery)
    settings_path, settings_reason = _verified_hpe_smartstorage_settings_path(capabilities)
    for source_key, items in (
        ("standard_redfish_storage", ((summary.get("standard_redfish_storage") or {}).get("controllers") or [])),
        ("hpe_smart_storage", ((summary.get("hpe_smart_storage") or {}).get("controllers") or [])),
    ):
        for item in items:
            controller = dict(item or {})
            controller_path = str(controller.get("path") or "").strip()
            controller_name = storage_controller_label(controller) or controller_path
            record = {
                "controller_path": controller_path,
                "controller_name": controller_name,
                "source": source_key,
                "can_delete_volumes": False,
                "can_create_volumes": False,
                "create_method": "inventory_only",
                "verified": False,
                "override_enabled": override_enabled,
                "warning": "",
                "reason": "",
                "volumes_path": "",
                "reset_target": "",
                "action_info_paths": [],
            }
            if source_key == "hpe_smart_storage":
                record["can_delete_volumes"] = bool(settings_path)
                record["can_create_volumes"] = bool(settings_path)
                record["create_method"] = "hpe_oem" if settings_path else "inventory_only"
                record["verified"] = bool(settings_path and ("Gen10" in server_gen))
                record["reason"] = "" if record["verified"] else settings_reason or "No verified HPE SmartStorageConfig settings URI was exposed."
            else:
                surface = _standard_redfish_storage_apply_surface(discovery, controller_path)
                record["volumes_path"] = str(surface.get("volumes_path") or "")
                record["reset_target"] = str(surface.get("reset_target") or "")
                record["can_delete_volumes"] = bool(record["volumes_path"] or record["reset_target"])
                record["verified"] = controller_path in verified_redfish_paths
                if record["verified"]:
                    record["can_create_volumes"] = True
                    record["create_method"] = "standard_redfish"
                    record["reason"] = "Standard Redfish volume creation is explicitly verified for this controller."
                elif override_enabled and record["volumes_path"]:
                    record["can_create_volumes"] = True
                    record["create_method"] = "standard_redfish"
                    record["reason"] = "Manual override is enabled. This bypasses verification and can destroy arrays if create fails."
                    record["warning"] = "Manual override enabled: unverified Standard Redfish create may be destructive."
                else:
                    record["reason"] = "Discovery works, but automated create/delete is not verified for this controller. Use manual SSA/StorCLI or add a verified apply adapter."
                    if record["volumes_path"]:
                        record["warning"] = "A writable-looking /Volumes path exists, but that alone does not verify create support."
                raw_storage = ((discovery.get("raw") or {}).get("standard_storage") or [])
                for storage in raw_storage:
                    if str(storage.get("@odata.id") or "").rstrip("/") != controller_path.rstrip("/"):
                        continue
                    action_info_paths = []
                    for action in list((storage.get("Actions") or {}).values()):
                        if isinstance(action, dict) and action.get("@Redfish.ActionInfo"):
                            action_info_paths.append(str(action.get("@Redfish.ActionInfo") or ""))
                    record["action_info_paths"] = action_info_paths
                    break
            controllers.append(record)
    return controllers


def choose_storage_apply_platform(discovery: dict, plan: dict) -> dict[str, Any]:
    summary = discovery.get("summary", {}) or {}
    server = summary.get("server", {}) or {}
    ilo = summary.get("ilo", {}) or {}
    capabilities = summary.get("capabilities", {}) or {}

    arrays = storage_plan_arrays(plan)
    controller = plan.get("source_discovery", {}).get("controller", {}) or {}
    controller_paths = sorted({str(array.get("controller_path") or "").strip() for array in arrays if str(array.get("controller_path") or "").strip()})
    profile = plan.get("hardware_profile") or detect_hardware_profile(server, (plan.get("source_discovery") or {}).get("controllers") or [])
    cfg = load_kit_config()
    capability_rows = build_storage_controller_capabilities(discovery, cfg=cfg)
    capability_by_path = {str(item.get("controller_path") or ""): item for item in capability_rows if str(item.get("controller_path") or "").strip()}
    settings_path, settings_reason = _verified_hpe_smartstorage_settings_path(capabilities)
    server_gen = str(server.get("generation") or "")
    ilo_version = str(ilo.get("version") or ilo.get("model") or "")
    if settings_path and capabilities.get("hpe_smart_storage") and ("Gen10" in server_gen or "iLO 5" in ilo_version):
        return {
            "id": "gen10_hpe_smartstorageconfig",
            "label": "Gen10 / iLO 5 / HPE SmartStorageConfig",
            "supported": True,
            "settings_path": settings_path,
            "controller_path": controller.get("path", ""),
            "reason": "",
        }
    if capabilities.get("standard_redfish_storage"):
        if len(controller_paths) > 1:
            controller_surfaces = standard_redfish_controller_surfaces(discovery, controller_paths)
            missing = [path for path, surface in controller_surfaces.items() if not surface.get("volumes_path")]
            if missing:
                reasons = [f"{path}: {(controller_surfaces[path] or {}).get('reason') or 'no writable volume collection'}" for path in missing]
                return {
                    "id": "multi_controller_planned_only",
                    "label": "Multi-controller plan requires controller-specific writable Redfish surfaces",
                    "supported": False,
                    "settings_path": "",
                    "controller_path": "",
                    "reason": "This storage plan spans multiple controllers, but not every controller exposed a writable Redfish Volumes collection. " + " | ".join(reasons),
                    "controller_surfaces": controller_surfaces,
                }
            unverified = [path for path in controller_paths if not (capability_by_path.get(path, {}) or {}).get("verified") and not (capability_by_path.get(path, {}) or {}).get("override_enabled")]
            if unverified:
                return {
                    "id": "standard_redfish_create_unverified",
                    "label": "Standard Redfish create support is not verified",
                    "supported": False,
                    "settings_path": "",
                    "controller_path": "",
                    "reason": "This storage plan would require Standard Redfish volume creation on controller paths that are not explicitly create-verified. Blocking before delete to avoid destructive partial apply. Unverified controllers: " + ", ".join(unverified),
                    "controller_surfaces": controller_surfaces,
                    "controller_capabilities": [capability_by_path.get(path, {}) for path in controller_paths],
                }
            return {
                "id": "multi_controller_standard_redfish_volumes",
                "label": "Standard Redfish Storage Volumes per controller",
                "supported": True,
                "settings_path": "",
                "controller_path": "",
                "reason": "Manual override enabled for unverified Standard Redfish create." if any((capability_by_path.get(path, {}) or {}).get("override_enabled") and not (capability_by_path.get(path, {}) or {}).get("verified") for path in controller_paths) else "",
                "controller_surfaces": controller_surfaces,
                "controller_capabilities": [capability_by_path.get(path, {}) for path in controller_paths],
            }
        surface = _standard_redfish_storage_apply_surface(discovery, controller.get("path", ""))
        selected_capability = capability_by_path.get(str(controller.get("path") or "").strip(), {})
        if not selected_capability.get("verified") and not selected_capability.get("override_enabled"):
            return {
                "id": "standard_redfish_create_unverified",
                "label": "Standard Redfish create support is not verified",
                "supported": False,
                "settings_path": "",
                "controller_path": controller.get("path", ""),
                "volumes_path": surface.get("volumes_path", ""),
                "reset_target": surface.get("reset_target", ""),
                "reason": "This controller exposes Standard Redfish storage inventory, but volume creation is not explicitly verified. Blocking before delete to avoid destructive partial apply.",
                "controller_capabilities": [selected_capability] if selected_capability else [],
            }
        if profile.get("expected_storage_layout") == "multi_controller" and "Gen11" in server_gen and not surface.get("volumes_path"):
            return {
                "id": "gen11_inventory_only",
                "label": "Gen11 standard Redfish inventory only",
                "supported": False,
                "settings_path": "",
                "controller_path": controller.get("path", ""),
                "volumes_path": surface.get("volumes_path", ""),
                "reset_target": surface.get("reset_target", ""),
                "reason": "Standard Redfish Volumes are not confirmed for create/apply on this Gen11 profile. The layout can be discovered and approved, but execution requires an HPE OEM or SSA or StorCLI path.",
            }
        if surface.get("volumes_path"):
            return {
                "id": "standard_redfish_volumes",
                "label": "Standard Redfish Storage Volumes",
                "supported": True,
                "settings_path": "",
                "controller_path": surface.get("storage_path", ""),
                "volumes_path": surface.get("volumes_path", ""),
                "reset_target": surface.get("reset_target", ""),
                "reason": "Manual override enabled for unverified Standard Redfish create." if selected_capability.get("override_enabled") and not selected_capability.get("verified") else "",
                "controller_capabilities": [selected_capability] if selected_capability else [],
            }
        return {
            "id": "gen11_standard_redfish",
            "label": "Standard Redfish Storage",
            "supported": False,
            "settings_path": "",
            "controller_path": controller.get("path", ""),
            "volumes_path": surface.get("volumes_path", ""),
            "reset_target": surface.get("reset_target", ""),
            "reason": surface.get("reason", "") or "Standard Redfish storage inventory was detected, but no writable volume collection was verified.",
        }
    if capabilities.get("hpe_smart_storage"):
        return {
            "id": "hpe_smart_storage_read_only",
            "label": "HPE Smart Storage inventory only",
            "supported": False,
            "settings_path": "",
            "controller_path": controller.get("path", ""),
            "reason": settings_reason,
        }
    return {
        "id": "unsupported",
        "label": "Unsupported storage apply path",
        "supported": False,
        "settings_path": "",
        "controller_path": controller.get("path", ""),
        "reason": settings_reason or "No supported writable storage apply path was discovered.",
    }


def validate_storage_apply_request(
    plan: dict,
    apply_mode: str,
    typed_confirmation: str,
    acknowledged: bool,
) -> None:
    if apply_mode not in {"create_only", "wipe_rebuild"}:
        raise ValueError("Unknown storage apply mode.")
    if not plan.get("valid", False):
        blockers = [str(item).strip() for item in list(plan.get("blockers") or []) if str(item).strip()]
        if blockers:
            raise ValueError("Storage plan validation failed: " + " ".join(blockers))
        raise ValueError("Storage plan validation failed. Rebuild the plan after reviewing the current inventory blockers.")
    arrays = storage_plan_arrays(plan)
    if not arrays:
        raise ValueError("No storage arrays are selected for apply.")
    used_paths: dict[str, str] = {}
    for array in arrays:
        role = str(array.get("role") or "custom")
        role_name = str(array.get("name") or role.upper())
        controller_path = str(array.get("controller_path") or "").strip()
        controller_name = str(array.get("controller_name") or controller_path or "(unknown controller)")
        if not controller_path:
            raise ValueError(f"{role_name} is missing its controller selection.")
        live_paths = []
        for drive in list(array.get("drives") or []):
            drive_path = str(drive.get("path") or drive.get("drive_path") or "").strip()
            drive_controller_path = str(drive.get("controller_path") or "").strip()
            drive_controller_name = str(drive.get("controller_name") or drive_controller_path or "(unknown controller)")
            if not drive_path:
                raise ValueError(f"{role_name} contains a drive without a Redfish drive path.")
            if storage_status_is_absent(str(drive.get("status") or "")):
                raise ValueError(f"{storage_drive_label(drive)} is absent and cannot be applied.")
            if drive_controller_path != controller_path:
                raise ValueError(f"{storage_drive_label(drive)} is on {drive_controller_name}, but {role_name} is set to {controller_name}.")
            if drive_path in used_paths and used_paths[drive_path] != role:
                raise ValueError(f"Drive path {drive_path} cannot be reused in both {used_paths[drive_path]} and {role}.")
            used_paths[drive_path] = role
            live_paths.append(drive_controller_path)
        if len(set(live_paths)) > 1:
            raise ValueError(f"{role_name} cannot span multiple storage controllers.")
        section = "os" if role == "os" else "data"
        cardinality = validate_raid_drive_count(str(array.get("raid_level") or array.get("raid") or ""), list(array.get("drives") or []), section=section)
        if cardinality:
            raise ValueError(" ".join(cardinality))
    by_role = {str(array.get("role") or ""): array for array in arrays}
    spare = (plan.get("hot_spare", {}) or {}).get("drive", {}) or {}
    if spare:
        spare_path = str(spare.get("path") or spare.get("drive_path") or "").strip()
        spare_controller_path = str(spare.get("controller_path") or "").strip()
        spare_controller_name = str(spare.get("controller_name") or spare_controller_path or "(unknown controller)")
        data_controller_path = str((by_role.get("data") or {}).get("controller_path") or "").strip()
        data_controller_name = str((by_role.get("data") or {}).get("controller_name") or data_controller_path or "(unknown controller)")
        if data_controller_path and spare_controller_path != data_controller_path:
            raise ValueError(f"{storage_drive_label(spare)} is on {spare_controller_name}, but the data array is set to {data_controller_name}.")
        if spare_path and spare_path in used_paths:
            raise ValueError(f"Drive path {spare_path} cannot be reused as both {used_paths[spare_path]} and hot spare.")
    array_controller_paths = {str(array.get("controller_path") or "").strip() for array in arrays if str(array.get("controller_path") or "").strip()}
    for volume in plan.get("existing_logical_volumes", []) or []:
        volume_controller_path = str(volume.get("controller_path") or "").strip()
        if not volume_controller_path and len(array_controller_paths) == 1:
            continue
        if array_controller_paths and volume_controller_path not in array_controller_paths:
            raise ValueError("Existing logical volumes must stay scoped to one of the selected array controllers before apply.")
    readiness = plan.get("apply_readiness", {}) or {}
    if apply_mode == "create_only" and not readiness.get("create_only_ready"):
        raise ValueError("Create-only apply is not ready for this plan.")
    if apply_mode == "wipe_rebuild" and not readiness.get("wipe_rebuild_ready"):
        raise ValueError("Wipe-and-rebuild apply is not ready for this plan.")
    if not acknowledged:
        raise ValueError("Storage apply requires the acknowledgment checkbox.")
    expected_confirmation = storage_apply_confirmation_for_mode(apply_mode)
    if typed_confirmation.strip() != expected_confirmation:
        raise ValueError(f"Storage apply requires the exact confirmation string: {expected_confirmation}")


def execute_storage_apply_gen10(
    client: ILOClient,
    plan: dict,
    apply_mode: str,
    platform: dict[str, Any],
    kit_name: str,
    job: dict[str, Any],
    apply_state: dict[str, Any],
    apply_paths: dict[str, Path],
    starting_step: int,
    total_steps: int,
    progress_resolver: Callable[[int, int], int] | None = None,
) -> tuple[int, list[Any]]:
    settings_path = platform.get("settings_path", "")
    if not settings_path:
        raise ILOError("Gen10 SmartStorageConfig settings path could not be determined from discovery.")

    responses = []
    current = starting_step
    intent = build_storage_apply_intent(plan, apply_mode)
    existing_volume_paths = [str(volume.get("path") or "").strip() for volume in plan.get("existing_logical_volumes", []) or [] if str(volume.get("path") or "").strip()]
    if apply_mode == "wipe_rebuild":
        targets = {
            "controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "",
            "path": settings_path,
        }
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Delete existing logical volumes",
            current,
            total_steps,
            "running",
            "Delete existing logical volumes",
            targets=targets,
            details=f"Preparing {len(existing_volume_paths)} logical-volume delete actions for one consolidated SmartStorageConfig payload.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Delete existing logical volumes",
            current,
            total_steps,
            "ok",
            "Delete existing logical volumes",
            targets=targets,
            details=f"Queued {len(existing_volume_paths)} logical-volume delete actions into the final pending config.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        current += 1
    else:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Delete existing logical volumes",
            current,
            total_steps,
            "skip",
            "Delete existing logical volumes",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="Create-only mode selected; no existing logical volumes will be removed.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        current += 1

    os_intent = intent["os_raid1"]
    os_label = str(os_intent.get("label") or "OS logical drive")
    if os_intent.get("drives"):
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            f"Create {os_label}",
            current,
            total_steps,
            "running",
            f"Create {os_label}",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": os_intent.get("bays", [])},
            details=f"Staging {os_label} into one consolidated SmartStorageConfig payload at {settings_path}.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            f"Create {os_label}",
            current,
            total_steps,
            "ok",
            f"Create {os_label}",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": os_intent.get("bays", [])},
            details=f"Queued {os_label} in the final pending config.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    else:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Create OS array",
            current,
            total_steps,
            "skip",
            "Create OS array",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="No OS array is selected in this plan.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    current += 1

    data_intent = intent["data_raid6"]
    data_label = str(data_intent.get("label") or "Data logical drive")
    if data_intent.get("drives"):
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            f"Create {data_label}",
            current,
            total_steps,
            "running",
            f"Create {data_label}",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": data_intent.get("bays", [])},
            details=f"Staging {data_label} into one consolidated SmartStorageConfig payload at {settings_path}.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            f"Create {data_label}",
            current,
            total_steps,
            "ok",
            f"Create {data_label}",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": data_intent.get("bays", [])},
            details=f"Queued {data_label} in the final pending config.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    else:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Create data array",
            current,
            total_steps,
            "skip",
            "Create data array",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="No data array is selected in this plan.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    current += 1

    spare_intent = intent["hot_spare"]
    spare_bay = str(spare_intent.get("bay", "") or "").strip()
    if spare_bay:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Assign hot spare",
            current,
            total_steps,
            "running",
            "Assign hot spare",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": [spare_bay]},
            details=f"Submitting one consolidated SmartStorageConfig payload with the optional dedicated spare at {settings_path}.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    else:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Assign hot spare",
            current,
            total_steps,
            "skip",
            "Assign hot spare",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="No dedicated hot spare was selected for this plan.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    response = client.apply_gen10_storage_layout(
        settings_path=settings_path,
        apply_mode=apply_mode,
        existing_volume_paths=existing_volume_paths,
        os_intent=os_intent,
        data_intent=data_intent,
        spare_intent=spare_intent,
    )
    responses.append(response)
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Assign hot spare",
        current,
        total_steps,
        "ok",
        "Assign hot spare",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": [spare_bay] if spare_bay else []},
        details=(
            f"Submitted the consolidated SmartStorageConfig pending payload with {os_label}, {data_label}, and the optional dedicated spare."
            if spare_bay
            else f"Submitted the consolidated SmartStorageConfig pending payload with {os_label} and {data_label}."
        ),
        response=response,
        progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
    )
    current += 1
    return current, responses


def execute_storage_apply_standard_redfish(
    client: ILOClient,
    plan: dict,
    apply_mode: str,
    platform: dict[str, Any],
    kit_name: str,
    job: dict[str, Any],
    apply_state: dict[str, Any],
    apply_paths: dict[str, Path],
    starting_step: int,
    total_steps: int,
    progress_resolver: Callable[[int, int], int] | None = None,
) -> tuple[int, list[Any]]:
    responses: list[Any] = []
    current = starting_step
    intent = build_storage_apply_intent(plan, apply_mode)
    readiness = client.wait_for_storage_device_discovery()
    responses.append({"device_discovery": readiness, "reboot_required": False})
    if not readiness.get("ready"):
        raise ILOError(
            "Storage device discovery is not complete on the active server. "
            f"Current state: {readiness.get('state') or 'unknown'}."
        )
    controller_surfaces = dict(platform.get("controller_surfaces") or {})
    if not controller_surfaces:
        controller_path = str(platform.get("controller_path") or "").strip()
        volumes_path = str(platform.get("volumes_path") or "").strip()
        if not volumes_path:
            raise ILOError("Standard Redfish volume collection path could not be determined from discovery.")
        controller_surfaces = {
            controller_path: {
                "storage_path": controller_path,
                "volumes_path": volumes_path,
                "reset_target": str(platform.get("reset_target") or "").strip(),
                "reason": "",
            }
        }

    array_by_role = {str(item.get("role") or ""): item for item in list(intent.get("arrays") or [])}
    spare_intent = intent["hot_spare"]
    existing_by_controller: dict[str, list[str]] = {}
    for volume in plan.get("existing_logical_volumes", []) or []:
        controller_path = str(volume.get("controller_path") or "").strip()
        volume_path = str(volume.get("path") or "").strip()
        if controller_path and volume_path:
            existing_by_controller.setdefault(controller_path, []).append(volume_path)

    capabilities_by_controller: dict[str, dict[str, Any]] = {}
    for controller_path, surface in controller_surfaces.items():
        volumes_path = str(surface.get("volumes_path") or "").strip()
        if not volumes_path:
            raise ILOError(f"Standard Redfish volume collection path could not be determined for controller {controller_path}.")
        capabilities = client.get_standard_storage_volume_capabilities(volumes_path)
        capabilities_by_controller[controller_path] = capabilities
        responses.append({"controller_path": controller_path, "volume_capabilities": capabilities, "reboot_required": False})

    if apply_mode == "wipe_rebuild":
        for controller_path, surface in controller_surfaces.items():
            existing_volume_paths = existing_by_controller.get(controller_path, [])
            controller_arrays = [item for item in list(intent.get("arrays") or []) if str(item.get("controller_path") or "").strip() == controller_path]
            controller_name = str((controller_arrays[0] if controller_arrays else {}).get("controller_name") or controller_path)
            volumes_path = str(surface.get("volumes_path") or "").strip()
            targets = {"controller": controller_name, "path": str(surface.get("storage_path") or volumes_path)}
            record_storage_apply_step(
                kit_name,
                job,
                apply_state,
                apply_paths,
                "Delete existing logical volumes",
                current,
                total_steps,
                "running",
                "Delete existing logical volumes",
                targets=targets,
                details=(
                    f"Deleting {len(existing_volume_paths)} existing standard Redfish volume(s) from {volumes_path}."
                    if existing_volume_paths
                    else "No existing standard Redfish volumes were captured for this controller; continuing directly to create."
                ),
                progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
            )
            if existing_volume_paths:
                for volume_path in existing_volume_paths:
                    delete_response = client.delete_standard_storage_volume(volume_path)
                    responses.append(delete_response)
            record_storage_apply_step(
                kit_name,
                job,
                apply_state,
                apply_paths,
                "Delete existing logical volumes",
                current,
                total_steps,
                "ok",
                "Delete existing logical volumes",
                targets=targets,
                details=(
                    f"Submitted deletion for {len(existing_volume_paths)} standard Redfish volume(s)."
                    if existing_volume_paths
                    else "No existing standard Redfish volumes were present for this controller; skipped delete/reset."
                ),
                progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
            )
    else:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Delete existing logical volumes",
            current,
            total_steps,
            "skip",
            "Delete existing logical volumes",
            targets={"controller": ", ".join(sorted(controller_surfaces))},
            details="Create-only mode selected; no existing standard Redfish volumes will be removed.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    current += 1

    os_intent = array_by_role.get("os", intent["os_raid1"])
    os_label = str(os_intent.get("label") or "OS logical drive")
    if os_intent.get("drives"):
        os_controller_path = str(os_intent.get("controller_path") or "").strip()
        os_surface = controller_surfaces.get(os_controller_path, {})
        os_volumes_path = str(os_surface.get("volumes_path") or "").strip()
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            f"Create {os_label}",
            current,
            total_steps,
            "running",
            f"Create {os_label}",
            targets={"controller": os_intent.get("controller_name") or os_controller_path, "bays": os_intent.get("bays", []), "path": os_volumes_path},
            details=f"Submitting {os_label} to the standard Redfish volume collection.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        os_response = client.create_standard_storage_volume(os_volumes_path, os_intent, capabilities=capabilities_by_controller.get(os_controller_path, {}))
        responses.append(os_response)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            f"Create {os_label}",
            current,
            total_steps,
            "ok",
            f"Create {os_label}",
            targets={"controller": os_intent.get("controller_name") or os_controller_path, "bays": os_intent.get("bays", []), "path": os_volumes_path},
            details=f"Submitted {os_label} to {os_volumes_path}.",
            response=os_response,
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    else:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Create OS array",
            current,
            total_steps,
            "skip",
            "Create OS array",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="No OS array is selected in this plan.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    current += 1

    data_intent = array_by_role.get("data", intent["data_raid6"])
    data_label = str(data_intent.get("label") or "Data logical drive")
    if data_intent.get("drives"):
        data_controller_path = str(data_intent.get("controller_path") or "").strip()
        data_surface = controller_surfaces.get(data_controller_path, {})
        data_volumes_path = str(data_surface.get("volumes_path") or "").strip()
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            f"Create {data_label}",
            current,
            total_steps,
            "running",
            f"Create {data_label}",
            targets={"controller": data_intent.get("controller_name") or data_controller_path, "bays": data_intent.get("bays", []), "path": data_volumes_path},
            details=f"Submitting {data_label} to the standard Redfish volume collection.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        spare_payload = spare_intent if str(spare_intent.get("drive", {}).get("controller_path") or "").strip() == data_controller_path else {}
        data_response = client.create_standard_storage_volume(data_volumes_path, data_intent, spare_intent=spare_payload, capabilities=capabilities_by_controller.get(data_controller_path, {}))
        responses.append(data_response)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            f"Create {data_label}",
            current,
            total_steps,
            "ok",
            f"Create {data_label}",
            targets={"controller": data_intent.get("controller_name") or data_controller_path, "bays": data_intent.get("bays", []), "path": data_volumes_path},
            details=f"Submitted {data_label} to {data_volumes_path}.",
            response=data_response,
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    else:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Create data array",
            current,
            total_steps,
            "skip",
            "Create data array",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="No data array is selected in this plan.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    current += 1

    spare_bay = str(spare_intent.get("bay", "") or "").strip()
    spare_controller_name = str(data_intent.get("controller_name") or apply_state["controller"].get("name") or apply_state["controller"].get("model") or "")
    if spare_bay and not data_intent.get("drives"):
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Assign hot spare",
            current,
            total_steps,
            "skip",
            "Assign hot spare",
            targets={"controller": spare_controller_name, "bays": [spare_bay]},
            details="A dedicated spare was selected, but no data array is being created.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    elif spare_bay:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Assign hot spare",
            current,
            total_steps,
            "ok",
            "Assign hot spare",
            targets={"controller": spare_controller_name, "bays": [spare_bay]},
            details="The dedicated spare, if supported, was included in the data volume creation request.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    else:
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Assign hot spare",
            current,
            total_steps,
            "skip",
            "Assign hot spare",
            targets={"controller": spare_controller_name},
            details="No dedicated hot spare was selected for this plan.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    current += 1
    return current, responses


def run_storage_apply(
    cfg: dict,
    discovery_raw_path: str,
    raid_plan_path: str,
    apply_mode: str,
    apply_paths: dict[str, Path],
) -> None:
    kit_name = cfg["site"]["name"]
    existing_job = load_job(kit_name)
    inherited_root_scope = str(existing_job.get("root_scope") or existing_job.get("scope") or f"storage-apply:{apply_mode}")
    apply_steps = 10
    total_steps = apply_steps
    job = {
        "status": "Running",
        "execution_mode": str(existing_job.get("execution_mode") or "real"),
        "execution_mode_label": str(existing_job.get("execution_mode_label") or "Real execution"),
        "scope": f"storage-apply:{apply_mode}",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total_steps,
        "logs": [],
        "root_scope": inherited_root_scope,
        "stage_statuses": merge_stage_statuses(
            initialize_stage_statuses(inherited_root_scope, cfg),
            existing_job.get("stage_statuses"),
        ),
        "failure_area": "",
        "failure_reason": "",
        "failure_explanation": "",
        "failure_recommended_fix": "",
        "failure_codex_handoff": "",
    }
    job = carry_forward_job_bundle_metadata(kit_name, job)
    save_job(kit_name, job)

    discovery = None
    discovery_paths = None
    plan = None
    plan_paths = None
    client = None
    apply_state = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "mode": apply_mode,
        "status": "Running",
        "apply_path": "",
        "controller": {},
        "paths": {key: str(value) for key, value in apply_paths.items()},
        "steps": [],
        "responses": [],
        "errors": [],
        "reboot_required": False,
        "workflow_state": "running_apply",
        "reboot_status": "Not requested",
        "reboot_requested": False,
        "post_reboot_validation": "Pending reboot decision",
    }
    save_storage_apply_state(apply_state, apply_paths)

    try:
        storage_target = resolve_storage_target_host(cfg)
        storage_credentials = resolve_storage_target_credentials(cfg)
        host = storage_target.get("resolved", "")
        username = storage_credentials.get("username", "")
        password = storage_credentials.get("password", "")
        if not host or not username or not password:
            raise ValueError(storage_target.get("error") or storage_credentials.get("error") or "Missing current iLO IP, username, or password for storage apply.")

        discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Validate controller and plan",
            0,
            apply_steps,
            "running",
            "Validate controller and plan",
            targets={"controller": (plan.get("source_discovery", {}).get("controller", {}) or {}).get("name") or (plan.get("source_discovery", {}).get("controller", {}) or {}).get("model") or ""},
            details=f"Validating mode={apply_mode} against host={host} ({storage_target.get('source')}) and current plan safety gates.",
        )
        validate_storage_apply_request(plan, apply_mode, storage_apply_confirmation_for_mode(apply_mode), True)
        apply_state["controller"] = plan.get("source_discovery", {}).get("controller", {}) or {}
        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))
        system_path, initial_power_state = read_current_power_state(client)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Validate controller and plan",
            1,
            apply_steps,
            "running",
            "Validate controller and plan",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=f"Storage apply initial PowerState={initial_power_state or 'unknown'} on {system_path}.",
        )
        power_on_result = ensure_client_power_state(client, "On", system_path=system_path, timeout_seconds=300, poll_interval=5)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Validate controller and plan",
            1,
            apply_steps,
            "skip" if str(power_on_result.get("action") or "") == "skip" else "running",
            "Validate controller and plan",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=(
                "[SKIP] Already On. "
                if str(power_on_result.get("action") or "") == "skip"
                else ""
            ) + power_reset_log_summary(power_on_result, default_action="On"),
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Validate controller and plan",
            1,
            apply_steps,
            "ok",
            "Validate controller and plan",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="Plan, controller, host, hot spare, and confirmation gates passed.",
        )

        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Export pre-change storage",
            1,
            apply_steps,
            "running",
            "Export pre-change storage",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=f"Connecting to {host} and reading current storage state before apply.",
        )
        pre_change_discovery = {}
        platform = {}
        storage_diagnosis = {}
        for attempt in range(1, 7):
            pre_change_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
            plan, storage_diagnosis = storage_preflight_compare_and_remap(plan, pre_change_discovery, apply_mode)
            attach_storage_diagnosis(job, apply_state, storage_diagnosis)
            apply_state["controller"] = plan.get("source_discovery", {}).get("controller", {}) or apply_state["controller"]
            if storage_diagnosis.get("status") == "blocked":
                platform = {
                    "id": "storage_preflight_blocked",
                    "label": "Storage preflight blocked",
                    "supported": False,
                    "controller_path": (apply_state.get("controller") or {}).get("path", ""),
                    "reason": "; ".join(storage_diagnosis.get("rejection_reasons") or []) or "Storage preflight blocked destructive apply.",
                }
                break
            if storage_diagnosis.get("status") == "already_applied":
                platform = {
                    "id": "storage_preflight_already_applied",
                    "label": "Storage layout already matches approved plan",
                    "supported": True,
                    "controller_path": (apply_state.get("controller") or {}).get("path", ""),
                    "reason": str(storage_diagnosis.get("selected_action") or "").strip(),
                }
                break
            platform = choose_storage_apply_platform(pre_change_discovery, plan)
            platform_id = str(platform.get("id") or "")
            if platform_id in {"gen10_hpe_smartstorageconfig", "standard_redfish_volumes", "multi_controller_standard_redfish_volumes"}:
                break
            if platform_id == "hpe_smart_storage_read_only" and attempt < 6:
                record_storage_apply_step(
                    kit_name,
                    job,
                    apply_state,
                    apply_paths,
                    "Choose storage apply path",
                    3,
                    apply_steps,
                    "running",
                    "Choose storage apply path",
                    targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
                    details=f"Writable storage apply path not ready yet (attempt {attempt}/6). Rechecking discovery.",
                )
                time.sleep(5)
                continue
            break
        attach_storage_diagnosis(job, apply_state, storage_diagnosis)
        for line in diagnostic_log_lines("Storage preflight", storage_diagnosis):
            update_job(
                kit_name,
                job,
                "Running",
                "Choose storage apply path",
                3,
                apply_steps,
                line,
                progress_percent=storage_workflow_progress_percent("running_apply", 3, apply_steps),
            )
        save_storage_apply_state(apply_state, apply_paths)
        write_storage_discovery_snapshot_files(
            apply_paths["pre_change_summary"],
            apply_paths["pre_change_raw"],
            cfg,
            pre_change_discovery,
            host=host,
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Export pre-change storage",
            2,
            apply_steps,
            "ok",
            "Export pre-change storage",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=f"Saved {apply_paths['pre_change_summary'].name} and {apply_paths['pre_change_raw'].name}.",
        )

        apply_state["apply_path"] = platform.get("label", "")
        platform_supported = platform.get("id") in {"gen10_hpe_smartstorageconfig", "standard_redfish_volumes", "multi_controller_standard_redfish_volumes", "storage_preflight_already_applied"}
        if not platform_supported:
            apply_state["status"] = "Failed"
            apply_state["workflow_state"] = "apply_failed"
            apply_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        platform_error = ""
        if not platform_supported:
            if str(platform.get("id") or "") == "hpe_smart_storage_read_only":
                platform_error = "Storage apply requires server power On and a writable Redfish Volumes path. Current path is inventory-only."
                platform_error += " Recommended fix: run storage discovery again while the server is powered On, review the writable Redfish Volumes options, and re-approve storage before applying."
            elif str(platform.get("id") or "") == "storage_preflight_blocked":
                recommended = str((storage_diagnosis or {}).get("recommended_fix") or "").strip()
                platform_error = str(platform.get("reason") or "").strip() or "Storage preflight blocked destructive apply."
                if recommended:
                    platform_error += f" Recommended fix: {recommended}"
            else:
                platform_error = str(platform.get("reason") or "").strip() or "No writable storage apply path is available."
            if str(platform.get("id") or "") != "storage_preflight_blocked":
                storage_diagnosis = storage_blocked_diagnosis(plan, pre_change_discovery, apply_mode, platform, platform_error)
                attach_storage_diagnosis(job, apply_state, storage_diagnosis)
                for line in diagnostic_log_lines("Storage preflight", storage_diagnosis):
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Choose storage apply path",
                        3,
                        apply_steps,
                        line,
                        progress_percent=storage_workflow_progress_percent("running_apply", 3, apply_steps),
                    )
                save_storage_apply_state(apply_state, apply_paths)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Choose storage apply path",
            3,
            apply_steps,
            "ok" if platform_supported else "failed",
            "Choose storage apply path",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "path": platform.get("settings_path", "")},
            details=f"Selected {platform.get('label')} ({platform.get('id')}).",
            error="" if platform_supported else platform_error,
        )

        current_step = 4
        if platform.get("id") == "storage_preflight_already_applied":
            apply_state["reboot_required"] = False
            apply_state["workflow_state"] = "apply_complete"
            apply_state["post_reboot_validation"] = "Not required"
            apply_state["status"] = "Completed"
            apply_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            record_storage_apply_step(
                kit_name,
                job,
                apply_state,
                apply_paths,
                "Skip storage apply",
                current_step,
                apply_steps,
                "ok",
                "Skip storage apply",
                targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
                details=str(platform.get("reason") or "Live storage already matches the approved plan."),
            )
            update_job(
                kit_name,
                job,
                "Completed",
                "Finished",
                apply_steps,
                apply_steps,
                "[DONE] Storage apply was skipped because the live layout already matches the approved plan.",
                progress_percent=100,
            )
            return {
                "apply_state": apply_state,
                "apply_paths": apply_paths,
                "pre_change_discovery": pre_change_discovery,
                "post_change_discovery": pre_change_discovery,
            }
        if platform.get("id") == "gen10_hpe_smartstorageconfig":
            current_step, responses = execute_storage_apply_gen10(
                client,
                plan,
                apply_mode,
                platform,
                kit_name,
                job,
                apply_state,
                apply_paths,
                current_step,
                apply_steps,
            )
            apply_state["responses"].extend({"step": "platform_apply", "response": storage_apply_response_excerpt(item)} for item in responses)
            combined_response = {"responses": [storage_apply_response_excerpt(item) for item in responses]}
        elif platform.get("id") in {"standard_redfish_volumes", "multi_controller_standard_redfish_volumes"}:
            current_step, responses = execute_storage_apply_standard_redfish(
                client,
                plan,
                apply_mode,
                platform,
                kit_name,
                job,
                apply_state,
                apply_paths,
                current_step,
                apply_steps,
            )
            apply_state["responses"].extend({"step": "platform_apply", "response": storage_apply_response_excerpt(item)} for item in responses)
            combined_response = {"responses": [storage_apply_response_excerpt(item) for item in responses]}
        else:
            raise ILOError(platform_error or "No writable storage apply path is available.")

        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Poll controller/apply status",
            current_step,
            apply_steps,
            "running",
            "Poll controller/apply status",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="Capturing immediate controller/apply responses for this attempt.",
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Record controller/apply response",
            current_step,
            apply_steps,
            "ok",
            "Poll controller/apply status",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="Recorded platform-specific controller/apply responses.",
            response=combined_response,
        )
        current_step += 1

        reboot_required = any(
            isinstance(item, dict) and item.get("response", {}).get("reboot_required")
            for item in apply_state.get("responses", [])
        )
        apply_state["reboot_required"] = bool(reboot_required)
        apply_state["workflow_state"] = "staged_reboot_required" if apply_state["reboot_required"] else "apply_complete"
        apply_state["post_reboot_validation"] = "Pending manual reboot" if apply_state["reboot_required"] else "Not required"
        apply_state["status"] = "Staged" if apply_state["reboot_required"] else "Completed"
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Determine whether reboot is required",
            current_step,
            apply_steps,
            "ok",
            "Determine whether reboot is required",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=f"reboot_required={apply_state['reboot_required']}",
        )
        current_step += 1

        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Export post-change storage",
            current_step,
            apply_steps,
            "running",
            "Export post-change storage",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=f"Reading current storage state after {apply_mode} apply.",
        )
        post_change_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
        write_storage_discovery_snapshot_files(
            apply_paths["post_change_summary"],
            apply_paths["post_change_raw"],
            cfg,
            post_change_discovery,
            host=host,
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Export post-change storage",
            apply_steps,
            apply_steps,
            "ok",
            "Finished",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=f"Saved {apply_paths['post_change_summary'].name} and {apply_paths['post_change_raw'].name}.",
        )
        apply_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_storage_apply_state(apply_state, apply_paths)
        if apply_state.get("reboot_required"):
            update_job(
                kit_name,
                job,
                "Staged",
                "Reboot required",
                apply_steps,
                15,
                f"[STAGED] Storage changes were staged via {apply_state.get('apply_path')}. Reboot is required before post-reboot validation can complete.",
                progress_percent=storage_workflow_progress_percent("staged_reboot_required", apply_steps, apply_steps),
            )
        else:
            update_job(
                kit_name,
                job,
                "Completed",
                "Finished",
                apply_steps,
                apply_steps,
                f"[DONE] Storage apply finished via {apply_state.get('apply_path')}. reboot_required={apply_state.get('reboot_required')}",
                progress_percent=storage_workflow_progress_percent("apply_complete", apply_steps, apply_steps),
            )
    except Exception as e:
        error_text = str(e).splitlines()[0]
        if getattr(e, "power_reset_details", None):
            diagnosis = power_failure_diagnosis("Storage apply", "On", e)
            attach_storage_diagnosis(job, apply_state, diagnosis)
        failure = build_storage_failure_fields(
            error_text,
            job.get("diagnosis") if isinstance(job.get("diagnosis"), dict) else {},
            stage="Storage apply",
        )
        job["failure_area"] = failure["area"]
        job["failure_reason"] = failure["reason"]
        job["failure_explanation"] = failure["explanation"]
        job["failure_recommended_fix"] = failure["recommended_fix"]
        job["failure_codex_handoff"] = failure["codex_handoff"]
        apply_state["status"] = "Failed"
        apply_state["workflow_state"] = "apply_failed"
        apply_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Storage apply failed",
            job.get("completed_steps", 0),
            apply_steps,
            "failed",
            "Storage apply failed",
            targets={"controller": apply_state.get("controller", {}).get("name") or apply_state.get("controller", {}).get("model") or ""},
            error=error_text,
        )
        if client is not None:
            try:
                post_failure_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
                host = resolve_storage_target_host(cfg).get("resolved", "")
                write_storage_discovery_snapshot_files(
                    apply_paths["post_change_summary"],
                    apply_paths["post_change_raw"],
                    cfg,
                    post_failure_discovery,
                    host=host,
                )
                record_storage_apply_step(
                    kit_name,
                    job,
                    apply_state,
                    apply_paths,
                    "Export post-change storage",
                    job.get("completed_steps", 0),
                    apply_steps,
                    "ok",
                    "Storage apply failed",
                    targets={"controller": apply_state.get("controller", {}).get("name") or apply_state.get("controller", {}).get("model") or ""},
                    details=f"Saved failure-state snapshots to {apply_paths['post_change_summary'].name} and {apply_paths['post_change_raw'].name}.",
                )
            except Exception as post_error:
                apply_state.setdefault("errors", []).append(
                    {"step": "Capture post-change storage discovery", "error": str(post_error).splitlines()[0]}
                )
        save_storage_apply_state(apply_state, apply_paths)
        update_job(
            kit_name,
            job,
            "Failed",
            "Storage apply failed",
            job.get("completed_steps", 0),
            job.get("total_steps", apply_steps),
            f"[FAILED] Storage apply failed: {error_text}",
            progress_percent=storage_workflow_progress_percent("apply_failed", job.get("completed_steps", 0), job.get("total_steps", apply_steps)),
        )


def execute_storage_apply_in_background(
    cfg: dict,
    discovery_raw_path: str,
    raid_plan_path: str,
    apply_mode: str,
    apply_paths: dict[str, Path],
) -> None:
    try:
        run_storage_apply(cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths)
    finally:
        append_storage_apply_history_snapshot(cfg)


def start_storage_apply_background(
    cfg: dict,
    discovery_raw_path: str,
    raid_plan_path: str,
    apply_mode: str,
    apply_paths: dict[str, Path],
) -> None:
    thread = threading.Thread(
        target=execute_storage_apply_in_background,
        args=(cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths),
        daemon=True,
    )
    thread.start()


def build_storage_reboot_validation(post_reboot_discovery: dict, apply_state: dict[str, Any]) -> dict[str, Any]:
    summary = post_reboot_discovery.get("summary", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    volumes = list(hpe.get("volumes", []) or []) + list(standard.get("volumes", []) or [])
    controllers = list(hpe.get("controllers", []) or []) + list(standard.get("controllers", []) or [])
    return {
        "controller_count": len(controllers),
        "logical_volume_count": len(volumes),
        "reboot_required": bool(apply_state.get("reboot_required")),
        "controller_present": bool(controllers),
        "validation_status": "ok" if controllers else "warning",
    }


def run_storage_reboot(
    cfg: dict,
    discovery_raw_path: str,
    raid_plan_path: str,
    apply_paths: dict[str, Path],
) -> None:
    kit_name = cfg["site"]["name"]
    existing_job = load_job(kit_name)
    inherited_root_scope = str(existing_job.get("root_scope") or existing_job.get("scope") or "storage-reboot")
    total_steps = 5
    job = {
        "status": "Running",
        "execution_mode": str(existing_job.get("execution_mode") or "real"),
        "execution_mode_label": str(existing_job.get("execution_mode_label") or "Real execution"),
        "scope": "storage-reboot",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total_steps,
        "logs": [],
        "root_scope": inherited_root_scope,
        "stage_statuses": merge_stage_statuses(
            initialize_stage_statuses(inherited_root_scope, cfg),
            existing_job.get("stage_statuses"),
        ),
        "apply_path": "",
        "reboot_required": True,
        "workflow_state": "reboot_requested",
        "reboot_status": "Running",
        "storage_run_directory": str(apply_paths["directory"]),
        "failure_area": "",
        "failure_reason": "",
        "failure_explanation": "",
        "failure_recommended_fix": "",
        "failure_codex_handoff": "",
    }
    job = carry_forward_job_bundle_metadata(kit_name, job)
    save_job(kit_name, job)

    apply_state = json.loads(apply_paths["apply_results"].read_text(encoding="utf-8")) if apply_paths["apply_results"].exists() else {}
    reboot_state = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "status": "Running",
        "requested": True,
        "steps": [],
        "errors": [],
    }
    apply_state["workflow_state"] = "reboot_requested"
    apply_state["reboot_status"] = "Running"
    apply_state["reboot_requested"] = True
    apply_state["post_reboot_validation"] = "Pending reboot completion"
    save_storage_apply_state(apply_state, apply_paths)
    save_storage_reboot_state(reboot_state, apply_paths)

    try:
        storage_target = resolve_storage_target_host(cfg)
        storage_credentials = resolve_storage_target_credentials(cfg)
        host = storage_target.get("resolved", "")
        username = storage_credentials.get("username", "")
        password = storage_credentials.get("password", "")
        if not host or not username or not password:
            raise ValueError(storage_target.get("error") or storage_credentials.get("error") or "Missing current iLO IP, username, or password for storage reboot.")

        discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        del discovery, discovery_paths, plan, plan_paths
        if apply_state.get("status") not in {"Completed", "Staged"}:
            raise ValueError("Storage reboot requires a completed storage apply run.")
        if not apply_state.get("reboot_required"):
            raise ValueError("Storage reboot is not available because the current run does not require reboot.")

        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))
        apply_state["apply_path"] = apply_state.get("apply_path", "")

        apply_state["workflow_state"] = "reboot_requested"
        apply_state["reboot_status"] = "Requested"
        apply_state["post_reboot_validation"] = "Pending reboot start"
        save_storage_apply_state(apply_state, apply_paths)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Request server reboot",
            0,
            total_steps,
            "running",
            "Request server reboot",
            targets={"controller": apply_state.get("controller", {}).get("name") or apply_state.get("controller", {}).get("model") or ""},
            details="Issuing ComputerSystem.Reset with ResetType=GracefulRestart.",
        )
        reboot_result = client.reboot_server_and_wait(reset_type="GracefulRestart")
        reboot_state["request"] = {
            "path": reboot_result.get("path", ""),
            "reset_type": reboot_result.get("reset_type", "GracefulRestart"),
            "system_path": reboot_result.get("system_path", ""),
        }
        reboot_state["steps"].append({"step": "Request server reboot", "status": "ok", "details": reboot_state["request"]})
        apply_state["workflow_state"] = "waiting_for_reboot_start"
        apply_state["reboot_status"] = "Waiting for reboot start"
        apply_state["post_reboot_validation"] = "Pending server reboot start"
        save_storage_apply_state(apply_state, apply_paths)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Request server reboot",
            1,
            total_steps,
            "ok",
            "Wait for reboot start",
            targets={"path": reboot_result.get("path", "")},
            details=f"Reboot request accepted by iLO. {storage_reboot_result_summary(reboot_result)}",
            response=reboot_result,
        )

        reboot_state["steps"].append({"step": "Wait for reboot start", "status": "ok", "details": {"observed": reboot_result.get("reboot_start_observed"), "detail": reboot_result.get("reboot_start_detail", "")}})
        apply_state["workflow_state"] = "waiting_for_server_return"
        apply_state["reboot_status"] = "Waiting for server return"
        apply_state["post_reboot_validation"] = "Pending server return"
        save_storage_apply_state(apply_state, apply_paths)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Wait for reboot start",
            2,
            total_steps,
            "ok",
            "Wait for reboot start",
            targets={"path": reboot_result.get("path", "")},
            details=reboot_result.get("reboot_start_detail", ""),
        )

        reboot_state["steps"].append({"step": "Wait for server to return", "status": "ok", "details": {"returned": reboot_result.get("system_returned"), "detail": reboot_result.get("return_detail", "")}})
        apply_state["workflow_state"] = "post_reboot_validation_pending"
        apply_state["reboot_status"] = "Server returned"
        apply_state["post_reboot_validation"] = "Capturing post-reboot storage discovery"
        save_storage_apply_state(apply_state, apply_paths)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Wait for server to return",
            3,
            total_steps,
            "ok",
            "Wait for server to return",
            targets={"path": reboot_result.get("path", "")},
            details=f"{reboot_result.get('return_detail', '')} {storage_reboot_result_summary(reboot_result)}",
        )

        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Export post-reboot storage",
            3,
            total_steps,
            "running",
            "Export post-reboot storage",
            targets={"controller": apply_state.get("controller", {}).get("name") or apply_state.get("controller", {}).get("model") or ""},
            details="Reading storage state after reboot.",
        )
        post_reboot_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
        write_storage_discovery_snapshot_files(
            apply_paths["post_reboot_summary"],
            apply_paths["post_reboot_raw"],
            cfg,
            post_reboot_discovery,
            host=host,
        )
        validation = build_storage_reboot_validation(post_reboot_discovery, apply_state)
        reboot_state["validation"] = validation
        reboot_state["status"] = "Completed"
        reboot_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        apply_state["status"] = "Completed"
        apply_state["workflow_state"] = "post_reboot_validation_complete"
        apply_state["reboot_status"] = "Completed"
        apply_state["post_reboot_validation"] = "Complete"
        save_storage_apply_state(apply_state, apply_paths)
        save_storage_reboot_state(reboot_state, apply_paths)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Export post-reboot storage",
            4,
            total_steps,
            "ok",
            "Validate post-reboot storage",
            targets={"controller": apply_state.get("controller", {}).get("name") or apply_state.get("controller", {}).get("model") or ""},
            details=f"Saved {apply_paths['post_reboot_summary'].name} and {apply_paths['post_reboot_raw'].name}.",
        )
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Validate post-reboot storage",
            total_steps,
            total_steps,
            "ok",
            "Finished",
            targets={"controller": apply_state.get("controller", {}).get("name") or apply_state.get("controller", {}).get("model") or ""},
            details=f"controller_present={validation['controller_present']} logical_volume_count={validation['logical_volume_count']}",
        )
        update_job(
            kit_name,
            job,
            "Completed",
            "Finished",
            total_steps,
            total_steps,
            "[DONE] Storage reboot workflow completed and post-reboot validation was captured.",
            progress_percent=storage_workflow_progress_percent("post_reboot_validation_complete", total_steps, total_steps),
        )
    except Exception as e:
        error_text = str(e).splitlines()[0]
        failure = build_storage_failure_fields(
            error_text,
            job.get("diagnosis") if isinstance(job.get("diagnosis"), dict) else {},
            stage="Storage reboot",
        )
        job["failure_area"] = failure["area"]
        job["failure_reason"] = failure["reason"]
        job["failure_explanation"] = failure["explanation"]
        job["failure_recommended_fix"] = failure["recommended_fix"]
        job["failure_codex_handoff"] = failure["codex_handoff"]
        reboot_state["status"] = "Failed"
        reboot_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        reboot_state.setdefault("errors", []).append(error_text)
        apply_state["workflow_state"] = "reboot_failed"
        apply_state["reboot_status"] = "Failed"
        apply_state["post_reboot_validation"] = "Failed"
        save_storage_apply_state(apply_state, apply_paths)
        save_storage_reboot_state(reboot_state, apply_paths)
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Storage reboot failed",
            job.get("completed_steps", 0),
            total_steps,
            "failed",
            "Storage reboot failed",
            targets={"controller": apply_state.get("controller", {}).get("name") or apply_state.get("controller", {}).get("model") or ""},
            error=error_text,
        )
        update_job(
            kit_name,
            job,
            "Failed",
            "Storage reboot failed",
            job.get("completed_steps", 0),
            total_steps,
            f"[FAILED] Storage reboot failed: {error_text}",
            progress_percent=storage_workflow_progress_percent("reboot_failed", job.get("completed_steps", 0), total_steps),
        )


def execute_storage_reboot_in_background(
    cfg: dict,
    discovery_raw_path: str,
    raid_plan_path: str,
    apply_paths: dict[str, Path],
) -> None:
    try:
        run_storage_reboot(cfg, discovery_raw_path, raid_plan_path, apply_paths)
    finally:
        append_storage_apply_history_snapshot(cfg)


def watch_storage_manual_reboot_completion(
    cfg: dict,
    discovery_raw_path: str,
    raid_plan_path: str,
    apply_paths: dict[str, Path],
    *,
    reboot_start_timeout: int = 7200,
    return_timeout: int = 1800,
    poll_interval: int = 15,
) -> None:
    kit_name = cfg["site"]["name"]
    try:
        storage_target = resolve_storage_target_host(cfg)
        storage_credentials = resolve_storage_target_credentials(cfg)
        host = storage_target.get("resolved", "")
        username = storage_credentials.get("username", "")
        password = storage_credentials.get("password", "")
        if not host or not username or not password:
            return

        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))
        system_path = client.get_system_path() if hasattr(client, "get_system_path") else client.get_systems()[0]
        job = load_job(kit_name)

        interruption_observed = False
        start_deadline = time.time() + max(reboot_start_timeout, 1)
        last_detail = ""
        while time.time() < start_deadline:
            apply_state = json.loads(apply_paths["apply_results"].read_text(encoding="utf-8")) if apply_paths["apply_results"].exists() else {}
            if apply_state.get("workflow_state") != "staged_reboot_required" or apply_state.get("reboot_requested"):
                return
            try:
                power_state = client.get_power_state(system_path=system_path) if hasattr(client, "get_power_state") else str(client.get_system(system_path).get("PowerState") or "")
                if power_state and power_state.lower() != "on":
                    interruption_observed = True
                    last_detail = f"Observed PowerState={power_state}."
                    break
            except Exception as e:
                interruption_observed = True
                last_detail = str(e).splitlines()[0]
                break
            time.sleep(max(poll_interval, 1))

        if not interruption_observed:
            return

        apply_state["workflow_state"] = "waiting_for_server_return"
        apply_state["reboot_status"] = "Waiting for server return"
        apply_state["post_reboot_validation"] = "Pending manual reboot return"
        save_storage_apply_state(apply_state, apply_paths)
        update_job(
            kit_name,
            job,
            "Running",
            "Wait for server to return",
            12,
            max(job.get("total_steps", 15), 15),
            f"[RUNNING] Manual reboot detected. Waiting for the server to return. {last_detail}",
            progress_percent=storage_workflow_progress_percent("waiting_for_server_return", 2, 5),
        )

        return_deadline = time.time() + max(return_timeout, 1)
        while time.time() < return_deadline:
            apply_state = json.loads(apply_paths["apply_results"].read_text(encoding="utf-8")) if apply_paths["apply_results"].exists() else {}
            if apply_state.get("reboot_requested"):
                return
            try:
                client.get_summary()
                break
            except Exception as e:
                last_detail = str(e).splitlines()[0]
                time.sleep(max(poll_interval, 1))
        else:
            return

        apply_state["workflow_state"] = "post_reboot_validation_pending"
        apply_state["reboot_status"] = "Server returned"
        apply_state["post_reboot_validation"] = "Capturing post-reboot storage discovery"
        save_storage_apply_state(apply_state, apply_paths)
        update_job(
            kit_name,
            job,
            "Running",
            "Export post-reboot storage",
            14,
            max(job.get("total_steps", 15), 15),
            "[RUNNING] Server returned after manual reboot. Capturing post-reboot storage discovery.",
            progress_percent=storage_workflow_progress_percent("post_reboot_validation_pending", 4, 5),
        )

        post_reboot_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
        write_storage_discovery_snapshot_files(
            apply_paths["post_reboot_summary"],
            apply_paths["post_reboot_raw"],
            cfg,
            post_reboot_discovery,
            host=host,
        )
        validation = build_storage_reboot_validation(post_reboot_discovery, apply_state)
        reboot_state = {
            "started_at": "",
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Completed",
            "requested": False,
            "steps": [
                {"step": "Detect manual reboot", "status": "ok", "details": {"detail": last_detail}},
                {"step": "Wait for server to return", "status": "ok", "details": {"detail": f"Reconnected to {host}."}},
            ],
            "validation": validation,
            "errors": [],
            "mode": "manual_reboot_detected",
        }
        apply_state["status"] = "Completed"
        apply_state["workflow_state"] = "post_reboot_validation_complete"
        apply_state["reboot_status"] = "Completed"
        apply_state["post_reboot_validation"] = "Complete"
        apply_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_storage_apply_state(apply_state, apply_paths)
        save_storage_reboot_state(reboot_state, apply_paths)
        update_job(
            kit_name,
            job,
            "Completed",
            "Finished",
            max(job.get("total_steps", 15), 15),
            max(job.get("total_steps", 15), 15),
            "[DONE] Manual reboot was detected and post-reboot storage validation completed.",
            progress_percent=storage_workflow_progress_percent("post_reboot_validation_complete", 5, 5),
        )
        append_storage_apply_history_snapshot(cfg)
        append_job_history_snapshot(cfg, "storage")
    except Exception:
        return


def start_storage_manual_reboot_watch_background(
    cfg: dict,
    discovery_raw_path: str,
    raid_plan_path: str,
    apply_paths: dict[str, Path],
) -> None:
    thread = threading.Thread(
        target=watch_storage_manual_reboot_completion,
        args=(cfg, discovery_raw_path, raid_plan_path, apply_paths),
        daemon=True,
    )
    thread.start()


def start_storage_reboot_background(
    cfg: dict,
    discovery_raw_path: str,
    raid_plan_path: str,
    apply_paths: dict[str, Path],
) -> None:
    thread = threading.Thread(
        target=execute_storage_reboot_in_background,
        args=(cfg, discovery_raw_path, raid_plan_path, apply_paths),
        daemon=True,
    )
    thread.start()


def combined_progress_percent(completed: int, total: int) -> int:
    if total <= 0:
        return 0
    completed = max(0, min(completed, total))
    return int((completed / total) * 100)


def run_storage_as_part_of_real_run(
    cfg: dict[str, Any],
    client: ILOClient,
    validation_host: str,
    active_host: str,
    storage_execution: dict[str, Any],
    kit_name: str,
    job: dict[str, Any],
    start_step: int,
    total_steps: int,
) -> dict[str, Any]:
    discovery_raw_path = str(storage_execution.get("discovery_raw_path") or "")
    raid_plan_path = str(storage_execution.get("plan_path") or "")
    approved_artifact_host = str(storage_execution.get("approved_host") or "").strip()
    expected_artifact_host = approved_artifact_host or validation_host
    if approved_artifact_host and active_host and approved_artifact_host != active_host:
        update_job(
            kit_name,
            job,
            "Running",
            "Run storage stage",
            start_step,
            total_steps,
            (
                "[INFO] Storage plan was approved from the previous iLO address "
                f"{approved_artifact_host}; applying it through the verified active iLO endpoint {active_host}."
            ),
        )
    discovery, _discovery_paths, plan, plan_paths = restore_storage_page_state(
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        expected_host=expected_artifact_host,
    )
    if not plan_paths:
        raise ValueError("Approved storage plan artifact is missing for the real run.")

    apply_mode = storage_apply_mode_for_plan(plan)
    apply_paths = initialize_storage_apply_artifacts(cfg, plan, plan_paths)
    apply_state = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "mode": apply_mode,
        "status": "Running",
        "apply_path": "",
        "controller": plan.get("source_discovery", {}).get("controller", {}) or {},
        "paths": {key: str(value) for key, value in apply_paths.items()},
        "steps": [],
        "responses": [],
        "errors": [],
        "reboot_required": False,
        "workflow_state": "running_apply",
        "reboot_status": "Not requested",
        "reboot_requested": False,
        "post_reboot_validation": "Pending reboot decision",
    }
    save_storage_apply_state(apply_state, apply_paths)
    job["storage_run_directory"] = str(apply_paths["directory"])
    save_job(kit_name, job)

    current_step = start_step
    system_path, initial_power_state = read_current_power_state(client)
    update_job(
        kit_name,
        job,
        "Running",
        "Run storage stage",
        current_step,
        total_steps,
        f"[INFO] Storage stage initial PowerState={initial_power_state or 'unknown'} on {system_path}.",
    )
    ensure_on = ensure_client_power_state(client, "On", system_path=system_path, timeout_seconds=300, poll_interval=5)
    if str(ensure_on.get("action") or "") == "skip":
        update_job(
            kit_name,
            job,
            "Running",
            "Run storage stage",
            current_step,
            total_steps,
            f"[SKIP] Already On. {power_reset_log_summary(ensure_on, default_action='On')}",
        )
    else:
        update_job(
            kit_name,
            job,
            "Running",
            "Run storage stage",
            current_step,
            total_steps,
            f"[INFO] Storage stage power-on request sent: {power_reset_log_summary(ensure_on, default_action='On')}",
        )
        update_job(
            kit_name,
            job,
            "Running",
            "Run storage stage",
            current_step,
            total_steps,
            f"[OK] Storage stage confirmed server PowerState={ensure_on.get('final_power_state') or ensure_on.get('last_observed_power_state') or 'On'}.",
        )

    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Validate controller and plan",
        current_step,
        total_steps,
        "running",
        "Validate controller and plan",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
        details=f"Validating approved storage plan for saved host={validation_host} while using active iLO endpoint {active_host} in {apply_mode.replace('_', ' ')} mode.",
        progress_percent=combined_progress_percent(current_step, total_steps),
    )
    validate_storage_apply_request(plan, apply_mode, storage_apply_confirmation_for_mode(apply_mode), True)
    current_step += 1
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Validate controller and plan",
        current_step,
        total_steps,
        "ok",
        "Validate controller and plan",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
        details="Approved storage plan, host, controller, and hot spare checks passed for the real run.",
        progress_percent=combined_progress_percent(current_step, total_steps),
    )

    current_step += 1
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Export pre-change storage",
        current_step,
        total_steps,
        "running",
        "Export pre-change storage",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
        details=f"Reading current storage state from active iLO endpoint {active_host} before the real storage apply.",
        progress_percent=combined_progress_percent(current_step, total_steps),
    )
    pre_change_discovery = {}
    platform = {}
    storage_diagnosis = {}
    for attempt in range(1, 7):
        pre_change_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
        plan, storage_diagnosis = storage_preflight_compare_and_remap(plan, pre_change_discovery, apply_mode)
        attach_storage_diagnosis(job, apply_state, storage_diagnosis)
        apply_state["controller"] = plan.get("source_discovery", {}).get("controller", {}) or apply_state["controller"]
        if storage_diagnosis.get("status") == "blocked":
            platform = {
                "id": "storage_preflight_blocked",
                "label": "Storage preflight blocked",
                "supported": False,
                "controller_path": (apply_state.get("controller") or {}).get("path", ""),
                "reason": "; ".join(storage_diagnosis.get("rejection_reasons") or []) or "Storage preflight blocked destructive apply.",
            }
            break
        if storage_diagnosis.get("status") == "already_applied":
            platform = {
                "id": "storage_preflight_already_applied",
                "label": "Storage layout already matches approved plan",
                "supported": True,
                "controller_path": (apply_state.get("controller") or {}).get("path", ""),
                "reason": str(storage_diagnosis.get("selected_action") or "").strip(),
            }
            break
        platform = choose_storage_apply_platform(pre_change_discovery, plan)
        platform_id = str(platform.get("id") or "")
        if platform_id in {"gen10_hpe_smartstorageconfig", "standard_redfish_volumes", "multi_controller_standard_redfish_volumes"}:
            break
        if platform_id == "hpe_smart_storage_read_only" and attempt < 6:
            update_job(
                kit_name,
                job,
                "Running",
                "Choose storage apply path",
                current_step,
                total_steps,
                (
                    f"[WARN] Storage writable apply path not ready yet (attempt {attempt}/6). "
                    "Rechecking storage discovery."
                ),
            )
            time.sleep(5)
            continue
        break
    attach_storage_diagnosis(job, apply_state, storage_diagnosis)
    for line in diagnostic_log_lines("Storage preflight", storage_diagnosis):
        update_job(
            kit_name,
            job,
            "Running",
            "Choose storage apply path",
            current_step,
            total_steps,
            line,
            progress_percent=combined_progress_percent(current_step, total_steps),
        )
    save_storage_apply_state(apply_state, apply_paths)
    write_storage_discovery_snapshot_files(
        apply_paths["pre_change_summary"],
        apply_paths["pre_change_raw"],
        cfg,
        pre_change_discovery,
        host=active_host,
    )
    current_step += 1
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Export pre-change storage",
        current_step,
        total_steps,
        "ok",
        "Export pre-change storage",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
        details=f"Saved {apply_paths['pre_change_summary'].name} and {apply_paths['pre_change_raw'].name}.",
        progress_percent=combined_progress_percent(current_step, total_steps),
    )

    apply_state["apply_path"] = platform.get("label", "")
    platform_supported = platform.get("id") in {"gen10_hpe_smartstorageconfig", "standard_redfish_volumes", "multi_controller_standard_redfish_volumes", "storage_preflight_already_applied"}
    if not platform_supported:
        apply_state["status"] = "Failed"
        apply_state["workflow_state"] = "apply_failed"
        apply_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    platform_error = ""
    if not platform_supported:
        if str(platform.get("id") or "") == "hpe_smart_storage_read_only":
            platform_error = "Storage apply requires server power On and a writable Redfish Volumes path. Current path is inventory-only."
            platform_error += " Recommended fix: run storage discovery again while the server is powered On, review the writable Redfish Volumes options, and re-approve storage before applying."
        elif str(platform.get("id") or "") == "storage_preflight_blocked":
            recommended = str((storage_diagnosis or {}).get("recommended_fix") or "").strip()
            platform_error = str(platform.get("reason") or "").strip() or "Storage preflight blocked destructive apply."
            if recommended:
                platform_error += f" Recommended fix: {recommended}"
        else:
            platform_error = str(platform.get("reason") or "").strip() or "No writable storage apply path is available."
        if str(platform.get("id") or "") != "storage_preflight_blocked":
            storage_diagnosis = storage_blocked_diagnosis(plan, pre_change_discovery, apply_mode, platform, platform_error)
            attach_storage_diagnosis(job, apply_state, storage_diagnosis)
            for line in diagnostic_log_lines("Storage preflight", storage_diagnosis):
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Choose storage apply path",
                    current_step,
                    total_steps,
                    line,
                    progress_percent=combined_progress_percent(current_step, total_steps),
                )
            save_storage_apply_state(apply_state, apply_paths)
    current_step += 1
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Choose storage apply path",
        current_step,
        total_steps,
        "ok" if platform_supported else "failed",
        "Choose storage apply path",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "path": platform.get("settings_path", "")},
        details=f"Selected {platform.get('label')} for the real storage stage.",
        error="" if platform_supported else platform_error,
        progress_percent=combined_progress_percent(current_step, total_steps),
    )

    current_step += 1
    if platform.get("id") == "storage_preflight_already_applied":
        apply_state["reboot_required"] = False
        apply_state["workflow_state"] = "apply_complete"
        apply_state["post_reboot_validation"] = "Not required"
        apply_state["status"] = "Completed"
        apply_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Skip storage apply",
            current_step,
            total_steps,
            "ok",
            "Skip storage apply",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=str(platform.get("reason") or "Live storage already matches the approved plan."),
            progress_percent=combined_progress_percent(current_step, total_steps),
        )
        current_step += 1
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Export post-change storage",
            current_step,
            total_steps,
            "ok",
            "Export post-change storage",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="Skipped storage rewrite because the live layout already matches the approved plan.",
            progress_percent=combined_progress_percent(current_step, total_steps),
        )
        update_job(
            kit_name,
            job,
            "Completed",
            "Finished",
            total_steps,
            total_steps,
            "[DONE] Storage apply was skipped because the live layout already matches the approved plan.",
            progress_percent=100,
        )
        return {
            "apply_state": apply_state,
            "apply_paths": apply_paths,
            "pre_change_discovery": pre_change_discovery,
            "post_change_discovery": pre_change_discovery,
        }
    if platform.get("id") == "gen10_hpe_smartstorageconfig":
        current_step, responses = execute_storage_apply_gen10(
            client,
            plan,
            apply_mode,
            platform,
            kit_name,
            job,
            apply_state,
            apply_paths,
            current_step,
            total_steps,
            progress_resolver=combined_progress_percent,
        )
        apply_state["responses"].extend({"step": "platform_apply", "response": storage_apply_response_excerpt(item)} for item in responses)
        combined_response = {"responses": [storage_apply_response_excerpt(item) for item in responses]}
    elif platform.get("id") in {"standard_redfish_volumes", "multi_controller_standard_redfish_volumes"}:
        current_step, responses = execute_storage_apply_standard_redfish(
            client,
            plan,
            apply_mode,
            platform,
            kit_name,
            job,
            apply_state,
            apply_paths,
            current_step,
            total_steps,
            progress_resolver=combined_progress_percent,
        )
        apply_state["responses"].extend({"step": "platform_apply", "response": storage_apply_response_excerpt(item)} for item in responses)
        combined_response = {"responses": [storage_apply_response_excerpt(item) for item in responses]}
    else:
        raise ILOError(platform_error or "No writable storage apply path is available.")

    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Record controller/apply response",
        current_step,
        total_steps,
        "ok",
        "Record controller/apply response",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
        details="Captured the immediate controller/apply response for the real storage stage.",
        response=combined_response,
        progress_percent=combined_progress_percent(current_step, total_steps),
    )

    current_step += 1
    reboot_required = any(
        isinstance(item, dict) and item.get("response", {}).get("reboot_required")
        for item in apply_state.get("responses", [])
    )
    apply_state["reboot_required"] = bool(reboot_required)
    apply_state["workflow_state"] = "staged_reboot_required" if apply_state["reboot_required"] else "apply_complete"
    apply_state["post_reboot_validation"] = "Pending reboot" if apply_state["reboot_required"] else "Not required"
    apply_state["status"] = "Staged" if apply_state["reboot_required"] else "Completed"
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Determine whether reboot is required",
        current_step,
        total_steps,
        "ok",
        "Determine whether reboot is required",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
        details=f"reboot_required={apply_state['reboot_required']}",
        progress_percent=combined_progress_percent(current_step, total_steps),
    )

    current_step += 1
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Export post-change storage",
        current_step,
        total_steps,
        "running",
        "Export post-change storage",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
        details=f"Reading current storage state after {apply_mode.replace('_', ' ')} apply.",
        progress_percent=combined_progress_percent(current_step, total_steps),
    )
    post_change_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
    write_storage_discovery_snapshot_files(
        apply_paths["post_change_summary"],
        apply_paths["post_change_raw"],
        cfg,
        post_change_discovery,
        host=active_host,
    )
    current_step += 1
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Export post-change storage",
        current_step,
        total_steps,
        "ok",
        "Export post-change storage",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
        details=f"Saved {apply_paths['post_change_summary'].name} and {apply_paths['post_change_raw'].name}.",
        progress_percent=combined_progress_percent(current_step, total_steps),
    )

    if apply_state.get("reboot_required"):
        apply_state["workflow_state"] = "reboot_requested"
        apply_state["reboot_status"] = "Requested"
        apply_state["reboot_requested"] = True
        apply_state["post_reboot_validation"] = "Pending reboot start"
        current_step += 1
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Request server reboot",
            current_step,
            total_steps,
            "running",
            "Request server reboot",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="Storage stage requires a reboot, so the real run is requesting it now.",
            progress_percent=combined_progress_percent(current_step, total_steps),
        )
        reboot_result = client.reboot_server_and_wait(reset_type="GracefulRestart")
        reboot_state = {
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "Completed",
            "requested": True,
            "steps": [{"step": "Request server reboot", "result": reboot_result}],
            "errors": [],
        }
        save_storage_reboot_state(reboot_state, apply_paths)

        apply_state["workflow_state"] = "waiting_for_server_return"
        apply_state["reboot_status"] = "Server returned"
        apply_state["post_reboot_validation"] = "Capturing post-reboot storage discovery"
        current_step += 1
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Wait for server return",
            current_step,
            total_steps,
            "ok",
            "Wait for server return",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=f"{reboot_result.get('return_detail') or reboot_result.get('reboot_start_detail') or 'Server returned after reboot.'} {storage_reboot_result_summary(reboot_result)}",
            response=reboot_result,
            progress_percent=combined_progress_percent(current_step, total_steps),
        )

        current_step += 1
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Export post-reboot storage",
            current_step,
            total_steps,
            "running",
            "Export post-reboot storage",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details="Capturing post-reboot storage state for validation.",
            progress_percent=combined_progress_percent(current_step, total_steps),
        )
        post_reboot_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
        write_storage_discovery_snapshot_files(
            apply_paths["post_reboot_summary"],
            apply_paths["post_reboot_raw"],
            cfg,
            post_reboot_discovery,
            host=active_host,
        )
        validation = build_storage_reboot_validation(post_reboot_discovery, apply_state)
        apply_state["status"] = "Completed"
        apply_state["workflow_state"] = "post_reboot_validation_complete"
        apply_state["reboot_status"] = "Completed"
        apply_state["post_reboot_validation"] = "Complete"
        current_step += 1
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Post-reboot validation",
            current_step,
            total_steps,
            "ok",
            "Post-reboot validation",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
            details=(
                f"controller_count={validation.get('controller_count')} | "
                f"logical_volume_count={validation.get('logical_volume_count')} | "
                f"validation_status={validation.get('validation_status')}"
            ),
            response=validation,
            progress_percent=combined_progress_percent(current_step, total_steps),
        )
    else:
        reboot_state = {
            "started_at": "",
            "finished_at": "",
            "status": "Not required",
            "requested": False,
            "steps": [],
            "errors": [],
        }
        save_storage_reboot_state(reboot_state, apply_paths)
        for step_name in ("Request server reboot", "Wait for server return", "Post-reboot validation"):
            current_step += 1
            record_storage_apply_step(
                kit_name,
                job,
                apply_state,
                apply_paths,
                step_name,
                current_step,
                total_steps,
                "skip",
                step_name,
                targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""},
                details="No storage reboot was required for this real run.",
                progress_percent=combined_progress_percent(current_step, total_steps),
            )

    apply_state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_storage_apply_state(apply_state, apply_paths)
    return {
        "apply_paths": apply_paths,
        "apply_state": apply_state,
        "plan": plan,
        "plan_paths": plan_paths,
        "discovery": discovery,
        "final_step": current_step,
    }


def render_exports_folder_listing(root: Path) -> str:
    lines = [f"Exports Root: {root}", ""]
    servers = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.lower())

    if not servers:
        lines.append("No live inventory exports yet.")
        return "\n".join(lines)

    for server_dir in servers:
        lines.append(f"{server_dir.name}/")
        captures = sorted([p for p in server_dir.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)
        for capture_dir in captures[:10]:
            lines.append(f"  {capture_dir.name}/")
            for file_name in ("summary.yml", "raw.json"):
                file_path = capture_dir / file_name
                if file_path.exists():
                    lines.append(f"    {file_name}")
        lines.append("")

    return "\n".join(lines).rstrip()

default_config = core_default_config


merge_defaults = core_merge_defaults
normalize_ilo_additional_users = core_normalize_ilo_additional_users
standard_ilo_policy_defaults = core_standard_ilo_policy_defaults
normalize_ilo_policy = core_normalize_ilo_policy
standard_ilo_policy_kit_id = core_standard_ilo_policy_kit_id
build_policy_ilo_username = core_build_policy_ilo_username
standard_ilo_policy_accounts = core_standard_ilo_policy_accounts
build_standard_ilo_policy = core_build_standard_ilo_policy
policy_enabled = core_policy_enabled
build_ilo_discovery_targets = core_build_ilo_discovery_targets
normalize_snmp_users = core_normalize_snmp_users
extract_ilo_additional_users_from_form = core_extract_ilo_additional_users_from_form
extract_snmp_users_from_form = core_extract_snmp_users_from_form
normalize_ilo_config = core_normalize_ilo_config
subnet_details = core_subnet_details
ip_at_offset = core_ip_at_offset
build_default_ip_plan = core_build_default_ip_plan
validate_ip_for_subnet = core_validate_ip_for_subnet
build_legacy_offset_plan = core_build_legacy_offset_plan
normalize_ip_plan = core_normalize_ip_plan
calc_ip_plan = core_calc_ip_plan
apply_ip_plan = core_apply_ip_plan


def section_state(complete: bool) -> dict:
    if complete:
        return {"label": "Complete", "class_name": "ready"}
    return {"label": "Not Complete", "class_name": "pending"}


def summarize_section_states(cfg: dict) -> dict:
    completion = cfg.get("section_completion", {})

    return {
        "basics": section_state(bool(completion.get("basics", False))),
        "network": section_state(bool(completion.get("network", False))),
        "included": section_state(bool(completion.get("included", False))),
        "credentials": section_state(bool(completion.get("credentials", False))),
    }


def build_cards():
    return [
        {"title": "iLO", "status": "Ready", "desc": "Connect, configure, mount media, power cycle"},
        {"title": "ESXi", "status": "Pending", "desc": "Generate KS.CFG and unattended install"},
        {"title": "Windows 2022", "status": "Pending", "desc": "Create VM and unattended setup"},
        {"title": "QNAP / ioSafe", "status": "Pending", "desc": "Storage and NAS configuration"},
        {"title": "Cisco Switch", "status": "Pending", "desc": "Layer 3 switch provisioning"},
    ]


def build_action_feedback(
    title: str,
    summary: str,
    *,
    tone: str = "progress",
    status_label: str | None = None,
    outcomes: list[str] | None = None,
    details: list[str] | None = None,
    links: list[dict[str, str]] | None = None,
):
    return {
        "title": title,
        "summary": summary,
        "tone": tone,
        "status_label": status_label or ("Done" if tone == "ready" else "Working" if tone == "progress" else "Needs attention"),
        "outcomes": outcomes or [],
        "details": details or [],
        "links": links or [],
    }


def build_execution_review(cfg: dict, scope: str, *, include_runtime: bool = True):
    lines = [f"Execution scope: {scope}", ""]
    execution_mode = execution_mode_for_scope(scope)
    storage_review = build_storage_review_context(cfg)
    selected_scope_keys = run_center_scope_keys(scope, cfg)
    esxi_install_review = build_esxi_install_review(cfg, include_runtime=include_runtime) if scope in {"esxi", "included"} or "esxi" in selected_scope_keys else {}
    storage_validation_error = None
    try:
        storage_execution = validate_storage_ready_for_ilo_run(cfg)
    except Exception as e:
        storage_execution = {"included": bool(storage_review.get("include_in_ilo_run"))}
        storage_validation_error = str(e).splitlines()[0]
    components = {
        "ilo": {
            "name": "iLO",
            "target": cfg["ilo"].get("current_ip") or cfg["ilo"].get("host", "") or "Not set",
            "summary": "Update the iLO network settings, hostname, and saved hardening settings.",
            "review_href": "/ilo",
        },
        "esxi": {
            "name": "ESXi",
            "target": cfg["esxi"].get("management_ip", "") or cfg.get("ip_plan", {}).get("esxi", "") or "Not set",
            "summary": "Use the saved ESXi setup and generated install inputs.",
            "review_href": "/esxi",
        },
        "windows": {
            "name": "Windows",
            "target": cfg["windows"].get("ip_address", "") or cfg.get("ip_plan", {}).get("windows", "") or "Not set",
            "summary": "Use the saved Windows VM name, network plan, and admin sign-in settings.",
            "review_href": "/windows",
        },
        "qnap": {
            "name": "QNAP",
            "target": cfg["qnap"].get("ip", "") or cfg.get("ip_plan", {}).get("qnap", "") or "Not set",
            "summary": "Use the saved QNAP hostname and sign-in settings.",
            "review_href": "/qnap",
        },
        "iosafe": {
            "name": "ioSafe",
            "target": cfg["iosafe"].get("ip", "") or cfg.get("ip_plan", {}).get("iosafe", "") or "Not set",
            "summary": "Run the saved ioSafe setup for the selected management target.",
            "review_href": "/global-settings",
        },
        "cisco_switch": {
            "name": "Cisco Switch",
            "target": cfg["cisco_switch"].get("management_ip", "") or cfg["cisco_switch"].get("ip", "") or cfg.get("ip_plan", {}).get("switch", "") or "Not set",
            "summary": "Run the saved switch management setup and template-driven changes.",
            "review_href": "/cisco",
        },
        "netapp": {
            "name": "NetApp",
            "target": cfg["netapp"].get("host", "") or cfg.get("ip_plan", {}).get("netapp", "") or "Not set",
            "summary": "Run supported NetApp safe-apply actions and log anything still blocked or manual.",
            "review_href": "/modules/netapp",
        },
        "storage": {
            "name": "Storage / RAID",
            "target": storage_review.get("approval", {}).get("host") or storage_review.get("latest", {}).get("host") or "Not set",
            "summary": "Use the exact approved storage plan if storage is included in this run.",
            "review_href": "/storage#storage-approval-actions" if storage_review.get("approved") else "/storage#storage-review-start",
        },
    }

    def build_storage_run_review() -> dict[str, Any]:
        approval = storage_review.get("approval", {}) or {}
        plan_summary = approval.get("plan_summary", {}) or {}
        plan_path = str(approval.get("plan_path") or "")
        discovery_raw_path = str(approval.get("discovery_raw_path") or "")
        apply_mode = str(plan_summary.get("mode") or "")
        if not apply_mode and plan_path:
            try:
                stored_plan, _plan_paths = load_storage_plan_artifact(plan_path)
                apply_mode = storage_apply_mode_for_plan(stored_plan)
            except Exception:
                apply_mode = ""
        mode_label = apply_mode.replace("_", " ") if apply_mode else "Not set"
        return {
            "approved_host": str(approval.get("host") or ""),
            "plan_path": plan_path,
            "discovery_raw_path": discovery_raw_path,
            "apply_mode": apply_mode,
            "apply_mode_label": mode_label,
            "reboot_expected": bool(approval.get("reboot_expected")),
            "controller": str(plan_summary.get("controller") or ""),
            "os_bays": str(plan_summary.get("os_bays") or ""),
            "data_bays": str(plan_summary.get("data_bays") or ""),
            "spare_bay": str(plan_summary.get("spare_bay") or ""),
        }

    def stage_state(key: str, included: bool) -> tuple[str, str, str, str | None]:
        if not included:
            return "not_included", "Not included", "progress", "This stage is not part of the selected run."
        if key == "storage":
            if storage_review.get("stale"):
                return "needs_review", "Needs review", "pending", storage_review.get("status_reason") or "The storage plan must be reviewed again."
            if storage_validation_error:
                return "blocked", "Blocked", "pending", storage_validation_error
            if not storage_review.get("approved"):
                return "blocked", "Blocked", "pending", "No approved storage plan is available."
            return "ready", "Ready", "ready", None
        checks = build_validation_checks(cfg, key)
        blocked_check = next((item for item in checks if not item.get("ok")), None)
        if blocked_check:
            return "blocked", "Blocked", "pending", blocked_check.get("details") or "This stage needs more setup."
        return "ready", "Ready", "ready", None

    def stage_checks(key: str, included: bool) -> list[dict[str, Any]]:
        if not included:
            return []
        if key == "storage":
            return build_validation_checks(cfg, "storage")
        if key in {"ilo", "esxi", "windows", "qnap", "netapp", "cisco_switch"}:
            return build_validation_checks(cfg, key)
        return []

    def stage_state_used(key: str) -> str:
        if key == "ilo":
            return (
                f"Saved iLO target {cfg['ilo'].get('current_ip') or cfg['ilo'].get('host') or 'Not set'}"
                f" -> final IP {cfg['ilo'].get('target_ip') or '(unchanged)'}"
            )
        if key == "esxi":
            return (
                f"Saved ESXi host {esxi_install_review.get('hostname') or '(not set)'} at "
                f"{esxi_install_review.get('management_ip') or 'Not set'}"
            )
        if key == "windows":
            return f"Saved Windows VM {cfg['windows'].get('vm_name') or '(not set)'} at {cfg['windows'].get('ip_address') or cfg.get('ip_plan', {}).get('windows', '') or 'Not set'}"
        if key == "qnap":
            return f"Saved QNAP host {cfg['qnap'].get('hostname') or '(not set)'} at {cfg['qnap'].get('ip') or cfg.get('ip_plan', {}).get('qnap', '') or 'Not set'}"
        if key == "netapp":
            protocol = str(cfg.get("netapp", {}).get("storage_protocol") or "nfs").upper()
            return f"Saved NetApp target {cfg['netapp'].get('host') or cfg.get('ip_plan', {}).get('netapp', '') or 'Not set'} with protocol {protocol}"
        if key == "cisco_switch":
            cisco_cfg = cfg.get("cisco_switch", {}) or {}
            return f"Saved Cisco target {cisco_cfg.get('management_ip') or cisco_cfg.get('ip') or cfg.get('ip_plan', {}).get('switch', '') or 'Not set'} with approval {dict(cisco_cfg.get('config_approval') or {}).get('state') or 'not approved'}"
        if key == "storage":
            approval = storage_review.get("approval", {}) or {}
            plan_summary = approval.get("plan_summary", {}) or {}
            mode = plan_summary.get("mode") or "Approved layout"
            return f"{mode} from {approval.get('plan_path') or '(missing approved plan path)'}"
        return "Saved workspace settings"

    def stage_settings(key: str) -> list[str]:
        if key == "ilo":
            return [
                f"Current iLO IP: {cfg['ilo'].get('current_ip') or cfg['ilo'].get('host') or 'Not set'}",
                f"Planned iLO IP: {cfg['ilo'].get('target_ip') or 'Unchanged'}",
                f"Gateway: {cfg['ilo'].get('gateway') or cfg.get('ip_plan', {}).get('gateway') or 'Not set'}",
                f"Hostname: {cfg['ilo'].get('hostname') or 'Unchanged'}",
                f"DNS servers: {', '.join([x for x in cfg.get('shared_network', {}).get('dns_servers', []) if x and str(x).strip()]) or 'Not set'}",
                f"SNMP user: {cfg.get('shared_snmp', {}).get('v3_username') or 'Not set'}",
                f"SNMP auth: {cfg.get('shared_snmp', {}).get('v3_auth_protocol', 'SHA')}",
                f"SNMP privacy: {cfg.get('shared_snmp', {}).get('v3_priv_protocol', 'AES')}",
            ]
        if key == "esxi":
            values = [
                f"Source: {esxi_install_review.get('source_label') or 'Not set'}",
                f"Management IP: {esxi_install_review.get('management_ip') or 'Not set'}",
                f"Subnet mask: {esxi_install_review.get('subnet_mask') or 'Not set'}",
                f"Gateway: {esxi_install_review.get('gateway') or 'Not set'}",
                f"Hostname: {esxi_install_review.get('hostname') or 'Not set'}",
                f"DNS servers: {', '.join(esxi_install_review.get('dns_servers') or []) or 'Not set'}",
                f"Root password: {'Saved' if esxi_install_review.get('root_password_saved') else 'Missing'}",
                f"Built ISO path: {esxi_install_review.get('output_iso_path') or 'Not set'}",
                f"Virtual media URL: {esxi_install_review.get('virtual_media_url') or 'Not set'}",
                f"Virtual media URL source: {esxi_install_review.get('virtual_media_base_url_source') or 'Not set'}",
                f"Current ESXi reachability: {(esxi_install_review.get('runtime_status') or {}).get('summary') or 'Not checked'}",
                f"Current ESXi action: {(esxi_install_review.get('runtime_status') or {}).get('recommended_action') or 'Not checked'}",
                f"Base ISO path: {esxi_install_review.get('base_iso_path') or 'Not set'}",
                f"Manual test defaults: {esxi_install_review.get('manual_defaults_label') or 'Not set'}",
            ]
            if esxi_install_review.get("vlan_id"):
                values.append(f"VLAN ID: {esxi_install_review.get('vlan_id')}")
            if esxi_install_review.get("ntp_server"):
                values.append(f"NTP server: {esxi_install_review.get('ntp_server')}")
            values.append(f"Enable SSH: {'Yes' if esxi_install_review.get('enable_ssh') else 'No'}")
            values.append(f"Disable IPv6: {'Yes' if esxi_install_review.get('disable_ipv6') else 'No'}")
            values.append(f"Debug no reboot: {'Yes' if esxi_install_review.get('debug_no_reboot') else 'No'}")
            install_target = dict(esxi_install_review.get("install_target") or {})
            values.append(f"KS.CFG install target: {install_target.get('kickstart_line') or 'Not set'}")
            values.append(f"Preferred install target: {install_target.get('preferred_target') or 'Not set'}")
            if esxi_install_review.get("missing_fields"):
                values.append(f"Missing required values: {', '.join(esxi_install_review.get('missing_fields') or [])}")
            if esxi_install_review.get("validation_errors"):
                values.append(f"Saved-value checks: {'; '.join(esxi_install_review.get('validation_errors') or [])}")
            return values
        if key == "windows":
            windows_cfg = cfg.get("windows", {}) or {}
            dns_values = [x for x in (windows_cfg.get("dns_servers") or cfg.get("shared_network", {}).get("dns_servers", [])) if x and str(x).strip()]
            return [
                f"Target IP: {windows_cfg.get('ip_address') or cfg.get('ip_plan', {}).get('windows') or 'Not set'}",
                f"Subnet mask: {windows_cfg.get('subnet_mask') or 'Not set'}",
                f"Gateway: {windows_cfg.get('gateway') or cfg.get('ip_plan', {}).get('gateway') or 'Not set'}",
                f"VM name: {windows_cfg.get('vm_name') or 'Not set'}",
                f"DNS servers: {', '.join(dns_values) or 'Not set'}",
                f"Admin password: {'Saved' if windows_cfg.get('admin_password') else 'Missing'}",
            ]
        if key == "qnap":
            qnap_cfg = cfg.get("qnap", {}) or {}
            return [
                f"Target IP: {qnap_cfg.get('ip') or cfg.get('ip_plan', {}).get('qnap') or 'Not set'}",
                f"Hostname: {qnap_cfg.get('hostname') or 'Not set'}",
                f"Username: {qnap_cfg.get('username') or 'Not set'}",
                f"Password: {'Saved' if qnap_cfg.get('password') else 'Missing'}",
            ]
        if key == "netapp":
            netapp_cfg = cfg.get("netapp", {}) or {}
            return [
                f"Target host: {netapp_cfg.get('host') or cfg.get('ip_plan', {}).get('netapp') or 'Not set'}",
                f"Username: {netapp_cfg.get('username') or 'Not set'}",
                f"Password: {'Saved' if netapp_cfg.get('password') else 'Missing'}",
                f"Storage protocol: {str(netapp_cfg.get('storage_protocol') or 'nfs').upper()}",
            ]
        if key == "cisco_switch":
            cisco_cfg = cfg.get("cisco_switch", {}) or {}
            approval = dict(cisco_cfg.get("config_approval") or {})
            return [
                f"Management IP: {cisco_cfg.get('management_ip') or cisco_cfg.get('ip') or cfg.get('ip_plan', {}).get('switch') or 'Not set'}",
                f"Console port: {cisco_cfg.get('console_port') or 'Not set'}",
                f"Username: {cisco_cfg.get('username') or 'Not set'}",
                f"Password: {'Saved' if cisco_cfg.get('password') else 'Missing'}",
                f"SSH test: {'Passed' if dict(cisco_cfg.get('last_ssh_test') or {}).get('ok') else 'Not passed'}",
                f"Config approval: {approval.get('state') or 'Not approved'}",
            ]
        if key == "storage":
            approval = storage_review.get("approval", {}) or {}
            plan_summary = approval.get("plan_summary", {}) or {}
            arrays = list(plan_summary.get("arrays") or [])
            return [
                f"Controller: {plan_summary.get('controller') or 'Not set'}",
                *[
                    f"{str(item.get('role') or '').upper()} {raid_label(str(item.get('raid_level') or ''))}: "
                    f"{item.get('controller') or item.get('controller_path') or 'Not set'} | "
                    f"bays {item.get('bays') or 'none'} | "
                    f"serials {', '.join(item.get('selected_drive_serials') or []) or 'none'}"
                    for item in arrays
                ],
                f"Hot spare: {(plan_summary.get('hot_spare') or {}).get('controller') or 'Not reserved'} | bay {(plan_summary.get('hot_spare') or {}).get('bay') or 'none'} | serial {(plan_summary.get('hot_spare') or {}).get('serial_number') or 'none'}",
                f"Approved host: {approval.get('host') or 'Not set'}",
            ]
        return []

    def stage_detail_rows(key: str) -> list[dict[str, str]]:
        if key == "ilo":
            dns_values = ", ".join([x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x and str(x).strip()]) or "Not set"
            extra_users = cfg.get("ilo", {}).get("additional_users", []) or []
            snmp_users = cfg.get("shared_snmp", {}).get("users", []) or []
            rows = [
                {"label": "Current iLO IP", "value": cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or "Not set"},
                {"label": "Planned iLO IP", "value": cfg["ilo"].get("target_ip") or "Unchanged"},
                {"label": "Hostname", "value": cfg["ilo"].get("hostname") or "Unchanged"},
                {"label": "Gateway", "value": cfg["ilo"].get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "Not set"},
                {"label": "DNS servers", "value": dns_values},
                {"label": "SNMP profile count", "value": str(len(snmp_users)) if snmp_users else "0"},
                {"label": "Extra local users", "value": str(len(extra_users)) if extra_users else "0"},
            ]
            if snmp_users:
                primary = snmp_users[0]
                rows.extend(
                    [
                        {"label": "Primary SNMPv3 username", "value": str(primary.get("username") or "Not set")},
                        {"label": "Primary SNMPv3 protocols", "value": f"{primary.get('auth_protocol') or 'SHA'} / {primary.get('priv_protocol') or 'AES'}"},
                    ]
                )
            return rows
        if key == "storage":
            review = build_storage_run_review()
            return [
                {"label": "Approved host", "value": review.get("approved_host") or "Not set"},
                {"label": "Apply mode", "value": review.get("apply_mode_label") or "Not set"},
                {"label": "Controller", "value": review.get("controller") or "Not set"},
                {"label": "OS RAID 1 bays", "value": review.get("os_bays") or "Not selected"},
                {"label": "Data RAID bays", "value": review.get("data_bays") or "Not selected"},
                {"label": "Hot spare bay", "value": review.get("spare_bay") or "Not reserved"},
                {"label": "Approved plan path", "value": review.get("plan_path") or "Not set"},
                {"label": "Approved discovery path", "value": review.get("discovery_raw_path") or "Not set"},
            ]
        if key == "esxi":
            rows = [
                {"label": "Source", "value": esxi_install_review.get("source_label") or "Not set"},
                {"label": "Hostname", "value": esxi_install_review.get("hostname") or "Not set"},
                {"label": "Management IP", "value": esxi_install_review.get("management_ip") or "Not set"},
                {"label": "Subnet mask", "value": esxi_install_review.get("subnet_mask") or "Not set"},
                {"label": "Gateway", "value": esxi_install_review.get("gateway") or "Not set"},
                {"label": "DNS servers", "value": ", ".join(esxi_install_review.get("dns_servers") or []) or "Not set"},
                {"label": "Root password saved", "value": "Yes" if esxi_install_review.get("root_password_saved") else "No"},
                {"label": "Built ISO path", "value": esxi_install_review.get("output_iso_path") or "Not set"},
                {"label": "Virtual media URL", "value": esxi_install_review.get("virtual_media_url") or "Not set"},
                {"label": "Virtual media URL source", "value": esxi_install_review.get("virtual_media_base_url_source") or "Not set"},
                {"label": "Virtual media URL probe", "value": f"{esxi_install_review.get('virtual_media_base_url_host') or 'unknown'}:{esxi_install_review.get('virtual_media_base_url_port') or 'unknown'} via {esxi_install_review.get('virtual_media_base_url_probe_target') or 'unknown'}"},
                {"label": "Current ESXi reachability", "value": (esxi_install_review.get("runtime_status") or {}).get("summary") or "Not checked"},
                {"label": "Current ESXi action", "value": (esxi_install_review.get("runtime_status") or {}).get("recommended_action") or "Not checked"},
                {"label": "Base ISO path", "value": esxi_install_review.get("base_iso_path") or "Not set"},
                {"label": "Manual test defaults", "value": esxi_install_review.get("manual_defaults_label") or "Not set"},
                {"label": "Enable SSH", "value": "Yes" if esxi_install_review.get("enable_ssh") else "No"},
                {"label": "Disable IPv6", "value": "Yes" if esxi_install_review.get("disable_ipv6") else "No"},
                {"label": "Debug no reboot", "value": "Yes" if esxi_install_review.get("debug_no_reboot") else "No"},
                {"label": "KS.CFG install target", "value": (esxi_install_review.get("install_target") or {}).get("kickstart_line") or "Not set"},
                {"label": "Preferred install target", "value": (esxi_install_review.get("install_target") or {}).get("preferred_target") or "Not set"},
            ]
            if esxi_install_review.get("vlan_id"):
                rows.append({"label": "VLAN ID", "value": str(esxi_install_review.get("vlan_id"))})
            if esxi_install_review.get("ntp_server"):
                rows.append({"label": "NTP server", "value": str(esxi_install_review.get("ntp_server"))})
            if esxi_install_review.get("missing_fields"):
                rows.append({"label": "Missing required values", "value": ", ".join(esxi_install_review.get("missing_fields") or [])})
            return rows
        return []

    def stage_dependencies(key: str) -> list[str]:
        if key == "ilo":
            return ["Global Settings", "Saved iLO target and credentials"]
        if key == "storage":
            return ["Current iLO target", "Approved storage plan"]
        if key == "esxi":
            deps = ["Global Settings", "Saved ESXi host details", "iLO target ready"]
            if cfg.get("included", {}).get("storage"):
                deps.append("Storage ready if the install depends on the approved layout")
            return deps
        if key == "windows":
            return ["Global Settings", "Saved Windows VM details"]
        if key == "qnap":
            return ["Global Settings", "Saved QNAP host details"]
        if key == "netapp":
            return ["NetApp bootstrap complete", "Saved NetApp host and credentials", "Current ONTAP discovery"]
        return ["Saved workspace settings"]

    def stage_restart_expected(key: str) -> bool:
        if key == "storage":
            return bool(storage_review.get("approval", {}).get("reboot_expected"))
        if key == "ilo":
            return bool(storage_review.get("include_in_ilo_run") and storage_review.get("approval", {}).get("reboot_expected"))
        return False

    def stage_fix_href(key: str) -> str:
        if key == "storage":
            return "/storage#storage-review-start"
        return f"/{key}"

    def stage_fix_label(key: str) -> str:
        if key == "storage":
            return "Fix on Storage / RAID"
        if key == "ilo":
            return "Fix on iLO"
        return f"Fix on {components[key]['name']}"

    def stage_change_summary(key: str) -> dict[str, str]:
        if key == "ilo":
            current_ip = cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or "Not set"
            target_ip = cfg["ilo"].get("target_ip") or current_ip
            gateway = cfg["ilo"].get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "Not set"
            hostname = cfg["ilo"].get("hostname") or "Unchanged"
            dns_values = [x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x and str(x).strip()]
            verify_suffix = f" ({', '.join(dns_values[:2])})" if dns_values else ""
            return {
                "name": "iLO",
                "before": f"Current iLO IP {current_ip}",
                "after": f"Final iLO IP {target_ip} | gateway {gateway} | hostname {hostname}",
                "verify": f"Read back DNS, SNMP, and final iLO endpoint{verify_suffix}.",
            }
        if key == "storage":
            review = build_storage_run_review()
            return {
                "name": "Storage",
                "before": storage_review.get("current_summary") or "Current storage will be read from the latest approved discovery.",
                "after": (
                    f"{review.get('apply_mode_label') or 'Approved layout'} | controller {review.get('controller') or 'Not set'} | "
                    f"OS {review.get('os_bays') or 'Not selected'} | data {review.get('data_bays') or 'Not selected'} | spare {review.get('spare_bay') or 'Not reserved'}"
                ),
                "verify": "Use the approved artifact, apply the layout, and validate the server again after any required reboot.",
            }
        if key == "esxi":
            return {
                "name": "ESXi",
                "before": f"Installer target {esxi_install_review.get('management_ip') or 'Not set'}",
                "after": (
                    f"Boot custom ISO for {esxi_install_review.get('hostname') or 'Not set'}"
                    f" | built ISO {esxi_install_review.get('output_iso_path') or 'Not set'}"
                ),
                "verify": f"Confirm virtual media mount, boot progression, and ESXi reachability on {esxi_install_review.get('management_ip') or 'the target IP'}.",
            }
        if key == "windows":
            return {
                "name": "Windows",
                "before": f"Current target {cfg['windows'].get('ip_address') or cfg.get('ip_plan', {}).get('windows') or 'Not set'}",
                "after": f"Apply the saved VM plan for {cfg['windows'].get('vm_name') or 'the selected Windows VM'}",
                "verify": "Use the saved network and administrator settings during the run.",
            }
        if key == "qnap":
            return {
                "name": "QNAP",
                "before": f"Current target {cfg['qnap'].get('ip') or cfg.get('ip_plan', {}).get('qnap') or 'Not set'}",
                "after": f"Apply the saved QNAP host details for {cfg['qnap'].get('hostname') or 'the selected NAS'}",
                "verify": "Use the saved hostname and sign-in details during the run.",
            }
        if key == "netapp":
            protocol = str(cfg.get("netapp", {}).get("storage_protocol") or "nfs").upper()
            return {
                "name": "NetApp",
                "before": f"Current target {cfg['netapp'].get('host') or cfg.get('ip_plan', {}).get('netapp') or 'Not set'}",
                "after": f"Run supported NetApp safe-apply actions for protocol {protocol}",
                "verify": "Review execution logs for blocked/manual actions and verify ONTAP state after the run.",
            }
        return {
            "name": components[key]["name"],
            "before": f"Current target {components[key]['target']}",
            "after": components[key]["summary"],
            "verify": "Use the saved stage settings during the run.",
        }

    def stage_change_items(key: str) -> list[dict[str, str]]:
        if key == "ilo":
            dns_values = ", ".join([x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x and str(x).strip()]) or "Not set"
            shared_snmp = cfg.get("shared_snmp", {}) or {}
            extra_users = cfg.get("ilo", {}).get("additional_users", []) or []
            items = [
                {
                    "stage": "iLO",
                    "label": "Controller login address",
                    "before": cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or "Not set",
                    "after": cfg["ilo"].get("target_ip") or cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or "Not set",
                },
                {
                    "stage": "iLO",
                    "label": "Gateway",
                    "before": "Current live gateway",
                    "after": cfg["ilo"].get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "Not set",
                },
                {
                    "stage": "iLO",
                    "label": "Server name",
                    "before": "Current live hostname",
                    "after": cfg["ilo"].get("hostname") or "Unchanged",
                },
                {
                    "stage": "iLO",
                    "label": "DNS servers",
                    "before": "Current live DNS",
                    "after": dns_values,
                },
            ]
            if shared_snmp.get("v3_username"):
                items.extend(
                    [
                        {
                            "stage": "iLO",
                            "label": "SNMPv3 username",
                            "before": "Current live SNMP user",
                            "after": shared_snmp.get("v3_username") or "Not set",
                        },
                        {
                            "stage": "iLO",
                            "label": "SNMPv3 protocols",
                            "before": "Current live SNMP protocols",
                            "after": f"{shared_snmp.get('v3_auth_protocol', 'SHA')} / {shared_snmp.get('v3_priv_protocol', 'AES')}",
                        },
                    ]
                )
            if extra_users:
                items.append(
                    {
                        "stage": "iLO",
                        "label": "Additional local iLO users",
                        "before": "Current live user set",
                        "after": str(len(extra_users)),
                    }
                )
            return items
        if key == "storage":
            review = build_storage_run_review()
            return [
                {
                    "stage": "Storage",
                    "label": "Apply mode",
                    "before": "Current storage layout",
                    "after": review.get("apply_mode_label") or "Not set",
                },
                {
                    "stage": "Storage",
                    "label": "Controller",
                    "before": "Detected controller",
                    "after": review.get("controller") or "Not set",
                },
                {
                    "stage": "Storage",
                    "label": "OS RAID 1 bays",
                    "before": "Current OS volume layout",
                    "after": review.get("os_bays") or "Not selected",
                },
                {
                    "stage": "Storage",
                    "label": "Data RAID bays",
                    "before": "Current data volume layout",
                    "after": review.get("data_bays") or "Not selected",
                },
                {
                    "stage": "Storage",
                    "label": "Hot spare bay",
                    "before": "Current spare layout",
                    "after": review.get("spare_bay") or "Not reserved",
                },
            ]
        if key == "esxi":
            items = [
                {
                    "stage": "ESXi",
                    "label": "Hostname",
                    "before": "Current installed ESXi hostname",
                    "after": esxi_install_review.get("hostname") or "Not set",
                },
                {
                    "stage": "ESXi",
                    "label": "Management IP",
                    "before": "Current installed ESXi IP",
                    "after": esxi_install_review.get("management_ip") or "Not set",
                },
                {
                    "stage": "ESXi",
                    "label": "Subnet mask",
                    "before": "Current installed ESXi subnet",
                    "after": esxi_install_review.get("subnet_mask") or "Not set",
                },
                {
                    "stage": "ESXi",
                    "label": "Gateway",
                    "before": "Current installed ESXi gateway",
                    "after": esxi_install_review.get("gateway") or "Not set",
                },
                {
                    "stage": "ESXi",
                    "label": "DNS servers",
                    "before": "Current installed ESXi DNS",
                    "after": ", ".join(esxi_install_review.get("dns_servers") or []) or "Not set",
                },
                {
                    "stage": "ESXi",
                    "label": "Root password",
                    "before": "Current installed ESXi root password",
                    "after": "Saved" if esxi_install_review.get("root_password_saved") else "Missing",
                },
            ]
            if esxi_install_review.get("vlan_id"):
                items.append(
                    {
                        "stage": "ESXi",
                        "label": "VLAN ID",
                        "before": "Current installed VLAN",
                        "after": str(esxi_install_review.get("vlan_id")),
                    }
                )
            if esxi_install_review.get("ntp_server"):
                items.append(
                    {
                        "stage": "ESXi",
                        "label": "NTP server",
                        "before": "Current installed NTP",
                        "after": esxi_install_review.get("ntp_server") or "Not set",
                    }
                )
            items.extend(
                [
                    {
                        "stage": "ESXi",
                        "label": "SSH",
                        "before": "Current installed SSH state",
                        "after": "Enabled" if esxi_install_review.get("enable_ssh") else "Disabled",
                    },
                    {
                        "stage": "ESXi",
                        "label": "IPv6",
                        "before": "Current installed IPv6 state",
                        "after": "Disabled" if esxi_install_review.get("disable_ipv6") else "Enabled",
                    },
                ]
            )
            return items
        if key == "windows":
            return [
                {
                    "stage": "Windows",
                    "label": "VM name",
                    "before": "Current Windows target",
                    "after": cfg["windows"].get("vm_name") or "Not set",
                },
                {
                    "stage": "Windows",
                    "label": "Target IP",
                    "before": "Current Windows IP",
                    "after": cfg["windows"].get("ip_address") or cfg.get("ip_plan", {}).get("windows") or "Not set",
                },
            ]
        if key == "qnap":
            return [
                {
                    "stage": "QNAP",
                    "label": "Hostname",
                    "before": "Current QNAP hostname",
                    "after": cfg["qnap"].get("hostname") or "Not set",
                },
                {
                    "stage": "QNAP",
                    "label": "Target IP",
                    "before": "Current QNAP IP",
                    "after": cfg["qnap"].get("ip") or cfg.get("ip_plan", {}).get("qnap") or "Not set",
                },
            ]
        return []

    def stage_entry(key: str, included: bool) -> dict:
        meta = components[key]
        summary = meta["summary"]
        if key == "storage":
            if not storage_review.get("include_in_ilo_run"):
                summary = "Storage will be skipped in this run."
            elif storage_validation_error:
                summary = f"Storage setup is blocked until it is reviewed again: {storage_validation_error}"
            elif storage_review.get("approved") and not storage_review.get("stale"):
                summary = "Storage will be applied during the real run using the approved layout."
                if storage_review.get("approval", {}).get("reboot_expected"):
                    summary += " Restart expected."
        state_key, status_label, status_tone, blocked_reason = stage_state(key, included)
        checks = stage_checks(key, included)
        blockers = [item for item in checks if not item.get("ok")]
        ready_items = [item for item in checks if item.get("ok")]
        corrective_action = ""
        fix_href = stage_fix_href(key)
        fix_label = stage_fix_label(key)
        why_blocked = ""
        if blockers:
            primary = blockers[0]
            corrective_action = primary.get("fix") or "Open the workspace and fix the missing setup."
            why_blocked = primary.get("why") or ""
            fix_href = primary.get("href") or fix_href
        elif state_key == "needs_review":
            corrective_action = "Review the latest saved state and approve it again."
        elif state_key == "not_included":
            corrective_action = "Turn this stage on if you want it in the run."
        return {
            "key": key,
            "name": meta["name"],
            "target": meta["target"],
            "included": included,
            "summary": summary,
            "review_href": meta["review_href"],
            "status_label": status_label,
            "status_tone": status_tone,
            "state_used": stage_state_used(key),
            "dependencies": stage_dependencies(key),
            "restart_expected": stage_restart_expected(key),
            "blocked_reason": blocked_reason,
            "why_blocked": why_blocked,
            "corrective_action": corrective_action,
            "fix_href": fix_href,
            "fix_label": fix_label,
            "preflight_checks": checks,
            "preflight_ready": [item.get("label") for item in ready_items],
            "preflight_blockers": blockers,
            "settings": stage_settings(key),
            "detail_rows": stage_detail_rows(key),
        }

    included_stages = []
    if scope == "included":
        included = cfg.get("included", {})
        lines.append("Will act on all included components in this kit:")
        for key in ["ilo", "esxi", "windows", "qnap", "iosafe", "cisco_switch", "netapp"]:
            if included.get(key):
                lines.append(f"- {components[key]['name']} -> {components[key]['target']}")
                included_stages.append(stage_entry(key, True))
        storage_included = bool(included.get("storage"))
        if storage_included:
            lines.append(f"- Storage plan -> {'approved exact artifact' if storage_execution.get('included') else 'not ready'}")
            included_stages.append(stage_entry("storage", True))
    elif scope.startswith("multi__"):
        lines.append("Will act on the selected stages:")
        for key in selected_scope_keys:
            lines.append(f"- {components[key]['name']} -> {components[key]['target']}")
            included_stages.append(stage_entry(key, True))
        if "ilo" in selected_scope_keys and storage_review.get("include_in_ilo_run"):
            lines.append(f"- Storage plan -> {'approved exact artifact' if storage_execution.get('included') else 'not ready'}")
            included_stages.append(stage_entry("storage", True))
    else:
        lines.append(f"Will act only on stage: {scope}")
        if scope == "ilo":
            lines.append(f"- Storage included in iLO run: {'Yes' if storage_review.get('include_in_ilo_run') else 'No'}")
            included_stages.append(stage_entry("ilo", True))
            if storage_review.get("include_in_ilo_run"):
                included_stages.append(stage_entry("storage", True))
        else:
            included_stages.append(stage_entry(scope, True))
    lines.append("")
    if scope in {"ilo", "included"}:
        lines.append("Pre-run review:")
        lines.append(
            f"- Sign in to iLO at {cfg['ilo'].get('current_ip') or cfg['ilo'].get('host', '') or '(not set)'}"
        )
        lines.append(
            f"- iLO network changes -> final IP {cfg['ilo'].get('target_ip') or '(unchanged)'} | "
            f"gateway {cfg['ilo'].get('gateway') or '(unchanged)'} | hostname {cfg['ilo'].get('hostname') or '(unchanged)'}"
        )
        if storage_review.get("include_in_ilo_run"):
            approval = storage_review.get("approval", {}) or {}
            lines.append("- Storage included -> Yes")
            lines.append(f"- Approved storage snapshot -> {approval.get('discovery_raw_path') or '(missing)'}")
            lines.append(f"- Approved storage plan -> {approval.get('plan_path') or '(missing)'}")
            lines.append(f"- Restart expected after storage -> {'Yes' if approval.get('reboot_expected') else 'No'}")
            if approval.get("plan_summary"):
                plan_summary = approval.get("plan_summary", {})
                arrays = list(plan_summary.get("arrays") or [])
                lines.append(
                    f"- Storage layout -> controller set {plan_summary.get('controller') or '(unknown)'}"
                )
                for item in arrays:
                    lines.append(
                        f"  {str(item.get('role') or '').upper()} {raid_label(str(item.get('raid_level') or ''))} -> "
                        f"{item.get('controller') or item.get('controller_path') or '(unknown)'} | "
                        f"bays {item.get('bays') or '(none)'} | "
                        f"serials {', '.join(item.get('selected_drive_serials') or []) or '(none)'}"
                    )
        else:
            lines.append("- Storage included -> No")
        if storage_validation_error:
            lines.append(f"- Storage readiness -> blocked: {storage_validation_error}")
    lines.append("")
    lines.append("WARNING: This may reboot, reconfigure, overwrite, or otherwise make destructive changes.")
    readiness_matrix = build_run_center_readiness_matrix(cfg, scope)
    will_run = [stage["name"] for stage in included_stages if stage["included"]]
    will_not_run: list[str] = []
    if scope == "included":
        included_cfg = cfg.get("included", {})
        for key in ["ilo", "esxi", "windows", "qnap", "iosafe", "cisco_switch", "netapp"]:
            if not included_cfg.get(key):
                will_not_run.append(components[key]["name"])
        if not included_cfg.get("storage"):
            will_not_run.append("Storage / RAID")
    elif scope == "ilo" and not storage_review.get("include_in_ilo_run"):
        will_not_run.append("Storage / RAID")
    if storage_validation_error and "Storage / RAID" not in will_not_run and any(stage["key"] == "storage" for stage in included_stages):
        will_not_run.append("Storage / RAID until it is reviewed again")
    if scope == "included":
        run_type_label = "Whole run"
    elif scope.startswith("multi__"):
        run_type_label = f"Selected stages: {', '.join([components[key]['name'] for key in selected_scope_keys])}"
    else:
        run_type_label = f"Single stage: {components.get(scope, {'name': scope}).get('name', scope)}"

    summary_items = [
        {"label": "Execution mode", "value": execution_mode["label"]},
        {"label": "Run type", "value": run_type_label},
        {"label": "Selected kit", "value": cfg.get("site", {}).get("name", "") or "Unknown"},
        {"label": "Storage in run", "value": "Yes" if (scope == "included" and cfg.get("included", {}).get("storage")) or (scope == "ilo" and storage_review.get("include_in_ilo_run")) else "No"},
        {"label": "Restart expected", "value": "Yes" if storage_review.get("approval", {}).get("reboot_expected") and any(stage["key"] == "storage" and stage["included"] for stage in included_stages) else "No"},
    ]
    warning_points = [
        "Review the selected targets, sign-in details, and included stages before starting.",
        execution_mode["summary"] if execution_mode["key"] == "preview" else "This run can restart equipment and make destructive changes.",
    ]
    if storage_validation_error:
        warning_points.append(f"Storage is blocked right now: {storage_validation_error}")
    elif any(stage["key"] == "storage" and stage["included"] for stage in included_stages):
        warning_points.append("Approved storage will be applied during the real run using the exact approved artifact.")
    validation_checks = build_execution_validation_overview(cfg, scope, included_stages)
    recoverability = build_recoverability_notes(cfg, scope, included_stages)
    ready_checks = sum(1 for item in validation_checks if item.get("ok"))
    total_checks = len(validation_checks)
    blocked_checks = [item for item in validation_checks if not item.get("ok")]
    review_checks = [item for item in readiness_matrix if item.get("label") == "Needs review"]
    confidence_score = int(round(((ready_checks + (0.5 * len(review_checks))) / max(total_checks or len(readiness_matrix), 1)) * 100))
    if blocked_checks:
        confidence_label = "Needs attention"
        confidence_tone = "pending"
        confidence_summary = "One or more required checks are still blocking a safe real run."
    elif review_checks:
        confidence_label = "Review again"
        confidence_tone = "progress"
        confidence_summary = "The run is close, but at least one stage still needs a fresh review."
    else:
        confidence_label = "Ready for review"
        confidence_tone = "ready"
        confidence_summary = "The selected stages have the required saved values and look ready for a final review."
    change_summary = [stage_change_summary(stage["key"]) for stage in included_stages if stage.get("included")]
    change_items = []
    for stage in included_stages:
        if not stage.get("included"):
            continue
        change_items.extend(stage_change_items(stage["key"]))
    final_summary = {
        "will_run": will_run or ["Nothing yet"],
        "will_not_run": will_not_run or ["Everything selected is in scope"],
        "restart_impact": "A restart is expected during this run." if any(stage.get("restart_expected") and stage.get("included") for stage in included_stages) else "No restart is expected from the selected stages.",
        "plans_in_use": [stage["state_used"] for stage in included_stages if stage.get("included")],
    }
    summary_artifacts = build_run_summary_artifacts(cfg, {"stages": included_stages}, scope)
    launch_options = build_execution_launch_options(cfg, scope)
    return {
        "scope": scope,
        "selected_scopes_for_form": ["included"] if scope == "included" else selected_scope_keys or ([scope] if scope else ["included"]),
        "execution_mode": execution_mode,
        "esxi_install_review": esxi_install_review,
        "storage_run_review": build_storage_run_review() if any(stage["key"] == "storage" and stage["included"] for stage in included_stages) or scope == "storage" else {},
        "execution_mode_rows": [
            {"label": "Mode", "value": execution_mode["badge"]},
            {"label": "What this does", "value": execution_mode["what_this_does"]},
            {"label": "Real changes made", "value": execution_mode["real_changes"]},
            {"label": "Next step", "value": execution_mode["next_step"]},
        ],
        "summary_items": summary_items,
        "stages": included_stages,
        "readiness_matrix": readiness_matrix,
        "final_summary": final_summary,
        "summary_artifacts": summary_artifacts,
        "launch_options": launch_options,
        "warning_title": "Review before you start",
        "warning_points": warning_points,
        "validation_checks": validation_checks,
        "confidence": {
            "score": confidence_score,
            "label": confidence_label,
            "tone": confidence_tone,
            "summary": confidence_summary,
            "ready_checks": ready_checks,
            "total_checks": total_checks,
            "blocked_checks": blocked_checks,
            "review_checks": review_checks,
        },
        "change_summary": change_summary,
        "change_items": change_items,
        "recoverability": recoverability,
        "restart_expected": any("Restart expected" in item.get("label", "") and item.get("value") == "Yes" for item in summary_items),
        "detail_text": "\n".join(lines),
    }


def get_steps_for_scope(cfg: dict, scope: str):
    registry = build_stage_registry(cfg)
    registered = {stage.name: stage for stage in registry.all()}
    if scope == "ilo":
        title = registered.get("ilo").title if registered.get("ilo") else "iLO"
        return [
            f"Preview {title} target and sign-in",
            f"Preview {title} network changes",
            f"Preview {title} policy and account changes",
            f"Preview complete - ready for real {title} execution",
        ]
    if scope == "esxi":
        title = registered.get("esxi").title if registered.get("esxi") else "ESXi"
        return [
            f"Preview {title} configuration",
            "Preview generated install inputs",
            "Preview ISO patch inputs",
            "Preview install target checks",
            f"Preview complete - ready for real {title} execution",
        ]
    if scope == "windows":
        return [
            "Preview Windows configuration",
            "Preview network plan",
            "Preview unattended settings",
            "Preview VM and build target checks",
            "Preview complete - ready for real Windows execution",
        ]
    if scope == "qnap":
        return [
            "Preview QNAP configuration",
            "Preview target IP",
            "Preview storage settings",
            "Preview credentials",
            "Preview complete - ready for real QNAP execution",
        ]
    if scope == "iosafe":
        return [
            "Preview ioSafe configuration",
            "Preview target IP",
            "Preview storage settings",
            "Preview credentials",
            "Preview complete - ready for real ioSafe execution",
        ]
    if scope == "cisco_switch":
        return [
            "Preview switch configuration",
            "Preview management IP",
            "Preview switch template",
            "Preview credentials",
            "Preview complete - ready for real switch execution",
        ]
    if scope == "included":
        steps = ["Preview included kit scope"]
        included = cfg.get("included", {})
        if included.get("storage") and registered.get("storage"):
            steps.append(f"Preview approved {registered['storage'].title} plan")
        if included.get("ilo") and registered.get("ilo"):
            steps.append(f"Preview {registered['ilo'].title} actions")
        if included.get("esxi") and registered.get("esxi"):
            steps.append(f"Preview {registered['esxi'].title} actions")
        if included.get("windows"):
            steps.append("Preview Windows actions")
        if included.get("qnap"):
            steps.append("Preview QNAP actions")
        if included.get("iosafe"):
            steps.append("Preview ioSafe actions")
        if included.get("cisco_switch"):
            steps.append("Preview Cisco switch actions")
        if included.get("netapp"):
            steps.append("Run NetApp safe-apply actions")
        steps.append("Preview complete - ready for real included-kit execution")
        return steps
    if scope.startswith("multi__"):
        steps = ["Preview selected stages"]
        for key in run_center_scope_keys(scope, cfg):
            label = (
                f"Preview {registered[key].title} actions"
                if key in registered
                else {
                    "windows": "Preview Windows actions",
                    "qnap": "Preview QNAP actions",
                    "iosafe": "Preview ioSafe actions",
                    "cisco_switch": "Preview Cisco switch actions",
                    "netapp": "Run NetApp safe-apply actions",
                }.get(key, f"Preview {key} actions")
            )
            steps.append(label)
        steps.append("Preview complete - ready for the selected run")
        return steps
    return ["Preview scope is not defined"]


def validate_execution_scope(cfg: dict, scope: str) -> None:
    upgrade_blockers = upgrade_gate_blockers(cfg)
    if upgrade_blockers:
        primary = upgrade_blockers[0]
        raise ValueError(primary.get("recommended_action") or f"{primary.get('label', 'Upgrade gate')} is blocking this run. Review Upgrade Helper first.")
    def validate_cisco_run_ready() -> None:
        cisco_cfg = cfg.get("cisco_switch", {}) or {}
        approval = dict(cisco_cfg.get("config_approval") or {})
        if approval.get("state") != "approved":
            raise ValueError("Cisco setup needs an approved config plan before Run Center can run it for real.")
        if not bool(dict(cisco_cfg.get("last_ssh_test") or {}).get("ok")):
            raise ValueError("Cisco SSH must test successfully before Run Center can apply switch config.")
        if not (cisco_cfg.get("management_ip") or cisco_cfg.get("ip") or cfg.get("ip_plan", {}).get("switch") or "").strip():
            raise ValueError("Cisco management IP is not set.")

    if scope == "esxi":
        esxi_values = get_esxi_effective_values(cfg)
        if esxi_values["missing_fields"]:
            raise ValueError(f"ESXi setup is missing: {', '.join(esxi_values['missing_fields'])}.")
        if esxi_values["validation_errors"]:
            raise ValueError(f"ESXi setup has invalid saved values: {'; '.join(esxi_values['validation_errors'])}.")
        if not (cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host") or "").strip():
            raise ValueError("ESXi setup also needs the current iLO address saved first.")
        if cfg.get("included", {}).get("storage"):
            storage_review = build_storage_review_context(cfg)
            if storage_review.get("stale"):
                raise ValueError("The approved storage plan is stale and must be reviewed again before an ESXi run.")
            if not storage_review.get("approved"):
                raise ValueError("ESXi depends on storage for this kit, but no approved storage plan is saved.")
        return
    if scope.startswith("multi__"):
        selected = run_center_scope_keys(scope, cfg)
        if "ilo" in selected:
            validate_storage_ready_for_ilo_run(cfg)
        if "storage" in selected:
            storage_review = build_storage_review_context(cfg)
            if storage_review.get("stale"):
                raise ValueError("The approved storage plan is stale and must be reviewed again before a storage run.")
            if not storage_review.get("approved"):
                raise ValueError("No approved storage plan is saved for this kit.")
        if "esxi" in selected:
            esxi_values = get_esxi_effective_values(cfg)
            if esxi_values["missing_fields"]:
                raise ValueError(f"ESXi setup is missing: {', '.join(esxi_values['missing_fields'])}.")
            if esxi_values["validation_errors"]:
                raise ValueError(f"ESXi setup has invalid saved values: {'; '.join(esxi_values['validation_errors'])}.")
        if "cisco_switch" in selected:
            validate_cisco_run_ready()
        return
    if scope == "cisco_switch":
        validate_cisco_run_ready()
        return
    if scope == "storage":
        storage_review = build_storage_review_context(cfg)
        if storage_review.get("stale"):
            raise ValueError("The approved storage plan is stale and must be reviewed again before a storage run.")
        if not storage_review.get("approved"):
            raise ValueError("No approved storage plan is saved for this kit.")
        return
    if scope not in {"ilo", "included"}:
        return
    included = cfg.get("included", {})
    if scope == "included" and included.get("cisco_switch"):
        validate_cisco_run_ready()
    if scope == "included" and not included.get("storage"):
        return
    if scope == "ilo" or included.get("storage"):
        validate_storage_ready_for_ilo_run(cfg)


def update_job(
    kit_name: str,
    job: dict,
    status: str,
    current_stage: str,
    completed: int,
    total: int,
    log_line: str,
    progress_percent: int | None = None,
):
    runner = JobStepRunner(
        kit_name=kit_name,
        job=job,
        save_job=save_job,
        ensure_run_bundle=ensure_run_bundle_for_job,
    )
    runner.step(
        status=status,
        stage=current_stage,
        completed=completed,
        total=total,
        log=log_line,
        progress_percent=progress_percent,
    )


def initialize_background_job(kit_name: str, scope: str):
    mode = execution_mode_for_scope(scope)
    save_job(
        kit_name,
        {
            "status": "Real run queued" if mode["key"] == "real" else "Preview queued",
            "execution_mode": mode["key"],
            "execution_mode_label": mode["label"],
            "scope": scope,
            "current_stage": "Queued",
            "progress_percent": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "logs": [f"[QUEUED] {mode['label']} requested for scope: {scope}"],
            "root_scope": scope,
            "stage_statuses": initialize_stage_statuses(scope),
        },
    )


def carry_forward_job_bundle_metadata(kit_name: str, job: dict[str, Any]) -> dict[str, Any]:
    existing = load_job(kit_name)
    for key in (
        "run_id",
        "run_bundle_dir",
        "run_live_log_path",
        "run_trace_path",
        "run_summary_path",
        "run_config_snapshot_path",
        "started_at",
    ):
        if existing.get(key) and not job.get(key):
            job[key] = existing.get(key)
    if existing.get("trace_events") and not job.get("trace_events"):
        job["trace_events"] = list(existing.get("trace_events") or [])
    if existing.get("logs") and not job.get("logs"):
        job["logs"] = list(existing.get("logs") or [])
    if existing.get("root_scope") and not job.get("root_scope"):
        job["root_scope"] = str(existing.get("root_scope") or "")
    merged_stage_statuses = merge_stage_statuses(existing.get("stage_statuses"), job.get("stage_statuses"))
    if merged_stage_statuses:
        job["stage_statuses"] = merged_stage_statuses
    return job


def append_job_history_snapshot(cfg: dict, scope: str):
    kit_name = cfg["site"]["name"]
    finished_job = load_job(kit_name)
    logs = finished_job.get("logs", [])
    issue_lines = [
        line for line in logs
        if "[FAILED]" in line or "[SKIP" in line or "[ERROR]" in line or "[WARN]" in line
    ]
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    summary_scope = "included" if str(finished_job.get("scope", scope)) == "included" else scope
    run_summary_path = write_run_summary_artifact(cfg, summary_scope, timestamp=timestamp)
    append_history_entry(
        kit_name,
        {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scope": finished_job.get("scope", scope),
            "status": finished_job.get("status", "Unknown"),
            "current_stage": finished_job.get("current_stage", ""),
            "progress_percent": finished_job.get("progress_percent", 0),
            "completed_steps": finished_job.get("completed_steps", 0),
            "total_steps": finished_job.get("total_steps", 0),
            "issues": issue_lines,
            "logs": logs,
            "run_bundle_dir": str(finished_job.get("run_bundle_dir") or ""),
            "run_live_log_path": str(finished_job.get("run_live_log_path") or ""),
            "run_trace_path": str(finished_job.get("run_trace_path") or ""),
            "run_config_snapshot_path": str(finished_job.get("run_config_snapshot_path") or ""),
            "config_summary": {
                **build_history_config_summary(cfg, scope),
                **(
                    {
                        "storage_run_directory": str(finished_job.get("storage_run_directory") or ""),
                        "storage_apply_path": str(finished_job.get("apply_path") or ""),
                        "reboot_required": bool(finished_job.get("reboot_required")),
                        "storage_server_reboot_required": bool(finished_job.get("storage_server_reboot_required")),
                        "storage_server_reboot_status": str(finished_job.get("storage_server_reboot_status") or ""),
                        "ilo_reset_required": bool(finished_job.get("ilo_reset_required")),
                        "ilo_reset_status": str(finished_job.get("ilo_reset_status") or ""),
                        "dns_apply_status": str(finished_job.get("dns_apply_status") or ""),
                        "dns_requested_values": finished_job.get("dns_requested_values") or [],
                        "dns_before_values": finished_job.get("dns_before_values") or [],
                        "dns_applied_values": finished_job.get("dns_applied_values") or [],
                        "dns_applied_keys": finished_job.get("dns_applied_keys") or [],
                        "dns_mismatches": finished_job.get("dns_mismatches") or [],
                        "dns_reset_recommended": bool(finished_job.get("dns_reset_recommended")),
                        "snmp_apply_status": str(finished_job.get("snmp_apply_status") or ""),
                        "snmp_applied_keys": finished_job.get("snmp_applied_keys") or [],
                        "snmp_verified_checks": finished_job.get("snmp_verified_checks") or [],
                        "snmp_mismatches": finished_job.get("snmp_mismatches") or [],
                        "snmp_reset_recommended": bool(finished_job.get("snmp_reset_recommended")),
                        "snmp_username": str(finished_job.get("snmp_username") or ""),
                        "snmp_auth_protocol": str(finished_job.get("snmp_auth_protocol") or ""),
                        "snmp_priv_protocol": str(finished_job.get("snmp_priv_protocol") or ""),
                        "snmp_auth_secret_present": bool(finished_job.get("snmp_auth_secret_present")),
                        "snmp_priv_secret_present": bool(finished_job.get("snmp_priv_secret_present")),
                        "local_account_status": str(finished_job.get("local_account_status") or ""),
                        "local_accounts_requested": finished_job.get("local_accounts_requested") or [],
                        "ilo_stage_finished": bool(finished_job.get("ilo_stage_finished")),
                        "ilo_final_ip_verified": bool(finished_job.get("ilo_final_ip_verified")),
                    }
                    if (
                        finished_job.get("storage_run_directory")
                        or finished_job.get("apply_path")
                        or finished_job.get("dns_apply_status")
                        or finished_job.get("snmp_apply_status")
                        or finished_job.get("local_account_status")
                        or finished_job.get("ilo_reset_status")
                        or finished_job.get("storage_server_reboot_status")
                    )
                    else {}
                ),
            },
            "run_summary_path": str(run_summary_path),
        },
    )


def execute_real_job_in_background(cfg: dict, scope: str):
    kit_name = cfg["site"]["name"]
    selected_tokens = run_center_scope_keys(scope, cfg)
    registry = build_stage_registry(cfg)

    def mark_stage(token: str, state: str) -> None:
        current_job = load_job(kit_name)
        current_job["root_scope"] = str(current_job.get("root_scope") or scope)
        if not current_job.get("stage_statuses"):
            current_job["stage_statuses"] = initialize_stage_statuses(current_job["root_scope"], cfg)
        set_stage_status(current_job, token, state)
        save_job(kit_name, current_job)

    current_job = load_job(kit_name)
    current_job["root_scope"] = str(current_job.get("root_scope") or scope)
    current_job["stage_statuses"] = merge_stage_statuses(
        initialize_stage_statuses(current_job["root_scope"], cfg),
        current_job.get("stage_statuses"),
    )
    save_job(kit_name, current_job)
    try:
        if scope.startswith("multi__"):
            selected = selected_tokens
            if not selected:
                raise RuntimeError("No stages were selected for the real run.")
            if not all(item in {"ilo", "storage", "esxi", "netapp", "cisco_switch"} for item in selected):
                raise RuntimeError("Real selected-stage execution currently supports iLO, storage, ESXi, Cisco, and NetApp only.")
            storage_was_handled_by_ilo = False
            if "ilo" in selected:
                mark_stage("ilo", "running")
                run_ilo_real(cfg)
                finished_job = load_job(kit_name)
                if finished_job.get("status") == "Failed":
                    mark_stage("ilo", "failed")
                    if "storage" in selected:
                        mark_stage("storage", "skipped")
                    if "esxi" in selected:
                        mark_stage("esxi", "skipped")
                    return
                mark_stage("ilo", "completed")
                cfg = load_kit_config(kit_name)
                promote_final_ilo_endpoint(cfg)
                save_kit_config(cfg)
                storage_was_handled_by_ilo = bool((finished_job.get("storage_run_directory") or "") or cfg.get("storage", {}).get("include_in_ilo_run"))
            if "storage" in selected and not storage_was_handled_by_ilo:
                mark_stage("storage", "running")
                cfg = load_kit_config(kit_name)
                promote_final_ilo_endpoint(cfg)
                save_kit_config(cfg)
                storage_execution = validate_storage_ready_for_ilo_run(cfg)
                discovery_raw_path = str(storage_execution.get("discovery_raw_path") or "")
                raid_plan_path = str(storage_execution.get("plan_path") or "")
                if not discovery_raw_path or not raid_plan_path:
                    raise RuntimeError("Approved storage artifacts are missing for the real storage run.")
                _discovery, _discovery_paths, plan, plan_paths = restore_storage_page_state(
                    discovery_raw_path=discovery_raw_path,
                    raid_plan_path=raid_plan_path,
                    expected_host=str(storage_execution.get("approved_host") or ""),
                )
                if not plan_paths:
                    raise RuntimeError("Approved storage plan artifact is missing for the real storage run.")
                apply_mode = storage_apply_mode_for_plan(plan)
                apply_paths = initialize_storage_apply_artifacts(cfg, plan, plan_paths)
                run_storage_apply(cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths)
                workflow_state = load_storage_workflow_state(apply_paths)
                apply_state = (workflow_state.get("apply", {}) if workflow_state else {}) or {}
                if apply_state.get("workflow_state") == "staged_reboot_required" and not apply_state.get("reboot_requested"):
                    mark_stage("storage", "running")
                    start_storage_manual_reboot_watch_background(cfg, discovery_raw_path, raid_plan_path, apply_paths)
                    return
                mark_stage("storage", "completed")
            elif "storage" in selected and storage_was_handled_by_ilo:
                mark_stage("storage", "completed")
                cfg = load_kit_config(kit_name)
                promote_final_ilo_endpoint(cfg)
                save_kit_config(cfg)
            if "esxi" in selected:
                mark_stage("esxi", "running")
                cfg = load_kit_config(kit_name)
                promote_final_ilo_endpoint(cfg)
                save_kit_config(cfg)
                run_esxi_real(cfg, run_stamp=str((cfg.get("_runtime", {}) or {}).get("esxi_run_stamp") or "").strip() or None)
                finished_job = load_job(kit_name)
                if finished_job.get("status") == "Failed":
                    mark_stage("esxi", "failed")
                    return
                mark_stage("esxi", "completed")
            if "netapp" in selected:
                mark_stage("netapp", "running")
                cfg = load_kit_config(kit_name)
                _execute_netapp_stage(cfg, kit_name)
                finished_job = load_job(kit_name)
                if finished_job.get("status") == "Failed":
                    mark_stage("netapp", "failed")
                    return
                mark_stage("netapp", "completed")
            if "cisco_switch" in selected:
                mark_stage("cisco_switch", "running")
                cfg = load_kit_config(kit_name)
                _execute_cisco_stage(cfg, kit_name)
                finished_job = load_job(kit_name)
                if finished_job.get("status") == "Failed":
                    mark_stage("cisco_switch", "failed")
                    return
                mark_stage("cisco_switch", "completed")
            return
        if scope == "cisco_switch":
            mark_stage("cisco_switch", "running")
            cfg = load_kit_config(kit_name)
            _execute_cisco_stage(cfg, kit_name)
            finished_job = load_job(kit_name)
            mark_stage("cisco_switch", "failed" if finished_job.get("status") == "Failed" else "completed")
        elif scope in {"ilo", "storage", "esxi", "windows", "netapp"}:
            stage = registry.get(scope)
            if stage is None:
                raise RuntimeError(f"Stage registry entry is missing for scope: {scope}")
            mark_stage(scope, "running")
            context = {
                "cfg": cfg,
                "executors": {
                    "ilo": lambda _job: run_ilo_real(cfg),
                    "storage": lambda _job: _execute_storage_stage(cfg, kit_name, mark_stage),
                    "esxi": lambda _job: _execute_esxi_stage(cfg, kit_name),
                    "netapp": lambda _job: _execute_netapp_stage(cfg, kit_name),
                    "windows": lambda _job: _execute_windows_stage(cfg, kit_name),
                },
            }
            stage.execute(context, load_job(kit_name))
            finished_job = load_job(kit_name)
            if scope != "storage" or finished_job.get("status") == "Failed" or finished_job.get("current_stage") != "Queued for manual reboot":
                mark_stage(scope, "failed" if finished_job.get("status") == "Failed" else "completed")
        else:
            raise RuntimeError(f"Real execution is not wired for scope: {scope}")
    except Exception as e:
        stage_to_fail = ""
        current_stage = str(load_job(kit_name).get("current_stage") or "").lower()
        if "esxi" in current_stage:
            stage_to_fail = "esxi"
        elif "storage" in current_stage or "reboot" in current_stage:
            stage_to_fail = "storage"
        elif "windows" in current_stage:
            stage_to_fail = "windows"
        elif "netapp" in current_stage or "ontap" in current_stage:
            stage_to_fail = "netapp"
        elif "cisco" in current_stage or "switch" in current_stage:
            stage_to_fail = "cisco_switch"
        elif "ilo" in current_stage:
            stage_to_fail = "ilo"
        if stage_to_fail:
            mark_stage(stage_to_fail, "failed")
        save_job(
            kit_name,
            {
                "status": "Failed",
                "scope": scope,
                "root_scope": scope,
                "stage_statuses": merge_stage_statuses(initialize_stage_statuses(scope, cfg), load_job(kit_name).get("stage_statuses")),
                "current_stage": "Unexpected error",
                "progress_percent": 0,
                "completed_steps": 0,
                "total_steps": 0,
                "logs": [f"[FAILED] Unexpected background execution error: {e}"],
            },
        )
    finally:
        append_job_history_snapshot(cfg, scope)


def _execute_storage_stage(cfg: dict[str, Any], kit_name: str, mark_stage: Callable[[str, str], None]) -> None:
    promote_final_ilo_endpoint(cfg)
    save_kit_config(cfg)
    storage_execution = validate_storage_ready_for_ilo_run(cfg)
    discovery_raw_path = str(storage_execution.get("discovery_raw_path") or "")
    raid_plan_path = str(storage_execution.get("plan_path") or "")
    if not discovery_raw_path or not raid_plan_path:
        raise RuntimeError("Approved storage artifacts are missing for the real storage run.")
    _discovery, _discovery_paths, plan, plan_paths = restore_storage_page_state(
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        expected_host=str(storage_execution.get("approved_host") or ""),
    )
    if not plan_paths:
        raise RuntimeError("Approved storage plan artifact is missing for the real storage run.")
    apply_mode = storage_apply_mode_for_plan(plan)
    apply_paths = initialize_storage_apply_artifacts(cfg, plan, plan_paths)
    run_storage_apply(cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths)
    workflow_state = load_storage_workflow_state(apply_paths)
    apply_state = (workflow_state.get("apply", {}) if workflow_state else {}) or {}
    if apply_state.get("workflow_state") == "staged_reboot_required" and not apply_state.get("reboot_requested"):
        mark_stage("storage", "running")
        start_storage_manual_reboot_watch_background(cfg, discovery_raw_path, raid_plan_path, apply_paths)
        current_job = load_job(kit_name)
        current_job["current_stage"] = "Queued for manual reboot"
        save_job(kit_name, current_job)


def _execute_esxi_stage(cfg: dict[str, Any], kit_name: str) -> None:
    promote_final_ilo_endpoint(cfg)
    save_kit_config(cfg)
    run_esxi_real(cfg, run_stamp=str((cfg.get("_runtime", {}) or {}).get("esxi_run_stamp") or "").strip() or None)


def _execute_windows_stage(cfg: dict[str, Any], kit_name: str) -> None:
    cfg = apply_ip_plan(cfg)
    windows_cfg = cfg.get("windows", {}) or {}
    plan = windows_cfg.get("install_plan", {}) or {}
    image_path = str(windows_cfg.get("source_image_path") or "").strip()
    image_kind = str(windows_cfg.get("source_image_kind") or "").strip().lower()
    warnings = list(plan.get("warnings") or [])

    total = 5
    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Validate Windows image", 1, total, "[RUNNING] Validating uploaded Windows OVA/OVF image.")
    if not image_path or not Path(image_path).exists() or image_kind not in {"ova", "ovf"}:
        raise RuntimeError("Windows safe execution blocked: upload a valid OVA/OVF image first.")

    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Validate Windows plan", 2, total, "[RUNNING] Validating Windows dry-run install plan.")
    if not bool(plan):
        raise RuntimeError("Windows safe execution blocked: run Plan Windows install (dry-run) first.")
    if not bool(plan.get("ready")):
        detail = "; ".join(warnings) if warnings else "planner reported unresolved warnings"
        raise RuntimeError(f"Windows safe execution blocked: install plan is not ready ({detail}).")

    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Record install inputs", 3, total, f"[INFO] VM={plan.get('vm_name') or '(not set)'} image={windows_cfg.get('source_image_name') or image_path}")
    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Simulate deployment", 4, total, "[INFO] Safe mode: no VM deployment actions are executed in this stage yet.")
    job = load_job(kit_name)
    update_job(kit_name, job, "Complete", "Windows safe stage complete", 5, total, "[OK] Windows safe execution completed. No VM changes were made.")


def _execute_cisco_stage(cfg: dict[str, Any], kit_name: str) -> None:
    from app.modules.cisco.service import CiscoModuleService

    cfg = apply_ip_plan(cfg)
    cisco_cfg = cfg.get("cisco_switch", {}) or {}
    approval = dict(cisco_cfg.get("config_approval") or {})
    mode = str(approval.get("mode") or "full").strip() or "full"
    target = str(cisco_cfg.get("management_ip") or cisco_cfg.get("ip") or cfg.get("ip_plan", {}).get("switch") or "").strip()
    total = 4

    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Validate Cisco run approval", 1, total, "[RUNNING] Checking approved Cisco config plan and SSH readiness.")
    if approval.get("state") != "approved":
        raise RuntimeError("Cisco run blocked: approve the Cisco config plan before using Run Center.")
    if not bool(dict(cisco_cfg.get("last_ssh_test") or {}).get("ok")):
        raise RuntimeError("Cisco run blocked: Test SSH must pass before Run Center applies switch config.")
    if not target:
        raise RuntimeError("Cisco run blocked: management IP is not set.")
    if not str(cisco_cfg.get("username") or "").strip() or not str(cisco_cfg.get("password") or ""):
        raise RuntimeError("Cisco run blocked: switch username and password are required.")

    service = CiscoModuleService()
    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Preview Cisco config", 2, total, f"[RUNNING] Rendering Cisco {mode} configuration for {target}.")
    preview = service.preview_config({"cfg": cfg}, mode=mode)
    validation = dict(preview.get("validation") or {})
    cisco_cfg["last_config_preview"] = str(preview.get("config") or "")
    cisco_cfg["last_config_validation"] = validation
    if not preview.get("ok"):
        errors = "; ".join(str(item) for item in validation.get("errors") or []) or "Cisco config validation failed."
        save_kit_config(cfg)
        raise RuntimeError(f"Cisco run blocked: {errors}")

    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Apply Cisco config", 3, total, "[RUNNING] Applying approved Cisco configuration over SSH.")
    result = service.apply_config({"cfg": cfg}, mode=mode)
    cisco_cfg["last_config_preview"] = str(result.get("config") or cisco_cfg.get("last_config_preview") or "")
    cisco_cfg["last_config_validation"] = dict(result.get("validation") or validation)
    cisco_cfg["last_cisco_action"] = {
        "mode": f"run_center_apply_{mode}",
        "ok": bool(result.get("ok")),
        "applied": bool(result.get("applied")),
        "error": str(result.get("error") or ""),
        "completed_at": datetime.now().astimezone().isoformat(),
    }
    save_kit_config(cfg)
    if not result.get("applied"):
        raise RuntimeError(str(result.get("error") or "Cisco Run Center apply did not complete."))

    job = load_job(kit_name)
    update_job(kit_name, job, "Completed", "Cisco config applied", total, total, f"[OK] Cisco {mode} configuration applied to {target}.")


def _execute_netapp_stage(cfg: dict[str, Any], kit_name: str) -> None:
    from app.modules.netapp.service import NetAppModuleService

    cfg = apply_ip_plan(cfg)
    service = NetAppModuleService()
    total = 5
    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Validate NetApp bootstrap", 1, total, "[RUNNING] Checking NetApp bootstrap and saved ONTAP API target.")

    netapp_cfg = cfg.get("netapp", {}) or {}
    if not bool(netapp_cfg.get("bootstrap_complete")):
        raise RuntimeError("NetApp safe execution blocked: mark NetApp bootstrap complete first.")
    host = str(netapp_cfg.get("host") or cfg.get("ip_plan", {}).get("netapp") or "").strip()
    username = str(netapp_cfg.get("username") or "").strip()
    password = str(netapp_cfg.get("password") or "")
    if not host or not username or not password:
        raise RuntimeError("NetApp safe execution blocked: save the ONTAP API host, username, and password first.")

    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Build NetApp plan", 2, total, "[RUNNING] Building the NetApp discovery, validation, and safe-apply plan.")
    payload = service.apply({"cfg": cfg}, {"job_id": f"{kit_name}-netapp-safe-apply", "scope": "netapp", "confirm": True})
    apply_stage = dict(payload.get("apply") or {})
    execution = dict(apply_stage.get("execution") or apply_stage)
    result = str(payload.get("result") or execution.get("result") or "failed").strip().lower()
    logs = list(execution.get("logs") or [])

    job = load_job(kit_name)
    update_job(kit_name, job, "Running", "Apply NetApp safe actions", 3, total, "[RUNNING] Executing supported NetApp API actions.")
    for line in logs:
        job = load_job(kit_name)
        update_job(kit_name, job, "Running", "Apply NetApp safe actions", 3, total, str(line))

    blocked_actions = list(execution.get("blocked_actions") or [])
    if blocked_actions:
        job = load_job(kit_name)
        update_job(
            kit_name,
            job,
            "Running",
            "Review blocked NetApp actions",
            4,
            total,
            "[WARN] Some NetApp actions still require manual review or a future automation handler.",
        )

    if result == "failed":
        reason = str(payload.get("error") or execution.get("reason") or "NetApp safe apply failed.").strip()
        job = load_job(kit_name)
        update_job(kit_name, job, "Failed", "NetApp safe apply failed", total, total, f"[FAILED] {reason}")
        return

    final_message = "[OK] NetApp safe apply completed."
    if result == "no_changes":
        final_message = "[OK] NetApp safe apply did not execute any supported changes."
    if blocked_actions:
        suffix = f" {len(blocked_actions)} action(s) were left blocked/manual."
        if result == "no_changes":
            final_message += suffix
        else:
            final_message += suffix
    job = load_job(kit_name)
    update_job(kit_name, job, "Completed", "NetApp safe apply complete", total, total, final_message)


def resolve_esxi_base_iso_path(cfg: dict) -> Path:
    return esxi_resolve_base_iso_path(cfg, media_base_dir=MEDIA_DIR / "esxi" / "base")


def normalize_esxi_version(value: Any) -> str:
    return esxi_normalize_version(value)


def discover_esxi_base_isos(version: str | None = None) -> list[dict[str, Any]]:
    return esxi_discover_base_isos(MEDIA_DIR / "esxi" / "base", version=version)


def infer_esxi_version_from_iso_path(path: Path) -> str:
    return esxi_infer_version_from_iso_path(path)


def validate_esxi_base_iso(path: Path, version: str) -> None:
    esxi_validate_base_iso(path, version)


def detect_public_base_url_details(target_host: str = "", runtime_public_base_url: str = "") -> dict[str, str]:
    return esxi_detect_public_base_url_details(target_host, runtime_public_base_url=runtime_public_base_url)


def detect_public_base_url(target_host: str = "", runtime_public_base_url: str = "") -> str:
    return esxi_detect_public_base_url(target_host, runtime_public_base_url=runtime_public_base_url)


def build_esxi_iso_url(cfg: dict, output_iso: Path, target_host: str = "") -> str:
    runtime_public_base_url = str((cfg.get("_runtime", {}) or {}).get("public_base_url") or "")
    try:
        public_base_url = detect_public_base_url(target_host, runtime_public_base_url=runtime_public_base_url)
    except TypeError as exc:
        if "runtime_public_base_url" not in str(exc):
            raise
        public_base_url = detect_public_base_url(target_host)
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    output_name = sanitize_kit_name(output_iso.stem)
    return f"{public_base_url}/esxi-built-iso/{quote(kit_name)}/{quote(output_name)}.iso"


def verify_esxi_virtual_media_url(iso_url: str, output_iso: Path, *, timeout_seconds: int = 10) -> dict[str, Any]:
    return esxi_verify_virtual_media_url(iso_url, output_iso, timeout_seconds=timeout_seconds)


def esxi_virtual_media_url_check_summary(check: dict[str, Any]) -> str:
    return esxi_url_check_summary(check)


def probe_tcp_port(host: str, port: int, *, timeout_seconds: float = 0.75) -> dict[str, Any]:
    return esxi_probe_tcp_port(host, port, timeout_seconds=timeout_seconds)


def _url_host_port(url: str) -> tuple[str, str]:
    return esxi_url_host_port(url)


def public_base_url_from_request(request: Request) -> str:
    try:
        parsed = urlparse(str(request.url))
        host = (parsed.hostname or "").strip().lower()
        if not host or host in {"127.0.0.1", "localhost", "testserver"} or host.startswith("127."):
            return ""
        scheme = parsed.scheme or "http"
        netloc = parsed.netloc.split("@")[-1]
        if not netloc:
            return ""
        return f"{scheme}://{netloc}".rstrip("/")
    except Exception:
        return ""


def apply_request_public_base_url(cfg: dict[str, Any], request: Request) -> None:
    public_base_url = public_base_url_from_request(request)
    if not public_base_url:
        return
    runtime = dict(cfg.get("_runtime", {}) or {})
    runtime["public_base_url"] = public_base_url
    cfg["_runtime"] = runtime


def build_esxi_runtime_status(cfg: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return esxi_build_runtime_status(
        cfg,
        review,
        sanitize_kit_name=sanitize_kit_name,
        load_job=load_job,
        probe_tcp_port_fn=lambda host, port: probe_tcp_port(host, port, timeout_seconds=0.75),
        client_factory=lambda host, username, password: ILOClient(
            ILOConfig(host=host, username=username, password=password, timeout=4)
        ),
    )


def get_esxi_effective_values(cfg: dict[str, Any]) -> dict[str, Any]:
    return esxi_get_effective_values(
        cfg,
        validate_esxi_hostname_fn=validate_esxi_hostname,
        build_esxi_password_policy_check_fn=build_esxi_password_policy_check,
        normalize_esxi_version_fn=normalize_esxi_version,
    )


def ensure_esxi_post_config_policy(cfg: dict[str, Any]) -> dict[str, Any]:
    return esxi_ensure_post_config_policy(cfg)


def build_esxi_post_config_preview(cfg: dict[str, Any]) -> dict[str, Any]:
    ensure_esxi_post_config_policy(cfg)
    return esxi_build_post_config_preview(cfg)


def validate_esxi_post_config_preview(preview: dict[str, Any]) -> dict[str, Any]:
    return esxi_validate_post_config_preview(preview)


def build_esxi_post_config_actions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    return esxi_build_post_config_actions(preview)


def execute_esxi_post_config_actions(
    cfg: dict[str, Any],
    *,
    preview: dict[str, Any],
    validation: dict[str, Any],
    run_action_fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return esxi_execute_post_config_actions(
        cfg,
        preview=preview,
        validation=validation,
        run_action_fn=run_action_fn,
    )


def build_esxi_post_config_ssh_run_action(
    cfg: dict[str, Any],
    preview: dict[str, Any],
    *,
    command_runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    return esxi_build_post_config_ssh_run_action(
        cfg,
        preview,
        command_runner=command_runner,
    )


def build_esxi_install_review(cfg: dict, *, run_stamp: str | None = None, include_runtime: bool = False) -> dict[str, Any]:
    return esxi_build_install_review(
        cfg,
        run_stamp=run_stamp,
        include_runtime=include_runtime,
        sanitize_kit_name=sanitize_kit_name,
        resolve_ilo_control_host=resolve_ilo_control_host,
        get_esxi_effective_values_fn=get_esxi_effective_values,
        resolve_esxi_base_iso_path_fn=resolve_esxi_base_iso_path,
        validate_esxi_base_iso_fn=validate_esxi_base_iso,
        detect_public_base_url_details_fn=detect_public_base_url_details,
        build_esxi_iso_url_fn=build_esxi_iso_url,
        build_esxi_install_target_review_fn=build_esxi_install_target_review,
        build_esxi_runtime_status_fn=build_esxi_runtime_status,
        datetime_cls=datetime,
        exports_dir=EXPORTS_DIR,
    )


def esxi_password_policy_valid(password: str) -> bool:
    return esxi_password_valid(
        password,
        build_esxi_password_policy_check_fn=build_esxi_password_policy_check,
    )


def save_esxi_trace(trace_path: Path, payload: dict[str, Any]) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def choose_virtual_media_device(client: ILOClient) -> dict[str, Any]:
    devices = client.get_virtual_media()
    for item in devices:
        media_types = [str(mt).lower() for mt in item.get("MediaTypes", [])]
        has_insert = bool(((item.get("Actions") or {}).get("#VirtualMedia.InsertMedia") or {}).get("target"))
        if has_insert and any(mt in {"cd", "dvd"} for mt in media_types):
            return item
    for item in devices:
        if ((item.get("Actions") or {}).get("#VirtualMedia.InsertMedia") or {}).get("target"):
            return item
    raise ILOError("No writable virtual media device with an InsertMedia action was found.")


def wait_for_power_state(
    client: ILOClient,
    expected_state: str,
    *,
    timeout_seconds: int = 180,
    poll_interval: int = 5,
) -> dict[str, Any]:
    system_path = client.get_system_path() if hasattr(client, "get_system_path") else client.get_systems()[0]
    deadline = time.time() + max(timeout_seconds, 1)
    last_seen = ""
    while time.time() < deadline:
        last_seen = client.get_power_state(system_path=system_path) if hasattr(client, "get_power_state") else str(client.get_system(system_path).get("PowerState") or "")
        if last_seen.lower() == expected_state.lower():
            return client.get_system(system_path)
        time.sleep(max(poll_interval, 1))
    raise ILOError(f"Timed out waiting for server power state {expected_state}. Last observed state: {last_seen or 'unknown'}.")


def read_current_power_state(client: ILOClient) -> tuple[str, str]:
    system_path = client.get_system_path() if hasattr(client, "get_system_path") else client.get_systems()[0]
    if hasattr(client, "get_power_state"):
        return system_path, str(client.get_power_state(system_path=system_path) or "")
    system = client.get_system(system_path)
    return system_path, str(system.get("PowerState") or "")


def wait_for_esxi_management_ready(
    host: str,
    *,
    timeout_seconds: int = 2400,
    poll_interval: int = 15,
    port: int = 443,
    on_poll: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + max(timeout_seconds, 1)
    attempts = 0
    last_error = "No connection attempt was made."
    while time.time() < deadline:
        attempts += 1
        try:
            with socket.create_connection((host, port), timeout=5):
                return {"host": host, "port": port, "attempts": attempts}
        except Exception as e:
            last_error = str(e).splitlines()[0]
        if on_poll:
            on_poll(
                {
                    "attempts": attempts,
                    "host": host,
                    "port": port,
                    "last_error": last_error,
                    "remaining_seconds": max(int(deadline - time.time()), 0),
                }
            )
        time.sleep(max(poll_interval, 1))
    raise ILOError(
        f"ESXi did not answer on configured IP {host}:{port} before timeout. "
        f"Last error: {last_error}"
    )


def collect_esxi_boot_evidence(client: ILOClient, *, system_path: str | None = None) -> dict[str, Any]:
    try:
        if not system_path:
            systems = client.get_systems()
            system_path = systems[0] if systems else None
    except Exception:
        system_path = system_path or None

    system: dict[str, Any] = {}
    if system_path:
        try:
            system = client.get_system(system_path) or {}
        except Exception as exc:
            system = {"@error": str(exc)}

    boot = dict(system.get("Boot") or {}) if isinstance(system, dict) else {}
    boot_progress = dict(system.get("BootProgress") or {}) if isinstance(system, dict) else {}
    post_state = str((((system.get("Oem") or {}).get("Hpe") or {}).get("PostState") or "")) if isinstance(system, dict) else ""

    virtual_media_items: list[dict[str, Any]] = []
    try:
        for item in client.get_virtual_media():
            virtual_media_items.append(
                {
                    "device_path": str(item.get("@odata.id") or ""),
                    "inserted": bool(item.get("Inserted")),
                    "image": str(item.get("Image") or ""),
                    "write_protected": item.get("WriteProtected"),
                    "media_types": list(item.get("MediaTypes") or []),
                }
            )
    except Exception as exc:
        virtual_media_items.append({"@error": str(exc)})

    mounted = next((item for item in virtual_media_items if item.get("inserted")), {}) if virtual_media_items else {}

    return {
        "system_path": str(system_path or ""),
        "power_state": str(system.get("PowerState") or ""),
        "boot_override_enabled": str(boot.get("BootSourceOverrideEnabled") or ""),
        "boot_override_target": str(boot.get("BootSourceOverrideTarget") or ""),
        "boot_progress_state": str(boot_progress.get("LastState") or ""),
        "post_state": post_state,
        "mounted_virtual_media": dict(mounted or {}),
        "virtual_media": virtual_media_items,
    }


def run_esxi_real(cfg: dict, run_stamp: str | None = None):
    kit_name = cfg["site"]["name"]
    existing_job = load_job(kit_name)
    inherited_root_scope = str(existing_job.get("root_scope") or "esxi")
    ilo_cfg = cfg.get("ilo", {}) or {}
    esxi_cfg = cfg.get("esxi", {}) or {}
    login_ip = str(
        ilo_cfg.get("target_ip")
        or (cfg.get("ip_plan") or {}).get("ilo")
        or resolve_ilo_control_host(cfg)
        or ""
    ).strip()
    username = str(ilo_cfg.get("username") or "").strip()
    password = ilo_cfg.get("password", "")
    total = 13
    job = {
        "status": "Running",
        "execution_mode": "real",
        "execution_mode_label": "Real execution",
        "scope": "esxi",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total,
        "logs": [],
        "esxi_iso_path": "",
        "esxi_iso_url": "",
        "esxi_trace_path": "",
        "esxi_base_iso_path": "",
        "esxi_builder_summary_path": "",
        "esxi_builder_generation": {},
        "esxi_ks_cfg": {},
        "esxi_install_target": {},
        "esxi_install_values": {},
        "esxi_virtual_media": {},
        "esxi_boot_override": {},
        "esxi_boot_evidence": {},
        "esxi_boot_evidence_samples": [],
        "esxi_installer_boot_observed": False,
        "esxi_installer_reboot_detected": False,
        "esxi_post_install_boot_guard": {},
        "esxi_power_transitions": {},
        "esxi_management_network": {},
        "root_scope": inherited_root_scope,
        "stage_statuses": merge_stage_statuses(
            initialize_stage_statuses(inherited_root_scope, cfg),
            existing_job.get("stage_statuses"),
        ),
    }
    job = carry_forward_job_bundle_metadata(kit_name, job)
    save_job(kit_name, job)

    if not login_ip or not username or not password:
        update_job(kit_name, job, "Failed", "Validation failed", 0, total, "[FAILED] Missing iLO host, username, or password.")
        return

    esxi_values = get_esxi_effective_values(cfg)
    management_ip = str(esxi_values["management_ip"])
    subnet_mask = str(esxi_values["subnet_mask"])
    gateway = str(esxi_values["gateway"])
    dns_servers = list(esxi_values["dns_servers"])
    hostname = str(esxi_values["hostname"])
    root_password = str(esxi_values["root_password"])

    if esxi_values["missing_fields"]:
        update_job(
            kit_name,
            job,
            "Failed",
            "Validation failed",
            0,
            total,
            f"[FAILED] Missing ESXi setup values: {', '.join(esxi_values['missing_fields'])}.",
        )
        return
    if esxi_values["validation_errors"]:
        update_job(
            kit_name,
            job,
            "Failed",
            "Validation failed",
            0,
            total,
            f"[FAILED] Invalid ESXi saved values: {'; '.join(esxi_values['validation_errors'])}.",
        )
        return
    job["esxi_expected_ip"] = management_ip
    save_job(kit_name, job)

    try:
        esxi_review = build_esxi_install_review(cfg, run_stamp=run_stamp)
        trace_path = Path(esxi_review["output_iso_path"]).parent / "esxi-run-trace.yml"
        trace_payload: dict[str, Any] = {
            "workflow": "esxi",
            "kit_name": kit_name,
            "source_of_truth": esxi_review.get("source_label"),
            "manual_defaults": esxi_review.get("manual_defaults_label"),
            "install_values": {
                "esxi_version": esxi_review.get("version"),
                "hostname": esxi_review.get("hostname"),
                "management_ip": esxi_review.get("management_ip"),
                "subnet_mask": esxi_review.get("subnet_mask"),
                "gateway": esxi_review.get("gateway"),
                "dns_servers": esxi_review.get("dns_servers"),
                "root_password_saved": bool(root_password),
                "root_password_policy_valid": esxi_password_policy_valid(root_password),
                "vlan_id": esxi_review.get("vlan_id"),
                "ntp_server": esxi_review.get("ntp_server"),
                "enable_ssh": bool(esxi_review.get("enable_ssh")),
                "disable_ipv6": bool(esxi_review.get("disable_ipv6")),
                "debug_no_reboot": bool(esxi_review.get("debug_no_reboot")),
                "install_target": dict(esxi_review.get("install_target") or {}),
            },
            "artifacts": {
                "selected_esxi_version": esxi_review.get("version"),
                "base_iso_path": esxi_review.get("base_iso_path"),
                "output_iso_path": esxi_review.get("output_iso_path"),
                "virtual_media_url": esxi_review.get("virtual_media_url"),
            },
            "steps": [],
        }
        job["esxi_install_values"] = dict(trace_payload["install_values"])
        job["esxi_base_iso_path"] = str(esxi_review.get("base_iso_path") or "")
        job["esxi_trace_path"] = str(trace_path)
        save_job(kit_name, job)
        save_esxi_trace(trace_path, trace_payload)
        base_iso_path = Path(esxi_review["base_iso_path"])
        spec = EsxiBuildSpec(
            kit_name=sanitize_kit_name(kit_name),
            base_iso_path=base_iso_path,
            output_name=Path(esxi_review["output_iso_path"]).stem,
            esxi_version=str(esxi_review.get("version") or "7"),
            hostname=esxi_review["hostname"],
            management_ip=esxi_review["management_ip"],
            subnet_mask=esxi_review["subnet_mask"],
            gateway=esxi_review["gateway"],
            dns_servers=esxi_review["dns_servers"],
            root_password=root_password,
            vlan_id=esxi_review["vlan_id"],
            ntp_server=esxi_review["ntp_server"],
            enable_ssh=bool(esxi_review["enable_ssh"]),
            disable_ipv6=bool(esxi_review["disable_ipv6"]),
            debug_no_reboot=bool(esxi_review.get("debug_no_reboot")),
        )

        def build_esxi_ilo_client() -> ILOClient:
            return ILOClient(ILOConfig(host=login_ip, username=username, password=password, verify_tls=False, timeout=15))

        def is_ilo_session_expired_error(exc: Exception) -> bool:
            text = str(exc)
            return "NoValidSession" in text or ("HTTP 401" in text and "redfish" in text.lower())

        client: ILOClient | None = None

        def run_with_session_refresh(stage_label: str, operation):
            nonlocal client
            if client is None:
                client = build_esxi_ilo_client()
            try:
                return operation(client)
            except Exception as exc:
                if not is_ilo_session_expired_error(exc):
                    raise
                update_job(
                    kit_name,
                    job,
                    "Running",
                    stage_label,
                    job.get("completed_steps", 0),
                    total,
                    "[INFO] iLO session expired during ESXi orchestration. Reconnecting and retrying once.",
                )
                client = build_esxi_ilo_client()
                return operation(client)

        def ensure_power_state_with_fallback(expected_state: str, *, system_path: str, timeout_seconds: int, poll_interval: int):
            def op(c):
                return ensure_client_power_state(
                    c,
                    expected_state,
                    system_path=system_path,
                    timeout_seconds=timeout_seconds,
                    poll_interval=poll_interval,
                )

            return run_with_session_refresh("Power off" if str(expected_state).lower() == "off" else "Power on", op)

        def reconnect_esxi_ilo_after_transport_drop(stage_label: str, message: str) -> None:
            nonlocal client
            update_job(
                kit_name,
                job,
                "Running",
                stage_label,
                job.get("completed_steps", 0),
                total,
                f"[WARN] iLO closed the {stage_label} connection without a response; reconnecting and reading back live state. {message}",
            )
            client = build_esxi_ilo_client()

        def read_virtual_media_item(vm_path: str, *, stage_label: str) -> dict[str, Any]:
            for item in run_with_session_refresh(stage_label, lambda c: c.get_virtual_media()):
                if str(item.get("@odata.id") or "") == str(vm_path):
                    return dict(item)
            return {}

        def poll_virtual_media_state(vm_path: str, predicate, *, stage_label: str, timeout_seconds: int = 20, poll_interval: int = 2) -> dict[str, Any]:
            deadline = time.time() + max(timeout_seconds, 1)
            first_seen: dict[str, Any] = {}
            last_seen: dict[str, Any] = {}
            while time.time() < deadline:
                try:
                    last_seen = read_virtual_media_item(vm_path, stage_label=stage_label)
                except Exception as exc:
                    last_seen = {"@error": str(exc).splitlines()[0], "@odata.id": vm_path}
                if last_seen and not first_seen:
                    first_seen = dict(last_seen)
                try:
                    if last_seen and predicate(last_seen):
                        return {
                            "matched": True,
                            "first_seen": first_seen,
                            "last_seen": last_seen,
                            "timeout_seconds": timeout_seconds,
                            "poll_interval_seconds": poll_interval,
                        }
                except Exception:
                    pass
                time.sleep(max(poll_interval, 1))
            return {
                "matched": False,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval,
            }

        def eject_existing_virtual_media(vm: dict[str, Any]) -> dict[str, Any]:
            vm_path = str(vm.get("@odata.id") or "").strip()
            result = {
                "stage": "eject_virtual_media",
                "device_path": vm_path,
                "initial_inserted": bool(vm.get("Inserted")),
                "initial_image": str(vm.get("Image") or ""),
                "connection_dropped": False,
                "status": "not_needed",
                "readback": {},
            }
            if not vm_path or not vm.get("Inserted"):
                return result
            try:
                run_with_session_refresh("Eject media", lambda c, vm_path=vm_path: c.eject_virtual_media(vm_path))
                result["status"] = "requested"
            except Exception as exc:
                error_text = str(exc).splitlines()[0]
                result["error"] = error_text
                if "iLO.2.25.UnsupportedOperation" in error_text or "UnsupportedOperation" in error_text:
                    result["status"] = "unsupported"
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Eject media",
                        3,
                        total,
                        "[WARN] iLO did not support ejecting the current virtual media. Continuing with best-effort media replacement.",
                    )
                    return result
                if not is_transport_disconnect_error(exc):
                    raise
                result["connection_dropped"] = True
                result["status"] = "transport_disconnect"
                reconnect_esxi_ilo_after_transport_drop("Eject media", error_text)

            if result.get("connection_dropped"):
                poll = poll_virtual_media_state(
                    vm_path,
                    lambda item: not bool(item.get("Inserted")),
                    stage_label="Eject media",
                    timeout_seconds=20,
                    poll_interval=2,
                )
            else:
                readback = read_virtual_media_item(vm_path, stage_label="Eject media")
                poll = {
                    "matched": bool(readback) and not bool(readback.get("Inserted")),
                    "first_seen": readback,
                    "last_seen": readback,
                    "timeout_seconds": 0,
                    "poll_interval_seconds": 0,
                }
            result["readback"] = poll
            if poll.get("matched"):
                result["status"] = "ejected_after_disconnect" if result.get("connection_dropped") else "ejected"
                if result.get("connection_dropped"):
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Eject media",
                        3,
                        total,
                        "[WARN] iLO closed EjectMedia without a response, but virtual media readback shows it ejected; treating eject as successful.",
                    )
                else:
                    update_job(kit_name, job, "Running", "Eject media", 3, total, f"[OK] Previous virtual media ejected: {vm_path}")
            else:
                last = dict(poll.get("last_seen") or {})
                result["status"] = "still_inserted_after_eject"
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Eject media",
                    3,
                    total,
                    (
                        "[WARN] EjectMedia did not verify as ejected before timeout; retrying once with a fresh iLO connection. "
                        f"inserted={'yes' if last.get('Inserted') else 'no'} image={last.get('Image') or '(none)'}"
                    ),
                )
                result["retry_attempted"] = True
                reconnect_esxi_ilo_after_transport_drop("Eject media", "EjectMedia did not verify as ejected before timeout.")
                retry_connection_dropped = False
                try:
                    run_with_session_refresh("Eject media", lambda c, vm_path=vm_path: c.eject_virtual_media(vm_path))
                    result["retry_status"] = "requested"
                except Exception as exc:
                    retry_error = str(exc).splitlines()[0]
                    result["retry_error"] = retry_error
                    if "iLO.2.25.UnsupportedOperation" in retry_error or "UnsupportedOperation" in retry_error:
                        result["retry_status"] = "unsupported"
                        update_job(
                            kit_name,
                            job,
                            "Running",
                            "Eject media",
                            3,
                            total,
                            "[WARN] iLO did not support the retry eject. Continuing only if the selected media already matches the generated ISO.",
                        )
                        return result
                    if not is_transport_disconnect_error(exc):
                        raise
                    result["retry_connection_dropped"] = True
                    retry_connection_dropped = True
                    result["retry_status"] = "transport_disconnect"
                    reconnect_esxi_ilo_after_transport_drop("Eject media", retry_error)

                if retry_connection_dropped:
                    retry_poll = poll_virtual_media_state(
                        vm_path,
                        lambda item: not bool(item.get("Inserted")),
                        stage_label="Eject media",
                        timeout_seconds=30,
                        poll_interval=2,
                    )
                else:
                    retry_readback = read_virtual_media_item(vm_path, stage_label="Eject media")
                    retry_poll = {
                        "matched": bool(retry_readback) and not bool(retry_readback.get("Inserted")),
                        "first_seen": retry_readback,
                        "last_seen": retry_readback,
                        "timeout_seconds": 0,
                        "poll_interval_seconds": 0,
                    }
                result["retry_readback"] = retry_poll
                if retry_poll.get("matched"):
                    result["status"] = "ejected_after_retry"
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Eject media",
                        3,
                        total,
                        "[OK] Previous virtual media ejected after retry.",
                    )
                else:
                    retry_last = dict(retry_poll.get("last_seen") or {})
                    result["status"] = "still_inserted_after_eject_retry"
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Eject media",
                        3,
                        total,
                        (
                            "[WARN] EjectMedia still did not verify as ejected after retry. "
                            f"inserted={'yes' if retry_last.get('Inserted') else 'no'} image={retry_last.get('Image') or '(none)'}"
                        ),
                    )
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Eject media",
                        3,
                        total,
                        "[WARN] Eject actions did not clear virtual media; trying Redfish PATCH Image=null, Inserted=false fallback.",
                    )
                    try:
                        run_with_session_refresh("Eject media", lambda c, vm_path=vm_path: c._patch(vm_path, {"Image": None, "Inserted": False}))
                        result["patch_clear_status"] = "requested"
                    except Exception as exc:
                        patch_error = str(exc).splitlines()[0]
                        result["patch_clear_error"] = patch_error
                        if not is_transport_disconnect_error(exc):
                            raise
                        result["patch_clear_connection_dropped"] = True
                        reconnect_esxi_ilo_after_transport_drop("Eject media", patch_error)
                    patch_poll = poll_virtual_media_state(
                        vm_path,
                        lambda item: not bool(item.get("Inserted")),
                        stage_label="Eject media",
                        timeout_seconds=20,
                        poll_interval=2,
                    )
                    result["patch_clear_readback"] = patch_poll
                    if patch_poll.get("matched"):
                        result["status"] = "ejected_after_patch_clear"
                        update_job(
                            kit_name,
                            job,
                            "Running",
                            "Eject media",
                            3,
                            total,
                            "[OK] Previous virtual media cleared with Redfish PATCH fallback.",
                        )
                    else:
                        patch_last = dict(patch_poll.get("last_seen") or {})
                        update_job(
                            kit_name,
                            job,
                            "Running",
                            "Eject media",
                            3,
                            total,
                            (
                                "[WARN] Redfish PATCH fallback did not clear virtual media. "
                                f"inserted={'yes' if patch_last.get('Inserted') else 'no'} image={patch_last.get('Image') or '(none)'}"
                            ),
                        )
            return result

        def insert_virtual_media_with_readback(insert_target: str, vm_path: str, image_url: str) -> dict[str, Any]:
            result = {
                "stage": "mount_virtual_media",
                "device_path": vm_path,
                "insert_target": insert_target,
                "image": image_url,
                "connection_dropped": False,
                "status": "requested",
                "readback": {},
            }
            try:
                run_with_session_refresh(
                    "Mount ISO",
                    lambda c: c._post(insert_target, {"Image": image_url, "Inserted": True, "WriteProtected": True}),
                )
            except Exception as exc:
                error_text = str(exc).splitlines()[0]
                result["error"] = error_text
                if "MaxVirtualMediaConnectionEstablished" in error_text:
                    result["status"] = "max_connection_retry"
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Mount ISO",
                        6,
                        total,
                        "[WARN] iLO reports the maximum virtual media connection is already established; ejecting stale media and retrying InsertMedia once.",
                    )
                    current_vm = read_virtual_media_item(vm_path, stage_label="Mount ISO")
                    eject_result = eject_existing_virtual_media(current_vm or {"@odata.id": vm_path, "Inserted": True})
                    result["eject_before_retry"] = eject_result
                    current_vm = read_virtual_media_item(vm_path, stage_label="Mount ISO")
                    best_effort_replace = eject_result.get("status") == "unsupported" or eject_result.get("retry_status") == "unsupported"
                    if current_vm and bool(current_vm.get("Inserted")) and str(current_vm.get("Image") or "") != image_url and not best_effort_replace:
                        raise ILOError(
                            "Virtual media device still has an active image after eject retry; cannot mount generated ESXi ISO safely. "
                            f"current_image={current_vm.get('Image') or '(none)'}"
                        )
                    reconnect_esxi_ilo_after_transport_drop("Mount ISO", "Retrying InsertMedia after stale virtual media eject.")
                    try:
                        run_with_session_refresh(
                            "Mount ISO",
                            lambda c: c._post(insert_target, {"Image": image_url, "Inserted": True, "WriteProtected": True}),
                        )
                        result["retry_status"] = "requested"
                    except Exception as retry_exc:
                        retry_error = str(retry_exc).splitlines()[0]
                        result["retry_error"] = retry_error
                        if not is_transport_disconnect_error(retry_exc):
                            raise
                        result["connection_dropped"] = True
                        result["retry_status"] = "transport_disconnect"
                        reconnect_esxi_ilo_after_transport_drop("Mount ISO", retry_error)
                elif is_transport_disconnect_error(exc):
                    result["connection_dropped"] = True
                    result["status"] = "transport_disconnect"
                    reconnect_esxi_ilo_after_transport_drop("Mount ISO", error_text)
                else:
                    raise

            if result.get("connection_dropped"):
                poll = poll_virtual_media_state(
                    vm_path,
                    lambda item: bool(item.get("Inserted")) and str(item.get("Image") or "") == image_url,
                    stage_label="Mount ISO",
                    timeout_seconds=30,
                    poll_interval=2,
                )
            else:
                readback = read_virtual_media_item(vm_path, stage_label="Mount ISO")
                poll = {
                    "matched": bool(readback) and bool(readback.get("Inserted")) and str(readback.get("Image") or "") == image_url,
                    "first_seen": readback,
                    "last_seen": readback,
                    "timeout_seconds": 0,
                    "poll_interval_seconds": 0,
                }
            result["readback"] = poll
            if result.get("connection_dropped") and poll.get("matched"):
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Mount ISO",
                    6,
                    total,
                    "[WARN] iLO closed InsertMedia without a response, but virtual media readback matches the generated ISO; treating mount as successful.",
                )
            elif result.get("connection_dropped"):
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Mount ISO",
                    6,
                    total,
                    "[WARN] iLO closed InsertMedia without a response and readback has not matched the generated ISO yet.",
                )
            result["status"] = "mounted" if poll.get("matched") else result["status"]
            return result

        update_job(kit_name, job, "Running", "Generate KS.CFG", 1, total, "[RUNNING] Generating KS.CFG")
        trace_payload["steps"].append({"stage": "generate_ks_cfg", "status": "running"})
        save_esxi_trace(trace_path, trace_payload)
        update_job(kit_name, job, "Running", "Review install values", 1, total, "[OK] KS.CFG generated")
        update_job(
            kit_name,
            job,
            "Running",
            "Review install values",
            1,
            total,
            (
                "[INFO] ESXi install values: "
                f"hostname={esxi_review['hostname']}, management_ip={esxi_review['management_ip']}, "
                f"subnet_mask={esxi_review['subnet_mask']}, gateway={esxi_review['gateway']}, "
                f"dns={','.join(esxi_review['dns_servers']) or '(none)'}"
            ),
        )
        update_job(
            kit_name,
            job,
            "Running",
            "Review install values",
            1,
            total,
            (
                "[INFO] root_password=SET "
                f"(policy-valid={'yes' if esxi_password_policy_valid(root_password) else 'no'})"
            ),
        )
        update_job(
            kit_name,
            job,
            "Running",
            "Review install values",
            1,
            total,
            (
                "[INFO] Optional settings: "
                f"vlan={esxi_review['vlan_id'] or '(none)'}, "
                f"ntp={esxi_review['ntp_server'] or '(none)'}, "
                f"ssh={'yes' if esxi_review['enable_ssh'] else 'no'}, "
                f"disable_ipv6={'yes' if esxi_review['disable_ipv6'] else 'no'}, "
                f"debug_no_reboot={'yes' if esxi_review.get('debug_no_reboot') else 'no'}"
            ),
        )
        if esxi_review.get("debug_no_reboot"):
            update_job(
                kit_name,
                job,
                "Running",
                "Review install values",
                1,
                total,
                "[INFO] ESXi debug_no_reboot is enabled; KS.CFG will not auto-reboot so the iLO console keeps the installer result visible.",
            )
        update_job(
            kit_name,
            job,
            "Running",
            "Review install values",
            1,
            total,
            "[INFO] First boot network check: attach ESXi management to the first physical NIC with link",
        )
        update_job(kit_name, job, "Running", "Review install values", 1, total, f"[INFO] Base ISO: {base_iso_path}")
        update_job(kit_name, job, "Running", "Review install values", 1, total, f"[INFO] Selected ESXi version: {esxi_review.get('version') or '7'}")
        install_target = dict(esxi_review.get("install_target") or {})
        job["esxi_install_target"] = install_target
        save_job(kit_name, job)
        update_job(
            kit_name,
            job,
            "Running",
            "Review install values",
            1,
            total,
            f"[INFO] KS.CFG install target: {install_target.get('kickstart_line') or 'install target not set'} | preferred={install_target.get('preferred_target') or 'unknown'}",
        )
        update_job(
            kit_name,
            job,
            "Running",
            "Review install values",
            1,
            total,
            f"[WARN] Install target guard: {install_target.get('safety_note') or 'Verify the OS RAID logical drive is first before installing.'}",
        )
        update_job(kit_name, job, "Running", "Review install values", 1, total, "[INFO] Boot mode assumptions: preserve vendor BIOS/UEFI boot entries and patch BOOT.CFG plus EFI/BOOT/BOOT.CFG when present")

        update_job(kit_name, job, "Running", "Building custom ESXi ISO", 1, total, "[RUNNING] Building custom ESXi ISO")
        output_iso = build_custom_iso(spec)
        iso_url = esxi_review["virtual_media_url"]
        job["esxi_iso_path"] = str(output_iso)
        job["esxi_iso_url"] = iso_url
        save_job(kit_name, job)
        trace_payload["artifacts"]["output_iso_path"] = str(output_iso)
        trace_payload["artifacts"]["virtual_media_url"] = iso_url
        build_summary_path = output_iso.parent / "build-summary.yml"
        if build_summary_path.exists():
            try:
                build_summary = yaml.safe_load(build_summary_path.read_text(encoding="utf-8")) or {}
            except Exception:
                build_summary = {}
            trace_payload["builder_summary_path"] = str(build_summary_path)
            trace_payload["builder_summary"] = build_summary
            job["esxi_builder_summary_path"] = str(build_summary_path)
            job["esxi_builder_generation"] = dict(build_summary.get("generation", {}) or {})
            job["esxi_builder_self_check"] = dict(build_summary.get("self_check", {}) or {})
            job["esxi_ks_cfg"] = dict((build_summary.get("generation", {}) or {}).get("ks_cfg", {}) or {})
            if build_summary.get("install_target"):
                job["esxi_install_target"] = dict(build_summary.get("install_target") or {})
            save_job(kit_name, job)
            save_esxi_trace(trace_path, trace_payload)
            generation = build_summary.get("generation", {}) or {}
            self_check = build_summary.get("self_check", {}) or {}
            if (generation.get("ks_cfg", {}) or {}).get("generated"):
                update_job(kit_name, job, "Running", "Build complete", 2, total, f"[OK] KS.CFG generated for ESXi {esxi_review.get('version') or '7'}")
                ks_meta = dict((generation.get("ks_cfg", {}) or {}))
                if ks_meta.get("iso_path") or ks_meta.get("inspection_path"):
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Build complete",
                        2,
                        total,
                        f"[INFO] KS.CFG path: iso={ks_meta.get('iso_path') or '/KS.CFG'} inspection={ks_meta.get('inspection_path') or '(not extracted)'} redacted_preview={ks_meta.get('redacted_preview_path') or '(not written)'}",
                    )
                if ks_meta.get("debug_no_reboot"):
                    update_job(kit_name, job, "Running", "Build complete", 2, total, "[INFO] KS.CFG debug_no_reboot confirmed: no automatic reboot command is present.")
            if (generation.get("boot_cfg", {}) or {}).get("patched"):
                update_job(kit_name, job, "Running", "Build complete", 2, total, "[OK] BOOT.CFG patched")
            efi_meta = generation.get("efi_boot_cfg", {}) or {}
            if efi_meta.get("present"):
                if efi_meta.get("patched"):
                    update_job(kit_name, job, "Running", "Build complete", 2, total, "[OK] EFI/BOOT/BOOT.CFG patched")
            else:
                update_job(kit_name, job, "Running", "Build complete", 2, total, "[INFO] EFI/BOOT/BOOT.CFG not present in base ISO")
            output_boot = self_check.get("output_boot_report", {}) or {}
            output_files = self_check.get("output_files_present", {}) or {}
            update_job(
                kit_name,
                job,
                "Running",
                "Build complete",
                2,
                total,
                (
                    "[INFO] ISO self-check: "
                    f"bios_boot={'yes' if output_boot.get('bios_entry_present') else 'no'}, "
                    f"uefi_boot={'yes' if output_boot.get('uefi_entry_present') else 'no'}, "
                    f"ks_cfg={'yes' if output_files.get('ks_cfg') else 'no'}, "
                    f"boot_cfg={'yes' if output_files.get('boot_cfg') else 'no'}, "
                    f"efi_boot_cfg={'yes' if output_files.get('efi_boot_cfg') else 'no'}"
                ),
            )
        update_job(kit_name, job, "Running", "Build complete", 2, total, f"[OK] Built ESXi ISO: {output_iso}")
        update_job(kit_name, job, "Running", "Build complete", 2, total, f"[INFO] Virtual media URL: {iso_url}")
        update_job(kit_name, job, "Running", "Build complete", 2, total, "[RUNNING] Verifying Lab Builder can serve the ESXi virtual media URL")
        media_url_check = verify_esxi_virtual_media_url(iso_url, output_iso)
        job["esxi_virtual_media_url_check"] = dict(media_url_check)
        trace_payload["artifacts"]["virtual_media_url_check"] = dict(media_url_check)
        save_job(kit_name, job)
        save_esxi_trace(trace_path, trace_payload)
        check_summary = esxi_virtual_media_url_check_summary(media_url_check)
        if media_url_check.get("status") == "ok":
            update_job(kit_name, job, "Running", "Build complete", 2, total, f"[OK] {check_summary}")
        elif media_url_check.get("status") == "skipped":
            update_job(kit_name, job, "Running", "Build complete", 2, total, f"[WARN] {check_summary}")
        else:
            raise ILOError(check_summary)
        client = build_esxi_ilo_client()
        update_job(kit_name, job, "Running", "Build complete", 2, total, "[INFO] Reconnected to iLO after ISO build")

        update_job(kit_name, job, "Running", "Eject media", 3, total, "[RUNNING] Ejecting previous virtual media")
        trace_payload["steps"].append({"stage": "eject_virtual_media", "status": "running"})
        save_esxi_trace(trace_path, trace_payload)
        for vm in run_with_session_refresh("Eject media", lambda c: c.get_virtual_media()):
            if vm.get("Inserted") and vm.get("@odata.id"):
                eject_result = eject_existing_virtual_media(vm)
                trace_payload["steps"].append(eject_result)
                save_esxi_trace(trace_path, trace_payload)

        system_path = run_with_session_refresh("Power off", lambda c: c.get_system_path() if hasattr(c, "get_system_path") else c.get_systems()[0])
        current_power = run_with_session_refresh(
            "Power off",
            lambda c: c.get_power_state(system_path=system_path) if hasattr(c, "get_power_state") else str(c.get_system(system_path).get("PowerState") or ""),
        )
        update_job(
            kit_name,
            job,
            "Running",
            "Power off",
            4,
            total,
            f"[INFO] ESXi stage initial PowerState={current_power or 'unknown'} on {system_path}.",
        )
        if current_power.lower() != "off":
            update_job(kit_name, job, "Running", "Power off", 4, total, "[RUNNING] Powering server off before setting one-time boot")
            trace_status = "running"
        else:
            update_job(kit_name, job, "Running", "Power off", 4, total, "[SKIP] Server already Off before ESXi boot preparation.")
            trace_status = "already_off"
        trace_payload["steps"].append({"stage": "power_off", "status": trace_status, "from_state": current_power})
        save_esxi_trace(trace_path, trace_payload)
        power_off_result = ensure_power_state_with_fallback("Off", system_path=system_path, timeout_seconds=180, poll_interval=5)
        update_job(
            kit_name,
            job,
            "Running",
            "Power off",
            4,
            total,
            f"[INFO] {power_reset_log_summary(power_off_result, default_action='ForceOff')}",
        )
        if str(power_off_result.get("action") or "") == "PushPowerButton":
            update_job(
                kit_name,
                job,
                "Running",
                "Power off",
                4,
                total,
                "[WARN] ForceOff did not reach Off; PushPowerButton fallback was used.",
            )
        update_job(kit_name, job, "Running", "Power off", 5, total, "[OK] Server is off")

        vm = run_with_session_refresh("Mount ISO", lambda c: choose_virtual_media_device(c))
        insert_target = ((vm.get("Actions") or {}).get("#VirtualMedia.InsertMedia") or {}).get("target")
        if not insert_target:
            raise ILOError("No InsertMedia action was found on the selected virtual media device.")
        job["esxi_virtual_media"] = {
            "device_path": str(vm.get("@odata.id") or ""),
            "insert_target": str(insert_target or ""),
            "initial_inserted": bool(vm.get("Inserted")),
            "initial_image": str(vm.get("Image") or ""),
        }
        save_job(kit_name, job)
        update_job(kit_name, job, "Running", "Mount ISO", 6, total, "[RUNNING] Mounting custom ESXi ISO")
        trace_payload["steps"].append({"stage": "mount_virtual_media", "status": "running", "target": insert_target, "image": iso_url})
        save_esxi_trace(trace_path, trace_payload)
        vm_path = str(vm.get("@odata.id") or "")
        if vm.get("Inserted") and str(vm.get("Image") or "") != iso_url:
            update_job(
                kit_name,
                job,
                "Running",
                "Mount ISO",
                6,
                total,
                "[WARN] Selected virtual media device still has a previous image mounted; ejecting it before mounting the generated ISO.",
            )
            pre_mount_eject = eject_existing_virtual_media(vm)
            trace_payload["steps"].append(pre_mount_eject)
            save_esxi_trace(trace_path, trace_payload)
            vm = read_virtual_media_item(vm_path, stage_label="Mount ISO")
            best_effort_replace = pre_mount_eject.get("status") == "unsupported" or pre_mount_eject.get("retry_status") == "unsupported"
            if vm.get("Inserted") and str(vm.get("Image") or "") != iso_url and not best_effort_replace:
                raise ILOError(
                    "Virtual media device still has an active image after eject retry; cannot mount generated ESXi ISO safely. "
                    f"current_image={vm.get('Image') or '(none)'}"
                )
        if vm.get("Inserted") and str(vm.get("Image") or "") == iso_url:
            insert_result = {
                "stage": "mount_virtual_media",
                "device_path": vm_path,
                "insert_target": str(insert_target or ""),
                "image": iso_url,
                "connection_dropped": False,
                "status": "already_mounted",
                "readback": {
                    "matched": True,
                    "first_seen": vm,
                    "last_seen": vm,
                    "timeout_seconds": 0,
                    "poll_interval_seconds": 0,
                },
            }
            update_job(kit_name, job, "Running", "Mount ISO", 6, total, "[SKIP] Generated ESXi ISO is already mounted on virtual media.")
        else:
            insert_result = insert_virtual_media_with_readback(insert_target, vm_path, iso_url)
        trace_payload["steps"].append(insert_result)
        save_esxi_trace(trace_path, trace_payload)
        readback_item = dict((insert_result.get("readback") or {}).get("last_seen") or {})
        mount_readback = {}
        if readback_item:
            mount_readback = {
                "device_path": str(readback_item.get("@odata.id") or readback_item.get("device_path") or ""),
                "inserted": bool(readback_item.get("Inserted")),
                "image": str(readback_item.get("Image") or ""),
                "write_protected": readback_item.get("WriteProtected"),
                "connection_dropped": bool(insert_result.get("connection_dropped")),
                "readback_matched": bool((insert_result.get("readback") or {}).get("matched")),
            }
        if mount_readback:
            image_matches = mount_readback.get("image") == iso_url
            job["esxi_virtual_media"] = {
                **dict(job.get("esxi_virtual_media") or {}),
                "post_mount_inserted": bool(mount_readback.get("inserted")),
                "post_mount_image": str(mount_readback.get("image") or ""),
                "post_mount_image_matches": bool(image_matches),
                "post_mount_write_protected": mount_readback.get("write_protected"),
            }
            save_job(kit_name, job)
            update_job(
                kit_name,
                job,
                "Running",
                "Mount ISO",
                7,
                total,
                (
                    "[INFO] Virtual media readback: "
                    f"inserted={'yes' if mount_readback.get('inserted') else 'no'} "
                    f"image={mount_readback.get('image') or '(empty)'}"
                ),
            )
            if not mount_readback.get("inserted") or not image_matches:
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Mount ISO",
                    7,
                    total,
                    "[FAILED] Virtual media mount readback did not match the built ESXi ISO URL.",
                )
                trace_payload["steps"].append({"stage": "mount_virtual_media", "status": "mismatch", "readback": mount_readback, "expected_image": iso_url})
                trace_payload["result"] = {
                    "status": "Failed",
                    "error": "Virtual media mount readback did not match the built ESXi ISO URL.",
                }
                save_esxi_trace(trace_path, trace_payload)
                return
        update_job(kit_name, job, "Running", "Mount ISO", 7, total, "[OK] Virtual media mounted")

        update_job(kit_name, job, "Running", "Set boot override", 8, total, "[RUNNING] Setting one-time boot to CD/DVD")
        trace_payload["steps"].append({"stage": "set_one_time_boot", "status": "running", "system_path": system_path})
        save_esxi_trace(trace_path, trace_payload)
        boot_override = run_with_session_refresh("Set boot override", lambda c: c.set_one_time_boot_cd(system_path=system_path))
        before_enabled = boot_override.get("before_enabled") or "(empty)"
        before_target = boot_override.get("before_target") or "(empty)"
        after_enabled = boot_override.get("after_enabled") or "(empty)"
        after_target = boot_override.get("after_target") or "(empty)"
        job["esxi_boot_override"] = dict(boot_override)
        save_job(kit_name, job)
        update_job(kit_name, job, "Running", "Set boot override", 8, total, f"[INFO] Boot override before: enabled={before_enabled} target={before_target}")
        if boot_override.get("matched"):
            update_job(kit_name, job, "Running", "Set boot override", 8, total, "[OK] One-time boot set to CD/DVD")
        else:
            update_job(
                kit_name,
                job,
                "Running",
                "Set boot override",
                8,
                total,
                f"[WARN] One-time boot did not stick cleanly; got enabled={after_enabled} target={after_target}. Continuing because mounted virtual media is verified on this hardware.",
            )
            save_job(kit_name, job)
            trace_payload["steps"].append({"stage": "set_one_time_boot", "status": "warning_mismatch", **boot_override})
            save_esxi_trace(trace_path, trace_payload)
        update_job(kit_name, job, "Running", "Set boot override", 8, total, f"[INFO] Boot override after: enabled={after_enabled} target={after_target}")
        selected_ref = str(boot_override.get("selected_boot_option_reference") or "")
        selected_target = str(boot_override.get("selected_uefi_target") or "")
        if selected_ref:
            update_job(
                kit_name,
                job,
                "Running",
                "Set boot override",
                8,
                total,
                f"[INFO] Boot override decision: selected UEFI virtual-media option {selected_ref} target={selected_target or selected_ref}.",
            )
        elif boot_override.get("matched"):
            update_job(
                kit_name,
                job,
                "Running",
                "Set boot override",
                8,
                total,
                "[INFO] Boot override decision: No concrete UEFI virtual CD option found. Generic Cd override read back successfully, continuing.",
            )
        else:
            update_job(
                kit_name,
                job,
                "Running",
                "Set boot override",
                8,
                total,
                "[WARN] Boot override decision: no concrete UEFI virtual CD option found and generic Cd did not read back cleanly.",
            )
        for note in boot_override.get("notes", []) or []:
            update_job(kit_name, job, "Running", "Set boot override", 8, total, f"[INFO] Boot override note: {note}")
        if str(after_target).strip().lower() != "cd":
            update_job(
                kit_name,
                job,
                "Running",
                "Set boot override",
                8,
                total,
                f"[INFO] iLO returned an equivalent CD/DVD target value: {after_target}",
            )
        trace_payload["steps"].append({"stage": "set_one_time_boot", "status": "verified", **boot_override})
        save_esxi_trace(trace_path, trace_payload)

        update_job(kit_name, job, "Running", "Power on", 9, total, "[RUNNING] Powering server on")
        trace_payload["steps"].append({"stage": "power_on", "status": "running", "system_path": system_path})
        job["esxi_power_transitions"] = {
            **dict(job.get("esxi_power_transitions") or {}),
            "power_off_confirmed": True,
            "power_on_requested": True,
        }
        save_job(kit_name, job)
        save_esxi_trace(trace_path, trace_payload)
        power_on_result = ensure_power_state_with_fallback("On", system_path=system_path, timeout_seconds=300, poll_interval=5)
        job["esxi_power_transitions"] = {
            **dict(job.get("esxi_power_transitions") or {}),
            "power_on_result": power_on_result,
        }
        save_job(kit_name, job)
        update_job(
            kit_name,
            job,
            "Running",
            "Power on",
            9,
            total,
            f"[INFO] {power_reset_log_summary(power_on_result, default_action='On')}",
        )
        if _power_connection_dropped(power_on_result):
            update_job(
                kit_name,
                job,
                "Running",
                "Power on",
                9,
                total,
                "[WARN] iLO closed the reset connection during power-on, but PowerState=On was verified.",
            )
        update_job(kit_name, job, "Running", "Wait for server power", 10, total, "[RUNNING] Waiting for the server to power back on")
        job["esxi_power_transitions"] = {
            **dict(job.get("esxi_power_transitions") or {}),
            "power_on_confirmed": True,
        }
        save_job(kit_name, job)
        update_job(kit_name, job, "Running", "Wait for server power", 11, total, "[OK] Server powered back on")
        boot_evidence = run_with_session_refresh(
            "Wait for server power",
            lambda c: collect_esxi_boot_evidence(c, system_path=system_path),
        )
        job["esxi_boot_evidence"] = dict(boot_evidence or {})
        save_job(kit_name, job)
        trace_payload["post_power_boot_evidence"] = dict(boot_evidence or {})
        save_esxi_trace(trace_path, trace_payload)
        mounted_vm = dict((boot_evidence or {}).get("mounted_virtual_media") or {})
        update_job(
            kit_name,
            job,
            "Running",
            "Wait for server power",
            11,
            total,
            (
                "[INFO] Post-power boot evidence: "
                f"power={boot_evidence.get('power_state') or '(unknown)'}, "
                f"post_state={boot_evidence.get('post_state') or '(unknown)'}, "
                f"boot_progress={boot_evidence.get('boot_progress_state') or '(unknown)'}, "
                f"boot_override={boot_evidence.get('boot_override_enabled') or '(empty)'}/"
                f"{boot_evidence.get('boot_override_target') or '(empty)'}"
            ),
        )
        update_job(
            kit_name,
            job,
            "Running",
            "Wait for server power",
            11,
            total,
            (
                "[INFO] Post-power virtual media: "
                f"inserted={'yes' if mounted_vm.get('inserted') else 'no'}, "
                f"image={mounted_vm.get('image') or '(none)'}"
            ),
        )

        update_job(
            kit_name,
            job,
            "Running",
            "Wait for ESXi network",
            12,
            total,
            f"[RUNNING] Waiting for ESXi management network on {management_ip}",
        )
        boot_evidence_samples: list[dict[str, Any]] = list(job.get("esxi_boot_evidence_samples") or [])
        last_boot_signature: tuple[Any, ...] | None = None
        stuck_post_polls = 0
        installer_boot_observed = False
        installer_boot_consumption_logged = False
        post_completed_seen = str(boot_evidence.get("post_state") or "").strip().lower() in {"finishedpost", "inpostdiscoverycomplete"}
        post_install_media_guard_attempted = False

        def cd_boot_override_enabled(enabled: Any, target: Any) -> bool:
            return str(enabled or "").strip().lower() == "once" and str(target or "").strip().lower() in {"cd", "uefitarget"}

        boot_override_seen_after_power_on = cd_boot_override_enabled(
            boot_evidence.get("boot_override_enabled"),
            boot_evidence.get("boot_override_target"),
        )

        def on_esxi_wait_poll(state: dict[str, Any]) -> None:
            nonlocal last_boot_signature, stuck_post_polls, installer_boot_observed
            nonlocal installer_boot_consumption_logged, post_completed_seen, post_install_media_guard_attempted
            nonlocal boot_override_seen_after_power_on
            attempts = int(state.get("attempts") or 0)
            if attempts != 1 and attempts % 2 != 0:
                return
            evidence = run_with_session_refresh(
                "Wait for ESXi network",
                lambda c: collect_esxi_boot_evidence(c, system_path=system_path),
            )
            mounted_vm = dict((evidence or {}).get("mounted_virtual_media") or {})
            sample = {
                "attempt": attempts,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "last_error": str(state.get("last_error") or ""),
                "power_state": str(evidence.get("power_state") or ""),
                "post_state": str(evidence.get("post_state") or ""),
                "boot_progress_state": str(evidence.get("boot_progress_state") or ""),
                "boot_override_enabled": str(evidence.get("boot_override_enabled") or ""),
                "boot_override_target": str(evidence.get("boot_override_target") or ""),
                "virtual_media_inserted": bool(mounted_vm.get("inserted")),
                "virtual_media_image": str(mounted_vm.get("image") or ""),
                "virtual_media_device_path": str(mounted_vm.get("device_path") or ""),
            }
            boot_evidence_samples.append(sample)
            job["esxi_boot_evidence_samples"] = list(boot_evidence_samples[-12:])
            job["esxi_boot_evidence"] = dict(evidence or {})
            save_job(kit_name, job)
            trace_payload["boot_evidence_samples"] = list(job["esxi_boot_evidence_samples"])
            save_esxi_trace(trace_path, trace_payload)

            signature = (
                sample["post_state"],
                sample["boot_progress_state"],
                sample["boot_override_enabled"],
                sample["boot_override_target"],
                sample["virtual_media_inserted"],
                sample["virtual_media_image"],
            )
            if signature != last_boot_signature:
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Wait for ESXi network",
                    12,
                    total,
                    (
                        f"[INFO] ESXi wait poll {attempts}: "
                        f"post_state={sample['post_state'] or '(unknown)'}, "
                        f"boot_progress={sample['boot_progress_state'] or '(unknown)'}, "
                        f"boot_override={sample['boot_override_enabled'] or '(empty)'}/"
                        f"{sample['boot_override_target'] or '(empty)'}, "
                        f"virtual_media={'yes' if sample['virtual_media_inserted'] else 'no'}"
                    ),
                )
                last_boot_signature = signature

            if cd_boot_override_enabled(sample["boot_override_enabled"], sample["boot_override_target"]):
                boot_override_seen_after_power_on = True

            stuck_in_post = (
                sample["power_state"] == "On"
                and sample["post_state"] == "InPost"
                and cd_boot_override_enabled(sample["boot_override_enabled"], sample["boot_override_target"])
                and sample["virtual_media_inserted"]
            )
            if stuck_in_post:
                stuck_post_polls += 1
            else:
                stuck_post_polls = 0
            if stuck_post_polls >= 4:
                raise ILOError(
                    "Server appears stuck in firmware/POST with the virtual CD/DVD still mounted "
                    "and the one-time CD/DVD boot override still pending."
                )

            boot_override_consumed = (
                boot_override_seen_after_power_on
                and sample["power_state"] == "On"
                and str(sample["boot_override_enabled"]).lower() in {"", "disabled"}
                and not str(sample["boot_override_target"]).strip().lower().replace("none", "")
                and sample["virtual_media_inserted"]
            )
            if boot_override_consumed and not installer_boot_observed:
                installer_boot_observed = True
                job["esxi_installer_boot_observed"] = True
                save_job(kit_name, job)
                trace_payload["installer_boot_observed"] = {
                    "reason": "one_time_boot_override_consumed",
                    "attempt": attempts,
                    "evidence": sample,
                }
                save_esxi_trace(trace_path, trace_payload)
            if boot_override_consumed and not installer_boot_consumption_logged:
                installer_boot_consumption_logged = True
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Wait for ESXi network",
                    12,
                    total,
                    "[INFO] One-time CD/DVD boot override was consumed; treating ESXi installer boot as started and not re-arming virtual media automatically.",
                )

            if str(sample["post_state"]).strip().lower() in {"finishedpost", "inpostdiscoverycomplete"}:
                post_completed_seen = True
            if installer_boot_observed and post_completed_seen and str(sample["post_state"]).strip().lower() == "inpost" and not post_install_media_guard_attempted:
                post_install_media_guard_attempted = True
                job["esxi_installer_reboot_detected"] = True
                guard_result = {
                    "reason": "installer_reboot_detected_before_management_ready",
                    "attempt": attempts,
                    "virtual_media_device_path": sample["virtual_media_device_path"],
                    "action": "do_not_rearm_virtual_cd_boot",
                    "eject_attempted": False,
                    "eject_status": "not_attempted",
                }
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Post-install boot guard",
                    12,
                    total,
                    "[WARN] ESXi installer appears to have rebooted before management became reachable; not re-arming virtual CD boot.",
                )
                vm_path = sample["virtual_media_device_path"]
                if vm_path:
                    guard_result["eject_attempted"] = True
                    try:
                        run_with_session_refresh("Post-install boot guard", lambda c, path=vm_path: c.eject_virtual_media(path))
                        guard_result["eject_status"] = "ejected"
                        update_job(
                            kit_name,
                            job,
                            "Running",
                            "Post-install boot guard",
                            12,
                            total,
                            "[INFO] Post-install boot guard ejected virtual media so the next boot can use local disk.",
                        )
                    except Exception as guard_exc:
                        guard_result["eject_status"] = "failed"
                        guard_result["error"] = str(guard_exc).splitlines()[0]
                        update_job(
                            kit_name,
                            job,
                            "Running",
                            "Post-install boot guard",
                            12,
                            total,
                            f"[WARN] Post-install boot guard could not eject virtual media: {guard_result['error']}",
                        )
                else:
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Post-install boot guard",
                        12,
                        total,
                        "[WARN] Post-install boot guard could not eject virtual media because no mounted device path was discovered.",
                    )
                job["esxi_post_install_boot_guard"] = guard_result
                save_job(kit_name, job)
                trace_payload["post_install_boot_guard"] = guard_result
                save_esxi_trace(trace_path, trace_payload)

        ready_result = wait_for_esxi_management_ready(management_ip, on_poll=on_esxi_wait_poll)
        job["esxi_management_network"] = dict(ready_result or {})
        post_preview = build_esxi_post_config_preview(cfg)
        post_validation = validate_esxi_post_config_preview(post_preview)
        post_actions = build_esxi_post_config_actions(post_preview)
        requested_transport = str((cfg.get("esxi", {}) or {}).get("post_config_transport") or "dry_run").strip().lower()
        post_run_action_fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
        transport_note = ""
        if requested_transport == "ssh":
            try:
                post_run_action_fn = build_esxi_post_config_ssh_run_action(cfg, post_preview)
                transport_note = "live-ssh"
            except Exception as transport_exc:
                transport_note = f"dry-run-fallback ({str(transport_exc).splitlines()[0]})"
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply ESXi post-config policy",
                    12,
                    total,
                    f"[WARN] ESXi post-config transport requested SSH but is not available: {str(transport_exc).splitlines()[0]}. Falling back to dry-run planning.",
                )
        else:
            transport_note = "dry-run"
        post_execution = execute_esxi_post_config_actions(
            cfg,
            preview=post_preview,
            validation=post_validation,
            run_action_fn=post_run_action_fn,
        )
        post_execution["transport_mode"] = transport_note
        job["esxi_post_config_preview"] = post_preview
        job["esxi_post_config_validation"] = post_validation
        job["esxi_post_config_actions"] = post_actions
        job["esxi_post_config_execution"] = post_execution
        save_job(kit_name, job)
        trace_payload["post_config"] = {
            "preview": post_preview,
            "validation": post_validation,
            "actions": post_actions,
            "execution": post_execution,
        }
        trace_payload["result"] = {
            "status": "Completed",
            "management_ready": ready_result,
            "post_config": {
                "ok": bool(post_execution.get("ok")),
                "reboot_required": bool(post_execution.get("reboot_required")),
                "reboot_performed": bool(post_execution.get("reboot_performed")),
            },
        }
        save_esxi_trace(trace_path, trace_payload)
        if post_execution.get("errors"):
            update_job(
                kit_name,
                job,
                "Running",
                "Apply ESXi post-config policy",
                12,
                total,
                f"[WARN] ESXi post-config policy reported errors: {' | '.join(post_execution.get('errors') or [])}",
            )
        else:
            update_job(
                kit_name,
                job,
                "Running",
                "Apply ESXi post-config policy",
                12,
                total,
                (
                    "[OK] ESXi post-config policy staged. "
                    f"actions={len(post_actions)} reboot_required={'yes' if post_execution.get('reboot_required') else 'no'} "
                    f"reboot_performed={'yes' if post_execution.get('reboot_performed') else 'no'}"
                ),
            )
        update_job(
            kit_name,
            job,
            "Completed",
            "Finished",
            total,
            total,
            (
                f"[OK] ESXi responded on configured IP {ready_result.get('host')}:{ready_result.get('port')} "
                f"after {ready_result.get('attempts')} checks. ESXi boot sequence started."
            ),
        )
        append_activity_event(
            kit_name,
            "esxi_real_run_started",
            workflow="esxi",
            summary="Built the custom ESXi installer ISO, booted the server from it, and confirmed ESXi answered on the configured management IP.",
            target=management_ip,
            details=[
                f"ISO: {output_iso}",
                f"Base ISO: {base_iso_path}",
                f"Post-config actions: {len(post_actions)}",
                f"Post-config reboot required: {'Yes' if post_execution.get('reboot_required') else 'No'}",
                f"Post-config reboot performed: {'Yes' if post_execution.get('reboot_performed') else 'No'}",
            ],
        )
    except Exception as e:
        detail = str(e).splitlines()[0]
        power_failure_details = dict(getattr(e, "power_reset_details", {}) or {})
        if power_failure_details:
            update_job(
                kit_name,
                job,
                "Running",
                "Power on" if str(power_failure_details.get("expected_power_state") or "").lower() == "on" else "Power off",
                job.get("completed_steps", 0),
                total,
                f"[INFO] {power_reset_log_summary(power_failure_details, default_action=power_failure_details.get('action') or 'On')}",
            )
        if "configured IP" in detail and "ESXi did not answer" in detail:
            try:
                if "client" in locals() and client:
                    final_boot_evidence = collect_esxi_boot_evidence(client, system_path=locals().get("system_path"))
                    job["esxi_boot_evidence"] = dict(final_boot_evidence or {})
                    save_job(kit_name, job)
                    trace_payload["final_boot_evidence"] = dict(final_boot_evidence or {})
                    save_esxi_trace(trace_path, trace_payload)
                    mounted_vm = dict((final_boot_evidence or {}).get("mounted_virtual_media") or {})
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Wait for ESXi network",
                        12,
                        total,
                        (
                            "[INFO] Final boot evidence before timeout: "
                            f"power={final_boot_evidence.get('power_state') or '(unknown)'}, "
                            f"post_state={final_boot_evidence.get('post_state') or '(unknown)'}, "
                            f"boot_progress={final_boot_evidence.get('boot_progress_state') or '(unknown)'}, "
                            f"boot_override={final_boot_evidence.get('boot_override_enabled') or '(empty)'}/"
                            f"{final_boot_evidence.get('boot_override_target') or '(empty)'}"
                        ),
                    )
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Wait for ESXi network",
                        12,
                        total,
                        (
                            "[INFO] Final virtual media state before timeout: "
                            f"inserted={'yes' if mounted_vm.get('inserted') else 'no'}, "
                            f"image={mounted_vm.get('image') or '(none)'}"
                        ),
                    )
            except Exception:
                pass
            if job.get("esxi_installer_boot_observed"):
                detail += (
                    " The one-time virtual CD boot was consumed, so the ESXi installer likely started. "
                    "Possible causes: kickstart failure, install target failure, early installer reboot, or management NIC mismatch."
                )
            else:
                detail += " This usually means the kickstart network settings did not apply or the installer did not finish."
        if 'trace_path' in locals():
            trace_payload["result"] = {
                "status": "Failed",
                "error": detail,
            }
            diagnosis = esxi_failure_diagnosis(job, detail, e)
            trace_payload["diagnosis"] = diagnosis
            save_esxi_trace(trace_path, trace_payload)
        else:
            diagnosis = esxi_failure_diagnosis(job, detail, e)
        attach_esxi_diagnosis(job, diagnosis)
        update_job(kit_name, job, "Failed", "ESXi error", job.get("completed_steps", 0), total, f"[FAILED] {detail}")


def run_ilo_real(cfg: dict):
    kit_name = cfg["site"]["name"]
    existing_job = load_job(kit_name)
    inherited_root_scope = str(existing_job.get("root_scope") or "ilo")
    ilo_cfg = cfg.get("ilo", {})
    standard_policy = build_standard_ilo_policy(cfg)
    policy_settings = dict(standard_policy.get("settings") or {})
    active_snmp_user = dict(standard_policy.get("snmp") or {})
    additional_ilo_users = normalize_ilo_additional_users(ilo_cfg.get("additional_users", []))
    policy_accounts = list(standard_policy.get("accounts") or []) if policy_settings.get("apply_standard_policy") and policy_settings.get("enable_standard_accounts") else []
    local_accounts_to_apply = policy_accounts + additional_ilo_users
    shared_dns = [x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x and x.strip()]
    storage_execution = validate_storage_ready_for_ilo_run(cfg)
    desired_auth_protocol = active_snmp_user.get("auth_protocol", "SHA")
    desired_priv_protocol = active_snmp_user.get("priv_protocol", "AES")

    total = 32 if storage_execution.get("included") else 15
    job = {
        "status": "Running",
        "execution_mode": "real",
        "execution_mode_label": "Real execution",
        "scope": "ilo",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total,
        "logs": [],
        "dns_apply_status": "Not attempted",
        "dns_requested_values": list(shared_dns),
        "dns_before_values": [],
        "dns_applied_values": [],
        "dns_applied_keys": [],
        "dns_mismatches": [],
        "dns_reset_recommended": False,
        "snmp_apply_status": "Not attempted",
        "snmp_applied_keys": [],
        "snmp_username": active_snmp_user.get("v3_username", "") or "",
        "snmp_auth_protocol": desired_auth_protocol,
        "snmp_priv_protocol": desired_priv_protocol,
        "snmp_auth_secret_present": bool(active_snmp_user.get("v3_auth_password")),
        "snmp_priv_secret_present": bool(active_snmp_user.get("v3_priv_password")),
        "snmp_verified_checks": [],
        "snmp_mismatches": [],
        "snmp_reset_recommended": False,
        "snmp_profile_count": 1 if active_snmp_user.get("v3_username") else 0,
        "local_account_status": "Not attempted",
        "local_accounts_requested": [item.get("username", "") for item in local_accounts_to_apply],
        "local_account_results": [],
        "license_status": "Not attempted",
        "license_warnings": [],
        "snmp_alert_status": "Not attempted",
        "snmp_alert_results": [],
        "ipv6_policy_status": "Not attempted",
        "ipv6_policy_checks": [],
        "time_policy_status": "Not attempted",
        "time_policy_notes": [],
        "ilo_policy_plan": {
            "accounts": [item.get("username", "") for item in local_accounts_to_apply],
            "license_check_enabled": bool(policy_enabled(cfg, "enable_license_check")),
            "snmp_enabled": bool(policy_enabled(cfg, "enable_snmp_policy")),
            "alert_destinations": list(active_snmp_user.get("alert_destinations") or []),
            "ipv6_disable_enabled": bool(policy_enabled(cfg, "enable_ipv6_disable")),
            "time_policy_enabled": bool(policy_enabled(cfg, "enable_time_policy")),
        },
        "ilo_policy_applied": [],
        "ilo_policy_warnings": [],
        "ilo_policy_failures": [],
        "ilo_policy_raw_results": {},
        "storage_server_reboot_required": False,
        "storage_server_reboot_status": "Not required",
        "ilo_reset_required": False,
        "ilo_reset_status": "Not required",
        "ilo_stage_finished": False,
        "ilo_final_ip_verified": False,
        "root_scope": inherited_root_scope,
        "stage_statuses": merge_stage_statuses(
            initialize_stage_statuses(inherited_root_scope, cfg),
            existing_job.get("stage_statuses"),
        ),
    }
    job = carry_forward_job_bundle_metadata(kit_name, job)
    save_job(kit_name, job)

    login_ip = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    username = ilo_cfg.get("username", "").strip()
    password = ilo_cfg.get("password", "")
    desired_hostname_raw = str(ilo_cfg.get("hostname", "") or "").strip()
    desired_hostname = normalize_ilo_hostname(desired_hostname_raw)
    target_ip = (ilo_cfg.get("target_ip") or "").strip()
    desired_gateway = (ilo_cfg.get("gateway") or "").strip()
    desired_subnet_mask = (ilo_cfg.get("subnet_mask") or "").strip()
    active_ip = login_ip
    expected_final_ip = target_ip or login_ip
    config_changes_attempted = False
    config_changes_succeeded = True
    endpoint_transition_pending = False
    ilo_stage_finished = False
    reset_reasons: list[str] = []

    if not login_ip or not username or not password:
        update_job(kit_name, job, "Failed", "Validation failed", 0, total, "[FAILED] Missing iLO host, username, or password.")
        return

    def build_ilo_client(hostname: str) -> ILOClient:
        return ILOClient(ILOConfig(host=hostname, username=username, password=password, verify_tls=False, timeout=15))

    def policy_adapter() -> HpeIloRedfishAdapter:
        return HpeIloRedfishAdapter(client)

    def reconnect_to_active_ip(next_ip: str, *, stage_name: str, step_index: int, retries: int = 6, wait_seconds: float = 5.0):
        nonlocal client, active_ip
        notes: list[str] = []
        for attempt in range(1, retries + 1):
            try:
                candidate = build_ilo_client(next_ip)
                candidate.get_summary()
                notes.append(f"Attempt {attempt}: connected to {next_ip}.")
                if hasattr(client, "cfg"):
                    client.cfg.host = next_ip
                if hasattr(client, "base") and hasattr(candidate, "base"):
                    client.base = candidate.base
                if hasattr(client, "redfish_root") and hasattr(candidate, "redfish_root"):
                    client.redfish_root = candidate.redfish_root
                if hasattr(client, "auth") and hasattr(candidate, "auth"):
                    client.auth = candidate.auth
                active_ip = next_ip
                update_job(
                    kit_name,
                    job,
                    "Running",
                    stage_name,
                    step_index,
                    total,
                    f"[OK] Reconnected to iLO on new IP {next_ip}",
                )
                return {"connected": True, "attempts": attempt, "notes": notes}
            except Exception as e:
                detail = str(e).splitlines()[0]
                notes.append(f"Attempt {attempt}: {detail}")
                if attempt < retries:
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        stage_name,
                        step_index,
                        total,
                        f"[RUNNING] Waiting for iLO to come up on new IP {next_ip} (attempt {attempt}/{retries})",
                    )
                    time.sleep(wait_seconds)
                else:
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        stage_name,
                        step_index,
                        total,
                        f"[WARN] iLO IP change may have applied, but reconnect to new IP {next_ip} failed | last_error={detail}",
                    )
        return {"connected": False, "attempts": retries, "notes": notes}

    def verify_active_ip_state(expected_ip: str, *, stage_name: str, step_index: int):
        try:
            iface = client.get_active_manager_interface()
            ipv4_values = []
            for item in list(iface.get("IPv4Addresses", []) or []) + list(iface.get("IPv4StaticAddresses", []) or []):
                if isinstance(item, dict):
                    ipv4_values.append(str(item.get("Address") or "").strip())
            matched = bool(expected_ip) and expected_ip in ipv4_values
            update_job(
                kit_name,
                job,
                "Running",
                stage_name,
                step_index,
                total,
                (
                    "[OK] Verified iLO active interface on final IP "
                    if matched
                    else "[WARN] iLO active interface did not read back on the expected final IP "
                )
                + f"{expected_ip or '(unchanged)'} | active_interface_ips={ipv4_values}",
            )
            return {"matched": matched, "active_ips": ipv4_values}
        except Exception as e:
            update_job(
                kit_name,
                job,
                "Running",
                stage_name,
                step_index,
                total,
                f"[WARN] Could not verify the final iLO active interface IP: {str(e).splitlines()[0]}",
            )
            return {"matched": False, "active_ips": []}

    def normalize_dns_values(values):
        return [
            str(item).strip()
            for item in (values or [])
            if str(item or "").strip() and str(item).strip() not in {"0.0.0.0", "::"}
        ]

    def active_interface_ipv4_matches(iface_doc: dict[str, Any], address: str, subnet_mask: str, gateway: str) -> bool:
        if not address or not subnet_mask or not gateway:
            return False
        for item in list(iface_doc.get("IPv4Addresses", []) or []) + list(iface_doc.get("IPv4StaticAddresses", []) or []):
            if not isinstance(item, dict):
                continue
            if (
                str(item.get("Address") or "").strip() == address
                and str(item.get("SubnetMask") or "").strip() == subnet_mask
                and str(item.get("Gateway") or "").strip() == gateway
            ):
                return True
        return False

    def current_hostname_value(network_protocol_doc: dict[str, Any], iface_doc: dict[str, Any]) -> str:
        return str(
            network_protocol_doc.get("HostName")
            or iface_doc.get("HostName")
            or ""
        ).strip()

    def current_snmp_matches(network_protocol_doc: dict[str, Any]) -> bool:
        return ilo_current_snmp_matches(
            network_protocol_doc,
            snmp_policy_enabled=bool(policy_enabled(cfg, "enable_snmp_policy")),
            requested_username=str(active_snmp_user.get("v3_username") or ""),
            desired_auth_protocol=desired_auth_protocol,
            desired_priv_protocol=desired_priv_protocol,
        )

    def verify_final_ilo_configuration(*, stage_name: str, step_index: int) -> dict[str, Any]:
        update_job(
            kit_name,
            job,
            "Running",
            stage_name,
            step_index,
            total,
            "[RUNNING] Verifying final iLO state after reset.",
        )

        try:
            _, network_protocol = client.get_network_protocol()
        except Exception as e:
            network_protocol = {}
            network_protocol_error = str(e).splitlines()[0]
        else:
            network_protocol_error = ""

        try:
            iface = client.get_active_manager_interface()
        except Exception as e:
            iface = {}
            active_interface_error = str(e).splitlines()[0]
        else:
            active_interface_error = ""

        result = ilo_verify_final_state(
            network_protocol_doc=network_protocol,
            iface_doc=iface,
            desired_hostname=desired_hostname,
            shared_dns=shared_dns,
            snmp_policy_enabled=bool(policy_enabled(cfg, "enable_snmp_policy")),
            requested_username=str(active_snmp_user.get("v3_username") or ""),
            desired_auth_protocol=desired_auth_protocol,
            desired_priv_protocol=desired_priv_protocol,
        )
        if network_protocol_error:
            result.setdefault("errors", []).append(f"network_protocol={network_protocol_error}")
        if active_interface_error:
            result.setdefault("errors", []).append(f"active_interface={active_interface_error}")

        hostname_expected = str(desired_hostname or "").strip()
        if hostname_expected:
            update_job(
                kit_name,
                job,
                "Running",
                stage_name,
                step_index,
                total,
                (
                    "[OK] Final hostname verified: "
                    if result["hostname_matched"]
                    else "[FAILED] Final hostname did not match: "
                )
                + f"expected={hostname_expected} actual={result.get('actual_hostname') or '(empty)'}",
            )

        requested_dns = list(result.get("requested_dns") or [])
        if requested_dns:
            update_job(
                kit_name,
                job,
                "Running",
                stage_name,
                step_index,
                total,
                (
                    "[OK] Final DNS verified: "
                    if result["dns_matched"]
                    else "[FAILED] Final DNS did not match: "
                )
                + f"expected={requested_dns} actual={result.get('actual_dns') or []}",
            )

        snmp_block = result.get("snmp_block") or {}
        snmp_checks = list(result.get("snmp_checks") or [])
        if policy_enabled(cfg, "enable_snmp_policy") and active_snmp_user.get("v3_username"):
            update_job(
                kit_name,
                job,
                "Running",
                stage_name,
                step_index,
                total,
                (
                    "[OK] Final SNMP verified: "
                    if result["snmp_matched"]
                    else "[FAILED] Final SNMP did not match: "
                )
                + f"checks={snmp_checks or '(no readable SNMP fields found)'} | raw={snmp_block}",
            )

        all_matched = result["matched"]
        update_job(
            kit_name,
            job,
            "Running",
            stage_name,
            step_index,
            total,
            (
                "[OK] Post-reset verification complete."
                if all_matched
                else "[FAILED] Post-reset verification found one or more mismatches."
            ),
        )
        return result

    def wait_for_ilo_reset_completion(expected_ip: str, *, stage_name: str, step_index: int, start_timeout: int = 90, return_timeout: int = 300, poll_interval: int = 5):
        nonlocal client, active_ip
        interrupt_observed = False
        interrupt_detail = "No interruption was observed before timeout."
        start_deadline = time.time() + max(start_timeout, 1)
        while time.time() < start_deadline:
            time.sleep(max(poll_interval, 1))
            try:
                build_ilo_client(expected_ip).get_summary()
            except Exception as e:
                interrupt_observed = True
                interrupt_detail = str(e).splitlines()[0]
                update_job(
                    kit_name,
                    job,
                    "Running",
                    stage_name,
                    step_index,
                    total,
                    f"[OK] iLO reset started on {expected_ip}: {interrupt_detail}",
                )
                break

        if not interrupt_observed:
            return {
                "matched": False,
                "interrupt_observed": False,
                "return_observed": False,
                "interrupt_detail": interrupt_detail,
                "return_detail": "",
            }

        return_deadline = time.time() + max(return_timeout, 1)
        return_detail = "iLO did not come back before timeout."
        while time.time() < return_deadline:
            time.sleep(max(poll_interval, 1))
            try:
                candidate = build_ilo_client(expected_ip)
                candidate.get_summary()
                client = candidate
                active_ip = expected_ip
                return {
                    "matched": True,
                    "interrupt_observed": True,
                    "return_observed": True,
                    "interrupt_detail": interrupt_detail,
                    "return_detail": f"Reconnected to iLO on {expected_ip}.",
                }
            except Exception as e:
                return_detail = str(e).splitlines()[0]

        return {
            "matched": False,
            "interrupt_observed": True,
            "return_observed": False,
            "interrupt_detail": interrupt_detail,
            "return_detail": return_detail,
        }

    try:
        update_job(kit_name, job, "Running", "Validate configuration", 0, total, f"[RUNNING] Validating iLO config for {login_ip}")
        update_job(
            kit_name,
            job,
            "Running",
            "Validate configuration",
            0,
            total,
            (
                f"[CONFIG] login_ip={login_ip} | target_ip={target_ip or '(unchanged)'} | active_ip={active_ip} | "
                f"hostname={desired_hostname or '(unchanged)'} | "
                f"subnet_mask={desired_subnet_mask or '(shared/default)'} | "
                f"gateway={desired_gateway or '(shared/default)'}"
            ),
        )
        update_job(
            kit_name,
            job,
            "Running",
            "Validate configuration",
            0,
            total,
            (
                f"[CONFIG] shared_dns={', '.join(shared_dns) if shared_dns else '(none)'} | "
                f"snmp_v3_user={active_snmp_user.get('v3_username', '') or '(none)'} | "
                f"auth={desired_auth_protocol} | priv={desired_priv_protocol} | "
                f"auth_password={'set' if active_snmp_user.get('v3_auth_password') else 'missing'} | "
                f"priv_password={'set' if active_snmp_user.get('v3_priv_password') else 'missing'} | "
                f"policy_accounts={len(policy_accounts)} | "
                f"additional_ilo_users={len(additional_ilo_users)} | "
                f"auto_reset={'on' if policy_settings.get('enable_auto_reset') else 'off'}"
            ),
        )
        client = build_ilo_client(active_ip)
        current_network_protocol = {}
        current_active_interface = {}
        ip_change_applied = False
        hostname_change_applied = False
        dns_change_applied = False
        snmp_change_applied = False
        license_change_applied = False
        alerts_change_applied = False
        ipv6_change_applied = False
        time_change_applied = False
        local_users_change_applied = False

        update_job(kit_name, job, "Running", "Connect to Redfish", 1, total, f"[RUNNING] Connecting to https://{active_ip}/redfish/v1/")
        summary = client.get_summary()
        update_job(kit_name, job, "Running", "Read service root", 2, total, f"[OK] Redfish version: {summary.get('redfish_version', '')}")
        update_job(
            kit_name,
            job,
            "Running",
            "Read system inventory",
            3,
            total,
            f"[INFO] iLO stage initial PowerState={summary.get('power_state', '') or 'unknown'}",
        )

        update_job(
            kit_name,
            job,
            "Running",
            "Read system inventory",
            3,
            total,
            f"[OK] System: {summary.get('system_manufacturer', '')} {summary.get('system_model', '')} | Power: {summary.get('power_state', '')}"
        )

        try:
            iface = client.get_active_manager_interface()
            current_active_interface = iface
            update_job(
                kit_name,
                job,
                "Running",
                "Inspect network state",
                4,
                total,
                (
                    f"[OK] Active interface {iface.get('@odata.id', '')} | "
                    f"dhcpv4={iface.get('DHCPv4', {})} | "
                    f"ipv4={iface.get('IPv4Addresses', []) or iface.get('IPv4StaticAddresses', [])}"
                ),
            )
        except Exception as e:
            update_job(
                kit_name,
                job,
                "Running",
                "Inspect network state",
                4,
                total,
                f"[SKIP/INFO] Could not read active interface details: {e}"
            )

        try:
            _, current_network_protocol = client.get_network_protocol()
        except Exception:
            current_network_protocol = {}

        if target_ip and desired_subnet_mask and desired_gateway:
            if active_interface_ipv4_matches(current_active_interface, target_ip, desired_subnet_mask, desired_gateway):
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply static IPv4",
                    5,
                    total,
                    f"[OK] iLO IPv4 already correct; no change needed for {target_ip}.",
                )
            else:
                config_changes_attempted = True
                try:
                    if target_ip != active_ip:
                        update_job(
                            kit_name,
                            job,
                            "Running",
                            "Apply static IPv4",
                            5,
                            total,
                            f"[RUNNING] Applying iLO IP change from {active_ip} to {target_ip}",
                        )
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Apply static IPv4",
                        5,
                        total,
                        f"[RUNNING] Disabling DHCPv4 and setting static IPv4 address={target_ip} subnet_mask={desired_subnet_mask} gateway={desired_gateway}"
                    )
                    ip_result = client.set_static_ipv4_best_effort(
                        address=target_ip,
                        subnet_mask=desired_subnet_mask,
                        gateway=desired_gateway,
                    )
                    ip_change_applied = True
                    current_active_interface = {
                        **current_active_interface,
                        "IPv4Addresses": ip_result.get("after_ipv4_addresses") or current_active_interface.get("IPv4Addresses", []),
                        "IPv4StaticAddresses": ip_result.get("after_static_addresses") or current_active_interface.get("IPv4StaticAddresses", []),
                    }
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Verify static IPv4",
                        6,
                        total,
                        (
                            f"[OK] Static IPv4 applied via {', '.join(ip_result.get('applied_keys', []))} | "
                            f"before_dhcpv4={ip_result.get('before_dhcpv4')} | after_dhcpv4={ip_result.get('after_dhcpv4')} | "
                            f"before_ipv4={ip_result.get('before_ipv4_addresses') or ip_result.get('before_static_addresses')} | "
                            f"after_ipv4={ip_result.get('after_ipv4_addresses') or ip_result.get('after_static_addresses')}"
                        ),
                    )
                    if target_ip and target_ip != active_ip:
                        before_ips = [
                            str(item.get("Address") or "").strip()
                            for item in (ip_result.get("before_ipv4_addresses") or ip_result.get("before_static_addresses") or [])
                            if isinstance(item, dict)
                        ]
                        after_ips = [
                            str(item.get("Address") or "").strip()
                            for item in (ip_result.get("after_ipv4_addresses") or ip_result.get("after_static_addresses") or [])
                            if isinstance(item, dict)
                        ]
                        target_seen_in_readback = target_ip in before_ips or target_ip in after_ips
                        if target_seen_in_readback:
                            endpoint_transition_pending = True
                            job["expected_final_ip"] = expected_final_ip
                            job["active_ip"] = active_ip
                            save_job(kit_name, job)
                            update_job(
                                kit_name,
                                job,
                                "Running",
                                "Reconnect to iLO",
                                6,
                                total,
                                (
                                    "[INFO] Target iLO IP already appeared in interface readback. "
                                    f"Keeping the current session on {active_ip} and deferring final target verification for {target_ip}."
                                ),
                            )
                        else:
                            reconnect_result = reconnect_to_active_ip(
                                target_ip,
                                stage_name="Reconnect to iLO",
                                step_index=6,
                            )
                            if reconnect_result.get("connected"):
                                job["active_ip"] = active_ip
                                job["expected_final_ip"] = expected_final_ip
                                save_job(kit_name, job)
                            else:
                                endpoint_transition_pending = True
                                job["active_ip"] = active_ip
                                job["expected_final_ip"] = expected_final_ip
                                job["ip_reconnect_failed"] = True
                                job["ip_reconnect_notes"] = reconnect_result.get("notes", [])
                                save_job(kit_name, job)
                                update_job(
                                    kit_name,
                                    job,
                                    "Running",
                                    "Reconnect to iLO",
                                    6,
                                    total,
                                    (
                                        "[WARN] Target iLO IP did not come up yet, but the current iLO session is still active. "
                                        f"Continuing on {active_ip} and deferring final verification for {target_ip}."
                                    ),
                                )
                except Exception as e:
                    config_changes_succeeded = False
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Apply static IPv4",
                        6,
                        total,
                        f"[FAILED] Static IPv4 update not applied: {e}"
                    )
        else:
            update_job(
                kit_name,
                job,
                "Running",
                "Skip static IPv4",
                6,
                total,
                "[SKIP] Missing target IP, subnet mask, or gateway for static IPv4 update."
            )

        if desired_hostname_raw and desired_hostname_raw != desired_hostname:
            cfg.setdefault("ilo", {})["hostname"] = desired_hostname
            save_kit_config(cfg)
            update_job(
                kit_name,
                job,
                "Running",
                "Apply iLO hostname",
                7,
                total,
                f"[INFO] Normalized invalid iLO hostname '{desired_hostname_raw}' to '{desired_hostname}' before apply.",
            )

        if desired_hostname:
            current_hostname = current_hostname_value(current_network_protocol, current_active_interface)
            if current_hostname == desired_hostname:
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply iLO hostname",
                    7,
                    total,
                    f"[OK] Hostname already correct; no change needed for {desired_hostname}.",
                )
            else:
                config_changes_attempted = True
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply iLO hostname",
                    7,
                    total,
                    f"[RUNNING] Attempting to set iLO hostname to: {desired_hostname}"
                )
                result = client.set_hostname_best_effort(desired_hostname)
                hostname_change_applied = bool(result.get("changed"))
                current_network_protocol["HostName"] = result.get("after", current_network_protocol.get("HostName"))
                current_active_interface["HostName"] = result.get("after", current_active_interface.get("HostName"))
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Verify iLO hostname",
                    8,
                    total,
                    f"[OK] Hostname write via {result.get('method')} | before='{result.get('before','')}' | after='{result.get('after','')}' | matched={result.get('matched')}"
                )
                if not result.get("matched"):
                    config_changes_succeeded = False
        else:
            update_job(
                kit_name,
                job,
                "Running",
                "Skip hostname",
                8,
                total,
                "[SKIP] No desired iLO hostname configured."
            )

        if shared_dns:
            current_dns = normalize_dns_values(
                current_active_interface.get("StaticNameServers")
                or current_active_interface.get("NameServers")
                or []
            )
            requested_dns = normalize_dns_values(shared_dns)
            if current_dns[: len(requested_dns)] == requested_dns:
                job["dns_apply_status"] = "Already correct"
                job["dns_before_values"] = list(current_dns)
                job["dns_applied_values"] = list(current_dns)
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply DNS",
                    9,
                    total,
                    f"[OK] DNS already correct; no change needed for {', '.join(requested_dns)}.",
                )
            else:
                config_changes_attempted = True
                try:
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Apply DNS",
                        9,
                        total,
                        (
                            "[RUNNING] DNS apply attempt | "
                            "source=shared_network.dns_servers | "
                            f"target={active_ip} | values={', '.join(shared_dns)}"
                        )
                    )
                    dns_result = client.set_dns_servers_best_effort(shared_dns)
                    dns_change_applied = bool(dns_result.get("changed"))
                    dns_matched = bool(dns_result.get("matched")) if "matched" in dns_result else bool(dns_result.get("verified"))
                    job["dns_apply_status"] = str(dns_result.get("status") or "Mismatch")
                    dns_before = dns_result.get("before") or {}
                    dns_after = dns_result.get("after") or {}
                    job["dns_before_values"] = list(dns_before.get("StaticNameServers") or dns_before.get("NameServers") or dns_result.get("before_static") or dns_result.get("before_names") or [])
                    job["dns_applied_values"] = list(
                        dns_after.get("StaticNameServers")
                        or dns_after.get("NameServers")
                        or dns_result.get("after_static")
                        or dns_result.get("after_names")
                        or []
                    )
                    current_active_interface["StaticNameServers"] = list(dns_after.get("StaticNameServers") or [])
                    current_active_interface["NameServers"] = list(dns_after.get("NameServers") or [])
                    job["dns_applied_keys"] = list(dns_result.get("applied_keys") or [])
                    job["dns_mismatches"] = list(dns_result.get("mismatches") or [])
                    job["dns_reset_recommended"] = bool(dns_result.get("reset_recommended"))
                    if dns_result.get("reset_recommended"):
                        reset_reasons.append("DNS update requested iLO reset")
                    save_job(kit_name, job)
                    if not dns_matched:
                        config_changes_succeeded = False
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Verify DNS",
                        10,
                        total,
                        (
                            (
                                "[OK] DNS verified and saved on active iLO interface"
                                if dns_matched
                                else "[WARN] DNS write accepted but readback did not match requested values"
                            )
                            + " | "
                            + f"path={dns_result.get('path', '(unknown)')} | "
                            + f"requested={dns_result.get('requested', [])} | "
                            + f"after={dns_result.get('after', {})} | "
                            + f"mismatches={dns_result.get('mismatches', []) or '(none)'} | "
                            + f"reset_recommended={dns_result.get('reset_recommended')} | "
                            + f"notes={dns_result.get('notes', [])}"
                        )
                    )
                except Exception as e:
                    config_changes_succeeded = False
                    job["dns_apply_status"] = "Failed"
                    job["dns_applied_values"] = list(shared_dns)
                    save_job(kit_name, job)
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Apply DNS",
                        10,
                        total,
                        (
                            "[FAILED] DNS apply failed | "
                            "source=shared_network.dns_servers | "
                            f"target={active_ip} | values={', '.join(shared_dns)} | error={e}"
                        )
                    )
        else:
            job["dns_apply_status"] = "Skipped"
            job["dns_applied_values"] = []
            save_job(kit_name, job)
            update_job(
                kit_name,
                job,
                "Running",
                "Skip DNS",
                10,
                total,
                "[SKIP] No shared DNS servers configured."
            )

        if policy_enabled(cfg, "enable_license_check"):
            try:
                update_job(kit_name, job, "Running", "Check iLO license", 11, total, "[RUNNING] Checking iLO license status.")
                license_result = policy_adapter().license_status()
                job["license_status"] = "OK" if license_result.get("ok") else "Warning"
                job["license_warnings"] = list(license_result.get("warnings") or [])
                job["ilo_policy_raw_results"]["license"] = dict(license_result)
                if license_result.get("warnings"):
                    job["ilo_policy_warnings"].extend(list(license_result.get("warnings") or []))
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Check iLO license",
                    11,
                    total,
                    (
                        "[OK] iLO license status is healthy."
                        if license_result.get("ok")
                        else "[WARN] iLO license status is not OK."
                    )
                    + f" | warnings={license_result.get('warnings', []) or '(none)'}",
                )
            except Exception as e:
                job["license_status"] = "Warning"
                job["license_warnings"] = [str(e).splitlines()[0]]
                job["ilo_policy_warnings"].append(f"License check failed: {str(e).splitlines()[0]}")
                save_job(kit_name, job)
                update_job(kit_name, job, "Running", "Check iLO license", 11, total, f"[WARN] iLO license check failed: {str(e).splitlines()[0]}")
        else:
            job["license_status"] = "Skipped"
            save_job(kit_name, job)
            update_job(kit_name, job, "Running", "Check iLO license", 11, total, "[SKIP] iLO license policy is disabled.")

        if policy_enabled(cfg, "enable_ipv6_disable"):
            try:
                update_job(kit_name, job, "Running", "Disable IPv6", 11, total, "[RUNNING] Attempting to disable IPv6 where supported")
                if type(client).configure_ipv6_policy_best_effort is ILO_CLIENT_BASE.configure_ipv6_policy_best_effort and type(client).disable_ipv6_best_effort is not ILO_CLIENT_BASE.disable_ipv6_best_effort:
                    legacy_ipv6 = client.disable_ipv6_best_effort()
                    ipv6_result = {
                        "changed": True,
                        "verified": True,
                        "checks": [],
                        "path": legacy_ipv6.get("path") or "",
                        "reset_recommended": bool(legacy_ipv6.get("reset_recommended")),
                    }
                else:
                    ipv6_result = policy_adapter().apply_ipv6_policy()
                ipv6_change_applied = bool(ipv6_result.get("changed"))
                job["ipv6_policy_status"] = "Verified" if ipv6_result.get("verified") else "Mismatch"
                job["ipv6_policy_checks"] = list(ipv6_result.get("checks") or [])
                job["ilo_policy_raw_results"]["ipv6"] = dict(ipv6_result)
                if ipv6_result.get("reset_recommended"):
                    reset_reasons.append("IPv6 policy requested iLO reset")
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Disable IPv6",
                    11,
                    total,
                    f"[OK] IPv6 hardening via {ipv6_result.get('path', '(unknown)')} | reset_recommended={ipv6_result.get('reset_recommended')} | checks={ipv6_result.get('checks', [])}",
                )
            except Exception as e:
                job["ipv6_policy_status"] = "Failed"
                job["ilo_policy_failures"].append(f"IPv6 policy failed: {str(e).splitlines()[0]}")
                save_job(kit_name, job)
                update_job(kit_name, job, "Running", "Disable IPv6", 11, total, f"[WARN] IPv6 hardening could not be applied: {str(e).splitlines()[0]}")
        else:
            job["ipv6_policy_status"] = "Skipped"
            save_job(kit_name, job)
            update_job(kit_name, job, "Running", "Disable IPv6", 11, total, "[SKIP] IPv6 policy is disabled.")

        if policy_enabled(cfg, "enable_snmp_policy") and active_snmp_user.get("v3_username"):
            try:
                if current_snmp_matches(current_network_protocol):
                    job["snmp_apply_status"] = "Already correct"
                    save_job(kit_name, job)
                    update_job(kit_name, job, "Running", "Harden SNMP", 12, total, "[OK] SNMP already correct; no change needed.")
                else:
                    config_changes_attempted = True
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Harden SNMP",
                        12,
                        total,
                        (
                            "[RUNNING] SNMP apply attempt | "
                            f"target={active_ip} | username={active_snmp_user.get('v3_username', '') or '(none)'} | "
                            f"auth={desired_auth_protocol} | priv={desired_priv_protocol} | "
                            f"auth_secret={'Yes' if active_snmp_user.get('v3_auth_password') else 'No'} | "
                            f"privacy_secret={'Yes' if active_snmp_user.get('v3_priv_password') else 'No'}"
                        ),
                    )
                    if type(client).configure_snmp_policy_best_effort is ILO_CLIENT_BASE.configure_snmp_policy_best_effort and type(client).harden_snmp_best_effort is not ILO_CLIENT_BASE.harden_snmp_best_effort:
                        snmp_result = client.harden_snmp_best_effort(
                            v3_username=active_snmp_user.get("v3_username", ""),
                            v3_auth_protocol=active_snmp_user.get("v3_auth_protocol", "SHA"),
                            v3_auth_password=active_snmp_user.get("v3_auth_password", ""),
                            v3_priv_protocol=active_snmp_user.get("v3_priv_protocol", "AES"),
                            v3_priv_password=active_snmp_user.get("v3_priv_password", ""),
                        )
                    else:
                        snmp_result = policy_adapter().apply_snmp_policy(
                            system_contact=active_snmp_user.get("system_contact", ""),
                            system_location=active_snmp_user.get("system_location", ""),
                            system_role=active_snmp_user.get("system_role", ""),
                            read_community=active_snmp_user.get("read_community", ""),
                            v3_username=active_snmp_user.get("v3_username", ""),
                            v3_auth_protocol=active_snmp_user.get("v3_auth_protocol", "SHA"),
                            v3_auth_password=active_snmp_user.get("v3_auth_password", ""),
                            v3_priv_protocol=active_snmp_user.get("v3_priv_protocol", "AES"),
                            v3_priv_password=active_snmp_user.get("v3_priv_password", ""),
                        )
                    snmp_change_applied = bool(snmp_result.get("changed"))
                    current_network_protocol["SNMP"] = dict(snmp_result.get("after") or current_network_protocol.get("SNMP") or {})
                    job["snmp_apply_status"] = str(snmp_result.get("status") or "Mismatch")
                    job["snmp_applied_keys"] = list(snmp_result.get("applied_keys") or [])
                    job["snmp_verified_checks"] = list(snmp_result.get("verification", {}).get("checks") or [])
                    job["snmp_mismatches"] = list(snmp_result.get("mismatches") or [])
                    job["snmp_reset_recommended"] = bool(snmp_result.get("reset_recommended"))
                    job["ilo_policy_raw_results"]["snmp"] = dict(snmp_result)
                    if snmp_result.get("reset_recommended"):
                        reset_reasons.append("SNMP policy requested iLO reset")
                    save_job(kit_name, job)
                    snmp_matched = bool(snmp_result.get("matched")) if "matched" in snmp_result else bool(snmp_result.get("verified"))
                    if not snmp_matched:
                        config_changes_succeeded = False
                    update_job(
                        kit_name,
                        job,
                        "Running",
                        "Harden SNMP",
                        12,
                        total,
                        (
                            "[OK] SNMP verified after apply"
                            if snmp_matched
                            else "[WARN] SNMP settings partially matched after apply"
                        )
                        + " | "
                        + f"path={snmp_result.get('path', '(unknown)')} | "
                        + f"username={active_snmp_user.get('v3_username', '') or '(none)'} | "
                        + f"active_ip={active_ip} | "
                        + f"auth={desired_auth_protocol} | priv={desired_priv_protocol} | "
                        + f"auth_secret={'Yes' if active_snmp_user.get('v3_auth_password') else 'No'} | "
                        + f"privacy_secret={'Yes' if active_snmp_user.get('v3_priv_password') else 'No'} | "
                        + f"checks={snmp_result.get('verification', {}).get('checks', [])} | "
                        + f"mismatches={snmp_result.get('mismatches', []) or '(none)'} | "
                        + f"reset_recommended={snmp_result.get('reset_recommended')} | "
                        + f"notes={snmp_result.get('notes', [])}",
                    )
            except Exception as e:
                config_changes_succeeded = False
                job["snmp_apply_status"] = "Failed"
                job["ilo_policy_failures"].append(f"SNMP policy failed: {str(e).splitlines()[0]}")
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Harden SNMP",
                    12,
                    total,
                    (
                        "[FAILED] SNMP settings could not be verified after apply | "
                        f"target={active_ip} | username={active_snmp_user.get('v3_username', '') or '(none)'} | "
                        f"auth={desired_auth_protocol} | priv={desired_priv_protocol} | "
                        f"auth_secret={'Yes' if active_snmp_user.get('v3_auth_password') else 'No'} | "
                        f"privacy_secret={'Yes' if active_snmp_user.get('v3_priv_password') else 'No'} | "
                        f"error={e}"
                    ),
                )
        else:
            job["snmp_apply_status"] = "Skipped"
            save_job(kit_name, job)
            update_job(kit_name, job, "Running", "Harden SNMP", 12, total, "[SKIP] SNMP policy is disabled.")

        if policy_enabled(cfg, "enable_alert_destinations") and active_snmp_user.get("alert_destinations"):
            try:
                config_changes_attempted = True
                update_job(kit_name, job, "Running", "Configure SNMP alerts", 13, total, "[RUNNING] Updating SNMP alert destinations.")
                alerts_result = policy_adapter().apply_alert_destinations(
                    destinations=list(active_snmp_user.get("alert_destinations") or []),
                    protocol=str(active_snmp_user.get("alert_protocol") or "SNMPv3Inform"),
                    snmpv3_user=str(active_snmp_user.get("v3_username") or ""),
                )
                alerts_change_applied = bool(alerts_result.get("changed"))
                job["snmp_alert_status"] = str(alerts_result.get("status") or ("Verified" if alerts_result.get("verified") else "Mismatch"))
                job["snmp_alert_results"] = list(alerts_result.get("destinations") or [])
                job["ilo_policy_raw_results"]["alerts"] = dict(alerts_result)
                if alerts_result.get("reset_recommended"):
                    reset_reasons.append("SNMP alert destinations requested iLO reset")
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Configure SNMP alerts",
                    13,
                    total,
                    f"[OK] SNMP alert destinations processed | protocol={active_snmp_user.get('alert_protocol')} | results={alerts_result.get('destinations', [])}",
                )
            except Exception as e:
                job["snmp_alert_status"] = "Failed"
                job["ilo_policy_failures"].append(f"SNMP alert destinations failed: {str(e).splitlines()[0]}")
                save_job(kit_name, job)
                update_job(kit_name, job, "Running", "Configure SNMP alerts", 13, total, f"[WARN] SNMP alert destinations could not be applied: {str(e).splitlines()[0]}")
        else:
            job["snmp_alert_status"] = "Skipped"
            save_job(kit_name, job)
            update_job(kit_name, job, "Running", "Configure SNMP alerts", 13, total, "[SKIP] SNMP alert destination policy is disabled.")

        if policy_enabled(cfg, "enable_time_policy") and standard_policy.get("time", {}).get("server"):
            try:
                config_changes_attempted = True
                update_job(kit_name, job, "Running", "Configure time policy", 13, total, "[RUNNING] Applying SNTP/time policy.")
                time_result = policy_adapter().apply_time_policy(
                    ntp_server=str(standard_policy.get("time", {}).get("server") or ""),
                    timezone=str(standard_policy.get("time", {}).get("timezone") or ""),
                )
                time_change_applied = bool(time_result.get("changed"))
                job["time_policy_status"] = "Verified" if time_result.get("verified") else "Mismatch"
                job["time_policy_notes"] = list(time_result.get("notes") or [])
                job["ilo_policy_raw_results"]["time"] = dict(time_result)
                if time_result.get("reset_recommended"):
                    reset_reasons.append("Time policy requested iLO reset")
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Configure time policy",
                    13,
                    total,
                    f"[OK] Time policy processed | server={standard_policy.get('time', {}).get('server')} | timezone={standard_policy.get('time', {}).get('timezone')} | notes={time_result.get('notes', [])}",
                )
            except Exception as e:
                job["time_policy_status"] = "Failed"
                job["ilo_policy_failures"].append(f"Time policy failed: {str(e).splitlines()[0]}")
                save_job(kit_name, job)
                update_job(kit_name, job, "Running", "Configure time policy", 13, total, f"[WARN] Time policy could not be applied: {str(e).splitlines()[0]}")
        else:
            job["time_policy_status"] = "Skipped"
            save_job(kit_name, job)
            update_job(kit_name, job, "Running", "Configure time policy", 13, total, "[SKIP] Time policy is disabled or missing a gateway-backed server.")

        if local_accounts_to_apply:
            try:
                config_changes_attempted = True
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply local users",
                    13,
                    total,
                    f"[RUNNING] Ensuring local iLO users: {', '.join([item.get('username', '') for item in local_accounts_to_apply])}",
                )
                accounts_result = client.ensure_local_accounts_best_effort(local_accounts_to_apply)
                local_users_change_applied = any(item.get("changed") for item in accounts_result.get("results") or [])
                job["local_account_status"] = str(accounts_result.get("status") or "Mismatch")
                job["local_account_results"] = list(accounts_result.get("results") or [])
                job["ilo_policy_raw_results"]["accounts"] = dict(accounts_result)
                if accounts_result.get("reset_recommended"):
                    reset_reasons.append("Local account policy requested iLO reset")
                save_job(kit_name, job)
                if not accounts_result.get("matched"):
                    config_changes_succeeded = False
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply local users",
                    13,
                    total,
                    (
                        "[OK] Local iLO users verified"
                        if accounts_result.get("matched")
                        else "[WARN] Local iLO users did not fully verify"
                    )
                    + f" | path={accounts_result.get('path', '(unknown)')} | results={accounts_result.get('results', [])}",
                )
            except Exception as e:
                config_changes_succeeded = False
                job["local_account_status"] = "Failed"
                job["ilo_policy_failures"].append(f"Local account policy failed: {str(e).splitlines()[0]}")
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply local users",
                    13,
                    total,
                    f"[FAILED] Local iLO users could not be applied: {str(e).splitlines()[0]}",
                )
        else:
            job["local_account_status"] = "Skipped"
            save_job(kit_name, job)
            update_job(
                kit_name,
                job,
                "Running",
                "Apply local users",
                13,
                total,
                "[SKIP] No local iLO user policy changes were requested.",
            )

        storage_result = None
        if endpoint_transition_pending:
            update_job(
                kit_name,
                job,
                "Running",
                "Finish iLO stage",
                14,
                total,
                (
                "[INFO] Final iLO target verification is still pending. "
                f"Current control session={active_ip} | expected_final_ip={expected_final_ip}"
            ),
        )
        change_summary = {
            "ipv4": "changed" if ip_change_applied else ("already-correct" if target_ip and desired_subnet_mask and desired_gateway else "not-requested"),
            "hostname": "changed" if hostname_change_applied else ("already-correct" if desired_hostname else "not-requested"),
            "dns": "changed" if dns_change_applied else ("already-correct" if shared_dns else "not-requested"),
            "snmp": "changed" if snmp_change_applied else ("already-correct" if policy_enabled(cfg, "enable_snmp_policy") and active_snmp_user.get("v3_username") else "not-requested"),
            "snmp_alerts": "changed" if alerts_change_applied else ("already-correct" if policy_enabled(cfg, "enable_alert_destinations") else "not-requested"),
            "ipv6": "changed" if ipv6_change_applied else ("already-correct" if policy_enabled(cfg, "enable_ipv6_disable") else "not-requested"),
            "time": "changed" if time_change_applied else ("already-correct" if policy_enabled(cfg, "enable_time_policy") else "not-requested"),
            "local_users": "changed" if local_users_change_applied else ("already-correct" if local_accounts_to_apply else "not-requested"),
        }
        job["ilo_change_summary"] = dict(change_summary)
        job["ilo_policy_applied"] = [
            name
            for name, changed in (
                ("ipv4", ip_change_applied),
                ("hostname", hostname_change_applied),
                ("dns", dns_change_applied),
                ("snmp", snmp_change_applied),
                ("snmp_alerts", alerts_change_applied),
                ("ipv6", ipv6_change_applied),
                ("time", time_change_applied),
                ("local_users", local_users_change_applied),
            )
            if changed
        ]
        update_job(
            kit_name,
            job,
            "Running",
            "Finish iLO stage",
            14,
            total,
            (
                "[INFO] Change summary | "
                f"IPv4={change_summary['ipv4']} | "
                f"Hostname={change_summary['hostname']} | "
                f"DNS={change_summary['dns']} | "
                f"SNMP={change_summary['snmp']} | "
                f"SNMP alerts={change_summary['snmp_alerts']} | "
                f"IPv6={change_summary['ipv6']} | "
                f"Time={change_summary['time']} | "
                f"Local users={change_summary['local_users']}"
            ),
        )
        if ip_change_applied:
            reset_reasons.insert(0, "iLO IP changed")
        reset_recommended = bool(reset_reasons)
        if not policy_settings.get("enable_auto_reset"):
            job["ilo_reset_reason"] = (
                "reset-worthy iLO changes were detected, but automatic iLO reset is disabled"
                if reset_recommended
                else "no reset-worthy iLO change was applied"
            )
        else:
            job["ilo_reset_reason"] = ", ".join(dict.fromkeys(reset_reasons)) if reset_recommended else "no reset-worthy iLO change was applied"
        update_job(
            kit_name,
            job,
            "Running",
            "Finish iLO stage",
            14,
            total,
            f"[INFO] Reset decision | required={'yes' if reset_recommended else 'no'} | reason={job['ilo_reset_reason']}",
        )

        if config_changes_attempted and not config_changes_succeeded:
            job["ilo_reset_required"] = False
            job["ilo_reset_status"] = "Not requested"
            save_job(kit_name, job)
            update_job(
                kit_name,
                job,
                "Failed",
                "Finish iLO stage",
                14,
                total,
                (
                    "[FAILED] Real run finished with iLO config failures. "
                    f"DNS={job.get('dns_apply_status')} | SNMP={job.get('snmp_apply_status')} | "
                    f"local_accounts={job.get('local_account_status')}. Review the iLO stage logs before retrying."
                )
            )
            update_job(
                kit_name,
                job,
                "Failed",
                "Finish iLO stage",
                14,
                total,
                "[SKIP] Storage and later stages were blocked because the iLO stage did not finish.",
            )
            return

        if config_changes_attempted and reset_recommended and policy_settings.get("enable_auto_reset"):
            job["ilo_reset_required"] = True
            job["ilo_reset_status"] = "Requested"
            save_job(kit_name, job)
            try:
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Reset iLO",
                    14,
                    total,
                    "[RUNNING] iLO reset is required before the next stage can start.",
                )
                reset_result = client.reset_ilo()
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Reset iLO",
                    14,
                    total,
                    f"[OK] iLO reset requested via {reset_result.get('path')} ({reset_result.get('reset_type')}).",
                )
                reset_wait_result = wait_for_ilo_reset_completion(expected_final_ip, stage_name="Wait for iLO reset", step_index=14)
                if not reset_wait_result.get("matched"):
                    job["ilo_reset_status"] = "Failed"
                    save_job(kit_name, job)
                    update_job(
                        kit_name,
                        job,
                        "Failed",
                        "Wait for iLO reset",
                        14,
                        total,
                        (
                            "[FAILED] iLO reset was requested but completion was not verified | "
                            f"interrupt_observed={reset_wait_result.get('interrupt_observed')} | "
                            f"interrupt_detail={reset_wait_result.get('interrupt_detail') or '(none)'} | "
                            f"return_detail={reset_wait_result.get('return_detail') or '(none)'}"
                        )
                    )
                    update_job(
                        kit_name,
                        job,
                        "Failed",
                        "Wait for iLO reset",
                        14,
                        total,
                        "[SKIP] Storage and later stages were blocked because the iLO stage did not finish.",
                    )
                    return
                ip_check = verify_active_ip_state(expected_final_ip, stage_name="Verify iLO final state", step_index=14)
                if target_ip and not ip_check.get("matched"):
                    job["ilo_reset_status"] = "Failed"
                    save_job(kit_name, job)
                    update_job(
                        kit_name,
                        job,
                        "Failed",
                        "Verify iLO final state",
                        14,
                        total,
                        f"[FAILED] iLO came back after reset, but the final IP did not read back as {expected_final_ip}.",
                    )
                    update_job(
                        kit_name,
                        job,
                        "Failed",
                        "Verify iLO final state",
                        14,
                        total,
                        "[SKIP] Storage and later stages were blocked because the iLO stage did not finish.",
                    )
                    return
                final_verification = verify_final_ilo_configuration(stage_name="Verify iLO final state", step_index=14)
                if not final_verification.get("matched"):
                    job["ilo_reset_status"] = "Failed"
                    save_job(kit_name, job)
                    update_job(
                        kit_name,
                        job,
                        "Failed",
                        "Verify iLO final state",
                        14,
                        total,
                        "[FAILED] iLO came back, but the final hostname, DNS, or SNMP state did not fully verify after reset.",
                    )
                    update_job(
                        kit_name,
                        job,
                        "Failed",
                        "Verify iLO final state",
                        14,
                        total,
                        "[SKIP] Storage and later stages were blocked because the iLO stage did not finish.",
                    )
                    return
                job["ilo_reset_status"] = "Completed"
                job["ilo_final_ip_verified"] = True
                job["ilo_stage_finished"] = True
                promote_final_ilo_endpoint(cfg, expected_final_ip)
                save_kit_config(cfg)
                save_job(kit_name, job)
                ilo_stage_finished = True
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Finish iLO stage",
                    14,
                    total,
                    f"[OK] iLO reset completed and the final iLO endpoint is reachable on {expected_final_ip}.",
                )
            except Exception as e:
                job["ilo_reset_status"] = "Failed"
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Reset iLO",
                    14,
                    total,
                    f"[FAILED] iLO reset failed after successful config changes: {str(e).splitlines()[0]}",
                )
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Reset iLO",
                    14,
                    total,
                    "[SKIP] Storage and later stages were blocked because the iLO stage did not finish.",
                )
                return
        else:
            if reset_recommended and not policy_settings.get("enable_auto_reset"):
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Finish iLO stage",
                    14,
                    total,
                    "[WARN] Reset-worthy iLO changes were detected, but automatic iLO reset is disabled in policy.",
                )
            ip_check = verify_active_ip_state(expected_final_ip, stage_name="Finish iLO stage", step_index=14)
            if target_ip and not ip_check.get("matched"):
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Finish iLO stage",
                    14,
                    total,
                    f"[FAILED] iLO changes were applied, but the final IP did not read back as {expected_final_ip}.",
                )
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Finish iLO stage",
                    14,
                    total,
                    "[SKIP] Storage and later stages were blocked because the iLO stage did not finish.",
                )
                return
            final_verification = verify_final_ilo_configuration(stage_name="Finish iLO stage", step_index=14)
            if not final_verification.get("matched"):
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Finish iLO stage",
                    14,
                    total,
                    "[FAILED] iLO changes were applied, but the final hostname, DNS, or SNMP state did not fully verify.",
                )
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Finish iLO stage",
                    14,
                    total,
                    "[SKIP] Storage and later stages were blocked because the iLO stage did not finish.",
                )
                return
            job["ilo_reset_required"] = False
            job["ilo_reset_status"] = "Not required"
            job["ilo_final_ip_verified"] = bool(expected_final_ip)
            job["ilo_stage_finished"] = True
            promote_final_ilo_endpoint(cfg, expected_final_ip)
            save_kit_config(cfg)
            save_job(kit_name, job)
            ilo_stage_finished = True
            update_job(
                kit_name,
                job,
                "Running",
                "Finish iLO stage",
                14,
                total,
                "[OK] iLO stage finished and no separate iLO reset was needed.",
            )

        if storage_execution.get("included"):
            if not ilo_stage_finished:
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Run storage stage",
                    15,
                    total,
                    "[SKIP] Storage stage was blocked because the iLO stage did not finish.",
                )
                return
            update_job(
                kit_name,
                job,
                "Running",
                "Run storage stage",
                15,
                total,
                "[RUNNING] Starting the approved storage stage after the iLO stage finished.",
            )
            storage_result = run_storage_as_part_of_real_run(
                cfg,
                client,
                login_ip,
                active_ip,
                storage_execution,
                kit_name,
                job,
                15,
                total,
            )
            job["storage_server_reboot_required"] = bool(storage_result["apply_state"].get("reboot_required"))
            job["storage_server_reboot_status"] = (
                "Completed"
                if storage_result["apply_state"].get("workflow_state") == "post_reboot_validation_complete"
                else "Required"
                if storage_result["apply_state"].get("reboot_required")
                else "Not required"
            )
            save_job(kit_name, job)

        update_job(
            kit_name,
            job,
            "Completed",
            "Finished",
            total,
            total,
            (
                "[DONE] Real run finished. "
                f"iLO reset status={job.get('ilo_reset_status')} | "
                f"iLO final IP verified={job.get('ilo_final_ip_verified')} | "
                f"Storage server reboot status={job.get('storage_server_reboot_status')} | "
                f"DNS={job.get('dns_apply_status')} | SNMP={job.get('snmp_apply_status')} | "
                f"Local users={job.get('local_account_status')}"
            )
        )
    except ILOError as e:
        current_stage_text = str(job.get("current_stage") or "").lower()
        failed_stage = "iLO error"
        if "reboot" in current_stage_text and ("storage" in str(job.get("scope") or "").lower() or job.get("storage_run_directory") or job.get("apply_path")):
            failed_stage = "Storage reboot wait failed"
        elif "storage" in current_stage_text:
            failed_stage = "Storage error"
        elif "esxi" in current_stage_text:
            failed_stage = "ESXi error"
        if getattr(e, "power_reset_details", None):
            expected_state = str((getattr(e, "power_reset_details", {}) or {}).get("expected_power_state") or "")
            job["diagnosis"] = power_failure_diagnosis(failed_stage, expected_state or "unknown", e)
            save_job(kit_name, job)
        update_job(kit_name, job, "Failed", failed_stage, job.get("completed_steps", 0), total, f"[FAILED] {e}")
    except Exception as e:
        update_job(kit_name, job, "Failed", "Unexpected error", job.get("completed_steps", 0), total, f"[FAILED] Unexpected error: {e}")

def run_job_simulation(cfg: dict, scope: str):
    kit_name = cfg["site"]["name"]
    steps = get_steps_for_scope(cfg, scope)
    total = len(steps)
    job = {
        "status": "Preview running",
        "execution_mode": "preview",
        "execution_mode_label": "Preview / safety mode",
        "scope": scope,
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total,
        "logs": [],
        "root_scope": scope,
        "stage_statuses": initialize_stage_statuses(scope, cfg),
    }
    job = carry_forward_job_bundle_metadata(kit_name, job)
    save_job(kit_name, job)

    for idx, step in enumerate(steps, start=1):
        job["current_stage"] = step
        job["completed_steps"] = idx - 1
        job["progress_percent"] = int(((idx - 1) / total) * 100)
        job["logs"].append(f"[PREVIEW] {step}")
        save_job(kit_name, job)
        time.sleep(0.2)

    job["status"] = "Preview complete"
    job["current_stage"] = "Ready for real execution"
    job["completed_steps"] = total
    job["progress_percent"] = 100
    job["logs"].append("[DONE] Preview complete. No real changes were made.")
    for token in run_center_scope_keys(scope, cfg):
        set_stage_status(job, token, "completed")
    save_job(kit_name, job)


def execute_preview_job_in_background(cfg: dict, scope: str):
    kit_name = cfg["site"]["name"]
    try:
        run_job_simulation(cfg, scope)
    except Exception as e:
        save_job(
            kit_name,
            {
                "status": "Failed",
                "scope": scope,
                "execution_mode": "preview",
                "execution_mode_label": "Preview / safety mode",
                "current_stage": "Unexpected error",
                "progress_percent": 0,
                "completed_steps": 0,
                "total_steps": 0,
                "logs": [f"[FAILED] Unexpected preview error: {e}"],
            },
        )
    finally:
        append_job_history_snapshot(cfg, scope)


def render_page(
    request: Request,
    cfg: dict,
    active_page: str = "dashboard",
    message: str | None = None,
    error_message: str | None = None,
    execution_preview: str | None = None,
    execution_review: dict | None = None,
    confirm_scope: str | None = None,
    config_view_title: str | None = None,
    config_view_content: str | None = None,
    action_feedback: dict | None = None,
    storage_discovery: dict | None = None,
    storage_export_paths: dict[str, Path] | None = None,
    storage_plan: dict | None = None,
    storage_plan_paths: dict[str, Path] | None = None,
    storage_apply_paths: dict[str, Path] | None = None,
    storage_repair_action: dict[str, str] | None = None,
    extra_context: dict[str, Any] | None = None,
):
    active_page = normalize_page_name(active_page)
    page_meta = PAGE_META[active_page]
    job = load_job(cfg["site"]["name"])
    history = load_history(cfg["site"]["name"])
    latest_ilo_history = latest_history_entry_for_scope(history, ["ilo"]) or {}
    storage_workflow_state = load_storage_workflow_state(storage_apply_paths)
    storage_review = build_storage_review_context(cfg)
    storage_target = resolve_storage_target_host(cfg)
    storage_credentials = resolve_storage_target_credentials(cfg)
    storage_execution_status = build_storage_execution_status(cfg)
    if not storage_discovery and not storage_export_paths:
        storage_cfg = ensure_storage_config(cfg)
        latest_raw_path = str(storage_cfg.get("latest_discovery_raw_path") or "").strip()
        latest_plan_path = str(storage_cfg.get("latest_plan_path") or "").strip()
        if latest_raw_path or latest_plan_path:
            try:
                restored_discovery, restored_export_paths, restored_plan, restored_plan_paths = restore_storage_page_state(
                    discovery_raw_path=latest_raw_path,
                    raid_plan_path=latest_plan_path,
                    expected_host=str(storage_target.get("resolved") or ""),
                )
                storage_discovery = restored_discovery or storage_discovery
                storage_export_paths = restored_export_paths or storage_export_paths
                storage_plan = restored_plan or storage_plan
                storage_plan_paths = restored_plan_paths or storage_plan_paths
            except Exception:
                pass
    storage_discovery_summary = (storage_discovery or {}).get("summary", storage_discovery) if storage_discovery else None
    storage_controller_capabilities = build_storage_controller_capabilities(storage_discovery) if storage_discovery else []
    storage_planning_drives = build_storage_planning_drives(storage_discovery_summary)
    storage_display_controller = select_primary_storage_controller(storage_discovery_summary)
    storage_controller_choices = build_storage_controller_choices(storage_discovery_summary)
    storage_display_drives = build_storage_display_drives(storage_discovery_summary)
    storage_plan_defaults = storage_plan
    if not storage_plan_defaults and storage_discovery and storage_export_paths:
        try:
            storage_plan_defaults = build_raid_plan(
                {"summary": storage_discovery, "raw": {"source_host": storage_target.get("resolved", "")}},
                storage_export_paths,
            )
        except Exception:
            storage_plan_defaults = None
    workflow_contexts = build_workflow_contexts(cfg, job, history)
    if storage_workflow_state:
        workflow_state = storage_workflow_state.get("workflow_state", "")
        if workflow_state in {"staged_reboot_required", "reboot_requested", "waiting_for_reboot_start", "waiting_for_server_return"}:
            workflow_contexts["storage"]["state"] = "waiting_for_restart"
        elif workflow_state in {"post_reboot_validation_pending"}:
            workflow_contexts["storage"]["state"] = "validating"
        elif workflow_state in {"post_reboot_validation_complete", "apply_complete"}:
            workflow_contexts["storage"]["state"] = "complete"
        elif workflow_state in {"apply_failed", "reboot_failed"}:
            workflow_contexts["storage"]["state"] = "failed"
        elif workflow_state not in {"", "idle"}:
            workflow_contexts["storage"]["state"] = "running"
        storage_ui = workflow_state_ui(workflow_contexts["storage"]["state"])
        workflow_contexts["storage"]["state_label"] = storage_ui["label"]
        workflow_contexts["storage"]["tone"] = storage_ui["tone"]
        workflow_contexts["storage"]["result_summary"] = storage_workflow_state.get("workflow_summary") or workflow_contexts["storage"]["result_summary"]
    recommended_next_step = build_recommended_next_step(cfg, workflow_contexts)
    setup_precheck_summary = build_setup_precheck_summary(cfg, workflow_contexts, recommended_next_step)
    page_precheck_summary = build_page_precheck_summary(active_page, cfg, workflow_contexts)
    activity_feed = build_activity_feed(history)
    history_display = build_history_display_entries(history)
    dashboard_job_status = build_dashboard_job_status(history)
    dashboard_overview = build_dashboard_overview(cfg, setup_precheck_summary, workflow_contexts, dashboard_job_status, job)
    hardware_identity = build_hardware_identity(cfg)
    upgrade_helper_summary = build_upgrade_helper_card(cfg)
    ilo_input_review = build_ilo_input_review(cfg, include_policy_validation=active_page == "ilo")
    snmp_input_review = build_snmp_input_review(cfg)
    ilo_field_errors = build_ilo_field_errors(cfg)
    snmp_field_errors = build_snmp_field_errors(cfg)
    ilo_advanced_profile = build_ilo_advanced_profile(cfg)
    ilo_latest_receipt = latest_scope_receipt(cfg, history, ["ilo"])
    storage_latest_receipt = latest_scope_receipt(cfg, history, ["storage-apply", "storage-reboot"])
    esxi_latest_receipt = latest_scope_receipt(cfg, history, ["esxi"])
    storage_page_readiness = build_storage_page_readiness(storage_review, storage_target, storage_credentials, storage_execution_status, storage_export_paths)
    storage_change_summary = build_storage_change_summary(storage_review, storage_plan)
    esxi_page_review = build_esxi_page_review(cfg)
    esxi_advanced_profile = build_esxi_advanced_profile(cfg, esxi_page_review)
    live_job_story = build_live_job_story(job)
    live_stage_cards = build_live_stage_cards(job)
    run_checklist = build_run_checklist(job, cfg)
    report_center = build_report_center(
        cfg,
        query=str(request.query_params.get("report_query", "") or ""),
        report_type=str(request.query_params.get("report_type", "all") or "all"),
    )
    selected_run_scopes = ["included"] if not confirm_scope else (run_center_scope_keys(confirm_scope, cfg) or ([confirm_scope] if confirm_scope == "included" else []))
    ilo_inclusion = component_inclusion_status(cfg, "ilo")
    esxi_inclusion = component_inclusion_status(cfg, "esxi")
    windows_inclusion = component_inclusion_status(cfg, "windows")
    qnap_inclusion = component_inclusion_status(cfg, "qnap")
    if action_feedback is None:
        if error_message:
            action_feedback = build_action_feedback(
                "Needs attention",
                error_message,
                tone="pending",
                status_label="Warning",
            )
        elif message:
            action_feedback = build_action_feedback(
                "Update complete",
                message,
                tone="ready",
                status_label="Done",
            )

    context = {
        "title": page_meta["title"],
        "page_subtitle": page_meta["subtitle"],
        "active_page": active_page,
        "cards": build_cards(),
        "cfg": cfg,
        "kits": list_kits(),
        "current_kit": cfg.get("site", {}).get("name", ""),
        "message": message,
        "error_message": error_message,
        "execution_preview": execution_preview,
        "execution_review": execution_review,
        "confirm_scope": confirm_scope,
        "selected_run_scopes": selected_run_scopes,
        "config_view_title": config_view_title,
        "config_view_content": config_view_content,
        "action_feedback": action_feedback,
        "storage_discovery": storage_discovery_summary,
        "storage_discovery_full": storage_discovery,
        "storage_discovery_summary": storage_discovery_summary,
        "storage_export_paths": storage_export_paths,
        "storage_plan": storage_plan,
        "storage_plan_paths": storage_plan_paths,
        "storage_apply_paths": storage_apply_paths,
        "storage_repair_action": storage_repair_action or {},
        "storage_workflow_state": storage_workflow_state,
        "storage_review": storage_review,
        "storage_target": storage_target,
        "storage_credentials": storage_credentials,
        "storage_execution_status": storage_execution_status,
        "storage_planning_drives": storage_planning_drives,
        "storage_controller_capabilities": storage_controller_capabilities,
        "storage_display_controller": storage_display_controller,
        "storage_controller_choices": storage_controller_choices,
        "storage_display_drives": storage_display_drives,
        "storage_plan_defaults": storage_plan_defaults,
        "workflow_contexts": workflow_contexts,
        "recommended_next_step": recommended_next_step,
        "setup_precheck_summary": setup_precheck_summary,
        "page_precheck_summary": page_precheck_summary,
        "dashboard_overview": dashboard_overview,
        "upgrade_helper_summary": upgrade_helper_summary,
        "activity_feed": activity_feed,
        "history_display": history_display,
        "dashboard_job_status": dashboard_job_status,
        "hardware_identity": hardware_identity,
        "ilo_input_review": ilo_input_review,
        "ilo_field_errors": ilo_field_errors,
        "snmp_input_review": snmp_input_review,
        "snmp_field_errors": snmp_field_errors,
        "ilo_advanced_profile": ilo_advanced_profile,
        "ilo_latest_receipt": ilo_latest_receipt,
        "storage_latest_receipt": storage_latest_receipt,
        "esxi_latest_receipt": esxi_latest_receipt,
        "storage_page_readiness": storage_page_readiness,
        "storage_change_summary": storage_change_summary,
        "esxi_page_review": esxi_page_review,
        "esxi_field_errors": build_esxi_field_errors(cfg),
        "esxi_advanced_profile": esxi_advanced_profile,
        "live_job_story": live_job_story,
        "live_stage_cards": live_stage_cards,
        "run_checklist": run_checklist,
        "report_center": report_center,
        "ilo_inclusion": ilo_inclusion,
        "esxi_inclusion": esxi_inclusion,
        "windows_inclusion": windows_inclusion,
        "qnap_inclusion": qnap_inclusion,
        "job": job,
        "history": history,
        "latest_ilo_history": latest_ilo_history,
        "section_states": summarize_section_states(cfg),
        "module_navigation": list(getattr(app.state, "module_navigation", []) or []),
    }
    if extra_context:
        context.update(dict(extra_context))

    # HTMX requests should only replace the main content region, never the app shell.
    template_name = MAIN_CONTENT_TEMPLATE if request.headers.get("HX-Request") == "true" else PAGE_TEMPLATE

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=context,
    )


def page_name_from_request_path(path: str) -> str:
    first = (path or "/").strip("/").split("/", 1)[0].replace("-", "_")
    route_map = {
        "": "dashboard",
        "global_settings": "global_settings",
        "save_global_settings": "global_settings",
        "save_config": "configuration",
        "save_ilo_settings": "ilo",
        "export_ilo_inventory": "ilo",
        "save_storage_target": "storage",
        "read_current_storage": "storage",
        "probe_storage_capabilities": "storage",
        "plan_raid_layout": "storage",
        "approve_storage_plan": "storage",
        "clear_storage_approval": "storage",
        "apply_storage_layout": "storage",
        "reboot_storage_now": "storage",
        "save_esxi_settings": "esxi",
        "save_windows_settings": "windows",
        "save_qnap_settings": "qnap",
        "prepare_execute": "execution",
        "execute": "execution",
        "configs": "configs",
        "reports": "configs",
        "history": "history",
        "kits": "dashboard",
    }
    return normalize_page_name(route_map.get(first, first))


@app.exception_handler(Exception)
async def global_http_exception_handler(request: Request, exc: Exception):
    error_text = str(exc).splitlines()[0] or exc.__class__.__name__
    print(f"[ERROR] Unhandled request error on {request.url.path}: {exc!r}")
    try:
        cfg = load_kit_config()
        response = render_page(
            request,
            cfg,
            active_page=page_name_from_request_path(request.url.path),
            error_message=f"The app hit an unexpected error: {error_text}",
        )
        response.status_code = 500
        return response
    except Exception as render_exc:
        print(f"[ERROR] Could not render error page: {render_exc!r}")
        return HTMLResponse(
            f"<div id='main-content'><section class='global-warning-popup' role='alert'>"
            f"<div><strong>Warning: something went wrong</strong></div>"
            f"<div>The app hit an unexpected error: {error_text}</div>"
            f"</section></div>",
            status_code=500,
        )


@app.websocket("/ws/job/{kit_name}")
async def websocket_job_stream(websocket: WebSocket, kit_name: str):
    await websocket.accept()
    kit_name = sanitize_kit_name(kit_name)
    last_payload = None

    try:
        while True:
            job = load_job(kit_name)
            payload = yaml.safe_dump(job, sort_keys=False)
            if payload != last_payload:
                await websocket.send_text(payload)
                last_payload = payload
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return


class ReactFormAdapter(dict):
    def getlist(self, key: str) -> list[Any]:
        value = self.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]


def react_ui_page_specs() -> list[dict[str, str]]:
    return [
        {"key": "dashboard", "label": "Dashboard", "group": "Overview", "legacy_href": "/dashboard"},
        {"key": "global_settings", "label": "Global Settings", "group": "Overview", "legacy_href": "/global-settings"},
        {"key": "upgrade_helper", "label": "Upgrade Helper", "group": "Overview", "legacy_href": "/upgrade-helper"},
        {"key": "ilo", "label": "iLO setup", "group": "Setup Modules", "legacy_href": "/ilo"},
        {"key": "storage", "label": "Storage setup", "group": "Setup Modules", "legacy_href": "/storage"},
        {"key": "esxi", "label": "ESXi setup", "group": "Setup Modules", "legacy_href": "/esxi"},
        {"key": "windows", "label": "Windows setup", "group": "Setup Modules", "legacy_href": "/windows"},
        {"key": "ovf_templates", "label": "OVF Templates", "group": "Setup Modules", "legacy_href": "/modules/ovf-templates"},
        {"key": "qnap", "label": "QNAP setup", "group": "Setup Modules", "legacy_href": "/qnap"},
        {"key": "netapp", "label": "NetApp setup", "group": "Setup Modules", "legacy_href": "/modules/netapp"},
        {"key": "cisco", "label": "Cisco setup", "group": "Setup Modules", "legacy_href": "/cisco"},
        {"key": "execution", "label": "Run Center", "group": "Run", "legacy_href": "/execution"},
        {"key": "configuration", "label": "Configuration / Kits", "group": "Manage", "legacy_href": "/configuration"},
        {"key": "reports", "label": "Reports / History", "group": "Manage", "legacy_href": "/configs"},
        {"key": "action-map", "label": "Action catalog", "group": "Manage", "legacy_href": "/configuration"},
        {"key": "technical", "label": "Technical details", "group": "Manage", "legacy_href": "/configs"},
    ]


def react_ui_action_inventory() -> dict[str, list[dict[str, str]]]:
    return {
        "dashboard": [
            {"label": "Load kit library", "method": "GET", "route": "/api/ui/kits", "mode": "json"},
            {"label": "Switch active kit", "method": "POST", "route": "/api/ui/kits/load", "mode": "json"},
            {"label": "Load existing kit", "method": "POST", "route": "/load-kit", "mode": "legacy-html"},
            {"label": "Create kit", "method": "POST", "route": "/api/ui/kits/create", "mode": "json"},
            {"label": "Open current config", "method": "POST", "route": "/view-current-kit-config", "mode": "legacy-html"},
            {"label": "Download current config", "method": "POST", "route": "/download-current-kit-config", "mode": "download"},
            {"label": "Open next step", "method": "GET", "route": "/execution", "mode": "legacy-html"},
            {"label": "Open Run Center", "method": "GET", "route": "/execution", "mode": "legacy-html"},
            {"label": "Prepare run review", "method": "POST", "route": "/prepare-execute", "mode": "legacy-html"},
            {"label": "Start preview run", "method": "POST", "route": "/execute-preview", "mode": "legacy-html"},
            {"label": "Start real run", "method": "POST", "route": "/execute", "mode": "legacy-html"},
            {"label": "Retry storage stage", "method": "POST", "route": "/retry-storage-stage", "mode": "legacy-html"},
        ],
        "global_settings": [
            {"label": "Load global settings", "method": "GET", "route": "/api/ui/global-settings", "mode": "json"},
            {"label": "Save global settings", "method": "POST", "route": "/api/ui/global-settings", "mode": "json"},
            {"label": "Autofill IP plan", "method": "POST", "route": "/api/ui/global-settings/autofill", "mode": "json"},
            {"label": "Open global settings", "method": "GET", "route": "/global-settings", "mode": "legacy-html"},
            {"label": "Save global settings HTML action", "method": "POST", "route": "/save-global-settings", "mode": "legacy-html"},
            {"label": "Autofill IP plan HTML action", "method": "POST", "route": "/autofill-ip-plan", "mode": "legacy-html"},
            {"label": "Save upgrade policies", "method": "POST", "route": "/save-upgrade-policies", "mode": "legacy-html"},
            {"label": "Upload firmware media", "method": "POST", "route": "/upload-upgrade-media", "mode": "legacy-html"},
        ],
        "upgrade_helper": [
            {"label": "Open upgrade helper", "method": "GET", "route": "/upgrade-helper", "mode": "legacy-html"},
            {"label": "Save policies", "method": "POST", "route": "/save-upgrade-policies", "mode": "legacy-html"},
            {"label": "Upload firmware media", "method": "POST", "route": "/upload-upgrade-media", "mode": "legacy-html"},
            {"label": "Review Cisco upgrade plan", "method": "POST", "route": "/modules/cisco/plan-upgrade", "mode": "legacy-html"},
            {"label": "Run Cisco upgrade", "method": "POST", "route": "/modules/cisco/run-upgrade", "mode": "legacy-html"},
            {"label": "Read Cisco version", "method": "POST", "route": "/modules/cisco/discover-version", "mode": "legacy-html"},
            {"label": "Review ONTAP upgrade plan", "method": "POST", "route": "/modules/netapp/plan-upgrade", "mode": "legacy-html"},
            {"label": "Run ONTAP upgrade", "method": "POST", "route": "/modules/netapp/run-upgrade", "mode": "legacy-html"},
            {"label": "Plan iLO upgrade", "method": "POST", "route": "/plan-ilo-upgrade", "mode": "legacy-html"},
            {"label": "Run iLO upgrade", "method": "POST", "route": "/run-ilo-upgrade", "mode": "legacy-html"},
            {"label": "Open iLO", "method": "GET", "route": "/ilo", "mode": "legacy-html"},
        ],
        "ilo": [
            {"label": "Load iLO state", "method": "GET", "route": "/api/ui/ilo", "mode": "json"},
            {"label": "Save iLO setup", "method": "POST", "route": "/api/ui/ilo/settings", "mode": "json"},
            {"label": "Setup iLO IP", "method": "POST", "route": "/api/ui/ilo/setup-ip", "mode": "json"},
            {"label": "Save iLO setup HTML action", "method": "POST", "route": "/save-ilo-settings", "mode": "legacy-html"},
            {"label": "Export iLO config", "method": "POST", "route": "/export-ilo-config", "mode": "legacy-html"},
            {"label": "Read current iLO", "method": "POST", "route": "/export-ilo-inventory", "mode": "legacy-html"},
            {"label": "View iLO config snapshot", "method": "POST", "route": "/view-ilo-config-snapshot", "mode": "legacy-html"},
            {"label": "Plan iLO firmware upgrade", "method": "POST", "route": "/plan-ilo-upgrade", "mode": "legacy-html"},
            {"label": "Run iLO firmware upgrade", "method": "POST", "route": "/run-ilo-upgrade", "mode": "legacy-html"},
            {"label": "iLO upgrade activity", "method": "GET", "route": "/ilo-upgrade-activity", "mode": "legacy-html"},
            {"label": "Open storage setup", "method": "GET", "route": "/storage", "mode": "legacy-html"},
        ],
        "esxi": [
            {"label": "Save ESXi setup", "method": "POST", "route": "/save-esxi-settings", "mode": "legacy-html"},
            {"label": "Prepare ESXi run", "method": "POST", "route": "/prepare-execute", "mode": "legacy-html"},
            {"label": "Preview ESXi run", "method": "POST", "route": "/execute-preview", "mode": "legacy-html"},
            {"label": "Start ESXi run", "method": "POST", "route": "/execute", "mode": "legacy-html"},
        ],
        "netapp": [
            {"label": "Open NetApp setup", "method": "GET", "route": "/modules/netapp", "mode": "legacy-html"},
            {"label": "Module status", "method": "GET", "route": "/modules/netapp/status", "mode": "json"},
            {"label": "Save NetApp setup", "method": "POST", "route": "/modules/netapp/save-settings", "mode": "legacy-html"},
            {"label": "Test ONTAP API", "method": "POST", "route": "/modules/netapp/test-connection", "mode": "legacy-html"},
            {"label": "Read current ONTAP", "method": "POST", "route": "/modules/netapp/read-current-config", "mode": "legacy-html"},
            {"label": "Read current NetApp config", "method": "POST", "route": "/modules/netapp/read-current-config", "mode": "legacy-html"},
            {"label": "Discover NetApp page", "method": "POST", "route": "/modules/netapp/discover-page", "mode": "legacy-html"},
            {"label": "Discover NetApp console", "method": "POST", "route": "/modules/netapp/discover-console", "mode": "legacy-html"},
            {"label": "Check console ports", "method": "POST", "route": "/modules/netapp/check-console-ports", "mode": "legacy-html"},
            {"label": "Save selected console", "method": "POST", "route": "/modules/netapp/save-console", "mode": "legacy-html"},
            {"label": "Read console state", "method": "POST", "route": "/modules/netapp/console-read-state", "mode": "legacy-html"},
            {"label": "Preview console IP commands", "method": "POST", "route": "/modules/netapp/console-cluster-mgmt-ip", "mode": "legacy-html"},
            {"label": "Apply cluster IP by console", "method": "POST", "route": "/modules/netapp/console-cluster-mgmt-ip", "mode": "legacy-html"},
            {"label": "Update NetApp convention", "method": "POST", "route": "/modules/netapp/update-convention", "mode": "legacy-html"},
            {"label": "Setup NetApp IP", "method": "POST", "route": "/modules/netapp/apply-ip-setup", "mode": "legacy-html"},
            {"label": "Preview cluster IP command", "method": "POST", "route": "/modules/netapp/cluster-mgmt-ip", "mode": "legacy-html"},
            {"label": "Apply cluster management IP", "method": "POST", "route": "/modules/netapp/cluster-mgmt-ip", "mode": "legacy-html"},
            {"label": "Ping all NetApp IPs", "method": "POST", "route": "/modules/netapp/bootstrap-test-all", "mode": "legacy-html"},
            {"label": "Use discovered values", "method": "POST", "route": "/modules/netapp/use-discovered-values", "mode": "legacy-html"},
            {"label": "Probe ESXi and NFS", "method": "POST", "route": "/modules/netapp/probe-vmware-nfs", "mode": "legacy-html"},
            {"label": "Discover NetApp", "method": "POST", "route": "/modules/netapp/discover", "mode": "json"},
            {"label": "Mark bootstrap complete", "method": "POST", "route": "/modules/netapp/bootstrap-complete", "mode": "legacy-html"},
            {"label": "API readiness", "method": "POST", "route": "/modules/netapp/api-readiness", "mode": "legacy-html"},
            {"label": "Plan NetApp", "method": "POST", "route": "/modules/netapp/plan", "mode": "json"},
            {"label": "Validate NetApp", "method": "POST", "route": "/modules/netapp/validate", "mode": "json"},
            {"label": "Validate NetApp page", "method": "POST", "route": "/modules/netapp/validate-page", "mode": "legacy-html"},
            {"label": "Export NetApp plan", "method": "POST", "route": "/modules/netapp/export-plan", "mode": "legacy-html"},
            {"label": "Safe apply NetApp", "method": "POST", "route": "/modules/netapp/apply", "mode": "json"},
            {"label": "Apply NetApp page", "method": "POST", "route": "/modules/netapp/apply-page", "mode": "legacy-html"},
            {"label": "Check reset readiness", "method": "POST", "route": "/modules/netapp/factory-reset", "mode": "legacy-html"},
            {"label": "Factory reset NetApp", "method": "POST", "route": "/modules/netapp/factory-reset", "mode": "legacy-html"},
            {"label": "Plan ONTAP upgrade", "method": "POST", "route": "/modules/netapp/plan-upgrade", "mode": "legacy-html"},
            {"label": "Run ONTAP upgrade", "method": "POST", "route": "/modules/netapp/run-upgrade", "mode": "legacy-html"},
            {"label": "ONTAP upgrade activity", "method": "GET", "route": "/modules/netapp/upgrade-activity", "mode": "legacy-html"},
        ],
        "cisco": [
            {"label": "Open Cisco setup", "method": "GET", "route": "/cisco", "mode": "legacy-html"},
            {"label": "Check version", "method": "POST", "route": "/modules/cisco/discover-version", "mode": "legacy-html"},
            {"label": "Test console access", "method": "POST", "route": "/modules/cisco/discover-console", "mode": "legacy-html"},
            {"label": "Fix serial access", "method": "POST", "route": "/modules/cisco/fix-serial-permissions", "mode": "legacy-html"},
            {"label": "Setup Cisco IP", "method": "POST", "route": "/modules/cisco/bootstrap-management", "mode": "legacy-html"},
            {"label": "Check current config", "method": "POST", "route": "/modules/cisco/verify-console-bootstrap", "mode": "legacy-html"},
            {"label": "Test SSH", "method": "POST", "route": "/modules/cisco/test-ssh", "mode": "legacy-html"},
            {"label": "Save to config", "method": "POST", "route": "/modules/cisco/save-port-map", "mode": "legacy-html"},
            {"label": "Discover ports", "method": "POST", "route": "/modules/cisco/discover-ports", "mode": "legacy-html"},
            {"label": "Discover current state", "method": "POST", "route": "/modules/cisco/discover-state", "mode": "legacy-html"},
            {"label": "Preview config", "method": "POST", "route": "/modules/cisco/preview-config", "mode": "legacy-html"},
            {"label": "Apply config", "method": "POST", "route": "/modules/cisco/apply-config", "mode": "legacy-html"},
            {"label": "Approve config", "method": "POST", "route": "/modules/cisco/approve-config-plan", "mode": "legacy-html"},
            {"label": "Backup config", "method": "POST", "route": "/modules/cisco/backup-config", "mode": "legacy-html"},
            {"label": "Factory reset switch", "method": "POST", "route": "/modules/cisco/factory-reset", "mode": "legacy-html"},
            {"label": "Plan Cisco upgrade", "method": "POST", "route": "/modules/cisco/plan-upgrade", "mode": "legacy-html"},
            {"label": "Run Cisco upgrade", "method": "POST", "route": "/modules/cisco/run-upgrade", "mode": "legacy-html"},
            {"label": "Cisco upgrade activity", "method": "GET", "route": "/modules/cisco/upgrade-activity", "mode": "legacy-html"},
        ],
        "configuration": [
            {"label": "Load kit library", "method": "GET", "route": "/api/ui/kits", "mode": "json"},
            {"label": "Load existing kit", "method": "POST", "route": "/api/ui/kits/load", "mode": "json"},
            {"label": "Create kit", "method": "POST", "route": "/api/ui/kits/create", "mode": "json"},
            {"label": "Import kit config", "method": "POST", "route": "/api/ui/kits/import", "mode": "json"},
            {"label": "Open current kit config", "method": "GET", "route": "/api/ui/current-kit-config", "mode": "json"},
            {"label": "Download current kit config", "method": "GET", "route": "/api/ui/current-kit-config/download", "mode": "download"},
            {"label": "Save global settings", "method": "POST", "route": "/save-global-settings", "mode": "legacy-html"},
            {"label": "Load kit", "method": "POST", "route": "/load-kit", "mode": "legacy-html"},
            {"label": "Create kit", "method": "POST", "route": "/new-kit", "mode": "legacy-html"},
            {"label": "Save kit config", "method": "POST", "route": "/save-config", "mode": "legacy-html"},
            {"label": "Autofill IP plan", "method": "POST", "route": "/autofill-ip-plan", "mode": "legacy-html"},
            {"label": "Upload firmware media", "method": "POST", "route": "/upload-upgrade-media", "mode": "legacy-html"},
            {"label": "View current kit config", "method": "POST", "route": "/view-current-kit-config", "mode": "legacy-html"},
            {"label": "Import kit config", "method": "POST", "route": "/import-kit-config", "mode": "legacy-html"},
        ],
        "storage": [
            {"label": "Load storage state", "method": "GET", "route": "/api/ui/storage", "mode": "json"},
            {"label": "Save storage target", "method": "POST", "route": "/save-storage-target", "mode": "legacy-html"},
            {"label": "Display current storage setup", "method": "POST", "route": "/read-current-storage", "mode": "legacy-html"},
            {"label": "Build storage plan", "method": "POST", "route": "/plan-raid-layout", "mode": "legacy-html"},
            {"label": "Approve this plan", "method": "POST", "route": "/approve-storage-plan", "mode": "legacy-html"},
            {"label": "Apply storage layout", "method": "POST", "route": "/apply-storage-layout", "mode": "legacy-html"},
            {"label": "Clear invalid selections and reload inventory", "method": "POST", "route": "/repair-storage-selection", "mode": "legacy-html"},
            {"label": "Probe storage capabilities", "method": "POST", "route": "/probe-storage-capabilities", "mode": "legacy-html"},
            {"label": "Remove approval", "method": "POST", "route": "/clear-storage-approval", "mode": "legacy-html"},
            {"label": "Reboot storage now", "method": "POST", "route": "/reboot-storage-now", "mode": "legacy-html"},
            {"label": "View storage artifact", "method": "POST", "route": "/view-storage-artifact", "mode": "legacy-html"},
            {"label": "Download storage artifact", "method": "POST", "route": "/download-storage-artifact", "mode": "download"},
            {"label": "Open reports", "method": "GET", "route": "/configs", "mode": "legacy-html"},
            {"label": "Open build files", "method": "GET", "route": "/configs", "mode": "legacy-html"},
        ],
        "windows": [
            {"label": "Save Windows setup", "method": "POST", "route": "/save-windows-settings", "mode": "legacy-html"},
            {"label": "Upload Windows image", "method": "POST", "route": "/upload-windows-image", "mode": "legacy-html"},
            {"label": "Plan Windows install (dry-run)", "method": "POST", "route": "/plan-windows-install", "mode": "legacy-html"},
            {"label": "Probe vSphere", "method": "POST", "route": "/probe-windows-vsphere", "mode": "legacy-html"},
            {"label": "Probe WinRM", "method": "POST", "route": "/probe-windows-winrm", "mode": "legacy-html"},
            {"label": "Register OVF path", "method": "POST", "route": "/register-windows-ovf-path", "mode": "legacy-html"},
            {"label": "Use selected template", "method": "POST", "route": "/select-windows-ovf-template", "mode": "legacy-html"},
        ],
        "ovf_templates": [
            {"label": "Open OVF Templates", "method": "GET", "route": "/modules/ovf-templates", "mode": "legacy-html"},
            {"label": "Open Windows template settings", "method": "GET", "route": "/windows", "mode": "legacy-html"},
            {"label": "Register OVF directory", "method": "POST", "route": "/modules/ovf-templates/register-directory", "mode": "legacy-html"},
            {"label": "Register OVF path", "method": "POST", "route": "/register-windows-ovf-path", "mode": "legacy-html"},
        ],
        "qnap": [
            {"label": "Open QNAP setup", "method": "GET", "route": "/qnap", "mode": "legacy-html"},
            {"label": "Save QNAP setup", "method": "POST", "route": "/save-qnap-settings", "mode": "legacy-html"},
        ],
        "execution": [
            {"label": "Open Run Center", "method": "GET", "route": "/execution", "mode": "legacy-html"},
            {"label": "Live job status", "method": "GET", "route": "/api/ui/job-status", "mode": "json"},
            {"label": "Prepare run review", "method": "POST", "route": "/prepare-execute", "mode": "legacy-html"},
            {"label": "Start preview run", "method": "POST", "route": "/execute-preview", "mode": "legacy-html"},
            {"label": "Start real run", "method": "POST", "route": "/execute", "mode": "legacy-html"},
            {"label": "Retry storage stage", "method": "POST", "route": "/retry-storage-stage", "mode": "legacy-html"},
            {"label": "View run summary", "method": "POST", "route": "/view-run-summary", "mode": "legacy-html"},
            {"label": "Download run summary", "method": "POST", "route": "/download-run-summary", "mode": "download"},
            {"label": "Open setup page", "method": "GET", "route": "/configuration", "mode": "legacy-html"},
            {"label": "Open Reports", "method": "GET", "route": "/configs", "mode": "legacy-html"},
        ],
        "reports": [
            {"label": "Run history API", "method": "GET", "route": "/api/ui/run-history", "mode": "json"},
            {"label": "Search reports", "method": "GET", "route": "/configs", "mode": "legacy-html"},
            {"label": "Open detailed history", "method": "GET", "route": "/history", "mode": "legacy-html"},
            {"label": "Open Reports", "method": "GET", "route": "/configs", "mode": "legacy-html"},
            {"label": "Related reports", "method": "GET", "route": "/configs", "mode": "legacy-html"},
            {"label": "View run summary", "method": "POST", "route": "/view-run-summary", "mode": "legacy-html"},
            {"label": "Download run summary", "method": "POST", "route": "/download-run-summary", "mode": "download"},
            {"label": "View report", "method": "POST", "route": "/view-report", "mode": "legacy-html"},
            {"label": "Download report", "method": "POST", "route": "/download-report", "mode": "download"},
            {"label": "View latest live summary", "method": "POST", "route": "/view-latest-live-summary", "mode": "legacy-html"},
            {"label": "Download debug bundle", "method": "GET", "route": "/debug-bundles/latest", "mode": "download"},
        ],
        "technical": [
            {"label": "Technical events API", "method": "GET", "route": "/api/ui/technical-events", "mode": "json"},
            {"label": "Live job websocket", "method": "WS", "route": "/ws/job/{kit_name}", "mode": "websocket"},
        ],
        "action-map": [
            {"label": "React action catalog", "method": "GET", "route": "/api/ui/action-catalog", "mode": "json"},
        ],
    }


def react_ui_route_category(path: str) -> str:
    rules = [
        ("/api/ui", "React API"),
        ("/modules/netapp", "NetApp"),
        ("/modules/cisco", "Cisco"),
        ("/modules/ovf-templates", "OVF templates"),
        ("/ws/", "Live job stream"),
        ("/ilo", "iLO"),
        ("/save-ilo", "iLO"),
        ("/export-ilo", "iLO"),
        ("/plan-ilo", "iLO"),
        ("/run-ilo", "iLO"),
        ("/esxi", "ESXi"),
        ("/save-esxi", "ESXi"),
        ("/storage", "Storage"),
        ("/save-storage", "Storage"),
        ("/read-current-storage", "Storage"),
        ("/plan-raid", "Storage"),
        ("/approve-storage", "Storage"),
        ("/apply-storage", "Storage"),
        ("/reboot-storage", "Storage"),
        ("/windows", "Windows"),
        ("/save-windows", "Windows"),
        ("/upload-windows", "Windows"),
        ("/plan-windows", "Windows"),
        ("/probe-windows", "Windows"),
        ("/register-windows", "Windows"),
        ("/select-windows", "Windows"),
        ("/qnap", "QNAP"),
        ("/save-qnap", "QNAP"),
        ("/configuration", "Configuration"),
        ("/global-settings", "Overview"),
        ("/configs", "Reports"),
        ("/kits", "Configuration"),
        ("/load-kit", "Configuration"),
        ("/new-kit", "Configuration"),
        ("/save-config", "Configuration"),
        ("/save-global", "Configuration"),
        ("/import-kit", "Configuration"),
        ("/upload-upgrade", "Configuration"),
        ("/upgrade-helper", "Upgrade helper"),
        ("/save-upgrade", "Upgrade helper"),
        ("/dashboard", "Overview"),
        ("/execution", "Run Center"),
        ("/prepare-execute", "Run Center"),
        ("/execute", "Run Center"),
        ("/retry-storage-stage", "Run Center"),
        ("/history", "Reports"),
        ("/view-report", "Reports"),
        ("/download-report", "Reports"),
        ("/view-run-summary", "Reports"),
        ("/download-run-summary", "Reports"),
        ("/debug-bundles", "Reports"),
        ("/view-latest", "Reports"),
        ("/download-latest", "Reports"),
        ("/react-preview", "React shell"),
        ("/health", "System"),
    ]
    for prefix, category in rules:
        if path == prefix or path.startswith(prefix):
            return category
    if path == "/":
        return "React shell"
    return "Other"


def react_ui_route_mode(path: str, methods: list[str], response_class: Any) -> str:
    if methods == ["WS"]:
        return "websocket"
    if path in {"/", "/react-preview"}:
        return "react-shell"
    if "download" in path or path.startswith("/debug-bundles"):
        return "download"
    if path.startswith("/api/ui"):
        return "json"
    if path in {
        "/modules/netapp/discover",
        "/modules/netapp/plan",
        "/modules/netapp/validate",
        "/modules/netapp/apply",
        "/modules/netapp/status",
        "/modules/netapp/repair/{issue_id}",
    }:
        return "json"
    response_name = getattr(response_class, "__name__", "")
    if response_name == "HTMLResponse":
        return "legacy-html"
    return "backend"


def react_ui_route_migration_status(path: str, mode: str) -> str:
    if mode == "legacy-html":
        return "HTML compatibility"
    if mode == "download":
        return "Shared download"
    if mode == "websocket":
        return "Shared live stream"
    if path.startswith("/api/ui"):
        return "React JSON API"
    if mode == "react-shell":
        return "React shell"
    return "Shared backend"


def react_ui_action_catalog() -> dict[str, Any]:
    docs_paths = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    mapped_routes = {action["route"] for actions in react_ui_action_inventory().values() for action in actions}
    routes = []
    for route in app.routes:
        path = str(getattr(route, "path", "") or "")
        if not path or path in docs_paths or path.startswith("/static"):
            continue
        raw_methods = sorted(str(method) for method in (getattr(route, "methods", None) or []) if str(method) not in {"HEAD", "OPTIONS"})
        methods = raw_methods or (["WS"] if path.startswith("/ws/") else [])
        if not methods:
            continue
        response_class = getattr(route, "response_class", None)
        mode = react_ui_route_mode(path, methods, response_class)
        routes.append(
            {
                "path": path,
                "methods": methods,
                "method": "/".join(methods),
                "name": str(getattr(route, "name", "") or ""),
                "category": react_ui_route_category(path),
                "mode": mode,
                "migration_status": react_ui_route_migration_status(path, mode),
                "mapped": path in mapped_routes,
            }
        )
    routes.sort(key=lambda item: (item["category"], item["path"], item["method"]))
    category_names = sorted({str(item["category"]) for item in routes})
    coverage = {
        "total_routes": len(routes),
        "react_api_routes": sum(1 for item in routes if str(item["path"]).startswith("/api/ui")),
        "legacy_routes": sum(1 for item in routes if item["mode"] == "legacy-html"),
        "download_routes": sum(1 for item in routes if item["mode"] == "download"),
        "websocket_routes": sum(1 for item in routes if item["mode"] == "websocket"),
        "mapped_actions": sum(1 for item in routes if item["mapped"]),
        "categories": len(category_names),
    }
    return {
        "coverage": coverage,
        "categories": category_names,
        "routes": routes,
        "actions": react_ui_action_inventory(),
    }


def react_ui_artifact_links(job: dict[str, Any]) -> list[dict[str, str]]:
    artifacts = [
        ("Run bundle", "run_bundle_dir"),
        ("Live job log", "run_live_log_path"),
        ("Trace", "run_trace_path"),
        ("Summary", "run_summary_path"),
        ("Config snapshot", "run_config_snapshot_path"),
        ("ESXi ISO path", "esxi_iso_path"),
        ("ESXi ISO URL", "esxi_iso_url"),
        ("Storage run directory", "storage_run_directory"),
    ]
    links = []
    for label, key in artifacts:
        value = str(job.get(key) or "").strip()
        if value:
            links.append({"label": label, "value": value})
    return links


def react_ui_job_payload(job: dict[str, Any]) -> dict[str, Any]:
    logs = [str(line) for line in list(job.get("logs") or [])]
    return {
        "status": str(job.get("status") or "Idle"),
        "scope": str(job.get("scope") or ""),
        "root_scope": str(job.get("root_scope") or ""),
        "execution_mode": str(job.get("execution_mode") or ""),
        "execution_mode_label": str(job.get("execution_mode_label") or ""),
        "current_stage": str(job.get("current_stage") or ""),
        "progress_percent": int(job.get("progress_percent") or 0),
        "completed_steps": int(job.get("completed_steps") or 0),
        "total_steps": int(job.get("total_steps") or 0),
        "started_at": str(job.get("started_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
        "last_message": str(logs[-1] if logs else ""),
        "logs": logs[-80:],
        "stage_statuses": dict(job.get("stage_statuses") or {}),
        "artifacts": react_ui_artifact_links(job),
    }


def _react_path_text(paths: dict[str, Any] | None, key: str) -> str:
    if not paths:
        return ""
    value = paths.get(key)
    return str(value or "")


def _react_storage_artifact_paths(paths: dict[str, Path] | None) -> dict[str, str]:
    if not paths:
        return {}
    return {str(key): str(value) for key, value in paths.items()}


def build_react_storage_state(cfg: dict[str, Any] | None = None, job: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_kit_config()
    job = job if job is not None else load_job(cfg.get("site", {}).get("name", ""))
    storage_cfg = ensure_storage_config(cfg)
    target = resolve_storage_target_host(cfg)
    credentials = resolve_storage_target_credentials(cfg)
    review = build_storage_review_context(cfg)
    execution_status = build_storage_execution_status(cfg)
    discovery = None
    discovery_paths = None
    plan = None
    plan_paths = None
    restore_error = ""
    latest_raw_path = str(storage_cfg.get("latest_discovery_raw_path") or "").strip()
    latest_plan_path = str(storage_cfg.get("latest_plan_path") or "").strip()
    if latest_raw_path or latest_plan_path:
        try:
            discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
                discovery_raw_path=latest_raw_path,
                raid_plan_path=latest_plan_path,
                expected_host=str(target.get("resolved") or ""),
            )
        except Exception as exc:
            restore_error = str(exc).splitlines()[0]
    discovery_summary = (discovery or {}).get("summary", discovery) if discovery else {}
    hpe = (discovery_summary or {}).get("hpe_smart_storage", {}) or {}
    standard = (discovery_summary or {}).get("standard_redfish_storage", {}) or {}
    controllers = list(hpe.get("controllers") or []) + list(standard.get("controllers") or [])
    drives = build_storage_display_drives(discovery_summary) if discovery_summary else []
    volumes = list(hpe.get("volumes") or []) + list(standard.get("volumes") or [])
    plan_summary = storage_plan_summary(plan) if plan else (storage_cfg.get("latest_plan_summary") or {})
    approval = storage_cfg.get("approval", {}) or {}
    apply_dir = str(job.get("storage_run_directory") or "").strip()
    apply_paths = None
    workflow_state = None
    if apply_dir:
        try:
            apply_paths = storage_apply_paths_from_directory(apply_dir)
            workflow_state = load_storage_workflow_state(apply_paths)
        except Exception:
            apply_paths = None
            workflow_state = None
    readiness = build_storage_page_readiness(review, target, credentials, execution_status, discovery_paths)
    blockers = [item for item in readiness if item.get("tone") != "ready"]
    return {
        "target": {
            "resolved": str(target.get("resolved") or ""),
            "source": str(target.get("source") or ""),
            "valid": bool(target.get("valid")),
            "error": str(target.get("error") or ""),
            "default_host": str((cfg.get("ilo", {}) or {}).get("current_ip") or (cfg.get("ilo", {}) or {}).get("host") or (cfg.get("ilo", {}) or {}).get("target_ip") or (cfg.get("ip_plan", {}) or {}).get("ilo") or ""),
        },
        "credentials": {
            "valid": bool(credentials.get("valid")),
            "username": str(credentials.get("username") or ""),
            "username_source": str(credentials.get("username_source") or ""),
            "password_saved": bool(credentials.get("password")),
            "error": str(credentials.get("error") or ""),
        },
        "values": {
            "target_host": str(storage_cfg.get("target_host_override") or ""),
            "username": str(storage_cfg.get("username") or (cfg.get("ilo", {}) or {}).get("username") or ""),
            "password": "",
            "password_saved": bool(storage_cfg.get("password") or (cfg.get("ilo", {}) or {}).get("password")),
            "target_mode": "override" if str(storage_cfg.get("target_host_override") or "").strip() else "defaults",
            "include_in_ilo_run": bool(storage_cfg.get("include_in_ilo_run")),
        },
        "review": {
            "state": str(review.get("state") or ""),
            "state_label": str(review.get("state_label") or ""),
            "state_tone": str(review.get("state_tone") or ""),
            "approved": bool(review.get("approved")),
            "stale": bool(review.get("stale")),
            "status_reason": str(review.get("status_reason") or ""),
        },
        "execution_status": execution_status,
        "readiness": readiness,
        "blockers": blockers,
        "discovery": {
            "available": bool(discovery_paths),
            "raw_path": _react_path_text(discovery_paths, "raw") or latest_raw_path,
            "directory": _react_path_text(discovery_paths, "directory"),
            "controllers": len(controllers),
            "drives": len(drives),
            "volumes": len(volumes),
            "server": (discovery_summary or {}).get("server", {}) or {},
            "restore_error": restore_error,
        },
        "plan": {
            "available": bool(plan_paths),
            "valid": bool((plan or {}).get("valid")) if plan else bool(plan_summary),
            "path": _react_path_text(plan_paths, "plan") or latest_plan_path,
            "directory": _react_path_text(plan_paths, "directory"),
            "summary": plan_summary,
            "arrays": list((plan_summary or {}).get("arrays") or []),
            "mode": str((plan_summary or {}).get("mode") or ""),
            "create_only_confirmation": STORAGE_APPLY_CONFIRM_CREATE,
            "wipe_rebuild_confirmation": STORAGE_APPLY_CONFIRM_WIPE,
        },
        "approval": {
            "approved": bool(approval.get("plan_path") and approval.get("discovery_raw_path") and review.get("state") == "approved"),
            "state": str(approval.get("state") or ""),
            "host": str(approval.get("host") or ""),
            "plan_path": str(approval.get("plan_path") or ""),
            "discovery_raw_path": str(approval.get("discovery_raw_path") or ""),
            "include_in_ilo_run": bool(storage_cfg.get("include_in_ilo_run")),
        },
        "apply": {
            "directory": str(apply_dir or ""),
            "paths": _react_storage_artifact_paths(apply_paths),
            "workflow": workflow_state or {},
        },
        "actions": react_ui_action_inventory().get("storage", []),
    }


def react_ui_module_summaries(cfg: dict[str, Any], workflow_contexts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    module_keys = [
        ("ilo", "iLO", "/ilo"),
        ("storage", "Storage setup", "/storage"),
        ("esxi", "ESXi", "/esxi"),
        ("windows", "Windows", "/windows"),
        ("qnap", "QNAP", "/qnap"),
        ("netapp", "NetApp", "/modules/netapp"),
        ("cisco_switch", "Cisco", "/cisco"),
    ]
    modules = []
    for key, label, legacy_href in module_keys:
        context = dict(workflow_contexts.get(key) or {})
        checks = list(context.get("checks") or [])
        modules.append(
            {
                "key": "cisco" if key == "cisco_switch" else key,
                "workflow_key": key,
                "label": label,
                "legacy_href": legacy_href,
                "target": str(context.get("target") or "Not set"),
                "state": str(context.get("state") or "not_started"),
                "state_label": str(context.get("state_label") or "Review"),
                "tone": str(context.get("tone") or "progress"),
                "planned_summary": str(context.get("planned_summary") or ""),
                "last_summary": str(context.get("result_summary") or ""),
                "included": bool((cfg.get("included") or {}).get(key)),
                "checks_ready": sum(1 for item in checks if item.get("ok")),
                "total_checks": len(checks),
                "blockers": [dict(item) for item in checks if not item.get("ok")],
            }
        )
    ip_plan = cfg.get("ip_plan", {}) or {}
    shared_ready = bool(str((cfg.get("shared_network") or {}).get("subnet") or "").strip() and str(ip_plan.get("gateway") or "").strip())
    modules.append(
        {
            "key": "global_settings",
            "workflow_key": "global_settings",
            "label": "Global Settings",
            "legacy_href": "/global-settings",
            "target": str((cfg.get("shared_network") or {}).get("subnet") or "Not set"),
            "state": "ready" if shared_ready else "not_started",
            "state_label": "Available" if shared_ready else "Needs defaults",
            "tone": "ready" if shared_ready else "pending",
            "planned_summary": "Shared subnet, gateway, DNS, address plan, and SNMP defaults.",
            "last_summary": f"Gateway: {ip_plan.get('gateway') or 'Not set'}",
            "included": True,
            "checks_ready": 1 if shared_ready else 0,
            "total_checks": 1,
            "blockers": [] if shared_ready else [{"label": "Shared defaults", "fix": "Set the subnet and gateway before running setup."}],
        }
    )
    registered_ovfs = len((((cfg.get("ovf_templates") or {}).get("templates") or {})))
    modules.append(
        {
            "key": "ovf_templates",
            "workflow_key": "ovf_templates",
            "label": "OVF Templates",
            "legacy_href": "/windows",
            "target": f"{registered_ovfs} registered" if registered_ovfs else "None registered",
            "state": "ready" if registered_ovfs else "not_started",
            "state_label": "Available" if registered_ovfs else "Review",
            "tone": "ready" if registered_ovfs else "progress",
            "planned_summary": "Reusable OVF/OVA template registration for VM workflows.",
            "last_summary": f"{registered_ovfs} template(s) registered.",
            "included": True,
            "checks_ready": 1 if registered_ovfs else 0,
            "total_checks": 1,
            "blockers": [],
        }
    )
    upgrade_card = build_upgrade_helper_card(cfg)
    modules.append(
        {
            "key": "upgrade_helper",
            "workflow_key": "upgrade_helper",
            "label": "Upgrade Helper",
            "legacy_href": "/upgrade-helper",
            "target": str(upgrade_card.get("label") or "Upgrade gates"),
            "state": "ready" if not int(upgrade_card.get("blockers") or 0) else "pending",
            "state_label": "Available" if not int(upgrade_card.get("blockers") or 0) else "Needs review",
            "tone": str(upgrade_card.get("tone") or "progress"),
            "planned_summary": "Firmware/media gates before execution.",
            "last_summary": str(upgrade_card.get("summary") or ""),
            "included": True,
            "checks_ready": int(upgrade_card.get("ready_checks") or 0),
            "total_checks": int(upgrade_card.get("total_checks") or 0),
            "blockers": [],
        }
    )
    modules.append(
        {
            "key": "configuration",
            "workflow_key": "configuration",
            "label": "Configuration",
            "legacy_href": "/configuration",
            "target": str(cfg.get("site", {}).get("name") or "Current kit"),
            "state": "ready",
            "state_label": "Available",
            "tone": "ready",
            "planned_summary": "Manage kit selection, global network values, and included modules.",
            "last_summary": f"{len(list_kits())} kit(s) available.",
            "included": True,
            "checks_ready": 1,
            "total_checks": 1,
            "blockers": [],
        }
    )
    return modules


def build_react_execution_review_state(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        return build_execution_review(cfg, "included", include_runtime=False)
    except Exception as exc:
        error_text = str(exc).splitlines()[0]
        lowered_error = error_text.lower()
        blocked_stage_key = "esxi" if "esxi" in lowered_error or "iso" in lowered_error else ""
        included = cfg.get("included", {}) or {}
        stages = []
        stage_specs = [
            ("ilo", "iLO", "/ilo"),
            ("storage", "Storage / RAID", "/storage#storage-review-start"),
            ("esxi", "ESXi", "/esxi"),
            ("windows", "Windows", "/windows"),
            ("qnap", "QNAP", "/qnap"),
            ("iosafe", "ioSafe", "/global-settings"),
            ("cisco_switch", "Cisco Switch", "/cisco"),
            ("netapp", "NetApp", "/modules/netapp"),
        ]
        for key, name, href in stage_specs:
            if not included.get(key):
                continue
            blocked = not blocked_stage_key or key == blocked_stage_key
            stages.append(
                {
                    "key": key,
                    "name": name,
                    "target": "Not checked",
                    "included": True,
                    "summary": "Open the setup page to review saved values." if not blocked else "Review data could not be built for Operator Mode.",
                    "review_href": href,
                    "status_label": "Needs attention" if blocked else "Review",
                    "status_tone": "pending" if blocked else "progress",
                    "blocked_reason": error_text if blocked else "",
                    "corrective_action": "Open the setup page and resolve the blocked review input." if blocked else "Open the setup page if you want to review it again.",
                    "fix_href": href if blocked else "",
                    "fix_label": f"Fix on {name}" if blocked else "",
                }
            )
        return {
            "scope": "included",
            "selected_scopes_for_form": ["included"],
            "stages": stages,
            "confidence": {
                "score": 0,
                "label": "Needs attention",
                "tone": "pending",
                "summary": error_text,
                "ready_checks": 0,
                "total_checks": len(stages),
                "blocked_checks": [{"label": "Execution review", "details": error_text, "ok": False}],
                "review_checks": [],
            },
            "fallback_error": error_text,
        }


def build_react_ui_state() -> dict[str, Any]:
    cfg = load_kit_config()
    kit_name = cfg.get("site", {}).get("name", "")
    job = load_job(kit_name)
    history = load_history(kit_name)
    workflow_contexts = build_workflow_contexts(cfg, job, history)
    recommended_next_step = build_recommended_next_step(cfg, workflow_contexts)
    setup_precheck_summary = build_setup_precheck_summary(cfg, workflow_contexts, recommended_next_step)
    dashboard_job_status = build_dashboard_job_status(history)
    dashboard_overview = build_dashboard_overview(cfg, setup_precheck_summary, workflow_contexts, dashboard_job_status, job)
    return {
        "app": {"name": APP_NAME, "version": app_version()},
        "kit": {
            "name": kit_name,
            "available": list_kits(),
            "site": cfg.get("site", {}),
            "ip_plan": cfg.get("ip_plan", {}),
            "included": cfg.get("included", {}),
        },
        "pages": react_ui_page_specs(),
        "actions": react_ui_action_inventory(),
        "action_catalog": react_ui_action_catalog(),
        "job": react_ui_job_payload(job),
        "dashboard": dashboard_overview,
        "modules": react_ui_module_summaries(cfg, workflow_contexts),
        "execution_review": build_react_execution_review_state(cfg),
        "setup_ip": build_react_setup_ip_state(cfg),
        "setup_values": build_react_module_detail_state(cfg),
        "storage": build_react_storage_state(cfg, job),
        "recent_activity": build_activity_feed(history, limit=10),
        "run_history": build_history_display_entries(history)[:30],
        "report_center": build_report_center(cfg),
        "technical": {
            "logs": react_ui_job_payload(job)["logs"],
            "artifacts": react_ui_artifact_links(job),
            "trace_events": list(job.get("trace_events") or [])[-50:],
        },
    }


def build_react_setup_ip_state(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_kit_config()
    ip_plan = cfg.get("ip_plan", {}) or {}
    ilo_cfg = cfg.get("ilo", {}) or {}
    cisco_cfg = cfg.get("cisco_switch", {}) or {}
    netapp_cfg = cfg.get("netapp", {}) or {}
    netapp_bootstrap = netapp_cfg.get("bootstrap_overrides", {}) or {}
    netapp_desired = netapp_cfg.get("desired", {}) or {}
    return {
        "ilo": {
            "current_ip": ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "",
            "target_ip": ilo_cfg.get("target_ip") or ip_plan.get("ilo") or "",
            "gateway": ilo_cfg.get("gateway") or ip_plan.get("gateway") or "",
            "hostname": ilo_cfg.get("hostname") or "",
            "username": ilo_cfg.get("username") or "",
            "password_saved": bool(ilo_cfg.get("password")),
        },
        "cisco": {
            "hostname": cisco_cfg.get("hostname") or "sw01",
            "management_ip": cisco_cfg.get("management_ip") or cisco_cfg.get("ip") or ip_plan.get("switch") or "",
            "username": cisco_cfg.get("username") or "admin",
            "password": "",
            "password_saved": bool(cisco_cfg.get("password")),
            "enable_password": "",
            "enable_password_saved": bool(cisco_cfg.get("enable_password")),
            "management_vlan": cisco_cfg.get("management_vlan") or 10,
            "subnet_mask": cisco_cfg.get("subnet_mask") or ip_plan.get("netmask") or "255.255.255.0",
            "gateway": cisco_cfg.get("gateway") or ip_plan.get("gateway") or "",
            "console_port": cisco_cfg.get("console_port") or "",
            "console_baud": cisco_cfg.get("console_baud") or 9600,
            "domain_name": cisco_cfg.get("domain_name") or "lab.local",
            "bootstrap_network_port": cisco_cfg.get("bootstrap_network_port") or "",
            "bootstrap_network_mode": cisco_cfg.get("bootstrap_network_mode") or "trunk",
        },
        "netapp": {
            "host": netapp_cfg.get("host") or ip_plan.get("netapp") or "",
            "username": netapp_cfg.get("username") or "admin",
            "password": "",
            "password_saved": bool(netapp_cfg.get("password")),
            "console_port": netapp_cfg.get("console_port") or "",
            "console_baud": netapp_cfg.get("console_baud") or "9600",
            "gateway": netapp_desired.get("management_gateway") or ip_plan.get("gateway") or "",
            "netmask": netapp_desired.get("management_netmask") or ip_plan.get("netmask") or "255.255.255.0",
            "sp_a_ip": netapp_bootstrap.get("netapp_sp_a") or "",
            "sp_b_ip": netapp_bootstrap.get("netapp_sp_b") or "",
            "cluster_mgmt_ip": netapp_bootstrap.get("netapp_cluster_mgmt") or netapp_cfg.get("host") or ip_plan.get("netapp") or "",
            "node_01_mgmt_ip": netapp_bootstrap.get("netapp_node_01_mgmt") or "",
            "node_02_mgmt_ip": netapp_bootstrap.get("netapp_node_02_mgmt") or "",
            "svm_mgmt_ip": netapp_bootstrap.get("netapp_svm_mgmt") or netapp_desired.get("svm_mgmt_ip") or "",
        },
    }


def build_react_module_detail_state(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_kit_config()
    ip_plan = cfg.get("ip_plan", {}) or {}
    shared = cfg.get("shared_network", {}) or {}
    included = cfg.get("included", {}) or {}
    esxi_cfg = cfg.get("esxi", {}) or {}
    windows_cfg = cfg.get("windows", {}) or {}
    qnap_cfg = cfg.get("qnap", {}) or {}
    ovf_templates = ((cfg.get("ovf_templates") or {}).get("templates") or {})
    esxi_post_policy = ensure_esxi_post_config_policy(cfg)

    def yes_no(value: Any) -> str:
        return "Yes" if bool(value) else "No"

    def dns_text(local_values: Any = None) -> str:
        values = list(local_values or shared.get("dns_servers") or [])
        return ", ".join(str(item) for item in values if str(item or "").strip()) or "Not set"

    return {
        "esxi": {
            "summary": [
                {"label": "Install IP", "value": str(esxi_cfg.get("ip_address") or ip_plan.get("esxi") or "Not set")},
                {"label": "Hostname", "value": str(esxi_cfg.get("hostname") or "Not set")},
                {"label": "ESXi version", "value": str(esxi_cfg.get("version") or "7")},
                {"label": "Base ISO", "value": str(esxi_cfg.get("base_iso_path") or "Not selected")},
                {"label": "Root password saved", "value": yes_no(esxi_cfg.get("root_password"))},
                {"label": "Included", "value": yes_no(included.get("esxi"))},
            ],
            "details": [
                {"label": "Gateway", "value": str(esxi_cfg.get("gateway") or ip_plan.get("gateway") or "Not set")},
                {"label": "DNS", "value": dns_text(esxi_cfg.get("dns_servers"))},
                {"label": "Debug no reboot", "value": yes_no(esxi_cfg.get("debug_no_reboot"))},
                {"label": "Post-config transport", "value": str(esxi_cfg.get("post_config_transport") or "dry_run")},
                {"label": "Discovery octets", "value": f"{esxi_post_policy.get('discovery_start_octet', 31)}-{esxi_post_policy.get('discovery_end_octet', 33)}"},
                {"label": "Datastore create", "value": yes_no(esxi_post_policy.get("allow_datastore_create"))},
            ],
            "primary_action": {"label": "Open ESXi form", "href": "/esxi"},
        },
        "windows": {
            "summary": [
                {"label": "Windows IP", "value": str(windows_cfg.get("ip_address") or ip_plan.get("windows") or "Not set")},
                {"label": "VM name", "value": str(windows_cfg.get("vm_name") or "Not set")},
                {"label": "vSphere host", "value": str(windows_cfg.get("vsphere_host") or "Not set")},
                {"label": "Datastore", "value": str(windows_cfg.get("vsphere_datastore") or "Not set")},
                {"label": "Template", "value": str(windows_cfg.get("source_image_name") or windows_cfg.get("ovf_template_id") or "Not selected")},
                {"label": "Included", "value": yes_no(included.get("windows"))},
            ],
            "details": [
                {"label": "Gateway", "value": str(windows_cfg.get("gateway") or ip_plan.get("gateway") or "Not set")},
                {"label": "DNS", "value": dns_text(windows_cfg.get("dns_servers"))},
                {"label": "vSphere user", "value": str(windows_cfg.get("vsphere_username") or "Not set")},
                {"label": "vSphere password saved", "value": yes_no(windows_cfg.get("vsphere_password"))},
                {"label": "WinRM user", "value": str(windows_cfg.get("winrm_username") or "Administrator")},
                {"label": "WinRM password saved", "value": yes_no(windows_cfg.get("winrm_password"))},
                {"label": "WinRM port", "value": str(windows_cfg.get("winrm_port") or "5986")},
                {"label": "Install plan", "value": "Ready" if (windows_cfg.get("install_plan") or {}).get("ready") else "Needs dry-run"},
            ],
            "primary_action": {"label": "Open Windows form", "href": "/windows"},
        },
        "qnap": {
            "summary": [
                {"label": "QNAP IP", "value": str(qnap_cfg.get("ip") or ip_plan.get("qnap") or "Not set")},
                {"label": "Hostname", "value": str(qnap_cfg.get("hostname") or "Not set")},
                {"label": "Username", "value": str(qnap_cfg.get("username") or "Not set")},
                {"label": "Password saved", "value": yes_no(qnap_cfg.get("password"))},
                {"label": "Gateway", "value": str(qnap_cfg.get("gateway") or ip_plan.get("gateway") or "Not set")},
                {"label": "Included", "value": yes_no(included.get("qnap"))},
            ],
            "details": [
                {"label": "DNS", "value": dns_text(qnap_cfg.get("dns_servers"))},
                {"label": "Shared subnet", "value": str(shared.get("subnet") or ip_plan.get("subnet") or "Not set")},
            ],
            "primary_action": {"label": "Open QNAP form", "href": "/qnap"},
        },
        "ovf_templates": {
            "summary": [
                {"label": "Registered templates", "value": str(len(ovf_templates))},
                {"label": "Windows selected template", "value": str(windows_cfg.get("ovf_template_id") or "Not selected")},
            ],
            "details": [
                {
                    "label": str((template or {}).get("name") or template_id),
                    "value": str((template or {}).get("descriptor_name") or (template or {}).get("directory") or "Registered template"),
                }
                for template_id, template in list(ovf_templates.items())[:8]
            ],
            "primary_action": {"label": "Open OVF Templates form", "href": "/modules/ovf-templates"},
        },
    }


def _react_body_value(body: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in body:
            return body.get(key)
    return default


def _ilo_service_for_api():
    return default_ilo_module_service(
        {
            "normalize_ilo_hostname": normalize_ilo_hostname,
            "extract_ilo_additional_users_from_form": extract_ilo_additional_users_from_form,
            "normalize_ilo_policy": normalize_ilo_policy,
        }
    )


def build_react_ilo_state(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_kit_config()
    history = load_history(cfg.get("site", {}).get("name", ""))
    job = load_job(cfg.get("site", {}).get("name", ""))
    workflow_contexts = build_workflow_contexts(cfg, job, history)
    context = dict(workflow_contexts.get("ilo") or {})
    review = build_ilo_input_review(cfg, include_policy_validation=False)
    field_errors = build_ilo_field_errors(cfg)
    ilo_cfg = cfg.get("ilo", {}) or {}
    latest = latest_history_entry_for_scope(history, ["ilo"]) or {}
    checks = list(context.get("checks") or [])
    blocker = next((dict(item) for item in checks if not item.get("ok")), None)
    setup_ready = all(
        str(value or "").strip()
        for value in (
            ilo_cfg.get("current_ip") or ilo_cfg.get("host"),
            ilo_cfg.get("target_ip") or cfg.get("ip_plan", {}).get("ilo"),
            ilo_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway"),
            ilo_cfg.get("username"),
            ilo_cfg.get("password"),
        )
    )
    status_state = "ready" if setup_ready else "not_started"
    status_label = "Ready for IP setup" if setup_ready else "Needs iLO values"
    status_tone = "ready" if setup_ready else "pending"
    if str(job.get("scope") or "") == "ilo-ip-setup":
        current_stage = str(job.get("current_stage") or "").strip()
        job_status = str(job.get("status") or "").strip()
        if job_status == "Failed":
            status_state = "failed"
            status_label = current_stage or "iLO IP setup failed"
            status_tone = "failed"
        elif "Running" in job_status or "queued" in job_status.lower():
            status_state = "running"
            status_label = current_stage or job_status or "iLO IP setup running"
            status_tone = "progress"
        elif job_status == "Complete":
            status_state = "ready"
            status_label = current_stage or "iLO IP setup complete"
            status_tone = "ready"
    return {
        "page": {
            "key": "ilo",
            "title": "iLO setup",
            "legacy_href": "/ilo",
            "what": "Set the controller address, sign-in, hostname, and saved policy inputs used by Run Center.",
            "next": blocker.get("fix") if blocker else "Review the saved iLO target, then continue to Run Center or Storage setup.",
            "last": latest.get("status") or context.get("result_summary") or "No iLO run has been recorded yet.",
        },
        "values": {
            "current_ip": ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "",
            "target_ip": ilo_cfg.get("target_ip") or "",
            "gateway": ilo_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "",
            "hostname": ilo_cfg.get("hostname") or "",
            "username": ilo_cfg.get("username") or "",
            "password_saved": bool(ilo_cfg.get("password")),
            "included": bool(cfg.get("included", {}).get("ilo")),
        },
        "status": {
            "state": status_state,
            "label": status_label,
            "tone": status_tone,
            "target": context.get("target") or "Not set",
        },
        "review": {
            "errors": list(review.get("errors") or []),
            "notes": list(review.get("notes") or []),
            "field_errors": field_errors,
            "checks": checks,
        },
        "actions": react_ui_action_inventory().get("ilo", []),
    }


def build_react_global_settings_state(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_kit_config()
    site = cfg.get("site", {}) or {}
    shared_network = cfg.get("shared_network", {}) or {}
    ip_plan = cfg.get("ip_plan", {}) or {}
    included = cfg.get("included", {}) or {}
    snmp_cfg = cfg.get("shared_snmp", {}) or {}
    dns_servers = [str(item or "") for item in list(shared_network.get("dns_servers") or [])[:4]]
    while len(dns_servers) < 4:
        dns_servers.append("")
    snmp_users = normalize_snmp_users(snmp_cfg.get("users", []))
    first_user = snmp_users[0] if snmp_users else {}
    primary_auth_password = str(snmp_cfg.get("v3_auth_password") or first_user.get("auth_password") or "")
    primary_priv_password = str(snmp_cfg.get("v3_priv_password") or first_user.get("priv_password") or "")
    review = build_snmp_input_review(cfg)
    return {
        "page": {
            "key": "global_settings",
            "title": "Global Settings",
            "legacy_href": "/global-settings",
            "what": "Shared kit defaults for subnet, gateway, DNS, address assignments, and SNMPv3 users.",
            "next": "Save shared defaults, then continue through the setup modules that are included for this kit.",
            "last": f"Current kit: {site.get('name') or 'Kit-01'}",
        },
        "values": {
            "site_name": site.get("name") or "",
            "shared_subnet": shared_network.get("subnet") or ip_plan.get("subnet") or "",
            "gateway_ip": ip_plan.get("gateway") or "",
            "ilo_target_ip": ip_plan.get("ilo") or (cfg.get("ilo", {}) or {}).get("target_ip") or "",
            "esxi_ip": ip_plan.get("esxi") or "",
            "windows_ip": ip_plan.get("windows") or "",
            "switch_ip": ip_plan.get("switch") or "",
            "netapp_ip": ip_plan.get("netapp") or "",
            "qnap_ip": ip_plan.get("qnap") or "",
            "iosafe_ip": ip_plan.get("iosafe") or "",
            "dns1": dns_servers[0],
            "dns2": dns_servers[1],
            "dns3": dns_servers[2],
            "dns4": dns_servers[3],
            "snmp_v3_username": snmp_cfg.get("v3_username") or first_user.get("username") or "",
            "snmp_v3_auth_protocol": snmp_cfg.get("v3_auth_protocol") or first_user.get("auth_protocol") or "SHA",
            "snmp_v3_auth_password": "",
            "snmp_v3_auth_password_saved": bool(primary_auth_password),
            "snmp_v3_priv_protocol": snmp_cfg.get("v3_priv_protocol") or first_user.get("priv_protocol") or "AES",
            "snmp_v3_priv_password": "",
            "snmp_v3_priv_password_saved": bool(primary_priv_password),
        },
        "included": {
            "ilo": bool(included.get("ilo")),
            "storage": bool(included.get("storage")),
            "esxi": bool(included.get("esxi")),
            "windows": bool(included.get("windows")),
            "qnap": bool(included.get("qnap")),
            "netapp": bool(included.get("netapp")),
            "cisco_switch": bool(included.get("cisco_switch")),
            "iosafe": bool(included.get("iosafe")),
        },
        "snmp_users": [
            {
                "username": item.get("username") or "",
                "auth_protocol": item.get("auth_protocol") or "SHA",
                "auth_password": "",
                "auth_password_saved": bool(item.get("auth_password")),
                "priv_protocol": item.get("priv_protocol") or "AES",
                "priv_password": "",
                "priv_password_saved": bool(item.get("priv_password")),
            }
            for item in snmp_users[1:]
        ],
        "review": {
            "errors": list(review.get("errors") or []),
            "notes": list(review.get("notes") or []),
        },
        "actions": react_ui_action_inventory().get("global_settings", []),
    }


def build_react_kit_library_state(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_kit_config()
    current_name = sanitize_kit_name((cfg.get("site") or {}).get("name") or get_current_kit_name())
    available = list_kits()
    return {
        "active": current_name,
        "available": available,
        "other_kits": [name for name in available if name != current_name],
        "site": cfg.get("site", {}),
        "ip_plan": cfg.get("ip_plan", {}),
        "included": cfg.get("included", {}),
        "actions": react_ui_action_inventory().get("configuration", []),
    }


def _react_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _react_preserve_secret(submitted: Any, existing: Any) -> str:
    submitted_value = str(submitted or "")
    return submitted_value if submitted_value else str(existing or "")


async def _react_json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@app.get("/api/ui/app-state")
async def api_ui_app_state():
    return jsonable_encoder(build_react_ui_state())


@app.get("/api/ui/global-settings")
async def api_ui_global_settings():
    return jsonable_encoder(build_react_global_settings_state())


@app.get("/api/ui/storage")
async def api_ui_storage():
    return jsonable_encoder(build_react_storage_state())


@app.post("/api/ui/global-settings/autofill")
async def api_ui_global_settings_autofill(request: Request):
    cfg = load_kit_config()
    body = await _react_json_body(request)
    subnet = str(
        body.get("shared_subnet")
        or body.get("subnet")
        or (cfg.get("shared_network") or {}).get("subnet")
        or "10.10.8.0/24"
    ).strip()
    try:
        plan = build_default_ip_plan(subnet)
        return jsonable_encoder(
            {
                "ok": True,
                "message": "Default IP plan generated.",
                "shared_subnet": subnet,
                "plan": plan,
            }
        )
    except Exception as e:
        return jsonable_encoder(
            {
                "ok": False,
                "message": f"IP plan generation failed: {str(e).splitlines()[0]}",
                "shared_subnet": subnet,
                "global": build_react_global_settings_state(cfg),
            }
        )


@app.post("/api/ui/global-settings")
async def api_ui_global_settings_save(request: Request):
    cfg = merge_defaults(load_kit_config())
    body = await _react_json_body(request)
    values = body.get("values") if isinstance(body.get("values"), dict) else body
    values = dict(values or {})
    included_values = body.get("included") if isinstance(body.get("included"), dict) else None
    submitted_snmp_users = body.get("snmp_users") if isinstance(body.get("snmp_users"), list) else []

    existing_snmp = cfg.get("shared_snmp", {}) or {}
    existing_users = normalize_snmp_users(existing_snmp.get("users", []))
    existing_extra_users = existing_users[1:] if existing_users else []
    previous_ilo_plan_ip = str((cfg.get("ip_plan") or {}).get("ilo") or "").strip()
    previous_ilo_current_ip = str((cfg.get("ilo") or {}).get("current_ip") or (cfg.get("ilo") or {}).get("host") or "").strip()

    def value_or_existing(key: str, existing: Any = "") -> str:
        if key in values:
            return str(values.get(key) or "").strip()
        return str(existing or "").strip()

    previous_subnet = str((cfg.get("shared_network") or {}).get("subnet") or "")
    shared_subnet = value_or_existing("shared_subnet", previous_subnet or "10.10.8.0/24")
    cfg.setdefault("site", {})["name"] = sanitize_kit_name(value_or_existing("site_name", (cfg.get("site") or {}).get("name") or "Kit-01"))
    cfg.setdefault("shared_network", {})["subnet"] = shared_subnet
    current_dns = [str(item or "") for item in list((cfg.get("shared_network") or {}).get("dns_servers") or [])[:4]]
    while len(current_dns) < 4:
        current_dns.append("")
    cfg["shared_network"]["dns_servers"] = [
        value_or_existing("dns1", current_dns[0]),
        value_or_existing("dns2", current_dns[1]),
        value_or_existing("dns3", current_dns[2]),
        value_or_existing("dns4", current_dns[3]),
    ]

    cfg.setdefault("ip_plan", {})
    module_ip_fields = {
        "gateway_ip": "gateway",
        "switch_ip": "switch",
        "esxi_ip": "esxi",
        "ilo_target_ip": "ilo",
        "windows_ip": "windows",
        "qnap_ip": "qnap",
        "iosafe_ip": "iosafe",
        "netapp_ip": "netapp",
    }
    reset_default_ip_plan = previous_subnet != shared_subnet and not any(field in values for field in module_ip_fields)
    if reset_default_ip_plan:
        try:
            cfg["ip_plan"].update(build_default_ip_plan(shared_subnet))
        except Exception as e:
            return jsonable_encoder(
                {
                    "ok": False,
                    "message": f"Could not save global settings: {str(e).splitlines()[0]}",
                    "global": build_react_global_settings_state(cfg),
                }
            )
    for field, plan_key in module_ip_fields.items():
        if field in values:
            cfg["ip_plan"][plan_key] = value_or_existing(field, cfg["ip_plan"].get(plan_key, ""))
    if "ilo_target_ip" in values:
        cfg.setdefault("ilo", {})["target_ip"] = cfg["ip_plan"].get("ilo", "")
        if not previous_ilo_current_ip or previous_ilo_current_ip == previous_ilo_plan_ip:
            cfg["ilo"]["current_ip"] = cfg["ip_plan"].get("ilo", "")
            cfg["ilo"]["host"] = cfg["ip_plan"].get("ilo", "")
    if "switch_ip" in values:
        cfg.setdefault("cisco_switch", {})["ip"] = cfg["ip_plan"].get("switch", "")
        cfg.setdefault("cisco_switch", {})["management_ip"] = cfg["ip_plan"].get("switch", "")
    if "netapp_ip" in values:
        cfg.setdefault("netapp", {})["host"] = cfg["ip_plan"].get("netapp", "")
        cfg.setdefault("netapp", {}).setdefault("management", {})["cluster_mgmt_ip"] = cfg["ip_plan"].get("netapp", "")

    if included_values is not None:
        cfg.setdefault("included", {}).update(
            {
                key: _react_bool(included_values.get(key))
                for key in ("ilo", "storage", "esxi", "windows", "qnap", "netapp", "cisco_switch", "iosafe")
                if key in included_values
            }
        )
    cfg.setdefault("storage", {})["include_in_ilo_run"] = bool((cfg.get("included") or {}).get("storage"))

    primary_username = value_or_existing("snmp_v3_username", existing_snmp.get("v3_username") or (existing_users[0].get("username") if existing_users else ""))
    primary_auth_protocol = value_or_existing("snmp_v3_auth_protocol", existing_snmp.get("v3_auth_protocol") or (existing_users[0].get("auth_protocol") if existing_users else "SHA")) or "SHA"
    primary_auth_password = _react_preserve_secret(values.get("snmp_v3_auth_password"), existing_snmp.get("v3_auth_password") or (existing_users[0].get("auth_password") if existing_users else ""))
    primary_priv_protocol = value_or_existing("snmp_v3_priv_protocol", existing_snmp.get("v3_priv_protocol") or (existing_users[0].get("priv_protocol") if existing_users else "AES")) or "AES"
    primary_priv_password = _react_preserve_secret(values.get("snmp_v3_priv_password"), existing_snmp.get("v3_priv_password") or (existing_users[0].get("priv_password") if existing_users else ""))

    normalized_snmp_payload: list[dict[str, str]] = []
    if primary_username:
        normalized_snmp_payload.append(
            {
                "username": primary_username,
                "auth_protocol": primary_auth_protocol,
                "auth_password": primary_auth_password,
                "priv_protocol": primary_priv_protocol,
                "priv_password": primary_priv_password,
            }
        )
    for index, item in enumerate(submitted_snmp_users):
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        if not username:
            continue
        existing_extra = existing_extra_users[index] if index < len(existing_extra_users) else {}
        normalized_snmp_payload.append(
            {
                "username": username,
                "auth_protocol": str(item.get("auth_protocol") or existing_extra.get("auth_protocol") or "SHA").strip() or "SHA",
                "auth_password": _react_preserve_secret(item.get("auth_password"), existing_extra.get("auth_password")),
                "priv_protocol": str(item.get("priv_protocol") or existing_extra.get("priv_protocol") or "AES").strip() or "AES",
                "priv_password": _react_preserve_secret(item.get("priv_password"), existing_extra.get("priv_password")),
            }
        )
    cfg["shared_snmp"] = {
        "v3_username": primary_username,
        "v3_auth_protocol": primary_auth_protocol,
        "v3_auth_password": primary_auth_password,
        "v3_priv_protocol": primary_priv_protocol,
        "v3_priv_password": primary_priv_password,
        "read_community": str(existing_snmp.get("read_community") or ""),
        "users": normalize_snmp_users(normalized_snmp_payload),
    }

    snmp_input_review = build_snmp_input_review(cfg)
    if snmp_input_review["errors"]:
        return jsonable_encoder(
            {
                "ok": False,
                "message": "Shared defaults need attention before they can be saved.",
                "errors": list(snmp_input_review["errors"]),
                "notes": list(snmp_input_review["notes"]),
                "global": build_react_global_settings_state(cfg),
            }
        )
    try:
        cfg = apply_ip_plan(cfg)
        save_kit_config(cfg)
        set_current_kit_name(cfg["site"]["name"])
        append_activity_event(
            cfg["site"]["name"],
            "global_settings_saved",
            workflow="global_settings",
            summary="Saved shared defaults from the React desktop UI.",
            target=cfg["site"]["name"],
            details=[
                f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                f"Gateway: {cfg['ip_plan'].get('gateway', '') or 'Not set'}",
            ],
        )
        return jsonable_encoder(
            {
                "ok": True,
                "message": "Global settings saved.",
                "global": build_react_global_settings_state(load_kit_config()),
                "ilo": build_react_ilo_state(load_kit_config()),
                "app_state": build_react_ui_state(),
            }
        )
    except Exception as e:
        return jsonable_encoder(
            {
                "ok": False,
                "message": f"Could not save global settings: {str(e).splitlines()[0]}",
                "global": build_react_global_settings_state(cfg),
            }
        )


@app.get("/api/ui/current-kit")
async def api_ui_current_kit():
    cfg = load_kit_config()
    return jsonable_encoder(
        {
            "name": cfg.get("site", {}).get("name", ""),
            "available": list_kits(),
            "site": cfg.get("site", {}),
            "ip_plan": cfg.get("ip_plan", {}),
            "included": cfg.get("included", {}),
        }
    )


@app.get("/api/ui/kits")
async def api_ui_kits():
    return jsonable_encoder(build_react_kit_library_state())


@app.post("/api/ui/kits/load")
async def api_ui_kits_load(request: Request):
    body = await _react_json_body(request)
    selected_kit = sanitize_kit_name(str(body.get("selected_kit") or body.get("kit") or ""))
    if not selected_kit or not kit_path(selected_kit).exists():
        return jsonable_encoder(
            {
                "ok": False,
                "message": f"Saved kit not found: {selected_kit or '(blank)'}",
                "kits": build_react_kit_library_state(),
            }
        )
    set_current_kit_name(selected_kit)
    cfg = load_kit_config(selected_kit)
    return jsonable_encoder(
        {
            "ok": True,
            "message": f"Loaded kit: {selected_kit}",
            "kits": build_react_kit_library_state(cfg),
            "global": build_react_global_settings_state(cfg),
            "ilo": build_react_ilo_state(cfg),
            "app_state": build_react_ui_state(),
        }
    )


@app.post("/api/ui/kits/create")
async def api_ui_kits_create(request: Request):
    body = await _react_json_body(request)
    new_kit_name = sanitize_kit_name(str(body.get("new_kit_name") or body.get("name") or ""))
    if not new_kit_name:
        return jsonable_encoder({"ok": False, "message": "Enter a kit name.", "kits": build_react_kit_library_state()})
    cfg = default_config()
    cfg.setdefault("site", {})["name"] = new_kit_name
    save_kit_config(cfg)
    save_job(
        new_kit_name,
        {
            "status": "Idle",
            "scope": "",
            "current_stage": "",
            "progress_percent": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "logs": [],
        },
    )
    save_history(new_kit_name, [])
    cfg = load_kit_config(new_kit_name)
    return jsonable_encoder(
        {
            "ok": True,
            "message": f"Created new kit: {new_kit_name}",
            "kits": build_react_kit_library_state(cfg),
            "global": build_react_global_settings_state(cfg),
            "ilo": build_react_ilo_state(cfg),
            "app_state": build_react_ui_state(),
        }
    )


@app.get("/api/ui/current-kit-config")
async def api_ui_current_kit_config():
    cfg = load_kit_config()
    snapshot_path = export_current_kit_config_snapshot(cfg)
    return jsonable_encoder(
        {
            "ok": True,
            "kit": cfg.get("site", {}).get("name", ""),
            "path": str(snapshot_path),
            "filename": snapshot_path.name,
            "content": snapshot_path.read_text(encoding="utf-8"),
        }
    )


@app.get("/api/ui/current-kit-config/download")
async def api_ui_current_kit_config_download():
    cfg = load_kit_config()
    snapshot_path = export_current_kit_config_snapshot(cfg)
    return FileResponse(path=snapshot_path, filename=snapshot_path.name, media_type="application/x-yaml")


@app.post("/api/ui/kits/import")
async def api_ui_kits_import(import_file: UploadFile = File(...)):
    current_cfg = load_kit_config()
    try:
        raw = await import_file.read()
        if not raw:
            raise ValueError("The uploaded file was empty.")
        imported = yaml.safe_load(raw.decode("utf-8")) or {}
        if not isinstance(imported, dict):
            raise ValueError("The uploaded file must contain a YAML or JSON object.")
        imported = merge_defaults(imported)
        imported_name = sanitize_kit_name(
            imported.get("site", {}).get("name", "") or current_cfg.get("site", {}).get("name", "Kit-01")
        )
        imported.setdefault("site", {})["name"] = imported_name
        save_kit_config(imported)
        imported_snapshot = current_build_output_dir(imported) / f"imported-config-{time.strftime('%Y%m%d-%H%M%S')}.yml"
        imported_snapshot.write_text(yaml.safe_dump(imported, sort_keys=False), encoding="utf-8")
        cfg = load_kit_config(imported_name)
        return jsonable_encoder(
            {
                "ok": True,
                "message": f"Config imported. Current kit: {imported_name}",
                "kits": build_react_kit_library_state(cfg),
                "global": build_react_global_settings_state(cfg),
                "ilo": build_react_ilo_state(cfg),
                "app_state": build_react_ui_state(),
                "snapshot_path": str(imported_snapshot),
            }
        )
    except Exception as e:
        return jsonable_encoder(
            {
                "ok": False,
                "message": f"Config import failed: {str(e).splitlines()[0]}",
                "kits": build_react_kit_library_state(current_cfg),
            }
        )


@app.get("/api/ui/job-status")
async def api_ui_job_status():
    cfg = load_kit_config()
    return jsonable_encoder(react_ui_job_payload(load_job(cfg.get("site", {}).get("name", ""))))


@app.get("/api/ui/recent-activity")
async def api_ui_recent_activity():
    cfg = load_kit_config()
    return jsonable_encoder(build_activity_feed(load_history(cfg.get("site", {}).get("name", "")), limit=20))


@app.get("/api/ui/modules")
async def api_ui_modules():
    cfg = load_kit_config()
    history = load_history(cfg.get("site", {}).get("name", ""))
    job = load_job(cfg.get("site", {}).get("name", ""))
    return jsonable_encoder(
        {
            "modules": react_ui_module_summaries(cfg, build_workflow_contexts(cfg, job, history)),
            "actions": react_ui_action_inventory(),
        }
    )


@app.get("/api/ui/action-catalog")
async def api_ui_action_catalog():
    return jsonable_encoder(react_ui_action_catalog())


@app.get("/api/ui/run-history")
async def api_ui_run_history():
    cfg = load_kit_config()
    history = load_history(cfg.get("site", {}).get("name", ""))
    return jsonable_encoder({"history": build_history_display_entries(history), "activity": build_activity_feed(history, limit=20)})


@app.get("/api/ui/technical-events")
async def api_ui_technical_events():
    cfg = load_kit_config()
    job = load_job(cfg.get("site", {}).get("name", ""))
    return jsonable_encoder(
        {
            "job": react_ui_job_payload(job),
            "logs": [str(line) for line in list(job.get("logs") or [])][-200:],
            "trace_events": list(job.get("trace_events") or [])[-100:],
            "artifacts": react_ui_artifact_links(job),
        }
    )


@app.get("/api/ui/ilo")
async def api_ui_ilo():
    return jsonable_encoder(build_react_ilo_state())


@app.post("/api/ui/ilo/settings")
async def api_ui_ilo_settings(request: Request):
    cfg = load_kit_config()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    ilo_cfg = cfg.get("ilo", {}) or {}
    policy_updates = dict(body.get("policy") or {}) if isinstance(body.get("policy"), dict) else {}
    password_default = ilo_cfg.get("password") or ""
    payload = {
        "form": ReactFormAdapter(body),
        "ilo_current_ip": _react_body_value(body, "current_ip", "ilo_current_ip", default=ilo_cfg.get("current_ip") or ilo_cfg.get("host") or ""),
        "ilo_target_ip": _react_body_value(body, "target_ip", "ilo_target_ip", default=ilo_cfg.get("target_ip") or ""),
        "ilo_gateway": _react_body_value(body, "gateway", "ilo_gateway", default=ilo_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or ""),
        "ilo_hostname": _react_body_value(body, "hostname", "ilo_hostname", default=ilo_cfg.get("hostname") or ""),
        "ilo_username": _react_body_value(body, "username", "ilo_username", default=ilo_cfg.get("username") or ""),
        "ilo_password": _react_body_value(body, "password", "ilo_password", default=password_default),
        "policy_updates": policy_updates,
        "ilo_policy_snmp_read_community": _react_body_value(
            body,
            "snmp_read_community",
            "ilo_policy_snmp_read_community",
            default=(cfg.get("shared_snmp", {}) or {}).get("read_community") or "",
        ),
    }
    updated = _ilo_service_for_api().update_saved_ilo_settings(cfg, payload)
    cfg = updated["cfg"]
    core_ilo_input_review = build_ilo_input_review(cfg, include_policy_validation=False)
    if core_ilo_input_review["errors"]:
        return jsonable_encoder(
            {
                "ok": False,
                "message": "iLO setup needs attention before it can be saved.",
                "errors": list(core_ilo_input_review["errors"]),
                "notes": list(core_ilo_input_review["notes"]),
                "ilo": build_react_ilo_state(cfg),
            }
        )
    try:
        cfg = apply_ip_plan(cfg)
    except Exception as e:
        return jsonable_encoder({"ok": False, "message": f"Could not save iLO setup: {str(e).splitlines()[0]}", "ilo": build_react_ilo_state(cfg)})
    cfg = propagate_active_ilo_endpoint(cfg, cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host"))
    save_kit_config(cfg)
    append_activity_event(
        cfg["site"]["name"],
        "ilo_settings_saved",
        workflow="ilo",
        summary="Saved iLO settings from the React desktop UI.",
        target=cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or "",
        details=[
            f"Planned final IP: {cfg['ilo'].get('target_ip') or 'Unchanged'}",
            f"Gateway: {cfg['ilo'].get('gateway') or 'Not set'}",
            f"Hostname: {cfg['ilo'].get('hostname') or 'Not set'}",
        ],
    )
    return jsonable_encoder(
        {
            "ok": True,
            "message": "iLO setup saved.",
            "normalized_hostname": updated.get("normalized_hostname") or "",
            "ilo": build_react_ilo_state(load_kit_config()),
            "app_state": build_react_ui_state(),
        }
    )


def verify_ilo_endpoint_reachable(
    host: str,
    username: str,
    password: str,
    *,
    attempts: int = 12,
    delay_seconds: float = 5.0,
    timeout_seconds: int = 15,
    client_factory: Callable[[ILOConfig], Any] | None = None,
) -> dict[str, Any]:
    normalized_host = str(host or "").strip()
    if not normalized_host:
        return {"ok": False, "host": "", "error": "No iLO endpoint was provided."}
    factory = client_factory or ILOClient
    last_error = ""
    for attempt in range(1, max(1, attempts) + 1):
        try:
            client = factory(ILOConfig(host=normalized_host, username=username, password=password, verify_tls=False, timeout=timeout_seconds))
            summary = client.get_summary()
            return {"ok": True, "host": normalized_host, "attempt": attempt, "summary": summary}
        except Exception as e:
            last_error = str(e).splitlines()[0] or e.__class__.__name__
            if attempt < attempts and delay_seconds > 0:
                time.sleep(delay_seconds)
    return {"ok": False, "host": normalized_host, "attempts": max(1, attempts), "error": last_error or "iLO endpoint did not respond."}


def request_ilo_reset_best_effort(client: Any, *, reset_type: str = "GracefulRestart") -> dict[str, Any]:
    if not hasattr(client, "reset_ilo"):
        return {"ok": False, "status": "not_available", "error": "iLO reset action is not available on this client."}
    try:
        if all(hasattr(client, name) for name in ("get_manager", "base", "auth", "cfg")):
            manager = client.get_manager()
            target = str((((manager.get("Actions") or {}).get("#Manager.Reset") or {}).get("target")) or "").strip()
            if not target:
                return {"ok": False, "status": "not_available", "error": "Manager reset action is not available on this iLO."}
            url = target if target.startswith("http") else f"{client.base}{target}"
            response = requests.post(
                url,
                auth=client.auth,
                verify=client.cfg.verify_tls,
                timeout=(2, 5),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"ResetType": reset_type},
            )
            if response.status_code >= 400:
                return {"ok": False, "status": "failed", "error": f"POST {url} failed with HTTP {response.status_code}: {response.text[:300]}", "reset_type": reset_type}
            body = {}
            try:
                body = response.json() if response.text.strip() else {}
            except Exception:
                body = {}
            message_ids = [
                str(item.get("MessageId") or "")
                for item in (((body.get("error") or {}).get("@Message.ExtendedInfo") or []) if isinstance(body, dict) else [])
                if isinstance(item, dict)
            ]
            status = "reset_in_progress" if any("ResetInProgress" in item for item in message_ids) else "requested"
            return {"ok": True, "status": status, "path": target, "reset_type": reset_type, "http_status": response.status_code, "message_ids": message_ids}
        result = client.reset_ilo(reset_type=reset_type)
        return {"ok": True, "status": "requested", **(result or {})}
    except Exception as exc:
        message = str(exc).splitlines()[0] or exc.__class__.__name__
        lowered = message.lower()
        if any(text in lowered for text in ["connection aborted", "connection reset", "remote end closed", "remotedisconnected", "read timed out", "temporarily unreachable"]):
            return {"ok": True, "status": "disconnect_after_request", "message": message, "reset_type": reset_type}
        return {"ok": False, "status": "failed", "error": message, "reset_type": reset_type}


def request_ilo_reset_with_deadline(client: Any, *, reset_type: str = "GracefulRestart", deadline_seconds: float = 8.0) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def submit_reset() -> None:
        nonlocal result
        result = request_ilo_reset_best_effort(client, reset_type=reset_type)

    worker = threading.Thread(target=submit_reset, daemon=True)
    worker.start()
    worker.join(max(1.0, deadline_seconds))
    if worker.is_alive():
        return {
            "ok": True,
            "status": "request_timeout_after_submit",
            "message": f"Reset request did not return within {deadline_seconds:g} seconds; polling will verify whether iLO reset.",
            "reset_type": reset_type,
        }
    return result


def wait_for_ilo_endpoint_after_reset(
    host: str,
    username: str,
    password: str,
    *,
    start_timeout: float = 90.0,
    return_timeout: float = 300.0,
    poll_interval: float = 5.0,
    client_factory: Callable[[ILOConfig], Any] | None = None,
) -> dict[str, Any]:
    normalized_host = str(host or "").strip()
    if not normalized_host:
        return {"ok": False, "host": "", "interrupt_observed": False, "return_observed": False, "error": "No iLO endpoint was provided."}

    interrupt_observed = False
    interrupt_detail = "No interruption was observed before timeout."
    start_deadline = time.time() + max(start_timeout, 1.0)
    while time.time() < start_deadline:
        probe = verify_ilo_endpoint_reachable(
            normalized_host,
            username,
            password,
            attempts=1,
            delay_seconds=0,
            client_factory=client_factory,
        )
        if not probe.get("ok"):
            interrupt_observed = True
            interrupt_detail = str(probe.get("error") or "temporarily unreachable")
            break
        time.sleep(max(poll_interval, 1.0))

    if not interrupt_observed:
        return {
            "ok": False,
            "host": normalized_host,
            "interrupt_observed": False,
            "return_observed": False,
            "interrupt_detail": interrupt_detail,
            "return_detail": "",
            "error": interrupt_detail,
        }

    return_deadline = time.time() + max(return_timeout, 1.0)
    return_detail = "iLO did not come back before timeout."
    while time.time() < return_deadline:
        probe = verify_ilo_endpoint_reachable(
            normalized_host,
            username,
            password,
            attempts=1,
            delay_seconds=0,
            client_factory=client_factory,
        )
        if probe.get("ok"):
            return {
                "ok": True,
                "host": normalized_host,
                "interrupt_observed": True,
                "return_observed": True,
                "interrupt_detail": interrupt_detail,
                "return_detail": f"Reconnected to iLO on {normalized_host}.",
                "summary": probe.get("summary") or {},
            }
        return_detail = str(probe.get("error") or return_detail)
        time.sleep(max(poll_interval, 1.0))

    return {
        "ok": False,
        "host": normalized_host,
        "interrupt_observed": True,
        "return_observed": False,
        "interrupt_detail": interrupt_detail,
        "return_detail": return_detail,
        "error": return_detail,
    }


def read_ilo_network_activation_state(host: str, username: str, password: str) -> dict[str, Any]:
    normalized_host = str(host or "").strip()
    if not normalized_host:
        return {"ok": False, "host": "", "error": "No iLO endpoint was provided."}
    try:
        client = ILOClient(ILOConfig(host=normalized_host, username=username, password=password, verify_tls=False, timeout=15))
        iface = client.get_active_manager_interface()
        ipv4_values = [
            str(item.get("Address") or "").strip()
            for item in list(iface.get("IPv4Addresses") or []) + list(iface.get("IPv4StaticAddresses") or [])
            if isinstance(item, dict) and str(item.get("Address") or "").strip()
        ]
        oem_hpe = ((iface.get("Oem") or {}).get("Hpe") or {}) if isinstance(iface, dict) else {}
        return {
            "ok": True,
            "host": normalized_host,
            "path": str(iface.get("@odata.id") or ""),
            "ipv4_values": ipv4_values,
            "configuration_settings": str(oem_hpe.get("ConfigurationSettings") or ""),
            "interface_type": str(oem_hpe.get("InterfaceType") or ""),
            "link_status": str(iface.get("LinkStatus") or ""),
        }
    except Exception as exc:
        return {"ok": False, "host": normalized_host, "error": str(exc).splitlines()[0] or exc.__class__.__name__}


def run_ilo_ip_setup_in_background(cfg: dict[str, Any]) -> None:
    kit_name = str((cfg.get("site") or {}).get("name") or "")
    ilo_cfg = cfg.get("ilo", {}) or {}
    login_ip = str(ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    target_ip = str(ilo_cfg.get("target_ip") or (cfg.get("ip_plan") or {}).get("ilo") or "").strip()
    gateway = str(ilo_cfg.get("gateway") or (cfg.get("ip_plan") or {}).get("gateway") or "").strip()
    subnet_mask = str(ilo_cfg.get("subnet_mask") or (cfg.get("ip_plan") or {}).get("netmask") or "255.255.255.0").strip()
    username = str(ilo_cfg.get("username") or "").strip()
    password = str(ilo_cfg.get("password") or "")
    job = load_job(kit_name)
    try:
        update_job(kit_name, job, "Running", "Setup iLO IP", 1, 5, f"[RUNNING] Connecting to iLO at {login_ip}.", progress_percent=10)
        client = ILOClient(ILOConfig(host=login_ip, username=username, password=password, verify_tls=False, timeout=15))
        update_job(
            kit_name,
            job,
            "Running",
            "Apply static IPv4",
            2,
            5,
            f"[RUNNING] Applying static IPv4 address={target_ip} subnet_mask={subnet_mask} gateway={gateway}.",
            progress_percent=45,
        )
        verification_host = target_ip or login_ip
        ip_changed = bool(target_ip and target_ip != login_ip)
        target_reached_during_apply = False
        try:
            result = client.set_static_ipv4_best_effort(address=target_ip, subnet_mask=subnet_mask, gateway=gateway)
        except Exception as apply_error:
            if not ip_changed:
                raise
            update_job(
                kit_name,
                load_job(kit_name),
                "Running",
                "Reconnect to iLO",
                2,
                5,
                (
                    "[WARN] Lost the old iLO endpoint during static IPv4 apply. "
                    f"Checking whether the final endpoint {verification_host} is now reachable. detail={str(apply_error).splitlines()[0]}"
                ),
                progress_percent=60,
            )
            transition_check = verify_ilo_endpoint_reachable(verification_host, username, password, attempts=8, delay_seconds=5, timeout_seconds=2)
            if not transition_check.get("ok"):
                raise
            result = {
                "applied_keys": ["old endpoint dropped during static IPv4 apply", "target endpoint verified"],
                "transition_check": transition_check,
            }
            target_reached_during_apply = True
            update_job(
                kit_name,
                load_job(kit_name),
                "Running",
                "Reconnect to iLO",
                3,
                5,
                f"[RUNNING] Final iLO endpoint {verification_host} is reachable after the old endpoint dropped.",
                progress_percent=78,
            )
        if ip_changed and not target_reached_during_apply:
            update_job(
                kit_name,
                load_job(kit_name),
                "Running",
                "Reset iLO",
                3,
                5,
                "[RUNNING] iLO IP changed; requesting GracefulRestart before final reachability verification.",
                progress_percent=62,
            )
            reset_result = request_ilo_reset_with_deadline(client, reset_type="GracefulRestart")
            if not reset_result.get("ok"):
                update_job(
                    kit_name,
                    load_job(kit_name),
                    "Failed",
                    "Reset iLO",
                    5,
                    5,
                    f"[FAILED] iLO IP changed, but iLO reset could not be requested: {reset_result.get('error') or reset_result.get('status') or 'unknown error'}",
                    progress_percent=100,
                )
                return
            update_job(
                kit_name,
                load_job(kit_name),
                "Running",
                "Wait for iLO reset",
                3,
                5,
                f"[RUNNING] iLO reset request status={reset_result.get('status')}; waiting for old endpoint {login_ip} to reset before checking {verification_host}.",
                progress_percent=70,
            )
            reset_wait = wait_for_ilo_endpoint_after_reset(login_ip, username, password, start_timeout=30, return_timeout=30)
            if not reset_wait.get("ok"):
                update_job(
                    kit_name,
                    load_job(kit_name),
                    "Running",
                    "Wait for iLO reset",
                    3,
                    5,
                    (
                        "[WARN] The old iLO endpoint reset window was not fully observed; "
                        f"continuing to final endpoint verification. detail={reset_wait.get('return_detail') or reset_wait.get('error') or 'No response'}"
                    ),
                    progress_percent=74,
                )
        else:
            update_job(
                kit_name,
                load_job(kit_name),
                "Running",
                "Reset iLO",
                3,
                5,
                (
                    f"[SKIP] Final iLO endpoint {verification_host} is already reachable after static IPv4 apply; reset wait is not required."
                    if target_reached_during_apply
                    else "[SKIP] Current and final iLO IP match; reset is not required for this IP setup action."
                ),
                progress_percent=70,
            )
        update_job(
            kit_name,
            load_job(kit_name),
            "Running",
            "Verify iLO reachability",
            4,
            5,
            f"[RUNNING] Verifying iLO endpoint is reachable at {verification_host}.",
            progress_percent=82,
        )
        verification = verify_ilo_endpoint_reachable(verification_host, username, password, attempts=2, delay_seconds=3, timeout_seconds=3)
        if ip_changed and not verification.get("ok"):
            old_state = read_ilo_network_activation_state(login_ip, username, password)
            if old_state.get("ok") and verification_host in list(old_state.get("ipv4_values") or []) and "pendingreset" in str(old_state.get("configuration_settings") or "").replace(" ", "").lower():
                update_job(
                    kit_name,
                    load_job(kit_name),
                    "Running",
                    "Force iLO network activation",
                    4,
                    5,
                    (
                        f"[RUNNING] Final IP {verification_host} is configured in Redfish but still pending reset on old endpoint {login_ip}; "
                        "requesting Manager.Reset ForceRestart once."
                    ),
                    progress_percent=88,
                )
                force_client = ILOClient(ILOConfig(host=login_ip, username=username, password=password, verify_tls=False, timeout=15))
                force_result = request_ilo_reset_with_deadline(force_client, reset_type="ForceRestart")
                if force_result.get("ok"):
                    update_job(
                        kit_name,
                        load_job(kit_name),
                        "Running",
                        "Force iLO network activation",
                        4,
                        5,
                        f"[RUNNING] ForceRestart request status={force_result.get('status')}; polling final endpoint {verification_host}.",
                        progress_percent=90,
                    )
                    verification = verify_ilo_endpoint_reachable(verification_host, username, password, attempts=8, delay_seconds=5, timeout_seconds=2)
                else:
                    update_job(
                        kit_name,
                        load_job(kit_name),
                        "Running",
                        "Force iLO network activation",
                        4,
                        5,
                        f"[WARN] ForceRestart could not be requested: {force_result.get('error') or force_result.get('status') or 'unknown error'}",
                        progress_percent=90,
                    )
        if not verification.get("ok"):
            old_state = read_ilo_network_activation_state(login_ip, username, password) if ip_changed else {}
            append_activity_event(
                kit_name,
                "ilo_ip_setup_unverified",
                workflow="ilo",
                summary="iLO IP setup did not verify reachability.",
                target=verification_host,
                details=[
                    f"Login IP: {login_ip}",
                    f"Target IP: {target_ip}",
                    f"Gateway: {gateway}",
                    f"Verification error: {verification.get('error') or 'No response'}",
                    (
                        "Old endpoint state: "
                        f"reachable={old_state.get('ok')} ips={old_state.get('ipv4_values') or []} "
                        f"configuration_settings={old_state.get('configuration_settings') or ''}"
                    )
                    if old_state
                    else "Old endpoint state: not checked",
                    f"Applied keys: {', '.join(result.get('applied_keys') or []) or 'not reported'}",
                ],
            )
            update_job(
                kit_name,
                load_job(kit_name),
                "Failed",
                "iLO reachability verification failed",
                5,
                5,
                (
                    f"[FAILED] iLO IP setup did not verify reachability at {verification_host}: {verification.get('error') or 'No response'}"
                    + (
                        f" | old_endpoint={login_ip} reachable={old_state.get('ok')} redfish_ips={old_state.get('ipv4_values') or []} "
                        f"configuration_settings={old_state.get('configuration_settings') or ''}"
                        if old_state
                        else ""
                    )
                ),
                progress_percent=100,
            )
            return
        latest_cfg = load_kit_config(kit_name)
        latest_cfg = propagate_active_ilo_endpoint(latest_cfg, verification_host)
        latest_cfg["ilo"]["target_ip"] = target_ip
        latest_cfg["ilo"]["gateway"] = gateway
        latest_cfg["ilo"]["subnet_mask"] = subnet_mask
        save_kit_config(latest_cfg)
        append_activity_event(
            kit_name,
            "ilo_ip_setup_verified",
            workflow="ilo",
            summary="Verified iLO endpoint reachable after static IP setup.",
            target=verification_host,
            details=[
                f"Login IP: {login_ip}",
                f"Target IP: {target_ip}",
                f"Gateway: {gateway}",
                f"Verification attempt: {verification.get('attempt') or 'unknown'}",
                f"Applied keys: {', '.join(result.get('applied_keys') or []) or 'not reported'}",
            ],
        )
        update_job(
            kit_name,
            load_job(kit_name),
            "Complete",
            "iLO IP setup verified",
            5,
            5,
            f"[VERIFIED] iLO endpoint is reachable at {verification_host}.",
            progress_percent=100,
        )
    except Exception as e:
        update_job(
            kit_name,
            load_job(kit_name),
            "Failed",
            "iLO IP setup failed",
            5,
            5,
            f"[FAILED] iLO IP setup failed: {str(e).splitlines()[0]}",
            progress_percent=100,
        )


@app.post("/api/ui/ilo/setup-ip")
async def api_ui_ilo_setup_ip():
    cfg = apply_ip_plan(load_kit_config())
    cfg = propagate_active_ilo_endpoint(cfg, cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host"))
    ilo_cfg = cfg.get("ilo", {}) or {}
    missing = []
    if not str(ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip():
        missing.append("current iLO IP")
    if not str(ilo_cfg.get("target_ip") or (cfg.get("ip_plan") or {}).get("ilo") or "").strip():
        missing.append("target iLO IP")
    if not str(ilo_cfg.get("gateway") or (cfg.get("ip_plan") or {}).get("gateway") or "").strip():
        missing.append("gateway")
    if not str(ilo_cfg.get("username") or "").strip():
        missing.append("username")
    if not str(ilo_cfg.get("password") or ""):
        missing.append("password")
    if missing:
        return jsonable_encoder(
            {
                "ok": False,
                "message": f"iLO IP setup needs {', '.join(missing)}.",
                "ilo": build_react_ilo_state(cfg),
                "app_state": build_react_ui_state(),
            }
        )
    kit_name = str((cfg.get("site") or {}).get("name") or "")
    save_kit_config(cfg)
    save_job(
        kit_name,
        {
            "status": "Real run queued",
            "execution_mode": "real",
            "execution_mode_label": "Setup iLO IP",
            "scope": "ilo-ip-setup",
            "current_stage": "Queued",
            "progress_percent": 0,
            "completed_steps": 0,
            "total_steps": 5,
            "logs": ["[QUEUED] iLO IP setup requested from the React desktop UI."],
            "root_scope": "ilo-ip-setup",
            "stage_statuses": {"ilo": "queued"},
        },
    )
    threading.Thread(target=run_ilo_ip_setup_in_background, args=(copy.deepcopy(cfg),), daemon=True).start()
    return jsonable_encoder(
        {
            "ok": True,
            "message": "iLO IP setup started; final completion waits for reachability verification.",
            "ilo": build_react_ilo_state(load_kit_config()),
            "app_state": build_react_ui_state(),
        }
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return react_app_response(request, title="Lab Builder")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="dashboard")


@app.get("/execution", response_class=HTMLResponse)
async def execution_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="execution")


def react_app_response(request: Request, title: str = "Lab Builder") -> HTMLResponse:
    cfg = load_kit_config()
    try:
        react_asset_version = str(int((STATIC_DIR / "js" / "react-desktop-ui.js").stat().st_mtime))
    except Exception:
        react_asset_version = app_version()
    return templates.TemplateResponse(
        request=request,
        name="react_preview.html",
        context={
            "title": title,
            "current_kit": cfg.get("site", {}).get("name", ""),
            "app_version": app_version(),
            "react_asset_version": react_asset_version,
        },
    )


@app.get("/react-preview", response_class=HTMLResponse)
async def react_preview_page(request: Request):
    return RedirectResponse(url="/", status_code=308)


@app.get("/global-settings", response_class=HTMLResponse)
async def global_settings_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="global_settings")


@app.get("/upgrade-helper", response_class=HTMLResponse)
async def upgrade_helper_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="upgrade_helper")


@app.post("/save-upgrade-policies", response_class=HTMLResponse)
async def save_upgrade_policies_route(
    request: Request,
    return_page: str = Form("upgrade_helper"),
    policy_ilo: str = Form("block"),
    policy_netapp: str = Form("block"),
    policy_cisco_switch: str = Form("block"),
):
    cfg = load_kit_config()
    cfg.setdefault("upgrade_helper", {})
    cfg["upgrade_helper"]["policies"] = normalize_upgrade_policies(
        policies={
            "ilo": policy_ilo,
            "netapp": policy_netapp,
            "cisco_switch": policy_cisco_switch,
        }
    )
    save_kit_config(cfg)
    page = str(return_page or "upgrade_helper").strip().lower()
    if page not in {"upgrade_helper", "global_settings"}:
        page = "upgrade_helper"
    return render_page(
        request,
        cfg,
        active_page=page,
        action_feedback=build_action_feedback(
            "Upgrade policies saved",
            "Updated how Upgrade Helper treats iLO, ONTAP, and Cisco version gaps before prebuild execution.",
            tone="ready",
            outcomes=[
                f"iLO policy: {cfg['upgrade_helper']['policies'].get('ilo', 'block')}",
                f"ONTAP policy: {cfg['upgrade_helper']['policies'].get('netapp', 'block')}",
                f"Cisco policy: {cfg['upgrade_helper']['policies'].get('cisco_switch', 'block')}",
            ],
        ),
    )


@app.post("/save-upgrade-override", response_class=HTMLResponse)
async def save_upgrade_override_route(
    request: Request,
    return_page: str = Form("upgrade_helper"),
    device_key: str = Form(""),
    override_upgrade_gate: str | None = Form(None),
):
    cfg = load_kit_config()
    key = str(device_key or "").strip()
    if key not in {"ilo", "netapp", "cisco_switch"}:
        key = ""
    cfg.setdefault("upgrade_helper", {}).setdefault("overrides", {})
    if key:
        cfg["upgrade_helper"]["overrides"][key] = bool(override_upgrade_gate)
    save_kit_config(cfg)
    page = str(return_page or "upgrade_helper").strip().lower()
    allowed_pages = {"upgrade_helper", "ilo", "netapp", "cisco", "global_settings"}
    if page not in allowed_pages:
        page = "upgrade_helper"
    return render_page(
        request,
        cfg,
        active_page=page,
        action_feedback=build_action_feedback(
            "Upgrade override saved",
            "Updated whether this device can continue configuration setup while the upgrade gate is still unresolved.",
            tone="progress" if key and cfg["upgrade_helper"]["overrides"].get(key) else "ready",
            outcomes=[f"Device: {key or 'unknown'}", f"Override: {'enabled' if key and cfg['upgrade_helper']['overrides'].get(key) else 'disabled'}"],
        ),
    )


def safe_upload_filename(filename: str) -> str:
    name = Path(str(filename or "")).name.strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    name = name.strip(".-")
    return name[:180]


@app.post("/upload-upgrade-media", response_class=HTMLResponse)
async def upload_upgrade_media_route(
    request: Request,
    return_page: str = Form("upgrade_helper"),
    media_file: UploadFile = File(...),
):
    cfg = load_kit_config()
    filename = safe_upload_filename(media_file.filename or "")
    if not filename:
        return render_page(
            request,
            cfg,
            active_page="upgrade_helper",
            error_message="No firmware or media file was selected.",
        )
    suffixes = [suffix.lower() for suffix in Path(filename).suffixes]
    if not suffixes or not any(suffix in ALLOWED_MEDIA_UPLOAD_SUFFIXES for suffix in suffixes):
        return render_page(
            request,
            cfg,
            active_page="upgrade_helper",
            error_message="Unsupported media file type. Upload firmware, ISO, ONTAP, or switch image files only.",
        )

    FIRMWARE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = FIRMWARE_UPLOAD_DIR / filename
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = FIRMWARE_UPLOAD_DIR / f"{target.stem}-{stamp}{target.suffix}"

    with target.open("wb") as handle:
        while chunk := await media_file.read(1024 * 1024):
            handle.write(chunk)
    await media_file.close()

    page = str(return_page or "upgrade_helper").strip().lower()
    if page not in {"upgrade_helper", "global_settings"}:
        page = "upgrade_helper"
    return render_page(
        request,
        cfg,
        active_page=page,
        action_feedback=build_action_feedback(
            "Firmware media uploaded",
            "The file was saved under media/firmware. Media folders are mounted runtime data and are never included in release packages or Docker images.",
            tone="ready",
            outcomes=[
                f"File: {target.name}",
                "Location: media/firmware",
            ],
        ),
    )


@app.post("/plan-ilo-upgrade", response_class=HTMLResponse)
async def plan_ilo_upgrade_route(request: Request, return_page: str = Form("upgrade_helper")):
    cfg = load_kit_config()
    sync_ilo_upgrade_inventory_from_latest_live(cfg)
    media_scan = scan_upgrade_media()
    plan = build_ilo_upgrade_plan(cfg, media_scan)
    cfg.setdefault("ilo", {})
    cfg["ilo"].setdefault("upgrade", {})
    cfg["ilo"]["upgrade"]["last_plan"] = plan
    _clear_stale_ilo_upgrade_block(cfg, plan)
    save_kit_config(cfg)
    details = list(plan.get("blockers") or []) + list(plan.get("warnings") or []) + list(plan.get("notes") or [])
    return render_page(
        request,
        cfg,
        active_page=return_page if return_page in {"upgrade_helper", "ilo"} else "upgrade_helper",
        action_feedback=build_action_feedback(
            "iLO upgrade plan ready" if plan.get("ready") else "iLO upgrade plan needs attention",
            (
                f"Matched {plan.get('media_filename') or 'no media file'} for {plan.get('manager_model') or 'unknown iLO family'}."
                if plan.get("ready")
                else "Review the matched media, current version, and saved iLO identity before attempting the upgrade."
            ),
            tone="ready" if plan.get("ready") else "pending",
            outcomes=[
                f"Target: {plan.get('host') or 'Not set'}",
                f"Current version: {plan.get('current_version') or 'Unknown'}",
                f"Matched media: {plan.get('media_version') or 'Not found'}",
            ],
            details=details,
            links=[{"label": "Open Upgrade Helper", "href": "/upgrade-helper"}, {"label": "Open iLO", "href": "/ilo"}],
        ),
    )


def _record_ilo_upgrade_activity(cfg: dict[str, Any], event: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    progress_by_phase = {
        "queued": 5,
        "precheck": 10,
        "upload": 35,
        "verify": 75,
        "complete": 100,
        "blocked": 100,
        "failed": 100,
    }
    cfg.setdefault("ilo", {}).setdefault("upgrade", {})
    activity = cfg["ilo"]["upgrade"].setdefault("activity", {})
    events = list(activity.get("events") or [])
    events.append(event)
    phase = str(event.get("phase") or activity.get("phase") or "")
    try:
        progress_percent = int(event.get("progress_percent")) if event.get("progress_percent") is not None else progress_by_phase.get(phase, int(activity.get("progress_percent") or 0))
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
    if not activity.get("started_at"):
        activity["started_at"] = event.get("timestamp") or datetime.now().isoformat()
    save_kit_config(cfg)
    return activity


def _clear_stale_ilo_upgrade_block(cfg: dict[str, Any], plan: dict[str, Any]) -> None:
    if not plan.get("ready"):
        return
    upgrade = cfg.setdefault("ilo", {}).setdefault("upgrade", {})
    activity = dict(upgrade.get("activity") or {})
    result = dict(upgrade.get("last_result") or {})
    stale_statuses = {str(activity.get("status") or "").lower(), str(result.get("status") or "").lower()}
    if "blocked" not in stale_statuses:
        return
    upgrade["activity"] = {}
    upgrade["last_result"] = {}


def _start_ilo_upgrade_worker(cfg: dict[str, Any], media_scan: dict[str, Any]) -> None:
    def progress(event: dict[str, Any]) -> None:
        _record_ilo_upgrade_activity(cfg, event, status="running")

    def worker() -> None:
        try:
            result = execute_ilo_upgrade(
                cfg,
                media_scan,
                build_client=lambda *, host, username, password: ILOClient(
                    ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=30)
                ),
                progress=progress,
            )
            cfg.setdefault("ilo", {}).setdefault("upgrade", {})["last_result"] = result
            _record_ilo_upgrade_activity(
                cfg,
                {
                    "phase": "complete",
                    "message": "iLO firmware upgrade completed and version was verified.",
                    "timestamp": result.get("completed_at") or datetime.now().isoformat(),
                    "progress_percent": 100,
                },
                status="completed",
            )
            save_kit_config(cfg)
        except Exception as exc:
            error = str(exc).splitlines()[0] if str(exc).strip() else "iLO upgrade failed."
            cfg.setdefault("ilo", {}).setdefault("upgrade", {})["last_result"] = {
                "status": "failed",
                "failed_at": datetime.now().isoformat(),
                "error": error,
            }
            _record_ilo_upgrade_activity(
                cfg,
                {"phase": "failed", "message": error, "timestamp": datetime.now().isoformat(), "progress_percent": 100},
                status="failed",
            )
            save_kit_config(cfg)

    threading.Thread(target=worker, name="ilo-upgrade-worker", daemon=True).start()


@app.post("/run-ilo-upgrade", response_class=HTMLResponse)
async def run_ilo_upgrade_route(request: Request, return_page: str = Form("upgrade_helper")):
    cfg = load_kit_config()
    sync_ilo_upgrade_inventory_from_latest_live(cfg)
    media_scan = scan_upgrade_media()
    plan = build_ilo_upgrade_plan(cfg, media_scan)
    cfg.setdefault("ilo", {})
    cfg["ilo"].setdefault("upgrade", {})
    cfg["ilo"]["upgrade"]["last_plan"] = plan
    activity = dict((cfg["ilo"].get("upgrade") or {}).get("activity") or {})
    active_page = return_page if return_page in {"upgrade_helper", "ilo"} else "upgrade_helper"
    if str(activity.get("status") or "").lower() == "running":
        return render_page(
            request,
            cfg,
            active_page=active_page,
            action_feedback=build_action_feedback(
                "iLO upgrade already running",
                "Watch the iLO upgrade status panel for upload, verification, completion, or errors.",
                tone="pending",
                outcomes=[f"Phase: {activity.get('phase') or 'unknown'}", f"Last message: {activity.get('message') or 'waiting'}"],
            ),
        )
    if not plan.get("ready"):
        cfg["ilo"]["upgrade"]["last_result"] = {"status": "blocked", "error": "; ".join(plan.get("blockers") or [])}
        _record_ilo_upgrade_activity(
            cfg,
            {"phase": "blocked", "message": "; ".join(plan.get("blockers") or ["iLO upgrade prechecks are not satisfied."]), "timestamp": datetime.now().isoformat(), "progress_percent": 100},
            status="blocked",
        )
        save_kit_config(cfg)
        return render_page(
            request,
            cfg,
            active_page=active_page,
            action_feedback=build_action_feedback(
                "iLO upgrade blocked",
                "The upgrade prechecks did not pass. Review the blockers before trying to flash firmware.",
                tone="pending",
                outcomes=[
                    f"Target: {plan.get('host') or 'Not set'}",
                    f"Current version: {plan.get('current_version') or 'Unknown'}",
                    f"Matched media: {plan.get('media_version') or 'Not found'}",
                ],
                details=list(plan.get("blockers") or []) + list(plan.get("warnings") or []) + list(plan.get("notes") or []),
            ),
        )
    now = datetime.now().isoformat()
    cfg["ilo"]["upgrade"]["activity"] = {
        "status": "running",
        "phase": "queued",
        "message": "iLO upgrade worker queued.",
        "started_at": now,
        "updated_at": now,
        "progress_percent": 5,
        "events": [{"phase": "queued", "message": "iLO upgrade worker queued.", "timestamp": now, "progress_percent": 5}],
    }
    save_kit_config(cfg)
    _start_ilo_upgrade_worker(cfg, media_scan)
    return render_page(
        request,
        cfg,
        active_page=active_page,
        action_feedback=build_action_feedback(
            "iLO upgrade started",
            "The firmware upgrade is running in the background. Watch the iLO upgrade status panel for upload, verification, completion, or errors.",
            tone="pending",
            outcomes=[
                f"Target: {plan.get('host') or 'Not set'}",
                f"Current version: {plan.get('current_version') or 'Unknown'}",
                f"Target version: {plan.get('media_version') or 'Unknown'}",
            ],
        ),
    )


@app.get("/ilo-upgrade-activity", response_class=HTMLResponse)
async def ilo_upgrade_activity_route(request: Request):
    cfg = load_kit_config()
    return templates.TemplateResponse(request, "partials/components/ilo_upgrade_activity.html", {"cfg": cfg})


@app.get("/ilo", response_class=HTMLResponse)
async def ilo_page(request: Request):
    return await ilo_page_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "render_page": render_page,
        },
    )


@app.get("/esxi", response_class=HTMLResponse)
async def esxi_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="esxi")


@app.get("/windows", response_class=HTMLResponse)
async def windows_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="windows")


@app.get("/qnap", response_class=HTMLResponse)
async def qnap_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="qnap")


@app.get("/configuration", response_class=HTMLResponse)
async def configuration_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="global_settings")


@app.get("/configs", response_class=HTMLResponse)
async def configs_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="configs")


@app.get("/storage", response_class=HTMLResponse)
async def storage_page(request: Request):
    return await storage_page_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "render_page": render_page,
        },
    )


@app.post("/save-storage-target", response_class=HTMLResponse)
async def save_storage_target(
    request: Request,
    return_page: str = Form("storage"),
    storage_target_host: str = Form(""),
    storage_username: str = Form(""),
    storage_password: str = Form(""),
    storage_target_mode: str = Form("override"),
):
    return await save_storage_target_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "ensure_storage_config": ensure_storage_config,
            "refresh_storage_approval_from_saved_state": refresh_storage_approval_from_saved_state,
            "save_kit_config": save_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "resolve_storage_target_credentials": resolve_storage_target_credentials,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        storage_target_host=storage_target_host,
        storage_username=storage_username,
        storage_password=storage_password,
        storage_target_mode=storage_target_mode,
    )


@app.get("/kits", response_class=HTMLResponse)
async def kits_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="dashboard")


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="history")


@app.post("/load-kit", response_class=HTMLResponse)
async def load_kit_route(request: Request, selected_kit: str = Form(...), return_page: str = Form("dashboard")):
    return await load_kit_handler(
        request,
        runtime={
            "set_current_kit_name": set_current_kit_name,
            "load_kit_config": load_kit_config,
            "render_page": render_page,
        },
        selected_kit=selected_kit,
        return_page=return_page,
    )


@app.post("/new-kit", response_class=HTMLResponse)
async def new_kit_route(request: Request, new_kit_name: str = Form(...), return_page: str = Form("dashboard")):
    return await new_kit_handler(
        request,
        runtime={
            "sanitize_kit_name": sanitize_kit_name,
            "default_config": default_config,
            "save_kit_config": save_kit_config,
            "save_job": save_job,
            "save_history": save_history,
            "render_page": render_page,
        },
        new_kit_name=new_kit_name,
        return_page=return_page,
    )


@app.post("/save-config", response_class=HTMLResponse)
async def save_config_route(
    request: Request,
    return_page: str = Form("configuration"),
    site_name: str = Form(...),
    shared_subnet: str = Form(...),
    gateway_ip: str = Form(...),
    switch_ip: str = Form(...),
    esxi_ip: str = Form(...),
    ilo_ip: str = Form(""),
    ilo_target_ip: str = Form(""),
    windows_ip: str = Form(...),
    qnap_ip: str = Form(...),
    iosafe_ip: str = Form(...),
    netapp_ip: str = Form(""),
    dns1: str = Form(""),
    dns2: str = Form(""),
    dns3: str = Form(""),
    dns4: str = Form(""),
    snmp_v3_username: str = Form(""),
    snmp_v3_auth_protocol: str = Form("SHA"),
    snmp_v3_auth_password: str = Form(""),
    snmp_v3_priv_protocol: str = Form("AES"),
    snmp_v3_priv_password: str = Form(""),
    included_ilo: str | None = Form(None),
    included_esxi: str | None = Form(None),
    included_windows: str | None = Form(None),
    included_qnap: str | None = Form(None),
    included_iosafe: str | None = Form(None),
    included_cisco_switch: str | None = Form(None),
    included_storage: str | None = Form(None),
    included_netapp: str | None = Form(None),
    section_basics_complete: str = Form("false"),
    section_network_complete: str = Form("false"),
    section_included_complete: str = Form("false"),
    section_credentials_complete: str = Form("false"),
    ilo_current_ip: str = Form(""),
    ilo_subnet_mask: str = Form(""),
    ilo_gateway: str = Form(""),
    ilo_dns1: str = Form(""),
    ilo_dns2: str = Form(""),
    ilo_dns3: str = Form(""),
    ilo_dns4: str = Form(""),
    ilo_hostname: str = Form(""),
    ilo_username: str = Form(""),
    ilo_password: str = Form(""),
    esxi_hostname: str = Form(""),
    esxi_root_password: str = Form(""),
    windows_vm_name: str = Form(""),
    windows_admin_password: str = Form(""),
    qnap_hostname: str = Form(""),
    qnap_username: str = Form(""),
    qnap_password: str = Form(""),
    iosafe_hostname: str = Form(""),
    iosafe_username: str = Form(""),
    iosafe_password: str = Form(""),
    cisco_switch_hostname: str = Form(""),
    cisco_switch_username: str = Form(""),
    cisco_switch_password: str = Form(""),
    cisco_console_port: str = Form(""),
    cisco_console_baud: int = Form(9600),
    cisco_management_vlan: int = Form(10),
    cisco_management_ip: str = Form(""),
    cisco_subnet_mask: str = Form(""),
    cisco_gateway: str = Form(""),
    cisco_enable_password: str = Form(""),
    netapp_host: str = Form(""),
    netapp_username: str = Form("admin"),
    netapp_password: str = Form(""),
    netapp_storage_protocol: str = Form("nfs"),
    netapp_iscsi_commands: str = Form(""),
    netapp_nfs_commands: str = Form(""),
):
    return await save_config_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "default_ip_offsets": DEFAULT_IP_OFFSETS,
            "build_default_ip_plan": build_default_ip_plan,
            "sanitize_kit_name": sanitize_kit_name,
            "extract_snmp_users_from_form": extract_snmp_users_from_form,
            "normalize_ilo_hostname": normalize_ilo_hostname,
            "extract_ilo_additional_users_from_form": extract_ilo_additional_users_from_form,
            "merge_defaults": merge_defaults,
            "build_snmp_input_review": build_snmp_input_review,
            "build_ilo_input_review": build_ilo_input_review,
            "apply_ip_plan": apply_ip_plan,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        site_name=site_name,
        shared_subnet=shared_subnet,
        gateway_ip=gateway_ip,
        switch_ip=switch_ip,
        esxi_ip=esxi_ip,
        ilo_ip=ilo_ip,
        ilo_target_ip=ilo_target_ip,
        windows_ip=windows_ip,
        qnap_ip=qnap_ip,
        iosafe_ip=iosafe_ip,
        netapp_ip=netapp_ip,
        dns1=dns1,
        dns2=dns2,
        dns3=dns3,
        dns4=dns4,
        snmp_v3_username=snmp_v3_username,
        snmp_v3_auth_protocol=snmp_v3_auth_protocol,
        snmp_v3_auth_password=snmp_v3_auth_password,
        snmp_v3_priv_protocol=snmp_v3_priv_protocol,
        snmp_v3_priv_password=snmp_v3_priv_password,
        included_ilo=included_ilo,
        included_esxi=included_esxi,
        included_windows=included_windows,
        included_qnap=included_qnap,
        included_iosafe=included_iosafe,
        included_cisco_switch=included_cisco_switch,
        included_storage=included_storage,
        included_netapp=included_netapp,
        section_basics_complete=section_basics_complete,
        section_network_complete=section_network_complete,
        section_included_complete=section_included_complete,
        section_credentials_complete=section_credentials_complete,
        ilo_current_ip=ilo_current_ip,
        ilo_subnet_mask=ilo_subnet_mask,
        ilo_gateway=ilo_gateway,
        ilo_dns1=ilo_dns1,
        ilo_dns2=ilo_dns2,
        ilo_dns3=ilo_dns3,
        ilo_dns4=ilo_dns4,
        ilo_hostname=ilo_hostname,
        ilo_username=ilo_username,
        ilo_password=ilo_password,
        esxi_hostname=esxi_hostname,
        esxi_root_password=esxi_root_password,
        windows_vm_name=windows_vm_name,
        windows_admin_password=windows_admin_password,
        qnap_hostname=qnap_hostname,
        qnap_username=qnap_username,
        qnap_password=qnap_password,
        iosafe_hostname=iosafe_hostname,
        iosafe_username=iosafe_username,
        iosafe_password=iosafe_password,
        cisco_switch_hostname=cisco_switch_hostname,
        cisco_switch_username=cisco_switch_username,
        cisco_switch_password=cisco_switch_password,
        cisco_console_port=cisco_console_port,
        cisco_console_baud=cisco_console_baud,
        cisco_management_vlan=cisco_management_vlan,
        cisco_management_ip=cisco_management_ip,
        cisco_subnet_mask=cisco_subnet_mask,
        cisco_gateway=cisco_gateway,
        cisco_enable_password=cisco_enable_password,
        netapp_host=netapp_host,
        netapp_username=netapp_username,
        netapp_password=netapp_password,
        netapp_storage_protocol=netapp_storage_protocol,
        netapp_iscsi_commands=netapp_iscsi_commands,
        netapp_nfs_commands=netapp_nfs_commands,
    )


@app.post("/save-global-settings", response_class=HTMLResponse)
async def save_global_settings_route(
    request: Request,
    return_page: str = Form("global_settings"),
    site_name: str = Form(...),
    shared_subnet: str = Form(...),
    gateway_ip: str = Form(...),
    switch_ip: str | None = Form(None),
    esxi_ip: str | None = Form(None),
    ilo_target_ip: str | None = Form(None),
    windows_ip: str | None = Form(None),
    qnap_ip: str | None = Form(None),
    iosafe_ip: str | None = Form(None),
    netapp_ip: str | None = Form(None),
    dns1: str = Form(""),
    dns2: str = Form(""),
    dns3: str = Form(""),
    dns4: str = Form(""),
    snmp_v3_username: str = Form(""),
    snmp_v3_auth_protocol: str = Form("SHA"),
    snmp_v3_auth_password: str = Form(""),
    snmp_v3_priv_protocol: str = Form("AES"),
    snmp_v3_priv_password: str = Form(""),
    included_ilo: str | None = Form(None),
    included_esxi: str | None = Form(None),
    included_windows: str | None = Form(None),
    included_qnap: str | None = Form(None),
    included_iosafe: str | None = Form(None),
    included_cisco_switch: str | None = Form(None),
    included_storage: str | None = Form(None),
    included_netapp: str | None = Form(None),
    netapp_host: str | None = Form(None),
    netapp_username: str | None = Form(None),
    netapp_password: str | None = Form(None),
    netapp_storage_protocol: str | None = Form(None),
    netapp_iscsi_commands: str | None = Form(None),
    netapp_nfs_commands: str | None = Form(None),
    cisco_switch_hostname: str | None = Form(None),
    cisco_switch_username: str | None = Form(None),
    cisco_switch_password: str | None = Form(None),
    cisco_console_port: str | None = Form(None),
    cisco_console_baud: int | None = Form(None),
    cisco_management_vlan: int | None = Form(None),
    cisco_management_ip: str | None = Form(None),
    cisco_subnet_mask: str | None = Form(None),
    cisco_gateway: str | None = Form(None),
    cisco_enable_password: str | None = Form(None),
):
    return await save_global_settings_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "sanitize_kit_name": sanitize_kit_name,
            "extract_snmp_users_from_form": extract_snmp_users_from_form,
            "build_snmp_input_review": build_snmp_input_review,
            "build_default_ip_plan": build_default_ip_plan,
            "apply_ip_plan": apply_ip_plan,
            "save_kit_config": save_kit_config,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        site_name=site_name,
        shared_subnet=shared_subnet,
        gateway_ip=gateway_ip,
        switch_ip=switch_ip,
        esxi_ip=esxi_ip,
        ilo_target_ip=ilo_target_ip,
        windows_ip=windows_ip,
        qnap_ip=qnap_ip,
        iosafe_ip=iosafe_ip,
        netapp_ip=netapp_ip,
        dns1=dns1,
        dns2=dns2,
        dns3=dns3,
        dns4=dns4,
        snmp_v3_username=snmp_v3_username,
        snmp_v3_auth_protocol=snmp_v3_auth_protocol,
        snmp_v3_auth_password=snmp_v3_auth_password,
        snmp_v3_priv_protocol=snmp_v3_priv_protocol,
        snmp_v3_priv_password=snmp_v3_priv_password,
        included_ilo=included_ilo,
        included_esxi=included_esxi,
        included_windows=included_windows,
        included_qnap=included_qnap,
        included_iosafe=included_iosafe,
        included_cisco_switch=included_cisco_switch,
        included_storage=included_storage,
        included_netapp=included_netapp,
        netapp_host=netapp_host,
        netapp_username=netapp_username,
        netapp_password=netapp_password,
        netapp_storage_protocol=netapp_storage_protocol,
        netapp_iscsi_commands=netapp_iscsi_commands,
        netapp_nfs_commands=netapp_nfs_commands,
        cisco_switch_hostname=cisco_switch_hostname,
        cisco_switch_username=cisco_switch_username,
        cisco_switch_password=cisco_switch_password,
        cisco_console_port=cisco_console_port,
        cisco_console_baud=cisco_console_baud,
        cisco_management_vlan=cisco_management_vlan,
        cisco_management_ip=cisco_management_ip,
        cisco_subnet_mask=cisco_subnet_mask,
        cisco_gateway=cisco_gateway,
        cisco_enable_password=cisco_enable_password,
    )


@app.post("/save-ilo-settings", response_class=HTMLResponse)
async def save_ilo_settings_route(
    request: Request,
    return_page: str = Form("ilo"),
    ilo_current_ip: str = Form(""),
    ilo_target_ip: str = Form(""),
    ilo_gateway: str = Form(""),
    ilo_hostname: str = Form(""),
    ilo_username: str = Form(""),
    ilo_password: str = Form(""),
    ilo_discover_start_octet: str = Form("21"),
    ilo_discover_end_octet: str = Form("29"),
    ilo_policy_apply_standard_policy: str | None = Form(None),
    ilo_policy_enable_standard_accounts: str | None = Form(None),
    ilo_policy_enable_license_check: str | None = Form(None),
    ilo_policy_enable_snmp_policy: str | None = Form(None),
    ilo_policy_enable_alert_destinations: str | None = Form(None),
    ilo_policy_enable_ipv6_disable: str | None = Form(None),
    ilo_policy_enable_time_policy: str | None = Form(None),
    ilo_policy_enable_auto_reset: str | None = Form(None),
    ilo_policy_kit_admin_password: str = Form(""),
    ilo_policy_kit_operator_password: str = Form(""),
    ilo_policy_shared_admin_username: str = Form("765CS"),
    ilo_policy_shared_admin_password: str = Form(""),
    ilo_policy_snmp_read_community: str = Form(""),
    ilo_policy_snmpv3_username: str = Form("765CS"),
    ilo_policy_snmpv3_auth_protocol: str = Form("SHA"),
    ilo_policy_snmpv3_auth_password: str = Form(""),
    ilo_policy_snmpv3_priv_protocol: str = Form("AES"),
    ilo_policy_snmpv3_priv_password: str = Form(""),
    ilo_policy_alert_destinations: str = Form("10.245.190.67, 10.245.190.68"),
):
    return await save_ilo_settings_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "render_page": render_page,
            "normalize_ilo_hostname": normalize_ilo_hostname,
            "extract_ilo_additional_users_from_form": extract_ilo_additional_users_from_form,
            "normalize_ilo_policy": normalize_ilo_policy,
            "build_ilo_discovery_targets": build_ilo_discovery_targets,
            "probe_tcp_port": probe_tcp_port,
            "build_ilo_input_review": build_ilo_input_review,
            "build_action_feedback": build_action_feedback,
            "apply_ip_plan": apply_ip_plan,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
        },
        return_page=return_page,
        ilo_current_ip=ilo_current_ip,
        ilo_target_ip=ilo_target_ip,
        ilo_gateway=ilo_gateway,
        ilo_hostname=ilo_hostname,
        ilo_username=ilo_username,
        ilo_password=ilo_password,
        ilo_discover_start_octet=ilo_discover_start_octet,
        ilo_discover_end_octet=ilo_discover_end_octet,
        ilo_policy_apply_standard_policy=ilo_policy_apply_standard_policy,
        ilo_policy_enable_standard_accounts=ilo_policy_enable_standard_accounts,
        ilo_policy_enable_license_check=ilo_policy_enable_license_check,
        ilo_policy_enable_snmp_policy=ilo_policy_enable_snmp_policy,
        ilo_policy_enable_alert_destinations=ilo_policy_enable_alert_destinations,
        ilo_policy_enable_ipv6_disable=ilo_policy_enable_ipv6_disable,
        ilo_policy_enable_time_policy=ilo_policy_enable_time_policy,
        ilo_policy_enable_auto_reset=ilo_policy_enable_auto_reset,
        ilo_policy_kit_admin_password=ilo_policy_kit_admin_password,
        ilo_policy_kit_operator_password=ilo_policy_kit_operator_password,
        ilo_policy_shared_admin_username=ilo_policy_shared_admin_username,
        ilo_policy_shared_admin_password=ilo_policy_shared_admin_password,
        ilo_policy_snmp_read_community=ilo_policy_snmp_read_community,
        ilo_policy_snmpv3_username=ilo_policy_snmpv3_username,
        ilo_policy_snmpv3_auth_protocol=ilo_policy_snmpv3_auth_protocol,
        ilo_policy_snmpv3_auth_password=ilo_policy_snmpv3_auth_password,
        ilo_policy_snmpv3_priv_protocol=ilo_policy_snmpv3_priv_protocol,
        ilo_policy_snmpv3_priv_password=ilo_policy_snmpv3_priv_password,
        ilo_policy_alert_destinations=ilo_policy_alert_destinations,
    )


@app.post("/save-esxi-settings", response_class=HTMLResponse)
async def save_esxi_settings_route(
    request: Request,
    return_page: str = Form("esxi"),
    esxi_version: str = Form("7"),
    esxi_base_iso_path: str = Form(""),
    esxi_hostname: str = Form(""),
    esxi_root_password: str = Form(""),
    esxi_debug_no_reboot: str | None = Form(None),
    esxi_post_discovery_start_octet: str = Form("31"),
    esxi_post_discovery_end_octet: str = Form("33"),
    esxi_post_allow_datastore_create: str | None = Form(None),
    esxi_post_allow_single_mgmt_uplink_override: str | None = Form(None),
    esxi_post_configure_only_no_reboot: str | None = Form(None),
    esxi_post_reboot_confirmed: str | None = Form(None),
    esxi_post_wug_snmp_target: str = Form(""),
    esxi_post_wug_notraps: str = Form(""),
    esxi_post_hostname_override: str = Form(""),
    esxi_post_domain_override: str = Form(""),
    esxi_post_dns1_override: str = Form(""),
    esxi_post_dns2_override: str = Form(""),
    esxi_post_transport: str = Form("dry_run"),
    esxi_post_secret_wug_password: str = Form(""),
    esxi_post_secret_snmpv3_auth_password: str = Form(""),
    esxi_post_secret_snmpv3_priv_password: str = Form(""),
    esxi_post_secret_kit_root_password: str = Form(""),
    esxi_post_secret_svmservice_password: str = Form(""),
    esxi_post_secret_localtech_password: str = Form(""),
    included_esxi: str | None = Form(None),
):
    return await save_esxi_settings_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "ensure_esxi_post_config_policy": ensure_esxi_post_config_policy,
            "normalize_esxi_version": normalize_esxi_version,
            "apply_ip_plan": apply_ip_plan,
            "get_esxi_effective_values": get_esxi_effective_values,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        esxi_version=esxi_version,
        esxi_base_iso_path=esxi_base_iso_path,
        esxi_hostname=esxi_hostname,
        esxi_root_password=esxi_root_password,
        esxi_debug_no_reboot=esxi_debug_no_reboot,
        esxi_post_discovery_start_octet=esxi_post_discovery_start_octet,
        esxi_post_discovery_end_octet=esxi_post_discovery_end_octet,
        esxi_post_allow_datastore_create=esxi_post_allow_datastore_create,
        esxi_post_allow_single_mgmt_uplink_override=esxi_post_allow_single_mgmt_uplink_override,
        esxi_post_configure_only_no_reboot=esxi_post_configure_only_no_reboot,
        esxi_post_reboot_confirmed=esxi_post_reboot_confirmed,
        esxi_post_wug_snmp_target=esxi_post_wug_snmp_target,
        esxi_post_wug_notraps=esxi_post_wug_notraps,
        esxi_post_hostname_override=esxi_post_hostname_override,
        esxi_post_domain_override=esxi_post_domain_override,
        esxi_post_dns1_override=esxi_post_dns1_override,
        esxi_post_dns2_override=esxi_post_dns2_override,
        esxi_post_transport=esxi_post_transport,
        esxi_post_secret_wug_password=esxi_post_secret_wug_password,
        esxi_post_secret_snmpv3_auth_password=esxi_post_secret_snmpv3_auth_password,
        esxi_post_secret_snmpv3_priv_password=esxi_post_secret_snmpv3_priv_password,
        esxi_post_secret_kit_root_password=esxi_post_secret_kit_root_password,
        esxi_post_secret_svmservice_password=esxi_post_secret_svmservice_password,
        esxi_post_secret_localtech_password=esxi_post_secret_localtech_password,
        included_esxi=included_esxi,
    )


@app.post("/save-windows-settings", response_class=HTMLResponse)
async def save_windows_settings_route(
    request: Request,
    return_page: str = Form("windows"),
    windows_vm_name: str = Form(""),
    windows_admin_password: str = Form(""),
    windows_vsphere_host: str = Form(""),
    windows_vsphere_username: str = Form(""),
    windows_vsphere_password: str = Form(""),
    windows_vsphere_datacenter: str = Form(""),
    windows_vsphere_datastore: str = Form(""),
    windows_vsphere_network: str = Form(""),
    windows_vsphere_folder: str = Form(""),
    windows_vsphere_resource_pool: str = Form(""),
    windows_winrm_username: str = Form("Administrator"),
    windows_winrm_password: str = Form(""),
    windows_winrm_port: str = Form("5986"),
    windows_winrm_use_https: str | None = Form(None),
    included_windows: str | None = Form(None),
):
    return await save_windows_settings_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "apply_ip_plan": apply_ip_plan,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        windows_vm_name=windows_vm_name,
        windows_admin_password=windows_admin_password,
        windows_vsphere_host=windows_vsphere_host,
        windows_vsphere_username=windows_vsphere_username,
        windows_vsphere_password=windows_vsphere_password,
        windows_vsphere_datacenter=windows_vsphere_datacenter,
        windows_vsphere_datastore=windows_vsphere_datastore,
        windows_vsphere_network=windows_vsphere_network,
        windows_vsphere_folder=windows_vsphere_folder,
        windows_vsphere_resource_pool=windows_vsphere_resource_pool,
        windows_winrm_username=windows_winrm_username,
        windows_winrm_password=windows_winrm_password,
        windows_winrm_port=windows_winrm_port,
        windows_winrm_use_https=windows_winrm_use_https,
        included_windows=included_windows,
    )


@app.post("/upload-windows-image", response_class=HTMLResponse)
async def upload_windows_image_route(
    request: Request,
    return_page: str = Form("windows"),
    windows_image: UploadFile = File(...),
):
    return await upload_windows_image_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "windows_upload_root": EXPORTS_DIR / "windows-images",
            "sanitize_kit_name": sanitize_kit_name,
            "time_str": lambda: time.strftime("%Y%m%d-%H%M%S"),
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        windows_image=windows_image,
    )


@app.post("/plan-windows-install", response_class=HTMLResponse)
async def plan_windows_install_route(
    request: Request,
    return_page: str = Form("windows"),
):
    return await plan_windows_install_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
            "validate_ovf_inputs": VsphereClient.validate_ovf_inputs,
        },
        return_page=return_page,
    )


@app.post("/probe-windows-vsphere", response_class=HTMLResponse)
async def probe_windows_vsphere_route(request: Request, return_page: str = Form("windows")):
    return await probe_windows_vsphere_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "save_kit_config": save_kit_config,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
            "build_vsphere_client": lambda windows_cfg: VsphereClient(
                VsphereConfig(
                    host=str(windows_cfg.get("vsphere_host") or ""),
                    username=str(windows_cfg.get("vsphere_username") or ""),
                    password=str(windows_cfg.get("vsphere_password") or ""),
                )
            ),
        },
        return_page=return_page,
    )


@app.post("/probe-windows-winrm", response_class=HTMLResponse)
async def probe_windows_winrm_route(request: Request, return_page: str = Form("windows")):
    return await probe_windows_winrm_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "save_kit_config": save_kit_config,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
            "build_winrm_client": lambda windows_cfg, host: WinRMClient(
                WinRMConfig(
                    host=host,
                    username=str(windows_cfg.get("winrm_username") or ""),
                    password=str(windows_cfg.get("winrm_password") or ""),
                    port=int(windows_cfg.get("winrm_port") or 5986),
                    use_https=bool(windows_cfg.get("winrm_use_https", True)),
                )
            ),
        },
        return_page=return_page,
    )


@app.post("/save-qnap-settings", response_class=HTMLResponse)
async def save_qnap_settings_route(
    request: Request,
    return_page: str = Form("qnap"),
    qnap_hostname: str = Form(""),
    qnap_username: str = Form(""),
    qnap_password: str = Form(""),
    included_qnap: str | None = Form(None),
):
    return await save_qnap_settings_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "apply_ip_plan": apply_ip_plan,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        qnap_hostname=qnap_hostname,
        qnap_username=qnap_username,
        qnap_password=qnap_password,
        included_qnap=included_qnap,
    )


@app.post("/autofill-ip-plan", response_class=HTMLResponse)
async def autofill_ip_plan(
    request: Request,
    return_page: str = Form("configuration"),
    shared_subnet: str = Form("10.10.8.0/24"),
):
    return await autofill_ip_plan_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "build_default_ip_plan": build_default_ip_plan,
            "apply_ip_plan": apply_ip_plan,
            "save_kit_config": save_kit_config,
            "render_page": render_page,
        },
        return_page=return_page,
        shared_subnet=shared_subnet,
    )


@app.post("/export-ilo-config", response_class=HTMLResponse)
async def export_ilo_config(request: Request, return_page: str = Form("configs")):
    return await export_ilo_config_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "export_ilo_config_snapshot": export_ilo_config_snapshot,
            "render_page": render_page,
        },
        return_page=return_page,
    )


@app.post("/export-ilo-inventory", response_class=HTMLResponse)
async def export_ilo_inventory(request: Request, return_page: str = Form("configs")):
    return await export_ilo_inventory_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "policy_enabled": policy_enabled,
            "normalize_ilo_policy": normalize_ilo_policy,
            "probe_tcp_port": probe_tcp_port,
            "build_ilo_discovery_targets": build_ilo_discovery_targets,
            "save_kit_config": save_kit_config,
            "build_ilo_client": lambda *, host, username, password: ILOClient(
                ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15)
            ),
            "export_ilo_inventory_snapshot": export_ilo_inventory_snapshot,
            "db_persist_ilo_inventory": db_persist_ilo_inventory,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
    )


@app.post("/export-ad-hoc-ilo-inventory", response_class=HTMLResponse)
async def export_ad_hoc_ilo_inventory(
    request: Request,
    return_page: str = Form("configs"),
    ad_hoc_ilo_host: str = Form(""),
    ad_hoc_ilo_username: str = Form(""),
    ad_hoc_ilo_password: str = Form(""),
    ad_hoc_ilo_label: str = Form(""),
    save_to_current_kit: str | None = Form(None),
):
    return await export_ad_hoc_ilo_inventory_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "build_ilo_client": lambda *, host, username, password: ILOClient(
                ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15)
            ),
            "export_ilo_inventory_snapshot": export_ilo_inventory_snapshot,
            "db_persist_ilo_inventory": db_persist_ilo_inventory,
            "save_kit_config": save_kit_config,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        ad_hoc_ilo_host=ad_hoc_ilo_host,
        ad_hoc_ilo_username=ad_hoc_ilo_username,
        ad_hoc_ilo_password=ad_hoc_ilo_password,
        ad_hoc_ilo_label=ad_hoc_ilo_label,
        save_to_current_kit=save_to_current_kit,
    )


@app.post("/view-latest-live-summary", response_class=HTMLResponse)
async def view_latest_live_summary(request: Request, return_page: str = Form("configs")):
    return await view_latest_live_summary_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "latest_live_inventory_export": latest_live_inventory_export,
            "ilo_live_export_dir": ILO_LIVE_EXPORT_DIR,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
    )


@app.post("/download-latest-live-summary")
async def download_latest_live_summary():
    return await download_latest_live_summary_handler(
        runtime={
            "latest_live_inventory_export": latest_live_inventory_export,
            "ilo_live_export_dir": ILO_LIVE_EXPORT_DIR,
            "live_inventory_download_headers": live_inventory_download_headers,
        }
    )


@app.post("/download-latest-live-raw")
async def download_latest_live_raw():
    return await download_latest_live_raw_handler(
        runtime={
            "latest_live_inventory_export": latest_live_inventory_export,
            "ilo_live_export_dir": ILO_LIVE_EXPORT_DIR,
            "live_inventory_download_headers": live_inventory_download_headers,
        }
    )


@app.post("/read-current-storage", response_class=HTMLResponse)
async def read_current_storage(
    request: Request,
    return_page: str = Form("storage"),
):
    return await read_current_storage_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "resolve_storage_target_credentials": resolve_storage_target_credentials,
            "build_ilo_client": lambda *, host, username, password: ILOClient(
                ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15)
            ),
            "export_storage_discovery_snapshot": export_storage_discovery_snapshot,
            "db_persist_storage_inventory": db_persist_storage_inventory,
            "update_storage_latest_state": update_storage_latest_state,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
    )


@app.post("/repair-storage-selection", response_class=HTMLResponse)
async def repair_storage_selection(
    request: Request,
    return_page: str = Form("storage"),
):
    return await repair_storage_selection_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "resolve_storage_target_credentials": resolve_storage_target_credentials,
            "clear_storage_plan_selection_state": clear_storage_plan_selection_state,
            "build_ilo_client": lambda *, host, username, password: ILOClient(
                ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15)
            ),
            "export_storage_discovery_snapshot": export_storage_discovery_snapshot,
            "db_persist_storage_inventory": db_persist_storage_inventory,
            "update_storage_latest_state": update_storage_latest_state,
            "save_kit_config": save_kit_config,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
    )


@app.post("/plan-raid-layout", response_class=HTMLResponse)
async def plan_raid_layout(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    controller_path: str = Form(""),
    os_controller_path: str = Form(""),
    data_controller_path: str = Form(""),
    os_raid_level: str | None = Form(None),
    data_raid_level: str | None = Form(None),
    os_drive_ids: list[str] = Form([]),
    data_drive_ids: list[str] = Form([]),
    hot_spare_drive_id: str = Form(""),
    os_drive_paths: list[str] = Form([]),
    data_drive_paths: list[str] = Form([]),
    hot_spare_path: str = Form(""),
    os_bays: list[str] = Form([]),
    data_bays: list[str] = Form([]),
    hot_spare_bay: str = Form(""),
):
    return await plan_raid_layout_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "load_storage_discovery_artifact": load_storage_discovery_artifact,
            "build_raid_plan": build_raid_plan,
            "export_raid_plan_snapshot": export_raid_plan_snapshot,
            "db_persist_storage_plan": db_persist_storage_plan,
            "update_storage_latest_state": update_storage_latest_state,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        discovery_raw_path=discovery_raw_path,
        controller_path=controller_path,
        os_controller_path=os_controller_path,
        data_controller_path=data_controller_path,
        os_raid_level=os_raid_level,
        data_raid_level=data_raid_level,
        os_drive_ids=os_drive_ids,
        data_drive_ids=data_drive_ids,
        hot_spare_drive_id=hot_spare_drive_id,
        os_drive_paths=os_drive_paths,
        data_drive_paths=data_drive_paths,
        hot_spare_path=hot_spare_path,
        os_bays=os_bays,
        data_bays=data_bays,
        hot_spare_bay=hot_spare_bay,
    )


@app.post("/approve-storage-plan", response_class=HTMLResponse)
async def approve_storage_plan(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    include_in_ilo_run: str | None = Form(None),
):
    return await approve_storage_plan_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "restore_storage_page_state": restore_storage_page_state,
            "validate_storage_plan_drive_paths": validate_storage_plan_drive_paths,
            "approve_storage_plan_for_cfg": approve_storage_plan_for_cfg,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
            "is_storage_drive_controller_mismatch_error": is_storage_drive_controller_mismatch_error,
            "db_record_known_issue_observation": db_record_known_issue_observation,
            "known_issue_storage_drive_controller_mismatch": KNOWN_ISSUE_STORAGE_DRIVE_CONTROLLER_MISMATCH,
        },
        return_page=return_page,
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        include_in_ilo_run=include_in_ilo_run,
    )


@app.post("/probe-storage-capabilities", response_class=HTMLResponse)
async def probe_storage_capabilities(
    request: Request,
    return_page: str = Form("storage"),
):
    return await probe_storage_capabilities_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "resolve_storage_target_credentials": resolve_storage_target_credentials,
            "build_ilo_client": lambda *, host, username, password: ILOClient(
                ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15)
            ),
            "export_storage_discovery_snapshot": export_storage_discovery_snapshot,
            "db_persist_storage_inventory": db_persist_storage_inventory,
            "update_storage_latest_state": update_storage_latest_state,
            "save_kit_config": save_kit_config,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
    )


@app.post("/clear-storage-approval", response_class=HTMLResponse)
async def clear_storage_approval(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
):
    return await clear_storage_approval_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "restore_storage_page_state": restore_storage_page_state,
            "clear_storage_approval_for_cfg": clear_storage_approval_for_cfg,
            "save_kit_config": save_kit_config,
            "append_activity_event": append_activity_event,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
    )


@app.post("/apply-storage-layout", response_class=HTMLResponse)
async def apply_storage_layout(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    apply_mode: str = Form("create_only"),
    acknowledge_apply: str | None = Form(None),
    typed_confirmation: str = Form(""),
):
    return await apply_storage_layout_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "restore_storage_page_state": restore_storage_page_state,
            "validate_storage_plan_drive_paths": validate_storage_plan_drive_paths,
            "validate_storage_apply_request": validate_storage_apply_request,
            "initialize_storage_apply_artifacts": initialize_storage_apply_artifacts,
            "initialize_background_job": initialize_background_job,
            "start_storage_apply_background": start_storage_apply_background,
            "build_action_feedback": build_action_feedback,
            "render_page": render_page,
            "is_storage_drive_controller_mismatch_error": is_storage_drive_controller_mismatch_error,
            "db_record_known_issue_observation": db_record_known_issue_observation,
            "known_issue_storage_drive_controller_mismatch": KNOWN_ISSUE_STORAGE_DRIVE_CONTROLLER_MISMATCH,
        },
        return_page=return_page,
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        apply_mode=apply_mode,
        acknowledge_apply=acknowledge_apply,
        typed_confirmation=typed_confirmation,
    )


@app.post("/reboot-storage-now", response_class=HTMLResponse)
async def reboot_storage_now(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    apply_artifact_dir: str = Form(""),
):
    return await reboot_storage_now_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "resolve_storage_target_host": resolve_storage_target_host,
            "restore_storage_page_state": restore_storage_page_state,
            "storage_apply_paths_from_directory": storage_apply_paths_from_directory,
            "load_storage_workflow_state": load_storage_workflow_state,
            "initialize_background_job": initialize_background_job,
            "start_storage_reboot_background": start_storage_reboot_background,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        apply_artifact_dir=apply_artifact_dir,
    )


@app.post("/view-storage-artifact", response_class=HTMLResponse)
async def view_storage_artifact(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    artifact_kind: str = Form("discovery_summary"),
    artifact_path: str = Form(""),
    artifact_title: str = Form(""),
    apply_artifact_dir: str = Form(""),
):
    return await view_storage_artifact_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "restore_storage_page_state": restore_storage_page_state,
            "storage_apply_paths_from_directory": storage_apply_paths_from_directory,
            "storage_artifact_target": storage_artifact_target,
            "render_page": render_page,
        },
        return_page=return_page,
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        artifact_kind=artifact_kind,
        artifact_path=artifact_path,
        artifact_title=artifact_title,
        apply_artifact_dir=apply_artifact_dir,
    )


@app.post("/download-storage-artifact")
async def download_storage_artifact(
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    artifact_kind: str = Form("discovery_summary"),
    artifact_path: str = Form(""),
    artifact_title: str = Form(""),
    apply_artifact_dir: str = Form(""),
):
    return await download_storage_artifact_handler(
        runtime={
            "load_kit_config": load_kit_config,
            "restore_storage_page_state": restore_storage_page_state,
            "storage_artifact_target": storage_artifact_target,
        },
        return_page=return_page,
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        artifact_kind=artifact_kind,
        artifact_path=artifact_path,
        artifact_title=artifact_title,
        apply_artifact_dir=apply_artifact_dir,
    )


@app.post("/view-current-kit-config", response_class=HTMLResponse)
async def view_current_kit_config(request: Request, return_page: str = Form("configs")):
    return await view_current_kit_config_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "export_current_kit_config_snapshot": export_current_kit_config_snapshot,
            "render_page": render_page,
        },
        return_page=return_page,
    )


@app.post("/download-current-kit-config")
async def download_current_kit_config():
    return await download_current_kit_config_handler(
        runtime={
            "load_kit_config": load_kit_config,
            "export_current_kit_config_snapshot": export_current_kit_config_snapshot,
        }
    )


@app.post("/import-kit-config", response_class=HTMLResponse)
async def import_kit_config(
    request: Request,
    return_page: str = Form("configs"),
    import_file: UploadFile = File(...),
):
    return await import_kit_config_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "yaml_safe_load": yaml.safe_load,
            "merge_defaults": merge_defaults,
            "sanitize_kit_name": sanitize_kit_name,
            "save_kit_config": save_kit_config,
            "current_build_output_dir": current_build_output_dir,
            "yaml_safe_dump": yaml.safe_dump,
            "time_str": lambda: time.strftime("%Y%m%d-%H%M%S"),
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        import_file=import_file,
    )


@app.post("/view-ilo-config-snapshot", response_class=HTMLResponse)
async def view_ilo_config_snapshot(request: Request, return_page: str = Form("configs")):
    return await view_ilo_config_snapshot_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "export_ilo_config_snapshot": export_ilo_config_snapshot,
            "render_page": render_page,
        },
        return_page=return_page,
    )


@app.post("/download-ilo-config-snapshot")
async def download_ilo_config_snapshot():
    return await download_ilo_config_snapshot_handler(
        runtime={
            "load_kit_config": load_kit_config,
            "export_ilo_config_snapshot": export_ilo_config_snapshot,
        }
    )


@app.post("/view-report", response_class=HTMLResponse)
async def view_report(
    request: Request,
    return_page: str = Form("configs"),
    report_path: str = Form(...),
):
    return await view_report_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "safe_report_path": safe_report_path,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
        },
        return_page=return_page,
        report_path=report_path,
    )


@app.post("/download-report")
async def download_report(report_path: str = Form(...)):
    return await download_report_handler(
        runtime={
            "safe_report_path": safe_report_path,
        },
        report_path=report_path,
    )


@app.post("/view-run-summary", response_class=HTMLResponse)
async def view_run_summary(
    request: Request,
    scope: str = Form(...),
    return_page: str = Form("execution"),
):
    return await view_run_summary_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "build_run_summary": build_run_summary,
            "build_execution_review": build_execution_review,
            "render_page": render_page,
            "build_action_feedback": build_action_feedback,
            "yaml_safe_dump": yaml.safe_dump,
        },
        scope=scope,
        return_page=return_page,
    )


@app.post("/download-run-summary")
async def download_run_summary(scope: str = Form(...)):
    return await download_run_summary_handler(
        runtime={
            "load_kit_config": load_kit_config,
            "write_run_summary_artifact": write_run_summary_artifact,
        },
        scope=scope,
    )


@app.get("/debug-bundles/latest")
async def download_latest_debug_bundle():
    return await download_latest_debug_bundle_handler(
        runtime={"debug_bundles_dir": DEBUG_BUNDLES_DIR}
    )


def resolve_built_esxi_iso_path(kit_name: str, output_name: str) -> Path:
    safe_kit_name = sanitize_kit_name(kit_name)
    safe_output_name = sanitize_kit_name(output_name)
    nested_path = EXPORTS_DIR / "esxi-isos" / safe_kit_name / safe_output_name / f"{safe_output_name}.iso"
    flat_path = EXPORTS_DIR / "esxi-isos" / safe_kit_name / f"{safe_output_name}.iso"
    return nested_path if nested_path.exists() else flat_path


def append_esxi_iso_access_log(path: Path, request: Request) -> None:
    try:
        client_host = request.client.host if request.client else ""
        range_header = request.headers.get("range", "")
        user_agent = request.headers.get("user-agent", "")
        line = (
            f"{datetime.now().isoformat(timespec='seconds')} "
            f"method={request.method} client={client_host} "
            f"range={range_header or '-'} user_agent={user_agent or '-'}\n"
        )
        with (path.parent / "iso-access.log").open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


@app.api_route("/esxi-built-iso/{kit_name}/{output_name}.iso", methods=["GET", "HEAD"])
async def download_built_esxi_iso(request: Request, kit_name: str, output_name: str):
    return await download_built_esxi_iso_handler(
        request,
        runtime={
            "resolve_built_esxi_iso_path": resolve_built_esxi_iso_path,
            "append_esxi_iso_access_log": append_esxi_iso_access_log,
        },
        kit_name=kit_name,
        output_name=output_name,
    )


@app.post("/prepare-execute", response_class=HTMLResponse)
async def prepare_execute(
    request: Request,
    scope: str = Form("included"),
    selected_scopes: list[str] = Form([]),
    return_page: str = Form("execution"),
):
    return await prepare_execute_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "apply_request_public_base_url": apply_request_public_base_url,
            "normalize_run_center_scope": normalize_run_center_scope,
            "validate_execution_scope": validate_execution_scope,
            "build_execution_review": build_execution_review,
            "render_page": render_page,
        },
        scope=scope,
        selected_scopes=selected_scopes,
        return_page=return_page,
    )


@app.post("/execute", response_class=HTMLResponse)
async def execute_scope(
    request: Request,
    scope: str = Form("included"),
    selected_scopes: list[str] = Form([]),
    confirm_phrase: str = Form(""),
    confirm_checkbox: str | None = Form(None),
    esxi_run_stamp: str = Form(""),
    return_page: str = Form("execution"),
):
    return await execute_scope_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "apply_request_public_base_url": apply_request_public_base_url,
            "normalize_run_center_scope": normalize_run_center_scope,
            "build_execution_launch_options": build_execution_launch_options,
            "validate_execution_scope": validate_execution_scope,
            "build_execution_review": build_execution_review,
            "initialize_background_job": initialize_background_job,
            "execute_real_job_in_background": execute_real_job_in_background,
            "render_page": render_page,
        },
        scope=scope,
        selected_scopes=selected_scopes,
        confirm_phrase=confirm_phrase,
        confirm_checkbox=confirm_checkbox,
        esxi_run_stamp=esxi_run_stamp,
        return_page=return_page,
    )


@app.post("/retry-storage-stage", response_class=HTMLResponse)
async def retry_storage_stage(
    request: Request,
    return_page: str = Form("execution"),
):
    return await retry_storage_stage_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "apply_request_public_base_url": apply_request_public_base_url,
            "build_execution_review": build_execution_review,
            "build_execution_launch_options": build_execution_launch_options,
            "validate_execution_scope": validate_execution_scope,
            "initialize_background_job": initialize_background_job,
            "execute_real_job_in_background": execute_real_job_in_background,
            "render_page": render_page,
        },
        return_page=return_page,
    )


@app.post("/execute-preview", response_class=HTMLResponse)
async def execute_preview_scope(
    request: Request,
    scope: str = Form("included"),
    selected_scopes: list[str] = Form([]),
    return_page: str = Form("execution"),
):
    return await execute_preview_scope_handler(
        request,
        runtime={
            "load_kit_config": load_kit_config,
            "normalize_run_center_scope": normalize_run_center_scope,
            "validate_execution_scope": validate_execution_scope,
            "build_execution_review": build_execution_review,
            "render_page": render_page,
            "save_job": save_job,
            "initialize_stage_statuses": initialize_stage_statuses,
            "execute_preview_job_in_background": execute_preview_job_in_background,
        },
        scope=scope,
        selected_scopes=selected_scopes,
        return_page=return_page,
    )
