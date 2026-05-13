# Module: storage

## Purpose
Owns storage discovery, RAID planning, approval gating, and storage apply workflow.

## Key Files
- `app/modules/storage/manifest.yml`
- `app/modules/storage/routes.py`
- `app/modules/storage/service.py`
- `app/modules/storage/schemas.py`

## Update Guide
- Route/UI interactions: `routes.py`
- Planning/apply logic: `service.py` and `app/stages/storage/runtime.py`
- Contracts/validation shapes: `schemas.py`
- Keep confirmation and safety gates intact when altering apply behavior.

## Validation
Verify discovery, approval, preview, apply, and retry-storage execution path.

