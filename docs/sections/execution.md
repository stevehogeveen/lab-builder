# Execution (Run Center) Section

## What It Does
Previews and launches staged runs with explicit execution confirmation and live job tracking.

## How It Works
Execution handlers in `app/modules/execution/routes.py` validate scope, enforce confirmation, and start background jobs.

## How To Update
- UI: `templates/partials/pages/execution.html`
- Run-center handlers: `app/modules/execution/routes.py`
- Job execution pipeline: `app/main.py`, `app/core/jobs.py`, `app/stages/*`

## Validate
Test preview, blocked execution paths, successful start path, and live status updates.

