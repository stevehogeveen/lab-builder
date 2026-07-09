from __future__ import annotations

import yaml
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.core.registry import load_modules
from app.netapp import NetAppClient, NetAppConfig, NetAppError
from app.core.config import calc_ip_plan
from app.storage_profiles import build_protocol_profile
from app.vmware import build_vmware_plan


@pytest.fixture()
def client(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    kits_dir = config_dir / "kits"
    artifacts_dir = tmp_path / "artifacts"
    generated_dir = artifacts_dir / "generated"
    jobs_dir = artifacts_dir / "jobs"
    history_dir = artifacts_dir / "history"
    ilo_export_dir = history_dir / "ilo-configs"
    config_export_dir = history_dir / "configs"
    live_ilo_config_dir = history_dir / "ilo-live-configs"
    ilo_inventory_dir = history_dir / "ilo-inventory"
    exports_dir = artifacts_dir / "exports"
    ilo_live_export_dir = exports_dir / "ilo" / "live"
    storage_raid_export_dir = exports_dir / "storage-raid"
    debug_bundles_dir = artifacts_dir / "debug-bundles"
    for value in (
        config_dir,
        kits_dir,
        artifacts_dir,
        generated_dir,
        jobs_dir,
        history_dir,
        ilo_export_dir,
        config_export_dir,
        live_ilo_config_dir,
        ilo_inventory_dir,
        ilo_live_export_dir,
        storage_raid_export_dir,
        debug_bundles_dir,
    ):
        value.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(main, "KITS_DIR", kits_dir)
    monkeypatch.setattr(main, "CURRENT_KIT_FILE", config_dir / "current_kit.txt")
    monkeypatch.setattr(main, "ARTIFACTS_DIR", artifacts_dir)
    monkeypatch.setattr(main, "GENERATED_DIR", generated_dir)
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(main, "ILO_CONFIG_EXPORT_DIR", ilo_export_dir)
    monkeypatch.setattr(main, "CONFIG_EXPORT_DIR", config_export_dir)
    monkeypatch.setattr(main, "LIVE_ILO_CONFIG_DIR", live_ilo_config_dir)
    monkeypatch.setattr(main, "ILO_INVENTORY_DIR", ilo_inventory_dir)
    monkeypatch.setattr(main, "EXPORTS_DIR", exports_dir)
    monkeypatch.setattr(main, "ILO_LIVE_EXPORT_DIR", ilo_live_export_dir)
    monkeypatch.setattr(main, "STORAGE_RAID_EXPORT_DIR", storage_raid_export_dir)
    monkeypatch.setattr(main, "DEBUG_BUNDLES_DIR", debug_bundles_dir)
    monkeypatch.setenv("LAB_BUILDER_VALIDATE_ESXI_MEDIA_URL", "0")
    monkeypatch.setenv("LAB_BUILDER_LIVE_RUN_CENTER_CHECKS", "0")
    main.set_current_kit_name("Kit-01")

    with TestClient(main.app) as test_client:
        yield test_client


def test_netapp_module_loads_independently(monkeypatch):
    test_app = FastAPI()
    monkeypatch.setenv("LAB_BUILDER_ENABLED_MODULES", "netapp")
    monkeypatch.delenv("LAB_BUILDER_DISABLED_MODULES", raising=False)

    manifests = load_modules(test_app, modules_dir=main.BASE_DIR / "app" / "modules", package_root="app.modules")
    enabled_modules = [item["name"] for item in manifests if item["enabled"]]

    assert enabled_modules == ["netapp"]
    assert test_app.state.module_navigation == [{"name": "netapp", "label": "NetApp Setup", "section": "More Setup", "href": "/modules/netapp", "active_page": "netapp"}]


def test_netapp_module_route_returns_200_with_bootstrap_and_connection_state(client):
    cfg = main.load_kit_config()
    cfg["included"]["netapp"] = True
    main.save_kit_config(cfg)

    response = client.get("/modules/netapp")

    assert response.status_code == 200
    assert "NetApp setup" in response.text
    assert "Connect to ONTAP" in response.text
    assert "Console/bootstrap IPs" in response.text
    assert "Cluster management IP" in response.text
    assert "Test connection" in response.text


def test_netapp_action_endpoints_return_planning_data(client):
    response = client.post("/modules/netapp/discover")
    assert response.status_code == 200
    assert response.json()["action"] == "discover"

    response = client.post("/modules/netapp/plan")
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "plan"
    assert isinstance(body.get("stages"), list)
    stage_names = [str(item.get("name")) for item in body.get("stages", [])]
    assert "NetApp Stage 1: Discover" in stage_names

    response = client.post("/modules/netapp/apply", json={"job": {"job_id": "test-job"}})
    assert response.status_code == 200
    apply_body = response.json()
    assert apply_body["action"] == "apply"
    assert apply_body["result"] == "blocked"

    response = client.get("/modules/netapp/status")
    assert response.status_code == 200
    assert response.json()["action"] == "status"


def test_netapp_test_connection_endpoint_returns_result(client):
    response = client.post(
        "/modules/netapp/test-connection",
        data={
            "netapp_host": "10.10.8.46",
            "netapp_username": "admin",
            "netapp_password": "secret",
            "netapp_storage_protocol": "nfs",
        },
    )

    assert response.status_code == 200
    assert "Last connection test" in response.text
    assert "Target 10.10.8.46" in response.text


def test_netapp_plan_includes_validate_and_plan_stage_order_when_host_is_set(client):
    cfg = main.load_kit_config()
    cfg["netapp"]["host"] = "10.10.8.45"
    cfg["netapp"]["username"] = "admin"
    cfg["netapp"]["password"] = "secret"
    cfg["netapp"]["storage_protocol"] = "iscsi"
    main.save_kit_config(cfg)

    response = client.post("/modules/netapp/plan")
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "plan"
    stage_names = [str(item.get("name")) for item in body.get("stages", [])]
    assert "NetApp Stage 1: Discover" in stage_names
    if body.get("ok"):
        assert "NetApp Stage 2: Validate" in stage_names
        assert "NetApp Stage 3: Plan" in stage_names


def test_netapp_service_renders_editable_command_template_tokens():
    service = main.NetAppModuleService() if hasattr(main, "NetAppModuleService") else None
    if service is None:
        from app.modules.netapp.service import NetAppModuleService

        service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "DOP-X70-Test"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "host": "",
                "username": "admin",
                "password": "",
                "storage_protocol": "iscsi",
                "command_templates": {
                    "iscsi": "vserver create -vserver <<SVM_NAME>>\nnet int create -address <<SUBNET>>.43 -netmask <<SUBNET_MASK>>",
                    "nfs": "",
                },
                "desired": {"svm_name": "DOP-X70-Test-SVM"},
            },
        }
    }

    settings = service.settings_context(context)
    commands = service._render_command_template(settings["command_templates"]["iscsi"], service._template_values(context, settings["desired"]))

    assert "vserver create -vserver DOP-X70-Test-SVM" in commands
    assert "net int create -address 10.10.10.43 -netmask 255.255.255.0" in commands


