import re
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main


EXPECTED_PAGE_ROUTES = [
    "/",
    "/react-preview",
    "/dashboard",
    "/global-settings",
    "/upgrade-helper",
    "/ilo",
    "/storage",
    "/esxi",
    "/windows",
    "/qnap",
    "/cisco",
    "/modules/netapp",
    "/modules/ovf-templates",
    "/execution",
    "/configs",
    "/history",
    "/configuration",
]

EXPECTED_VISIBLE_WORKFLOWS = {
    "dashboard": [
        "Switch active kit",
        "Load existing kit",
        "Create kit",
        "Open current config",
        "Download current config",
        "Prepare run review",
        "Start preview run",
    ],
    "global_settings": [
        "Load global settings",
        "Save global settings",
        "Autofill IP plan",
        "Save upgrade policies",
        "Upload firmware media",
    ],
    "upgrade_helper": [
        "Save policies",
        "Review Cisco upgrade plan",
        "Run Cisco upgrade",
        "Read Cisco version",
        "Review ONTAP upgrade plan",
        "Run ONTAP upgrade",
        "Plan iLO upgrade",
        "Run iLO upgrade",
    ],
    "ilo": [
        "Save iLO setup",
        "Setup iLO IP",
        "Export iLO config",
        "Read current iLO",
        "View iLO config snapshot",
    ],
    "storage": [
        "Save storage target",
        "Display current storage setup",
        "Clear invalid selections and reload inventory",
        "Build storage plan",
        "Approve this plan",
        "Remove approval",
        "Apply storage layout",
        "Probe storage capabilities",
        "Reboot storage now",
        "Reboot Machine Now",
        "Retry Reboot Now",
        "Approved",
        "View storage artifact",
        "View Apply Log",
        "View Apply Results",
        "View details",
        "View discovery summary",
        "View raw discovery",
    ],
    "esxi": [
        "Save ESXi setup",
        "Prepare ESXi run",
        "Preview ESXi run",
        "Start ESXi run",
    ],
    "windows": [
        "Save Windows setup",
        "Probe vSphere",
        "Probe WinRM",
        "Plan Windows install (dry-run)",
        "Register OVF path",
        "Use selected template",
    ],
    "qnap": [
        "Save QNAP setup",
    ],
    "cisco": [
        "Check version",
        "Test console access",
        "Setup Cisco IP",
        "Apply Access Configs",
        "Check current config",
        "Test SSH",
        "Discover ports",
        "Preview config",
        "Approve config",
        "Apply config",
        "Backup config",
        "Factory reset switch",
    ],
    "netapp": [
        "Save NetApp setup",
        "Test ONTAP API",
        "Read current ONTAP",
        "Read current NetApp config",
        "Discover NetApp console",
        "Check console ports",
        "Save selected console",
        "Read console state",
        "Preview console IP commands",
        "Apply cluster IP by console",
        "Setup NetApp IP",
        "Preview cluster IP command",
        "Apply cluster management IP",
        "Ping all NetApp IPs",
        "Use discovered values",
        "Probe ESXi and NFS",
        "Validate NetApp page",
        "Export NetApp plan",
        "Apply NetApp page",
        "Check reset readiness",
        "Factory reset NetApp",
    ],
    "ovf_templates": [
        "Register directory",
        "Register OVF path",
    ],
    "configuration": [
        "Load existing kit",
        "Create kit",
        "Import kit config",
        "Open current kit config",
        "Download current kit config",
        "Save kit config",
    ],
    "execution": [
        "Prepare run review",
        "Start preview run",
        "Start real run",
        "Retry storage stage",
        "View run summary",
        "Download run summary",
        "Open Reports",
    ],
    "reports": [
        "Load filtered reports",
        "Search reports",
        "Open detailed history",
        "View run summary",
        "Open run summary",
        "Open storage plan used",
        "Download run summary",
        "View report",
        "Download report",
        "Download debug bundle",
    ],
    "technical": [
        "Technical events API",
        "Live job websocket",
    ],
}

GUARDED_LABELS = {
    "Apply config",
    "Factory reset switch",
    "Run Cisco upgrade",
    "Run ONTAP upgrade",
    "Run iLO upgrade",
    "Run iLO firmware upgrade",
    "Start real run",
    "Apply NetApp page",
    "Safe apply NetApp",
    "Factory reset NetApp",
    "Apply cluster IP by console",
    "Apply cluster management IP",
    "Apply storage layout",
    "Reboot storage now",
}

