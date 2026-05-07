from __future__ import annotations

import threading
from typing import Any, Callable

from fastapi import Form, Request
from fastapi.responses import FileResponse, HTMLResponse


ExecutionRuntime = dict[str, Callable[..., Any]]


async def view_run_summary_handler(
    request: Request,
    runtime: ExecutionRuntime,
    scope: str = Form(...),
    return_page: str = Form("execution"),
):
    cfg = runtime["load_kit_config"]()
    summary = runtime["build_run_summary"](cfg, scope)
    review = runtime["build_execution_review"](cfg, scope)
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Run summary ready",
            "Built a concise review of the selected run that you can print or export.",
            tone="ready",
            outcomes=[
                f"Scope: {scope}",
                f"Target server: {summary.get('target_server') or 'Not set'}",
                f"Included stages: {', '.join(summary.get('final_summary', {}).get('will_run', []))}",
            ],
        ),
        config_view_title=f"Run Summary: {scope}",
        config_view_content=runtime["yaml_safe_dump"](summary, sort_keys=False),
        execution_preview=review.get("detail_text"),
        execution_review=review,
        confirm_scope=scope,
    )


async def download_run_summary_handler(runtime: ExecutionRuntime, scope: str = Form(...)):
    cfg = runtime["load_kit_config"]()
    path = runtime["write_run_summary_artifact"](cfg, scope)
    return FileResponse(path=path, filename=path.name, media_type="application/x-yaml")


async def download_latest_debug_bundle_handler(runtime: ExecutionRuntime):
    path = runtime["debug_bundles_dir"] / "latest-failure.txt"
    if not path.exists():
        return HTMLResponse("No debug bundle has been generated yet.", status_code=404)
    return FileResponse(path=path, filename="latest-failure.txt", media_type="text/plain")


async def download_built_esxi_iso_handler(
    request: Request,
    runtime: ExecutionRuntime,
    kit_name: str,
    output_name: str,
):
    path = runtime["resolve_built_esxi_iso_path"](kit_name, output_name)
    if not path.exists():
        return HTMLResponse(f"Built ESXi ISO not found: {path}", status_code=404)
    runtime["append_esxi_iso_access_log"](path, request)
    return FileResponse(path=path, filename=path.name, media_type="application/octet-stream")


async def prepare_execute_handler(
    request: Request,
    runtime: ExecutionRuntime,
    scope: str = Form("included"),
    selected_scopes: list[str] = Form([]),
    return_page: str = Form("execution"),
):
    cfg = runtime["load_kit_config"]()
    runtime["apply_request_public_base_url"](cfg, request)
    scope = runtime["normalize_run_center_scope"](scope, selected_scopes)
    preview_error = None
    try:
        runtime["validate_execution_scope"](cfg, scope)
    except Exception as e:
        preview_error = str(e).splitlines()[0]
    review = runtime["build_execution_review"](cfg, scope)
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        execution_preview=review.get("detail_text"),
        execution_review=review,
        confirm_scope=scope,
        error_message=preview_error,
    )


