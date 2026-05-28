from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
import glob
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Callable

import yaml

from app.cisco import CiscoSerialClient, CiscoSerialDiscovery, mask_secrets, serial_runtime_diagnostics
from app.core.secrets import resolve_secret
from app.ilo import ILOClient, ILOConfig


OVERNIGHT_MODES = ("discovery_only", "guided_setup", "full_overnight")
OVERNIGHT_DEFAULT_MODE = "discovery_only"
OVERNIGHT_ILO_HOST = "192.168.1.200"
HARDWARE_STOP_TIME = datetime_time(5, 30)
FINALIZATION_DEADLINE = datetime_time(6, 0)
HARDWARE_STOP_MARKER = "STOP_HARDWARE_WORK"
OVERNIGHT_COMMIT_MESSAGE = "Finalize overnight hardware run"
OVERNIGHT_COMMIT_PATHS = [
    "app/main.py",
    "app/overnight_run.py",
    "app/overnight_finalize.py",
    "scripts/finalize-overnight-run",
    "docs/HOWTO.md",
    "templates/partials/main_content.html",
    "templates/partials/sidebar.html",
    "templates/partials/pages/overnight_hardware.html",
    "tests/test_overnight_run.py",
]

DESTRUCTIVE_FLAG_DEFAULTS: dict[str, bool] = {
    "allow_power_cycle": False,
    "allow_virtual_media_mount": False,
    "allow_boot_override": False,
    "allow_esxi_install": False,
    "allow_cisco_config_changes": False,
    "allow_cisco_factory_reset": False,
    "allow_cisco_write_memory": False,
}

REQUIRED_ARTIFACTS = [
    "config-snapshot.yml",
    "live-job.log",
    "trace.yml",
    "summary.yml",
    "MORNING_READY.md",
    "ilo/discovery.json",
    "ilo/power-state-before.json",
    "ilo/boot-options.json",
    "ilo/virtual-media.json",
    "ilo/final-state.json",
    "cisco/console-detect.txt",
    "cisco/initial-session.txt",
    "cisco/show-version.txt",
    "cisco/running-config-before.txt",
    "cisco/setup-transcript.txt",
    "cisco/running-config-after.txt",
]

PENDING_ARTIFACT_VALUES = {"", "pending", "status: pending"}
SKIPPED_ARTIFACT_VALUES = {"skipped", "status: skipped"}
SAFE_SECRET_METADATA_KEYS = {"secret_scan_result", "secret_findings_count"}


def _local_wall_datetime(value: datetime | None = None) -> datetime:
    current = value or datetime.now().astimezone()
    if current.tzinfo is not None:
        current = current.astimezone()
    return current.replace(tzinfo=None)


def overnight_run_started_at(run_dir: Path | str) -> datetime | None:
    match = re.search(r"(\d{8})-(\d{6})", Path(run_dir).name)
    if not match:
        return None
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _overnight_finalization_date(run_started_at: datetime) -> date:
    started = _local_wall_datetime(run_started_at)
    if started.time() < FINALIZATION_DEADLINE:
        return started.date()
    return started.date() + timedelta(days=1)


def _overnight_cutoff_at(cutoff: datetime_time, current: datetime, run_started_at: datetime | None) -> datetime:
    cutoff_date = _overnight_finalization_date(run_started_at) if run_started_at else current.date()
    return datetime.combine(cutoff_date, cutoff)


def normalize_overnight_mode(value: str | None) -> str:
    mode = str(value or OVERNIGHT_DEFAULT_MODE).strip().lower()
    if mode not in OVERNIGHT_MODES:
        raise ValueError(f"Unsupported overnight hardware mode: {mode or '(empty)'}")
    return mode


def should_stop_hardware_actions(now: datetime | None = None, *, run_started_at: datetime | None = None) -> bool:
    current = _local_wall_datetime(now)
    return current >= _overnight_cutoff_at(HARDWARE_STOP_TIME, current, run_started_at)


def finalization_deadline_ok(now: datetime | None = None, *, run_started_at: datetime | None = None) -> bool:
    current = _local_wall_datetime(now)
    return current < _overnight_cutoff_at(FINALIZATION_DEADLINE, current, run_started_at)


def hardware_stop_marker_path(run_dir: Path) -> Path:
    return Path(run_dir) / HARDWARE_STOP_MARKER


def hardware_stop_requested(run_dir: Path) -> bool:
    return hardware_stop_marker_path(run_dir).exists()


def overnight_timestamp(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    return current.strftime("%Y%m%d-%H%M%S")


def safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class OvernightHardwareConfig:
    mode: str = OVERNIGHT_DEFAULT_MODE
    ilo_host: str = OVERNIGHT_ILO_HOST
    cisco_console_port: str = ""
    cisco_console_baud: int = 9600
    allow_auto_commit_push: bool = True
    destructive_flags: dict[str, bool] = field(default_factory=lambda: dict(DESTRUCTIVE_FLAG_DEFAULTS))

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None, cfg: dict[str, Any] | None = None) -> "OvernightHardwareConfig":
        source = dict(values or {})
        kit_cfg = dict(cfg or {})
        cisco_cfg = dict(kit_cfg.get("cisco_switch") or {})
        flags = dict(DESTRUCTIVE_FLAG_DEFAULTS)
        for key in flags:
            flags[key] = safe_bool(source.get(key, flags[key]))
        baud_raw = source.get("cisco_console_baud") or cisco_cfg.get("console_baud") or 9600
        try:
            baud = int(baud_raw)
        except (TypeError, ValueError):
            baud = 9600
        return cls(
            mode=normalize_overnight_mode(str(source.get("mode") or OVERNIGHT_DEFAULT_MODE)),
            ilo_host=str(source.get("ilo_host") or OVERNIGHT_ILO_HOST).strip() or OVERNIGHT_ILO_HOST,
            cisco_console_port=str(source.get("cisco_console_port") or cisco_cfg.get("console_port") or "").strip(),
            cisco_console_baud=baud,
            allow_auto_commit_push=safe_bool(source.get("allow_auto_commit_push", True)),
            destructive_flags=flags,
        )

    @property
    def requires_safety_confirmation(self) -> bool:
        return self.mode != OVERNIGHT_DEFAULT_MODE or any(self.destructive_flags.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ilo_host": self.ilo_host,
            "cisco_console_port": self.cisco_console_port,
            "cisco_console_baud": self.cisco_console_baud,
            "allow_auto_commit_push": self.allow_auto_commit_push,
            **dict(self.destructive_flags),
        }


