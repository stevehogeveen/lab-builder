from pathlib import Path
import asyncio
import copy
from datetime import datetime
import ipaddress
import json
import os
import re
import socket
import threading
import time
import yaml
from typing import Any, Callable
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.ilo import ILOClient, ILOConfig, ILOError
from app.esxi.builder import build_custom_iso
from app.esxi.models import EsxiBuildSpec
from app.debug_bundle import create_debug_bundle
from app.diagnostics import diagnostic_log_lines, diagnostic_result

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = BASE_DIR / "config"
KITS_DIR = CONFIG_DIR / "kits"
CURRENT_KIT_FILE = CONFIG_DIR / "current_kit.txt"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
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
STORAGE_APPLY_CONFIRM_CREATE = "CREATE STORAGE"
STORAGE_APPLY_CONFIRM_WIPE = "WIPE STORAGE"

app = FastAPI(title="Lab Builder")

STATIC_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
KITS_DIR.mkdir(parents=True, exist_ok=True)
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

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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
}
PAGE_META = {
    "dashboard": {
        "title": "Lab Builder Dashboard",
        "subtitle": "Per-kit deployment dashboard for offline builds.",
    },
    "global_settings": {
        "title": "Global Settings",
        "subtitle": "Shared defaults for network, inclusion, and kit-wide behavior.",
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
    "configuration": {
        "title": "Global Settings",
        "subtitle": "Shared defaults for network, inclusion, and kit-wide behavior.",
    },
    "configs": {
        "title": "Reports & Technical Details",
        "subtitle": "Open logs, reports, raw output, and saved technical details in one place.",
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
RUN_CENTER_STAGE_KEYS = ["ilo", "storage", "esxi", "windows", "qnap", "iosafe", "cisco_switch"]
DEFAULT_KIT_NAME = "Kit-01"


def sanitize_kit_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return DEFAULT_KIT_NAME
    name = re.sub(r"[^\w\- ]+", "", name)
    name = name.replace(" ", "-")
    return name or DEFAULT_KIT_NAME


def normalize_ilo_hostname(value: str) -> str:
    hostname = str(value or "").strip()
    if not hostname:
        return ""
    hostname = re.sub(r"[^A-Za-z0-9\-]+", "-", hostname)
    hostname = re.sub(r"-{2,}", "-", hostname).strip("-")
    return hostname[:63]


def has_non_printable_chars(value: str) -> bool:
    return any((not ch.isprintable()) or ch in "\r\n\t" for ch in str(value or ""))


def count_password_classes(value: str) -> int:
    text = str(value or "")
    classes = 0
    if any(ch.islower() for ch in text):
        classes += 1
    if any(ch.isupper() for ch in text):
        classes += 1
    if any(ch.isdigit() for ch in text):
        classes += 1
    if any(not ch.isalnum() for ch in text):
        classes += 1
    return classes


def validate_ilo_login_name(value: str, *, label: str, required: bool = True) -> list[str]:
    username = str(value or "").strip()
    if not username:
        return [f"{label} is required."] if required else []
    errors: list[str] = []
    if len(username) > 39:
        errors.append(f"{label} must be 39 characters or less.")
    if has_non_printable_chars(username):
        errors.append(f"{label} must use printable characters only.")
    if re.search(r"\s", username):
        errors.append(f"{label} cannot contain spaces.")
    return errors


def validate_ilo_password(value: str, *, username: str = "", label: str, required: bool = True) -> dict[str, list[str]]:
    password = str(value or "")
    errors: list[str] = []
    notes: list[str] = []
    if not password:
        if required:
            errors.append(f"{label} is required.")
        return {"errors": errors, "notes": notes}
    if len(password) > 39:
        errors.append(f"{label} must be 39 characters or less.")
    if has_non_printable_chars(password):
        errors.append(f"{label} must use printable characters only.")
    if len(password) < 8:
        notes.append(f"{label} is under 8 characters. Many iLO policies use a minimum of 8.")
    if count_password_classes(password) < 3:
        notes.append(f"{label} does not use 3 character types. iLO complexity policy may reject it.")
    if username and username.lower() in password.lower():
        notes.append(f"{label} contains the user name. HPE recommends avoiding that.")
    return {"errors": errors, "notes": notes}


def validate_snmpv3_username(value: str, *, label: str) -> list[str]:
    username = str(value or "").strip()
    if not username:
        return [f"{label} is required."]
    errors: list[str] = []
    if len(username) > 32:
        errors.append(f"{label} must be 32 characters or less.")
    if has_non_printable_chars(username):
        errors.append(f"{label} must use printable characters only.")
    if re.search(r"\s", username):
        errors.append(f"{label} cannot contain spaces.")
    return errors


def validate_snmpv3_password(value: str, *, label: str, required: bool = True) -> list[str]:
    password = str(value or "")
    if not password:
        return [f"{label} is required."] if required else []
    errors: list[str] = []
    if has_non_printable_chars(password):
        errors.append(f"{label} must use printable characters only.")
    if len(password) < 8:
        errors.append(f"{label} must be at least 8 characters.")
    return errors


def build_ilo_input_review(cfg: dict[str, Any]) -> dict[str, Any]:
    ilo_cfg = cfg.get("ilo", {}) or {}
    errors: list[str] = []
    notes: list[str] = []
    errors.extend(validate_ilo_login_name(ilo_cfg.get("username", ""), label="iLO username"))
    password_check = validate_ilo_password(
        ilo_cfg.get("password", ""),
        username=str(ilo_cfg.get("username") or ""),
        label="iLO password",
    )
    errors.extend(password_check["errors"])
    notes.extend(password_check["notes"])
    for index, item in enumerate(normalize_ilo_additional_users(ilo_cfg.get("additional_users", [])), start=1):
        prefix = f"Extra iLO user {index}"
        errors.extend(validate_ilo_login_name(item.get("username", ""), label=f"{prefix} username"))
        extra_check = validate_ilo_password(
            item.get("password", ""),
            username=str(item.get("username") or ""),
            label=f"{prefix} password",
        )
        errors.extend(extra_check["errors"])
        notes.extend(extra_check["notes"])
    return {"errors": errors, "notes": notes}


def build_snmp_input_review(cfg: dict[str, Any]) -> dict[str, Any]:
    snmp_cfg = cfg.get("shared_snmp", {}) or {}
    errors: list[str] = []
    notes: list[str] = []
    users = normalize_snmp_users(snmp_cfg.get("users", []))
    primary_username = str(snmp_cfg.get("v3_username") or "").strip()
    primary_auth_password = str(snmp_cfg.get("v3_auth_password") or "")
    primary_priv_password = str(snmp_cfg.get("v3_priv_password") or "")
    if primary_username or primary_auth_password or primary_priv_password:
        errors.extend(validate_snmpv3_username(primary_username, label="SNMPv3 user"))
        errors.extend(validate_snmpv3_password(primary_auth_password, label="SNMPv3 auth password"))
        errors.extend(validate_snmpv3_password(primary_priv_password, label="SNMPv3 privacy password"))
    for index, item in enumerate(users[1:] if users else [], start=1):
        prefix = f"Additional SNMPv3 user {index}"
        errors.extend(validate_snmpv3_username(item.get("username", ""), label=f"{prefix}"))
        errors.extend(validate_snmpv3_password(item.get("auth_password", ""), label=f"{prefix} auth password"))
        errors.extend(validate_snmpv3_password(item.get("priv_password", ""), label=f"{prefix} privacy password"))
    if primary_username and not users:
        notes.append("The primary SNMPv3 user is saved, but the normalized user list is empty.")
    return {"errors": errors, "notes": notes}


def build_esxi_field_errors(cfg: dict[str, Any]) -> dict[str, list[str]]:
    values = get_esxi_effective_values(cfg)
    return {
        "hostname": list(values.get("hostname_errors") or []),
        "root_password": list(values.get("root_password_errors") or []),
    }


def build_ilo_field_errors(cfg: dict[str, Any]) -> dict[str, Any]:
    ilo_cfg = cfg.get("ilo", {}) or {}
    main_username_errors = validate_ilo_login_name(ilo_cfg.get("username", ""), label="iLO username")
    main_password_check = validate_ilo_password(
        ilo_cfg.get("password", ""),
        username=str(ilo_cfg.get("username") or ""),
        label="iLO password",
    )
    extra_users = []
    for item in normalize_ilo_additional_users(ilo_cfg.get("additional_users", [])):
        username_errors = validate_ilo_login_name(item.get("username", ""), label="Extra iLO user username")
        password_check = validate_ilo_password(
            item.get("password", ""),
            username=str(item.get("username") or ""),
            label="Extra iLO user password",
        )
        extra_users.append(
            {
                "username": username_errors,
                "password": list(password_check.get("errors") or []),
            }
        )
    return {
        "username": main_username_errors,
        "password": list(main_password_check.get("errors") or []),
        "extra_users": extra_users,
    }


def build_snmp_field_errors(cfg: dict[str, Any]) -> dict[str, Any]:
    snmp_cfg = cfg.get("shared_snmp", {}) or {}
    primary_username = str(snmp_cfg.get("v3_username") or "").strip()
    primary_auth_password = str(snmp_cfg.get("v3_auth_password") or "")
    primary_priv_password = str(snmp_cfg.get("v3_priv_password") or "")
    extra_users = normalize_snmp_users(snmp_cfg.get("users", []))[1:] if snmp_cfg.get("users") else []
    return {
        "username": validate_snmpv3_username(primary_username, label="SNMPv3 user") if primary_username or primary_auth_password or primary_priv_password else [],
        "auth_password": validate_snmpv3_password(primary_auth_password, label="SNMPv3 auth password", required=bool(primary_username or primary_priv_password)),
        "priv_password": validate_snmpv3_password(primary_priv_password, label="SNMPv3 privacy password", required=bool(primary_username or primary_auth_password)),
        "extra_users": [
            {
                "username": validate_snmpv3_username(item.get("username", ""), label="Additional SNMPv3 user"),
                "auth_password": validate_snmpv3_password(item.get("auth_password", ""), label="Additional SNMPv3 auth password"),
                "priv_password": validate_snmpv3_password(item.get("priv_password", ""), label="Additional SNMPv3 privacy password"),
            }
            for item in extra_users
        ],
    }


def validate_esxi_hostname(value: str) -> list[str]:
    hostname = str(value or "").strip()
    if not hostname:
        return ["Server name is required."]
    if len(hostname) > 253:
        return ["Server name is too long. Keep the full name at 253 characters or less."]
    if re.search(r"[^A-Za-z0-9.\-]", hostname):
        return ["Use only letters, numbers, hyphens, and dots in the ESXi server name."]
    if hostname.startswith(".") or hostname.endswith("."):
        return ["Do not start or end the ESXi server name with a dot."]
    labels = hostname.split(".")
    if any(not label for label in labels):
        return ["Do not use empty name parts or two dots in a row in the ESXi server name."]
    errors: list[str] = []
    for label in labels:
        if len(label) > 63:
            errors.append("Each part of the ESXi server name must be 63 characters or less.")
            break
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?", label):
            errors.append("Each part of the ESXi server name must start and end with a letter or number.")
            break
    return errors


def build_esxi_password_policy_check(password: str, *, username: str = "root") -> dict[str, Any]:
    value = str(password or "")
    errors: list[str] = []
    notes: list[str] = []
    if not value:
        errors.append("Root password is required.")
        return {"valid": False, "errors": errors, "notes": notes, "class_count": 0, "length": 0}
    if any(ch.isspace() for ch in value):
        errors.append("Do not use spaces in the ESXi root password.")
    length = len(value)
    if length < 7:
        errors.append("Use at least 7 characters for the ESXi root password.")
    if length > 39:
        errors.append("Keep the ESXi root password under 40 characters.")

    lower_count = sum(1 for ch in value if ch.islower())
    upper_count = sum(1 for ch in value if ch.isupper())
    digit_count = sum(1 for ch in value if ch.isdigit())
    special_count = sum(1 for ch in value if not ch.isalnum())

    effective_classes = 0
    if lower_count:
        effective_classes += 1
    if upper_count:
        if upper_count == 1 and value[:1].isupper():
            notes.append("A single uppercase letter at the start may not count toward ESXi complexity.")
        else:
            effective_classes += 1
    if digit_count:
        if digit_count == 1 and value[-1:].isdigit():
            notes.append("A single number at the end may not count toward ESXi complexity.")
        else:
            effective_classes += 1
    if special_count:
        effective_classes += 1

    if effective_classes < 3:
        errors.append("Use at least 3 character types: lowercase, uppercase, number, or special.")

    if username and username.lower() in value.lower():
        notes.append("Avoid using the username inside the ESXi root password.")

    return {
        "valid": not errors,
        "errors": errors,
        "notes": notes,
        "class_count": effective_classes,
        "length": length,
    }


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
                "current_stage": "Refreshing live status",
                "progress_percent": 0,
                "completed_steps": 0,
                "total_steps": 0,
                "logs": ["[WARN] Live job state was mid-write. Refreshing."],
            }
    return {
        "status": "Idle",
        "scope": "",
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

    status = str(job.get("status") or "")
    completed = as_int(job.get("completed_steps"))
    total = as_int(job.get("total_steps"))
    progress = as_int(job.get("progress_percent"))
    if status == "Running" and total > 0 and completed >= total and progress >= 100:
        normalized = dict(job)
        normalized["status"] = "Completed"
        normalized["current_stage"] = str(normalized.get("current_stage") or "Finished")
        logs = list(normalized.get("logs") or [])
        if not any(str(line).startswith("[DONE]") for line in logs):
            logs.append("[DONE] Run reached all recorded steps; marking stale running state as completed.")
        normalized["logs"] = logs
        return normalized
    return job


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


def append_history_entry(kit_name: str, entry: dict):
    history = load_history(kit_name)
    history.insert(0, entry)
    history = history[:25]
    save_history(kit_name, history)


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
    return str(item.get("logical_drive_name") or item.get("name") or item.get("id") or "").strip()


def ensure_storage_config(cfg: dict[str, Any]) -> dict[str, Any]:
    storage_cfg = cfg.setdefault("storage", {})
    approval = storage_cfg.setdefault("approval", {})
    storage_cfg.setdefault("target_host_override", "")
    storage_cfg.setdefault("username", "")
    storage_cfg.setdefault("password", "")
    storage_cfg.setdefault("include_in_ilo_run", False)
    storage_cfg.setdefault("latest_discovery_raw_path", "")
    storage_cfg.setdefault("latest_plan_path", "")
    storage_cfg.setdefault("state", "idle")
    storage_cfg.setdefault("status_reason", "")
    approval.setdefault("state", "")
    approval.setdefault("approved_at", "")
    approval.setdefault("host", "")
    approval.setdefault("serial_number", "")
    approval.setdefault("discovery_raw_path", "")
    approval.setdefault("plan_path", "")
    approval.setdefault("discovery_fingerprint", "")
    approval.setdefault("plan_summary", {})
    approval.setdefault("reboot_expected", False)
    return storage_cfg


def storage_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "controller": (plan.get("source_discovery", {}).get("controller", {}) or {}).get("name")
        or (plan.get("source_discovery", {}).get("controller", {}) or {}).get("model")
        or "",
        "os_bays": plan_drive_bays(plan.get("os_raid1", {}).get("drives", []) or []),
        "data_bays": plan_drive_bays(plan.get("data_raid6", {}).get("drives", []) or []),
        "spare_bay": str((plan.get("hot_spare", {}).get("drive", {}) or {}).get("bay", "")),
        "mode": (plan.get("apply_readiness", {}) or {}).get("next_action", ""),
    }


def refresh_storage_approval_from_saved_state(cfg: dict[str, Any]) -> None:
    storage_cfg = ensure_storage_config(cfg)
    approval = storage_cfg.get("approval", {}) or {}
    if not approval.get("plan_path") or not approval.get("discovery_raw_path"):
        if storage_cfg.get("latest_plan_path"):
            storage_cfg["state"] = "planned"
        elif storage_cfg.get("latest_discovery_raw_path"):
            storage_cfg["state"] = "discovered"
        else:
            storage_cfg["state"] = "idle"
        return

    latest_raw = str(storage_cfg.get("latest_discovery_raw_path") or "").strip()
    latest_fingerprint = str(storage_cfg.get("latest_discovery_fingerprint") or "").strip()
    approved_fingerprint = str(approval.get("discovery_fingerprint") or "").strip()
    configured_target_host = str(
        storage_cfg.get("target_host_override")
        or cfg.get("ilo", {}).get("current_ip")
        or cfg.get("ilo", {}).get("host")
        or ""
    ).strip()
    approved_host = str(approval.get("host") or "").strip()
    if configured_target_host and approved_host and configured_target_host != approved_host:
        approval["state"] = "stale"
        storage_cfg["state"] = "stale"
        storage_cfg["status_reason"] = (
            f"Current storage target host ({configured_target_host}) differs from the approved storage host ({approved_host})."
        )
    elif latest_raw and latest_fingerprint and approved_fingerprint and latest_fingerprint != approved_fingerprint:
        approval["state"] = "stale"
        storage_cfg["state"] = "stale"
        storage_cfg["status_reason"] = "Latest storage discovery differs from the approved discovery basis."
    else:
        approval["state"] = "approved"
        storage_cfg["state"] = "approved"
        storage_cfg["status_reason"] = ""


def update_storage_latest_state(
    cfg: dict[str, Any],
    discovery: dict[str, Any] | None = None,
    discovery_paths: dict[str, Path] | None = None,
    plan: dict[str, Any] | None = None,
    plan_paths: dict[str, Path] | None = None,
) -> None:
    storage_cfg = ensure_storage_config(cfg)
    if discovery is not None and discovery_paths is not None:
        storage_cfg["latest_discovery_raw_path"] = str(discovery_paths["raw"])
        storage_cfg["latest_discovery_fingerprint"] = storage_discovery_fingerprint(discovery)
        summary = discovery.get("summary", {}) or {}
        storage_cfg["latest_host"] = str((discovery.get("raw", {}) or {}).get("source_host") or summary.get("source_host") or cfg.get("ilo", {}).get("current_ip") or "")
        storage_cfg["latest_serial_number"] = str((summary.get("server", {}) or {}).get("serial_number") or "")
    if plan is not None and plan_paths is not None:
        storage_cfg["latest_plan_path"] = str(plan_paths["plan"])
        storage_cfg["latest_plan_summary"] = storage_plan_summary(plan)
    refresh_storage_approval_from_saved_state(cfg)
    if storage_cfg.get("state") == "idle":
        if storage_cfg.get("latest_plan_path"):
            storage_cfg["state"] = "planned"
        elif storage_cfg.get("latest_discovery_raw_path"):
            storage_cfg["state"] = "discovered"


def approve_storage_plan_for_cfg(
    cfg: dict[str, Any],
    discovery: dict[str, Any],
    discovery_paths: dict[str, Path],
    plan: dict[str, Any],
    plan_paths: dict[str, Path],
    include_in_ilo_run: bool,
) -> None:
    storage_cfg = ensure_storage_config(cfg)
    approval = storage_cfg["approval"]
    summary = discovery.get("summary", {}) or {}
    approval.update(
        {
            "state": "approved",
            "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "host": str((discovery.get("raw", {}) or {}).get("source_host") or cfg.get("ilo", {}).get("current_ip") or ""),
            "serial_number": str((summary.get("server", {}) or {}).get("serial_number") or ""),
            "discovery_raw_path": str(discovery_paths["raw"]),
            "plan_path": str(plan_paths["plan"]),
            "discovery_fingerprint": storage_discovery_fingerprint(discovery),
            "plan_summary": storage_plan_summary(plan),
            "reboot_expected": True,
        }
    )
    storage_cfg["include_in_ilo_run"] = bool(include_in_ilo_run)
    update_storage_latest_state(cfg, discovery=discovery, discovery_paths=discovery_paths, plan=plan, plan_paths=plan_paths)
    storage_cfg["state"] = "approved"
    storage_cfg["status_reason"] = ""


def clear_storage_approval_for_cfg(cfg: dict[str, Any]) -> None:
    storage_cfg = ensure_storage_config(cfg)
    storage_cfg["approval"] = {
        "state": "",
        "approved_at": "",
        "host": "",
        "serial_number": "",
        "discovery_raw_path": "",
        "plan_path": "",
        "discovery_fingerprint": "",
        "plan_summary": {},
        "reboot_expected": False,
    }
    if storage_cfg.get("latest_plan_path"):
        storage_cfg["state"] = "planned"
    elif storage_cfg.get("latest_discovery_raw_path"):
        storage_cfg["state"] = "discovered"
    else:
        storage_cfg["state"] = "idle"
    storage_cfg["status_reason"] = ""


def build_storage_review_context(cfg: dict[str, Any]) -> dict[str, Any]:
    storage_cfg = ensure_storage_config(cfg)
    approval = storage_cfg.get("approval", {}) or {}
    state = storage_cfg.get("state", "idle")
    state_map = {
        "idle": ("not configured", "warning"),
        "discovered": ("discovered", "progress"),
        "planned": ("planned", "progress"),
        "approved": ("approved", "ready"),
        "stale": ("stale", "danger"),
    }
    state_label, state_tone = state_map.get(state, (state, "warning"))
    return {
        "state": state,
        "state_label": state_label,
        "state_tone": state_tone,
        "status_reason": storage_cfg.get("status_reason", ""),
        "include_in_ilo_run": bool(storage_cfg.get("include_in_ilo_run")),
        "latest_discovery_raw_path": storage_cfg.get("latest_discovery_raw_path", ""),
        "latest_plan_path": storage_cfg.get("latest_plan_path", ""),
        "approval": approval,
        "approved": bool(approval.get("plan_path") and approval.get("discovery_raw_path") and state == "approved"),
        "stale": state == "stale",
    }


def resolve_storage_target_host(cfg: dict[str, Any]) -> dict[str, Any]:
    storage_cfg = ensure_storage_config(cfg)
    ilo_cfg = cfg.get("ilo", {}) or {}
    approval = storage_cfg.get("approval", {}) or {}

    candidates = [
        ("explicit storage target override", str(storage_cfg.get("target_host_override") or "").strip()),
        ("current kit iLO IP", str(ilo_cfg.get("current_ip") or "").strip()),
        ("current kit iLO host", str(ilo_cfg.get("host") or "").strip()),
        ("planned iLO target IP", str(ilo_cfg.get("target_ip") or cfg.get("ip_plan", {}).get("ilo") or "").strip()),
        ("latest discovery artifact", str(storage_cfg.get("latest_host") or "").strip()),
        ("approved storage artifact", str(approval.get("host") or "").strip()),
    ]
    for source, host in candidates:
        if host:
            return {
                "resolved": host,
                "source": source,
                "artifact_fallback": source in {"latest discovery artifact", "approved storage artifact"},
                "override_active": source == "explicit storage target override",
                "latest_artifact_host": str(storage_cfg.get("latest_host") or "").strip(),
                "approved_host": str(approval.get("host") or "").strip(),
                "valid": True,
                "error": "",
            }
    return {
        "resolved": "",
        "source": "",
        "artifact_fallback": False,
        "override_active": False,
        "latest_artifact_host": str(storage_cfg.get("latest_host") or "").strip(),
        "approved_host": str(approval.get("host") or "").strip(),
        "valid": False,
        "error": "No storage target host is resolved. Set the planned iLO IP on the iLO page or enter an explicit storage target override before using Storage setup actions.",
    }


def resolve_ilo_control_host(cfg: dict[str, Any]) -> str:
    ilo_cfg = cfg.get("ilo", {}) or {}
    return str(
        ilo_cfg.get("target_ip")
        or cfg.get("ip_plan", {}).get("ilo")
        or ilo_cfg.get("current_ip")
        or ilo_cfg.get("host")
        or ""
    ).strip()


def promote_final_ilo_endpoint(cfg: dict[str, Any], final_ip: str | None = None) -> dict[str, Any]:
    final = str(final_ip or resolve_ilo_control_host(cfg) or "").strip()
    if final:
        cfg.setdefault("ilo", {})["current_ip"] = final
        cfg.setdefault("ilo", {})["host"] = final
    return cfg


def resolve_storage_target_credentials(cfg: dict[str, Any]) -> dict[str, Any]:
    storage_cfg = ensure_storage_config(cfg)
    ilo_cfg = cfg.get("ilo", {}) or {}

    username = str(storage_cfg.get("username") or "").strip()
    username_source = "storage page override" if username else "current kit iLO username"
    if not username:
        username = str(ilo_cfg.get("username") or "").strip()

    password = str(storage_cfg.get("password") or "")
    password_source = "storage page override" if password else "current kit iLO password"
    if not password:
        password = str(ilo_cfg.get("password") or "")

    return {
        "username": username,
        "username_source": username_source,
        "password": password,
        "password_source": password_source,
        "valid": bool(username and password),
        "error": "" if (username and password) else "Storage credentials are incomplete. Set a username and password on the Storage / RAID page or in the current kit iLO config.",
    }


def build_storage_execution_status(cfg: dict[str, Any]) -> dict[str, Any]:
    storage_review = build_storage_review_context(cfg)
    included = bool(cfg.get("included", {}).get("storage"))
    if storage_review.get("approved") and included:
        return {
            "tone": "ready",
            "badge": "Storage approved",
            "summary": "Storage is approved and will be part of the upcoming iLO run.",
        }
    if storage_review.get("approved"):
        return {
            "tone": "progress",
            "badge": "Storage approved, not included",
            "summary": "Storage is approved, but it is not currently selected for the upcoming iLO run.",
        }
    return {
        "tone": "warning",
        "badge": "Storage not approved",
        "summary": "Storage will stay out of the run until it is reviewed and approved. Go to Storage setup, display current storage, build the layout, and approve it.",
    }


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
        if item.get("scope") in scope_set:
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
        tone = "ready" if "complete" in status.lower() else "pending" if "fail" in status.lower() else "progress"
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
        tone = "ready" if "complete" in status.lower() else "pending" if "fail" in status.lower() or "block" in status.lower() else "progress"
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
    items = [
        {
            "label": "Target server",
            "status": "Ready" if storage_target.get("valid") else "Needs setup",
            "tone": "ready" if storage_target.get("valid") else "pending",
            "summary": str(storage_target.get("resolved") or storage_target.get("error") or "No target server is resolved yet."),
        },
        {
            "label": "Sign-in",
            "status": "Ready" if storage_credentials.get("valid") else "Needs setup",
            "tone": "ready" if storage_credentials.get("valid") else "pending",
            "summary": str(storage_credentials.get("username") or storage_credentials.get("error") or "No sign-in details are ready yet."),
        },
        {
            "label": "Current storage read",
            "status": "Captured" if storage_export_paths else "Not read yet",
            "tone": "ready" if storage_export_paths else "pending",
            "summary": (
                str(storage_review.get("status_reason") or "The latest storage view is on this page.")
                if storage_export_paths
                else "Read the current storage before building a plan."
            ),
        },
        {
            "label": "Approved plan",
            "status": "Approved" if storage_review.get("approved") else "Needs review again" if storage_review.get("stale") else "Not approved",
            "tone": "ready" if storage_review.get("approved") else "pending",
            "summary": str(storage_review.get("approval", {}).get("plan_summary", {}).get("mode") or storage_review.get("status_reason") or "No approved storage plan yet."),
        },
        {
            "label": "Real-run handoff",
            "status": str(storage_execution_status.get("badge") or "Not ready"),
            "tone": str(storage_execution_status.get("tone") or "pending"),
            "summary": str(storage_execution_status.get("summary") or ""),
        },
    ]
    return items


def build_storage_change_summary(storage_review: dict[str, Any], storage_plan: dict[str, Any] | None) -> list[dict[str, str]]:
    approval = storage_review.get("approval", {}) or {}
    plan_summary = (
        (storage_plan or {}).get("planned_layout", {})
        or approval.get("plan_summary", {})
        or {}
    )
    if "os_raid1" in plan_summary:
        os_bays = str((plan_summary.get("os_raid1", {}) or {}).get("bays") or "Not selected")
        data_bays = str((plan_summary.get("data_raid6", {}) or {}).get("bays") or "Not selected")
        spare_bay = str((plan_summary.get("hot_spare", {}) or {}).get("bay") or "Not reserved")
    else:
        os_bays = str(plan_summary.get("os_bays") or "Not selected")
        data_bays = str(plan_summary.get("data_bays") or "Not selected")
        spare_bay = str(plan_summary.get("spare_bay") or "Not reserved")
    controller = str(plan_summary.get("controller") or "Not set")
    reboot_expected = bool(approval.get("reboot_expected"))
    approved_host = str(approval.get("host") or "Not set")
    return [
        {
            "name": "Current hardware view",
            "before": storage_review.get("status_reason") or "Read the current storage to capture the controller, drives, and existing volumes.",
            "after": f"Use host {approved_host} with controller {controller}.",
            "verify": "Make sure the approved plan still matches the same server and latest storage read.",
        },
        {
            "name": "Planned layout",
            "before": "Current volumes and drives stay untouched until the real run starts.",
            "after": f"OS RAID 1 bays {os_bays} | Data RAID bays {data_bays} | Hot spare {spare_bay}",
            "verify": "Use the exact approved plan artifact during the real run.",
        },
        {
            "name": "Apply confirmation",
            "before": "No destructive changes have been made yet on this page.",
            "after": f"Restart expected: {'Yes' if reboot_expected else 'No'} | Included in iLO run: {'Yes' if storage_review.get('include_in_ilo_run') else 'No'}",
            "verify": "Capture post-change storage discovery and validate the result after any required restart.",
        },
    ]


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
                os_drives = list(plan.get("os_drives") or [])
                data_drives = list(plan.get("data_drives") or [])
        except Exception:
            os_drives = []
            data_drives = []

    os_bays = str(plan_summary.get("os_bays") or ", ".join(str(d.get("bay") or "") for d in os_drives if d.get("bay")) or "unknown")
    data_bays = str(plan_summary.get("data_bays") or ", ".join(str(d.get("bay") or "") for d in data_drives if d.get("bay")) or "unknown")
    approved = str(approval.get("state") or storage_cfg.get("state") or "").lower() == "approved"
    preferred_target = (
        f"Approved OS RAID logical drive ({plan_summary.get('controller') or 'selected controller'}, bays {os_bays})"
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
    if workflow in {"ilo", "esxi", "windows", "qnap"}:
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
                fix="Read current storage again and approve the refreshed plan.",
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
    if workflow in {"esxi", "windows", "qnap"}:
        target_map = {
            "esxi": cfg.get("esxi", {}).get("management_ip") or cfg.get("ip_plan", {}).get("esxi", ""),
            "windows": cfg.get("windows", {}).get("ip_address") or cfg.get("ip_plan", {}).get("windows", ""),
            "qnap": cfg.get("qnap", {}).get("ip") or cfg.get("ip_plan", {}).get("qnap", ""),
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
        if workflow == "esxi" and cfg.get("included", {}).get("storage"):
            storage_review = build_storage_review_context(cfg)
            checks.append(
                validation_check(
                    "Storage readiness",
                    storage_review.get("approved") and not storage_review.get("stale"),
                    "Ready" if storage_review.get("approved") and not storage_review.get("stale") else "Review storage first if ESXi depends on the approved storage layout.",
                    why="If ESXi depends on storage, the approved storage plan must still be current.",
                    fix="Open Storage / RAID and approve the latest storage plan.",
                    href="/storage#storage-review-start",
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
    return checks


def checks_status(checks: list[dict[str, Any]]) -> tuple[str, str, str]:
    if any(not item.get("ok") for item in checks):
        return "failed", "Needs attention", "pending"
    if checks:
        return "complete", "Ready", "ready"
    return "not_started", "Not started", "pending"


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
        "current_summary": storage_review.get("status_reason") or "Read current storage to see what the server has today.",
        "planned_summary": storage_review.get("approval", {}).get("plan_summary", {}).get("mode") or "Build a storage plan to see the proposed layout.",
        "approved_summary": "Approved for a later iLO run." if storage_review.get("approved") else "No approved storage plan yet.",
        "result_summary": "Recent storage activity appears in Run History and the storage reports." if latest_history_entry_for_scope(history, ["storage-apply", "storage-reboot"]) else "No storage run has been recorded yet.",
        "checks": build_validation_checks(cfg, "storage"),
        "review_href": "/storage",
    }

    for key, name, target, config_summary in [
        ("ilo", "iLO", (cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host") or "").strip(), (cfg.get("ilo", {}).get("hostname") or "").strip()),
        ("esxi", "ESXi", (cfg.get("esxi", {}).get("management_ip") or cfg.get("ip_plan", {}).get("esxi") or "").strip(), (cfg.get("esxi", {}).get("hostname") or "").strip()),
        ("windows", "Windows", (cfg.get("windows", {}).get("ip_address") or cfg.get("ip_plan", {}).get("windows") or "").strip(), (cfg.get("windows", {}).get("vm_name") or "").strip()),
        ("qnap", "QNAP", (cfg.get("qnap", {}).get("ip") or cfg.get("ip_plan", {}).get("qnap") or "").strip(), (cfg.get("qnap", {}).get("hostname") or "").strip()),
    ]:
        checks = build_validation_checks(cfg, key)
        state, label, tone = checks_status(checks)
        if str(job.get("scope") or "") == key and str(job.get("status") or "") == "Running":
            state, label, tone = "running", workflow_state_ui("running")["label"], workflow_state_ui("running")["tone"]
        latest = latest_history_entry_for_scope(history, [key])
        if latest and "Fail" in str(latest.get("status", "")):
            state, label, tone = "failed", workflow_state_ui("failed")["label"], workflow_state_ui("failed")["tone"]
        elif latest and "Complete" in str(latest.get("status", "")) and state not in {"failed", "running"}:
            state, label, tone = "complete", workflow_state_ui("complete")["label"], workflow_state_ui("complete")["tone"]
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
            "review_href": f"/{key}",
        }

    return contexts


def build_recommended_next_step(cfg: dict[str, Any], workflow_contexts: dict[str, dict[str, Any]]) -> dict[str, str]:
    ilo_host = (cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host") or "").strip()
    if not ilo_host:
        return {"title": "Set the iLO target", "summary": "Start on the iLO page and save the current iLO address and credentials first.", "href": "/ilo"}
    if cfg.get("included", {}).get("storage") and workflow_contexts["storage"]["state"] in {"not_started", "discovered", "planned", "stale"}:
        return {"title": "Finish storage review", "summary": "Go to Storage / RAID, confirm the current server, and approve the exact storage plan before the final run.", "href": "/storage"}
    for key in ["esxi", "windows", "qnap"]:
        if cfg.get("included", {}).get(key) and workflow_contexts[key]["state"] in {"not_started", "failed"}:
            return {"title": f"Review {workflow_contexts[key]['name']} setup", "summary": f"Open the {workflow_contexts[key]['name']} page and finish the saved setup values.", "href": workflow_contexts[key]["review_href"]}
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
                "tone": "ready" if "Complete" in result else "pending" if ("Fail" in result or "Blocked" in result) else "progress",
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
        elif key in {"ilo", "esxi", "windows", "qnap"}:
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
        supported_real = [item for item in selected if item in {"ilo", "storage", "esxi"}]
        unsupported_real = [item for item in selected if item not in {"ilo", "storage", "esxi"}]
        if unsupported_real:
            return {"preview": preview_option, "real": None}
        if len(supported_real) > 1:
            real_scope = "multi__" + "__".join(supported_real)
            multi_stage_summary = "Runs the included iLO, storage, and ESXi stages in order."
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
    if scope == "esxi":
        return {
            "preview": preview_option,
            "real": {
                "scope": "esxi",
                "label": "Run for real",
                "summary": "Builds the custom ESXi installer ISO, mounts it through virtual media, sets one-time boot, and starts the real ESXi boot sequence.",
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
        if selected and all(item in {"ilo", "storage", "esxi"} for item in selected):
            return {
                "preview": preview_option,
                "real": {
                    "scope": scope,
                    "label": "Run selected for real",
                    "summary": "Runs the selected iLO, storage, and ESXi stages in order. Later stages use the final iLO IP after the iLO stage finishes.",
                },
            }
    return {"preview": preview_option, "real": None}


def build_run_center_readiness_matrix(cfg: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    included_cfg = cfg.get("included", {}) or {}
    storage_review = build_storage_review_context(cfg)
    selected_keys = run_center_scope_keys(scope, cfg)

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

    for key, name in [("ilo", "iLO"), ("storage", "Storage"), ("esxi", "ESXi"), ("windows", "Windows"), ("qnap", "QNAP")]:
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
            matrix.append(
                {
                    "name": name,
                    "label": "Ready",
                    "tone": "ready",
                    "summary": "Saved settings are ready for this run.",
                    "action": "Open the workspace if you want to review it again.",
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
        "controller_path": controller_path,
        "id": str(drive.get("id") or ""),
        "bay": str(drive.get("bay") or drive.get("id") or ""),
        "name": drive.get("name", ""),
        "model": drive.get("model", ""),
        "serial_number": drive.get("serial_number", ""),
        "size_gib": size_gib,
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
            controller = controller_by_path.get(str(drive.get("controller_path") or "").strip(), {})
            drive["controller_name"] = storage_controller_label(controller) if controller else str(drive.get("controller_path") or "")
            drive["eligible"] = bool(drive["size_gib"] > 0 and storage_status_is_eligible(drive["status"]))
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
    name = str(controller.get("name") or "").strip()
    model = str(controller.get("model") or "").strip()
    label_bits = [bit for bit in [name, model] if bit]
    return " / ".join(dict.fromkeys(label_bits)) or str(controller.get("path") or "").rsplit("/", 1)[-1]


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

    existing_volumes = []
    for source, items in (("hpe_smart_storage", hpe.get("volumes", [])), ("standard_redfish_storage", standard.get("volumes", []))):
        for item in items or []:
            existing_volumes.append({**item, "source": source})
    if existing_volumes:
        warnings.append("Existing logical volumes detected; default recommendation is wipe and rebuild before applying this target layout.")

    eligible_drives = []
    excluded_drives = []
    for source, items in (("hpe_smart_storage", hpe.get("drives", [])), ("standard_redfish_storage", standard.get("drives", []))):
        for item in items or []:
            drive = normalized_plan_drive(item, source)
            drive_controller = controller_by_path.get(str(drive.get("controller_path") or "").strip(), {})
            drive["controller_name"] = storage_controller_label(drive_controller) if drive_controller else str(drive.get("controller_path") or "")
            if drive["size_gib"] <= 0:
                excluded_drives.append({**drive, "exclude_reason": "Missing or zero drive size."})
            elif not storage_status_is_eligible(drive["status"]):
                excluded_drives.append({**drive, "exclude_reason": f"Drive status is not eligible: {drive['status'] or 'unknown'}."})
            else:
                eligible_drives.append(drive)

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

    eligible_by_identity = {storage_drive_identity(drive): drive for drive in eligible_drives if storage_drive_identity(drive)}
    eligible_by_bay = {str(drive.get("bay") or ""): drive for drive in eligible_drives}
    eligible_by_bay_by_controller: dict[str, dict[str, dict[str, Any]]] = {}
    for drive in eligible_drives:
        controller_path = str(drive.get("controller_path") or "").strip()
        bay = str(drive.get("bay") or "")
        eligible_by_bay_by_controller.setdefault(controller_path, {})[bay] = drive
    bay_counts: dict[str, int] = {}
    for drive in eligible_drives:
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
            os_pair = [eligible_by_identity[drive_id] for drive_id in selected_os_ids if drive_id in eligible_by_identity]
            missing_os = [drive_id for drive_id in selected_os_ids if drive_id not in eligible_by_identity]
            if missing_os:
                custom_blockers.append(f"Selected OS drives are not eligible or were not found by drive identity: {', '.join(missing_os)}.")
            os_explanation = "Using the drives chosen below for the OS mirror."
        elif selected_os_bays:
            os_bay_lookup = eligible_by_bay_by_controller.get(selected_os_controller_path, eligible_by_bay) if selected_os_controller_path else eligible_by_bay
            os_pair = [os_bay_lookup[bay] for bay in selected_os_bays if bay in os_bay_lookup]
            missing_os = [bay for bay in selected_os_bays if bay not in os_bay_lookup]
            if missing_os:
                custom_blockers.append(f"Selected OS drives are not eligible or were not found: {', '.join(missing_os)}.")
            os_explanation = "Using the drives chosen below for the OS mirror."
        if selected_data_ids:
            data_set = [eligible_by_identity[drive_id] for drive_id in selected_data_ids if drive_id in eligible_by_identity]
            missing_data = [drive_id for drive_id in selected_data_ids if drive_id not in eligible_by_identity]
            if missing_data:
                custom_blockers.append(f"Selected data drives are not eligible or were not found by drive identity: {', '.join(missing_data)}.")
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
            if not hot_spare:
                custom_blockers.append(f"Selected hot spare drive identity was not eligible or was not found: {selected_spare_id}.")
        elif selected_spare_bay:
            spare_bay_lookup = eligible_by_bay_by_controller.get(selected_data_controller_path, eligible_by_bay) if selected_data_controller_path else eligible_by_bay
            hot_spare = dict(spare_bay_lookup.get(selected_spare_bay) or {})
            if not hot_spare:
                custom_blockers.append(f"Selected hot spare bay was not eligible or was not found: {selected_spare_bay}.")
        os_identity_set = {storage_drive_identity(drive) for drive in os_pair if storage_drive_identity(drive)}
        data_identity_set = {storage_drive_identity(drive) for drive in data_set if storage_drive_identity(drive)}
        spare_identity = storage_drive_identity(hot_spare) if hot_spare else ""
        if len(os_identity_set) != len(os_pair) or len(data_identity_set) != len(data_set) or (hot_spare and not spare_identity):
            custom_blockers.append("Every selected drive must have a stable drive identity before it can be approved.")
        selected_identities = [storage_drive_identity(drive) for drive in os_pair + data_set + ([hot_spare] if hot_spare else []) if storage_drive_identity(drive)]
        if len(selected_identities) != len(set(selected_identities)):
            overlap_blockers.append("The same drive identity cannot be reused in the OS, data, or hot spare selections.")
        os_controller_set = {str(drive.get("controller_path") or "").strip() for drive in os_pair if str(drive.get("controller_path") or "").strip()}
        data_controller_set = {str(drive.get("controller_path") or "").strip() for drive in data_set if str(drive.get("controller_path") or "").strip()}
        if len(os_controller_set) > 1:
            custom_blockers.append("The OS array cannot span multiple storage controllers.")
        if len(data_controller_set) > 1:
            custom_blockers.append("The data array cannot span multiple storage controllers.")
        if selected_os_controller_path and os_controller_set and os_controller_set != {selected_os_controller_path}:
            custom_blockers.append("Selected OS drives do not belong to the selected OS controller.")
        if selected_data_controller_path and data_controller_set and data_controller_set != {selected_data_controller_path}:
            custom_blockers.append("Selected data drives do not belong to the selected data controller.")
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
        if data_set and len(compatibility_group) > 1:
            warnings.append("The selected data drives and hot spare differ by media type, protocol, or size. Review the layout before approving.")
        blockers.extend(custom_blockers + overlap_blockers)
        selected_identity_set = set(selected_identities)
        raid_excluded = [
            {**drive, "exclude_reason": "Not selected for the custom data layout."}
            for drive in eligible_drives
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
    pre_apply_summary = {
        "mode": apply_readiness["next_action"],
        "volumes_to_remove": existing_volumes,
        "planned_layout": planned_layout,
        "reserved_hot_spare": hot_spare,
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
        "planned_layout": planned_layout,
        "os_raid1": {"raid": selected_os_raid, "label": raid_label(selected_os_raid), "target_size_gib": 500, "drives": os_pair, "explanation": os_explanation},
        "data_raid6": {"raid": selected_data_raid, "label": raid_label(selected_data_raid), "feasible": not validate_raid_drive_count(selected_data_raid, data_set, section="data"), "drives": data_set, "drive_count": len(data_set), "explanation": data_explanation},
        "hot_spare": {"required": False, "drive": hot_spare, "reserved": bool(hot_spare)},
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
    controller = plan.get("source_discovery", {}).get("controller", {}) or {}
    return {
        "mode": apply_mode,
        "controller": controller,
        "os_raid1": {
            "raid": normalize_raid_choice("os", str(plan.get("os_raid1", {}).get("raid") or ""), allow_empty=True) if plan.get("os_raid1", {}).get("drives") else "",
            "label": f"OS {raid_label(str(plan.get('os_raid1', {}).get('raid') or 'RAID1'))} logical drive",
            "target_size_gib": plan.get("os_raid1", {}).get("target_size_gib", 500),
            "bays": [drive.get("bay") for drive in plan.get("os_raid1", {}).get("drives", [])],
            "drive_paths": [drive.get("path") for drive in plan.get("os_raid1", {}).get("drives", [])],
            "drives": list(plan.get("os_raid1", {}).get("drives", []) or []),
        },
        "data_raid6": {
            "raid": normalize_raid_choice("data", str(plan.get("data_raid6", {}).get("raid") or ""), allow_empty=True) if plan.get("data_raid6", {}).get("drives") else "",
            "label": f"Data {raid_label(str(plan.get('data_raid6', {}).get('raid') or 'RAID6'))} logical drive",
            "bays": [drive.get("bay") for drive in plan.get("data_raid6", {}).get("drives", [])],
            "drive_paths": [drive.get("path") for drive in plan.get("data_raid6", {}).get("drives", [])],
            "drives": list(plan.get("data_raid6", {}).get("drives", []) or []),
        },
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
    for section in ("os_raid1", "data_raid6"):
        for drive in (plan.get(section, {}) or {}).get("drives", []) or []:
            if drive:
                drives.append({**drive, "_section": section})
    spare = (plan.get("hot_spare", {}) or {}).get("drive", {}) or {}
    if spare:
        drives.append({**spare, "_section": "hot_spare"})
    return drives


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

    candidates = _storage_controller_candidates(plan, live_discovery)
    live_controller: dict[str, Any] = {}
    live_drive_map: dict[str, dict[str, Any]] = {}

    for candidate in candidates:
        candidate_drives = _candidate_drives_for_controller(live_discovery, candidate)
        candidate_map: dict[str, dict[str, Any]] = {}
        candidate_rejections: list[str] = []
        for saved_drive in selected_drives:
            live_drive = _find_live_drive(saved_drive, candidate_drives)
            if not live_drive:
                candidate_rejections.append(f"Approved bay {saved_drive.get('bay') or '?'} was not found on live controller {candidate.get('path') or '(unknown)'}.")
                continue
            matched, reason = _drive_identity_matches(saved_drive, live_drive)
            if not matched:
                candidate_rejections.append(reason)
                continue
            candidate_map[str(saved_drive.get("path") or saved_drive.get("bay") or len(candidate_map))] = live_drive
        if not candidate_rejections:
            live_controller = candidate
            live_drive_map = candidate_map
            break
        if not rejection_reasons:
            rejection_reasons = candidate_rejections

    if not live_controller:
        if not rejection_reasons:
            rejection_reasons.append("The approved storage controller was not found in live discovery.")
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

    live_controller_path = str(live_controller.get("path") or "").rstrip("/")
    if saved_controller_path and live_controller_path and saved_controller_path != live_controller_path:
        differences.append(f"Controller Redfish path changed from {saved_controller_path} to {live_controller_path}.")
        corrections.append(f"Remapped controller path to {live_controller_path}.")
    remapped_plan.setdefault("source_discovery", {})["controller"] = dict(live_controller)

    def remap_drive(drive: dict[str, Any]) -> dict[str, Any]:
        key = str(drive.get("path") or drive.get("bay") or "")
        live_drive = live_drive_map.get(key) or _find_live_drive(drive, _candidate_drives_for_controller(live_discovery, live_controller))
        if not live_drive:
            return drive
        normalized = normalized_plan_drive(live_drive, str(live_controller.get("source") or live_drive.get("source") or ""))
        if str(drive.get("path") or "").strip() and normalized.get("path") and str(drive.get("path")) != str(normalized.get("path")):
            corrections.append(f"Remapped bay {drive.get('bay') or normalized.get('bay')} drive path to {normalized.get('path')}.")
        return {**drive, **normalized}

    for section in ("os_raid1", "data_raid6"):
        section_state = remapped_plan.get(section, {}) or {}
        section_state["drives"] = [remap_drive(drive) for drive in section_state.get("drives", []) or []]
        remapped_plan[section] = section_state
    spare = (remapped_plan.get("hot_spare", {}) or {}).get("drive", {}) or {}
    if spare:
        remapped_plan.setdefault("hot_spare", {})["drive"] = remap_drive(spare)

    live_volumes = [
        volume
        for volume in storage_discovery_sources(live_discovery)["volumes"]
        if storage_item_matches_controller(volume, live_controller)
    ]
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
            "controller_path": live_controller_path,
            "controller_model": live_controller.get("model") or live_controller.get("name") or "",
            "selected_bays": [drive.get("bay") for drive in selected_drives],
        },
        differences=differences,
        safe_corrections_attempted=corrections,
        options_discovered=storage_discovered_options(live_discovery),
        selected_action=f"Use live controller path {live_controller_path or '(unknown)'} for storage apply.",
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
    manual_curl = power_manual_curl_command({**power_details, "action": power_details.get("action") or "On"})
    if power_details:
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


def choose_storage_apply_platform(discovery: dict, plan: dict) -> dict[str, Any]:
    summary = discovery.get("summary", {}) or {}
    server = summary.get("server", {}) or {}
    ilo = summary.get("ilo", {}) or {}
    capabilities = summary.get("capabilities", {}) or {}

    controller = plan.get("source_discovery", {}).get("controller", {}) or {}
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
        surface = _standard_redfish_storage_apply_surface(discovery, controller.get("path", ""))
        if surface.get("volumes_path"):
            return {
                "id": "standard_redfish_volumes",
                "label": "Standard Redfish Storage Volumes",
                "supported": True,
                "settings_path": "",
                "controller_path": surface.get("storage_path", ""),
                "volumes_path": surface.get("volumes_path", ""),
                "reset_target": surface.get("reset_target", ""),
                "reason": "",
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
        raise ValueError("RAID plan is not valid for apply.")
    controller = plan.get("source_discovery", {}).get("controller", {}) or {}
    if not (controller.get("name") or controller.get("model") or controller.get("path")):
        raise ValueError("No storage controller is selected for apply.")
    for drive in list(plan.get("os_raid1", {}).get("drives", []) or []) + list(plan.get("data_raid6", {}).get("drives", []) or []):
        if not storage_item_matches_controller(drive, controller):
            raise ValueError("Selected storage drives must all belong to the chosen controller.")
    for volume in plan.get("existing_logical_volumes", []) or []:
        if not storage_item_matches_controller(volume, controller):
            raise ValueError("Existing logical volumes must belong to the chosen controller before apply.")
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
    volumes_path = str(platform.get("volumes_path") or "").strip()
    if not volumes_path:
        raise ILOError("Standard Redfish volume collection path could not be determined from discovery.")

    responses: list[Any] = []
    current = starting_step
    intent = build_storage_apply_intent(plan, apply_mode)
    controller_name = apply_state["controller"].get("name") or apply_state["controller"].get("model") or ""
    storage_path = str(platform.get("controller_path") or "").strip()
    readiness = client.wait_for_storage_device_discovery()
    responses.append({"device_discovery": readiness, "reboot_required": False})
    if not readiness.get("ready"):
        raise ILOError(
            "Storage device discovery is not complete on the active server. "
            f"Current state: {readiness.get('state') or 'unknown'}."
        )

    capabilities = client.get_standard_storage_volume_capabilities(volumes_path)
    responses.append({"volume_capabilities": capabilities, "reboot_required": False})

    existing_volume_paths = [
        str(volume.get("path") or "").strip()
        for volume in plan.get("existing_logical_volumes", []) or []
        if str(volume.get("path") or "").strip()
    ]

    if apply_mode == "wipe_rebuild":
        targets = {"controller": controller_name, "path": storage_path or volumes_path}
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
                else "No existing volumes were captured in the approved plan; attempting controller reset to defaults if available."
            ),
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        if existing_volume_paths:
            for volume_path in existing_volume_paths:
                delete_response = client.delete_standard_storage_volume(volume_path)
                responses.append(delete_response)
        elif platform.get("reset_target"):
            reset_response = client.reset_standard_storage_to_defaults(str(platform.get("reset_target") or "").strip(), reset_type="ResetAll")
            responses.append(reset_response)
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
                else "Submitted Storage.ResetToDefaults because no explicit volume paths were available in the approved plan."
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
            targets={"controller": controller_name},
            details="Create-only mode selected; no existing standard Redfish volumes will be removed.",
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
            targets={"controller": controller_name, "bays": os_intent.get("bays", []), "path": volumes_path},
            details=f"Submitting {os_label} to the standard Redfish volume collection.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        os_response = client.create_standard_storage_volume(volumes_path, os_intent, capabilities=capabilities)
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
            targets={"controller": controller_name, "bays": os_intent.get("bays", []), "path": volumes_path},
            details=f"Submitted {os_label} to {volumes_path}.",
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
            targets={"controller": controller_name},
            details="No OS array is selected in this plan.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    current += 1

    data_intent = intent["data_raid6"]
    data_label = str(data_intent.get("label") or "Data logical drive")
    spare_intent = intent["hot_spare"]
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
            targets={"controller": controller_name, "bays": data_intent.get("bays", []), "path": volumes_path},
            details=f"Submitting {data_label} to the standard Redfish volume collection.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
        data_response = client.create_standard_storage_volume(volumes_path, data_intent, spare_intent=spare_intent, capabilities=capabilities)
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
            targets={"controller": controller_name, "bays": data_intent.get("bays", []), "path": volumes_path},
            details=f"Submitted {data_label} to {volumes_path}.",
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
            targets={"controller": controller_name},
            details="No data array is selected in this plan.",
            progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
        )
    current += 1

    spare_bay = str(spare_intent.get("bay", "") or "").strip()
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
            targets={"controller": controller_name, "bays": [spare_bay]},
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
            targets={"controller": controller_name, "bays": [spare_bay]},
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
            targets={"controller": controller_name},
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
    apply_steps = 10
    total_steps = apply_steps
    job = {
        "status": "Running",
        "scope": f"storage-apply:{apply_mode}",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total_steps,
        "logs": [],
    }
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
            platform = choose_storage_apply_platform(pre_change_discovery, plan)
            platform_id = str(platform.get("id") or "")
            if platform_id in {"gen10_hpe_smartstorageconfig", "standard_redfish_volumes"}:
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
        platform_supported = platform.get("id") in {"gen10_hpe_smartstorageconfig", "standard_redfish_volumes"}
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
        elif platform.get("id") == "standard_redfish_volumes":
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
    total_steps = 5
    job = {
        "status": "Running",
        "scope": "storage-reboot",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total_steps,
        "logs": [],
        "apply_path": "",
        "reboot_required": True,
        "workflow_state": "reboot_requested",
        "reboot_status": "Running",
        "storage_run_directory": str(apply_paths["directory"]),
    }
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
        platform = choose_storage_apply_platform(pre_change_discovery, plan)
        platform_id = str(platform.get("id") or "")
        if platform_id in {"gen10_hpe_smartstorageconfig", "standard_redfish_volumes"}:
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
    platform_supported = platform.get("id") in {"gen10_hpe_smartstorageconfig", "standard_redfish_volumes"}
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
    elif platform.get("id") == "standard_redfish_volumes":
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

def default_config():
    return {
        "site": {"name": "Kit-01"},
        "shared_network": {
            "subnet": "10.10.8.0/24",
            "dns_servers": ["", "", "", ""],
        },
        "shared_snmp": {
            "v3_username": "",
            "v3_auth_protocol": "SHA",
            "v3_auth_password": "",
            "v3_priv_protocol": "AES",
            "v3_priv_password": "",
            "users": [],
        },
        "ip_plan": {
            "gateway": "10.10.8.1",
            "switch": "10.10.8.2",
            "esxi": "10.10.8.10",
            "ilo": "10.10.8.11",
            "windows": "10.10.8.20",
            "qnap": "10.10.8.30",
            "iosafe": "10.10.8.31",
        },
        "included": {
            "ilo": True,
            "esxi": True,
            "windows": False,
            "qnap": False,
            "iosafe": False,
            "cisco_switch": False,
            "storage": False,
        },
        "section_completion": {
            "basics": False,
            "network": False,
            "included": False,
            "credentials": False,
        },
        "ilo": {
            "host": "",
            "current_ip": "",
            "target_ip": "",
            "subnet_mask": "255.255.255.0",
            "gateway": "",
            "dns_servers": ["", "", "", ""],
            "hostname": "ilo01",
            "username": "Administrator",
            "password": "",
            "additional_users": [],
        },
        "esxi": {
            "version": "7",
            "base_iso_path": "",
            "hostname": "esxi01",
            "management_ip": "",
            "subnet_mask": "255.255.255.0",
            "gateway": "",
            "dns_servers": [],
            "root_password": "",
            "debug_no_reboot": False,
        },
        "windows": {
            "vm_name": "win2022-01",
            "admin_password": "",
            "ip_address": "",
            "subnet_mask": "255.255.255.0",
            "gateway": "",
            "dns_servers": [],
        },
        "qnap": {
            "hostname": "qnap01",
            "ip": "",
            "username": "admin",
            "password": "",
        },
        "iosafe": {
            "hostname": "iosafe01",
            "ip": "",
            "username": "admin",
            "password": "",
        },
        "cisco_switch": {
            "hostname": "sw01",
            "ip": "",
            "username": "admin",
            "password": "",
        },
        "storage": {
            "target_host_override": "",
            "username": "",
            "password": "",
            "include_in_ilo_run": False,
            "latest_discovery_raw_path": "",
            "latest_discovery_fingerprint": "",
            "latest_plan_path": "",
            "latest_plan_summary": {},
            "latest_host": "",
            "latest_serial_number": "",
            "state": "idle",
            "status_reason": "",
            "approval": {
                "state": "",
                "approved_at": "",
                "host": "",
                "serial_number": "",
                "discovery_raw_path": "",
                "plan_path": "",
                "discovery_fingerprint": "",
                "plan_summary": {},
                "reboot_expected": False,
            },
        },
    }


def merge_defaults(cfg):
    base = default_config()
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return normalize_ilo_config(base)


def normalize_ilo_additional_users(entries: list[dict[str, Any]] | Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(entries, list):
        return normalized
    for item in entries:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        password = str(item.get("password") or "")
        role = str(item.get("role") or "Administrator").strip() or "Administrator"
        if not username or not password:
            continue
        normalized.append({
            "username": username,
            "password": password,
            "role": role,
        })
    return normalized


def normalize_snmp_users(entries: list[dict[str, Any]] | Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(entries, list):
        return normalized
    for item in entries:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        auth_protocol = str(item.get("auth_protocol") or "SHA").strip() or "SHA"
        auth_password = str(item.get("auth_password") or "")
        priv_protocol = str(item.get("priv_protocol") or "AES").strip() or "AES"
        priv_password = str(item.get("priv_password") or "")
        if not username:
            continue
        normalized.append({
            "username": username,
            "auth_protocol": auth_protocol,
            "auth_password": auth_password,
            "priv_protocol": priv_protocol,
            "priv_password": priv_password,
        })
    return normalized


def extract_ilo_additional_users_from_form(form: Any) -> list[dict[str, str]]:
    usernames = form.getlist("ilo_extra_username")
    passwords = form.getlist("ilo_extra_password")
    roles = form.getlist("ilo_extra_role")
    entries: list[dict[str, str]] = []
    for index, username in enumerate(usernames):
        entries.append({
            "username": username,
            "password": passwords[index] if index < len(passwords) else "",
            "role": roles[index] if index < len(roles) else "Administrator",
        })
    return normalize_ilo_additional_users(entries)


def extract_snmp_users_from_form(
    form: Any,
    *,
    primary_username: str,
    primary_auth_protocol: str,
    primary_auth_password: str,
    primary_priv_protocol: str,
    primary_priv_password: str,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if str(primary_username or "").strip():
        entries.append({
            "username": primary_username,
            "auth_protocol": primary_auth_protocol,
            "auth_password": primary_auth_password,
            "priv_protocol": primary_priv_protocol,
            "priv_password": primary_priv_password,
        })

    usernames = form.getlist("snmp_extra_username")
    auth_protocols = form.getlist("snmp_extra_auth_protocol")
    auth_passwords = form.getlist("snmp_extra_auth_password")
    priv_protocols = form.getlist("snmp_extra_priv_protocol")
    priv_passwords = form.getlist("snmp_extra_priv_password")
    for index, username in enumerate(usernames):
        entries.append({
            "username": username,
            "auth_protocol": auth_protocols[index] if index < len(auth_protocols) else "SHA",
            "auth_password": auth_passwords[index] if index < len(auth_passwords) else "",
            "priv_protocol": priv_protocols[index] if index < len(priv_protocols) else "AES",
            "priv_password": priv_passwords[index] if index < len(priv_passwords) else "",
        })
    return normalize_snmp_users(entries)


def normalize_ilo_config(cfg: dict):
    ilo_cfg = cfg.setdefault("ilo", {})
    snmp_cfg = cfg.setdefault("shared_snmp", {})
    legacy_host = (ilo_cfg.get("host") or "").strip()
    current_ip = (ilo_cfg.get("current_ip") or legacy_host or "").strip()
    target_ip = (ilo_cfg.get("target_ip") or "").strip()
    subnet_mask = (ilo_cfg.get("subnet_mask") or cfg.get("ip_plan", {}).get("netmask") or "").strip()
    gateway = (ilo_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip()
    dns_servers = ilo_cfg.get("dns_servers")

    if not target_ip:
        target_ip = (cfg.get("ip_plan", {}).get("ilo") or current_ip or legacy_host).strip()
    if not current_ip:
        current_ip = target_ip
    if not isinstance(dns_servers, list):
        dns_servers = cfg.get("shared_network", {}).get("dns_servers", [])

    normalized_dns = [str(x).strip() for x in dns_servers[:4]]
    while len(normalized_dns) < 4:
        normalized_dns.append("")

    ilo_cfg["current_ip"] = current_ip
    ilo_cfg["target_ip"] = target_ip
    ilo_cfg["subnet_mask"] = subnet_mask
    ilo_cfg["gateway"] = gateway
    ilo_cfg["dns_servers"] = normalized_dns
    ilo_cfg["host"] = current_ip
    ilo_cfg["additional_users"] = normalize_ilo_additional_users(ilo_cfg.get("additional_users", []))

    normalized_snmp_users = normalize_snmp_users(snmp_cfg.get("users", []))
    if not normalized_snmp_users:
        primary_snmp_username = str(snmp_cfg.get("v3_username") or "").strip()
        if primary_snmp_username:
            normalized_snmp_users = [{
                "username": primary_snmp_username,
                "auth_protocol": str(snmp_cfg.get("v3_auth_protocol") or "SHA").strip() or "SHA",
                "auth_password": str(snmp_cfg.get("v3_auth_password") or ""),
                "priv_protocol": str(snmp_cfg.get("v3_priv_protocol") or "AES").strip() or "AES",
                "priv_password": str(snmp_cfg.get("v3_priv_password") or ""),
            }]
    snmp_cfg["users"] = normalized_snmp_users
    primary_snmp_user = normalized_snmp_users[0] if normalized_snmp_users else {}
    snmp_cfg["v3_username"] = primary_snmp_user.get("username", "")
    snmp_cfg["v3_auth_protocol"] = primary_snmp_user.get("auth_protocol", str(snmp_cfg.get("v3_auth_protocol") or "SHA"))
    snmp_cfg["v3_auth_password"] = primary_snmp_user.get("auth_password", str(snmp_cfg.get("v3_auth_password") or ""))
    snmp_cfg["v3_priv_protocol"] = primary_snmp_user.get("priv_protocol", str(snmp_cfg.get("v3_priv_protocol") or "AES"))
    snmp_cfg["v3_priv_password"] = primary_snmp_user.get("priv_password", str(snmp_cfg.get("v3_priv_password") or ""))
    return cfg


def subnet_details(subnet: str):
    net = ipaddress.ip_network(subnet, strict=False)
    total = net.num_addresses
    if total >= 2:
        first_usable = net.network_address + 1
        last_usable = net.broadcast_address - 1
        max_usable_offset = total - 2
    else:
        first_usable = net.network_address
        last_usable = net.broadcast_address
        max_usable_offset = 0

    return {
        "subnet": str(net),
        "network_address": str(net.network_address),
        "broadcast_address": str(net.broadcast_address),
        "netmask": str(net.netmask),
        "prefixlen": net.prefixlen,
        "total_addresses": total,
        "first_usable": str(first_usable),
        "last_usable": str(last_usable),
        "max_usable_offset": max_usable_offset,
    }


def ip_at_offset(network_cidr: str, offset: int, require_usable: bool = True) -> str:
    net = ipaddress.ip_network(network_cidr, strict=False)

    if offset < 0:
        raise ValueError(f"Offset {offset} cannot be negative")

    addr = net.network_address + offset

    if addr not in net:
        raise ValueError(f"Offset {offset} is outside subnet {network_cidr}")

    if require_usable:
        if addr == net.network_address:
            raise ValueError(f"Offset {offset} resolves to network address {addr}")
        if addr == net.broadcast_address:
            raise ValueError(f"Offset {offset} resolves to broadcast address {addr}")

    return str(addr)


def build_default_ip_plan(subnet: str) -> dict:
    return {key: ip_at_offset(subnet, offset) for key, offset in DEFAULT_IP_OFFSETS.items()}


def validate_ip_for_subnet(network_cidr: str, value: str, label: str) -> str:
    try:
        addr = ipaddress.ip_address((value or "").strip())
    except ValueError as e:
        raise ValueError(f"{label} must be a valid IP address") from e

    net = ipaddress.ip_network(network_cidr, strict=False)
    if addr not in net:
        raise ValueError(f"{label} must be inside subnet {network_cidr}")
    if addr == net.network_address:
        raise ValueError(f"{label} cannot be the network address")
    if addr == net.broadcast_address:
        raise ValueError(f"{label} cannot be the broadcast address")
    return str(addr)


def build_legacy_offset_plan(cfg: dict, subnet: str) -> dict:
    nd = cfg.get("shared_network", {})
    return {
        key: ip_at_offset(subnet, int(nd.get(f"{key}_offset", offset)))
        for key, offset in DEFAULT_IP_OFFSETS.items()
    }


def normalize_ip_plan(cfg: dict, subnet: str) -> dict:
    raw_plan = cfg.get("ip_plan") or {}
    if all(raw_plan.get(key) for key in DEFAULT_IP_OFFSETS):
        plan_source = raw_plan
    else:
        # Older kits may still have offset-based config only.
        plan_source = build_legacy_offset_plan(cfg, subnet)

    plan = {
        key: validate_ip_for_subnet(subnet, plan_source.get(key, ""), key.replace("_", " ").upper())
        for key in DEFAULT_IP_OFFSETS
    }

    ip_owners: dict[str, list[str]] = {}
    for key, value in plan.items():
        ip_owners.setdefault(value, []).append(key.replace("_", " "))
    duplicates = [
        f"{ip} ({', '.join(labels)})"
        for ip, labels in ip_owners.items()
        if len(labels) > 1
    ]
    if duplicates:
        raise ValueError("Each device IP must be unique within the kit. Duplicate: " + "; ".join(duplicates))
    return plan


def calc_ip_plan(cfg):
    nd = cfg.get("shared_network", {})
    subnet = nd.get("subnet", "10.10.8.0/24")
    details = subnet_details(subnet)
    plan = normalize_ip_plan(cfg, subnet)

    return {
        "subnet": details["subnet"],
        "netmask": details["netmask"],
        "prefixlen": details["prefixlen"],
        "first_usable": details["first_usable"],
        "last_usable": details["last_usable"],
        "max_usable_offset": details["max_usable_offset"],
        **plan,
    }


def apply_ip_plan(cfg):
    cfg = merge_defaults(cfg)
    plan = calc_ip_plan(cfg)
    shared_dns = [x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x]

    cfg["ip_plan"] = plan
    cfg["ilo"]["target_ip"] = plan["ilo"]
    cfg["ilo"]["current_ip"] = (cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or plan["ilo"]).strip()
    cfg["ilo"]["subnet_mask"] = (cfg["ilo"].get("subnet_mask") or plan["netmask"]).strip()
    cfg["ilo"]["gateway"] = (cfg["ilo"].get("gateway") or plan["gateway"]).strip()
    ilo_dns = cfg["ilo"].get("dns_servers", [])
    cfg["ilo"]["dns_servers"] = ilo_dns if any(x and str(x).strip() for x in ilo_dns) else cfg.get("shared_network", {}).get("dns_servers", ["", "", "", ""])[:4]
    cfg["ilo"]["host"] = cfg["ilo"]["current_ip"]

    cfg["esxi"]["management_ip"] = plan["esxi"]
    cfg["esxi"]["gateway"] = plan["gateway"]
    cfg["esxi"]["subnet_mask"] = plan["netmask"]
    cfg["esxi"]["dns_servers"] = shared_dns if shared_dns else [plan["gateway"]]

    cfg["windows"]["ip_address"] = plan["windows"]
    cfg["windows"]["gateway"] = plan["gateway"]
    cfg["windows"]["subnet_mask"] = plan["netmask"]
    cfg["windows"]["dns_servers"] = shared_dns if shared_dns else [plan["gateway"]]

    cfg["qnap"]["ip"] = plan["qnap"]
    cfg["iosafe"]["ip"] = plan["iosafe"]
    cfg["cisco_switch"]["ip"] = plan["switch"]

    return cfg


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


def build_execution_review(cfg: dict, scope: str):
    lines = [f"Execution scope: {scope}", ""]
    execution_mode = execution_mode_for_scope(scope)
    storage_review = build_storage_review_context(cfg)
    selected_scope_keys = run_center_scope_keys(scope, cfg)
    esxi_install_review = build_esxi_install_review(cfg) if scope in {"esxi", "included"} or "esxi" in selected_scope_keys else {}
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
            "target": cfg["cisco_switch"].get("ip", "") or cfg.get("ip_plan", {}).get("switch", "") or "Not set",
            "summary": "Run the saved switch management setup and template-driven changes.",
            "review_href": "/global-settings",
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
        if key in {"ilo", "esxi", "windows", "qnap"}:
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
        if key == "storage":
            approval = storage_review.get("approval", {}) or {}
            plan_summary = approval.get("plan_summary", {}) or {}
            return [
                f"Controller: {plan_summary.get('controller') or 'Not set'}",
                f"OS RAID 1 bays: {plan_summary.get('os_bays') or 'Not selected'}",
                f"Data RAID bays: {plan_summary.get('data_bays') or 'Not selected'}",
                f"Hot spare bay: {plan_summary.get('spare_bay') or 'Not reserved'}",
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
        for key in ["ilo", "esxi", "windows", "qnap", "iosafe", "cisco_switch"]:
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
                lines.append(
                    f"- Storage layout -> controller {plan_summary.get('controller') or '(unknown)'} | "
                    f"OS bays {plan_summary.get('os_bays') or '(none)'} | "
                    f"data bays {plan_summary.get('data_bays') or '(none)'} | "
                    f"spare bay {plan_summary.get('spare_bay') or '(none)'}"
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
        for key in ["ilo", "esxi", "windows", "qnap", "iosafe", "cisco_switch"]:
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
    if scope == "ilo":
        return [
            "Preview iLO target and sign-in",
            "Preview iLO network changes",
            "Preview DNS and SNMP changes",
            "Preview complete - ready for real iLO execution",
        ]
    if scope == "esxi":
        return [
            "Preview ESXi configuration",
            "Preview generated install inputs",
            "Preview ISO patch inputs",
            "Preview install target checks",
            "Preview complete - ready for real ESXi execution",
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
        if included.get("storage"):
            steps.append("Preview approved storage plan")
        if included.get("ilo"):
            steps.append("Preview iLO actions")
        if included.get("esxi"):
            steps.append("Preview ESXi actions")
        if included.get("windows"):
            steps.append("Preview Windows actions")
        if included.get("qnap"):
            steps.append("Preview QNAP actions")
        if included.get("iosafe"):
            steps.append("Preview ioSafe actions")
        if included.get("cisco_switch"):
            steps.append("Preview Cisco switch actions")
        steps.append("Preview complete - ready for real included-kit execution")
        return steps
    if scope.startswith("multi__"):
        steps = ["Preview selected stages"]
        for key in run_center_scope_keys(scope, cfg):
            label = {
                "ilo": "Preview iLO actions",
                "esxi": "Preview ESXi actions",
                "windows": "Preview Windows actions",
                "qnap": "Preview QNAP actions",
                "iosafe": "Preview ioSafe actions",
                "cisco_switch": "Preview Cisco switch actions",
            }.get(key, f"Preview {key} actions")
            steps.append(label)
        steps.append("Preview complete - ready for the selected run")
        return steps
    return ["Preview scope is not defined"]


def validate_execution_scope(cfg: dict, scope: str) -> None:
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
    ensure_run_bundle_for_job(kit_name, job)
    job["status"] = status
    job["current_stage"] = current_stage
    job["completed_steps"] = completed
    job["total_steps"] = total
    job["progress_percent"] = progress_percent if progress_percent is not None else (int((completed / total) * 100) if total else 0)
    job["logs"].append(log_line)
    job.setdefault("trace_events", []).append(
        {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "stage": current_stage,
            "completed_steps": completed,
            "total_steps": total,
            "progress_percent": job["progress_percent"],
            "log": log_line,
        }
    )
    save_job(kit_name, job)


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
    try:
        if scope.startswith("multi__"):
            selected = run_center_scope_keys(scope, cfg)
            if not selected:
                raise RuntimeError("No stages were selected for the real run.")
            if not all(item in {"ilo", "storage", "esxi"} for item in selected):
                raise RuntimeError("Real selected-stage execution currently supports iLO, storage, and ESXi only.")
            storage_was_handled_by_ilo = False
            if "ilo" in selected:
                run_ilo_real(cfg)
                finished_job = load_job(kit_name)
                if finished_job.get("status") == "Failed":
                    return
                cfg = load_kit_config(kit_name)
                promote_final_ilo_endpoint(cfg)
                save_kit_config(cfg)
                storage_was_handled_by_ilo = bool((finished_job.get("storage_run_directory") or "") or cfg.get("storage", {}).get("include_in_ilo_run"))
            if "storage" in selected and not storage_was_handled_by_ilo:
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
                    start_storage_manual_reboot_watch_background(cfg, discovery_raw_path, raid_plan_path, apply_paths)
                    return
            elif "storage" in selected and storage_was_handled_by_ilo:
                cfg = load_kit_config(kit_name)
                promote_final_ilo_endpoint(cfg)
                save_kit_config(cfg)
            if "esxi" in selected:
                cfg = load_kit_config(kit_name)
                promote_final_ilo_endpoint(cfg)
                save_kit_config(cfg)
                run_esxi_real(cfg, run_stamp=str((cfg.get("_runtime", {}) or {}).get("esxi_run_stamp") or "").strip() or None)
            return
        if scope == "ilo":
            run_ilo_real(cfg)
        elif scope == "storage":
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
                start_storage_manual_reboot_watch_background(cfg, discovery_raw_path, raid_plan_path, apply_paths)
        elif scope == "esxi":
            promote_final_ilo_endpoint(cfg)
            save_kit_config(cfg)
            run_esxi_real(cfg, run_stamp=str((cfg.get("_runtime", {}) or {}).get("esxi_run_stamp") or "").strip() or None)
        else:
            raise RuntimeError(f"Real execution is not wired for scope: {scope}")
    except Exception as e:
        save_job(
            kit_name,
            {
                "status": "Failed",
                "scope": scope,
                "current_stage": "Unexpected error",
                "progress_percent": 0,
                "completed_steps": 0,
                "total_steps": 0,
                "logs": [f"[FAILED] Unexpected background execution error: {e}"],
            },
        )
    finally:
        append_job_history_snapshot(cfg, scope)


def resolve_esxi_base_iso_path(cfg: dict) -> Path:
    version = normalize_esxi_version((cfg.get("esxi", {}) or {}).get("version"))
    configured = str((cfg.get("esxi", {}) or {}).get("base_iso_path") or "").strip()
    if configured:
        path = Path(configured)
        if path.exists() and path.suffix.lower() == ".iso":
            return path
        if path.exists():
            raise ValueError(f"Configured ESXi base ISO is not an .iso file: {path}")
        raise FileNotFoundError(f"Configured ESXi base ISO was not found: {path}")

    candidates = [Path(item["path"]) for item in discover_esxi_base_isos(version=version)]
    if not candidates:
        raise FileNotFoundError(f"No ESXi {version} base ISO was found under {BASE_DIR / 'media' / 'esxi' / 'base'}")
    return candidates[0]


def normalize_esxi_version(value: Any) -> str:
    text = str(value or "7").strip()
    if text in {"7", "8"}:
        return text
    raise ValueError(f"Unsupported ESXi version: {text or '(empty)'}")


def discover_esxi_base_isos(version: str | None = None) -> list[dict[str, Any]]:
    requested = normalize_esxi_version(version) if version else ""
    base_dir = BASE_DIR / "media" / "esxi" / "base"
    search_dirs: list[tuple[str, Path]] = [
        ("", base_dir),
        ("7", base_dir / "esxi7"),
        ("8", base_dir / "esxi8"),
    ]
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for discovered_version, directory in search_dirs:
        if requested and discovered_version and discovered_version != requested:
            continue
        for path in sorted(list(directory.glob("*.iso")) + list(directory.glob("*.ISO"))):
            resolved = str(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            inferred_version = discovered_version or infer_esxi_version_from_iso_path(path)
            if requested and inferred_version != requested:
                continue
            results.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "version": inferred_version,
                    "exists": path.exists(),
                    "readable": os.access(path, os.R_OK),
                }
            )
    return results


def infer_esxi_version_from_iso_path(path: Path) -> str:
    text = str(path).lower()
    if "esxi8" in text or "esxi-8" in text or "esxi_8" in text or "vmware-vmvisor-installer-8" in text:
        return "8"
    return "7"


def validate_esxi_base_iso(path: Path, version: str) -> None:
    normalize_esxi_version(version)
    if not path.exists():
        raise FileNotFoundError(f"Selected ESXi {version} base ISO was not found: {path}")
    if path.suffix.lower() != ".iso":
        raise ValueError(f"Selected ESXi {version} base ISO must be an .iso file: {path}")
    if not path.is_file():
        raise ValueError(f"Selected ESXi {version} base ISO is not a file: {path}")
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except Exception as exc:
        raise OSError(f"Selected ESXi {version} base ISO could not be read: {path}") from exc


def detect_public_base_url(target_host: str = "") -> str:
    configured = os.getenv("LAB_BUILDER_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured

    host = "127.0.0.1"
    probe_target = (target_host or "8.8.8.8").strip()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((probe_target, 443))
            host = sock.getsockname()[0] or host
    except Exception:
        pass

    port = os.getenv("LAB_BUILDER_PORT", "").strip() or os.getenv("PORT", "").strip() or "8000"
    return f"http://{host}:{port}"


def build_esxi_iso_url(cfg: dict, output_iso: Path, target_host: str = "") -> str:
    public_base_url = detect_public_base_url(target_host)
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    output_name = sanitize_kit_name(output_iso.stem)
    return f"{public_base_url}/esxi-built-iso/{quote(kit_name)}/{quote(output_name)}.iso"


def get_esxi_effective_values(cfg: dict[str, Any]) -> dict[str, Any]:
    esxi_cfg = cfg.get("esxi", {}) or {}
    try:
        version = normalize_esxi_version(esxi_cfg.get("version"))
    except ValueError:
        version = str(esxi_cfg.get("version") or "").strip()
    values = {
        "version": version,
        "base_iso_path": str(esxi_cfg.get("base_iso_path") or "").strip(),
        "hostname": str(esxi_cfg.get("hostname") or "").strip(),
        "management_ip": str(esxi_cfg.get("management_ip") or cfg.get("ip_plan", {}).get("esxi") or "").strip(),
        "subnet_mask": str(esxi_cfg.get("subnet_mask") or "").strip(),
        "gateway": str(esxi_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip(),
        "dns_servers": [
            x.strip()
            for x in (esxi_cfg.get("dns_servers") or cfg.get("shared_network", {}).get("dns_servers") or [])
            if x and str(x).strip()
        ],
        "root_password": str(esxi_cfg.get("root_password") or ""),
        "vlan_id": str(esxi_cfg.get("vlan_id") or "").strip(),
        "ntp_server": str(esxi_cfg.get("ntp_server") or "").strip(),
        "enable_ssh": bool(esxi_cfg.get("enable_ssh", True)),
        "disable_ipv6": bool(esxi_cfg.get("disable_ipv6", True)),
        "debug_no_reboot": bool(esxi_cfg.get("debug_no_reboot", False)),
    }
    missing: list[str] = []
    if not values["hostname"]:
        missing.append("hostname")
    if not values["management_ip"]:
        missing.append("management IP")
    if not values["subnet_mask"]:
        missing.append("subnet mask")
    if not values["gateway"]:
        missing.append("gateway")
    if not values["root_password"]:
        missing.append("root password")
    version_errors = []
    try:
        normalize_esxi_version(values["version"])
    except ValueError as exc:
        version_errors.append(str(exc))
    hostname_errors = [] if not values["hostname"] else validate_esxi_hostname(values["hostname"])
    password_check = build_esxi_password_policy_check(values["root_password"]) if values["root_password"] else {
        "valid": False,
        "errors": [],
        "notes": [],
        "class_count": 0,
        "length": 0,
    }
    values["missing_fields"] = missing
    values["hostname_valid"] = not hostname_errors
    values["hostname_errors"] = hostname_errors
    values["hostname_warnings"] = (
        ["If you later join this host to Active Directory, keep the short name under 15 characters to avoid NetBIOS name changes."]
        if values["hostname"] and len(values["hostname"].split(".", 1)[0]) >= 15
        else []
    )
    values["root_password_policy_valid"] = bool(password_check.get("valid"))
    values["root_password_errors"] = list(password_check.get("errors") or [])
    values["root_password_notes"] = list(password_check.get("notes") or [])
    values["root_password_class_count"] = int(password_check.get("class_count") or 0)
    values["root_password_length"] = int(password_check.get("length") or 0)
    values["validation_errors"] = list(version_errors) + list(hostname_errors) + list(password_check.get("errors") or [])
    values["validation_notes"] = list(values["hostname_warnings"]) + list(password_check.get("notes") or [])
    return values


def build_esxi_install_review(cfg: dict, *, run_stamp: str | None = None) -> dict[str, Any]:
    ilo_cfg = cfg.get("ilo", {}) or {}
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    login_ip = resolve_ilo_control_host(cfg)
    stamp = (run_stamp or datetime.now().strftime("%Y%m%d-%H%M%S")).strip()
    output_name = f"esxi-{stamp}"
    output_iso = EXPORTS_DIR / "esxi-isos" / kit_name / output_name / f"{output_name}.iso"
    values = get_esxi_effective_values(cfg)
    base_iso_path = resolve_esxi_base_iso_path(cfg)
    validate_esxi_base_iso(base_iso_path, values["version"])
    iso_url = build_esxi_iso_url(cfg, output_iso, login_ip)
    return {
        "run_stamp": stamp,
        "source_label": "Saved kit values from the ESXi Setup page and shared defaults",
        "manual_defaults_label": "Manual test script defaults are not used by Run Center",
        "version": values["version"],
        "base_iso_path": str(base_iso_path),
        "output_iso_path": str(output_iso),
        "virtual_media_url": iso_url,
        "hostname": values["hostname"],
        "management_ip": values["management_ip"],
        "subnet_mask": values["subnet_mask"],
        "gateway": values["gateway"],
        "dns_servers": values["dns_servers"],
        "root_password_saved": bool(values["root_password"]),
        "vlan_id": values["vlan_id"],
        "ntp_server": values["ntp_server"],
        "enable_ssh": values["enable_ssh"],
        "disable_ipv6": values["disable_ipv6"],
        "debug_no_reboot": values["debug_no_reboot"],
        "install_target": build_esxi_install_target_review(cfg),
        "missing_fields": list(values["missing_fields"]),
        "validation_errors": list(values["validation_errors"]),
        "validation_notes": list(values["validation_notes"]),
    }


def esxi_password_policy_valid(password: str) -> bool:
    return bool(build_esxi_password_policy_check(password).get("valid"))


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
    ilo_cfg = cfg.get("ilo", {}) or {}
    esxi_cfg = cfg.get("esxi", {}) or {}
    login_ip = resolve_ilo_control_host(cfg)
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
        save_job(kit_name, job)
        trace_payload["result"] = {
            "status": "Completed",
            "management_ready": ready_result,
        }
        save_esxi_trace(trace_path, trace_payload)
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
            details=[f"ISO: {output_iso}", f"Base ISO: {base_iso_path}"],
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
    ilo_cfg = cfg.get("ilo", {})
    snmp_cfg = cfg.get("shared_snmp", {})
    snmp_users = normalize_snmp_users(snmp_cfg.get("users", []))
    active_snmp_user = snmp_users[0] if snmp_users else {
        "username": str(snmp_cfg.get("v3_username") or "").strip(),
        "auth_protocol": str(snmp_cfg.get("v3_auth_protocol") or "SHA").strip() or "SHA",
        "auth_password": str(snmp_cfg.get("v3_auth_password") or ""),
        "priv_protocol": str(snmp_cfg.get("v3_priv_protocol") or "AES").strip() or "AES",
        "priv_password": str(snmp_cfg.get("v3_priv_password") or ""),
    }
    additional_ilo_users = normalize_ilo_additional_users(ilo_cfg.get("additional_users", []))
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
        "snmp_username": active_snmp_user.get("username", "") or "",
        "snmp_auth_protocol": desired_auth_protocol,
        "snmp_priv_protocol": desired_priv_protocol,
        "snmp_auth_secret_present": bool(active_snmp_user.get("auth_password")),
        "snmp_priv_secret_present": bool(active_snmp_user.get("priv_password")),
        "snmp_verified_checks": [],
        "snmp_mismatches": [],
        "snmp_reset_recommended": False,
        "snmp_profile_count": len(snmp_users),
        "local_account_status": "Not attempted",
        "local_accounts_requested": [item.get("username", "") for item in additional_ilo_users],
        "local_account_results": [],
        "storage_server_reboot_required": False,
        "storage_server_reboot_status": "Not required",
        "ilo_reset_required": False,
        "ilo_reset_status": "Not required",
        "ilo_stage_finished": False,
        "ilo_final_ip_verified": False,
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

    if not login_ip or not username or not password:
        update_job(kit_name, job, "Failed", "Validation failed", 0, total, "[FAILED] Missing iLO host, username, or password.")
        return

    def build_ilo_client(hostname: str) -> ILOClient:
        return ILOClient(ILOConfig(host=hostname, username=username, password=password, verify_tls=False, timeout=15))

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

    def build_snmp_readback_checks(network_protocol_doc: dict[str, Any]) -> list[dict[str, Any]]:
        snmp_block = network_protocol_doc.get("SNMP") or {}
        checks: list[dict[str, Any]] = []
        requested_username = str(active_snmp_user.get("username") or "").strip()
        if "ProtocolEnabled" in snmp_block:
            checks.append({
                "label": "protocol_enabled",
                "requested": True,
                "actual": snmp_block.get("ProtocolEnabled"),
                "matched": snmp_block.get("ProtocolEnabled") is True,
            })
        username_key = next(
            (
                key
                for key in ("UserName", "Username", "SNMPv3UserName", "SNMPv3Username")
                if key in snmp_block
            ),
            "",
        )
        if username_key and requested_username:
            checks.append({
                "label": "username",
                "requested": requested_username,
                "actual": str(snmp_block.get(username_key) or "").strip(),
                "matched": str(snmp_block.get(username_key) or "").strip() == requested_username,
            })
        auth_key = next(
            (key for key in ("AuthProtocol", "SNMPv3AuthProtocol") if key in snmp_block),
            "",
        )
        if auth_key and desired_auth_protocol:
            checks.append({
                "label": "auth_protocol",
                "requested": desired_auth_protocol,
                "actual": str(snmp_block.get(auth_key) or "").strip(),
                "matched": str(snmp_block.get(auth_key) or "").strip() == desired_auth_protocol,
            })
        priv_key = next(
            (key for key in ("PrivacyProtocol", "SNMPv3PrivacyProtocol") if key in snmp_block),
            "",
        )
        if priv_key and desired_priv_protocol:
            checks.append({
                "label": "privacy_protocol",
                "requested": desired_priv_protocol,
                "actual": str(snmp_block.get(priv_key) or "").strip(),
                "matched": str(snmp_block.get(priv_key) or "").strip() == desired_priv_protocol,
            })
        for legacy_key in (
            "SNMPv1Enabled",
            "EnableSNMPv1",
            "SNMPv1RequestsEnabled",
            "SNMPv1TrapEnabled",
            "SNMPv1GetEnabled",
            "SNMPv1SetEnabled",
            "SNMPv2Enabled",
            "EnableSNMPv2",
            "SNMPv2RequestsEnabled",
            "SNMPv2TrapEnabled",
            "SNMPv2cEnabled",
            "EnableSNMPv2c",
            "SNMPv2cRequestsEnabled",
            "SNMPv2cTrapEnabled",
            "CommunityAccessEnabled",
        ):
            if legacy_key in snmp_block:
                checks.append({
                    "label": legacy_key,
                    "requested": False,
                    "actual": snmp_block.get(legacy_key),
                    "matched": snmp_block.get(legacy_key) is False,
                })
        for v3_key in ("SNMPv3RequestsEnabled", "SNMPv3Enabled", "SNMPv3TrapEnabled"):
            if v3_key in snmp_block:
                checks.append({
                    "label": v3_key,
                    "requested": True,
                    "actual": snmp_block.get(v3_key),
                    "matched": snmp_block.get(v3_key) is True,
                })
        return checks

    def current_snmp_matches(network_protocol_doc: dict[str, Any]) -> bool:
        snmp_checks = build_snmp_readback_checks(network_protocol_doc)
        return bool(snmp_checks) and all(item.get("matched") for item in snmp_checks)

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

        result: dict[str, Any] = {
            "hostname_matched": True,
            "dns_matched": True,
            "snmp_matched": True,
            "errors": [],
        }

        try:
            _, network_protocol = client.get_network_protocol()
        except Exception as e:
            network_protocol = {}
            result["errors"].append(f"network_protocol={str(e).splitlines()[0]}")

        try:
            iface = client.get_active_manager_interface()
        except Exception as e:
            iface = {}
            result["errors"].append(f"active_interface={str(e).splitlines()[0]}")

        actual_hostname = str(
            network_protocol.get("HostName")
            or iface.get("HostName")
            or ""
        ).strip()
        hostname_expected = str(desired_hostname or "").strip()
        if hostname_expected:
            result["hostname_matched"] = actual_hostname == hostname_expected
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
                + f"expected={hostname_expected} actual={actual_hostname or '(empty)'}",
            )

        actual_dns = [
            item
            for item in (
                iface.get("StaticNameServers")
                or iface.get("NameServers")
                or []
            )
            if str(item or "").strip() and str(item).strip() not in {"0.0.0.0", "::"}
        ]
        requested_dns = [str(item).strip() for item in shared_dns if str(item).strip()]
        if requested_dns:
            result["dns_matched"] = actual_dns[: len(requested_dns)] == requested_dns
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
                + f"expected={requested_dns} actual={actual_dns}",
            )

        snmp_block = network_protocol.get("SNMP") or {}
        snmp_checks = build_snmp_readback_checks(network_protocol)
        if active_snmp_user.get("username"):
            result["snmp_matched"] = bool(snmp_checks) and all(item.get("matched") for item in snmp_checks)
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

        all_matched = result["hostname_matched"] and result["dns_matched"] and result["snmp_matched"] and not result["errors"]
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
        result["matched"] = all_matched
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
                f"snmp_v3_user={active_snmp_user.get('username', '') or '(none)'} | "
                f"auth={desired_auth_protocol} | priv={desired_priv_protocol} | "
                f"auth_password={'set' if active_snmp_user.get('auth_password') else 'missing'} | "
                f"priv_password={'set' if active_snmp_user.get('priv_password') else 'missing'} | "
                f"additional_ilo_users={len(additional_ilo_users)} | "
                f"snmp_profiles={len(snmp_users) or (1 if active_snmp_user.get('username') else 0)}"
            ),
        )
        if len(snmp_users) > 1:
            update_job(
                kit_name,
                job,
                "Running",
                "Validate configuration",
                0,
                total,
                "[INFO] Multiple SNMPv3 profiles are saved, but the current iLO Redfish path applies only the first profile.",
            )
        client = build_ilo_client(active_ip)
        current_network_protocol = {}
        current_active_interface = {}
        ip_change_applied = False
        hostname_change_applied = False
        dns_change_applied = False
        snmp_change_applied = False
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

        try:
            update_job(
                kit_name,
                job,
                "Running",
                "Disable IPv6",
                11,
                total,
                "[RUNNING] Attempting to disable IPv6 where supported"
            )
            ipv6_result = client.disable_ipv6_best_effort()
            update_job(
                kit_name,
                job,
                "Running",
                "Disable IPv6",
                11,
                total,
                f"[OK] IPv6 hardening via {ipv6_result.get('method')} at {ipv6_result.get('path')}"
            )
        except Exception as e:
            update_job(
                kit_name,
                job,
                "Running",
                "Disable IPv6",
                11,
                total,
                f"[SKIP/INFO] IPv6 hardening not applied: {e}"
            )

        try:
            if current_snmp_matches(current_network_protocol):
                job["snmp_apply_status"] = "Already correct"
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Harden SNMP",
                    12,
                    total,
                    "[OK] SNMP already correct; no change needed.",
                )
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
                        f"target={active_ip} | username={active_snmp_user.get('username', '') or '(none)'} | "
                        f"auth={desired_auth_protocol} | priv={desired_priv_protocol} | "
                        f"auth_secret={'Yes' if active_snmp_user.get('auth_password') else 'No'} | "
                        f"privacy_secret={'Yes' if active_snmp_user.get('priv_password') else 'No'}"
                    )
                )
                snmp_result = client.harden_snmp_best_effort(
                    v3_username=active_snmp_user.get("username", ""),
                    v3_auth_protocol=active_snmp_user.get("auth_protocol", "SHA"),
                    v3_auth_password=active_snmp_user.get("auth_password", ""),
                    v3_priv_protocol=active_snmp_user.get("priv_protocol", "AES"),
                    v3_priv_password=active_snmp_user.get("priv_password", ""),
                )
                snmp_change_applied = bool(snmp_result.get("changed"))
                current_network_protocol["SNMP"] = dict(snmp_result.get("after") or current_network_protocol.get("SNMP") or {})
                job["snmp_apply_status"] = str(snmp_result.get("status") or "Mismatch")
                job["snmp_applied_keys"] = list(snmp_result.get("applied_keys") or [])
                job["snmp_verified_checks"] = list(snmp_result.get("verification", {}).get("checks") or [])
                job["snmp_mismatches"] = list(snmp_result.get("mismatches") or [])
                job["snmp_reset_recommended"] = bool(snmp_result.get("reset_recommended"))
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
                        (
                            "[OK] SNMP verified after apply"
                            if snmp_matched
                            else "[WARN] SNMP settings partially matched after apply"
                        )
                        + " | "
                        + f"path={snmp_result.get('path', '(unknown)')} | "
                        + f"username={active_snmp_user.get('username', '') or '(none)'} | "
                        + f"active_ip={active_ip} | "
                        + f"auth={desired_auth_protocol} | priv={desired_priv_protocol} | "
                        + f"auth_secret={'Yes' if active_snmp_user.get('auth_password') else 'No'} | "
                        + f"privacy_secret={'Yes' if active_snmp_user.get('priv_password') else 'No'} | "
                        + f"checks={snmp_result.get('verification', {}).get('checks', [])} | "
                        + f"mismatches={snmp_result.get('mismatches', []) or '(none)'} | "
                        + f"reset_recommended={snmp_result.get('reset_recommended')} | "
                        + f"notes={snmp_result.get('notes', [])}"
                    )
                )
        except Exception as e:
            config_changes_succeeded = False
            job["snmp_apply_status"] = "Failed"
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
                    f"target={active_ip} | username={active_snmp_user.get('username', '') or '(none)'} | "
                    f"auth={desired_auth_protocol} | priv={desired_priv_protocol} | "
                    f"auth_secret={'Yes' if active_snmp_user.get('auth_password') else 'No'} | "
                    f"privacy_secret={'Yes' if active_snmp_user.get('priv_password') else 'No'} | "
                    f"error={e}"
                )
            )

        if additional_ilo_users:
            try:
                config_changes_attempted = True
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply local users",
                    13,
                    total,
                    f"[RUNNING] Ensuring additional local iLO users: {', '.join([item.get('username', '') for item in additional_ilo_users])}",
                )
                accounts_result = client.ensure_local_accounts_best_effort(additional_ilo_users)
                local_users_change_applied = any(item.get("changed") for item in accounts_result.get("results") or [])
                job["local_account_status"] = str(accounts_result.get("status") or "Mismatch")
                job["local_account_results"] = list(accounts_result.get("results") or [])
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
                        "[OK] Additional local iLO users verified"
                        if accounts_result.get("matched")
                        else "[WARN] Additional local iLO users did not fully verify"
                    )
                    + f" | path={accounts_result.get('path', '(unknown)')} | results={accounts_result.get('results', [])}",
                )
            except Exception as e:
                config_changes_succeeded = False
                job["local_account_status"] = "Failed"
                save_job(kit_name, job)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply local users",
                    13,
                    total,
                    f"[FAILED] Additional local iLO users could not be applied: {str(e).splitlines()[0]}",
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
                "[SKIP] No additional local iLO users were requested.",
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
            "snmp": "changed" if snmp_change_applied else ("already-correct" if active_snmp_user.get("username") else "not-requested"),
            "local_users": "changed" if local_users_change_applied else ("already-correct" if additional_ilo_users else "not-requested"),
        }
        job["ilo_change_summary"] = dict(change_summary)
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
                f"Local users={change_summary['local_users']}"
            ),
        )
        reset_recommended = ip_change_applied
        job["ilo_reset_reason"] = "iLO IP changed" if reset_recommended else "no reset-worthy iLO change was applied"
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

        if config_changes_attempted and reset_recommended:
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
    storage_discovery_summary = (storage_discovery or {}).get("summary", storage_discovery) if storage_discovery else None
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
    activity_feed = build_activity_feed(history)
    history_display = build_history_display_entries(history)
    dashboard_job_status = build_dashboard_job_status(history)
    hardware_identity = build_hardware_identity(cfg)
    ilo_input_review = build_ilo_input_review(cfg)
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
        "storage_discovery": storage_discovery,
        "storage_export_paths": storage_export_paths,
        "storage_plan": storage_plan,
        "storage_plan_paths": storage_plan_paths,
        "storage_apply_paths": storage_apply_paths,
        "storage_workflow_state": storage_workflow_state,
        "storage_review": storage_review,
        "storage_target": storage_target,
        "storage_credentials": storage_credentials,
        "storage_execution_status": storage_execution_status,
        "storage_planning_drives": storage_planning_drives,
        "storage_display_controller": storage_display_controller,
        "storage_controller_choices": storage_controller_choices,
        "storage_display_drives": storage_display_drives,
        "storage_plan_defaults": storage_plan_defaults,
        "workflow_contexts": workflow_contexts,
        "recommended_next_step": recommended_next_step,
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
        "report_center": report_center,
        "ilo_inclusion": ilo_inclusion,
        "esxi_inclusion": esxi_inclusion,
        "windows_inclusion": windows_inclusion,
        "qnap_inclusion": qnap_inclusion,
        "job": job,
        "history": history,
        "latest_ilo_history": latest_ilo_history,
        "section_states": summarize_section_states(cfg),
    }

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


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="dashboard")


@app.get("/execution", response_class=HTMLResponse)
async def execution_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="execution")


@app.get("/global-settings", response_class=HTMLResponse)
async def global_settings_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="global_settings")


