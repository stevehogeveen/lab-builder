from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Form, Request


EsxiConfigRuntime = dict[str, Callable[..., Any]]


async def save_esxi_settings_handler(
    request: Request,
    runtime: EsxiConfigRuntime,
    return_page: str = Form("esxi"),
    esxi_version: str = Form("7"),
    esxi_base_iso_path: str = Form(""),
    esxi_hostname: str = Form(""),
    esxi_root_password: str = Form(""),
    esxi_debug_no_reboot: str | None = Form(None),
    esxi_post_discovery_start_octet: str = Form("31"),
    esxi_post_discovery_end_octet: str = Form("33"),
    esxi_post_allow_datastore_create: str | None = Form(None),
    esxi_post_allow_single_mgmt_uplink_override: str | None = Form(None),
    esxi_post_configure_only_no_reboot: str | None = Form(None),
    esxi_post_reboot_confirmed: str | None = Form(None),
    esxi_post_wug_snmp_target: str = Form(""),
    esxi_post_wug_notraps: str = Form(""),
    esxi_post_hostname_override: str = Form(""),
    esxi_post_domain_override: str = Form(""),
    esxi_post_dns1_override: str = Form(""),
    esxi_post_dns2_override: str = Form(""),
    esxi_post_transport: str = Form("dry_run"),
    esxi_post_secret_wug_password: str = Form(""),
    esxi_post_secret_snmpv3_auth_password: str = Form(""),
    esxi_post_secret_snmpv3_priv_password: str = Form(""),
    esxi_post_secret_kit_root_password: str = Form(""),
    esxi_post_secret_svmservice_password: str = Form(""),
    esxi_post_secret_localtech_password: str = Form(""),
    included_esxi: str | None = Form(None),
):
    cfg = runtime["load_kit_config"]()
    policy = runtime["ensure_esxi_post_config_policy"](cfg)
    secrets = cfg.setdefault("esxi", {}).setdefault("post_config_secrets", {})
    cfg["esxi"]["version"] = runtime["normalize_esxi_version"](esxi_version)
    cfg["esxi"]["base_iso_path"] = esxi_base_iso_path.strip()
    cfg["esxi"]["hostname"] = esxi_hostname
    cfg["esxi"]["root_password"] = esxi_root_password
    cfg["esxi"]["debug_no_reboot"] = esxi_debug_no_reboot == "on"
    try:
        policy["discovery_start_octet"] = max(1, min(int(esxi_post_discovery_start_octet or "31"), 254))
    except ValueError:
        policy["discovery_start_octet"] = 31
    try:
        policy["discovery_end_octet"] = max(1, min(int(esxi_post_discovery_end_octet or "33"), 254))
    except ValueError:
        policy["discovery_end_octet"] = 33
    policy["allow_datastore_create"] = esxi_post_allow_datastore_create == "on"
    policy["allow_single_mgmt_uplink_override"] = esxi_post_allow_single_mgmt_uplink_override == "on"
    policy["configure_only_no_reboot"] = esxi_post_configure_only_no_reboot == "on"
    policy["reboot_confirmed"] = esxi_post_reboot_confirmed == "on"
    if esxi_post_wug_snmp_target.strip():
        policy["wug_snmp_target"] = esxi_post_wug_snmp_target.strip()
    if esxi_post_wug_notraps.strip():
        policy["wug_notraps"] = esxi_post_wug_notraps.strip()
    cfg["esxi"]["post_config_hostname_override"] = esxi_post_hostname_override.strip()
    cfg["esxi"]["post_config_domain_override"] = esxi_post_domain_override.strip()
    cfg["esxi"]["post_config_dns1_override"] = esxi_post_dns1_override.strip()
    cfg["esxi"]["post_config_dns2_override"] = esxi_post_dns2_override.strip()
    cfg["esxi"]["post_config_transport"] = "ssh" if esxi_post_transport.strip().lower() == "ssh" else "dry_run"
    if esxi_post_secret_wug_password:
        secrets["wug_password"] = esxi_post_secret_wug_password
    if esxi_post_secret_snmpv3_auth_password:
        secrets["snmpv3_auth_password"] = esxi_post_secret_snmpv3_auth_password
    if esxi_post_secret_snmpv3_priv_password:
        secrets["snmpv3_priv_password"] = esxi_post_secret_snmpv3_priv_password
    if esxi_post_secret_kit_root_password:
        secrets["kit_root_password"] = esxi_post_secret_kit_root_password
    if esxi_post_secret_svmservice_password:
        secrets["svmservice_password"] = esxi_post_secret_svmservice_password
    if esxi_post_secret_localtech_password:
        secrets["localtech_password"] = esxi_post_secret_localtech_password
    cfg["esxi"]["post_config_secrets"] = dict(secrets)
    cfg["esxi"]["post_config_policy"] = dict(policy)
    if included_esxi is not None:
        cfg["included"]["esxi"] = included_esxi == "on"
    cfg = runtime["apply_ip_plan"](cfg)
    effective_values = runtime["get_esxi_effective_values"](cfg)
    if effective_values["validation_errors"]:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "ESXi setup needs attention",
                "Fix the ESXi server name or root password rules before saving this page.",
                tone="pending",
                outcomes=[
                    f"Server name: {effective_values.get('hostname') or 'Not set'}",
                    f"Target: {effective_values.get('management_ip') or 'Not set'}",
                    f"ESXi version: {effective_values.get('version') or '7'}",
                    f"Gateway: {effective_values.get('gateway') or 'Not set'}",
                    f"DNS: {', '.join(effective_values.get('dns_servers') or []) or 'Not set'}",
                ],
                details=list(effective_values["validation_errors"]) + list(effective_values["validation_notes"]),
                links=[{"label": "Open Run Center", "href": "/execution"}],
            ),
        )
    runtime["save_kit_config"](cfg)
    runtime["append_activity_event"](
        cfg["site"]["name"],
        "esxi_settings_saved",
        workflow="esxi",
        summary="Saved the ESXi setup values for this kit.",
        target=cfg["esxi"].get("management_ip") or cfg.get("ip_plan", {}).get("esxi", ""),
    )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "ESXi setup saved",
            "Updated the local ESXi setup values for this kit.",
            tone="ready",
            outcomes=[
                f"Hostname: {cfg['esxi'].get('hostname', '') or 'Not set'}",
                f"Target: {cfg['esxi'].get('management_ip', '') or cfg.get('ip_plan', {}).get('esxi', '') or 'Not set'}",
                f"ESXi version: {cfg['esxi'].get('version') or '7'}",
                f"Debug no reboot: {'Yes' if cfg['esxi'].get('debug_no_reboot') else 'No'}",
                f"Gateway: {effective_values.get('gateway') or 'Not set'}",
                f"DNS: {', '.join(effective_values.get('dns_servers') or []) or 'Not set'}",
                f"Root password saved: {'Yes' if effective_values.get('root_password') else 'No'}",
            ],
        ),
    )


def register_module_routes(app: FastAPI) -> None:
    # ESXi config routes are still served by legacy app/main.py endpoints during migration.
    _ = app
