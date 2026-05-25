from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable

from app.netapp import NetAppError
from app.upgrade_helper import build_upgrade_inventory, compare_versions, record_upgrade_inventory, select_upgrade_candidate


ONTAP_UPGRADE_PATH_SOURCE = "https://docs.netapp.com/us-en/ontap/upgrade/concept_upgrade_paths.html"
ONTAP_HA_MISMATCH_WARNING = (
    "Version mismatch detected between HA partners. This can be expected during a rolling ONTAP upgrade while one controller "
    "has upgraded and the partner has not yet completed. Check update progress and giveback status."
)
ONTAP_GIVEBACK_WARNING = (
    "Waiting for giveback. Do not force giveback automatically. Review storage failover status and giveback status."
)
ONTAP_EXPECTED_MISMATCH_PATTERNS = (
    "kernel mismatch",
    "nvram version mismatch",
    "wafl fsinfo version mismatch",
    "nvram nvlog version mismatch",
    "raid version mismatch",
    "raid nvram version mismatch",
)
ONTAP_SUPPORTED_UPGRADE_PATHS: dict[tuple[str, str], list[str]] = {
    ("9.9.1", "9.17.1"): ["9.9.1", "9.13.1", "9.17.1"],
    ("9.9.1", "9.18.1"): ["9.9.1", "9.13.1", "9.17.1", "9.18.1"],
    ("9.9.1", "9.19.1"): ["9.9.1", "9.13.1", "9.17.1", "9.19.1"],
    ("9.10.1", "9.17.1"): ["9.10.1", "9.14.1", "9.17.1"],
    ("9.10.1", "9.18.1"): ["9.10.1", "9.14.1", "9.18.1"],
    ("9.10.1", "9.19.1"): ["9.10.1", "9.14.1", "9.18.1", "9.19.1"],
    ("9.11.1", "9.17.1"): ["9.11.1", "9.15.1", "9.17.1"],
    ("9.11.1", "9.18.1"): ["9.11.1", "9.15.1", "9.18.1"],
    ("9.11.1", "9.19.1"): ["9.11.1", "9.15.1", "9.19.1"],
    ("9.12.1", "9.17.1"): ["9.12.1", "9.16.1", "9.17.1"],
    ("9.12.1", "9.18.1"): ["9.12.1", "9.16.1", "9.18.1"],
    ("9.12.1", "9.19.1"): ["9.12.1", "9.16.1", "9.19.1"],
    ("9.13.1", "9.17.1"): ["9.13.1", "9.17.1"],
    ("9.13.1", "9.18.1"): ["9.13.1", "9.17.1", "9.18.1"],
    ("9.13.1", "9.19.1"): ["9.13.1", "9.17.1", "9.19.1"],
    ("9.14.1", "9.17.1"): ["9.14.1", "9.17.1"],
    ("9.14.1", "9.18.1"): ["9.14.1", "9.18.1"],
    ("9.14.1", "9.19.1"): ["9.14.1", "9.18.1", "9.19.1"],
}


