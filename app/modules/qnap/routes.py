from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Form, Request


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
    cfg["qnap"]["hostname"] = qnap_hostname
    cfg["qnap"]["username"] = qnap_username
    cfg["qnap"]["password"] = qnap_password
    cfg["included"]["qnap"] = included_qnap == "on"
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
            ],
        ),
    )


def register_module_routes(app: FastAPI) -> None:
    # QNAP routes are still served by legacy app/main.py endpoints during migration.
    _ = app
