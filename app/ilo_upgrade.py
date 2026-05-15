from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any, Callable

from app.ilo import ILOError
from app.upgrade_helper import (
    build_upgrade_inventory,
    compare_versions,
    infer_ilo_family,
    infer_ilo_media_family,
    record_upgrade_inventory,
    select_upgrade_candidate,
)


def _normalize_version(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    generic = re.search(r"(\d+(?:\.\d+){1,3}[A-Za-z]?\d*)", text)
    return generic.group(1) if generic else text


def build_ilo_upgrade_plan(cfg: dict[str, Any], media_scan: dict[str, Any]) -> dict[str, Any]:
    inventory = build_upgrade_inventory(cfg)
    ilo_inventory = dict(inventory.get("ilo") or {})
    manager_model = str(ilo_inventory.get("manager_model") or "").strip()
    current_version = _normalize_version(str(ilo_inventory.get("current_version") or ""))
    current_source = str(ilo_inventory.get("source") or "").strip()
    host = str(((cfg.get("ilo") or {}).get("current_ip") or (cfg.get("ilo") or {}).get("host") or "")).strip()
    username = str(((cfg.get("ilo") or {}).get("username") or "")).strip()
    password_present = bool(str(((cfg.get("ilo") or {}).get("password") or "")))

    selected = select_upgrade_candidate(media_scan, "ilo", {"manager_model": manager_model})
    media_version = _normalize_version(str(selected.get("version") or ""))
    media_path = str(selected.get("path") or "").strip()
    media_filename = str(selected.get("filename") or "").strip()
    manager_family = infer_ilo_family(manager_model)
    media_family = infer_ilo_media_family(media_filename)
    comparison = compare_versions(current_version, media_version) if current_version and media_version else None

    blockers: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    if manager_family:
        notes.append(f"Detected manager family: {manager_family}")
    if current_version:
        notes.append(f"Current iLO version: {current_version}")
    if media_version:
        notes.append(f"Matched media version: {media_version}")
    if media_path:
        notes.append(f"Matched media file: {media_path}")
    if current_source:
        notes.append(f"Current version source: {current_source}")

    if not host:
        blockers.append("Current iLO address is not set.")
    if not username or not password_present:
        blockers.append("Saved iLO credentials are incomplete.")
    if not manager_model:
        blockers.append("Read current iLO first so the app can identify the iLO family.")
    if not current_version:
        blockers.append("Current iLO firmware version is unknown.")
    if not media_version or not media_path:
        blockers.append("No approved iLO firmware package was found under the media directory.")
    if manager_family and media_family and manager_family != media_family:
        blockers.append(f"Detected {manager_family}, but matched media is for {media_family}.")
    elif manager_family and media_filename and not media_family:
        warnings.append(f"Matched media filename does not clearly encode an iLO family for detected {manager_family}.")
    if comparison is not None and comparison >= 0:
        warnings.append("Current iLO version is already equal to or newer than the matched media.")

    ready = not blockers and comparison is not None and comparison < 0
    if ready:
        notes.append("Upgrade can proceed through the iLO Redfish update service.")

    return {
        "ready": ready,
        "host": host,
        "username": username,
        "password_present": password_present,
        "manager_model": manager_model,
        "manager_family": manager_family,
        "current_version": current_version,
        "current_source": current_source,
        "media_version": media_version,
        "media_filename": media_filename,
        "media_path": media_path,
        "media_family": media_family,
        "comparison": comparison,
        "blockers": blockers,
        "warnings": warnings,
        "notes": notes,
        "planned_at": datetime.now(timezone.utc).isoformat(),
    }


def execute_ilo_upgrade(
    cfg: dict[str, Any],
    media_scan: dict[str, Any],
    *,
    build_client: Callable[..., Any],
    wait_timeout: int = 1800,
    poll_interval: float = 15.0,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    plan = build_ilo_upgrade_plan(cfg, media_scan)
    if progress:
        progress(
            {
                "phase": "precheck",
                "message": "iLO upgrade prechecks completed.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "progress_percent": 10,
            }
        )
    if not plan.get("ready"):
        raise ILOError("; ".join(list(plan.get("blockers") or []) or ["iLO upgrade prechecks are not satisfied."]))

    host = str(plan.get("host") or "")
    username = str(plan.get("username") or "")
    password = str(((cfg.get("ilo") or {}).get("password") or ""))
    client = build_client(host=host, username=username, password=password)
    media_path = Path(str(plan.get("media_path") or ""))
    expected_version = str(plan.get("media_version") or "")

    if progress:
        progress(
            {
                "phase": "upload",
                "message": f"Uploading {media_path.name} to iLO update service.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "progress_percent": 25,
            }
        )
    upload = client.upload_firmware_component(media_path, update_repository=True, update_target=True)
    update_status = wait_for_ilo_update_service(
        client,
        expected_version=expected_version,
        media_filename=str(plan.get("media_filename") or media_path.name),
        timeout=wait_timeout,
        poll_interval=poll_interval,
        progress=progress,
    )
    reset_result: dict[str, Any] = {}
    if update_status.get("activation") == "AfterDeviceReset":
        if progress:
            progress(
                {
                    "phase": "reset",
                    "message": "Firmware flash completed. Resetting iLO so the new firmware can activate.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "progress_percent": 70,
                }
            )
        reset_result = request_ilo_activation_reset(client)
    if progress:
        progress(
            {
                "phase": "verify",
                "message": "Firmware flash completed. Waiting for iLO to report the target version.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "progress_percent": 75,
            }
        )
    final = wait_for_ilo_firmware_version(
        client,
        expected_version=expected_version,
        timeout=wait_timeout,
        poll_interval=poll_interval,
        progress=progress,
    )
    result = {
        "status": "completed",
        "host": host,
        "manager_model": plan.get("manager_model") or "",
        "previous_version": plan.get("current_version") or "",
        "target_version": expected_version,
        "media_path": str(media_path),
        "media_filename": str(plan.get("media_filename") or ""),
        "upload": upload,
        "update_service": update_status,
        "reset": reset_result,
        "verification": final,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    record_upgrade_inventory(
        cfg,
        "ilo",
        current_version=str(final.get("current_version") or expected_version),
        raw_version=str(final.get("raw_version") or final.get("current_version") or expected_version),
        source="Post-upgrade iLO verification",
        manager_model=str(plan.get("manager_model") or ""),
    )
    cfg.setdefault("ilo", {})
    cfg["ilo"].setdefault("upgrade", {})
    cfg["ilo"]["upgrade"]["last_plan"] = plan
    cfg["ilo"]["upgrade"]["last_result"] = result
    if progress:
        progress(
            {
                "phase": "complete",
                "message": f"iLO firmware verified at {result['target_version']}.",
                "timestamp": result["completed_at"],
                "progress_percent": 100,
            }
        )
    return result


def wait_for_ilo_update_service(
    client: Any,
    *,
    expected_version: str,
    media_filename: str = "",
    timeout: int = 1800,
    poll_interval: float = 15.0,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not hasattr(client, "get_update_service"):
        return {"status": "not_available", "activation": ""}

    deadline = time.time() + max(timeout, 60)
    last_state = ""
    last_progress: int | None = None
    last_error = ""
    activation = ""
    component = ""

    while time.time() < deadline:
        try:
            service = client.get_update_service()
            hpe = ((service.get("Oem") or {}).get("Hpe") or {})
            last_state = str(hpe.get("State") or "").strip()
            try:
                last_progress = int(hpe.get("FlashProgressPercent")) if hpe.get("FlashProgressPercent") is not None else last_progress
            except (TypeError, ValueError):
                pass
            component_info = find_ilo_repository_component(client, expected_version=expected_version, media_filename=media_filename)
            activation = str(component_info.get("activation") or activation or "").strip()
            component = str(component_info.get("filename") or component or "").strip()
            if progress:
                detail = f"iLO update service state {last_state or 'unknown'}"
                if last_progress is not None:
                    detail += f", flash {last_progress}%"
                if activation:
                    detail += f", activates {activation}"
                progress(
                    {
                        "phase": "flash",
                        "message": detail + ".",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "progress_percent": max(35, min(65, int(last_progress or 0) if last_progress is not None else 55)),
                        "update_state": last_state,
                        "flash_progress_percent": last_progress,
                        "activation": activation,
                    }
                )
            if last_state.lower() in {"complete", "completed", "idle"} or (last_progress is not None and last_progress >= 100):
                return {
                    "status": "complete",
                    "state": last_state,
                    "flash_progress_percent": last_progress,
                    "activation": activation,
                    "component": component,
                }
            last_error = ""
        except Exception as exc:
            last_error = str(exc).splitlines()[0]
            if progress:
                progress(
                    {
                        "phase": "flash",
                        "message": f"Waiting for iLO update service: {last_error or 'temporarily unreachable'}.",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "progress_percent": 50,
                    }
                )
            try:
                client._reset_transport()  # type: ignore[attr-defined]
            except Exception:
                pass
        time.sleep(max(poll_interval, 1.0))

    raise ILOError(
        f"Timed out waiting for iLO update service to complete. "
        f"Last state: {last_state or 'unknown'}. "
        f"Last flash progress: {last_progress if last_progress is not None else 'unknown'}. "
        f"Last error: {last_error or 'none'}."
    )


def find_ilo_repository_component(client: Any, *, expected_version: str, media_filename: str = "") -> dict[str, Any]:
    if not hasattr(client, "_get"):
        return {}
    try:
        collection = client._get("/redfish/v1/UpdateService/ComponentRepository/")  # type: ignore[attr-defined]
    except Exception:
        return {}
    members = list((collection or {}).get("Members") or [])
    expected = _normalize_version(expected_version)
    wanted_name = str(media_filename or "").strip().lower()
    best: dict[str, Any] = {}
    for member in members:
        path = str((member or {}).get("@odata.id") or "").strip()
        if not path:
            continue
        try:
            item = client._get(path)  # type: ignore[attr-defined]
        except Exception:
            continue
        filename = str(item.get("Filename") or item.get("Filepath") or "").strip()
        version = _normalize_version(str(item.get("Version") or ""))
        if wanted_name and filename.lower() == wanted_name:
            best = item
            break
        if expected and version == expected and str(item.get("Name") or "").strip().lower().startswith("ilo"):
            best = item
    if not best:
        return {}
    return {
        "filename": str(best.get("Filename") or best.get("Filepath") or "").strip(),
        "version": _normalize_version(str(best.get("Version") or "")),
        "activation": str(best.get("Activates") or "").strip(),
        "component_uri": str(best.get("ComponentUri") or "").strip(),
    }


def request_ilo_activation_reset(client: Any) -> dict[str, Any]:
    if not hasattr(client, "reset_ilo"):
        return {"status": "not_available"}
    try:
        result = client.reset_ilo(reset_type="GracefulRestart")
        return {"status": "requested", **(result or {})}
    except Exception as exc:
        message = str(exc).splitlines()[0]
        lowered = message.lower()
        if any(text in lowered for text in ["connection aborted", "connection reset", "remote end closed", "remotedisconnected", "read timed out", "temporarily unreachable"]):
            return {"status": "disconnect_after_request", "message": message}
        raise


def wait_for_ilo_firmware_version(
    client: Any,
    *,
    expected_version: str,
    timeout: int = 1800,
    poll_interval: float = 15.0,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + max(timeout, 60)
    saw_disconnect = False
    last_error = ""
    last_version = ""

    while time.time() < deadline:
        try:
            summary = client.get_summary()
            last_version = _normalize_version(str(summary.get("manager_firmware") or ""))
            if last_version:
                last_error = ""
                if progress:
                    progress(
                        {
                            "phase": "verify",
                            "message": f"iLO reports firmware {last_version}; waiting for {expected_version}.",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "progress_percent": 75,
                        }
                    )
            if compare_versions(last_version, expected_version) is not None and compare_versions(last_version, expected_version) >= 0:
                return {
                    "status": "verified",
                    "current_version": last_version,
                    "raw_version": str(summary.get("manager_firmware") or ""),
                    "saw_disconnect": saw_disconnect,
                }
        except Exception as exc:  # best-effort during reboot window
            saw_disconnect = True
            last_error = str(exc).splitlines()[0]
            if progress:
                progress(
                    {
                        "phase": "verify",
                        "message": f"Waiting for iLO to return: {last_error or 'temporarily unreachable'}.",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "progress_percent": 70,
                    }
                )
            try:
                client._reset_transport()  # type: ignore[attr-defined]
            except Exception:
                pass
        time.sleep(max(poll_interval, 1.0))

    raise ILOError(
        f"Timed out waiting for iLO firmware version {expected_version}. "
        f"Last seen version: {last_version or 'unknown'}. "
        f"Last error: {last_error or 'none'}."
    )
