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
    cfg["ip_plan"]["gateway"] = "10.10.8.1"
    cfg["shared_network"]["dns_servers"] = ["10.10.8.10"]
    cfg["vmware"]["vcenter_install"].update(
        {
            "target_ip": "10.10.8.50",
            "system_name": "10.10.8.50",
            "vm_name": "SVCNTR-Kit-01",
            "iso_path": str(iso),
            "esxi_host": "10.10.8.111",
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
    assert contexts["vcenter"]["target"] == "10.10.8.50"
    assert contexts["vcenter"]["review_href"] == "/vcenter"
    assert any(check["label"] == "Install context" for check in contexts["vcenter"]["checks"])
    assert contexts["vmware"]["name"] == "vCenter"
    assert contexts["vmware"]["target"] == "10.10.8.50"
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
