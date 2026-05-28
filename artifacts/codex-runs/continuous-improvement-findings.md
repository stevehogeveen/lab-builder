# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required artifacts were read with secret-bearing fields redacted from inspection output; `job-state.yml` was not present in the run folder.
- `STOP_HARDWARE_WORK` is present and `MORNING_READY.md`/`summary.yml` report `Needs attention`, with iLO/Cisco evidence artifacts still pending.
- The current code already contains the prior repair that anchors 05:30/06:00 cutoffs to the run folder timestamp, and safe defaults remain in place (`discovery_only`, destructive flags false).
- App state still reports the old job as `Running` from `artifacts/jobs/Kit-01_job.yml`, while the finalized run artifacts say `Needs attention`.
- `/api/ui/overnight-hardware` therefore tells Operator Mode to wait for finalization even though finalization has already finished and the real next issue is pending hardware evidence.
- Repair target: make Operator Mode reconcile stale in-progress job state from the finalized latest run and provide one next action specific to pending required artifacts.
