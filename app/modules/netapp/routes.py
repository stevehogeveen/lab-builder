from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse

from app.modules.netapp.service import NetAppModuleService


router = APIRouter()


@router.get("/modules/netapp", response_class=HTMLResponse)
async def netapp_module_page(request: Request):
    # Import lazily to avoid startup import cycles while main.py is still monolithic.
    from app import main

    cfg = main.load_kit_config()
    service = NetAppModuleService()
    preview = service.preview({"cfg": cfg, "request": request})
    return main.render_page(
        request,
        cfg,
        active_page="netapp",
        action_feedback=main.build_action_feedback(
            "NetApp module",
            "NetApp module scaffold is loaded and isolated.",
            tone="progress",
            status_label="Scaffold",
            outcomes=[str(preview.get("note") or "NetApp module preview stub")],
        ),
    )


def register_module_routes(app: FastAPI) -> None:
    app.include_router(router)

