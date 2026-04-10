from pathlib import Path
import asyncio
import ipaddress
import json
import re
import threading
import time
import yaml
from typing import Any

from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.ilo import ILOClient, ILOConfig, ILOError

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = BASE_DIR / "config"
KITS_DIR = CONFIG_DIR / "kits"
CURRENT_KIT_FILE = CONFIG_DIR / "current_kit.txt"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
GENERATED_DIR = ARTIFACTS_DIR / "generated"
JOBS_DIR = ARTIFACTS_DIR / "jobs"
KS_OUTPUT_PATH = GENERATED_DIR / "KS.CFG"
HISTORY_DIR = ARTIFACTS_DIR / "history"
ILO_CONFIG_EXPORT_DIR = HISTORY_DIR / "ilo-configs"
CONFIG_EXPORT_DIR = HISTORY_DIR / "configs"
LIVE_ILO_CONFIG_DIR = HISTORY_DIR / "ilo-live-configs"
ILO_INVENTORY_DIR = HISTORY_DIR / "ilo-inventory"
EXPORTS_DIR = ARTIFACTS_DIR / "exports"
ILO_LIVE_EXPORT_DIR = EXPORTS_DIR / "ilo" / "live"
STORAGE_RAID_EXPORT_DIR = EXPORTS_DIR / "storage-raid"
STORAGE_APPLY_CONFIRM_CREATE = "CREATE STORAGE"
STORAGE_APPLY_CONFIRM_WIPE = "WIPE STORAGE"

app = FastAPI(title="Lab Builder")

STATIC_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
KITS_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
ILO_CONFIG_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
LIVE_ILO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
ILO_INVENTORY_DIR.mkdir(parents=True, exist_ok=True)
ILO_LIVE_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_RAID_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

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
        "title": "Configs",
        "subtitle": "View and export generated configuration snapshots.",
    },
    "storage": {
        "title": "Storage / RAID",
        "subtitle": "Read current storage controllers, arrays, volumes, and drives.",
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


def load_job(kit_name: str):
    path = job_path(kit_name)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
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
    with open(job_path(kit_name), "w", encoding="utf-8") as f:
        yaml.safe_dump(job, f, sort_keys=False)

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
            "gateway": (cfg.get("ip_plan", {}).get("gateway") or "").strip(),
            "dns_servers": shared_dns,
            "snmp_v3_username": (snmp_cfg.get("v3_username") or "").strip(),
            "snmp_v3_auth_protocol": snmp_cfg.get("v3_auth_protocol", "SHA"),
            "snmp_v3_priv_protocol": snmp_cfg.get("v3_priv_protocol", "AES"),
            "storage_included": bool(storage_review.get("include_in_ilo_run")),
            "storage_plan_path": (storage_review.get("approval", {}) or {}).get("plan_path", ""),
        }

    return {
        "target_ip": (cfg.get("ip_plan", {}).get(scope) or "").strip(),
        "gateway": (cfg.get("ip_plan", {}).get("gateway") or "").strip(),
        "dns_servers": [x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x and x.strip()],
    }


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

    return snapshot_path


