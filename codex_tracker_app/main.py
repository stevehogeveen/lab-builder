from __future__ import annotations

import json
import subprocess
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
REPO_ROOT = Path(__file__).resolve().parents[1]
SESSIONS_PATH = BASE_DIR / "data" / "sessions.json"
CODEX_USAGE_QUOTA = int(os.getenv("CODEX_USAGE_QUOTA", "120"))

app = FastAPI(title="Codex App Planner", docs_url=None)
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@dataclass
class Session:
    session_id: str
    title: str
    project_name: str
    feature_goal: str
    feature_area: str
    session_purpose: str = ""
    audience: str = ""
    applies_to_session_id: str = ""
    notes: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    codex_usage_count: int = 0
    suggestion_focus: str = ""
    project_context_summary: list[dict[str, Any]] = field(default_factory=list)
    status: str = "planning"
    created_at: str = ""
    updated_at: str = ""
    git_summary: dict[str, Any] = field(default_factory=dict)
    plan_steps: list[str] = field(default_factory=list)
    test_plan: list[str] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_project(value: Any) -> str:
    return _normalize_text(value) or "General project"


def _usage_left(all_sessions: list[dict[str, Any]]) -> int:
    used = sum(int(item.get("codex_usage_count") or 0) for item in all_sessions)
    return max(CODEX_USAGE_QUOTA - used, 0)


def _total_usage(all_sessions: list[dict[str, Any]]) -> int:
    return sum(int(item.get("codex_usage_count") or 0) for item in all_sessions)


