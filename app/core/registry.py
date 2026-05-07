from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Protocol

import yaml
from fastapi import FastAPI

from app.core.errors import ModuleRegistryError


class ModuleService(Protocol):
    def discover(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def plan(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def validate(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def preview(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]: ...
    def status(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]: ...


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def discover_module_manifests(modules_dir: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    if not modules_dir.exists():
        return manifests
    for child in sorted(modules_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.yml"
        if not manifest_path.exists():
            continue
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ModuleRegistryError(f"Invalid manifest format: {manifest_path}")
        payload["_module_dir"] = str(child)
        payload["_manifest_path"] = str(manifest_path)
        payload["name"] = str(payload.get("name") or child.name).strip()
        payload["enabled"] = _bool(payload.get("enabled"), default=True)
        payload.setdefault("capabilities", ["discover", "plan", "validate", "preview", "apply", "status", "repair"])
        manifests.append(payload)
    return manifests


def module_navigation(manifests: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in manifests:
        if not _bool(item.get("enabled"), True):
            continue
        nav = dict(item.get("navigation") or {})
        href = str(nav.get("href") or "").strip()
        if not href:
            prefix = str((item.get("routes") or {}).get("prefix") or "").strip()
            href = prefix or f"/modules/{item['name']}"
        rows.append(
            {
                "name": str(item.get("name") or ""),
                "label": str(nav.get("label") or item.get("title") or item.get("name") or ""),
                "section": str(nav.get("section") or "Modules"),
                "href": href,
                "active_page": str(nav.get("active_page") or item.get("name") or ""),
            }
        )
    return rows


def _env_module_set(var_name: str) -> set[str]:
    value = str(os.getenv(var_name, "") or "").strip()
    if not value:
        return set()
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def apply_module_enable_overrides(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    disabled = _env_module_set("LAB_BUILDER_DISABLED_MODULES")
    enabled_only = _env_module_set("LAB_BUILDER_ENABLED_MODULES")
    for item in manifests:
        name = str(item.get("name") or "").strip().lower()
        enabled = _bool(item.get("enabled"), True)
        if enabled_only:
            enabled = name in enabled_only
        if name in disabled:
            enabled = False
        item["enabled"] = bool(enabled)
    return manifests


def load_modules(app: FastAPI, *, modules_dir: Path, package_root: str = "app.modules") -> list[dict[str, Any]]:
    manifests = apply_module_enable_overrides(discover_module_manifests(modules_dir))
    for manifest in manifests:
        if not _bool(manifest.get("enabled"), True):
            continue
        module_name = str(manifest.get("name") or "").strip()
        if not module_name:
            continue
        routes_module = importlib.import_module(f"{package_root}.{module_name}.routes")
        register_fn = getattr(routes_module, "register_module_routes", None)
        if register_fn is None:
            raise ModuleRegistryError(f"Module {module_name} is missing register_module_routes(app).")
        register_fn(app)
    app.state.module_manifests = manifests
    app.state.module_navigation = module_navigation(manifests)
    app.state.module_enabled = {
        str(item.get("name") or ""): bool(item.get("enabled"))
        for item in manifests
    }
    return manifests
