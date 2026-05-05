from __future__ import annotations

from typing import Any, Callable

from .models import CommandResult, OperationCommand


class PolicyRunner:
    def __init__(self, handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]] | None = None):
        self.handlers = handlers or {}

    def run(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for action in actions:
            action_type = str(action.get("type") or "").strip()
            handler = self.handlers.get(action_type)
            if handler is None:
                results.append(CommandResult(ok=False, failures=[f"No handler registered for policy action: {action_type}"], raw=action).model_dump())
                continue
            payload = handler(action, context) or {}
            results.append(payload)
        return results


def command_from_policy_action(name: str, action: dict[str, Any], *, destructive: bool = False, requires_confirmation: bool = False) -> OperationCommand:
    labels = ["destructive"] if destructive else []
    return OperationCommand(
        name=name,
        preview={"action": action},
        validate_payload={"action": action},
        apply_payload={"action": action},
        result_recording_payload={"action": action},
        requires_confirmation=requires_confirmation,
        risk_labels=labels,
    )
