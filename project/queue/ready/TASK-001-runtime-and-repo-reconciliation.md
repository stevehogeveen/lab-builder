# TASK-001 - Runtime and Repo Reconciliation

Status: Completed with follow-up queued

Repository: `stevehogeveen/lab-builder`

Routing source: COO routing from completed TASK-000, `project/CURRENT_EPIC.md`, and `docs/13-Repository-Map.md`.

## Objective

Verify the LabBuilder runtime/test path on this Windows machine and reconcile this repository against the newer `infra-config-portal` workstream before product implementation resumes.

## Why This Is Next

TASK-000 produced the repository map and found two immediate risks:

- The local Windows shell does not currently have a usable project Python/pytest environment.
- This repository appears materially different from the newer `infra-config-portal` app workstream.

Both must be understood before safe product changes continue.

## Scope

1. Create or validate a local Windows Python virtual environment without changing product source code.
2. Install repository dependencies from `requirements.txt`.
3. Run `python -m pytest --collect-only -q`.
4. Run the full pytest suite if collection passes.
5. Check Docker build/run/health without touching hardware workflows.
6. Compare this repository to the newer `infra-config-portal` workstream and document which codebase should be authoritative for future LabBuilder work.
7. Produce `project/reports/2026-07-09-runtime-and-repo-reconciliation.md`.

## Non-Goals

- Do not implement product features.
- Do not redesign Project OS.
- Do not run hardware, RAID, factory reset, rebuild, storage apply, switch reset, or NetApp destructive workflows.
- Do not merge repositories without an explicit decision.
- Do not make product-direction decisions that belong to Steve or architecture decisions that belong to CTO.

## Acceptance Criteria

- Windows runtime status is documented.
- Test collection result is documented.
- Full pytest result is documented, or the exact blocker is recorded.
- Docker health status is documented, or the exact blocker is recorded.
- Repo reconciliation findings are documented.
- A recommended next product task is written.
- Any cross-product or product-direction decision is clearly marked for CTO/Steve instead of assumed.

## Result

Result report: `project/reports/2026-07-09-runtime-and-repo-reconciliation.md`

Follow-up task: `project/queue/ready/TASK-002-windows-runtime-compatibility.md`

## Suggested Verification Commands

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest --collect-only -q
.\.venv\Scripts\python.exe -m pytest
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

Use equivalent commands if this machine exposes Python through another path.
