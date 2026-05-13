# Dashboard Section

## What It Does
Provides mission-control status for the current kit and overall workflow readiness.

## How It Works
Rendered through page partials and shared `render_page` context built in `app/main.py`.

## How To Update
- Change dashboard layout/content in `templates/partials/pages/dashboard.html`.
- Update computed readiness context in `app/main.py` helpers that build `workflow_contexts` and `section_states`.

## Validate
Load `/dashboard` and confirm section status tones and navigation state match kit data.
