from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Form, Request

from app.modules.storage.service import default_storage_module_service


StorageRuntime = dict[str, Callable[..., Any]]


async def storage_page_handler(request: Request, runtime: StorageRuntime):
    cfg = runtime["load_kit_config"]()
    return runtime["render_page"](request, cfg, active_page="storage")


async def save_storage_target_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
    storage_target_host: str = Form(""),
    storage_username: str = Form(""),
    storage_password: str = Form(""),
    storage_target_mode: str = Form("override"),
):
    cfg = runtime["load_kit_config"]()
    service = default_storage_module_service()
    updated = service.update_saved_storage_target(
        cfg,
        {
            "ensure_storage_config": runtime["ensure_storage_config"],
            "storage_target_mode": storage_target_mode,
            "storage_target_host": storage_target_host,
            "storage_username": storage_username,
            "storage_password": storage_password,
        },
    )
    cfg = updated["cfg"]
    using_defaults = bool(updated["using_defaults"])
    runtime["refresh_storage_approval_from_saved_state"](cfg)
    runtime["save_kit_config"](cfg)
    target = runtime["resolve_storage_target_host"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "storage_target_saved",
        workflow="storage",
        summary=f"Storage review will use {target.get('resolved') or 'no resolved host yet'}.",
        target=target.get("resolved", ""),
        details=[
            f"Address source: {target.get('source') or 'Not resolved'}",
            f"Username source: {runtime['resolve_storage_target_credentials'](cfg).get('username_source') or 'Not resolved'}",
        ],
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Storage target updated",
            "Storage setup will now use the selected server address and sign-in details.",
            tone="ready",
            outcomes=[
                f"Server address: {target.get('resolved') or 'Not resolved yet'}",
                "Using iLO defaults." if using_defaults else "Using the entered address and sign-in details.",
                "Next step: Display current storage setup",
            ],
            links=[{"label": "Open storage setup", "href": "/storage"}],
        ),
    )


def register_module_routes(app: FastAPI) -> None:
    # Storage routes are still served by legacy app/main.py endpoints during migration.
    _ = app
