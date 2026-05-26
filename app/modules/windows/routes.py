from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.core.forms import preserve_secret
from app.modules.ovf_templates.service import OvfTemplateService
from app.windows import inspect_ovf_source


WindowsRuntime = dict[str, Callable[..., Any]]
router = APIRouter()
ovf_service = OvfTemplateService()


WINDOWS_SETTINGS_TEXT_FIELDS = {
    "windows_vm_name",
    "windows_admin_password",
    "windows_vsphere_host",
    "windows_vsphere_username",
    "windows_vsphere_password",
    "windows_vsphere_datacenter",
    "windows_vsphere_datastore",
    "windows_vsphere_network",
    "windows_vsphere_folder",
    "windows_vsphere_resource_pool",
    "windows_winrm_username",
    "windows_winrm_password",
    "windows_winrm_port",
}
WINDOWS_SETTINGS_CHECKBOX_FIELDS = {"windows_winrm_use_https", "included_windows"}
WINDOWS_SETTINGS_FORM_FIELDS = WINDOWS_SETTINGS_TEXT_FIELDS | WINDOWS_SETTINGS_CHECKBOX_FIELDS


def _apply_ovf_template_to_windows_cfg(windows_cfg: dict[str, Any], template: dict[str, Any]) -> None:
    windows_cfg["ovf_template_id"] = str(template.get("id") or "")
    windows_cfg["source_image_path"] = str(template.get("descriptor_path") or "")
    windows_cfg["source_image_name"] = str(template.get("descriptor_name") or template.get("name") or "")
    windows_cfg["source_image_kind"] = str(template.get("kind") or "ovf")
    windows_cfg["source_image_origin"] = "ovf_template"
    windows_cfg["source_image_folder"] = str(template.get("directory") or "")
    windows_cfg["source_image_files"] = list(template.get("files") or [])
    windows_cfg["source_image_total_size_bytes"] = int(template.get("total_size_bytes") or 0)
    windows_cfg["source_image_total_size_display"] = str(template.get("total_size_display") or "0 B")
    windows_cfg["source_image_summary"] = {
        "vm_name": template.get("vm_name", ""),
        "network_names": template.get("network_names", []),
        "os_description": template.get("os_description", ""),
        "hardware_version": template.get("hardware_version", ""),
        "cpu_count": template.get("cpu_count", ""),
        "memory_mb": template.get("memory_mb", ""),
        "disk_capacity": template.get("disk_capacity", ""),
    }
    windows_cfg["install_plan"] = {}


def _apply_windows_settings_form(cfg: dict[str, Any], form: dict[str, Any]) -> bool:
    if not any(key in form for key in WINDOWS_SETTINGS_FORM_FIELDS):
        return False

    windows_cfg = cfg.setdefault("windows", {})
    cfg.setdefault("included", {})

    if "windows_vm_name" in form:
        windows_cfg["vm_name"] = str(form.get("windows_vm_name") or "")
    if "windows_admin_password" in form:
        windows_cfg["admin_password"] = preserve_secret(
            str(form.get("windows_admin_password") or ""),
            windows_cfg.get("admin_password"),
        )
    if "windows_vsphere_host" in form:
        windows_cfg["vsphere_host"] = str(form.get("windows_vsphere_host") or "").strip()
    if "windows_vsphere_username" in form:
        windows_cfg["vsphere_username"] = str(form.get("windows_vsphere_username") or "").strip()
    if form.get("windows_vsphere_password"):
        windows_cfg["vsphere_password"] = str(form.get("windows_vsphere_password") or "")
    if "windows_vsphere_datacenter" in form:
        windows_cfg["vsphere_datacenter"] = str(form.get("windows_vsphere_datacenter") or "").strip()
    if "windows_vsphere_datastore" in form:
        windows_cfg["vsphere_datastore"] = str(form.get("windows_vsphere_datastore") or "").strip()
    if "windows_vsphere_network" in form:
        windows_cfg["vsphere_network"] = str(form.get("windows_vsphere_network") or "").strip()
    if "windows_vsphere_folder" in form:
        windows_cfg["vsphere_folder"] = str(form.get("windows_vsphere_folder") or "").strip()
    if "windows_vsphere_resource_pool" in form:
        windows_cfg["vsphere_resource_pool"] = str(form.get("windows_vsphere_resource_pool") or "").strip()
    if "windows_winrm_username" in form:
        windows_cfg["winrm_username"] = str(form.get("windows_winrm_username") or "").strip() or "Administrator"
    if form.get("windows_winrm_password"):
        windows_cfg["winrm_password"] = str(form.get("windows_winrm_password") or "")
    if "windows_winrm_port" in form:
        try:
            windows_cfg["winrm_port"] = int(str(form.get("windows_winrm_port") or "5986"))
        except ValueError:
            windows_cfg["winrm_port"] = 5986

    full_settings_form = WINDOWS_SETTINGS_TEXT_FIELDS.issubset(set(form.keys()))
    if "windows_winrm_use_https" in form or full_settings_form:
        windows_cfg["winrm_use_https"] = form.get("windows_winrm_use_https") == "on"
    if "included_windows" in form or full_settings_form:
        cfg["included"]["windows"] = form.get("included_windows") == "on"
    return True


