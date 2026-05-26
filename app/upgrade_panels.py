from __future__ import annotations

from pathlib import Path
from typing import Any


STANDARD_STATUSES = {
    "not_started",
    "idle",
    "running",
    "waiting",
    "blocked",
    "paused_failed",
    "failed",
    "completed",
    "warning",
    "skipped",
}

UPGRADE_TAB_ORDER = ("ilo", "ontap", "cisco", "esxi", "firmware")

UPGRADE_TAB_LABELS = {
    "ilo": "iLO",
    "ontap": "ONTAP",
    "cisco": "Cisco",
    "esxi": "ESXi",
    "firmware": "Firmware/SPP",
}

UPGRADE_TAB_ALIASES = {
    "ilo": "ilo",
    "hpe_ilo": "ilo",
    "netapp": "ontap",
    "ontap": "ontap",
    "cisco": "cisco",
    "cisco_switch": "cisco",
    "esxi": "esxi",
    "firmware": "firmware",
    "firmware_spp": "firmware",
    "spp": "firmware",
}

DEVICE_KEY_BY_TAB = {
    "ilo": "ilo",
    "ontap": "netapp",
    "cisco": "cisco_switch",
}


def _clamped_progress(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(100, parsed))


def normalize_upgrade_tab(value: Any, default: str = "ilo") -> str:
    key = str(value or "").strip().lower().replace("-", "_").replace("/", "_")
    fallback = UPGRADE_TAB_ALIASES.get(str(default or "ilo").strip().lower().replace("-", "_"), "ilo")
    return UPGRADE_TAB_ALIASES.get(key, fallback)


def _title_status(value: str) -> str:
    return str(value or "not_started").replace("_", " ").title()


def _standard_status(value: str, *, phase: str = "", message: str = "") -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    phase_text = str(phase or "").strip().lower()
    message_text = str(message or "").strip().lower()
    if raw in {"complete", "completed", "current", "success", "succeeded"}:
        return "completed"
    if raw in {"not_started", "notstarted", "none"}:
        return "not_started"
    if raw in {"paused", "paused_failed", "pause"}:
        return "paused_failed"
    if raw in {"fail", "failed", "error"}:
        return "failed"
    if raw in {"block", "blocked"}:
        return "blocked"
    if raw in {"wait", "waiting"} or "waiting" in message_text:
        return "waiting"
    if raw in {"warn", "warning"}:
        return "warning"
    if raw in {"skip", "skipped"}:
        return "skipped"
    if raw in {"run", "running", "queued", "in_progress", "updating"} or phase_text in {"queued", "upload", "transfer", "flash", "validate", "start", "upgrade"}:
        return "running"
    if raw in {"idle"}:
        return "idle"
    return raw if raw in STANDARD_STATUSES else "not_started"


