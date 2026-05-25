from __future__ import annotations

from typing import Any


class EsxiConfigModuleService:
    def _manual(self, action: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "module": "esxi_config",
            "action": action,
            "ok": False,
            "implemented": False,
            "state": "not_implemented",
            "message": "ESXi configuration is currently driven by the ESXi page and shared stage registry; this module service endpoint is not wired directly.",
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
