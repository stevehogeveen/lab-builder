from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Form, Request

from app.core.forms import preserve_secret


QnapRuntime = dict[str, Callable[..., Any]]


async def save_qnap_settings_handler(
    request: Request,
    runtime: QnapRuntime,
    return_page: str = Form("qnap"),
    qnap_hostname: str = Form(""),
    qnap_username: str = Form(""),
    qnap_password: str = Form(""),
    included_qnap: str | None = Form(None),
):
    cfg = runtime["load_kit_config"]()
    qnap_cfg = cfg.get("qnap")
    if not isinstance(qnap_cfg, dict):
        qnap_cfg = {}
        cfg["qnap"] = qnap_cfg
    included_cfg = cfg.get("included")
    if not isinstance(included_cfg, dict):
        included_cfg = {}
        cfg["included"] = included_cfg

    qnap_cfg["hostname"] = qnap_hostname
    qnap_cfg["username"] = qnap_username
    qnap_cfg["password"] = preserve_secret(qnap_password, qnap_cfg.get("password"))
    included_cfg["qnap"] = included_qnap == "on"
    cfg = runtime["apply_ip_plan"](cfg)
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "qnap_settings_saved",
        workflow="qnap",
        summary="Saved the QNAP setup values for this kit.",
        target=cfg["qnap"].get("ip") or cfg.get("ip_plan", {}).get("qnap", ""),
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "QNAP setup saved",
            "Updated the local QNAP setup values for this kit.",
            tone="ready",
            outcomes=[
                f"Hostname: {cfg['qnap'].get('hostname', '') or 'Not set'}",
                f"Target: {cfg['qnap'].get('ip', '') or cfg.get('ip_plan', {}).get('qnap', '') or 'Not set'}",
                f"Included in kit: {'Yes' if cfg['included'].get('qnap') else 'No'}",
            ],
        ),
    )


def register_module_routes(app: FastAPI) -> None:
    # QNAP routes are still served by legacy app/main.py endpoints during migration.
    _ = app
