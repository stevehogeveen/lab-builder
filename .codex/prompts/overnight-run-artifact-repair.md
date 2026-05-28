You are repairing Lab Builder based on real overnight run artifacts.

Goal:
Inspect actual run outputs under artifacts/runs/overnight and artifacts/codex-runs, find anything wrong, confusing, incomplete, unsafe, or inconsistent, and fix the app so the next overnight run is better.

This is a repair pass, not a rewrite.

Do not touch real hardware.
Do not connect to iLO.
Do not open Cisco console.
Do not power cycle anything.
Do not factory reset anything.
Do not install ESXi.
Do not wipe storage.
Do not make broad unrelated rewrites.
Do not delete useful diagnostics.
Do not commit secrets.

Before editing:
1. Run git status -sb.
2. Inspect recent commits.
3. Inspect artifacts/runs/overnight.
4. Inspect the newest overnight run folders first.
5. Inspect artifacts/codex-runs summaries related to overnight automation.
6. Read live-job.log, trace.yml, summary.yml, MORNING_READY.md, and any ilo/ or cisco/ artifacts that exist.
7. Identify:
   - failed stages
   - warnings
   - missing expected files
   - confusing messages
   - contradictory Operator Mode state
   - Debug Mode gaps
   - finalization issues
   - test gaps
   - secret-scan false positives or misses
   - commit/push issues
   - routes or buttons that do not line up with backend behavior
   - discovery steps that silently skip work without explaining why
8. Write findings to artifacts/codex-runs/overnight-run-artifact-repair-findings.md before changing code.

Repair priorities:
1. Safety problems first.
2. Broken finalization/commit/push behavior second.
3. Missing or misleading morning report details third.
4. Missing artifacts/logs/traces fourth.
5. UI clarity fifth.
6. Tests last, but every repaired behavior should get test coverage.

Expected behavior:
- Overnight run should clearly show whether it is safe, running, stopped, finalized, ready, or needs attention.
- discovery_only must remain the safe default.
- destructive flags must remain false by default.
- guided_setup and full_overnight must require clear confirmation.
- hardware stop marker must be respected.
- finalization must create MORNING_READY.md even if tests fail.
- finalization must not commit/push if possible secrets are found.
- finalization must record:
  - run folder
  - branch
  - commit SHA if committed
  - test result
  - compile result
  - push result
  - secret scan result
  - git status before and after
  - exact reason for Needs attention
- Operator Mode should stay compact.
- Debug Mode should contain raw paths, logs, traces, route/API output, console transcripts, and artifact details.

Artifact expectations:
If an expected artifact is missing, do not fake it.
Instead:
- make the app report it clearly
- improve the writer/finalizer so the next run creates it
- add a test if reasonable

Cisco expectations:
If Cisco artifacts show no console port, busy console port, timeout, bad baud, no prompt, or command output not captured:
- improve the message
- improve next-step guidance
- preserve raw transcript
- do not attempt real connection during this repair pass

iLO expectations:
If iLO artifacts show auth failure, TLS/certificate issue, timeout, unreachable host, unexpected JSON, missing Redfish path, or permission problem:
- improve the message
- improve next-step guidance
- preserve raw response/error
- do not attempt real connection during this repair pass

UI expectations:
On /overnight-hardware:
- show the latest run status
- show last run folder
- show MORNING_READY.md status if present
- show what failed or needs attention
- show one clear next action
- keep raw paths and logs in Debug Mode
- avoid duplicate/conflicting cards

Tests:
Add/update tests for any repaired behavior.
At minimum, if not already covered, add tests for:
- missing artifact reported clearly
- MORNING_READY.md Needs attention reason
- stop marker prevents further hardware work
- secret scan blocks commit
- latest run status appears in UI/API state
- discovery_only remains default

After editing:
1. Run focused tests.
2. Run ~/lab-builder/.venv/bin/python -m pytest -q tests/test_overnight_run.py.
3. Run ~/lab-builder/.venv/bin/python -m pytest -q.
4. Run ~/lab-builder/.venv/bin/python -m compileall app.
5. Run git diff --check.
6. Run git status -sb.
7. Write final summary to artifacts/codex-runs/overnight-run-artifact-repair-summary.md.

Commit/push rules:
- Commit and push only if tests pass, compile passes, git diff --check passes, and secret scan is clean.
- Commit message:
  Repair overnight run artifact handling
- If anything fails, do not commit. Leave clear notes in the summary file explaining exactly what failed and what to do next.
