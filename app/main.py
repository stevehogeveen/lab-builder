from pathlib import Path
import asyncio
import ipaddress
import json
import re
import threading
import time
import yaml

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
    "execution": {
        "title": "Execution",
        "subtitle": "Run staged actions and monitor live job progress.",
    },
    "configuration": {
        "title": "Configuration",
        "subtitle": "Edit kit settings, network values, and credentials.",
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
        return {
            "login_ip": (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip(),
            "target_ip": (ilo_cfg.get("target_ip") or "").strip(),
            "hostname": (ilo_cfg.get("hostname") or "").strip(),
            "gateway": (cfg.get("ip_plan", {}).get("gateway") or "").strip(),
            "dns_servers": shared_dns,
            "snmp_v3_username": (snmp_cfg.get("v3_username") or "").strip(),
            "snmp_v3_auth_protocol": snmp_cfg.get("v3_auth_protocol", "SHA"),
            "snmp_v3_priv_protocol": snmp_cfg.get("v3_priv_protocol", "AES"),
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

    with open(summary_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(summary_payload, f, sort_keys=False)

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_payload, f, indent=2, sort_keys=False)

    return {
        "directory": export_dir,
        "summary": summary_path,
        "raw": raw_path,
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


def build_execution_preview(cfg: dict, scope: str):
    lines = [f"Execution scope: {scope}", ""]
    if scope == "included":
        included = cfg.get("included", {})
        lines.append("Will act on all included components in this kit:")
        if included.get("ilo"):
            lines.append(f"- iLO -> {cfg['ilo'].get('current_ip') or cfg['ilo'].get('host', '')}")
        if included.get("esxi"):
            lines.append(f"- ESXi -> {cfg['esxi'].get('management_ip', '')}")
        if included.get("windows"):
            lines.append(f"- Windows -> {cfg['windows'].get('ip_address', '')}")
        if included.get("qnap"):
            lines.append(f"- QNAP -> {cfg['qnap'].get('ip', '')}")
        if included.get("iosafe"):
            lines.append(f"- ioSafe -> {cfg['iosafe'].get('ip', '')}")
        if included.get("cisco_switch"):
            lines.append(f"- Cisco Switch -> {cfg['cisco_switch'].get('ip', '')}")
    else:
        lines.append(f"Will act only on stage: {scope}")
    lines.append("")
    lines.append("WARNING: This may reboot, reconfigure, overwrite, or otherwise make destructive changes.")
    return "\n".join(lines)


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


def update_job(kit_name: str, job: dict, status: str, current_stage: str, completed: int, total: int, log_line: str):
    job["status"] = status
    job["current_stage"] = current_stage
    job["completed_steps"] = completed
    job["total_steps"] = total
    job["progress_percent"] = int((completed / total) * 100) if total else 0
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

    total = 13
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
        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))

        update_job(kit_name, job, "Running", "Connect to Redfish", 1, total, f"[RUNNING] Connecting to https://{host}/redfish/v1/")
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

        if target_ip and desired_subnet_mask and desired_gateway:
            config_changes_attempted = True
            try:
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
            config_changes_attempted = True
            try:
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Apply DNS",
                    9,
                    total,
                    f"[RUNNING] Applying DNS servers to active iLO interface: {', '.join(shared_dns)}"
                )
                dns_result = client.set_dns_servers_best_effort(shared_dns)
                update_job(
                    kit_name,
                    job,
                    "Running",
                    "Verify DNS",
                    10,
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
                    10,
                    total,
                    f"[SKIP/INFO] DNS write not applied: {e}"
                )
        else:
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
            config_changes_attempted = True
            update_job(
                kit_name,
                job,
                "Running",
                "Harden SNMP",
                12,
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
                12,
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
                12,
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
                    13,
                    total,
                    "[RUNNING] All requested config changes succeeded. Restarting iLO to apply them cleanly."
                )
                reset_result = client.manager_reset_best_effort()
                update_job(
                    kit_name,
                    job,
                    "Completed",
                    "Finished",
                    13,
                    total,
                    f"[DONE] Real iLO automation finished. iLO reset requested via {reset_result.get('path')} ({reset_result.get('reset_type')})."
                )
            except Exception as e:
                update_job(
                    kit_name,
                    job,
                    "Failed",
                    "Reset iLO",
                    13,
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
                13,
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
    confirm_scope: str | None = None,
    config_view_title: str | None = None,
    config_view_content: str | None = None,
    live_inventory_status: dict | None = None,
    storage_discovery: dict | None = None,
    storage_export_paths: dict[str, Path] | None = None,
):
    active_page = normalize_page_name(active_page)
    page_meta = PAGE_META[active_page]
    job = load_job(cfg["site"]["name"])
    history = load_history(cfg["site"]["name"])

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
        "confirm_scope": confirm_scope,
        "config_view_title": config_view_title,
        "config_view_content": config_view_content,
        "live_inventory_status": live_inventory_status,
        "storage_discovery": storage_discovery,
        "storage_export_paths": storage_export_paths,
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


@app.get("/configuration", response_class=HTMLResponse)
async def configuration_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="configuration")


@app.get("/configs", response_class=HTMLResponse)
async def configs_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="configs")


@app.get("/storage", response_class=HTMLResponse)
async def storage_page(request: Request):
    cfg = load_kit_config()
    return render_page(request, cfg, active_page="storage")


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
            "dns_servers": [dns1, dns2, dns3, dns4],
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

    try:
        cfg = apply_ip_plan(cfg)
        save_kit_config(cfg)
        return render_page(request, cfg, active_page=return_page, message=f"Saved kit: {cfg['site']['name']}")
    except Exception as e:
        return render_page(request, cfg, active_page=return_page, error_message=f"Could not apply IP plan: {e}")


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
            message=(
                f"Captured live inventory from {host}. "
                f"Latest export path: {export_paths['summary'].parent}"
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
            message=(
                f"Captured live inventory from {host}. "
                f"Latest export path: {export_paths['summary'].parent}"
                f"{saved_msg}"
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
        message=f"Viewing latest live summary from {latest['directory']}",
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
        message=f"Exports folder: {ILO_LIVE_EXPORT_DIR}",
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
    ilo_cfg = cfg.get("ilo", {})
    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    username = (ilo_cfg.get("username") or "").strip()
    password = ilo_cfg.get("password", "")

    if not host or not username or not password:
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message="Storage discovery failed: missing current iLO IP, username, or password.",
        )

    try:
        client = ILOClient(ILOConfig(host=host, username=username, password=password, verify_tls=False, timeout=15))
        discovery = client.get_storage_discovery(deep_smart_storage_scan=deep_smart_storage_scan == "on")
        export_paths = export_storage_discovery_snapshot(cfg, discovery, host=host)
        return render_page(
            request,
            cfg,
            active_page=return_page,
            message=f"Read current storage from {host}. Export path: {export_paths['directory']}",
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
    preview = build_execution_preview(cfg, scope)
    return render_page(
        request,
        cfg,
        active_page=return_page,
        execution_preview=preview,
        confirm_scope=scope,
        error_message="WARNING: Execution may modify, reboot, overwrite, or reconfigure equipment. Review carefully before continuing.",
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

    if confirm_checkbox != "on":
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message="Execution blocked: you must check the confirmation box.",
            execution_preview=build_execution_preview(cfg, scope),
            confirm_scope=scope,
        )

    if confirm_phrase.strip().upper() != "EXECUTE":
        return render_page(
            request,
            cfg,
            active_page=return_page,
            error_message='Execution blocked: confirmation phrase must be exactly EXECUTE.',
            execution_preview=build_execution_preview(cfg, scope),
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