def _redacted_secret_findings_for_report(value: Any) -> Any:
    if not value:
        return [] if isinstance(value, list) else value
    if not isinstance(value, list):
        return "[REDACTED]"
    redacted: list[dict[str, Any]] = []
    for finding in value:
        item = finding if isinstance(finding, dict) else {}
        redacted.append(
            {
                "path": str(item.get("path") or ""),
                "line": item.get("line") or "",
                "reason": str(item.get("reason") or ""),
                "excerpt": "[redacted possible secret]",
            }
        )
    return redacted


def redact_nested(value: Any, key_name: str = "") -> Any:
    lowered = str(key_name or "").lower()
    normalized_key = lowered.replace("-", "_")
    if normalized_key in SAFE_SECRET_METADATA_KEYS:
        return value
    if normalized_key == "secret_findings":
        return _redacted_secret_findings_for_report(value)
    secret_key = any(token in lowered for token in ("password", "secret", "token", "authorization", "api_key", "apikey", "community", "passphrase"))
    if secret_key and value not in (None, "", [], {}):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(key): redact_nested(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_nested(item, key_name) for item in value]
    return value


def _redacted_secret_excerpt(line: str) -> str:
    if not str(line or "").strip():
        return "[redacted possible secret]"
    return "[redacted possible secret]"


class OvernightArtifactWriter:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.events: list[dict[str, Any]] = []
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "ilo").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "cisco").mkdir(parents=True, exist_ok=True)

    @property
    def live_log_path(self) -> Path:
        return self.run_dir / "live-job.log"

    @property
    def trace_path(self) -> Path:
        return self.run_dir / "trace.yml"

    @property
    def summary_path(self) -> Path:
        return self.run_dir / "summary.yml"

    @property
    def morning_report_path(self) -> Path:
        return self.run_dir / "MORNING_READY.md"

    def artifact_path(self, relative: str) -> Path:
        return self.run_dir / relative

    def initialize_placeholders(self) -> None:
        for relative in REQUIRED_ARTIFACTS:
            path = self.artifact_path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                continue
            if path.suffix == ".json":
                path.write_text(json.dumps({"status": "pending"}, indent=2) + "\n", encoding="utf-8")
            elif path.suffix in {".yml", ".yaml"}:
                path.write_text(yaml.safe_dump({"status": "pending"}, sort_keys=False), encoding="utf-8")
            elif path.name == "MORNING_READY.md":
                path.write_text("# Morning Ready\n\nStatus: pending\n", encoding="utf-8")
            else:
                path.write_text("pending\n", encoding="utf-8")

    def write_config_snapshot(self, cfg: dict[str, Any], run_config: OvernightHardwareConfig) -> None:
        payload = {
            "captured_at": datetime.now().astimezone().isoformat(),
            "overnight_hardware": run_config.to_dict(),
            "kit_config": redact_nested(cfg),
        }
        self.write_yaml("config-snapshot.yml", payload)

    def write_json(self, relative: str, payload: Any) -> None:
        path = self.artifact_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(redact_nested(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def write_text(self, relative: str, text: str) -> None:
        path = self.artifact_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or ""), encoding="utf-8")

    def write_yaml(self, relative: str, payload: Any) -> None:
        path = self.artifact_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(redact_nested(payload), sort_keys=False), encoding="utf-8")

    def log(self, message: str) -> None:
        line = f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        with self.live_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def trace(self, *, stage: str, status: str, progress: int, message: str) -> dict[str, Any]:
        event = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "stage": stage,
            "status": status,
            "progress": max(0, min(100, int(progress))),
            "message": str(message or ""),
        }
        self.events.append(event)
        self.write_yaml("trace.yml", {"events": self.events})
        self.log(f"{stage}: {status} - {message}")
        return event

    def write_summary(self, payload: dict[str, Any]) -> None:
        summary = {
            "run_folder": str(self.run_dir),
            "generated_at": datetime.now().astimezone().isoformat(),
            **dict(payload),
            "trace_event_count": len(self.events),
            "artifacts": [str(self.artifact_path(relative)) for relative in REQUIRED_ARTIFACTS],
        }
        self.write_yaml("summary.yml", summary)


def _artifact_placeholder_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "not_file"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable"
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered in PENDING_ARTIFACT_VALUES:
        return "pending"
    if lowered in SKIPPED_ARTIFACT_VALUES:
        return "skipped"
    first_line = next((line.strip().lower() for line in stripped.splitlines() if line.strip()), "")
    if first_line.endswith("skipped.") or first_line.endswith("skipped") or " discovery skipped" in first_line:
        return "skipped"
    if path.suffix == ".json":
        try:
            parsed = json.loads(stripped or "{}")
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            status = str(parsed.get("status") or "").lower()
            if status == "pending":
                return "pending"
            if status == "skipped":
                return "skipped"
    if path.suffix in {".yml", ".yaml"}:
        try:
            parsed = yaml.safe_load(stripped) if stripped else {}
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, dict):
            status = str(parsed.get("status") or "").lower()
            if status == "pending":
                return "pending"
            if status == "skipped":
                return "skipped"
    return "present"


def inspect_overnight_artifacts(run_dir: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    missing: list[str] = []
    pending: list[str] = []
    skipped: list[str] = []
    unreadable: list[str] = []
    for relative in REQUIRED_ARTIFACTS:
        path = Path(run_dir) / relative
        status = _artifact_placeholder_status(path)
        item = {
            "relative": relative,
            "path": str(path),
            "exists": path.exists(),
            "status": status,
        }
        items.append(item)
        if status == "missing":
            missing.append(relative)
        elif status == "pending":
            pending.append(relative)
        elif status == "skipped":
            skipped.append(relative)
        elif status in {"unreadable", "not_file"}:
            unreadable.append(relative)
    return {
        "run_folder": str(run_dir),
        "items": items,
        "missing": missing,
        "pending": pending,
        "skipped": skipped,
        "unreadable": unreadable,
        "ok": not missing and not pending and not skipped and not unreadable,
    }


def read_morning_report_status(path: Path) -> dict[str, str]:
    if not Path(path).exists():
        return {"status": "missing", "reason": "MORNING_READY.md is missing."}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"status": "unreadable", "reason": "MORNING_READY.md could not be read."}
    match = re.search(r"(?im)^Status:\s*(.+?)\s*$", text)
    status = match.group(1).strip() if match else "unknown"
    reason_section = ""
    if "## Needs Attention Reasons" in text:
        reason_section = text.split("## Needs Attention Reasons", 1)[1].split("\n## ", 1)[0]
    reasons = re.findall(r"(?m)^-\s+(.+)$", reason_section)
    return {"status": status, "reason": reasons[0] if reasons else ""}


