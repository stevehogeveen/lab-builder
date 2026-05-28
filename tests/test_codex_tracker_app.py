from fastapi.testclient import TestClient
import pytest

import codex_tracker_app.main as planner


@pytest.fixture()
def planner_client(tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "SESSIONS_PATH", tmp_path / "sessions.json")
    monkeypatch.setattr(planner, "_run_git_status", lambda repo_root=None: [])
    with TestClient(planner.app) as client:
        yield client


def test_dashboard_renders_new_app_form(planner_client):
    response = planner_client.get("/")
    assert response.status_code == 200
    assert "Codex App Planner" in response.text
    assert "Create a new planning session" in response.text
    assert "Planning sessions" in response.text


def test_creating_session_stores_snapshot_and_generates_plan(planner_client, monkeypatch):
    monkeypatch.setattr(
        planner,
        "_run_git_status",
        lambda repo_root=None: [
            " M app/main.py",
            " M templates/index.html",
            "?? tests/new_test.py",
        ],
    )

    response = planner_client.post(
        "/sessions",
        data={
            "title": "Codex runner flow",
            "project_name": "Portal",
            "session_purpose": "Simplify app creation planning",
            "audience": "Operators who do releases",
            "feature_goal": "Create a simple page to run codex plan and tests",
            "feature_area": "backend",
            "notes": "Start with a simple session list and capture summary.",
            "applies_to_session_id": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Codex runner flow" in response.text
    assert "Simple plan" in response.text
    assert "backend" in response.text.lower()

    data = planner.load_sessions()
    assert len(data) == 1
    session = data[0]
    assert session["title"] == "Codex runner flow"
    assert session["project_name"] == "Portal"
    assert session["feature_area"] == "backend"
    assert session["git_summary"]["status"] == "dirty"
    assert session["git_summary"]["count"] == 3
    assert session["session_purpose"] == "Simplify app creation planning"
    assert session["audience"] == "Operators who do releases"


def test_session_status_and_note_updates(planner_client):
    session = planner._create_session(
        "Session update",
        "track note updates",
        "frontend",
        project_name="Portal",
        session_purpose="Refine notes",
        audience="Operators",
        applies_to_session_id="",
    )
    planner.save_sessions([session])

    response = planner_client.post(
        f"/session/{session['session_id']}/status",
        data={"status": "done"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "done" in response.text

    note_text = "Add a final smoke test for this one feature"
    response = planner_client.post(
        f"/session/{session['session_id']}/note",
        data={"note": note_text},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert note_text in response.text

    sessions = planner.load_sessions()
    updated = sessions[0]
    assert updated["status"] == "done"
    assert note_text in updated.get("notes", "")


def test_chat_increments_codex_usage_and_saves_message(planner_client):
    session = planner._create_session(
        "Session chat",
        "Build better prompts",
        "frontend",
        project_name="Portal",
        session_purpose="Help codex with wording",
        audience="Operators",
        applies_to_session_id="",
    )
    planner.save_sessions([session])

    response = planner_client.post(
        f"/session/{session['session_id']}/chat",
        data={"message": "Give me a quick plan"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Start from the goal" in response.text or "Use this session plan" in response.text

    refreshed = planner.load_sessions()
    assert refreshed[0]["codex_usage_count"] == 1
    assert any(msg["role"] == "user" for msg in refreshed[0].get("messages", []))


def test_project_filters_and_linked_sessions(planner_client, monkeypatch):
    monkeypatch.setattr(
        planner,
        "_run_git_status",
        lambda repo_root=None: [],
    )

    first = planner._create_session(
        "Design session",
        "Create project baseline",
        "frontend",
        project_name="Alpha",
        session_purpose="Start flow for app",
        audience="Field users",
        applies_to_session_id="",
    )
    second = planner._create_session(
        "Build session",
        "Refine flow with test cases",
        "backend",
        project_name="Alpha",
        session_purpose="Continue flow for app",
        audience="Field users",
        applies_to_session_id=first["session_id"],
        existing_sessions=[first],
    )
    planner.save_sessions([first, second])

    response = planner_client.get("/?project=Alpha")
    assert response.status_code == 200
    assert "Planning sessions" in response.text
    assert "Design session" in response.text
    assert "Build session" in response.text


def test_new_session_inherits_project_from_linked_session(planner_client):
    base = planner._create_session(
        "Baseline session",
        "Set base context",
        "backend",
        project_name="Delta",
        session_purpose="Keep release prep stable",
        audience="Operators",
        applies_to_session_id="",
    )
    follower = planner._create_session(
        "Follow-up session",
        "Refine release prep",
        "tests",
        project_name="",
        session_purpose="",
        audience="",
        applies_to_session_id=base["session_id"],
        existing_sessions=[base],
    )
    assert follower["project_name"] == "Delta"
    assert follower["session_purpose"] == "Keep release prep stable"
    assert follower["audience"] == "Operators"


def test_dashboard_shows_session_detail_fields(planner_client):
    first = planner._create_session(
        "Dashboard details",
        "Show richer session cards",
        "frontend",
        project_name="Gamma",
        session_purpose="Understand at a glance",
        audience="Field operators",
        applies_to_session_id="",
    )
    second = planner._create_session(
        "Linked details",
        "Reuse prior context",
        "backend",
        project_name="Gamma",
        session_purpose="Track follow-up task",
        audience="Engineers",
        applies_to_session_id=first["session_id"],
        existing_sessions=[first],
    )
    planner.save_sessions([first, second])

    response = planner_client.get("/?project=Gamma")
    assert response.status_code == 200
    assert "Project: Gamma" in response.text
    assert "Purpose:" in response.text
    assert "Audience:" in response.text
    assert "Applies to:" in response.text
    assert "Codex calls:" in response.text
