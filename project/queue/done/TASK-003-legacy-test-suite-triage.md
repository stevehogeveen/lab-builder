# TASK-003 - Legacy Test Suite Triage

Status: Completed

Repository: `stevehogeveen/lab-builder`

Routing source: `project/reports/2026-07-09-windows-runtime-compatibility.md`

## Objective

Triage the remaining legacy test-suite issues after Windows runtime compatibility work so the repository can be trusted as a migration source.

## Why This Is Next

TASK-002 made requirements install and pytest collection work on Windows. Focused lanes pass, but two NetApp module tests fail and the full suite did not complete within a 5-minute guard.

## Scope

1. Investigate `tests/test_netapp_module.py` failures for missing `Connection Target` and `API connection test` text.
2. Determine whether the failures are stale tests, stale templates, or a real regression.
3. Profile or split the full 402-test suite into predictable Windows lanes.
4. Document recommended fast/slow test commands.
5. Make only safe non-destructive test/docs/product fixes.

## Non-Goals

- Do not execute live NetApp actions.
- Do not run storage apply, RAID, factory reset, rebuild, Cisco writes, or provider writes.
- Do not decide the authoritative LabBuilder implementation.
- Do not merge this repository with `infra-config-portal`.

## Acceptance Criteria

- NetApp module failures are explained and fixed or marked with a specific follow-up.
- Windows test lanes are documented.
- At least one broad Windows test lane runs to completion.
- Safety gates remain unchanged.

## Result

Result report: `project/reports/2026-07-09-legacy-test-suite-triage.md`

No live provider or destructive workflow was executed.
