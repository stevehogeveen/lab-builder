# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required files were read: `live-job.log`, `trace.yml`, `summary.yml`, `MORNING_READY.md`, `ilo/*`, and `cisco/*`. No `job-state.yml` exists in the old run folder; persisted app job state is in `artifacts/jobs/Kit-01_job.yml`.
- `STOP_HARDWARE_WORK` is present with a finalization-scheduler request. The required iLO and Cisco evidence files are still explicit pending placeholders from the old run.
- The on-disk legacy `MORNING_READY.md` and `summary.yml` still contain the stale deadline-missed reason, but current `/api/ui/overnight-hardware` reconciles it correctly: completion `2026-05-27 20:35 local`, deadline `2026-05-28 06:00 local`, status `before_deadline`.
- Safe defaults are intact in the UI API: default mode is `discovery_only`, and all destructive flags are false.
- Operator Mode now reconciles the stale persisted job to `Needs attention`, `Finalization complete`, 100% completion, and one clear next action.
- New issue found: the `/overnight-hardware/start` POST path can queue a new overnight hardware run while the persisted overnight job is genuinely still `Running` or `Overnight queued`. The display path reconciles stale finalized jobs, but the start path does not use that safety gate before creating a new run.
- Planned repair: block `/overnight-hardware/start` when another overnight hardware run is still active after reconciliation, while still allowing a stale finalized job to be reviewed or superseded safely.