async def execute_scope_handler(
    request: Request,
    runtime: ExecutionRuntime,
    scope: str = Form("included"),
    selected_scopes: list[str] = Form([]),
    confirm_phrase: str = Form(""),
    confirm_checkbox: str | None = Form(None),
    esxi_run_stamp: str = Form(""),
    return_page: str = Form("execution"),
):
    cfg = runtime["load_kit_config"]()
    runtime["apply_request_public_base_url"](cfg, request)
    scope = runtime["normalize_run_center_scope"](scope, selected_scopes)
    launch_options = runtime["build_execution_launch_options"](cfg, scope)
    real_launch = launch_options.get("real")
    if not real_launch:
        review = runtime["build_execution_review"](cfg, scope)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Execution blocked: a real run is not available for the selected stages.",
            execution_preview=review.get("detail_text"),
            execution_review=review,
            confirm_scope=scope,
        )
    scope = str(real_launch.get("scope") or scope)
    runtime_state = dict(cfg.get("_runtime", {}) or {})
    if esxi_run_stamp.strip():
        runtime_state["esxi_run_stamp"] = esxi_run_stamp.strip()
    if runtime_state:
        cfg["_runtime"] = runtime_state
    try:
        runtime["validate_execution_scope"](cfg, scope)
    except Exception as e:
        review = runtime["build_execution_review"](cfg, scope)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Execution blocked: {str(e).splitlines()[0]}",
            execution_preview=review.get("detail_text"),
            execution_review=review,
            confirm_scope=scope,
        )
    if confirm_checkbox != "on":
        review = runtime["build_execution_review"](cfg, scope)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Execution blocked: you must check the confirmation box.",
            execution_preview=review.get("detail_text"),
            execution_review=review,
            confirm_scope=scope,
        )
    if confirm_phrase.strip().upper() != "EXECUTE":
        review = runtime["build_execution_review"](cfg, scope)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Execution blocked: confirmation phrase must be exactly EXECUTE.",
            execution_preview=review.get("detail_text"),
            execution_review=review,
            confirm_scope=scope,
        )
    runtime["initialize_background_job"](cfg["site"]["name"], scope)
    threading.Thread(
        target=runtime["execute_real_job_in_background"],
        args=(cfg, scope),
        daemon=True,
    ).start()
    msg = "Execution started."
    if scope == "ilo":
        msg = "Real iLO automation started in the background. Check Job Monitor for live progress and logs."
    elif scope == "storage":
        msg = "Real storage automation started in the background. Check Job Monitor for live progress and logs."
    elif scope == "esxi":
        msg = "Real ESXi automation started in the background. Check Job Monitor for live progress and logs."
    elif scope == "windows":
        msg = "Windows safe execution started in the background. It validates and records the staged install plan without deploying a VM."
    elif scope.startswith("multi__"):
        msg = "Real selected-stage automation started in the background. Check Job Monitor for live progress and logs."
    else:
        msg = f"Preview started for scope: {scope}. No real changes will be made."
    return runtime["render_page"](request, cfg, active_page=return_page, message=msg)


async def retry_storage_stage_handler(
    request: Request,
    runtime: ExecutionRuntime,
    return_page: str = Form("execution"),
):
    cfg = runtime["load_kit_config"]()
    runtime["apply_request_public_base_url"](cfg, request)
    scope = "storage"
    review = runtime["build_execution_review"](cfg, scope)
    launch_options = runtime["build_execution_launch_options"](cfg, scope)
    real_launch = launch_options.get("real")
    if not real_launch:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Storage retry blocked: a real storage run is not currently available.",
            execution_preview=review.get("detail_text"),
            execution_review=review,
            confirm_scope=scope,
        )
    scope = str(real_launch.get("scope") or scope)
    try:
        runtime["validate_execution_scope"](cfg, scope)
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Storage retry blocked: {str(e).splitlines()[0]}",
            execution_preview=review.get("detail_text"),
            execution_review=review,
            confirm_scope=scope,
        )
    runtime["initialize_background_job"](cfg["site"]["name"], scope)
    threading.Thread(
        target=runtime["execute_real_job_in_background"],
        args=(cfg, scope),
        daemon=True,
    ).start()
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        message="Storage retry started. Follow the Live job panel for step-by-step progress.",
    )


async def execute_preview_scope_handler(
    request: Request,
    runtime: ExecutionRuntime,
    scope: str = Form("included"),
    selected_scopes: list[str] = Form([]),
    return_page: str = Form("execution"),
):
    cfg = runtime["load_kit_config"]()
    scope = runtime["normalize_run_center_scope"](scope, selected_scopes)
    try:
        runtime["validate_execution_scope"](cfg, scope)
    except Exception as e:
        review = runtime["build_execution_review"](cfg, scope)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Preview blocked: {str(e).splitlines()[0]}",
            execution_preview=review.get("detail_text"),
            execution_review=review,
            confirm_scope=scope,
        )
    runtime["save_job"](
        cfg["site"]["name"],
        {
            "status": "Preview queued",
            "execution_mode": "preview",
            "execution_mode_label": "Preview / safety mode",
            "scope": scope,
            "current_stage": "Queued",
            "progress_percent": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "logs": [f"[QUEUED] Preview / safety mode requested for scope: {scope}"],
            "root_scope": scope,
            "stage_statuses": runtime["initialize_stage_statuses"](scope, cfg),
        },
    )
    threading.Thread(
        target=runtime["execute_preview_job_in_background"],
        args=(cfg, scope),
        daemon=True,
    ).start()
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        message=f"Preview started for scope: {scope}. No real changes will be made.",
    )