def build_ontap_upgrade_status(cfg: dict[str, Any]) -> dict[str, Any]:
    netapp_cfg = dict(cfg.get("netapp") or {})
    upgrade = dict(netapp_cfg.get("upgrade") or {})
    activity = dict(upgrade.get("activity") or {})
    result = dict(upgrade.get("last_result") or {})
    plan = dict(upgrade.get("last_plan") or {})
    inventory = dict((cfg.get("upgrade_inventory") or {}).get("netapp") or {})
    events = [event for event in list(activity.get("events") or []) if isinstance(event, dict)]
    latest_event = dict(events[-1]) if events else {}
    raw_payload = _latest_software_payload(result, latest_event)
    raw_output = _build_ontap_raw_output(events, raw_payload, result)
    current_release = (
        str(inventory.get("current_version") or "").strip()
        or str(latest_event.get("current_version") or "").strip()
        or str(result.get("current_version") or "").strip()
        or str(raw_payload.get("version") or "").strip()
        or str(plan.get("current_version") or "").strip()
        or str(netapp_cfg.get("last_discovered_ontap_version") or "").strip()
    )
    target_release = (
        str(plan.get("media_version") or "").strip()
        or str(result.get("target_version") or "").strip()
        or str(latest_event.get("pending_version") or "").strip()
        or str(raw_payload.get("pending_version") or "").strip()
    )
    activity_status = str(activity.get("status") or result.get("status") or "not_started").strip().lower()
    phase = str(activity.get("phase") or latest_event.get("phase") or "").strip().lower()
    software_state = str(latest_event.get("software_state") or raw_payload.get("state") or "").strip().lower()
    progress = _clamped_int(activity.get("progress_percent"), 0)
    if activity_status == "completed":
        progress = 100
    elif activity_status in {"failed", "blocked"} and not progress:
        progress = 100

    waiting_for_giveback = _detect_waiting_for_giveback(raw_payload, latest_event, raw_output)
    ha_version_mismatch = _detect_ha_version_mismatch(raw_payload, raw_output)
    warnings: list[str] = []
    if ha_version_mismatch:
        warnings.append(ONTAP_HA_MISMATCH_WARNING)
    if waiting_for_giveback:
        warnings.append(ONTAP_GIVEBACK_WARNING)

    status_label = _ontap_status_label(activity_status, phase, software_state, waiting_for_giveback)
    current_step = _ontap_current_step(phase, software_state, waiting_for_giveback)

    return {
        "status": status_label,
        "mode": "ONTAP upgrade",
        "current_release": current_release or "Unknown",
        "target_release": target_release or "",
        "current_step": current_step,
        "progress": progress,
        "completed_events": len(events),
        "waiting_for_giveback": waiting_for_giveback,
        "ha_version_mismatch": ha_version_mismatch,
        "warnings": warnings,
        "raw_output": raw_output,
        "events": _structured_ontap_events(events),
        "activity_status": activity_status or "not_started",
        "phase": phase,
        "software_state": software_state,
        "latest_event": latest_event,
        "update_details": list(latest_event.get("update_details") or raw_payload.get("update_details") or []),
        "status_details": list(latest_event.get("status_details") or raw_payload.get("status_details") or []),
        "job_uuid": str(activity.get("job_uuid") or "").strip(),
        "source": str(inventory.get("source") or "").strip(),
    }


