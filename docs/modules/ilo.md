# Module: iLO

## Purpose
Implements iLO discovery, validation, preview, apply, status, and repair behaviors.

## Key Files
- `app/modules/ilo/manifest.yml`
- `app/modules/ilo/routes.py`
- `app/modules/ilo/service.py`
- `app/modules/ilo/schemas.py`

## Update Guide
- Update endpoints/page actions in `routes.py`.
- Update iLO behavior and comparisons in `service.py` and `app/stages/ilo/runtime.py`.
- Update request/response models in `schemas.py`.
- Keep manifest metadata aligned with navigation and route prefix.

## Validation
Run iLO preview/apply flow and verify readback checks and artifacts.

## Manual Physical Testing
Use `docs/ilo-physical-flow-checklist.md` when the real HPE iLO/server is available. Automated validation must keep using fake clients, fake inventory, route tests, and mocks.
