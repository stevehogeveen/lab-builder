# Runtime and Repo Reconciliation - 2026-07-09

Task: `project/queue/ready/TASK-001-runtime-and-repo-reconciliation.md`

Repository: `stevehogeveen/lab-builder`

Local path: `C:\Users\TLANADMIN\Documents\Codex\2026-06-22\have-we\work\lab-builder`

## Summary

LabBuilder can be partially exercised on Windows, but the official dependency and full test path are currently blocked by Linux/POSIX assumptions.

The newer `infra-config-portal` workstream is structurally more aligned with the current Product Team Beta direction: modern React/Vite UI, FastAPI backend, safety gates, provider modes, profile handling, and active design/testing artifacts. This `lab-builder` repository remains valuable as the original hardware workflow knowledge base and migration source.

No product behavior changes, hardware actions, RAID, factory reset, rebuild, storage apply, switch reset, or NetApp destructive workflows were run.

## Runtime Verification

Created a local venv using the bundled Codex Python runtime:

```powershell
C:\Users\TLANADMIN\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m venv .venv
.\.venv\Scripts\python.exe --version
```

Result:

```text
Python 3.12.13
```

Official dependency install failed:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Failure:

```text
RuntimeError: uvloop does not support Windows at the moment
```

Non-repo workaround attempted to isolate the blocker:

```powershell
$reqs = Get-Content requirements.txt | Where-Object { $_.Trim() -and $_ -notmatch '^uvloop==' }
.\.venv\Scripts\python.exe -m pip install @reqs
```

Result: dependencies installed successfully with only `uvloop` omitted.

## Test Verification

Full collection command after the workaround:

```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q
```

Result:

- 30 tests collected before interruption.
- 7 collection errors.
- Blocking error class: `ModuleNotFoundError: No module named 'grp'`.
- Root cause: `app/cisco.py` imports POSIX-only modules `grp` and `pwd` at import time.

Affected test collection paths included:

- `tests/test_app.py`
- `tests/test_cisco_config_rendering.py`
- `tests/test_cisco_console_feedback.py`
- `tests/test_cisco_module.py`
- `tests/test_cisco_serial.py`
- `tests/test_cisco_upgrade.py`
- `tests/test_netapp_module.py`

Windows-compatible subset run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ilo_upgrade.py tests/test_netapp_upgrade.py tests/test_ontap_api_catalog.py tests/test_upgrade_helper.py tests/test_windows_deploy.py -q
```

Result:

```text
30 passed in 1.46s
```

## Docker Verification

Docker is not available on PATH:

```text
docker : The term 'docker' is not recognized as the name of a cmdlet, function, script file, or operable program.
```

Docker build/run/health could not be verified on this machine during this shift.

## Repo Reconciliation Findings

### LabBuilder

- Stack: Python FastAPI, Jinja templates, static HTMX/Alpine/Gridstack assets, SQLite runtime, Docker packaging.
- Shape: original hardware-oriented app with a very large `app/main.py`.
- Inventory:
  - Python files in app/tests/scripts: 103
  - Test files: 12
  - `app/main.py` size: 767,474 bytes
  - Template files: 24
- Strengths:
  - Deep hardware workflow knowledge for iLO, ESXi, Cisco, NetApp, storage, Windows, upgrade media, reports, and debug bundles.
  - Existing safety confirmation constants and guarded hardware flows.
  - Broad mocked/unit coverage for legacy workflows.
- Risks:
  - Official requirements are not Windows-installable because `uvloop` is unconditional.
  - Cisco import path assumes POSIX modules (`grp`, `pwd`) and blocks full test collection on Windows.
  - Bash/Linux scripts dominate the run path.
  - Monolithic `app/main.py` raises change risk.

### infra-config-portal

Path inspected: `C:\Users\TLANADMIN\Documents\Codex\2026-06-22\have-we\work\infra-config-portal\app`

Branch inspected: `zones-map-home`

Latest commit inspected: `751bb00 docs: record picker polish heartbeat`

- Stack: FastAPI backend, SQLAlchemy/Alembic, React/Vite/TypeScript frontend, Playwright E2E, PowerShell helpers, Makefile.
- Inventory:
  - Backend Python files: 185
  - Backend test files: 55
  - Frontend TypeScript/TSX source files: 15
  - E2E test files: 1
- Strengths:
  - More aligned with the current control-plane direction.
  - Has active profile/subnet, safety, provider-mode, audit, and visual builder work.
  - Has Windows-oriented helper scripts such as `scripts/check-windows.ps1`, `scripts/fast-verify.ps1`, `scripts/start-backend.ps1`, and `scripts/start-frontend.ps1`.
  - Claude review packet records recent design/test milestones and fast-verify evidence.
- Risks:
  - It is a separate workstream and not the `stevehogeveen/lab-builder` repository requested for onboarding.
  - Treating it as authoritative is a product-direction decision and should not be silently assumed from this product-local shift.

## Recommendation

Product-local recommendation:

1. Treat `infra-config-portal/app` as the active implementation candidate for the new LabBuilder control-plane/UI direction.
2. Treat `stevehogeveen/lab-builder` as the legacy hardware workflow source of truth to mine and migrate from.
3. Do not merge or replace either codebase until Steve/CTO confirms the authoritative product direction.
4. First fix the old repo's Windows runtime blockers so it remains testable as a migration source.

## Recommended Next Product Task

Create and execute `TASK-002-windows-runtime-compatibility.md`.

Suggested scope:

1. Make `uvloop` conditional for non-Windows platforms.
2. Make Cisco POSIX-only imports lazy or guarded so Windows can import the app and collect tests.
3. Add a Windows collection test command to docs.
4. Re-run full test collection and the full pytest suite.
5. Keep hardware/destructive flows gated and untouched.

## Escalation Notes

CTO/Steve is not needed for the Windows compatibility task.

CTO/Steve will be needed before declaring `infra-config-portal` the authoritative LabBuilder implementation, because that is a product-direction/cross-repository decision.
