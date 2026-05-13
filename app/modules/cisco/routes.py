from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse


router = APIRouter()


async def _render_cisco_page(request: Request) -> HTMLResponse:
    from app import main

    cfg = main.load_kit_config()
    return main.render_page(
        request,
        cfg,
        active_page="cisco",
        action_feedback=main.build_action_feedback(
            "Cisco module",
            "Cisco setup workspace is loaded and isolated.",
            tone="progress",
            status_label="Ready",
            outcomes=["Cisco workflow is isolated in app/modules/cisco/"],
        ),
    )


@router.get("/modules/cisco", response_class=HTMLResponse)
async def cisco_module_page(request: Request):
    return await _render_cisco_page(request)


@router.get("/cisco", response_class=HTMLResponse)
async def cisco_legacy_page(request: Request):
    return await _render_cisco_page(request)


def register_module_routes(app: FastAPI) -> None:
    app.include_router(router)
