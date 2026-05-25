from __future__ import annotations

import copy
from typing import Any, Callable

from fastapi import Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from app.upgrade_helper import record_upgrade_inventory


ConfigsRuntime = dict[str, Callable[..., Any]]


def _record_ilo_upgrade_inventory_from_snapshot(cfg: dict[str, Any], inventory: dict[str, Any]) -> bool:
    summary = dict((inventory.get("summary") or {}))
    manager = dict(summary.get("manager") or {})
    firmware = str(manager.get("firmware") or "").strip()
    model = str(manager.get("model") or "").strip()
    if not firmware:
        return False
    record_upgrade_inventory(
        cfg,
        "ilo",
        current_version=firmware,
        raw_version=firmware,
        source="Latest live iLO inventory",
        manager_model=model,
    )
    return True


async def view_latest_live_summary_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    latest = runtime["latest_live_inventory_export"]()
    if not latest:
        error_text = f"No live inventory exports found under {runtime['ilo_live_export_dir']}"
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=error_text,
        )
    return runtime["render_page"](
        request,
        cfg,
        active_page=return_page,
        action_feedback=runtime["build_action_feedback"](
            "Latest live summary opened",
            "Showing the newest saved live inventory summary for this kit.",
            tone="ready",
            outcomes=[f"Source folder: {latest['directory']}"],
        ),
        config_view_title=f"Latest Live Summary: {latest['directory'].name}",
        config_view_content=latest["summary"].read_text(encoding="utf-8"),
    )


async def load_kit_handler(
    request: Request,
    runtime: ConfigsRuntime,
    selected_kit: str = Form(...),
    return_page: str = Form("dashboard"),
):
    runtime["set_current_kit_name"](selected_kit)
    cfg = runtime["load_kit_config"](selected_kit)
    return runtime["render_page"](request, cfg, active_page=return_page, message=f"Loaded kit: {selected_kit}")


async def new_kit_handler(
    request: Request,
    runtime: ConfigsRuntime,
    new_kit_name: str = Form(...),
    return_page: str = Form("dashboard"),
):
    raw_name = str(new_kit_name or "").strip()
    if not raw_name:
        cfg = runtime["load_kit_config"]() if "load_kit_config" in runtime else runtime["default_config"]()
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Enter a kit name before creating a new kit.",
        )
    name = runtime["sanitize_kit_name"](new_kit_name)
    kit_path = runtime.get("kit_path")
    if kit_path and kit_path(name).exists():
        cfg = runtime["load_kit_config"]() if "load_kit_config" in runtime else runtime["default_config"]()
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Kit already exists: {name}. Load it or choose a different name.",
        )
    cfg = runtime["default_config"]()
    cfg["site"]["name"] = name
    runtime["save_kit_config"](cfg)
    runtime["save_job"](
        name,
        {
            "status": "Idle",
            "scope": "",
            "current_stage": "",
            "progress_percent": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "logs": [],
        },
    )
    runtime["save_history"](name, [])
    return runtime["render_page"](request, cfg, active_page=return_page, message=f"Created new kit: {name}")


