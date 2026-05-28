# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required files were read: `live-job.log`, `trace.yml`, `summary.yml`, `MORNING_READY.md`, `ilo/*`, and `cisco/*`. No `job-state.yml` exists in the run folder; persisted app job state is in `artifacts/jobs/Kit-01_job.yml`.
- `STOP_HARDWARE_WORK` is present. The required iLO and Cisco evidence files are still explicit pending placeholders from the old run.
- The on-disk legacy `MORNING_READY.md` and `summary.yml` still contain the stale deadline-missed reason, but current `/api/ui/overnight-hardware` reconciles it correctly: completion `2026-05-27 20:35 local`, deadline `2026-05-28 06:00 local`, status `before_deadline`.
- Safe defaults are intact in the UI API: default mode is `discovery_only`, and all destructive flags are false.
- New issue found: Operator Mode reconciles the stale persisted job status to `Needs attention` and `Finalization complete`, but it still shows stale in-progress details: 72% completion and the last action as finalization running. This is misleading after the latest run finalized.
- Planned repair: keep Operator Mode compact, but when a stale in-progress job is reconciled to a finalized run, show 100% completion and a completed finalization last-action result.
