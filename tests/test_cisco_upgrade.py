from app.cisco_upgrade import build_cisco_upgrade_plan


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