async def save_config_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configuration"),
    site_name: str = Form(...),
    shared_subnet: str = Form(...),
    gateway_ip: str = Form(...),
    switch_ip: str = Form(...),
    esxi_ip: str = Form(...),
    ilo_ip: str = Form(""),
    ilo_target_ip: str = Form(""),
    windows_ip: str = Form(...),
    qnap_ip: str = Form(...),
    iosafe_ip: str = Form(...),
    netapp_ip: str = Form(""),
    dns1: str = Form(""),
    dns2: str = Form(""),
    dns3: str = Form(""),
    dns4: str = Form(""),
    snmp_v3_username: str = Form(""),
    snmp_v3_auth_protocol: str = Form("SHA"),
    snmp_v3_auth_password: str = Form(""),
    snmp_v3_priv_protocol: str = Form("AES"),
    snmp_v3_priv_password: str = Form(""),
    included_ilo: str | None = Form(None),
    included_esxi: str | None = Form(None),
    included_windows: str | None = Form(None),
    included_qnap: str | None = Form(None),
    included_iosafe: str | None = Form(None),
    included_cisco_switch: str | None = Form(None),
    included_storage: str | None = Form(None),
    included_netapp: str | None = Form(None),
    section_basics_complete: str = Form("false"),
    section_network_complete: str = Form("false"),
    section_included_complete: str = Form("false"),
    section_credentials_complete: str = Form("false"),
    ilo_current_ip: str = Form(""),
    ilo_subnet_mask: str = Form(""),
    ilo_gateway: str = Form(""),
    ilo_dns1: str = Form(""),
    ilo_dns2: str = Form(""),
    ilo_dns3: str = Form(""),
    ilo_dns4: str = Form(""),
    ilo_hostname: str = Form(""),
    ilo_username: str = Form(""),
    ilo_password: str = Form(""),
    esxi_hostname: str = Form(""),
    esxi_root_password: str = Form(""),
    windows_vm_name: str = Form(""),
    windows_admin_password: str = Form(""),
    qnap_hostname: str = Form(""),
    qnap_username: str = Form(""),
    qnap_password: str = Form(""),
    iosafe_hostname: str = Form(""),
    iosafe_username: str = Form(""),
    iosafe_password: str = Form(""),
    cisco_switch_hostname: str = Form(""),
    cisco_switch_username: str = Form(""),
    cisco_switch_password: str = Form(""),
    cisco_console_port: str = Form(""),
    cisco_console_baud: int = Form(9600),
    cisco_management_vlan: int = Form(10),
    cisco_management_ip: str = Form(""),
    cisco_subnet_mask: str = Form(""),
    cisco_gateway: str = Form(""),
    cisco_enable_password: str = Form(""),
    netapp_host: str = Form(""),
    netapp_username: str = Form("admin"),
    netapp_password: str = Form(""),
    netapp_storage_protocol: str = Form("nfs"),
    netapp_iscsi_commands: str = Form(""),
    netapp_nfs_commands: str = Form(""),
):
    existing_cfg = runtime["load_kit_config"]()
    form = await request.form()

    def preserve_existing_secret(submitted: str, existing: Any) -> str:
        submitted_value = str(submitted or "")
        return submitted_value if submitted_value else str(existing or "")

    previous_subnet = existing_cfg.get("shared_network", {}).get("subnet", "")
    previous_plan = existing_cfg.get("ip_plan", {})
    submitted_plan = {
        "gateway": gateway_ip,
        "switch": switch_ip,
        "esxi": esxi_ip,
        "ilo": ilo_target_ip or ilo_ip,
        "windows": windows_ip,
        "qnap": qnap_ip,
        "iosafe": iosafe_ip,
        "netapp": netapp_ip,
    }
    if shared_subnet != previous_subnet:
        same_as_previous_plan = all(submitted_plan.get(key, "") == previous_plan.get(key, "") for key in runtime["default_ip_offsets"])
        if same_as_previous_plan:
            submitted_plan = runtime["build_default_ip_plan"](shared_subnet)
    resolved_ilo_target_ip = ilo_target_ip or ilo_ip
    resolved_ilo_current_ip = ilo_current_ip or resolved_ilo_target_ip
    cfg = {
        "site": {"name": runtime["sanitize_kit_name"](site_name)},
        "shared_network": {"subnet": shared_subnet, "dns_servers": [dns1, dns2, dns3, dns4]},
        "ip_plan": {
            "gateway": submitted_plan["gateway"],
            "switch": submitted_plan["switch"],
            "esxi": submitted_plan["esxi"],
            "ilo": submitted_plan["ilo"],
            "windows": submitted_plan["windows"],
            "qnap": submitted_plan["qnap"],
            "iosafe": submitted_plan["iosafe"],
            "netapp": submitted_plan["netapp"],
        },
        "shared_snmp": {
            "v3_username": snmp_v3_username,
            "v3_auth_protocol": snmp_v3_auth_protocol,
            "v3_auth_password": preserve_existing_secret(snmp_v3_auth_password, (existing_cfg.get("shared_snmp") or {}).get("v3_auth_password")),
            "v3_priv_protocol": snmp_v3_priv_protocol,
            "v3_priv_password": preserve_existing_secret(snmp_v3_priv_password, (existing_cfg.get("shared_snmp") or {}).get("v3_priv_password")),
            "read_community": str((existing_cfg.get("shared_snmp") or {}).get("read_community") or ""),
            "users": runtime["extract_snmp_users_from_form"](
                form,
                primary_username=snmp_v3_username,
                primary_auth_protocol=snmp_v3_auth_protocol,
                primary_auth_password=preserve_existing_secret(snmp_v3_auth_password, (existing_cfg.get("shared_snmp") or {}).get("v3_auth_password")),
                primary_priv_protocol=snmp_v3_priv_protocol,
                primary_priv_password=preserve_existing_secret(snmp_v3_priv_password, (existing_cfg.get("shared_snmp") or {}).get("v3_priv_password")),
            ),
        },
        "included": {
            "ilo": included_ilo == "on",
            "esxi": included_esxi == "on",
            "windows": included_windows == "on",
            "qnap": included_qnap == "on",
            "iosafe": included_iosafe == "on",
            "cisco_switch": included_cisco_switch == "on",
            "storage": included_storage == "on",
            "netapp": included_netapp == "on",
        },
        "section_completion": {
            "basics": section_basics_complete == "true",
            "network": section_network_complete == "true",
            "included": section_included_complete == "true",
            "credentials": section_credentials_complete == "true",
        },
        "ilo": {
            "host": resolved_ilo_current_ip,
            "current_ip": resolved_ilo_current_ip,
            "target_ip": resolved_ilo_target_ip,
            "subnet_mask": ilo_subnet_mask,
            "gateway": ilo_gateway,
            "dns_servers": [ilo_dns1, ilo_dns2, ilo_dns3, ilo_dns4],
            "hostname": runtime["normalize_ilo_hostname"](ilo_hostname),
            "username": ilo_username,
            "password": preserve_existing_secret(ilo_password, (existing_cfg.get("ilo") or {}).get("password")),
            "additional_users": runtime["extract_ilo_additional_users_from_form"](form),
            "policy": dict((existing_cfg.get("ilo") or {}).get("policy") or {}),
            "upgrade": copy.deepcopy((existing_cfg.get("ilo") or {}).get("upgrade") or {}),
        },
        "esxi": {"hostname": esxi_hostname, "root_password": preserve_existing_secret(esxi_root_password, (existing_cfg.get("esxi") or {}).get("root_password"))},
        "windows": {"vm_name": windows_vm_name, "admin_password": preserve_existing_secret(windows_admin_password, (existing_cfg.get("windows") or {}).get("admin_password"))},
        "qnap": {"hostname": qnap_hostname, "username": qnap_username, "password": preserve_existing_secret(qnap_password, (existing_cfg.get("qnap") or {}).get("password"))},
        "iosafe": {"hostname": iosafe_hostname, "username": iosafe_username, "password": preserve_existing_secret(iosafe_password, (existing_cfg.get("iosafe") or {}).get("password"))},
        "cisco_switch": {
            "hostname": cisco_switch_hostname,
            "username": cisco_switch_username,
            "password": preserve_existing_secret(cisco_switch_password, (existing_cfg.get("cisco_switch") or {}).get("password")),
            "connection_method": str(((existing_cfg.get("cisco_switch") or {}).get("connection_method")) or "auto"),
            "console_port": cisco_console_port or str(((existing_cfg.get("cisco_switch") or {}).get("console_port")) or ""),
            "console_baud": int(cisco_console_baud or ((existing_cfg.get("cisco_switch") or {}).get("console_baud")) or 9600),
            "management_vlan": int(cisco_management_vlan or ((existing_cfg.get("cisco_switch") or {}).get("management_vlan")) or 10),
            "management_ip": cisco_management_ip or str(((existing_cfg.get("cisco_switch") or {}).get("management_ip")) or ""),
            "subnet_mask": cisco_subnet_mask or str(((existing_cfg.get("cisco_switch") or {}).get("subnet_mask")) or ""),
            "gateway": cisco_gateway or str(((existing_cfg.get("cisco_switch") or {}).get("gateway")) or ""),
            "bootstrap_network_port": str(((existing_cfg.get("cisco_switch") or {}).get("bootstrap_network_port")) or ""),
            "bootstrap_network_mode": str(((existing_cfg.get("cisco_switch") or {}).get("bootstrap_network_mode")) or "trunk"),
            "enable_password": preserve_existing_secret(cisco_enable_password, (existing_cfg.get("cisco_switch") or {}).get("enable_password")),
            "domain_name": str(((existing_cfg.get("cisco_switch") or {}).get("domain_name")) or "lab.local"),
            "dns_servers": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("dns_servers") or []),
            "ntp_servers": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("ntp_servers") or []),
            "vlans": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("vlans") or []),
            "port_profiles": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("port_profiles") or {}),
            "ports": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("ports") or {}),
            "custom_global_commands": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("custom_global_commands") or []),
            "custom_port_commands": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("custom_port_commands") or {}),
            "apply_mode": str(((existing_cfg.get("cisco_switch") or {}).get("apply_mode")) or "initial_install"),
            "discovery": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("discovery") or {"prefer_console": True, "allow_network_scan": True}),
            "last_discovered_version": str(((existing_cfg.get("cisco_switch") or {}).get("last_discovered_version")) or ""),
            "last_discovered_at": str(((existing_cfg.get("cisco_switch") or {}).get("last_discovered_at")) or ""),
            "last_show_version": str(((existing_cfg.get("cisco_switch") or {}).get("last_show_version")) or ""),
            "last_discovery_error": str(((existing_cfg.get("cisco_switch") or {}).get("last_discovery_error")) or ""),
            "last_discovered_model": str(((existing_cfg.get("cisco_switch") or {}).get("last_discovered_model")) or ""),
            "last_discovered_platform": str(((existing_cfg.get("cisco_switch") or {}).get("last_discovered_platform")) or ""),
            "last_discovered_hostname": str(((existing_cfg.get("cisco_switch") or {}).get("last_discovered_hostname")) or ""),
            "last_console_candidates": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("last_console_candidates") or []),
            "last_serial_output": str(((existing_cfg.get("cisco_switch") or {}).get("last_serial_output")) or ""),
            "last_bootstrap": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("last_bootstrap") or {}),
            "last_ssh_test": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("last_ssh_test") or {}),
            "upgrade": copy.deepcopy((existing_cfg.get("cisco_switch") or {}).get("upgrade") or {}),
        },
        "netapp": {
            "host": netapp_host,
            "username": netapp_username,
            "password": preserve_existing_secret(netapp_password, (existing_cfg.get("netapp") or {}).get("password")),
            "storage_protocol": netapp_storage_protocol if netapp_storage_protocol in {"nfs", "iscsi"} else "nfs",
            "last_discovered_ontap_version": str(((existing_cfg.get("netapp") or {}).get("last_discovered_ontap_version")) or ""),
            "last_discovered_cluster_name": str(((existing_cfg.get("netapp") or {}).get("last_discovered_cluster_name")) or ""),
            "command_templates": {
                "iscsi": netapp_iscsi_commands,
                "nfs": netapp_nfs_commands,
            },
            "upgrade": copy.deepcopy((existing_cfg.get("netapp") or {}).get("upgrade") or {}),
        },
        "upgrade_inventory": copy.deepcopy(existing_cfg.get("upgrade_inventory") or {}),
        "upgrade_helper": copy.deepcopy(existing_cfg.get("upgrade_helper") or {}),
    }
    cfg = runtime["merge_defaults"](cfg)
    cfg["storage"]["include_in_ilo_run"] = cfg.get("included", {}).get("storage", False)
    snmp_input_review = runtime["build_snmp_input_review"](cfg)
    ilo_input_review = runtime["build_ilo_input_review"](cfg, include_policy_validation=False)
    combined_errors = list(snmp_input_review["errors"]) + list(ilo_input_review["errors"])
    combined_notes = list(snmp_input_review["notes"]) + list(ilo_input_review["notes"])
    try:
        cfg = runtime["apply_ip_plan"](cfg)
        runtime["save_kit_config"](cfg)
        runtime["append_activity_event"](
            cfg["site"]["name"],
            "global_settings_saved",
            workflow="global_settings",
            summary="Shared defaults were updated for this kit.",
            target=cfg["site"]["name"],
            details=[
                f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                f"Gateway: {cfg['ip_plan'].get('gateway', '') or 'Not set'}",
            ],
        )
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Kit saved with warnings" if combined_errors else "Kit saved",
                (
                    "Saved the kit, but some legacy iLO or SNMP values still need attention."
                    if combined_errors
                    else f"Saved the kit and refreshed the shared address plan for {cfg['site']['name']}."
                ),
                tone="pending" if combined_errors else "ready",
                outcomes=[
                    f"Kit: {cfg['site']['name']}",
                    f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                ],
                details=combined_errors + combined_notes,
            ),
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"Could not apply IP plan: {e}")