@app.get("/ilo", response_class=HTMLResponse)
async def ilo_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="ilo")


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
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="storage")


@app.post("/save-storage-target", response_class=HTMLResponse)
async def save_storage_target(
    request: Request,
    return_page: str = Form("storage"),
    storage_target_host: str = Form(""),
    storage_username: str = Form(""),
    storage_password: str = Form(""),
    storage_target_mode: str = Form("override"),
):
    cfg = load_kit_config()
    storage_cfg = ensure_storage_config(cfg)
    if storage_target_mode == "defaults":
        storage_cfg["target_host_override"] = ""
        storage_cfg["username"] = ""
        storage_cfg["password"] = ""
    else:
        storage_cfg["target_host_override"] = storage_target_host.strip()
        storage_cfg["username"] = storage_username.strip()
        storage_cfg["password"] = storage_password
    refresh_storage_approval_from_saved_state(cfg)
    save_kit_config(cfg)
    target = resolve_storage_target_host(cfg)
    using_defaults = storage_target_mode == "defaults"
    append_activity_event(
        cfg["site"]["name"],
        "storage_target_saved",
        workflow="storage",
        summary=f"Storage review will use {target.get('resolved') or 'no resolved host yet'}.",
        target=target.get("resolved", ""),
        details=[
            f"Address source: {target.get('source') or 'Not resolved'}",
            f"Username source: {resolve_storage_target_credentials(cfg).get('username_source') or 'Not resolved'}",
        ],
    )
    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "Storage target updated",
            "Storage setup will now use the selected server address and sign-in details.",
            tone="ready",
            outcomes=[
                f"Server address: {target.get('resolved') or 'Not resolved yet'}",
                "Using iLO defaults." if using_defaults else "Using the entered address and sign-in details.",
                "Next step: Display current storage setup",
            ],
            links=[{"label": "Open storage setup", "href": "/storage"}],
        ),
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
    set_current_kit_name(selected_kit)
    cfg = load_kit_config(selected_kit)
    if str(return_page).strip().lower() == "kits":
        return_page = "dashboard"
    return render_page(request, cfg, active_page=return_page, message=f"Loaded kit: {selected_kit}")