CONTEXT_REQUIRED_LABELS = {
    "Save global settings HTML action",
    "Save iLO setup HTML action",
    "Export iLO config",
    "Read current iLO",
    "View iLO config snapshot",
    "Save storage target",
    "Display current storage setup",
    "Build storage plan",
    "Approve this plan",
    "View storage artifact",
    "Download storage artifact",
    "Save ESXi setup",
    "Save Windows setup",
    "Plan Windows install (dry-run)",
    "Use selected template",
    "Register directory",
    "Register OVF path",
    "Save QNAP setup",
    "Check version",
    "Test console access",
    "Setup Cisco IP",
    "Check current config",
    "Save to config",
    "Approve config",
    "Save NetApp setup",
    "Read current ONTAP",
    "Read current NetApp config",
    "Discover NetApp console",
    "Save selected console",
    "Preview console IP commands",
    "Apply cluster IP by console",
    "Setup NetApp IP",
    "Preview cluster IP command",
    "Apply cluster management IP",
    "View run summary",
    "Download run summary",
    "View report",
    "Download report",
}


def route_paths() -> set[str]:
    return {route.path for route in main.app.routes}


def route_methods() -> dict[str, set[str]]:
    methods_by_path: dict[str, set[str]] = {}
    for route in main.app.routes:
        path = getattr(route, "path", "")
        methods = {str(method) for method in (getattr(route, "methods", None) or []) if str(method) not in {"HEAD", "OPTIONS"}}
        if not methods and str(path).startswith("/ws/"):
            methods = {"WS"}
        methods_by_path.setdefault(path, set()).update(methods)
    return methods_by_path


def all_actions() -> dict[str, list[dict[str, str]]]:
    return main.react_ui_action_inventory()


def test_expected_pages_return_200_without_touching_hardware():
    with TestClient(main.app) as client:
        for route in EXPECTED_PAGE_ROUTES:
            response = client.get(route)
            assert response.status_code == 200, route


def test_every_expected_visible_workflow_is_in_react_inventory():
    inventory = all_actions()
    for page, labels in EXPECTED_VISIBLE_WORKFLOWS.items():
        available = {action["label"] for action in inventory.get(page, [])}
        for label in labels:
            assert label in available, (
                f"page={page} missing original action={label!r}; "
                "expected React equivalent in react_ui_action_inventory"
            )


def test_every_react_visible_action_maps_to_registered_backend_route():
    paths = route_paths()
    methods_by_path = route_methods()
    for page, actions in all_actions().items():
        for action in actions:
            route = action["route"]
            if action["method"] == "WS" or "{" in route:
                continue
            assert route in paths, f"page={page} visible action={action['label']!r} maps missing route {route}"
            assert action["method"] in methods_by_path.get(route, set()), (
                f"page={page} visible action={action['label']!r} expects {action['method']} "
                f"but route {route} allows {sorted(methods_by_path.get(route, set()))}"
            )


def test_hard_coded_react_internal_urls_map_to_registered_routes():
    paths = route_paths()
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    patterns = [
        r'href:\s*"(/[^"]+)"',
        r'apiGet\("(/[^"]+)"',
        r'htmlActionPost\("(/[^"]+)"',
        r'runForm\("(/[^"]+)"',
        r'reportPostForm\("(/[^"]+)"',
    ]
    urls = set()
    for pattern in patterns:
        urls.update(re.findall(pattern, js))

    assert urls
    missing = sorted(url for url in urls if "{" not in url and url not in paths)
    assert not missing, f"React bundle references unregistered route(s): {missing}"


def test_react_action_catalog_categories_match_operator_navigation():
    assert main.react_ui_route_category("/dashboard") == "Overview"
    assert main.react_ui_route_category("/global-settings") == "Overview"
    assert main.react_ui_route_category("/configs") == "Reports"
    assert main.react_ui_route_category("/history") == "Reports"
    assert main.react_ui_route_category("/execution") == "Run Center"


def test_setup_ip_copy_does_not_claim_cisco_reachability_before_ssh_test():
    source = Path("app/modules/cisco/routes.py").read_text(encoding="utf-8")
    bootstrap_body = source.split('"/modules/cisco/bootstrap-management"', 1)[1].split('"/modules/cisco/verify-console-bootstrap"', 1)[0]
    assert "Cisco management IP configured" not in bootstrap_body
    assert "Cisco management IP command sent" in bootstrap_body
    assert "Use Test SSH to verify" in bootstrap_body


def test_react_legacy_html_posts_surface_in_page_warnings_as_failures():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    helper_body = js.split("function legacyHtmlWarningMessage", 1)[1].split("function htmlActionPost", 1)[0]
    post_body = js.split("function htmlActionPost", 1)[1].split("function checkPasswordAttention", 1)[0]
    assert "DOMParser" in helper_body
    assert ".global-warning-popup" in helper_body
    assert "legacyHtmlWarningMessage(text)" in post_body
    assert "throw new Error(warning)" in post_body


