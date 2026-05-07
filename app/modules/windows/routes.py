from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Form, Request


WindowsRuntime = dict[str, Callable[..., Any]]


async def save_windows_settings_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
    windows_vm_name: str = Form(""),
    windows_admin_password: str = Form(""),
    included_windows: str | None = Form(None),
):
    cfg = runtime["load_kit_config"]()
    cfg["windows"]["vm_name"] = windows_vm_name
    cfg["windows"]["admin_password"] = windows_admin_password
    cfg["included"]["windows"] = included_windows == "on"
    cfg = runtime["apply_ip_plan"](cfg)
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "windows_settings_saved",
        workflow="windows",
        summary="Saved the Windows setup values for this kit.",
        target=cfg["windows"].get("ip_address") or cfg.get("ip_plan", {}).get("windows", ""),
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Windows setup saved",
            "Updated the local Windows setup values for this kit.",
            tone="ready",
            outcomes=[
                f"VM name: {cfg['windows'].get('vm_name', '') or 'Not set'}",
                f"Target: {cfg['windows'].get('ip_address', '') or cfg.get('ip_plan', {}).get('windows', '') or 'Not set'}",
            ],
        ),
    )


def register_module_routes(app: FastAPI) -> None:
    # Windows routes are still served by legacy app/main.py endpoints during migration.
    _ = app
