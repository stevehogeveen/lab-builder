# Full Visible Workflow Parity Summary

Status: completed for currently inventoried visible workflow parity.

Changes made:
- Added visible Reports action inventory coverage in the React shell.
- Changed React action inventory controls so legacy POST actions render as real POST forms using the original backend routes.
- Changed POST download actions to submit forms instead of rendering incorrect GET links.
- Added concrete React action inventory entries for original navigation/report/history controls that were visible in the legacy templates.
- Added durable visible workflow parity tests in tests/test_full_visible_workflow_parity.py.
- Regenerated visible original/react inventories, parity matrix, missing controls, and remaining gaps artifacts.

Latest follow-up:
- Resolved the two documented dynamic-link gaps.
- Dashboard module cards now render backend-provided `legacy_href` destinations through the React-aware route mapper.
- Run Center now includes a stage readiness panel sourced from `execution_review.stages`, including original review and blocked-stage fix links.
- React app-state now degrades missing execution-review prerequisites, such as absent ESXi ISO media, into visible Needs attention stage rows instead of breaking Operator Mode.
- React app-state execution review is passive and does not run ESXi runtime reachability probes during normal Operator Mode polling.
- NetApp now exposes the explicit guarded "Factory reset NetApp" label in addition to reset readiness, matching the original visible control while still opening the original confirmation form.
- React execution-review fallback now scopes missing ESXi ISO/media blockers to the ESXi stage instead of making every included stage look blocked by the same ESXi issue.
- A follow-up NetApp template scan restored additional original console and cluster management IP action labels in React: save selected console, read current NetApp config, preview/apply console cluster IP, and preview/apply cluster management IP.
- Storage action labels were aligned back to the original operator wording for current-state display, plan build, approval, invalid-selection repair, and approval removal.
- Cisco action labels were aligned back to the original operator wording for console access, current-config check, version check, save-to-config, and config approval while preserving the explicit Setup Cisco IP action.
- NetApp connection/save labels were aligned back to the original operator wording for "Test ONTAP API" and "Save NetApp setup."
- iLO inventory readback was relabeled to the original operator wording, "Read current iLO."
- Dashboard and command header text sizing was tightened by removing viewport-scaled heading sizes and negative letter spacing from the app shell.
- Windows action labels were aligned to the original operator wording for template selection and dry-run install planning.
- Storage page primary copy now uses the original "Display current storage setup" and "Approve this plan" operator labels.
- NetApp setup-IP React feedback now reports the saved-values-only backend placeholder as a warning instead of a successful/sent action.
- Dashboard kit switching now uses the original "Switch active kit" wording while preserving the legacy load-kit route in the action inventory.
- Upgrade Helper, OVF directory registration, storage repair/reboot/artifact, and history artifact labels were aligned with the original visible controls.
- Exact visible-label comparison is down to three known differences: the intentional Cisco "Setup Cisco IP" alias and two raw Jinja conditional button expressions that resolve to existing React labels.
- Removed tracked stale Codex prompt files that still described re-adding the removed Overnight Hardware Run feature.

Remaining gaps:
- No unmatched original visible actions are currently listed in artifacts/codex-runs/full-visible-workflow-missing-controls.md.

Validation:
- /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_visible_workflow_parity.py: 9 passed
- /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_ui_parity_contract.py: 8 passed
- /home/administrator/lab-builder/.venv/bin/python -m pytest -q: 441 passed
- /home/administrator/lab-builder/.venv/bin/python -m compileall app: passed
- git diff --check: passed
- Latest focused validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_visible_workflow_parity.py tests/test_full_ui_parity_contract.py: 31 passed
- Latest current-head focused validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_visible_workflow_parity.py tests/test_full_ui_parity_contract.py: 33 passed
- Latest current-head full validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q: 457 passed in 242.63s
- Latest current-head compile/JS/diff checks: compileall app passed; node --check static/js/react-desktop-ui.js passed; git diff --check passed
- Latest Overnight removal/app-state validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_ui_parity_contract.py::test_overnight_hardware_feature_is_removed_from_routes_and_nav tests/test_full_visible_workflow_parity.py::test_overnight_hardware_surface_remains_removed tests/test_app.py::test_react_ui_app_state_api_exposes_desktop_shell_state: 3 passed

Hardware safety:
- No hardware actions were executed by tests or validation.
- Overnight Hardware Run remains removed from route/nav coverage, and no tracked non-test app/prompt/docs surface advertises it.
