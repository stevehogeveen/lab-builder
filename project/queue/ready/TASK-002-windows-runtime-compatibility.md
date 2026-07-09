# TASK-002 - Windows Runtime Compatibility

Status: Completed

Repository: `stevehogeveen/lab-builder`

Routing source: `project/reports/2026-07-09-runtime-and-repo-reconciliation.md`

## Objective

Make the original LabBuilder repository installable and test-collectable on Windows without weakening safety gates or touching hardware execution paths.

## Why This Is Next

TASK-001 found that:

- `pip install -r requirements.txt` fails on Windows because `uvloop` is unconditional.
- Full pytest collection fails because `app/cisco.py` imports POSIX-only `grp` and `pwd` at module import time.
- A subset of non-Cisco tests passes on Windows once `uvloop` is skipped, proving the repo is close enough to preserve as a useful migration source.

## Scope

1. Make `uvloop` conditional for platforms that support it.
2. Make Cisco POSIX-only imports lazy, optional, or guarded with Windows-safe fallbacks.
3. Preserve Cisco diagnostics and permission guidance on Linux.
4. Ensure Windows imports fail closed with clear "unsupported on this platform" messages for serial permission operations that truly require POSIX behavior.
5. Add or update tests for Windows import/collection behavior.
6. Update docs with a Windows setup/test command path.

## Non-Goals

- Do not execute Cisco serial writes.
- Do not apply switch config.
- Do not run RAID/storage/NetApp/factory reset/rebuild actions.
- Do not merge this repository with `infra-config-portal`.
- Do not decide the authoritative LabBuilder implementation.

## Acceptance Criteria

- `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` succeeds on Windows.
- `.\.venv\Scripts\python.exe -m pytest --collect-only -q` succeeds on Windows.
- The Windows-compatible subset continues to pass.
- Any Linux-only Cisco permission functions still report useful diagnostics on Linux.
- Safety confirmation strings and guarded destructive workflows remain unchanged.

## Result

Result report: `project/reports/2026-07-09-windows-runtime-compatibility.md`

Follow-up task: `project/queue/ready/TASK-003-legacy-test-suite-triage.md`

## Verification Commands

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest --collect-only -q
.\.venv\Scripts\python.exe -m pytest tests/test_ilo_upgrade.py tests/test_netapp_upgrade.py tests/test_ontap_api_catalog.py tests/test_upgrade_helper.py tests/test_windows_deploy.py -q
```
