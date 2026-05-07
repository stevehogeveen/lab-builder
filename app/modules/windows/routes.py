from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Form, Request, UploadFile


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


async def plan_windows_install_handler(
    request: Request,
    runtime: WindowsRuntime,
    return_page: str = Form("windows"),
):
    cfg = runtime["load_kit_config"]()
    windows_cfg = cfg.get("windows", {}) or {}
    warnings: list[str] = []
    image_path = str(windows_cfg.get("source_image_path") or "").strip()
    image_kind = str(windows_cfg.get("source_image_kind") or "").strip().lower()
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
        "warnings": warnings,
        "ready": not warnings,
    }
    cfg["windows"]["install_plan"] = plan
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "windows_install_planned",
        workflow="windows",
        summary="Built a dry-run Windows VM install plan from saved settings.",
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
                f"Readiness: {'Ready' if plan.get('ready') else 'Needs attention'}",
            ],
            details=warnings if warnings else ["Dry-run plan looks complete."],
        ),
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