def test_netapp_upgrade_posture_marks_below_baseline_as_required():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    desired = {
        "baseline": {
            "target_ontap_version": "9.12.1",
            "minimum_ontap_version": "9.9.1",
            "upgrade_enforcement": "required",
        }
    }
    discovery = {"ontap_version": "NetApp Release 9.9.1P2: Thu Aug 19 13:53:06 UTC 2021"}

    posture = service._build_upgrade_posture(desired, discovery)

    assert posture["current_version"].startswith("NetApp Release 9.9.1P2")
    assert posture["target_version"] == "9.12.1"
    assert posture["status"] == "upgrade_required"
    assert posture["meets_baseline"] is False


def test_storage_profile_uses_desired_values_and_preserves_subnet_truth():
    cfg = {
        "site": {"name": "KIT100"},
        "shared_network": {"subnet": "10.44.12.0/24"},
        "ip_plan": {"cluster_mgmt_ip": "10.44.12.45", "svm_mgmt_ip": "10.44.12.48"},
        "netapp": {
            "storage_protocol": "nfs",
            "desired": {
                "cluster_name": "KIT100",
                "svm_name": "KIT100-SVM",
                "aggregate_node_01": "aggr_01",
                "aggregate_node_02": "aggr_02",
                "nfs": {"allowed_subnet": "10.44.12.0/24", "export_policy": "KIT100_ESXi_NFS"},
            },
        },
        "vmware": {},
    }

    profile = build_protocol_profile(cfg)

    assert profile["protocol"] == "nfs"
    assert profile["base"]["management_subnet"]["cidr"] == "10.44.12.0/24"
    assert profile["base"]["cluster_name"] == "KIT100"
    assert profile["nfs"]["export_policy"] == "KIT100_ESXi_NFS"


def test_vmware_plan_uses_offset_range_for_esxi_hosts():
    cfg = {
        "site": {"name": "KIT100"},
        "shared_network": {"subnet": "10.44.12.0/24"},
        "vmware": {
            "esxi_host_start_offset": 31,
            "esxi_host_end_offset": 33,
            "datacenter_name": "KIT100",
            "cluster_name": "KIT100-Cluster",
        },
    }

    plan = build_vmware_plan(cfg, storage_protocol="iscsi")

    assert plan["esxi_hosts"] == ["10.44.12.31", "10.44.12.32", "10.44.12.33"]
    step_names = [step["name"] for step in plan["steps"]]
    assert "rescan_iscsi_hbas" in step_names


def test_vmware_nfs_plan_uses_discovered_lifs_and_volume_context():
    cfg = {
        "site": {"name": "KIT100"},
        "shared_network": {"subnet": "10.44.12.0/24"},
        "esxi": {"management_ip": "10.44.12.31", "root_password": "Secret123!"},
        "netapp": {
            "storage_protocol": "nfs",
            "nfs": {},
            "desired": {"nfs": {"volume": "esxi_datastore_01", "mount_path": "/esxi_datastore_01"}},
        },
        "vmware": {
            "esxi_host_start_offset": 31,
            "esxi_host_end_offset": 32,
            "nfs": {"nfs_version": "4.1"},
        },
    }
    discovery = {
        "svms": ["stage_nfs"],
        "discovered_nfs_lifs": [
            {"name": "nfs_lif_01", "address": "10.44.12.48"},
            {"name": "nfs_lif_02", "address": "10.44.12.49"},
        ],
    }

    plan = build_vmware_plan(cfg, storage_protocol="nfs", discovery=discovery)
    validate_step = next(step for step in plan["steps"] if step["name"] == "validate_nfs_mount_inputs")
    mount_step = next(step for step in plan["steps"] if step["name"] == "plan_nfs_datastore_mounts")

    assert plan["connection_mode"] == "standalone_esxi"
    assert plan["esxi_hosts"] == ["10.44.12.31"]
    assert plan["nfs_context"]["svm_name"] == "stage_nfs"
    assert plan["nfs_context"]["lif_ips"] == ["10.44.12.48", "10.44.12.49"]
    assert plan["nfs_context"]["datastore_name"] == "esxi_datastore_01"
    assert validate_step["status"] == "ok"
    assert len(mount_step["details"]["mount_plan"]) == 1
    assert mount_step["details"]["mount_plan"][0]["server"] == "10.44.12.48"
    assert mount_step["details"]["mount_plan"][0]["alternate_servers"] == ["10.44.12.49"]
    assert mount_step["details"]["mount_plan"][0]["export_path"] == "/esxi_datastore_01"
    assert "esxcli storage nfs41 add -H '10.44.12.48,10.44.12.49'" in mount_step["details"]["mount_plan"][0]["esxcli_command"]
    assert "New-Datastore -Nfs -VMHost 10.44.12.31" in mount_step["details"]["mount_plan"][0]["powercli_command"]


def test_netapp_plan_includes_standalone_esxi_nfs_mount_action():
    from app.modules.netapp.service import NetAppModuleService

    class FakeNetAppClient:
        def build_discovery_summary(self):
            return {
                "cluster_name": "cluster-a",
                "ontap_version": "ONTAP 9.12.1",
                "node_count": 2,
                "node_names": ["KIT100-01", "KIT100-02"],
                "node_models": ["A", "A"],
                "nodes": ["KIT100-01", "KIT100-02"],
                "available_ports": ["KIT100-01:e0M", "KIT100-01:e0b", "KIT100-02:e0M", "KIT100-02:e0b"],
                "existing_broadcast_domains": ["NFS_BD"],
                "aggregates": ["aggr_01", "aggr_02"],
                "svms": ["stage_nfs"],
                "volume_details": [{"name": "esxi_datastore_01", "svm": "stage_nfs", "aggregate": "aggr_01", "state": "online"}],
                "discovered_nfs_lifs": [
                    {"name": "nfs_lif_01", "address": "10.44.12.48", "home_node": "KIT100-01", "home_port": "e0b"},
                    {"name": "nfs_lif_02", "address": "10.44.12.49", "home_node": "KIT100-02", "home_port": "e0b"},
                ],
                "enabled_protocols": ["nfs"],
                "warnings": [],
                "capabilities": {
                    "cluster": True,
                    "nodes": True,
                    "ports": True,
                    "broadcast_domains": True,
                    "aggregates": True,
                    "svms": True,
                    "volumes": True,
                    "network_interfaces": True,
                },
                "capability_status": {
                    "cluster": "native",
                    "nodes": "native",
                    "ports": "native",
                    "broadcast_domains": "native",
                    "aggregates": "native",
                    "svms": "native",
                    "volumes": "native",
                    "network_interfaces": "native",
                },
                "raw": {
                    "aggregates": [{"name": "aggr_01", "node": {"name": "KIT100-01"}}, {"name": "aggr_02", "node": {"name": "KIT100-02"}}],
                    "svms": [{"name": "stage_nfs", "allowed_protocols": ["nfs"]}],
                    "ports": [{"name": "e0b", "broadcast_domain": {"name": "NFS_BD"}, "mtu": 9000}],
                },
            }

    service = NetAppModuleService()
    service._build_client = lambda context: FakeNetAppClient()
    context = {
        "cfg": {
            "site": {"name": "KIT100"},
            "shared_network": {"subnet": "10.44.12.0/24"},
            "ip_plan": {"gateway": "10.44.12.1"},
            "esxi": {"management_ip": "10.44.12.31", "root_password": "Secret123!"},
            "netapp": {"host": "10.44.12.45", "username": "admin", "password": "secret", "storage_protocol": "nfs", "bootstrap_complete": True},
        }
    }

    payload = service.plan(context)
    actions = (((payload.get("plan") or {}).get("protocol_profile")) or {}).get("actions") or []
    mount_action = next(action for action in actions if action["name"] == "ensure_esxi_nfs_datastore_mount")
    assert mount_action["status"] == "create"


