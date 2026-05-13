from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from app.cisco_upgrade import build_cisco_upgrade_plan, execute_cisco_upgrade
from app.modules.cisco.service import CiscoModuleService
from app.upgrade_helper import record_upgrade_inventory


router = APIRouter()
service = CiscoModuleService()


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
        action_feedback=action_feedback
        or main.build_action_feedback(
            "Cisco setup",
            "Read the switch software version here, then let Upgrade Helper compare it against /media.",
            tone="progress",
            status_label="Ready",
            outcomes=["Cisco workflow is isolated in app/modules/cisco/."],
        ),
        extra_context={"cisco_payload": service.status(context)},
    )


def _store_cisco_upgrade_plan(cfg: dict, plan: dict) -> None:
    cfg.setdefault("cisco_switch", {})
    cfg["cisco_switch"].setdefault("upgrade", {})
    cfg["cisco_switch"]["upgrade"]["last_plan"] = plan


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
):
    from app import main

    cfg = main.load_kit_config()
    cisco_cfg = cfg.setdefault("cisco_switch", {})
    if cisco_switch_hostname:
        cisco_cfg["hostname"] = str(cisco_switch_hostname).strip()
    if cisco_switch_username:
        cisco_cfg["username"] = str(cisco_switch_username).strip()
    if cisco_switch_password:
        cisco_cfg["password"] = str(cisco_switch_password)
    context = {"cfg": cfg}
    result = service.discover(context)
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
            "Read the current switch software version and cached it for Upgrade Helper.",
            tone="ready",
            outcomes=[
                f"Target: {str(result.get('target') or '').strip() or 'Not set'}",
                f"Version: {version or 'Unknown'}",
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
    try:
        result = execute_cisco_upgrade(cfg, main.scan_upgrade_media())
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Cisco upgrade submitted",
            "Submitted the Cisco image transfer and install sequence. This path is implemented but not yet live-tested in this workspace.",
            tone="ready",
            outcomes=[
                f"Target: {result.get('host') or 'Not set'}",
                f"Previous version: {result.get('previous_version') or 'Unknown'}",
                f"Target version: {result.get('target_version') or 'Unknown'}",
            ],
            details=[f"Image: {result.get('media_path') or 'Unknown'}"],
        )
    except Exception as exc:
        plan = build_cisco_upgrade_plan(cfg, main.scan_upgrade_media())
        _store_cisco_upgrade_plan(cfg, plan)
        cfg.setdefault("cisco_switch", {}).setdefault("upgrade", {})["last_result"] = {"status": "failed", "error": str(exc).splitlines()[0]}
        main.save_kit_config(cfg)
        feedback = main.build_action_feedback(
            "Cisco upgrade failed",
            "The Cisco upgrade command path did not complete cleanly.",
            tone="danger",
            outcomes=[
                f"Target: {plan.get('host') or 'Not set'}",
                f"Current version: {plan.get('current_version') or 'Unknown'}",
                f"Matched media: {plan.get('media_version') or 'Not found'}",
            ],
            details=[str(exc).splitlines()[0]] + list(plan.get("notes") or []),
        )
    page = str(return_page or "").strip().lower()
    if page in {"upgrade_helper", "global_settings"}:
        return main.render_page(request, cfg, active_page=page, action_feedback=feedback)
    return _render_cisco_page(request, cfg, action_feedback=feedback)


def register_module_routes(app: FastAPI) -> None:
    app.include_router(router)
