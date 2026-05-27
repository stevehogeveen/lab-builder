from pathlib import Path

import app.main as main
from app.modules.esxi_config.service import EsxiConfigModuleService
from app.modules.esxi_install.service import EsxiInstallModuleService
from app.modules.qnap.service import QnapModuleService
from app.modules.windows.service import WindowsModuleService


def test_session_docs_define_operator_flow_contract():
    root = Path(__file__).resolve().parents[1]
    agents = root / "AGENTS.md"
    contract = root / "docs" / "operator-flow-contract.md"
    scopes = root / "docs" / "workflow-session-scopes.md"

    assert agents.exists()
    assert contract.exists()
    assert scopes.exists()

    agents_text = agents.read_text(encoding="utf-8")
    contract_text = contract.read_text(encoding="utf-8")
    scopes_text = scopes.read_text(encoding="utf-8")

    for required in [
        "SESSION_COORDINATION.md",
        "docs/workflow-session-scopes.md",
        "docs/operator-flow-contract.md",
        "docs/automation-principles.md",
        "docs/ux-product-principles.md",
        "docs/validation.md",
    ]:
        assert required in agents_text

    assert "I am going to be working with Cisco this round. Use the operator flow contract." in agents_text
    assert "If no active coordination entry exists" in agents_text
    assert "Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step" in agents_text
    assert "Current access" in contract_text
    assert "Desired final" in contract_text
    assert "[DISCOVER]" in contract_text
    assert "[BLOCKED]" in contract_text
    assert "## Operator Mode Checkpoint" in contract_text
    for label in ["Next step", "Completion state", "Last result", "Logs/status", "Open Debug Mode/details"]:
        assert label in contract_text
    assert "## Saved Secret Rendering" in contract_text
    assert "Saved secrets must never be rendered back into a page" in contract_text
    assert "JavaScript/Alpine state" in contract_text
    assert "secret fields as empty strings" in contract_text
    assert "### Cisco" in scopes_text
    assert "app/modules/cisco/**" in scopes_text
    assert "### vCenter" in scopes_text
    assert "create one from the template" in scopes_text
    assert (root / "docs" / "validation.md").exists()


