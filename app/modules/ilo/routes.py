from __future__ import annotations

import re
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from app.core.forms import preserve_secret
from app.modules.ilo.service import default_ilo_module_service


router = APIRouter()


IloRuntime = dict[str, Callable[..., Any]]


def _ilo_service(runtime: IloRuntime):
    return default_ilo_module_service(
        {
            "normalize_ilo_hostname": runtime["normalize_ilo_hostname"],
            "extract_ilo_additional_users_from_form": runtime["extract_ilo_additional_users_from_form"],
            "normalize_ilo_policy": runtime["normalize_ilo_policy"],
        }
    )


async def ilo_page_handler(request: Request, runtime: IloRuntime):
    cfg = runtime["load_kit_config"]()
    return runtime["render_page"](request, cfg, active_page="ilo")


async def save_ilo_settings_handler(
    request: Request,
    runtime: IloRuntime,
    return_page: str = Form("ilo"),
    ilo_current_ip: str = Form(""),
    ilo_target_ip: str = Form(""),
    ilo_gateway: str = Form(""),
    ilo_hostname: str = Form(""),
    ilo_username: str = Form(""),
    ilo_password: str = Form(""),
    ilo_discover_start_octet: str = Form("21"),
    ilo_discover_end_octet: str = Form("29"),
    ilo_policy_apply_standard_policy: str | None = Form(None),
    ilo_policy_enable_standard_accounts: str | None = Form(None),
    ilo_policy_enable_license_check: str | None = Form(None),
    ilo_policy_enable_snmp_policy: str | None = Form(None),
    ilo_policy_enable_alert_destinations: str | None = Form(None),
    ilo_policy_enable_ipv6_disable: str | None = Form(None),
    ilo_policy_enable_time_policy: str | None = Form(None),
    ilo_policy_enable_auto_reset: str | None = Form(None),
    ilo_policy_kit_admin_password: str = Form(""),
    ilo_policy_kit_operator_password: str = Form(""),
    ilo_policy_shared_admin_username: str = Form("765CS"),
    ilo_policy_shared_admin_password: str = Form(""),
    ilo_policy_snmp_read_community: str = Form(""),
    ilo_policy_snmpv3_username: str = Form("765CS"),
    ilo_policy_snmpv3_auth_protocol: str = Form("SHA"),
    ilo_policy_snmpv3_auth_password: str = Form(""),
    ilo_policy_snmpv3_priv_protocol: str = Form("AES"),
    ilo_policy_snmpv3_priv_password: str = Form(""),
    ilo_policy_alert_destinations: str = Form("192.168.1.67, 192.168.1.68"),
):
    cfg = runtime["load_kit_config"]()
    form = await request.form()
    service = _ilo_service(runtime)
    existing_ilo = cfg.get("ilo", {}) or {}
    existing_policy = runtime["normalize_ilo_policy"](existing_ilo.get("policy"))
    policy_updates = {
            "discover_start_octet": ilo_discover_start_octet,
            "discover_end_octet": ilo_discover_end_octet,
            "apply_standard_policy": ilo_policy_apply_standard_policy == "on",
            "enable_standard_accounts": ilo_policy_enable_standard_accounts == "on",
            "enable_license_check": ilo_policy_enable_license_check == "on",
            "enable_snmp_policy": ilo_policy_enable_snmp_policy == "on",
            "enable_alert_destinations": ilo_policy_enable_alert_destinations == "on",
            "enable_ipv6_disable": ilo_policy_enable_ipv6_disable == "on",
            "enable_time_policy": ilo_policy_enable_time_policy == "on",
            "enable_auto_reset": ilo_policy_enable_auto_reset == "on",
            "kit_admin_password": preserve_secret(ilo_policy_kit_admin_password, existing_policy.get("kit_admin_password")),
            "kit_operator_password": preserve_secret(ilo_policy_kit_operator_password, existing_policy.get("kit_operator_password")),
            "shared_admin_username": ilo_policy_shared_admin_username.strip() or "765CS",
            "shared_admin_password": preserve_secret(ilo_policy_shared_admin_password, existing_policy.get("shared_admin_password")),
            "snmp_read_community": preserve_secret(ilo_policy_snmp_read_community, existing_policy.get("snmp_read_community")),
            "snmpv3_username": ilo_policy_snmpv3_username.strip() or "765CS",
            "snmpv3_auth_protocol": ilo_policy_snmpv3_auth_protocol.strip() or "SHA",
            "snmpv3_auth_password": preserve_secret(ilo_policy_snmpv3_auth_password, existing_policy.get("snmpv3_auth_password")),
            "snmpv3_priv_protocol": ilo_policy_snmpv3_priv_protocol.strip() or "AES",
            "snmpv3_priv_password": preserve_secret(ilo_policy_snmpv3_priv_password, existing_policy.get("snmpv3_priv_password")),
            "alert_destinations": [
                item.strip()
                for item in re.split(r"[\s,]+", str(ilo_policy_alert_destinations or "").strip())
                if item.strip()
            ],
    }
    updated = service.update_saved_ilo_settings(
        cfg,
        {
            "form": form,
            "ilo_current_ip": ilo_current_ip,
            "ilo_target_ip": ilo_target_ip,
            "ilo_gateway": ilo_gateway,
            "ilo_hostname": ilo_hostname,
            "ilo_username": ilo_username,
            "ilo_password": preserve_secret(ilo_password, existing_ilo.get("password")),
            "policy_updates": policy_updates,
            "ilo_policy_snmp_read_community": policy_updates["snmp_read_community"],
        },
    )
    cfg = updated["cfg"]
    normalized_hostname = str(updated["normalized_hostname"] or "")
    core_ilo_input_review = runtime["build_ilo_input_review"](cfg, include_policy_validation=False)
    if core_ilo_input_review["errors"]:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            message=(
                f"Normalized iLO hostname to: {normalized_hostname}"
                if ilo_hostname.strip() and ilo_hostname.strip() != normalized_hostname
                else None
            ),
            action_feedback=runtime["build_action_feedback"](
                "iLO setup needs attention",
                "Fix the iLO user names or passwords before saving this page.",
                tone="pending",
                outcomes=[
                    f"Current iLO address: {cfg['ilo'].get('current_ip') or 'Not set'}",
                    f"Planned final IP: {cfg['ilo'].get('target_ip') or 'Unchanged'}",
                    f"Hostname: {normalized_hostname or 'Not set'}",
                ],
                details=list(core_ilo_input_review["errors"]) + list(core_ilo_input_review["notes"]),
            ),
        )
    ilo_input_review = runtime["build_ilo_input_review"](cfg, include_policy_validation=True)
    policy_details = list(ilo_input_review["errors"]) + list(ilo_input_review["notes"])
    core_details = set(list(core_ilo_input_review["errors"]) + list(core_ilo_input_review["notes"]))
    policy_warnings = [detail for detail in policy_details if detail not in core_details]
    try:
        cfg = runtime["apply_ip_plan"](cfg)
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"Could not save iLO setup: {e}")
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "ilo_settings_saved",
        workflow="ilo",
        summary="Saved the current iLO address and planned iLO settings.",
        target=cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or "",
        details=[
            f"Planned final IP: {cfg['ilo'].get('target_ip') or 'Unchanged'}",
            f"Gateway: {cfg['ilo'].get('gateway') or 'Not set'}",
            f"Hostname: {normalized_hostname or 'Not set'}",
        ],
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        message=(
            f"Normalized iLO hostname to: {normalized_hostname}"
            if ilo_hostname.strip() and ilo_hostname.strip() != normalized_hostname
            else None
        ),
        action_feedback=runtime["build_action_feedback"](
            "iLO setup saved with warnings" if policy_warnings else "iLO setup saved",
            (
                "Updated the saved iLO target and local sign-in settings, but some optional standard-policy secrets still need attention."
                if policy_warnings
                else "Updated the saved iLO target and local sign-in settings for this kit."
            ),
            tone="pending" if policy_warnings else "ready",
            outcomes=[
                f"Target: {cfg['ilo'].get('current_ip') or cfg['ilo'].get('host', '') or 'Not set'}",
                f"Planned final IP: {cfg['ilo'].get('target_ip') or 'Unchanged'}",
                f"Gateway: {cfg['ilo'].get('gateway') or 'Not set'}",
            ],
            details=policy_warnings,
            links=[{"label": "Open Storage setup", "href": "/storage"}, {"label": "Review run prep", "href": "/execution"}],
        ),
    )


def register_module_routes(app: FastAPI) -> None:
    # iLO routes are still served by legacy app/main.py endpoints during migration.
    _ = app
