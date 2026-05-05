from __future__ import annotations

from typing import Any

from app.core.stage_registry import CallableStagePlugin


def create_ilo_stage() -> CallableStagePlugin:
    return CallableStagePlugin(
        name="ilo",
        title="iLO",
        enabled_fn=lambda context: bool((context.get("cfg") or {}).get("included", {}).get("ilo", True)),
        plan_fn=lambda context: dict((context.get("planners") or {}).get("ilo", lambda: {})()),
        validate_fn=lambda context: dict((context.get("validators") or {}).get("ilo", lambda: {})()),
        execute_fn=lambda context, job: (context.get("executors") or {}).get("ilo", lambda _job: None)(job),
    )