def _display_time(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 19 and "T" in text:
        return text[11:19]
    if len(text) >= 19 and text[10:11] == " ":
        return text[11:19]
    return text or "--:--:--"


def _event_severity(phase: str, message: str) -> str:
    phase_text = str(phase or "").strip().lower()
    message_text = str(message or "").strip().lower()
    if phase_text in {"failed", "blocked"} or "error" in message_text or "failed" in message_text:
        return "warning" if phase_text == "blocked" else "error"
    if phase_text in {"complete", "completed", "current"}:
        return "ready"
    if "warning" in message_text or "waiting" in message_text:
        return "warning"
    return "info"


def _activity_events(activity: dict[str, Any], *, limit: int = 24) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    events = [event for event in list(activity.get("events") or []) if isinstance(event, dict)]
    for event in events[-limit:]:
        phase = str(event.get("phase") or event.get("stage") or "event").strip() or "event"
        message = str(event.get("message") or "").strip() or "Event recorded."
        rows.append(
            {
                "time": _display_time(event.get("timestamp") or event.get("time")),
                "stage": phase,
                "severity": _event_severity(phase, message),
                "message": message,
            }
        )
    return list(reversed(rows))


def _raw_activity_log(activity: dict[str, Any], empty_message: str) -> str:
    events = [event for event in list(activity.get("events") or []) if isinstance(event, dict)]
    if not events:
        return empty_message
    lines = []
    for event in reversed(events[-80:]):
        timestamp = str(event.get("timestamp") or event.get("time") or "").strip()
        phase = str(event.get("phase") or event.get("stage") or "event").strip()
        message = str(event.get("message") or "").strip()
        lines.append(f"{timestamp} [{phase}] {message}".strip())
    return "\n".join(lines)


def _panel_base(
    *,
    panel_id: str,
    module: str,
    title: str,
    status: str,
    status_label: str = "",
    progress: int = 0,
    poll_url: str = "",
    poll_interval: str = "5s",
) -> dict[str, Any]:
    standard_status = _standard_status(status)
    return {
        "id": panel_id,
        "module": module,
        "title": title,
        "status": standard_status,
        "status_label": status_label or _title_status(standard_status),
        "progress": _clamped_progress(progress),
        "polling": standard_status in {"running", "waiting"},
        "poll_url": poll_url,
        "poll_interval": poll_interval,
        "metrics": [],
        "events": [],
        "raw_output": "",
        "raw_label": "Raw output",
    }


def _planner_entry(upgrade_helper_summary: dict[str, Any] | None, key: str) -> dict[str, Any]:
    planner = dict(((upgrade_helper_summary or {}).get("planner") or {}))
    for entry in list(planner.get("entries") or []):
        if str((entry or {}).get("key") or "") == key:
            return dict(entry or {})
    for device in list((upgrade_helper_summary or {}).get("devices") or []):
        if str((device or {}).get("key") or "") == key:
            return dict(device or {})
    return {}


def _action(
    label: str,
    *,
    hx_post: str = "",
    href: str = "",
    style: str = "",
    hx_vals: dict[str, Any] | None = None,
    hx_include: str = "",
    action_title: str = "",
    action_start: str = "",
    action_complete: str = "",
) -> dict[str, Any]:
    return {
        "type": "link" if href else "button",
        "label": label,
        "style": style,
        "href": href,
        "hx_post": hx_post,
        "hx_vals": hx_vals or {"return_page": "upgrade_helper"},
        "hx_include": hx_include,
        "hx_target": "#main-content",
        "hx_swap": "outerHTML",
        "action_title": action_title,
        "action_start": action_start,
        "action_complete": action_complete,
    }


def _status_action(label: str, status: str = "completed") -> dict[str, Any]:
    return {"type": "status", "label": label, "status": _standard_status(status)}


def _override_control(tab: str, device_key: str, checked: bool) -> dict[str, Any]:
    return {
        "type": "checkbox",
        "label": "Override gate for config setup",
        "name": "override_upgrade_gate",
        "value": "true",
        "checked": bool(checked),
        "hx_post": f"/save-upgrade-override?upgrade_tab={tab}",
        "hx_vals": {"return_page": "upgrade_helper", "device_key": device_key},
        "hx_target": "#main-content",
        "hx_swap": "outerHTML",
    }


def _helper_poll_url(tab: str) -> str:
    return f"/upgrade-helper/panel/{normalize_upgrade_tab(tab)}"


def _set_helper_poll(panel: dict[str, Any], tab: str) -> None:
    if panel.get("polling"):
        panel["poll_url"] = _helper_poll_url(tab)


def _tab_badge(status: str, status_label: str = "") -> tuple[str, str]:
    normalized = _standard_status(status)
    labels = {
        "not_started": "Idle",
        "idle": "Idle",
        "running": "Running",
        "waiting": "Waiting",
        "blocked": "Attention",
        "paused_failed": "Failed",
        "failed": "Failed",
        "completed": "Completed",
        "warning": "Attention",
        "skipped": "Skipped",
    }
    return normalized, labels.get(normalized) or status_label or _title_status(normalized)


def _activity_running(status: str) -> bool:
    return _standard_status(status) in {"running", "waiting"}


def build_ilo_upgrade_panel(cfg: dict[str, Any]) -> dict[str, Any]:
    ilo_upgrade = dict(((cfg.get("ilo") or {}).get("upgrade") or {}))
    activity = dict(ilo_upgrade.get("activity") or {})
    result = dict(ilo_upgrade.get("last_result") or {})
    plan = dict(ilo_upgrade.get("last_plan") or {})
    activity_status = str(activity.get("status") or result.get("status") or "not_started").strip().lower()
    phase = str(activity.get("phase") or "").strip()
    message = str(activity.get("message") or result.get("message") or result.get("error") or "").strip()
    status = _standard_status(activity_status, phase=phase, message=message)
    progress = _clamped_progress(activity.get("progress_percent"), 0)
    if status in {"completed", "failed", "blocked", "paused_failed"}:
        progress = 100
    current_step = phase.replace("_", " ").title() if phase else "Idle"
    if phase.lower() == "current":
        current_step = "Current"
    latest_message = message or "No iLO upgrade is running."
    panel = _panel_base(
        panel_id="ilo-upgrade-activity",
        module="ilo",
        title="iLO upgrade status",
        status=status,
        status_label="Completed" if status == "completed" else _title_status(status),
        progress=progress,
        poll_url="/ilo-upgrade-activity",
        poll_interval="5s",
    )
    image = str(plan.get("media_filename") or result.get("media_filename") or "Not selected").strip()
    target = str(plan.get("host") or result.get("host") or ((cfg.get("ilo") or {}).get("current_ip")) or ((cfg.get("ilo") or {}).get("host")) or "Not set").strip()
    panel.update(
        {
            "current_step": current_step,
            "image": image,
            "target": target,
            "latest_message": latest_message,
            "focus_title": "Last error" if result.get("error") else current_step,
            "focus_message": str(result.get("error") or latest_message).strip(),
            "raw_label": "Upgrade log",
            "events": _activity_events(activity),
            "raw_output": _raw_activity_log(activity, "Nothing is running right now. Start an iLO upgrade to see live updates here."),
            "metrics": [
                {"label": "Current step", "value": current_step},
                {"label": "Progress", "value": f"{progress}%"},
                {"label": "Image", "value": image},
                {"label": "Target", "value": target},
            ],
        }
    )
    return panel


def build_cisco_upgrade_panel(cfg: dict[str, Any]) -> dict[str, Any]:
    cisco_upgrade = dict(((cfg.get("cisco_switch") or {}).get("upgrade") or {}))
    activity = dict(cisco_upgrade.get("activity") or {})
    result = dict(cisco_upgrade.get("last_result") or {})
    plan = dict(cisco_upgrade.get("last_plan") or {})
    activity_status = str(activity.get("status") or result.get("status") or "not_started").strip().lower()
    phase = str(activity.get("phase") or "").strip()
    message = str(activity.get("message") or result.get("error") or "").strip()
    status = _standard_status(activity_status, phase=phase, message=message)
    progress = _clamped_progress(activity.get("progress_percent"), 0)
    if status in {"completed", "failed", "blocked", "paused_failed"}:
        progress = 100
    current_step = phase.replace("_", " ").title() if phase else "Idle"
    latest_message = message or "No Cisco upgrade is running."
    focus_title = "Last error" if result.get("error") else current_step
    focus_message = latest_message
    if "Administratively disabled" in str(result.get("error") or ""):
        focus_title = "File transfer blocked"
        focus_message = (
            "The switch accepted SSH but rejected SCP file transfer because the Cisco SCP server was disabled. "
            "Lab Builder enables ip scp server enable before copying the image."
        )
    panel = _panel_base(
        panel_id="cisco-upgrade-activity",
        module="cisco",
        title="Cisco upgrade status",
        status=status,
        status_label=_title_status(status),
        progress=progress,
        poll_url="/modules/cisco/upgrade-activity",
        poll_interval="5s",
    )
    image = str(plan.get("media_filename") or result.get("media_filename") or "Not selected").strip()
    target = str(plan.get("host") or result.get("host") or "Not set").strip()
    panel.update(
        {
            "current_step": current_step,
            "image": image,
            "target": target,
            "latest_message": latest_message,
            "focus_title": focus_title,
            "focus_message": focus_message,
            "raw_label": "Raw Cisco output",
            "events": _activity_events(activity),
            "raw_output": _raw_activity_log(activity, "Nothing is running right now. Start a Cisco upgrade to see live updates here."),
            "metrics": [
                {"label": "Current step", "value": current_step},
                {"label": "Progress", "value": f"{progress}%"},
                {"label": "Image", "value": image},
                {"label": "Target", "value": target},
            ],
        }
    )
    return panel


def _ontap_status(status_model: dict[str, Any], activity: dict[str, Any]) -> tuple[str, str]:
    status_text = str(status_model.get("status") or "").strip()
    status_lower = status_text.lower()
    activity_status = str(status_model.get("activity_status") or activity.get("status") or "not_started").strip().lower()
    software_state = str(status_model.get("software_state") or "").strip().lower()
    if status_model.get("waiting_for_giveback") or "giveback" in status_lower:
        return "waiting", "Waiting"
    if "paused" in status_lower or "failed" in status_lower or software_state in {"failed", "failure", "canceled", "cancelled", "paused", "pause"}:
        return "paused_failed", "Paused/failed"
    if activity_status in {"failed"}:
        return "failed", "Failed"
    if activity_status in {"blocked"}:
        return "paused_failed", "Paused/failed"
    if activity_status == "completed" or software_state == "completed" or status_lower == "completed":
        return "completed", "Completed"
    if "uploading" in status_lower or "staging" in status_lower or activity_status == "running" or software_state in {"running", "in_progress", "updating"}:
        return "running", status_text or "Running"
    if activity_status == "idle":
        return "idle", "Idle"
    return "not_started", "Not Started"


def build_netapp_upgrade_panel(cfg: dict[str, Any], ontap_upgrade_status: dict[str, Any] | None = None) -> dict[str, Any]:
    if ontap_upgrade_status is None:
        from app.netapp_upgrade import build_ontap_upgrade_status

        ontap_upgrade_status = build_ontap_upgrade_status(cfg)
    status_model = dict(ontap_upgrade_status or {})
    netapp_upgrade = dict(((cfg.get("netapp") or {}).get("upgrade") or {}))
    activity = dict(netapp_upgrade.get("activity") or {})
    status, status_label = _ontap_status(status_model, activity)
    progress = _clamped_progress(status_model.get("progress", activity.get("progress_percent", 0)), 0)
    if status in {"completed", "failed", "blocked", "paused_failed"}:
        progress = 100
    current_step = str(status_model.get("current_step") or activity.get("phase") or "Idle").strip()
    latest_message = str(activity.get("message") or "").strip() or "No ONTAP upgrade activity yet."
    warnings = [str(item).strip() for item in list(status_model.get("warnings") or []) if str(item).strip()]
    if warnings and latest_message == "No ONTAP upgrade activity yet.":
        latest_message = warnings[0]
    focus_message = latest_message
    if warnings:
        focus_message = " ".join([latest_message] + warnings)
    completed_events = status_model.get("completed_events")
    try:
        completed_value = int(completed_events)
    except (TypeError, ValueError):
        completed_value = len(list(activity.get("events") or []))
    events = [dict(event) for event in list(status_model.get("events") or []) if isinstance(event, dict)]
    normalized_events = [
        {
            "time": _display_time(event.get("time")),
            "stage": str(event.get("stage") or "event"),
            "severity": str(event.get("severity") or "info"),
            "message": str(event.get("message") or "Event recorded."),
        }
        for event in reversed(events[-24:])
    ]
    for warning in reversed(warnings):
        normalized_events.insert(0, {"time": "--:--:--", "stage": "warning", "severity": "warning", "message": warning})
    job_id = str(status_model.get("job_uuid") or activity.get("job_uuid") or "").strip()
    current_release = str(status_model.get("current_release") or "Unknown").strip()
    target_release = str(status_model.get("target_release") or "").strip()
    panel = _panel_base(
        panel_id="netapp-upgrade-activity",
        module="ontap",
        title="ONTAP upgrade job",
        status=status,
        status_label=status_label,
        progress=progress,
        poll_url="/modules/netapp/upgrade-activity",
        poll_interval="3s",
    )
    panel.update(
        {
            "current_step": current_step,
            "current_release": current_release,
            "target_release": target_release,
            "mode": str(status_model.get("mode") or "ONTAP upgrade").strip(),
            "completed": f"{completed_value} events",
            "latest_message": latest_message,
            "focus_title": current_step,
            "focus_message": focus_message,
            "job_id": job_id,
            "events": normalized_events,
            "raw_label": "Raw ONTAP output",
            "raw_output": str(status_model.get("raw_output") or "No raw ONTAP output has been captured for this upgrade yet.").strip(),
            "metrics": [
                {"label": "Status", "value": status_label},
                {"label": "Mode", "value": str(status_model.get("mode") or "ONTAP upgrade").strip()},
                {"label": "Current release", "value": current_release},
                {"label": "Target release", "value": target_release or "Not selected"},
                {"label": "Current step", "value": current_step},
                {"label": "Progress", "value": f"{progress}%"},
                {"label": "Completed", "value": f"{completed_value} events"},
            ],
        }
    )
    return panel


def build_esxi_upgrade_panel(cfg: dict[str, Any]) -> dict[str, Any]:
    esxi_cfg = dict((cfg.get("esxi") or {}))
    version = str(esxi_cfg.get("version") or "7").strip() or "7"
    base_iso = str(esxi_cfg.get("base_iso_path") or "").strip()
    target = str(esxi_cfg.get("management_ip") or (cfg.get("ip_plan") or {}).get("esxi") or "Not set").strip()
    status = "idle" if base_iso else "warning"
    current_step = "Ready" if base_iso else "Needs media"
    latest_message = (
        "ESXi installer media is selected. Run the ESXi workflow from Execution when ready."
        if base_iso
        else "Select an ESXi base ISO before running the ESXi install workflow."
    )
    raw_output = "\n".join(
        [
            f"ESXi version: {version}",
            f"Base ISO: {base_iso or 'not selected'}",
            f"Management IP: {target}",
            f"Hostname: {str(esxi_cfg.get('hostname') or '').strip() or 'not set'}",
            f"Debug no reboot: {bool(esxi_cfg.get('debug_no_reboot', False))}",
        ]
    )
    panel = _panel_base(
        panel_id="esxi-upgrade-activity",
        module="esxi",
        title="ESXi upgrade status",
        status=status,
        status_label="Ready" if status == "idle" else "Attention",
        progress=0,
    )
    panel.update(
        {
            "current_step": current_step,
            "image": Path(base_iso).name if base_iso else "Not selected",
            "target": target,
            "latest_message": latest_message,
            "focus_title": current_step,
            "focus_message": latest_message,
            "raw_label": "Raw ESXi details",
            "raw_output": raw_output,
            "metrics": [
                {"label": "Current step", "value": current_step},
                {"label": "Progress", "value": "0%"},
                {"label": "Base ISO", "value": Path(base_iso).name if base_iso else "Not selected"},
                {"label": "Target", "value": target},
                {"label": "Version", "value": version},
            ],
        }
    )
    return panel


def build_firmware_spp_upgrade_panel(cfg: dict[str, Any], upgrade_helper_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    media_scan = dict(((upgrade_helper_summary or {}).get("media_scan") or {}))
    media_root = str((upgrade_helper_summary or {}).get("target") or media_scan.get("root") or "media/").strip()
    candidates = [dict(item) for item in list(media_scan.get("candidates") or []) if isinstance(item, dict)]
    counts = dict(media_scan.get("counts") or {})
    detected_count = len(candidates)
    raw_lines = []
    for item in candidates:
        raw_lines.append(
            f"{str(item.get('device') or 'unknown'):14} {str(item.get('version') or 'unknown'):12} {str(item.get('filename') or Path(str(item.get('path') or '')).name)}"
        )
    raw_output = "\n".join(raw_lines) if raw_lines else f"No upgrade media candidates were detected under {media_root}."
    panel = _panel_base(
        panel_id="firmware-upgrade-activity",
        module="firmware",
        title="Firmware/SPP status",
        status="idle" if detected_count else "not_started",
        status_label="Media found" if detected_count else "Idle",
        progress=0,
    )
    panel.update(
        {
            "current_step": "Media inventory",
            "latest_message": (
                f"Detected {detected_count} upgrade media file{'s' if detected_count != 1 else ''} under {media_root}."
                if detected_count
                else "Upload firmware, SPP, ONTAP, Cisco, or ISO media when an upgrade workflow needs it."
            ),
            "focus_title": "Media inventory",
            "focus_message": "Firmware/SPP is a shared media area. Module-specific upgrade actions live in the iLO, ONTAP, Cisco, and ESXi tabs.",
            "raw_label": "Raw media inventory",
            "raw_output": raw_output,
            "metrics": [
                {"label": "Current step", "value": "Media inventory"},
                {"label": "Progress", "value": "0%"},
                {"label": "Media root", "value": media_root},
                {"label": "Detected files", "value": str(detected_count)},
                {"label": "iLO files", "value": str(counts.get("ilo", 0))},
                {"label": "ONTAP files", "value": str(counts.get("netapp", 0))},
                {"label": "Cisco files", "value": str(counts.get("cisco_switch", 0))},
            ],
        }
    )
    return panel


def _ilo_actions(cfg: dict[str, Any], upgrade_helper_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    entry = _planner_entry(upgrade_helper_summary, "ilo")
    comparison = str(entry.get("comparison") or entry.get("status") or "").strip()
    actions: list[dict[str, Any]] = []
    if comparison == "current_unknown":
        actions.append(
            _action(
                "Read current iLO",
                hx_post="/export-ilo-inventory?upgrade_tab=ilo",
                style="primary",
                action_title="Reading current iLO",
                action_start="Connecting to iLO and reading the current firmware version.",
            )
        )
    actions.append(_action("Plan iLO upgrade", hx_post="/plan-ilo-upgrade?upgrade_tab=ilo"))
    if comparison == "upgrade_available":
        actions.append(
            _action(
                "Run iLO upgrade",
                hx_post="/run-ilo-upgrade?upgrade_tab=ilo",
                style="primary",
                action_title="Starting iLO upgrade",
                action_start="Queueing iLO firmware upload and verification. The iLO upgrade status panel will keep updating.",
                action_complete="iLO upgrade worker started. Watch the status panel.",
            )
        )
    elif comparison == "current_enough":
        actions.append(_status_action("No iLO upgrade required", "completed"))
    actions.append(_override_control("ilo", "ilo", bool(((upgrade_helper_summary or {}).get("overrides") or {}).get("ilo"))))
    actions.append(_action("Open iLO", href="/ilo"))
    return actions


def _netapp_actions(cfg: dict[str, Any], upgrade_helper_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        _action(
            "Read current ONTAP release",
            hx_post="/modules/netapp/plan-upgrade?upgrade_tab=ontap",
            style="primary",
            action_title="Reading current ONTAP release",
            action_start="Connecting to ONTAP and reading the current cluster release.",
            action_complete="Current ONTAP release check finished.",
        ),
        _action("Review ONTAP upgrade plan", hx_post="/modules/netapp/plan-upgrade?upgrade_tab=ontap"),
        _action(
            "Run ONTAP upgrade",
            hx_post="/modules/netapp/run-upgrade?upgrade_tab=ontap",
            style="primary",
            action_title="Starting ONTAP upgrade",
            action_start="Queueing ONTAP upload and validation. The ONTAP upgrade job panel will keep updating.",
            action_complete="ONTAP upgrade worker started. Watch the status panel.",
        ),
        _override_control("ontap", "netapp", bool(((upgrade_helper_summary or {}).get("overrides") or {}).get("netapp"))),
        _action("Open NetApp", href="/modules/netapp"),
    ]


def _cisco_actions(cfg: dict[str, Any], upgrade_helper_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    cisco_vals = {"return_page": "upgrade_helper"}
    return [
        _action("Review Cisco upgrade plan", hx_post="/modules/cisco/plan-upgrade?upgrade_tab=cisco"),
        _action(
            "Run Cisco upgrade",
            hx_post="/modules/cisco/run-upgrade?upgrade_tab=cisco",
            style="primary",
            action_title="Starting Cisco upgrade",
            action_start="Queueing Cisco image transfer and install. The Cisco upgrade status panel will keep updating.",
            action_complete="Cisco upgrade worker started. Watch the status panel.",
        ),
        _action("Read Cisco version", hx_post="/modules/cisco/discover-version?upgrade_tab=cisco", hx_vals=cisco_vals),
        _override_control("cisco", "cisco_switch", bool(((upgrade_helper_summary or {}).get("overrides") or {}).get("cisco_switch"))),
        _action("Open Cisco", href="/modules/cisco"),
    ]


def _esxi_actions(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _action("Open ESXi settings", href="/esxi", style="primary"),
        _action("Open Execution", href="/execution"),
    ]


def _firmware_actions() -> list[dict[str, Any]]:
    return [
        _action("Upload firmware/media", href="#upgrade-media-upload", style="primary"),
        _action("Open Reports", href="/configs"),
    ]


def _decorate_helper_panel(panel: dict[str, Any], tab: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(panel)
    result["tab"] = tab
    result["actions"] = actions
    _set_helper_poll(result, tab)
    return result


def build_upgrade_helper_tab_context(
    cfg: dict[str, Any],
    ontap_upgrade_status: dict[str, Any] | None = None,
    upgrade_helper_summary: dict[str, Any] | None = None,
    active_tab: Any = "ilo",
) -> dict[str, Any]:
    active = normalize_upgrade_tab(active_tab)
    panels = {
        "ilo": _decorate_helper_panel(build_ilo_upgrade_panel(cfg), "ilo", _ilo_actions(cfg, upgrade_helper_summary)),
        "ontap": _decorate_helper_panel(build_netapp_upgrade_panel(cfg, ontap_upgrade_status), "ontap", _netapp_actions(cfg, upgrade_helper_summary)),
        "cisco": _decorate_helper_panel(build_cisco_upgrade_panel(cfg), "cisco", _cisco_actions(cfg, upgrade_helper_summary)),
        "esxi": _decorate_helper_panel(build_esxi_upgrade_panel(cfg), "esxi", _esxi_actions(cfg)),
        "firmware": _decorate_helper_panel(build_firmware_spp_upgrade_panel(cfg, upgrade_helper_summary), "firmware", _firmware_actions()),
    }
    tabs: list[dict[str, Any]] = []
    for key in UPGRADE_TAB_ORDER:
        panel = panels[key]
        badge_status, badge_label = _tab_badge(str(panel.get("status") or ""), str(panel.get("status_label") or ""))
        tabs.append(
            {
                "key": key,
                "label": UPGRADE_TAB_LABELS[key],
                "active": key == active,
                "status": badge_status,
                "status_css": badge_status.replace("_", "-"),
                "badge_label": badge_label,
                "attention": badge_status in {"blocked", "paused_failed", "failed", "warning"},
                "running": _activity_running(badge_status),
                "href": f"/upgrade-helper?tab={key}",
                "partial_href": f"/upgrade-helper/tab/{key}",
            }
        )
    return {
        "upgrade_tabs": tabs,
        "upgrade_panels": panels,
        "active_upgrade_tab": active,
        "active_upgrade_panel": panels[active],
    }