def test_vmware_nfs_probe_reports_esxi_and_nfs_reachability():
    from app.modules.netapp.service import NetAppModuleService

    class FakeNetAppClient:
        def build_discovery_summary(self):
            return {
                "cluster_name": "cluster-a",
                "ontap_version": "ONTAP 9.12.1",
                "node_count": 2,
                "node_names": ["kit-01", "kit-02"],
                "node_models": ["A", "A"],
                "nodes": ["kit-01", "kit-02"],
                "available_ports": ["kit-01:e0M", "kit-02:e0M"],
                "existing_broadcast_domains": ["NFS_BD"],
                "aggregates": ["aggr_01", "aggr_02"],
                "svms": ["stage_nfs"],
                "discovered_nfs_lifs": [
                    {"name": "nfs_lif_01", "address": "10.44.12.48", "home_node": "kit-01", "home_port": "e0b"},
                    {"name": "nfs_lif_02", "address": "10.44.12.49", "home_node": "kit-02", "home_port": "e0b"},
                ],
                "lif_details": [],
                "volume_details": [{"name": "esxi_datastore_01", "svm": "stage_nfs", "aggregate": "aggr_01", "state": "online"}],
                "enabled_protocols": ["nfs"],
                "disk_count": 0,
                "disk_inventory": [],
                "warnings": [],
                "capabilities": {
                    "cluster": True,
                    "nodes": True,
                    "ports": True,
                    "broadcast_domains": True,
                    "aggregates": True,
                    "svms": True,
                    "network_interfaces": True,
                },
                "capability_status": {
                    "cluster": "native",
                    "nodes": "native",
                    "ports": "native",
                    "broadcast_domains": "native",
                    "aggregates": "native",
                    "svms": "native",
                    "network_interfaces": "native",
                },
                "raw": {
                    "aggregates": [{"name": "aggr_01"}, {"name": "aggr_02"}],
                    "svms": [{"name": "stage_nfs", "allowed_protocols": ["nfs"]}],
                    "ports": [{"name": "e0b", "broadcast_domain": {"name": "NFS_BD"}, "mtu": 9000}],
                },
            }

    service = NetAppModuleService()
    service._build_client = lambda context: FakeNetAppClient()

    def fake_probe(host: str, ports: list[int] | None = None, timeout: float = 1.5):
        _ = timeout
        return {"host": host, "reachable": True, "ports": {str(port): "open" for port in (ports or [])}, "error": ""}

    service._probe_host = fake_probe
    context = {
        "cfg": {
            "site": {"name": "KIT100"},
            "shared_network": {"subnet": "10.44.12.0/24"},
            "ip_plan": {"gateway": "10.44.12.1"},
            "esxi": {"management_ip": "10.44.12.31", "root_password": "Secret123!"},
            "netapp": {"host": "10.44.12.45", "username": "admin", "password": "secret", "storage_protocol": "nfs", "bootstrap_complete": True},
            "vmware": {"nfs": {"nfs_version": "4.1"}},
        }
    }

    payload = service.test_vmware_nfs_targets(context)

    assert payload["ok"] is True
    assert payload["vmware_probe"]["ready"] is True
    assert payload["vmware_probe"]["connection_mode"] == "standalone_esxi"
    checks = payload["vmware_probe"]["checks"]
    assert any(item["kind"] == "esxi_host" and item["host"] == "10.44.12.31" for item in checks)
    assert any(item["kind"] == "nfs_server" and item["host"] == "10.44.12.48" for item in checks)


def test_netapp_plan_includes_snapshot_inventory_details():
    from app.modules.netapp.service import NetAppModuleService

    class FakeNetAppClient:
        def build_discovery_summary(self):
            return {
                "cluster_name": "cluster-a",
                "ontap_version": "ONTAP 9.12.1",
                "node_count": 2,
                "node_names": ["kit-01", "kit-02"],
                "node_models": ["FAS2750", "FAS2750"],
                "nodes": ["kit-01", "kit-02"],
                "node_details": [
                    {"name": "kit-01", "model": "FAS2750", "serial_number": "SN1", "state": "healthy", "ontap_version": "ONTAP 9.12.1", "ha_enabled": True},
                    {"name": "kit-02", "model": "FAS2750", "serial_number": "SN2", "state": "healthy", "ontap_version": "ONTAP 9.12.1", "ha_enabled": True},
                ],
                "physical_ports": ["kit-01:a0a", "kit-02:a0a"],
                "available_ports": ["kit-01:a0a", "kit-01:e0M", "kit-02:a0a", "kit-02:e0M"],
                "existing_interface_groups": ["kit-01:a0a", "kit-02:a0a"],
                "existing_broadcast_domains": ["Default", "Data"],
                "aggregates": ["aggr_01", "aggr_02"],
                "svms": ["KIT-01-SVM"],
                "svm_details": [{"name": "KIT-01-SVM", "state": "running", "subtype": "default", "allowed_protocols": ["nfs"]}],
                "lif_details": [{"name": "KIT-01-SVM_admin1", "svm": "KIT-01-SVM", "address": "10.10.10.43", "home_node": "kit-01", "home_port": "e0M", "service_policy": "default-management", "enabled": True}],
                "volume_details": [{"name": "esxi_datastore_01", "svm": "KIT-01-SVM", "aggregate": "aggr_01", "state": "online", "type": "rw"}],
                "enabled_protocols": ["nfs"],
                "disk_count": 2,
                "disk_inventory": [
                    {"name": "0a.00.1", "node": "kit-01", "vendor": "NETAPP", "model": "X", "type": "SSD", "state": "present"},
                    {"name": "0a.00.2", "node": "kit-02", "vendor": "NETAPP", "model": "X", "type": "SSD", "state": "present"},
                ],
                "warnings": [],
                "raw": {
                    "aggregates": [{"name": "aggr_01"}, {"name": "aggr_02"}],
                    "svms": [{"name": "KIT-01-SVM", "allowed_protocols": ["nfs"]}],
                    "interfaces": [{"name": "KIT-01-SVM_admin1", "ip": {"address": "10.10.10.43"}}],
                    "licenses": [{"name": "nfs"}],
                    "protocol_services": {"nfs": [{"svm": {"name": "KIT-01-SVM"}, "enabled": True, "v3": True, "v4_1": True, "v4_2": False}], "iscsi": []},
                    "ports": [{"name": "a0a", "broadcast_domain": {"name": "Data"}, "mtu": 9000}, {"name": "e0M", "broadcast_domain": {"name": "Default"}, "mtu": 1500}],
                    "ntp_servers": [{"server": "10.10.10.1"}],
                    "users": [{"name": "Power"}, {"name": "Kit-01_Tech"}],
                    "autosupport": {"enabled": True},
                },
            }

    service = NetAppModuleService()
    service._build_client = lambda context: FakeNetAppClient()
    context = {"cfg": {"site": {"name": "Kit-01"}, "shared_network": {"subnet": "10.10.10.0/24"}, "ip_plan": {"gateway": "10.10.10.1"}, "netapp": {"host": "10.10.10.45", "username": "admin", "password": "secret", "storage_protocol": "nfs", "bootstrap_complete": True}}}

    payload = service.plan(context)

    assert payload["discovery"]["disk_count"] == 2
    assert payload["discovery"]["node_details"][0]["model"] == "FAS2750"
    assert payload["discovery"]["svm_details"][0]["allowed_protocols"] == ["nfs"]
    assert payload["plan"]["adaptive_discovery"]["cluster_name"] == "cluster-a"


