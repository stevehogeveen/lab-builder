# Legacy Test Suite Triage: 2026-07-09

## Summary

Ran the legacy Lab Builder test suite on Windows after Project OS Executive Router routed Codex to `TASK-003-legacy-test-suite-triage`.

The referenced product queue file was not present in the cloned `main` branch or remote branches, so the task was completed from the safe implied scope: non-destructive local test-suite triage.

## Environment

- Repository: `stevehogeveen/lab-builder`
- Branch: `main`
- OS: Windows
- Python: local `.venv` created from bundled Codex Python 3.12.13

## Setup Findings

System `python` was not available on PATH.

Installing `requirements.txt` failed on Windows because `uvloop==0.22.1` does not support Windows.

To continue triage, the same requirements were installed into `.venv` with `uvloop` excluded.

## Fix Applied

`app/cisco.py` imported Unix-only `grp` and `pwd` at module import time, which prevented pytest collection on Windows.

The Cisco helpers now import `grp` and `pwd` optionally and return a clear Unix-only result for serial permission repair on Windows.

## Verification

Compile:

```powershell
.\.venv\Scripts\python.exe -m compileall -q app tests
```

Result: passed.

Cisco focused tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cisco_config_rendering.py tests/test_cisco_console_feedback.py tests/test_cisco_module.py tests/test_cisco_serial.py tests/test_cisco_upgrade.py -q
```

Result: `34 passed`.

Initial full suite after dependency workaround and Cisco import fix:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result before the final Cisco monkeypatch compatibility fix: `387 passed, 13 failed, 1 warning`.

After the Cisco monkeypatch compatibility fix, full-suite quiet runs exceeded the command timeout. A failure-oriented run was used:

```powershell
.\.venv\Scripts\python.exe -m pytest -vv --maxfail=1
```

Result: `83 passed, 1 failed, 1 warning`.

First remaining failure:

```text
tests/test_app.py::test_prepare_execute_shows_blocked_stage_guidance
FileNotFoundError: No ESXi 7 base ISO was found under media/esxi/base
```

## Remaining Failure Classes

The first remaining blocker is ESXi execution-review behavior when no local ESXi base ISO exists.

Expected behavior for several legacy tests appears to be a rendered blocked/review state, not an unhandled `FileNotFoundError`.

Earlier full-suite output also showed failures in:

- ESXi prepare/execute review expectations.
- Windows path separator assertions for ESXi trace paths.
- NetApp module copy expectations.

## Next Safe Task

Fix ESXi execution-review handling so missing local base ISO media is represented as a blocked review state instead of an unhandled request exception.

This should be done before chasing path-separator and NetApp copy assertions, because the missing-media exception blocks several prepare-execute scenarios.

## Boundaries Observed

- No live provider actions were run.
- No storage apply, RAID, factory reset, Cisco writes, NetApp writes, or ESXi live operations were run.
- No product direction decisions were made.
- No secrets were committed.