async def save_windows_settings_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
    windows_vm_name: str = Form(""),
    windows_admin_password: str = Form(""),
    windows_vsphere_host: str = Form(""),
    windows_vsphere_username: str = Form(""),
    windows_vsphere_password: str = Form(""),
    windows_vsphere_datacenter: str = Form(""),
    windows_vsphere_datastore: str = Form(""),
    windows_vsphere_network: str = Form(""),
    windows_vsphere_folder: str = Form(""),
    windows_vsphere_resource_pool: str = Form(""),
    windows_winrm_username: str = Form("Administrator"),
    windows_winrm_password: str = Form(""),
    windows_winrm_port: str = Form("5986"),
    windows_winrm_use_https: str | None = Form(None),
    included_windows: str | None = Form(None),
):
    cfg = runtime["load_kit_config"]()
    form = {
        "windows_vm_name": windows_vm_name,
        "windows_admin_password": windows_admin_password,
        "windows_vsphere_host": windows_vsphere_host,
        "windows_vsphere_username": windows_vsphere_username,
        "windows_vsphere_password": windows_vsphere_password,
        "windows_vsphere_datacenter": windows_vsphere_datacenter,
        "windows_vsphere_datastore": windows_vsphere_datastore,
        "windows_vsphere_network": windows_vsphere_network,
        "windows_vsphere_folder": windows_vsphere_folder,
        "windows_vsphere_resource_pool": windows_vsphere_resource_pool,
        "windows_winrm_username": windows_winrm_username,
        "windows_winrm_password": windows_winrm_password,
        "windows_winrm_port": windows_winrm_port,
    }
    if windows_winrm_use_https is not None:
        form["windows_winrm_use_https"] = windows_winrm_use_https
    if included_windows is not None:
        form["included_windows"] = included_windows
    _apply_windows_settings_form(cfg, form)
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
            "Updated the Windows VM, deployment target, and WinRM settings for this kit.",
            tone="ready",
            outcomes=[
                f"VM name: {cfg['windows'].get('vm_name', '') or 'Not set'}",
                f"Target: {cfg['windows'].get('ip_address', '') or cfg.get('ip_plan', {}).get('windows', '') or 'Not set'}",
                f"vSphere/ESXi: {cfg['windows'].get('vsphere_host') or 'Not set'}",
                f"WinRM: {cfg['windows'].get('winrm_username') or 'Not set'}:{cfg['windows'].get('winrm_port') or 5986}",
            ],
        ),
    )


def _windows_upload_dir(runtime: WindowsRuntime, cfg: dict[str, Any]) -> Path:
    base = Path(str(runtime["windows_upload_root"]))
    kit_name = str((cfg.get("site") or {}).get("name") or "kit")
    path = base / kit_name
    path.mkdir(parents=True, exist_ok=True)
    return path


