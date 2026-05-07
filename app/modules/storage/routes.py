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


def _storage_discovery_error(prefix: str, access: dict[str, Any]) -> str:
    return (
        f"{prefix}: "
        f"{access.get('storage_target', {}).get('error') or access.get('storage_credentials', {}).get('error') or 'missing current iLO IP, username, or password.'}"
    )


async def read_current_storage_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
):
    cfg = runtime["load_kit_config"]()
    service = default_storage_module_service()
    access = service.resolve_storage_access(
        cfg,
        {
            "resolve_storage_target_host": runtime["resolve_storage_target_host"],
            "resolve_storage_target_credentials": runtime["resolve_storage_target_credentials"],
        },
    )
    if not access["valid"]:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=_storage_discovery_error("Storage discovery failed", access),
        )
    host = str(access["host"])
    try:
        client = runtime["build_ilo_client"](host=host, username=access["username"], password=access["password"])
        discovery = client.get_storage_discovery(deep_smart_storage_scan=True)
        export_paths = runtime["export_storage_discovery_snapshot"](cfg, discovery, host=host)
        try:
            runtime["db_persist_storage_inventory"](cfg, discovery, host=host)
        except Exception:
            pass
        runtime["update_storage_latest_state"](cfg, discovery=discovery, discovery_paths=export_paths)
        runtime["save_kit_config"](cfg)
        runtime["append_activity_event"](
            cfg["site"]["name"],
            "storage_discovered",
            workflow="storage",
            state="discovered",
            summary="Read the current storage layout and saved a fresh discovery snapshot.",
            target=host,
            details=[f"Run folder: {export_paths['directory']}"],
        )
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Current storage setup loaded",
                "Read what is on the server and displayed the current storage setup.",
                tone="ready",
                outcomes=[
                    "The current storage layout is now ready to review.",
                    "Next step: Build storage plan",
                ],
                links=[
                    {"label": "Build storage plan", "href": "/storage#build-storage-plan"},
                    {"label": "Open reports", "href": "/configs"},
                ],
            ),
            storage_discovery=discovery,
            storage_export_paths=export_paths,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage discovery failed: {str(e).splitlines()[0]}",
        )


async def repair_storage_selection_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
):
    cfg = runtime["load_kit_config"]()
    service = default_storage_module_service()
    access = service.resolve_storage_access(
        cfg,
        {
            "resolve_storage_target_host": runtime["resolve_storage_target_host"],
            "resolve_storage_target_credentials": runtime["resolve_storage_target_credentials"],
        },
    )
    if not access["valid"]:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=_storage_discovery_error("Storage repair failed", access),
        )
    host = str(access["host"])
    try:
        runtime["clear_storage_plan_selection_state"](cfg)
        client = runtime["build_ilo_client"](host=host, username=access["username"], password=access["password"])
        discovery = client.get_storage_discovery(deep_smart_storage_scan=True)
        export_paths = runtime["export_storage_discovery_snapshot"](cfg, discovery, host=host)
        try:
            runtime["db_persist_storage_inventory"](cfg, discovery, host=host)
        except Exception:
            pass
        runtime["update_storage_latest_state"](cfg, discovery=discovery, discovery_paths=export_paths)
        runtime["save_kit_config"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Invalid selections cleared",
                "Cleared the saved storage plan selections and loaded fresh inventory from the current server.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    "Previous invalid drive selections were removed.",
                    "Next step: build a new storage plan",
                ],
                links=[{"label": "Build storage plan", "href": "/storage#build-storage-plan"}],
            ),
            storage_discovery=discovery,
            storage_export_paths=export_paths,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage repair failed: {str(e).splitlines()[0]}",
        )


async def probe_storage_capabilities_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
):
    cfg = runtime["load_kit_config"]()
    service = default_storage_module_service()
    access = service.resolve_storage_access(
        cfg,
        {
            "resolve_storage_target_host": runtime["resolve_storage_target_host"],
            "resolve_storage_target_credentials": runtime["resolve_storage_target_credentials"],
        },
    )
    if not access["valid"]:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=_storage_discovery_error("Storage capability probe failed", access),
        )
    host = str(access["host"])
    try:
        client = runtime["build_ilo_client"](host=host, username=access["username"], password=access["password"])
        discovery = client.get_storage_discovery(deep_smart_storage_scan=True)
        export_paths = runtime["export_storage_discovery_snapshot"](cfg, discovery, host=host)
        try:
            runtime["db_persist_storage_inventory"](cfg, discovery, host=host)
        except Exception:
            pass
        runtime["update_storage_latest_state"](cfg, discovery=discovery, discovery_paths=export_paths)
        runtime["save_kit_config"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Storage capability probe complete",
                "Read controller metadata, volume collections, and advertised Redfish actions without making storage changes.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    "No delete or create requests were issued.",
                    "Review the controller capability table below before approving apply.",
                ],
            ),
            storage_discovery=discovery,
            storage_export_paths=export_paths,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage capability probe failed: {str(e).splitlines()[0]}",
        )


def register_module_routes(app: FastAPI) -> None:
    # Storage routes are still served by legacy app/main.py endpoints during migration.
    _ = app