async def save_global_settings_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("global_settings"),
    site_name: str = Form(...),
    shared_subnet: str = Form(...),
    gateway_ip: str = Form(...),
    switch_ip: str | None = Form(None),
    esxi_ip: str | None = Form(None),
    ilo_target_ip: str | None = Form(None),
    ilo_current_ip: str | None = Form(None),
    ilo_username: str | None = Form(None),
    ilo_password: str | None = Form(None),
    windows_ip: str | None = Form(None),
    qnap_ip: str | None = Form(None),
    iosafe_ip: str | None = Form(None),
    netapp_ip: str | None = Form(None),
    dns1: str = Form(""),
    dns2: str = Form(""),
    dns3: str = Form(""),
    dns4: str = Form(""),
    snmp_v3_username: str = Form(""),
    snmp_v3_auth_protocol: str = Form("SHA"),
    snmp_v3_auth_password: str = Form(""),
    snmp_v3_priv_protocol: str = Form("AES"),
    snmp_v3_priv_password: str = Form(""),
    included_ilo: str | None = Form(None),
    included_esxi: str | None = Form(None),
    included_windows: str | None = Form(None),
    included_qnap: str | None = Form(None),
    included_iosafe: str | None = Form(None),
    included_cisco_switch: str | None = Form(None),
    included_storage: str | None = Form(None),
    included_netapp: str | None = Form(None),
    netapp_host: str | None = Form(None),
    netapp_username: str | None = Form(None),
    netapp_password: str | None = Form(None),
    netapp_storage_protocol: str | None = Form(None),
    netapp_iscsi_commands: str | None = Form(None),
    netapp_nfs_commands: str | None = Form(None),
    cisco_switch_hostname: str | None = Form(None),
    cisco_switch_username: str | None = Form(None),
    cisco_switch_password: str | None = Form(None),
    cisco_console_port: str | None = Form(None),
    cisco_console_baud: int | None = Form(None),
    cisco_management_vlan: int | None = Form(None),
    cisco_management_ip: str | None = Form(None),
    cisco_subnet_mask: str | None = Form(None),
    cisco_gateway: str | None = Form(None),
    cisco_enable_password: str | None = Form(None),
):
    cfg = runtime["load_kit_config"]()
    form = await request.form()

    def preserve_existing_secret(submitted: str, existing: Any) -> str:
        submitted_value = str(submitted or "")
        return submitted_value if submitted_value else str(existing or "")
    previous_subnet = str((cfg.get("shared_network") or {}).get("subnet") or "")
    cfg["site"]["name"] = runtime["sanitize_kit_name"](site_name)
    cfg["shared_network"]["subnet"] = shared_subnet
    cfg["shared_network"]["dns_servers"] = [dns1, dns2, dns3, dns4]
    cfg["shared_snmp"] = {
        "v3_username": snmp_v3_username,
        "v3_auth_protocol": snmp_v3_auth_protocol,
        "v3_auth_password": snmp_v3_auth_password,
        "v3_priv_protocol": snmp_v3_priv_protocol,
        "v3_priv_password": snmp_v3_priv_password,
        "users": runtime["extract_snmp_users_from_form"](
            form,
            primary_username=snmp_v3_username,
            primary_auth_protocol=snmp_v3_auth_protocol,
            primary_auth_password=snmp_v3_auth_password,
            primary_priv_protocol=snmp_v3_priv_protocol,
            primary_priv_password=snmp_v3_priv_password,
        ),
    }
    included_fields = {
        "included_ilo": ("ilo", included_ilo),
        "included_esxi": ("esxi", included_esxi),
        "included_windows": ("windows", included_windows),
        "included_qnap": ("qnap", included_qnap),
        "included_iosafe": ("iosafe", included_iosafe),
        "included_cisco_switch": ("cisco_switch", included_cisco_switch),
        "included_storage": ("storage", included_storage),
        "included_netapp": ("netapp", included_netapp),
    }
    if any(field in form for field in included_fields):
        cfg["included"].update({key: value == "on" for field, (key, value) in included_fields.items()})
    cfg["storage"]["include_in_ilo_run"] = cfg["included"].get("storage", cfg["storage"].get("include_in_ilo_run", False))
    snmp_input_review = runtime["build_snmp_input_review"](cfg)
    if snmp_input_review["errors"]:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Shared defaults need attention",
                "Fix the SNMPv3 user or passwords before saving this page.",
                tone="pending",
                outcomes=[
                    f"Kit: {cfg['site'].get('name', '') or 'Unknown'}",
                    f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                ],
                details=list(snmp_input_review["errors"]) + list(snmp_input_review["notes"]),
            ),
        )
    module_ip_fields = {
        "switch_ip": ("switch", switch_ip),
        "esxi_ip": ("esxi", esxi_ip),
        "ilo_target_ip": ("ilo", ilo_target_ip),
        "windows_ip": ("windows", windows_ip),
        "qnap_ip": ("qnap", qnap_ip),
        "iosafe_ip": ("iosafe", iosafe_ip),
        "netapp_ip": ("netapp", netapp_ip),
    }
    reset_default_ip_plan = previous_subnet != shared_subnet and not all(field in form for field in module_ip_fields)
    if reset_default_ip_plan:
        try:
            cfg["ip_plan"].update(runtime["build_default_ip_plan"](shared_subnet))
        except Exception as e:
            return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"Could not save global settings: {e}")
    cfg["ip_plan"]["gateway"] = gateway_ip
    for field, (key, value) in module_ip_fields.items():
        if field in form:
            cfg["ip_plan"][key] = str(value or "")
    if "ilo_target_ip" in form:
        cfg["ilo"]["target_ip"] = str(ilo_target_ip or "")

    ilo_access_fields = {"ilo_current_ip", "ilo_username", "ilo_password"}
    if any(field in form for field in ilo_access_fields):
        existing_ilo = cfg.get("ilo") or {}
        current_ip = str(
            ilo_current_ip
            if ilo_current_ip is not None
            else existing_ilo.get("current_ip") or existing_ilo.get("host") or ""
        ).strip()
        cfg["ilo"]["current_ip"] = current_ip
        cfg["ilo"]["host"] = current_ip
        cfg["ilo"]["username"] = str(ilo_username if ilo_username is not None else existing_ilo.get("username", ""))
        cfg["ilo"]["password"] = preserve_existing_secret(ilo_password or "", existing_ilo.get("password"))

    netapp_fields = {
        "netapp_host",
        "netapp_username",
        "netapp_password",
        "netapp_storage_protocol",
        "netapp_iscsi_commands",
        "netapp_nfs_commands",
    }
    if any(field in form for field in netapp_fields):
        existing_netapp = cfg.get("netapp") or {}
        existing_templates = existing_netapp.get("command_templates") or {}
        cfg["netapp"].update(
            {
                "host": str(netapp_host if netapp_host is not None else existing_netapp.get("host", "")),
                "username": str(netapp_username if netapp_username is not None else existing_netapp.get("username", "admin")),
                "password": preserve_existing_secret(netapp_password or "", existing_netapp.get("password")),
                "storage_protocol": netapp_storage_protocol if netapp_storage_protocol in {"nfs", "iscsi"} else existing_netapp.get("storage_protocol", "nfs"),
                "command_templates": {
                    "iscsi": str(netapp_iscsi_commands if netapp_iscsi_commands is not None else existing_templates.get("iscsi", "")),
                    "nfs": str(netapp_nfs_commands if netapp_nfs_commands is not None else existing_templates.get("nfs", "")),
                },
            }
        )

    cisco_fields = {
        "cisco_switch_hostname",
        "cisco_switch_username",
        "cisco_switch_password",
        "cisco_console_port",
        "cisco_console_baud",
        "cisco_management_vlan",
        "cisco_management_ip",
        "cisco_subnet_mask",
        "cisco_gateway",
        "cisco_enable_password",
    }
    if any(field in form for field in cisco_fields):
        existing_cisco = cfg.get("cisco_switch") or {}
        cfg["cisco_switch"].update(
            {
                "hostname": str(cisco_switch_hostname if cisco_switch_hostname is not None else existing_cisco.get("hostname", "")),
                "username": str(cisco_switch_username if cisco_switch_username is not None else existing_cisco.get("username", "admin")),
                "password": preserve_existing_secret(cisco_switch_password or "", existing_cisco.get("password")),
                "console_port": str(cisco_console_port if cisco_console_port is not None else existing_cisco.get("console_port", "")),
                "console_baud": int(cisco_console_baud if cisco_console_baud is not None else existing_cisco.get("console_baud", 9600) or 9600),
                "management_vlan": int(cisco_management_vlan if cisco_management_vlan is not None else existing_cisco.get("management_vlan", 10) or 10),
                "management_ip": str(cisco_management_ip if cisco_management_ip is not None else existing_cisco.get("management_ip", "")),
                "subnet_mask": str(cisco_subnet_mask if cisco_subnet_mask is not None else existing_cisco.get("subnet_mask", "")),
                "gateway": str(cisco_gateway if cisco_gateway is not None else existing_cisco.get("gateway", "")),
                "bootstrap_network_port": str(existing_cisco.get("bootstrap_network_port") or ""),
                "bootstrap_network_mode": str(existing_cisco.get("bootstrap_network_mode") or "trunk"),
                "enable_password": preserve_existing_secret(cisco_enable_password or "", existing_cisco.get("enable_password")),
            }
        )
    try:
        cfg = runtime["apply_ip_plan"](cfg)
        runtime["save_kit_config"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Shared defaults saved",
                "Updated the global settings that feed the workflow pages.",
                tone="ready",
                outcomes=[
                    f"Kit: {cfg['site'].get('name', '') or 'Unknown'}",
                    f"Shared subnet: {cfg['shared_network'].get('subnet', '') or 'Not set'}",
                ],
                links=[{"label": "Review iLO", "href": "/ilo"}, {"label": "Review Storage / RAID", "href": "/storage"}],
            ),
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"Could not save global settings: {e}")


