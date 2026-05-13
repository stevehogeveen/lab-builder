# Module: esxi_config

## Purpose
Manages post-install ESXi host policy/configuration settings and validation.

## Key Files
- `app/modules/esxi_config/manifest.yml`
- `app/modules/esxi_config/routes.py`
- `app/modules/esxi_config/service.py`
- `app/modules/esxi_config/schemas.py`

## Update Guide
- Config page actions: `routes.py`
- Post-config operation logic: `service.py` and ESXi stage runtime helpers
- Schema/model updates: `schemas.py`

## Validation
Confirm save/preview behavior and run config-only execution scope in preview/real-safe environment.

