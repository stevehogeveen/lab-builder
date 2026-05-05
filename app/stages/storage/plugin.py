from __future__ import annotations

from app.core.stage_registry import CallableStagePlugin


def create_storage_stage() -> CallableStagePlugin:
    return CallableStagePlugin(
        name="storage",
        title="Storage",
        enabled_fn=lambda context: bool((context.get("cfg") or {}).get("included", {}).get("storage", False)),
        plan_fn=lambda context: dict((context.get("planners") or {}).get("storage", lambda: {})()),
        validate_fn=lambda context: dict((context.get("validators") or {}).get("storage", lambda: {})()),
        execute_fn=lambda context, job: (context.get("executors") or {}).get("storage", lambda _job: None)(job),
    )