def export_current_kit_config_snapshot(cfg: dict) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    snapshot_path = CONFIG_EXPORT_DIR / f"{base_name}-{timestamp}.yml"

    with open(snapshot_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

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
        "error": "No storage target host is resolved. Set the current kit iLO IP/host or an explicit storage target override before using Storage / RAID actions.",
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


def build_raid_plan(discovery: dict, discovery_paths: dict[str, Path]) -> dict:
    summary = discovery.get("summary", {})
    standard = summary.get("standard_redfish_storage", {}) or {}
    hpe = summary.get("hpe_smart_storage", {}) or {}
    server = summary.get("server", {}) or {}
    warnings = []
    blockers = []

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

    os_pair, os_explanation = choose_os_drive_pair(sorted(eligible_drives, key=storage_drive_sort_key))
    if len(os_pair) < 2:
        blockers.append("Could not choose two suitable drives for the OS RAID 1 pair.")

    os_paths = {drive["path"] for drive in os_pair}
    remaining = [drive for drive in eligible_drives if drive["path"] not in os_paths]
    data_set, hot_spare, raid6_excluded, raid6_explanation, raid6_blockers = choose_raid6_layout(sorted(remaining, key=storage_drive_sort_key))
    blockers.extend(raid6_blockers)

    excluded_drives.extend({**drive, "exclude_reason": "Reserved for OS RAID 1 pair."} for drive in os_pair)
    if hot_spare:
        excluded_drives.append({**hot_spare, "exclude_reason": "Reserved as the data-side hot spare."})
    excluded_drives.extend(raid6_excluded)

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
        progress_percent=storage_workflow_progress_percent(workflow_state, completed, total),
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
    total_steps = 10
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
            total_steps,
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
            total_steps,
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
            total_steps,
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
            total_steps,
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
            total_steps,
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
                total_steps,
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
            total_steps,
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
            total_steps,
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
            total_steps,
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
            total_steps,
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
            total_steps,
            total_steps,
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
                total_steps,
                total_steps,
                f"[STAGED] Storage changes were staged via {apply_state.get('apply_path')}. Reboot is required before post-reboot validation can complete.",
                progress_percent=storage_workflow_progress_percent("staged_reboot_required", total_steps, total_steps),
            )
        else:
            update_job(
                kit_name,
                job,
                "Completed",
                "Finished",
                total_steps,
                total_steps,
                f"[DONE] Storage apply finished via {apply_state.get('apply_path')}. reboot_required={apply_state.get('reboot_required')}",
                progress_percent=storage_workflow_progress_percent("apply_complete", total_steps, total_steps),
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
            total_steps,
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
                    total_steps,
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
            total_steps,
            f"[FAILED] Storage apply failed: {error_text}",
            progress_percent=storage_workflow_progress_percent("apply_failed", job.get("completed_steps", 0), total_steps),
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


def normalize_ilo_config(cfg: dict):
    ilo_cfg = cfg.setdefault("ilo", {})
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


def render_ks_cfg(cfg):
    template = templates.env.get_template("ks.cfg.j2")
    esxi = cfg.get("esxi", {})
    return template.render(
        hostname=esxi.get("hostname", ""),
        management_ip=esxi.get("management_ip", ""),
        subnet_mask=esxi.get("subnet_mask", ""),
        gateway=esxi.get("gateway", ""),
        dns_servers=esxi.get("dns_servers", []),
        root_password=esxi.get("root_password", ""),
    )


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
    storage_review = build_storage_review_context(cfg)
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
            "summary": "Apply the planned iLO network, hostname, and hardening settings.",
            "review_href": "/ilo",
        },
        "esxi": {
            "name": "ESXi",
            "target": cfg["esxi"].get("management_ip", "") or cfg.get("ip_plan", {}).get("esxi", "") or "Not set",
            "summary": "Use the saved ESXi setup values and generated install inputs.",
            "review_href": "/esxi",
        },
        "windows": {
            "name": "Windows",
            "target": cfg["windows"].get("ip_address", "") or cfg.get("ip_plan", {}).get("windows", "") or "Not set",
            "summary": "Use the saved Windows VM name, network plan, and admin settings.",
            "review_href": "/windows",
        },
        "qnap": {
            "name": "QNAP",
            "target": cfg["qnap"].get("ip", "") or cfg.get("ip_plan", {}).get("qnap", "") or "Not set",
            "summary": "Use the saved QNAP hostname and credential settings.",
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
            "summary": "Apply the exact approved storage artifact if storage is included in this run.",
            "review_href": "/storage#storage-approval-actions" if storage_review.get("approved") else "/storage#storage-review-start",
        },
    }

    def stage_entry(key: str, included: bool) -> dict:
        meta = components[key]
        summary = meta["summary"]
        if key == "storage":
            if not storage_review.get("include_in_ilo_run"):
                summary = "Storage will be skipped in this run."
            elif storage_validation_error:
                summary = f"Storage setup is blocked until it is reviewed again: {storage_validation_error}"
            elif storage_review.get("approved") and not storage_review.get("stale"):
                plan_summary = storage_review.get("approval", {}).get("plan_summary", {}) or {}
                mode = str(plan_summary.get("mode") or "").lower()
                if "wipe" in mode or "rebuild" in mode:
                    summary = "Storage will be erased and rebuilt using the approved layout."
                elif plan_summary.get("os_bays") or plan_summary.get("data_bays"):
                    summary = "Storage will be set up using the approved layout."
                else:
                    summary = "Storage will be checked against the approved layout."
                if storage_review.get("approval", {}).get("reboot_expected"):
                    summary += " Restart required."
        return {
            "key": key,
            "name": meta["name"],
            "target": meta["target"],
            "included": included,
            "summary": summary,
            "review_href": meta["review_href"],
        }

    included_stages = []
    if scope == "included":
        included = cfg.get("included", {})
        lines.append("Will act on all included components in this kit:")
        for key in ["ilo", "esxi", "windows", "qnap", "iosafe", "cisco_switch"]:
            if included.get(key):
                lines.append(f"- {components[key]['name']} -> {components[key]['target']}")
            included_stages.append(stage_entry(key, bool(included.get(key))))
        storage_included = bool(included.get("storage"))
        if storage_included:
            lines.append(f"- Storage plan -> {'approved exact artifact' if storage_execution.get('included') else 'not ready'}")
        included_stages.append(stage_entry("storage", storage_included))
    else:
        lines.append(f"Will act only on stage: {scope}")
        if scope == "ilo":
            lines.append(f"- Storage included in iLO run: {'Yes' if storage_review.get('include_in_ilo_run') else 'No'}")
            included_stages.append(stage_entry("ilo", True))
            included_stages.append(stage_entry("storage", bool(storage_review.get("include_in_ilo_run"))))
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
    summary_items = [
        {"label": "Run type", "value": "Full kit run" if scope == "included" else f"Single stage: {components.get(scope, {'name': scope}).get('name', scope)}"},
        {"label": "Selected kit", "value": cfg.get("site", {}).get("name", "") or "Unknown"},
        {"label": "Storage in run", "value": "Yes" if (scope == "included" and cfg.get("included", {}).get("storage")) or (scope == "ilo" and storage_review.get("include_in_ilo_run")) else "No"},
        {"label": "Restart expected", "value": "Yes" if storage_review.get("approval", {}).get("reboot_expected") and any(stage["key"] == "storage" and stage["included"] for stage in included_stages) else "No"},
    ]
    warning_points = [
        "Review the selected targets, credentials, and included stages before starting.",
        "This run can reboot equipment and apply destructive changes.",
    ]
    if storage_validation_error:
        warning_points.append(f"Storage is blocked right now: {storage_validation_error}")
    return {
        "scope": scope,
        "summary_items": summary_items,
        "stages": included_stages,
        "warning_title": "Review before you start",
        "warning_points": warning_points,
        "detail_text": "\n".join(lines),
    }


