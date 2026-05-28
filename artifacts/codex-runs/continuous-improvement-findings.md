# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required run artifacts were read with secret-bearing fields redacted from inspection output. `job-state.yml` is not present in the run folder; persisted app job state remains in `artifacts/jobs/Kit-01_job.yml`.
- `STOP_HARDWARE_WORK` is present. iLO and Cisco evidence artifacts are still explicit pending placeholders, and artifact health reports them as pending.
- The latest artifact reports were generated at `2026-05-27T20:35:11-04:00` for a run started from folder timestamp `20260527-175700`. Current cutoff logic treats that run's finalization deadline as `2026-05-28 06:00 local`, so the existing `MORNING_READY.md`/`summary.yml` deadline-missed reason is stale and misleading.
- `/api/ui/overnight-hardware` was still surfacing that stale deadline-missed reason as the first Operator Mode attention item, even though the actionable issue is pending iLO/Cisco evidence.
- Default mode remains `discovery_only`; destructive flags remain false in the UI API payload.
- Most important repair this cycle: reconcile legacy deadline-missed reasons against run timestamps for the Operator Mode/API summary, and add explicit finalization deadline/timing lines to newly generated `MORNING_READY.md` reports.
