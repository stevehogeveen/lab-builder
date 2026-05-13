from __future__ import annotations

import re
import shutil
import socket
import subprocess
from typing import Any

from app.netapp import NetAppClient, NetAppConfig, NetAppError
from app.core.config import ip_at_offset, subnet_details
from app.storage_profiles import build_naming, build_protocol_profile, validate_protocol_networks
from app.vmware import build_vmware_plan


class NetAppModuleService:
    module = "netapp"

    def _netapp_cfg(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = (context.get("cfg") or {}).get("netapp") or {}
        return cfg if isinstance(cfg, dict) else {}

    def _desired_cfg(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = self._netapp_cfg(context)
        desired = cfg.get("desired") or {}
        return desired if isinstance(desired, dict) else {}

    def _explicit_svm_name(self, context: dict[str, Any]) -> str:
        netapp_cfg = self._netapp_cfg(context)
        desired_cfg = self._desired_cfg(context)
        return str(netapp_cfg.get("svm_name") or desired_cfg.get("svm_name") or "").strip()

    def _discovered_svm_names(self, discovery: dict[str, Any]) -> list[str]:
        names: list[str] = []
        raw_svms = list(((discovery.get("raw") or {}).get("svms") or []))
        if raw_svms:
            for item in raw_svms:
                name = str((item or {}).get("name") or "").strip()
                if name:
                    names.append(name)
        else:
            for item in list(discovery.get("svms") or []):
                name = str(item or "").strip()
                if name:
                    names.append(name)
        deduped: list[str] = []
        seen: set[str] = set()
        for name in names:
            lowered = name.lower()
            if lowered in {"cluster", "admin"}:
                continue
            if name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return deduped

    def _resolved_svm_name(self, context: dict[str, Any], discovery: dict[str, Any]) -> str:
        explicit = self._explicit_svm_name(context)
        if explicit:
            return explicit
        discovered = self._discovered_svm_names(discovery)
        if len(discovered) == 1:
            return discovered[0]
        return ""

    def _resolved_aggregate_targets(self, context: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
        desired = self._profile_defaults(context)
        raw_aggregates = [item for item in list(((discovery.get("raw") or {}).get("aggregates") or [])) if isinstance(item, dict)]
        detected_names = [str(item.get("name") or "").strip() for item in raw_aggregates if str(item.get("name") or "").strip()]
        explicit_01 = str(desired.get("aggregate_node_01") or "").strip()
        explicit_02 = str(desired.get("aggregate_node_02") or "").strip()
        resolved_01 = explicit_01
        resolved_02 = explicit_02
        adopted = False

        by_node: dict[str, list[str]] = {}
        for item in raw_aggregates:
            name = str(item.get("name") or "").strip()
            node_name = str(((item.get("node") or {}).get("name") or "")).strip()
            state = str(item.get("state") or "").strip().lower()
            if not name or not node_name:
                continue
            if state and state != "online":
                continue
            by_node.setdefault(node_name, []).append(name)

        discovered_nodes = [str(item).strip() for item in list(discovery.get("node_names") or discovery.get("nodes") or []) if str(item).strip()]
        if len(discovered_nodes) >= 2:
            node_01 = discovered_nodes[0]
            node_02 = discovered_nodes[1]
            if explicit_01 not in detected_names:
                options = by_node.get(node_01) or []
                if len(options) == 1:
                    resolved_01 = options[0]
                    adopted = True
            if explicit_02 not in detected_names:
                options = by_node.get(node_02) or []
                if len(options) == 1:
                    resolved_02 = options[0]
                    adopted = True

        return {
            "desired": [name for name in [explicit_01, explicit_02] if name],
            "resolved_01": resolved_01,
            "resolved_02": resolved_02,
            "resolved": [name for name in [resolved_01, resolved_02] if name],
            "detected": detected_names,
            "adopted": adopted,
        }

    def _resolved_data_broadcast_domain(self, context: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
        desired = self._profile_defaults(context)
        requested = str(desired.get("data_broadcast_domain") or "").strip()
        raw_domains = [item for item in list(((discovery.get("raw") or {}).get("broadcast_domains") or [])) if isinstance(item, dict)]
        discovered_names = [str(item.get("name") or "").strip() for item in raw_domains if str(item.get("name") or "").strip()]
        if requested and requested in discovered_names:
            return {"requested": requested, "resolved": requested, "detected": discovered_names, "adopted": False}

        protocol = str(desired.get("storage_protocol") or "nfs").strip().lower()
        lifs = list(discovery.get("discovered_nfs_lifs") or []) if protocol == "nfs" else list(discovery.get("discovered_iscsi_lifs") or [])
        desired_ports = {
            f"{str((lif or {}).get('home_node') or '').strip()}:{str((lif or {}).get('home_port') or '').strip()}"
            for lif in lifs
            if str((lif or {}).get("home_node") or "").strip() and str((lif or {}).get("home_port") or "").strip()
        }
        for domain in raw_domains:
            ports = {
                f"{str(((item.get('node') or {}).get('name') or '')).strip()}:{str(item.get('name') or '').strip()}"
                for item in list(domain.get("ports") or [])
                if str(item.get("name") or "").strip()
            }
            if desired_ports and desired_ports.issubset(ports):
                resolved = str(domain.get("name") or "").strip()
                return {"requested": requested, "resolved": resolved, "detected": discovered_names, "adopted": True}
        return {"requested": requested, "resolved": requested, "detected": discovered_names, "adopted": False}

    def _profile_defaults(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = self._netapp_cfg(context)
        desired = self._desired_cfg(context)
        app_cfg = context.get("cfg") or {}
        kit_name = str(((app_cfg.get("site") or {}).get("name") or "KIT-01")).strip()
        subnet = str(((app_cfg.get("shared_network") or {}).get("subnet") or "10.10.10.0/24")).strip()
        details = subnet_details(subnet)
        subnet_prefix = subnet.split("/")[0].rsplit(".", 1)[0] if "." in subnet else "10.10.10"
        gateway = str(((app_cfg.get("ip_plan") or {}).get("gateway") or f"{subnet_prefix}.1")).strip()
        merged: dict[str, Any] = {
            "storage_protocol": str(cfg.get("storage_protocol") or "nfs").strip().lower() or "nfs",
            "cluster_name": "",
            "svm_name": f"{kit_name}-SVM",
            "required_nodes": [f"{kit_name}-01", f"{kit_name}-02"],
            "expected_ports": ["a0a", "e0M"],
            "data_broadcast_domain": "Data",
            "target_mtu": 9000,
            "baseline": {
                "target_ontap_version": "9.12.1",
                "minimum_ontap_version": "9.9.1",
                "upgrade_enforcement": "required",
            },
            "aggregate_node_01": "aggr_01",
            "aggregate_node_02": "aggr_02",
            "aggregate_diskcount": 11,
            "aggregate_raidtype": "raid_dp",
            "svm_mgmt_lif": f"{kit_name}-SVM_admin1",
            "svm_mgmt_ip": ip_at_offset(subnet, 48),
            "management_subnet": details["subnet"],
            "management_gateway": gateway,
            "management_netmask": details["netmask"],
            "autosupport_enabled": True,
            "autosupport_from": f"{kit_name}-NetApp@forces.gc.ca",
            "autosupport_to": f"{kit_name}.Alert.Reporting@",
            "autosupport_mail_hosts": [f"{subnet_prefix}.63"],
            "ntp_servers": [gateway],
            "required_users": ["Power", f"{kit_name}_Tech"],
            "esxi_hosts": [ip_at_offset(subnet, offset) for offset in (31, 32, 33)],
            "iscsi": {
                "subnet": "192.168.1.0/24",
                "subnet_cidr": "192.168.1.0/24",
                "gateway": "192.168.1.1",
                "ip_range": "192.168.1.11-192.168.1.60",
                "lifs": [],
                "portset": "iSCSI",
                "igroup": f"{kit_name}_ESXi_Servers",
                "lun": "esxi_lun01",
                "vmfs_datastore": "vmfs_ds01",
                "iqns": [],
            },
            "nfs": {
                "lifs": [],
                "volume": "esxi_datastore_01",
                "export_policy": "esxi_nfs_policy",
                "mount_path": "/esxi_datastore_01",
                "esxi_mount_targets": [],
            },
        }
        merged.update(desired if isinstance(desired, dict) else {})
        if not isinstance(merged.get("iscsi"), dict):
            merged["iscsi"] = {}
        if not isinstance(merged.get("nfs"), dict):
            merged["nfs"] = {}
        if not isinstance(merged.get("baseline"), dict):
            merged["baseline"] = {
                "target_ontap_version": "9.12.1",
                "minimum_ontap_version": "9.9.1",
                "upgrade_enforcement": "required",
            }
        return merged

    def _bootstrap_context(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = context.get("cfg") or {}
        netapp_cfg = self._netapp_cfg(context)
        plan = (cfg.get("ip_plan") or {})
        shared = (cfg.get("shared_network") or {})
        kit_name = str(((cfg.get("site") or {}).get("name") or "KIT-01")).strip()
        password = str(netapp_cfg.get("password") or "")
        return {
            "kit_id": kit_name,
            "subnet": str(plan.get("subnet") or shared.get("subnet") or "").strip(),
            "gateway": str(plan.get("gateway") or "").strip(),
            "netmask": str(plan.get("netmask") or "").strip(),
            "cluster_name": kit_name,
            "controller_a_name": f"{kit_name}-01",
            "controller_b_name": f"{kit_name}-02",
            "sp_port_name": "SP/BMC",
            "node_mgmt_port_name": "e0M",
            "cluster_mgmt_port_name": "e0M",
            "svm_mgmt_port_name": "e0M",
            "sp_a_ip": str(plan.get("netapp_sp_a") or "").strip(),
            "sp_b_ip": str(plan.get("netapp_sp_b") or "").strip(),
            "cluster_mgmt_ip": str(plan.get("netapp_cluster_mgmt") or "").strip(),
            "node_01_mgmt_ip": str(plan.get("netapp_node_01_mgmt") or "").strip(),
            "node_02_mgmt_ip": str(plan.get("netapp_node_02_mgmt") or "").strip(),
            "svm_mgmt_ip": str(plan.get("netapp_svm_mgmt") or "").strip(),
            "admin_password_masked": ("*" * 8) if password else "",
            "bootstrap_complete": bool(netapp_cfg.get("bootstrap_complete")),
            "bootstrap_checks": dict(netapp_cfg.get("bootstrap_checks") or {}),
        }

    def _legacy_convention_warning(self, context: dict[str, Any]) -> str:
        cfg = context.get("cfg") or {}
        netapp_cfg = self._netapp_cfg(context)
        plan = cfg.get("ip_plan") or {}
        legacy_values = {
            "cluster_mgmt": str(plan.get("subnet") or "").split("/")[0].rsplit(".", 1)[0] + ".40" if "." in str(plan.get("subnet") or "") else "",
            "node_01": str(plan.get("subnet") or "").split("/")[0].rsplit(".", 1)[0] + ".41" if "." in str(plan.get("subnet") or "") else "",
            "node_02": str(plan.get("subnet") or "").split("/")[0].rsplit(".", 1)[0] + ".42" if "." in str(plan.get("subnet") or "") else "",
            "svm": str(plan.get("subnet") or "").split("/")[0].rsplit(".", 1)[0] + ".43" if "." in str(plan.get("subnet") or "") else "",
        }
        observed = {
            "cluster_mgmt": str(((netapp_cfg.get("bootstrap_overrides") or {}).get("netapp_cluster_mgmt") or (netapp_cfg.get("management") or {}).get("cluster_mgmt_ip") or netapp_cfg.get("host") or "").strip()),
            "node_01": str(((netapp_cfg.get("bootstrap_overrides") or {}).get("netapp_node_01_mgmt") or (netapp_cfg.get("management") or {}).get("node_01_mgmt_ip") or "").strip()),
            "node_02": str(((netapp_cfg.get("bootstrap_overrides") or {}).get("netapp_node_02_mgmt") or (netapp_cfg.get("management") or {}).get("node_02_mgmt_ip") or "").strip()),
            "svm": str(((netapp_cfg.get("bootstrap_overrides") or {}).get("netapp_svm_mgmt") or (netapp_cfg.get("management") or {}).get("svm_mgmt_ip") or (self._desired_cfg(context).get("svm_mgmt_ip") or "")).strip()),
        }
        if any(observed.get(key) and observed.get(key) == legacy_values.get(key) for key in observed):
            return "This kit still uses the old NetApp management convention (.40/.41/.42/.43). Update to current NetApp convention."
        return ""

    def _bootstrap_validation(self, context: dict[str, Any]) -> list[str]:
        cfg = context.get("cfg") or {}
        plan = (cfg.get("ip_plan") or {})
        subnet = str(plan.get("subnet") or "").strip()
        labels = {
            "netapp_sp_a": "NetApp SP A",
            "netapp_sp_b": "NetApp SP B",
            "netapp_cluster_mgmt": "NetApp cluster management",
            "netapp_node_01_mgmt": "NetApp node 1 management",
            "netapp_node_02_mgmt": "NetApp node 2 management",
            "netapp_svm_mgmt": "NetApp SVM management",
        }
        warnings: list[str] = []
        values: dict[str, str] = {}
        owners: dict[str, list[str]] = {}
        for key, label in labels.items():
            value = str(plan.get(key) or "").strip()
            if not value:
                continue
            values[key] = value
            owners.setdefault(value, []).append(label)
            try:
                self._parse_ontap_version("9.1.1")
                # no-op; keep local helper-free validation path small
                socket.inet_aton(value)
            except OSError:
                warnings.append(f"{label} IP is not a valid IPv4 address.")
            try:
                from app.core.config import validate_ip_for_subnet
                validate_ip_for_subnet(subnet, value, label)
            except Exception as exc:
                warnings.append(str(exc))
        for ip, claimed in owners.items():
            if len(claimed) > 1:
                warnings.append(f"Bootstrap IP conflict: {ip} is assigned to {', '.join(claimed)}.")
        device_keys = {"gateway", "switch", "esxi", "ilo", "windows", "qnap", "iosafe", "netapp"}
        for key in ("netapp_sp_a", "netapp_sp_b", "netapp_cluster_mgmt", "netapp_node_01_mgmt", "netapp_node_02_mgmt", "netapp_svm_mgmt"):
            value = str(plan.get(key) or "").strip()
            if not value:
                continue
            conflicts = [name for name in device_keys if str(plan.get(name) or "").strip() == value]
            if key == "netapp_cluster_mgmt":
                conflicts = [name for name in conflicts if name != "netapp"]
            if conflicts:
                warnings.append(f"{labels[key]} IP {value} conflicts with {', '.join(conflicts)}.")
        legacy_warning = self._legacy_convention_warning(context)
        if legacy_warning:
            warnings.append(legacy_warning)
        return list(dict.fromkeys(warnings))

    def _build_bootstrap_checklist(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        info = self._bootstrap_context(context)
        return [
            {"title": "Controller A", "items": [
                f"{info['controller_a_name']} serial cable.",
                "PuTTY serial at 115200.",
                "Power on, press Ctrl-C at AUTOBOOT.",
                "`sp setup`",
                f"{info['controller_a_name']} {info['sp_port_name']} | {info['sp_a_ip']}",
                f"DHCP no | Netmask {info['netmask']} | Gateway {info['gateway']} | IPv6 no",
                f"`bye` then power off {info['controller_a_name']}.",
            ]},
            {"title": "Controller B", "items": [
                f"Move serial cable to {info['controller_b_name']}.",
                "Power on, press Ctrl-C at AUTOBOOT.",
                "`sp setup`",
                f"{info['controller_b_name']} {info['sp_port_name']} | {info['sp_b_ip']}",
                f"DHCP no | Netmask {info['netmask']} | Gateway {info['gateway']} | IPv6 no",
                "`bye`",
            ]},
            {"title": "Create cluster from Controller A", "items": [
                f"Power on {info['controller_a_name']}, SSH to {info['sp_a_ip']}.",
                "`system console`",
                "`system image show`",
                "`cluster setup`",
                f"{info['controller_a_name']} {info['node_mgmt_port_name']} | {info['node_01_mgmt_ip']}",
                f"Netmask {info['netmask']} | Gateway {info['gateway']}",
                f"Create cluster `{info['cluster_name']}` | Cluster {info['cluster_mgmt_port_name']} | {info['cluster_mgmt_ip']}",
            ]},
            {"title": "Join Controller B", "items": [
                f"SSH to {info['sp_b_ip']}.",
                "`system console` then `cluster setup`",
                f"{info['controller_b_name']} {info['node_mgmt_port_name']} | {info['node_02_mgmt_ip']}",
                f"Netmask {info['netmask']} | Gateway {info['gateway']}",
                f"Join existing cluster `{info['cluster_name']}`",
                f"If asked for private cluster IP: run `net int show` on Controller A and use `{info['cluster_name']}-01_clus1`.",
            ]},
            {"title": "Finish", "items": [
                "Apply licenses manually if required.",
                f"Confirm https://{info['cluster_mgmt_ip']} responds.",
                "Return here and click Read current NetApp.",
            ]},
        ]

    def _probe_host(self, host: str, ports: list[int] | None = None, timeout: float = 1.5) -> dict[str, Any]:
        result = {"host": host, "reachable": False, "ports": {}, "error": ""}
        if not host:
            result["error"] = "Host not set."
            return result
        for port in ports or [22, 80, 443]:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    result["ports"][str(port)] = "open"
                    result["reachable"] = True
            except OSError:
                result["ports"][str(port)] = "closed"
        if not result["reachable"]:
            result["error"] = "No tested TCP ports responded."
        return result

    def _parse_ontap_version(self, value: str) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(value or ""))
        if not match:
            return None
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def _compare_versions(self, left: str, right: str) -> int | None:
        left_parsed = self._parse_ontap_version(left)
        right_parsed = self._parse_ontap_version(right)
        if not left_parsed or not right_parsed:
            return None
        if left_parsed < right_parsed:
            return -1
        if left_parsed > right_parsed:
            return 1
        return 0

    def _build_upgrade_posture(self, desired: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
        baseline = desired.get("baseline") or {}
        current_version = str(discovery.get("ontap_version") or "").strip()
        target_version = str(baseline.get("target_ontap_version") or "9.12.1").strip()
        minimum_version = str(baseline.get("minimum_ontap_version") or target_version).strip()
        enforcement = str(baseline.get("upgrade_enforcement") or "required").strip().lower()
        target_cmp = self._compare_versions(current_version, target_version)
        minimum_cmp = self._compare_versions(current_version, minimum_version)
        if not current_version:
            status = "unknown"
        elif target_cmp is None:
            status = "unknown"
        elif target_cmp >= 0:
            status = "meets_baseline"
        elif minimum_cmp is not None and minimum_cmp < 0:
            status = "upgrade_required"
        elif enforcement == "required":
            status = "upgrade_required"
        else:
            status = "upgrade_recommended"
        return {
            "current_version": current_version,
            "target_version": target_version,
            "minimum_version": minimum_version,
            "enforcement": enforcement,
            "status": status,
            "meets_baseline": status == "meets_baseline",
        }

    def _build_capability_matrix(self, desired: dict[str, Any], discovery: dict[str, Any]) -> list[dict[str, Any]]:
        discovered = dict(discovery.get("capabilities") or {})
        statuses = dict(discovery.get("capability_status") or {})
        baseline = desired.get("baseline") or {}
        baseline_version = str(baseline.get("target_ontap_version") or "9.12.1").strip()
        labels = {
            "cluster": "Cluster identity",
            "nodes": "Node inventory",
            "ports": "Port inventory",
            "broadcast_domains": "Broadcast domains",
            "aggregates": "Aggregates",
            "svms": "SVMs",
            "subnets": "Cluster subnet objects",
            "licenses": "Licenses",
            "protocol_services": "NFS/iSCSI services",
            "export_policies": "NFS export policies",
            "igroups": "SAN initiator groups",
            "portsets": "SAN portsets",
            "luns": "LUN inventory",
            "lun_maps": "LUN mappings",
            "disk_inventory": "Disk inventory",
            "autosupport": "AutoSupport",
            "ntp_servers": "NTP servers",
            "users": "Users and roles",
            "volumes": "Volume inventory",
            "network_interfaces": "Network interfaces",
        }
        matrix: list[dict[str, Any]] = []
        for key, label in labels.items():
            current_supported = bool(discovered.get(key))
            status = str(statuses.get(key) or ("native" if current_supported else "missing"))
            baseline_expected = True
            matrix.append(
                {
                    "key": key,
                    "label": label,
                    "current_supported": current_supported,
                    "status": status,
                    "used_fallback": status == "fallback",
                    "baseline_expected": baseline_expected,
                    "gap": baseline_expected and not current_supported,
                    "baseline_version": baseline_version,
                }
            )
        return matrix

    def _template_values(self, context: dict[str, Any], desired: dict[str, Any]) -> dict[str, str]:
        cfg = context.get("cfg") or {}
        kit_name = str(((cfg.get("site") or {}).get("name") or "KIT-01")).strip()
        subnet = str(((cfg.get("shared_network") or {}).get("subnet") or "")).strip()
        subnet_prefix = subnet.split("/")[0].rsplit(".", 1)[0] if "." in subnet else ""
        mask = str(desired.get("management_netmask") or "255.255.255.0").strip()
        svm_name = str(desired.get("svm_name") or f"{kit_name}-SVM").strip()
        return {
            "KITID": kit_name,
            "SUBNET": subnet_prefix,
            "SUBNET_MASK": mask,
            "SVM_NAME": svm_name,
            "DATA_BROADCAST_DOMAIN": str(desired.get("data_broadcast_domain") or "Data").strip(),
            "MGMT_GATEWAY": str(desired.get("management_gateway") or "").strip(),
            "MGMT_SUBNET": str(desired.get("management_subnet") or "").strip(),
            "SVM_MGMT_IP": str(desired.get("svm_mgmt_ip") or "").strip(),
            "AUTOSUPPORT_FROM": str(desired.get("autosupport_from") or "").strip(),
            "AUTOSUPPORT_TO": str(desired.get("autosupport_to") or "").strip(),
            "AUTOSUPPORT_MAIL_HOSTS": ",".join(str(item).strip() for item in list(desired.get("autosupport_mail_hosts") or []) if str(item).strip()),
        }

    def _render_command_template(self, template: str, values: dict[str, str]) -> list[str]:
        lines: list[str] = []
        for raw_line in str(template or "").splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            if line.lstrip().startswith("#"):
                continue
            rendered = line
            for key, value in values.items():
                rendered = rendered.replace(f"<<{key}>>", value)
            lines.append(rendered)
        return lines

    def _default_iscsi_template(self) -> str:
        return "\n".join(
            [
                "storage aggregate create -aggregate aggr_01 -node <<KITID>>-01 -raidtype raid_dp -diskcount 11 -simulate true",
                "storage aggregate create -aggregate aggr_01 -node <<KITID>>-01 -raidtype raid_dp -diskcount 11",
                "storage aggregate create -aggregate aggr_02 -node <<KITID>>-02 -raidtype raid_dp -diskcount 11 -simulate true",
                "storage aggregate create -aggregate aggr_02 -node <<KITID>>-02 -raidtype raid_dp -diskcount 11",
                "broadcast-domain create -broadcast-domain Data -mtu 9000 -ports <<KITID>>-01:a0a,<<KITID>>-02:a0a -ipspace Default",
                "subnet create -subnet-name Management -broadcast-domain Default -subnet <<MGMT_SUBNET>> -gateway <<MGMT_GATEWAY>>",
                "subnet create -subnet-name iSCSI -broadcast-domain Data -subnet 192.168.1.0/24 -force-update-lif-associations true -gateway 192.168.1.1 -ip-ranges 192.168.1.11-192.168.1.60",
                "vserver create -vserver <<SVM_NAME>> -subtype default -rootvolume <<KITID>>_SVM_root -rootvolume-security-style unix -aggregate aggr_01",
                "vserver modify -vserver <<SVM_NAME>> -allowed-protocols iscsi",
                "net int create -vserver <<SVM_NAME>> -lif <<KITID>>-01_iscsi_lif_1 -service-policy default-data-blocks -data-protocol iscsi -address 192.168.1.51 -netmask 255.255.255.0 -home-node <<KITID>>-01 -home-port a0a -force-subnet-association true -status-admin up -broadcast-domain Data",
                "net int create -vserver <<SVM_NAME>> -lif <<KITID>>-01_iscsi_lif_2 -service-policy default-data-blocks -data-protocol iscsi -address 192.168.1.52 -netmask 255.255.255.0 -home-node <<KITID>>-01 -home-port a0a -force-subnet-association true -status-admin up -broadcast-domain Data",
                "net int create -vserver <<SVM_NAME>> -lif <<KITID>>-02_iscsi_lif_1 -service-policy default-data-blocks -data-protocol iscsi -address 192.168.1.53 -netmask 255.255.255.0 -home-node <<KITID>>-02 -home-port a0a -force-subnet-association true -status-admin up -broadcast-domain Data",
                "net int create -vserver <<SVM_NAME>> -lif <<KITID>>-02_iscsi_lif_2 -service-policy default-data-blocks -data-protocol iscsi -address 192.168.1.54 -netmask 255.255.255.0 -home-node <<KITID>>-02 -home-port a0a -force-subnet-association true -status-admin up -broadcast-domain Data",
                "net int create -vserver <<SVM_NAME>> -lif <<KITID>>-SVM_admin1 -firewall-policy mgmt -data-protocol none -home-node <<KITID>>-01 -home-port e0M -address <<SVM_MGMT_IP>> -netmask <<SUBNET_MASK>>",
                "iscsi create -vserver <<SVM_NAME>>",
                "autosupport modify -node * -state enable -from <<AUTOSUPPORT_FROM>> -to <<AUTOSUPPORT_TO>> -mail-hosts <<AUTOSUPPORT_MAIL_HOSTS>> -transport smtp -support disable",
                "portset create -portset iSCSI -protocol iscsi -vserver <<SVM_NAME>>",
            ]
        )

    def _default_nfs_template(self) -> str:
        return "\n".join(
            [
                "broadcast-domain create -broadcast-domain Data -mtu 9000 -ports <<KITID>>-01:a0a,<<KITID>>-02:a0a -ipspace Default",
                "vserver create -vserver <<SVM_NAME>> -subtype default -rootvolume <<KITID>>_SVM_root -rootvolume-security-style unix -aggregate aggr_01",
                "vserver modify -vserver <<SVM_NAME>> -allowed-protocols nfs",
                "net int create -vserver <<SVM_NAME>> -lif <<KITID>>-01_nfs_lif_1 -service-policy default-data-files -data-protocol nfs -home-node <<KITID>>-01 -home-port a0a -status-admin up -broadcast-domain Data",
                "net int create -vserver <<SVM_NAME>> -lif <<KITID>>-02_nfs_lif_1 -service-policy default-data-files -data-protocol nfs -home-node <<KITID>>-02 -home-port a0a -status-admin up -broadcast-domain Data",
                "net int create -vserver <<SVM_NAME>> -lif <<KITID>>-SVM_admin1 -firewall-policy mgmt -data-protocol none -home-node <<KITID>>-01 -home-port e0M -address <<SVM_MGMT_IP>> -netmask <<SUBNET_MASK>>",
                "nfs create -vserver <<SVM_NAME>>",
                "volume create -vserver <<SVM_NAME>> -volume esxi_datastore_01 -aggregate aggr_01 -size 500GB",
                "export-policy create -vserver <<SVM_NAME>> -policyname esxi_nfs_policy",
            ]
        )

    def settings_context(self, context: dict[str, Any]) -> dict[str, Any]:
        desired = self._profile_defaults(context)
        netapp_cfg = self._netapp_cfg(context)
        templates = netapp_cfg.get("command_templates") or {}
        protocol_profile = build_protocol_profile(context.get("cfg") or {})
        return {
            "desired": desired,
            "protocol_profile": protocol_profile,
            "bootstrap": self._bootstrap_context(context),
            "bootstrap_checklist": self._build_bootstrap_checklist(context),
            "bootstrap_warnings": self._bootstrap_validation(context),
            "vmware_checks": dict(netapp_cfg.get("vmware_checks") or {}),
            "command_templates": {
                "iscsi": str(templates.get("iscsi") or self._default_iscsi_template()),
                "nfs": str(templates.get("nfs") or self._default_nfs_template()),
            },
        }

    def _build_client(self, context: dict[str, Any]) -> NetAppClient:
        netapp_cfg = self._netapp_cfg(context)
        return NetAppClient(
            NetAppConfig(
                host=str(netapp_cfg.get("host") or "").strip(),
                username=str(netapp_cfg.get("username") or "").strip(),
                password=str(netapp_cfg.get("password") or ""),
                verify_tls=bool(netapp_cfg.get("verify_tls", False)),
                timeout=int(netapp_cfg.get("timeout") or 20),
            )
        )

    def _response(self, context: dict[str, Any], action: str) -> dict[str, Any]:
        return {
            "module": self.module,
            "action": action,
            "ok": True,
            "dry_run_only": False,
            "supports_safe_apply": True,
            "bootstrap": self._bootstrap_context(context),
            "bootstrap_checklist": self._build_bootstrap_checklist(context),
            "bootstrap_warnings": self._bootstrap_validation(context),
            "context": {
                "module_name": str((context.get("module_name") or self.module) or self.module),
                "site_name": str(((context.get("cfg") or {}).get("site") or {}).get("name") or "Kit-01"),
            },
        }

    def _discover_stage(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self._response(context, "discover")
        netapp_cfg = self._netapp_cfg(context)
        bootstrap = self._bootstrap_context(context)
        if not str(netapp_cfg.get("host") or "").strip():
            payload["ok"] = False
            payload["error"] = "NetApp host is not configured."
            payload["stages"] = [{"name": "NetApp Stage 1: Discover", "ok": False, "steps": []}]
            payload["warnings"] = ["Set netapp.host before discovery."]
            payload["discovery"] = {}
            return payload
        if not bootstrap.get("bootstrap_complete"):
            cluster_probe = dict((bootstrap.get("bootstrap_checks") or {}).get("cluster_mgmt") or {})
            if not cluster_probe.get("reachable"):
                payload["ok"] = False
                payload["error"] = "NetApp bootstrap is not complete. Bring the cluster management IP online first."
                payload["stages"] = [{"name": "NetApp Stage 1: Discover", "ok": False, "steps": []}]
                payload["warnings"] = ["Run the NetApp bootstrap checklist, then test cluster management connectivity before discovery."]
                payload["discovery"] = {}
                return payload
        try:
            client = self._build_client(context)
            discovery = client.build_discovery_summary()
        except NetAppError as exc:
            payload["ok"] = False
            payload["error"] = str(exc)
            payload["stages"] = [{"name": "NetApp Stage 1: Discover", "ok": False, "steps": []}]
            payload["warnings"] = ["Discovery failed. Review host, credentials, and ONTAP reachability."]
            payload["discovery"] = {}
            return payload

        stage_steps = [
            "Connect to cluster management IP",
            "Read ONTAP version",
            "Read model",
            "Read nodes",
            "Read ports",
            "Read aggregates",
            "Read SVMs",
            "Read LIFs",
            "Read enabled protocols",
        ]
        payload["stages"] = [{"name": "NetApp Stage 1: Discover", "ok": True, "steps": stage_steps}]
        payload["warnings"] = list(discovery.get("warnings") or [])
        payload["discovery"] = discovery
        return payload

    def test_connection(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self._response(context, "test_connection")
        netapp_cfg = self._netapp_cfg(context)
        host = str(netapp_cfg.get("host") or "").strip()
        username = str(netapp_cfg.get("username") or "").strip()
        password = str(netapp_cfg.get("password") or "")
        result = {
            "target_host": host,
            "username": username,
            "password_present": bool(password),
            "api_auth_ok": False,
            "first_endpoint": "/api/cluster",
            "cluster_name": "",
        }
        if not host:
            payload["ok"] = False
            payload["error"] = "NetApp host is not configured."
            payload["warnings"] = ["Set the cluster management IP before testing the connection."]
            payload["connection_test"] = result
            return payload
        try:
            client = self._build_client(context)
            cluster = client.get_cluster()
            result["api_auth_ok"] = True
            result["cluster_name"] = str(cluster.get("name") or "")
            payload["connection_test"] = result
            payload["outcome"] = "connected"
            return payload
        except NetAppError as exc:
            payload["ok"] = False
            payload["error"] = str(exc)
            payload["warnings"] = ["Connection test failed."]
            payload["connection_test"] = result
            return payload

    def test_bootstrap_target(self, context: dict[str, Any], target: str) -> dict[str, Any]:
        payload = self._response(context, f"bootstrap_{target}")
        info = self._bootstrap_context(context)
        target_map = {
            "sp_a": ("sp_a_ip", [22, 443]),
            "sp_b": ("sp_b_ip", [22, 443]),
            "node_01_mgmt": ("node_01_mgmt_ip", [22, 443]),
            "node_02_mgmt": ("node_02_mgmt_ip", [22, 443]),
            "cluster_mgmt": ("cluster_mgmt_ip", [443, 80, 22]),
        }
        field, ports = target_map.get(target, ("", []))
        host = str(info.get(field) or "").strip()
        result = self._probe_host(host, ports)
        payload["bootstrap_test"] = {"target": target, **result}
        if not result.get("reachable"):
            payload["ok"] = False
            payload["warnings"] = [f"{target.replace('_', ' ').title()} did not respond on tested ports."]
        return payload

    def test_vmware_nfs_targets(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self.plan(context)
        vmware_plan = dict((((payload.get("plan") or {}).get("vmware_plan")) or {}))
        probe_result: dict[str, Any] = {
            "connection_mode": str(vmware_plan.get("connection_mode") or ""),
            "datastore_name": str(((vmware_plan.get("nfs_context") or {}).get("datastore_name")) or ""),
            "export_path": str(((vmware_plan.get("nfs_context") or {}).get("export_path")) or ""),
            "svm_name": str(((vmware_plan.get("nfs_context") or {}).get("svm_name")) or ""),
            "esxi_hosts": list(vmware_plan.get("esxi_hosts") or []),
            "server_ips": list(((vmware_plan.get("nfs_context") or {}).get("lif_ips")) or []),
            "checks": [],
            "ready": False,
        }
        payload["vmware_probe"] = probe_result
        if not payload.get("discovery"):
            payload["warnings"] = list(payload.get("warnings") or []) + ["Run NetApp discovery successfully before probing VMware NFS targets."]
            payload["ok"] = False
            return payload

        validate_step = next(
            (step for step in list(vmware_plan.get("steps") or []) if str(step.get("name") or "") == "validate_nfs_mount_inputs"),
            {},
        )
        if str(validate_step.get("status") or "") != "ok":
            payload["ok"] = False
            payload["warnings"] = list(payload.get("warnings") or []) + ["VMware NFS mount inputs are not ready yet."]
            return payload

        checks: list[dict[str, Any]] = []
        for host in probe_result["esxi_hosts"]:
            result = self._probe_host(str(host), [443, 22])
            checks.append(
                {
                    "kind": "esxi_host",
                    "label": f"ESXi host {host}",
                    "host": host,
                    "ports_tested": [443, 22],
                    **result,
                }
            )
        for server_ip in probe_result["server_ips"]:
            result = self._probe_host(str(server_ip), [2049])
            checks.append(
                {
                    "kind": "nfs_server",
                    "label": f"NFS server {server_ip}",
                    "host": server_ip,
                    "ports_tested": [2049],
                    **result,
                }
            )
        probe_result["checks"] = checks

        esxi_ok = any(item.get("reachable") for item in checks if item.get("kind") == "esxi_host")
        nfs_ok = bool(probe_result["server_ips"]) and all(item.get("reachable") for item in checks if item.get("kind") == "nfs_server")
        probe_result["ready"] = bool(esxi_ok and nfs_ok and probe_result["export_path"])
        payload["ok"] = bool(probe_result["ready"])
        if not probe_result["ready"]:
            payload["warnings"] = list(payload.get("warnings") or []) + ["Standalone ESXi or one or more NFS targets did not respond on the expected ports."]
        return payload

    def _validate_stage(self, context: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
        desired = self._profile_defaults(context)
        checks: list[dict[str, Any]] = []
        warnings: list[str] = []
        suggestions: list[str] = []
        raw = discovery.get("raw") or {}
        capabilities = dict(discovery.get("capabilities") or {})
        upgrade_posture = self._build_upgrade_posture(desired, discovery)
        protocol_profile = build_protocol_profile(context.get("cfg") or {})
        aggregate_targets = self._resolved_aggregate_targets(context, discovery)
        resolved_svm_name = self._resolved_svm_name(context, discovery)
        broadcast_domain_target = self._resolved_data_broadcast_domain(context, discovery)
        warnings.extend(validate_protocol_networks(protocol_profile))

        nodes = {str(item).strip() for item in list(discovery.get("nodes") or discovery.get("node_names") or []) if str(item).strip()}
        required_nodes = {str(item).strip() for item in list(desired.get("required_nodes") or []) if str(item).strip()}
        required_ok = required_nodes.issubset(nodes) if required_nodes else True
        checks.append({"name": "required_nodes_exist", "ok": required_ok, "details": {"required": sorted(required_nodes), "detected": sorted(nodes)}})
        if not required_ok:
            warnings.append("One or more required nodes were not discovered.")
            suggestions.append("Adjust required node list or confirm cluster membership.")

        available_ports = {str(token.split(":")[-1]).strip() for token in list(discovery.get("available_ports") or []) if str(token).strip()}
        expected_ports = {str(item).strip() for item in list(desired.get("expected_ports") or []) if str(item).strip()}
        profile = str(desired.get("storage_protocol") or "nfs").strip().lower()
        lif_key = "iscsi" if profile == "iscsi" else "nfs"
        for lif in list((desired.get(lif_key) or {}).get("lifs") or []):
            port = str((lif or {}).get("port") or "").strip()
            if port:
                expected_ports.add(port)
        expected_ports_ok = expected_ports.issubset(available_ports) if expected_ports else True
        checks.append({"name": "expected_ports_exist", "ok": expected_ports_ok, "details": {"expected": sorted(expected_ports), "detected": sorted(available_ports)}})
        if not expected_ports_ok:
            warnings.append("One or more expected ports were not discovered on this hardware/release.")
            suggestions.append("Use discovered ports/interface groups instead of fixed legacy ports.")

        aggregates = {str(item.get("name") or "").strip() for item in list(raw.get("aggregates") or []) if str(item.get("name") or "").strip()}
        desired_aggrs = list(aggregate_targets.get("desired") or [])
        resolved_aggrs = list(aggregate_targets.get("resolved") or [])
        missing_aggrs = [item for item in resolved_aggrs if item and item not in aggregates]
        checks.append(
            {
                "name": "aggregates_exist_or_can_be_created",
                "ok": True,
                "details": {
                    "desired": desired_aggrs,
                    "resolved": resolved_aggrs,
                    "detected": sorted(aggregates),
                    "missing": missing_aggrs,
                    "adopted": bool(aggregate_targets.get("adopted")),
                    "can_create_in_apply_phase": True,
                },
            }
        )
        if missing_aggrs:
            warnings.append(f"Missing desired aggregates: {', '.join(missing_aggrs)}.")
            suggestions.append("Plan aggregate creation as explicit apply actions after confirmation.")
        elif aggregate_targets.get("adopted"):
            suggestions.append(f"Using discovered aggregates {', '.join(resolved_aggrs)} in place of legacy defaults.")

        broadcast_domains = {str(item).strip() for item in list(discovery.get("existing_broadcast_domains") or []) if str(item).strip()}
        desired_domain = str(broadcast_domain_target.get("resolved") or "").strip()
        domain_ok = (not desired_domain) or (desired_domain in broadcast_domains)
        checks.append(
            {
                "name": "data_broadcast_domain_exists",
                "ok": domain_ok,
                "details": {
                    "requested": str(broadcast_domain_target.get("requested") or "").strip(),
                    "desired": desired_domain,
                    "detected": sorted(broadcast_domains),
                    "adopted": bool(broadcast_domain_target.get("adopted")),
                },
            }
        )
        if not domain_ok:
            warnings.append(f"Data broadcast domain '{desired_domain}' was not found.")
            suggestions.append("Create or remap broadcast domain in a controlled apply phase.")
        elif broadcast_domain_target.get("adopted"):
            suggestions.append(f"Using discovered broadcast domain {desired_domain} in place of legacy default {broadcast_domain_target.get('requested') or 'Data'}.")

        target_mtu = int(desired.get("target_mtu") or 9000)
        domain_ports = []
        for port in list(raw.get("ports") or []):
            domain = str(((port.get("broadcast_domain") or {}).get("name") or "")).strip()
            if desired_domain and domain != desired_domain:
                continue
            domain_ports.append(port)
        mtu_values = {int(item.get("mtu") or 0) for item in domain_ports if item.get("mtu") is not None}
        mtu_ok = (not domain_ports) or all(value == target_mtu for value in mtu_values)
        checks.append({"name": "mtu_can_be_set_to_9000", "ok": mtu_ok, "details": {"target_mtu": target_mtu, "detected_values": sorted(mtu_values)}})
        if not mtu_ok:
            warnings.append(f"Detected MTU values do not match target MTU {target_mtu}.")
            suggestions.append("Plan MTU alignment action and verify switch path supports jumbo frames.")

        licenses = {str(item.get("name") or "").strip().lower() for item in list(raw.get("licenses") or []) if str(item.get("name") or "").strip()}
        enabled_protocols = {str(item).strip().lower() for item in list(discovery.get("enabled_protocols") or []) if str(item).strip()}
        protocol = str(desired.get("storage_protocol") or "nfs").strip().lower()
        protocol_verifiable = bool(capabilities.get("protocol_services")) or bool(capabilities.get("licenses"))
        protocol_ok = protocol in {"nfs", "iscsi"} and (
            protocol in enabled_protocols or protocol in licenses or not protocol_verifiable
        )
        checks.append(
            {
                "name": "protocol_is_licensed_supported",
                "ok": protocol_ok,
                "details": {
                    "selected": protocol,
                    "enabled": sorted(enabled_protocols),
                    "licenses": sorted(licenses),
                    "verifiable": protocol_verifiable,
                },
            }
        )
        if not protocol_ok and protocol_verifiable:
            warnings.append(f"Selected protocol '{protocol}' does not appear enabled/licensed.")
            suggestions.append("Confirm protocol entitlement and SVM protocol configuration before apply.")
        elif not protocol_verifiable:
            suggestions.append("Protocol entitlement could not be fully verified through REST on this ONTAP release.")

        subnet_name = str(((desired.get("iscsi") or {}).get("subnet") or "")).strip() if protocol == "iscsi" else ""
        discovered_subnets = {str(item).strip() for item in list(discovery.get("subnets") or []) if str(item).strip()}
        subnet_verifiable = bool(capabilities.get("subnets"))
        subnet_ok = (not subnet_name) or (subnet_name in discovered_subnets) or (not subnet_verifiable)
        ip_ranges_ok = True
        if protocol == "iscsi":
            desired_range = str(((desired.get("iscsi") or {}).get("ip_range") or "")).strip()
            used_ips = {str((item.get("ip") or {}).get("address") or "").strip() for item in list(raw.get("interfaces") or [])}
            if desired_range and "-" in desired_range:
                start_ip, end_ip = [part.strip() for part in desired_range.split("-", 1)]
                ip_ranges_ok = start_ip not in used_ips and end_ip not in used_ips
        checks.append(
            {
                "name": "selected_ip_ranges_are_free",
                "ok": subnet_ok and ip_ranges_ok,
                "details": {
                    "iscsi_subnet": subnet_name,
                    "detected_subnets": sorted(discovered_subnets),
                    "verifiable": subnet_verifiable,
                },
            }
        )
        if not ip_ranges_ok:
            warnings.append("Selected iSCSI subnet/range may conflict with discovered configuration.")
            suggestions.append("Adjust iSCSI subnet/range after reviewing existing LIF IP usage.")
        elif protocol == "iscsi" and not subnet_verifiable:
            suggestions.append("Cluster subnet objects could not be verified through REST; review iSCSI subnet naming manually.")

        svms = list(raw.get("svms") or [])
        desired_svm = resolved_svm_name
        svm_match = next((item for item in svms if str(item.get("name") or "").strip() == desired_svm), None)
        svm_protocol_ok = True
        if desired_svm and svm_match:
            allowed = {str(item).strip().lower() for item in list(svm_match.get("allowed_protocols") or []) if str(item).strip()}
            svm_protocol_ok = protocol in allowed if allowed else True
        checks.append(
            {
                "name": "svm_exists_and_protocol_matches",
                "ok": (not desired_svm) or (svm_match is not None and svm_protocol_ok),
                "details": {"svm": desired_svm, "exists": svm_match is not None, "protocol_matches": svm_protocol_ok, "adopted": not bool(self._explicit_svm_name(context)) and bool(desired_svm)},
            }
        )
        if desired_svm and svm_match is None:
            warnings.append(f"SVM '{desired_svm}' was not found.")
            suggestions.append("Plan SVM creation only after confirming naming and protocol profile.")
        elif desired_svm and not svm_protocol_ok:
            warnings.append(f"SVM '{desired_svm}' exists but protocol '{protocol}' is not in allowed protocols.")
            suggestions.append("Plan SVM protocol update as explicit apply action.")

        desired_svm_lif = str(desired.get("svm_mgmt_lif") or "").strip()
        desired_svm_ip = str(desired.get("svm_mgmt_ip") or "").strip()
        interfaces = list(raw.get("interfaces") or [])
        discovered_lifs = list(discovery.get("lif_details") or [])
        svm_mgmt_match = False
        for item in interfaces:
            name = str(item.get("name") or "").strip()
            ip_address = str((item.get("ip") or {}).get("address") or "").strip()
            if desired_svm_lif and name == desired_svm_lif and ((not desired_svm_ip) or ip_address == desired_svm_ip):
                svm_mgmt_match = True
        checks.append(
            {
                "name": "svm_management_lif_exists",
                "ok": (not desired_svm_lif) or svm_mgmt_match,
                "details": {"lif": desired_svm_lif, "ip": desired_svm_ip, "exists": svm_mgmt_match},
            }
        )
        if desired_svm_lif and not svm_mgmt_match:
            warnings.append("Desired SVM management LIF was not discovered.")
            suggestions.append("Plan SVM management LIF creation or update after confirming management IP.")

        lifs_verifiable = bool(capabilities.get("network_interfaces"))
        desired_protocol_lifs = list((protocol_profile.get(protocol) or {}).get("lifs") or [])
        detected_lif_map = {
            str(item.get("name") or "").strip(): item
            for item in discovered_lifs
            if str(item.get("name") or "").strip()
        }
        missing_protocol_lifs: list[str] = []
        conflicting_protocol_lifs: list[dict[str, Any]] = []
        for lif in desired_protocol_lifs:
            lif_name = str((lif or {}).get("name") or "").strip()
            if not lif_name:
                continue
            detected = detected_lif_map.get(lif_name)
            if not detected:
                missing_protocol_lifs.append(lif_name)
                continue
            desired_ip = str((lif or {}).get("ip") or "").strip()
            desired_node = str((lif or {}).get("node") or "").strip()
            desired_port = str((lif or {}).get("port") or "").strip()
            actual_ip = str(detected.get("address") or "").strip()
            actual_node = str(detected.get("home_node") or "").strip()
            actual_port = str(detected.get("home_port") or "").strip()
            conflict_fields: list[str] = []
            if desired_ip and actual_ip and desired_ip != actual_ip:
                conflict_fields.append("ip")
            if desired_node and actual_node and desired_node != actual_node:
                conflict_fields.append("node")
            if desired_port and actual_port and desired_port != actual_port:
                conflict_fields.append("port")
            if conflict_fields:
                conflicting_protocol_lifs.append(
                    {
                        "name": lif_name,
                        "conflicts": conflict_fields,
                        "desired": {"ip": desired_ip, "node": desired_node, "port": desired_port},
                        "actual": {"ip": actual_ip, "node": actual_node, "port": actual_port},
                    }
                )
        protocol_lifs_ok = (
            not desired_protocol_lifs
            or ((not missing_protocol_lifs) and (not conflicting_protocol_lifs))
            or (not lifs_verifiable)
        )
        checks.append(
            {
                "name": "protocol_lifs_match",
                "ok": protocol_lifs_ok,
                "details": {
                    "protocol": protocol,
                    "desired_names": [str((item or {}).get("name") or "").strip() for item in desired_protocol_lifs if str((item or {}).get("name") or "").strip()],
                    "missing": missing_protocol_lifs,
                    "conflicting": conflicting_protocol_lifs,
                    "verifiable": lifs_verifiable,
                },
            }
        )
        if lifs_verifiable and missing_protocol_lifs:
            warnings.append(f"One or more desired {protocol.upper()} LIFs were not discovered.")
            suggestions.append("Plan protocol LIF creation after confirming node-to-port placement.")
        if lifs_verifiable and conflicting_protocol_lifs:
            warnings.append(f"One or more discovered {protocol.upper()} LIFs do not match the desired IP, node, or port.")
            suggestions.append("Review LIF naming and placement before reusing legacy defaults.")
        elif desired_protocol_lifs and not lifs_verifiable:
            suggestions.append(f"{protocol.upper()} LIF state could not be fully verified through REST on this ONTAP release.")

        autosupport = raw.get("autosupport") or {}
        autosupport_enabled = bool(autosupport.get("enabled")) if isinstance(autosupport, dict) else False
        autosupport_verifiable = bool(capabilities.get("autosupport"))
        autosupport_ok = (not desired.get("autosupport_enabled", True)) or autosupport_enabled or (not autosupport_verifiable)
        checks.append(
            {
                "name": "autosupport_configured",
                "ok": autosupport_ok,
                "details": {
                    "desired_enabled": bool(desired.get("autosupport_enabled", True)),
                    "detected_enabled": autosupport_enabled,
                    "verifiable": autosupport_verifiable,
                    "from": desired.get("autosupport_from"),
                    "to": desired.get("autosupport_to"),
                    "mail_hosts": list(desired.get("autosupport_mail_hosts") or []),
                },
            }
        )
        if not autosupport_ok and autosupport_verifiable:
            warnings.append("AutoSupport is not enabled or could not be verified.")
            suggestions.append("Plan AutoSupport configuration and confirm SMTP/mail-host reachability.")
        elif not autosupport_verifiable:
            suggestions.append("AutoSupport settings could not be fully verified through REST on this ONTAP release.")

        discovered_ntp = {str(item.get("server") or "").strip() for item in list(raw.get("ntp_servers") or []) if str(item.get("server") or "").strip()}
        desired_ntp = {str(item).strip() for item in list(desired.get("ntp_servers") or []) if str(item).strip()}
        ntp_verifiable = bool(capabilities.get("ntp_servers"))
        ntp_ok = desired_ntp.issubset(discovered_ntp) if desired_ntp and ntp_verifiable else True
        checks.append(
            {
                "name": "ntp_servers_configured",
                "ok": ntp_ok,
                "details": {"desired": sorted(desired_ntp), "detected": sorted(discovered_ntp), "verifiable": ntp_verifiable},
            }
        )
        if not ntp_ok and ntp_verifiable:
            warnings.append("One or more desired NTP servers are missing.")
            suggestions.append("Plan NTP server update and retry if the time server is temporarily unreachable.")
        elif desired_ntp and not ntp_verifiable:
            suggestions.append("NTP configuration could not be fully verified through REST on this ONTAP release.")

        discovered_users = {str(item.get("name") or "").strip() for item in list(raw.get("users") or []) if str(item.get("name") or "").strip()}
        desired_users = {str(item).strip() for item in list(desired.get("required_users") or []) if str(item).strip()}
        users_verifiable = bool(capabilities.get("users"))
        users_ok = desired_users.issubset(discovered_users) if desired_users and users_verifiable else True
        checks.append(
            {
                "name": "required_users_exist",
                "ok": users_ok,
                "details": {"desired": sorted(desired_users), "detected": sorted(discovered_users), "verifiable": users_verifiable},
            }
        )
        if not users_ok and users_verifiable:
            warnings.append("One or more required NetApp users are missing.")
            suggestions.append("Plan role/user creation for Power and kit technician accounts.")
        elif desired_users and not users_verifiable:
            suggestions.append("User and role state could not be fully verified through REST on this ONTAP release.")

        export_policies = list(raw.get("export_policies") or [])
        igroups = list(raw.get("igroups") or [])
        portsets = list(raw.get("portsets") or [])
        luns = list(raw.get("luns") or [])
        lun_maps = list(raw.get("lun_maps") or [])
        if protocol == "nfs":
            desired_export_policy = str(((desired.get("nfs") or {}).get("export_policy") or "")).strip()
            desired_allowed_subnet = str(((desired.get("nfs") or {}).get("allowed_subnet") or "")).strip()
            desired_nfs_volume = str(((desired.get("nfs") or {}).get("volume") or "")).strip()
            policy_verifiable = bool(capabilities.get("export_policies"))
            volume_verifiable = bool(capabilities.get("volumes"))
            matched_policy = next((item for item in export_policies if str(item.get("name") or "").strip() == desired_export_policy), None)
            matched_volume = next((item for item in list(raw.get("volumes") or []) if str(item.get("name") or "").strip() == desired_nfs_volume), None)
            subnet_allowed = True
            if policy_verifiable and matched_policy and desired_allowed_subnet:
                subnet_allowed = False
                for rule in list(matched_policy.get("rules") or []):
                    clients = list(rule.get("clients") or [])
                    for client in clients:
                        if desired_allowed_subnet == str(client.get("match") or "").strip():
                            subnet_allowed = True
                            break
                    if subnet_allowed:
                        break
            volume_policy_name = str(((((matched_volume or {}).get("nas") or {}).get("export_policy") or {}).get("name") or "")).strip()
            volume_policy_ok = True
            if volume_verifiable and desired_export_policy and matched_volume is not None and volume_policy_name:
                volume_policy_ok = volume_policy_name == desired_export_policy
            checks.append(
                {
                    "name": "nfs_export_policy_matches",
                    "ok": (
                        (not desired_export_policy)
                        or ((matched_policy is not None) and subnet_allowed and volume_policy_ok)
                        or (not policy_verifiable)
                    ),
                    "details": {
                        "policy": desired_export_policy,
                        "exists": matched_policy is not None,
                        "allowed_subnet": desired_allowed_subnet,
                        "subnet_allowed": subnet_allowed,
                        "volume": desired_nfs_volume,
                        "volume_policy": volume_policy_name,
                        "volume_policy_ok": volume_policy_ok,
                        "verifiable": policy_verifiable,
                    },
                }
            )
            if policy_verifiable and desired_export_policy and matched_policy is None:
                warnings.append(f"NFS export policy '{desired_export_policy}' was not found.")
                suggestions.append("Plan export policy creation before mounting NFS datastores.")
            elif policy_verifiable and matched_policy is not None and desired_allowed_subnet and not subnet_allowed:
                warnings.append(f"NFS export policy '{desired_export_policy}' does not clearly allow {desired_allowed_subnet}.")
                suggestions.append("Review export rules and align them with the ESXi management or storage subnet.")
            elif policy_verifiable and matched_policy is not None and volume_verifiable and matched_volume is not None and not volume_policy_ok:
                warnings.append(f"NFS volume '{desired_nfs_volume}' is using export policy '{volume_policy_name or 'unknown'}' instead of '{desired_export_policy}'.")
                suggestions.append("Assign the intended export policy to the NFS datastore volume before mounting from ESXi.")
            elif desired_export_policy and not policy_verifiable:
                suggestions.append("NFS export policies could not be fully verified through REST on this ONTAP release.")

            volume_names = {str(item.get("name") or "").strip() for item in list(raw.get("volumes") or []) if str(item.get("name") or "").strip()}
            checks.append(
                {
                    "name": "nfs_volume_exists",
                    "ok": (not desired_nfs_volume) or (desired_nfs_volume in volume_names) or (not volume_verifiable),
                    "details": {"volume": desired_nfs_volume, "detected": sorted(volume_names), "verifiable": volume_verifiable},
                }
            )
            if volume_verifiable and desired_nfs_volume and desired_nfs_volume not in volume_names:
                warnings.append(f"NFS volume '{desired_nfs_volume}' was not found.")
                suggestions.append("Plan NFS volume creation and junction path setup before mounting the datastore.")
            elif desired_nfs_volume and not volume_verifiable:
                suggestions.append("NFS volume state could not be fully verified through REST on this ONTAP release.")
        else:
            desired_igroup = str(((desired.get("iscsi") or {}).get("igroup") or "")).strip()
            desired_portset = str(((desired.get("iscsi") or {}).get("portset") or "")).strip()
            desired_iscsi_volumes = [item for item in list(((desired.get("iscsi") or {}).get("volumes") or [])) if isinstance(item, dict)]
            igroup_verifiable = bool(capabilities.get("igroups"))
            portset_verifiable = bool(capabilities.get("portsets"))
            lun_verifiable = bool(capabilities.get("luns")) and bool(capabilities.get("lun_maps"))
            matched_igroup = next((item for item in igroups if str(item.get("name") or "").strip() == desired_igroup), None)
            matched_portset = next((item for item in portsets if str(item.get("name") or "").strip() == desired_portset), None)
            checks.append(
                {
                    "name": "iscsi_igroup_exists",
                    "ok": (not desired_igroup) or (matched_igroup is not None) or (not igroup_verifiable),
                    "details": {"igroup": desired_igroup, "exists": matched_igroup is not None, "verifiable": igroup_verifiable},
                }
            )
            if igroup_verifiable and desired_igroup and matched_igroup is None:
                warnings.append(f"iSCSI igroup '{desired_igroup}' was not found.")
                suggestions.append("Plan igroup creation before mapping VMware LUNs.")
            elif desired_igroup and not igroup_verifiable:
                suggestions.append("iSCSI igroup state could not be fully verified through REST on this ONTAP release.")

            igroup_metadata_ok = True
            if igroup_verifiable and matched_igroup is not None:
                detected_protocol = str(matched_igroup.get("protocol") or "").strip().lower()
                detected_os_type = str(matched_igroup.get("os_type") or "").strip().lower()
                igroup_metadata_ok = (not detected_protocol or detected_protocol == "iscsi") and (not detected_os_type or detected_os_type == "vmware")
            checks.append(
                {
                    "name": "iscsi_igroup_metadata_matches",
                    "ok": igroup_metadata_ok or (not igroup_verifiable) or (matched_igroup is None and not desired_igroup),
                    "details": {
                        "igroup": desired_igroup,
                        "protocol": str((matched_igroup or {}).get("protocol") or "").strip().lower() if matched_igroup else "",
                        "os_type": str((matched_igroup or {}).get("os_type") or "").strip().lower() if matched_igroup else "",
                        "verifiable": igroup_verifiable,
                    },
                }
            )
            if igroup_verifiable and matched_igroup is not None and not igroup_metadata_ok:
                warnings.append(f"iSCSI igroup '{desired_igroup}' exists but its protocol or OS type does not match VMware iSCSI expectations.")
                suggestions.append("Review igroup metadata before reusing it for VMware datastore LUN mapping.")

            checks.append(
                {
                    "name": "iscsi_portset_exists",
                    "ok": (not desired_portset) or (matched_portset is not None) or (not portset_verifiable),
                    "details": {"portset": desired_portset, "exists": matched_portset is not None, "verifiable": portset_verifiable},
                }
            )
            if portset_verifiable and desired_portset and matched_portset is None:
                warnings.append(f"iSCSI portset '{desired_portset}' was not found.")
                suggestions.append("Plan portset creation or confirm whether the array intentionally avoids legacy portsets.")
            elif desired_portset and not portset_verifiable:
                suggestions.append("iSCSI portset state could not be fully verified through REST on this ONTAP release.")

            portset_metadata_ok = True
            if portset_verifiable and matched_portset is not None:
                detected_protocol = str(matched_portset.get("protocol") or "").strip().lower()
                portset_metadata_ok = (not detected_protocol) or detected_protocol == "iscsi"
            checks.append(
                {
                    "name": "iscsi_portset_metadata_matches",
                    "ok": portset_metadata_ok or (not portset_verifiable) or (matched_portset is None and not desired_portset),
                    "details": {
                        "portset": desired_portset,
                        "protocol": str((matched_portset or {}).get("protocol") or "").strip().lower() if matched_portset else "",
                        "verifiable": portset_verifiable,
                    },
                }
            )
            if portset_verifiable and matched_portset is not None and not portset_metadata_ok:
                warnings.append(f"iSCSI portset '{desired_portset}' exists but is not marked for iSCSI.")
                suggestions.append("Review portset protocol metadata before reusing it.")

            desired_lun_names = {
                str(item.get("lun_name") or item.get("volume_name") or "").strip()
                for item in desired_iscsi_volumes
                }
            desired_lun_names = {name for name in desired_lun_names if name}
            detected_luns = {str(item.get("name") or "").strip() for item in luns if str(item.get("name") or "").strip()}
            mapped_igroups = {str(((item.get("igroup") or {}).get("name") or "")).strip() for item in lun_maps if str(((item.get("igroup") or {}).get("name") or "")).strip()}
            lun_ok = (not desired_lun_names) or (
                desired_lun_names.issubset(detected_luns) and ((not desired_igroup) or (desired_igroup in mapped_igroups))
            ) or (not lun_verifiable)
            checks.append(
                {
                    "name": "iscsi_lun_inventory_matches",
                    "ok": lun_ok,
                    "details": {
                        "desired_luns": sorted(desired_lun_names),
                        "detected_luns": sorted(detected_luns),
                        "mapped_igroups": sorted(mapped_igroups),
                        "verifiable": lun_verifiable,
                    },
                }
            )
            if lun_verifiable and desired_lun_names and not desired_lun_names.issubset(detected_luns):
                warnings.append("One or more desired iSCSI LUNs were not found.")
                suggestions.append("Plan volume/LUN creation only after confirming datastore sizing and LUN IDs.")
            elif lun_verifiable and desired_igroup and desired_igroup not in mapped_igroups and desired_lun_names:
                warnings.append(f"Desired iSCSI LUNs are not mapped to igroup '{desired_igroup}'.")
                suggestions.append("Plan LUN mapping after verifying the VMware initiator group.")
            elif desired_lun_names and not lun_verifiable:
                suggestions.append("iSCSI LUN and mapping state could not be fully verified through REST on this ONTAP release.")

        baseline_ok = bool(upgrade_posture.get("meets_baseline"))
        checks.append(
            {
                "name": "ontap_meets_baseline",
                "ok": baseline_ok,
                "details": upgrade_posture,
            }
        )
        if not baseline_ok:
            if upgrade_posture.get("status") == "upgrade_required":
                warnings.append(
                    f"ONTAP {upgrade_posture.get('current_version') or 'unknown'} is below the baseline target {upgrade_posture.get('target_version')}."
                )
                suggestions.append("Plan an ONTAP upgrade before applying configuration changes.")
            elif upgrade_posture.get("status") == "upgrade_recommended":
                warnings.append(
                    f"ONTAP {upgrade_posture.get('current_version') or 'unknown'} is below the preferred baseline {upgrade_posture.get('target_version')}."
                )
                suggestions.append("Upgrade ONTAP to the current baseline to reduce capability gaps.")

        stage = {
            "name": "NetApp Stage 2: Validate",
            "ok": all(bool(item.get("ok")) for item in checks),
            "steps": [
                "Check if required nodes exist",
                "Check if expected ports exist",
                "Check if aggregates exist or can be created",
                "Check if Data broadcast domain exists",
                "Check if MTU can be set to 9000",
                "Check if selected protocol is licensed/supported",
                "Check if selected IP ranges are free",
                "Check if SVM already exists and whether its protocol matches",
                "Check if SVM management LIF exists",
                "Check AutoSupport, NTP servers, and required users",
                "Check protocol-specific export policy or SAN object state",
                "Check ONTAP version against baseline",
            ],
            "checks": checks,
        }
        return {
            "stage": stage,
            "warnings": list(dict.fromkeys(warnings)),
            "suggestions": list(dict.fromkeys(suggestions)),
        }

    def _build_action_plan(self, context: dict[str, Any], discovery: dict[str, Any], validate_stage: dict[str, Any]) -> dict[str, Any]:
        desired = self._profile_defaults(context)
        protocol = str(desired.get("storage_protocol") or "nfs").strip().lower()
        vmware_plan = build_vmware_plan(context.get("cfg") or {}, storage_protocol=protocol, discovery=discovery)
        actions: list[dict[str, Any]] = []
        checks = {str(item.get("name")): item for item in list(validate_stage.get("checks") or [])}
        capability_status = dict(discovery.get("capability_status") or {})
        aggregate_targets = self._resolved_aggregate_targets(context, discovery)
        broadcast_domain_target = self._resolved_data_broadcast_domain(context, discovery)
        baseline_check = checks.get("ontap_meets_baseline") or {}
        baseline_details = baseline_check.get("details") or {}
        subnet_write_status = "create" if capability_status.get("subnets", "native") != "missing" else "manual"
        explicit_svm_name = self._explicit_svm_name(context)
        resolved_svm_name = self._resolved_svm_name(context, discovery)
        svm_exists = bool((checks.get("svm_exists_and_protocol_matches") or {}).get("details", {}).get("exists"))
        svm_write_status = "create" if explicit_svm_name else "manual"
        svm_followup_status = "update" if resolved_svm_name else "manual"

        missing_aggrs = list(((checks.get("aggregates_exist_or_can_be_created") or {}).get("details") or {}).get("missing") or [])
        aggregates_ready = not bool(missing_aggrs)
        if not bool(baseline_check.get("ok")):
            actions.append({"name": "plan_ontap_upgrade_to_baseline", "type": "manual", "status": "warn"})
        actions.append({"name": "simulate_aggregate_aggr_01", "type": "manual", "status": "manual"})
        actions.append({"name": "simulate_aggregate_aggr_02", "type": "manual", "status": "manual"})
        actions.append({"name": "ensure_aggregate_aggr_01", "type": "create", "status": "create" if str(aggregate_targets.get("resolved_01") or "") in missing_aggrs else "skip"})
        actions.append({"name": "ensure_aggregate_aggr_02", "type": "create", "status": "create" if str(aggregate_targets.get("resolved_02") or "") in missing_aggrs else "skip"})
        actions.append({"name": "review_link_aggregation_groups", "type": "manual", "status": "manual"})
        actions.append({"name": "remove_default_broadcast_domains", "type": "manual", "status": "warn"})
        actions.append({"name": "ensure_data_broadcast_domain", "type": "create", "status": "skip" if bool((checks.get("data_broadcast_domain_exists") or {}).get("ok")) else "warn"})
        actions.append({"name": "ensure_management_subnet", "type": "create", "status": subnet_write_status})
        actions.append({"name": "ensure_svm", "type": "create", "status": "skip" if svm_exists or resolved_svm_name else svm_write_status})
        actions.append({"name": "ensure_svm_management_lif", "type": "create", "status": "skip" if bool((checks.get("svm_management_lif_exists") or {}).get("ok")) else ("create" if resolved_svm_name else "manual")})
        actions.append({"name": "ensure_autosupport", "type": "update", "status": "skip" if bool((checks.get("autosupport_configured") or {}).get("ok")) else "update"})
        actions.append({"name": "ensure_ntp_servers", "type": "update", "status": "skip" if bool((checks.get("ntp_servers_configured") or {}).get("ok")) else "update"})
        actions.append({"name": "ensure_power_and_tech_users", "type": "create", "status": "skip" if bool((checks.get("required_users_exist") or {}).get("ok")) else "manual"})

        if protocol == "iscsi":
            actions.extend(
                [
                    {
                        "name": "ensure_iscsi_subnet",
                        "type": "create",
                        "status": (
                            "manual"
                            if capability_status.get("subnets", "native") == "missing"
                            else "warn" if not bool((checks.get("selected_ip_ranges_are_free") or {}).get("ok")) else "create"
                        ),
                    },
                    {
                        "name": "ensure_iscsi_lifs",
                        "type": "create",
                        "status": (
                            "skip"
                            if bool((checks.get("protocol_lifs_match") or {}).get("ok"))
                            else "warn" if not bool((checks.get("expected_ports_exist") or {}).get("ok")) else "create"
                        ),
                    },
                    {"name": "ensure_iscsi_service", "type": "update", "status": svm_followup_status},
                    {"name": "ensure_iscsi_portset", "type": "create", "status": "skip" if bool((checks.get("iscsi_portset_exists") or {}).get("ok")) else ("create" if resolved_svm_name else "manual")},
                    {"name": "ensure_iscsi_igroup", "type": "create", "status": "skip" if bool((checks.get("iscsi_igroup_exists") or {}).get("ok")) else ("create" if resolved_svm_name else "manual")},
                    {"name": "ensure_iscsi_iqns", "type": "update", "status": "manual"},
                    {"name": "ensure_netapp_volumes", "type": "create", "status": "skip" if bool((checks.get("iscsi_lun_inventory_matches") or {}).get("ok")) else "manual"},
                    {"name": "plan_lun_vmfs_datastore", "type": "create", "status": "skip" if bool((checks.get("iscsi_lun_inventory_matches") or {}).get("ok")) else "manual"},
                    {"name": "plan_vmware_datastore_script", "type": "manual", "status": "manual"},
                ]
            )
        else:
            vmware_mount_validate = next(
                (step for step in list(vmware_plan.get("steps") or []) if str(step.get("name") or "") == "validate_nfs_mount_inputs"),
                {},
            )
            standalone_mount_state = self._standalone_esxi_nfs_mount_state(context, vmware_plan)
            standalone_mount_ready = (
                str(vmware_plan.get("connection_mode") or "") == "standalone_esxi"
                and str(vmware_mount_validate.get("status") or "") == "ok"
            )
            actions.extend(
                [
                    {
                        "name": "ensure_nfs_lifs",
                        "type": "create",
                        "status": (
                            "skip"
                            if bool((checks.get("protocol_lifs_match") or {}).get("ok"))
                            else "warn" if not bool((checks.get("expected_ports_exist") or {}).get("ok")) else "create"
                        ),
                    },
                    {"name": "ensure_nfs_service", "type": "update", "status": svm_followup_status},
                    {"name": "ensure_nfs_volume", "type": "create", "status": "skip" if bool((checks.get("nfs_volume_exists") or {}).get("ok")) else ("create" if resolved_svm_name and aggregates_ready else "manual")},
                    {"name": "ensure_export_policy", "type": "create", "status": "skip" if bool((checks.get("nfs_export_policy_matches") or {}).get("ok")) else ("create" if resolved_svm_name else "manual")},
                    {
                        "name": "ensure_esxi_nfs_datastore_mount",
                        "type": "create",
                        "status": (
                            "skip"
                            if bool(standalone_mount_state.get("verifiable")) and bool(standalone_mount_state.get("mounted"))
                            else "create" if standalone_mount_ready else "manual"
                        ),
                    },
                    {"name": "plan_vmware_datastore_script", "type": "manual", "status": "manual"},
                ]
            )

        netapp_cfg = self._netapp_cfg(context)
        templates = netapp_cfg.get("command_templates") or {}
        template_text = str((templates.get("iscsi") if protocol == "iscsi" else templates.get("nfs")) or "")
        if not template_text.strip():
            template_text = self._default_iscsi_template() if protocol == "iscsi" else self._default_nfs_template()
        command_preview = self._render_command_template(template_text, self._template_values(context, desired))

        stage = {
            "name": "NetApp Stage 3: Plan",
            "ok": True,
            "steps": [
                "Build list of API actions",
                "Mark each action as create/update/skip/warn/manual",
                "Show the user before applying",
            ],
            "actions": actions,
            "command_preview": command_preview,
        }
        return {"stage": stage, "actions": actions, "command_preview": command_preview}

    def _find_svm_record(self, discovery: dict[str, Any], svm_name: str) -> dict[str, Any] | None:
        for item in list(((discovery.get("raw") or {}).get("svms") or [])):
            if str(item.get("name") or "").strip() == svm_name:
                return item
        return None

    def _find_protocol_service_record(self, discovery: dict[str, Any], protocol: str, svm_name: str) -> dict[str, Any] | None:
        services = ((((discovery.get("raw") or {}).get("protocol_services") or {}).get(protocol)) or [])
        for item in list(services):
            service_svm = str(((item.get("svm") or {}).get("name") or "")).strip()
            if service_svm == svm_name:
                return item
        return None

    def _find_volume_record(self, discovery: dict[str, Any], volume_name: str) -> dict[str, Any] | None:
        for item in list(((discovery.get("raw") or {}).get("volumes") or [])):
            if str(item.get("name") or "").strip() == volume_name:
                return item
        return None

    def _create_svm(self, client: NetAppClient, desired: dict[str, Any], naming: dict[str, str]) -> dict[str, Any]:
        protocol = str(desired.get("storage_protocol") or "nfs").strip().lower()
        body = {
            "name": str(desired.get("svm_name") or naming["svm_name"]).strip(),
            "subtype": "default",
            "aggregates": [{"name": str(desired.get("aggregate_node_01") or "aggr_01").strip()}],
        }
        if protocol == "nfs":
            body["nfs"] = {"allowed": True}
        elif protocol == "iscsi":
            body["iscsi"] = {"enabled": True}
        return client.post("/api/svm/svms", body)

    def _enable_protocol_on_svm(self, client: NetAppClient, discovery: dict[str, Any], desired: dict[str, Any], protocol: str) -> dict[str, Any]:
        svm_name = str(desired.get("svm_name") or "").strip()
        svm = self._find_svm_record(discovery, svm_name)
        if not svm:
            raise NetAppError(f"SVM '{svm_name}' is not present to enable protocol '{protocol}'.")
        svm_uuid = str(svm.get("uuid") or "").strip()
        if not svm_uuid:
            raise NetAppError(f"SVM '{svm_name}' does not expose a UUID through the current ONTAP API surface.")
        allowed = [str(item).strip().lower() for item in list(svm.get("allowed_protocols") or []) if str(item).strip()]
        if protocol not in allowed:
            allowed.append(protocol)
        return client.patch(f"/api/svm/svms/{svm_uuid}", {"allowed_protocols": sorted(set(allowed))})

    def _create_ip_interface(
        self,
        client: NetAppClient,
        *,
        svm_name: str,
        lif_name: str,
        address: str,
        netmask: str,
        home_node: str,
        home_port: str,
        service_policy: str,
    ) -> dict[str, Any]:
        body = {
            "name": lif_name,
            "svm": {"name": svm_name},
            "ip": {"address": address, "netmask": netmask},
            "location": {"home_node": {"name": home_node}, "home_port": {"name": home_port}},
            "service_policy": {"name": service_policy},
            "enabled": True,
        }
        return client.post("/api/network/ip/interfaces", body)

    def _ensure_subnet(self, client: NetAppClient, *, name: str, subnet_cidr: str, gateway: str, broadcast_domain: str, ip_ranges: list[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "subnet": subnet_cidr,
            "gateway": gateway,
            "broadcast_domain": {"name": broadcast_domain},
        }
        if ip_ranges:
            body["ranges"] = [{"start": item.split("-", 1)[0].strip(), "end": item.split("-", 1)[1].strip()} for item in ip_ranges if "-" in item]
        return client.post("/api/network/ip/subnets", body)

    def _ensure_nfs_service(self, client: NetAppClient, *, svm_name: str) -> dict[str, Any]:
        return client.post("/api/protocols/nfs/services", {"svm": {"name": svm_name}, "enabled": True})

    def _ensure_iscsi_service(self, client: NetAppClient, *, svm_name: str) -> dict[str, Any]:
        return client.post("/api/protocols/san/iscsi/services", {"svm": {"name": svm_name}, "enabled": True})

    def _ensure_export_policy(self, client: NetAppClient, *, svm_name: str, policy_name: str) -> dict[str, Any]:
        return client.post("/api/protocols/nfs/export-policies", {"svm": {"name": svm_name}, "name": policy_name})

    def _ensure_export_policy_rule(self, client: NetAppClient, *, policy_id: int, client_match: str) -> dict[str, Any]:
        return client.post(
            f"/api/protocols/nfs/export-policies/{policy_id}/rules",
            {
                "clients": [{"match": client_match}],
                "protocols": ["nfs"],
                "ro_rule": ["sys"],
                "rw_rule": ["sys"],
                "superuser": ["sys"],
            },
        )

    def _ensure_igroup(self, client: NetAppClient, *, svm_name: str, igroup_name: str) -> dict[str, Any]:
        return client.post(
            "/api/protocols/san/igroups",
            {"svm": {"name": svm_name}, "name": igroup_name, "protocol": "iscsi", "os_type": "vmware"},
        )

    def _ensure_portset(self, client: NetAppClient, *, svm_name: str, portset_name: str) -> dict[str, Any]:
        return client.post(
            "/api/protocols/san/portsets",
            {"svm": {"name": svm_name}, "name": portset_name, "protocol": "iscsi"},
        )

    def _ensure_volume(
        self,
        client: NetAppClient,
        *,
        svm_name: str,
        volume_name: str,
        aggregate_name: str,
        size: str,
        nas_path: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "svm": {"name": svm_name},
            "name": volume_name,
            "aggregates": [{"name": aggregate_name}],
            "size": size,
        }
        if nas_path:
            body["nas"] = {"path": nas_path}
        return client.post("/api/storage/volumes", body)

    def _assign_volume_export_policy(self, client: NetAppClient, *, volume_uuid: str, policy_name: str) -> dict[str, Any]:
        return client.patch(f"/api/storage/volumes/{volume_uuid}", {"nas": {"export_policy": {"name": policy_name}}})

    def _esxi_ssh_command(self, context: dict[str, Any], remote_command: str) -> tuple[int, str, str]:
        cfg = context.get("cfg") or {}
        esxi_cfg = cfg.get("esxi") or {}
        vmware_cfg = cfg.get("vmware") or {}
        host = str(esxi_cfg.get("management_ip") or (cfg.get("ip_plan") or {}).get("esxi") or "").strip()
        password = str(esxi_cfg.get("root_password") or vmware_cfg.get("esxi_root_password") or "").strip()
        if not host:
            raise NetAppError("Standalone ESXi management IP is not configured.")
        if not password:
            raise NetAppError("Standalone ESXi root password is not configured.")
        if not shutil.which("sshpass"):
            raise NetAppError("sshpass is required for standalone ESXi NFS datastore automation.")
        try:
            proc = subprocess.run(
                [
                    "sshpass",
                    "-p",
                    password,
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "LogLevel=ERROR",
                    f"root@{host}",
                    remote_command,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise NetAppError(f"ESXi SSH command timed out after 30s: {remote_command}") from exc
        return proc.returncode, str(proc.stdout or ""), str(proc.stderr or "")

    def _ensure_standalone_esxi_nfs_datastore(self, context: dict[str, Any], vmware_plan: dict[str, Any]) -> dict[str, Any]:
        validate_step = next(
            (step for step in list(vmware_plan.get("steps") or []) if str(step.get("name") or "") == "validate_nfs_mount_inputs"),
            {},
        )
        if str(vmware_plan.get("connection_mode") or "") != "standalone_esxi":
            return {"status": "skipped", "reason": "not_standalone_esxi"}
        if str(validate_step.get("status") or "") != "ok":
            return {"status": "skipped", "reason": "mount_inputs_not_ready"}
        mount_step = next(
            (step for step in list(vmware_plan.get("steps") or []) if str(step.get("name") or "") == "plan_nfs_datastore_mounts"),
            {},
        )
        mount_plan = list(((mount_step.get("details") or {}).get("mount_plan")) or [])
        if not mount_plan:
            return {"status": "skipped", "reason": "no_mount_plan"}
        target = dict(mount_plan[0] or {})
        datastore_name = str(target.get("datastore_name") or "").strip()
        nfs_version = str(target.get("nfs_version") or "4.1").strip()
        list_cmd = "esxcli storage nfs41 list" if nfs_version == "4.1" else "esxcli storage nfs list"
        code, out, err = self._esxi_ssh_command(context, list_cmd)
        if code != 0:
            raise NetAppError((err or out or "Failed to read existing ESXi NFS mounts.").splitlines()[0])
        existing_line = next((line for line in out.splitlines() if datastore_name and datastore_name in line), "")
        if existing_line:
            lowered = existing_line.lower()
            if "true" in lowered:
                return {"status": "skipped", "reason": "datastore_already_mounted", "stdout": out}
            raise NetAppError(
                f"Datastore '{datastore_name}' already exists on ESXi but is not accessible or mounted. "
                "Manual cleanup or remount review is required before retrying automation."
            )
        add_cmd = str(target.get("esxcli_command") or "").strip()
        if not add_cmd:
            raise NetAppError("No standalone ESXi NFS mount command could be generated.")
        fallback_cmd = str(target.get("esxcli_fallback_command") or "").strip()
        fallback_used = False
        try:
            code, out, err = self._esxi_ssh_command(context, add_cmd)
        except NetAppError as exc:
            if fallback_cmd and nfs_version == "4.1":
                cleanup_code, cleanup_out, cleanup_err = self._esxi_ssh_command(context, "esxcli storage nfs41 list")
                cleanup_line = next((line for line in cleanup_out.splitlines() if datastore_name and datastore_name in line), "")
                if cleanup_line:
                    self._esxi_ssh_command(context, f"esxcli storage nfs41 remove -v {datastore_name}")
                code, out, err = self._esxi_ssh_command(context, fallback_cmd)
                if code == 0:
                    fallback_used = True
                    list_cmd = "esxcli storage nfs list"
                else:
                    raise NetAppError((err or out or str(exc)).splitlines()[0]) from exc
            else:
                raise
        if code != 0 and fallback_cmd and nfs_version == "4.1":
            cleanup_code, cleanup_out, cleanup_err = self._esxi_ssh_command(context, "esxcli storage nfs41 list")
            cleanup_line = next((line for line in cleanup_out.splitlines() if datastore_name and datastore_name in line), "")
            if cleanup_line:
                self._esxi_ssh_command(context, f"esxcli storage nfs41 remove -v {datastore_name}")
            code, out, err = self._esxi_ssh_command(context, fallback_cmd)
            if code == 0:
                fallback_used = True
                list_cmd = "esxcli storage nfs list"
            else:
                raise NetAppError((err or out or "ESXi NFS datastore mount command failed.").splitlines()[0])
        elif code != 0:
            raise NetAppError((err or out or "ESXi NFS datastore mount command failed.").splitlines()[0])
        verify_code, verify_out, verify_err = self._esxi_ssh_command(context, list_cmd)
        if verify_code != 0:
            raise NetAppError((verify_err or verify_out or "Failed to verify ESXi NFS mounts after apply.").splitlines()[0])
        if datastore_name and datastore_name not in verify_out:
            raise NetAppError(f"Datastore '{datastore_name}' was not present in ESXi NFS mounts after apply.")
        return {
            "status": "applied",
            "command": fallback_cmd if fallback_used else add_cmd,
            "stdout": verify_out,
            "fallback_used": fallback_used,
            "requested_nfs_version": nfs_version,
            "effective_nfs_version": "3" if fallback_used else nfs_version,
        }

    def _standalone_esxi_nfs_mount_state(self, context: dict[str, Any], vmware_plan: dict[str, Any]) -> dict[str, Any]:
        if str(vmware_plan.get("connection_mode") or "") != "standalone_esxi":
            return {"verifiable": False, "mounted": False, "reason": "not_standalone_esxi"}
        mount_step = next(
            (step for step in list(vmware_plan.get("steps") or []) if str(step.get("name") or "") == "plan_nfs_datastore_mounts"),
            {},
        )
        mount_plan = list(((mount_step.get("details") or {}).get("mount_plan")) or [])
        if not mount_plan:
            return {"verifiable": False, "mounted": False, "reason": "no_mount_plan"}
        datastore_name = str((mount_plan[0] or {}).get("datastore_name") or "").strip()
        try:
            _, nfs_out, _ = self._esxi_ssh_command(context, "esxcli storage nfs list")
            _, nfs41_out, _ = self._esxi_ssh_command(context, "esxcli storage nfs41 list")
        except Exception as exc:
            return {"verifiable": False, "mounted": False, "reason": str(exc)}
        for line in (nfs_out + "\n" + nfs41_out).splitlines():
            if datastore_name and datastore_name in line:
                lowered = line.lower()
                return {
                    "verifiable": True,
                    "mounted": "true" in lowered,
                    "line": line,
                }
        return {"verifiable": True, "mounted": False, "line": ""}

    def _api_surface_missing(self, exc: Exception) -> bool:
        message = str(exc or "").lower()
        return (
            "api not found" in message
            or ("failed (404)" in message)
            or ("code\": \"3\"" in message)
            or ("resource not found" in message and "/api/" in message)
        )

    def _dependency_missing(self, exc: Exception) -> bool:
        message = str(exc or "").lower()
        return (
            ("aggregate" in message and "not found" in message)
            or ("entry doesn't exist" in message and "aggregate" in message)
            or ("svm" in message and "not found" in message)
            or ("vserver" in message and "not found" in message)
        )

    def _execute_safe_apply(self, context: dict[str, Any], plan_payload: dict[str, Any]) -> dict[str, Any]:
        discovery = dict(plan_payload.get("discovery") or {})
        desired = self._profile_defaults(context)
        resolved_svm_name = self._resolved_svm_name(context, discovery)
        aggregate_targets = self._resolved_aggregate_targets(context, discovery)
        if resolved_svm_name:
            desired["svm_name"] = resolved_svm_name
        if aggregate_targets.get("resolved_01"):
            desired["aggregate_node_01"] = str(aggregate_targets.get("resolved_01"))
        if aggregate_targets.get("resolved_02"):
            desired["aggregate_node_02"] = str(aggregate_targets.get("resolved_02"))
        cfg = context.get("cfg") or {}
        naming = build_naming(str(((cfg.get("site") or {}).get("name") or "Kit-01")).strip())
        protocol = str(desired.get("storage_protocol") or "nfs").strip().lower()
        action_plan = list((((plan_payload.get("plan") or {}).get("protocol_profile") or {}).get("actions") or []))
        executable_statuses = {"create", "update"}
        logs: list[str] = []
        executed: list[str] = []
        skipped: list[str] = []
        blocked: list[str] = []
        client = self._build_client(context)
        protocol_profile = build_protocol_profile(cfg)
        vmware_plan = dict((((plan_payload.get("plan") or {}).get("vmware_plan")) or {}))
        supported_handlers = {
            "ensure_management_subnet",
            "ensure_iscsi_subnet",
            "ensure_svm",
            "ensure_svm_management_lif",
            "ensure_nfs_lifs",
            "ensure_iscsi_lifs",
            "ensure_nfs_service",
            "ensure_iscsi_service",
            "ensure_export_policy",
            "ensure_iscsi_igroup",
            "ensure_iscsi_portset",
            "ensure_nfs_volume",
            "ensure_esxi_nfs_datastore_mount",
        }

        def execute(name: str) -> bool:
            if name == "ensure_management_subnet":
                if "Management" in {str(item).strip() for item in list(discovery.get("subnets") or []) if str(item).strip()}:
                    logs.append("[SKIP] Management subnet already exists.")
                    skipped.append(name)
                    return False
                client_result = self._ensure_subnet(
                    client,
                    name="Management",
                    subnet_cidr=str(desired.get("management_subnet") or "").strip(),
                    gateway=str(desired.get("management_gateway") or "").strip(),
                    broadcast_domain=str((self._netapp_cfg(context).get("management_broadcast_domain") or "Default")).strip(),
                )
                logs.append(f"[APPLY] Created or verified Management subnet.")
                _ = client_result
                return True
            if name == "ensure_iscsi_subnet":
                iscsi = desired.get("iscsi") or {}
                if "iSCSI" in {str(item).strip() for item in list(discovery.get("subnets") or []) if str(item).strip()}:
                    logs.append("[SKIP] iSCSI subnet already exists.")
                    skipped.append(name)
                    return False
                ranges = [str(iscsi.get("ip_range") or "").strip()] if str(iscsi.get("ip_range") or "").strip() else []
                self._ensure_subnet(
                    client,
                    name="iSCSI",
                    subnet_cidr=str(iscsi.get("subnet_cidr") or iscsi.get("subnet") or "").strip(),
                    gateway=str(iscsi.get("gateway") or "").strip(),
                    broadcast_domain=str(desired.get("data_broadcast_domain") or "Data").strip(),
                    ip_ranges=ranges,
                )
                logs.append("[APPLY] Created or verified iSCSI subnet.")
                return True
            if name == "ensure_svm":
                self._create_svm(client, desired, naming)
                logs.append(f"[APPLY] Created SVM {desired.get('svm_name')}.")
                return True
            if name == "ensure_svm_management_lif":
                existing = next((item for item in list(discovery.get("lif_details") or []) if str(item.get("name") or "").strip() == str(desired.get("svm_mgmt_lif") or naming["svm_mgmt_lif"]).strip()), None)
                if existing:
                    raise NetAppError(f"SVM management LIF '{existing.get('name')}' already exists with different settings.")
                self._create_ip_interface(
                    client,
                    svm_name=str(desired.get("svm_name") or naming["svm_name"]).strip(),
                    lif_name=str(desired.get("svm_mgmt_lif") or naming["svm_mgmt_lif"]).strip(),
                    address=str(desired.get("svm_mgmt_ip") or "").strip(),
                    netmask=str(desired.get("management_netmask") or "255.255.255.0").strip(),
                    home_node=naming["node_01"],
                    home_port="e0M",
                    service_policy="default-management",
                )
                logs.append(f"[APPLY] Created SVM management LIF {desired.get('svm_mgmt_lif') or naming['svm_mgmt_lif']}.")
                return True
            if name == "ensure_nfs_lifs":
                created = False
                for lif in list((protocol_profile.get("nfs") or {}).get("lifs") or []):
                    existing = next((item for item in list(discovery.get("lif_details") or []) if str(item.get("name") or "").strip() == str((lif or {}).get("name") or "").strip()), None)
                    if existing:
                        actual_ip = str(existing.get("address") or "").strip()
                        desired_ip = str((lif or {}).get("ip") or "").strip()
                        if desired_ip and actual_ip == desired_ip:
                            continue
                        raise NetAppError(f"NFS LIF '{(lif or {}).get('name')}' already exists with different settings.")
                    self._create_ip_interface(
                        client,
                        svm_name=str(desired.get("svm_name") or naming["svm_name"]).strip(),
                        lif_name=str((lif or {}).get("name") or "").strip(),
                        address=str((lif or {}).get("ip") or "").strip(),
                        netmask=str(desired.get("management_netmask") or "255.255.255.0").strip(),
                        home_node=str((lif or {}).get("node") or "").strip(),
                        home_port=str((lif or {}).get("port") or "").strip(),
                        service_policy="default-data-files",
                    )
                    created = True
                logs.append("[APPLY] Created NFS data LIFs.")
                return created
            if name == "ensure_iscsi_lifs":
                created = False
                for lif in list((protocol_profile.get("iscsi") or {}).get("lifs") or []):
                    existing = next((item for item in list(discovery.get("lif_details") or []) if str(item.get("name") or "").strip() == str((lif or {}).get("name") or "").strip()), None)
                    if existing:
                        actual_ip = str(existing.get("address") or "").strip()
                        desired_ip = str((lif or {}).get("ip") or "").strip()
                        if desired_ip and actual_ip == desired_ip:
                            continue
                        raise NetAppError(f"iSCSI LIF '{(lif or {}).get('name')}' already exists with different settings.")
                    self._create_ip_interface(
                        client,
                        svm_name=str(desired.get("svm_name") or naming["svm_name"]).strip(),
                        lif_name=str((lif or {}).get("name") or "").strip(),
                        address=str((lif or {}).get("ip") or "").strip(),
                        netmask="255.255.255.0",
                        home_node=str((lif or {}).get("node") or "").strip(),
                        home_port=str((lif or {}).get("port") or "").strip(),
                        service_policy="default-data-blocks",
                    )
                    created = True
                logs.append("[APPLY] Created iSCSI data LIFs.")
                return created
            if name == "ensure_nfs_service":
                if self._find_protocol_service_record(discovery, "nfs", str(desired.get("svm_name") or naming["svm_name"]).strip()):
                    logs.append("[SKIP] NFS service already exists.")
                    skipped.append(name)
                    return False
                if "ensure_svm" not in executed:
                    self._enable_protocol_on_svm(client, discovery, desired, "nfs")
                self._ensure_nfs_service(client, svm_name=str(desired.get("svm_name") or naming["svm_name"]).strip())
                logs.append("[APPLY] Enabled or created NFS service.")
                return True
            if name == "ensure_iscsi_service":
                if self._find_protocol_service_record(discovery, "iscsi", str(desired.get("svm_name") or naming["svm_name"]).strip()):
                    logs.append("[SKIP] iSCSI service already exists.")
                    skipped.append(name)
                    return False
                if "ensure_svm" not in executed:
                    self._enable_protocol_on_svm(client, discovery, desired, "iscsi")
                self._ensure_iscsi_service(client, svm_name=str(desired.get("svm_name") or naming["svm_name"]).strip())
                logs.append("[APPLY] Enabled or created iSCSI service.")
                return True
            if name == "ensure_export_policy":
                policy_name = str(((desired.get("nfs") or {}).get("export_policy") or "")).strip()
                svm_name = str(desired.get("svm_name") or naming["svm_name"]).strip()
                desired_nfs_volume = str(((desired.get("nfs") or {}).get("volume") or "esxi_datastore_01")).strip()
                desired_allowed_subnet = str(((desired.get("nfs") or {}).get("allowed_subnet") or "")).strip()
                current_policies = list(((discovery.get("raw") or {}).get("export_policies") or []))
                policy = next((item for item in current_policies if str(item.get("name") or "").strip() == policy_name), None)
                changed = False
                if not policy:
                    self._ensure_export_policy(client, svm_name=svm_name, policy_name=policy_name)
                    changed = True
                    current_policies = client.get_export_policies()
                    policy = next((item for item in current_policies if str(item.get("name") or "").strip() == policy_name), None)
                allowed_clients = [desired_allowed_subnet] if desired_allowed_subnet else [f"{host}/32" for host in list((vmware_plan.get("esxi_hosts") or [])) if str(host).strip()]
                existing_matches = {
                    str((client_rule or {}).get("match") or "").strip()
                    for rule in list((policy or {}).get("rules") or [])
                    for client_rule in list((rule or {}).get("clients") or [])
                    if str((client_rule or {}).get("match") or "").strip()
                }
                policy_id = int((policy or {}).get("id") or 0)
                for match in allowed_clients:
                    if match and match not in existing_matches and policy_id:
                        self._ensure_export_policy_rule(client, policy_id=policy_id, client_match=match)
                        changed = True
                volume_record = self._find_volume_record(discovery, desired_nfs_volume)
                volume_uuid = str((volume_record or {}).get("uuid") or "").strip()
                current_volume_policy = str(((((volume_record or {}).get("nas") or {}).get("export_policy") or {}).get("name") or "")).strip()
                if volume_uuid and current_volume_policy != policy_name:
                    self._assign_volume_export_policy(client, volume_uuid=volume_uuid, policy_name=policy_name)
                    changed = True
                logs.append(f"[APPLY] {'Updated' if changed else 'Verified'} export policy {policy_name} and NFS volume policy binding.")
                return changed
            if name == "ensure_iscsi_igroup":
                igroup_name = str(((desired.get("iscsi") or {}).get("igroup") or naming["iscsi_igroup"])).strip()
                self._ensure_igroup(client, svm_name=str(desired.get("svm_name") or naming["svm_name"]).strip(), igroup_name=igroup_name)
                logs.append(f"[APPLY] Created iSCSI igroup {igroup_name}.")
                return True
            if name == "ensure_iscsi_portset":
                portset_name = str(((desired.get("iscsi") or {}).get("portset") or "iSCSI")).strip()
                self._ensure_portset(client, svm_name=str(desired.get("svm_name") or naming["svm_name"]).strip(), portset_name=portset_name)
                logs.append(f"[APPLY] Created iSCSI portset {portset_name}.")
                return True
            if name == "ensure_nfs_volume":
                nfs_cfg = desired.get("nfs") or {}
                volume_name = str(nfs_cfg.get("volume") or "esxi_datastore_01").strip()
                self._ensure_volume(
                    client,
                    svm_name=str(desired.get("svm_name") or naming["svm_name"]).strip(),
                    volume_name=volume_name,
                    aggregate_name=str(desired.get("aggregate_node_01") or "aggr_01").strip(),
                    size="500GB",
                    nas_path=str(nfs_cfg.get("mount_path") or f"/{volume_name}").strip(),
                )
                logs.append(f"[APPLY] Created NFS volume {volume_name}.")
                return True
            if name == "ensure_esxi_nfs_datastore_mount":
                result = self._ensure_standalone_esxi_nfs_datastore(context, vmware_plan)
                if str(result.get("status") or "") == "skipped":
                    skipped.append(name)
                    logs.append(f"[SKIP] {name} {str(result.get('reason') or 'skipped')}.")
                    return False
                logs.append("[APPLY] Mounted NFS datastore on standalone ESXi.")
                return True
            raise NetAppError(f"Action '{name}' is not yet implemented for safe apply.")

        for action in action_plan:
            name = str(action.get("name") or "").strip()
            status = str(action.get("status") or "").strip().lower()
            if not name:
                continue
            if status == "skip":
                skipped.append(name)
                logs.append(f"[SKIP] {name} already matches the desired state.")
                continue
            if status in {"manual", "warn"}:
                blocked.append(name)
                logs.append(f"[BLOCKED] {name} requires manual review or a safer handler before apply.")
                continue
            if status not in executable_statuses:
                blocked.append(name)
                logs.append(f"[BLOCKED] {name} has unsupported action status '{status}'.")
                continue
            if name not in supported_handlers:
                blocked.append(name)
                logs.append(f"[BLOCKED] {name} does not have a safe automation handler yet.")
                continue
            try:
                if execute(name):
                    executed.append(name)
            except NetAppError as exc:
                if self._api_surface_missing(exc):
                    blocked.append(name)
                    logs.append(f"[BLOCKED] {name} is not supported through the current ONTAP API surface: {exc}")
                    continue
                if self._dependency_missing(exc):
                    blocked.append(name)
                    logs.append(f"[BLOCKED] {name} depends on a missing ONTAP object: {exc}")
                    continue
                logs.append(f"[ERROR] {name}: {exc}")
                return {
                    "ok": False,
                    "result": "failed",
                    "execution_mode": "safe_apply",
                    "executed_actions": executed,
                    "skipped_actions": skipped,
                    "blocked_actions": blocked,
                    "failed_action": name,
                    "logs": logs,
                    "reason": str(exc),
                }

        return {
            "ok": True,
            "result": "completed" if executed else "no_changes",
            "execution_mode": "safe_apply",
            "executed_actions": executed,
            "skipped_actions": skipped,
            "blocked_actions": blocked,
            "logs": logs,
        }

    def _apply_stage(self, action_plan: list[dict[str, Any]], execution: dict[str, Any] | None = None) -> dict[str, Any]:
        if execution:
            return {
                "name": "NetApp Stage 4: Apply",
                "ok": bool(execution.get("ok")),
                "steps": [
                    "Execute safe changes through ONTAP API",
                    "Log every step",
                    "Skip anything already correct",
                    "Stop on destructive mismatch unless user explicitly confirms",
                ],
                "execution_mode": str(execution.get("execution_mode") or "safe_apply"),
                "result": str(execution.get("result") or "unknown"),
                "planned_actions": action_plan,
                "executed_actions": list(execution.get("executed_actions") or []),
                "skipped_actions": list(execution.get("skipped_actions") or []),
                "blocked_actions": list(execution.get("blocked_actions") or []),
                "failed_action": execution.get("failed_action"),
                "reason": execution.get("reason", ""),
                "required_confirmation": {
                    "explicit_user_confirm": True,
                    "block_on_destructive_mismatch": True,
                },
                "logs": list(execution.get("logs") or []),
            }
        return {
            "name": "NetApp Stage 4: Apply",
            "ok": False,
            "steps": [
                "Execute safe changes through ONTAP API",
                "Log every step",
                "Skip anything already correct",
                "Stop on destructive mismatch unless user explicitly confirms",
            ],
            "execution_mode": "dry_run_only",
            "result": "blocked",
            "reason": "Safe apply needs explicit confirmation and still blocks unsupported actions.",
            "planned_actions": action_plan,
            "required_confirmation": {
                "explicit_user_confirm": True,
                "block_on_destructive_mismatch": True,
            },
            "logs": ["[DRY-RUN] Apply stage defined but not executed."],
        }

    def discover(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._discover_stage(context)

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        discover_payload = self._discover_stage(context)
        payload = self._response(context, "validate")
        payload["ok"] = bool(discover_payload.get("ok"))
        payload["discovery"] = discover_payload.get("discovery") or {}
        payload["stages"] = list(discover_payload.get("stages") or [])
        warnings = list(discover_payload.get("warnings") or [])
        suggestions: list[str] = []
        if discover_payload.get("ok"):
            result = self._validate_stage(context, payload["discovery"])
            payload["stages"].append(result["stage"])
            payload["validation_checks"] = list(result["stage"].get("checks") or [])
            warnings.extend(list(result.get("warnings") or []))
            suggestions.extend(list(result.get("suggestions") or []))
            payload["ok"] = bool(result["stage"].get("ok"))
        if discover_payload.get("error"):
            payload["error"] = discover_payload.get("error")
        payload["warnings"] = list(dict.fromkeys(warnings))
        payload["suggestions"] = list(dict.fromkeys(suggestions))
        return payload

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        validate_payload = self.validate(context)
        payload = self._response(context, "plan")
        payload["ok"] = bool(validate_payload.get("ok"))
        if validate_payload.get("error"):
            payload["error"] = validate_payload.get("error")
        payload["discovery"] = validate_payload.get("discovery") or {}
        payload["warnings"] = list(validate_payload.get("warnings") or [])
        payload["suggestions"] = list(validate_payload.get("suggestions") or [])
        payload["stages"] = list(validate_payload.get("stages") or [])
        validate_stage = next((item for item in payload["stages"] if str(item.get("name")) == "NetApp Stage 2: Validate"), {})
        action_plan = self._build_action_plan(context, payload["discovery"], validate_stage)
        vmware_plan = build_vmware_plan(
            context.get("cfg") or {},
            storage_protocol=str(self._profile_defaults(context).get("storage_protocol") or "nfs"),
            discovery=payload["discovery"],
        )
        payload["stages"].append(action_plan["stage"])
        payload["plan"] = {
            "mode": "safe_apply_available",
            "storage_protocol": str(self._profile_defaults(context).get("storage_protocol") or "nfs"),
            "base_workflow": payload["stages"][:2],
            "adaptive_discovery": {
                "cluster_name": str((payload["discovery"] or {}).get("cluster_name") or ""),
                "ontap_version": str((payload["discovery"] or {}).get("ontap_version") or ""),
                "node_models": list((payload["discovery"] or {}).get("node_models") or []),
                "nodes": list((payload["discovery"] or {}).get("nodes") or []),
                "physical_ports": list((payload["discovery"] or {}).get("physical_ports") or []),
                "interface_groups": list((payload["discovery"] or {}).get("existing_interface_groups") or []),
                "broadcast_domains": list((payload["discovery"] or {}).get("existing_broadcast_domains") or []),
                "aggregates": list((payload["discovery"] or {}).get("aggregates") or []),
                "svm_protocols": list((payload["discovery"] or {}).get("enabled_protocols") or []),
                "export_policies": list((payload["discovery"] or {}).get("export_policy_details") or []),
                "igroups": list((payload["discovery"] or {}).get("igroup_details") or []),
                "portsets": list((payload["discovery"] or {}).get("portset_details") or []),
                "luns": list((payload["discovery"] or {}).get("lun_details") or []),
                "lun_maps": list((payload["discovery"] or {}).get("lun_map_details") or []),
                "capabilities": dict((payload["discovery"] or {}).get("capabilities") or {}),
                "capability_status": dict((payload["discovery"] or {}).get("capability_status") or {}),
                "upgrade_posture": self._build_upgrade_posture(self._profile_defaults(context), payload["discovery"] or {}),
                "capability_matrix": self._build_capability_matrix(self._profile_defaults(context), payload["discovery"] or {}),
            },
            "protocol_profile": {
                "selected_protocol": str(self._profile_defaults(context).get("storage_protocol") or "nfs"),
                "actions": list(action_plan.get("actions") or []),
                "command_preview": list(action_plan.get("command_preview") or []),
                "profile": build_protocol_profile(context.get("cfg") or {}),
            },
            "vmware_plan": vmware_plan,
            "proposed_writes": [],
        }
        payload["validation_checks"] = list(validate_payload.get("validation_checks") or [])
        return payload

    def preview(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.plan(context)

    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        payload = self.plan(context)
        payload["action"] = "apply"
        planned_actions = list((((payload.get("plan") or {}).get("protocol_profile") or {}).get("actions") or []))
        payload["job_id"] = str((job or {}).get("job_id") or "job-netapp-dryrun-001")
        payload["scope"] = str((job or {}).get("scope") or "netapp.apply")
        if not bool((job or {}).get("confirm")):
            apply_stage = self._apply_stage(planned_actions)
            payload["stages"].append(apply_stage)
            payload["apply"] = apply_stage
            payload["ok"] = False
            payload["result"] = "blocked"
            payload["error"] = "Safe apply requires explicit confirmation."
            return payload
        execution = self._execute_safe_apply(context, payload)
        apply_stage = self._apply_stage(planned_actions, execution)
        payload["stages"].append(apply_stage)
        payload["apply"] = apply_stage
        payload["ok"] = bool(execution.get("ok"))
        payload["result"] = str(execution.get("result") or "failed")
        if execution.get("reason") and not payload["ok"]:
            payload["error"] = str(execution.get("reason"))
        payload["warnings"] = list(payload.get("warnings") or [])
        if execution.get("blocked_actions"):
            payload["warnings"].append(
                "Some planned NetApp actions still require manual review or a future automation handler."
            )
        payload["warnings"] = list(dict.fromkeys(payload["warnings"]))
        return payload

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self._response(context, "status")
        payload["status"] = "safe_apply_available"
        payload["health"] = {
            "discovery": "available",
            "validation": "available",
            "planning": "available",
            "apply": "supported_safe_actions_only",
        }
        return payload

    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]:
        payload = self._response(context, "repair")
        payload["issue_id"] = str(issue_id)
        payload["resolution"] = "tracked"
        payload["details"] = {
            "attempted": "validation-only-repair",
            "next_step": "review warnings and regenerate plan",
        }
        return payload