def test_netapp_setup_ip_copy_matches_placeholder_backend_behavior():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    panel_body = js.split("function NetAppSetupIpPanel", 1)[1].split("function ActionLogPanel", 1)[0]
    handler_body = js.split("function setupNetAppIp", 1)[1].split("function storagePathFields", 1)[0]
    assert "live NetApp IP apply is not implemented yet" in panel_body
    assert "Save setup IP values" in panel_body
    assert "Applying..." not in panel_body
    assert "NetApp IP setup was not applied" in handler_body
    assert 'appendSetupAction("NetApp setup IP", message, false, "warn")' in handler_body


def test_legacy_post_actions_render_as_visible_operator_forms():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    assert "inline-action-form" in js
    assert 'action: action.route' in js
    assert 'method: "post"' in js
    assert "action.label" in js
    assert "Open confirmation" in js


def test_disabled_react_links_render_as_inert_buttons():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    button_body = js.split("function Button(props)", 1)[1].split("function DownloadButton", 1)[0]
    assert "const isLink = props.href && !props.disabled" in button_body
    assert 'isLink ? "a" : "button"' in button_body
    assert "href: isLink ? props.href : undefined" in button_body
    assert "disabled: isLink ? undefined : props.disabled" in button_body


def test_guarded_hardware_actions_do_not_auto_submit_from_action_inventory():
    inventory = all_actions()
    guarded_routes = {
        action["route"]
        for actions in inventory.values()
        for action in actions
        if action["label"] in GUARDED_LABELS
    }
    assert guarded_routes
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    guarded_branch = js.split("function actionControl(action)", 1)[1].split('if (action.method === "GET"', 1)[0]
    assert "Open confirmation" in guarded_branch
    assert "apply cluster" in js
    for route in guarded_routes:
        assert route in {action["route"] for actions in inventory.values() for action in actions}


def test_context_required_legacy_actions_open_original_forms_instead_of_empty_posts():
    inventory = all_actions()
    available = {
        action["label"]
        for actions in inventory.values()
        for action in actions
    }
    missing_labels = CONTEXT_REQUIRED_LABELS - available
    assert not missing_labels

    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    action_control = js.split("function actionControl(action)", 1)[1].split("return h(Panel", 1)[0]
    assert "needsOriginalFormContext(action)" in action_control
    assert "Open full form" in action_control
    assert 'route === "/prepare-execute"' in js
    assert 'route === "/execute-preview"' in js


def test_reports_page_exposes_action_inventory_and_debug_downloads():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    reports_body = js.split("function ReportsPage", 1)[1].split("function TechnicalPage", 1)[0]
    assert "ActionInventoryPanel" in reports_body
    assert "ReportCenterPanel" in reports_body
    assert "Download debug bundle" in {action["label"] for action in all_actions()["reports"]}


def test_react_app_state_exposes_report_center_bundles_and_files():
    state = main.build_react_ui_state()
    report_center = state.get("report_center") or {}
    assert "latest_bundles" in report_center
    assert "entries_preview" in report_center
    assert "entries_total" in report_center


def test_report_center_panel_has_view_and_download_forms():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    report_body = js.split("function ReportCenterPanel", 1)[1].split("function TechnicalPage", 1)[0]
    assert '"/view-report"' in report_body
    assert '"/download-report"' in report_body
    assert 'name: "report_path"' in report_body
    assert "onSubmit: submitSearch" in report_body
    assert 'name: "report_query"' in report_body
    assert "Search reports" in report_body
    assert "relatedReportsQuery" in report_body
    assert "Related reports" in report_body


def test_execution_page_has_dedicated_scope_review_and_preview_forms():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    assert "function ExecutionPage" in js
    execution_body = js.split("function ExecutionPage", 1)[1].split("function ReportCenterPanel", 1)[0]
    assert '"/prepare-execute"' in execution_body
    assert '"/execute-preview"' in execution_body
    assert '"/view-run-summary"' in execution_body
    assert '"/download-run-summary"' in execution_body
    assert 'name: "selected_scopes"' in execution_body
    assert 'name: "scope"' in execution_body
    assert "Open full confirmation" in execution_body
    assert "Open summary" in execution_body
    assert "Download summary" in execution_body
    assert "state.execution_review" in execution_body
    assert "stage.fix_href" in execution_body
    assert "Fix blocked stages before launch" in execution_body
    app_switch = js.split('} else if (activePage === "storage")', 1)[1].split('} else if (activePage === "reports")', 1)[0]
    assert "ExecutionPage" in app_switch


