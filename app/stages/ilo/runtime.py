from __future__ import annotations

from typing import Any


def build_snmp_readback_checks(
    network_protocol_doc: dict[str, Any],
    *,
    requested_username: str = "",
    desired_auth_protocol: str = "",
    desired_priv_protocol: str = "",
) -> list[dict[str, Any]]:
    snmp_block = network_protocol_doc.get("SNMP") or {}
    checks: list[dict[str, Any]] = []
    requested_username = str(requested_username or "").strip()
    desired_auth_protocol = str(desired_auth_protocol or "").strip()
    desired_priv_protocol = str(desired_priv_protocol or "").strip()
    if "ProtocolEnabled" in snmp_block:
        checks.append(
            {
                "label": "protocol_enabled",
                "requested": True,
                "actual": snmp_block.get("ProtocolEnabled"),
                "matched": snmp_block.get("ProtocolEnabled") is True,
            }
        )
    username_key = next(
        (
            key
            for key in ("UserName", "Username", "SNMPv3UserName", "SNMPv3Username")
            if key in snmp_block
        ),
        "",
    )
    if username_key and requested_username:
        checks.append(
            {
                "label": "username",
                "requested": requested_username,
                "actual": str(snmp_block.get(username_key) or "").strip(),
                "matched": str(snmp_block.get(username_key) or "").strip() == requested_username,
            }
        )
    auth_key = next((key for key in ("AuthProtocol", "SNMPv3AuthProtocol") if key in snmp_block), "")
    if auth_key and desired_auth_protocol:
        checks.append(
            {
                "label": "auth_protocol",
                "requested": desired_auth_protocol,
                "actual": str(snmp_block.get(auth_key) or "").strip(),
                "matched": str(snmp_block.get(auth_key) or "").strip() == desired_auth_protocol,
            }
        )
    priv_key = next((key for key in ("PrivacyProtocol", "SNMPv3PrivacyProtocol") if key in snmp_block), "")
    if priv_key and desired_priv_protocol:
        checks.append(
            {
                "label": "privacy_protocol",
                "requested": desired_priv_protocol,
                "actual": str(snmp_block.get(priv_key) or "").strip(),
                "matched": str(snmp_block.get(priv_key) or "").strip() == desired_priv_protocol,
            }
        )
    for legacy_key in (
        "SNMPv1Enabled",
        "EnableSNMPv1",
        "SNMPv1RequestsEnabled",
        "SNMPv1TrapEnabled",
        "SNMPv1GetEnabled",
        "SNMPv1SetEnabled",
        "SNMPv2Enabled",
        "EnableSNMPv2",
        "SNMPv2RequestsEnabled",
        "SNMPv2TrapEnabled",
        "SNMPv2cEnabled",
        "EnableSNMPv2c",
        "SNMPv2cRequestsEnabled",
        "SNMPv2cTrapEnabled",
        "CommunityAccessEnabled",
    ):
        if legacy_key in snmp_block:
            checks.append(
                {
                    "label": legacy_key,
                    "requested": False,
                    "actual": snmp_block.get(legacy_key),
                    "matched": snmp_block.get(legacy_key) is False,
                }
            )
    for v3_key in ("SNMPv3RequestsEnabled", "SNMPv3Enabled", "SNMPv3TrapEnabled"):
        if v3_key in snmp_block:
            checks.append(
                {
                    "label": v3_key,
                    "requested": True,
                    "actual": snmp_block.get(v3_key),
                    "matched": snmp_block.get(v3_key) is True,
                }
            )
    return checks


def current_snmp_matches(
    network_protocol_doc: dict[str, Any],
    *,
    snmp_policy_enabled: bool,
    requested_username: str = "",
    desired_auth_protocol: str = "",
    desired_priv_protocol: str = "",
) -> bool:
    if not snmp_policy_enabled or not str(requested_username or "").strip():
        return True
    checks = build_snmp_readback_checks(
        network_protocol_doc,
        requested_username=requested_username,
        desired_auth_protocol=desired_auth_protocol,
        desired_priv_protocol=desired_priv_protocol,
    )
    return bool(checks) and all(item.get("matched") for item in checks)


def verify_final_ilo_state(
    *,
    network_protocol_doc: dict[str, Any],
    iface_doc: dict[str, Any],
    desired_hostname: str = "",
    shared_dns: list[str] | None = None,
    snmp_policy_enabled: bool = False,
    requested_username: str = "",
    desired_auth_protocol: str = "",
    desired_priv_protocol: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "hostname_matched": True,
        "dns_matched": True,
        "snmp_matched": True,
        "errors": [],
    }
    actual_hostname = str(network_protocol_doc.get("HostName") or iface_doc.get("HostName") or "").strip()
    desired_hostname = str(desired_hostname or "").strip()
    if desired_hostname:
        result["hostname_matched"] = actual_hostname == desired_hostname
    actual_dns = [
        item
        for item in (iface_doc.get("StaticNameServers") or iface_doc.get("NameServers") or [])
        if str(item or "").strip() and str(item).strip() not in {"0.0.0.0", "::"}
    ]
    requested_dns = [str(item).strip() for item in (shared_dns or []) if str(item).strip()]
    if requested_dns:
        result["dns_matched"] = actual_dns[: len(requested_dns)] == requested_dns
    snmp_checks = build_snmp_readback_checks(
        network_protocol_doc,
        requested_username=requested_username,
        desired_auth_protocol=desired_auth_protocol,
        desired_priv_protocol=desired_priv_protocol,
    )
    if snmp_policy_enabled and str(requested_username or "").strip():
        result["snmp_matched"] = bool(snmp_checks) and all(item.get("matched") for item in snmp_checks)
    result["actual_hostname"] = actual_hostname
    result["actual_dns"] = actual_dns
    result["requested_dns"] = requested_dns
    result["snmp_checks"] = snmp_checks
    result["snmp_block"] = dict(network_protocol_doc.get("SNMP") or {})
    result["matched"] = result["hostname_matched"] and result["dns_matched"] and result["snmp_matched"] and not result["errors"]
    return result
