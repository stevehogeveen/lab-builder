from app.netapp import NetAppError
from app.netapp import NetAppClient, NetAppConfig
from app.netapp_upgrade import build_netapp_upgrade_plan, build_ontap_upgrade_path_plan, execute_netapp_upgrade


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
            return {"validation_results": []}

    result = execute_netapp_upgrade(cfg, media_scan, build_client=lambda **_: FakeClient(), wait_timeout=2, poll_interval=0.01)

    assert result["target_version"] == "9.17.1"
    assert cfg["upgrade_inventory"]["netapp"]["current_version"] == "9.17.1"


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
