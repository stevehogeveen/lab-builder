from __future__ import annotations

from app.core.stage_registry import CallableStagePlugin


def create_netapp_stage() -> CallableStagePlugin:
    return CallableStagePlugin(
        name="netapp",
        title="NetApp",
        enabled_fn=lambda context: bool((context.get("cfg") or {}).get("included", {}).get("netapp", False)),
        plan_fn=lambda context: dict((context.get("planners") or {}).get("netapp", lambda: {})()),
        validate_fn=lambda context: dict((context.get("validators") or {}).get("netapp", lambda: {})()),
        execute_fn=lambda context, job: (context.get("executors") or {}).get("netapp", lambda _job: None)(job),
    )
