from __future__ import annotations

from typing import Any


class QnapModuleService:
    def _manual(self, action: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "module": "qnap",
            "action": action,
            "ok": False,
            "implemented": False,
            "state": "manual_only",
            "message": "QNAP automation is not implemented yet. Save the target values here and perform the device work manually.",
            **(extra or {}),
        }

    def discover(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._manual("discover")

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._manual("plan")

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._manual("validate")

    def preview(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._manual("preview")

    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        return self._manual("apply")

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._manual("status")

    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]:
        return self._manual("repair", {"issue_id": issue_id})
