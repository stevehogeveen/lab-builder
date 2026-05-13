# Reports Section

## What It Does
Exposes generated summaries, configs, exports, and run artifacts for download/review.

## How It Works
Config/execution route handlers load and return saved artifacts from `artifacts/` and `config/` history.

## How To Update
- UI: `templates/partials/pages/reports.html`
- Report/export handlers: `app/modules/configs/routes.py`, `app/modules/execution/routes.py`
- Artifact writers: `app/main.py` runtime helpers

## Validate
Generate sample run/report outputs and verify each report action opens/downloads correct file.

