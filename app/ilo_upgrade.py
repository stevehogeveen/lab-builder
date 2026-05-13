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
) -> dict[str, Any]:
    plan = build_ilo_upgrade_plan(cfg, media_scan)
    if not plan.get("ready"):
        raise ILOError("; ".join(list(plan.get("blockers") or []) or ["iLO upgrade prechecks are not satisfied."]))

    host = str(plan.get("host") or "")
    username = str(plan.get("username") or "")
    password = str(((cfg.get("ilo") or {}).get("password") or ""))
    client = build_client(host=host, username=username, password=password)
    media_path = Path(str(plan.get("media_path") or ""))
    expected_version = str(plan.get("media_version") or "")

    upload = client.upload_firmware_component(media_path, update_repository=True, update_target=True)
    final = wait_for_ilo_firmware_version(
        client,
        expected_version=expected_version,
        timeout=wait_timeout,
        poll_interval=poll_interval,
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
    return result


def wait_for_ilo_firmware_version(
    client: Any,
    *,
    expected_version: str,
    timeout: int = 1800,
    poll_interval: float = 15.0,
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
