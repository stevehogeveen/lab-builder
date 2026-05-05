from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class StagePlugin(Protocol):
    name: str
    title: str

    def enabled(self, context: dict[str, Any]) -> bool: ...
    def plan(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def validate(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def execute(self, context: dict[str, Any], job: dict[str, Any]) -> Any: ...


@dataclass
class CallableStagePlugin:
    name: str
    title: str
    enabled_fn: Callable[[dict[str, Any]], bool]
    plan_fn: Callable[[dict[str, Any]], dict[str, Any]]
    validate_fn: Callable[[dict[str, Any]], dict[str, Any]]
    execute_fn: Callable[[dict[str, Any], dict[str, Any]], Any]

    def enabled(self, context: dict[str, Any]) -> bool:
        return bool(self.enabled_fn(context))

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        return dict(self.plan_fn(context) or {})

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        return dict(self.validate_fn(context) or {})

    def execute(self, context: dict[str, Any], job: dict[str, Any]) -> Any:
        return self.execute_fn(context, job)


class StageRegistry:
    def __init__(self, stages: list[StagePlugin] | None = None):
        self._stages: dict[str, StagePlugin] = {}
        for stage in stages or []:
            self.register(stage)

    def register(self, stage: StagePlugin) -> None:
        self._stages[stage.name] = stage

    def get(self, name: str) -> StagePlugin | None:
        return self._stages.get(str(name or ""))

    def all(self) -> list[StagePlugin]:
        return [self._stages[key] for key in sorted(self._stages)]

    def enabled(self, context: dict[str, Any]) -> list[StagePlugin]:
        return [stage for stage in self.all() if stage.enabled(context)]