@app.post("/new-kit", response_class=HTMLResponse)
async def new_kit_route(request: Request, new_kit_name: str = Form(...), return_page: str = Form("dashboard")):
    name = sanitize_kit_name(new_kit_name)
    cfg = default_config()
    cfg["site"]["name"] = name
    save_kit_config(cfg)
    save_job(name, {
        "status": "Idle",
        "scope": "",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": 0,
        "logs": [],
    })
    save_history(name, [])
    if str(return_page).strip().lower() == "kits":
        return_page = "dashboard"
    return render_page(request, cfg, active_page=return_page, message=f"Created new kit: {name}")


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
):
    existing_cfg = load_kit_config()
    form = await request.form()
    previous_subnet = existing_cfg.get("shared_network", {}).get("subnet", "")
    previous_plan = existing_cfg.get("ip_plan", {})
    submitted_plan = {
        "gateway": gateway_ip,
        "switch": switch_ip,
        "esxi": esxi_ip,
        "ilo": ilo_target_ip or ilo_ip,
        "windows": windows_ip,
        "qnap": qnap_ip,
        "iosafe": iosafe_ip,
    }
    if shared_subnet != previous_subnet:
        same_as_previous_plan = all(submitted_plan.get(key, "") == previous_plan.get(key, "") for key in DEFAULT_IP_OFFSETS)
        if same_as_previous_plan:
            submitted_plan = build_default_ip_plan(shared_subnet)

    resolved_ilo_target_ip = ilo_target_ip or ilo_ip
    resolved_ilo_current_ip = ilo_current_ip or resolved_ilo_target_ip
    cfg = {
        "site": {
            "name": sanitize_kit_name(site_name),
        },
        "shared_network": {
            "subnet": shared_subnet,
            "dns_servers": [dns1, dns2, dns3, dns4],
        },
        "ip_plan": {
            "gateway": submitted_plan["gateway"],
            "switch": submitted_plan["switch"],
            "esxi": submitted_plan["esxi"],
            "ilo": submitted_plan["ilo"],
            "windows": submitted_plan["windows"],
            "qnap": submitted_plan["qnap"],
            "iosafe": submitted_plan["iosafe"],
        },
        "shared_snmp": {
            "v3_username": snmp_v3_username,
            "v3_auth_protocol": snmp_v3_auth_protocol,
            "v3_auth_password": snmp_v3_auth_password,
            "v3_priv_protocol": snmp_v3_priv_protocol,
            "v3_priv_password": snmp_v3_priv_password,
            "users": extract_snmp_users_from_form(
                form,
                primary_username=snmp_v3_username,
                primary_auth_protocol=snmp_v3_auth_protocol,
                primary_auth_password=snmp_v3_auth_password,
                primary_priv_protocol=snmp_v3_priv_protocol,
                primary_priv_password=snmp_v3_priv_password,
            ),
        },
        "included": {
            "ilo": included_ilo == "on",
            "esxi": included_esxi == "on",
            "windows": included_windows == "on",
            "qnap": included_qnap == "on",
            "iosafe": included_iosafe == "on",
            "cisco_switch": included_cisco_switch == "on",
            "storage": included_storage == "on",
        },
        "section_completion": {
            "basics": section_basics_complete == "true",
            "network": section_network_complete == "true",
            "included": section_included_complete == "true",
            "credentials": section_credentials_complete == "true",
        },
        "ilo": {
            "host": resolved_ilo_current_ip,
            "current_ip": resolved_ilo_current_ip,
            "target_ip": resolved_ilo_target_ip,
            "subnet_mask": ilo_subnet_mask,
            "gateway": ilo_gateway,
            "dns_servers": [ilo_dns1, ilo_dns2, ilo_dns3, ilo_dns4],
            "hostname": normalize_ilo_hostname(ilo_hostname),
            "username": ilo_username,
            "password": ilo_password,
            "additional_users": extract_ilo_additional_users_from_form(form),
        },
        "esxi": {
            "hostname": esxi_hostname,
            "root_password": esxi_root_password,
        },
        "windows": {
            "vm_name": windows_vm_name,
            "admin_password": windows_admin_password,
        },
        "qnap": {
            "hostname": qnap_hostname,
            "username": qnap_username,
            "password": qnap_password,
        },
        "iosafe": {
            "hostname": iosafe_hostname,
            "username": iosafe_username,
            "password": iosafe_password,
        },
        "cisco_switch": {
            "hostname": cisco_switch_hostname,
            "username": cisco_switch_username,
            "password": cisco_switch_password,
        },
    }

    cfg = merge_defaults(cfg)
    cfg["storage"]["include_in_ilo_run"] = cfg.get("included", {}).get("storage", False)
    snmp_input_review = build_snmp_input_review(cfg)
    ilo_input_review = build_ilo_input_review(cfg)
    combined_errors = list(snmp_input_review["errors"]) + list(ilo_input_review["errors"])
    combined_notes = list(snmp_input_review["notes"]) + list(ilo_input_review["notes"])
    if combined_errors:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Kit needs attention",
                "Fix the iLO or SNMP saved values before saving this page.",
                tone="pending",
                outcomes=[
                    f"Kit: {cfg['site']['name']}",
                    f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                ],
                details=combined_errors + combined_notes,
            ),
        )

    try:
        cfg = apply_ip_plan(cfg)
        save_kit_config(cfg)
        append_activity_event(
            cfg["site"]["name"],
            "global_settings_saved",
            workflow="global_settings",
            summary="Shared defaults were updated for this kit.",
            target=cfg["site"]["name"],
            details=[
                f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                f"Gateway: {cfg['ip_plan'].get('gateway', '') or 'Not set'}",
            ],
        )
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Kit saved",
                f"Saved the kit and refreshed the shared address plan for {cfg['site']['name']}.",
                tone="ready",
                outcomes=[
                    f"Kit: {cfg['site']['name']}",
                    f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                ],
            ),
        )
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"Could not apply IP plan: {e}")


