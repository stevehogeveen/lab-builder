# Codex App Planner

Small, standalone helper app for tracking app-creation sessions.

It gives you:

- Lightweight session list and details view
- Project-aware workflow (sessions can be tagged to a project)
- Human-readable git change summaries
- Auto-generated plain-language implementation and test plans
- Codex prompt builder (purpose, audience, and related-session context)
- In-session chat draft loop and usage tracking
- Manual session snapshots, status updates, and delete flow

## Run

From this repository root:

```bash
./makeit
```

Or, if you prefer direct uvicorn:

```bash
uvicorn codex_tracker_app.main:app --reload
```

Open:

- Home: `http://127.0.0.1:8000/`
- Session API: `http://127.0.0.1:8000/api/sessions`

Session data is stored in:

- `codex_tracker_app/data/sessions.json`

This project is intentionally self-contained and does not modify the existing Lab Builder app.
### Session workflow upgrades

- Set project context while creating sessions (project, purpose, audience, linked prior session).
- See remaining Codex usage in the dashboard and session screen (`CODEX_USAGE_QUOTA`, defaults to `120`).
- Open a session to:
  - copy a generated starter prompt
  - send quick suggestions
  - type free-form messages to build codex-ready assistant replies
  - reuse previous sessions in the same project

You can also filter sessions by project on the dashboard.

Optional env var:

```bash
export CODEX_USAGE_QUOTA=200
```
