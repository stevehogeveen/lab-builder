from __future__ import annotations

from typing import Any

from app.netapp import NetAppClient, NetAppConfig, NetAppError


class NetAppModuleService:
    module = "netapp"

    def _netapp_cfg(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = (context.get("cfg") or {}).get("netapp") or {}
        return cfg if isinstance(cfg, dict) else {}

    def _desired_cfg(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = self._netapp_cfg(context)
        desired = cfg.get("desired") or {}
        return desired if isinstance(desired, dict) else {}

    def _profile_defaults(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = self._netapp_cfg(context)
        desired = self._desired_cfg(context)
        app_cfg = context.get("cfg") or {}
        kit_name = str(((app_cfg.get("site") or {}).get("name") or "KIT-01")).strip()
        subnet = str(((app_cfg.get("shared_network") or {}).get("subnet") or "10.10.10.0/24")).strip()
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
            "aggregate_node_01": "aggr_01",
            "aggregate_node_02": "aggr_02",
            "aggregate_diskcount": 11,
            "aggregate_raidtype": "raid_dp",
            "svm_mgmt_lif": f"{kit_name}-SVM_admin1",
            "svm_mgmt_ip": f"{subnet_prefix}.43",
            "management_subnet": f"{subnet_prefix}.0/24",
            "management_gateway": gateway,
            "management_netmask": "255.255.255.0",
            "autosupport_enabled": True,
            "autosupport_from": f"{kit_name}-NetApp@forces.gc.ca",
            "autosupport_to": f"{kit_name}.Alert.Reporting@",
            "autosupport_mail_hosts": [f"{subnet_prefix}.63"],
            "ntp_servers": [gateway],
            "required_users": ["Power", f"{kit_name}_Tech"],
            "esxi_hosts": [f"{subnet_prefix}.31", f"{subnet_prefix}.32", f"{subnet_prefix}.33"],
            "iscsi": {
                "subnet": "iSCSI",
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
        return merged

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
        return {
            "desired": desired,
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
            "dry_run_only": True,
            "context": {
                "module_name": str((context.get("module_name") or self.module) or self.module),
                "site_name": str(((context.get("cfg") or {}).get("site") or {}).get("name") or "Kit-01"),
            },
        }

    def _discover_stage(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self._response(context, "discover")
        netapp_cfg = self._netapp_cfg(context)
        if not str(netapp_cfg.get("host") or "").strip():
            payload["ok"] = False
            payload["error"] = "NetApp host is not configured."
            payload["stages"] = [{"name": "NetApp Stage 1: Discover", "ok": False, "steps": []}]
            payload["warnings"] = ["Set netapp.host before discovery."]
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

    def _validate_stage(self, context: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
        desired = self._profile_defaults(context)
        checks: list[dict[str, Any]] = []
        warnings: list[str] = list(discovery.get("warnings") or [])
        suggestions: list[str] = []
        raw = discovery.get("raw") or {}

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
        desired_aggrs = [str(desired.get("aggregate_node_01") or "").strip(), str(desired.get("aggregate_node_02") or "").strip()]
        missing_aggrs = [item for item in desired_aggrs if item and item not in aggregates]
        checks.append(
            {
                "name": "aggregates_exist_or_can_be_created",
                "ok": True,
                "details": {"desired": [item for item in desired_aggrs if item], "detected": sorted(aggregates), "missing": missing_aggrs, "can_create_in_apply_phase": True},
            }
        )
        if missing_aggrs:
            warnings.append(f"Missing desired aggregates: {', '.join(missing_aggrs)}.")
            suggestions.append("Plan aggregate creation as explicit apply actions after confirmation.")

        broadcast_domains = {str(item).strip() for item in list(discovery.get("existing_broadcast_domains") or []) if str(item).strip()}
        desired_domain = str(desired.get("data_broadcast_domain") or "").strip()
        domain_ok = (not desired_domain) or (desired_domain in broadcast_domains)
        checks.append({"name": "data_broadcast_domain_exists", "ok": domain_ok, "details": {"desired": desired_domain, "detected": sorted(broadcast_domains)}})
        if not domain_ok:
            warnings.append(f"Data broadcast domain '{desired_domain}' was not found.")
            suggestions.append("Create or remap broadcast domain in a controlled apply phase.")

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
        protocol_ok = protocol in {"nfs", "iscsi"} and (protocol in enabled_protocols or protocol in licenses)
        checks.append({"name": "protocol_is_licensed_supported", "ok": protocol_ok, "details": {"selected": protocol, "enabled": sorted(enabled_protocols), "licenses": sorted(licenses)}})
        if not protocol_ok:
            warnings.append(f"Selected protocol '{protocol}' does not appear enabled/licensed.")
            suggestions.append("Confirm protocol entitlement and SVM protocol configuration before apply.")

        subnet_name = str(((desired.get("iscsi") or {}).get("subnet") or "")).strip() if protocol == "iscsi" else ""
        discovered_subnets = {str(item).strip() for item in list(discovery.get("subnets") or []) if str(item).strip()}
        subnet_ok = (not subnet_name) or (subnet_name in discovered_subnets)
        ip_ranges_ok = True
        if protocol == "iscsi":
            desired_range = str(((desired.get("iscsi") or {}).get("ip_range") or "")).strip()
            used_ips = {str((item.get("ip") or {}).get("address") or "").strip() for item in list(raw.get("interfaces") or [])}
            if desired_range and "-" in desired_range:
                start_ip, end_ip = [part.strip() for part in desired_range.split("-", 1)]
                ip_ranges_ok = start_ip not in used_ips and end_ip not in used_ips
        checks.append({"name": "selected_ip_ranges_are_free", "ok": subnet_ok and ip_ranges_ok, "details": {"iscsi_subnet": subnet_name, "detected_subnets": sorted(discovered_subnets)}})
        if not (subnet_ok and ip_ranges_ok):
            warnings.append("Selected iSCSI subnet/range may conflict with discovered configuration.")
            suggestions.append("Adjust iSCSI subnet/range after reviewing existing LIF IP usage.")

        svms = list(raw.get("svms") or [])
        desired_svm = str(desired.get("svm_name") or "").strip()
        svm_match = next((item for item in svms if str(item.get("name") or "").strip() == desired_svm), None)
        svm_protocol_ok = True
        if desired_svm and svm_match:
            allowed = {str(item).strip().lower() for item in list(svm_match.get("allowed_protocols") or []) if str(item).strip()}
            svm_protocol_ok = protocol in allowed if allowed else True
        checks.append(
            {
                "name": "svm_exists_and_protocol_matches",
                "ok": (not desired_svm) or (svm_match is not None and svm_protocol_ok),
                "details": {"svm": desired_svm, "exists": svm_match is not None, "protocol_matches": svm_protocol_ok},
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

        autosupport = raw.get("autosupport") or {}
        autosupport_enabled = bool(autosupport.get("enabled")) if isinstance(autosupport, dict) else False
        autosupport_ok = (not desired.get("autosupport_enabled", True)) or autosupport_enabled
        checks.append(
            {
                "name": "autosupport_configured",
                "ok": autosupport_ok,
                "details": {
                    "desired_enabled": bool(desired.get("autosupport_enabled", True)),
                    "detected_enabled": autosupport_enabled,
                    "from": desired.get("autosupport_from"),
                    "to": desired.get("autosupport_to"),
                    "mail_hosts": list(desired.get("autosupport_mail_hosts") or []),
                },
            }
        )
        if not autosupport_ok:
            warnings.append("AutoSupport is not enabled or could not be verified.")
            suggestions.append("Plan AutoSupport configuration and confirm SMTP/mail-host reachability.")

        discovered_ntp = {str(item.get("server") or "").strip() for item in list(raw.get("ntp_servers") or []) if str(item.get("server") or "").strip()}
        desired_ntp = {str(item).strip() for item in list(desired.get("ntp_servers") or []) if str(item).strip()}
        ntp_ok = desired_ntp.issubset(discovered_ntp) if desired_ntp else True
        checks.append({"name": "ntp_servers_configured", "ok": ntp_ok, "details": {"desired": sorted(desired_ntp), "detected": sorted(discovered_ntp)}})
        if not ntp_ok:
            warnings.append("One or more desired NTP servers are missing.")
            suggestions.append("Plan NTP server update and retry if the time server is temporarily unreachable.")

        discovered_users = {str(item.get("name") or "").strip() for item in list(raw.get("users") or []) if str(item.get("name") or "").strip()}
        desired_users = {str(item).strip() for item in list(desired.get("required_users") or []) if str(item).strip()}
        users_ok = desired_users.issubset(discovered_users) if desired_users else True
        checks.append({"name": "required_users_exist", "ok": users_ok, "details": {"desired": sorted(desired_users), "detected": sorted(discovered_users)}})
        if not users_ok:
            warnings.append("One or more required NetApp users are missing.")
            suggestions.append("Plan role/user creation for Power and kit technician accounts.")

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
            ],
            "checks": checks,
        }
        return {"stage": stage, "warnings": warnings, "suggestions": suggestions}

    def _build_action_plan(self, context: dict[str, Any], discovery: dict[str, Any], validate_stage: dict[str, Any]) -> dict[str, Any]:
        desired = self._profile_defaults(context)
        protocol = str(desired.get("storage_protocol") or "nfs").strip().lower()
        actions: list[dict[str, Any]] = []
        checks = {str(item.get("name")): item for item in list(validate_stage.get("checks") or [])}

        missing_aggrs = list(((checks.get("aggregates_exist_or_can_be_created") or {}).get("details") or {}).get("missing") or [])
        actions.append({"name": "simulate_aggregate_aggr_01", "type": "manual", "status": "manual"})
        actions.append({"name": "simulate_aggregate_aggr_02", "type": "manual", "status": "manual"})
        actions.append({"name": "ensure_aggregate_aggr_01", "type": "create", "status": "create" if "aggr_01" in missing_aggrs else "skip"})
        actions.append({"name": "ensure_aggregate_aggr_02", "type": "create", "status": "create" if "aggr_02" in missing_aggrs else "skip"})
        actions.append({"name": "review_link_aggregation_groups", "type": "manual", "status": "manual"})
        actions.append({"name": "remove_default_broadcast_domains", "type": "manual", "status": "warn"})
        actions.append({"name": "ensure_data_broadcast_domain", "type": "create", "status": "skip" if bool((checks.get("data_broadcast_domain_exists") or {}).get("ok")) else "warn"})
        actions.append({"name": "ensure_management_subnet", "type": "create", "status": "create"})
        actions.append({"name": "ensure_svm", "type": "create", "status": "skip" if bool((checks.get("svm_exists_and_protocol_matches") or {}).get("details", {}).get("exists")) else "create"})
        actions.append({"name": "ensure_svm_management_lif", "type": "create", "status": "skip" if bool((checks.get("svm_management_lif_exists") or {}).get("ok")) else "create"})
        actions.append({"name": "ensure_autosupport", "type": "update", "status": "skip" if bool((checks.get("autosupport_configured") or {}).get("ok")) else "update"})
        actions.append({"name": "ensure_ntp_servers", "type": "update", "status": "skip" if bool((checks.get("ntp_servers_configured") or {}).get("ok")) else "update"})
        actions.append({"name": "ensure_power_and_tech_users", "type": "create", "status": "skip" if bool((checks.get("required_users_exist") or {}).get("ok")) else "manual"})

        if protocol == "iscsi":
            actions.extend(
                [
                    {"name": "ensure_iscsi_subnet", "type": "create", "status": "warn" if not bool((checks.get("selected_ip_ranges_are_free") or {}).get("ok")) else "create"},
                    {"name": "ensure_iscsi_lifs", "type": "create", "status": "warn" if not bool((checks.get("expected_ports_exist") or {}).get("ok")) else "create"},
                    {"name": "ensure_iscsi_service", "type": "update", "status": "update"},
                    {"name": "ensure_iscsi_portset", "type": "create", "status": "create"},
                    {"name": "ensure_iscsi_igroup", "type": "create", "status": "create"},
                    {"name": "ensure_iscsi_iqns", "type": "update", "status": "manual"},
                    {"name": "ensure_netapp_volumes", "type": "create", "status": "manual"},
                    {"name": "plan_lun_vmfs_datastore", "type": "create", "status": "manual"},
                    {"name": "plan_vmware_datastore_script", "type": "manual", "status": "manual"},
                ]
            )
        else:
            actions.extend(
                [
                    {"name": "ensure_nfs_lifs", "type": "create", "status": "warn" if not bool((checks.get("expected_ports_exist") or {}).get("ok")) else "create"},
                    {"name": "ensure_nfs_service", "type": "update", "status": "update"},
                    {"name": "ensure_nfs_volume", "type": "create", "status": "create"},
                    {"name": "ensure_export_policy", "type": "create", "status": "create"},
                    {"name": "plan_esxi_nfs_datastore_mount", "type": "manual", "status": "manual"},
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

    def _apply_stage(self, action_plan: list[dict[str, Any]]) -> dict[str, Any]:
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
            "reason": "Apply is intentionally disabled in this first implementation.",
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
        payload["warnings"] = warnings
        payload["suggestions"] = suggestions
        return payload

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        validate_payload = self.validate(context)
        payload = self._response(context, "plan")
        payload["ok"] = bool(validate_payload.get("ok"))
        payload["discovery"] = validate_payload.get("discovery") or {}
        payload["warnings"] = list(validate_payload.get("warnings") or [])
        payload["suggestions"] = list(validate_payload.get("suggestions") or [])
        payload["stages"] = list(validate_payload.get("stages") or [])
        validate_stage = next((item for item in payload["stages"] if str(item.get("name")) == "NetApp Stage 2: Validate"), {})
        action_plan = self._build_action_plan(context, payload["discovery"], validate_stage)
        payload["stages"].append(action_plan["stage"])
        payload["plan"] = {
            "mode": "dry_run_only",
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
            },
            "protocol_profile": {
                "selected_protocol": str(self._profile_defaults(context).get("storage_protocol") or "nfs"),
                "actions": list(action_plan.get("actions") or []),
                "command_preview": list(action_plan.get("command_preview") or []),
            },
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
        apply_stage = self._apply_stage(planned_actions)
        payload["stages"].append(apply_stage)
        payload["apply"] = apply_stage
        payload["job_id"] = str((job or {}).get("job_id") or "job-netapp-dryrun-001")
        payload["scope"] = str((job or {}).get("scope") or "netapp.apply")
        payload["ok"] = False
        payload["result"] = "blocked"
        return payload

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = self._response(context, "status")
        payload["status"] = "dry_run_only"
        payload["health"] = {
            "discovery": "available",
            "validation": "available",
            "planning": "available",
            "apply": "blocked",
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
