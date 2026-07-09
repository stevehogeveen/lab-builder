# 13 - Repository Map

Date: 2026-07-09

Repository: `stevehogeveen/lab-builder`

Local path: `C:\Users\TLANADMIN\Documents\Codex\2026-06-22\have-we\work\lab-builder`

Project OS role: LabBuilder / Product Team Beta

Mission: `project/queue/ready/TASK-000-repository-discovery.md`

Version discovered: `0.1.0`

## Summary

LabBuilder is a Python FastAPI application for controlled lab provisioning. It manages per-kit configuration, device setup pages, staged execution workflows, generated artifacts, hardware discovery, upgrade helpers, and release packaging.

This repository appears to be the original LabBuilder product codebase. It is materially different from the newer `infra-config-portal` app workstream discussed in Product Team Beta collaboration, so a reconciliation task should happen before major implementation work.

## Technology Stack

- Python application runtime.
- FastAPI app served by Uvicorn.
- Jinja2 templates for server-rendered UI.
- Static browser assets including HTMX, Alpine, Gridstack, dashboard JavaScript, and Tailwind CSS output.
- Pydantic models and PyYAML configuration parsing.
- SQLite runtime persistence under `artifacts/lab-builder.sqlite3`.
- Requests/httpx for HTTP integrations.
- Paramiko, pyserial, sshpass, and openssh-client for network and console automation.
- pyVmomi for VMware/vSphere workflows.
- pywinrm and requests-ntlm for Windows workflows.
- pytest and Playwright listed for testing.
- Docker and Docker Compose packaging.
- xorriso and ESXi kickstart generation for installer media workflows.

Primary dependency files:

- `requirements.txt`
- `requirements-runtime.txt`
- `Dockerfile`
- `docker-compose.yml`
- `docker-compose.build.yml`

## Folder Structure

- `app/`: main FastAPI application package. Includes legacy application logic, hardware helpers, module registry, stage registry, ESXi build helpers, upgrade helpers, and integration code.
- `app/main.py`: central application file. It defines the FastAPI app, runtime directories, SQLite runtime, legacy routes, page handlers, job execution, and safety confirmation constants.
- `app/core/`: shared product core for config, database, errors, jobs, models, policies, registry, secrets, and stage registry.
- `app/modules/`: manifest-driven product modules. Discovered modules include Cisco, ESXi config, ESXi install, iLO, NetApp, OVF templates, QNAP, Storage, and Windows.
- `app/stages/`: stage plugin/runtime implementations for ESXi, iLO, NetApp, Storage, and Windows.
- `app/esxi/`: ESXi boot config, builder, kickstart, and models.
- `app/api_catalog/`: ONTAP API catalog support.
- `templates/`: Jinja templates, including the main shell, sidebar, setup strip, upgrade gate panels, and section pages.
- `static/`: CSS and JavaScript assets used by the server-rendered UI.
- `docs/`: operator documentation, module documentation, section documentation, product principles, debugging, HOWTO, and API catalog notes.
- `tests/`: pytest suite for app behavior, Cisco, iLO upgrade, NetApp, ONTAP API catalog, upgrade helper, and Windows deployment coverage.
- `scripts/`: local run, health check, release, Docker image, backup/restore, debug collection, ESXi ISO build, and Windows OVF deployment helpers.
- `config/`: runtime product configuration, including kit configuration.
- `artifacts/`: generated/runtime artifacts, reports, jobs, history, SQLite runtime, and exported outputs.
- `api_catalog/`: repository-level ONTAP API catalog assets.

## Entry Points

Application entry points:

- `app/main.py`: defines `app = FastAPI(title=APP_NAME)`.
- `Dockerfile`: starts `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- `docker-compose.yml`: exposes `${LAB_BUILDER_PORT:-8000}:8000` and mounts `config/`, `artifacts/`, and `media/`.
- `scripts/start-app-dev`: development start helper.
- `scripts/start-app`: non-development start helper.
- `scripts/run_lab_builder.sh`: Docker-oriented run helper.
- `scripts/health-check`: health check helper.
- `/health`: FastAPI health endpoint.

Operational entry points:

- `scripts/build_release.sh`
- `scripts/export_docker_image.sh`
- `scripts/load_docker_image.sh`
- `scripts/backup_data.sh`
- `scripts/restore_data.sh`
- `scripts/collect-debug`
- `scripts/build_esxi_iso.py`
- `scripts/deploy_windows_ovf_to_esxi.py`
- `scripts/ontap-api-catalog`

UI entry points:

- `/`
- `/dashboard`
- `/execution`
- `/global-settings`
- `/upgrade-helper`
- `/ilo`
- `/esxi`
- `/windows`
- `/qnap`
- `/configuration`
- `/configs`
- `/storage`
- `/kits`
- `/history`
- Module routes under `/modules/*`

Discovery found 66 direct route decorators in `app/main.py`, plus manifest-loaded module routes from `app/modules/*/routes.py`.

## Current Features

- Kit-oriented configuration under `config/kits/`.
- Dashboard and sectioned setup pages.
- Global settings and IP plan autofill.
- iLO configuration, validation, inventory export, storage interactions, virtual media, and upgrade planning/running.
- ESXi installer ISO/kickstart build and virtual media boot workflows.
- ESXi post-install configuration module.
- Windows image upload, OVF template selection, vSphere probing, WinRM probing, and deployment helper logic.
- QNAP setup and validation workflow.
- Cisco switch setup, validation, console handling, upgrade support, and guarded factory reset flow.
- Storage discovery, target selection, RAID planning, approval, apply, reboot, artifact viewing, and stale-plan checks.
- NetApp ONTAP discovery, configuration, bootstrap checks, NFS/iSCSI planning, VMware NFS probing, API readiness, upgrade planning/running, and plan export.
- OVF template registration for local OVA/OVF directories.
- Execution center for preview and real staged runs.
- Background job tracking and WebSocket job status.
- Run history, reports, debug bundles, and redacted artifact exports.
- Manifest-driven module registry and module navigation.
- Stage registry for staged execution.
- Docker release packaging, image export/load, backup/restore, and health checks.

## Tests

Test files discovered:

- `tests/test_app.py`
- `tests/test_cisco_config_rendering.py`
- `tests/test_cisco_console_feedback.py`
- `tests/test_cisco_module.py`
- `tests/test_cisco_serial.py`
- `tests/test_cisco_upgrade.py`
- `tests/test_ilo_upgrade.py`
- `tests/test_netapp_module.py`
- `tests/test_netapp_upgrade.py`
- `tests/test_ontap_api_catalog.py`
- `tests/test_upgrade_helper.py`
- `tests/test_windows_deploy.py`

Primary test command:

```powershell
python -m pytest
```

Collection check attempted on this Windows machine:

- `python -m pytest --collect-only -q` failed because `python` resolves to the Microsoft Store app execution alias, not a real Python installation.
- The bundled Codex Python runtime is available at `C:\Users\TLANADMIN\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`, but it does not have `pytest` installed.

Recommended local verification setup:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
```

If running on Ubuntu/Linux as documented:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
.venv/bin/python -m pytest
```

## Run Commands

Linux development run:

```bash
sudo apt install -y python3 python3-venv xorriso sshpass
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
./scripts/start-app-dev
```

Linux non-development run:

```bash
./scripts/start-app
```

Docker run:

```bash
./scripts/run_lab_builder.sh
```

Docker Compose run:

```bash
docker compose up
```

Default URL:

```text
http://localhost:8000
```

Health check:

```bash
./scripts/health-check
```

## Build Commands

Docker image build through Compose:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

Release package build:

```bash
./scripts/build_release.sh
```

Docker image export:

```bash
./scripts/export_docker_image.sh
```

Release audit:

```bash
./scripts/audit_release.sh
```

ESXi ISO build helper:

```bash
python3 scripts/build_esxi_iso.py
```

## Gaps

- No Product OS structure existed before this onboarding task.
- Windows-first setup is not documented as a first-class path. Current scripts are mostly Bash/Linux oriented.
- Test collection cannot currently run from this Windows shell until Python and dependencies are installed in a real venv.
- The product boundary between this repository and the newer `infra-config-portal` workstream needs a written reconciliation before implementation work resumes.
- `app/main.py` is very large and still owns many legacy routes and workflows.
- Runtime folders such as `config/`, `artifacts/`, and `media/` are mounted and mutable; future work should be careful to avoid committing environment-specific state.
- Hardware workflows have real-world side effects and must keep explicit safety gates.
- Some UI and workflow documentation may lag behind the active design direction from the newer app workstream.

## Technical Debt

- `app/main.py` is a high-risk monolith with route handlers, runtime setup, persistence, hardware logic, and orchestration in one file.
- The repo mixes older direct routes with newer manifest-driven modules.
- `tests/test_app.py` is very large and likely difficult to navigate during focused maintenance.
- Static vendor assets are checked in directly.
- Scripts are Linux-centric, which conflicts with the desired Windows-machine operator workflow.
- Runtime/generated data sits near source folders, which increases drift and cleanup risk.
- Hardware integration tests appear mostly mocked/unit-level; a formal read-only live verification lane is not obvious from the repository structure.

## Recommended Next Task

Create `TASK-001-runtime-and-repo-reconciliation.md`.

Recommended scope:

1. Install a local Windows venv for this repository without changing product code.
2. Run `python -m pytest --collect-only -q` and then the full pytest suite.
3. Confirm Docker build/run health.
4. Compare this `stevehogeveen/lab-builder` repository against the newer `infra-config-portal` workstream and decide which codebase is authoritative for Product Team Beta implementation.
5. Produce a written migration/reconciliation recommendation before product changes begin.

## Project OS Promotion Candidates

- The minimal `project/` onboarding scaffold should become a reusable Project OS product template.
- The repository map checklist used in this task should become a reusable Project OS discovery template.
- Hardware-lab safety gate taxonomy may be reusable across products, but LabBuilder-specific device details should stay in this repository.