def _parse_artifact_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_deadline_missed_reason(reason: str) -> bool:
    lowered = str(reason or "").lower()
    return "6:00 am finalization deadline was missed" in lowered


def _finalization_deadline_label(deadline_at: datetime | None) -> str:
    if deadline_at is None:
        return ""
    return deadline_at.strftime("%Y-%m-%d %H:%M local")


def reconcile_overnight_needs_attention_reasons(
    reasons: list[Any],
    *,
    run_dir: Path,
    generated_at: Any = "",
) -> tuple[list[str], dict[str, Any]]:
    normalized = [str(item) for item in reasons if str(item).strip()]
    started_at = overnight_run_started_at(run_dir)
    completed_at = _parse_artifact_datetime(generated_at)
    if started_at is None or completed_at is None:
        return normalized, {}

    local_completed_at = _local_wall_datetime(completed_at)
    deadline_at = _overnight_cutoff_at(FINALIZATION_DEADLINE, local_completed_at, started_at)
    before_deadline = finalization_deadline_ok(completed_at, run_started_at=started_at)
    if before_deadline:
        filtered = [reason for reason in normalized if not _is_deadline_missed_reason(reason)]
    else:
        filtered = normalized
    return filtered, {
        "completed_at": local_completed_at.strftime("%Y-%m-%d %H:%M local"),
        "deadline": _finalization_deadline_label(deadline_at),
        "status": "before_deadline" if before_deadline else "missed_deadline",
        "removed_stale_deadline_reason": before_deadline and len(filtered) != len(normalized),
    }


def create_overnight_run_dir(artifacts_root: Path, now: datetime | None = None) -> Path:
    root = Path(artifacts_root) / "runs" / "overnight"
    root.mkdir(parents=True, exist_ok=True)
    base = root / f"{overnight_timestamp(now)}-ilo-cisco"
    path = base
    suffix = 1
    while path.exists():
        suffix += 1
        path = Path(f"{base}-{suffix}")
    path.mkdir(parents=True, exist_ok=False)
    return path


def initialize_overnight_artifacts(
    cfg: dict[str, Any],
    run_config: OvernightHardwareConfig,
    artifacts_root: Path,
    now: datetime | None = None,
) -> OvernightArtifactWriter:
    writer = OvernightArtifactWriter(create_overnight_run_dir(artifacts_root, now=now))
    writer.initialize_placeholders()
    writer.write_config_snapshot(cfg, run_config)
    writer.trace(stage="initialize", status="completed", progress=3, message="Created overnight iLO and Cisco run folder.")
    return writer


def default_ilo_client_factory(*, host: str, username: str, password: str, timeout: int = 10) -> ILOClient:
    return ILOClient(ILOConfig(host=host, username=username, password=password, timeout=timeout, verify_tls=False))


def _ilo_failure_guidance(error: str) -> list[str]:
    lowered = str(error or "").lower()
    if any(token in lowered for token in ("401", "403", "unauthorized", "forbidden", "auth")):
        return [
            "Verify the saved iLO username and password or LAB_BUILDER_ILO_USERNAME/LAB_BUILDER_ILO_PASSWORD.",
            "Confirm the account has permission to read Redfish Manager and System resources.",
        ]
    if any(token in lowered for token in ("certificate", "ssl", "tls")):
        return [
            "Check whether the iLO TLS certificate changed or the host is presenting an unexpected certificate.",
            "Retry discovery only after confirming the saved target is the intended iLO.",
        ]
    if any(token in lowered for token in ("timed out", "timeout", "connection", "unreachable", "name or service")):
        return [
            "Confirm the iLO host is reachable from this workstation before starting another overnight run.",
            "Check cabling, IP address, gateway, and whether another maintenance window is using the controller.",
        ]
    if any(token in lowered for token in ("json", "decode", "redfish", "odata", "manager", "system")):
        return [
            "Open the raw iLO discovery artifact and verify the Redfish service root returned valid JSON.",
            "Confirm `/redfish/v1/Managers` and `/redfish/v1/Systems` are available on this iLO firmware.",
        ]
    return [
        "Review the raw iLO error in Debug Mode before retrying.",
        "Retry discovery only after confirming the target host and credentials are correct.",
    ]


def write_ilo_skipped_artifacts(writer: OvernightArtifactWriter, reason: str, *, now: datetime | None = None) -> None:
    current = now or datetime.now().astimezone()
    payload = {
        "ok": False,
        "status": "skipped",
        "reason": reason,
        "recorded_at": current.isoformat(),
        "next_steps": [
            "Start the overnight run before the hardware stop window if iLO discovery evidence is required.",
            "Use the finalizer only when no new hardware discovery should be attempted.",
        ],
    }
    for relative in (
        "ilo/discovery.json",
        "ilo/power-state-before.json",
        "ilo/boot-options.json",
        "ilo/virtual-media.json",
        "ilo/final-state.json",
    ):
        writer.write_json(relative, payload)