async def upload_windows_image_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
    windows_image: UploadFile | None = None,
):
    cfg = runtime["load_kit_config"]()
    if windows_image is None:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message="No image file was uploaded.")
    filename = str(windows_image.filename or "").strip()
    suffix = Path(filename).suffix.lower()
    if suffix not in {".ova", ".ovf"}:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message="Only .ova or .ovf uploads are supported.")
    data = await windows_image.read()
    if not data:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message="Uploaded image file was empty.")
    upload_dir = _windows_upload_dir(runtime, cfg)
    stamp = runtime["time_str"]()
    safe_name = runtime["sanitize_kit_name"](Path(filename).stem) + suffix
    target = upload_dir / f"{stamp}-{safe_name}"
    target.write_bytes(data)
    cfg["windows"]["source_image_path"] = str(target)
    cfg["windows"]["source_image_name"] = filename
    cfg["windows"]["source_image_kind"] = suffix.lstrip(".")
    cfg["windows"]["source_image_origin"] = "uploaded_artifact"
    cfg["windows"]["source_image_total_size_bytes"] = len(data)
    cfg["windows"].pop("source_image_folder", None)
    cfg["windows"].pop("source_image_files", None)
    cfg["windows"].pop("source_image_total_size_display", None)
    cfg["windows"].pop("source_image_summary", None)
    cfg["windows"]["install_plan"] = {}
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "windows_image_uploaded",
        workflow="windows",
        summary=f"Uploaded Windows source image ({suffix}) for VM install planning.",
        target=str(target),
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Windows image uploaded",
            "Saved OVA/OVF source image for install planning.",
            tone="ready",
            outcomes=[
                f"File: {filename}",
                f"Stored at: {target}",
            ],
        ),
    )


async def register_windows_ovf_path_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
    windows_ovf_path: str = Form(""),
):
    cfg = runtime["load_kit_config"]()
    windows_cfg = cfg.setdefault("windows", {})
    source_text = str(windows_ovf_path or "").strip()
    if not source_text:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message="Enter a local OVA or OVF path.")
    source = Path(source_text).expanduser()
    if source.suffix.lower() not in {".ova", ".ovf"}:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message="Local Windows source must be an .ova or .ovf file.")
    summary = runtime["inspect_ovf_source"](source)
    if summary.get("warnings"):
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=" ".join(str(item) for item in summary.get("warnings", [])),
        )

    windows_cfg["source_image_path"] = str(source)
    windows_cfg["source_image_name"] = source.name
    windows_cfg["source_image_kind"] = source.suffix.lower().lstrip(".")
    windows_cfg["source_image_origin"] = "local_path"
    windows_cfg["source_image_folder"] = str(source.parent)
    windows_cfg["source_image_files"] = summary.get("files", [])
    windows_cfg["source_image_total_size_bytes"] = summary.get("total_size_bytes", 0)
    windows_cfg["source_image_total_size_display"] = summary.get("total_size_display", "0 B")
    windows_cfg["source_image_summary"] = {
        "vm_name": summary.get("vm_name", ""),
        "network_names": summary.get("network_names", []),
        "os_description": summary.get("os_description", ""),
        "hardware_version": summary.get("hardware_version", ""),
        "cpu_count": summary.get("cpu_count", ""),
        "memory_mb": summary.get("memory_mb", ""),
        "disk_capacity": summary.get("disk_capacity", ""),
    }
    windows_cfg["install_plan"] = {}
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "windows_local_ovf_registered",
        workflow="windows",
        summary="Registered a local Windows OVA/OVF source for install planning.",
        target=str(source),
        details=[
            f"Files: {len(summary.get('files') or [])}",
            f"Size: {summary.get('total_size_display') or '0 B'}",
        ],
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Windows OVF source registered",
            "Validated the local source and sidecar files without copying them into artifacts.",
            tone="ready",
            outcomes=[
                f"Source: {source.name}",
                f"Files: {len(summary.get('files') or [])}",
                f"Total size: {summary.get('total_size_display') or '0 B'}",
            ],
        ),
    )


async def select_windows_ovf_template_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
    windows_ovf_template_id: str = Form(""),
):
    cfg = runtime["load_kit_config"]()
    template = ovf_service.get_template(cfg, windows_ovf_template_id)
    if not template:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message="Select a registered OVF template first.")
    readiness = dict(template.get("readiness") or {})
    if not readiness.get("ready"):
        blockers = list(readiness.get("blockers") or [])
        message = readiness.get("summary") or "Selected OVF template source is not ready."
        if blockers:
            message = f"{message} {' '.join(str(item) for item in blockers)}"
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=message)
    windows_cfg = cfg.setdefault("windows", {})
    _apply_ovf_template_to_windows_cfg(windows_cfg, template)
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "windows_ovf_template_selected",
        workflow="windows",
        summary="Selected a registered OVF template for Windows install planning.",
        target=str(template.get("descriptor_path") or ""),
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Windows OVF template selected",
            "Windows will use the registered OVF template directory for dry-run planning.",
            tone="ready",
            outcomes=[
                f"Template: {template.get('name') or template.get('id')}",
                f"Descriptor: {template.get('descriptor_name') or 'Not set'}",
                f"Files: {template.get('file_count', 0)}",
            ],
        ),
    )