def test_vcenter_participates_in_shared_workflow_context(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    iso = media_dir / "VMware-VCSA-all.iso"
    iso.write_text("iso", encoding="utf-8")
    monkeypatch.setattr(main, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(main, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(main, "ARTIFACTS_DIR", tmp_path / "artifacts")

    cfg = main.default_config()
    cfg["included"]["vmware"] = True
    cfg["ip_plan"]["gateway"] = "192.168.1.1"
    cfg["shared_network"]["dns_servers"] = ["192.168.1.10"]
    cfg["vmware"]["vcenter_install"].update(
        {
            "target_ip": "192.168.1.50",
            "system_name": "192.168.1.50",
            "vm_name": "SVCNTR-Kit-01",
            "iso_path": str(iso),
            "esxi_host": "192.168.1.111",
            "esxi_username": "root",
            "esxi_password": "Valid1Pass!",
            "datastore": "netapp_nfs",
            "deployment_network": "VM Network",
            "root_password": "Valid1Pass!",
            "sso_password": "Valid1Pass!",
        }
    )

    contexts = main.build_workflow_contexts(cfg, {}, [])

    assert "vcenter" in contexts
    assert "vmware" in contexts
    assert contexts["vcenter"]["name"] == "vCenter"
    assert contexts["vcenter"]["target"] == "192.168.1.50"
    assert contexts["vcenter"]["review_href"] == "/vcenter"
    assert any(check["label"] == "Install context" for check in contexts["vcenter"]["checks"])
    assert contexts["vmware"]["name"] == "vCenter"
    assert contexts["vmware"]["target"] == "192.168.1.50"
    assert contexts["vmware"]["review_href"] == "/vcenter"
    assert any(check["label"] == "Install context" for check in contexts["vmware"]["checks"])

    recommended = main.build_recommended_next_step(cfg, contexts)
    summary = main.build_setup_precheck_summary(cfg, contexts, recommended)
    assert any(item["key"] == "vcenter" for item in summary["items"])

    page_summary = main.build_page_precheck_summary("vcenter", cfg, contexts)
    assert page_summary is not None
    assert page_summary["key"] == "vcenter"


def test_setup_pages_render_shared_flow_components():
    root = Path(__file__).resolve().parents[1]
    page_names = ["ilo", "storage", "esxi", "vcenter", "windows", "qnap", "netapp", "cisco"]

    for page_name in page_names:
        text = (root / "templates" / "partials" / "pages" / f"{page_name}.html").read_text(encoding="utf-8")
        assert 'partials/components/precheck_summary.html' in text, page_name
        assert 'partials/components/setup_strip.html' in text, page_name


def test_available_physical_pages_render_operator_checkpoint_labels():
    root = Path(__file__).resolve().parents[1]
    page_names = ["cisco", "ilo", "esxi", "ovf_templates"]
    required_labels = [
        "Operator Mode",
        "Open Debug Mode/details",
        "Debug Mode/details",
        "Next step",
        "Completion state",
        "Last result",
    ]

    for page_name in page_names:
        text = (root / "templates" / "partials" / "pages" / f"{page_name}.html").read_text(encoding="utf-8")
        for label in required_labels:
            assert label in text, f"{page_name} missing {label}"
        assert "Logs/status" in text or "logs/status" in text, f"{page_name} missing logs/status"


def test_available_physical_debug_modes_render_recovery_suggestions():
    root = Path(__file__).resolve().parents[1]
    page_names = ["cisco", "ilo", "esxi", "ovf_templates"]

    for page_name in page_names:
        text = (root / "templates" / "partials" / "pages" / f"{page_name}.html").read_text(encoding="utf-8")
        assert "Debug Mode/details" in text, page_name
        assert "Recovery suggestions" in text, page_name


def test_available_physical_debug_modes_name_troubleshooting_payloads():
    root = Path(__file__).resolve().parents[1]
    pages = {
        "cisco": [
            "last serial output here",
            "choose 0 at the final wizard menu",
            "discovered/current switch state",
        ],
        "ilo": [
            "Redfish endpoints, response summaries, artifacts, and recovery context",
            "allowed ComputerSystem.Reset values",
            "virtual media issues",
        ],
        "esxi": [
            "Installer artifacts, kickstart inputs, iLO virtual media, boot override, power state, and recovery context",
            "generated ISO path",
            "Start the physical ESXi install only from an explicit operator action",
        ],
        "ovf_templates": [
            "Template registration validates local OVF/OVA files only",
            "Discovered files",
            "stays dry-run unless a deployment target is available and explicitly started",
        ],
    }

    for page_name, required_texts in pages.items():
        text = (root / "templates" / "partials" / "pages" / f"{page_name}.html").read_text(encoding="utf-8")
        for required_text in required_texts:
            assert required_text in text, f"{page_name} missing {required_text}"


def test_available_physical_pages_keep_real_actions_manual_or_confirmed():
    root = Path(__file__).resolve().parents[1]
    pages = {
        "cisco": "Type FACTORY RESET to confirm",
        "ilo": "manual operator-triggered action",
        "esxi": "explicit operator action in Run Center",
        "ovf_templates": "stays dry-run unless a deployment target is available and explicitly started",
    }

    for page_name, required_text in pages.items():
        text = (root / "templates" / "partials" / "pages" / f"{page_name}.html").read_text(encoding="utf-8")
        assert required_text in text, f"{page_name} missing real-action boundary text"


def test_netapp_tomorrow_checklist_preserves_mock_boundary_and_checkpoint_labels():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "netapp-tomorrow-manual-test-checklist.md").read_text(encoding="utf-8")

    assert "Do not run any real NetApp API, SSH, SP, serial, or console action" in text
    assert "192.168.1.0/24" in text
    for value in ["192.168.1.13", "192.168.1.14", "192.168.1.45", "192.168.1.46", "192.168.1.47", "192.168.1.48"]:
        assert value in text
    for label in ["Operator Mode", "Next step", "Completion state", "Last result", "Logs/status", "Open Debug Mode/details"]:
        assert label in text
    for value_label in ["Saved kit config", "Discovered/current state", "Suggested values"]:
        assert value_label in text


def test_netapp_tomorrow_checklist_names_guided_flow_steps():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "netapp-tomorrow-manual-test-checklist.md").read_text(encoding="utf-8")

    assert "## Cisco-Style Guided NetApp Flow" in text
    for step in [
        "Initial access/status",
        "SP/e0M/cluster/SVM management IP plan",
        "Apply or verify management IPs",
        "Verify SSH/API access",
        "Discover controllers/nodes/interfaces/version",
        "Validate readiness",
        "Configure required settings",
        "Upgrade readiness/action if available",
        "Completed state",
    ]:
        assert step in text


def test_netapp_tomorrow_checklist_names_debug_mode_and_dry_run_boundaries():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "netapp-tomorrow-manual-test-checklist.md").read_text(encoding="utf-8")

    for requirement in [
        "## Debug Mode Checklist",
        "Logs/status appear in a consistent Debug Mode/details area",
        "Raw detected ONTAP state is available when safe and redacted where needed",
        "Command/API response summaries are available without secrets",
        "Artifacts and test history are linked or named clearly",
        "Recovery suggestions explain the discovered problem, safe options, and the recommended next step",
        "Redundant operator controls are hidden, consolidated, or moved into Debug Mode without deleting useful diagnostics",
        "Route/template tests render the NetApp operator flow without contacting hardware",
        "Dry-run fixtures use `192.168.1.0/24` suggestions only when saved kit values are absent",
        "Tests assert saved kit config is not globally overwritten by suggested values",
        "Tests assert no secrets appear in page logs, debug output, artifacts, or test output",
    ]:
        assert requirement in text


