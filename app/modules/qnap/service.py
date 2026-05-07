from __future__ import annotations

from typing import Any


class QnapModuleService:
    def discover(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "qnap", "action": "discover", "ok": True}

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "qnap", "action": "plan", "ok": True}

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "qnap", "action": "validate", "ok": True}

    def preview(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "qnap", "action": "preview", "ok": True}

    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        return {"module": "qnap", "action": "apply", "ok": True}

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "qnap", "action": "status", "ok": True}

    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]:
        return {"module": "qnap", "action": "repair", "issue_id": issue_id, "ok": True}

