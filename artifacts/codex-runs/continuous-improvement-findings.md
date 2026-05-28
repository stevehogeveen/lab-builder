# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required run artifacts were read: `live-job.log`, `trace.yml`, `summary.yml`, `MORNING_READY.md`, `job-state.yml`, `ilo/*`, and `cisco/*`.
- `STOP_HARDWARE_WORK` is present. The run finalized before the `2026-05-28 06:00 local` deadline with tests, compile, and secret scan clean.
- iLO and Cisco evidence artifacts remain pending placeholders from the earlier discovery-only run; no hardware action was taken during this cycle.
- Safe defaults remain intact: default mode is `discovery_only`, destructive flags are false, and `guided_setup`/`full_overnight` require confirmation.
- Real issue found: `/overnight-hardware` Operator Mode exposes the full pending-artifact list as its first Needs Attention message. That makes Operator Mode noisy and duplicates raw Debug Mode detail.
- Planned repair: keep raw artifact health in Debug Mode, but summarize pending-artifact reasons in Operator Mode with a compact count and one clear next action.