def test_physical_manual_checklists_pin_automated_pytest_boundaries():
    root = Path(__file__).resolve().parents[1]
    checklists = [
        "cisco-factory-reset-onboarding-checklist.md",
        "ilo-physical-flow-checklist.md",
        "esxi-physical-install-checklist.md",
        "ovf-ova-prep-checklist.md",
        "netapp-tomorrow-manual-test-checklist.md",
    ]

    for checklist in checklists:
        text = (root / "docs" / checklist).read_text(encoding="utf-8")
        assert "Automated pytest" in text, checklist
        assert any(term in text for term in ["fake", "mocks", "dry-runs", "route tests", "template tests"]), checklist


def test_checked_in_example_kit_uses_home_lab_network():
    root = Path(__file__).resolve().parents[1]
    text = (root / "config" / "examples" / "Kit-01.example.yml").read_text(encoding="utf-8")

    assert "192.168.1.0/24" in text
    assert "192.168.1.1" in text
    assert "10.10.8" not in text


def test_cisco_test_fixtures_use_home_lab_network():
    root = Path(__file__).resolve().parents[1]
    cisco_test_files = [
        "test_cisco_serial.py",
        "test_cisco_config_rendering.py",
        "test_cisco_module.py",
        "test_cisco_upgrade.py",
    ]

    for file_name in cisco_test_files:
        text = (root / "tests" / file_name).read_text(encoding="utf-8")
        assert "192.168.1" in text, file_name
        assert "10.10.8" not in text, file_name


def test_ilo_upgrade_test_fixtures_use_home_lab_network():
    root = Path(__file__).resolve().parents[1]
    text = (root / "tests" / "test_ilo_upgrade.py").read_text(encoding="utf-8")

    assert "192.168.1" in text
    assert "10.10.8" not in text


def test_vcenter_test_fixtures_use_home_lab_network():
    root = Path(__file__).resolve().parents[1]
    text = (root / "tests" / "test_vcenter.py").read_text(encoding="utf-8")

    assert "192.168.1" in text
    assert "10.10.8" not in text


def test_physical_manual_checklists_include_operator_checkpoint_labels():
    root = Path(__file__).resolve().parents[1]
    checklist_names = [
        "cisco-factory-reset-onboarding-checklist.md",
        "ilo-physical-flow-checklist.md",
        "esxi-physical-install-checklist.md",
        "ovf-ova-prep-checklist.md",
        "netapp-tomorrow-manual-test-checklist.md",
    ]
    required_labels = ["Operator Mode", "Next step", "Completion state", "Last result", "Logs/status", "Open Debug Mode/details"]

    for checklist_name in checklist_names:
        text = (root / "docs" / checklist_name).read_text(encoding="utf-8")
        for label in required_labels:
            assert label in text, f"{checklist_name} missing {label}"


def test_physical_manual_checklists_name_shared_operator_flow_sequence():
    root = Path(__file__).resolve().parents[1]
    checklist_names = [
        "cisco-factory-reset-onboarding-checklist.md",
        "ilo-physical-flow-checklist.md",
        "esxi-physical-install-checklist.md",
        "ovf-ova-prep-checklist.md",
        "netapp-tomorrow-manual-test-checklist.md",
    ]
    sequence = "Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step"

    for checklist_name in checklist_names:
        text = (root / "docs" / checklist_name).read_text(encoding="utf-8")
        assert sequence in text, f"{checklist_name} missing shared operator-flow sequence"


def test_cisco_checklist_keeps_logs_in_debug_mode_wording():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "cisco-factory-reset-onboarding-checklist.md").read_text(encoding="utf-8")

    assert "Last action result shows the latest setup or verification result" in text
    assert "raw log excerpts stay in Debug Mode/details" in text
    assert "Last action result/log" not in text


def test_cisco_checklist_uses_planned_suggested_label():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "cisco-factory-reset-onboarding-checklist.md").read_text(encoding="utf-8")

    assert "planned/suggested values" in text
    assert "values ready to apply" not in text


def test_cisco_checklist_pins_password_policy_and_final_menu_zero():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "cisco-factory-reset-onboarding-checklist.md").read_text(encoding="utf-8")

    for requirement in [
        "at least 10 characters",
        "at least 1 uppercase letter",
        "at least 1 lowercase letter",
        "at least 1 digit",
        "confirm Lab Builder chooses `0`",
        "Confirm Lab Builder never chooses `2`",
    ]:
        assert requirement in text