@app.post("/save-global-settings", response_class=HTMLResponse)
async def save_global_settings_route(
    request: Request,
    return_page: str = Form("global_settings"),
    site_name: str = Form(...),
    shared_subnet: str = Form(...),
    gateway_ip: str = Form(...),
    switch_ip: str = Form(...),
    esxi_ip: str = Form(...),
    ilo_target_ip: str = Form(...),
    windows_ip: str = Form(...),
    qnap_ip: str = Form(...),
    iosafe_ip: str = Form(...),
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
):
    cfg = load_kit_config()
    form = await request.form()
    cfg["site"]["name"] = sanitize_kit_name(site_name)
    cfg["shared_network"]["subnet"] = shared_subnet
    cfg["shared_network"]["dns_servers"] = [dns1, dns2, dns3, dns4]
    cfg["shared_snmp"] = {
        "v3_username": snmp_v3_username,
        "v3_auth_protocol": snmp_v3_auth_protocol,
        "v3_auth_password": snmp_v3_auth_password,
        "v3_priv_protocol": snmp_v3_priv_protocol,
        "v3_priv_password": snmp_v3_priv_password,
        "users": extract_snmp_users_from_form(
            form,
            primary_username=snmp_v3_username,
            primary_auth_protocol=snmp_v3_auth_protocol,
            primary_auth_password=snmp_v3_auth_password,
            primary_priv_protocol=snmp_v3_priv_protocol,
            primary_priv_password=snmp_v3_priv_password,
        ),
    }
    cfg["included"].update(
        {
            "ilo": included_ilo == "on",
            "esxi": included_esxi == "on",
            "windows": included_windows == "on",
            "qnap": included_qnap == "on",
            "iosafe": included_iosafe == "on",
            "cisco_switch": included_cisco_switch == "on",
            "storage": included_storage == "on",
        }
    )
    cfg["storage"]["include_in_ilo_run"] = cfg["included"]["storage"]
    snmp_input_review = build_snmp_input_review(cfg)
    if snmp_input_review["errors"]:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Shared defaults need attention",
                "Fix the SNMPv3 user or passwords before saving this page.",
                tone="pending",
                outcomes=[
                    f"Kit: {cfg['site'].get('name', '') or 'Unknown'}",
                    f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                ],
                details=list(snmp_input_review["errors"]) + list(snmp_input_review["notes"]),
            ),
        )
    cfg["ip_plan"].update(
        {
            "gateway": gateway_ip,
            "switch": switch_ip,
            "esxi": esxi_ip,
            "ilo": ilo_target_ip,
            "windows": windows_ip,
            "qnap": qnap_ip,
            "iosafe": iosafe_ip,
        }
    )
    cfg["ilo"]["target_ip"] = ilo_target_ip
    try:
        cfg = apply_ip_plan(cfg)
        save_kit_config(cfg)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Shared defaults saved",
                "Updated the global settings that feed the workflow pages.",
                tone="ready",
                outcomes=[
                    f"Kit: {cfg['site'].get('name', '') or 'Unknown'}",
                    f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                ],
                links=[{"label": "Review iLO", "href": "/ilo"}, {"label": "Review Storage / RAID", "href": "/storage"}],
            ),
        )
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"Could not save global settings: {e}")


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
):
    cfg = load_kit_config()
    form = await request.form()
    cfg["ilo"]["current_ip"] = ilo_current_ip.strip()
    cfg["ilo"]["host"] = cfg["ilo"]["current_ip"]
    if ilo_target_ip.strip():
        cfg["ilo"]["target_ip"] = ilo_target_ip.strip()
        cfg["ip_plan"]["ilo"] = ilo_target_ip.strip()
    cfg["ilo"]["gateway"] = (ilo_gateway.strip() or cfg.get("ip_plan", {}).get("gateway", "") or "").strip()
    normalized_hostname = normalize_ilo_hostname(ilo_hostname)
    cfg["ilo"]["hostname"] = normalized_hostname
    cfg["ilo"]["username"] = ilo_username
    cfg["ilo"]["password"] = ilo_password
    cfg["ilo"]["additional_users"] = extract_ilo_additional_users_from_form(form)
    cfg["included"]["ilo"] = True
    ilo_input_review = build_ilo_input_review(cfg)
    if ilo_input_review["errors"]:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            message=(f"Normalized iLO hostname to: {normalized_hostname}" if ilo_hostname.strip() and ilo_hostname.strip() != normalized_hostname else None),
            action_feedback=build_action_feedback(
                "iLO setup needs attention",
                "Fix the iLO user names or passwords before saving this page.",
                tone="pending",
                outcomes=[
                    f"Current iLO address: {cfg['ilo'].get('current_ip') or 'Not set'}",
                    f"Planned final IP: {cfg['ilo'].get('target_ip') or 'Unchanged'}",
                    f"Hostname: {normalized_hostname or 'Not set'}",
                ],
                details=list(ilo_input_review["errors"]) + list(ilo_input_review["notes"]),
            ),
        )
    try:
        cfg = apply_ip_plan(cfg)
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"Could not save iLO setup: {e}")
    save_kit_config(cfg)
    append_activity_event(
        cfg["site"]["name"],
        "ilo_settings_saved",
        workflow="ilo",
        summary="Saved the current iLO address and planned iLO settings.",
        target=cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or "",
        details=[
            f"Planned final IP: {cfg['ilo'].get('target_ip') or 'Unchanged'}",
            f"Gateway: {cfg['ilo'].get('gateway') or 'Not set'}",
            f"Hostname: {normalized_hostname or 'Not set'}",
        ],
    )
    return render_page(
        request,
        cfg,
        active_page=return_page,
        message=(f"Normalized iLO hostname to: {normalized_hostname}" if ilo_hostname.strip() and ilo_hostname.strip() != normalized_hostname else None),
        action_feedback=build_action_feedback(
            "iLO setup saved",
            "Updated the saved iLO target and local sign-in settings for this kit.",
            tone="ready",
        outcomes=[
            f"Target: {cfg['ilo'].get('current_ip') or cfg['ilo'].get('host', '') or 'Not set'}",
            f"Planned final IP: {cfg['ilo'].get('target_ip') or 'Unchanged'}",
            f"Gateway: {cfg['ilo'].get('gateway') or 'Not set'}",
        ],
        links=[{"label": "Open Storage setup", "href": "/storage"}, {"label": "Review run prep", "href": "/execution"}],
    ),
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
    included_esxi: str | None = Form(None),
):
    cfg = load_kit_config()
    cfg["esxi"]["version"] = normalize_esxi_version(esxi_version)
    cfg["esxi"]["base_iso_path"] = esxi_base_iso_path.strip()
    cfg["esxi"]["hostname"] = esxi_hostname
    cfg["esxi"]["root_password"] = esxi_root_password
    cfg["esxi"]["debug_no_reboot"] = esxi_debug_no_reboot == "on"
    if included_esxi is not None:
        cfg["included"]["esxi"] = included_esxi == "on"
    cfg = apply_ip_plan(cfg)
    effective_values = get_esxi_effective_values(cfg)
    if effective_values["validation_errors"]:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "ESXi setup needs attention",
                "Fix the ESXi server name or root password rules before saving this page.",
                tone="pending",
                outcomes=[
                    f"Server name: {effective_values.get('hostname') or 'Not set'}",
                f"Target: {effective_values.get('management_ip') or 'Not set'}",
                f"ESXi version: {effective_values.get('version') or '7'}",
                    f"Gateway: {effective_values.get('gateway') or 'Not set'}",
                    f"DNS: {', '.join(effective_values.get('dns_servers') or []) or 'Not set'}",
                ],
                details=list(effective_values["validation_errors"]) + list(effective_values["validation_notes"]),
                links=[{"label": "Open Run Center", "href": "/execution"}],
            ),
        )
    save_kit_config(cfg)
    append_activity_event(
        cfg["site"]["name"],
        "esxi_settings_saved",
        workflow="esxi",
        summary="Saved the ESXi setup values for this kit.",
        target=cfg["esxi"].get("management_ip") or cfg.get("ip_plan", {}).get("esxi", ""),
    )
    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "ESXi setup saved",
            "Updated the local ESXi setup values for this kit.",
            tone="ready",
            outcomes=[
                f"Hostname: {cfg['esxi'].get('hostname', '') or 'Not set'}",
                f"Target: {cfg['esxi'].get('management_ip', '') or cfg.get('ip_plan', {}).get('esxi', '') or 'Not set'}",
                f"ESXi version: {cfg['esxi'].get('version') or '7'}",
                f"Debug no reboot: {'Yes' if cfg['esxi'].get('debug_no_reboot') else 'No'}",
                f"Gateway: {effective_values.get('gateway') or 'Not set'}",
                f"DNS: {', '.join(effective_values.get('dns_servers') or []) or 'Not set'}",
                f"Root password saved: {'Yes' if effective_values.get('root_password') else 'No'}",
            ],
        ),
    )


