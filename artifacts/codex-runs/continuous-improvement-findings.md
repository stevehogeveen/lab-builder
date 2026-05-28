# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required run artifacts were read: `live-job.log`, `trace.yml`, `summary.yml`, `MORNING_READY.md`, `job-state.yml`, `ilo/*`, and `cisco/*`.
- `STOP_HARDWARE_WORK` is present. The iLO and Cisco evidence artifacts are explicit pending placeholders from the discovery-only run; no hardware action was taken during this cycle.
- Safe defaults remain intact in code and current API state: default mode is `discovery_only`, destructive flags default to false, and guided/full modes require explicit confirmation.
- Current `/api/ui/overnight-hardware` Operator Mode is compact: status `Needs attention`, completion `100`, one next action to start a new `discovery_only` run before the stop window, and no stale deadline-missed reason.
- Debug Mode retains raw artifact links, logs, trace, API output, console transcript placeholders, and the raw `MORNING_READY.md` excerpt.
- Real issue found: the latest durable `MORNING_READY.md`, `summary.yml`, persisted job finalization snapshot, and `job-state.yml` still carry the stale deadline-missed reason or blank finalization timing even though the run completed at `2026-05-27 20:35 local`, before the `2026-05-28 06:00 local` deadline.
- Planned repair: reconcile stale deadline reasons and fill finalization timing when syncing persisted overnight job state, then add a regression test that prevents stale CLI/job-state snapshots.