def test_cisco_checklist_names_debug_mode_troubleshooting_boundaries():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "cisco-factory-reset-onboarding-checklist.md").read_text(encoding="utf-8")

    for requirement in [
        "## Debug Mode Checklist",
        "Logs/status appear in Debug Mode/details",
        "Raw console excerpts, command output, and setup wizard diagnostics are redacted",
        "Artifacts and test history are linked or named clearly",
        "Recovery suggestions explain the detected prompt",
        "do not appear in page logs, artifacts, command output, or test output",
    ]:
        assert requirement in text


def test_ilo_checklist_names_debug_mode_troubleshooting_boundaries():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "ilo-physical-flow-checklist.md").read_text(encoding="utf-8")

    for requirement in [
        "## Debug Mode Checklist",
        "Redfish endpoint details are available for manager, system, network, virtual media, boot override, and reset targets",
        "Response summaries omit Authorization headers, cookies, session IDs, tokens, and raw passwords",
        "Artifacts and test history are linked or named clearly",
        "Recovery suggestions explain what was attempted, what was discovered, safe options, and the next manual fix",
        "Raw detected state is available only where useful and safe",
        "no secrets appear in logs, rendered pages, artifacts, or command/API summaries",
    ]:
        assert requirement in text


def test_esxi_checklist_keeps_media_urls_in_debug_mode_wording():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "esxi-physical-install-checklist.md").read_text(encoding="utf-8")

    assert "Media readiness only" in text
    assert "virtual-media URLs belong in Debug Mode/details" in text
    assert "generated ISO path, virtual media URL, serving URL validation" in text


def test_esxi_checklist_names_debug_mode_troubleshooting_boundaries():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "esxi-physical-install-checklist.md").read_text(encoding="utf-8")

    for requirement in [
        "## Debug Mode Checklist",
        "Artifact details show base ISO, generated ISO path, virtual media URL, serving URL validation, build log, kickstart summary, and artifact identifiers",
        "Latest iLO debug details show virtual media path, insert/eject counts, boot override, reset target, allowed reset types, and PowerState",
        "Logs/status include safe command/API summaries and redact secrets",
        "Recovery suggestions cover unreachable ISO URL, no virtual media action, stuck old media, unsupported boot override, failed power transition, and missing ESXi reachability",
        "Raw output is available only when safe and useful",
        "no ESXi root password or iLO credential appears in rendered pages, logs, artifacts, or raw output",
    ]:
        assert requirement in text


def test_ovf_checklist_names_debug_mode_troubleshooting_boundaries():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "ovf-ova-prep-checklist.md").read_text(encoding="utf-8")

    for requirement in [
        "## Debug Mode Checklist",
        "Debug Mode/details shows discovered files, descriptor path, referenced files, validation errors, readiness blockers, and source policy",
        "Artifacts and test history are linked or named clearly when created",
        "Recovery suggestions explain which file/path/source issue to fix next",
        "No secrets appear in logs, page output, artifacts, or raw summaries",
        "Automated pytest tests for this flow must use local fixture files, fake clients, mocks, dry-runs, route tests, or template tests",
        "They must not deploy VMs, mount media, call vSphere, or change ESXi state",
    ]:
        assert requirement in text


def test_setup_pages_do_not_render_saved_secret_values():
    root = Path(__file__).resolve().parents[1]
    page_names = ["configuration", "ilo", "storage", "esxi", "vcenter", "windows", "qnap", "netapp", "cisco"]

    for page_name in page_names:
        text = (root / "templates" / "partials" / "pages" / f"{page_name}.html").read_text(encoding="utf-8")
        assert 'type="password"' in text, page_name
        assert 'type="password"' not in text or 'value="{{' not in "\n".join(
            line for line in text.splitlines() if 'type="password"' in line
        ), page_name


def test_placeholder_module_services_report_manual_state():
    for service in [QnapModuleService(), EsxiInstallModuleService(), EsxiConfigModuleService(), WindowsModuleService()]:
        result = service.apply({}, {})
        assert result["ok"] is False
        assert result["implemented"] is False
        assert result["state"] in {"manual_only", "not_implemented"}


def test_live_job_websocket_uses_json_payloads():
    root = Path(__file__).resolve().parents[1]
    main_text = (root / "app" / "main.py").read_text(encoding="utf-8")
    live_job_text = (root / "static" / "js" / "live-job.js").read_text(encoding="utf-8")
    index_text = (root / "templates" / "index.html").read_text(encoding="utf-8")
    websocket_section = main_text.split("async def websocket_job_stream", 1)[1].split("@app.get", 1)[0]

    assert "json.dumps(job" in websocket_section
    assert "yaml.safe_dump(job" not in websocket_section
    assert "JSON.parse" in live_job_text
    assert "JSON.parse" in index_text