@app.post("/save-windows-settings", response_class=HTMLResponse)
async def save_windows_settings_route(
    request: Request,
    return_page: str = Form("windows"),
    windows_vm_name: str = Form(""),
    windows_admin_password: str = Form(""),
    included_windows: str | None = Form(None),
):
    cfg = load_kit_config()
    cfg["windows"]["vm_name"] = windows_vm_name
    cfg["windows"]["admin_password"] = windows_admin_password
    cfg["included"]["windows"] = included_windows == "on"
    cfg = apply_ip_plan(cfg)
    save_kit_config(cfg)
    append_activity_event(
        cfg["site"]["name"],
        "windows_settings_saved",
        workflow="windows",
        summary="Saved the Windows setup values for this kit.",
        target=cfg["windows"].get("ip_address") or cfg.get("ip_plan", {}).get("windows", ""),
    )
    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "Windows setup saved",
            "Updated the local Windows setup values for this kit.",
            tone="ready",
            outcomes=[
                f"VM name: {cfg['windows'].get('vm_name', '') or 'Not set'}",
                f"Target: {cfg['windows'].get('ip_address', '') or cfg.get('ip_plan', {}).get('windows', '') or 'Not set'}",
            ],
        ),
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
    cfg = load_kit_config()
    cfg["qnap"]["hostname"] = qnap_hostname
    cfg["qnap"]["username"] = qnap_username
    cfg["qnap"]["password"] = qnap_password
    cfg["included"]["qnap"] = included_qnap == "on"
    cfg = apply_ip_plan(cfg)
    save_kit_config(cfg)
    append_activity_event(
        cfg["site"]["name"],
        "qnap_settings_saved",
        workflow="qnap",
        summary="Saved the QNAP setup values for this kit.",
        target=cfg["qnap"].get("ip") or cfg.get("ip_plan", {}).get("qnap", ""),
    )
    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "QNAP setup saved",
            "Updated the local QNAP setup values for this kit.",
            tone="ready",
            outcomes=[
                f"Hostname: {cfg['qnap'].get('hostname', '') or 'Not set'}",
                f"Target: {cfg['qnap'].get('ip', '') or cfg.get('ip_plan', {}).get('qnap', '') or 'Not set'}",
            ],
        ),
    )


