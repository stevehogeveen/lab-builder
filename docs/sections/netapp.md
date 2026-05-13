# NetApp Section

## What It Does
Handles ONTAP discovery/config flow and command-template-driven storage protocol operations.

## How It Works
Module routes and services render/setup NetApp inputs and policy-backed actions.

## How To Update
- UI: `templates/partials/pages/netapp.html`, `app/modules/netapp/templates/*`
- Handlers: `app/modules/netapp/routes.py`
- Logic/policy: `app/modules/netapp/service.py`, `app/modules/netapp/policy.yml`
- Schema: `app/modules/netapp/schemas.py`

## Validate
Run NetApp page save/preview path and confirm policy/rendered command outputs stay consistent.