async def plan_windows_install_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
):
    cfg = runtime["load_kit_config"]()
    _apply_windows_settings_form(cfg, {key: value for key, value in dict(await request.form()).items()})
    windows_cfg = cfg.get("windows", {}) or {}
    warnings: list[str] = []
    image_path = str(windows_cfg.get("source_image_path") or "").strip()
    image_kind = str(windows_cfg.get("source_image_kind") or "").strip().lower()
    template_id = str(windows_cfg.get("ovf_template_id") or "").strip()
    if template_id:
        template = ovf_service.get_template(cfg, template_id)
        if template:
            readiness = dict(template.get("readiness") or {})
            if not readiness.get("ready"):
                warnings.append(str(readiness.get("summary") or "Selected OVF template source is not ready."))
                warnings.extend([str(item) for item in list(readiness.get("blockers") or [])])
    if not image_path:
        warnings.append("No OVA/OVF image is uploaded yet.")
    elif not Path(image_path).exists():
        warnings.append("Configured OVA/OVF path does not exist anymore.")
    if image_kind not in {"ova", "ovf"}:
        warnings.append("Source image type must be OVA or OVF.")
    if not str(windows_cfg.get("vm_name") or "").strip():
        warnings.append("Windows VM name is missing.")
    if not str(windows_cfg.get("admin_password") or ""):
        warnings.append("Windows administrator password is missing.")
    plan = {
        "mode": "dry_run",
        "image_path": image_path,
        "image_kind": image_kind,
        "vm_name": str(windows_cfg.get("vm_name") or ""),
        "target_ip": str(windows_cfg.get("ip_address") or cfg.get("ip_plan", {}).get("windows") or ""),
        "gateway": str(windows_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or ""),
        "dns_servers": list(windows_cfg.get("dns_servers") or cfg.get("shared_network", {}).get("dns_servers") or []),
        "vsphere_host": str(windows_cfg.get("vsphere_host") or ""),
        "vsphere_username": str(windows_cfg.get("vsphere_username") or ""),
        "datacenter": str(windows_cfg.get("vsphere_datacenter") or ""),
        "datastore": str(windows_cfg.get("vsphere_datastore") or ""),
        "network": str(windows_cfg.get("vsphere_network") or ""),
        "folder": str(windows_cfg.get("vsphere_folder") or ""),
        "resource_pool": str(windows_cfg.get("vsphere_resource_pool") or ""),
        "winrm_host": str(windows_cfg.get("ip_address") or cfg.get("ip_plan", {}).get("windows") or ""),
        "winrm_username": str(windows_cfg.get("winrm_username") or ""),
        "winrm_port": int(windows_cfg.get("winrm_port") or 5986),
        "warnings": warnings,
        "ready": not warnings,
    }
    interface_check = runtime["validate_ovf_inputs"](plan)
    warnings.extend([item for item in interface_check.get("warnings", []) if item not in warnings])
    if interface_check.get("source_summary"):
        plan["source_summary"] = interface_check.get("source_summary")
    if interface_check.get("deployment_preview"):
        plan["deployment_preview"] = interface_check.get("deployment_preview")
    plan["warnings"] = warnings
    plan["ready"] = not warnings
    cfg["windows"]["install_plan"] = plan
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "windows_install_planned",
        workflow="windows",
        summary="Built a dry-run Windows VM install plan from current Windows settings.",
        target=plan.get("vm_name") or "",
        details=[f"Warnings: {len(warnings)}"],
    )
    tone = "ready" if not warnings else "pending"
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Windows install plan preview",
            "Built a dry-run install plan. No VM changes were made.",
            tone=tone,
            outcomes=[
                f"VM: {plan.get('vm_name') or 'Not set'}",
                f"Image: {windows_cfg.get('source_image_name') or 'Not uploaded'}",
                f"Target: {plan.get('vsphere_host') or 'Not set'} / {plan.get('datastore') or 'No datastore'}",
                f"Readiness: {'Ready' if plan.get('ready') else 'Needs attention'}",
            ],
            details=warnings if warnings else ["Dry-run plan looks complete."],
        ),
    )


