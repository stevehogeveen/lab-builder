from dataclasses import fields

from app.operator_home import OperatorHomeState, build_operator_home_state


def setup_summary(*, blocker: dict | None = None) -> dict:
    has_issue = blocker is not None
    return {
        "items": [
            {
                "name": "Switch",
                "href": "/modules/cisco",
                "checks_ready": 2 if not has_issue else 1,
                "total_checks": 2,
                "blockers": 1 if has_issue else 0,
                "next_blocker": blocker,
            }
        ]
    }


def test_operator_home_state_has_only_the_contract_fields():
    state = build_operator_home_state(
        kit_name="Kit-01",
        setup_summary=setup_summary(),
        recommended_next_step={"title": "Review the run", "summary": "Review saved work.", "href": "/execution"},
        job={"status": "Idle"},
    )

    assert {field.name for field in fields(OperatorHomeState)} == {
        "KitName",
        "CurrentPhase",
        "DisplayState",
        "Headline",
        "SupportingMessage",
        "DeviceSummary",
        "AttentionItems",
        "NextAction",
        "Progress",
    }
    assert state.KitName == "Kit-01"
    assert state.DisplayState == "ready"
    assert state.NextAction.Href == "/execution"

def test_operator_home_translates_internal_terms_into_plain_language():
    internal_copy = (
        "PROVIDER_MODE=local-readonly returned a Redfish API payload for a "
        "dependency-node capability key environment variable raw error."
    )
    state = build_operator_home_state(
        kit_name="Plain Language Kit",
        setup_summary=setup_summary(
            blocker={
                "label": "Internal failure",
                "details": internal_copy,
                "fix": internal_copy,
                "href": "/modules/cisco",
            }
        ),
        recommended_next_step={"title": "Open switch setup", "summary": internal_copy, "href": "/modules/cisco"},
        job={"status": "Idle"},
    )

    rendered_copy = " ".join(
        [
            state.Headline,
            state.SupportingMessage,
            state.NextAction.Label,
            state.NextAction.SupportingMessage,
            state.AttentionItems[0].Explanation,
            state.AttentionItems[0].Resolution,
        ]
    ).lower()
    for internal_term in [
        "provider_mode",
        "local-readonly",
        "redfish",
        "api payload",
        "dependency-node",
        "capability key",
        "environment variable",
        "raw error",
    ]:
        assert internal_term not in rendered_copy
    assert "hardware interface" in rendered_copy
    assert "required step" in rendered_copy


def test_operator_home_active_run_owns_the_single_next_action():
    state = build_operator_home_state(
        kit_name="Running Kit",
        setup_summary=setup_summary(),
        recommended_next_step={"title": "Review the run", "summary": "Review saved work.", "href": "/execution"},
        job={"status": "Running", "current_stage": "Checking the server"},
    )

    assert state.DisplayState == "running"
    assert state.CurrentPhase == "Build in progress"
    assert state.NextAction.Label == "Open current run"
    assert state.NextAction.Href == "/execution"
