from __future__ import annotations

from app.core.stage_registry import CallableStagePlugin


def create_windows_stage() -> CallableStagePlugin:
    return CallableStagePlugin(
        name="windows",
        title="Windows",
        enabled_fn=lambda context: bool((context.get("cfg") or {}).get("included", {}).get("windows", False)),
        plan_fn=lambda context: dict((context.get("planners") or {}).get("windows", lambda: {})()),
        validate_fn=lambda context: dict((context.get("validators") or {}).get("windows", lambda: {})()),
        execute_fn=lambda context, job: (context.get("executors") or {}).get("windows", lambda _job: None)(job),
    )