def collect_ilo_discovery(
    cfg: dict[str, Any],
    run_config: OvernightHardwareConfig,
    writer: OvernightArtifactWriter,
    *,
    client_factory: Callable[..., Any] = default_ilo_client_factory,
) -> dict[str, Any]:
    ilo_cfg = dict(cfg.get("ilo") or {})
    host = run_config.ilo_host or OVERNIGHT_ILO_HOST
    username = resolve_secret(str(ilo_cfg.get("username") or ""), env_name="LAB_BUILDER_ILO_USERNAME")
    password = resolve_secret(str(ilo_cfg.get("password") or ""), env_name="LAB_BUILDER_ILO_PASSWORD")
    if not username or not password:
        error = "iLO credentials are not available from saved kit config or LAB_BUILDER_ILO_USERNAME/LAB_BUILDER_ILO_PASSWORD."
        payload = {"ok": False, "host": host, "error": error, "next_steps": _ilo_failure_guidance(error)}
        writer.write_json("ilo/discovery.json", payload)
        writer.write_json("ilo/power-state-before.json", payload)
        writer.write_json("ilo/boot-options.json", payload)
        writer.write_json("ilo/virtual-media.json", payload)
        writer.write_json("ilo/final-state.json", payload)
        writer.trace(stage="ilo_discovery", status="blocked", progress=18, message=error)
        return payload

    writer.trace(stage="ilo_discovery", status="running", progress=12, message=f"Connecting to Redfish service root on https://{host}/redfish/v1/.")
    try:
        client = client_factory(host=host, username=username, password=password, timeout=10)
        service_root = client.get_service_root()
        managers = client.get_managers()
        systems = client.get_systems()
        manager_path = managers[0] if managers else ""
        system_path = systems[0] if systems else ""
        manager = client.get_manager(manager_path) if manager_path else {}
        system = client.get_system(system_path) if system_path else {}
        virtual_media = client.get_virtual_media(manager_path) if manager_path else []
        if hasattr(client, "collect_boot_option_inventory"):
            boot_options = client.collect_boot_option_inventory(system_path or None)
        else:
            boot_options = {"system_path": system_path, "boot": dict(system.get("Boot") or {})}
        final_state = client.get_summary() if hasattr(client, "get_summary") else {
            "manager_path": manager_path,
            "system_path": system_path,
            "power_state": system.get("PowerState", ""),
        }
        discovery = {
            "ok": True,
            "target_url": f"https://{host}/redfish/v1/",
            "service_root": service_root,
            "manager_paths": managers,
            "system_paths": systems,
            "manager": manager,
            "system": system,
        }
        writer.write_json("ilo/discovery.json", discovery)
        writer.write_json("ilo/power-state-before.json", {"system_path": system_path, "power_state": system.get("PowerState", ""), "system": system})
        writer.write_json("ilo/boot-options.json", boot_options)
        writer.write_json("ilo/virtual-media.json", {"manager_path": manager_path, "items": virtual_media})
        writer.write_json("ilo/final-state.json", final_state)
        writer.trace(stage="ilo_discovery", status="completed", progress=38, message=f"iLO discovery captured power={system.get('PowerState', 'unknown')}.")
        return discovery
    except Exception as exc:
        error = str(exc).splitlines()[0]
        payload = {"ok": False, "host": host, "error": error, "next_steps": _ilo_failure_guidance(error)}
        for relative in (
            "ilo/discovery.json",
            "ilo/power-state-before.json",
            "ilo/boot-options.json",
            "ilo/virtual-media.json",
            "ilo/final-state.json",
        ):
            writer.write_json(relative, payload)
        writer.trace(stage="ilo_discovery", status="failed", progress=38, message=f"iLO discovery failed: {error}")
        return payload


def likely_serial_devices() -> list[str]:
    devices: list[str] = []
    seen: set[str] = set()
    for pattern in ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        for path in sorted(glob.glob(pattern)):
            real = os.path.realpath(path)
            if real in seen:
                continue
            devices.append(path)
            seen.add(real)
    return devices


def _console_detect_text(diagnostics: dict[str, Any], candidates: list[dict[str, Any]], selected_port: str, selected_baud: int) -> str:
    lines = [
        "Cisco console detection",
        f"selected_port: {selected_port or '(none)'}",
        f"selected_baud: {selected_baud}",
        "likely_devices:",
    ]
    likely = list(diagnostics.get("ordered_ports") or []) or likely_serial_devices()
    lines.extend(f"- {item}" for item in likely)
    lines.append("probe_results:")
    for candidate in candidates:
        lines.append(
            "- "
            + " ".join(
                [
                    f"port={candidate.get('port') or ''}",
                    f"baud={candidate.get('baud') or ''}",
                    f"prompt={candidate.get('prompt_type') or ''}",
                    f"score={candidate.get('score') or 0}",
                    f"error={candidate.get('error') or ''}",
                ]
            ).strip()
        )
    lines.append("diagnostics:")
    lines.append(yaml.safe_dump(redact_nested(diagnostics), sort_keys=False))
    return "\n".join(lines).rstrip() + "\n"


def _cisco_console_guidance(error: str = "") -> list[str]:
    lowered = str(error or "").lower()
    if any(token in lowered for token in ("busy", "permission", "denied", "resource")):
        return [
            "Close any terminal session that is already using the Cisco console port.",
            "Check local serial device permissions for the app user.",
        ]
    if any(token in lowered for token in ("timeout", "timed out", "no prompt", "prompt")):
        return [
            "Verify the console cable is connected to the switch console port.",
            "Try the saved baud rate first, then check whether the device expects 115200 or 9600.",
        ]
    if any(token in lowered for token in ("baud", "framing", "decode")):
        return [
            "Confirm the Cisco console baud rate in the saved kit settings.",
            "Retry with the expected platform baud rate after preserving the transcript.",
        ]
    return [
        "Set a saved Cisco console port or plug in a USB serial adapter before the next run.",
        "Check Debug Mode for detected serial devices and raw console transcript evidence.",
    ]


def _cisco_guidance_text(reason: str, *, selected_port: str = "", selected_baud: int = 9600) -> str:
    lines = [
        "Cisco console discovery did not capture command output.",
        f"reason: {reason}",
        f"selected_port: {selected_port or '(none)'}",
        f"selected_baud: {selected_baud}",
        "next_steps:",
    ]
    lines.extend(f"- {item}" for item in _cisco_console_guidance(reason))
    return "\n".join(lines).rstrip() + "\n"


def write_cisco_skipped_artifacts(writer: OvernightArtifactWriter, reason: str, *, now: datetime | None = None) -> None:
    current = now or datetime.now().astimezone()
    text = "\n".join(
        [
            "Cisco console discovery skipped.",
            f"reason: {reason}",
            f"recorded_at: {current.isoformat()}",
            "next_steps:",
            "- Start the overnight run before the hardware stop window if Cisco evidence is required.",
            "- Keep this transcript as the raw record that no console connection was attempted.",
            "",
        ]
    )
    writer.write_text("cisco/console-detect.txt", text)
    writer.write_text("cisco/initial-session.txt", text)
    writer.write_text("cisco/show-version.txt", text)
    writer.write_text("cisco/running-config-before.txt", text)
    writer.write_text("cisco/setup-transcript.txt", text)
    writer.write_text("cisco/running-config-after.txt", text)