def test_netapp_client_retries_with_smaller_field_sets():
    client = NetAppClient(NetAppConfig(host="netapp.local", username="admin", password="secret"))
    attempts: list[str | None] = []

    def fake_get(path, params=None):
        _ = path
        fields = (params or {}).get("fields")
        attempts.append(fields)
        if fields and "broadcast_domain" in str(fields):
            raise NetAppError("invalid field selection")
        return {"records": [{"name": "Mgmt", "subnet": "10.10.10.0/24"}]}

    client._get = fake_get  # type: ignore[method-assign]

    records, status = client._records_with_fallback(
        "/api/network/ip/subnets",
        [
            "name,ipspace,subnet,broadcast_domain,ranges,gateway",
            "name,subnet,broadcast_domain,gateway",
            "name,subnet,gateway",
        ],
    )

    assert records == [{"name": "Mgmt", "subnet": "10.10.10.0/24"}]
    assert status == "fallback"
    assert any(item and "broadcast_domain" in item for item in attempts)
    assert any(item == "name,subnet,gateway" for item in attempts)


def test_validate_stage_treats_unverifiable_ntp_and_users_as_manual_not_missing():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {"storage_protocol": "nfs", "desired": {"required_users": ["Power"], "ntp_servers": ["10.10.10.1"]}},
        }
    }
    discovery = {
        "nodes": ["Kit-01-01", "Kit-01-02"],
        "available_ports": ["Kit-01-01:a0a", "Kit-01-01:e0M", "Kit-01-02:a0a", "Kit-01-02:e0M"],
        "existing_broadcast_domains": ["Data"],
        "enabled_protocols": ["nfs"],
        "subnets": [],
        "capabilities": {
            "subnets": False,
            "licenses": True,
            "protocol_services": True,
            "autosupport": False,
            "ntp_servers": False,
            "users": False,
        },
        "raw": {
            "aggregates": [{"name": "aggr_01"}, {"name": "aggr_02"}],
            "svms": [{"name": "Kit-01-SVM"}],
            "interfaces": [],
            "licenses": [{"name": "nfs"}],
            "ports": [{"name": "a0a", "broadcast_domain": {"name": "Data"}, "mtu": 9000}],
            "ntp_servers": [],
            "users": [],
            "autosupport": {},
        },
    }

    result = service._validate_stage(context, discovery)
    check_map = {item["name"]: item for item in result["stage"]["checks"]}

    assert check_map["ntp_servers_configured"]["ok"] is True
    assert check_map["required_users_exist"]["ok"] is True
    assert check_map["autosupport_configured"]["ok"] is True
    assert "One or more desired NTP servers are missing." not in result["warnings"]
    assert "One or more required NetApp users are missing." not in result["warnings"]


def test_discovery_inferrs_protocols_from_export_and_san_objects_when_service_endpoints_are_empty():
    client = NetAppClient(NetAppConfig(host="netapp.local", username="admin", password="secret"))

    def fake_get(path, params=None):
        _ = params
        if path == "/api/cluster":
            return {"name": "X20", "version": {"full": "ONTAP 9.9.1"}}
        if path == "/api/cluster/nodes":
            return {"records": [{"name": "X20-01", "model": "AFF-A250", "serial_number": "SN1", "state": "healthy", "version": {"full": "ONTAP 9.9.1"}, "ha": {}}]}
        if path == "/api/network/ethernet/ports":
            return {"records": [{"name": "a0a", "node": {"name": "X20-01"}, "broadcast_domain": {"name": "Data"}, "mtu": 9000, "type": "if_group"}]}
        if path == "/api/storage/aggregates":
            return {"records": [{"name": "aggr_01"}]}
        if path == "/api/svm/svms":
            return {"records": [{"name": "stage_nfs", "state": "running", "subtype": "default"}]}
        if path == "/api/network/ethernet/broadcast-domains":
            return {"records": [{"name": "Data", "mtu": 9000}]}
        if path == "/api/network/ip/interfaces":
            return {"records": [{"name": "stage_nfs_lif1", "svm": {"name": "stage_nfs"}, "ip": {"address": "10.10.8.51", "netmask": "255.255.255.0"}, "location": {"home_node": {"name": "X20-01"}, "home_port": {"name": "a0a"}}, "service_policy": {"name": "default-data-files"}, "enabled": True}]}
        if path == "/api/protocols/nfs/services":
            return {"records": []}
        if path == "/api/protocols/san/iscsi/services":
            return {"records": []}
        if path == "/api/protocols/nfs/export-policies":
            return {"records": [{"name": "stage_nfs_policy", "svm": {"name": "stage_nfs"}, "rules": [{}]}]}
        if path == "/api/protocols/san/igroups":
            return {"records": [{"name": "stage_esxi", "svm": {"name": "stage_nfs"}, "protocol": "iscsi", "os_type": "vmware"}]}
        if path == "/api/protocols/san/portsets":
            return {"records": []}
        if path == "/api/storage/luns":
            return {"records": [{"name": "esxi_lun01", "svm": {"name": "stage_nfs"}, "os_type": "vmware", "state": "online"}]}
        if path == "/api/protocols/san/lun-maps":
            return {"records": [{"lun": {"name": "esxi_lun01"}, "igroup": {"name": "stage_esxi"}, "logical_unit_number": 1}]}
        return {"records": []}

    client._get = fake_get  # type: ignore[method-assign]
    summary = client.build_discovery_summary()

    assert "nfs" in summary["enabled_protocols"]
    assert "iscsi" in summary["enabled_protocols"]
    assert summary["export_policy_details"][0]["name"] == "stage_nfs_policy"
    assert summary["igroup_details"][0]["protocol"] == "iscsi"
    assert summary["lun_map_details"][0]["igroup"] == "stage_esxi"


