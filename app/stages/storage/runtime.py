from __future__ import annotations

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