def collect_cisco_console_discovery(
    cfg: dict[str, Any],
    run_config: OvernightHardwareConfig,
    writer: OvernightArtifactWriter,
    *,
    diagnostics_fn: Callable[[], dict[str, Any]] = serial_runtime_diagnostics,
    discovery_factory: Callable[[], Any] = CiscoSerialDiscovery,
    client_factory: Callable[[str, int], Any] = CiscoSerialClient,
) -> dict[str, Any]:
    cisco_cfg = dict(cfg.get("cisco_switch") or {})
    selected_port = run_config.cisco_console_port or str(cisco_cfg.get("console_port") or "").strip()
    selected_baud = int(run_config.cisco_console_baud or cisco_cfg.get("console_baud") or 9600)
    writer.trace(stage="cisco_console", status="running", progress=44, message="Listing serial devices and preparing safe console reads.")
    diagnostics = diagnostics_fn()
    candidates: list[dict[str, Any]] = []
    if not selected_port:
        try:
            scanned = discovery_factory().scan()
            candidates = [item.as_dict(include_raw=True) if hasattr(item, "as_dict") else dict(item) for item in scanned]
            match = next((item for item in candidates if int(item.get("score") or 0) >= 50), None)
            if match:
                selected_port = str(match.get("port") or "")
                selected_baud = int(match.get("baud") or selected_baud)
        except Exception as exc:
            candidates = [{"error": str(exc).splitlines()[0]}]
    writer.write_text("cisco/console-detect.txt", _console_detect_text(diagnostics, candidates, selected_port, selected_baud))

    if not selected_port:
        error = "No saved or detected Cisco console port is available."
        payload = {"ok": False, "error": error, "diagnostics": diagnostics, "candidates": candidates}
        guidance = _cisco_guidance_text(error, selected_port=selected_port, selected_baud=selected_baud)
        writer.write_text("cisco/initial-session.txt", guidance)
        writer.write_text("cisco/show-version.txt", guidance)
        writer.write_text("cisco/running-config-before.txt", guidance)
        writer.write_text("cisco/setup-transcript.txt", guidance + "No setup actions executed because no console port was selected.\n")
        writer.write_text("cisco/running-config-after.txt", guidance)
        writer.trace(stage="cisco_console", status="blocked", progress=63, message=error)
        return payload

    try:
        with client_factory(selected_port, selected_baud) as client:
            prompt = client.read_prompt()
            terminal = client.run_command("terminal length 0", wait_seconds=1.0)
            show_version = client.run_command("show version", wait_seconds=5.0)
            running_config = client.run_command("show running-config", wait_seconds=8.0)
        writer.write_text("cisco/initial-session.txt", prompt + terminal)
        writer.write_text("cisco/show-version.txt", show_version)
        writer.write_text("cisco/running-config-before.txt", running_config)
        if run_config.destructive_flags.get("allow_cisco_config_changes"):
            transcript = "Cisco config changes are allowed by flag, but this overnight runner did not apply configuration changes in the safe discovery slice.\n"
        else:
            transcript = "No Cisco setup actions executed. allow_cisco_config_changes is false.\n"
        if not run_config.destructive_flags.get("allow_cisco_write_memory"):
            transcript += "No write memory command executed. allow_cisco_write_memory is false.\n"
        writer.write_text("cisco/setup-transcript.txt", transcript)
        writer.write_text("cisco/running-config-after.txt", running_config)
        writer.trace(stage="cisco_console", status="completed", progress=66, message=f"Cisco console read completed on {selected_port}.")
        return {"ok": True, "selected_port": selected_port, "selected_baud": selected_baud, "diagnostics": diagnostics, "candidates": candidates}
    except Exception as exc:
        error = str(exc).splitlines()[0]
        guidance = _cisco_guidance_text(error, selected_port=selected_port, selected_baud=selected_baud)
        writer.write_text("cisco/initial-session.txt", f"Console open/read failed: {error}\n\n{guidance}")
        writer.write_text("cisco/show-version.txt", guidance)
        writer.write_text("cisco/running-config-before.txt", guidance)
        writer.write_text("cisco/setup-transcript.txt", guidance + "No setup actions executed because console read failed.\n")
        writer.write_text("cisco/running-config-after.txt", guidance)
        writer.trace(stage="cisco_console", status="failed", progress=66, message=f"Cisco console read failed: {error}")
        return {"ok": False, "selected_port": selected_port, "selected_baud": selected_baud, "error": error, "diagnostics": diagnostics, "candidates": candidates}


@dataclass
class CommandCapture:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-4000:],
        }


@dataclass(frozen=True)
class FinalizationGitDecision:
    should_commit_push: bool
    notes: tuple[str, ...] = ()


def decide_finalization_git_action(
    *,
    allow_git: bool,
    tests_ok: bool,
    secret_findings_count: int,
    deadline_ok: bool,
) -> FinalizationGitDecision:
    notes: list[str] = []
    if secret_findings_count:
        notes.append("Possible secrets were found. Auto-commit and push were skipped.")
    if not tests_ok:
        notes.append("Tests or compileall failed. Auto-commit and push were skipped.")
    if not deadline_ok:
        notes.append("Auto-commit/push skipped because the 6:00 AM finalization deadline was missed.")
    if not allow_git:
        notes.append("Auto-commit/push disabled for this run.")
    return FinalizationGitDecision(
        should_commit_push=allow_git and tests_ok and secret_findings_count == 0 and deadline_ok,
        notes=tuple(notes),
    )


