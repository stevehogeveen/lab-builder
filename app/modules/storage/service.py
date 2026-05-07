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

    def build_plan_overrides(self, payload: dict[str, Any]) -> dict[str, Any]:
        overrides: dict[str, Any] = {
            "controller_path": payload.get("controller_path", ""),
            "os_controller_path": payload.get("os_controller_path", ""),
            "data_controller_path": payload.get("data_controller_path", ""),
            "os_drive_ids": payload.get("os_drive_ids", []),
            "data_drive_ids": payload.get("data_drive_ids", []),
            "hot_spare_drive_id": payload.get("hot_spare_drive_id", ""),
            "os_drive_paths": payload.get("os_drive_paths", []),
            "data_drive_paths": payload.get("data_drive_paths", []),
            "hot_spare_path": payload.get("hot_spare_path", ""),
        }
        if not any(
            overrides.get(key)
            for key in (
                "os_drive_ids",
                "data_drive_ids",
                "hot_spare_drive_id",
                "os_drive_paths",
                "data_drive_paths",
                "hot_spare_path",
            )
        ):
            overrides["os_bays"] = payload.get("os_bays", [])
            overrides["data_bays"] = payload.get("data_bays", [])
            overrides["hot_spare_bay"] = payload.get("hot_spare_bay", "")
        if payload.get("os_raid_level") is not None:
            overrides["os_raid_level"] = payload.get("os_raid_level")
        if payload.get("data_raid_level") is not None:
            overrides["data_raid_level"] = payload.get("data_raid_level")
        return overrides


def default_storage_module_service() -> StorageModuleService:
    return StorageModuleService()
