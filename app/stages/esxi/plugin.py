from __future__ import annotations

from app.core.stage_registry import CallableStagePlugin


def create_esxi_stage() -> CallableStagePlugin:
    return CallableStagePlugin(
        name="esxi",
        title="ESXi",
        enabled_fn=lambda context: bool((context.get("cfg") or {}).get("included", {}).get("esxi", True)),
        plan_fn=lambda context: dict((context.get("planners") or {}).get("esxi", lambda: {})()),
        validate_fn=lambda context: dict((context.get("validators") or {}).get("esxi", lambda: {})()),
        execute_fn=lambda context, job: (context.get("executors") or {}).get("esxi", lambda _job: None)(job),
    )
