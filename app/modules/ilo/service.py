from __future__ import annotations

from typing import Any


class IloModuleService:
    def __init__(self, deps: dict[str, Any] | None = None) -> None:
        self.deps = deps or {}

    def discover(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "ilo", "action": "discover", "ok": True}

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "ilo", "action": "plan", "ok": True}

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "ilo", "action": "validate", "ok": True}

    def preview(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "ilo", "action": "preview", "ok": True}

    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        return {"module": "ilo", "action": "apply", "ok": True}

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "ilo", "action": "status", "ok": True}

    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]:
        return {"module": "ilo", "action": "repair", "issue_id": issue_id, "ok": True}

    def update_saved_ilo_settings(self, cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        normalize_ilo_hostname = self.deps["normalize_ilo_hostname"]
        extract_ilo_additional_users_from_form = self.deps["extract_ilo_additional_users_from_form"]
        normalize_ilo_policy = self.deps["normalize_ilo_policy"]
        form = payload["form"]

        ilo_current_ip = str(payload.get("ilo_current_ip") or "")
        ilo_target_ip = str(payload.get("ilo_target_ip") or "")
        ilo_gateway = str(payload.get("ilo_gateway") or "")
        ilo_hostname = str(payload.get("ilo_hostname") or "")
        ilo_username = str(payload.get("ilo_username") or "")
        ilo_password = str(payload.get("ilo_password") or "")
        cfg["ilo"]["current_ip"] = ilo_current_ip.strip()
        cfg["ilo"]["host"] = cfg["ilo"]["current_ip"]
        if ilo_target_ip.strip():
            cfg["ilo"]["target_ip"] = ilo_target_ip.strip()
            cfg["ip_plan"]["ilo"] = ilo_target_ip.strip()
        cfg["ilo"]["gateway"] = (ilo_gateway.strip() or cfg.get("ip_plan", {}).get("gateway", "") or "").strip()
        normalized_hostname = normalize_ilo_hostname(ilo_hostname)
        cfg["ilo"]["hostname"] = normalized_hostname
        cfg["ilo"]["username"] = ilo_username
        cfg["ilo"]["password"] = ilo_password
        cfg["ilo"]["additional_users"] = extract_ilo_additional_users_from_form(form)
        existing_policy = normalize_ilo_policy((cfg.get("ilo") or {}).get("policy"))
        existing_policy.update(dict(payload.get("policy_updates") or {}))
        cfg["ilo"]["policy"] = normalize_ilo_policy(existing_policy)
        cfg.setdefault("shared_snmp", {})["read_community"] = str(payload.get("ilo_policy_snmp_read_community") or "")
        cfg["included"]["ilo"] = True
        return {"cfg": cfg, "normalized_hostname": normalized_hostname}

    def discover_ilo_hosts(self, cfg: dict[str, Any]) -> dict[str, Any]:
        normalize_ilo_policy = self.deps["normalize_ilo_policy"]
        build_ilo_discovery_targets = self.deps["build_ilo_discovery_targets"]
        probe_tcp_port = self.deps["probe_tcp_port"]
        policy = normalize_ilo_policy((cfg.get("ilo") or {}).get("policy"))
        targets = build_ilo_discovery_targets(cfg)
        results = [probe_tcp_port(target, 443, timeout_seconds=0.75) for target in targets]
        policy["discovered_hosts"] = results
        cfg["ilo"]["policy"] = normalize_ilo_policy(policy)
        reachable = [item for item in results if item.get("reachable")]
        current_ip = str((cfg.get("ilo") or {}).get("current_ip") or "").strip()
        if len(reachable) == 1 and (
            not current_ip
            or current_ip == str((cfg.get("ip_plan") or {}).get("ilo") or "").strip()
            or current_ip not in {item.get("host") for item in reachable}
        ):
            cfg["ilo"]["current_ip"] = str(reachable[0].get("host") or "")
            cfg["ilo"]["host"] = cfg["ilo"]["current_ip"]
        return {"cfg": cfg, "policy": policy, "results": results, "reachable": reachable}


def default_ilo_module_service(deps: dict[str, Any]) -> IloModuleService:
    return IloModuleService(deps)
