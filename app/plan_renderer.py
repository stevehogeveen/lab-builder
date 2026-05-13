from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


TOKEN_KEYS = [
    "KITID",
    "SUBNET",
    "SUBNET_MASK",
    "GATEWAY",
    "SVM_NAME",
    "NODE_01",
    "NODE_02",
    "CLUSTER_MGMT_IP",
    "SVM_MGMT_IP",
]


def render_command_preview(template: str, tokens: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for raw_line in str(template or "").splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        rendered = line
        for key in TOKEN_KEYS:
            rendered = rendered.replace(f"<<{key}>>", str(tokens.get(key, "")))
        lines.append(rendered)
    return lines


def build_token_map(cfg: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    site_name = str(((cfg.get("site") or {}).get("name") or "Kit-01")).strip()
    ip_plan = cfg.get("ip_plan") or {}
    base = profile.get("base") or {}
    return {
        "KITID": site_name,
        "SUBNET": str((ip_plan.get("subnet") or "10.10.8.0/24")).split("/")[0].rsplit(".", 1)[0],
        "SUBNET_MASK": str(ip_plan.get("netmask") or "255.255.255.0"),
        "GATEWAY": str(ip_plan.get("gateway") or ""),
        "SVM_NAME": str(base.get("svm_name") or ""),
        "NODE_01": str(base.get("node_01") or ""),
        "NODE_02": str(base.get("node_02") or ""),
        "CLUSTER_MGMT_IP": str(((cfg.get("netapp") or {}).get("management") or {}).get("cluster_mgmt_ip") or ip_plan.get("cluster_mgmt_ip") or ""),
        "SVM_MGMT_IP": str(((cfg.get("netapp") or {}).get("management") or {}).get("svm_mgmt_ip") or ip_plan.get("svm_mgmt_ip") or ""),
    }


def write_plan_artifacts(base_dir: Path, prefix: str, payload: dict[str, Any]) -> dict[str, str]:
    base_dir.mkdir(parents=True, exist_ok=True)
    json_path = base_dir / f"{prefix}.json"
    yaml_path = base_dir / f"{prefix}.yml"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    yaml_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return {"json": str(json_path), "yaml": str(yaml_path)}
