Implemented and pushed.

Key changes:
- Added scheduler command: [app/overnight_finalize.py](/home/administrator/lab-builder-react/app/overnight_finalize.py)
- Added wrapper: [scripts/finalize-overnight-run](/home/administrator/lab-builder-react/scripts/finalize-overnight-run)
- Extended finalizer in [app/overnight_run.py](/home/administrator/lab-builder-react/app/overnight_run.py) to write the hardware stop marker, `MORNING_READY.md`, git status before/after, branch, SHA, push/test results, artifact folder, and secret-scan decisions.
- Added exact `at` and `cron` scheduling docs in [docs/HOWTO.md](/home/administrator/lab-builder-react/docs/HOWTO.md).
- Added tests for finalization decision logic and stop-marker behavior in [tests/test_overnight_run.py](/home/administrator/lab-builder-react/tests/test_overnight_run.py).

Validation:
- Focused: `.venv/bin/python -m pytest -q tests/test_overnight_run.py` -> `15 passed`
- Full: `.venv/bin/python -m pytest -q` -> `422 passed`
- Compile: `.venv/bin/python -m compileall app` -> passed
- Staged secret scan: `0` findings
- `git diff --cached --check` -> clean

Note: bare `python` is not on this host’s PATH, so I used the repo venv equivalent.

Committed and pushed:
- Branch: `experiment/react-desktop-ui`
- Commit: `9fd20411d433147cf93241819297948d0e74d9b9`
- Push: `origin/experiment/react-desktop-ui`

Left untracked, as they were not part of the code change:
- `.codex/prompts/overnight-finalize-scheduler.md`
- `artifacts/codex-runs/overnight-finalize-scheduler-summary.md`
