# iLO Section

## What It Does
Manages iLO target connectivity, account policy, SNMP/time/network settings, and pre-run checks.

## How It Works
Routes under iLO module handlers render forms, compare desired vs live state, and stage apply actions.

## How To Update
- UI: `templates/partials/pages/ilo.html`
- Route/handler behavior: `app/modules/ilo/routes.py`
- Domain logic and API calls: `app/modules/ilo/service.py`, `app/stages/ilo/runtime.py`
- Schema changes: `app/modules/ilo/schemas.py`

## Validate
Run iLO preview and a safe apply on a test endpoint; verify live readback and reports.

