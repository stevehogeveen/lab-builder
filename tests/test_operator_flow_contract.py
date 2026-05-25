from pathlib import Path

import app.main as main


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

    assert "vmware" in contexts
    assert contexts["vmware"]["name"] == "vCenter"
    assert contexts["vmware"]["target"] == "10.10.8.50"
    assert contexts["vmware"]["review_href"] == "/vcenter"
    assert any(check["label"] == "Install context" for check in contexts["vmware"]["checks"])

    recommended = main.build_recommended_next_step(cfg, contexts)
    summary = main.build_setup_precheck_summary(cfg, contexts, recommended)
    assert any(item["key"] == "vmware" for item in summary["items"])

    page_summary = main.build_page_precheck_summary("vcenter", cfg, contexts)
    assert page_summary is not None
    assert page_summary["key"] == "vmware"
