# Windows Section

## What It Does
Stages Windows post-install/vSphere-related settings and validation steps.

## How It Works
Windows routes gather settings, perform probes, and feed execution review for Windows scope.

## How To Update
- UI: `templates/partials/pages/windows.html`
- Route handlers: `app/modules/windows/routes.py`
- Service and platform calls: `app/modules/windows/service.py`, `app/windows.py`
- Schema: `app/modules/windows/schemas.py`

## Validate
Test probe endpoints and run Windows preview to confirm expected staged actions and validations.

