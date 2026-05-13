# Lab Builder

Lab Builder is a FastAPI application for offline/controlled lab provisioning workflows. It helps operators configure kit settings, stage infrastructure actions (iLO, storage, ESXi, Windows, and optional modules), and run guarded execution with artifacts and diagnostics.

## What The App Does

- Centralizes per-kit configuration under `config/kits/`.
- Provides sectioned setup pages (Global, iLO, Storage, ESXi, Windows, extended modules).
- Executes staged workflows from Run Center with confirmations and background job tracking.
- Writes run/history/debug artifacts under `artifacts/`.

## Quick Start

Create or refresh the local environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

```bash
./scripts/start-app-dev
```

or:

```bash
./scripts/start-app
```

Default URL: `http://localhost:8000`

## High-Level Architecture

- App entrypoint: `app/main.py`
- Module loading: `app/core/registry.py` (`manifest.yml` + `register_module_routes`)
- Stage execution framework: `app/stages/*` and `app/core/jobs.py`
- UI templates: `templates/` and `templates/partials/pages/`
- Static assets: `static/`

## Documentation Map

- Main docs index: [docs/README.md](/home/administrator/lab-builder/docs/README.md)
- Full operator + maintainer guide: [docs/HOWTO.md](/home/administrator/lab-builder/docs/HOWTO.md)
- Health check: `./scripts/health-check`
- Existing operations references:
  - [docs/automation-principles.md](/home/administrator/lab-builder/docs/automation-principles.md)
  - [docs/esxi-operations.md](/home/administrator/lab-builder/docs/esxi-operations.md)
  - [docs/debugging.md](/home/administrator/lab-builder/docs/debugging.md)
