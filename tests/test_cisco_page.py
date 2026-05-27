from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture()
def cisco_client(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    media_dir = tmp_path / "media"
    artifacts_dir = tmp_path / "artifacts"
    exports_dir = artifacts_dir / "exports"
    paths = {
        "CONFIG_DIR": config_dir,
        "KITS_DIR": config_dir / "kits",
        "CURRENT_KIT_FILE": config_dir / "current_kit.txt",
        "MEDIA_DIR": media_dir,
        "FIRMWARE_UPLOAD_DIR": media_dir / "firmware",
        "ARTIFACTS_DIR": artifacts_dir,
        "GENERATED_DIR": artifacts_dir / "generated",
        "JOBS_DIR": artifacts_dir / "jobs",
        "HISTORY_DIR": artifacts_dir / "history",
        "RUNS_DIR": artifacts_dir / "runs",
        "EXPORTS_DIR": exports_dir,
        "BUILD_OUTPUT_DIR": exports_dir / "builds",
        "ILO_CONFIG_EXPORT_DIR": artifacts_dir / "history" / "ilo-configs",
        "CONFIG_EXPORT_DIR": artifacts_dir / "history" / "configs",
        "LIVE_ILO_CONFIG_DIR": artifacts_dir / "history" / "ilo-live-configs",
        "ILO_INVENTORY_DIR": artifacts_dir / "history" / "ilo-inventory",
        "ILO_LIVE_EXPORT_DIR": exports_dir / "ilo" / "live",
        "STORAGE_RAID_EXPORT_DIR": exports_dir / "storage-raid",
        "DEBUG_BUNDLES_DIR": artifacts_dir / "debug-bundles",
    }
    for value in paths.values():
        if isinstance(value, Path) and value.suffix == "":
            value.mkdir(parents=True, exist_ok=True)
    for name, value in paths.items():
        monkeypatch.setattr(main, name, value)
    monkeypatch.setenv("LAB_BUILDER_VALIDATE_ESXI_MEDIA_URL", "0")
    monkeypatch.setenv("LAB_BUILDER_LIVE_RUN_CENTER_CHECKS", "0")
    monkeypatch.setattr(
        main,
        "scan_upgrade_media",
        lambda: {"root": str(media_dir), "latest": {}, "counts": {}, "candidates": []},
    )
    main.set_current_kit_name("Cisco-Page-Test-Kit")

    with TestClient(main.app) as test_client:
        yield test_client


def test_cisco_console_current_config_and_version_actions_use_shared_feedback_metadata(cisco_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Page Test Kit"
    cfg["cisco_switch"].update(
        {
            "console_port": "/dev/ttyUSB0",
            "console_baud": 9600,
            "username": "admin",
            "password": "CiscoSecret123!",
        }
    )
    main.save_kit_config(cfg)

    response = cisco_client.get("/cisco")

    assert response.status_code == 200
    assert 'hx-post="/modules/cisco/test-console-access"' in response.text
    assert 'hx-post="/modules/cisco/trust-console-adapter"' in response.text
    assert 'hx-post="/modules/cisco/check-current-config"' in response.text
    assert 'hx-post="/modules/cisco/use-discovered-values"' in response.text
    assert 'hx-post="/modules/cisco/test-ssh"' in response.text
    assert 'hx-post="/modules/cisco/discover-version"' in response.text
    assert 'class="btn action-button" type="button" hx-post="/modules/cisco/test-console-access"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/trust-console-adapter"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/check-current-config"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/use-discovered-values"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/test-ssh"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/discover-version"' in response.text
    assert 'data-action-title="Testing Cisco console access"' in response.text
    assert "Checking the selected serial console adapter without changing switch configuration." in response.text
    assert 'data-action-title="Trusting Cisco console adapter"' in response.text
    assert "Saving the selected serial console adapter for this kit." in response.text
    assert 'data-action-title="Checking Cisco current config"' in response.text
    assert "Reading VLAN, management IP, gateway, SSH, and SCP from the selected console path." in response.text
    assert 'data-action-complete="Cisco current config check finished."' in response.text
    assert 'data-action-title="Saving Cisco discovered values"' in response.text
    assert "Copying the latest console-discovered values into this kit." in response.text
    assert 'data-action-title="Testing Cisco SSH"' in response.text
    assert "Connecting to the saved Cisco management IP with the saved switch credentials." in response.text
    assert 'data-action-complete="Cisco SSH test finished."' in response.text
    assert 'data-action-title="Reading Cisco version"' in response.text
    assert "Reading the current switch software version for Upgrade Helper and Run Center approval." in response.text
    assert 'data-action-complete="Cisco version check finished."' in response.text
    assert "Cisco operator checkpoint" in response.text
    assert "Operator Mode" in response.text
    assert 'id="cisco-operator-mode"' in response.text
    assert 'href="#cisco-debug-details"' in response.text
    assert "Open Debug Mode/details" in response.text
    assert "Next step" in response.text
    assert "Completion state" in response.text
    assert "Last result" in response.text
    assert "Logs/status" in response.text
    assert 'id="cisco-debug-details"' in response.text
    assert "Debug Mode/details" in response.text
    assert "logs/status" in response.text
    assert "Recovery suggestions" in response.text
    assert "choose 0 at the final wizard menu" in response.text
    assert "never choose 2" in response.text
    assert "should not save startup config until CLI configuration succeeds" in response.text
    operator_section = response.text.split('id="cisco-operator-mode"', 1)[1].split("</section>", 1)[0]
    debug_section = response.text.split('id="cisco-debug-details"', 1)[1]
    assert "Console selected; no action log yet" in operator_section
    assert "choose 0 at the final wizard menu" not in operator_section
    assert "never choose 2" not in operator_section
    assert "choose 0 at the final wizard menu" in debug_section
    assert "never choose 2" in debug_section
    assert "CiscoSecret123!" not in response.text

    assert any(
        route.path == "/modules/cisco/test-console-access" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/trust-console-adapter" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/check-current-config" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/use-discovered-values" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/test-ssh" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/discover-version" and "POST" in route.methods
        for route in main.app.routes
    )


def test_cisco_run_approval_actions_use_shared_feedback_metadata(cisco_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Run Approval Test Kit"
    main.save_kit_config(cfg)

    response = cisco_client.get("/cisco")

    assert response.status_code == 200
    assert 'hx-post="/modules/cisco/save-port-map"' in response.text
    assert 'hx-post="/modules/cisco/approve-config-plan"' in response.text
    assert 'class="btn action-button" type="submit" hx-post="/modules/cisco/save-port-map"' in response.text
    assert 'class="btn btn-primary action-button" type="submit" hx-post="/modules/cisco/approve-config-plan"' in response.text
    assert 'data-action-title="Saving Cisco config"' in response.text
    assert "Saving the desired Cisco switch config and SNMP values." in response.text
    assert 'data-action-complete="Cisco config save finished."' in response.text
    assert 'data-action-title="Testing Cisco SSH for approval"' in response.text
    assert "Checking SSH with the saved Cisco management IP and credentials before approval." in response.text
    assert 'data-action-title="Approving Cisco config"' in response.text
    assert "Validating SSH, version, upgrade gate, and saved config before Run Center approval." in response.text
    assert 'data-action-complete="Cisco config approval check finished."' in response.text

    assert any(
        route.path == "/modules/cisco/save-port-map" and "POST" in route.methods
        for route in main.app.routes
    )
    assert any(
        route.path == "/modules/cisco/approve-config-plan" and "POST" in route.methods
        for route in main.app.routes
    )


def test_cisco_access_settings_show_password_policy_constraints(cisco_client):
    response = cisco_client.get("/cisco")

    assert response.status_code == 200
    assert 'name="cisco_switch_password"' in response.text
    assert 'name="cisco_enable_secret"' in response.text
    assert response.text.count('minlength="10"') >= 2
    assert response.text.count(r'pattern="(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{10,}"') >= 2
    assert response.text.count("Use at least 10 characters with uppercase, lowercase, and a digit.") >= 2


def test_cisco_page_does_not_render_saved_access_or_snmp_secrets(cisco_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Secret Render Kit"
    cfg["cisco_switch"].update(
        {
            "hostname": "sw-secret",
            "username": "admin",
            "password": "CiscoLoginSecret1!",
            "console_password": "CiscoConsoleSecret1!",
            "enable_password": "CiscoEnableSecret1!",
            "management_ip": "192.168.1.2",
            "snmp": {
                "v3_username": "snmpuser",
                "v3_auth_password": "CiscoSnmpAuthSecret1!",
                "v3_priv_password": "CiscoSnmpPrivSecret1!",
            },
            "last_serial_output": "Password: CiscoLoginSecret1!\nenable secret CiscoEnableSecret1!",
            "last_console_management_state": "Console asked for CiscoConsoleSecret1!",
            "last_console_diagnostics": {"raw": "Console diagnostics echoed CiscoConsoleSecret1!"},
            "last_console_bootstrap_check": {"warnings": ["Bootstrap check saw CiscoSnmpAuthSecret1!"]},
            "last_raw_port_discovery": "Port discovery printed CiscoLoginSecret1!",
            "last_config_preview": "username admin privilege 15 secret CiscoLoginSecret1!\nenable secret CiscoEnableSecret1!\nsnmp auth CiscoSnmpAuthSecret1!",
            "last_cisco_action": {"ok": False, "error": "Action saw CiscoEnableSecret1!", "log_excerpt": "Console saw CiscoConsoleSecret1!"},
            "last_running_config_backup": "enable secret CiscoEnableSecret1!\nsnmp priv CiscoSnmpPrivSecret1!",
        }
    )
    main.save_kit_config(cfg)

    response = cisco_client.get("/cisco")

    assert response.status_code == 200
    assert "sw-secret" in response.text
    assert "snmpuser" in response.text
    for secret in [
        "CiscoLoginSecret1!",
        "CiscoConsoleSecret1!",
        "CiscoEnableSecret1!",
        "CiscoSnmpAuthSecret1!",
        "CiscoSnmpPrivSecret1!",
    ]:
        assert secret not in response.text
    assert "Saved - leave blank to keep" in response.text
    assert "********" in response.text
    debug_section_index = response.text.index('id="cisco-debug-details"')
    assert response.text.index("Last action log excerpt") > debug_section_index
    assert "Console saw ********" in response.text
    assert "Last log excerpt" not in response.text


def test_cisco_routes_redact_result_output_before_saving(cisco_client, monkeypatch):
    import app.modules.cisco.routes as cisco_routes
    from app.modules.cisco.service import CiscoModuleService

    real_service = CiscoModuleService()
    secrets = [
        "ValidSecret123",
        "CiscoConsoleSecret1!",
        "CiscoEnableSecret1!",
        "CiscoSnmpAuthSecret1!",
        "CiscoSnmpPrivSecret1!",
        "SharedSnmpAuthSecret1!",
        "SharedSnmpPrivSecret1!",
    ]

    class FakeCiscoRouteService:
        def status(self, context):
            return real_service.status(context)

        def test_ssh(self, _context):
            return {
                "ok": True,
                "host": "192.168.1.2",
                "raw_excerpt": "show version echoed ValidSecret123 and CiscoSnmpAuthSecret1!",
            }

        def discover(self, _context):
            return {
                "ok": True,
                "version": "",
                "model": "C9300 CiscoSnmpPrivSecret1!",
                "platform": "IOS-XE",
                "hostname": "sw01 SharedSnmpAuthSecret1!",
                "raw_excerpt": "show version echoed CiscoSnmpAuthSecret1!",
            }

        def discover_ports(self, _context):
            return {
                "ok": True,
                "host": "192.168.1.2",
                "discovery": {
                    "interfaces": {
                        "GigabitEthernet1/0/1": {
                            "status": "connected",
                            "description": "uplink CiscoSnmpPrivSecret1!",
                        }
                    }
                },
                "raw_output": "show interface output echoed SharedSnmpPrivSecret1!",
            }

        def backup_config(self, _context):
            return {
                "ok": True,
                "host": "192.168.1.2",
                "running_config": "enable secret CiscoEnableSecret1!\nsnmp-server user snmp CiscoSnmpAuthSecret1! CiscoSnmpPrivSecret1!",
            }

        def preview_config(self, _context, *, mode="full", selected_ports=None):
            return {
                "ok": True,
                "mode": mode,
                "config": "username admin privilege 15 secret ValidSecret123\nsnmp-server user snmp CiscoSnmpAuthSecret1! CiscoSnmpPrivSecret1!",
                "validation": {
                    "ok": True,
                    "warnings": ["Preview validation echoed CiscoSnmpAuthSecret1!"],
                    "errors": [],
                },
            }

        def apply_config(self, _context, *, mode="full", selected_ports=None):
            return {
                "ok": False,
                "applied": False,
                "mode": mode,
                "config": "username admin privilege 15 secret ValidSecret123\nsnmp-server user snmp CiscoSnmpAuthSecret1! CiscoSnmpPrivSecret1!",
                "error": "Apply output echoed CiscoSnmpPrivSecret1!",
                "validation": {"ok": False, "errors": ["Validation saw CiscoEnableSecret1!"], "warnings": []},
            }

    monkeypatch.setattr(cisco_routes, "service", FakeCiscoRouteService())
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Route Saved-State Redaction Test Kit"
    cfg["shared_snmp"] = {
        "v3_auth_password": "SharedSnmpAuthSecret1!",
        "v3_priv_password": "SharedSnmpPrivSecret1!",
    }
    cfg["cisco_switch"].update(
        {
            "hostname": "sw01",
            "username": "admin",
            "password": "ValidSecret123",
            "console_password": "CiscoConsoleSecret1!",
            "enable_password": "CiscoEnableSecret1!",
            "management_ip": "192.168.1.2",
            "snmp": {
                "v3_username": "snmp",
                "v3_auth_password": "CiscoSnmpAuthSecret1!",
                "v3_priv_password": "CiscoSnmpPrivSecret1!",
            },
        }
    )
    main.save_kit_config(cfg)

    access_form = {
        "cisco_switch_hostname": "sw01",
        "cisco_switch_username": "admin",
        "cisco_switch_password": "ValidSecret123",
        "cisco_console_password": "CiscoConsoleSecret1!",
        "cisco_enable_secret": "CiscoEnableSecret1!",
        "cisco_management_ip": "192.168.1.2",
    }
    model_form = {
        **access_form,
        "cisco_snmp_v3_username": "snmp",
        "cisco_snmp_v3_auth_password": "CiscoSnmpAuthSecret1!",
        "cisco_snmp_v3_priv_password": "CiscoSnmpPrivSecret1!",
    }

    responses = [
        cisco_client.post("/modules/cisco/test-ssh", data=access_form),
        cisco_client.post("/modules/cisco/discover-state", data=access_form),
        cisco_client.post("/modules/cisco/preview-config", data=model_form),
        cisco_client.post("/modules/cisco/backup-config", data=access_form),
        cisco_client.post("/modules/cisco/apply-config", data=model_form),
    ]

    for response in responses:
        assert response.status_code == 200
        for secret in secrets:
            assert secret not in response.text

    saved = main.load_kit_config()
    cisco_saved = saved["cisco_switch"]
    assert cisco_saved["password"] == "ValidSecret123"
    assert cisco_saved["snmp"]["v3_auth_password"] == "CiscoSnmpAuthSecret1!"

    for field in [
        "last_ssh_test",
        "last_show_version",
        "last_discovered_model",
        "last_discovered_hostname",
        "last_port_discovery",
        "last_raw_port_discovery",
        "last_running_config_backup",
        "last_config_preview",
        "last_config_validation",
        "last_cisco_action",
    ]:
        rendered = repr(cisco_saved.get(field))
        for secret in secrets:
            assert secret not in rendered
    assert "********" in cisco_saved["last_show_version"]
    assert "********" in cisco_saved["last_discovered_model"]
    assert "********" in cisco_saved["last_discovered_hostname"]
    assert "********" in repr(cisco_saved["last_port_discovery"])
    assert "********" in cisco_saved["last_raw_port_discovery"]
    assert "********" in cisco_saved["last_running_config_backup"]
    assert "********" in cisco_saved["last_config_preview"]
    assert "********" in repr(cisco_saved["last_config_validation"])
    assert "********" in repr(cisco_saved["last_cisco_action"])

    inventory = repr((saved.get("upgrade_inventory") or {}).get("cisco_switch") or {})
    for secret in secrets:
        assert secret not in inventory
    assert "********" in inventory


def test_cisco_factory_reset_requires_confirmation_before_reset_paths(cisco_client, monkeypatch):
    import app.modules.cisco.routes as cisco_routes

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Factory reset path must not run without typed confirmation")

    monkeypatch.setattr(cisco_routes.service, "factory_reset_console", fail_if_called)
    monkeypatch.setattr(cisco_routes, "execute_cisco_factory_reset", fail_if_called)

    page = cisco_client.get("/cisco")

    assert page.status_code == 200
    assert "Factory reset switch" in page.text
    assert '<span class="status danger">manual only</span>' in page.text
    assert '<span class="status danger">destructive</span>' in page.text
    assert "Manual operator-triggered action." in page.text
    assert "Type FACTORY RESET to confirm" in page.text
    assert 'hx-post="/modules/cisco/factory-reset"' in page.text

    response = cisco_client.post(
        "/modules/cisco/factory-reset",
        data={
            "cisco_factory_reset_confirm": "reset",
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "ValidSecret123",
            "cisco_enable_secret": "ValidSecret123",
            "cisco_management_ip": "192.168.1.2",
            "cisco_subnet_mask": "255.255.255.0",
            "cisco_gateway": "192.168.1.1",
            "cisco_domain_name": "lab.local",
            "cisco_management_vlan": "10",
            "cisco_console_port": "/dev/ttyUSB0",
            "cisco_console_baud": "9600",
            "cisco_management_port": "GigabitEthernet1/0/1",
            "cisco_management_port_mode": "do_not_touch",
        },
    )

    assert response.status_code == 200
    assert "Cisco factory reset blocked" in response.text
    assert "Type FACTORY RESET to confirm deleting startup-config" in response.text
    saved = main.load_kit_config()
    assert saved["cisco_switch"]["last_factory_reset"]["status"] == "blocked"


def test_cisco_factory_reset_redacts_confirmed_console_result(cisco_client, monkeypatch):
    import app.modules.cisco.routes as cisco_routes

    def fake_console_reset(_context):
        return {
            "status": "reload_issued",
            "source": "console",
            "steps": ["Factory reset used enable secret ValidSecret123."],
            "output": "Password: ValidSecret123\nwrite erase\nSwitch#",
            "output_excerpt": "Password: ValidSecret123\nwrite erase",
        }

    def fail_ssh_reset(*_args, **_kwargs):
        raise AssertionError("Confirmed console factory reset test must not use SSH")

    monkeypatch.setattr(cisco_routes.service, "factory_reset_console", fake_console_reset)
    monkeypatch.setattr(cisco_routes, "execute_cisco_factory_reset", fail_ssh_reset)
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Factory Reset Redaction Test Kit"
    cfg["cisco_switch"].update(
        {
            "last_console_bootstrap_check": {"ok": True, "current_management_ip": "192.168.1.2"},
            "last_running_config_backup": "interface Vlan10",
            "config_approval": {"state": "approved", "summary": "Previously approved."},
        }
    )
    main.save_kit_config(cfg)

    response = cisco_client.post(
        "/modules/cisco/factory-reset",
        data={
            "cisco_factory_reset_confirm": "FACTORY RESET",
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "ValidSecret123",
            "cisco_enable_secret": "ValidSecret123",
            "cisco_management_ip": "192.168.1.2",
            "cisco_subnet_mask": "255.255.255.0",
            "cisco_gateway": "192.168.1.1",
            "cisco_domain_name": "lab.local",
            "cisco_management_vlan": "10",
            "cisco_console_port": "/dev/ttyUSB0",
            "cisco_console_baud": "9600",
            "cisco_management_port": "GigabitEthernet1/0/1",
            "cisco_management_port_mode": "do_not_touch",
        },
    )

    assert response.status_code == 200
    assert "Cisco factory reset issued" in response.text
    assert "ValidSecret123" not in response.text
    saved = main.load_kit_config()
    assert saved["cisco_switch"]["last_factory_reset"]["status"] == "reload_issued"
    assert saved["cisco_switch"]["connection_method"] == "console"
    assert saved["cisco_switch"]["last_console_bootstrap_check"] == {}
    assert saved["cisco_switch"]["last_running_config_backup"] == ""
    assert saved["cisco_switch"]["config_approval"]["state"] == "blocked"
    assert "Factory reset was issued" in saved["cisco_switch"]["config_approval"]["blockers"][0]
    for key in ["last_serial_output", "last_factory_reset", "last_cisco_action"]:
        assert "ValidSecret123" not in repr(saved["cisco_switch"].get(key))
    assert "********" in saved["cisco_switch"]["last_serial_output"]
    assert "********" in saved["cisco_switch"]["last_factory_reset"]["output_excerpt"]


def test_cisco_page_shows_discovered_ip_separately_from_saved_config(cisco_client):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Discovered Separate Test Kit"
    cfg["ip_plan"] = {"switch": "192.168.1.2", "gateway": "192.168.1.1", "netmask": "255.255.255.0"}
    cfg["cisco_switch"].update(
        {
            "management_ip": "192.168.1.2",
            "ip": "192.168.1.2",
            "gateway": "192.168.1.1",
            "subnet_mask": "255.255.255.0",
            "last_console_bootstrap_check": {
                "management_vlan": 10,
                "current_management_ip": "192.168.1.50",
                "current_subnet_mask": "255.255.255.0",
                "default_gateway": "192.168.1.1",
                "ssh_enabled": True,
                "scp_enabled": False,
            },
        }
    )
    main.save_kit_config(cfg)

    response = cisco_client.get("/cisco")

    assert response.status_code == 200
    assert "Discovered/current switch state" in response.text
    assert "Saved Lab Builder kit config" in response.text
    assert "Planned/suggested values" in response.text
    assert "Last action result" in response.text
    operator_section = response.text.split('id="cisco-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Current config check cached" in operator_section
    assert "192.168.1.50" in response.text
    assert "192.168.1.2" in response.text
    assert "Discovered IP differs from saved kit config" in response.text
    assert "Use discovered values in this kit" in response.text


def test_cisco_page_missing_saved_config_does_not_contradict_discovered_values(cisco_client, monkeypatch):
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Missing Saved Config Test Kit"
    cfg["ip_plan"] = {"switch": "192.168.1.2", "gateway": "192.168.1.1", "netmask": "255.255.255.0"}
    cfg["cisco_switch"].update(
        {
            "management_ip": "",
            "ip": "",
            "gateway": "",
            "last_console_bootstrap_check": {
                "management_vlan": 10,
                "current_management_ip": "192.168.1.50",
                "current_subnet_mask": "255.255.255.0",
                "default_gateway": "192.168.1.1",
                "ssh_enabled": True,
                "scp_enabled": True,
            },
        }
    )
    monkeypatch.setattr(main, "load_kit_config", lambda kit_name=None: cfg)

    response = cisco_client.get("/cisco")

    assert response.status_code == 200
    assert "Discovered/current switch state" in response.text
    assert "192.168.1.50" in response.text
    assert "Saved Lab Builder kit config" in response.text
    assert "Not saved yet" in response.text
    assert "Discovered, not saved to this kit yet." in response.text
    assert "Current IP</span><strong>192.168.1.50" in response.text
    assert "Saved IP</span><strong>Not saved yet" in response.text


def test_cisco_page_render_does_not_touch_serial_or_ssh(cisco_client, monkeypatch):
    import app.modules.cisco.service as cisco_service

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Cisco page render must not open real hardware clients")

    monkeypatch.setattr(cisco_service, "CiscoSerialClient", fail_if_called)
    monkeypatch.setattr(cisco_service, "CiscoSSHClient", fail_if_called)
    monkeypatch.setattr(cisco_service, "CiscoSerialDiscovery", fail_if_called)

    response = cisco_client.get("/cisco")

    assert response.status_code == 200


def test_cisco_setup_console_rejects_weak_password_before_serial(cisco_client, monkeypatch):
    import app.modules.cisco.service as cisco_service

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Setup Console must validate password policy before opening serial hardware")

    monkeypatch.setattr(cisco_service, "CiscoSerialClient", fail_if_called)
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Weak Password Test Kit"
    main.save_kit_config(cfg)

    response = cisco_client.post(
        "/modules/cisco/setup-console",
        data={
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "short1A",
            "cisco_enable_secret": "ValidSecret123",
            "cisco_management_ip": "192.168.1.2",
            "cisco_subnet_mask": "255.255.255.0",
            "cisco_gateway": "192.168.1.1",
            "cisco_domain_name": "lab.local",
            "cisco_management_vlan": "10",
            "cisco_console_port": "/dev/ttyUSB0",
            "cisco_console_baud": "9600",
            "cisco_management_port": "GigabitEthernet1/0/1",
            "cisco_management_port_mode": "do_not_touch",
        },
    )

    assert response.status_code == 200
    assert "Cisco password must satisfy the Cisco setup wizard password policy" in response.text
    assert "short1A" not in response.text


def test_cisco_setup_console_rejects_weak_enable_secret_before_serial(cisco_client, monkeypatch):
    import app.modules.cisco.service as cisco_service

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Setup Console must validate enable-secret policy before opening serial hardware")

    monkeypatch.setattr(cisco_service, "CiscoSerialClient", fail_if_called)
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Weak Enable Secret Test Kit"
    main.save_kit_config(cfg)

    response = cisco_client.post(
        "/modules/cisco/setup-console",
        data={
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "ValidSecret123",
            "cisco_enable_secret": "short1A",
            "cisco_management_ip": "192.168.1.2",
            "cisco_subnet_mask": "255.255.255.0",
            "cisco_gateway": "192.168.1.1",
            "cisco_domain_name": "lab.local",
            "cisco_management_vlan": "10",
            "cisco_console_port": "/dev/ttyUSB0",
            "cisco_console_baud": "9600",
            "cisco_management_port": "GigabitEthernet1/0/1",
            "cisco_management_port_mode": "do_not_touch",
        },
    )

    assert response.status_code == 200
    assert "Cisco enable secret must satisfy the Cisco setup wizard password policy" in response.text
    assert "short1A" not in response.text


def test_cisco_setup_console_success_shows_completed_access_settings(cisco_client, monkeypatch):
    import app.modules.cisco.routes as cisco_routes
    from app.modules.cisco.service import CiscoModuleService

    real_service = CiscoModuleService()

    class FakeCiscoRouteService:
        def status(self, context):
            return real_service.status(context)

        def bootstrap_management(self, context, *, trunk_review_ack=False):
            return {
                "module": "cisco",
                "action": "bootstrap_management",
                "ok": True,
                "management_ip": "192.168.1.2",
                "steps": ["Detected Cisco initial configuration dialog; answered no with ValidSecret123."],
                "warnings": ["Console output echoed enable secret ValidSecret123."],
                "output": "Password: ValidSecret123\nSwitch#\nenable secret ValidSecret123",
            }

        def verify_console_bootstrap(self, context):
            return {
                "module": "cisco",
                "action": "verify_console_bootstrap",
                "ok": True,
                "management_vlan": 10,
                "current_management_ip": "192.168.1.2",
                "current_subnet_mask": "255.255.255.0",
                "default_gateway": "192.168.1.1",
                "ssh_enabled": True,
                "scp_enabled": True,
                "warnings": ["Raw verification mentioned ValidSecret123."],
                "raw_output": "username admin privilege 15 secret ValidSecret123\nSwitch#",
            }

    monkeypatch.setattr(cisco_routes, "service", FakeCiscoRouteService())
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Access Settings Complete Test Kit"
    main.save_kit_config(cfg)

    response = cisco_client.post(
        "/modules/cisco/setup-console",
        data={
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "ValidSecret123",
            "cisco_enable_secret": "ValidSecret123",
            "cisco_management_ip": "192.168.1.2",
            "cisco_subnet_mask": "255.255.255.0",
            "cisco_gateway": "192.168.1.1",
            "cisco_domain_name": "lab.local",
            "cisco_management_vlan": "10",
            "cisco_console_port": "/dev/ttyUSB0",
            "cisco_console_baud": "9600",
            "cisco_management_port": "GigabitEthernet1/0/1",
            "cisco_management_port_mode": "do_not_touch",
        },
    )

    assert response.status_code == 200
    assert "Cisco Access Settings complete" in response.text
    assert "Access Settings completed through the console" in response.text
    assert "Access Settings completed" in response.text
    assert "Management IP: 192.168.1.2" in response.text
    assert "Access settings" in response.text
    operator_section = response.text.split('id="cisco-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Action log captured" in operator_section
    assert "ValidSecret123" not in response.text

    saved = main.load_kit_config()
    assert saved["cisco_switch"]["last_cisco_action"]["ok"] is True
    for key in [
        "last_bootstrap",
        "last_serial_output",
        "last_console_bootstrap_check",
        "last_raw_console_bootstrap_check",
        "last_console_management_state",
        "last_cisco_action",
    ]:
        assert "ValidSecret123" not in repr(saved["cisco_switch"].get(key))
    assert "********" in saved["cisco_switch"]["last_serial_output"]
    assert "********" in saved["cisco_switch"]["last_cisco_action"]["log_excerpt"]


def test_cisco_setup_console_success_with_failed_verification_needs_attention(cisco_client, monkeypatch):
    import app.modules.cisco.routes as cisco_routes
    from app.modules.cisco.service import CiscoModuleService

    real_service = CiscoModuleService()

    class FakeCiscoRouteService:
        def status(self, context):
            return real_service.status(context)

        def bootstrap_management(self, context, *, trunk_review_ack=False):
            return {
                "module": "cisco",
                "action": "bootstrap_management",
                "ok": True,
                "management_ip": "192.168.1.2",
                "steps": ["Applied Access Settings through CLI with ValidSecret123."],
                "output": "Password: ValidSecret123\nSwitch#",
            }

        def verify_console_bootstrap(self, context):
            return {
                "module": "cisco",
                "action": "verify_console_bootstrap",
                "ok": False,
                "error": "Management SVI is down after CLI apply with ValidSecret123.",
                "warnings": ["Verify cabling and VLAN before saving next steps."],
                "raw_output": "interface Vlan10 is down\nusername admin secret ValidSecret123",
            }

    monkeypatch.setattr(cisco_routes, "service", FakeCiscoRouteService())
    cfg = main.default_config()
    cfg["site"]["name"] = "Cisco Access Settings Verification Attention Kit"
    main.save_kit_config(cfg)

    response = cisco_client.post(
        "/modules/cisco/setup-console",
        data={
            "cisco_switch_hostname": "sw01",
            "cisco_switch_username": "admin",
            "cisco_switch_password": "ValidSecret123",
            "cisco_enable_secret": "ValidSecret123",
            "cisco_management_ip": "192.168.1.2",
            "cisco_subnet_mask": "255.255.255.0",
            "cisco_gateway": "192.168.1.1",
            "cisco_domain_name": "lab.local",
            "cisco_management_vlan": "10",
            "cisco_console_port": "/dev/ttyUSB0",
            "cisco_console_baud": "9600",
            "cisco_management_port": "GigabitEthernet1/0/1",
            "cisco_management_port_mode": "do_not_touch",
        },
    )

    assert response.status_code == 200
    assert "Cisco Access Settings needs verification" in response.text
    assert "Management SVI is down after CLI apply" in response.text
    operator_section = response.text.split('id="cisco-operator-mode"', 1)[1].split("</section>", 1)[0]
    assert "Access Settings completed" not in operator_section
    assert "Action log captured" in operator_section
    assert "ValidSecret123" not in response.text

    saved = main.load_kit_config()
    assert saved["cisco_switch"]["connection_method"] == "console"
    assert saved["cisco_switch"]["last_cisco_action"]["ok"] is False
    assert "Management SVI is down after CLI apply" in saved["cisco_switch"]["last_cisco_action"]["error"]
    assert "ValidSecret123" not in repr(saved["cisco_switch"]["last_console_bootstrap_check"])
    assert "ValidSecret123" not in saved["cisco_switch"]["last_raw_console_bootstrap_check"]
    assert "********" in saved["cisco_switch"]["last_raw_console_bootstrap_check"]
