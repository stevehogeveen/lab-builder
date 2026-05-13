from __future__ import annotations

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from app.modules.ovf_templates.service import OvfTemplateService


router = APIRouter()
service = OvfTemplateService()


def _render_ovf_page(request: Request, cfg: dict, *, action_feedback: dict | None = None, error_message: str = "") -> HTMLResponse:
    from app import main

    return main.render_page(
        request,
        cfg,
        active_page="ovf_templates",
        action_feedback=action_feedback,
        error_message=error_message,
        extra_context={"ovf_template_payload": {"templates": service.templates(cfg)}},
    )


@router.get("/modules/ovf-templates", response_class=HTMLResponse)
async def ovf_templates_page(request: Request):
    from app import main

    return _render_ovf_page(request, main.load_kit_config())


@router.post("/modules/ovf-templates/register-directory", response_class=HTMLResponse)
async def register_ovf_template_directory(
    request: Request,
    return_page: str = Form("ovf_templates"),
    ovf_template_directory: str = Form(""),
    ovf_template_name: str = Form(""),
    ovf_template_os_family: str = Form(""),
    ovf_source_location_type: str = Form("local"),
    ovf_descriptor_name: str = Form(""),
):
    from app import main

    cfg = main.load_kit_config()
    result = service.register_directory(
        cfg,
        directory_path=ovf_template_directory,
        template_name=ovf_template_name,
        os_family=ovf_template_os_family,
        ovf_name=ovf_descriptor_name,
        source_location_type=ovf_source_location_type,
    )
    if not result.get("ok"):
        candidates = list((result.get("summary") or {}).get("candidates") or [])
        details = list(result.get("warnings") or [])
        if candidates:
            details.append(f"Candidates: {', '.join(candidates)}")
        return _render_ovf_page(request, cfg, error_message=" ".join(details))

    main.save_kit_config(cfg)
    template = dict(result.get("template") or {})
    feedback = main.build_action_feedback(
        "OVF template registered",
        "Validated the whole local template directory and saved it for VM workflows.",
        tone="ready",
        outcomes=[
            f"Template: {template.get('name') or template.get('id')}",
            f"Descriptor: {template.get('descriptor_name') or 'Not set'}",
            f"Files: {template.get('file_count', 0)}",
            f"Size: {template.get('total_size_display') or '0 B'}",
            f"Source: {str(template.get('source_location_type') or 'local').replace('_', ' ').title()}",
        ],
        details=list(((template.get("readiness") or {}).get("blockers") or [])),
    )
    page = str(return_page or "ovf_templates").strip().lower()
    if page == "windows":
        return main.render_page(request, cfg, active_page="windows", action_feedback=feedback)
    return _render_ovf_page(request, cfg, action_feedback=feedback)


def register_module_routes(app: FastAPI) -> None:
    app.include_router(router)