def test_discovery_summary_separates_cluster_and_node_management_ips():
    client = NetAppClient(NetAppConfig(host="netapp.local", username="admin", password="secret"))

    def fake_get(path, params=None):
        _ = params
        if path == "/api/cluster":
            return {"name": "X20", "version": {"full": "ONTAP 9.9.1"}}
        if path == "/api/cluster/nodes":
            return {"records": [
                {"name": "X20-01", "model": "AFF-A250", "serial_number": "SN1", "state": "healthy", "version": {"full": "ONTAP 9.9.1"}, "ha": {}},
                {"name": "X20-02", "model": "AFF-A250", "serial_number": "SN2", "state": "healthy", "version": {"full": "ONTAP 9.9.1"}, "ha": {}},
            ]}
        if path == "/api/network/ethernet/ports":
            return {"records": [{"name": "e0M", "node": {"name": "X20-01"}, "broadcast_domain": {"name": "Default"}, "mtu": 1500, "type": "physical"}]}
        if path == "/api/storage/aggregates":
            return {"records": []}
        if path == "/api/svm/svms":
            return {"records": [{"name": "stage_nfs", "state": "running", "subtype": "default"}]}
        if path == "/api/network/ethernet/broadcast-domains":
            return {"records": [{"name": "Default", "mtu": 1500}]}
        if path == "/api/network/ip/interfaces":
            return {"records": [
                {"name": "cluster_mgmt", "ip": {"address": "10.10.8.45", "netmask": "255.255.255.0"}, "location": {"home_node": {"name": "X20-01"}, "home_port": {"name": "e0M"}}, "service_policy": {"name": "default-management"}},
                {"name": "node_mgmt_01", "ip": {"address": "10.10.8.41", "netmask": "255.255.255.0"}, "location": {"home_node": {"name": "X20-01"}, "home_port": {"name": "e0M"}}, "service_policy": {"name": "default-management"}},
                {"name": "node_mgmt_02", "ip": {"address": "10.10.8.42", "netmask": "255.255.255.0"}, "location": {"home_node": {"name": "X20-02"}, "home_port": {"name": "e0M"}}, "service_policy": {"name": "default-management"}},
                {"name": "stage_nfs_lif1", "svm": {"name": "stage_nfs"}, "ip": {"address": "10.10.8.51", "netmask": "255.255.255.0"}, "location": {"home_node": {"name": "X20-01"}, "home_port": {"name": "a0a"}}, "service_policy": {"name": "default-data-files"}, "enabled": True},
            ]}
        return {"records": []}

    client._get = fake_get  # type: ignore[method-assign]
    summary = client.build_discovery_summary()

    assert summary["discovered_cluster_mgmt_ip"] == "10.10.8.45"
    assert summary["discovered_node_mgmt_ips"] == {"X20-01": "10.10.8.41", "X20-02": "10.10.8.42"}
    assert summary["discovered_nfs_lifs"][0]["address"] == "10.10.8.51"


def test_use_discovered_values_updates_connection_and_management_fields(client, monkeypatch):
    import app.modules.netapp.routes as netapp_routes

    class FakeNetAppClient:
        def get_cluster(self):
            return {"name": "X20"}

        def build_discovery_summary(self):
            return {
                "cluster_name": "X20",
                "ontap_version": "ONTAP 9.9.1",
                "node_count": 2,
                "node_names": ["X20-01", "X20-02"],
                "nodes": ["X20-01", "X20-02"],
                "available_ports": ["X20-01:e0M", "X20-02:e0M"],
                "existing_broadcast_domains": ["Default"],
                "enabled_protocols": ["nfs"],
                "discovered_cluster_mgmt_ip": "10.10.8.45",
                "discovered_node_mgmt_ips": {"X20-01": "10.10.8.41", "X20-02": "10.10.8.42"},
                "discovered_node_mgmt_ip_list": ["10.10.8.41", "10.10.8.42"],
                "discovered_nfs_lifs": [
                    {"name": "stage_nfs_lif1", "address": "10.10.8.51", "home_node": "X20-01", "home_port": "a0a"},
                    {"name": "stage_nfs_lif2", "address": "10.10.8.52", "home_node": "X20-02", "home_port": "a0a"},
                ],
                "warnings": [],
                "raw": {
                    "aggregates": [],
                    "svms": [],
                    "interfaces": [],
                    "licenses": [],
                    "ports": [],
                },
            }

    monkeypatch.setattr(netapp_routes.service, "_build_client", lambda context: FakeNetAppClient())
    cfg = main.load_kit_config()
    cfg["netapp"]["host"] = "10.10.8.45"
    cfg["netapp"]["bootstrap_complete"] = True
    cfg["netapp"]["bootstrap_checks"] = {"cluster_mgmt": {"reachable": True}}
    main.save_kit_config(cfg)

    response = client.post("/modules/netapp/use-discovered-values")

    assert response.status_code == 200
    cfg = main.load_kit_config()
    assert cfg["netapp"]["host"] == "10.10.8.45"
    assert cfg["netapp"]["management"]["node_01_mgmt_ip"] == "10.10.8.41"
    assert cfg["netapp"]["management"]["node_02_mgmt_ip"] == "10.10.8.42"
    assert cfg["netapp"]["cluster_name"] == "X20"
    assert cfg["netapp"]["nfs"]["lifs"][0]["address"] == "10.10.8.51"


