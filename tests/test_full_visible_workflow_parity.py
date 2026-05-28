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
        "Review Cisco upgrade plan",
        "Run Cisco upgrade",
        "Read Cisco version",
        "Review ONTAP upgrade plan",
        "Run ONTAP upgrade",
        "Plan iLO firmware upgrade",
        "Run iLO firmware upgrade",
    ],
    "ilo": [
        "Save iLO setup",
        "Setup iLO IP",
        "Export iLO config",
        "Export iLO inventory",
        "View iLO config snapshot",
    ],
    "storage": [
        "Save storage target",
        "Read current storage",
        "Plan RAID layout",
        "Approve storage plan",
        "Apply storage layout",
        "Probe storage capabilities",
        "Reboot storage now",
        "View storage artifact",
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
        "Plan Windows install",
        "Register OVF path",
        "Select Windows OVF template",
    ],
    "qnap": [
        "Save QNAP setup",
    ],
    "cisco": [
        "Discover Cisco version",
        "Discover Cisco console",
        "Setup Cisco IP",
        "Verify console bootstrap",
        "Test SSH",
        "Discover ports",
        "Preview config",
        "Approve config plan",
        "Apply config",
        "Backup config",
        "Factory reset switch",
    ],
    "netapp": [
        "Save NetApp settings",
        "Test NetApp connection",
        "Read current ONTAP",
        "Check console ports",
        "Read console state",
        "Setup NetApp IP",
        "Ping all NetApp IPs",
        "Use discovered values",
        "Probe ESXi and NFS",
        "Validate NetApp page",
        "Export NetApp plan",
        "Apply NetApp page",
        "Check reset readiness",
    ],
    "ovf_templates": [
        "Register OVF directory",
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
        "Open Reports",
    ],
    "reports": [
        "Search reports",
        "Open detailed history",
        "View run summary",
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
    "Run iLO firmware upgrade",
    "Start real run",
    "Apply NetApp page",
    "Safe apply NetApp",
    "Apply storage layout",
    "Reboot storage now",
}

CONTEXT_REQUIRED_LABELS = {
    "Save global settings HTML action",
    "Save iLO setup HTML action",
    "Export iLO config",
    "Export iLO inventory",
    "View iLO config snapshot",
    "Save storage target",
    "Read current storage",
    "Plan RAID layout",
    "Approve storage plan",
    "View storage artifact",
    "Download storage artifact",
    "Save ESXi setup",
    "Save Windows setup",
    "Register OVF directory",
    "Register OVF path",
    "Save QNAP setup",
    "Discover Cisco version",
    "Discover Cisco console",
    "Setup Cisco IP",
    "Save NetApp settings",
    "Read current ONTAP",
    "Setup NetApp IP",
    "View run summary",
    "Download run summary",
    "View report",
    "Download report",
}


def route_paths() -> set[str]:
    return {route.path for route in main.app.routes}


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
    for page, actions in all_actions().items():
        for action in actions:
            route = action["route"]
            if action["method"] == "WS" or "{" in route:
                continue
            assert route in paths, f"page={page} visible action={action['label']!r} maps missing route {route}"


def test_legacy_post_actions_render_as_visible_operator_forms():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    assert "inline-action-form" in js
    assert 'action: action.route' in js
    assert 'method: "post"' in js
    assert "action.label" in js
    assert "Open confirmation" in js


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
    assert "Open form" in action_control
    assert 'route === "/prepare-execute"' in js
    assert 'route === "/execute-preview"' in js


def test_reports_page_exposes_action_inventory_and_debug_downloads():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    reports_body = js.split("function ReportsPage", 1)[1].split("function TechnicalPage", 1)[0]
    assert "ActionInventoryPanel" in reports_body
    assert "Download debug bundle" in {action["label"] for action in all_actions()["reports"]}


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
