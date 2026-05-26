from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any

import yaml

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from app.cisco_upgrade import build_cisco_upgrade_plan, execute_cisco_factory_reset, execute_cisco_upgrade
from app.modules.cisco.service import CiscoModuleService
from app.upgrade_helper import record_upgrade_inventory
from app.upgrade_panels import build_cisco_upgrade_panel


router = APIRouter()
service = CiscoModuleService()


def _record_cisco_upgrade_activity(cfg: dict[str, Any], event: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    from app import main

    progress_by_phase = {
        "queued": 5,
        "precheck": 10,
        "transfer": 25,
        "install": 60,
        "reload": 75,
        "verify": 85,
        "complete": 100,
        "blocked": 100,
        "failed": 100,
    }
    cfg.setdefault("cisco_switch", {}).setdefault("upgrade", {})
    activity = cfg["cisco_switch"]["upgrade"].setdefault("activity", {})
    events = list(activity.get("events") or [])
    events.append(event)
    phase = str(event.get("phase") or activity.get("phase") or "")
    try:
        progress_percent = int(event.get("progress_percent")) if event.get("progress_percent") is not None else progress_by_phase.get(phase, int(activity.get("progress_percent") or 0))
    except (TypeError, ValueError):
        progress_percent = progress_by_phase.get(phase, int(activity.get("progress_percent") or 0))
    activity.update(
        {
            "status": status or activity.get("status") or "running",
            "phase": phase,
            "message": event.get("message") or activity.get("message") or "",
            "updated_at": event.get("timestamp") or activity.get("updated_at") or "",
            "events": events[-80:],
            "progress_percent": max(0, min(100, progress_percent)),
        }
    )
    if not activity.get("started_at"):
        activity["started_at"] = event.get("timestamp") or datetime.now(timezone.utc).isoformat()
    main.save_kit_config(cfg)
    return activity


def _start_cisco_upgrade_worker(cfg: dict[str, Any]) -> None:
    from app import main

    def progress(event: dict[str, Any]) -> None:
        _record_cisco_upgrade_activity(cfg, event, status="running")

    def worker() -> None:
        try:
            result = execute_cisco_upgrade(cfg, main.scan_upgrade_media(), progress=progress)
            cfg.setdefault("cisco_switch", {}).setdefault("upgrade", {})["last_result"] = result
            _record_cisco_upgrade_activity(
                cfg,
                {"phase": "complete", "message": "Cisco upgrade command completed.", "timestamp": result.get("completed_at") or datetime.now(timezone.utc).isoformat(), "progress_percent": 100},
                status="completed",
            )
            main.save_kit_config(cfg)
        except Exception as exc:
            error = str(exc).strip() or "Cisco upgrade failed."
            cfg.setdefault("cisco_switch", {}).setdefault("upgrade", {})["last_result"] = {"status": "failed", "error": error, "failed_at": datetime.now(timezone.utc).isoformat()}
            _record_cisco_upgrade_activity(
                cfg,
                {"phase": "failed", "message": error, "timestamp": datetime.now(timezone.utc).isoformat(), "progress_percent": 100},
                status="failed",
            )
            main.save_kit_config(cfg)

    threading.Thread(target=worker, name="cisco-upgrade-worker", daemon=True).start()


def _module_context() -> dict:
    from app import main

    return {"cfg": main.load_kit_config()}


def _render_cisco_page(request: Request, cfg: dict, *, action_feedback: dict | None = None) -> HTMLResponse:
    from app import main

    context = {"cfg": cfg}
    return main.render_page(
        request,
        cfg,
        active_page="cisco",
        action_feedback=action_feedback,
        extra_context={"cisco_payload": service.status(context), "suppress_action_feedback_banner": True},
    )


def _store_cisco_upgrade_plan(cfg: dict, plan: dict) -> None:
    cfg.setdefault("cisco_switch", {})
    cfg["cisco_switch"].setdefault("upgrade", {})
    cfg["cisco_switch"]["upgrade"]["last_plan"] = plan


def _parse_yaml_value(raw: str, default: Any) -> Any:
    text = str(raw or "").strip()
    if not text:
        return default
    parsed = yaml.safe_load(text)
    return parsed if parsed is not None else default


def _split_multiline(raw: str) -> list[str]:
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def _coerce_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _update_cisco_model_from_form(cisco_cfg: dict, form: dict[str, Any], cfg: dict[str, Any] | None = None) -> None:
    for form_key, cfg_key in (
        ("cisco_switch_hostname", "hostname"),
        ("cisco_management_ip", "management_ip"),
        ("cisco_subnet_mask", "subnet_mask"),
        ("cisco_gateway", "gateway"),
        ("cisco_domain_name", "domain_name"),
        ("cisco_console_port", "console_port"),
        ("cisco_management_port", "management_port"),
        ("cisco_management_port_mode", "management_port_mode"),
        ("cisco_apply_mode", "apply_mode"),
    ):
        if form_key in form:
            cisco_cfg[cfg_key] = str(form.get(form_key) or "").strip()
    if cisco_cfg.get("management_ip"):
        cisco_cfg["ip"] = cisco_cfg["management_ip"]
    if form.get("cisco_management_vlan"):
        cisco_cfg["management_vlan"] = int(str(form.get("cisco_management_vlan") or "10"))
    if form.get("cisco_console_baud"):
        cisco_cfg["console_baud"] = int(str(form.get("cisco_console_baud") or "9600"))
    if form.get("cisco_switch_username"):
        cisco_cfg["username"] = str(form.get("cisco_switch_username") or "").strip()
    if form.get("cisco_switch_password"):
        cisco_cfg["password"] = str(form.get("cisco_switch_password") or "")
    if form.get("cisco_console_password"):
        cisco_cfg["console_password"] = str(form.get("cisco_console_password") or "")
    enable_secret = str(form.get("cisco_enable_secret") or form.get("cisco_enable_password") or "")
    if enable_secret:
        cisco_cfg["enable_password"] = enable_secret
    if "cisco_trusted_console_adapter" in form:
        cisco_cfg["trusted_console_adapter"] = _coerce_bool(form.get("cisco_trusted_console_adapter"))
    if form.get("cisco_dns_servers") is not None:
        cisco_cfg["dns_servers"] = _split_multiline(str(form.get("cisco_dns_servers") or ""))
    if form.get("cisco_ntp_servers") is not None:
        cisco_cfg["ntp_servers"] = _split_multiline(str(form.get("cisco_ntp_servers") or ""))
    for form_key, cfg_key, default in (
        ("cisco_vlans_yaml", "vlans", []),
        ("cisco_port_profiles_yaml", "port_profiles", {}),
        ("cisco_ports_yaml", "ports", {}),
        ("cisco_custom_port_commands_yaml", "custom_port_commands", {}),
    ):
        if form_key in form:
            cisco_cfg[cfg_key] = _parse_yaml_value(str(form.get(form_key) or ""), default)
    if "cisco_custom_global_commands" in form:
        cisco_cfg["custom_global_commands"] = _split_multiline(str(form.get("cisco_custom_global_commands") or ""))
    snmp_form_keys = {
        "cisco_snmp_v3_username",
        "cisco_snmp_v3_auth_protocol",
        "cisco_snmp_v3_auth_password",
        "cisco_snmp_v3_priv_protocol",
        "cisco_snmp_v3_priv_password",
    }
    if any(key in form for key in snmp_form_keys):
        existing = dict(cisco_cfg.get("snmp") or {})
        shared = dict((cfg or {}).get("shared_snmp") or {})
        snmp_cfg = {
            "v3_username": str(form.get("cisco_snmp_v3_username") or existing.get("v3_username") or shared.get("v3_username") or "").strip(),
            "v3_auth_protocol": str(form.get("cisco_snmp_v3_auth_protocol") or existing.get("v3_auth_protocol") or shared.get("v3_auth_protocol") or "SHA").strip() or "SHA",
            "v3_auth_password": str(form.get("cisco_snmp_v3_auth_password") or existing.get("v3_auth_password") or shared.get("v3_auth_password") or ""),
            "v3_priv_protocol": str(form.get("cisco_snmp_v3_priv_protocol") or existing.get("v3_priv_protocol") or shared.get("v3_priv_protocol") or "AES").strip() or "AES",
            "v3_priv_password": str(form.get("cisco_snmp_v3_priv_password") or existing.get("v3_priv_password") or shared.get("v3_priv_password") or ""),
        }
        cisco_cfg["snmp"] = snmp_cfg
        if cfg is not None:
            shared_snmp = cfg.setdefault("shared_snmp", {})
            shared_snmp.update(snmp_cfg)
            users = list(shared_snmp.get("users") or [])
            if snmp_cfg["v3_username"]:
                primary = {
                    "username": snmp_cfg["v3_username"],
                    "auth_protocol": snmp_cfg["v3_auth_protocol"],
                    "auth_password": snmp_cfg["v3_auth_password"],
                    "priv_protocol": snmp_cfg["v3_priv_protocol"],
                    "priv_password": snmp_cfg["v3_priv_password"],
                }
                shared_snmp["users"] = [primary] + users[1:] if users else [primary]


@router.get("/modules/cisco", response_class=HTMLResponse)
async def cisco_module_page(request: Request):
    context = _module_context()
    return _render_cisco_page(request, context["cfg"])


@router.get("/cisco", response_class=HTMLResponse)
async def cisco_legacy_page(request: Request):
    context = _module_context()
    return _render_cisco_page(request, context["cfg"])


@router.post("/modules/cisco/discover-version", response_class=HTMLResponse)
async def cisco_discover_version(
    request: Request,
    return_page: str = Form("cisco"),
    cisco_switch_hostname: str = Form(""),
    cisco_switch_username: str = Form(""),
    cisco_switch_password: str = Form(""),
    cisco_management_ip: str = Form(""),
    cisco_console_port: str = Form(""),
    cisco_console_baud: int = Form(9600),
):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    if form:
        _update_cisco_access_from_form(cisco_cfg, form)
    else:
        _update_cisco_access(
            cisco_cfg,
            hostname=cisco_switch_hostname,
            username=cisco_switch_username,
            password=cisco_switch_password,
            management_ip=cisco_management_ip,
            console_port=cisco_console_port,
            console_baud=cisco_console_baud,
        )
    context = {"cfg": cfg}
    result = service.discover_version_any(context)
    if result.get("ok"):
        version = str(result.get("version") or "").strip()
        cisco_cfg["last_discovered_version"] = version
        cisco_cfg["last_discovered_at"] = datetime.now(timezone.utc).isoformat()
        cisco_cfg["last_show_version"] = str(result.get("raw_excerpt") or "").strip()
        cisco_cfg["last_discovered_model"] = str(result.get("model") or "").strip()
        cisco_cfg["last_discovered_platform"] = str(result.get("platform") or "").strip()
        cisco_cfg["last_discovered_hostname"] = str(result.get("hostname") or "").strip()
        cisco_cfg["last_discovery_error"] = ""
        record_upgrade_inventory(
            cfg,
            "cisco_switch",
            current_version=version,
            source="Last Cisco discovery",
            raw_version=version or str(result.get("raw_excerpt") or "").strip(),
            checked_at=cisco_cfg["last_discovered_at"],
            model=str(result.get("model") or "").strip(),
            platform=str(result.get("platform") or "").strip(),
            hostname=str(result.get("hostname") or "").strip(),
        )
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Cisco version read",
            f"Read the current switch software version by {str(result.get('source') or 'auto')} and cached it for Upgrade Helper.",
            tone="ready",
            outcomes=[
                f"Target: {str(result.get('target') or '').strip() or 'Not set'}",
                f"Version: {version or 'Unknown'}",
                f"Source: {str(result.get('source') or 'auto')}",
            ],
            details=list(result.get("warnings") or []),
        )
    else:
        cisco_cfg["last_discovery_error"] = str(result.get("error") or "").strip()
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Cisco version read failed",
            str(result.get("error") or "Cisco discovery failed."),
            tone="pending",
            outcomes=[f"Target: {str(result.get('target') or '').strip() or 'Not set'}"],
            details=list(result.get("warnings") or []),
        )

    page = str(return_page or "").strip().lower()
    if page in {"global_settings", "upgrade_helper"}:
        return main.render_page(
            request,
            cfg,
            active_page=page,
            action_feedback=feedback,
        )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