def _project_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _is_hx_request(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _session_label(session: dict[str, Any]) -> str:
    return _normalize_text(session.get("title") or "(untitled session)")


def _find_applied_session_title(sessions: list[dict[str, Any]], session_id: str) -> str:
    session = find_session(sessions, session_id)
    if not session:
        return ""
    return _session_label(session)


def _utc_like_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_git_status(repo_root: Path | None = None) -> list[str]:
    root = Path(repo_root or REPO_ROOT)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception:
        return []

    if not result.stdout:
        return []
    return [line.strip("\n") for line in result.stdout.splitlines() if line.strip()]


def _git_area(path: str) -> str:
    normalized = path.lower()

    if normalized == "app/main.py" or normalized.startswith("app/"):
        return "backend"
    if normalized.startswith("templates/"):
        return "frontend"
    if normalized.startswith("tests/"):
        return "tests"
    if normalized.startswith("docs/"):
        return "docs"
    if normalized.startswith("static/"):
        return "frontend"
    if normalized.startswith("media/"):
        return "media"
    return "other"


def _git_area_label(area: str) -> str:
    return {
        "backend": "App code",
        "frontend": "Screen and forms",
        "tests": "Checks and quality",
        "docs": "How-to docs",
        "media": "Files and assets",
        "other": "Other files",
    }.get(area, "Other files")


def summarize_git_changes(repo_root: Path | None = None) -> dict[str, Any]:
    lines = [line for line in _run_git_status(repo_root=repo_root) if line]
    if not lines:
        return {
            "status": "clean",
            "count": 0,
            "headline": "No local file changes are waiting to be captured.",
            "areas": {},
            "items": [],
        }

    status_names = {
        "M": "Changed",
        "A": "New",
        "D": "Removed",
        "R": "Renamed",
        "C": "Copied",
        "?": "New untracked",
        "U": "Needs attention",
    }

    items: list[dict[str, Any]] = []
    area_totals: dict[str, int] = {}

    for line in lines:
        marker = _normalize_text(line[:2])
        if marker.startswith("R") or marker.startswith("C"):
            status_key = marker[:1]
            raw_path = (line[3:] or "").split(" -> ")[-1].strip()
        elif marker.startswith("?"):
            status_key = "?"
            raw_path = line[2:].strip()
        else:
            status_key = marker[:1]
            raw_path = line[3:].strip()

        if not raw_path:
            continue

        area = _git_area(raw_path)
        area_totals[area] = area_totals.get(area, 0) + 1
        status = status_names.get(status_key, "Changed")
        items.append(
            {
                "status": status,
                "path": raw_path,
                "area": area,
                "area_label": _git_area_label(area),
                "plain": f"{status}: {raw_path}",
            }
        )

    headline_bits: list[str] = []
    for area, count in sorted(area_totals.items(), key=lambda pair: pair[0] == "other"):
        if area:
            headline_bits.append(f"{_git_area_label(area)} ({count})")

    return {
        "status": "dirty",
        "count": len(items),
        "headline": f"You currently have {len(items)} changed item(s): {', '.join(headline_bits)}.",
        "areas": area_totals,
        "items": items,
    }


def _goal_words_to_plan(
    goal: str,
    summary: dict[str, Any],
    *,
    session_purpose: str = "",
    audience: str = "",
    project_name: str = "",
    related_context: list[dict[str, Any]] | None = None,
) -> list[str]:
    normalized_goal = _normalize_text(goal).lower()
    areas = set((summary.get("areas") or {}).keys())
    plan: list[str] = []

    if not normalized_goal:
        plan.append("Write one clear feature sentence: what should this new app do for the first user?" )
        return plan[:6]

    if any(word in normalized_goal for word in ("route", "api", "handler", "server", "request", "response")):
        plan.append("Finish the backend behavior first: input handling, validation, and response text.")

    if any(word in normalized_goal for word in ("page", "screen", "form", "ui", "template", "layout")):
        plan.append("Finish the screen flow next: what the user sees and the easiest wording for each action.")

    if any(word in normalized_goal for word in ("test", "coverage", "quality", "validate", "ci")):
        plan.append("Add checks and test names before coding final behavior.")

    if "backend" in areas:
        plan.append("Keep one session field updated for every backend file changed.")
    if "frontend" in areas:
        plan.append("Rename confusing labels into plain language and remove any technical-only wording on controls.")
    if "tests" in areas:
        plan.append("Tie each changed behavior to at least one test that can fail before implementation is correct.")
    if not areas:
        plan.append("No changes are staged yet; capture a snapshot after adding core files so this plan becomes specific.")

    if summary.get("count", 0) >= 8:
        plan.append("Large change set: split into smaller milestones and keep this session focused to one milestone.")

    if _normalize_text(project_name):
        plan.append(f"Keep this session in scope for the '{_normalize_project(project_name)}' project.")

    if _normalize_text(session_purpose):
        plan.append(f"Check your work against the stated purpose: {_normalize_text(session_purpose)}.")

    if _normalize_text(audience):
        plan.append(f"Keep wording and behavior simple for the audience: {_normalize_text(audience)}.")

    if related_context:
        plan.append("Review one previous session from this project before implementing.")

    if not plan:
        plan.append("Create a short rollout checklist with exactly three acceptance checks.")

    return plan[:6]


def _build_test_plan(
    goal: str,
    summary: dict[str, Any],
    *,
    audience: str = "",
    project_name: str = "",
    related_context_count: int = 0,
) -> list[str]:
    tests = ["Start with one quick smoke check: app route can be opened and a session is saved."]
    areas = set((summary.get("areas") or {}).keys())

    if "backend" in areas:
        tests.append("Add a focused backend test for the new form/endpoint handling this feature.")
    if "frontend" in areas:
        tests.append("Run a visual check that form labels and messages are clear to non-technical users.")
    if "tests" in areas:
        tests.append("Update existing session tests and add at least one negative test case for bad input.")
    if "docs" in areas:
        tests.append("Update one doc page that explains the new flow in plain English.")
    if project_name:
        tests.append("Add a manual check that this session does not break another area in the same project.")
    if _normalize_text(audience):
        tests.append("Run one real-user path check for the intended audience.")
    if related_context_count:
        tests.append("Validate this change in sequence with one earlier session from the same project.")

    return tests


def _related_project_sessions(
    sessions: list[dict[str, Any]],
    current_session: dict[str, Any],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    project = _normalize_project(current_session.get("project_name"))
    current_id = _normalize_text(current_session.get("session_id"))
    related = [
        {
            "session_id": session.get("session_id", ""),
            "title": _session_label(session),
            "goal": _normalize_text(session.get("feature_goal")),
            "status": _normalize_text(session.get("status")),
            "updated_at": _normalize_text(session.get("updated_at")),
        }
        for session in sessions
        if _normalize_project(session.get("project_name")) == project
        and _normalize_text(session.get("session_id")) != current_id
    ]
    related.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return related[:limit]


def _build_project_context(
    sessions: list[dict[str, Any]],
    current_session: dict[str, Any],
) -> list[dict[str, Any]]:
    return _related_project_sessions(sessions, current_session, limit=6)


def _build_codex_prompt(session: dict[str, Any], sessions: list[dict[str, Any]]) -> dict[str, str]:
    project_name = _normalize_project(session.get("project_name"))
    purpose = _normalize_text(session.get("session_purpose"))
    audience = _normalize_text(session.get("audience"))
    feature_goal = _normalize_text(session.get("feature_goal"))
    feature_area = _normalize_text(session.get("feature_area"))
    applies_to_session_id = _normalize_text(session.get("applies_to_session_id"))
    git_summary = session.get("git_summary") or {}
    context_sessions = _build_project_context(sessions, session)

    intro: list[str] = [
        f"You are helping with project '{project_name}'.",
        f"Session: {_session_label(session)}",
        f"Goal: {feature_goal or 'Not set yet.'}",
        f"Purpose: {purpose or 'No purpose entered.'}",
        f"Audience: {audience or 'General operator/user.'}",
    ]

    if applies_to_session_id:
        target = _find_applied_session_title(sessions, applies_to_session_id) or applies_to_session_id
        intro.append(f"Apply guidance to existing work: \"{target}\".")

    if context_sessions:
        intro.append("")
        intro.append("Recent project sessions you can reuse:")
        for item in context_sessions[:3]:
            intro.append(
                f"- {item['title']} ({item.get('status') or 'unknown'}, updated {item.get('updated_at') or 'n/a'})"
            )

    intro.append("")
    intro.append(f"Feature area: {feature_area or 'General'}")
    intro.append(f"Current git snapshot: {git_summary.get('headline', 'No changes captured yet.')}")
    intro.append("Output in simple, clear language.")

    suggestion_title = _find_applied_session_title(sessions, applies_to_session_id)
    suggestions = [
        f"Give me a minimum viable version of {project_name}.",
        "Draft exact endpoint and form edits for this session.",
        "Write test cases I can run before committing.",
        "List exactly what should change first in the UI.",
        "Show a short handoff I can paste into Codex now.",
    ]
    if context_sessions:
        first_context = context_sessions[0].get("title")
        if first_context:
            suggestions.append(f"Compare against {first_context} and reuse what worked.")
    if suggestion_title:
        suggestions.append(f"Adapt just the scope from \"{suggestion_title}\" and keep this feature aligned.")

    return {
        "title": f"Codex prompt for {_session_label(session)}",
        "prompt": "\n".join(intro),
        "command": "codex",
        "suggestions": suggestions[:6],
    }


def _build_chat_reply(session: dict[str, Any], message: str, sessions: list[dict[str, Any]]) -> str:
    prompt = _normalize_text(message).lower()
    feature_area = _normalize_text(session.get("feature_area")).lower()
    feature_goal = _normalize_text(session.get("feature_goal"))
    if not prompt:
        return "Send a short instruction and I will convert it into a codex-ready next step."

    if "test" in prompt or "verify" in prompt:
        return (
            f"For '{feature_goal or 'this feature'}', test plan first: "
            "1) smoke route check, 2) check changed area form/output, 3) one negative case."
        )

    if "plan" in prompt:
        return f"Use this session plan: {', '.join(session.get('plan_steps') or ['Define one small milestone first']).strip()}"

    if "context" in prompt or "reuse" in prompt:
        context = _build_project_context(sessions, session)
        if not context:
            return "No prior sessions in this project. Add a linked session and capture a baseline to reuse."
        return (
            "Reuse prior project work by checking these sessions first: "
            + ", ".join(item["title"] for item in context[:3])
        )

    if "session" in prompt:
        return "Keep this session scoped to one clear milestone and mark status when you move forward or pause."

    if "template" in prompt or "prompt" in prompt:
        codex_prompt = _build_codex_prompt(session, sessions)["prompt"]
        return f"Here is a starter prompt:\n{codex_prompt}"

    if feature_area in prompt:
        return f"Keep implementation in {feature_area}: one behavior at a time, then test before moving on."

    return (
        f"Start from the goal '{feature_goal or 'current feature'}', then implement the smallest user-visible change first."
    )


def _load_dashboard_context(
    project_filter: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], int, int, dict[str, Any]]:
    all_sessions = sorted(load_sessions(), key=lambda item: item.get("updated_at") or "", reverse=True)
    sessions, projects = _enrich_sessions_for_listing(
        all_sessions,
        project_filter=project_filter,
    )
    workspace = summarize_git_changes()
    return (
        all_sessions,
        sessions,
        projects,
        _total_usage(all_sessions),
        _usage_left(all_sessions),
        workspace,
    )


def _render_session_panel(session: dict[str, Any], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "session": session,
        "current_summary": summarize_git_changes(),
        "related_project_sessions": [
            item
            for item in _project_sessions_for_session(session, sessions)
            if item.get("session_id") != session.get("session_id")
        ][:6],
        "project_name": _normalize_project(session.get("project_name")),
        "codex_usage_left": _usage_left(sessions),
        "codex_total_budget": CODEX_USAGE_QUOTA,
        "applies_to_title": _find_applied_session_title(sessions, _normalize_text(session.get("applies_to_session_id"))),
        "suggestions": _build_codex_prompt(session, sessions)["suggestions"],
        "title": session.get("title", "Session"),
        "codex_run": codex_run_signature(session),
    }


def _create_session(
    title: str,
    feature_goal: str,
    feature_area: str,
    *,
    project_name: str,
    session_purpose: str,
    audience: str,
    applies_to_session_id: str,
    notes: str = "",
    existing_sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now = _utc_like_now()
    feature_goal = _normalize_text(feature_goal)
    feature_area = _normalize_text(feature_area)
    notes = _normalize_text(notes)
    project_name = _normalize_project(project_name)
    session_purpose = _normalize_text(session_purpose)
    audience = _normalize_text(audience)
    applies_to_session_id = _normalize_text(applies_to_session_id)
    existing_sessions = existing_sessions or []
    summary = summarize_git_changes()
    session_id = str(uuid.uuid4())
    context_session = {
        "session_id": session_id,
        "project_name": project_name,
        "title": title,
        "updated_at": now,
        "status": "planning",
        "feature_goal": feature_goal,
    }
    related_context = _related_project_sessions(existing_sessions, context_session, limit=4)
    plan_steps = _goal_words_to_plan(
        feature_goal,
        summary,
        session_purpose=session_purpose,
        audience=audience,
        project_name=project_name,
        related_context=related_context,
    )
    test_plan = _build_test_plan(
        feature_goal,
        summary,
        audience=audience,
        project_name=project_name,
        related_context_count=len(related_context),
    )
    codex_prompt = _build_codex_prompt(
        {
            "session_id": session_id,
            "title": title,
            "project_name": project_name,
            "feature_goal": feature_goal,
            "plan_steps": plan_steps,
            "feature_area": feature_area,
            "session_purpose": session_purpose,
            "audience": audience,
            "applies_to_session_id": applies_to_session_id,
            "git_summary": summary,
        },
        existing_sessions,
    )["prompt"]
    messages = [
        {
            "role": "system",
            "text": f"Session '{_normalize_text(title) or 'untitled'}' created for project '{project_name}'.",
            "created_at": now,
        },
        {
            "role": "assistant",
            "text": codex_prompt,
            "created_at": now,
        },
    ]

    session = Session(
        session_id=session_id,
        title=_normalize_text(title) or "New coding session",
        project_name=project_name,
        feature_goal=feature_goal,
        feature_area=feature_area,
        session_purpose=session_purpose,
        audience=audience,
        applies_to_session_id=applies_to_session_id,
        notes=notes,
        messages=messages,
        suggestion_focus=f"{project_name}: {feature_goal[:80]}",
        project_context_summary=related_context,
        status="planning",
        created_at=now,
        updated_at=now,
        git_summary=summary,
        plan_steps=plan_steps,
        test_plan=test_plan,
        codex_usage_count=0,
        snapshots=[
            {
                "captured_at": now,
                "headline": summary.get("headline", "No local changes"),
                "areas": summary.get("areas", {}),
            }
        ],
    )

    return session.__dict__


def load_sessions() -> list[dict[str, Any]]:
    if not SESSIONS_PATH.exists():
        return []
    try:
        payload = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []

    sessions: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            sessions.append(item)
    return sessions


def save_sessions(sessions: list[dict[str, Any]]) -> None:
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = SESSIONS_PATH.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(sessions, handle, indent=2, ensure_ascii=False)
    temp.replace(SESSIONS_PATH)


def find_session(sessions: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
    return next((session for session in sessions if session.get("session_id") == session_id), None)


def normalize_status(value: str) -> str:
    normalized = _normalize_text(value).lower()
    if normalized in {"done", "planning", "in_progress", "blocked"}:
        return normalized
    return "planning"


def _projects_from_sessions(sessions: list[dict[str, Any]]) -> list[str]:
    project_set = {_normalize_project(session.get("project_name")) for session in sessions}
    return sorted(project_set)


def _enrich_sessions_for_listing(
    sessions: list[dict[str, Any]],
    *,
    project_filter: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    project_set = _projects_from_sessions(sessions)
    filtered = _dashboard_session_filters(sessions, project_filter=project_filter)
    by_id = {item.get("session_id"): item for item in sessions if item.get("session_id")}
    enriched: list[dict[str, Any]] = []

    for session in filtered:
        item = dict(session)
        applies_to_session_id = _normalize_text(item.get("applies_to_session_id"))
        if applies_to_session_id:
            item["applies_to_title"] = _session_label(by_id.get(applies_to_session_id, {})) or applies_to_session_id
        else:
            item["applies_to_title"] = ""
        item["codex_usage_count"] = int(item.get("codex_usage_count") or 0)
        enriched.append(item)

    return enriched, project_set


def _dashboard_session_filters(
    sessions: list[dict[str, Any]],
    *,
    project_filter: str | None = None,
) -> list[dict[str, Any]]:
    project_filter = _normalize_project(project_filter)
    if not project_filter:
        return sessions
    return [session for session in sessions if _normalize_project(session.get("project_name")) == project_filter]


def codex_run_signature(session: dict[str, Any]) -> dict[str, Any]:
    sessions = load_sessions()
    prompt = _build_codex_prompt(session, sessions)
    usage_left = _usage_left(load_sessions())
    return {
        "command": prompt["command"],
        "prompt": prompt["prompt"],
        "checks": [
            "Create one small milestone",
            "Capture a plan snapshot before coding",
            "Run manual verification on the changed screen",
            "Run focused tests from the session checklist",
        ],
        "suggestions": prompt["suggestions"],
        "usage_left": usage_left,
    }


def _project_sessions_for_session(session: dict[str, Any], sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    project_name = _normalize_project(session.get("project_name"))
    return [
        item
        for item in sorted(sessions, key=lambda item: item.get("updated_at") or "", reverse=True)
        if _normalize_project(item.get("project_name")) == project_name
    ]


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, project: str | None = None):
    all_sessions, sessions, projects, usage_used, usage_left, workspace_summary = _load_dashboard_context(
        project_filter=project
    )
    total = len(all_sessions)
    selected_session = sessions[0] if sessions else None
    selected_context = {}
    if selected_session:
        selected_context = _render_session_panel(selected_session, all_sessions)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "sessions": sessions,
            "all_sessions": all_sessions,
            "projects": projects,
            "project_filter": _normalize_project(project),
            "total_sessions": total,
            "selected_session": selected_session,
            "selected_session_id": selected_session.get("session_id") if selected_session else "",
            "usage_used": usage_used,
            "usage_left": usage_left,
            "codex_usage": {
                "quota": CODEX_USAGE_QUOTA,
                "used": usage_used,
                "left": usage_left,
            },
            "workspace_summary": workspace_summary,
            **selected_context,
            "title": "Codex App Planner",
        },
    )


@app.get("/session-list", response_class=HTMLResponse)
async def session_list_fragment(request: Request, project: str | None = None):
    all_sessions, sessions, projects, usage_used, usage_left, workspace_summary = _load_dashboard_context(
        project_filter=project
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/session_list.html",
        context={
            "sessions": sessions,
            "projects": projects,
            "project_filter": _normalize_project(project),
            "usage_used": usage_used,
            "usage_left": usage_left,
            "workspace_summary": workspace_summary,
            "title": "Session list",
        },
    )


@app.get("/session/{session_id}", response_class=HTMLResponse)
async def view_session(request: Request, session_id: str):
    sessions = load_sessions()
    session = find_session(sessions, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    context = _render_session_panel(session, sessions)
    return templates.TemplateResponse(
        request=request,
        name="session.html",
        context={"request": request, **context},
    )


@app.get("/session/{session_id}/panel", response_class=HTMLResponse)
async def session_panel(request: Request, session_id: str):
    sessions = load_sessions()
    session = find_session(sessions, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return templates.TemplateResponse(
        request=request,
        name="partials/session_panel.html",
        context={"request": request, **_render_session_panel(session, sessions)},
    )


@app.post("/sessions", response_class=HTMLResponse)
async def create_session(
    request: Request,
    title: str = Form(""),
    feature_goal: str = Form(""),
    feature_area: str = Form(""),
    project_name: str = Form(""),
    session_purpose: str = Form(""),
    audience: str = Form(""),
    applies_to_session_id: str = Form(""),
    notes: str = Form(""),
):
    sessions = load_sessions()
    session = _create_session(
        title=title,
        feature_goal=feature_goal,
        feature_area=feature_area,
        project_name=project_name,
        session_purpose=session_purpose,
        audience=audience,
        applies_to_session_id=applies_to_session_id,
        notes=notes,
        existing_sessions=sessions,
    )
    sessions.append(session)
    save_sessions(sessions)
    if _is_hx_request(request):
        all_sessions, enriched, projects, _, _, workspace_summary = _load_dashboard_context()
        return templates.TemplateResponse(
            request=request,
            name="partials/session_list.html",
            context={
                "request": request,
                "sessions": enriched,
                "projects": projects,
                "project_filter": _normalize_project(""),
                "usage_used": _total_usage(all_sessions),
                "usage_left": _usage_left(all_sessions),
                "workspace_summary": workspace_summary,
            },
        )
    return RedirectResponse(url=f"/session/{session['session_id']}", status_code=303)


@app.post("/sessions/hx", response_class=HTMLResponse)
async def create_session_fragment(
    request: Request,
    title: str = Form(""),
    feature_goal: str = Form(""),
    feature_area: str = Form(""),
    project_name: str = Form(""),
    session_purpose: str = Form(""),
    audience: str = Form(""),
    applies_to_session_id: str = Form(""),
    notes: str = Form(""),
):
    sessions = load_sessions()
    session = _create_session(
        title=title,
        feature_goal=feature_goal,
        feature_area=feature_area,
        project_name=project_name,
        session_purpose=session_purpose,
        audience=audience,
        applies_to_session_id=applies_to_session_id,
        notes=notes,
        existing_sessions=sessions,
    )
    sessions.append(session)
    save_sessions(sessions)

    all_sessions, enriched, projects, _, _, workspace_summary = _load_dashboard_context()
    return templates.TemplateResponse(
        request=request,
        name="partials/session_list.html",
        context={
            "request": request,
            "sessions": enriched,
            "projects": projects,
            "project_filter": "",
            "usage_used": _total_usage(all_sessions),
            "usage_left": _usage_left(all_sessions),
            "workspace_summary": workspace_summary,
        },
    )


@app.post("/session/{session_id}/status", response_class=HTMLResponse)
async def set_status(
    request: Request,
    session_id: str,
    status: str = Form("planning"),
):
    sessions = load_sessions()
    session = find_session(sessions, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session["status"] = normalize_status(status)
    session["updated_at"] = _utc_like_now()
    save_sessions(sessions)
    if _is_hx_request(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/session_panel.html",
            context={"request": request, **_render_session_panel(session, sessions)},
        )
    return RedirectResponse(url=f"/session/{session_id}", status_code=303)


@app.post("/session/{session_id}/snapshot", response_class=HTMLResponse)
async def capture_snapshot(request: Request, session_id: str):
    sessions = load_sessions()
    session = find_session(sessions, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot = summarize_git_changes()
    session["git_summary"] = snapshot
    session["updated_at"] = _utc_like_now()
    session.setdefault("snapshots", []).append(
        {
            "captured_at": _utc_like_now(),
            "headline": snapshot.get("headline", "No local changes"),
            "areas": snapshot.get("areas", {}),
        }
    )

    save_sessions(sessions)
    if _is_hx_request(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/session_panel.html",
            context={"request": request, **_render_session_panel(session, sessions)},
        )
    return RedirectResponse(url=f"/session/{session_id}", status_code=303)


@app.post("/session/{session_id}/note", response_class=HTMLResponse)
async def add_note(
    request: Request,
    session_id: str,
    note: str = Form(""),
):
    sessions = load_sessions()
    session = find_session(sessions, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    cleaned = _normalize_text(note)
    if cleaned:
        session.setdefault("notes", "")
        existing = _normalize_text(session.get("notes"))
        session["notes"] = f"{existing}\n\n- {cleaned}" if existing else f"- {cleaned}"
        session["updated_at"] = _utc_like_now()
        save_sessions(sessions)

    if _is_hx_request(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/session_panel.html",
            context={"request": request, **_render_session_panel(session, sessions)},
        )
    return RedirectResponse(url=f"/session/{session_id}", status_code=303)


@app.post("/session/{session_id}/chat", response_class=HTMLResponse)
async def chat_with_codex(
    request: Request,
    session_id: str,
    message: str = Form(""),
    suggestion: str = Form(""),
):
    sessions = load_sessions()
    session = find_session(sessions, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    user_message = _normalize_text(message or suggestion)
    if not user_message:
        return RedirectResponse(url=f"/session/{session_id}", status_code=303)

    session.setdefault("messages", [])
    session["messages"].append(
        {
            "role": "user",
            "text": user_message,
            "created_at": _utc_like_now(),
        }
    )
    session["messages"].append(
        {
            "role": "assistant",
            "text": _build_chat_reply(session, user_message, sessions),
            "created_at": _utc_like_now(),
        }
    )
    session["codex_usage_count"] = int(session.get("codex_usage_count") or 0) + 1
    session["updated_at"] = _utc_like_now()
    save_sessions(sessions)

    if _is_hx_request(request):
        return templates.TemplateResponse(
            request=request,
            name="partials/session_panel.html",
            context={"request": request, **_render_session_panel(session, sessions)},
        )
    return RedirectResponse(url=f"/session/{session_id}", status_code=303)


@app.post("/session/{session_id}/delete", response_class=HTMLResponse)
async def delete_session(request: Request, session_id: str):
    sessions = [session for session in load_sessions() if session.get("session_id") != session_id]
    save_sessions(sessions)
    if _is_hx_request(request):
        all_sessions, _, projects, usage_used, usage_left, workspace_summary = _load_dashboard_context()
        if all_sessions:
            first = all_sessions[0]
            return templates.TemplateResponse(
                request=request,
                name="partials/session_panel.html",
                context={
                    "request": request,
                    **_render_session_panel(first, all_sessions),
                },
            )
        return HTMLResponse("<div class='card'><p class='muted'>Session removed. Pick another session from the sidebar.</p></div>")
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/sessions")
async def api_sessions():
    sessions = load_sessions()
    return {
        "app": "Codex App Planner",
        "workspace_summary": summarize_git_changes(),
        "sessions": sessions,
        "projects": _projects_from_sessions(sessions),
        "codex_usage": {
            "quota": CODEX_USAGE_QUOTA,
            "used": _total_usage(sessions),
            "left": _usage_left(sessions),
        },
    }
