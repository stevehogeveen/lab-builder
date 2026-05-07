from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse

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


async def plan_raid_layout_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    controller_path: str = Form(""),
    os_controller_path: str = Form(""),
    data_controller_path: str = Form(""),
    os_raid_level: str | None = Form(None),
    data_raid_level: str | None = Form(None),
    os_drive_ids: list[str] = Form([]),
    data_drive_ids: list[str] = Form([]),
    hot_spare_drive_id: str = Form(""),
    os_drive_paths: list[str] = Form([]),
    data_drive_paths: list[str] = Form([]),
    hot_spare_path: str = Form(""),
    os_bays: list[str] = Form([]),
    data_bays: list[str] = Form([]),
    hot_spare_bay: str = Form(""),
):
    cfg = runtime["load_kit_config"]()
    storage_target = runtime["resolve_storage_target_host"](cfg)
    host = storage_target.get("resolved", "")
    if not host:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"RAID planning failed: {storage_target.get('error')}",
        )
    try:
        discovery, discovery_paths = runtime["load_storage_discovery_artifact"](discovery_raw_path, expected_host=host)
        service = default_storage_module_service()
        overrides = service.build_plan_overrides(
            {
                "controller_path": controller_path,
                "os_controller_path": os_controller_path,
                "data_controller_path": data_controller_path,
                "os_raid_level": os_raid_level,
                "data_raid_level": data_raid_level,
                "os_drive_ids": os_drive_ids,
                "data_drive_ids": data_drive_ids,
                "hot_spare_drive_id": hot_spare_drive_id,
                "os_drive_paths": os_drive_paths,
                "data_drive_paths": data_drive_paths,
                "hot_spare_path": hot_spare_path,
                "os_bays": os_bays,
                "data_bays": data_bays,
                "hot_spare_bay": hot_spare_bay,
            }
        )
        plan = runtime["build_raid_plan"](discovery, discovery_paths, overrides=overrides)
        plan_paths = runtime["export_raid_plan_snapshot"](cfg, plan, discovery_paths)
        try:
            runtime["db_persist_storage_plan"](
                cfg,
                discovery=discovery,
                discovery_paths=discovery_paths,
                plan=plan,
                plan_paths=plan_paths,
                approved=False,
            )
        except Exception:
            pass
        runtime["update_storage_latest_state"](cfg, discovery=discovery, discovery_paths=discovery_paths, plan=plan, plan_paths=plan_paths)
        runtime["save_kit_config"](cfg)
        runtime["append_activity_event"](
            cfg["site"]["name"],
            "storage_plan_built",
            workflow="storage",
            state="planned",
            summary="Built a proposed storage layout from the latest discovery snapshot.",
            target=host,
            details=[f"Plan saved to: {plan_paths['plan']}"],
        )
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Storage plan ready",
                "Built the new layout from the latest storage read.",
                tone="ready",
                outcomes=[
                    "This is still a preview. No storage changes were made.",
                    "Next step: Approve this plan",
                ],
                links=[
                    {"label": "Approve this plan", "href": "/storage#approve-storage-plan"},
                    {"label": "Open reports", "href": "/configs"},
                ],
            ),
            storage_discovery=discovery,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"RAID planning failed: {str(e).splitlines()[0]}",
        )


async def approve_storage_plan_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    include_in_ilo_run: str | None = Form(None),
):
    cfg = runtime["load_kit_config"]()
    storage_target = runtime["resolve_storage_target_host"](cfg)
    host = storage_target.get("resolved", "")
    discovery = None
    discovery_paths = None
    plan = None
    plan_paths = None
    try:
        if not host:
            raise ValueError(storage_target.get("error"))
        discovery, discovery_paths, plan, plan_paths = runtime["restore_storage_page_state"](
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        if not discovery or not discovery_paths:
            raise ValueError("A storage discovery artifact must be selected before approval.")
        if not plan or not plan_paths:
            raise ValueError("A RAID plan artifact must be selected before approval.")
        runtime["validate_storage_plan_drive_paths"](plan, discovery)
        if not plan.get("valid", False):
            raise ValueError("Only a valid RAID plan can be approved for a later iLO run.")
        runtime["approve_storage_plan_for_cfg"](
            cfg,
            discovery=discovery,
            discovery_paths=discovery_paths,
            plan=plan,
            plan_paths=plan_paths,
            include_in_ilo_run=include_in_ilo_run == "on",
        )
        cfg["included"]["storage"] = cfg["storage"]["include_in_ilo_run"]
        runtime["save_kit_config"](cfg)
        runtime["append_activity_event"](
            cfg["site"]["name"],
            "storage_plan_approved",
            workflow="storage",
            state="approved",
            summary="Approved the current storage plan for use in a later iLO run.",
            target=cfg["storage"]["approval"].get("host") or host,
            details=[
                f"Plan: {plan_paths['plan']}",
                f"Included in iLO run: {'Yes' if cfg['storage']['include_in_ilo_run'] else 'No'}",
            ],
        )
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Storage approved",
                "The current storage plan is approved for the real run.",
                tone="ready",
                outcomes=[
                    f"Apply it during the real run: {'Yes' if cfg['storage']['include_in_ilo_run'] else 'No'}",
                    "Next step: Run for real",
                ],
                links=[{"label": "Run for real", "href": "/execution"}],
            ),
            storage_discovery=discovery if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
        )
    except Exception as e:
        error_text = str(e).splitlines()[0]
        repair_action = None
        if runtime["is_storage_drive_controller_mismatch_error"](error_text):
            try:
                runtime["db_record_known_issue_observation"](
                    cfg,
                    fingerprint=runtime["known_issue_storage_drive_controller_mismatch"],
                    title="Storage drive/controller mismatch",
                    description="A selected storage drive path resolved to a different controller than the saved OS or data controller selection.",
                    message=error_text,
                    discovery=discovery,
                    plan=plan,
                )
            except Exception:
                pass
            repair_action = {"return_page": return_page}
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage approval failed: {error_text}",
            storage_discovery=discovery if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_repair_action=repair_action,
        )


