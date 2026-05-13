from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ovf import inspect_ovf_directory


class OvfTemplateService:
    def register_directory(
        self,
        cfg: dict[str, Any],
        *,
        directory_path: str,
        template_name: str = "",
        os_family: str = "",
        ovf_name: str = "",
        source_location_type: str = "local",
    ) -> dict[str, Any]:
        summary = inspect_ovf_directory(directory_path, ovf_name=ovf_name)
        if summary.get("warnings"):
            return {"ok": False, "summary": summary, "warnings": list(summary.get("warnings") or [])}
        template_id = self.template_id(summary, template_name=template_name)
        entry = self.template_entry(
            template_id,
            summary,
            template_name=template_name,
            os_family=os_family,
            source_location_type=source_location_type,
            readiness=self.source_readiness(cfg, source_location_type),
        )
        cfg.setdefault("ovf_templates", {})
        templates = cfg["ovf_templates"].setdefault("templates", {})
        templates[template_id] = entry
        cfg["ovf_templates"]["last_selected_template_id"] = template_id
        return {"ok": True, "template_id": template_id, "template": entry, "summary": summary, "warnings": []}

    @staticmethod
    def template_id(summary: dict[str, Any], *, template_name: str = "") -> str:
        name = str(template_name or summary.get("vm_name") or summary.get("name") or "ovf-template").strip()
        safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
        safe = "-".join(part for part in safe.split("-") if part)
        return safe or "ovf-template"

    @staticmethod
    def template_entry(
        template_id: str,
        summary: dict[str, Any],
        *,
        template_name: str = "",
        os_family: str = "",
        source_location_type: str = "local",
        readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        location_type = OvfTemplateService.normalize_source_location_type(source_location_type)
        return {
            "id": template_id,
            "name": str(template_name or summary.get("vm_name") or summary.get("name") or template_id).strip(),
            "os_family": str(os_family or "").strip().lower(),
            "source_location_type": location_type,
            "required_components": ["netapp"] if location_type == "netapp" else [],
            "readiness": readiness or OvfTemplateService.source_readiness({}, location_type),
            "kind": str(summary.get("kind") or "ovf").strip().lower(),
            "directory": str(summary.get("directory") or Path(str(summary.get("path") or "")).parent),
            "descriptor_path": str(summary.get("path") or ""),
            "descriptor_name": str(summary.get("name") or ""),
            "vm_name": str(summary.get("vm_name") or ""),
            "network_names": list(summary.get("network_names") or []),
            "os_description": str(summary.get("os_description") or ""),
            "hardware_version": str(summary.get("hardware_version") or ""),
            "cpu_count": str(summary.get("cpu_count") or ""),
            "memory_mb": str(summary.get("memory_mb") or ""),
            "disk_capacity": str(summary.get("disk_capacity") or ""),
            "files": list(summary.get("files") or []),
            "file_count": len(list(summary.get("files") or [])),
            "total_size_bytes": int(summary.get("total_size_bytes") or 0),
            "total_size_display": str(summary.get("total_size_display") or "0 B"),
            "warnings": list(summary.get("warnings") or []),
        }

    @staticmethod
    def normalize_source_location_type(value: str) -> str:
        normalized = str(value or "local").strip().lower()
        return normalized if normalized in {"local", "netapp", "esxi_datastore"} else "local"

    @staticmethod
    def source_readiness(cfg: dict[str, Any], source_location_type: str) -> dict[str, Any]:
        location_type = OvfTemplateService.normalize_source_location_type(source_location_type)
        if location_type == "local":
            return {
                "ready": True,
                "tone": "ready",
                "label": "Ready",
                "summary": "Local server source does not require NetApp.",
                "blockers": [],
            }
        if location_type == "netapp":
            probe = (((cfg.get("netapp") or {}).get("vmware_checks") or {}).get("nfs_mount") or {})
            if probe.get("ready"):
                return {
                    "ready": True,
                    "tone": "ready",
                    "label": "Ready",
                    "summary": f"NetApp NFS datastore probe is ready: {probe.get('datastore_name') or 'datastore ready'}.",
                    "blockers": [],
                }
            return {
                "ready": False,
                "tone": "pending",
                "label": "Blocked",
                "summary": "NetApp-backed OVF source needs a ready NetApp VMware/NFS datastore probe first.",
                "blockers": ["Run NetApp discovery and the ESXi/NFS probe before using this template."],
            }
        return {
            "ready": False,
            "tone": "pending",
            "label": "Blocked",
            "summary": "ESXi datastore sources are planned but not wired yet.",
            "blockers": ["Use Local server or NetApp-backed storage for now."],
        }

    def refresh_template_readiness(self, cfg: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
        item = dict(template)
        item["readiness"] = self.source_readiness(cfg, str(item.get("source_location_type") or "local"))
        return item

    def templates(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        values = (cfg.get("ovf_templates") or {}).get("templates") or {}
        return sorted([self.refresh_template_readiness(cfg, dict(item)) for item in values.values()], key=lambda item: str(item.get("name") or item.get("id") or ""))

    def get_template(self, cfg: dict[str, Any], template_id: str) -> dict[str, Any] | None:
        templates = (cfg.get("ovf_templates") or {}).get("templates") or {}
        item = templates.get(str(template_id or "").strip())
        return self.refresh_template_readiness(cfg, dict(item)) if isinstance(item, dict) else None
