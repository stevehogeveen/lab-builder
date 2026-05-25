from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict


class NetworkConfigModel(BaseModel):
    subnet: str = "10.10.8.0/24"
    dns_servers: list[str] = Field(default_factory=lambda: ["", "", "", ""])
    netapp_sp_a_offset: int = 13
    netapp_sp_b_offset: int = 14
    netapp_cluster_mgmt_offset: int = 45
    netapp_node_01_mgmt_offset: int = 46
    netapp_node_02_mgmt_offset: int = 47
    netapp_svm_mgmt_offset: int = 48


class IloPolicyModel(BaseModel):
    discover_enabled: bool = True
    discover_start_octet: int = 21
    discover_end_octet: int = 29
    apply_standard_policy: bool = True
    enable_standard_accounts: bool = True
    enable_license_check: bool = True
    enable_snmp_policy: bool = True
    enable_alert_destinations: bool = True
    enable_ipv6_disable: bool = True
    enable_time_policy: bool = True
    enable_auto_reset: bool = True
    kit_admin_password: str = ""
    kit_operator_password: str = ""
    shared_admin_username: str = "765CS"
    shared_admin_password: str = ""
    snmp_read_community: str = ""
    snmpv3_username: str = "765CS"
    snmpv3_auth_protocol: str = "SHA"
    snmpv3_auth_password: str = ""
    snmpv3_priv_protocol: str = "AES"
    snmpv3_priv_password: str = ""
    snmp_system_contact: str = "765 DSS"
    snmp_system_role: str = "iLO"
    snmp_location_source: str = "kit_id"
    alert_destinations: list[str] = Field(default_factory=lambda: ["10.245.190.67", "10.245.190.68"])
    alert_protocol: str = "SNMPv3Inform"
    timezone: str = "Bogota, Lima, Quito, Eastern Time(US & Canada)"
    discovered_hosts: list[dict[str, Any]] = Field(default_factory=list)


class IloConfigModel(BaseModel):
    host: str = ""
    current_ip: str = ""
    target_ip: str = ""
    subnet_mask: str = "255.255.255.0"
    gateway: str = ""
    dns_servers: list[str] = Field(default_factory=lambda: ["", "", "", ""])
    hostname: str = "ilo01"
    username: str = "Administrator"
    password: str = ""
    additional_users: list[dict[str, str]] = Field(default_factory=list)
    policy: IloPolicyModel = Field(default_factory=IloPolicyModel)
    upgrade: dict[str, Any] = Field(default_factory=dict)


class EsxiConfigModel(BaseModel):
    version: str = "7"
    base_iso_path: str = ""
    hostname: str = "esxi01"
    management_ip: str = ""
    subnet_mask: str = "255.255.255.0"
    gateway: str = ""
    dns_servers: list[str] = Field(default_factory=list)
    root_password: str = ""
    debug_no_reboot: bool = False
    post_config_policy: dict[str, Any] = Field(default_factory=dict)
    post_config_inventory: dict[str, Any] = Field(default_factory=dict)
    post_config_hostname_override: str = ""
    post_config_domain_override: str = ""
    post_config_dns1_override: str = ""
    post_config_dns2_override: str = ""


class StoragePlanModel(BaseModel):
    state: str = "idle"
    status_reason: str = ""
    target_host_override: str = ""
    username: str = ""
    password: str = ""
    include_in_ilo_run: bool = False
    latest_discovery_raw_path: str = ""
    latest_discovery_fingerprint: str = ""
    latest_plan_path: str = ""
    latest_plan_summary: dict[str, Any] = Field(default_factory=dict)
    latest_host: str = ""
    latest_serial_number: str = ""
    approval: dict[str, Any] = Field(default_factory=dict)


class KitConfigModel(BaseModel):
    site: dict[str, str] = Field(default_factory=lambda: {"name": "Kit-01"})
    shared_network: NetworkConfigModel = Field(default_factory=NetworkConfigModel)
    shared_snmp: dict[str, Any] = Field(default_factory=lambda: {
        "v3_username": "",
        "v3_auth_protocol": "SHA",
        "v3_auth_password": "",
        "v3_priv_protocol": "AES",
        "v3_priv_password": "",
        "users": [],
    })
    ip_plan: dict[str, Any] = Field(default_factory=dict)
    included: dict[str, bool] = Field(default_factory=dict)
    section_completion: dict[str, bool] = Field(default_factory=dict)
    ilo: IloConfigModel = Field(default_factory=IloConfigModel)
    esxi: EsxiConfigModel = Field(default_factory=EsxiConfigModel)
    windows: dict[str, Any] = Field(default_factory=dict)
    qnap: dict[str, Any] = Field(default_factory=dict)
    iosafe: dict[str, Any] = Field(default_factory=dict)
    cisco_switch: dict[str, Any] = Field(default_factory=dict)
    storage: StoragePlanModel = Field(default_factory=StoragePlanModel)
    netapp: dict[str, Any] = Field(default_factory=dict)
    vmware: dict[str, Any] = Field(default_factory=dict)
    upgrade_inventory: dict[str, Any] = Field(default_factory=dict)
    upgrade_helper: dict[str, Any] = Field(default_factory=dict)


class JobStatusModel(BaseModel):
    status: str = "Idle"
    scope: str = ""
    current_stage: str = ""
    progress_percent: int = 0
    completed_steps: int = 0
    total_steps: int = 0
    logs: list[str] = Field(default_factory=list)
    execution_mode: str = ""
    execution_mode_label: str = ""
    root_scope: str = ""
    stage_statuses: dict[str, str] = Field(default_factory=dict)


class CommandResult(BaseModel):
    ok: bool = True
    changed: bool = False
    warnings: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class OperationCommand(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    preview: dict[str, Any] = Field(default_factory=dict)
    validate_payload: dict[str, Any] = Field(default_factory=dict, alias="validate")
    apply_payload: dict[str, Any] = Field(default_factory=dict, alias="apply")
    result_recording_payload: dict[str, Any] = Field(default_factory=dict, alias="result_recording")
    requires_confirmation: bool = False
    risk_labels: list[Literal["destructive", "network", "credential", "reboot"]] = Field(default_factory=list)
