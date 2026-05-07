from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable


def resolve_storage_target_host(
    cfg: dict[str, Any],
    *,
    ensure_storage_config_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    storage_cfg = ensure_storage_config_fn(cfg)
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


def promote_final_ilo_endpoint(
    cfg: dict[str, Any],
    *,
    resolve_ilo_control_host_fn: Callable[[dict[str, Any]], str],
    final_ip: str | None = None,
) -> dict[str, Any]:
    final = str(final_ip or resolve_ilo_control_host_fn(cfg) or "").strip()
    if final:
        cfg.setdefault("ilo", {})["current_ip"] = final
        cfg.setdefault("ilo", {})["host"] = final
    return cfg


def resolve_storage_target_credentials(
    cfg: dict[str, Any],
    *,
    ensure_storage_config_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    storage_cfg = ensure_storage_config_fn(cfg)
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


def storage_item_display_name(item: dict[str, Any]) -> str:
    return str(item.get("logical_drive_name") or item.get("name") or item.get("id") or "").strip()


def ensure_storage_config(cfg: dict[str, Any]) -> dict[str, Any]:
    storage_cfg = cfg.setdefault("storage", {})
    approval = storage_cfg.setdefault("approval", {})
    storage_cfg.setdefault("target_host_override", "")
    storage_cfg.setdefault("username", "")
    storage_cfg.setdefault("password", "")
    storage_cfg.setdefault("allow_unverified_standard_redfish_create", False)
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
    *,
    discovery: dict[str, Any] | None = None,
    discovery_paths: dict[str, Path] | None = None,
    plan: dict[str, Any] | None = None,
    plan_paths: dict[str, Path] | None = None,
    storage_discovery_fingerprint_fn: Callable[[dict[str, Any]], str],
    storage_plan_summary_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    storage_cfg = ensure_storage_config(cfg)
    if discovery is not None and discovery_paths is not None:
        storage_cfg["latest_discovery_raw_path"] = str(discovery_paths["raw"])
        storage_cfg["latest_discovery_fingerprint"] = storage_discovery_fingerprint_fn(discovery)
        summary = discovery.get("summary", {}) or {}
        storage_cfg["latest_host"] = str(
            (discovery.get("raw", {}) or {}).get("source_host") or summary.get("source_host") or cfg.get("ilo", {}).get("current_ip") or ""
        )
        storage_cfg["latest_serial_number"] = str((summary.get("server", {}) or {}).get("serial_number") or "")
    if plan is not None and plan_paths is not None:
        storage_cfg["latest_plan_path"] = str(plan_paths["plan"])
        storage_cfg["latest_plan_summary"] = storage_plan_summary_fn(plan)
    refresh_storage_approval_from_saved_state(cfg)
    if storage_cfg.get("state") == "idle":
        if storage_cfg.get("latest_plan_path"):
            storage_cfg["state"] = "planned"
        elif storage_cfg.get("latest_discovery_raw_path"):
            storage_cfg["state"] = "discovered"


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


def clear_storage_plan_selection_state(cfg: dict[str, Any]) -> None:
    storage_cfg = ensure_storage_config(cfg)
    storage_cfg["latest_plan_path"] = ""
    storage_cfg["latest_plan_summary"] = {}
    clear_storage_approval_for_cfg(cfg)
    if storage_cfg.get("latest_discovery_raw_path"):
        storage_cfg["state"] = "discovered"
    else:
        storage_cfg["state"] = "idle"
    storage_cfg["status_reason"] = ""


def is_storage_drive_controller_mismatch_error(message: str) -> bool:
    text = str(message or "").lower()
    return "controller mismatch" in text or (
        "selected storage drives must all belong to the chosen controller" in text
        or ("drive" in text and "controller" in text and "not found in the current inventory" in text)
        or ("data controller is set to" in text)
        or ("os controller is set to" in text)
    )


def approve_storage_plan_for_cfg(
    cfg: dict[str, Any],
    *,
    discovery: dict[str, Any],
    discovery_paths: dict[str, Path],
    plan: dict[str, Any],
    plan_paths: dict[str, Path],
    include_in_ilo_run: bool,
    storage_discovery_fingerprint_fn: Callable[[dict[str, Any]], str],
    storage_plan_summary_fn: Callable[[dict[str, Any]], dict[str, Any]],
    update_storage_latest_state_fn: Callable[..., None],
    db_persist_storage_plan_fn: Callable[..., None] | None = None,
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
            "discovery_fingerprint": storage_discovery_fingerprint_fn(discovery),
            "plan_summary": storage_plan_summary_fn(plan),
            "reboot_expected": True,
        }
    )
    storage_cfg["include_in_ilo_run"] = bool(include_in_ilo_run)
    update_storage_latest_state_fn(
        cfg,
        discovery=discovery,
        discovery_paths=discovery_paths,
        plan=plan,
        plan_paths=plan_paths,
    )
    if db_persist_storage_plan_fn is not None:
        try:
            db_persist_storage_plan_fn(
                cfg,
                discovery=discovery,
                discovery_paths=discovery_paths,
                plan=plan,
                plan_paths=plan_paths,
                approved=True,
            )
        except Exception:
            pass
    storage_cfg["state"] = "approved"
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


def build_storage_page_readiness(
    storage_review: dict[str, Any],
    storage_target: dict[str, Any],
    storage_credentials: dict[str, Any],
    storage_execution_status: dict[str, Any],
    storage_export_paths: dict[str, Path] | None,
) -> list[dict[str, str]]:
    return [
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


def build_storage_change_summary(
    storage_review: dict[str, Any],
    storage_plan: dict[str, Any] | None,
    *,
    storage_plan_summary_fn: Callable[[dict[str, Any]], dict[str, Any]],
    raid_label_fn: Callable[[str], str],
) -> list[dict[str, str]]:
    approval = storage_review.get("approval", {}) or {}
    if storage_plan:
        plan_summary = approval.get("plan_summary", {}) or storage_plan_summary_fn(storage_plan or {})
    else:
        plan_summary = approval.get("plan_summary", {}) or {}
    array_lines = []
    for entry in list(plan_summary.get("arrays") or []):
        serials = ", ".join([item for item in list(entry.get("selected_drive_serials") or []) if item][:3])
        if len(list(entry.get("selected_drive_serials") or [])) > 3:
            serials += ", ..."
        array_lines.append(
            f"{str(entry.get('role') or '').upper()} {raid_label_fn(str(entry.get('raid_level') or ''))}: "
            f"{entry.get('controller') or entry.get('controller_path') or 'Not set'} | "
            f"bays {entry.get('bays') or 'none'} | "
            f"serials {serials or 'none'}"
        )
    spare = plan_summary.get("hot_spare") or {}
    spare_text = (
        f"{spare.get('controller') or 'Not reserved'} | bay {spare.get('bay') or 'none'} | serial {spare.get('serial_number') or 'none'}"
        if spare.get("path") or spare.get("serial_number") or spare.get("bay")
        else "Not reserved"
    )
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
            "after": " | ".join(array_lines + [f"Hot spare {spare_text}"]) if array_lines else f"Hot spare {spare_text}",
            "verify": "Use the exact approved plan artifact during the real run.",
        },
        {
            "name": "Apply confirmation",
            "before": "No destructive changes have been made yet on this page.",
            "after": f"Restart expected: {'Yes' if reboot_expected else 'No'} | Included in iLO run: {'Yes' if storage_review.get('include_in_ilo_run') else 'No'}",
            "verify": "Capture post-change storage discovery and validate the result after any required restart.",
        },
    ]
