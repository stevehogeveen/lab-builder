# Status

Product: LabBuilder

Repository: `stevehogeveen/lab-builder`

Local path: `C:\Users\TLANADMIN\Documents\Codex\2026-06-22\have-we\work\lab-builder`

Project OS role: Product Team Beta

Current phase: Legacy test-suite triage completed

Product version discovered: `0.1.0`

Primary runtime discovered: Python FastAPI app served by Uvicorn, with Jinja templates and Docker packaging.

Verification status:

- Repository status was clean before onboarding files were added.
- Route, module, script, doc, and test inventories were inspected.
- Test collection was not completed because the Windows PATH only exposes the Microsoft Store Python alias, and the bundled Codex Python runtime does not have `pytest` installed.
- COO routing selected `TASK-001-runtime-and-repo-reconciliation.md` as the next safe task.
- A Windows venv was created with Python 3.12.13.
- Official dependency install failed because `uvloop==0.22.1` does not support Windows.
- With `uvloop` skipped as a non-repo workaround, dependencies installed and a 30-test subset passed.
- Full pytest collection is blocked by POSIX-only Cisco imports of `grp` and `pwd`.
- Docker verification is blocked because Docker is not available on PATH.
- TASK-002 resolved Windows dependency install and full collection blockers.
- Windows dependency install now succeeds by skipping `uvloop` only on Windows.
- Pytest collection now succeeds with 402 tests collected.
- Focused Cisco lane passed 36/36.
- Known Windows-compatible subset passed 30/30.
- Full pytest timed out after 5 minutes.
- NetApp module lane has 2 UI assertion failures and 29 passing tests.
- TASK-003 fixed stale NetApp UI assertions.
- NetApp module lane now passes 31/31.
- Broad Windows lane excluding `tests/test_app.py` passes 97/97.
- Full collection still succeeds with 402 tests collected.

Safety status:

- No product behavior changes have been made.
- No hardware, storage, RAID, factory reset, rebuild, or destructive workflows were executed.

Current risk:

- This product repo appears older and materially different from the newer `infra-config-portal` app workstream. Reconciliation should happen before major implementation work.
- Product-local recommendation is to keep this repo as legacy hardware workflow source while treating `infra-config-portal/app` as the active implementation candidate, pending Steve/CTO confirmation.

Next safe task:

- None selected in this product repo after TASK-003. Use COO routing for the next safe task.
