You are running one cycle of continuous overnight improvement for Lab Builder.

Goal:
Inspect the newest overnight hardware run artifacts, Codex summaries, tests, app state, and UI/backend behavior. Fix only the most important problems found. Then test, compile, secret-scan, commit, and push if safe.

This is one cycle only.
Do not loop inside Codex.
The outer shell script will run repeated cycles overnight.

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
2. Inspect latest commits.
3. Inspect newest folders under artifacts/runs/overnight.
4. Inspect latest files under artifacts/codex-runs.
5. Read live-job.log, trace.yml, summary.yml, MORNING_READY.md, job-state.yml, ilo/*, and cisco/* when present.
6. Inspect /overnight-hardware related backend and template code only if artifacts point to an issue.
7. Write concise findings to artifacts/codex-runs/continuous-improvement-findings.md.

Fix priority:
1. Safety bugs.
2. Finalization/commit/push bugs.
3. Missing or misleading MORNING_READY.md information.
4. Missing/skipped/pending artifact handling.
5. UI confusion on /overnight-hardware.
6. Test gaps for the repaired behavior.
7. Small polish only if everything above is clean.

Hard constraints:
- discovery_only remains the default.
- destructive flags remain false by default.
- guided_setup and full_overnight require explicit confirmation.
- stop marker must block new hardware work.
- secret scan must block commit/push.
- no raw suspicious secret excerpts in reports.
- Debug Mode gets raw details.
- Operator Mode stays compact with one clear next action.

If no real problem is found:
- Improve docs, tests, or reporting only if there is a clear gap.
- Otherwise write "No repair needed this cycle" to the summary and stop without commit.

After editing:
1. Run focused tests for changed behavior.
2. Run ~/lab-builder/.venv/bin/python -m pytest -q tests/test_overnight_run.py.
3. Run ~/lab-builder/.venv/bin/python -m pytest -q.
4. Run ~/lab-builder/.venv/bin/python -m compileall app.
5. Run git diff --check.
6. Run a staged secret scan before commit.
7. Write final cycle summary to artifacts/codex-runs/continuous-improvement-cycle-summary.md.

Commit/push rules:
- Commit and push only if tests pass, compile passes, diff check passes, and secret scan is clean.
- Use commit message:
  Continuous overnight artifact repair
- If anything fails, do not commit. Write exact failure and next step to the summary.
