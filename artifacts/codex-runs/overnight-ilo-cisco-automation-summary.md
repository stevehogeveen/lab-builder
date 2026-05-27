Implemented and pushed the Overnight Hardware Run feature.

Commit: `a962052afbf111610b3842d17ce826e1eb71ca21`  
Branch: `experiment/react-desktop-ui` pushed to `origin`

Added:
- Safe default overnight config/model and artifact writer in `app/overnight_run.py`
- iLO Redfish discovery and Cisco serial read-only discovery
- 5:30 AM hardware stop gate and 6:00 AM finalization gate
- Secret-scan-gated auto commit/push finalization
- `/overnight-hardware` UI with Operator Mode and Debug Mode
- Tests covering modes, safety flags, deadlines, secret scan blocking, mocked iLO/Cisco discovery, and UI/API exposure

Verification:
- `.venv/bin/python -m pytest -q tests/test_overnight_run.py` -> `8 passed`
- `.venv/bin/python -m pytest -q` -> `415 passed`
- `.venv/bin/python -m compileall app` -> passed
- Staged secret scan -> clean

Note: the shell does not have a `python` binary available, so verification used the project venv interpreter. I left the pre-existing unrelated dirty files uncommitted.
