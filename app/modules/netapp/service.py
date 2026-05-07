from __future__ import annotations

from typing import Any


class NetAppModuleService:
    module = "netapp"

    def _preview(self, context: dict[str, Any], action: str) -> dict[str, Any]:
        return {
            "module": self.module,
            "action": action,
            "ok": True,
            "note": f"NetApp {action} stub",
            "context": {
                "module_name": str((context.get("module_name") or self.module) or self.module),
                "site_name": str(((context.get("cfg") or {}).get("site") or {}).get("name") or "Kit-01"),
                "payload_summary": list((context.get("payload") or {}).keys()),
            },
        }

    def discover(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._preview(context, "discover")

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        plan = {
            "mode": "snapshot-only",
            "steps": [
                "Discover storage and cluster metadata.",
                "Evaluate policy deltas.",
                "Render a mock execution plan.",
            ],
        }
        payload = self._preview(context, "plan")
        payload["plan"] = plan
        return payload

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self._preview(context, "validate")
        payload["checks"] = {
            "cluster_reachable": True,
            "policy_shape_valid": True,
            "ip_plan_available": True,
        }
        payload["warnings"] = []
        return payload

    def preview(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self._preview(context, "preview")
        payload["preview"] = {
            "targets": [
                {"name": "netapp-cluster-01", "ip": "10.55.66.240"},
                {"name": "netapp-node-a", "ip": "10.55.66.241"},
            ],
            "operations": [
                "Read-only inventory lookup",
                "Policy plan generation (mock)",
                "Diff output preview",
            ],
        }
        return payload

    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        payload = self._preview(context, "apply")
        payload.update(
            {
                "job_id": str((job or {}).get("job_id") or "job-netapp-mock-001"),
                "scope": str((job or {}).get("scope") or "netapp.apply"),
                "result": "accepted",
            }
        )
        return payload

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self._preview(context, "status")
        payload.update(
            {
                "status": "safe-mock",
                "health": {
                    "cluster": "healthy",
                    "policy": "pending review",
                    "storage": "discovered",
                },
            }
        )
        return payload

    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]:
        payload = self._preview(context, "repair")
        payload["issue_id"] = str(issue_id)
        payload["resolution"] = "tracked"
        payload["details"] = {
            "attempted": "non-destructive-mock-repair",
            "next_step": "review after re-run",
        }
        return payload
