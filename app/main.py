from pathlib import Path
import asyncio
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
    "kits": {
        "title": "Kits",
        "subtitle": "Load existing kits or create a new kit.",
    },
    "history": {
        "title": "History",
        "subtitle": "Review recent execution runs for the active kit.",
    },
}

STORAGE_APPROVAL_CONFIRM = "APPROVE STORAGE"
RUN_CENTER_STAGE_KEYS = ["ilo", "storage", "esxi", "windows", "qnap", "iosafe", "cisco_switch"]


def sanitize_kit_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Kit-01"
    name = re.sub(r"[^\w\- ]+", "", name)
    name = name.replace(" ", "-")
    return name or "Kit-01"


def normalize_page_name(name: str | None) -> str:
    page = (name or "dashboard").strip().lower()
    return page if page in PAGE_META else "dashboard"


def kit_path(kit_name: str) -> Path:
    return KITS_DIR / f"{sanitize_kit_name(kit_name)}.yml"


def list_kits():
    return sorted([p.stem for p in KITS_DIR.glob("*.yml")])


def normalize_run_center_scope(scope: str | None, selected_scopes: list[str] | None = None) -> str:
    normalized_scope = str(scope or "included").strip().lower() or "included"
    picks: list[str] = []
    for item in selected_scopes or []:
        clean = str(item or "").strip().lower()
        if not clean:
            continue
        if clean == "included":
            return "included"
        if clean in RUN_CENTER_STAGE_KEYS and clean not in picks:
            picks.append(clean)
    if picks:
        if len(picks) == 1:
            return picks[0]
        return "multi__" + "__".join(picks)
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
        return CURRENT_KIT_FILE.read_text(encoding="utf-8").strip()
    kits = list_kits()
    return kits[0] if kits else "Kit-01"


def set_current_kit_name(name: str):
    CURRENT_KIT_FILE.write_text(sanitize_kit_name(name), encoding="utf-8")


def load_kit_config(kit_name: str | None = None):
    name = sanitize_kit_name(kit_name or get_current_kit_name())
    path = kit_path(name)
    if path.exists():
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
                "base_iso_path": str(job.get("esxi_base_iso_path") or ""),
                "built_iso_path": str(job.get("esxi_iso_path") or ""),
                "virtual_media_url": str(job.get("esxi_iso_url") or ""),
                "trace_path": str(job.get("esxi_trace_path") or ""),
                "builder_summary_path": str(job.get("esxi_builder_summary_path") or ""),
            },
            "builder_generation": dict(job.get("esxi_builder_generation") or {}),
            "builder_self_check": dict(job.get("esxi_builder_self_check") or {}),
            "virtual_media": dict(job.get("esxi_virtual_media") or {}),
            "boot_override": dict(job.get("esxi_boot_override") or {}),
            "boot_evidence": dict(job.get("esxi_boot_evidence") or {}),
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
                return yaml.safe_load(f) or {}
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


def save_job(kit_name: str, job: dict):
    ensure_run_bundle_for_job(kit_name, job)
    job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = job_path(kit_name)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(job, f, sort_keys=False)
    os.replace(tmp_path, path)
    write_run_bundle_files(kit_name, job)

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
    with open(history_path(kit_name), "w", encoding="utf-8") as f:
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


def build_live_inventory_status(
    state: str,
    title: str,
    details: list[str] | None = None,
    busy: bool = False,
) -> dict:
    if state == "Complete":
        class_name = "ready"
    elif state == "Failed":
        class_name = "pending"
    else:
        class_name = "progress"

    return {
        "state": state,
        "title": title,
        "details": details or [],
        "busy": busy,
        "class_name": class_name,
    }


def live_inventory_success_status(title: str, export_paths: dict[str, Path], host: str = "", label: str = "") -> dict:
    metadata = live_inventory_export_metadata(export_paths)
    details = [
        f"Export path: {Path(metadata['summary_path']).parent if metadata['summary_path'] else 'not set'}",
        f"Summary file: {metadata['summary_path']}",
        f"Raw file: {metadata['raw_path']}",
        f"Label: {label or metadata['label'] or 'not set'}",
        f"Host: {host or metadata['host'] or 'not set'}",
    ]
    return build_live_inventory_status("Complete", title, details)


def live_inventory_failure_status(title: str, error: Exception | str) -> dict:
    message = str(error).strip() or "The Live Inventory action failed."
    message = message.splitlines()[0]
    return build_live_inventory_status("Failed", title, [message])


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
        ("planned iLO target IP", str(ilo_cfg.get("target_ip") or cfg.get("ip_plan", {}).get("ilo") or "").strip()),
        ("current kit iLO IP", str(ilo_cfg.get("current_ip") or "").strip()),
        ("current kit iLO host", str(ilo_cfg.get("host") or "").strip()),
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
        "summary": "RAID/storage will not be configured during the iLO run unless you go to the Storage / RAID page, read current storage, plan the layout, and approve it.",
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