async def download_latest_live_summary_handler(runtime: ConfigsRuntime):
    latest = runtime["latest_live_inventory_export"]()
    if not latest:
        return HTMLResponse(f"No live inventory exports found under {runtime['ilo_live_export_dir']}", status_code=404)
    return FileResponse(
        path=latest["summary"],
        filename=f"{latest['directory'].parent.name}-{latest['directory'].name}-summary.yml",
        media_type="application/x-yaml",
        headers=runtime["live_inventory_download_headers"](latest),
    )


async def download_latest_live_raw_handler(runtime: ConfigsRuntime):
    latest = runtime["latest_live_inventory_export"]()
    if not latest:
        return HTMLResponse(f"No live inventory exports found under {runtime['ilo_live_export_dir']}", status_code=404)
    return FileResponse(
        path=latest["raw"],
        filename=f"{latest['directory'].parent.name}-{latest['directory'].name}-raw.json",
        media_type="application/json",
        headers=runtime["live_inventory_download_headers"](latest),
    )


async def view_current_kit_config_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    try:
        snapshot_path = runtime["export_current_kit_config_snapshot"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            message=f"Generated current kit config snapshot at {snapshot_path}",
            config_view_title=f"Current Kit Config: {snapshot_path.name}",
            config_view_content=snapshot_path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"Current kit config view failed: {e}")