def _update_cisco_access(
    cisco_cfg: dict,
    *,
    hostname: str = "",
    username: str = "",
    password: str = "",
    console_password: str = "",
    console_port: str = "",
    console_baud: int | str = "",
    management_vlan: int | str = "",
    management_ip: str = "",
    subnet_mask: str = "",
    gateway: str = "",
    domain_name: str = "",
    enable_secret: str = "",
    enable_password: str = "",
    management_port: str = "",
    management_port_mode: str = "",
    trusted_console_adapter: bool | None = None,
    bootstrap_network_port: str = "",
    bootstrap_network_mode: str = "",
) -> None:
    if hostname:
        cisco_cfg["hostname"] = str(hostname).strip()
    if username:
        cisco_cfg["username"] = str(username).strip()
    if password:
        cisco_cfg["password"] = str(password)
    if console_password:
        cisco_cfg["console_password"] = str(console_password)
    if console_port:
        cisco_cfg["console_port"] = str(console_port).strip()
    if console_baud:
        cisco_cfg["console_baud"] = int(console_baud)
    if management_vlan:
        cisco_cfg["management_vlan"] = int(management_vlan)
    if management_ip:
        cisco_cfg["management_ip"] = str(management_ip).strip()
        cisco_cfg["ip"] = str(management_ip).strip()
    if subnet_mask:
        cisco_cfg["subnet_mask"] = str(subnet_mask).strip()
    if gateway:
        cisco_cfg["gateway"] = str(gateway).strip()
    if domain_name:
        cisco_cfg["domain_name"] = str(domain_name).strip()
    resolved_enable = str(enable_secret or enable_password or "")
    if resolved_enable:
        cisco_cfg["enable_password"] = resolved_enable
    if management_port:
        cisco_cfg["management_port"] = str(management_port).strip()
    if management_port_mode:
        cisco_cfg["management_port_mode"] = str(management_port_mode).strip()
    if trusted_console_adapter is not None:
        cisco_cfg["trusted_console_adapter"] = bool(trusted_console_adapter)
    if bootstrap_network_port:
        cisco_cfg["bootstrap_network_port"] = str(bootstrap_network_port).strip()
    if bootstrap_network_mode:
        cisco_cfg["bootstrap_network_mode"] = str(bootstrap_network_mode).strip()