@app.post("/autofill-ip-plan", response_class=HTMLResponse)
async def autofill_ip_plan(
    request: Request,
    return_page: str = Form("configuration"),
    shared_subnet: str = Form("10.10.8.0/24"),
):
    cfg = load_kit_config()
    try:
        cfg["shared_network"]["subnet"] = shared_subnet
        cfg["ip_plan"] = build_default_ip_plan(shared_subnet)
        cfg = apply_ip_plan(cfg)
        save_kit_config(cfg)
        return render_page(request, cfg, active_page=return_page, message="Default IP plan generated and applied.")
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"IP plan generation failed: {e}")


@app.post("/export-ilo-config", response_class=HTMLResponse)
async def export_ilo_config(request: Request, return_page: str = Form("configs")):
    cfg = load_kit_config()
    try:
        snapshot_path = export_ilo_config_snapshot(cfg)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            message=f"Exported iLO config snapshot to {snapshot_path}",
        )
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"iLO config export failed: {e}")


@app.post("/export-ilo-inventory", response_class=HTMLResponse)
async def export_ilo_inventory(request: Request, return_page: str = Form("configs")):
    cfg = load_kit_config()
    ilo_cfg = cfg.get("ilo", {})
    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    username = (ilo_cfg.get("username") or "").strip()
    password = ilo_cfg.get("password", "")

    if not host or not username or not password:
        error_text = "Current iLO config fetch failed: missing current iLO IP, username, or password."
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
        )

    try:
        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))
        inventory = client.get_current_config_snapshot()
        export_paths = export_ilo_inventory_snapshot(cfg, inventory)
        yaml_text = export_paths["summary"].read_text(encoding="utf-8")
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Current iLO inventory captured",
                "Read the live iLO state and saved a fresh summary and raw export.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    f"Saved under: {export_paths['summary'].parent}",
                ],
                links=[{"label": "Open artifacts page", "href": "/configs"}],
            ),
            config_view_title=f"Latest Live Summary: {export_paths['summary'].parent.name}",
            config_view_content=yaml_text,
        )
    except Exception as e:
        error_text = f"Current iLO config fetch failed: {str(e).splitlines()[0]}"
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
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
    cfg = load_kit_config()
    host = ad_hoc_ilo_host.strip()
    username = ad_hoc_ilo_username.strip()
    password = ad_hoc_ilo_password
    label = ad_hoc_ilo_label.strip()

    if not host or not username or not password:
        error_text = "Ad hoc iLO inventory export failed: missing iLO IP/hostname, username, or password."
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
        )

    try:
        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))
        inventory = client.get_current_config_snapshot()
        export_paths = export_ilo_inventory_snapshot(
            cfg,
            inventory,
            label=label,
            source_host=host,
        )

        saved_msg = ""
        if save_to_current_kit == "on":
            cfg["ilo"]["host"] = host
            cfg["ilo"]["current_ip"] = host
            cfg["ilo"]["username"] = username
            cfg["ilo"]["password"] = password
            save_kit_config(cfg)
            saved_msg = " Saved these connection values to the current kit."

        yaml_text = export_paths["summary"].read_text(encoding="utf-8")
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Ad hoc iLO inventory captured",
                "Read the live iLO state from the temporary target and saved fresh exports.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    f"Saved under: {export_paths['summary'].parent}",
                    saved_msg.strip() or "Current kit settings were left unchanged.",
                ],
                links=[{"label": "Open artifacts page", "href": "/configs"}],
            ),
            config_view_title=f"Latest Live Summary: {export_paths['summary'].parent.name}",
            config_view_content=yaml_text,
        )
    except Exception as e:
        error_text = f"Ad hoc iLO inventory export failed: {str(e).splitlines()[0]}"
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
        )