async def download_current_kit_config_handler(runtime: ConfigsRuntime):
    cfg = runtime["load_kit_config"]()
    snapshot_path = runtime["export_current_kit_config_snapshot"](cfg)
    return FileResponse(path=snapshot_path, filename=snapshot_path.name, media_type="application/x-yaml")


async def import_kit_config_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configs"),
    import_file: UploadFile | None = None,
):
    current_cfg = runtime["load_kit_config"]()
    try:
        if import_file is None:
            raise ValueError("No config file was uploaded.")
        raw = await import_file.read()
        if not raw:
            raise ValueError("The uploaded file was empty.")
        imported = runtime["yaml_safe_load"](raw.decode("utf-8")) or {}
        if not isinstance(imported, dict):
            raise ValueError("The uploaded file must contain a YAML or JSON object.")
        imported = runtime["merge_defaults"](imported)
        imported_name = runtime["sanitize_kit_name"](
            imported.get("site", {}).get("name", "") or current_cfg.get("site", {}).get("name", "Kit-01")
        )
        imported.setdefault("site", {})["name"] = imported_name
        runtime["save_kit_config"](imported)
        imported_snapshot = runtime["current_build_output_dir"](imported) / f"imported-config-{runtime['time_str']()}.yml"
        imported_snapshot.write_text(runtime["yaml_safe_dump"](imported, sort_keys=False), encoding="utf-8")
        cfg = runtime["load_kit_config"](imported_name)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Config imported",
                "Loaded the uploaded config into the app and switched the current kit to it.",
                tone="ready",
                status_label="Imported",
                outcomes=[
                    f"Current kit: {imported_name}",
                    f"Build folder: {runtime['current_build_output_dir'](cfg)}",
                ],
                links=[
                    {"label": "Open Global Settings", "href": "/global-settings"},
                    {"label": "Open Run Center", "href": "/execution"},
                ],
            ),
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            current_cfg,
            active_page=return_page,
            error_message=f"Config import failed: {str(e).splitlines()[0]}",
        )


