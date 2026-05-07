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


async def autofill_ip_plan_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configuration"),
    shared_subnet: str = Form("10.10.8.0/24"),
):
    cfg = runtime["load_kit_config"]()
    try:
        cfg["shared_network"]["subnet"] = shared_subnet
        cfg["ip_plan"] = runtime["build_default_ip_plan"](shared_subnet)
        cfg = runtime["apply_ip_plan"](cfg)
        runtime["save_kit_config"](cfg)
        return runtime["render_page"](request, cfg, active_page=return_page, message="Default IP plan generated and applied.")
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"IP plan generation failed: {e}")


async def export_ilo_config_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    try:
        snapshot_path = runtime["export_ilo_config_snapshot"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            message=f"Exported iLO config snapshot to {snapshot_path}",
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"iLO config export failed: {e}")


async def export_ilo_inventory_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    ilo_cfg = cfg.get("ilo", {})
    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    username = (ilo_cfg.get("username") or "").strip()
    password = ilo_cfg.get("password", "")
    if not host and runtime["policy_enabled"](cfg, "discover_enabled"):
        policy = runtime["normalize_ilo_policy"]((cfg.get("ilo") or {}).get("policy"))
        discovered = [runtime["probe_tcp_port"](target, 443, timeout_seconds=0.75) for target in runtime["build_ilo_discovery_targets"](cfg)]
        policy["discovered_hosts"] = discovered
        reachable = [item for item in discovered if item.get("reachable")]
        cfg["ilo"]["policy"] = runtime["normalize_ilo_policy"](policy)
        if reachable:
            host = str(reachable[0].get("host") or "")
            cfg["ilo"]["current_ip"] = host
            cfg["ilo"]["host"] = host
        runtime["save_kit_config"](cfg)
    if not host or not username or not password:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Current iLO config fetch failed: missing current iLO IP, username, or password.",
        )
    try:
        client = runtime["build_ilo_client"](host=host, username=username, password=password)
        inventory = client.get_current_config_snapshot()
        export_paths = runtime["export_ilo_inventory_snapshot"](cfg, inventory)
        try:
            runtime["db_persist_ilo_inventory"](cfg, inventory, source_host=host)
        except Exception:
            pass
        yaml_text = export_paths["summary"].read_text(encoding="utf-8")
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Current iLO inventory captured",
                "Read the live iLO state and saved a fresh summary and raw export.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    f"Saved under: {export_paths['summary'].parent}",
                ],
                links=[{"label": "Open artifacts page", "href": "/configs"}],
            ),
            config_view_title=f"Latest Live Summary: {export_paths['summary'].parent.name}",
            config_view_content=yaml_text,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Current iLO config fetch failed: {str(e).splitlines()[0]}",
        )


async def export_ad_hoc_ilo_inventory_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configs"),
    ad_hoc_ilo_host: str = Form(""),
    ad_hoc_ilo_username: str = Form(""),
    ad_hoc_ilo_password: str = Form(""),
    ad_hoc_ilo_label: str = Form(""),
    save_to_current_kit: str | None = Form(None),
):
    cfg = runtime["load_kit_config"]()
    host = ad_hoc_ilo_host.strip()
    username = ad_hoc_ilo_username.strip()
    password = ad_hoc_ilo_password
    label = ad_hoc_ilo_label.strip()
    if not host or not username or not password:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Ad hoc iLO inventory export failed: missing iLO IP/hostname, username, or password.",
        )
    try:
        client = runtime["build_ilo_client"](host=host, username=username, password=password)
        inventory = client.get_current_config_snapshot()
        export_paths = runtime["export_ilo_inventory_snapshot"](cfg, inventory, label=label, source_host=host)
        try:
            runtime["db_persist_ilo_inventory"](cfg, inventory, source_host=host)
        except Exception:
            pass
        saved_msg = ""
        if save_to_current_kit == "on":
            cfg["ilo"]["host"] = host
            cfg["ilo"]["current_ip"] = host
            cfg["ilo"]["username"] = username
            cfg["ilo"]["password"] = password
            runtime["save_kit_config"](cfg)
            saved_msg = " Saved these connection values to the current kit."
        yaml_text = export_paths["summary"].read_text(encoding="utf-8")
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Ad hoc iLO inventory captured",
                "Read the live iLO state from the temporary target and saved fresh exports.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    f"Saved under: {export_paths['summary'].parent}",
                    saved_msg.strip() or "Current kit settings were left unchanged.",
                ],
                links=[{"label": "Open artifacts page", "href": "/configs"}],
            ),
            config_view_title=f"Latest Live Summary: {export_paths['summary'].parent.name}",
            config_view_content=yaml_text,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Ad hoc iLO inventory export failed: {str(e).splitlines()[0]}",
        )