def get_steps_for_scope(cfg: dict, scope: str):
    if scope == "esxi":
        return [
            "Validate ESXi config",
            "Generate KS.CFG",
            "Prepare ISO patch inputs",
            "Validate install target settings",
            "Ready for real ESXi actions",
        ]
    if scope == "windows":
        return [
            "Validate Windows config",
            "Validate network plan",
            "Prepare unattended settings",
            "Validate VM/build target",
            "Ready for real Windows actions",
        ]
    if scope == "qnap":
        return [
            "Validate QNAP config",
            "Validate target IP",
            "Prepare storage settings",
            "Validate credentials",
            "Ready for real QNAP actions",
        ]
    if scope == "iosafe":
        return [
            "Validate ioSafe config",
            "Validate target IP",
            "Prepare storage settings",
            "Validate credentials",
            "Ready for real ioSafe actions",
        ]
    if scope == "cisco_switch":
        return [
            "Validate switch config",
            "Validate management IP",
            "Prepare switch template",
            "Validate credentials",
            "Ready for real switch actions",
        ]
    if scope == "included":
        steps = ["Validate included kit scope"]
        included = cfg.get("included", {})
        if included.get("storage"):
            steps.append("Stage approved storage plan")
        if included.get("ilo"):
            steps.append("Stage iLO actions")
        if included.get("esxi"):
            steps.append("Stage ESXi actions")
        if included.get("windows"):
            steps.append("Stage Windows actions")
        if included.get("qnap"):
            steps.append("Stage QNAP actions")
        if included.get("iosafe"):
            steps.append("Stage ioSafe actions")
        if included.get("cisco_switch"):
            steps.append("Stage Cisco switch actions")
        steps.append("Ready for real included-kit execution")
        return steps
    return ["Unknown scope"]


