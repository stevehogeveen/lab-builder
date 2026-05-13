from __future__ import annotations

import copy
import ipaddress
import re
from typing import Any

from .models import KitConfigModel

DEFAULT_IP_OFFSETS = {
    "gateway": 1,
    "switch": 2,
    "esxi": 10,
    "ilo": 11,
    "windows": 20,
    "qnap": 30,
    "iosafe": 31,
    "netapp": 45,
}
NETAPP_MANAGEMENT_OFFSETS = {
    "netapp_sp_a": 13,
    "netapp_sp_b": 14,
    "netapp_cluster_mgmt": 45,
    "netapp_node_01_mgmt": 46,
    "netapp_node_02_mgmt": 47,
    "netapp_svm_mgmt": 48,
    "cluster_mgmt_ip": 45,
    "node_01_mgmt_ip": 46,
    "node_02_mgmt_ip": 47,
    "svm_mgmt_ip": 48,
    "autosupport_mailhost": 63,
}
DEFAULT_KIT_NAME = "Kit-01"


def sanitize_kit_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return DEFAULT_KIT_NAME
    name = re.sub(r"[^\w\- ]+", "", name)
    name = name.replace(" ", "-")
    return name or DEFAULT_KIT_NAME


def normalize_ilo_hostname(value: str) -> str:
    hostname = str(value or "").strip()
    if not hostname:
        return ""
    hostname = re.sub(r"[^A-Za-z0-9\-]+", "-", hostname)
    hostname = re.sub(r"-{2,}", "-", hostname).strip("-")
    return hostname[:63]


def has_non_printable_chars(value: str) -> bool:
    return any((not ch.isprintable()) or ch in "\r\n\t" for ch in str(value or ""))


def count_password_classes(value: str) -> int:
    text = str(value or "")
    classes = 0
    if any(ch.islower() for ch in text):
        classes += 1
    if any(ch.isupper() for ch in text):
        classes += 1
    if any(ch.isdigit() for ch in text):
        classes += 1
    if any(not ch.isalnum() for ch in text):
        classes += 1
    return classes


def validate_ilo_login_name(value: str, *, label: str, required: bool = True) -> list[str]:
    username = str(value or "").strip()
    if not username:
        return [f"{label} is required."] if required else []
    errors: list[str] = []
    if len(username) > 39:
        errors.append(f"{label} must be 39 characters or less.")
    if has_non_printable_chars(username):
        errors.append(f"{label} must use printable characters only.")
    if re.search(r"\s", username):
        errors.append(f"{label} cannot contain spaces.")
    return errors


def validate_ilo_password(value: str, *, username: str = "", label: str, required: bool = True) -> dict[str, list[str]]:
    password = str(value or "")
    errors: list[str] = []
    notes: list[str] = []
    if not password:
        if required:
            errors.append(f"{label} is required.")
        return {"errors": errors, "notes": notes}
    if len(password) > 39:
        errors.append(f"{label} must be 39 characters or less.")
    if has_non_printable_chars(password):
        errors.append(f"{label} must use printable characters only.")
    if len(password) < 8:
        notes.append(f"{label} is under 8 characters. Many iLO policies use a minimum of 8.")
    if count_password_classes(password) < 3:
        notes.append(f"{label} does not use 3 character types. iLO complexity policy may reject it.")
    if username and username.lower() in password.lower():
        notes.append(f"{label} contains the user name. HPE recommends avoiding that.")
    return {"errors": errors, "notes": notes}


def validate_snmpv3_username(value: str, *, label: str) -> list[str]:
    username = str(value or "").strip()
    if not username:
        return [f"{label} is required."]
    errors: list[str] = []
    if len(username) > 32:
        errors.append(f"{label} must be 32 characters or less.")
    if has_non_printable_chars(username):
        errors.append(f"{label} must use printable characters only.")
    if re.search(r"\s", username):
        errors.append(f"{label} cannot contain spaces.")
    return errors


def validate_snmpv3_password(value: str, *, label: str, required: bool = True) -> list[str]:
    password = str(value or "")
    if not password:
        return [f"{label} is required."] if required else []
    errors: list[str] = []
    if has_non_printable_chars(password):
        errors.append(f"{label} must use printable characters only.")
    if len(password) < 8:
        errors.append(f"{label} must be at least 8 characters.")
    return errors