def test_netapp_upgrade_plan_reads_live_version_from_current_page_fields(client, monkeypatch):
    import app.modules.netapp.routes as netapp_routes

    cfg = main.default_config()
    cfg["included"]["netapp"] = True
    cfg["netapp"]["host"] = "10.10.8.40"
    cfg["netapp"]["username"] = "admin"
    cfg["netapp"]["password"] = ""
    cfg["upgrade_inventory"] = {"netapp": {"current_version": "", "source": ""}}
    main.save_kit_config(cfg)

    class FakeNetAppClient:
        def __init__(self, config):
            assert config.host == "10.10.8.45"
            assert config.password == "secret"

        def get_cluster(self):
            return {"name": "X20", "version": {"full": "NetApp Release 9.9.1P2"}}

    monkeypatch.setattr(netapp_routes, "NetAppClient", FakeNetAppClient)
    monkeypatch.setattr(
        main,
        "scan_upgrade_media",
        lambda: {
            "root": "/repo/media",
            "latest": {"netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}},
            "counts": {"netapp": 2},
            "candidates": [
                {"device": "netapp", "version": "9.13.1", "filename": "9131_q_image.tgz", "path": "/repo/media/9131_q_image.tgz"},
                {"device": "netapp", "version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"},
            ],
        },
    )

    response = client.post(
        "/modules/netapp/plan-upgrade",
        data={
            "return_page": "netapp",
            "netapp_host": "10.10.8.45",
            "netapp_username": "admin",
            "netapp_password": "secret",
            "netapp_storage_protocol": "nfs",
        },
    )

    assert response.status_code == 200
    assert "ONTAP upgrade readiness checked" in response.text
    assert "NetApp Release 9.9.1P2" in response.text
    cfg = main.load_kit_config()
    assert cfg["netapp"]["host"] == "10.10.8.45"
    assert cfg["netapp"]["last_discovered_ontap_version"] == "NetApp Release 9.9.1P2"
    assert cfg["upgrade_inventory"]["netapp"]["source"] == "Live ONTAP upgrade readiness check"


def test_validate_stage_checks_nfs_export_policy_and_volume():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "storage_protocol": "nfs",
                "desired": {
                    "nfs": {
                        "export_policy": "KIT-01_ESXi_NFS",
                        "allowed_subnet": "10.10.10.0/24",
                        "volume": "esxi_datastore_01",
                    }
                },
            },
        }
    }
    discovery = {
        "nodes": ["Kit-01-01", "Kit-01-02"],
        "available_ports": ["Kit-01-01:a0a", "Kit-01-01:e0M", "Kit-01-02:a0a", "Kit-01-02:e0M"],
        "existing_broadcast_domains": ["Data"],
        "enabled_protocols": ["nfs"],
        "capabilities": {"licenses": True, "protocol_services": True, "export_policies": True, "volumes": True, "autosupport": False, "ntp_servers": False, "users": False},
        "raw": {
            "aggregates": [{"name": "aggr_01"}, {"name": "aggr_02"}],
            "svms": [{"name": "Kit-01-SVM"}],
            "interfaces": [],
            "licenses": [{"name": "nfs"}],
            "ports": [{"name": "a0a", "broadcast_domain": {"name": "Data"}, "mtu": 9000}],
            "export_policies": [{"name": "KIT-01_ESXi_NFS", "rules": [{"clients": [{"match": "10.10.10.0/24"}]}]}],
            "volumes": [{"name": "esxi_datastore_01"}],
        },
    }

    result = service._validate_stage(context, discovery)
    check_map = {item["name"]: item for item in result["stage"]["checks"]}

    assert check_map["nfs_export_policy_matches"]["ok"] is True
    assert check_map["nfs_volume_exists"]["ok"] is True


def test_plan_marks_iscsi_objects_skip_when_discovered():
    from app.modules.netapp.service import NetAppModuleService

    class FakeNetAppClient:
        def build_discovery_summary(self):
            return {
                "cluster_name": "X20",
                "ontap_version": "ONTAP 9.12.1",
                "nodes": ["Kit-01-01", "Kit-01-02"],
                "node_names": ["Kit-01-01", "Kit-01-02"],
                "available_ports": ["Kit-01-01:a0a", "Kit-01-01:e0M", "Kit-01-02:a0a", "Kit-01-02:e0M"],
                "existing_broadcast_domains": ["Data"],
                "enabled_protocols": ["iscsi"],
                "capabilities": {
                    "licenses": True,
                    "protocol_services": True,
                    "igroups": True,
                    "portsets": True,
                    "luns": True,
                    "lun_maps": True,
                    "autosupport": False,
                    "ntp_servers": False,
                    "users": False,
                },
                "warnings": [],
                "raw": {
                    "aggregates": [{"name": "aggr_01"}, {"name": "aggr_02"}],
                    "svms": [{"name": "Kit-01-SVM"}],
                    "interfaces": [],
                    "licenses": [{"name": "iscsi"}],
                    "ports": [{"name": "a0a", "broadcast_domain": {"name": "Data"}, "mtu": 9000}],
                    "igroups": [{"name": "Kit-01_ESXi_Servers"}],
                    "portsets": [{"name": "iSCSI"}],
                    "luns": [{"name": "vol1"}],
                    "lun_maps": [{"igroup": {"name": "Kit-01_ESXi_Servers"}, "lun": {"name": "vol1"}}],
                },
            }

    service = NetAppModuleService()
    service._build_client = lambda context: FakeNetAppClient()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "host": "10.10.10.45",
                "username": "admin",
                "password": "secret",
                "storage_protocol": "iscsi",
                "bootstrap_complete": True,
                "desired": {
                    "iscsi": {
                        "igroup": "Kit-01_ESXi_Servers",
                        "portset": "iSCSI",
                        "volumes": [{"volume_name": "vol1"}],
                    }
                },
            },
        }
    }

    payload = service.plan(context)
    actions = {item["name"]: item["status"] for item in payload["plan"]["protocol_profile"]["actions"]}

    assert actions["ensure_iscsi_portset"] == "skip"
    assert actions["ensure_iscsi_igroup"] == "skip"
    assert actions["ensure_netapp_volumes"] == "skip"


def test_validate_stage_marks_protocol_lifs_matching_for_nfs():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"cluster_mgmt_ip": "10.10.10.45", "svm_mgmt_ip": "10.10.10.48", "gateway": "10.10.10.1"},
            "netapp": {"storage_protocol": "nfs"},
        }
    }
    discovery = {
        "nodes": ["Kit-01-01", "Kit-01-02"],
        "available_ports": ["Kit-01-01:a0a", "Kit-01-01:e0M", "Kit-01-02:a0a", "Kit-01-02:e0M"],
        "existing_broadcast_domains": ["Data"],
        "enabled_protocols": ["nfs"],
        "lif_details": [
            {"name": "Kit-01-01_nfs_lif_1", "address": "10.10.10.45", "home_node": "Kit-01-01", "home_port": "a0a"},
            {"name": "Kit-01-02_nfs_lif_1", "address": "10.10.10.48", "home_node": "Kit-01-02", "home_port": "a0a"},
        ],
        "capabilities": {"licenses": True, "protocol_services": True, "network_interfaces": True, "autosupport": False, "ntp_servers": False, "users": False},
        "raw": {
            "aggregates": [{"name": "aggr_01"}, {"name": "aggr_02"}],
            "svms": [{"name": "Kit-01-SVM"}],
            "interfaces": [],
            "licenses": [{"name": "nfs"}],
            "ports": [{"name": "a0a", "broadcast_domain": {"name": "Data"}, "mtu": 9000}],
        },
    }

    result = service._validate_stage(context, discovery)
    check_map = {item["name"]: item for item in result["stage"]["checks"]}

    assert check_map["protocol_lifs_match"]["ok"] is True