def validate_execution_scope(cfg: dict, scope: str) -> None:
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
    job["status"] = status
    job["current_stage"] = current_stage
    job["completed_steps"] = completed
    job["total_steps"] = total
    job["progress_percent"] = progress_percent if progress_percent is not None else (int((completed / total) * 100) if total else 0)
    job["logs"].append(log_line)
    save_job(kit_name, job)


def initialize_background_job(kit_name: str, scope: str):
    save_job(
        kit_name,
        {
            "status": "Starting",
            "scope": scope,
            "current_stage": "Queued",
            "progress_percent": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "logs": [f"[QUEUED] Execution requested for scope: {scope}"],
        },
    )


def append_job_history_snapshot(cfg: dict, scope: str):
    kit_name = cfg["site"]["name"]
    finished_job = load_job(kit_name)
    logs = finished_job.get("logs", [])
    issue_lines = [
        line for line in logs
        if "[FAILED]" in line or "[SKIP" in line or "[ERROR]" in line or "[WARN]" in line
    ]
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
            "config_summary": build_history_config_summary(cfg, scope),
        },
    )


def execute_job_in_background(cfg: dict, scope: str):
    kit_name = cfg["site"]["name"]
    try:
        run_job_simulation(cfg, scope)
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


def run_ilo_real(cfg: dict):
    kit_name = cfg["site"]["name"]
    ilo_cfg = cfg.get("ilo", {})
    snmp_cfg = cfg.get("shared_snmp", {})
    shared_dns = [x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x and x.strip()]
    storage_execution = validate_storage_ready_for_ilo_run(cfg)

    total = 14 if storage_execution.get("included") else 13
    job = {
        "status": "Running",
        "scope": "ilo",
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total,
        "logs": [],
    }
    save_job(kit_name, job)

    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    username = ilo_cfg.get("username", "").strip()
    password = ilo_cfg.get("password", "")
    desired_hostname = ilo_cfg.get("hostname", "").strip()
    target_ip = (ilo_cfg.get("target_ip") or "").strip()
    desired_gateway = (ilo_cfg.get("gateway") or "").strip()
    desired_subnet_mask = (ilo_cfg.get("subnet_mask") or "").strip()
    desired_auth_protocol = snmp_cfg.get("v3_auth_protocol", "SHA")
    desired_priv_protocol = snmp_cfg.get("v3_priv_protocol", "AES")
    config_changes_attempted = False
    config_changes_succeeded = True

    if not host or not username or not password:
        update_job(kit_name, job, "Failed", "Validation failed", 0, total, "[FAILED] Missing iLO host, username, or password.")
        return

    try:
        update_job(kit_name, job, "Running", "Validate configuration", 0, total, f"[RUNNING] Validating iLO config for {host}")
        update_job(
            kit_name,
            job,
            "Running",
            "Validate configuration",
            0,
            total,
            (
                f"[CONFIG] login_ip={host} | target_ip={target_ip or '(unchanged)'} | "
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
                f"snmp_v3_user={snmp_cfg.get('v3_username', '') or '(none)'} | "
                f"auth={desired_auth_protocol} | priv={desired_priv_protocol}"
            ),
        )
        step_offset = 0
        if storage_execution.get("included"):
            plan_summary = storage_execution.get("plan_summary", {}) or {}
            update_job(
                kit_name,
                job,
                "Running",
                "Load approved storage plan",
                1,
                total,
                (
                    f"[RUNNING] Using approved storage discovery artifact {storage_execution.get('discovery_raw_path')} "
                    f"and approved plan artifact {storage_execution.get('plan_path')}."
                ),
            )
            update_job(
                kit_name,
                job,
                "Running",
                "Load approved storage plan",
                1,
                total,
                (
                    f"[OK] Approved storage plan loaded into the iLO run context | "
                    f"controller={plan_summary.get('controller') or '(unknown)'} | "
                    f"os_bays={plan_summary.get('os_bays') or '(none)'} | "
                    f"data_bays={plan_summary.get('data_bays') or '(none)'} | "
                    f"spare_bay={plan_summary.get('spare_bay') or '(none)'} | "
                    f"reboot_expected={storage_execution.get('reboot_expected')}"
                ),
            )
            step_offset = 1
        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))

        update_job(kit_name, job, "Running", "Connect to Redfish", 1 + step_offset, total, f"[RUNNING] Connecting to https://{host}/redfish/v1/")
        summary = client.get_summary()
        update_job(kit_name, job, "Running", "Read service root", 2 + step_offset, total, f"[OK] Redfish version: {summary.get('redfish_version', '')}")

        update_job(
            kit_name,
            job,
            "Running",
            "Read system inventory",
            3 + step_offset,
            total,
            f"[OK] System: {summary.get('system_manufacturer', '')} {summary.get('system_model', '')} | Power: {summary.get('power_state', '')}"
        )

        try:
            iface = client.get_active_manager_interface()
            update_job(
                kit_name,
                job,
                "Running",
                "Inspect network state",
                4 + step_offset,
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
                4 + step_offset,
                total,
                f"[SKIP/INFO] Could not read active interface details: {e}"
            )

        if target_ip and desired_subnet_mask and desired_gateway:
            config_changes_attempted = True
            try:
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply static IPv4",
                    5 + step_offset,
                    total,
                    f"[RUNNING] Disabling DHCPv4 and setting static IPv4 address={target_ip} subnet_mask={desired_subnet_mask} gateway={desired_gateway}"
                )
                ip_result = client.set_static_ipv4_best_effort(
                    address=target_ip,
                    subnet_mask=desired_subnet_mask,
                    gateway=desired_gateway,
                )
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Verify static IPv4",
                    6 + step_offset,
                    total,
                    (
                        f"[OK] Static IPv4 applied via {', '.join(ip_result.get('applied_keys', []))} | "
                        f"before_dhcpv4={ip_result.get('before_dhcpv4')} | after_dhcpv4={ip_result.get('after_dhcpv4')} | "
                        f"before_ipv4={ip_result.get('before_ipv4_addresses') or ip_result.get('before_static_addresses')} | "
                        f"after_ipv4={ip_result.get('after_ipv4_addresses') or ip_result.get('after_static_addresses')}"
                    ),
                )
            except Exception as e:
                config_changes_succeeded = False
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply static IPv4",
                    6 + step_offset,
                    total,
                    f"[FAILED] Static IPv4 update not applied: {e}"
                )
        else:
            update_job(
                kit_name,
                job,
                "Running",
                "Skip static IPv4",
                6 + step_offset,
                total,
                "[SKIP] Missing target IP, subnet mask, or gateway for static IPv4 update."
            )

        if desired_hostname:
            config_changes_attempted = True
            update_job(
                kit_name,
                job,
                "Running",
                "Apply iLO hostname",
                7 + step_offset,
                total,
                f"[RUNNING] Attempting to set iLO hostname to: {desired_hostname}"
            )
            result = client.set_hostname_best_effort(desired_hostname)
            update_job(
                kit_name,
                job,
                "Running",
                "Verify iLO hostname",
                8 + step_offset,
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
                8 + step_offset,
                total,
                "[SKIP] No desired iLO hostname configured."
            )

        if shared_dns:
            config_changes_attempted = True
            try:
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply DNS",
                    9 + step_offset,
                    total,
                    f"[RUNNING] Applying DNS servers to active iLO interface: {', '.join(shared_dns)}"
                )
                dns_result = client.set_dns_servers_best_effort(shared_dns)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Verify DNS",
                    10 + step_offset,
                    total,
                    f"[OK] DNS applied via {', '.join(dns_result.get('applied_keys', []))} | before={dns_result.get('before_static')} | after={dns_result.get('after_static')}"
                )
            except Exception as e:
                config_changes_succeeded = False
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply DNS",
                    10 + step_offset,
                    total,
                    f"[SKIP/INFO] DNS write not applied: {e}"
                )
        else:
            update_job(
                kit_name,
                job,
                "Running",
                "Skip DNS",
                10 + step_offset,
                total,
                "[SKIP] No shared DNS servers configured."
            )

        try:
            update_job(
                kit_name,
                job,
                "Running",
                "Disable IPv6",
                11 + step_offset,
                total,
                "[RUNNING] Attempting to disable IPv6 where supported"
            )
            ipv6_result = client.disable_ipv6_best_effort()
            update_job(
                kit_name,
                job,
                "Running",
                "Disable IPv6",
                11 + step_offset,
                total,
                f"[OK] IPv6 hardening via {ipv6_result.get('method')} at {ipv6_result.get('path')}"
            )
        except Exception as e:
            update_job(
                kit_name,
                job,
                "Running",
                "Disable IPv6",
                11 + step_offset,
                total,
                f"[SKIP/INFO] IPv6 hardening not applied: {e}"
            )

        try:
            config_changes_attempted = True
            update_job(
                kit_name,
                job,
                "Running",
                "Harden SNMP",
                12 + step_offset,
                total,
                "[RUNNING] Enabling SNMP, forcing SNMPv3-only where supported, disabling SNMPv1 where supported"
            )
            snmp_result = client.harden_snmp_best_effort(
                v3_username=snmp_cfg.get("v3_username", ""),
                v3_auth_protocol=snmp_cfg.get("v3_auth_protocol", "SHA"),
                v3_auth_password=snmp_cfg.get("v3_auth_password", ""),
                v3_priv_protocol=snmp_cfg.get("v3_priv_protocol", "AES"),
                v3_priv_password=snmp_cfg.get("v3_priv_password", ""),
            )
            update_job(
                kit_name,
                job,
                "Running",
                "Harden SNMP",
                12 + step_offset,
                total,
                f"[OK] SNMP hardening applied. Keys touched: {', '.join(snmp_result.get('applied_keys', []))}"
            )
        except Exception as e:
            config_changes_succeeded = False
            update_job(
                kit_name,
                job,
                "Running",
                "Harden SNMP",
                12 + step_offset,
                total,
                f"[SKIP/INFO] SNMP hardening not fully applied: {e}"
            )

        if config_changes_attempted and config_changes_succeeded:
            try:
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Reset iLO",
                    13 + step_offset,
                    total,
                    "[RUNNING] All requested config changes succeeded. Restarting iLO to apply them cleanly."
                )
                reset_result = client.manager_reset_best_effort()
                update_job(
                    kit_name,
                    job,
                    "Completed",
                    "Finished",
                    13 + step_offset,
                    total,
                    f"[DONE] Real iLO automation finished. iLO reset requested via {reset_result.get('path')} ({reset_result.get('reset_type')})."
                )
            except Exception as e:
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Reset iLO",
                    13 + step_offset,
                    total,
                    f"[FAILED] Config changes succeeded but iLO reset could not be requested: {e}"
                )
        else:
            reason = "No config changes were requested."
            if config_changes_attempted and not config_changes_succeeded:
                reason = "One or more config changes did not succeed."
            update_job(
                kit_name,
                job,
                "Completed",
                "Finished",
                13 + step_offset,
                total,
                f"[DONE] Real iLO automation finished without iLO reset. {reason}"
            )
    except ILOError as e:
        update_job(kit_name, job, "Failed", "iLO error", job.get("completed_steps", 0), total, f"[FAILED] {e}")
    except Exception as e:
        update_job(kit_name, job, "Failed", "Unexpected error", job.get("completed_steps", 0), total, f"[FAILED] Unexpected error: {e}")

