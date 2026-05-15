import app.cisco_upgrade as cisco_upgrade
from app.cisco_upgrade import build_cisco_upgrade_plan, execute_cisco_upgrade


def test_build_cisco_upgrade_plan_uses_discovered_version_and_media():
    cfg = {
        "ip_plan": {"switch": "10.10.8.5"},
        "cisco_switch": {"username": "admin", "password": "secret"},
        "upgrade_inventory": {
            "cisco_switch": {
                "current_version": "17.03.01",
                "source": "Last Cisco discovery",
                "model": "C9300-48P",
                "platform": "C9300-UNIVERSALK9-M",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"cisco_switch": {"version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"}},
        "counts": {"cisco_switch": 1},
        "candidates": [{"device": "cisco_switch", "version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"}],
    }

    plan = build_cisco_upgrade_plan(cfg, media_scan)

    assert plan["current_version"] == "17.03.01"
    assert plan["media_version"] == "17.09.04"
    assert plan["host"] == "10.10.8.5"


def test_execute_cisco_upgrade_enables_scp_before_transfer(monkeypatch):
    cfg = {
        "ip_plan": {"switch": "10.10.8.5"},
        "cisco_switch": {"username": "admin", "password": "secret", "last_ssh_test": {"ok": True}},
        "upgrade_inventory": {
            "cisco_switch": {
                "current_version": "17.03.01",
                "source": "Last Cisco discovery",
                "model": "C9300-48P",
                "platform": "C9300-UNIVERSALK9-M",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"cisco_switch": {"version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"}},
        "counts": {"cisco_switch": 1},
        "candidates": [{"device": "cisco_switch", "version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"}],
    }
    monkeypatch.setattr(cisco_upgrade.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cisco_upgrade, "_local_file_size", lambda path: 1284011433)
    prep_calls = []
    command_calls = []
    events = []

    def fake_prep(host, username, password, commands, *, timeout, **kwargs):
        prep_calls.append((host, username, password, commands, timeout, kwargs))
        if "show run" in " ".join(commands):
            return {"command": "check", "output_excerpt": ""}
        if "dir flash:" in " ".join(commands):
            return {"command": "dir", "output_excerpt": "Directory of flash:/\n188448  -rw-        293623808  May 14 2026 17:20:02 +00:00  cat9k_lite_iosxe.17.09.04.SPA.bin"}
        if any(command.startswith("install add") for command in commands):
            return {"command": "install", "output_excerpt": "install_add_activate_commit: START install activate commit finished successfully"}
        return {"command": "prep", "output_excerpt": "ok"}

    def fake_run_command(cmd, *, timeout):
        command_calls.append(cmd)
        return {"command": " ".join(cmd), "stdout_excerpt": "", "stderr_excerpt": "", "returncode": 0}

    monkeypatch.setattr(cisco_upgrade, "_run_interactive_ssh_commands", fake_prep)
    monkeypatch.setattr(cisco_upgrade, "_run_command", fake_run_command)
    monkeypatch.setattr(cisco_upgrade, "_wait_for_cisco_version", lambda *args, **kwargs: {"status": "verified", "version": "17.09.04", "raw_version": "17.09.04"})
    monkeypatch.setattr(cisco_upgrade, "_verify_install_committed", lambda *args, **kwargs: {"status": "committed"})

    result = execute_cisco_upgrade(cfg, media_scan, progress=events.append)

    assert result["status"] == "completed"
    assert prep_calls
    assert "ip scp server enable" in prep_calls[2][3]
    assert "no ip scp server enable" in prep_calls[3][3]
    assert command_calls[0][3] == "scp"
    assert [event["phase"] for event in events][:4] == ["precheck", "transfer", "precheck", "precheck"]


def test_cisco_upgrade_error_keeps_real_scp_reason(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "Warning: Permanently added '10.10.8.5' (RSA) to the list of known hosts.\nAdministratively disabled.\n"

    monkeypatch.setattr(cisco_upgrade.subprocess, "run", lambda *args, **kwargs: Proc())

    try:
        cisco_upgrade._run_command(["sshpass", "-p", "secret", "scp"], timeout=1)
        assert False, "expected CiscoModuleError"
    except Exception as exc:
        assert str(exc) == "Administratively disabled."


def test_execute_cisco_upgrade_skips_upload_when_flash_image_matches(monkeypatch):
    cfg = {
        "ip_plan": {"switch": "10.10.8.5"},
        "cisco_switch": {"username": "admin", "password": "secret", "last_ssh_test": {"ok": True}},
        "upgrade_inventory": {
            "cisco_switch": {
                "current_version": "17.03.01",
                "source": "Last Cisco discovery",
                "model": "C9300-48P",
                "platform": "C9300-UNIVERSALK9-M",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"cisco_switch": {"version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"}},
        "counts": {"cisco_switch": 1},
        "candidates": [{"device": "cisco_switch", "version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"}],
    }
    monkeypatch.setattr(cisco_upgrade.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cisco_upgrade, "_local_file_size", lambda path: 1284011433)
    command_calls = []

    def fake_prep(host, username, password, commands, *, timeout, **kwargs):
        if "dir flash:" in " ".join(commands):
            return {"command": "dir", "output_excerpt": "Directory of flash:/\n188448  -rw-       1284011433  May 14 2026 17:07:17 +00:00  cat9k_lite_iosxe.17.09.04.SPA.bin"}
        if any(command.startswith("install add") for command in commands):
            return {"command": "install", "output_excerpt": "install_add_activate_commit: finished successfully"}
        return {"command": "prep", "output_excerpt": ""}

    monkeypatch.setattr(cisco_upgrade, "_run_interactive_ssh_commands", fake_prep)
    monkeypatch.setattr(cisco_upgrade, "_run_command", lambda cmd, *, timeout: command_calls.append(cmd))
    monkeypatch.setattr(cisco_upgrade, "_wait_for_cisco_version", lambda *args, **kwargs: {"status": "verified", "version": "17.09.04", "raw_version": "17.09.04"})
    monkeypatch.setattr(cisco_upgrade, "_verify_install_committed", lambda *args, **kwargs: {"status": "committed"})

    result = execute_cisco_upgrade(cfg, media_scan)

    assert result["transfer"]["status"] == "skipped"
    assert command_calls == []


def test_execute_cisco_upgrade_reloads_when_packages_conf_is_prepared(monkeypatch):
    cfg = {
        "ip_plan": {"switch": "10.10.8.5"},
        "cisco_switch": {"username": "admin", "password": "secret", "last_ssh_test": {"ok": True}},
        "upgrade_inventory": {
            "cisco_switch": {
                "current_version": "17.03.01",
                "source": "Last Cisco discovery",
                "model": "C9300-48P",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"cisco_switch": {"version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"}},
        "counts": {"cisco_switch": 1},
        "candidates": [{"device": "cisco_switch", "version": "17.09.04", "filename": "cat9k_lite_iosxe.17.09.04.SPA.bin", "path": "/repo/media/cat9k_lite_iosxe.17.09.04.SPA.bin"}],
    }
    events = []
    reload_calls = []
    monkeypatch.setattr(cisco_upgrade.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cisco_upgrade, "_local_file_size", lambda path: 1284011433)
    monkeypatch.setattr(cisco_upgrade, "_remote_flash_file_size", lambda *args, **kwargs: 1284011433)
    monkeypatch.setattr(
        cisco_upgrade,
        "_run_interactive_ssh_commands",
        lambda *args, **kwargs: {"command": "install", "output_excerpt": "install failed because ISSU compatibility check failed"},
    )
    monkeypatch.setattr(cisco_upgrade, "_install_prepared_for_reload", lambda *args, **kwargs: True)
    monkeypatch.setattr(cisco_upgrade, "_reload_switch", lambda *args, **kwargs: reload_calls.append(args) or {"command": "reload"})
    monkeypatch.setattr(cisco_upgrade, "_wait_for_cisco_version", lambda *args, **kwargs: {"status": "verified", "version": "17.09.04", "raw_version": "17.09.04"})
    monkeypatch.setattr(cisco_upgrade, "_verify_install_committed", lambda *args, **kwargs: {"status": "committed"})

    result = execute_cisco_upgrade(cfg, media_scan, progress=events.append)

    assert result["status"] == "completed"
    assert reload_calls
    assert "reload" in [event["phase"] for event in events]
