# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required files were read: `live-job.log`, `trace.yml`, `summary.yml`, `MORNING_READY.md`, `ilo/*`, and `cisco/*`. No `job-state.yml` exists in the old run folder; persisted app job state is in `artifacts/jobs/Kit-01_job.yml`.
- `STOP_HARDWARE_WORK` is present with a finalization-scheduler request. The required iLO and Cisco evidence files are still explicit pending placeholders from the old run.
- The on-disk legacy `MORNING_READY.md` and `summary.yml` still contain the stale deadline-missed reason, but current `/api/ui/overnight-hardware` reconciles it correctly: completion `2026-05-27 20:35 local`, deadline `2026-05-28 06:00 local`, status `before_deadline`.
- Safe defaults are intact in the UI API: default mode is `discovery_only`, and all destructive flags are false.
- Operator Mode now reconciles the stale persisted job to `Needs attention`, `Finalization complete`, 100% completion, and one clear next action. The start path now blocks a genuinely active overnight job after reconciliation.
- New issue found: the standalone `app.overnight_finalize` CLI can complete `MORNING_READY.md` and `summary.yml` without syncing the persisted app job. That leaves generic app state and `/api/ui/job-status` stuck at `Running` even after the overnight folder is finalized, and the old run has no `job-state.yml` snapshot.
- Planned repair: after CLI finalization, update the matching overnight job file to a finalized display state and write a bounded `job-state.yml` snapshot, without touching hardware or storing raw secret excerpts.
