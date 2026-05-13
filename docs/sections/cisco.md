# Cisco Section

## What It Does
Supports Cisco switch setup/validation as an optional workflow module.

## How It Works
Manifest-enabled module contributes navigation, routes, and staged validation/apply behavior.

## How To Update
- UI: `templates/partials/pages/cisco.html`
- Handlers: `app/modules/cisco/routes.py`
- Service logic: `app/modules/cisco/service.py`
- Schema + manifest: `app/modules/cisco/schemas.py`, `app/modules/cisco/manifest.yml`

## Validate
Enable Cisco in included components, verify sidebar visibility, and run preview/apply checks.