def _update_cisco_access_from_form(cisco_cfg: dict, form: dict[str, Any]) -> None:
    enable_secret = str(form.get("cisco_enable_secret") or form.get("cisco_enable_password") or "")
    management_port = str(form.get("cisco_management_port") or form.get("cisco_bootstrap_network_port") or "")
    management_port_mode = str(form.get("cisco_management_port_mode") or form.get("cisco_bootstrap_network_mode") or "")
    trusted: bool | None = None
    if "cisco_trusted_console_adapter" in form:
        trusted = _coerce_bool(form.get("cisco_trusted_console_adapter"))
    _update_cisco_access(
        cisco_cfg,
        hostname=str(form.get("cisco_switch_hostname") or ""),
        username=str(form.get("cisco_switch_username") or ""),
        password=str(form.get("cisco_switch_password") or ""),
        console_password=str(form.get("cisco_console_password") or ""),
        console_port=str(form.get("cisco_console_port") or ""),
        console_baud=str(form.get("cisco_console_baud") or ""),
        management_vlan=str(form.get("cisco_management_vlan") or ""),
        management_ip=str(form.get("cisco_management_ip") or ""),
        subnet_mask=str(form.get("cisco_subnet_mask") or ""),
        gateway=str(form.get("cisco_gateway") or ""),
        domain_name=str(form.get("cisco_domain_name") or ""),
        enable_secret=enable_secret,
        management_port=management_port,
        management_port_mode=management_port_mode,
        trusted_console_adapter=trusted,
        bootstrap_network_port=str(form.get("cisco_bootstrap_network_port") or ""),
        bootstrap_network_mode=str(form.get("cisco_bootstrap_network_mode") or ""),
    )


def _clear_cisco_live_state_after_factory_reset(cisco_cfg: dict[str, Any]) -> None:
    for key, value in {
        "last_bootstrap": {},
        "last_console_bootstrap_check": {},
        "last_raw_console_bootstrap_check": "",
        "last_console_management_state": "",
        "last_ssh_test": {},
        "last_port_discovery": {},
        "last_raw_port_discovery": "",
        "last_running_config_backup": "",
        "last_config_preview": "",
        "last_config_validation": {},
    }.items():
        cisco_cfg[key] = value
    cisco_cfg["connection_method"] = "console"
    cisco_cfg["config_approval"] = {
        "state": "blocked",
        "mode": "full",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "blockers": ["Factory reset was issued. Run Setup Console and re-approve the Cisco config before Run Center."],
    }


