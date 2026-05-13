# Module: configs (Internal)

## Purpose
Contains global configuration and reporting/export handlers used across the app.

## Key Files
- `app/modules/configs/routes.py`
- `app/core/config.py`
- `app/main.py` (runtime wiring and render context)

## Update Guide
- Form handlers and report actions: `routes.py`
- Normalization/defaulting/validation helpers: `app/core/config.py`
- Shared page context and runtime callable map: `app/main.py`

## Validation
Create/load kit, save global config, and verify report/export actions.

