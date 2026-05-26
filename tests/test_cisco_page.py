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
    assert "Values ready to apply" in response.text
    assert "Last action result/log" in response.text
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
    assert "Discovered on the switch, but not saved in this kit yet." in response.text
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