def run_job_simulation(cfg: dict, scope: str):
    if scope == "ilo":
        run_ilo_real(cfg)
        return

    kit_name = cfg["site"]["name"]
    steps = get_steps_for_scope(cfg, scope)
    total = len(steps)
    job = {
        "status": "Running",
        "scope": scope,
        "current_stage": "",
        "progress_percent": 0,
        "completed_steps": 0,
        "total_steps": total,
        "logs": [],
    }
    save_job(kit_name, job)

    for idx, step in enumerate(steps, start=1):
        job["current_stage"] = step
        job["completed_steps"] = idx - 1
        job["progress_percent"] = int(((idx - 1) / total) * 100)
        job["logs"].append(f"[RUNNING] {step}")
        save_job(kit_name, job)
        time.sleep(0.2)

    job["status"] = "Completed"
    job["current_stage"] = "Finished"
    job["completed_steps"] = total
    job["progress_percent"] = 100
    job["logs"].append("[DONE] Execution path completed in safety mode.")
    save_job(kit_name, job)


def render_page(
    request: Request,
    cfg: dict,
    active_page: str = "dashboard",
    message: str | None = None,
    ks_result: str | None = None,
    ks_content: str | None = None,
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
    storage_workflow_state = load_storage_workflow_state(storage_apply_paths)
    storage_review = build_storage_review_context(cfg)
    storage_target = resolve_storage_target_host(cfg)
    storage_credentials = resolve_storage_target_credentials(cfg)
    storage_execution_status = build_storage_execution_status(cfg)
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
        elif ks_result:
            tone = "ready" if not ks_result.lower().startswith("failed") else "pending"
            action_feedback = build_action_feedback(
                "Kickstart result",
                ks_result,
                tone=tone,
                status_label="Ready" if tone == "ready" else "Warning",
                details=["The full kickstart content is available below."] if ks_content else [],
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
        "ks_result": ks_result,
        "ks_content": ks_content,
        "error_message": error_message,
        "execution_preview": execution_preview,
        "execution_review": execution_review,
        "confirm_scope": confirm_scope,
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
        "ilo_inclusion": ilo_inclusion,
        "esxi_inclusion": esxi_inclusion,
        "windows_inclusion": windows_inclusion,
        "qnap_inclusion": qnap_inclusion,
        "job": job,
        "history": history,
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
):
    cfg = load_kit_config()
    storage_cfg = ensure_storage_config(cfg)
    storage_cfg["target_host_override"] = storage_target_host.strip()
    storage_cfg["username"] = storage_username.strip()
    storage_cfg["password"] = storage_password
    refresh_storage_approval_from_saved_state(cfg)
    save_kit_config(cfg)
    target = resolve_storage_target_host(cfg)
    return render_page(
        request,
        cfg,
        active_page=return_page,
        message=(
            f"Saved storage target settings. Storage / RAID will use {target.get('resolved') or 'no resolved host'} "
            f"from {target.get('source') or 'no source'}."
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
    cfg["site"]["name"] = sanitize_kit_name(site_name)
    cfg["shared_network"]["subnet"] = shared_subnet
    cfg["shared_network"]["dns_servers"] = [dns1, dns2, dns3, dns4]
    cfg["shared_snmp"] = {
        "v3_username": snmp_v3_username,
        "v3_auth_protocol": snmp_v3_auth_protocol,
        "v3_auth_password": snmp_v3_auth_password,
        "v3_priv_protocol": snmp_v3_priv_protocol,
        "v3_priv_password": snmp_v3_priv_password,
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
    included_ilo: str | None = Form(None),
):
    cfg = load_kit_config()
    cfg["ilo"]["current_ip"] = ilo_current_ip.strip()
    cfg["ilo"]["host"] = cfg["ilo"]["current_ip"]
    if ilo_target_ip.strip():
        cfg["ilo"]["target_ip"] = ilo_target_ip.strip()
        cfg["ip_plan"]["ilo"] = ilo_target_ip.strip()
    cfg["ilo"]["gateway"] = ilo_gateway.strip()
    cfg["ilo"]["hostname"] = ilo_hostname
    cfg["ilo"]["username"] = ilo_username
    cfg["ilo"]["password"] = ilo_password
    cfg["included"]["ilo"] = included_ilo == "on"
    cfg = apply_ip_plan(cfg)
    save_kit_config(cfg)
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
            links=[{"label": "Review run prep", "href": "/execution"}],
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


@app.post("/generate-ks", response_class=HTMLResponse)
async def generate_ks(request: Request, return_page: str = Form("configuration")):
    cfg = load_kit_config()
    try:
        ks_content = render_ks_cfg(cfg)
        with open(KS_OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(ks_content)
        result = f"KS.CFG generated successfully at {KS_OUTPUT_PATH}"
    except Exception as e:
        ks_content = None
        result = f"Failed to generate KS.CFG: {e}"

    return render_page(request, cfg, active_page=return_page, ks_result=result, ks_content=ks_content)


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


@app.post("/fetch-current-ilo-config", response_class=HTMLResponse)
async def fetch_current_ilo_config(request: Request, return_page: str = Form("configs")):
    return await export_ilo_inventory(request, return_page)


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


@app.post("/open-exports-folder", response_class=HTMLResponse)
async def open_exports_folder(request: Request, return_page: str = Form("configs")):
    cfg = load_kit_config()
    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "Exports folder opened",
            "Showing the current exports folder listing for quick review.",
            tone="ready",
            outcomes=[f"Folder: {ILO_LIVE_EXPORT_DIR}"],
        ),
        config_view_title="Exports Folder",
        config_view_content=render_exports_folder_listing(ILO_LIVE_EXPORT_DIR),
    )


@app.post("/read-current-storage", response_class=HTMLResponse)
async def read_current_storage(
    request: Request,
    return_page: str = Form("storage"),
    deep_smart_storage_scan: str | None = Form(None),
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
        discovery = client.get_storage_discovery(deep_smart_storage_scan=deep_smart_storage_scan == "on")
        export_paths = export_storage_discovery_snapshot(cfg, discovery, host=host)
        update_storage_latest_state(cfg, discovery=discovery, discovery_paths=export_paths)
        save_kit_config(cfg)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Current storage read complete",
                "Read the current storage layout and refreshed the latest discovery snapshot.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    f"Source: {storage_target.get('source')}",
                    f"Run folder: {export_paths['directory']}",
                ],
                links=[{"label": "Review storage setup", "href": "/storage#storage-approval-status"}],
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
        plan = build_raid_plan(discovery, discovery_paths)
        plan_paths = export_raid_plan_snapshot(cfg, plan, discovery_paths)
        update_storage_latest_state(cfg, discovery=discovery, discovery_paths=discovery_paths, plan=plan, plan_paths=plan_paths)
        save_kit_config(cfg)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Storage plan ready",
                "Built a read-only storage plan from the latest discovery snapshot.",
                tone="ready",
                outcomes=[
                    f"Discovery source: {discovery_paths['raw']}",
                    f"Plan saved to: {plan_paths['plan']}",
                ],
                links=[{"label": "Review storage setup", "href": "/storage#storage-approval-actions"}],
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
        return render_page(
            request,
            cfg,
            active_page=return_page,
            action_feedback=build_action_feedback(
                "Storage approved",
                "The exact storage plan is now approved for later use in the iLO run.",
                tone="ready",
                outcomes=[
                    f"Approved plan: {plan_paths['plan']}",
                    f"Target host: {cfg['storage']['approval'].get('host') or host}",
                    f"Included in iLO run: {'Yes' if cfg['storage']['include_in_ilo_run'] else 'No'}",
                ],
                links=[{"label": "Review run center", "href": "/execution"}],
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
    return render_page(
        request,
        cfg,
        active_page=return_page,
        action_feedback=build_action_feedback(
            "Storage marked for review again",
            "Removed the current storage approval so the plan can be reviewed again before a later run.",
            tone="ready",
            outcomes=["Storage will not be included in the iLO run until it is approved again."],
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


@app.post("/prepare-execute", response_class=HTMLResponse)
async def prepare_execute(request: Request, scope: str = Form(...), return_page: str = Form("execution")):
    cfg = load_kit_config()
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
    scope: str = Form(...),
    confirm_phrase: str = Form(""),
    confirm_checkbox: str | None = Form(None),
    return_page: str = Form("execution"),
):
    cfg = load_kit_config()
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
        target=execute_job_in_background,
        args=(cfg, scope),
        daemon=True,
    ).start()

    msg = "Execution started."
    if scope == "ilo":
        msg = "Real iLO automation started in the background. Check Job Monitor for live progress and logs."
    else:
        msg = f"Execution started for scope: {scope}."

    return render_page(request, cfg, active_page=return_page, message=msg)
