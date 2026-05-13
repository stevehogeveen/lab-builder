from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
MEDIA_SCAN_ROOT = REPO_ROOT / "media"
DEFAULT_UPGRADE_POLICIES = {
    "ilo": "block",
    "netapp": "block",
    "cisco_switch": "block",
}


def infer_ilo_family(manager_model: str) -> str:
    text = str(manager_model or "").strip().lower()
    if "ilo 6" in text or "ilo6" in text:
        return "iLO 6"
    if "ilo 5" in text or "ilo5" in text:
        return "iLO 5"
    return ""


def infer_ilo_media_family(filename: str) -> str:
    text = str(filename or "").strip().lower()
    if "ilo6" in text:
        return "iLO 6"
    if "ilo5" in text:
        return "iLO 5"
    return ""


@dataclass
class UpgradeCandidate:
    device: str
    version: str
    path: Path


def _normalize_version(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    ontap = re.search(r"(\d+\.\d+\.\d+(?:P\d+)?)", text, flags=re.IGNORECASE)
    if ontap:
        return ontap.group(1)
    generic = re.search(r"(\d+(?:\.\d+){1,3}[A-Za-z]?\d*)", text)
    if generic:
        return generic.group(1)
    return ""


def _version_key(value: str) -> tuple[int, ...]:
    text = _normalize_version(value)
    if not text:
        return tuple()
    patched = re.match(r"(\d+)\.(\d+)\.(\d+)(?:P(\d+))?$", text, flags=re.IGNORECASE)
    if patched:
        return (int(patched.group(1)), int(patched.group(2)), int(patched.group(3)), int(patched.group(4) or 0))
    parts = re.findall(r"\d+", text)
    return tuple(int(item) for item in parts)


def compare_versions(left: str, right: str) -> int | None:
    left_key = _version_key(left)
    right_key = _version_key(right)
    if not left_key or not right_key:
        return None
    width = max(len(left_key), len(right_key))
    left_key = left_key + (0,) * (width - len(left_key))
    right_key = right_key + (0,) * (width - len(right_key))
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def detect_upgrade_candidate(path: Path) -> UpgradeCandidate | None:
    name = path.name.lower()
    if not path.is_file():
        return None
    if "ilo" in name:
        version = _normalize_version(path.name)
        if not version:
            compact = re.search(r"ilo[56][._-](\d{3})\b", name)
            if compact:
                digits = compact.group(1)
                version = f"{digits[0]}.{digits[1:]}"
        if not version:
            return None
        return UpgradeCandidate(device="ilo", version=version, path=path)
    if "ontap" in name or "netapp" in name or "q_image" in name:
        version = _normalize_version(path.name)
        if not version:
            compact = re.search(r"\b(\d)(\d{2})(\d)(?:p(\d+))?_q_image\b", name, flags=re.IGNORECASE)
            if compact:
                version = f"{compact.group(1)}.{compact.group(2)}.{compact.group(3)}"
                if compact.group(4):
                    version = f"{version}P{compact.group(4)}"
        if not version:
            return None
        return UpgradeCandidate(device="netapp", version=version, path=path)
    if any(token in name for token in ("cisco", "iosxe", "cat9k", "nxos", "ios-")):
        version = _normalize_version(path.name)
        if not version:
            return None
        return UpgradeCandidate(device="cisco_switch", version=version, path=path)
    return None


def scan_upgrade_media(root: Path = MEDIA_SCAN_ROOT) -> dict[str, Any]:
    candidates: list[UpgradeCandidate] = []
    if root.exists():
        for dirpath, _, filenames in os.walk(root, onerror=lambda _: None):
            base = Path(dirpath)
            for filename in filenames:
                candidate = detect_upgrade_candidate(base / filename)
                if candidate is not None:
                    candidates.append(candidate)

    by_device: dict[str, list[UpgradeCandidate]] = {}
    for candidate in candidates:
        by_device.setdefault(candidate.device, []).append(candidate)

    latest: dict[str, dict[str, str]] = {}
    for device, items in by_device.items():
        winner = max(items, key=lambda item: _version_key(item.version))
        latest[device] = {
            "version": winner.version,
            "path": str(winner.path),
            "filename": winner.path.name,
        }

    return {
        "root": str(root),
        "latest": latest,
        "counts": {device: len(items) for device, items in by_device.items()},
        "candidates": [
            {"device": item.device, "version": item.version, "path": str(item.path), "filename": item.path.name}
            for item in sorted(candidates, key=lambda entry: (entry.device, _version_key(entry.version), entry.path.name))
        ],
    }


def select_upgrade_candidate(
    media_scan: dict[str, Any],
    device: str,
    details: dict[str, str] | None = None,
) -> dict[str, str]:
    details = dict(details or {})
    candidates = [dict(item) for item in list(media_scan.get("candidates") or []) if str(item.get("device") or "") == device]
    if not candidates:
        return dict((media_scan.get("latest") or {}).get(device) or {})
    if device != "ilo":
        winner = max(candidates, key=lambda item: _version_key(str(item.get("version") or "")))
        return {
            "version": str(winner.get("version") or "").strip(),
            "path": str(winner.get("path") or "").strip(),
            "filename": str(winner.get("filename") or "").strip(),
        }

    manager_family = infer_ilo_family(str(details.get("manager_model") or details.get("model") or "").strip())
    matched = []
    for candidate in candidates:
        media_family = infer_ilo_media_family(str(candidate.get("filename") or ""))
        if manager_family and media_family and manager_family == media_family:
            matched.append(candidate)
    pool = matched or candidates
    winner = max(pool, key=lambda item: _version_key(str(item.get("version") or "")))
    selected = {
        "version": str(winner.get("version") or "").strip(),
        "path": str(winner.get("path") or "").strip(),
        "filename": str(winner.get("filename") or "").strip(),
    }
    if manager_family:
        selected["manager_family"] = manager_family
    media_family = infer_ilo_media_family(selected.get("filename", ""))
    if media_family:
        selected["media_family"] = media_family
    return selected


def build_upgrade_planner(media_scan: dict[str, Any], current_versions: dict[str, str], current_sources: dict[str, str] | None = None) -> dict[str, Any]:
    return build_upgrade_planner_with_policies(media_scan, current_versions, current_sources=current_sources)


def normalize_upgrade_policies(cfg: dict[str, Any] | None = None, policies: dict[str, Any] | None = None) -> dict[str, str]:
    source = dict(policies or {})
    if cfg:
        source.update(dict((((cfg.get("upgrade_helper") or {}).get("policies")) or {})))
    normalized: dict[str, str] = {}
    for key, default in DEFAULT_UPGRADE_POLICIES.items():
        value = str(source.get(key) or default).strip().lower()
        normalized[key] = value if value in {"block", "warn", "ignore"} else default
    return normalized


def _policy_outcome(comparison: str, policy: str) -> tuple[bool, bool]:
    if comparison not in {"current_unknown", "upgrade_available"}:
        return False, False
    if policy == "ignore":
        return False, False
    if policy == "warn":
        return False, True
    return True, False


def build_upgrade_planner_with_policies(
    media_scan: dict[str, Any],
    current_versions: dict[str, str],
    current_sources: dict[str, str] | None = None,
    policies: dict[str, str] | None = None,
    device_details: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    latest = dict(media_scan.get("latest") or {})
    current_sources = dict(current_sources or {})
    policies = normalize_upgrade_policies(policies=policies)
    device_details = dict(device_details or {})
    device_meta = {
        "ilo": "iLO",
        "netapp": "ONTAP",
        "cisco_switch": "Cisco",
    }
    entries: list[dict[str, Any]] = []
    blockers = 0
    warnings = 0
    for key, label in device_meta.items():
        current_raw = str(current_versions.get(key) or "").strip()
        current = _normalize_version(current_raw)
        details = dict(device_details.get(key) or {})
        available = select_upgrade_candidate(media_scan, key, details) if latest.get(key) or media_scan.get("candidates") else {}
        media_version = _normalize_version(available.get("version", ""))
        cmp = compare_versions(current, media_version) if current and media_version else None
        if not current and not media_version:
            continue
        comparison = "unknown"
        recommendation = ""
        severity = "ready"
        compatibility_summary = ""
        compatibility_tone = "ready"
        detail_lines: list[str] = []
        if not media_version:
            comparison = "no_media"
            recommendation = f"No approved {label} upgrade file was found under {media_scan.get('root')}. Add one if this device should be evaluated before build."
        elif not current:
            comparison = "current_unknown"
            severity = "pending"
            recommendation = f"Read the current {label} version before prebuild. Media {media_version} is available but the device version is still unknown."
        elif cmp is not None and cmp < 0:
            comparison = "upgrade_available"
            severity = "pending"
            recommendation = f"Upgrade {label} to {media_version} before continuing if that file is the approved baseline."
        else:
            comparison = "current_enough"
            recommendation = f"No prebuild upgrade is required for {label} based on the discovered media."
        if key == "ilo":
            manager_model = str(details.get("manager_model") or details.get("model") or "").strip()
            media_filename = str(available.get("filename") or "").lower()
            current_family = infer_ilo_family(manager_model)
            media_family = infer_ilo_media_family(media_filename)
            if current_family:
                detail_lines.append(f"Detected manager family: {current_family}.")
            if media_family:
                detail_lines.append(f"Matched media family: {media_family}.")
            if current_family and media_family and current_family != media_family:
                compatibility_summary = f"Detected {current_family}, but matched media is for {media_family}."
                compatibility_tone = "pending"
            elif current_family and media_family:
                compatibility_summary = f"Detected {current_family} matches the selected media family."
            elif current_family and media_filename and not media_family:
                compatibility_summary = f"Matched media filename does not clearly identify an iLO family for detected {current_family}."
                compatibility_tone = "progress"
        elif key == "netapp":
            baseline_target = str(details.get("baseline_target") or "9.12.1").strip()
            minimum_version = str(details.get("minimum_version") or baseline_target).strip()
            if current:
                detail_lines.append(f"Current ONTAP: {current}.")
            if media_version:
                detail_lines.append(f"Matched media: {media_version}.")
            detail_lines.append(f"Baseline target: {baseline_target}.")
            if minimum_version and minimum_version != baseline_target:
                detail_lines.append(f"Minimum supported: {minimum_version}.")
            if current and compare_versions(current, baseline_target) is not None and compare_versions(current, baseline_target) < 0:
                compatibility_summary = f"Current ONTAP {current} is below the target baseline {baseline_target}."
                compatibility_tone = "pending"
            elif media_version and compare_versions(media_version, baseline_target) is not None and compare_versions(media_version, baseline_target) < 0:
                compatibility_summary = f"Matched media {media_version} is still below the target baseline {baseline_target}."
                compatibility_tone = "pending"
            elif media_version:
                compatibility_summary = f"Matched media {media_version} meets or exceeds the target baseline {baseline_target}."
        elif key == "cisco_switch":
            platform = str(details.get("platform") or "").strip()
            model = str(details.get("model") or "").strip()
            media_filename = str(available.get("filename") or "")
            if model:
                detail_lines.append(f"Detected model: {model}.")
            if platform:
                detail_lines.append(f"Detected platform: {platform}.")
            if media_filename:
                detail_lines.append(f"Matched media file: {media_filename}.")
            if model and media_filename and model.lower().split("-")[0] not in media_filename.lower():
                compatibility_summary = f"Matched media filename does not explicitly mention detected model {model}."
                compatibility_tone = "progress"
            elif platform and media_filename and platform.lower().split("-")[0] in media_filename.lower():
                compatibility_summary = f"Matched media filename aligns with detected platform {platform}."
        policy = str(policies.get(key) or DEFAULT_UPGRADE_POLICIES[key]).strip().lower()
        blocks_run, warns_only = _policy_outcome(comparison, policy)
        if blocks_run:
            blockers += 1
        elif warns_only:
            warnings += 1
            severity = "progress"
        entries.append(
            {
                "key": key,
                "label": label,
                "current_version": current,
                "current_raw": current_raw,
                "current_source": str(current_sources.get(key) or "").strip(),
                "media_version": media_version,
                "media_filename": str(available.get("filename") or "").strip(),
                "media_path": str(available.get("path") or "").strip(),
                "comparison": comparison,
                "prebuild_gate": blocks_run,
                "severity": severity,
                "recommended_action": recommendation,
                "policy": policy,
                "warn_only": warns_only,
                "blocks_run": blocks_run,
                "compatibility_summary": compatibility_summary,
                "compatibility_tone": compatibility_tone,
                "detail_lines": detail_lines,
            }
        )
    return {
        "entries": entries,
        "blockers": blockers,
        "warnings": warnings,
        "ready": len([item for item in entries if not item.get("blocks_run")]),
        "total": len(entries),
        "policies": policies,
    }


def build_upgrade_helper_summary(media_scan: dict[str, Any], current_versions: dict[str, str]) -> dict[str, Any]:
    latest = dict(media_scan.get("latest") or {})
    device_meta = {
        "ilo": "iLO",
        "netapp": "ONTAP",
        "cisco_switch": "Cisco",
    }
    items: list[dict[str, Any]] = []
    blockers = 0
    ready = 0
    total = 0
    next_blocker: dict[str, str] | None = None

    for device, label in device_meta.items():
        available = dict(latest.get(device) or {})
        current = _normalize_version(current_versions.get(device, ""))
        available_version = _normalize_version(available.get("version", ""))
        if not available_version and not current:
            continue
        total += 1
        if not available_version:
            items.append(
                {
                    "label": f"{label}: no upgrade media found",
                    "status": "Ready",
                    "tone": "ready",
                    "details": f"Current {current or 'unknown'} | Media root {media_scan.get('root')}",
                    "fix": f"Drop the approved upgrade package under {media_scan.get('root')} if you want the app to compare it.",
                }
            )
            ready += 1
            continue

        cmp = compare_versions(current, available_version) if current else None
        if not current:
            blockers += 1
            item = {
                "label": f"{label}: current version unknown",
                "status": "Blocked",
                "tone": "pending",
                "details": f"Media has {available_version} at {available.get('filename')}. Read the current device version before prebuild execution.",
                "fix": f"Capture the current {label} version, then compare it to the file under {media_scan.get('root')}.",
            }
            items.append(item)
            next_blocker = next_blocker or item
            continue
        if cmp is not None and cmp < 0:
            blockers += 1
            item = {
                "label": f"{label}: upgrade available",
                "status": "Blocked",
                "tone": "pending",
                "details": f"Current {current} | Media {available_version} | {available.get('filename')}",
                "fix": f"Upgrade {label} before configuration if {available_version} is the approved baseline.",
            }
            items.append(item)
            next_blocker = next_blocker or item
            continue
        ready += 1
        items.append(
            {
                "label": f"{label}: current is current enough",
                "status": "Ready",
                "tone": "ready",
                "details": f"Current {current} | Media {available_version} | {available.get('filename')}",
                "fix": "No upgrade is required before configuration based on the discovered media.",
            }
        )

    tone = "pending" if blockers else "ready"
    label = "Needs attention" if blockers else "Ready"
    return {
        "key": "upgrade_helper",
        "name": "Upgrade Helper",
        "label": label,
        "tone": tone,
        "target": str(media_scan.get("root") or "/media"),
        "href": "/configuration",
        "checks_ready": ready,
        "total_checks": total,
        "blockers": blockers,
        "state_label": label,
        "next_blocker": next_blocker,
        "items": items,
        "media_scan": media_scan,
        "current_versions": current_versions,
    }


def build_upgrade_helper_context(
    media_scan: dict[str, Any],
    current_versions: dict[str, str],
    current_sources: dict[str, str] | None = None,
    device_details: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    summary = build_upgrade_helper_summary(media_scan, current_versions)
    latest = dict(media_scan.get("latest") or {})
    current_sources = dict(current_sources or {})
    device_details = dict(device_details or {})
    device_meta = {
        "ilo": "iLO",
        "netapp": "ONTAP",
        "cisco_switch": "Cisco",
    }
    devices: list[dict[str, Any]] = []
    for key, label in device_meta.items():
        current_raw = str(current_versions.get(key) or "").strip()
        current = _normalize_version(current_raw)
        available = select_upgrade_candidate(media_scan, key, dict(device_details.get(key) or {})) if latest.get(key) or media_scan.get("candidates") else {}
        available_version = _normalize_version(available.get("version", ""))
        cmp = compare_versions(current, available_version) if current and available_version else None
        if not current and not available_version:
            continue
        if not available_version:
            status = "no_media"
            tone = "ready"
        elif not current:
            status = "current_unknown"
            tone = "pending"
        elif cmp is not None and cmp < 0:
            status = "upgrade_available"
            tone = "pending"
        else:
            status = "current_enough"
            tone = "ready"
        devices.append(
            {
                "key": key,
                "label": label,
                "current_version": current,
                "current_raw": current_raw,
                "current_source": str(current_sources.get(key) or "").strip(),
                "media_version": available_version,
                "media_filename": str(available.get("filename") or "").strip(),
                "media_path": str(available.get("path") or "").strip(),
                "status": status,
                "tone": tone,
            }
        )
    summary["devices"] = devices
    summary["current_sources"] = current_sources
    summary["planner"] = build_upgrade_planner(media_scan, current_versions, current_sources)
    return summary


def build_upgrade_inventory(cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    inventory = dict((cfg.get("upgrade_inventory") or {}))
    output: dict[str, dict[str, str]] = {}
    for key in ("ilo", "netapp", "cisco_switch"):
        item = dict(inventory.get(key) or {})
        if key == "netapp" and not item.get("current_version"):
            item["current_version"] = str((((cfg.get("netapp") or {}).get("last_discovered_ontap_version")) or "").strip())
            if item["current_version"] and not item.get("source"):
                item["source"] = "Last NetApp discovery"
        if key == "netapp" and not item.get("current_version"):
            item["current_version"] = str((((((cfg.get("netapp") or {}).get("upgrade") or {}).get("last_plan")) or {}).get("current_version")) or "").strip()
            if item["current_version"] and not item.get("source"):
                item["source"] = "Last ONTAP upgrade plan"
        if key == "netapp" and not item.get("current_version"):
            item["current_version"] = str((((((cfg.get("netapp") or {}).get("upgrade") or {}).get("last_result")) or {}).get("previous_version")) or "").strip()
            if item["current_version"] and not item.get("source"):
                item["source"] = "Last ONTAP upgrade result"
        if key == "cisco_switch" and not item.get("current_version"):
            item["current_version"] = str((((cfg.get("cisco_switch") or {}).get("last_discovered_version")) or "").strip())
            if item["current_version"] and not item.get("source"):
                item["source"] = "Last Cisco discovery"
        if key == "cisco_switch":
            if not item.get("model"):
                item["model"] = str((((cfg.get("cisco_switch") or {}).get("last_discovered_model")) or "").strip())
            if not item.get("platform"):
                item["platform"] = str((((cfg.get("cisco_switch") or {}).get("last_discovered_platform")) or "").strip())
            if not item.get("hostname"):
                item["hostname"] = str((((cfg.get("cisco_switch") or {}).get("last_discovered_hostname")) or "").strip())
        output[key] = {
            "current_version": str(item.get("current_version") or "").strip(),
            "raw_version": str(item.get("raw_version") or "").strip(),
            "source": str(item.get("source") or "").strip(),
            "last_checked_at": str(item.get("last_checked_at") or "").strip(),
            "manager_model": str(item.get("manager_model") or "").strip(),
            "model": str(item.get("model") or "").strip(),
            "platform": str(item.get("platform") or "").strip(),
            "hostname": str(item.get("hostname") or "").strip(),
        }
    return output


def record_upgrade_inventory(
    cfg: dict[str, Any],
    key: str,
    *,
    current_version: str,
    source: str,
    raw_version: str = "",
    checked_at: str = "",
    manager_model: str = "",
    model: str = "",
    platform: str = "",
    hostname: str = "",
) -> None:
    if key not in {"ilo", "netapp", "cisco_switch"}:
        return
    cfg.setdefault("upgrade_inventory", {})
    item = dict((cfg["upgrade_inventory"].get(key) or {}))
    item.update(
        {
            "current_version": _normalize_version(current_version) or str(current_version or "").strip(),
            "raw_version": str(raw_version or current_version or "").strip(),
            "source": str(source or "").strip(),
            "last_checked_at": str(checked_at or datetime.now(timezone.utc).isoformat()).strip(),
            "manager_model": str(manager_model or "").strip(),
            "model": str(model or "").strip(),
            "platform": str(platform or "").strip(),
            "hostname": str(hostname or "").strip(),
        }
    )
    cfg["upgrade_inventory"][key] = item
