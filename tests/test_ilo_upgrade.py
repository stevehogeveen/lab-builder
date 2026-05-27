from app.ilo import ILOError
from app.ilo_upgrade import build_ilo_upgrade_plan, execute_ilo_upgrade


def test_build_ilo_upgrade_plan_requires_live_identity_and_matching_media():
    cfg = {
        "ilo": {
            "current_ip": "192.168.1.50",
            "host": "192.168.1.50",
            "username": "Administrator",
            "password": "secret",
        },
        "upgrade_inventory": {
            "ilo": {
                "current_version": "1.50",
                "source": "Latest live iLO inventory",
                "manager_model": "iLO 6",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "3.19", "filename": "ilo5_319.fwpkg", "path": "/repo/media/ilo5_319.fwpkg"}},
        "counts": {"ilo": 2},
        "candidates": [
            {"device": "ilo", "version": "3.19", "filename": "ilo5_319.fwpkg", "path": "/repo/media/ilo5_319.fwpkg"},
            {"device": "ilo", "version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"},
        ],
    }

    plan = build_ilo_upgrade_plan(cfg, media_scan)

    assert plan["ready"] is True
    assert plan["media_filename"] == "ilo6_176.fwpkg"
    assert plan["media_version"] == "1.76"


def test_build_ilo_upgrade_plan_marks_current_firmware_as_noop():
    cfg = {
        "ilo": {
            "current_ip": "192.168.1.50",
            "host": "192.168.1.50",
            "username": "Administrator",
            "password": "secret",
        },
        "upgrade_inventory": {
            "ilo": {
                "current_version": "1.76",
                "source": "Latest live iLO inventory",
                "manager_model": "iLO 6",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [
            {"device": "ilo", "version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"},
        ],
    }

    plan = build_ilo_upgrade_plan(cfg, media_scan)

    assert plan["ready"] is False
    assert plan["state"] == "already_current"
    assert plan["no_upgrade_required"] is True
    assert plan["blockers"] == []


def test_execute_ilo_upgrade_noops_when_current_firmware_is_not_older():
    cfg = {
        "ilo": {
            "current_ip": "192.168.1.50",
            "host": "192.168.1.50",
            "username": "Administrator",
            "password": "secret",
        },
        "upgrade_inventory": {
            "ilo": {
                "current_version": "1.76",
                "source": "Latest live iLO inventory",
                "manager_model": "iLO 6",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [
            {"device": "ilo", "version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"},
        ],
    }
    events = []

    result = execute_ilo_upgrade(
        cfg,
        media_scan,
        build_client=lambda **_: (_ for _ in ()).throw(AssertionError("client should not be built")),
        progress=events.append,
    )

    assert result["status"] == "current"
    assert cfg["ilo"]["upgrade"]["last_result"]["status"] == "current"
    assert [event["phase"] for event in events] == ["precheck", "current"]


def test_execute_ilo_upgrade_updates_cached_inventory():
    cfg = {
        "ilo": {
            "current_ip": "192.168.1.50",
            "host": "192.168.1.50",
            "username": "Administrator",
            "password": "secret",
        },
        "upgrade_inventory": {
            "ilo": {
                "current_version": "1.50",
                "source": "Latest live iLO inventory",
                "manager_model": "iLO 6",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [
            {"device": "ilo", "version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"},
        ],
    }

    class FakeClient:
        def __init__(self):
            self.version = "1.50"
            self.uploads = []

        def upload_firmware_component(self, file_path, **kwargs):
            self.uploads.append((str(file_path), dict(kwargs)))
            self.version = "1.76"
            return {"status_code": 200}

        def get_summary(self):
            return {"manager_firmware": self.version}

    fake = FakeClient()
    events = []
    result = execute_ilo_upgrade(
        cfg,
        media_scan,
        build_client=lambda **_: fake,
        wait_timeout=2,
        poll_interval=0.01,
        progress=events.append,
    )

    assert result["target_version"] == "1.76"
    assert cfg["upgrade_inventory"]["ilo"]["current_version"] == "1.76"
    assert cfg["ilo"]["upgrade"]["last_result"]["status"] == "completed"
    phases = [event["phase"] for event in events]
    assert phases[:3] == ["precheck", "upload", "verify"]
    assert phases[-1] == "complete"
    assert events[-1]["progress_percent"] == 100


def test_execute_ilo_upgrade_waits_for_hpe_flash_and_resets_for_activation():
    cfg = {
        "ilo": {
            "current_ip": "192.168.1.50",
            "host": "192.168.1.50",
            "username": "Administrator",
            "password": "secret",
        },
        "upgrade_inventory": {
            "ilo": {
                "current_version": "1.50",
                "source": "Latest live iLO inventory",
                "manager_model": "iLO 6",
            }
        },
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"ilo": {"version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"}},
        "counts": {"ilo": 1},
        "candidates": [
            {"device": "ilo", "version": "1.76", "filename": "ilo6_176.fwpkg", "path": "/repo/media/ilo6_176.fwpkg"},
        ],
    }

    class FakeClient:
        def __init__(self):
            self.version = "1.50"
            self.reset_calls = []
            self.service_calls = 0

        def upload_firmware_component(self, file_path, **kwargs):
            return {"status_code": 200}

        def get_update_service(self):
            self.service_calls += 1
            if self.service_calls == 1:
                return {"Oem": {"Hpe": {"State": "Updating", "FlashProgressPercent": 52}}}
            return {"Oem": {"Hpe": {"State": "Complete", "FlashProgressPercent": 100}}}

        def _get(self, path):
            if path == "/redfish/v1/UpdateService/ComponentRepository/":
                return {"Members": [{"@odata.id": "/redfish/v1/UpdateService/ComponentRepository/ilo"}]}
            if path == "/redfish/v1/UpdateService/ComponentRepository/ilo":
                return {
                    "Name": "iLO 6",
                    "Filename": "ilo6_176.fwpkg",
                    "Version": "1.76",
                    "Activates": "AfterDeviceReset",
                }
            raise AssertionError(path)

        def reset_ilo(self, reset_type="GracefulRestart"):
            self.reset_calls.append(reset_type)
            self.version = "1.76"
            return {"path": "/redfish/v1/Managers/1/Actions/Manager.Reset", "reset_type": reset_type}

        def get_summary(self):
            return {"manager_firmware": self.version}

    fake = FakeClient()
    events = []

    result = execute_ilo_upgrade(
        cfg,
        media_scan,
        build_client=lambda **_: fake,
        wait_timeout=4,
        poll_interval=0.01,
        progress=events.append,
    )

    assert result["status"] == "completed"
    assert result["update_service"]["state"] == "Complete"
    assert result["update_service"]["activation"] == "AfterDeviceReset"
    assert result["reset"]["status"] == "requested"
    assert fake.reset_calls == ["GracefulRestart"]
    assert cfg["upgrade_inventory"]["ilo"]["current_version"] == "1.76"
    phases = [event["phase"] for event in events]
    assert "flash" in phases
    assert "reset" in phases


def test_execute_ilo_upgrade_blocks_when_prechecks_fail():
    cfg = {
        "ilo": {"current_ip": "", "host": "", "username": "", "password": ""},
        "upgrade_inventory": {"ilo": {"current_version": "", "source": "", "manager_model": ""}},
    }
    media_scan = {"root": "/repo/media", "latest": {}, "counts": {}, "candidates": []}

    try:
        execute_ilo_upgrade(cfg, media_scan, build_client=lambda **_: object())
        assert False, "expected ILOError"
    except ILOError as exc:
        assert "prechecks" in str(exc).lower() or "current ilo address" in str(exc).lower()
