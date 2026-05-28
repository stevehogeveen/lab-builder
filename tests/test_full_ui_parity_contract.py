from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main


IMPORTANT_GET_PAGES = [
    "/",
    "/dashboard",
    "/global-settings",
    "/upgrade-helper",
    "/ilo",
    "/storage",
    "/esxi",
    "/windows",
    "/qnap",
    "/configuration",
    "/configs",
    "/execution",
    "/modules/cisco",
    "/cisco",
    "/modules/netapp",
    "/modules/ovf-templates",
]

IMPORTANT_POST_ACTIONS = [
    "/save-global-settings",
    "/autofill-ip-plan",
    "/save-ilo-settings",
    "/save-storage-target",
    "/read-current-storage",
    "/repair-storage-selection",
    "/plan-raid-layout",
    "/approve-storage-plan",
    "/clear-storage-approval",
    "/apply-storage-layout",
    "/reboot-storage-now",
    "/save-esxi-settings",
    "/save-windows-settings",
    "/probe-windows-vsphere",
    "/probe-windows-winrm",
    "/plan-windows-install",
    "/save-qnap-settings",
    "/modules/cisco/discover-version",
    "/modules/cisco/discover-console",
    "/modules/cisco/fix-serial-permissions",
    "/modules/cisco/bootstrap-management",
    "/modules/cisco/verify-console-bootstrap",
    "/modules/cisco/test-ssh",
    "/modules/cisco/save-port-map",
    "/modules/cisco/discover-ports",
    "/modules/cisco/discover-state",
    "/modules/cisco/preview-config",
    "/modules/cisco/apply-config",
    "/modules/cisco/approve-config-plan",
    "/modules/cisco/backup-config",
    "/modules/cisco/factory-reset",
    "/modules/netapp/save-settings",
    "/modules/netapp/test-connection",
    "/modules/netapp/read-current-config",
    "/modules/netapp/discover-page",
    "/modules/netapp/discover-console",
    "/modules/netapp/check-console-ports",
    "/modules/netapp/save-console",
    "/modules/netapp/console-read-state",
    "/modules/netapp/console-cluster-mgmt-ip",
    "/modules/netapp/bootstrap-test-all",
    "/modules/netapp/apply-ip-setup",
    "/modules/netapp/cluster-mgmt-ip",
    "/modules/netapp/use-discovered-values",
    "/modules/netapp/probe-vmware-nfs",
    "/modules/netapp/api-readiness",
    "/modules/netapp/validate-page",
    "/modules/netapp/export-plan",
    "/modules/netapp/apply-page",
    "/modules/ovf-templates/register-directory",
    "/register-windows-ovf-path",
    "/select-windows-ovf-template",
    "/prepare-execute",
    "/execute",
    "/execute-preview",
    "/retry-storage-stage",
    "/view-report",
    "/download-report",
    "/view-run-summary",
    "/download-run-summary",
]

EXPECTED_REACT_CONTROLS = [
    "Setup iLO IP",
    "Display current storage setup",
    "Build storage plan",
    "Apply storage layout",
    "Save shared defaults",
    "Create new kit",
    "Load existing kit",
    "Setup Cisco IP",
    "Setup NetApp IP",
    "Save ESXi setup",
    "Save Windows setup",
    "Save QNAP setup",
    "Register OVF path",
    "Prepare run review",
    "Start preview run",
    "View run summary",
    "Download run summary",
]


def route_paths() -> set[str]:
    return {route.path for route in main.app.routes}


def test_important_original_get_pages_have_react_or_backend_equivalents():
    paths = route_paths()
    with TestClient(main.app) as client:
        for path in IMPORTANT_GET_PAGES:
            assert path in paths
            response = client.get(path)
            assert response.status_code == 200, path


def test_important_original_post_actions_are_registered():
    paths = route_paths()
    for path in IMPORTANT_POST_ACTIONS:
        assert path in paths


def test_react_action_inventory_routes_exist():
    paths = route_paths()
    for page_key, actions in main.react_ui_action_inventory().items():
        for action in actions:
            if action["method"] == "WS" or "{" in action["route"]:
                continue
            assert action["route"] in paths, f"{page_key} maps missing route {action['route']}"


def test_original_sidebar_pages_have_react_nav_equivalents():
    page_keys = {page["key"] for page in main.react_ui_page_specs()}
    assert {
        "dashboard",
        "global_settings",
        "upgrade_helper",
        "ilo",
        "storage",
        "esxi",
        "windows",
        "ovf_templates",
        "qnap",
        "netapp",
        "cisco",
        "execution",
        "configuration",
        "reports",
        "action-map",
        "technical",
    }.issubset(page_keys)


def test_expected_operator_controls_are_present_in_react_bundle():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    app_state_actions = {
        action["label"]
        for actions in main.react_ui_action_inventory().values()
        for action in actions
    }
    for label in EXPECTED_REACT_CONTROLS:
        if label in app_state_actions:
            continue
        assert label in js


def test_react_aware_navigation_includes_legacy_module_aliases():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    assert '"/modules/cisco": "cisco"' in js
    assert '"/modules/netapp": "netapp"' in js
    assert '"/modules/ovf-templates": "ovf_templates"' in js


def test_overnight_hardware_feature_is_removed_from_routes_and_nav():
    paths = route_paths()
    assert "/overnight-hardware" not in paths
    assert "/api/ui/overnight-hardware" not in paths
    assert "/overnight-hardware/start" not in paths
    assert "overnight_hardware" not in {page["key"] for page in main.react_ui_page_specs()}

    sidebar = Path("templates/partials/sidebar.html").read_text(encoding="utf-8")
    assert "Overnight Hardware" not in sidebar
    assert "overnight-hardware" not in sidebar


def test_operator_mode_keeps_raw_diagnostics_in_debug_surfaces():
    js = Path("static/js/react-desktop-ui.js").read_text(encoding="utf-8")
    assert "Technical details" in js
    assert "Live job log" in js
    assert "traces" in js
    assert "Raw" not in js.split("function DashboardPage", 1)[1].split("function IloPage", 1)[0]


def test_parity_audit_artifacts_exist_and_include_statuses():
    root = Path("artifacts/codex-runs")
    expected = [
        "full-original-route-inventory.md",
        "full-react-route-inventory.md",
        "full-original-to-react-parity-matrix.md",
        "full-original-to-react-missing-functionality.md",
    ]
    for name in expected:
        path = root / name
        assert path.exists(), name
        assert path.read_text(encoding="utf-8").strip()
    matrix = (root / "full-original-to-react-parity-matrix.md").read_text(encoding="utf-8")
    assert "present" in matrix
    assert "Status" in matrix