async def view_ilo_config_snapshot_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    try:
        snapshot_path = runtime["export_ilo_config_snapshot"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            message=f"Generated iLO config snapshot at {snapshot_path}",
            config_view_title=f"iLO Config Snapshot: {snapshot_path.name}",
            config_view_content=snapshot_path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"iLO config snapshot view failed: {e}")


async def download_ilo_config_snapshot_handler(runtime: ConfigsRuntime):
    cfg = runtime["load_kit_config"]()
    snapshot_path = runtime["export_ilo_config_snapshot"](cfg)
    return FileResponse(path=snapshot_path, filename=snapshot_path.name, media_type="application/x-yaml")


async def view_report_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configs"),
    report_path: str = Form(...),
):
    cfg = runtime["load_kit_config"]()
    try:
        path = runtime["safe_report_path"](report_path)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Report opened",
                "Showing the selected saved report.",
                tone="ready",
                outcomes=[f"Source: {path}"],
            ),
            config_view_title=f"Report: {path.name}",
            config_view_content=path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"Report view failed: {str(e).splitlines()[0]}")


async def download_report_handler(runtime: ConfigsRuntime, report_path: str = Form(...)):
    path = runtime["safe_report_path"](report_path)
    media_type = "application/json" if path.suffix.lower() == ".json" else "text/yaml; charset=utf-8"
    return FileResponse(path=path, filename=path.name, media_type=media_type)