def test_plan_keeps_iscsi_lif_action_create_when_discovered_lifs_conflict():
    from app.modules.netapp.service import NetAppModuleService

    class FakeNetAppClient:
        def build_discovery_summary(self):
            return {
                "cluster_name": "X20",
                "ontap_version": "ONTAP 9.12.1",
                "nodes": ["Kit-01-01", "Kit-01-02"],
                "node_names": ["Kit-01-01", "Kit-01-02"],
                "available_ports": ["Kit-01-01:a0a", "Kit-01-01:e0M", "Kit-01-02:a0a", "Kit-01-02:e0M"],
                "existing_broadcast_domains": ["Data"],
                "enabled_protocols": ["iscsi"],
                "lif_details": [
                    {"name": "Kit-01-01_iscsi_lif_1", "address": "192.168.1.99", "home_node": "Kit-01-01", "home_port": "a0a"},
                ],
                "capabilities": {
                    "licenses": True,
                    "protocol_services": True,
                    "igroups": True,
                    "portsets": True,
                    "luns": True,
                    "lun_maps": True,
                    "network_interfaces": True,
                    "autosupport": False,
                    "ntp_servers": False,
                    "users": False,
                },
                "warnings": [],
                "raw": {
                    "aggregates": [{"name": "aggr_01"}, {"name": "aggr_02"}],
                    "svms": [{"name": "Kit-01-SVM"}],
                    "interfaces": [],
                    "licenses": [{"name": "iscsi"}],
                    "ports": [{"name": "a0a", "broadcast_domain": {"name": "Data"}, "mtu": 9000}],
                    "igroups": [],
                    "portsets": [],
                    "luns": [],
                    "lun_maps": [],
                },
            }

    service = NetAppModuleService()
    service._build_client = lambda context: FakeNetAppClient()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "host": "10.10.10.45",
                "username": "admin",
                "password": "secret",
                "storage_protocol": "iscsi",
                "bootstrap_complete": True,
            },
        }
    }

    payload = service.plan(context)
    actions = {item["name"]: item["status"] for item in payload["plan"]["protocol_profile"]["actions"]}
    validation = {item["name"]: item for item in payload["validation_checks"]}

    assert validation["protocol_lifs_match"]["ok"] is False
    assert actions["ensure_iscsi_lifs"] == "create"


def test_calc_ip_plan_includes_netapp_bootstrap_addresses():
    cfg = {"shared_network": {"subnet": "10.10.3.0/24"}}

    plan = calc_ip_plan(cfg)

    assert plan["netapp_sp_a"] == "10.10.3.13"
    assert plan["netapp_sp_b"] == "10.10.3.14"
    assert plan["netapp_cluster_mgmt"] == "10.10.3.45"
    assert plan["netapp_node_01_mgmt"] == "10.10.3.46"
    assert plan["netapp_node_02_mgmt"] == "10.10.3.47"
    assert plan["netapp_svm_mgmt"] == "10.10.3.48"


def test_bootstrap_checklist_uses_generated_ip_plan_values():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "KIT100"},
            "shared_network": {"subnet": "10.10.3.0/24"},
            "ip_plan": {
                "subnet": "10.10.3.0/24",
                "netmask": "255.255.255.0",
                "gateway": "10.10.3.1",
                "netapp_sp_a": "10.10.3.13",
                "netapp_sp_b": "10.10.3.14",
                "netapp_cluster_mgmt": "10.10.3.45",
                "netapp_node_01_mgmt": "10.10.3.46",
                "netapp_node_02_mgmt": "10.10.3.47",
                "netapp_svm_mgmt": "10.10.3.48",
            },
            "netapp": {"password": "secret"},
        }
    }

    checklist = service._build_bootstrap_checklist(context)
    all_lines = " ".join(" ".join(section["items"]) for section in checklist)

    assert "10.10.3.13" in all_lines
    assert "10.10.3.14" in all_lines
    assert "10.10.3.45" in all_lines
    assert "10.10.3.46" in all_lines
    assert "10.10.3.47" in all_lines


def test_safe_apply_blocks_missing_subnet_api_surface_instead_of_failing():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    service._build_client = lambda context: object()

    def fail_subnet(*args, **kwargs):
        raise NetAppError('POST /api/network/ip/subnets failed (404): {"error":{"message":"API not found","code":"3"}}')

    service._ensure_subnet = fail_subnet
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "host": "10.10.10.45",
                "username": "admin",
                "password": "secret",
                "storage_protocol": "nfs",
                "bootstrap_complete": True,
            },
        }
    }
    payload = {
        "discovery": {"subnets": []},
        "plan": {
            "protocol_profile": {
                "actions": [
                    {"name": "ensure_management_subnet", "status": "create"},
                ]
            }
        },
    }

    result = service._execute_safe_apply(context, payload)

    assert result["ok"] is True
    assert result["result"] == "no_changes"
    assert "ensure_management_subnet" in result["blocked_actions"]
    assert any("not supported through the current ONTAP API surface" in line for line in result["logs"])


def test_action_plan_adopts_single_discovered_svm_when_config_is_blank():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "host": "10.10.10.45",
                "username": "admin",
                "password": "secret",
                "storage_protocol": "nfs",
                "svm_name": "",
                "desired": {"svm_name": ""},
            },
        }
    }
    discovery = {
        "raw": {"svms": [{"name": "stage_nfs"}]},
        "svms": ["stage_nfs"],
        "capability_status": {},
    }
    validate_stage = {
        "checks": [
            {"name": "ontap_meets_baseline", "ok": True, "details": {}},
            {"name": "svm_exists_and_protocol_matches", "ok": False, "details": {"exists": False}},
            {"name": "svm_management_lif_exists", "ok": False, "details": {}},
            {"name": "nfs_volume_exists", "ok": False, "details": {}},
            {"name": "nfs_export_policy_matches", "ok": False, "details": {}},
            {"name": "expected_ports_exist", "ok": True, "details": {}},
            {"name": "protocol_lifs_match", "ok": False, "details": {}},
            {"name": "data_broadcast_domain_exists", "ok": True, "details": {}},
            {"name": "aggregates_exist_or_can_be_created", "ok": True, "details": {"missing": []}},
        ]
    }

    plan = service._build_action_plan(context, discovery, validate_stage)
    statuses = {item["name"]: item["status"] for item in plan["actions"]}

    assert statuses["ensure_svm"] == "skip"
    assert statuses["ensure_nfs_service"] == "update"
    assert statuses["ensure_nfs_volume"] == "create"
    assert statuses["ensure_export_policy"] == "create"


