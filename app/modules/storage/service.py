from __future__ import annotations

from typing import Any


class StorageModuleService:
    def discover(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "storage", "action": "discover", "ok": True}

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "storage", "action": "plan", "ok": True}

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "storage", "action": "validate", "ok": True}

    def preview(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "storage", "action": "preview", "ok": True}

    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        return {"module": "storage", "action": "apply", "ok": True}

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "storage", "action": "status", "ok": True}

    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]:
        return {"module": "storage", "action": "repair", "issue_id": issue_id, "ok": True}

    def update_saved_storage_target(self, cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        ensure_storage_config = payload["ensure_storage_config"]
        storage_cfg = ensure_storage_config(cfg)
        storage_target_mode = str(payload.get("storage_target_mode") or "override")
        if storage_target_mode == "defaults":
            storage_cfg["target_host_override"] = ""
            storage_cfg["username"] = ""
            storage_cfg["password"] = ""
        else:
            storage_cfg["target_host_override"] = str(payload.get("storage_target_host") or "").strip()
            storage_cfg["username"] = str(payload.get("storage_username") or "").strip()
            storage_cfg["password"] = str(payload.get("storage_password") or "")
        return {"cfg": cfg, "using_defaults": storage_target_mode == "defaults"}

    def resolve_storage_access(self, cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        resolve_storage_target_host = payload["resolve_storage_target_host"]
        resolve_storage_target_credentials = payload["resolve_storage_target_credentials"]
        storage_target = resolve_storage_target_host(cfg)
        storage_credentials = resolve_storage_target_credentials(cfg)
        host = str(storage_target.get("resolved") or "")
        username = str(storage_credentials.get("username") or "")
        password = str(storage_credentials.get("password") or "")
        valid = bool(host and username and password)
        return {
            "valid": valid,
            "host": host,
            "username": username,
            "password": password,
            "storage_target": storage_target,
            "storage_credentials": storage_credentials,
        }


def default_storage_module_service() -> StorageModuleService:
    return StorageModuleService()
