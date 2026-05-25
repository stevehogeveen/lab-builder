from app.netapp import NetAppError
from app.netapp import NetAppClient, NetAppConfig
from app.netapp_upgrade import _software_update_reached_target, build_netapp_upgrade_plan, build_ontap_upgrade_path_plan, build_ontap_upgrade_status, execute_netapp_upgrade


def test_build_netapp_upgrade_plan_blocks_when_required_intermediate_media_is_missing():
    cfg = {
        "netapp": {"host": "10.10.8.45", "username": "admin", "password": "secret"},
        "upgrade_inventory": {"netapp": {"current_version": "9.9.1P2", "source": "Last NetApp discovery"}},
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}},
        "counts": {"netapp": 1},
        "candidates": [{"device": "netapp", "version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}],
    }

    plan = build_netapp_upgrade_plan(cfg, media_scan)

    assert plan["ready"] is False
    assert plan["upgrade_path"]["path"] == ["9.9.1", "9.13.1", "9.17.1"]
    assert plan["upgrade_path"]["next_hop"] == "9.13.1"
    assert "9.13.1" in plan["upgrade_path"]["missing_media"]


def test_build_netapp_upgrade_plan_selects_next_hop_media_from_stored_path():
    cfg = {
        "netapp": {"host": "10.10.8.45", "username": "admin", "password": "secret"},
        "upgrade_inventory": {"netapp": {"current_version": "9.9.1P2", "source": "Last NetApp discovery"}},
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}},
        "counts": {"netapp": 2},
        "candidates": [
            {"device": "netapp", "version": "9.13.1", "filename": "9131_q_image.tgz", "path": "/repo/media/9131_q_image.tgz"},
            {"device": "netapp", "version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"},
        ],
    }

    plan = build_netapp_upgrade_plan(cfg, media_scan)

    assert plan["ready"] is True
    assert plan["media_version"] == "9.13.1"
    assert plan["media_filename"] == "9131_q_image.tgz"
    assert plan["highest_media_version"] == "9.17.1"