def test_action_plan_blocks_nfs_volume_when_aggregate_is_missing():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "host": "10.10.10.45",
                "username": "admin",
                "password": "secret",
                "storage_protocol": "nfs",
                "svm_name": "",
                "desired": {"svm_name": ""},
            },
        }
    }
    discovery = {
        "raw": {"svms": [{"name": "stage_nfs"}]},
        "svms": ["stage_nfs"],
        "capability_status": {},
    }
    validate_stage = {
        "checks": [
            {"name": "ontap_meets_baseline", "ok": True, "details": {}},
            {"name": "svm_exists_and_protocol_matches", "ok": False, "details": {"exists": False}},
            {"name": "svm_management_lif_exists", "ok": False, "details": {}},
            {"name": "nfs_volume_exists", "ok": False, "details": {}},
            {"name": "nfs_export_policy_matches", "ok": False, "details": {}},
            {"name": "expected_ports_exist", "ok": True, "details": {}},
            {"name": "protocol_lifs_match", "ok": False, "details": {}},
            {"name": "data_broadcast_domain_exists", "ok": True, "details": {}},
            {"name": "aggregates_exist_or_can_be_created", "ok": False, "details": {"missing": ["aggr_01"]}},
        ]
    }

    plan = service._build_action_plan(context, discovery, validate_stage)
    statuses = {item["name"]: item["status"] for item in plan["actions"]}

    assert statuses["ensure_nfs_volume"] == "manual"


def test_validation_adopts_discovered_aggregates_when_legacy_defaults_are_missing():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "host": "10.10.10.45",
                "username": "admin",
                "password": "secret",
                "storage_protocol": "nfs",
                "svm_name": "",
                "desired": {"svm_name": ""},
            },
        }
    }
    discovery = {
        "raw": {
            "aggregates": [
                {"name": "X20_01_NVME_SSD_1", "node": {"name": "X20-01"}, "state": "online"},
                {"name": "X20_02_NVME_SSD_1", "node": {"name": "X20-02"}, "state": "online"},
            ],
            "svms": [{"name": "stage_nfs", "allowed_protocols": ["nfs"]}],
            "volumes": [],
            "interfaces": [],
            "licenses": [{"name": "nfs"}],
            "ports": [],
            "users": [],
        },
        "capabilities": {"volumes": True, "export_policies": True, "protocol_services": True, "licenses": True, "network_interfaces": True},
        "node_names": ["X20-01", "X20-02"],
        "nodes": ["X20-01", "X20-02"],
        "svms": ["stage_nfs"],
        "existing_broadcast_domains": ["Default"],
        "subnets": [],
        "enabled_protocols": ["nfs"],
        "lif_details": [],
    }

    result = service._validate_stage(context, discovery)
    checks = {item["name"]: item for item in result["stage"]["checks"]}

    assert checks["aggregates_exist_or_can_be_created"]["details"]["resolved"] == ["X20_01_NVME_SSD_1", "X20_02_NVME_SSD_1"]
    assert checks["aggregates_exist_or_can_be_created"]["details"]["missing"] == []
    assert checks["svm_exists_and_protocol_matches"]["details"]["svm"] == "stage_nfs"


def test_validation_adopts_discovered_broadcast_domain_for_protocol_lifs():
    from app.modules.netapp.service import NetAppModuleService

    service = NetAppModuleService()
    context = {
        "cfg": {
            "site": {"name": "Kit-01"},
            "shared_network": {"subnet": "10.10.10.0/24"},
            "ip_plan": {"gateway": "10.10.10.1"},
            "netapp": {
                "host": "10.10.10.45",
                "username": "admin",
                "password": "secret",
                "storage_protocol": "nfs",
            },
        }
    }
    discovery = {
        "raw": {
            "aggregates": [],
            "svms": [{"name": "stage_nfs", "allowed_protocols": ["nfs"]}],
            "volumes": [],
            "interfaces": [],
            "licenses": [{"name": "nfs"}],
            "ports": [],
            "users": [],
            "broadcast_domains": [
                {
                    "name": "NFS_BD",
                    "ports": [
                        {"name": "e0b", "node": {"name": "X20-01"}},
                        {"name": "e0b", "node": {"name": "X20-02"}},
                    ],
                    "mtu": 1500,
                }
            ],
        },
        "capabilities": {"volumes": True, "export_policies": True, "protocol_services": True, "licenses": True, "network_interfaces": True},
        "node_names": ["X20-01", "X20-02"],
        "nodes": ["X20-01", "X20-02"],
        "svms": ["stage_nfs"],
        "existing_broadcast_domains": ["NFS_BD", "Default"],
        "subnets": [],
        "enabled_protocols": ["nfs"],
        "lif_details": [],
        "discovered_nfs_lifs": [
            {"name": "nfs_lif_01", "home_node": "X20-01", "home_port": "e0b"},
            {"name": "nfs_lif_02", "home_node": "X20-02", "home_port": "e0b"},
        ],
    }

    result = service._validate_stage(context, discovery)
    checks = {item["name"]: item for item in result["stage"]["checks"]}

    assert checks["data_broadcast_domain_exists"]["ok"] is True
    assert checks["data_broadcast_domain_exists"]["details"]["desired"] == "NFS_BD"
    assert checks["data_broadcast_domain_exists"]["details"]["adopted"] is True


def test_disabled_netapp_does_not_break_other_modules(tmp_path, monkeypatch):
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "__init__.py").write_text("", encoding="utf-8")

    other = modules_dir / "other"
    other.mkdir()
    (other / "__init__.py").write_text("", encoding="utf-8")
    (other / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "name": "other",
                "title": "Other Module",
                "enabled": True,
                "routes": {"prefix": "/modules/other"},
                "navigation": {"label": "Other", "href": "/modules/other", "active_page": "other"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (other / "routes.py").write_text(
        "from fastapi import APIRouter, FastAPI, Request\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/modules/other')\n"
        "async def other_module_page(request: Request):\n"
        "    _ = request\n    \n"
        "    return 'other'\n\n"
        "def register_module_routes(app: FastAPI) -> None:\n"
        "    app.state.other_route_registered = True\n"
        "    app.include_router(router)\n",
        encoding="utf-8",
    )

    netapp = modules_dir / "netapp"
    netapp.mkdir()
    (netapp / "__init__.py").write_text("", encoding="utf-8")
    (netapp / "manifest.yml").write_text(
        yaml.safe_dump({"name": "netapp", "title": "NetApp", "enabled": True, "routes": {"prefix": "/modules/netapp"}, "navigation": {"label": "NetApp", "href": "/modules/netapp", "active_page": "netapp"}}, sort_keys=False),
        encoding="utf-8",
    )
    (netapp / "routes.py").write_text(
        "from fastapi import FastAPI\n\n"
        "def register_module_routes(app: FastAPI) -> None:\n"
        "    app.state.netapp_route_registered = True\n",
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("LAB_BUILDER_DISABLED_MODULES", "netapp")
    monkeypatch.delenv("LAB_BUILDER_ENABLED_MODULES", raising=False)

    test_app = FastAPI()
    manifests = load_modules(test_app, modules_dir=modules_dir, package_root="modules")

    assert {item["name"]: item["enabled"] for item in manifests} == {"netapp": False, "other": True}
    assert getattr(test_app.state, "other_route_registered", False) is True
    assert not getattr(test_app.state, "netapp_route_registered", False)
    assert any(route.path == "/modules/other" for route in test_app.router.routes)
