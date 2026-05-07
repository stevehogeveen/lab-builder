from __future__ import annotations

import yaml
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.core.registry import load_modules


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


def test_netapp_module_route_returns_200_with_placeholder(client):
    response = client.get("/modules/netapp")

    assert response.status_code == 200
    assert "NetApp setup" in response.text


def test_netapp_action_endpoints_return_mock_data(client):
    response = client.post("/modules/netapp/discover")
    assert response.status_code == 200
    assert response.json()["action"] == "discover"

    response = client.post("/modules/netapp/plan")
    assert response.status_code == 200
    assert response.json()["action"] == "plan"

    response = client.post("/modules/netapp/apply", json={"job": {"job_id": "test-job"}})
    assert response.status_code == 200
    assert response.json()["action"] == "apply"

    response = client.get("/modules/netapp/status")
    assert response.status_code == 200
    assert response.json()["action"] == "status"


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