def default_config() -> dict[str, Any]:
    return KitConfigModel(
        ip_plan={
            "gateway": "10.10.8.1",
            "switch": "10.10.8.2",
            "esxi": "10.10.8.10",
            "ilo": "10.10.8.11",
            "windows": "10.10.8.20",
            "qnap": "10.10.8.30",
            "iosafe": "10.10.8.31",
            "netapp": "10.10.8.45",
        },
        included={
            "ilo": True,
            "esxi": True,
            "windows": False,
            "qnap": False,
            "netapp": False,
            "vmware": False,
            "iosafe": False,
            "cisco_switch": False,
            "storage": False,
        },
        section_completion={
            "basics": False,
            "network": False,
            "included": False,
            "credentials": False,
        },
        windows={
            "vm_name": "win2022-01",
            "admin_password": "",
            "ip_address": "",
            "subnet_mask": "255.255.255.0",
            "gateway": "",
            "dns_servers": [],
            "source_image_path": "",
            "source_image_name": "",
            "source_image_kind": "",
            "vsphere_host": "",
            "vsphere_username": "",
            "vsphere_password": "",
            "vsphere_datacenter": "",
            "vsphere_datastore": "",
            "vsphere_network": "",
            "vsphere_folder": "",
            "vsphere_resource_pool": "",
            "winrm_username": "Administrator",
            "winrm_password": "",
            "winrm_port": 5986,
            "winrm_use_https": True,
            "last_vsphere_probe": {},
            "last_winrm_probe": {},
            "install_plan": {},
        },
        qnap={"hostname": "qnap01", "ip": "", "username": "admin", "password": ""},
        netapp={
            "host": "",
            "username": "admin",
            "password": "",
            "storage_protocol": "iscsi",
            "cluster_name": "",
            "svm_name": "",
            "data_broadcast_domain": "Data",
            "management_broadcast_domain": "Default",
            "mtu": 9000,
            "aggregate_node_01": "aggr_01",
            "aggregate_node_02": "aggr_02",
            "svm_root_aggregate": "aggr_01",
            "preferred_data_ports": [],
            "auto_detect_ports": True,
            "autosupport": {
                "enabled": True,
                "from": "<KitId>-NetApp",
                "to": "<KitId>Alert.Reporting",
                "mailhost": "",
                "transport": "smtp",
                "support_enabled": False,
            },
            "management": {
                "cluster_mgmt_ip": "",
                "node_01_mgmt_ip": "",
                "node_02_mgmt_ip": "",
                "svm_mgmt_ip": "",
            },
            "bootstrap_complete": False,
            "bootstrap_checks": {},
            "iscsi": {
                "subnet": "192.168.1.0/24",
                "gateway": "192.168.1.1",
                "ip_range": "192.168.1.11-192.168.1.60",
                "portset_name": "iSCSI",
                "igroup_name": "",
                "lifs": [],
                "volumes": [],
            },
            "nfs": {
                "export_policy": "",
                "allowed_subnet": "",
                "lifs": [],
                "volumes": [],
            },
            "command_templates": {
                "iscsi": "",
                "nfs": "",
            },
            "desired": {},
            "discovery": {},
            "validation": {},
        },
        vmware={
            "vcenter_ip": "",
            "username": "vsphere.local\\administrator",
            "password": "",
            "datacenter_name": "",
            "cluster_name": "",
            "esxi_host_start_offset": 31,
            "esxi_host_end_offset": 39,
            "esxi_root_user": "root",
            "esxi_root_password": "",
            "vcenter_vm_name_match": "SVCNTR",
            "ha_enabled": True,
            "ha_isolation_response": "Shutdown",
            "drs_enabled": False,
            "startup_policy_enabled": True,
            "iscsi": {
                "rescan_hba": True,
                "rescan_vmfs": True,
                "multipath_policy": "RoundRobin",
            },
            "nfs": {
                "datastore_name": "",
                "nfs_version": "4.1",
            },
        },
        iosafe={"hostname": "iosafe01", "ip": "", "username": "admin", "password": ""},
        cisco_switch={"hostname": "sw01", "ip": "", "username": "admin", "password": ""},
    ).model_dump()


def normalize_ilo_additional_users(entries: list[dict[str, Any]] | Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(entries, list):
        return normalized
    for item in entries:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        password = str(item.get("password") or "")
        role = str(item.get("role") or "Administrator").strip() or "Administrator"
        if not username or not password:
            continue
        normalized.append({"username": username, "password": password, "role": role})
    return normalized


def standard_ilo_policy_defaults() -> dict[str, Any]:
    return copy.deepcopy(default_config()["ilo"]["policy"])


def normalize_ilo_policy(policy: dict[str, Any] | Any) -> dict[str, Any]:
    normalized = standard_ilo_policy_defaults()
    if isinstance(policy, dict):
        normalized.update(policy)
    for key in (
        "discover_enabled", "apply_standard_policy", "enable_standard_accounts", "enable_license_check",
        "enable_snmp_policy", "enable_alert_destinations", "enable_ipv6_disable", "enable_time_policy", "enable_auto_reset",
    ):
        value = normalized.get(key)
        normalized[key] = value.strip().lower() not in {"0", "false", "no", "off", ""} if isinstance(value, str) else bool(value)
    try:
        start_octet = int(normalized.get("discover_start_octet") or 21)
    except (TypeError, ValueError):
        start_octet = 21
    try:
        end_octet = int(normalized.get("discover_end_octet") or 29)
    except (TypeError, ValueError):
        end_octet = 29
    start_octet = max(1, min(start_octet, 254))
    end_octet = max(1, min(end_octet, 254))
    if start_octet > end_octet:
        start_octet, end_octet = end_octet, start_octet
    normalized["discover_start_octet"] = start_octet
    normalized["discover_end_octet"] = end_octet
    normalized["alert_destinations"] = [str(item).strip() for item in list(normalized.get("alert_destinations") or []) if str(item or "").strip()]
    discovered_hosts = []
    for item in list(normalized.get("discovered_hosts") or []):
        if not isinstance(item, dict):
            continue
        host = str(item.get("host") or "").strip()
        if not host:
            continue
        discovered_hosts.append({
            "host": host,
            "reachable": bool(item.get("reachable")),
            "latency_ms": item.get("latency_ms", ""),
            "error": str(item.get("error") or "").strip(),
        })
    normalized["discovered_hosts"] = discovered_hosts
    return normalized


def standard_ilo_policy_kit_id(cfg: dict[str, Any]) -> str:
    return sanitize_kit_name(str((cfg.get("site") or {}).get("name") or "KIT")).upper()


def build_policy_ilo_username(kit_id: str, suffix: str) -> str:
    raw = f"{str(kit_id or '').strip()}_{suffix}"
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "", raw)
    if len(normalized) <= 39:
        return normalized
    keep = max(1, 39 - len(suffix) - 1)
    return f"{normalized[:keep]}_{suffix}"


