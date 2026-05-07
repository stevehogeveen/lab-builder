from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse


router = APIRouter()


@router.get("/modules/cisco", response_class=HTMLResponse)
async def cisco_module_page(request: Request):
    from app import main

    cfg = main.load_kit_config()
    return main.render_page(
        request,
        cfg,
        active_page="cisco",
        action_feedback=main.build_action_feedback(
            "Cisco module",
            "Cisco module scaffold is loaded and isolated.",
            tone="progress",
            status_label="Scaffold",
            outcomes=["Cisco module preview stub"],
        ),
    )


def register_module_routes(app: FastAPI) -> None:
    app.include_router(router)