async def clear_storage_approval_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
):
    cfg = runtime["load_kit_config"]()
    storage_target = runtime["resolve_storage_target_host"](cfg)
    host = storage_target.get("resolved", "")
    if not host:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage approval clear failed: {storage_target.get('error')}",
        )
    discovery, discovery_paths, plan, plan_paths = runtime["restore_storage_page_state"](
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        expected_host=host,
    )
    runtime["clear_storage_approval_for_cfg"](cfg)
    cfg["included"]["storage"] = False
    cfg["storage"]["include_in_ilo_run"] = False
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "storage_plan_unapproved",
        workflow="storage",
        state="stale",
        summary="Removed approval from the current storage plan so it must be reviewed again.",
        target=host,
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Approval removed",
            "This storage plan now needs review again before it can be used in a real run.",
            tone="ready",
            outcomes=["Next step: Review the plan and approve it again if it still looks right."],
        ),
        storage_discovery=discovery.get("summary", {}) if discovery else None,
        storage_export_paths=discovery_paths,
        storage_plan=plan,
        storage_plan_paths=plan_paths,
    )


async def apply_storage_layout_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    apply_mode: str = Form("create_only"),
    acknowledge_apply: str | None = Form(None),
    typed_confirmation: str = Form(""),
):
    cfg = runtime["load_kit_config"]()
    storage_target = runtime["resolve_storage_target_host"](cfg)
    host = storage_target.get("resolved", "")
    discovery = None
    discovery_paths = None
    plan = None
    plan_paths = None
    try:
        if not host:
            raise ValueError(storage_target.get("error"))
        discovery, discovery_paths, plan, plan_paths = runtime["restore_storage_page_state"](
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        if not plan_paths:
            raise ValueError("A RAID plan artifact must be selected before apply.")
        runtime["validate_storage_plan_drive_paths"](plan, discovery)
        runtime["validate_storage_apply_request"](
            plan,
            apply_mode,
            typed_confirmation,
            acknowledged=acknowledge_apply == "on",
        )
        apply_paths = runtime["initialize_storage_apply_artifacts"](cfg, plan, plan_paths)
        runtime["initialize_background_job"](cfg["site"]["name"], f"storage-apply:{apply_mode}")
        runtime["start_storage_apply_background"](cfg, discovery_raw_path, raid_plan_path, apply_mode, apply_paths)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Storage apply started",
                f"Applying the approved storage plan in {apply_mode.replace('_', ' ')} mode.",
                tone="progress",
                status_label="Running",
                outcomes=[
                    f"Target: {host}",
                    f"Run folder: {apply_paths['directory']}",
                ],
                details=["Use the storage progress card and the live log below to follow each step."],
                links=[{"label": "Jump to storage progress", "href": "/storage#storage-progress-card"}],
            ),
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_apply_paths=apply_paths,
        )
    except Exception as e:
        error_text = str(e).splitlines()[0]
        repair_action = None
        if runtime["is_storage_drive_controller_mismatch_error"](error_text):
            try:
                runtime["db_record_known_issue_observation"](
                    cfg,
                    fingerprint=runtime["known_issue_storage_drive_controller_mismatch"],
                    title="Storage drive/controller mismatch",
                    description="A selected storage drive path resolved to a different controller than the saved OS or data controller selection.",
                    message=error_text,
                    discovery=discovery,
                    plan=plan,
                )
            except Exception:
                pass
            repair_action = {"return_page": return_page}
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage apply failed: {error_text}",
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_repair_action=repair_action,
        )


