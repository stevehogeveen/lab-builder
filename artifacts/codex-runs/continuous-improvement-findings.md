# Continuous Improvement Findings

- Latest overnight run inspected: `artifacts/runs/overnight/20260527-175700-ilo-cisco`.
- Required run artifacts were read: `live-job.log`, `trace.yml`, `summary.yml`, `MORNING_READY.md`, `job-state.yml`, `ilo/*`, and `cisco/*`.
- `STOP_HARDWARE_WORK` is present. iLO and Cisco evidence artifacts are pending placeholders from the discovery-only run; no hardware action was taken during this cycle.
- Current safe defaults remain intact: default mode is `discovery_only`, destructive flags are false, `guided_setup`/`full_overnight` require confirmation, and Operator Mode exposes one clear next action while Debug Mode keeps raw detail.
- The latest durable report correctly shows finalization before the `2026-05-28 06:00 local` deadline and a clean secret scan.
- Real issue found: `MORNING_READY.md`, `summary.yml`, `job-state.yml`, and the `/overnight-hardware` API can retain the generic `Git commit or push did not complete.` reason after the stale evening-deadline skip has been reconciled away. This is misleading when no real git failure note remains; the report already exposes `Push: not run`.
- Planned repair: only add/retain the generic git reason for actual commit/push attempts or surviving git failure notes, and add regression coverage for the reconciled stale-deadline snapshot.
