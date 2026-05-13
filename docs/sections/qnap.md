# QNAP Section

## What It Does
Provides optional QNAP setup and validation flow for kits that include QNAP.

## How It Works
QNAP module routes manage page rendering, saves, and staged execution interactions.

## How To Update
- UI: `templates/partials/pages/qnap.html`
- Route handlers: `app/modules/qnap/routes.py`
- Service logic: `app/modules/qnap/service.py`
- Schema/manifest: `app/modules/qnap/schemas.py`, `app/modules/qnap/manifest.yml`

## Validate
Toggle QNAP inclusion, verify section appears/disappears, then test save/preview behavior.