async def autofill_ip_plan_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configuration"),
    shared_subnet: str = Form("10.10.8.0/24"),
):
    cfg = runtime["load_kit_config"]()
    try:
        cfg["shared_network"]["subnet"] = shared_subnet
        cfg["ip_plan"] = runtime["build_default_ip_plan"](shared_subnet)
        cfg = runtime["apply_ip_plan"](cfg)
        runtime["save_kit_config"](cfg)
        return runtime["render_page"](request, cfg, active_page=return_page, message="Default IP plan generated and applied.")
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"IP plan generation failed: {e}")


async def export_ilo_config_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    try:
        snapshot_path = runtime["export_ilo_config_snapshot"](cfg)
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            message=f"Exported iLO config snapshot to {snapshot_path}",
        )
    except Exception as e:
        return runtime["render_page"](request, cfg, active_page=return_page, error_message=f"iLO config export failed: {e}")


async def export_ilo_inventory_handler(request: Request, runtime: ConfigsRuntime, return_page: str = Form("configs")):
    cfg = runtime["load_kit_config"]()
    ilo_cfg = cfg.get("ilo", {})
    host = (ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
    username = (ilo_cfg.get("username") or "").strip()
    password = ilo_cfg.get("password", "")
    if not host and runtime["policy_enabled"](cfg, "discover_enabled"):
        policy = runtime["normalize_ilo_policy"]((cfg.get("ilo") or {}).get("policy"))
        discovered = [runtime["probe_tcp_port"](target, 443, timeout_seconds=0.75) for target in runtime["build_ilo_discovery_targets"](cfg)]
        policy["discovered_hosts"] = discovered
        reachable = [item for item in discovered if item.get("reachable")]
        cfg["ilo"]["policy"] = runtime["normalize_ilo_policy"](policy)
        if reachable:
            host = str(reachable[0].get("host") or "")
            cfg["ilo"]["current_ip"] = host
            cfg["ilo"]["host"] = host
        runtime["save_kit_config"](cfg)
    if not host or not username or not password:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Current iLO config fetch failed: missing current iLO IP, username, or password.",
        )
    try:
        client = runtime["build_ilo_client"](host=host, username=username, password=password)
        inventory = client.get_current_config_snapshot()
        export_paths = runtime["export_ilo_inventory_snapshot"](cfg, inventory)
        inventory_recorded = _record_ilo_upgrade_inventory_from_snapshot(cfg, inventory)
        try:
            runtime["db_persist_ilo_inventory"](cfg, inventory, source_host=host)
        except Exception:
            pass
        if inventory_recorded:
            runtime["save_kit_config"](cfg)
        yaml_text = export_paths["summary"].read_text(encoding="utf-8")
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Current iLO inventory captured",
                "Read the live iLO state and saved a fresh summary and raw export.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    f"Saved under: {export_paths['summary'].parent}",
                ],
                links=[{"label": "Open artifacts page", "href": "/configs"}],
            ),
            config_view_title=f"Latest Live Summary: {export_paths['summary'].parent.name}",
            config_view_content=yaml_text,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Current iLO config fetch failed: {str(e).splitlines()[0]}",
        )