async def probe_windows_vsphere_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
):
    cfg = runtime["load_kit_config"]()
    posted_settings = _apply_windows_settings_form(cfg, {key: value for key, value in dict(await request.form()).items()})
    windows_cfg = cfg.get("windows", {}) or {}
    if not str(windows_cfg.get("vsphere_host") or "").strip() or not str(windows_cfg.get("vsphere_username") or "").strip() or not str(windows_cfg.get("vsphere_password") or ""):
        if posted_settings:
            runtime["save_kit_config"](cfg)
        return runtime["render_page"](request, cfg, active_page=return_page, error_message="vSphere probe failed: host, username, and password are required.")
    try:
        client = runtime["build_vsphere_client"](windows_cfg)
        result = client.inventory_summary()
        cfg["windows"]["last_vsphere_probe"] = result
        runtime["save_kit_config"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "vSphere interface reachable",
                "Connected to the VMware control plane and read basic inventory.",
                tone="ready",
                outcomes=[
                    f"Product: {result.get('product') or 'Unknown'}",
                    f"API: {result.get('api_version') or 'Unknown'}",
                    f"Datacenters: {', '.join(result.get('datacenters') or []) or 'None returned'}",
                ],
            ),
        )
    except Exception as e:
        cfg["windows"]["last_vsphere_probe"] = {"connected": False, "error": str(e).splitlines()[0]}
        runtime["save_kit_config"](cfg)
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"vSphere probe failed: {str(e).splitlines()[0]}")


async def probe_windows_winrm_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
):
    cfg = runtime["load_kit_config"]()
    posted_settings = _apply_windows_settings_form(cfg, {key: value for key, value in dict(await request.form()).items()})
    windows_cfg = cfg.get("windows", {}) or {}
    host = str(windows_cfg.get("ip_address") or cfg.get("ip_plan", {}).get("windows") or "").strip()
    if not host or not str(windows_cfg.get("winrm_username") or "").strip() or not str(windows_cfg.get("winrm_password") or ""):
        if posted_settings:
            runtime["save_kit_config"](cfg)
        return runtime["render_page"](request, cfg, active_page=return_page, error_message="WinRM probe failed: host, username, and password are required.")
    try:
        client = runtime["build_winrm_client"](windows_cfg, host)
        result = client.probe()
        cfg["windows"]["last_winrm_probe"] = result
        runtime["save_kit_config"](cfg)
        tone = "ready" if result.get("connected") else "pending"
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "WinRM probe complete",
                "Ran a Windows remote-management reachability check.",
                tone=tone,
                outcomes=[
                    f"Endpoint: {result.get('endpoint') or host}",
                    f"Status: {result.get('status_code')}",
                    f"Hostname: {result.get('stdout') or 'Not returned'}",
                ],
                details=[result.get("stderr")] if result.get("stderr") else [],
            ),
        )
    except Exception as e:
        cfg["windows"]["last_winrm_probe"] = {"connected": False, "error": str(e).splitlines()[0]}
        runtime["save_kit_config"](cfg)
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"WinRM probe failed: {str(e).splitlines()[0]}")


@router.post("/register-windows-ovf-path", response_class=HTMLResponse)
async def register_windows_ovf_path_route(
    request: Request,
    return_page: str = Form("windows"),
    windows_ovf_path: str = Form(""),
):
    from app import main

    return await register_windows_ovf_path_handler(
        request,
        runtime={
            "load_kit_config": main.load_kit_config,
            "save_kit_config": main.save_kit_config,
            "append_activity_event": main.append_activity_event,
            "render_page": main.render_page,
            "build_action_feedback": main.build_action_feedback,
            "inspect_ovf_source": inspect_ovf_source,
        },
        return_page=return_page,
        windows_ovf_path=windows_ovf_path,
    )


@router.post("/select-windows-ovf-template", response_class=HTMLResponse)
async def select_windows_ovf_template_route(
    request: Request,
    return_page: str = Form("windows"),
    windows_ovf_template_id: str = Form(""),
):
    from app import main

    return await select_windows_ovf_template_handler(
        request,
        runtime={
            "load_kit_config": main.load_kit_config,
            "save_kit_config": main.save_kit_config,
            "append_activity_event": main.append_activity_event,
            "render_page": main.render_page,
            "build_action_feedback": main.build_action_feedback,
        },
        return_page=return_page,
        windows_ovf_template_id=windows_ovf_template_id,
    )


def register_module_routes(app: FastAPI) -> None:
    # Most Windows routes are still served by legacy app/main.py endpoints during migration.
    app.include_router(router)
