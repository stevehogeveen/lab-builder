# Overnight Run Artifact Repair Findings

Inspected on 2026-05-27 from `/home/administrator/lab-builder-react`.

## Repo and Recent Automation Context

- `git status -sb` before code edits showed branch `experiment/react-desktop-ui` tracking origin with two untracked files:
  - `.codex/prompts/overnight-run-artifact-repair.md`
  - `artifacts/codex-runs/overnight-run-artifact-repair-summary.md`
- Recent commits added the overnight hardware runner, UI, scheduler, and finalizer:
  - `0bf21d4 Record overnight finalizer Codex prompt`
  - `9fd2041 Add overnight finalization scheduler`
  - `a9c76d2 Refine overnight automation UI and Codex run notes`
  - `a962052 Add overnight iLO and Cisco hardware run automation`
- Relevant Codex summaries reported clean tests and push for the feature/scheduler, but the real run artifacts below expose finalization/reporting gaps.

## Newest Overnight Run

Run folder: `artifacts/runs/overnight/20260527-175700-ilo-cisco`

Files present:
- `live-job.log`
- `trace.yml`
- `summary.yml`
- `MORNING_READY.md`
- `STOP_HARDWARE_WORK`
- `config-snapshot.yml`
- `ilo/*.json`
- `cisco/*.txt`

## Findings

1. Finalization did not produce a usable morning report.
   - `MORNING_READY.md` only contains `Status: pending`.
   - `trace.yml`, `summary.yml`, and `artifacts/jobs/Kit-01_job.yml` still show `Running` / `Finalization` at 72%.
   - There is no recorded branch, commit SHA, test result, compile result, push result, secret scan result, git status before/after, or exact Needs attention reason.

2. The run skipped all hardware discovery after the stop gate, but artifacts do not explain that.
   - `live-job.log` records `Finalization window is active; no additional hardware actions will start.`
   - Every iLO artifact contains only `{"status": "pending"}`.
   - Every Cisco artifact contains only `pending`.
   - The next run should preserve the safety behavior but write explicit stopped/skipped artifacts with the reason and next-step guidance.

3. `summary.yml` is overwritten by generic job-bundle state.
   - The finalizer writes a detailed summary, but `save_job` / `write_run_bundle_files` can later overwrite the same `summary.yml` with generic job fields.
   - This loses finalization detail and makes Debug Mode less useful.

4. Operator Mode is ambiguous.
   - The page state can show `Running`, `Finalization`, and `Morning: Pending` without saying whether the latest run is safe, stopped, stale, finalized, ready, or needs attention.
   - It does not prominently show the latest run folder or one clear next action.

5. Debug Mode has raw paths and excerpts, but lacks artifact health.
   - It lists artifact paths and yes/no existence, but does not call out missing artifacts or placeholder `pending` files.
   - Missing/placeholder artifacts should be reported clearly instead of silently looking complete.

6. Finalization reporting is incomplete.
   - Current report has a combined `Tests` field but no separate compile result.
   - Needs attention reasons are notes, not a stable structured field.
   - A provisional `MORNING_READY.md` should be written before long checks so interrupted finalization does not leave only the initial placeholder.

7. Cisco/iLO skip and failure guidance is too thin.
   - No-console, timeout, bad baud, busy port, no prompt, and command-output failures need clearer operator guidance while preserving raw transcript text.
   - iLO failures should include likely next steps for auth, TLS/cert, timeout/unreachable host, unexpected Redfish JSON, missing Redfish paths, and permissions.
   - This repair pass must not attempt real iLO or Cisco connections.

8. Test gaps remain around the real artifact failure modes.
   - Existing tests cover safe defaults, stop marker blocking collectors, secret scan blocking commit, mocked discovery, and basic UI/API exposure.
   - Missing coverage includes explicit missing artifact reporting, pending morning report Needs attention reason, latest run status in API/UI state, compile result reporting, and stopped/skipped artifact content.