async def export_ad_hoc_ilo_inventory_handler(
    request: Request,
    runtime: ConfigsRuntime,
    return_page: str = Form("configs"),
    ad_hoc_ilo_host: str = Form(""),
    ad_hoc_ilo_username: str = Form(""),
    ad_hoc_ilo_password: str = Form(""),
    ad_hoc_ilo_label: str = Form(""),
    save_to_current_kit: str | None = Form(None),
):
    cfg = runtime["load_kit_config"]()
    host = ad_hoc_ilo_host.strip()
    username = ad_hoc_ilo_username.strip()
    password = ad_hoc_ilo_password
    label = ad_hoc_ilo_label.strip()
    if not host or not username or not password:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message="Ad hoc iLO inventory export failed: missing iLO IP/hostname, username, or password.",
        )
    try:
        client = runtime["build_ilo_client"](host=host, username=username, password=password)
        inventory = client.get_current_config_snapshot()
        export_paths = runtime["export_ilo_inventory_snapshot"](cfg, inventory, label=label, source_host=host)
        try:
            runtime["db_persist_ilo_inventory"](cfg, inventory, source_host=host)
        except Exception:
            pass
        saved_msg = ""
        if save_to_current_kit == "on":
            cfg["ilo"]["host"] = host
            cfg["ilo"]["current_ip"] = host
            cfg["ilo"]["username"] = username
            cfg["ilo"]["password"] = password
            _record_ilo_upgrade_inventory_from_snapshot(cfg, inventory)
            runtime["save_kit_config"](cfg)
            saved_msg = " Saved these connection values to the current kit."
        yaml_text = export_paths["summary"].read_text(encoding="utf-8")
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            action_feedback=runtime["build_action_feedback"](
                "Ad hoc iLO inventory captured",
                "Read the live iLO state from the temporary target and saved fresh exports.",
                tone="ready",
                outcomes=[
                    f"Target: {host}",
                    f"Saved under: {export_paths['summary'].parent}",
                    saved_msg.strip() or "Current kit settings were left unchanged.",
                ],
                links=[{"label": "Open artifacts page", "href": "/configs"}],
            ),
            config_view_title=f"Latest Live Summary: {export_paths['summary'].parent.name}",
            config_view_content=yaml_text,
        )
    except Exception as e:
        return runtime["render_page"](
            request,
            cfg,
            active_page=return_page,
            error_message=f"Ad hoc iLO inventory export failed: {str(e).splitlines()[0]}",
        )