def standard_ilo_policy_accounts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    policy = normalize_ilo_policy((cfg.get("ilo") or {}).get("policy"))
    kit_id = standard_ilo_policy_kit_id(cfg)
    oa_privileges = {
        "LoginPriv": True,
        "RemoteConsolePriv": True,
        "VirtualPowerAndResetPriv": True,
        "UserConfigPriv": False,
        "iLOConfigPriv": False,
        "VirtualMediaPriv": False,
        "HostNICConfigPriv": False,
        "HostBIOSConfigPriv": False,
        "HostStorageConfigPriv": False,
        "SystemRecoveryConfigPriv": False,
    }
    accounts = [
        {"username": build_policy_ilo_username(kit_id, "Admin"), "password": str(policy.get("kit_admin_password") or ""), "role": "Administrator"},
        {"username": build_policy_ilo_username(kit_id, "OA"), "password": str(policy.get("kit_operator_password") or ""), "role": "Operator", "privileges": oa_privileges},
        {"username": str(policy.get("shared_admin_username") or "765CS").strip() or "765CS", "password": str(policy.get("shared_admin_password") or ""), "role": "Administrator"},
    ]
    return [item for item in accounts if str(item.get("username") or "").strip() and str(item.get("password") or "")]


def build_standard_ilo_policy(cfg: dict[str, Any]) -> dict[str, Any]:
    policy = normalize_ilo_policy((cfg.get("ilo") or {}).get("policy"))
    kit_id = standard_ilo_policy_kit_id(cfg)
    shared_snmp = dict((cfg.get("shared_snmp") or {}))
    configured_v3_username = str(policy.get("snmpv3_username") or "").strip()
    shared_v3_username = str(shared_snmp.get("v3_username") or "").strip()
    v3_username = shared_v3_username if configured_v3_username in {"", "765CS"} and shared_v3_username else (configured_v3_username or "765CS")
    v3_auth_password = str(policy.get("snmpv3_auth_password") or shared_snmp.get("v3_auth_password") or "")
    v3_priv_password = str(policy.get("snmpv3_priv_password") or shared_snmp.get("v3_priv_password") or "")
    return {
        "kit_id": kit_id,
        "settings": policy,
        "accounts": standard_ilo_policy_accounts(cfg),
        "snmp": {
            "system_contact": str(policy.get("snmp_system_contact") or "765 DSS"),
            "system_location": kit_id if str(policy.get("snmp_location_source") or "kit_id") == "kit_id" else str(policy.get("snmp_system_location") or kit_id),
            "system_role": str(policy.get("snmp_system_role") or "iLO"),
            "read_community": str(policy.get("snmp_read_community") or shared_snmp.get("read_community") or ""),
            "v3_username": v3_username,
            "v3_auth_protocol": str(policy.get("snmpv3_auth_protocol") or "SHA").strip() or "SHA",
            "v3_auth_password": v3_auth_password,
            "v3_priv_protocol": str(policy.get("snmpv3_priv_protocol") or "AES").strip() or "AES",
            "v3_priv_password": v3_priv_password,
            "alert_destinations": list(policy.get("alert_destinations") or []),
            "alert_protocol": str(policy.get("alert_protocol") or "SNMPv3Inform"),
        },
        "time": {
            "server": str(((cfg.get("ilo") or {}).get("gateway") or (cfg.get("ip_plan") or {}).get("gateway") or "")).strip(),
            "timezone": str(policy.get("timezone") or "Bogota, Lima, Quito, Eastern Time(US & Canada)"),
        },
    }


def policy_enabled(cfg: dict[str, Any], key: str) -> bool:
    policy = normalize_ilo_policy((cfg.get("ilo") or {}).get("policy"))
    if key == "discover_enabled":
        return bool(policy.get("discover_enabled"))
    return bool(policy.get("apply_standard_policy")) and bool(policy.get(key))