PAGE_RUNBOOKS = {
    "dashboard": {
        "title": "Command center",
        "what": "This page shows what is ready, what is stale, what is currently running, and the best next step.",
        "before": "Set shared defaults first, then fill in the workflow pages that matter for this kit.",
        "when_run": "Use this page to navigate. The real work happens on the workflow pages and in Run Center.",
        "restart": "Restarts may be needed later if storage or iLO changes are included.",
        "reports": "Recent activity, run history, and saved reports are available from the Dashboard, Run History, and Artifacts & Reports pages.",
    },
    "global_settings": {
        "title": "Shared defaults",
        "what": "This page sets the shared network, DNS, SNMP, and inclusion defaults used across the app.",
        "before": "Set the kit name, shared subnet, and default addresses before tuning workflow pages.",
        "when_run": "Saving here updates the shared defaults that other pages can inherit.",
        "restart": "Saving defaults does not trigger a restart by itself.",
        "reports": "The saved kit config and exported snapshots are available from Artifacts & Reports.",
    },
    "ilo": {
        "title": "iLO setup",
        "what": "This page controls the iLO target, sign-in details, and the settings that will be applied during an iLO run.",
        "before": "Make sure the current iLO address and credentials are known before preparing the run.",
        "when_run": "A real iLO run can change network settings, hostname, DNS, SNMP, and may request an iLO reset.",
        "restart": "An iLO reset may happen as part of the run. Storage work may also require a server restart if included.",
        "reports": "Current snapshots, run reviews, and resulting history entries are available from Artifacts & Reports and Run History.",
    },
    "storage": {
        "title": "Storage planning",
        "what": "This page reads current storage, builds a proposed layout, and lets you approve the exact plan for a later iLO run.",
        "before": "Set the current iLO address and credentials first, then read the current storage before planning.",
        "when_run": "Read and plan steps are safe. Storage apply and restart actions are destructive and use the approved plan exactly as saved.",
        "restart": "A restart is often needed after staged storage changes.",
        "reports": "Discovery exports, plan files, apply logs, and post-change results are saved under storage-raid exports and linked from this page.",
    },
    "esxi": {
        "title": "ESXi prep",
        "what": "This page stores the ESXi-specific hostname and credentials and compares them with the shared network defaults.",
        "before": "Set the shared IP plan first so the ESXi target inherits the right address and gateway.",
        "when_run": "The ESXi workflow uses the saved setup here plus generated install inputs like KS.CFG.",
        "restart": "A real ESXi build can reboot the target.",
        "reports": "Saved config exports and run history entries are available from Artifacts & Reports and Run History.",
    },
    "windows": {
        "title": "Windows prep",
        "what": "This page stores the Windows VM name, password, and inherited network target.",
        "before": "Set the shared network plan first so the Windows target address is correct.",
        "when_run": "The Windows workflow uses the saved VM and credential settings from this page.",
        "restart": "A real Windows deployment may reboot as part of installation.",
        "reports": "Saved config exports and run history entries are available from Artifacts & Reports and Run History.",
    },
    "qnap": {
        "title": "QNAP prep",
        "what": "This page stores the QNAP hostname and credentials along with the inherited target address.",
        "before": "Set the shared IP plan first so the target address is correct.",
        "when_run": "The QNAP workflow uses the saved local settings from this page.",
        "restart": "A real QNAP setup may require a restart depending on the changes applied.",
        "reports": "Saved config exports and run history entries are available from Artifacts & Reports and Run History.",
    },
    "execution": {
        "title": "Run center",
        "what": "This page is the final review and launch point for full-kit runs and single-stage runs.",
        "before": "Make sure targets, credentials, and any required approved plans are ready before starting.",
        "when_run": "This page shows the run review, validation checks, live progress, and the resulting history.",
        "restart": "Restarts may happen if storage or iLO changes are included.",
        "reports": "Detailed logs live in Run History and Artifacts & Reports.",
    },
    "configs": {
        "title": "Artifacts and reports",
        "what": "This page is the report center for saved snapshots, configs, and exported run artifacts.",
        "before": "Run captures or setup actions first so there is something to browse.",
        "when_run": "Use this page to view, search, open, and download reports without changing the kit state.",
        "restart": "Viewing reports never requires a restart.",
        "reports": "All exported snapshots and run artifacts are indexed here.",
    },
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


def build_page_comparisons(cfg: dict[str, Any], workflow_contexts: dict[str, dict[str, Any]], history: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    latest_ilo = latest_history_entry_for_scope(history, ["ilo"]) or {}
    latest_storage = latest_history_entry_for_scope(history, ["storage-apply", "storage-reboot"]) or {}
    latest_ilo_cfg = latest_ilo.get("config_summary", {}) or {}
    ilo_result_parts = [latest_ilo.get("status") or "No iLO run recorded yet."]
    if latest_ilo_cfg.get("dns_apply_status"):
        ilo_result_parts.append(f"DNS {latest_ilo_cfg.get('dns_apply_status')}")
    if latest_ilo_cfg.get("snmp_apply_status"):
        ilo_result_parts.append(f"SNMP {latest_ilo_cfg.get('snmp_apply_status')}")
    if latest_ilo_cfg.get("ilo_reset_status"):
        ilo_result_parts.append(f"iLO reset {latest_ilo_cfg.get('ilo_reset_status')}")
    if latest_ilo_cfg.get("storage_server_reboot_status"):
        ilo_result_parts.append(f"Storage reboot {latest_ilo_cfg.get('storage_server_reboot_status')}")
    return {
        "ilo": [
            {"label": "Current", "value": (cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host") or "Not set")},
            {"label": "Planned", "value": f"IP {cfg.get('ilo', {}).get('target_ip') or 'Unchanged'} | gateway {cfg.get('ilo', {}).get('gateway') or 'Unchanged'} | hostname {cfg.get('ilo', {}).get('hostname') or 'Unchanged'}"},
            {"label": "Approved", "value": "iLO uses the saved settings on this page directly."},
            {"label": "Result", "value": " | ".join(ilo_result_parts)},
        ],
        "storage": [
            {"label": "Current", "value": workflow_contexts["storage"]["current_summary"]},
            {"label": "Planned", "value": workflow_contexts["storage"]["planned_summary"]},
            {"label": "Approved", "value": workflow_contexts["storage"]["approved_summary"]},
            {"label": "Result", "value": latest_storage.get("status") or "No storage run recorded yet."},
        ],
        "esxi": [
            {"label": "Current", "value": workflow_contexts["esxi"]["target"]},
            {"label": "Planned", "value": workflow_contexts["esxi"]["planned_summary"]},
            {"label": "Approved", "value": workflow_contexts["esxi"]["approved_summary"]},
            {"label": "Result", "value": workflow_contexts["esxi"]["result_summary"]},
        ],
        "windows": [
            {"label": "Current", "value": workflow_contexts["windows"]["target"]},
            {"label": "Planned", "value": workflow_contexts["windows"]["planned_summary"]},
            {"label": "Approved", "value": workflow_contexts["windows"]["approved_summary"]},
            {"label": "Result", "value": workflow_contexts["windows"]["result_summary"]},
        ],
        "qnap": [
            {"label": "Current", "value": workflow_contexts["qnap"]["target"]},
            {"label": "Planned", "value": workflow_contexts["qnap"]["planned_summary"]},
            {"label": "Approved", "value": workflow_contexts["qnap"]["approved_summary"]},
            {"label": "Result", "value": workflow_contexts["qnap"]["result_summary"]},
        ],
    }


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


def latest_page_history_entry(history: list[dict[str, Any]], *, workflows: list[str] | None = None, scopes: list[str] | None = None) -> dict[str, Any] | None:
    workflows = workflows or []
    scopes = scopes or []
    for item in history:
        item_scope = str(item.get("scope") or "")
        item_workflow = str(item.get("workflow") or "")
        if item_scope in scopes or item_workflow in workflows:
            return item
    return None


def build_page_briefing(
    active_page: str,
    cfg: dict[str, Any],
    workflow_contexts: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    execution_review: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    if active_page in {"configs", "history", "kits"}:
        return None

    storage_review = build_storage_review_context(cfg)
    mapping = {
        "dashboard": {
            "title": "Start here",
            "purpose": "This is the home page. It shows the most important next step for this kit.",
            "next": build_recommended_next_step(cfg, workflow_contexts)["summary"],
            "next_label": build_recommended_next_step(cfg, workflow_contexts)["title"],
            "next_href": build_recommended_next_step(cfg, workflow_contexts)["href"],
            "last": (history[0].get("summary") if history else "") or "Nothing has happened yet for this kit.",
        },
        "global_settings": {
            "title": "Global settings",
            "purpose": "Use this page to save the shared defaults that the rest of the app can reuse.",
            "next": "Fill in the shared defaults, save them, then open the setup page you want to finish next.",
            "next_label": "Save shared defaults",
            "next_href": "/global-settings",
            "last": (
                (latest_page_history_entry(history, workflows=["global_settings"]) or {}).get("summary")
                or "Nothing has been saved here recently."
            ),
        },
        "configuration": {
            "title": "Global settings",
            "purpose": "Use this page to save the shared defaults that the rest of the app can reuse.",
            "next": "Fill in the shared defaults, save them, then open the setup page you want to finish next.",
            "next_label": "Save shared defaults",
            "next_href": "/global-settings",
            "last": (
                (latest_page_history_entry(history, workflows=["global_settings"]) or {}).get("summary")
                or "Nothing has been saved here recently."
            ),
        },
        "ilo": {
            "title": "iLO setup",
            "purpose": "Use this page to tell the app how to reach iLO and what iLO settings you want to use.",
            "next": (
                "Save the iLO address and sign-in details first."
                if not (cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host"))
                else "Save the iLO setup, then go to Run Center when you are ready."
            ),
            "next_label": (
                "Save iLO setup"
                if not (cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host"))
                else "Open Run Center"
            ),
            "next_href": "/ilo" if not (cfg.get("ilo", {}).get("current_ip") or cfg.get("ilo", {}).get("host")) else "/execution",
            "last": (
                (latest_page_history_entry(history, workflows=["ilo"], scopes=["ilo"]) or {}).get("summary")
                or "No iLO run has finished yet."
            ),
        },
        "storage": {
            "title": "Storage setup",
            "purpose": "Use this page to read the disks, build a layout, approve it, and send it into the real run.",
            "next": (
                "Read current storage first."
                if storage_review.get("state") in {"not_started", "idle"} or not storage_review.get("latest")
                else "Build storage plan next."
                if storage_review.get("state") == "discovered"
                else "Approve this plan next."
                if storage_review.get("state") == "planned" or storage_review.get("stale")
                else "Open Run Center when you are ready to use the approved plan."
            ),
            "next_label": (
                "Read current storage"
                if storage_review.get("state") in {"not_started", "idle"} or not storage_review.get("latest")
                else "Build storage plan"
                if storage_review.get("state") == "discovered"
                else "Approve this plan"
                if storage_review.get("state") == "planned" or storage_review.get("stale")
                else "Run for real"
            ),
            "next_href": (
                "/storage#read-current-storage"
                if storage_review.get("state") in {"not_started", "idle"} or not storage_review.get("latest")
                else "/storage#build-storage-plan"
                if storage_review.get("state") == "discovered"
                else "/storage#approve-storage-plan"
                if storage_review.get("state") == "planned" or storage_review.get("stale")
                else "/execution"
            ),
            "last": (
                (latest_page_history_entry(history, workflows=["storage"], scopes=["storage-apply", "storage-reboot"]) or {}).get("summary")
                or storage_review.get("status_reason")
                or "No storage run has finished yet."
            ),
        },
        "esxi": {
            "title": "ESXi setup",
            "purpose": "Use this page to save the ESXi name and password for this kit.",
            "next": (
                "Enter the ESXi name and password, then save them."
                if not (cfg.get("esxi", {}).get("hostname") and cfg.get("esxi", {}).get("root_password"))
                else "Open Run Center when you are ready."
            ),
            "next_label": (
                "Save ESXi setup"
                if not (cfg.get("esxi", {}).get("hostname") and cfg.get("esxi", {}).get("root_password"))
                else "Open Run Center"
            ),
            "next_href": "/esxi" if not (cfg.get("esxi", {}).get("hostname") and cfg.get("esxi", {}).get("root_password")) else "/execution",
            "last": (
                (latest_page_history_entry(history, workflows=["esxi"], scopes=["esxi"]) or {}).get("summary")
                or "No ESXi run has finished yet."
            ),
        },
        "windows": {
            "title": "Windows setup",
            "purpose": "Use this page to save the Windows details for this kit.",
            "next": "Fill in the Windows setup, save it, then use Run Center when you are ready.",
            "next_label": "Save Windows setup",
            "next_href": "/windows",
            "last": (
                (latest_page_history_entry(history, workflows=["windows"], scopes=["windows"]) or {}).get("summary")
                or "No Windows run has finished yet."
            ),
        },
        "qnap": {
            "title": "QNAP setup",
            "purpose": "Use this page to save the QNAP details for this kit.",
            "next": "Fill in the QNAP setup, save it, then use Run Center when you are ready.",
            "next_label": "Save QNAP setup",
            "next_href": "/qnap",
            "last": (
                (latest_page_history_entry(history, workflows=["qnap"], scopes=["qnap"]) or {}).get("summary")
                or "No QNAP run has finished yet."
            ),
        },
        "execution": {
            "title": "Run Center",
            "purpose": "Use this page to check the plan one last time before you run anything.",
            "next": (
                "Pick Review full run to see the full checklist."
                if not execution_review
                else "If the review looks right, choose Preview only or Run for real."
            ),
            "next_label": "Review full run" if not execution_review else "Run for real",
            "next_href": "/execution",
            "last": (
                (history[0].get("summary") if history else "") or "No run has finished yet."
            ),
        },
    }
    return mapping.get(active_page)


def mask_secret(value: str) -> str:
    return "Saved" if str(value or "") else "Not set"


def source_label(kind: str) -> str:
    mapping = {
        "global": "Using global value",
        "override": "Overridden on this page",
        "local": "Saved on this page",
        "current": "Using current iLO address",
        "planned": "Using planned iLO address",
        "storage_override": "Overridden on this page",
        "storage_fallback": "Using previously saved address",
    }
    return mapping.get(kind, kind)


def build_settings_sources(cfg: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    storage_target = resolve_storage_target_host(cfg)
    storage_credentials = resolve_storage_target_credentials(cfg)
    ilo_cfg = cfg.get("ilo", {}) or {}
    return {
        "global_settings": [
            {"label": "Shared subnet", "value": cfg.get("shared_network", {}).get("subnet", "") or "Not set", "source": "Feeds iLO, ESXi, Windows, QNAP, and Run Center"},
            {"label": "Shared gateway", "value": cfg.get("ip_plan", {}).get("gateway", "") or "Not set", "source": "Feeds every workflow that inherits the shared network"},
            {"label": "Shared DNS", "value": ", ".join([item for item in cfg.get("shared_network", {}).get("dns_servers", []) if item]) or "Not set", "source": "Used unless a workflow page overrides it later"},
        ],
        "ilo": [
            {"label": "Current iLO address", "value": ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "Not set", "source": source_label("local")},
            {"label": "Planned final iLO IP", "value": ilo_cfg.get("target_ip") or cfg.get("ip_plan", {}).get("ilo", "") or "Not set", "source": source_label("override") if ilo_cfg.get("target_ip") and ilo_cfg.get("target_ip") != cfg.get("ip_plan", {}).get("ilo", "") else source_label("global")},
            {"label": "Gateway", "value": ilo_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway", "") or "Not set", "source": source_label("override") if ilo_cfg.get("gateway") and ilo_cfg.get("gateway") != cfg.get("ip_plan", {}).get("gateway", "") else source_label("global")},
            {"label": "Username", "value": ilo_cfg.get("username") or "Not set", "source": source_label("local")},
            {"label": "Password", "value": mask_secret(ilo_cfg.get("password") or ""), "source": source_label("local")},
        ],
        "storage": [
            {
                "label": "Using right now",
                "value": storage_target.get("resolved") or "Not set",
                "source": (
                    source_label("storage_override")
                    if storage_target.get("override_active")
                    else source_label("planned")
                    if storage_target.get("source") == "planned iLO target IP"
                    else source_label("current")
                    if storage_target.get("source") in {"current kit iLO IP", "current kit iLO host"}
                    else source_label("storage_fallback")
                ),
            },
            {"label": "Username", "value": storage_credentials.get("username") or "Not set", "source": source_label("override") if storage_credentials.get("username_source") == "storage page override" else source_label("local")},
            {"label": "Password", "value": mask_secret(storage_credentials.get("password") or ""), "source": source_label("override") if storage_credentials.get("password_source") == "storage page override" else source_label("local")},
        ],
        "esxi": [
            {"label": "Target IP", "value": cfg.get("esxi", {}).get("management_ip") or cfg.get("ip_plan", {}).get("esxi", "") or "Not set", "source": source_label("global")},
            {"label": "Hostname", "value": cfg.get("esxi", {}).get("hostname", "") or "Not set", "source": source_label("local")},
            {"label": "Root password", "value": mask_secret(cfg.get("esxi", {}).get("root_password", "")), "source": source_label("local")},
        ],
        "windows": [
            {"label": "Target IP", "value": cfg.get("windows", {}).get("ip_address") or cfg.get("ip_plan", {}).get("windows", "") or "Not set", "source": source_label("global")},
            {"label": "VM name", "value": cfg.get("windows", {}).get("vm_name", "") or "Not set", "source": source_label("local")},
            {"label": "Admin password", "value": mask_secret(cfg.get("windows", {}).get("admin_password", "")), "source": source_label("local")},
        ],
        "qnap": [
            {"label": "Target IP", "value": cfg.get("qnap", {}).get("ip") or cfg.get("ip_plan", {}).get("qnap", "") or "Not set", "source": source_label("global")},
            {"label": "Hostname", "value": cfg.get("qnap", {}).get("hostname", "") or "Not set", "source": source_label("local")},
            {"label": "Username", "value": cfg.get("qnap", {}).get("username", "") or "Not set", "source": source_label("local")},
            {"label": "Password", "value": mask_secret(cfg.get("qnap", {}).get("password", "")), "source": source_label("local")},
        ],
    }


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


def build_run_bundles(cfg: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    for item in history:
        if item.get("kind") == "event":
            continue
        scope = str(item.get("scope") or "")
        config_summary = item.get("config_summary", {}) or {}
        target = str(config_summary.get("target_ip") or config_summary.get("login_ip") or "") or "Not set"
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
                "summary": item.get("current_stage") or item.get("summary") or "Run recorded",
                "run_summary_path": run_summary_path,
                "related_reports_query": related_reports_query,
                "related_reports": related_reports,
                "config_summary": config_summary,
            }
        )
    return bundles[:12]


def build_report_center(cfg: dict[str, Any], query: str = "", report_type: str = "all") -> dict[str, Any]:
    history = load_history(cfg.get("site", {}).get("name", ""))
    return {
        "query": query,
        "report_type": report_type,
        "entries": collect_report_entries(cfg, query=query, report_type=report_type),
        "bundles": build_run_bundles(cfg, history),
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
    if scope == "included" and cfg.get("included", {}).get("ilo"):
        return {
            "preview": preview_option,
            "real": {
                "scope": "ilo",
                "label": "Run for real",
                "summary": "Starts the live iLO run for this kit." + (" The approved storage plan will also be applied." if storage_real else ""),
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
    return {
        "included": True,
        "discovery_raw_path": approval.get("discovery_raw_path", ""),
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
    return {
        "source": source,
        "path": drive.get("path", ""),
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
            capacity_delta = abs(left["size_gib"] - right["size_gib"])
            usable_size = min(left["size_gib"], right["size_gib"])
            acceptable_for_target = usable_size >= 450
            target_distance = abs(usable_size - 500) if acceptable_for_target else 500 - usable_size
            pair = sorted([left, right], key=storage_drive_sort_key)
            candidates.append((
                (
                    0 if same_media else 1,
                    0 if same_protocol else 1,
                    capacity_delta,
                    0 if acceptable_for_target else 1,
                    target_distance,
                    storage_drive_sort_key(pair[0]),
                    storage_drive_sort_key(pair[1]),
                ),
                pair,
            ))

    if not candidates:
        return [], "No eligible pair was available."

    _, pair = sorted(candidates, key=lambda item: item[0])[0]
    return pair, (
        "Selected the best matched pair by media type, protocol, capacity, and deterministic bay/order. "
        f"Usable mirror size is about {min(d['size_gib'] for d in pair):.0f} GiB before applying the 500 GiB target."
    )


def choose_raid6_layout(remaining_drives: list[dict]) -> tuple[list[dict], dict, list[dict], str, list[str]]:
    if not remaining_drives:
        return [], {}, [], "No remaining eligible drives were available for RAID 6.", ["No remaining eligible drives are available for the Data RAID 6 set."]

    groups: dict[tuple, list[dict]] = {}
    for drive in remaining_drives:
        groups.setdefault(drive_group_key(drive), []).append(drive)

    ranked_groups = sorted(groups.items(), key=lambda item: (-len(item[1]), -item[0][2], item[0][0], item[0][1]))
    selected_key, selected_group = ranked_groups[0]
    compatible_group = sorted(selected_group, key=storage_drive_sort_key)
    excluded = [
        {**drive, "exclude_reason": "Not in the selected RAID 6 compatible media/protocol/capacity group."}
        for drive in remaining_drives
        if drive not in selected_group
    ]
    blockers = []
    if len(compatible_group) < 4:
        blockers.append("RAID 6 requires at least four compatible remaining drives.")
        explanation = (
            f"Best remaining compatible group for RAID 6 was too small: media={selected_key[0]}, "
            f"protocol={selected_key[1]}, capacity≈{selected_key[2]} GiB, drives={len(compatible_group)}."
        )
        return compatible_group, {}, excluded, explanation, blockers

    if len(compatible_group) < 5:
        blockers.append("Storage policy requires one additional compatible hot spare beyond the RAID 6 drive set.")
        explanation = (
            f"Compatible RAID 6 group found with media={selected_key[0]}, protocol={selected_key[1]}, "
            f"capacity≈{selected_key[2]} GiB, but only {len(compatible_group)} drives remain so no hot spare can be reserved."
        )
        return compatible_group, {}, excluded, explanation, blockers

    spare = compatible_group[-1]
    raid6_set = compatible_group[:-1]
    explanation = (
        f"Selected the largest compatible remaining group for RAID 6: media={selected_key[0]}, "
        f"protocol={selected_key[1]}, capacity≈{selected_key[2]} GiB. "
        f"Reserved bay {spare['bay'] or spare['id']} as the data-side hot spare."
    )
    return raid6_set, spare, excluded, explanation, blockers


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


def build_storage_planning_drives(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    summary = summary or {}
    standard = summary.get("standard_redfish_storage", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    planning_drives = []
    for source, items in (("hpe_smart_storage", hpe.get("drives", [])), ("standard_redfish_storage", standard.get("drives", []))):
        for item in items or []:
            drive = normalized_plan_drive(item, source)
            drive["eligible"] = bool(drive["size_gib"] > 0 and storage_status_is_eligible(drive["status"]))
            planning_drives.append(drive)
    return sorted(planning_drives, key=storage_drive_sort_key)


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
            controllers.append({**item, "source": source})
    controller = controllers[0] if controllers else {}
    if not controller:
        blockers.append("No detected storage controller is available for planning.")
    elif controller.get("firmware_version") is not None:
        controller = {**controller, "firmware_version": storage_firmware_display(controller.get("firmware_version"))}

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
            if drive["size_gib"] <= 0:
                excluded_drives.append({**drive, "exclude_reason": "Missing or zero drive size."})
            elif not storage_status_is_eligible(drive["status"]):
                excluded_drives.append({**drive, "exclude_reason": f"Drive status is not eligible: {drive['status'] or 'unknown'}."})
            else:
                eligible_drives.append(drive)

    default_os_pair, default_os_explanation = choose_os_drive_pair(sorted(eligible_drives, key=storage_drive_sort_key))

    default_os_paths = {drive["path"] for drive in default_os_pair}
    default_remaining = [drive for drive in eligible_drives if drive["path"] not in default_os_paths]
    default_data_set, default_hot_spare, default_raid6_excluded, default_raid6_explanation, default_raid6_blockers = choose_raid6_layout(sorted(default_remaining, key=storage_drive_sort_key))

    eligible_by_bay = {str(drive.get("bay") or ""): drive for drive in eligible_drives}
    selected_os_bays = [str(item).strip() for item in list(overrides.get("os_bays") or []) if str(item).strip()]
    selected_data_bays = [str(item).strip() for item in list(overrides.get("data_bays") or []) if str(item).strip()]
    selected_spare_bay = str(overrides.get("hot_spare_bay") or "").strip()

    customization_active = bool(selected_os_bays or selected_data_bays or selected_spare_bay)
    os_pair = list(default_os_pair)
    os_explanation = default_os_explanation
    data_set = list(default_data_set)
    hot_spare = dict(default_hot_spare) if default_hot_spare else {}
    raid6_excluded = list(default_raid6_excluded)
    raid6_explanation = default_raid6_explanation

    if customization_active:
        custom_blockers = []
        overlap_blockers = []
        if selected_os_bays:
            os_pair = [eligible_by_bay[bay] for bay in selected_os_bays if bay in eligible_by_bay]
            missing_os = [bay for bay in selected_os_bays if bay not in eligible_by_bay]
            if missing_os:
                custom_blockers.append(f"Selected OS drives are not eligible or were not found: {', '.join(missing_os)}.")
            os_explanation = "Using the drives chosen below for the OS mirror."
        if selected_data_bays:
            data_set = [eligible_by_bay[bay] for bay in selected_data_bays if bay in eligible_by_bay]
            missing_data = [bay for bay in selected_data_bays if bay not in eligible_by_bay]
            if missing_data:
                custom_blockers.append(f"Selected data drives are not eligible or were not found: {', '.join(missing_data)}.")
            raid6_explanation = "Using the drives chosen below for the data array."
        if selected_spare_bay:
            hot_spare = dict(eligible_by_bay.get(selected_spare_bay) or {})
            if not hot_spare:
                custom_blockers.append(f"Selected hot spare bay was not eligible or was not found: {selected_spare_bay}.")
        os_bays_set = {drive.get("bay") for drive in os_pair}
        data_bays_set = {drive.get("bay") for drive in data_set}
        if os_bays_set & data_bays_set:
            overlap_blockers.append("The same drive cannot be used for both the OS mirror and the data array.")
        if selected_spare_bay and (selected_spare_bay in os_bays_set or selected_spare_bay in data_bays_set):
            overlap_blockers.append("The hot spare must be different from the OS and data drives.")
        if len(os_pair) != 2:
            custom_blockers.append("Choose exactly two drives for the OS RAID 1 pair.")
        if len(data_set) < 4:
            custom_blockers.append("Choose at least four compatible drives for the Data RAID 6 set.")
        if hot_spare:
            compatibility_group = {drive_group_key(drive) for drive in data_set + [hot_spare]}
        else:
            compatibility_group = {drive_group_key(drive) for drive in data_set}
        if data_set and len(compatibility_group) > 1:
            custom_blockers.append("The selected data drives and hot spare must use the same media type, protocol, and size.")
        blockers.extend(custom_blockers + overlap_blockers)
        raid6_excluded = [
            {**drive, "exclude_reason": "Not selected for the custom data layout."}
            for drive in eligible_drives
            if drive.get("bay") not in {*(drive.get("bay") for drive in os_pair), *(drive.get("bay") for drive in data_set), selected_spare_bay}
        ]
        warnings.append("This plan was customized from the default drive selection.")

    excluded_drives.extend({**drive, "exclude_reason": "Reserved for OS RAID 1 pair."} for drive in os_pair)
    if hot_spare:
        excluded_drives.append({**hot_spare, "exclude_reason": "Reserved as the data-side hot spare."})
        excluded_drives.extend(raid6_excluded)
    elif len(default_os_pair) < 2:
        blockers.append("Could not choose two suitable drives for the OS RAID 1 pair.")
        blockers.extend(default_raid6_blockers)

    apply_readiness = {
        "next_action": "wipe and rebuild" if existing_volumes else "create only",
        "create_only_ready": not existing_volumes and len(data_set) >= 4 and bool(hot_spare) and len(os_pair) == 2,
        "wipe_rebuild_ready": len(data_set) >= 4 and bool(hot_spare) and len(os_pair) == 2,
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
    if len(os_pair) != 2:
        create_only_blockers.append("Two suitable drives were not selected for the OS RAID 1 pair.")
    if len(data_set) < 4:
        create_only_blockers.append("At least four compatible data drives are required for the Data RAID 6 set.")
    if not hot_spare:
        create_only_blockers.append("A compatible hot spare could not be reserved.")
    wipe_rebuild_blockers = []
    if len(os_pair) != 2:
        wipe_rebuild_blockers.append("Two suitable drives were not selected for the OS RAID 1 pair.")
    if len(data_set) < 4:
        wipe_rebuild_blockers.append("At least four compatible data drives are required for the Data RAID 6 set.")
    if not hot_spare:
        wipe_rebuild_blockers.append("A compatible hot spare could not be reserved.")
    apply_readiness["create_only_blockers"] = create_only_blockers
    apply_readiness["wipe_rebuild_blockers"] = wipe_rebuild_blockers
    planned_layout = {
        "os_raid1": {
            "raid": "RAID 1",
            "target_size_gib": 500,
            "bays": plan_drive_bays(os_pair),
            "drives": os_pair,
        },
        "data_raid6": {
            "raid": "RAID 6",
            "bays": plan_drive_bays(data_set),
            "capacity_intent": "Use the remaining compatible eligible drives after reserving one hot spare.",
            "drives": data_set,
        },
        "hot_spare": {
            "required": True,
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
            "directory": str(discovery_paths["directory"]),
            "summary": str(discovery_paths["summary"]),
            "raw": str(discovery_paths["raw"]),
        },
        "existing_logical_volumes_detected": bool(existing_volumes),
        "default_recommendation": "wipe and rebuild" if existing_volumes else "create only",
        "existing_logical_volumes": existing_volumes,
        "desired_layout": {
            "os_volume": {"raid": "RAID 1", "target_size_gib": 500},
            "data_volume": {"raid": "RAID 6", "capacity": "remaining compatible eligible drives after reserving one hot spare"},
            "hot_spare": {"required": True, "scope": "data-side compatible spare"},
        },
        "customization": {
            "active": customization_active,
            "selected_os_bays": [str(drive.get("bay") or "") for drive in os_pair],
            "selected_data_bays": [str(drive.get("bay") or "") for drive in data_set],
            "selected_hot_spare_bay": str((hot_spare or {}).get("bay") or ""),
        },
        "planned_layout": planned_layout,
        "os_raid1": {"target_size_gib": 500, "drives": os_pair, "explanation": os_explanation},
        "data_raid6": {"feasible": len(data_set) >= 4 and bool(hot_spare), "drives": data_set, "drive_count": len(data_set), "explanation": raid6_explanation},
        "hot_spare": {"required": True, "drive": hot_spare, "reserved": bool(hot_spare)},
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
        "reboot_required",
        "settings_path",
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
            "raid": "RAID1",
            "target_size_gib": plan.get("os_raid1", {}).get("target_size_gib", 500),
            "bays": [drive.get("bay") for drive in plan.get("os_raid1", {}).get("drives", [])],
            "drive_paths": [drive.get("path") for drive in plan.get("os_raid1", {}).get("drives", [])],
            "drives": list(plan.get("os_raid1", {}).get("drives", []) or []),
        },
        "data_raid6": {
            "raid": "RAID6",
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


def storage_apply_mode_for_plan(plan: dict[str, Any]) -> str:
    next_action = str((plan.get("apply_readiness", {}) or {}).get("next_action") or "").strip().lower()
    default_recommendation = str(plan.get("default_recommendation") or "").strip().lower()
    mode = next_action or default_recommendation
    if "wipe" in mode or "rebuild" in mode:
        return "wipe_rebuild"
    return "create_only"


def choose_storage_apply_platform(discovery: dict, plan: dict) -> dict[str, Any]:
    summary = discovery.get("summary", {}) or {}
    server = summary.get("server", {}) or {}
    ilo = summary.get("ilo", {}) or {}
    capabilities = summary.get("capabilities", {}) or {}
    hpe_diag = capabilities.get("hpe_smart_storage_diagnostics", {}) or {}
    found_paths = [str(item.get("path") or "") for item in hpe_diag.get("found_paths", []) if item.get("path")]

    settings_path = ""
    for path in found_paths:
        lower = path.lower()
        if "smartstorageconfig" in lower and lower.endswith("/settings"):
            settings_path = path
            break
    if not settings_path:
        for path in found_paths:
            lower = path.lower()
            if "smartstorageconfig" in lower:
                settings_path = path.rstrip("/") + "/settings"
                break

    controller = plan.get("source_discovery", {}).get("controller", {}) or {}
    server_gen = str(server.get("generation") or "")
    ilo_version = str(ilo.get("version") or ilo.get("model") or "")
    if capabilities.get("hpe_smart_storage") and ("Gen10" in server_gen or "iLO 5" in ilo_version):
        return {
            "id": "gen10_hpe_smartstorageconfig",
            "label": "Gen10 / iLO 5 / HPE SmartStorageConfig",
            "supported": True,
            "settings_path": settings_path,
            "controller_path": controller.get("path", ""),
        }
    if capabilities.get("standard_redfish_storage"):
        return {
            "id": "gen11_standard_redfish",
            "label": "Gen11 / iLO 6 / standard Redfish Storage",
            "supported": False,
            "settings_path": "",
            "controller_path": controller.get("path", ""),
        }
    return {
        "id": "unsupported",
        "label": "Unsupported storage apply path",
        "supported": False,
        "settings_path": "",
        "controller_path": controller.get("path", ""),
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
    controllers = []
    for source_key in ("hpe_smart_storage", "standard_redfish_storage"):
        controllers.extend(((plan.get("source_discovery", {}) or {}).get(source_key, {}) or {}).get("controllers", []) or [])
    if len(controllers) > 1:
        raise ValueError("This destructive apply path only supports a single detected storage controller.")
    if not plan.get("hot_spare", {}).get("reserved"):
        raise ValueError("Storage apply requires a reserved hot spare.")
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
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Create OS RAID 1 logical drive",
        current,
        total_steps,
        "running",
        "Create OS RAID 1 logical drive",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": os_intent.get("bays", [])},
        details=f"Staging OS RAID 1 into one consolidated SmartStorageConfig payload at {settings_path}.",
        progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
    )
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Create OS RAID 1 logical drive",
        current,
        total_steps,
        "ok",
        "Create OS RAID 1 logical drive",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": os_intent.get("bays", [])},
        details="Queued OS RAID 1 in the final pending config.",
        progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
    )
    current += 1

    data_intent = intent["data_raid6"]
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Create Data RAID 6 logical drive",
        current,
        total_steps,
        "running",
        "Create Data RAID 6 logical drive",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": data_intent.get("bays", [])},
        details=f"Staging Data RAID 6 into one consolidated SmartStorageConfig payload at {settings_path}.",
        progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
    )
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Create Data RAID 6 logical drive",
        current,
        total_steps,
        "ok",
        "Create Data RAID 6 logical drive",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": data_intent.get("bays", [])},
        details="Queued Data RAID 6 in the final pending config.",
        progress_percent=progress_resolver(current, total_steps) if progress_resolver else None,
    )
    current += 1

    spare_intent = intent["hot_spare"]
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
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": [spare_intent.get("bay", "")]},
        details=f"Submitting one consolidated SmartStorageConfig payload with the reserved hot spare at {settings_path}.",
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
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "bays": [spare_intent.get("bay", "")]},
        details="Submitted the consolidated SmartStorageConfig pending payload with OS RAID 1, Data RAID 6, and dedicated hot spare.",
        response=response,
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
        pre_change_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
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

        platform = choose_storage_apply_platform(pre_change_discovery, plan)
        apply_state["apply_path"] = platform.get("label", "")
        record_storage_apply_step(
            kit_name,
            job,
            apply_state,
            apply_paths,
            "Choose storage apply path",
            3,
            apply_steps,
            "ok",
            "Choose storage apply path",
            targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "path": platform.get("settings_path", "")},
            details=f"Selected {platform.get('label')} ({platform.get('id')}).",
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
        else:
            raise ILOError(f"Storage apply path {platform.get('label')} is not implemented yet.")

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
            details="Reboot request accepted by iLO.",
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
            details=reboot_result.get("return_detail", ""),
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
        system_path = client.get_systems()[0]
        job = load_job(kit_name)

        interruption_observed = False
        start_deadline = time.time() + max(reboot_start_timeout, 1)
        last_detail = ""
        while time.time() < start_deadline:
            apply_state = json.loads(apply_paths["apply_results"].read_text(encoding="utf-8")) if apply_paths["apply_results"].exists() else {}
            if apply_state.get("workflow_state") != "staged_reboot_required" or apply_state.get("reboot_requested"):
                return
            try:
                system = client.get_system(system_path)
                power_state = str(system.get("PowerState") or "")
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
    discovery, _discovery_paths, plan, plan_paths = restore_storage_page_state(
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        expected_host=validation_host,
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
    pre_change_discovery = client.get_storage_discovery(deep_smart_storage_scan=False)
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

    platform = choose_storage_apply_platform(pre_change_discovery, plan)
    apply_state["apply_path"] = platform.get("label", "")
    current_step += 1
    record_storage_apply_step(
        kit_name,
        job,
        apply_state,
        apply_paths,
        "Choose storage apply path",
        current_step,
        total_steps,
        "ok",
        "Choose storage apply path",
        targets={"controller": apply_state["controller"].get("name") or apply_state["controller"].get("model") or "", "path": platform.get("settings_path", "")},
        details=f"Selected {platform.get('label')} for the real storage stage.",
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
    else:
        raise ILOError(f"Storage apply path {platform.get('label')} is not implemented yet.")

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
            details=str(reboot_result.get("return_detail") or reboot_result.get("reboot_start_detail") or "Server returned after reboot."),
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
            "windows": True,
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
            "hostname": "esxi01",
            "management_ip": "",
            "subnet_mask": "255.255.255.0",
            "gateway": "",
            "dns_servers": [],
            "root_password": "",
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

    unique_values = list(plan.values())
    if len(unique_values) != len(set(unique_values)):
        raise ValueError("Each device IP must be unique within the kit")
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
    esxi_install_review = build_esxi_install_review(cfg) if scope in {"esxi", "included"} else {}
    selected_scope_keys = run_center_scope_keys(scope, cfg)
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
            if esxi_install_review.get("missing_fields"):
                values.append(f"Missing required values: {', '.join(esxi_install_review.get('missing_fields') or [])}")
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
        return
    if scope == "storage":
        storage_review = build_storage_review_context(cfg)
        if storage_review.get("stale"):
            raise ValueError("The approved storage plan is stale and must be reviewed again before a storage run.")
        if not storage_review.get("approved"):
            raise ValueError("No approved storage plan is saved for this kit.")
        if "esxi" in selected:
            esxi_values = get_esxi_effective_values(cfg)
            if esxi_values["missing_fields"]:
                raise ValueError(f"ESXi setup is missing: {', '.join(esxi_values['missing_fields'])}.")
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
        if scope == "ilo":
            run_ilo_real(cfg)
        elif scope == "storage":
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
    configured = str((cfg.get("esxi", {}) or {}).get("base_iso_path") or "").strip()
    if configured:
        path = Path(configured)
        if path.exists():
            return path
        raise FileNotFoundError(f"Configured ESXi base ISO was not found: {path}")

    base_dir = BASE_DIR / "media" / "esxi" / "base"
    candidates = sorted(list(base_dir.glob("*.iso")) + list(base_dir.glob("*.ISO")))
    if not candidates:
        raise FileNotFoundError(f"No ESXi base ISO was found under {base_dir}")
    return candidates[0]


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
    values = {
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
    values["missing_fields"] = missing
    return values


def build_esxi_install_review(cfg: dict, *, run_stamp: str | None = None) -> dict[str, Any]:
    ilo_cfg = cfg.get("ilo", {}) or {}
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    login_ip = str(ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    stamp = (run_stamp or datetime.now().strftime("%Y%m%d-%H%M%S")).strip()
    output_name = f"esxi-{stamp}"
    output_iso = EXPORTS_DIR / "esxi-isos" / kit_name / output_name / f"{output_name}.iso"
    values = get_esxi_effective_values(cfg)
    base_iso_path = resolve_esxi_base_iso_path(cfg)
    iso_url = build_esxi_iso_url(cfg, output_iso, login_ip)
    return {
        "run_stamp": stamp,
        "source_label": "Saved kit values from the ESXi Setup page and shared defaults",
        "manual_defaults_label": "Manual test script defaults are not used by Run Center",
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
        "missing_fields": list(values["missing_fields"]),
    }


def esxi_password_policy_valid(password: str) -> bool:
    value = str(password or "")
    return (
        len(value) >= 8
        and any(ch.islower() for ch in value)
        and any(ch.isupper() for ch in value)
        and any(ch.isdigit() for ch in value)
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
    system_path = client.get_systems()[0]
    deadline = time.time() + max(timeout_seconds, 1)
    last_seen = ""
    while time.time() < deadline:
        current = client.get_system(system_path)
        last_seen = str(current.get("PowerState") or "")
        if last_seen.lower() == expected_state.lower():
            return current
        time.sleep(max(poll_interval, 1))
    raise ILOError(f"Timed out waiting for server power state {expected_state}. Last observed state: {last_seen or 'unknown'}.")


def wait_for_esxi_management_ready(
    host: str,
    *,
    timeout_seconds: int = 2400,
    poll_interval: int = 15,
    port: int = 443,
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
    login_ip = str(ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
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
        "esxi_install_values": {},
        "esxi_virtual_media": {},
        "esxi_boot_override": {},
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
            },
            "artifacts": {
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
                f"disable_ipv6={'yes' if esxi_review['disable_ipv6'] else 'no'}"
            ),
        )
        update_job(kit_name, job, "Running", "Review install values", 1, total, f"[INFO] Base ISO: {base_iso_path}")

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
            save_job(kit_name, job)
            save_esxi_trace(trace_path, trace_payload)
            generation = build_summary.get("generation", {}) or {}
            self_check = build_summary.get("self_check", {}) or {}
            if (generation.get("ks_cfg", {}) or {}).get("generated"):
                update_job(kit_name, job, "Running", "Build complete", 2, total, "[OK] KS.CFG generated")
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
                run_with_session_refresh("Eject media", lambda c, vm_path=vm["@odata.id"]: c.eject_virtual_media(vm_path))

        system_path = run_with_session_refresh("Power off", lambda c: c.get_systems())[0]
        current_system = run_with_session_refresh("Power off", lambda c: c.get_system(system_path))
        current_power = str(current_system.get("PowerState") or "")
        if current_power.lower() != "off":
            update_job(kit_name, job, "Running", "Power off", 4, total, "[RUNNING] Powering server off before setting one-time boot")
            trace_payload["steps"].append({"stage": "power_off", "status": "running", "from_state": current_power})
            save_esxi_trace(trace_path, trace_payload)
            try:
                run_with_session_refresh("Power off", lambda c: c.power_reset(reset_type="GracefulShutdown", system_path=system_path))
            except Exception:
                run_with_session_refresh("Power off", lambda c: c.power_reset(reset_type="ForceOff", system_path=system_path))
            wait_for_power_state(client, "Off", timeout_seconds=180, poll_interval=5)
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
        run_with_session_refresh(
            "Mount ISO",
            lambda c: c._post(insert_target, {"Image": iso_url, "Inserted": True, "WriteProtected": True}),
        )
        mount_readback = {}
        for item in run_with_session_refresh("Mount ISO", lambda c: c.get_virtual_media()):
            if str(item.get("@odata.id") or "") == str(vm.get("@odata.id") or ""):
                mount_readback = {
                    "device_path": str(item.get("@odata.id") or ""),
                    "inserted": bool(item.get("Inserted")),
                    "image": str(item.get("Image") or ""),
                    "write_protected": item.get("WriteProtected"),
                }
                break
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
                "Failed",
                "Set boot override",
                8,
                total,
                f"[FAILED] One-time boot did not stick; expected Once/Cd but got enabled={after_enabled} target={after_target}.",
            )
            job["logs"].append("[SKIP] Server power-on blocked because one-time boot was not verified")
            save_job(kit_name, job)
            trace_payload["steps"].append({"stage": "set_one_time_boot", "status": "mismatch", **boot_override})
            trace_payload["result"] = {
                "status": "Failed",
                "error": f"One-time boot did not stick; expected Once/Cd but got enabled={after_enabled} target={after_target}.",
            }
            save_esxi_trace(trace_path, trace_payload)
            return
        update_job(kit_name, job, "Running", "Set boot override", 8, total, f"[INFO] Boot override after: enabled={after_enabled} target={after_target}")
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
        run_with_session_refresh("Power on", lambda c: c.power_reset(reset_type="On", system_path=system_path))
        update_job(kit_name, job, "Running", "Wait for server power", 10, total, "[RUNNING] Waiting for the server to power back on")
        wait_for_power_state(client, "On", timeout_seconds=300, poll_interval=5)
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
        ready_result = wait_for_esxi_management_ready(management_ip)
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
            detail += " This usually means the kickstart network settings did not apply or the installer did not finish."
        if 'trace_path' in locals():
            trace_payload["result"] = {
                "status": "Failed",
                "error": detail,
            }
            save_esxi_trace(trace_path, trace_payload)
        update_job(kit_name, job, "Failed", job.get("current_stage") or "ESXi real run failed", job.get("completed_steps", 0), total, f"[FAILED] {detail}")


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
    desired_hostname = ilo_cfg.get("hostname", "").strip()
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
        update_job(kit_name, job, "Failed", "iLO error", job.get("completed_steps", 0), total, f"[FAILED] {e}")
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
    live_inventory_status: dict | None = None,
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
    storage_planning_drives = build_storage_planning_drives((storage_discovery or {}).get("summary") if storage_discovery else None)
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
    page_comparisons = build_page_comparisons(cfg, workflow_contexts, history)
    recommended_next_step = build_recommended_next_step(cfg, workflow_contexts)
    activity_feed = build_activity_feed(history)
    dashboard_job_status = build_dashboard_job_status(history)
    report_center = build_report_center(
        cfg,
        query=str(request.query_params.get("report_query", "") or ""),
        report_type=str(request.query_params.get("report_type", "all") or "all"),
    )
    page_briefing = build_page_briefing(active_page, cfg, workflow_contexts, history, execution_review)
    page_runbook = PAGE_RUNBOOKS.get(active_page, {})
    settings_sources = build_settings_sources(cfg)
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
        "live_inventory_status": live_inventory_status,
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
        "storage_plan_defaults": storage_plan_defaults,
        "workflow_contexts": workflow_contexts,
        "page_comparisons": page_comparisons,
        "recommended_next_step": recommended_next_step,
        "activity_feed": activity_feed,
        "dashboard_job_status": dashboard_job_status,
        "report_center": report_center,
        "page_briefing": page_briefing,
        "page_runbook": page_runbook,
        "settings_sources": settings_sources,
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
    except WebSocketDisconnect:
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
    return render_page(request, cfg, active_page="kits")


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="history")


@app.post("/load-kit", response_class=HTMLResponse)
async def load_kit_route(request: Request, selected_kit: str = Form(...), return_page: str = Form("kits")):
    set_current_kit_name(selected_kit)
    cfg = load_kit_config(selected_kit)
    return render_page(request, cfg, active_page=return_page, message=f"Loaded kit: {selected_kit}")


@app.post("/new-kit", response_class=HTMLResponse)
async def new_kit_route(request: Request, new_kit_name: str = Form(...), return_page: str = Form("kits")):
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
            "hostname": ilo_hostname,
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
    cfg["ilo"]["hostname"] = ilo_hostname
    cfg["ilo"]["username"] = ilo_username
    cfg["ilo"]["password"] = ilo_password
    cfg["ilo"]["additional_users"] = extract_ilo_additional_users_from_form(form)
    cfg["included"]["ilo"] = True
    cfg = apply_ip_plan(cfg)
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
        ],
    )
    return render_page(
        request,
        cfg,
        active_page=return_page,
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
    esxi_hostname: str = Form(""),
    esxi_root_password: str = Form(""),
    included_esxi: str | None = Form(None),
):
    cfg = load_kit_config()
    cfg["esxi"]["hostname"] = esxi_hostname
    cfg["esxi"]["root_password"] = esxi_root_password
    cfg["included"]["esxi"] = included_esxi == "on"
    cfg = apply_ip_plan(cfg)
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
            live_inventory_status=build_live_inventory_status("Failed", "Failed", [error_text]),
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
            live_inventory_status=live_inventory_success_status("Complete", export_paths, host=host),
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
            live_inventory_status=live_inventory_failure_status("Failed", error_text),
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
            live_inventory_status=build_live_inventory_status("Failed", "Failed", [error_text]),
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
            live_inventory_status=live_inventory_success_status("Complete", export_paths, host=host, label=label),
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
            live_inventory_status=live_inventory_failure_status("Failed", error_text),
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
            live_inventory_status=build_live_inventory_status("Failed", "Failed", [error_text]),
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
        live_inventory_status=live_inventory_success_status("Complete", latest),
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
        plan = build_raid_plan(
            discovery,
            discovery_paths,
            overrides={
                "os_bays": os_bays,
                "data_bays": data_bays,
                "hot_spare_bay": hot_spare_bay,
            },
        )
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


@app.get("/esxi-built-iso/{kit_name}/{output_name}.iso")
async def download_built_esxi_iso(kit_name: str, output_name: str):
    safe_kit_name = sanitize_kit_name(kit_name)
    safe_output_name = sanitize_kit_name(output_name)
    path = EXPORTS_DIR / "esxi-isos" / safe_kit_name / f"{safe_output_name}.iso"
    if not path.exists():
        return HTMLResponse(f"Built ESXi ISO not found: {path}", status_code=404)
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
