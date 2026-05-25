from __future__ import annotations

from typing import Any

from app.core.workflows import workflow_included, workflow_page_map, workflow_setup_order


def operator_setup_workflow_keys(cfg: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in workflow_setup_order():
        if key == "ilo" or workflow_included(cfg, key):
            keys.append(key)
    return keys


def operator_page_workflow_key(active_page: str) -> str | None:
    return workflow_page_map().get(str(active_page or "").strip().lower())


def operator_recommended_order() -> list[str]:
    return [key for key in workflow_setup_order() if key not in {"ilo", "storage"}]