def test_execute_netapp_upgrade_updates_inventory():
    cfg = {
        "netapp": {"host": "10.10.8.45", "username": "admin", "password": "secret"},
        "upgrade_inventory": {"netapp": {"current_version": "9.13.1", "source": "Last NetApp discovery"}},
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}},
        "counts": {"netapp": 1},
        "candidates": [{"device": "netapp", "version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}],
    }

    class FakeClient:
        def upload_cluster_software(self, file_path):
            return {"job": {"uuid": "upload-job"}, "file": str(file_path)}

        def get_cluster_software_package(self, version):
            raise NetAppError("not uploaded")

        def validate_cluster_software(self, version):
            return {"job": {"uuid": "validate-job"}, "version": version}

        def start_cluster_software_update(self, version, **kwargs):
            return {"job": {"uuid": "start-job"}, "version": version}

        def get_job(self, uuid):
            return {"uuid": uuid, "state": "success", "message": ""}

        def get_cluster_software(self):
            return {"version": "9.17.1", "state": "success", "validation_results": []}

    result = execute_netapp_upgrade(cfg, media_scan, build_client=lambda **_: FakeClient(), wait_timeout=2, poll_interval=0.01)

    assert result["target_version"] == "9.17.1"
    assert cfg["upgrade_inventory"]["netapp"]["current_version"] == "9.17.1"


def test_execute_netapp_upgrade_starts_after_validation_pending_state():
    cfg = {
        "netapp": {"host": "10.10.8.45", "username": "admin", "password": "secret"},
        "upgrade_inventory": {"netapp": {"current_version": "9.13.1P17", "source": "Last NetApp discovery"}},
    }
    media_scan = {
        "root": "/repo/media",
        "latest": {"netapp": {"version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}},
        "counts": {"netapp": 1},
        "candidates": [{"device": "netapp", "version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"}],
    }

    class FakeClient:
        def __init__(self):
            self.started = False

        def get_cluster_software_package(self, version):
            return {"version": version}

        def validate_cluster_software(self, version):
            return {"job": {"uuid": "validate-job"}, "version": version}

        def start_cluster_software_update(self, version, **kwargs):
            self.started = True
            return {"job": {"uuid": "start-job"}, "version": version}

        def get_job(self, uuid):
            return {"uuid": uuid, "state": "success", "message": ""}

        def get_cluster_software(self):
            if not self.started:
                return {
                    "version": "9.13.1P17",
                    "pending_version": "9.17.1",
                    "state": "in_progress",
                    "validation_results": [],
                }
            return {"version": "9.17.1", "state": "success", "validation_results": []}

    fake_client = FakeClient()

    result = execute_netapp_upgrade(cfg, media_scan, build_client=lambda **_: fake_client, wait_timeout=2, poll_interval=0.01, skip_warnings=True)

    assert fake_client.started is True
    assert result["target_version"] == "9.17.1"
    assert cfg["upgrade_inventory"]["netapp"]["current_version"] == "9.17.1"


def test_execute_netapp_upgrade_does_not_complete_on_partial_node_update():
    payload = {
        "version": "9.13.1P17",
        "pending_version": "9.17.1",
        "state": "in_progress",
        "nodes": [{"name": "X20-02", "version": "9.17.1"}],
    }

    assert _software_update_reached_target(payload, "9.17.1") is False


def test_software_update_does_not_complete_while_target_state_is_in_progress():
    payload = {
        "version": "9.17.1",
        "pending_version": "9.17.1",
        "state": "in_progress",
        "nodes": [{"name": "X20-01", "version": "9.17.1"}, {"name": "X20-02", "version": "9.17.1"}],
    }

    assert _software_update_reached_target(payload, "9.17.1") is False


def test_build_ontap_upgrade_status_detects_giveback_and_expected_mismatch():
    cfg = {
        "upgrade_inventory": {"netapp": {"current_version": "9.17.1", "source": "Live ONTAP activity reconciliation"}},
        "netapp": {
            "upgrade": {
                "last_plan": {"current_version": "9.13.1P17", "media_version": "9.17.1"},
                "last_result": {
                    "status": "running",
                    "target_version": "9.17.1",
                    "raw_output": "kernel mismatch: local node running NetApp/9.17.1 while partner is NetApp/9.13.1P17\nWaiting for giveback...",
                    "raw": {
                        "version": "9.17.1",
                        "pending_version": "9.17.1",
                        "state": "in_progress",
                        "nodes": [{"name": "X20-01", "version": "9.17.1"}, {"name": "X20-02", "version": "9.13.1P17"}],
                        "status_details": [{"name": "do-giveback-job", "state": "waiting", "node": {"name": "X20-01"}}],
                    },
                },
                "activity": {
                    "status": "running",
                    "phase": "upgrade",
                    "progress_percent": 80,
                    "message": "ONTAP software state: in_progress; current 9.17.1; pending 9.17.1.",
                    "events": [{"phase": "upgrade", "message": "Waiting for giveback...", "timestamp": "2026-05-19T15:00:00+00:00"}],
                },
            }
        },
    }

    status = build_ontap_upgrade_status(cfg)

    assert status["status"] == "Waiting for giveback"
    assert status["waiting_for_giveback"] is True
    assert status["ha_version_mismatch"] is True
    assert any("rolling ONTAP upgrade" in warning for warning in status["warnings"])
    assert any("Do not force giveback automatically" in warning for warning in status["warnings"])


def test_build_ontap_upgrade_status_distinguishes_upload_staging():
    cfg = {
        "upgrade_inventory": {"netapp": {"current_version": "9.13.1P17"}},
        "netapp": {
            "upgrade": {
                "last_plan": {"current_version": "9.13.1P17", "media_version": "9.17.1"},
                "activity": {
                    "status": "running",
                    "phase": "upload",
                    "progress_percent": 35,
                    "message": "Uploading ONTAP image 9171_q_image.tgz.",
                    "events": [{"phase": "upload", "message": "Uploading ONTAP image 9171_q_image.tgz.", "timestamp": "2026-05-19T15:00:00+00:00"}],
                },
            }
        },
    }

    status = build_ontap_upgrade_status(cfg)

    assert status["status"] == "Uploading/staging"
    assert status["current_step"] == "Uploading/staging image package"
    assert status["progress"] == 35


def test_build_ontap_upgrade_path_plan_remembers_991_to_9171_path():
    media_scan = {
        "candidates": [
            {"device": "netapp", "version": "9.17.1", "filename": "9171_q_image.tgz", "path": "/repo/media/9171_q_image.tgz"},
        ]
    }

    plan = build_ontap_upgrade_path_plan("NetApp Release 9.9.1P2", "9.17.1", media_scan)

    assert plan["path"] == ["9.9.1", "9.13.1", "9.17.1"]
    assert plan["next_hop"] == "9.13.1"
    assert plan["missing_media"] == ["9.13.1"]


def test_execute_netapp_upgrade_blocks_when_prechecks_fail():
    cfg = {"netapp": {"host": "", "username": "", "password": ""}, "upgrade_inventory": {"netapp": {"current_version": "", "source": ""}}}
    media_scan = {"root": "/repo/media", "latest": {}, "counts": {}, "candidates": []}

    try:
        execute_netapp_upgrade(cfg, media_scan, build_client=lambda **_: object())
        assert False, "expected NetAppError"
    except NetAppError as exc:
        assert "ontap api target" in str(exc).lower() or "prechecks" in str(exc).lower()


def test_netapp_start_upgrade_sends_stabilize_minutes_as_query_param():
    calls = []

    class FakeResponse:
        status_code = 202
        text = '{"job":{"uuid":"job-1"}}'

        def json(self):
            return {"job": {"uuid": "job-1"}}

    class FakeSession:
        verify = False
        auth = None

        def __init__(self):
            self.headers = {}

        def request(self, method, url, params=None, json=None, timeout=None):
            calls.append({"method": method, "url": url, "params": params, "json": json, "timeout": timeout})
            return FakeResponse()

    client = NetAppClient(NetAppConfig(host="10.10.8.45", username="admin", password="secret"))
    client.session = FakeSession()

    result = client.start_cluster_software_update("9.17.1", stabilize_minutes=8)

    assert result["job"]["uuid"] == "job-1"
    assert calls[0]["params"]["stabilize_minutes"] == 8
    assert calls[0]["json"] == {"version": "9.17.1"}


def test_netapp_private_cli_upgrade_monitor_commands_use_supported_paths():
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"records":[]}'

        def json(self):
            return {"records": []}

    class FakeSession:
        verify = False
        auth = None

        def __init__(self):
            self.headers = {}

        def request(self, method, url, params=None, json=None, timeout=None):
            calls.append({"method": method, "url": url, "params": params or {}, "json": json, "timeout": timeout})
            return FakeResponse()

    client = NetAppClient(NetAppConfig(host="10.10.8.45", username="admin", password="secret"))
    client.session = FakeSession()

    client.get_cluster_image_update_progress()
    client.get_in_progress_jobs(instance=True)
    client.get_compact_jobs()
    client.get_cluster_image_repository()
    client.get_storage_failover_status()
    client.get_storage_failover_giveback_status()
    client.get_system_node_cli_status()
    client.get_error_events()
    client.get_giveback_events()
    snapshot = client.get_ontap_upgrade_monitor_snapshot(include_instance_jobs=True)

    urls = [call["url"] for call in calls]
    assert any(url.endswith("/api/private/cli/cluster/image/show-update-progress") for url in urls)
    assert any(url.endswith("/api/private/cli/job/show") and call["params"].get("instance") == "true" for call, url in zip(calls, urls))
    assert any(url.endswith("/api/private/cli/cluster/image/package/show-repository") for url in urls)
    assert any(url.endswith("/api/private/cli/storage/failover/show") for url in urls)
    assert any(url.endswith("/api/private/cli/storage/failover/show-giveback") for url in urls)
    assert any(url.endswith("/api/private/cli/system/node/show") for url in urls)
    assert any(url.endswith("/api/private/cli/event/log/show") and call["params"].get("severity") == "ERROR" for call, url in zip(calls, urls))
    assert any(url.endswith("/api/private/cli/event/log/show") and call["params"].get("message-name") == "*giveback*" for call, url in zip(calls, urls))
    assert "cluster image show-update-progress" in snapshot
    assert "job show -inprogress -instance" in snapshot
