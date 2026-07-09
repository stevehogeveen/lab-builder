# Windows Runtime Compatibility - 2026-07-09

Task: `project/queue/ready/TASK-002-windows-runtime-compatibility.md`

Repository: `stevehogeveen/lab-builder`

## Summary

TASK-002 made the original LabBuilder repository installable and test-collectable on Windows without touching hardware execution behavior or destructive gates.

The fix keeps `uvloop` installed on non-Windows platforms while skipping it on Windows, and makes Cisco serial diagnostics importable on Windows by guarding POSIX-only `grp`/`pwd` usage. Linux/POSIX serial permission repair still uses the existing `sudo`, `usermod`, and `setfacl` path. Windows fails closed with a clear unsupported message for that permission repair action.

## Changes

- `requirements.txt`: `uvloop==0.22.1` is now conditional with `sys_platform != "win32"`.
- `requirements-runtime.txt`: same conditional runtime marker.
- `app/cisco.py`: POSIX `grp` and `pwd` imports are optional. Diagnostics include `posix_permissions_supported`.
- `app/cisco.py`: `apply_serial_permission_fix` blocks with a clear POSIX-only message when POSIX permission APIs are unavailable.
- `tests/test_cisco_serial.py`: added Windows-safe diagnostics and permission-repair guard coverage.
- `README.md`: added Windows PowerShell setup and collection commands.

## Verification

Official dependency install:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Result:

```text
Ignoring uvloop: markers 'sys_platform != "win32"' don't match your environment
```

Command completed successfully.

Focused Cisco lane:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cisco_serial.py tests/test_cisco_module.py tests/test_cisco_config_rendering.py tests/test_cisco_console_feedback.py tests/test_cisco_upgrade.py -q
```

Result:

```text
36 passed in 66.78s
```

Full collection:

```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q
```

Result:

```text
402 tests collected in 1.80s
```

Known Windows-compatible subset:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ilo_upgrade.py tests/test_netapp_upgrade.py tests/test_ontap_api_catalog.py tests/test_upgrade_helper.py tests/test_windows_deploy.py -q
```

Result:

```text
30 passed in 1.66s
```

Hygiene:

```powershell
git diff --check
```

Result: passed, with only normal Windows LF/CRLF warnings.

## Residual Test Findings

Full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result: timed out after 5 minutes.

NetApp module lane:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_netapp_module.py -q
```

Result:

```text
2 failed, 29 passed
```

The two failures are UI text assertions:

- Expected `Connection Target` in `/modules/netapp`.
- Expected `API connection test` in `/modules/netapp/test-connection`.

These failures were not caused by the Windows compatibility changes, but they should be triaged before treating the legacy repo test suite as green.

## Safety Review

No hardware workflow was executed.

No RAID, storage apply, factory reset, rebuild, switch reset, NetApp destructive action, or provider write was run.

Existing destructive confirmation strings and guarded workflow paths were not changed.

## Recommended Next Safe Task

Create and execute `TASK-003-legacy-test-suite-triage.md`.

Focus:

1. Investigate the two NetApp module UI assertion failures.
2. Split or profile the 402-test suite so it can complete under a predictable Windows timeout.
3. Keep the work limited to tests/docs or clearly safe non-destructive fixes.