def test_react_app_state_exposes_execution_review_fix_links():
    state = main.build_react_ui_state()
    review = state.get("execution_review") or {}
    assert review.get("stages")
    for stage in review["stages"]:
        assert stage.get("review_href"), stage
        if stage.get("blocked_reason"):
            assert stage.get("fix_href"), stage
            assert stage.get("fix_label"), stage


def test_react_execution_review_does_not_probe_live_esxi_runtime(monkeypatch, tmp_path):
    cfg = main.load_kit_config()
    iso = tmp_path / "base-esxi.iso"
    iso.write_bytes(b"iso")
    cfg.setdefault("included", {})["esxi"] = True
    cfg.setdefault("esxi", {})["base_iso_path"] = str(iso)

    def fail_runtime_probe(*args, **kwargs):
        raise AssertionError("React app-state should not run ESXi runtime probes")

    monkeypatch.setattr(main, "build_esxi_runtime_status", fail_runtime_probe)
    review = main.build_react_execution_review_state(cfg)
    assert review.get("stages")
    assert "fallback_error" not in review


def test_react_execution_review_fallback_scopes_missing_esxi_iso_to_esxi(monkeypatch, tmp_path):
    cfg = main.load_kit_config()
    cfg.setdefault("included", {})["ilo"] = True
    cfg.setdefault("included", {})["esxi"] = True
    cfg.setdefault("esxi", {}).pop("base_iso_path", None)
    monkeypatch.setattr(main, "MEDIA_DIR", tmp_path / "media")

    review = main.build_react_execution_review_state(cfg)
    stages = {stage["key"]: stage for stage in review.get("stages", [])}

    assert review.get("fallback_error")
    assert stages["esxi"]["blocked_reason"]
    assert stages["esxi"]["fix_href"] == "/esxi"
    assert not stages["ilo"]["blocked_reason"]
    assert stages["ilo"]["status_label"] == "Review"


def test_dashboard_module_cards_preserve_original_dynamic_links():
    state = main.build_react_ui_state()
    assert {module["legacy_href"] for module in state.get("modules", [])}
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    module_body = js.split("function ModuleCard", 1)[1].split("function JobTimelinePanel", 1)[0]
    dashboard_body = js.split("function DashboardPage", 1)[1].split("function IloPage", 1)[0]
    assert "module.legacy_href" in module_body
    assert "ReactAwareButton" in module_body
    assert "nextHref" in module_body
    assert "nextHref: (dashboard.next_step || {}).href" in dashboard_body


def test_react_app_state_exposes_saved_setup_values_for_generic_pages():
    state = main.build_react_ui_state()
    setup_values = state.get("setup_values") or {}
    for page in ["esxi", "windows", "qnap", "ovf_templates"]:
        assert page in setup_values
        assert setup_values[page]["summary"], page
        assert setup_values[page]["primary_action"]["href"], page


def test_generic_migration_pages_show_saved_setup_value_panel():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    assert "function ModuleDetailPanel" in js
    migration_body = js.split("function MigrationPage", 1)[1].split("function ModuleDetailPanel", 1)[0]
    assert "state.setup_values" in migration_body
    assert "ModuleDetailPanel" in migration_body
    detail_body = js.split("function ModuleDetailPanel", 1)[1].split("function NetAppStatusPanel", 1)[0]
    assert "Open full form" in detail_body
    assert "ReactAwareButton" not in detail_body


def test_operator_mode_keeps_raw_logs_in_debug_surfaces():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    dashboard_body = js.split("function DashboardPage", 1)[1].split("function IloPage", 1)[0]
    assert "code-mini" not in dashboard_body
    assert "Technical details" in js
    assert "Live job log" in js


def test_overnight_hardware_surface_remains_removed():
    paths = route_paths()
    assert "/overnight-hardware" not in paths
    assert "/api/ui/overnight-hardware" not in paths
    assert "/overnight-hardware/start" not in paths
    assert "overnight_hardware" not in {page["key"] for page in main.react_ui_page_specs()}


def test_visible_parity_artifacts_exist_and_record_statuses():
    root = Path("artifacts/codex-runs")
    expected = [
        "full-visible-original-action-inventory.md",
        "full-visible-react-action-inventory.md",
        "full-visible-workflow-parity-matrix.md",
        "full-visible-workflow-missing-controls.md",
        "full-visible-workflow-remaining-gaps.md",
    ]
    for name in expected:
        path = root / name
        assert path.exists(), name
        assert path.read_text(encoding="utf-8").strip()
    matrix = (root / "full-visible-workflow-parity-matrix.md").read_text(encoding="utf-8")
    assert "Status" in matrix
    assert "present" in matrix
