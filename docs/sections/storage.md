# Storage Section

## What It Does
Discovers server storage, builds RAID plans, manages approval, and applies guarded storage changes.

## How It Works
Storage handlers coordinate discovery, plan selection, approval state, and execution handoff.

## How To Update
- UI: `templates/partials/pages/storage.html`
- Routes/workflow handlers: `app/modules/storage/routes.py`
- Planning/apply logic: `app/modules/storage/service.py`, `app/stages/storage/runtime.py`
- Data schema: `app/modules/storage/schemas.py`

## Validate
Run discovery, approve a plan, test preview path, then confirm apply gating and artifact generation.