async def reboot_storage_now_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    apply_artifact_dir: str = Form(""),
):
    cfg = runtime["load_kit_config"]()
    storage_target = runtime["resolve_storage_target_host"](cfg)
    host = storage_target.get("resolved", "")
    discovery = None
    discovery_paths = None
    plan = None
    plan_paths = None
    apply_paths = None
    try:
        if not host:
            raise ValueError(storage_target.get("error"))
        discovery, discovery_paths, plan, plan_paths = runtime["restore_storage_page_state"](
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        if not apply_artifact_dir.strip():
            raise ValueError("A storage apply run folder is required before reboot can be requested.")
        apply_paths = runtime["storage_apply_paths_from_directory"](apply_artifact_dir)
        workflow_state = runtime["load_storage_workflow_state"](apply_paths) or {}
        apply_state = workflow_state.get("apply", {}) or {}
        reboot_state = workflow_state.get("reboot", {}) or {}
        if apply_state.get("status") not in {"Completed", "Staged"}:
            raise ValueError("Reboot Now is only available after a completed storage apply run.")
        if not apply_state.get("reboot_required"):
            raise ValueError("Reboot Now is not available because the current storage run does not require reboot.")
        if reboot_state.get("status") == "Running":
            raise ValueError("A storage reboot workflow is already running for this storage run.")
        runtime["initialize_background_job"](cfg["site"]["name"], "storage-reboot")
        runtime["start_storage_reboot_background"](cfg, discovery_raw_path, raid_plan_path, apply_paths)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Restart requested",
                "Requested the server restart so the staged storage changes can continue.",
                tone="progress",
                status_label="Running",
                outcomes=[f"Run folder: {apply_paths['directory']}"],
                details=["The storage progress card will now track restart and post-reboot validation."],
                links=[{"label": "Jump to storage progress", "href": "/storage#storage-progress-card"}],
            ),
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_apply_paths=apply_paths,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage reboot failed: {str(e).splitlines()[0]}",
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_apply_paths=apply_paths,
        )


async def view_storage_artifact_handler(
    request: Request,
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    artifact_kind: str = Form("discovery_summary"),
    artifact_path: str = Form(""),
    artifact_title: str = Form(""),
    apply_artifact_dir: str = Form(""),
):
    cfg = runtime["load_kit_config"]()
    ilo_cfg = cfg.get("ilo", {})
    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    try:
        discovery, discovery_paths, plan, plan_paths = runtime["restore_storage_page_state"](
            discovery_raw_path=discovery_raw_path,
            raid_plan_path=raid_plan_path,
            expected_host=host,
        )
        apply_paths = runtime["storage_apply_paths_from_directory"](apply_artifact_dir) if apply_artifact_dir else None
        selected_artifact_path, viewer_title = runtime["storage_artifact_target"](
            artifact_kind,
            discovery_paths,
            plan_paths,
            artifact_path_text=artifact_path,
            artifact_title=artifact_title,
        )
        viewer_content = selected_artifact_path.read_text(encoding="utf-8")
        if selected_artifact_path.suffix.lower() == ".json":
            viewer_content = json.dumps(json.loads(viewer_content), indent=2, sort_keys=False)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            message=f"Viewing storage artifact {selected_artifact_path}",
            config_view_title=viewer_title,
            config_view_content=viewer_content,
            storage_discovery=discovery.get("summary", {}) if discovery else None,
            storage_export_paths=discovery_paths,
            storage_plan=plan,
            storage_plan_paths=plan_paths,
            storage_apply_paths=apply_paths,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage artifact view failed: {str(e).splitlines()[0]}",
        )


async def download_storage_artifact_handler(
    runtime: StorageRuntime,
    return_page: str = Form("storage"),
    discovery_raw_path: str = Form(""),
    raid_plan_path: str = Form(""),
    artifact_kind: str = Form("discovery_summary"),
    artifact_path: str = Form(""),
    artifact_title: str = Form(""),
    apply_artifact_dir: str = Form(""),
):
    del return_page
    del artifact_title
    del apply_artifact_dir
    cfg = runtime["load_kit_config"]()
    ilo_cfg = cfg.get("ilo", {})
    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    discovery, discovery_paths, plan, plan_paths = runtime["restore_storage_page_state"](
        discovery_raw_path=discovery_raw_path,
        raid_plan_path=raid_plan_path,
        expected_host=host,
    )
    del discovery, plan
    selected_artifact_path, _ = runtime["storage_artifact_target"](
        artifact_kind,
        discovery_paths,
        plan_paths,
        artifact_path_text=artifact_path,
    )
    media_type = "application/json" if selected_artifact_path.suffix.lower() == ".json" else "text/yaml; charset=utf-8"
    return FileResponse(path=selected_artifact_path, filename=selected_artifact_path.name, media_type=media_type)


def register_module_routes(app: FastAPI) -> None:
    # Storage routes are still served by legacy app/main.py endpoints during migration.
    _ = app
