Repaired and pushed.

Commit: `730599a` (`Repair overnight run artifact handling`)  
Branch: `experiment/react-desktop-ui` pushed to origin.

What changed:
- Overnight finalization now writes a provisional `MORNING_READY.md` before long checks and records explicit Needs attention reasons, compile result, artifact health, git status, secret scan, and push/test results.
- Stop-gated hardware discovery now writes clear skipped iLO/Cisco artifacts instead of leaving `pending` placeholders.
- Overnight job saves no longer overwrite runner-owned `live-job.log`, `trace.yml`, and `summary.yml`; job mirror state goes to `job-state.yml`.
- `/overnight-hardware` now shows latest run status, run folder, MORNING_READY status, top blocker, one next action, and artifact health in Debug Mode.
- Secret findings no longer write raw suspicious excerpts into reports.

Artifacts written:
- [findings](/home/administrator/lab-builder-react/artifacts/codex-runs/overnight-run-artifact-repair-findings.md)
- [summary](/home/administrator/lab-builder-react/artifacts/codex-runs/overnight-run-artifact-repair-summary.md)

Validation:
- `~/lab-builder/.venv/bin/python -m pytest -q tests/test_overnight_run.py` -> `19 passed`
- `~/lab-builder/.venv/bin/python -m pytest -q` -> `426 passed`
- `~/lab-builder/.venv/bin/python -m compileall app` -> passed
- `git diff --check` and staged `git diff --cached --check` -> clean
- staged secret scan -> `0` findings

No real hardware was touched. Final `git status -sb` is clean except for the pre-existing untracked `.codex/prompts/overnight-run-artifact-repair.md`.
ritten or faked; the repair changes make the next run and UI report these conditions clearly.
