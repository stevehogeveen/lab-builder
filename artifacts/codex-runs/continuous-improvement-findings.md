# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required run artifacts were read: `live-job.log`, `trace.yml`, `summary.yml`, `MORNING_READY.md`, `job-state.yml`, `ilo/*`, and `cisco/*`.
- `STOP_HARDWARE_WORK` is present. iLO and Cisco evidence artifacts are pending placeholders from the discovery-only run; no hardware action was taken during this cycle.
- Current job state records finalization completed at `2026-05-27 20:35 local`, deadline `2026-05-28 06:00 local`, and timing `before deadline`.
- Real issue found: durable `MORNING_READY.md` and `summary.yml` still report the stale missed-deadline reason and stale deadline note, even though the synced job state and UI/operator state correctly identify the run as before deadline.
- Planned repair: make the finalizer sync reconcile durable `summary.yml` and `MORNING_READY.md`, then add a regression test for stale report repair.
