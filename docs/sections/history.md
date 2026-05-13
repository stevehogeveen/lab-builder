# History Section

## What It Does
Shows historical run and configuration activity for troubleshooting and audit context.

## How It Works
History content is rendered from stored run/config/event artifacts under `artifacts/history` and run logs.

## How To Update
- UI: `templates/partials/pages/history.html`
- History loading/render context: `app/main.py`
- Event recording points: configs/execution/storage/ilo route handlers and stage runtime logic

## Validate
Run at least one workflow, then confirm history entries and ordering are correct on refresh.
