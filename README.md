# Lab Builder

Lab Builder is a FastAPI application for offline/controlled lab provisioning workflows. It helps operators configure kit settings, stage infrastructure actions (iLO, storage, ESXi, Windows, and optional modules), and run guarded execution with artifacts and diagnostics.

## What The App Does

- Centralizes per-kit configuration under `config/kits/`.
- Provides sectioned setup pages (Global, iLO, Storage, ESXi, Windows, extended modules).
- Executes staged workflows from Run Center with confirmations and background job tracking.
- Writes run/history/debug artifacts under `artifacts/`.

## Quick Start

Install system prerequisites first:

```bash
sudo apt update
sudo apt install -y python3 python3-venv xorriso sshpass
```

Create or refresh the local environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

```bash
./scripts/start-app-dev
```

or:

```bash
./scripts/start-app
```

Default URL: `http://localhost:8000`

## Dependency Notes

- Python packages are pinned in `requirements.txt`.
- ESXi ISO customization uses `xorriso`.
- ESXi live SSH post-config uses `sshpass` when password-based root login is used.
- Standalone ESXi OVF deployment can use `scripts/deploy_windows_ovf_to_esxi.py`; it tries VMware NFC import first, then falls back to SSH/SCP plus `vmkfstools` registration when a standalone host rejects NFC upload.
- Browser-based UI sanity checks use Playwright Chromium; install it with `.venv/bin/playwright install chromium`.
- Local media under `media/` is intentionally not tracked by git. Put ESXi ISOs, firmware, OVA/OVF, and VMDK files there on each machine that needs them, or upload firmware and upgrade media from Upgrade Helper.

## High-Level Architecture

- App entrypoint: `app/main.py`
- Module loading: `app/core/registry.py` (`manifest.yml` + `register_module_routes`)
- Stage execution framework: `app/stages/*` and `app/core/jobs.py`
- UI templates: `templates/` and `templates/partials/pages/`
- Static assets: `static/`

## Documentation Map

- Main docs index: [docs/README.md](docs/README.md)
- Full operator + maintainer guide: [docs/HOWTO.md](docs/HOWTO.md)
- Offline ONTAP REST compatibility catalog: [docs/ontap-api-catalog.md](docs/ontap-api-catalog.md)
- Health check: `./scripts/health-check`
- Existing operations references:
  - [docs/automation-principles.md](docs/automation-principles.md)
  - [docs/esxi-operations.md](docs/esxi-operations.md)
  - [docs/debugging.md](docs/debugging.md)

## Codex App Planner

This repository now includes a standalone planning helper under `codex_tracker_app/`:

- Project-tagged sessions for app-creation work
- Plain-language git change snapshots
- Codex-ready prompts, suggestions, and session chat drafts
- Usage budget tracking and session-level history

Run it from the repository root:

```bash
./makeit
```
