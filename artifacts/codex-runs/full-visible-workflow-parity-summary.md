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
- Exact visible-label comparison is down to two known raw Jinja conditional rows; their resolved labels are now all represented in React.
- Removed tracked stale Codex prompt files that still described re-adding the removed Overnight Hardware Run feature.
- React visible action inventory artifact was resynced from `react_ui_action_inventory`; it now records all 176 live React actions.
- React Global Settings now updates the NetApp cluster-management bootstrap override when the global NetApp IP changes, so Setup IP, app-state, saved config, and NetApp management values stay aligned.
- React Setup IP now fills NetApp SP, cluster, node, and SVM management address fields from the saved default IP plan when no explicit bootstrap override exists.
- React JSON save responses for Global Settings and iLO setup now say the values were saved locally and that reachability has not been verified, matching the Operator Mode wording.
- React-aware navigation now maps the legacy `/modules/cisco` page alias back into the Cisco React page, matching the existing NetApp and OVF module aliases.
- Reports search and related-report filtering now load through `/api/ui/reports` and update the React Reports page in place instead of navigating back to `/configs`.

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
- Latest visible/UI parity validation after inventory resync: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_visible_workflow_parity.py tests/test_full_ui_parity_contract.py: 33 passed
- Latest NetApp/global-settings propagation validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_visible_workflow_parity.py tests/test_full_ui_parity_contract.py tests/test_app.py::test_react_ui_global_settings_api_saves_editable_shared_defaults tests/test_app.py::test_react_ui_global_settings_updates_netapp_cluster_management_override: 35 passed
- Latest compile/diff checks after NetApp propagation fix: /home/administrator/lab-builder/.venv/bin/python -m compileall app passed; git diff --check passed
- Latest NetApp setup-IP default validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_visible_workflow_parity.py tests/test_full_ui_parity_contract.py tests/test_app.py::test_react_ui_app_state_api_exposes_desktop_shell_state tests/test_app.py::test_react_ui_setup_ip_state_defaults_netapp_bootstrap_addresses_from_plan tests/test_app.py::test_react_ui_global_settings_updates_netapp_cluster_management_override: 36 passed
- Latest local-save wording validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_visible_workflow_parity.py tests/test_full_ui_parity_contract.py tests/test_app.py::test_react_ui_global_settings_api_saves_editable_shared_defaults tests/test_app.py::test_react_ui_ilo_settings_api_reuses_backend_save_logic tests/test_app.py::test_react_ui_setup_ip_state_defaults_netapp_bootstrap_addresses_from_plan tests/test_app.py::test_react_ui_global_settings_updates_netapp_cluster_management_override: 37 passed
- Latest React-aware module alias validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_full_ui_parity_contract.py::test_react_aware_navigation_includes_legacy_module_aliases tests/test_full_ui_parity_contract.py tests/test_full_visible_workflow_parity.py: 34 passed; node --check static/js/react-desktop-ui.js passed
- Latest Reports React-shell validation: /home/administrator/lab-builder/.venv/bin/python -m pytest -q tests/test_app.py::test_react_ui_reports_api_filters_report_center_without_legacy_navigation tests/test_app.py::test_react_ui_mapped_pages_and_actions_match_registered_routes tests/test_full_ui_parity_contract.py::test_reports_search_stays_in_react_shell tests/test_full_visible_workflow_parity.py::test_hard_coded_react_internal_urls_map_to_registered_routes tests/test_full_visible_workflow_parity.py::test_report_center_panel_has_view_and_download_forms tests/test_full_ui_parity_contract.py tests/test_full_visible_workflow_parity.py: 37 passed; node --check static/js/react-desktop-ui.js and git diff --check passed

Hardware safety:
- No hardware actions were executed by tests or validation.
- Overnight Hardware Run remains removed from route/nav coverage, and no tracked non-test app/prompt/docs surface advertises it.
