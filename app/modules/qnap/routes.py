from __future__ import annotations

from fastapi import FastAPI


def register_module_routes(app: FastAPI) -> None:
    # QNAP routes are still served by legacy app/main.py endpoints during migration.
    _ = app