@app.post("/view-latest-live-summary", response_class=HTMLResponse)
async def view_latest_live_summary(request: Request, return_page: str = Form("configs")):
    cfg = load_kit_config()
    latest = latest_live_inventory_export()
    if not latest:
        error_text = f"No live inventory exports found under {ILO_LIVE_EXPORT_DIR}"
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
        )

    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "Latest live summary opened",
            "Showing the newest saved live inventory summary for this kit.",
            tone="ready",
            outcomes=[f"Source folder: {latest['directory']}"],
        ),
        config_view_title=f"Latest Live Summary: {latest['directory'].name}",
        config_view_content=latest["summary"].read_text(encoding="utf-8"),
    )


@app.post("/download-latest-live-summary")
async def download_latest_live_summary():
    latest = latest_live_inventory_export()
    if not latest:
        return HTMLResponse(f"No live inventory exports found under {ILO_LIVE_EXPORT_DIR}", status_code=404)
    return FileResponse(
        path=latest["summary"],
        filename=f"{latest['directory'].parent.name}-{latest['directory'].name}-summary.yml",
        media_type="application/x-yaml",
        headers=live_inventory_download_headers(latest),
    )


@app.post("/download-latest-live-raw")
async def download_latest_live_raw():
    latest = latest_live_inventory_export()
    if not latest:
        return HTMLResponse(f"No live inventory exports found under {ILO_LIVE_EXPORT_DIR}", status_code=404)
    return FileResponse(
        path=latest["raw"],
        filename=f"{latest['directory'].parent.name}-{latest['directory'].name}-raw.json",
        media_type="application/json",
        headers=live_inventory_download_headers(latest),
    )


@app.post("/read-current-storage", response_class=HTMLResponse)
async def read_current_storage(
    request: Request,
    return_page: str = Form("storage"),
):
    cfg = load_kit_config()
    storage_target = resolve_storage_target_host(cfg)
    storage_credentials = resolve_storage_target_credentials(cfg)
    host = storage_target.get("resolved", "")
    username = storage_credentials.get("username", "")
    password = storage_credentials.get("password", "")

    if not host or not username or not password:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage discovery failed: {storage_target.get('error') or storage_credentials.get('error') or 'missing current iLO IP, username, or password.'}",
        )

    try:
        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))
        discovery = client.get_storage_discovery(deep_smart_storage_scan=True)
        export_paths = export_storage_discovery_snapshot(cfg, discovery, host=host)
        update_storage_latest_state(cfg, discovery=discovery, discovery_paths=export_paths)
        save_kit_config(cfg)
        append_activity_event(
            cfg["site"]["name"],
            "storage_discovered",
            workflow="storage",
            state="discovered",
            summary="Read the current storage layout and saved a fresh discovery snapshot.",
            target=host,
            details=[f"Run folder: {export_paths['directory']}"],
        )
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Current storage setup loaded",
                "Read what is on the server and displayed the current storage setup.",
                tone="ready",
                outcomes=[
                    "The current storage layout is now ready to review.",
                    "Next step: Build storage plan",
                ],
                links=[
                    {"label": "Build storage plan", "href": "/storage#build-storage-plan"},
                    {"label": "Open reports", "href": "/configs"},
                ],
            ),
            storage_discovery=discovery.get("summary", {}),
            storage_export_paths=export_paths,
        )
    except Exception as e:
        error_text = f"Storage discovery failed: {str(e).splitlines()[0]}"
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
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
    cfg = load_kit_config()
    storage_target = resolve_storage_target_host(cfg)
    host = storage_target.get("resolved", "")
    if not host:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=f"RAID planning failed: {storage_target.get('error')}",
        )

    try:
        discovery, discovery_paths = load_storage_discovery_artifact(discovery_raw_path, expected_host=host)
        overrides = {
            "controller_path": controller_path,
            "os_controller_path": os_controller_path,
            "data_controller_path": data_controller_path,
            "os_drive_ids": os_drive_ids,
            "data_drive_ids": data_drive_ids,
            "hot_spare_drive_id": hot_spare_drive_id,
            "os_drive_paths": os_drive_paths,
            "data_drive_paths": data_drive_paths,
            "hot_spare_path": hot_spare_path,
            "os_bays": os_bays,
            "data_bays": data_bays,
            "hot_spare_bay": hot_spare_bay,
        }
        if os_raid_level is not None:
            overrides["os_raid_level"] = os_raid_level
        if data_raid_level is not None:
            overrides["data_raid_level"] = data_raid_level
        plan = build_raid_plan(discovery, discovery_paths, overrides=overrides)
        plan_paths = export_raid_plan_snapshot(cfg, plan, discovery_paths)
        update_storage_latest_state(cfg, discovery=discovery, discovery_paths=discovery_paths, plan=plan, plan_paths=plan_paths)
        save_kit_config(cfg)
        append_activity_event(
            cfg["site"]["name"],
            "storage_plan_built",
            workflow="storage",
            state="planned",
            summary="Built a proposed storage layout from the latest discovery snapshot.",
            target=host,
            details=[f"Plan saved to: {plan_paths['plan']}"],
        )
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Storage plan ready",
                "Built the new layout from the latest storage read.",
                tone="ready",
                outcomes=[
                    "This is still a preview. No storage changes were made.",
                    "Next step: Approve this plan",
                ],
                links=[
                    {"label": "Approve this plan", "href": "/storage#approve-storage-plan"},
                    {"label": "Open reports", "href": "/configs"},
                ],
            ),
            storage_discovery=discovery.get("summary", {}),
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
        )
    except Exception as e:
        error_text = f"RAID planning failed: {str(e).splitlines()[0]}"
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
        )


