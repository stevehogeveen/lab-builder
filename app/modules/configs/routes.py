from __future__ import annotations

from typing import Any, Callable

from fastapi import Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse


ConfigsRuntime = dict[str, Callable[..., Any]]


async def view_latest_live_summary_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    latest = runtime["latest_live_inventory_export"]()
    if not latest:
        error_text = f"No live inventory exports found under {runtime['ilo_live_export_dir']}"
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
        )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Latest live summary opened",
            "Showing the newest saved live inventory summary for this kit.",
            tone="ready",
            outcomes=[f"Source folder: {latest['directory']}"],
        ),
        config_view_title=f"Latest Live Summary: {latest['directory'].name}",
        config_view_content=latest["summary"].read_text(encoding="utf-8"),
    )


async def download_latest_live_summary_handler(runtime: ConfigsRuntime):
    latest = runtime["latest_live_inventory_export"]()
    if not latest:
        return HTMLResponse(f"No live inventory exports found under {runtime['ilo_live_export_dir']}", status_code=404)
    return FileResponse(
        path=latest["summary"],
        filename=f"{latest['directory'].parent.name}-{latest['directory'].name}-summary.yml",
        media_type="application/x-yaml",
        headers=runtime["live_inventory_download_headers"](latest),
    )


async def download_latest_live_raw_handler(runtime: ConfigsRuntime):
    latest = runtime["latest_live_inventory_export"]()
    if not latest:
        return HTMLResponse(f"No live inventory exports found under {runtime['ilo_live_export_dir']}", status_code=404)
    return FileResponse(
        path=latest["raw"],
        filename=f"{latest['directory'].parent.name}-{latest['directory'].name}-raw.json",
        media_type="application/json",
        headers=runtime["live_inventory_download_headers"](latest),
    )


async def view_current_kit_config_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    try:
        snapshot_path = runtime["export_current_kit_config_snapshot"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            message=f"Generated current kit config snapshot at {snapshot_path}",
            config_view_title=f"Current Kit Config: {snapshot_path.name}",
            config_view_content=snapshot_path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"Current kit config view failed: {e}")


async def download_current_kit_config_handler(runtime: ConfigsRuntime):
    cfg = runtime["load_kit_config"]()
    snapshot_path = runtime["export_current_kit_config_snapshot"](cfg)
    return FileResponse(path=snapshot_path, filename=snapshot_path.name, media_type="application/x-yaml")


async def import_kit_config_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configs"),
    import_file: UploadFile | None = None,
):
    current_cfg = runtime["load_kit_config"]()
    try:
        if import_file is None:
            raise ValueError("No config file was uploaded.")
        raw = await import_file.read()
        if not raw:
            raise ValueError("The uploaded file was empty.")
        imported = runtime["yaml_safe_load"](raw.decode("utf-8")) or {}
        if not isinstance(imported, dict):
            raise ValueError("The uploaded file must contain a YAML or JSON object.")
        imported = runtime["merge_defaults"](imported)
        imported_name = runtime["sanitize_kit_name"](
            imported.get("site", {}).get("name", "") or current_cfg.get("site", {}).get("name", "Kit-01")
        )
        imported.setdefault("site", {})["name"] = imported_name
        runtime["save_kit_config"](imported)
        imported_snapshot = runtime["current_build_output_dir"](imported) / f"imported-config-{runtime['time_str']()}.yml"
        imported_snapshot.write_text(runtime["yaml_safe_dump"](imported, sort_keys=False), encoding="utf-8")
        cfg = runtime["load_kit_config"](imported_name)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Config imported",
                "Loaded the uploaded config into the app and switched the current kit to it.",
                tone="ready",
                status_label="Imported",
                outcomes=[
                    f"Current kit: {imported_name}",
                    f"Build folder: {runtime['current_build_output_dir'](cfg)}",
                ],
                links=[
                    {"label": "Open Global Settings", "href": "/global-settings"},
                    {"label": "Open Run Center", "href": "/execution"},
                ],
            ),
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            current_cfg,
            active_page=return_page,
            error_message=f"Config import failed: {str(e).splitlines()[0]}",
        )


async def view_ilo_config_snapshot_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    try:
        snapshot_path = runtime["export_ilo_config_snapshot"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            message=f"Generated iLO config snapshot at {snapshot_path}",
            config_view_title=f"iLO Config Snapshot: {snapshot_path.name}",
            config_view_content=snapshot_path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"iLO config snapshot view failed: {e}")


async def download_ilo_config_snapshot_handler(runtime: ConfigsRuntime):
    cfg = runtime["load_kit_config"]()
    snapshot_path = runtime["export_ilo_config_snapshot"](cfg)
    return FileResponse(path=snapshot_path, filename=snapshot_path.name, media_type="application/x-yaml")


async def view_report_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configs"),
    report_path: str = Form(...),
):
    cfg = runtime["load_kit_config"]()
    try:
        path = runtime["safe_report_path"](report_path)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Report opened",
                "Showing the selected saved report.",
                tone="ready",
                outcomes=[f"Source: {path}"],
            ),
            config_view_title=f"Report: {path.name}",
            config_view_content=path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"Report view failed: {str(e).splitlines()[0]}")


async def download_report_handler(runtime: ConfigsRuntime, report_path: str = Form(...)):
    path = runtime["safe_report_path"](report_path)
    media_type = "application/json" if path.suffix.lower() == ".json" else "text/yaml; charset=utf-8"
    return FileResponse(path=path, filename=path.name, media_type=media_type)

