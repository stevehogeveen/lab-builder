from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.modules.netapp.schemas import NetAppModuleContext
from app.modules.netapp.service import NetAppModuleService

router = APIRouter()

MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"
template_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

service = NetAppModuleService()


def _module_context(request: Request) -> dict[str, Any]:
    # Import from app at call time to avoid startup cycles.
    from app import main

    cfg = main.load_kit_config()
    return NetAppModuleContext(
        module_name="netapp",
        payload={"path": str(request.url.path), "query": str(request.url.query), "method": request.method},
        cfg=cfg,
    ).model_dump()


def _render_template(template_name: str, context: dict[str, Any]) -> str:
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        return (
            '<section class="panel space-y-6">\n'
            "    <h1>NetApp module</h1>\n"
            "    <p>NetApp preview template is not available.</p>\n"
            "</section>\n"
        )
    template = template_env.get_template(template_name)
    return template.render(**context)


@router.get("/modules/netapp", response_class=HTMLResponse)
async def netapp_module_page(request: Request):
    from app import main

    context = _module_context(request)
    payload = service.preview(context)
    return main.render_page(
        request,
        context["cfg"],
        active_page="netapp",
        action_feedback=main.build_action_feedback(
            "NetApp module",
            "NetApp module scaffold is loaded and isolated.",
            tone="progress",
            status_label="Scaffold",
            outcomes=[str(payload.get("note") or "NetApp module preview stub")],
        ),
    )


@router.get("/modules/netapp/preview", response_class=HTMLResponse)
async def netapp_module_preview(request: Request):
    context = _module_context(request)
    payload = service.preview(context)
    html = _render_template("netapp_preview.html", {"payload": payload, "config": context["cfg"]})
    return HTMLResponse(html)


@router.post("/modules/netapp/discover")
async def netapp_module_discover(request: Request):
    return service.discover(_module_context(request))


@router.post("/modules/netapp/plan")
async def netapp_module_plan(request: Request):
    return service.plan(_module_context(request))


@router.post("/modules/netapp/validate")
async def netapp_module_validate(request: Request):
    return service.validate(_module_context(request))


@router.post("/modules/netapp/apply")
async def netapp_module_apply(request: Request):
    context = _module_context(request)
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    return service.apply(context, dict(body.get("job", {}) if body else {}))


@router.get("/modules/netapp/status")
async def netapp_module_status(request: Request):
    return service.status(_module_context(request))


@router.post("/modules/netapp/repair/{issue_id}")
async def netapp_module_repair(request: Request, issue_id: str):
    return service.repair(_module_context(request), issue_id)


def register_module_routes(app: FastAPI) -> None:
    app.include_router(router)