@app.post("/approve-storage-plan", response_class=HTMLResponse)
async def approve_storage_plan(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    include_in_ilo_run: str | None = Form(None),
):
    cfg = load_kit_config()
    storage_target = resolve_storage_target_host(cfg)
    host = storage_target.get("resolved", "")

    try:
        if not host:
            raise ValueError(storage_target.get("error"))
        discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        if not discovery or not discovery_paths:
            raise ValueError("A storage discovery artifact must be selected before approval.")
        if not plan or not plan_paths:
            raise ValueError("A RAID plan artifact must be selected before approval.")
        if not plan.get("valid", False):
            raise ValueError("Only a valid RAID plan can be approved for a later iLO run.")
        approve_storage_plan_for_cfg(
            cfg,
            discovery=discovery,
            discovery_paths=discovery_paths,
            plan=plan,
            plan_paths=plan_paths,
            include_in_ilo_run=include_in_ilo_run == "on",
        )
        cfg["included"]["storage"] = cfg["storage"]["include_in_ilo_run"]
        save_kit_config(cfg)
        append_activity_event(
            cfg["site"]["name"],
            "storage_plan_approved",
            workflow="storage",
            state="approved",
            summary="Approved the current storage plan for use in a later iLO run.",
            target=cfg["storage"]["approval"].get("host") or host,
            details=[
                f"Plan: {plan_paths['plan']}",
                f"Included in iLO run: {'Yes' if cfg['storage']['include_in_ilo_run'] else 'No'}",
            ],
        )
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Storage approved",
                "The current storage plan is approved for the real run.",
                tone="ready",
                outcomes=[
                    f"Apply it during the real run: {'Yes' if cfg['storage']['include_in_ilo_run'] else 'No'}",
                    "Next step: Run for real",
                ],
                links=[{"label": "Run for real", "href": "/execution"}],
            ),
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
        )
    except Exception as e:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage approval failed: {str(e).splitlines()[0]}",
        )


@app.post("/clear-storage-approval", response_class=HTMLResponse)
async def clear_storage_approval(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
):
    cfg = load_kit_config()
    storage_target = resolve_storage_target_host(cfg)
    host = storage_target.get("resolved", "")
    if not host:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage approval clear failed: {storage_target.get('error')}",
        )
    discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        expected_host=host,
    )
    clear_storage_approval_for_cfg(cfg)
    cfg["included"]["storage"] = False
    cfg["storage"]["include_in_ilo_run"] = False
    save_kit_config(cfg)
    append_activity_event(
        cfg["site"]["name"],
        "storage_plan_unapproved",
        workflow="storage",
        state="stale",
        summary="Removed approval from the current storage plan so it must be reviewed again.",
        target=host,
    )
    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "Approval removed",
            "This storage plan now needs review again before it can be used in a real run.",
            tone="ready",
            outcomes=["Next step: Review the plan and approve it again if it still looks right."],
        ),
        storage_discovery=discovery.get("summary", {}) if discovery else None,
        storage_export_paths=discovery_paths,
        storage_plan=plan,
        storage_plan_paths=plan_paths,
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
    cfg = load_kit_config()
    storage_target = resolve_storage_target_host(cfg)
    host = storage_target.get("resolved", "")

    try:
        if not host:
            raise ValueError(storage_target.get("error"))
        discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        if not plan_paths:
            raise ValueError("A RAID plan artifact must be selected before apply.")
        validate_storage_apply_request(
            plan,
            apply_mode,
            typed_confirmation,
            acknowledged=acknowledge_apply == "on",
        )
        apply_paths = initialize_storage_apply_artifacts(cfg, plan, plan_paths)
        initialize_background_job(cfg["site"]["name"], f"storage-apply:{apply_mode}")
        start_storage_apply_background(cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Storage apply started",
                f"Applying the approved storage plan in {apply_mode.replace('_', ' ')} mode.",
                tone="progress",
                status_label="Running",
                outcomes=[
                    f"Target: {host}",
                    f"Run folder: {apply_paths['directory']}",
                ],
                details=["Use the storage progress card and the live log below to follow each step."],
                links=[{"label": "Jump to storage progress", "href": "/storage#storage-progress-card"}],
            ),
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_apply_paths=apply_paths,
        )
    except Exception as e:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage apply failed: {str(e).splitlines()[0]}",
        )


@app.post("/reboot-storage-now", response_class=HTMLResponse)
async def reboot_storage_now(
    request: Request,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    apply_artifact_dir: str = Form(""),
):
    cfg = load_kit_config()
    storage_target = resolve_storage_target_host(cfg)
    host = storage_target.get("resolved", "")
    discovery = None
    discovery_paths = None
    plan = None
    plan_paths = None
    apply_paths = None

    try:
        if not host:
            raise ValueError(storage_target.get("error"))
        discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        if not apply_artifact_dir:
            raise ValueError("A storage apply run folder is required before reboot can be requested.")
        apply_paths = storage_apply_paths_from_directory(apply_artifact_dir)
        workflow_state = load_storage_workflow_state(apply_paths) or {}
        apply_state = workflow_state.get("apply", {}) or {}
        reboot_state = workflow_state.get("reboot", {}) or {}
        if apply_state.get("status") not in {"Completed", "Staged"}:
            raise ValueError("Reboot Now is only available after a completed storage apply run.")
        if not apply_state.get("reboot_required"):
            raise ValueError("Reboot Now is not available because the current storage run does not require reboot.")
        if reboot_state.get("status") == "Running":
            raise ValueError("A storage reboot workflow is already running for this storage run.")
        initialize_background_job(cfg["site"]["name"], "storage-reboot")
        start_storage_reboot_background(cfg, discovery_raw_path, raid_plan_path, apply_paths)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Restart requested",
                "Requested the server restart so the staged storage changes can continue.",
                tone="progress",
                status_label="Running",
                outcomes=[f"Run folder: {apply_paths['directory']}"],
                details=["The storage progress card will now track restart and post-reboot validation."],
                links=[{"label": "Jump to storage progress", "href": "/storage#storage-progress-card"}],
            ),
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_apply_paths=apply_paths,
        )
    except Exception as e:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage reboot failed: {str(e).splitlines()[0]}",
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_apply_paths=apply_paths,
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
    cfg = load_kit_config()
    ilo_cfg = cfg.get("ilo", {})
    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()

    try:
        discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        apply_paths = storage_apply_paths_from_directory(apply_artifact_dir) if apply_artifact_dir else None
        selected_artifact_path, viewer_title = storage_artifact_target(
            artifact_kind,
            discovery_paths,
            plan_paths,
            artifact_path_text=artifact_path,
            artifact_title=artifact_title,
        )
        viewer_content = selected_artifact_path.read_text(encoding="utf-8")
        if selected_artifact_path.suffix.lower() == ".json":
            viewer_content = json.dumps(json.loads(viewer_content), indent=2, sort_keys=False)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            message=f"Viewing storage artifact {selected_artifact_path}",
            config_view_title=viewer_title,
            config_view_content=viewer_content,
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_apply_paths=apply_paths,
        )
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"Storage artifact view failed: {str(e).splitlines()[0]}")


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
    del return_page
    del artifact_title
    del apply_artifact_dir
    cfg = load_kit_config()
    ilo_cfg = cfg.get("ilo", {})
    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()

    discovery, discovery_paths, plan, plan_paths = restore_storage_page_state(
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        expected_host=host,
    )
    del discovery, plan
    selected_artifact_path, _ = storage_artifact_target(
        artifact_kind,
        discovery_paths,
        plan_paths,
        artifact_path_text=artifact_path,
    )
    media_type = "application/json" if selected_artifact_path.suffix.lower() == ".json" else "text/yaml; charset=utf-8"
    return FileResponse(path=selected_artifact_path, filename=selected_artifact_path.name, media_type=media_type)


@app.post("/view-current-kit-config", response_class=HTMLResponse)
async def view_current_kit_config(request: Request, return_page: str = Form("configs")):
    cfg = load_kit_config()
    try:
        snapshot_path = export_current_kit_config_snapshot(cfg)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            message=f"Generated current kit config snapshot at {snapshot_path}",
            config_view_title=f"Current Kit Config: {snapshot_path.name}",
            config_view_content=snapshot_path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"Current kit config view failed: {e}")


@app.post("/download-current-kit-config")
async def download_current_kit_config():
    cfg = load_kit_config()
    snapshot_path = export_current_kit_config_snapshot(cfg)
    return FileResponse(path=snapshot_path, filename=snapshot_path.name, media_type="application/x-yaml")


@app.post("/import-kit-config", response_class=HTMLResponse)
async def import_kit_config(
    request: Request,
    return_page: str = Form("configs"),
    import_file: UploadFile = File(...),
):
    current_cfg = load_kit_config()
    try:
        raw = await import_file.read()
        if not raw:
            raise ValueError("The uploaded file was empty.")
        imported = yaml.safe_load(raw.decode("utf-8")) or {}
        if not isinstance(imported, dict):
            raise ValueError("The uploaded file must contain a YAML or JSON object.")
        imported = merge_defaults(imported)
        imported_name = sanitize_kit_name(imported.get("site", {}).get("name", "") or current_cfg.get("site", {}).get("name", "Kit-01"))
        imported.setdefault("site", {})["name"] = imported_name
        save_kit_config(imported)
        imported_snapshot = current_build_output_dir(imported) / f"imported-config-{time.strftime('%Y%m%d-%H%M%S')}.yml"
        imported_snapshot.write_text(yaml.safe_dump(imported, sort_keys=False), encoding="utf-8")
        cfg = load_kit_config(imported_name)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Config imported",
                "Loaded the uploaded config into the app and switched the current kit to it.",
                tone="ready",
                status_label="Imported",
                outcomes=[
                    f"Current kit: {imported_name}",
                    f"Build folder: {current_build_output_dir(cfg)}",
                ],
                links=[
                    {"label": "Open Global Settings", "href": "/global-settings"},
                    {"label": "Open Run Center", "href": "/execution"},
                ],
            ),
        )
    except Exception as e:
        return render_page(request, current_cfg, active_page=return_page, error_message=f"Config import failed: {str(e).splitlines()[0]}")


@app.post("/view-ilo-config-snapshot", response_class=HTMLResponse)
async def view_ilo_config_snapshot(request: Request, return_page: str = Form("configs")):
    cfg = load_kit_config()
    try:
        snapshot_path = export_ilo_config_snapshot(cfg)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            message=f"Generated iLO config snapshot at {snapshot_path}",
            config_view_title=f"iLO Config Snapshot: {snapshot_path.name}",
            config_view_content=snapshot_path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"iLO config snapshot view failed: {e}")


@app.post("/download-ilo-config-snapshot")
async def download_ilo_config_snapshot():
    cfg = load_kit_config()
    snapshot_path = export_ilo_config_snapshot(cfg)
    return FileResponse(path=snapshot_path, filename=snapshot_path.name, media_type="application/x-yaml")


@app.post("/view-report", response_class=HTMLResponse)
async def view_report(
    request: Request,
    return_page: str = Form("configs"),
    report_path: str = Form(...),
):
    cfg = load_kit_config()
    try:
        path = safe_report_path(report_path)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Report opened",
                "Showing the selected saved report.",
                tone="ready",
                outcomes=[f"Source: {path}"],
            ),
            config_view_title=f"Report: {path.name}",
            config_view_content=path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"Report view failed: {str(e).splitlines()[0]}")


@app.post("/download-report")
async def download_report(report_path: str = Form(...)):
    path = safe_report_path(report_path)
    media_type = "application/json" if path.suffix.lower() == ".json" else "text/yaml; charset=utf-8"
    return FileResponse(path=path, filename=path.name, media_type=media_type)


@app.post("/view-run-summary", response_class=HTMLResponse)
async def view_run_summary(
    request: Request,
    scope: str = Form(...),
    return_page: str = Form("execution"),
):
    cfg = load_kit_config()
    summary = build_run_summary(cfg, scope)
    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "Run summary ready",
            "Built a concise review of the selected run that you can print or export.",
            tone="ready",
            outcomes=[
                f"Scope: {scope}",
                f"Target server: {summary.get('target_server') or 'Not set'}",
                f"Included stages: {', '.join(summary.get('final_summary', {}).get('will_run', []))}",
            ],
        ),
        config_view_title=f"Run Summary: {scope}",
        config_view_content=yaml.safe_dump(summary, sort_keys=False),
        execution_preview=build_execution_review(cfg, scope).get("detail_text"),
        execution_review=build_execution_review(cfg, scope),
        confirm_scope=scope,
    )


@app.post("/download-run-summary")
async def download_run_summary(scope: str = Form(...)):
    cfg = load_kit_config()
    path = write_run_summary_artifact(cfg, scope)
    return FileResponse(path=path, filename=path.name, media_type="application/x-yaml")


@app.get("/debug-bundles/latest")
async def download_latest_debug_bundle():
    path = DEBUG_BUNDLES_DIR / "latest-failure.txt"
    if not path.exists():
        return HTMLResponse("No debug bundle has been generated yet.", status_code=404)
    return FileResponse(path=path, filename="latest-failure.txt", media_type="text/plain")


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
    path = resolve_built_esxi_iso_path(kit_name, output_name)
    if not path.exists():
        return HTMLResponse(f"Built ESXi ISO not found: {path}", status_code=404)
    append_esxi_iso_access_log(path, request)
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


@app.post("/prepare-execute", response_class=HTMLResponse)
async def prepare_execute(
    request: Request,
    scope: str = Form("included"),
    selected_scopes: list[str] = Form([]),
    return_page: str = Form("execution"),
):
    cfg = load_kit_config()
    scope = normalize_run_center_scope(scope, selected_scopes)
    preview_error = None
    try:
        validate_execution_scope(cfg, scope)
    except Exception as e:
        preview_error = str(e).splitlines()[0]
    review = build_execution_review(cfg, scope)
    return render_page(
        request,
        cfg,
        active_page=return_page,
        execution_preview=review.get("detail_text"),
        execution_review=review,
        confirm_scope=scope,
        error_message=preview_error,
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
    cfg = load_kit_config()
    scope = normalize_run_center_scope(scope, selected_scopes)
    launch_options = build_execution_launch_options(cfg, scope)
    real_launch = launch_options.get("real")
    if not real_launch:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message="Execution blocked: a real run is not available for the selected stages.",
            execution_preview=build_execution_review(cfg, scope).get("detail_text"),
            execution_review=build_execution_review(cfg, scope),
            confirm_scope=scope,
        )
    scope = str(real_launch.get("scope") or scope)
    runtime = dict(cfg.get("_runtime", {}) or {})
    if esxi_run_stamp.strip():
        runtime["esxi_run_stamp"] = esxi_run_stamp.strip()
    if runtime:
        cfg["_runtime"] = runtime
    try:
        validate_execution_scope(cfg, scope)
    except Exception as e:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=f"Execution blocked: {str(e).splitlines()[0]}",
            execution_preview=build_execution_review(cfg, scope).get("detail_text"),
            execution_review=build_execution_review(cfg, scope),
            confirm_scope=scope,
        )

    if confirm_checkbox != "on":
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message="Execution blocked: you must check the confirmation box.",
            execution_preview=build_execution_review(cfg, scope).get("detail_text"),
            execution_review=build_execution_review(cfg, scope),
            confirm_scope=scope,
        )

    if confirm_phrase.strip().upper() != "EXECUTE":
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message='Execution blocked: confirmation phrase must be exactly EXECUTE.',
            execution_preview=build_execution_review(cfg, scope).get("detail_text"),
            execution_review=build_execution_review(cfg, scope),
            confirm_scope=scope,
        )

    initialize_background_job(cfg["site"]["name"], scope)
    threading.Thread(
        target=execute_real_job_in_background,
        args=(cfg, scope),
        daemon=True,
    ).start()

    msg = "Execution started."
    if scope == "ilo":
        msg = "Real iLO automation started in the background. Check Job Monitor for live progress and logs."
    elif scope == "storage":
        msg = "Real storage automation started in the background. Check Job Monitor for live progress and logs."
    elif scope == "esxi":
        msg = "Real ESXi automation started in the background. Check Job Monitor for live progress and logs."
    elif scope.startswith("multi__"):
        msg = "Real selected-stage automation started in the background. Check Job Monitor for live progress and logs."
    else:
        msg = f"Preview started for scope: {scope}. No real changes will be made."

    return render_page(request, cfg, active_page=return_page, message=msg)


@app.post("/execute-preview", response_class=HTMLResponse)
async def execute_preview_scope(
    request: Request,
    scope: str = Form("included"),
    selected_scopes: list[str] = Form([]),
    return_page: str = Form("execution"),
):
    cfg = load_kit_config()
    scope = normalize_run_center_scope(scope, selected_scopes)
    try:
        validate_execution_scope(cfg, scope)
    except Exception as e:
        review = build_execution_review(cfg, scope)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message=f"Preview blocked: {str(e).splitlines()[0]}",
            execution_preview=review.get("detail_text"),
            execution_review=review,
            confirm_scope=scope,
        )

    save_job(
        cfg["site"]["name"],
        {
            "status": "Preview queued",
            "execution_mode": "preview",
            "execution_mode_label": "Preview / safety mode",
            "scope": scope,
            "current_stage": "Queued",
            "progress_percent": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "logs": [f"[QUEUED] Preview / safety mode requested for scope: {scope}"],
        },
    )
    threading.Thread(
        target=execute_preview_job_in_background,
        args=(cfg, scope),
        daemon=True,
    ).start()

    return render_page(
        request,
        cfg,
        active_page=return_page,
        message=f"Preview started for scope: {scope}. No real changes will be made.",
    )