def run_command(command: list[str], *, cwd: Path, timeout: int = 1800) -> CommandCapture:
    try:
        proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False, timeout=timeout)
        return CommandCapture(command=list(command), returncode=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        return CommandCapture(command=list(command), returncode=124, stdout=str(exc.stdout or ""), stderr=f"Timed out after {timeout} seconds.")
    except Exception as exc:
        return CommandCapture(command=list(command), returncode=1, stderr=str(exc).splitlines()[0])


SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bauthorization\s*:\s*(?:basic|bearer|token)\s+\S+"),
    re.compile(r"(?i)\bx-auth-token\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|secret)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{12,}[\"']?\s*(?:$|[,#])"),
    re.compile(r"(?im)^\s*(?:enable\s+secret|username\s+\S+\s+privilege\s+\d+\s+secret|password\s+\S+)\b.+$"),
    re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def scan_text_for_secrets(text: str, *, path: str = "") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for number, line in enumerate(str(text or "").splitlines(), start=1):
        if "[REDACTED]" in line or "********" in line:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append({"path": path, "line": number, "reason": pattern.pattern, "excerpt": _redacted_secret_excerpt(line)})
                break
    return findings


def scan_paths_for_secrets(paths: list[Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in paths:
        item = Path(path)
        if item.is_dir():
            nested = [p for p in item.rglob("*") if p.is_file()]
            findings.extend(scan_paths_for_secrets(nested))
            continue
        if not item.exists() or not item.is_file():
            continue
        if item.stat().st_size > 2_000_000:
            continue
        try:
            text = item.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(scan_text_for_secrets(text, path=str(item)))
    return findings


def _command_dict(result: CommandCapture) -> dict[str, Any]:
    return result.as_dict()


def _parse_git_branch(status_stdout: str) -> str:
    if not status_stdout:
        return ""
    first_line = status_stdout.splitlines()[0]
    if not first_line.startswith("##"):
        return ""
    return first_line.replace("##", "", 1).split("...", 1)[0].strip()


def _existing_commit_paths(repo_root: Path, commit_paths: list[str]) -> list[str]:
    paths: list[str] = []
    for item in commit_paths:
        relative = str(item or "").strip()
        if not relative:
            continue
        if (Path(repo_root) / relative).exists():
            paths.append(relative)
    return paths


def request_hardware_stop(writer: OvernightArtifactWriter, *, now: datetime | None = None) -> Path:
    current = now or datetime.now().astimezone()
    marker = hardware_stop_marker_path(writer.run_dir)
    marker.write_text(
        yaml.safe_dump(
            {
                "requested_at": current.isoformat(),
                "reason": "finalization scheduler",
                "deadline": HARDWARE_STOP_TIME.strftime("%H:%M"),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    writer.trace(
        stage="hardware_stop",
        status="stopped",
        progress=70,
        message="Hardware stop marker written before finalization.",
    )
    return marker


def _write_morning_report(writer: OvernightArtifactWriter, payload: dict[str, Any]) -> None:
    reasons = list(payload.get("needs_attention_reasons") or [])
    generated_at = payload.get("generated_at") or datetime.now().astimezone().isoformat()
    lines = [
        "# Morning Ready",
        "",
        f"Status: {payload.get('status_label', 'Needs attention')}",
        f"Artifact folder: {payload.get('artifact_folder') or writer.run_dir}",
        f"Generated at: {generated_at}",
        "",
        "## Results",
        f"- Branch: {payload.get('branch') or 'unknown'}",
        f"- Commit: {payload.get('commit_sha') or 'not created'}",
        f"- Tests: {payload.get('test_result') or 'not run'}",
        f"- Compile: {payload.get('compile_result') or 'not run'}",
        f"- Push: {payload.get('push_result') or 'not run'}",
        f"- Secret scan: {payload.get('secret_scan_result') or 'not run'}",
        f"- Hardware stop marker: {payload.get('hardware_stop_marker') or 'not written'}",
    ]
    if payload.get("finalization_completed_at"):
        lines.append(f"- Finalization completed: {payload.get('finalization_completed_at')}")
    if payload.get("finalization_deadline"):
        lines.append(f"- Finalization deadline: {payload.get('finalization_deadline')}")
    if payload.get("finalization_timing"):
        lines.append(f"- Finalization timing: {payload.get('finalization_timing')}")
    if reasons:
        lines.extend(["", "## Needs Attention Reasons"])
        lines.extend(f"- {reason}" for reason in reasons)
    lines.extend(["", "## Notes"])
    notes = list(payload.get("notes") or [])
    if not notes:
        notes = ["No additional notes."]
    lines.extend(f"- {note}" for note in notes)
    if payload.get("commands"):
        lines.extend(["", "## Command Results"])
        for item in payload["commands"]:
            command = " ".join(item.get("command") or [])
            lines.append(f"- `{command}` -> {item.get('returncode')}")
    if payload.get("secret_findings"):
        lines.extend(["", "## Secret Findings"])
        for finding in payload["secret_findings"][:40]:
            lines.append(f"- {finding.get('path')}:{finding.get('line')} possible secret ({_redacted_secret_excerpt(str(finding.get('excerpt') or ''))})")
    artifact_health = dict(payload.get("artifact_health") or {})
    if artifact_health:
        lines.extend(["", "## Artifact Health"])
        any_issue = False
        for key, label in (("missing", "Missing"), ("pending", "Still pending"), ("skipped", "Skipped"), ("unreadable", "Unreadable")):
            values = list(artifact_health.get(key) or [])
            if values:
                any_issue = True
                lines.append(f"- {label}: {', '.join(values)}")
        if not any_issue:
            lines.append("- Required artifacts are present; no placeholders or skipped evidence remain.")
    if payload.get("git_status_before") or payload.get("git_status_after"):
        lines.extend(["", "## Git Status Before", "```text", str(payload.get("git_status_before") or "").rstrip(), "```"])
        lines.extend(["", "## Git Status After", "```text", str(payload.get("git_status_after") or "").rstrip(), "```"])
    writer.morning_report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def finalize_overnight_run(
    writer: OvernightArtifactWriter,
    *,
    repo_root: Path,
    run_tests: bool = True,
    allow_git: bool = True,
    commit_paths: list[str] | None = None,
    commit_message: str = OVERNIGHT_COMMIT_MESSAGE,
    python_executable: str | None = None,
    command_runner: Callable[[list[str], Path], CommandCapture] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    runner = command_runner or (lambda command, cwd: run_command(command, cwd=cwd))
    started_at = now or datetime.now().astimezone()
    run_started_at = overnight_run_started_at(writer.run_dir)
    stop_marker = request_hardware_stop(writer, now=started_at)
    writer.trace(stage="finalization", status="running", progress=72, message="Stopping hardware work and starting morning finalization.")
    commands: list[dict[str, Any]] = []
    notes: list[str] = []
    tests_ok = True
    compile_ok = True
    test_result = "not run"
    compile_result = "not run"
    artifact_health: dict[str, Any] = {}
    status_before = CommandCapture(["git", "status", "--short", "--branch"], 1, "", "not run")
    status_after = CommandCapture(["git", "status", "--short", "--branch"], 1, "", "not run")

    _write_morning_report(
        writer,
        {
            "status_label": "Needs attention",
            "branch": "unknown",
            "commit_sha": "",
            "test_result": "running" if run_tests else "not run",
            "compile_result": "pending" if run_tests else "not run",
            "push_result": "not run",
            "secret_scan_result": "pending",
            "artifact_folder": str(writer.run_dir),
            "hardware_stop_marker": str(stop_marker),
            "needs_attention_reasons": ["Finalization has started but has not completed yet."],
            "notes": ["This provisional report is overwritten when finalization completes."],
        },
    )

    def run(command: list[str]) -> CommandCapture:
        try:
            result = runner(command, Path(repo_root))
        except Exception as exc:
            result = CommandCapture(command=list(command), returncode=1, stdout="", stderr=str(exc).splitlines()[0])
        commands.append(_command_dict(result))
        return result

    python_cmd = python_executable or sys.executable or "python"
    status_before = run(["git", "status", "--short", "--branch"])
    branch = _parse_git_branch(status_before.stdout)

    focused_path = Path(repo_root) / "tests" / "test_overnight_run.py"
    if run_tests and focused_path.exists():
        focused = run([python_cmd, "-m", "pytest", "-q", "tests/test_overnight_run.py"])
        tests_ok = tests_ok and focused.ok
    elif run_tests:
        tests_ok = False
        notes.append("Focused overnight tests were not found.")

    if run_tests:
        full = run([python_cmd, "-m", "pytest", "-q"])
        compileall = run([python_cmd, "-m", "compileall", "app"])
        tests_ok = tests_ok and full.ok
        compile_ok = compileall.ok
        test_result = "passed" if tests_ok else "failed"
        compile_result = "passed" if compile_ok else "failed"

    secret_findings = scan_paths_for_secrets([writer.run_dir])
    secret_scan_result = "clean" if not secret_findings else f"blocked ({len(secret_findings)} finding(s))"
    decision_time = now or datetime.now().astimezone()
    local_decision_time = _local_wall_datetime(decision_time)
    deadline_at = _overnight_cutoff_at(FINALIZATION_DEADLINE, local_decision_time, run_started_at)
    deadline_ok = finalization_deadline_ok(decision_time, run_started_at=run_started_at)
    if should_stop_hardware_actions(started_at, run_started_at=run_started_at):
        notes.append("Hardware stop marker was written at or after 5:30 AM local time; verify no hardware action overran the stop window.")
    decision = decide_finalization_git_action(
        allow_git=allow_git,
        tests_ok=tests_ok and compile_ok,
        secret_findings_count=len(secret_findings),
        deadline_ok=deadline_ok,
    )
    notes.extend(decision.notes)

    commit_sha = ""
    push_result = "not run"
    git_ok = not allow_git
    if decision.should_commit_push:
        git_ok = False
        add_paths = _existing_commit_paths(Path(repo_root), commit_paths or OVERNIGHT_COMMIT_PATHS)
        if not add_paths:
            notes.append("No configured commit paths exist. Auto-commit and push were skipped.")
        else:
            add_result = run(["git", "add", *add_paths])
            staged_names = run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMRT"])
            staged_paths = [Path(repo_root) / line.strip() for line in staged_names.stdout.splitlines() if line.strip()]
            staged_findings = scan_paths_for_secrets(staged_paths)
            if staged_findings:
                secret_findings.extend(staged_findings)
                secret_scan_result = f"blocked ({len(secret_findings)} finding(s))"
                notes.append("Possible secrets were found in staged files. Auto-commit and push were skipped.")
            elif not add_result.ok:
                notes.append("git add failed. Auto-commit and push were skipped.")
            else:
                branch_result = run(["git", "branch", "--show-current"])
                branch_name = branch_result.stdout.strip() if branch_result.ok else branch
                if not staged_paths:
                    rev = run(["git", "rev-parse", "HEAD"])
                    commit_sha = rev.stdout.strip() if rev.ok else ""
                    if branch_name:
                        notes.append("No staged changes were present; pushing the current branch only.")
                        push = run(["git", "push", "origin", branch_name])
                        push_result = "pushed" if push.ok else f"failed ({push.returncode})"
                        git_ok = push.ok
                        if not push.ok:
                            notes.append("git push failed. Review command output in MORNING_READY.md.")
                    else:
                        notes.append("Could not determine the current branch. Auto-push was skipped.")
                else:
                    commit = run(["git", "commit", "-m", commit_message])
                    if commit.ok:
                        rev = run(["git", "rev-parse", "HEAD"])
                        commit_sha = rev.stdout.strip() if rev.ok else ""
                        if branch_name:
                            push = run(["git", "push", "origin", branch_name])
                            push_result = "pushed" if push.ok else f"failed ({push.returncode})"
                            git_ok = push.ok
                            if not push.ok:
                                notes.append("git push failed. Review command output in MORNING_READY.md.")
                        else:
                            notes.append("Could not determine the current branch. Auto-push was skipped.")
                    else:
                        notes.append("git commit failed. Auto-push was skipped.")

    status_after = run(["git", "status", "--short", "--branch"])
    if not branch:
        branch = _parse_git_branch(status_after.stdout)

    writer.write_summary({"status": "finalization_running", "finalization": {"status_label": "Running"}})
    artifact_health = inspect_overnight_artifacts(writer.run_dir)
    artifact_issues = bool(
        artifact_health.get("missing")
        or artifact_health.get("pending")
        or artifact_health.get("skipped")
        or artifact_health.get("unreadable")
    )
    needs_attention_reasons: list[str] = []
    if not tests_ok:
        needs_attention_reasons.append("Pytest did not pass; review command output in MORNING_READY.md.")
    if not compile_ok:
        needs_attention_reasons.append("compileall did not pass; review command output in MORNING_READY.md.")
    if secret_findings:
        needs_attention_reasons.append("Possible secrets were found; auto-commit and push were blocked.")
    if not deadline_ok:
        needs_attention_reasons.append("The 6:00 AM finalization deadline was missed.")
    if artifact_health.get("missing"):
        needs_attention_reasons.append("Expected artifacts are missing: " + ", ".join(artifact_health["missing"]))
    if artifact_health.get("pending"):
        needs_attention_reasons.append("Expected artifacts still contain placeholders: " + ", ".join(artifact_health["pending"]))
    if artifact_health.get("skipped"):
        needs_attention_reasons.append("Expected artifacts were skipped: " + ", ".join(artifact_health["skipped"]))
    if artifact_health.get("unreadable"):
        needs_attention_reasons.append("Expected artifacts could not be read: " + ", ".join(artifact_health["unreadable"]))
    if decision.should_commit_push and not git_ok and not secret_findings:
        needs_attention_reasons.append("Git commit or push did not complete.")

    status_label = "Ready for review" if tests_ok and compile_ok and not secret_findings and git_ok and deadline_ok and not artifact_issues else "Needs attention"
    payload = {
        "status_label": status_label,
        "branch": branch,
        "commit_sha": commit_sha,
        "test_result": test_result,
        "compile_result": compile_result,
        "push_result": push_result,
        "secret_scan_result": secret_scan_result,
        "secret_findings": secret_findings,
        "artifact_folder": str(writer.run_dir),
        "hardware_stop_marker": str(stop_marker),
        "finalization_completed_at": local_decision_time.strftime("%Y-%m-%d %H:%M local"),
        "finalization_deadline": _finalization_deadline_label(deadline_at),
        "finalization_timing": "before deadline" if deadline_ok else "missed deadline",
        "git_status_before": status_before.stdout,
        "git_status_after": status_after.stdout,
        "commands": commands,
        "notes": notes,
        "needs_attention_reasons": needs_attention_reasons,
        "artifact_health": artifact_health,
    }
    _write_morning_report(writer, payload)
    writer.trace(stage="finalization", status="completed" if status_label == "Ready for review" else "needs_attention", progress=100, message=f"Finalization result: {status_label}.")
    writer.write_summary(
        {
            "status": status_label,
            "finalization": payload,
        }
    )
    return payload


def run_overnight_hardware(
    cfg: dict[str, Any],
    run_config: OvernightHardwareConfig,
    writer: OvernightArtifactWriter,
    *,
    repo_root: Path,
    ilo_client_factory: Callable[..., Any] = default_ilo_client_factory,
    cisco_diagnostics_fn: Callable[[], dict[str, Any]] = serial_runtime_diagnostics,
    cisco_discovery_factory: Callable[[], Any] = CiscoSerialDiscovery,
    cisco_client_factory: Callable[[str, int], Any] = CiscoSerialClient,
    finalizer: Callable[..., dict[str, Any]] = finalize_overnight_run,
    now_fn: Callable[[], datetime] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    now = now_fn or (lambda: datetime.now().astimezone())
    run_started_at = overnight_run_started_at(writer.run_dir)
    original_trace = writer.trace

    def traced(*, stage: str, status: str, progress: int, message: str) -> dict[str, Any]:
        event = original_trace(stage=stage, status=status, progress=progress, message=message)
        if on_event:
            on_event(event)
        return event

    writer.trace = traced  # type: ignore[method-assign]
    writer.trace(stage="run", status="running", progress=8, message=f"Overnight hardware mode is {run_config.mode}.")
    results: dict[str, Any] = {"mode": run_config.mode, "run_folder": str(writer.run_dir)}

    def stop_reason() -> str:
        if hardware_stop_requested(writer.run_dir):
            return "Hardware stop marker is present; no additional hardware actions will start."
        if should_stop_hardware_actions(now(), run_started_at=run_started_at):
            return "Finalization window is active; no additional hardware actions will start."
        return ""

    reason = stop_reason()
    if reason:
        write_ilo_skipped_artifacts(writer, reason, now=now())
        writer.trace(stage="hardware_stop", status="stopped", progress=70, message=reason)
        results["ilo"] = {"ok": False, "status": "skipped", "reason": reason}
    else:
        results["ilo"] = collect_ilo_discovery(cfg, run_config, writer, client_factory=ilo_client_factory)

    reason = stop_reason()
    if reason:
        write_cisco_skipped_artifacts(writer, reason, now=now())
        writer.trace(stage="hardware_stop", status="stopped", progress=70, message=reason)
        results["cisco"] = {"ok": False, "status": "skipped", "reason": reason}
    else:
        results["cisco"] = collect_cisco_console_discovery(
            cfg,
            run_config,
            writer,
            diagnostics_fn=cisco_diagnostics_fn,
            discovery_factory=cisco_discovery_factory,
            client_factory=cisco_client_factory,
        )

    if run_config.mode != OVERNIGHT_DEFAULT_MODE:
        writer.trace(
            stage="guided_actions",
            status="skipped",
            progress=68,
            message="Guided/full mode was selected, but destructive experiment flags are false by default and no storage wipe, factory reset, or ESXi install action is executed by this safe slice.",
        )
    writer.write_summary({"status": "hardware_complete", "results": results})
    results["finalization"] = finalizer(
        writer,
        repo_root=repo_root,
        run_tests=True,
        allow_git=run_config.allow_auto_commit_push,
        now=now(),
    )
    return results


def list_overnight_run_artifacts(artifacts_root: Path) -> list[dict[str, Any]]:
    root = Path(artifacts_root) / "runs" / "overnight"
    if not root.exists():
        return []
    runs: list[dict[str, Any]] = []
    for run_dir in sorted([item for item in root.iterdir() if item.is_dir()], reverse=True):
        summary_path = run_dir / "summary.yml"
        morning_path = run_dir / "MORNING_READY.md"
        summary: dict[str, Any] = {}
        if summary_path.exists():
            try:
                summary = yaml.safe_load(summary_path.read_text(encoding="utf-8")) or {}
            except Exception:
                summary = {}
        morning = read_morning_report_status(morning_path)
        artifact_health = inspect_overnight_artifacts(run_dir)
        finalization = dict(summary.get("finalization") or {}) if isinstance(summary.get("finalization"), dict) else {}
        needs_attention_reasons = list(finalization.get("needs_attention_reasons") or [])
        if not needs_attention_reasons and morning.get("reason"):
            needs_attention_reasons = [str(morning.get("reason") or "")]
        generated_at = str(summary.get("generated_at") or "")
        needs_attention_reasons, deadline_reconciliation = reconcile_overnight_needs_attention_reasons(
            needs_attention_reasons,
            run_dir=run_dir,
            generated_at=generated_at,
        )
        if str(morning.get("status") or "").lower() == "pending" and not needs_attention_reasons:
            needs_attention_reasons = ["MORNING_READY.md is still pending; finalization did not record a completed result."]
        if not needs_attention_reasons and artifact_health.get("skipped"):
            needs_attention_reasons = ["Expected artifacts were skipped: " + ", ".join(artifact_health["skipped"])]
        runs.append(
            {
                "name": run_dir.name,
                "path": str(run_dir),
                "summary_path": str(summary_path),
                "morning_report_path": str(morning_path),
                "status": str(summary.get("status") or "pending"),
                "morning_status": str(morning.get("status") or ""),
                "display_status": str(morning.get("status") or summary.get("status") or "pending"),
                "needs_attention_reasons": needs_attention_reasons,
                "deadline_reconciliation": deadline_reconciliation,
                "artifact_health": artifact_health,
                "generated_at": generated_at,
                "artifact_count": len([item for item in run_dir.rglob("*") if item.is_file()]),
            }
        )
    return runs
