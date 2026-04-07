import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

import app.main as main


def get_free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture()
def isolated_app(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    kits_dir = config_dir / "kits"
    artifacts_dir = tmp_path / "artifacts"
    generated_dir = artifacts_dir / "generated"
    jobs_dir = artifacts_dir / "jobs"
    history_dir = artifacts_dir / "history"
    ilo_export_dir = history_dir / "ilo-configs"
    config_export_dir = history_dir / "configs"

    for path in (
        config_dir,
        kits_dir,
        generated_dir,
        jobs_dir,
        history_dir,
        ilo_export_dir,
        config_export_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(main, "KITS_DIR", kits_dir)
    monkeypatch.setattr(main, "CURRENT_KIT_FILE", config_dir / "current_kit.txt")
    monkeypatch.setattr(main, "ARTIFACTS_DIR", artifacts_dir)
    monkeypatch.setattr(main, "GENERATED_DIR", generated_dir)
    monkeypatch.setattr(main, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(main, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(main, "ILO_CONFIG_EXPORT_DIR", ilo_export_dir)
    monkeypatch.setattr(main, "CONFIG_EXPORT_DIR", config_export_dir)
    main.set_current_kit_name("Kit-01")

    return {
        "ilo_export_dir": ilo_export_dir,
        "config_export_dir": config_export_dir,
    }


@pytest.fixture()
def live_server(isolated_app):
    port = get_free_port()
    config = uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    last_error = None
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError(f"server did not start: {last_error}")

    yield f"http://127.0.0.1:{port}", isolated_app

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


def test_sidebar_navigation(page, live_server):
    base_url, _ = live_server
    page.goto(f"{base_url}/dashboard")

    page.get_by_role("link", name="Configuration").click()
    page.wait_for_url(f"{base_url}/configuration")
    assert page.locator("h1", has_text="Configuration").is_visible()

    page.get_by_role("link", name="Configs").click()
    page.wait_for_url(f"{base_url}/configs")
    assert page.locator("h1", has_text="Configs").is_visible()

    page.get_by_role("link", name="History").click()
    page.wait_for_url(f"{base_url}/history")
    assert page.locator("h1", has_text="History").is_visible()


def test_browser_config_save_export_and_download(page, live_server):
    base_url, paths = live_server
    page.goto(f"{base_url}/configuration")

    page.locator('input[name="site_name"]').fill("Browser Kit")
    page.locator("summary", has_text="Network & DNS").click()
    page.locator("summary", has_text="Credentials & Hostnames").click()
    page.locator('input[name="ilo_current_ip"]').fill("10.10.8.50")
    page.locator('input[name="ilo_target_ip"]').fill("10.10.8.11")
    page.locator('input[name="ilo_hostname"]').fill("ilo-browser")
    page.locator('input[name="section_network_complete"][value="true"]').check()
    page.locator('input[name="section_credentials_complete"][value="true"]').check()
    page.get_by_role("button", name="Save Dashboard Config").click()
    page.get_by_text("Saved kit: Browser-Kit").wait_for()

    page.get_by_role("link", name="Dashboard").click()
    page.wait_for_url(f"{base_url}/dashboard")
    assert page.get_by_text("Redfish Login IP: 10.10.8.50").is_visible()
    assert page.get_by_text("Final iLO IP: 10.10.8.11").is_visible()

    page.get_by_role("link", name="Configs").click()
    page.wait_for_url(f"{base_url}/configs")

    page.get_by_role("button", name="Export iLO Config Snapshot").click()
    page.get_by_text("Exported iLO config snapshot to").wait_for()

    snapshot_files = list(paths["ilo_export_dir"].glob("ilo-browser-*.yml"))
    assert len(snapshot_files) == 1
    assert "current_ip: 10.10.8.50" in snapshot_files[0].read_text(encoding="utf-8")

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Download Current Kit Config").click()
    download = download_info.value
    assert download.suggested_filename.startswith("Browser-Kit-")