def build_ilo_discovery_targets(cfg: dict[str, Any]) -> list[str]:
    policy = normalize_ilo_policy((cfg.get("ilo") or {}).get("policy"))
    if not policy.get("discover_enabled"):
        return []
    subnet = str((cfg.get("shared_network") or {}).get("subnet") or "").strip()
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except Exception:
        return []
    if network.version != 4:
        return []
    start_octet = int(policy.get("discover_start_octet") or 21)
    end_octet = int(policy.get("discover_end_octet") or 29)
    targets: list[str] = []
    for octet in range(start_octet, end_octet + 1):
        candidate = f"{network.network_address.exploded.rsplit('.', 1)[0]}.{octet}"
        try:
            if ipaddress.ip_address(candidate) in network:
                targets.append(candidate)
        except Exception:
            continue
    return targets


def normalize_snmp_users(entries: list[dict[str, Any]] | Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(entries, list):
        return normalized
    for item in entries:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        auth_protocol = str(item.get("auth_protocol") or "SHA").strip() or "SHA"
        auth_password = str(item.get("auth_password") or "")
        priv_protocol = str(item.get("priv_protocol") or "AES").strip() or "AES"
        priv_password = str(item.get("priv_password") or "")
        if not username:
            continue
        normalized.append({
            "username": username,
            "auth_protocol": auth_protocol,
            "auth_password": auth_password,
            "priv_protocol": priv_protocol,
            "priv_password": priv_password,
        })
    return normalized


def extract_ilo_additional_users_from_form(form: Any) -> list[dict[str, str]]:
    usernames = form.getlist("ilo_extra_username")
    passwords = form.getlist("ilo_extra_password")
    roles = form.getlist("ilo_extra_role")
    entries: list[dict[str, str]] = []
    for index, username in enumerate(usernames):
        entries.append({
            "username": username,
            "password": passwords[index] if index < len(passwords) else "",
            "role": roles[index] if index < len(roles) else "Administrator",
        })
    return normalize_ilo_additional_users(entries)


def extract_snmp_users_from_form(form: Any, *, primary_username: str, primary_auth_protocol: str, primary_auth_password: str, primary_priv_protocol: str, primary_priv_password: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if str(primary_username or "").strip():
        entries.append({
            "username": primary_username,
            "auth_protocol": primary_auth_protocol,
            "auth_password": primary_auth_password,
            "priv_protocol": primary_priv_protocol,
            "priv_password": primary_priv_password,
        })
    usernames = form.getlist("snmp_extra_username")
    auth_protocols = form.getlist("snmp_extra_auth_protocol")
    auth_passwords = form.getlist("snmp_extra_auth_password")
    priv_protocols = form.getlist("snmp_extra_priv_protocol")
    priv_passwords = form.getlist("snmp_extra_priv_password")
    for index, username in enumerate(usernames):
        entries.append({
            "username": username,
            "auth_protocol": auth_protocols[index] if index < len(auth_protocols) else "SHA",
            "auth_password": auth_passwords[index] if index < len(auth_passwords) else "",
            "priv_protocol": priv_protocols[index] if index < len(priv_protocols) else "AES",
            "priv_password": priv_passwords[index] if index < len(priv_passwords) else "",
        })
    return normalize_snmp_users(entries)


def normalize_ilo_config(cfg: dict[str, Any]) -> dict[str, Any]:
    ilo_cfg = cfg.setdefault("ilo", {})
    snmp_cfg = cfg.setdefault("shared_snmp", {})
    legacy_host = (ilo_cfg.get("host") or "").strip()
    current_ip = (ilo_cfg.get("current_ip") or legacy_host or "").strip()
    target_ip = (ilo_cfg.get("target_ip") or "").strip()
    subnet_mask = (ilo_cfg.get("subnet_mask") or cfg.get("ip_plan", {}).get("netmask") or "").strip()
    gateway = (ilo_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip()
    dns_servers = ilo_cfg.get("dns_servers")
    if not target_ip:
        target_ip = (cfg.get("ip_plan", {}).get("ilo") or current_ip or legacy_host).strip()
    if not current_ip:
        current_ip = target_ip
    if not isinstance(dns_servers, list):
        dns_servers = cfg.get("shared_network", {}).get("dns_servers", [])
    normalized_dns = [str(x).strip() for x in dns_servers[:4]]
    while len(normalized_dns) < 4:
        normalized_dns.append("")
    ilo_cfg["current_ip"] = current_ip
    ilo_cfg["target_ip"] = target_ip
    ilo_cfg["subnet_mask"] = subnet_mask
    ilo_cfg["gateway"] = gateway
    ilo_cfg["dns_servers"] = normalized_dns
    ilo_cfg["host"] = current_ip
    ilo_cfg["additional_users"] = normalize_ilo_additional_users(ilo_cfg.get("additional_users", []))
    ilo_cfg["policy"] = normalize_ilo_policy(ilo_cfg.get("policy"))
    normalized_snmp_users = normalize_snmp_users(snmp_cfg.get("users", []))
    if not normalized_snmp_users:
        primary_snmp_username = str(snmp_cfg.get("v3_username") or "").strip()
        if primary_snmp_username:
            normalized_snmp_users = [{
                "username": primary_snmp_username,
                "auth_protocol": str(snmp_cfg.get("v3_auth_protocol") or "SHA").strip() or "SHA",
                "auth_password": str(snmp_cfg.get("v3_auth_password") or ""),
                "priv_protocol": str(snmp_cfg.get("v3_priv_protocol") or "AES").strip() or "AES",
                "priv_password": str(snmp_cfg.get("v3_priv_password") or ""),
            }]
    snmp_cfg["users"] = normalized_snmp_users
    return cfg


def merge_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    base = default_config()
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return normalize_ilo_config(base)


def subnet_details(subnet: str) -> dict[str, Any]:
    network = ipaddress.ip_network(subnet, strict=False)
    total = network.num_addresses
    if total >= 2:
        first_usable = network.network_address + 1
        last_usable = network.broadcast_address - 1
        max_usable_offset = total - 2
    else:
        first_usable = network.network_address
        last_usable = network.broadcast_address
        max_usable_offset = 0
    return {
        "subnet": str(network),
        "network_address": str(network.network_address),
        "broadcast_address": str(network.broadcast_address),
        "netmask": str(network.netmask),
        "prefixlen": network.prefixlen,
        "total_addresses": total,
        "first_usable": str(first_usable),
        "last_usable": str(last_usable),
        "max_usable_offset": max_usable_offset,
    }


def ip_at_offset(network_cidr: str, offset: int, require_usable: bool = True) -> str:
    network = ipaddress.ip_network(network_cidr, strict=False)
    if int(offset) < 0:
        raise ValueError(f"Offset {offset} cannot be negative")
    candidate = network.network_address + int(offset)
    if candidate not in network:
        raise ValueError(f"Offset {offset} is outside subnet {network_cidr}")
    if require_usable:
        if candidate == network.network_address:
            raise ValueError(f"Offset {offset} resolves to network address {candidate}")
        if candidate == network.broadcast_address:
            raise ValueError(f"Offset {offset} resolves to broadcast address {candidate}")
    return str(candidate)


def build_default_ip_plan(subnet: str) -> dict[str, Any]:
    return {key: ip_at_offset(subnet, offset) for key, offset in DEFAULT_IP_OFFSETS.items()}


def validate_ip_for_subnet(network_cidr: str, value: str, label: str) -> str:
    try:
        address = ipaddress.ip_address((value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid IP address") from exc
    network = ipaddress.ip_network(network_cidr, strict=False)
    if address not in network:
        raise ValueError(f"{label} must be inside subnet {network_cidr}")
    if address == network.network_address:
        raise ValueError(f"{label} cannot be the network address")
    if address == network.broadcast_address:
        raise ValueError(f"{label} cannot be the broadcast address")
    return str(address)


def build_legacy_offset_plan(cfg: dict[str, Any], subnet: str) -> dict[str, Any]:
    shared_network = cfg.get("shared_network", {})
    return {
        key: ip_at_offset(subnet, int(shared_network.get(f"{key}_offset", offset)))
        for key, offset in DEFAULT_IP_OFFSETS.items()
    }


def normalize_ip_plan(cfg: dict[str, Any], subnet: str) -> dict[str, Any]:
    raw_plan = cfg.get("ip_plan") or {}
    if all(raw_plan.get(key) for key in DEFAULT_IP_OFFSETS):
        plan_source = raw_plan
    else:
        plan_source = build_legacy_offset_plan(cfg, subnet)
    plan = {
        key: validate_ip_for_subnet(subnet, plan_source.get(key, ""), key.replace("_", " ").upper())
        for key in DEFAULT_IP_OFFSETS
    }
    ip_owners: dict[str, list[str]] = {}
    for key, value in plan.items():
        ip_owners.setdefault(value, []).append(key.replace("_", " "))
    duplicates = [
        f"{ip} ({', '.join(labels)})"
        for ip, labels in ip_owners.items()
        if len(labels) > 1
    ]
    if duplicates:
        raise ValueError("Each device IP must be unique within the kit. Duplicate: " + "; ".join(duplicates))
    return plan


def calc_ip_plan(cfg: dict[str, Any]) -> dict[str, Any]:
    shared_network = cfg.get("shared_network", {})
    netapp_cfg = cfg.get("netapp", {}) or {}
    bootstrap_overrides = (netapp_cfg.get("bootstrap_overrides") or {}) if isinstance(netapp_cfg, dict) else {}
    subnet = shared_network.get("subnet", "10.10.8.0/24")
    details = subnet_details(subnet)
    plan = normalize_ip_plan(cfg, subnet)
    netapp_offsets = {
        "netapp_sp_a": int(shared_network.get("netapp_sp_a_offset", 13) or 13),
        "netapp_sp_b": int(shared_network.get("netapp_sp_b_offset", 14) or 14),
        "netapp_cluster_mgmt": int(shared_network.get("netapp_cluster_mgmt_offset", 45) or 45),
        "netapp_node_01_mgmt": int(shared_network.get("netapp_node_01_mgmt_offset", 46) or 46),
        "netapp_node_02_mgmt": int(shared_network.get("netapp_node_02_mgmt_offset", 47) or 47),
        "netapp_svm_mgmt": int(shared_network.get("netapp_svm_mgmt_offset", 48) or 48),
        "autosupport_mailhost": 63,
    }
    netapp_mgmt: dict[str, str] = {}
    for key, offset in netapp_offsets.items():
        candidate = str(bootstrap_overrides.get(key) or "").strip() or ip_at_offset(subnet, offset)
        netapp_mgmt[key] = validate_ip_for_subnet(subnet, candidate, key.replace("_", " ").upper())
    netapp_aliases = {
        "cluster_mgmt_ip": netapp_mgmt["netapp_cluster_mgmt"],
        "node_01_mgmt_ip": netapp_mgmt["netapp_node_01_mgmt"],
        "node_02_mgmt_ip": netapp_mgmt["netapp_node_02_mgmt"],
        "svm_mgmt_ip": netapp_mgmt["netapp_svm_mgmt"],
    }
    ip_owners: dict[str, list[str]] = {}
    uniqueness_map = {**plan, **netapp_mgmt}
    # `ip_plan.netapp` is the same cluster-management endpoint surfaced elsewhere as
    # `netapp_cluster_mgmt`/`cluster_mgmt_ip`; do not treat those aliases as conflicts.
    uniqueness_map.pop("netapp", None)
    for key, value in uniqueness_map.items():
        ip_owners.setdefault(value, []).append(key)
    duplicates = {ip: labels for ip, labels in ip_owners.items() if len(labels) > 1}
    if duplicates:
        rendered = "; ".join(f"{ip} ({', '.join(labels)})" for ip, labels in duplicates.items())
        raise ValueError("Each device and NetApp bootstrap IP must be unique within the kit. Duplicate: " + rendered)
    return {
        "subnet": details["subnet"],
        "netmask": details["netmask"],
        "prefixlen": details["prefixlen"],
        "first_usable": details["first_usable"],
        "last_usable": details["last_usable"],
        "broadcast": details["broadcast_address"],
        "max_usable_offset": details["max_usable_offset"],
        **plan,
        **netapp_mgmt,
        **netapp_aliases,
    }


def apply_ip_plan(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = merge_defaults(cfg)
    plan = calc_ip_plan(cfg)
    shared_dns = [x for x in cfg.get("shared_network", {}).get("dns_servers", []) if x]
    cfg["ip_plan"] = plan
    cfg["ilo"]["target_ip"] = plan["ilo"]
    cfg["ilo"]["current_ip"] = (cfg["ilo"].get("current_ip") or cfg["ilo"].get("host") or plan["ilo"]).strip()
    cfg["ilo"]["subnet_mask"] = (cfg["ilo"].get("subnet_mask") or plan["netmask"]).strip()
    cfg["ilo"]["gateway"] = (cfg["ilo"].get("gateway") or plan["gateway"]).strip()
    ilo_dns = cfg["ilo"].get("dns_servers", [])
    cfg["ilo"]["dns_servers"] = ilo_dns if any(x and str(x).strip() for x in ilo_dns) else cfg.get("shared_network", {}).get("dns_servers", ["", "", "", ""])[:4]
    cfg["ilo"]["host"] = cfg["ilo"]["current_ip"]
    cfg["esxi"]["management_ip"] = plan["esxi"]
    cfg["esxi"]["gateway"] = plan["gateway"]
    cfg["esxi"]["subnet_mask"] = plan["netmask"]
    cfg["esxi"]["dns_servers"] = shared_dns if shared_dns else [plan["gateway"]]
    cfg["windows"]["ip_address"] = plan["windows"]
    cfg["windows"]["gateway"] = plan["gateway"]
    cfg["windows"]["subnet_mask"] = plan["netmask"]
    cfg["windows"]["dns_servers"] = shared_dns if shared_dns else [plan["gateway"]]
    cfg["qnap"]["ip"] = plan["qnap"]
    cfg["iosafe"]["ip"] = plan["iosafe"]
    cfg["cisco_switch"]["ip"] = plan["switch"]
    cfg.setdefault("netapp", {})
    cfg["netapp"]["host"] = str(cfg["netapp"].get("host") or plan["cluster_mgmt_ip"]).strip()
    cfg["netapp"].setdefault("management", {})
    cfg["netapp"]["management"].update(
        {
            "cluster_mgmt_ip": plan["netapp_cluster_mgmt"],
            "node_01_mgmt_ip": plan["netapp_node_01_mgmt"],
            "node_02_mgmt_ip": plan["netapp_node_02_mgmt"],
            "svm_mgmt_ip": plan["netapp_svm_mgmt"],
        }
    )
    cfg["netapp"].setdefault("autosupport", {})
    cfg["netapp"]["autosupport"]["mailhost"] = str(cfg["netapp"]["autosupport"].get("mailhost") or plan["autosupport_mailhost"]).strip()
    cfg["netapp"].setdefault("bootstrap_checks", {})
    cfg.setdefault("vmware", {})
    start_offset = int(cfg["vmware"].get("esxi_host_start_offset") or 31)
    end_offset = int(cfg["vmware"].get("esxi_host_end_offset") or 39)
    if start_offset > end_offset:
        start_offset, end_offset = end_offset, start_offset
    cfg["vmware"]["discovered_host_ips"] = [ip_at_offset(plan["subnet"], offset) for offset in range(start_offset, end_offset + 1)]
    cfg["vmware"]["datacenter_name"] = str(cfg["vmware"].get("datacenter_name") or cfg.get("site", {}).get("name") or "").strip()
    cfg["vmware"]["cluster_name"] = str(cfg["vmware"].get("cluster_name") or f"{cfg.get('site', {}).get('name', DEFAULT_KIT_NAME)}-Cluster").strip()
    return cfg


def build_ilo_input_review(cfg: dict[str, Any], *, include_policy_validation: bool = False) -> dict[str, Any]:
    ilo_cfg = cfg.get("ilo", {}) or {}
    policy = normalize_ilo_policy(ilo_cfg.get("policy"))
    errors: list[str] = []
    notes: list[str] = []
    errors.extend(validate_ilo_login_name(ilo_cfg.get("username", ""), label="iLO username"))
    password_check = validate_ilo_password(ilo_cfg.get("password", ""), username=str(ilo_cfg.get("username") or ""), label="iLO password")
    errors.extend(password_check["errors"])
    notes.extend(password_check["notes"])
    for index, item in enumerate(normalize_ilo_additional_users(ilo_cfg.get("additional_users", [])), start=1):
        prefix = f"Extra iLO user {index}"
        errors.extend(validate_ilo_login_name(item.get("username", ""), label=f"{prefix} username"))
        extra_check = validate_ilo_password(item.get("password", ""), username=str(item.get("username") or ""), label=f"{prefix} password")
        errors.extend(extra_check["errors"])
        notes.extend(extra_check["notes"])
    if include_policy_validation and policy.get("apply_standard_policy") and policy.get("enable_standard_accounts"):
        for item in standard_ilo_policy_accounts(cfg):
            username = str(item.get("username") or "").strip()
            password = str(item.get("password") or "")
            errors.extend(validate_ilo_login_name(username, label=f"Policy iLO user {username}"))
            if not password:
                errors.append(f"Policy iLO user {username} password is required.")
                continue
            extra_check = validate_ilo_password(password, username=username, label=f"Policy iLO user {username} password")
            errors.extend(extra_check["errors"])
            notes.extend(extra_check["notes"])
    if include_policy_validation and policy.get("apply_standard_policy") and policy.get("enable_snmp_policy"):
        snmp_username = str(policy.get("snmpv3_username") or "765CS").strip()
        errors.extend(validate_snmpv3_username(snmp_username, label="Policy SNMPv3 user"))
        errors.extend(validate_snmpv3_password(str(policy.get("snmpv3_auth_password") or ""), label="Policy SNMPv3 auth password"))
        errors.extend(validate_snmpv3_password(str(policy.get("snmpv3_priv_password") or ""), label="Policy SNMPv3 privacy password"))
    return {"errors": errors, "notes": notes}


def build_snmp_input_review(cfg: dict[str, Any]) -> dict[str, Any]:
    snmp_cfg = cfg.get("shared_snmp", {}) or {}
    errors: list[str] = []
    notes: list[str] = []
    users = normalize_snmp_users(snmp_cfg.get("users", []))
    primary_username = str(snmp_cfg.get("v3_username") or "").strip()
    primary_auth_password = str(snmp_cfg.get("v3_auth_password") or "")
    primary_priv_password = str(snmp_cfg.get("v3_priv_password") or "")
    if primary_username or primary_auth_password or primary_priv_password:
        errors.extend(validate_snmpv3_username(primary_username, label="SNMPv3 user"))
        errors.extend(validate_snmpv3_password(primary_auth_password, label="SNMPv3 auth password"))
        errors.extend(validate_snmpv3_password(primary_priv_password, label="SNMPv3 privacy password"))
    for index, item in enumerate(users[1:] if users else [], start=1):
        prefix = f"Additional SNMPv3 user {index}"
        errors.extend(validate_snmpv3_username(item.get("username", ""), label=f"{prefix}"))
        errors.extend(validate_snmpv3_password(item.get("auth_password", ""), label=f"{prefix} auth password"))
        errors.extend(validate_snmpv3_password(item.get("priv_password", ""), label=f"{prefix} privacy password"))
    if primary_username and not users:
        notes.append("The primary SNMPv3 user is saved, but the normalized user list is empty.")
    return {"errors": errors, "notes": notes}


def build_ilo_field_errors(cfg: dict[str, Any]) -> dict[str, Any]:
    ilo_cfg = cfg.get("ilo", {}) or {}
    main_username_errors = validate_ilo_login_name(ilo_cfg.get("username", ""), label="iLO username")
    main_password_check = validate_ilo_password(
        ilo_cfg.get("password", ""),
        username=str(ilo_cfg.get("username") or ""),
        label="iLO password",
    )
    extra_users = []
    for item in normalize_ilo_additional_users(ilo_cfg.get("additional_users", [])):
        username_errors = validate_ilo_login_name(item.get("username", ""), label="Extra iLO user username")
        password_check = validate_ilo_password(
            item.get("password", ""),
            username=str(item.get("username") or ""),
            label="Extra iLO user password",
        )
        extra_users.append(
            {
                "username": username_errors,
                "password": list(password_check.get("errors") or []),
            }
        )
    return {
        "username": main_username_errors,
        "password": list(main_password_check.get("errors") or []),
        "extra_users": extra_users,
    }


def build_snmp_field_errors(cfg: dict[str, Any]) -> dict[str, Any]:
    snmp_cfg = cfg.get("shared_snmp", {}) or {}
    primary_username = str(snmp_cfg.get("v3_username") or "").strip()
    primary_auth_password = str(snmp_cfg.get("v3_auth_password") or "")
    primary_priv_password = str(snmp_cfg.get("v3_priv_password") or "")
    extra_users = normalize_snmp_users(snmp_cfg.get("users", []))[1:] if snmp_cfg.get("users") else []
    return {
        "username": validate_snmpv3_username(primary_username, label="SNMPv3 user") if primary_username or primary_auth_password or primary_priv_password else [],
        "auth_password": validate_snmpv3_password(primary_auth_password, label="SNMPv3 auth password", required=bool(primary_username or primary_priv_password)),
        "priv_password": validate_snmpv3_password(primary_priv_password, label="SNMPv3 privacy password", required=bool(primary_username or primary_auth_password)),
        "extra_users": [
            {
                "username": validate_snmpv3_username(item.get("username", ""), label="Additional SNMPv3 user"),
                "auth_password": validate_snmpv3_password(item.get("auth_password", ""), label="Additional SNMPv3 auth password"),
                "priv_password": validate_snmpv3_password(item.get("priv_password", ""), label="Additional SNMPv3 privacy password"),
            }
            for item in extra_users
        ],
    }


def validate_esxi_hostname(value: str) -> list[str]:
    hostname = str(value or "").strip()
    if not hostname:
        return ["Server name is required."]
    if len(hostname) > 253:
        return ["Server name is too long. Keep the full name at 253 characters or less."]
    if re.search(r"[^A-Za-z0-9.\-]", hostname):
        return ["Use only letters, numbers, hyphens, and dots in the ESXi server name."]
    if hostname.startswith(".") or hostname.endswith("."):
        return ["Do not start or end the ESXi server name with a dot."]
    labels = hostname.split(".")
    if any(not label for label in labels):
        return ["Do not use empty name parts or two dots in a row in the ESXi server name."]
    errors: list[str] = []
    for label in labels:
        if len(label) > 63:
            errors.append("Each part of the ESXi server name must be 63 characters or less.")
            break
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?", label):
            errors.append("Each part of the ESXi server name must start and end with a letter or number.")
            break
    return errors


def build_esxi_password_policy_check(password: str, *, username: str = "root") -> dict[str, Any]:
    value = str(password or "")
    errors: list[str] = []
    notes: list[str] = []
    if not value:
        errors.append("Root password is required.")
        return {"valid": False, "errors": errors, "notes": notes, "class_count": 0, "length": 0}
    if any(ch.isspace() for ch in value):
        errors.append("Do not use spaces in the ESXi root password.")
    length = len(value)
    if length < 7:
        errors.append("Use at least 7 characters for the ESXi root password.")
    if length > 39:
        errors.append("Keep the ESXi root password under 40 characters.")
    lower_count = sum(1 for ch in value if ch.islower())
    upper_count = sum(1 for ch in value if ch.isupper())
    digit_count = sum(1 for ch in value if ch.isdigit())
    special_count = sum(1 for ch in value if not ch.isalnum())
    effective_classes = 0
    if lower_count:
        effective_classes += 1
    if upper_count:
        if upper_count == 1 and value[:1].isupper():
            notes.append("A single uppercase letter at the start may not count toward ESXi complexity.")
        else:
            effective_classes += 1
    if digit_count:
        if digit_count == 1 and value[-1:].isdigit():
            notes.append("A single number at the end may not count toward ESXi complexity.")
        else:
            effective_classes += 1
    if special_count:
        effective_classes += 1
    if effective_classes < 3:
        errors.append("Use at least 3 character types: lowercase, uppercase, number, or special.")
    if username and username.lower() in value.lower():
        notes.append("Avoid using the username inside the ESXi root password.")
    return {
        "valid": not errors,
        "errors": errors,
        "notes": notes,
        "class_count": effective_classes,
        "length": length,
    }
