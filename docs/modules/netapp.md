# Module: netapp

## Purpose
Implements NetApp ONTAP discovery/config flow and protocol command-template behavior.

## Key Files
- `app/modules/netapp/manifest.yml`
- `app/modules/netapp/routes.py`
- `app/modules/netapp/service.py`
- `app/modules/netapp/schemas.py`
- `app/modules/netapp/policy.yml`

## Update Guide
- UI route behavior: `routes.py`
- ONTAP logic and command rendering: `service.py` + `policy.yml`
- Validation/data models: `schemas.py`

## Validation
Save NetApp settings, run preview, and verify command-template output for selected protocol.