@router.post("/modules/cisco/discover-console", response_class=HTMLResponse)
@router.post("/modules/cisco/test-console-access", response_class=HTMLResponse)
async def cisco_discover_console(request: Request, return_page: str = Form("cisco")):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    _update_cisco_access_from_form(cisco_cfg, form)
    result = service.discover_console({"cfg": cfg})
    candidates = list(result.get("candidates") or [])
    probe_results = list(result.get("probe_results") or [])
    diagnostics = dict(result.get("diagnostics") or {})
    suggestions = list(result.get("suggestions") or diagnostics.get("suggestions") or [])
    cisco_cfg["last_console_candidates"] = [{key: value for key, value in item.items() if key != "raw_output"} for item in candidates]
    cisco_cfg["last_console_probe_results"] = probe_results
    cisco_cfg["last_console_suggestions"] = suggestions
    cisco_cfg["last_serial_output"] = "\n\n".join(str(item.get("raw_output") or "") for item in candidates if item.get("raw_output"))
    cisco_cfg["last_console_diagnostics"] = diagnostics
    cisco_cfg["last_cisco_action"] = {
        "mode": "discover_console",
        "ok": bool(result.get("ok")),
        "error": str(result.get("error") or ""),
        "suggestions": suggestions,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    cisco_cfg["last_discovery_error"] = ""
    if len(candidates) == 1:
        cisco_cfg["console_port"] = str(candidates[0].get("port") or "")
        cisco_cfg["console_baud"] = int(candidates[0].get("baud") or 9600)
        cisco_cfg["connection_method"] = "console"
    elif diagnostics.get("permission_denied"):
        cisco_cfg["last_discovery_error"] = str(diagnostics.get("error_summary") or result.get("error") or "")
    elif not result.get("ok"):
        port_errors = [str(item.get("error") or "").strip() for item in probe_results if str(item.get("error") or "").strip()]
        cisco_cfg["last_discovery_error"] = port_errors[0] if port_errors else str(result.get("error") or "")
    main.save_kit_config(cfg)
    details = list(result.get("warnings") or [])
    if diagnostics:
        details.extend(
            [
                f"pyserial import status: {'ready' if diagnostics.get('serial_imported') else 'missing'}",
                f"Visible serial ports: {', '.join(list(diagnostics.get('ordered_ports') or [])) or 'none'}",
            ]
        )
    if diagnostics.get("permission_denied"):
        details.append("Serial access is blocked by Linux device permissions. Add the Lab Builder server user to the dialout group and restart the app session.")
    details.extend(suggestions)
    port_errors = [str(item.get("error") or "").strip() for item in probe_results if str(item.get("error") or "").strip()]
    if port_errors:
        details.append(f"Probe error: {port_errors[0]}")
    main.append_activity_event(
        cfg["site"]["name"],
        "cisco_console_discovery",
        workflow="cisco",
        state="complete" if result.get("ok") else "failed",
        summary="Cisco console discovery completed." if result.get("ok") else "Cisco console discovery failed.",
        target=str(candidates[0].get("port") or "") if len(candidates) == 1 else "",
        details=details + ([str(result.get("error") or "")] if str(result.get("error") or "").strip() else []),
    )

    if result.get("ok"):
        if len(candidates) == 1:
            outcomes = [f"Console: {cisco_cfg.get('console_port')} @ {cisco_cfg.get('console_baud')}"]
            title = "Cisco console found" if not result.get("error") else "Cisco console responded"
        else:
            outcomes = [f"{len(candidates)} console candidates found", "Select the intended port before configuring management IP."]
            title = "Choose Cisco console"
        feedback = main.build_action_feedback(title, str(result.get("error") or "Serial discovery only sent a newline and did not change switch configuration."), tone="ready", outcomes=outcomes, details=details)
    else:
        message = str(cisco_cfg.get("last_discovery_error") or result.get("error") or "No Cisco console prompt was detected.")
        feedback = main.build_action_feedback("Cisco console not found", message, tone="pending", details=details)
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/trust-console-adapter", response_class=HTMLResponse)
async def cisco_trust_console_adapter(request: Request):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    _update_cisco_access_from_form(cisco_cfg, form)
    selected_port = str(cisco_cfg.get("console_port") or "").strip()
    if not selected_port:
        candidates = list(cisco_cfg.get("last_console_candidates") or cisco_cfg.get("last_console_probe_results") or [])
        if len(candidates) == 1:
            selected_port = str(candidates[0].get("port") or "").strip()
            cisco_cfg["console_port"] = selected_port
            cisco_cfg["console_baud"] = int(candidates[0].get("baud") or cisco_cfg.get("console_baud") or 9600)
    if selected_port:
        cisco_cfg["trusted_console_adapter"] = True
        cisco_cfg["connection_method"] = "console"
        cisco_cfg["last_discovery_error"] = ""
        cisco_cfg["last_cisco_action"] = {
            "mode": "trust_console_adapter",
            "ok": True,
            "port": selected_port,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Cisco console adapter trusted",
            "The selected serial adapter is saved for console setup even though discovery may not have confirmed the Cisco prompt.",
            tone="ready",
            outcomes=[f"Console: {selected_port} @ {cisco_cfg.get('console_baud') or 9600}"],
        )
    else:
        cisco_cfg["last_cisco_action"] = {
            "mode": "trust_console_adapter",
            "ok": False,
            "error": "Select a console port before trusting the adapter.",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Choose Cisco console",
            "Select a serial adapter before trusting it for console setup.",
            tone="pending",
        )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/fix-serial-permissions", response_class=HTMLResponse)
async def cisco_fix_serial_permissions(
    request: Request,
    cisco_host_sudo_password: str = Form(""),
):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    result = service.fix_serial_permissions({"cfg": cfg}, cisco_host_sudo_password)
    diagnostics = dict(result.get("diagnostics") or {})
    cisco_cfg["last_console_diagnostics"] = diagnostics
    cisco_cfg["last_host_fix"] = {
        "ok": bool(result.get("ok")),
        "applied": list(result.get("applied") or []),
        "warnings": list(result.get("warnings") or []),
        "restart_required": bool(result.get("restart_required")),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "error": str(result.get("error") or ""),
    }
    cisco_cfg["last_cisco_action"] = {
        "mode": "fix_serial_permissions",
        "ok": bool(result.get("ok")),
        "error": str(result.get("error") or ""),
        "completed_at": cisco_cfg["last_host_fix"]["completed_at"],
    }
    if result.get("ok"):
        cisco_cfg["last_discovery_error"] = ""
    else:
        cisco_cfg["last_discovery_error"] = str(result.get("error") or "")
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "Serial permissions updated" if result.get("ok") else "Serial permissions update failed",
        "Applied the host-side serial access fix for the Lab Builder user." if result.get("ok") else str(result.get("error") or "Could not update serial permissions."),
        tone="ready" if result.get("ok") else "danger",
        outcomes=list(result.get("applied") or []),
        details=list(result.get("warnings") or []),
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/bootstrap-management", response_class=HTMLResponse)
@router.post("/modules/cisco/setup-console", response_class=HTMLResponse)
async def cisco_bootstrap_management(
    request: Request,
    return_page: str = Form("cisco"),
):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    _update_cisco_access_from_form(cisco_cfg, form)
    matches = list(cisco_cfg.get("last_console_candidates") or [])
    if not cisco_cfg.get("console_port") and len(matches) > 1:
        feedback = main.build_action_feedback("Choose Cisco console", "Multiple console ports matched. Select the intended port before configuring management IP.", tone="pending")
        main.save_kit_config(cfg)
        return _render_cisco_page(request, cfg, action_feedback=feedback)

    trunk_review_ack = _coerce_bool(form.get("cisco_trunk_review_ack"))
    result = service.bootstrap_management({"cfg": cfg}, trunk_review_ack=trunk_review_ack)
    cisco_cfg["last_bootstrap"] = {key: value for key, value in result.items() if key not in {"status", "output"}}
    cisco_cfg["last_serial_output"] = str(result.get("output") or cisco_cfg.get("last_serial_output") or "")
    completed_at = datetime.now(timezone.utc).isoformat()
    cisco_cfg["last_bootstrap_at"] = completed_at
    bootstrap_check: dict[str, Any] = {}
    if result.get("ok"):
        check = service.verify_console_bootstrap({"cfg": cfg})
        cisco_cfg["last_console_bootstrap_check"] = {key: value for key, value in check.items() if key not in {"status", "raw_output"}}
        cisco_cfg["last_raw_console_bootstrap_check"] = str(check.get("raw_output") or "")
        if check.get("raw_output"):
            cisco_cfg["last_console_management_state"] = str(check.get("raw_output") or "")
        cisco_cfg["connection_method"] = "ssh" if check.get("ok") else "console"
        bootstrap_check = dict(cisco_cfg.get("last_console_bootstrap_check") or {})
    action_error = str(result.get("error") or bootstrap_check.get("error") or "")
    action_ok = bool(result.get("ok")) and bool(bootstrap_check.get("ok"))
    cisco_cfg["last_cisco_action"] = {
        "mode": "bootstrap_management",
        "ok": action_ok,
        "error": action_error,
        "completed_at": completed_at,
        "log_excerpt": "\n".join(str(result.get("output") or "").splitlines()[-18:]),
    }
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "Cisco console setup complete" if result.get("ok") else ("Cisco trunk review needed" if result.get("requires_trunk_review") else "Cisco console setup failed"),
        "Console bootstrap completed. Use Test SSH before running version discovery or upgrades." if result.get("ok") and bootstrap_check.get("ok") else str(result.get("error") or bootstrap_check.get("error") or "Console bootstrap needs attention."),
        tone="ready" if result.get("ok") and bootstrap_check.get("ok") else ("pending" if result.get("ok") or result.get("requires_trunk_review") else "danger"),
        outcomes=[f"Management IP: {cisco_cfg.get('management_ip') or cisco_cfg.get('ip') or 'Not set'}", f"Console: {cisco_cfg.get('console_port') or 'Not selected'}"],
        details=list(result.get("steps") or []) + list(result.get("warnings") or []) + list(bootstrap_check.get("warnings") or []),
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/verify-console-bootstrap", response_class=HTMLResponse)
@router.post("/modules/cisco/check-current-config", response_class=HTMLResponse)
async def cisco_verify_console_bootstrap(request: Request):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    _update_cisco_access_from_form(cisco_cfg, form)
    result = service.verify_console_bootstrap({"cfg": cfg})
    cisco_cfg["last_console_bootstrap_check"] = {key: value for key, value in result.items() if key not in {"status", "raw_output"}}
    cisco_cfg["last_console_bootstrap_check"]["checked_at"] = datetime.now(timezone.utc).isoformat()
    cisco_cfg["last_raw_console_bootstrap_check"] = str(result.get("raw_output") or "")
    if result.get("raw_output"):
        cisco_cfg["last_console_management_state"] = str(result.get("raw_output") or "")
    cisco_cfg["last_cisco_action"] = {
        "mode": "verify_console_bootstrap",
        "ok": bool(result.get("ok")),
        "error": str(result.get("error") or ""),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "Console bootstrap network is ready" if result.get("ok") else "Console bootstrap network needs attention",
        "Management SVI and SSH look ready from the console." if result.get("ok") else str(result.get("error") or "Console bootstrap verification failed."),
        tone="ready" if result.get("ok") else "pending",
        outcomes=[
            f"VLAN {result.get('management_vlan') or cisco_cfg.get('management_vlan')}: {'exists' if result.get('vlan_exists') else 'missing'}",
            f"IP: {result.get('current_management_ip') or 'not set'}",
            f"Gateway: {result.get('default_gateway') or 'not set'}",
            f"SSH: {'enabled' if result.get('ssh_enabled') else 'not enabled'}",
            f"SCP: {'enabled' if result.get('scp_enabled') else 'not enabled'}",
        ],
        details=list(result.get("warnings") or []),
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/use-discovered-values", response_class=HTMLResponse)
async def cisco_use_discovered_values(request: Request):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    check = dict(cisco_cfg.get("last_console_bootstrap_check") or {})
    applied: list[str] = []

    discovered_ip = str(check.get("current_management_ip") or "").strip()
    if discovered_ip:
        cisco_cfg["management_ip"] = discovered_ip
        cisco_cfg["ip"] = discovered_ip
        applied.append(f"Management IP {discovered_ip}")
    discovered_mask = str(check.get("current_subnet_mask") or "").strip()
    if discovered_mask:
        cisco_cfg["subnet_mask"] = discovered_mask
        applied.append(f"Subnet mask {discovered_mask}")
    discovered_gateway = str(check.get("default_gateway") or "").strip()
    if discovered_gateway:
        cisco_cfg["gateway"] = discovered_gateway
        applied.append(f"Gateway {discovered_gateway}")
    discovered_domain = str(check.get("domain_name") or "").strip()
    if discovered_domain:
        cisco_cfg["domain_name"] = discovered_domain
        applied.append(f"Domain {discovered_domain}")
    if check.get("management_vlan") not in (None, ""):
        try:
            cisco_cfg["management_vlan"] = int(check.get("management_vlan"))
            applied.append(f"VLAN {cisco_cfg['management_vlan']}")
        except (TypeError, ValueError):
            pass
    discovered_name_servers = [str(item).strip() for item in list(check.get("name_servers") or []) if str(item).strip()]
    if discovered_name_servers:
        cisco_cfg["dns_servers"] = discovered_name_servers
        applied.append("DNS servers from switch")

    if applied:
        completed_at = datetime.now(timezone.utc).isoformat()
        cisco_cfg["last_cisco_action"] = {
            "mode": "use_discovered_values",
            "ok": True,
            "completed_at": completed_at,
        }
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Cisco discovered values saved",
            "Saved the latest console-discovered switch values into this Lab Builder kit.",
            tone="ready",
            outcomes=applied,
        )
    else:
        cisco_cfg["last_cisco_action"] = {
            "mode": "use_discovered_values",
            "ok": False,
            "error": "No discovered Cisco management values are available. Run Check current config first.",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Cisco discovered values not saved",
            "No discovered Cisco management values are available. Run Check current config first.",
            tone="pending",
        )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/test-ssh", response_class=HTMLResponse)
async def cisco_test_ssh(
    request: Request,
    return_page: str = Form("cisco"),
):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    _update_cisco_access_from_form(cisco_cfg, form)
    result = service.test_ssh({"cfg": cfg})
    cisco_cfg["last_ssh_test"] = {"ok": bool(result.get("ok")), "host": str(result.get("host") or ""), "error": str(result.get("error") or ""), "tested_at": datetime.now(timezone.utc).isoformat()}
    if result.get("ok"):
        cisco_cfg["connection_method"] = "ssh"
        cisco_cfg["last_show_version"] = str(result.get("raw_excerpt") or cisco_cfg.get("last_show_version") or "")
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "Cisco SSH reachable" if result.get("ok") else "Cisco SSH test failed",
        "SSH is reachable and can be used for normal configuration and upgrades." if result.get("ok") else str(result.get("error") or "SSH test failed."),
        tone="ready" if result.get("ok") else "pending",
        outcomes=[f"Target: {result.get('host') or 'Not set'}"],
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/save-port-map", response_class=HTMLResponse)
async def cisco_save_port_map(request: Request):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form_data = await request.form()
    form = dict(form_data)
    _update_cisco_model_from_form(cisco_cfg, form, cfg)
    selected = [str(item) for item in form_data.getlist("selected_ports") if str(item).strip()]
    bulk_profile = str(form.get("bulk_profile") or "").strip()
    if selected and bulk_profile:
        ports = cisco_cfg.setdefault("ports", {})
        for port in selected:
            ports.setdefault(port, {})
            ports[port]["profile"] = bulk_profile
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "Cisco port map saved",
        "Saved the port profiles, VLANs, overrides, and Cisco SNMP defaults.",
        tone="ready",
        outcomes=[f"Ports saved: {len(cisco_cfg.get('ports') or {})}"],
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/discover-ports", response_class=HTMLResponse)
async def cisco_discover_ports(request: Request):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    _update_cisco_access_from_form(cisco_cfg, form)
    result = service.discover_ports({"cfg": cfg})
    if result.get("ok"):
        discovery = dict(result.get("discovery") or {})
        cisco_cfg["last_port_discovery"] = discovery
        existing_ports = cisco_cfg.setdefault("ports", {})
        for interface in (discovery.get("interfaces") or {}):
            existing_ports.setdefault(interface, {"profile": "custom"})
        cisco_cfg["last_raw_port_discovery"] = str(result.get("raw_output") or "")
    cisco_cfg["last_cisco_action"] = {"mode": "discover", "ok": bool(result.get("ok")), "error": str(result.get("error") or ""), "completed_at": datetime.now(timezone.utc).isoformat()}
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "Cisco ports discovered" if result.get("ok") else "Cisco port discovery failed",
        "Discovered interface names are now available in the port map." if result.get("ok") else str(result.get("error") or "Cisco port discovery failed."),
        tone="ready" if result.get("ok") else "pending",
        outcomes=[f"Target: {result.get('host') or cisco_cfg.get('management_ip') or 'Not set'}"],
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/discover-state", response_class=HTMLResponse)
async def cisco_discover_state(request: Request):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    _update_cisco_access_from_form(cisco_cfg, form)
    version_result = service.discover({"cfg": cfg})
    ports_result = service.discover_ports({"cfg": cfg}) if version_result.get("ok") else {"ok": False, "error": "Skipped because SSH version discovery did not succeed."}
    backup_result = service.backup_config({"cfg": cfg}) if version_result.get("ok") else {"ok": False, "error": "Skipped because SSH version discovery did not succeed."}

    if version_result.get("ok"):
        version = str(version_result.get("version") or "").strip()
        cisco_cfg["last_discovered_version"] = version
        cisco_cfg["last_discovered_at"] = datetime.now(timezone.utc).isoformat()
        cisco_cfg["last_show_version"] = str(version_result.get("raw_excerpt") or "").strip()
        cisco_cfg["last_discovered_model"] = str(version_result.get("model") or "").strip()
        cisco_cfg["last_discovered_platform"] = str(version_result.get("platform") or "").strip()
        cisco_cfg["last_discovered_hostname"] = str(version_result.get("hostname") or "").strip()
        cisco_cfg["last_discovery_error"] = ""
        record_upgrade_inventory(
            cfg,
            "cisco_switch",
            current_version=version,
            source="Last Cisco discovery",
            raw_version=version or str(version_result.get("raw_excerpt") or "").strip(),
            checked_at=cisco_cfg["last_discovered_at"],
            model=str(version_result.get("model") or "").strip(),
            platform=str(version_result.get("platform") or "").strip(),
            hostname=str(version_result.get("hostname") or "").strip(),
        )
    else:
        cisco_cfg["last_discovery_error"] = str(version_result.get("error") or "").strip()

    if ports_result.get("ok"):
        discovery = dict(ports_result.get("discovery") or {})
        cisco_cfg["last_port_discovery"] = discovery
        cisco_cfg["last_raw_port_discovery"] = str(ports_result.get("raw_output") or "")
        for interface in (discovery.get("interfaces") or {}):
            cisco_cfg.setdefault("ports", {}).setdefault(interface, {"profile": "custom"})

    if backup_result.get("ok"):
        cisco_cfg["last_running_config_backup"] = str(backup_result.get("running_config") or "")

    cisco_cfg["last_cisco_action"] = {
        "mode": "discover_state",
        "ok": bool(version_result.get("ok")),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "errors": [str(item) for item in [version_result.get("error"), ports_result.get("error"), backup_result.get("error")] if str(item or "").strip()],
    }
    main.save_kit_config(cfg)
    details: list[str] = []
    if not version_result.get("ok"):
        details.append(str(version_result.get("error") or "Cisco version discovery failed."))
    if version_result.get("ok") and not ports_result.get("ok"):
        details.append(str(ports_result.get("error") or "Cisco port discovery failed."))
    if version_result.get("ok") and not backup_result.get("ok"):
        details.append(str(backup_result.get("error") or "Cisco backup failed."))
    feedback = main.build_action_feedback(
        "Cisco live state read" if version_result.get("ok") else "Cisco live state read failed",
        "Read the current version, discovered live interfaces, and backed up the running config."
        if version_result.get("ok")
        else str(version_result.get("error") or "Cisco state discovery failed."),
        tone="ready" if version_result.get("ok") else "pending",
        outcomes=[
            f"Version: {cisco_cfg.get('last_discovered_version') or 'Unknown'}",
            f"Live interfaces: {len(dict((cisco_cfg.get('last_port_discovery') or {}).get('interfaces') or {}))}",
        ],
        details=details,
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/preview-config", response_class=HTMLResponse)
async def cisco_preview_config(request: Request, mode: str = Form("full")):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form_data = await request.form()
    form = dict(form_data)
    _update_cisco_model_from_form(cisco_cfg, form, cfg)
    selected_ports = [str(item) for item in form_data.getlist("selected_ports") if str(item).strip()]
    result = service.preview_config({"cfg": cfg}, mode=mode, selected_ports=selected_ports)
    cisco_cfg["last_config_preview"] = str(result.get("config") or "")
    cisco_cfg["last_config_validation"] = dict(result.get("validation") or {})
    cisco_cfg["last_cisco_action"] = {"mode": "preview", "ok": bool(result.get("ok")), "completed_at": datetime.now(timezone.utc).isoformat()}
    main.save_kit_config(cfg)
    validation = dict(result.get("validation") or {})
    feedback = main.build_action_feedback(
        "Cisco config preview ready" if result.get("ok") else "Cisco config needs attention",
        "Generated the requested Cisco configuration preview without applying it.",
        tone="ready" if result.get("ok") else "pending",
        outcomes=[f"Mode: {mode}", f"Selected ports: {len(selected_ports) if selected_ports else 'all'}"],
        details=list(validation.get("errors") or []) + list(validation.get("warnings") or []),
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/apply-config", response_class=HTMLResponse)
async def cisco_apply_config(request: Request, mode: str = Form("full")):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form_data = await request.form()
    form = dict(form_data)
    _update_cisco_model_from_form(cisco_cfg, form, cfg)
    selected_ports = [str(item) for item in form_data.getlist("selected_ports") if str(item).strip()]
    result = service.apply_config({"cfg": cfg}, mode=mode, selected_ports=selected_ports)
    cisco_cfg["last_config_preview"] = str(result.get("config") or "")
    cisco_cfg["last_cisco_action"] = {"mode": f"apply_{mode}", "ok": bool(result.get("ok")), "applied": bool(result.get("applied")), "error": str(result.get("error") or ""), "completed_at": datetime.now(timezone.utc).isoformat()}
    main.save_kit_config(cfg)
    validation = dict(result.get("validation") or {})
    feedback = main.build_action_feedback(
        "Cisco config applied" if result.get("applied") else "Cisco config not applied",
        "Applied the requested Cisco configuration over SSH." if result.get("applied") else str(result.get("error") or "Validation blocked the apply."),
        tone="ready" if result.get("applied") else "danger",
        outcomes=[f"Mode: {mode}", f"Selected ports: {len(selected_ports) if selected_ports else 'all'}"],
        details=list(validation.get("errors") or []) + list(validation.get("warnings") or []),
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/approve-config-plan", response_class=HTMLResponse)
async def cisco_approve_config_plan(request: Request, mode: str = Form("full")):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form_data = await request.form()
    form = dict(form_data)
    _update_cisco_model_from_form(cisco_cfg, form, cfg)
    result = service.preview_config({"cfg": cfg}, mode=mode)
    validation = dict(result.get("validation") or {})
    cisco_cfg["last_config_preview"] = str(result.get("config") or "")
    cisco_cfg["last_config_validation"] = validation

    blockers: list[str] = []
    if not dict(cisco_cfg.get("last_ssh_test") or {}).get("ok"):
        blockers.append("SSH must pass before the Cisco config plan can be approved for Run Center.")
    if not str(cisco_cfg.get("last_discovered_version") or "").strip():
        blockers.append("Read the current switch version before approving the Cisco config plan.")
    upgrade = dict(cisco_cfg.get("upgrade") or {})
    upgrade_result = dict(upgrade.get("last_result") or {})
    upgrade_plan = dict(upgrade.get("last_plan") or {})
    upgrade_override = bool((((cfg.get("upgrade_helper") or {}).get("overrides") or {}).get("cisco_switch")))
    upgrade_gate = main.upgrade_gate_entry(cfg, "cisco_switch")
    nonblocking_gate = bool(upgrade_gate) and not bool(upgrade_gate.get("blocks_run"))
    upgrade_comparison = upgrade_plan.get("comparison")
    upgrade_ok = (
        upgrade_override
        or str(upgrade_result.get("status") or "").lower() == "completed"
        or upgrade_comparison in {"current_enough", "already_current", "equal"}
        or upgrade_comparison == 0
        or nonblocking_gate
    )
    if not upgrade_ok:
        blockers.append("Review the Cisco upgrade gate on Upgrade Helper, complete the upgrade, or enable the override before approving the final config plan.")
    blockers.extend(str(item) for item in validation.get("errors") or [])

    if result.get("ok") and not blockers:
        approved_at = datetime.now(timezone.utc).isoformat()
        cisco_cfg["config_approval"] = {
            "state": "approved",
            "mode": mode,
            "approved_at": approved_at,
            "version": str(cisco_cfg.get("last_discovered_version") or ""),
            "ports": len(dict(cisco_cfg.get("ports") or {})),
            "summary": "Cisco config plan approved for Run Center.",
        }
        cisco_cfg["last_cisco_action"] = {"mode": "approve_config_plan", "ok": True, "completed_at": approved_at}
        cfg.setdefault("included", {})["cisco_switch"] = True
        feedback = main.build_action_feedback(
            "Cisco config plan approved",
            "Run Center can now include the Cisco switch stage.",
            tone="ready",
            outcomes=[f"Mode: {mode}", f"Ports: {len(dict(cisco_cfg.get('ports') or {}))}", "Cisco switch included in Run Center"],
            details=list(validation.get("warnings") or []),
        )
    else:
        blocker_count = len(blockers)
        first_blocker = blockers[0] if blockers else "Cisco approval was blocked by validation."
        cisco_cfg["config_approval"] = {
            "state": "blocked",
            "mode": mode,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "blockers": blockers,
        }
        cisco_cfg["last_cisco_action"] = {"mode": "approve_config_plan", "ok": False, "completed_at": datetime.now(timezone.utc).isoformat()}
        feedback = main.build_action_feedback(
            "Cisco config plan not approved",
            first_blocker,
            tone="pending",
            outcomes=[f"Mode: {mode}", f"Blocked by {blocker_count} issue{'s' if blocker_count != 1 else ''}"],
            details=blockers + list(validation.get("warnings") or []),
        )

    main.save_kit_config(cfg)
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/backup-config", response_class=HTMLResponse)
async def cisco_backup_config(request: Request):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    _update_cisco_access(
        cisco_cfg,
        hostname=str(form.get("cisco_switch_hostname") or ""),
        username=str(form.get("cisco_switch_username") or ""),
        password=str(form.get("cisco_switch_password") or ""),
        management_ip=str(form.get("cisco_management_ip") or ""),
    )
    result = service.backup_config({"cfg": cfg})
    if result.get("ok"):
        cisco_cfg["last_running_config_backup"] = str(result.get("running_config") or "")
    cisco_cfg["last_cisco_action"] = {"mode": "backup_config", "ok": bool(result.get("ok")), "error": str(result.get("error") or ""), "completed_at": datetime.now(timezone.utc).isoformat()}
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "Cisco config backed up" if result.get("ok") else "Cisco config backup failed",
        "Captured the current running config for preview and audit." if result.get("ok") else str(result.get("error") or "Backup failed."),
        tone="ready" if result.get("ok") else "pending",
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/factory-reset", response_class=HTMLResponse)
async def cisco_factory_reset(request: Request):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    form = dict(await request.form())
    confirm = str(form.get("cisco_factory_reset_confirm") or "").strip().upper()
    _update_cisco_access_from_form(cisco_cfg, form)
    if confirm != "FACTORY RESET":
        result = {"status": "blocked", "error": "Type FACTORY RESET to confirm deleting startup-config, vlan.dat, and reloading the switch."}
        tone = "danger"
    else:
        if str(cisco_cfg.get("console_port") or "").strip():
            result = service.factory_reset_console({"cfg": cfg})
            tone = "pending" if result.get("status") == "reload_issued" else "danger"
        else:
            try:
                result = execute_cisco_factory_reset(
                    str(cisco_cfg.get("ip") or cisco_cfg.get("management_ip") or ""),
                    str(cisco_cfg.get("username") or ""),
                    str(cisco_cfg.get("password") or ""),
                )
                result["source"] = "ssh"
                tone = "pending"
            except Exception as exc:
                result = {"status": "failed", "source": "ssh", "error": str(exc).strip() or "Cisco factory reset failed."}
                tone = "danger"
    if result.get("status") == "reload_issued":
        _clear_cisco_live_state_after_factory_reset(cisco_cfg)
    if result.get("output"):
        cisco_cfg["last_serial_output"] = str(result.get("output") or "")
    cisco_cfg["last_factory_reset"] = {key: value for key, value in result.items() if key != "output"}
    cisco_cfg["last_cisco_action"] = {"mode": "factory_reset", "ok": result.get("status") == "reload_issued", "error": str(result.get("error") or ""), "completed_at": datetime.now(timezone.utc).isoformat()}
    main.save_kit_config(cfg)
    source = str(result.get("source") or ("console" if cisco_cfg.get("console_port") else "ssh"))
    feedback = main.build_action_feedback(
        "Cisco factory reset issued" if result.get("status") == "reload_issued" else "Cisco factory reset blocked",
        "Startup config and vlan.dat deletion were issued and the switch is reloading." if result.get("status") == "reload_issued" else str(result.get("error") or "Factory reset was not started."),
        tone=tone,
        outcomes=[f"Source: {source}", f"Console: {cisco_cfg.get('console_port') or 'not selected'}"],
        details=list(result.get("steps") or []),
    )
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/plan-upgrade", response_class=HTMLResponse)
async def cisco_plan_upgrade(request: Request, return_page: str = Form("cisco")):
    from app import main

    cfg = main.load_kit_config()
    plan = build_cisco_upgrade_plan(cfg, main.scan_upgrade_media())
    _store_cisco_upgrade_plan(cfg, plan)
    main.save_kit_config(cfg)
    feedback = main.build_action_feedback(
        "Cisco upgrade plan ready" if plan.get("ready") else "Cisco upgrade plan needs attention",
        "Matched the current Cisco version against the best media file and checked whether the SSH-based upgrade path is ready.",
        tone="ready" if plan.get("ready") else "pending",
        outcomes=[
            f"Target: {plan.get('host') or 'Not set'}",
            f"Current version: {plan.get('current_version') or 'Unknown'}",
            f"Matched media: {plan.get('media_version') or 'Not found'}",
        ],
        details=list(plan.get("blockers") or []) + list(plan.get("warnings") or []) + list(plan.get("notes") or []),
    )
    page = str(return_page or "").strip().lower()
    if page in {"upgrade_helper", "global_settings"}:
        return main.render_page(request, cfg, active_page=page, action_feedback=feedback)
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.post("/modules/cisco/run-upgrade", response_class=HTMLResponse)
async def cisco_run_upgrade(request: Request, return_page: str = Form("cisco")):
    from app import main

    cfg = main.load_kit_config()
    plan = build_cisco_upgrade_plan(cfg, main.scan_upgrade_media())
    _store_cisco_upgrade_plan(cfg, plan)
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    activity = dict(((cisco_cfg.get("upgrade") or {}).get("activity") or {}))
    if str(activity.get("status") or "").lower() == "running":
        feedback = main.build_action_feedback(
            "Cisco upgrade already running",
            "Watch the Cisco upgrade status panel for the latest transfer/install state.",
            tone="pending",
            outcomes=[f"Phase: {activity.get('phase') or 'unknown'}", f"Last message: {activity.get('message') or 'waiting'}"],
        )
    elif not plan.get("ready"):
        cisco_cfg.setdefault("upgrade", {})["last_result"] = {"status": "blocked", "error": "; ".join(plan.get("blockers") or [])}
        _record_cisco_upgrade_activity(
            cfg,
            {"phase": "blocked", "message": "; ".join(plan.get("blockers") or ["Cisco upgrade prechecks are not satisfied."]), "timestamp": datetime.now(timezone.utc).isoformat(), "progress_percent": 100},
            status="blocked",
        )
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Cisco upgrade blocked",
            "The app did not start the upgrade because readiness checks did not pass.",
            tone="danger",
            outcomes=[
                f"Target: {plan.get('host') or 'Not set'}",
                f"Current version: {plan.get('current_version') or 'Unknown'}",
                f"Matched media: {plan.get('media_version') or 'Not found'}",
            ],
            details=list(plan.get("blockers") or []) + list(plan.get("warnings") or []),
        )
    else:
        now = datetime.now(timezone.utc).isoformat()
        cisco_cfg.setdefault("upgrade", {})["activity"] = {
            "status": "running",
            "phase": "queued",
            "message": "Cisco upgrade worker queued.",
            "started_at": now,
            "updated_at": now,
            "progress_percent": 5,
            "events": [{"phase": "queued", "message": "Cisco upgrade worker queued.", "timestamp": now, "progress_percent": 5}],
        }
        main.save_kit_config(cfg)
        _start_cisco_upgrade_worker(cfg)
        feedback = main.build_action_feedback(
            "Cisco upgrade started",
            "The upgrade is running in the background. Watch the Cisco upgrade status panel for transfer, install, completion, or errors.",
            tone="pending",
            outcomes=[
                f"Target: {plan.get('host') or 'Not set'}",
                f"Current version: {plan.get('current_version') or 'Unknown'}",
                f"Target version: {plan.get('media_version') or 'Unknown'}",
            ],
        )
    page = str(return_page or "").strip().lower()
    if page in {"upgrade_helper", "global_settings"}:
        return main.render_page(request, cfg, active_page=page, action_feedback=feedback)
    return _render_cisco_page(request, cfg, action_feedback=feedback)


@router.get("/modules/cisco/upgrade-activity", response_class=HTMLResponse)
async def cisco_upgrade_activity(request: Request):
    from app import main

    cfg = main.load_kit_config()
    return main.templates.TemplateResponse(request, "partials/components/cisco_upgrade_activity.html", {"cfg": cfg, "cisco_upgrade_panel": build_cisco_upgrade_panel(cfg)})


def register_module_routes(app: FastAPI) -> None:
    app.include_router(router)
