# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- `STOP_HARDWARE_WORK` is present and the finalized report is `Needs attention`.
- `MORNING_READY.md` reports tests and compile passed, push not run, and required iLO/Cisco artifacts still pending.
- The run started at 2026-05-27 17:57 local time but was treated as inside the same-day 05:30 hardware stop and past the same-day 06:00 finalization deadline. That misclassifies evening starts as missed morning finalizations.
- Repair target: anchor hardware-stop and finalization-deadline decisions to the run folder timestamp so evening runs use the next morning's cutoff while pre-06:00 runs still use the current morning.
