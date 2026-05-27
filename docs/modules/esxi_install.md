# Module: esxi_install

## Purpose
Handles ESXi installer media workflow (base ISO, custom ISO build, virtual media setup checks).

## Key Files
- `app/modules/esxi_install/manifest.yml`
- `app/modules/esxi_install/routes.py`
- `app/modules/esxi_install/service.py`
- `app/modules/esxi_install/schemas.py`

## Update Guide
- Install form and actions: `routes.py`
- Build/serve/mount behavior: `service.py`, `app/stages/esxi/runtime.py`, `app/esxi/builder.py`
- Input/output contracts: `schemas.py`

## Validation
Run ESXi install preview, verify generated ISO URL checks, and download artifact path.

## Manual Physical Testing
Use `docs/esxi-physical-install-checklist.md` when the real iLO/server is available. Automated validation must stay on mocks, fake iLO inventory, dry-runs, route tests, and template tests.