def _latest_software_payload(result: dict[str, Any], latest_event: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw")
    if isinstance(raw, dict):
        return raw
    start = result.get("start")
    if isinstance(start, dict) and isinstance(start.get("raw"), dict):
        return dict(start.get("raw") or {})
    payload = {
        "version": latest_event.get("current_version"),
        "pending_version": latest_event.get("pending_version"),
        "state": latest_event.get("software_state"),
        "elapsed_duration": latest_event.get("elapsed_duration"),
        "estimated_duration": latest_event.get("estimated_duration"),
        "status_details": latest_event.get("status_details") or [],
        "update_details": latest_event.get("update_details") or [],
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _build_ontap_raw_output(events: list[dict[str, Any]], raw_payload: dict[str, Any], result: dict[str, Any]) -> str:
    chunks: list[str] = []
    raw_output = str(result.get("raw_output") or "").strip()
    if raw_output:
        chunks.append(raw_output)
    if raw_payload:
        try:
            chunks.append("Latest cluster software payload:\n" + json.dumps(raw_payload, indent=2, default=str))
        except TypeError:
            chunks.append("Latest cluster software payload:\n" + str(raw_payload))
    if events:
        lines = []
        for event in events[-80:]:
            line = f"{event.get('timestamp', '')} [{event.get('phase', 'event')}] {event.get('message', '')}"
            if event.get("job_uuid"):
                line += f" | job={event.get('job_uuid')}"
            if event.get("job_state"):
                line += f" | state={event.get('job_state')}"
            lines.append(line)
        chunks.append("Activity events:\n" + "\n".join(lines))
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def _structured_ontap_events(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in events[-24:]:
        phase = str(event.get("phase") or "event").strip()
        message = str(event.get("message") or "").strip()
        severity = "info"
        if phase in {"failed", "blocked"} or "error" in message.lower() or "failed" in message.lower():
            severity = "error"
        elif phase in {"complete", "completed"}:
            severity = "ready"
        elif "warning" in message.lower() or "waiting" in message.lower():
            severity = "warning"
        rows.append(
            {
                "time": str(event.get("timestamp") or "").strip(),
                "stage": phase,
                "severity": severity,
                "message": message,
            }
        )
    return rows


def _detect_waiting_for_giveback(raw_payload: dict[str, Any], latest_event: dict[str, Any], raw_output: str) -> bool:
    haystack = " ".join(
        [
            str(raw_output or ""),
            str(latest_event.get("message") or ""),
            json.dumps(raw_payload.get("status_details") or [], default=str),
            json.dumps(raw_payload.get("update_details") or [], default=str),
        ]
    ).lower()
    if "waiting for giveback" in haystack:
        return True
    for detail in list(raw_payload.get("status_details") or []) + list(latest_event.get("status_details") or []):
        if not isinstance(detail, dict):
            continue
        name = str(detail.get("name") or "").lower()
        state = str(detail.get("state") or "").lower()
        message = str(((detail.get("issue") or {}).get("message")) or "").lower()
        if "giveback" in f"{name} {message}" and state in {"waiting", "in_progress", "running"}:
            return True
    return False


def _detect_ha_version_mismatch(raw_payload: dict[str, Any], raw_output: str) -> bool:
    lowered = str(raw_output or "").lower()
    if any(pattern in lowered for pattern in ONTAP_EXPECTED_MISMATCH_PATTERNS):
        return True
    node_versions = {
        normalize_ontap_feature_release(str(node.get("version") or ""))
        for node in raw_payload.get("nodes") or []
        if isinstance(node, dict) and str(node.get("version") or "").strip()
    }
    node_versions.discard("")
    return len(node_versions) > 1


def _ontap_status_label(activity_status: str, phase: str, software_state: str, waiting_for_giveback: bool) -> str:
    if waiting_for_giveback:
        return "Waiting for giveback"
    if activity_status == "completed" or software_state == "completed":
        return "Completed"
    if activity_status in {"failed", "blocked"} or software_state in {"failed", "failure", "canceled", "cancelled", "paused", "pause"}:
        return "Paused/failed"
    if phase in {"upload", "validate", "start"}:
        return "Uploading/staging"
    if activity_status == "running" or software_state in {"running", "in_progress", "updating"}:
        return "Running"
    return "No upgrade currently running"


def _ontap_current_step(phase: str, software_state: str, waiting_for_giveback: bool) -> str:
    if waiting_for_giveback:
        return "Waiting for giveback"
    phase_map = {
        "queued": "Queued",
        "precheck": "Upgrade readiness check",
        "connect": "Connecting to ONTAP",
        "upload": "Uploading/staging image package",
        "validate": "Pre-update checks",
        "start": "Starting ONTAP update",
        "upgrade": "ONTAP upgrade running",
        "blocked": "Blocked",
        "failed": "Paused/failed",
        "complete": "Complete",
    }
    if phase in phase_map:
        return phase_map[phase]
    if software_state:
        return software_state.replace("_", " ").title()
    return "Idle"


def _clamped_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(100, parsed))


def build_netapp_upgrade_plan(cfg: dict[str, Any], media_scan: dict[str, Any]) -> dict[str, Any]:
    inventory = build_upgrade_inventory(cfg)
    item = dict(inventory.get("netapp") or {})
    netapp_cfg = dict(cfg.get("netapp") or {})
    current_version = str(item.get("current_version") or "").strip()
    current_source = str(item.get("source") or "").strip()
    host = str(netapp_cfg.get("host") or "").strip()
    username = str(netapp_cfg.get("username") or "").strip()
    password_present = bool(str(netapp_cfg.get("password") or ""))
    selected = select_upgrade_candidate(media_scan, "netapp", {})
    highest_media_version = str(selected.get("version") or "").strip()
    media_version = highest_media_version
    media_filename = str(selected.get("filename") or "").strip()
    media_path = str(selected.get("path") or "").strip()
    path_plan = build_ontap_upgrade_path_plan(current_version, highest_media_version, media_scan)
    next_hop = str(path_plan.get("next_hop") or "").strip()
    next_hop_media = dict(path_plan.get("next_hop_media") or {})
    if next_hop_media:
        media_version = str(next_hop_media.get("version") or "").strip()
        media_filename = str(next_hop_media.get("filename") or "").strip()
        media_path = str(next_hop_media.get("path") or "").strip()

    blockers: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    if current_version:
        notes.append(f"Current ONTAP version: {current_version}")
    if media_version:
        notes.append(f"Matched image version: {media_version}")
    if path_plan.get("path"):
        notes.append(f"Supported ONTAP path: {' -> '.join(path_plan.get('path') or [])}")
    if path_plan.get("next_hop"):
        notes.append(f"Next required ONTAP hop: {path_plan.get('next_hop')}")
    if media_path:
        notes.append(f"Matched image file: {media_path}")
    if current_source:
        notes.append(f"Current version source: {current_source}")

    if not host:
        blockers.append("ONTAP API target is not set.")
    if not username or not password_present:
        blockers.append("Saved ONTAP credentials are incomplete.")
    if not current_version:
        blockers.append("Current ONTAP version is unknown. Run NetApp discovery first.")
    if not media_version or not media_path:
        blockers.append("No approved ONTAP image was found under the media directory.")
    if path_plan.get("blockers"):
        blockers.extend([str(item) for item in path_plan.get("blockers") or []])
    if path_plan.get("warnings"):
        warnings.extend([str(item) for item in path_plan.get("warnings") or []])

    comparison = None
    if current_version and media_version:
        comparison = compare_versions(current_version, media_version)
        if comparison is not None and comparison >= 0:
            warnings.append("Current ONTAP version is already equal to or newer than the matched image.")

    ready = not blockers and comparison is not None and comparison < 0
    if ready:
        notes.append("Upgrade can proceed through ONTAP REST image upload, validation, and cluster software start.")

    return {
        "ready": ready,
        "host": host,
        "username": username,
        "password_present": password_present,
        "current_version": current_version,
        "current_source": current_source,
        "media_version": media_version,
        "highest_media_version": highest_media_version,
        "media_filename": media_filename,
        "media_path": media_path,
        "upgrade_path": path_plan,
        "comparison": comparison,
        "blockers": blockers,
        "warnings": warnings,
        "notes": notes,
        "planned_at": datetime.now(timezone.utc).isoformat(),
    }


def build_ontap_upgrade_path_plan(current_version: str, target_version: str, media_scan: dict[str, Any]) -> dict[str, Any]:
    current = normalize_ontap_feature_release(current_version)
    target = normalize_ontap_feature_release(target_version)
    candidates = [dict(item) for item in media_scan.get("candidates") or [] if dict(item).get("device") == "netapp"]
    media_by_release: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        release = normalize_ontap_feature_release(str(candidate.get("version") or ""))
        if release:
            media_by_release[release] = candidate

    plan: dict[str, Any] = {
        "source": ONTAP_UPGRADE_PATH_SOURCE,
        "current": current,
        "target": target,
        "path": [],
        "next_hop": "",
        "next_hop_media": {},
        "missing_media": [],
        "blockers": [],
        "warnings": [],
    }
    if not current or not target:
        return plan
    if current == target:
        plan["path"] = [current]
        return plan

    path = list(ONTAP_SUPPORTED_UPGRADE_PATHS.get((current, target)) or [])
    if not path:
        comparison = compare_versions(current, target)
        if comparison is not None and comparison < 0:
            path = [current, target]
            plan["warnings"].append("No offline ONTAP path rule is stored for this source/target pair; treating the target as a direct candidate and relying on ONTAP validation.")
        else:
            path = [current, target]
    plan["path"] = path

    try:
        current_index = path.index(current)
    except ValueError:
        current_index = 0
    next_hop = path[current_index + 1] if current_index + 1 < len(path) else target
    plan["next_hop"] = next_hop
    if next_hop in media_by_release:
        plan["next_hop_media"] = media_by_release[next_hop]
    else:
        plan["missing_media"] = [next_hop]
        plan["blockers"].append(
            f"Required ONTAP intermediate image {next_hop} is missing from the media directory. "
            f"Stored path for {current} to {target}: {' -> '.join(path)}."
        )
    return plan


def normalize_ontap_feature_release(value: str) -> str:
    import re

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(value or ""))
    if not match:
        return ""
    return ".".join(match.groups())


def execute_netapp_upgrade(
    cfg: dict[str, Any],
    media_scan: dict[str, Any],
    *,
    build_client: Callable[..., Any],
    progress: Callable[[dict[str, Any]], None] | None = None,
    wait_timeout: int = 14400,
    poll_interval: float = 30.0,
    skip_warnings: bool = False,
) -> dict[str, Any]:
    def emit(phase: str, message: str, **extra: Any) -> None:
        if progress:
            progress({"phase": phase, "message": message, "timestamp": datetime.now(timezone.utc).isoformat(), **extra})

    emit("precheck", "Building ONTAP upgrade plan.")
    plan = build_netapp_upgrade_plan(cfg, media_scan)
    if not plan.get("ready"):
        emit("blocked", "ONTAP upgrade prechecks are not satisfied.", blockers=list(plan.get("blockers") or []))
        raise NetAppError("; ".join(list(plan.get("blockers") or []) or ["ONTAP upgrade prechecks are not satisfied."]))

    emit("connect", f"Connecting to ONTAP API target {plan.get('host')}.")
    client = build_client(
        host=str(plan.get("host") or ""),
        username=str(plan.get("username") or ""),
        password=str((cfg.get("netapp") or {}).get("password") or ""),
    )
    media_path = Path(str(plan.get("media_path") or ""))
    target_version = str(plan.get("media_version") or "")

    upload: dict[str, Any]
    try:
        package = client.get_cluster_software_package(target_version)
        upload = {"status": "skipped", "reason": "package_already_present", "package": package}
        emit("upload", f"ONTAP image {target_version} is already present on the cluster.", package=package)
    except Exception:
        emit("upload", f"Uploading ONTAP image {media_path.name}.")
        upload = client.upload_cluster_software(media_path)
        emit("upload", "ONTAP image upload request completed.", response=upload)
        upload_job = _extract_job_uuid(upload)
        if upload_job:
            emit("upload", f"Upload job started: {upload_job}.", job_uuid=upload_job)
            wait_for_netapp_job(client, upload_job, timeout=3600, poll_interval=15.0, progress=progress, phase="upload")
            emit("upload", f"Upload job completed: {upload_job}.", job_uuid=upload_job)

    emit("validate", f"Starting ONTAP validation for {target_version}.")
    validation = client.validate_cluster_software(target_version)
    validation_job = _extract_job_uuid(validation)
    if validation_job:
        emit("validate", f"Validation job started: {validation_job}.", job_uuid=validation_job)
    validation_status = (
        wait_for_netapp_job(client, validation_job, timeout=1800, poll_interval=15.0, progress=progress, phase="validate")
        if validation_job
        else {"state": "unknown", "raw": validation}
    )
    validation_message = str(((validation_status.get("raw") or {}).get("message")) or "")
    if not skip_warnings and ("warning" in validation_message.lower() or "error" in validation_message.lower()):
        emit("blocked", f"ONTAP validation did not pass cleanly: {validation_message or validation_status}")
        raise NetAppError(f"ONTAP upgrade validation did not pass cleanly: {validation_message or validation_status}")
    software_status = client.get_cluster_software()
    validation_errors = _format_validation_results(software_status)
    if validation_errors:
        emit(
            "blocked",
            "ONTAP validation reported blocking errors.",
            validation_results=software_status.get("validation_results") or [],
        )
        raise NetAppError("ONTAP validation reported blocking errors. " + " ".join(validation_errors))

    start: dict[str, Any] = {}
    if skip_warnings:
        emit("start", "ONTAP validation has no blocking errors; proceeding with skip_warnings=true for acknowledged warnings.")
    emit("start", f"Starting ONTAP software update to {target_version}.")
    try:
        start = client.start_cluster_software_update(target_version, skip_warnings=skip_warnings)
        start_job = _extract_job_uuid(start)
        if start_job:
            emit("start", f"Upgrade job started: {start_job}.", job_uuid=start_job)
            wait_for_netapp_job(
                client,
                start_job,
                timeout=wait_timeout,
                poll_interval=poll_interval,
                progress=progress,
                phase="upgrade",
            )
            start_status = wait_for_netapp_software_update(
                client,
                target_version,
                timeout=wait_timeout,
                poll_interval=poll_interval,
                progress=progress,
            )
        else:
            start_status = wait_for_netapp_software_update(
                client,
                target_version,
                timeout=wait_timeout,
                poll_interval=poll_interval,
                progress=progress,
            )
    except NetAppError as exc:
        latest_status: dict[str, Any] = {}
        try:
            latest_status = client.get_cluster_software()
        except Exception:
            latest_status = {}
        if _software_update_matches_target(latest_status, target_version) and _software_update_is_running(latest_status):
            emit(
                "upgrade",
                "REST start returned an error, but ONTAP reports the upgrade is running; monitoring cluster software state.",
                rest_error=str(exc),
            )
            start = {"status": "rest_error_but_running", "error": str(exc)}
            start_status = wait_for_netapp_software_update(
                client,
                target_version,
                timeout=wait_timeout,
                poll_interval=poll_interval,
                progress=progress,
            )
        elif hasattr(client, "private_cli_cluster_image_update"):
            emit(
                "start",
                "REST start did not accept acknowledged validation warnings; using ONTAP private CLI fallback.",
                rest_error=str(exc),
            )
            start = client.private_cli_cluster_image_update(
                target_version,
                ignore_validation_warning=True,
                skip_confirmation=True,
                stabilize_minutes=8,
            )
            emit("upgrade", "Private CLI update request submitted; polling cluster software state.", response=start)
            start_status = wait_for_netapp_software_update(
                client,
                target_version,
                timeout=wait_timeout,
                poll_interval=poll_interval,
                progress=progress,
            )
        else:
            raise

    result = {
        "status": "completed",
        "host": str(plan.get("host") or ""),
        "previous_version": str(plan.get("current_version") or ""),
        "target_version": target_version,
        "media_path": str(media_path),
        "media_filename": str(plan.get("media_filename") or ""),
        "upload": upload,
        "validation": validation_status,
        "start": start_status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    record_upgrade_inventory(cfg, "netapp", current_version=target_version, raw_version=target_version, source="Post-upgrade ONTAP verification")
    cfg.setdefault("netapp", {})
    cfg["netapp"].setdefault("upgrade", {})
    cfg["netapp"]["upgrade"]["last_plan"] = plan
    cfg["netapp"]["upgrade"]["last_result"] = result
    emit("complete", f"ONTAP upgrade completed to {target_version}.", result=result)
    return result


def _extract_job_uuid(payload: dict[str, Any]) -> str:
    job = dict((payload.get("job") or {}))
    uuid = str(job.get("uuid") or payload.get("uuid") or "").strip()
    if uuid:
        return uuid
    link = str(((payload.get("_links") or {}).get("self") or {}).get("href") or "").strip()
    if "/api/cluster/jobs/" in link:
        return link.rstrip("/").rsplit("/", 1)[-1]
    return ""


def wait_for_netapp_job(
    client: Any,
    uuid: str,
    *,
    timeout: int = 3600,
    poll_interval: float = 30.0,
    progress: Callable[[dict[str, Any]], None] | None = None,
    phase: str = "job",
) -> dict[str, Any]:
    def emit(message: str, **extra: Any) -> None:
        if progress:
            progress({"phase": phase, "message": message, "timestamp": datetime.now(timezone.utc).isoformat(), **extra})

    deadline = time.time() + max(timeout, 60)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = client.get_job(uuid)
        state = str(last.get("state") or "").strip().lower()
        emit(f"ONTAP job {uuid} state: {state or 'unknown'}.", job_uuid=uuid, job_state=state, job=last)
        if state in {"success", "failure"}:
            if state == "failure":
                details: dict[str, Any] = {}
                validation_lines: list[str] = []
                for _ in range(18):
                    try:
                        details = client.get_cluster_software()
                    except Exception:
                        details = {}
                    validation_lines = _format_validation_results(details)
                    software_state = str(details.get("state") or "").lower()
                    if validation_lines or software_state in {"failed", "failure"}:
                        break
                    time.sleep(5.0)
                validation_lines = _format_validation_results(details)
                if validation_lines:
                    emit(
                        "ONTAP validation details captured.",
                        job_uuid=uuid,
                        job_state=state,
                        validation_results=details.get("validation_results") or [],
                    )
                message = str(last.get("message") or f"ONTAP job {uuid} failed.")
                if validation_lines:
                    message = message + " " + " ".join(validation_lines)
                raise NetAppError(message)
            return {"state": state, "raw": last}
        time.sleep(max(poll_interval, 5.0))
    raise NetAppError(f"Timed out waiting for ONTAP job {uuid}. Last state: {str(last.get('state') or 'unknown')}")


def wait_for_netapp_software_update(
    client: Any,
    target_version: str,
    *,
    timeout: int = 14400,
    poll_interval: float = 30.0,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    def emit(message: str, payload: dict[str, Any], **extra: Any) -> None:
        if not progress:
            return
        elapsed = _duration_seconds(payload.get("elapsed_duration"))
        estimated = _duration_seconds(payload.get("estimated_duration"))
        percent = 85
        if elapsed is not None and estimated and estimated > 0:
            percent = min(99, max(70, 70 + int((elapsed / estimated) * 29)))
        progress(
            {
                "phase": "upgrade",
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "progress_percent": percent,
                "software_state": str(payload.get("state") or ""),
                "pending_version": str(payload.get("pending_version") or ""),
                "current_version": str(payload.get("version") or ""),
                "elapsed_duration": payload.get("elapsed_duration"),
                "estimated_duration": payload.get("estimated_duration"),
                "status_details": payload.get("status_details") or [],
                "update_details": payload.get("update_details") or [],
                **extra,
            }
        )

    deadline = time.time() + max(timeout, 60)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = client.get_cluster_software()
        except Exception as exc:
            if progress:
                progress(
                    {
                        "phase": "upgrade",
                        "message": f"Waiting for ONTAP API during upgrade: {exc}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "progress_percent": 85,
                    }
                )
            time.sleep(max(poll_interval, 5.0))
            continue

        current = str(last.get("version") or "").strip()
        pending = str(last.get("pending_version") or "").strip()
        state = str(last.get("state") or "").strip().lower()
        if _software_update_reached_target(last, target_version):
            emit(f"ONTAP reports target version {target_version}.", last, progress_percent=100)
            return {"state": "success", "raw": last}
        if state in {"failed", "failure", "canceled", "cancelled"}:
            validation_lines = _format_validation_results(last)
            message = f"ONTAP software update failed while targeting {target_version}."
            if validation_lines:
                message += " " + " ".join(validation_lines)
            raise NetAppError(message)
        if state == "completed" and pending and not _version_matches(current, target_version):
            validation_lines = _format_validation_results(last)
            message = f"ONTAP software update stopped at {current or 'unknown'} with pending target {pending}."
            if validation_lines:
                message += " " + " ".join(validation_lines)
            raise NetAppError(message)
        emit(
            f"ONTAP software state: {state or 'unknown'}; current {current or 'unknown'}; pending {pending or target_version}.",
            last,
        )
        time.sleep(max(poll_interval, 5.0))
    raise NetAppError(f"Timed out waiting for ONTAP software update to {target_version}.")


def _format_validation_results(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in payload.get("validation_results") or []:
        status = str(item.get("status") or "").strip()
        check = str(item.get("update_check") or "validation").strip()
        issue = str(((item.get("issue") or {}).get("message")) or "").strip()
        action = str(((item.get("action") or {}).get("message")) or "").strip()
        if status.lower() == "error":
            lines.append(f"{check}: {issue}" + (f" Action: {action}" if action else ""))
    return lines


def _software_update_is_running(payload: dict[str, Any]) -> bool:
    return str(payload.get("state") or "").strip().lower() in {"running", "in_progress", "updating"}


def _software_update_matches_target(payload: dict[str, Any], target_version: str) -> bool:
    current = str(payload.get("version") or "").strip()
    pending = str(payload.get("pending_version") or "").strip()
    if _version_matches(current, target_version) or _version_matches(pending, target_version):
        return True
    for node in payload.get("nodes") or []:
        if isinstance(node, dict) and _version_matches(str(node.get("version") or ""), target_version):
            return True
    return False


def _software_update_reached_target(payload: dict[str, Any], target_version: str) -> bool:
    if _software_update_is_running(payload):
        return False
    current = str(payload.get("version") or "").strip()
    if _version_matches(current, target_version):
        return True
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    return bool(nodes) and all(_version_matches(str(node.get("version") or ""), target_version) for node in nodes)


def _version_matches(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_text = str(left).strip().lower()
    right_text = str(right).strip().lower()
    if right_text in left_text or left_text in right_text:
        return True
    comparison = compare_versions(left, right)
    return comparison == 0


def _duration_seconds(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
