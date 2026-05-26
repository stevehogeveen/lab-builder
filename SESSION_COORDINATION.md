# Session Coordination

Purpose: keep parallel Codex sessions from colliding in the same workspace.

Update this file before large edits and after finishing a meaningful slice.

## Rules

1. Read `AGENTS.md`, `docs/workflow-session-scopes.md`, `docs/operator-flow-contract.md`, `docs/automation-principles.md`, and `docs/ux-product-principles.md` before editing.
2. Claim write scope before editing shared files.
3. Keep ownership narrow: list exact files or directories.
4. Add a short "working on" note while active.
5. Append a "changed" note when you finish a slice.
6. If two sessions need the same file, stop and re-assign explicitly here first.

## Session Template

Copy this block and update it in place.

```md
### Session: <name>
- Status: active | paused | done
- Branch: <branch-name>
- Scope owner: <what this session owns>
- Working on: <current task>
- Blocked by: <session/file/dependency or none>
- Ready to hand off: <next clean handoff point or none>
- Files claimed:
  - path/a
  - path/b
- Shared files touched with caution:
  - path/c
- Last changed:
  - YYYY-MM-DD HH:MM TZ - <short note>
- Next intended change:
  - <short note>
```

## Active Sessions

### Session: ovf-templates-status-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: OVF Templates persistent last-action and next-step status
- Working on: Completed OVF Templates persistent last-action and next-step status without hardware access.
- Blocked by: none
- Ready to hand off: OVF Templates now keeps a persistent latest-registration status and focused route/template coverage passes.
- Files claimed:
  - templates/partials/pages/ovf_templates.html
  - tests/test_ovf_templates_page.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
- Last changed:
  - 2026-05-26 00:15 EDT - Claimed narrow OVF Templates persistent status slice for the 14-hour quality run.
  - 2026-05-26 00:15 EDT - Added persistent OVF Templates last-action/next-step status and verified focused route/template coverage.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed setup page status placement or button-route audit.

### Session: upgrade-helper-panel-actions-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Upgrade Helper generated tab action button feedback consistency
- Working on: Completed generated Upgrade Helper tab action button feedback consistency without hardware access.
- Blocked by: none
- Ready to hand off: Generated Upgrade Helper tab HTMX actions now render with the shared action-button class; focused Upgrade Helper tests, tab render guard, and compile check pass.
- Files claimed:
  - templates/partials/components/upgrade_components.html
  - tests/test_upgrade_helper.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - templates/partials/components/upgrade_components.html
- Last changed:
  - 2026-05-26 00:07 EDT - Claimed narrow Upgrade Helper generated tab action-control consistency slice for the 14-hour quality run.
  - 2026-05-26 00:07 EDT - Added shared action-button class to generated Upgrade Helper tab actions and verified focused route/template coverage.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed page button/route audit or status placement guard.

### Session: dashboard-map-navigation-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Dashboard Living kit map native navigation controls
- Working on: Completed Dashboard Living kit map native navigation controls without hardware access.
- Blocked by: none
- Ready to hand off: Living kit map navigation now works as native links; focused Dashboard tests, operator-flow contract tests, and compile check pass.
- Files claimed:
  - templates/partials/pages/dashboard.html
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - tests/test_app.py
- Last changed:
  - 2026-05-25 23:59 EDT - Claimed narrow Dashboard Living kit map navigation-control slice for the 14-hour quality run.
  - 2026-05-25 23:59 EDT - Converted Living kit map navigation controls to anchors and verified focused route/template coverage.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is a small clutter/status audit on an unclaimed page or another native-navigation fallback check.

### Session: kits-destructive-controls-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Kits page destructive clean/delete control clarity
- Working on: Completed Kits cleanup/delete destructive-control clarity without hardware access.
- Blocked by: none
- Ready to hand off: Kits cleanup/delete controls are now visibly destructive; focused Kits render test, operator-flow contract tests, and compile check pass.
- Files claimed:
  - templates/partials/pages/kits.html
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - tests/test_app.py
- Last changed:
  - 2026-05-25 23:52 EDT - Claimed narrow Kits destructive-control clarity slice for the 14-hour quality run.
  - 2026-05-25 23:52 EDT - Marked Kits cleanup/delete submit buttons as destructive controls and verified focused coverage.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed setup page clutter/status placement audit.

### Session: upgrade-helper-controls-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Upgrade Helper media and policy control feedback audit
- Working on: Completed Upgrade Helper media and policy control feedback cleanup without hardware access.
- Blocked by: none
- Ready to hand off: Upload and policy-save controls now use the shared action feedback metadata pattern; focused Upgrade Helper tests, operator-flow contract tests, and compile check pass.
- Files claimed:
  - templates/partials/pages/upgrade_helper.html
  - tests/test_upgrade_helper.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
- Last changed:
  - 2026-05-25 23:43 EDT - Claimed narrow Upgrade Helper media/policy control feedback slice for the 14-hour quality run.
  - 2026-05-25 23:43 EDT - Added action metadata to Upgrade Helper upload/policy controls and verified route/template coverage.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed page button/route audit or a setup-page clutter/status placement pass.

### Session: global-settings-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Configuration / Global Settings page button-route rendering guard
- Working on: Completed focused Global Settings visible-control route/template coverage without hardware access.
- Blocked by: none
- Ready to hand off: Global Settings and Configuration aliases now have focused render coverage for save, populate, and SNMP user controls; focused tests, operator-flow contract tests, and compile check pass.
- Files claimed:
  - tests/test_global_settings_page.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
- Last changed:
  - 2026-05-26 00:00 EDT - Claimed narrow Global Settings control wiring test slice for the 14-hour quality run.
  - 2026-05-26 00:06 EDT - Added Global Settings alias control wiring guard and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed setup page button/route audit or status placement guard.

### Session: execution-summary-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Run Center summary-open destination cleanup
- Working on: Completed Run Center summary-open destination cleanup without hardware access.
- Blocked by: none
- Ready to hand off: Run Center opened summaries now render on Reports; focused Run Center tests, operator-flow contract tests, and compile check pass.
- Files claimed:
  - templates/partials/pages/execution.html
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - templates/partials/pages/execution.html
  - tests/test_app.py
- Last changed:
  - 2026-05-26 00:00 EDT - Claimed narrow Run Center summary destination cleanup for the 14-hour quality run.
  - 2026-05-26 00:04 EDT - Routed Run Center opened summaries to Reports and verified focused route/template coverage.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is a focused Configuration page button/route rendering guard or another unclaimed Run Center control audit.

### Session: ilo-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: iLO setup page last-action/log visibility audit
- Working on: Completed iLO latest action/log visibility and focused route/template coverage without hardware access.
- Blocked by: none
- Ready to hand off: iLO page now keeps the latest action/log section visible by default; focused iLO page tests, operator-flow contract tests, and compile check pass.
- Files claimed:
  - templates/partials/pages/ilo.html
  - tests/test_ilo_page.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
- Last changed:
  - 2026-05-25 23:19 EDT - Claimed narrow iLO setup latest-action/log visibility slice for the 14-hour quality run.
  - 2026-05-25 23:20 EDT - Opened the iLO latest-action section by default and verified iLO action/log routes with focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is a focused ESXi last-action visibility or another unclaimed page action-route audit.

### Session: upgrade-helper-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Upgrade Helper Cisco-tab secret-safe button/route audit
- Working on: Completed Upgrade Helper Cisco-tab action payload safety cleanup without hardware access.
- Blocked by: none
- Ready to hand off: Upgrade Helper Cisco tab no longer renders saved Cisco credentials in HTMX values; focused tests and compile check pass.
- Files claimed:
  - app/upgrade_panels.py
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - tests/test_app.py
- Last changed:
  - 2026-05-25 23:11 EDT - Claimed narrow Upgrade Helper Cisco-tab action payload safety slice for the 14-hour quality run.
  - 2026-05-25 23:12 EDT - Removed rendered Cisco credentials from the Upgrade Helper version-read action and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed setup page action-payload and log/status placement audit.

### Session: history-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: History page report-opening flow cleanup
- Working on: Completed History page report-opening destination cleanup without hardware access.
- Blocked by: none
- Ready to hand off: History page run-summary and storage-plan buttons now open technical report content on the Reports surface; focused render tests, Reports route test, and compile check pass.
- Files claimed:
  - templates/partials/pages/history.html
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - tests/test_app.py
- Last changed:
  - 2026-05-25 23:03 EDT - Claimed narrow History page report-opening destination slice for the 14-hour quality run.
  - 2026-05-25 23:04 EDT - Routed History page report-open forms to Reports and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is a focused setup page last-action/log placement audit for an unclaimed page.

### Session: dashboard-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Dashboard job-status Open log route audit
- Working on: Completed Dashboard job-status Open log route fix without hardware access.
- Blocked by: none
- Ready to hand off: Dashboard job-status Open log buttons now open saved reports; focused test, operator-flow contract test, and compile check pass.
- Files claimed:
  - templates/partials/pages/dashboard.html
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - templates/partials/pages/dashboard.html
  - tests/test_app.py
- Last changed:
  - 2026-05-25 22:56 EDT - Claimed narrow Dashboard job-status Open log route/template slice for the 14-hour quality run.
  - 2026-05-25 22:56 EDT - Routed Dashboard Open log buttons to the saved-report handler and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is a focused History page details/log clutter audit.

### Session: reports-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Reports page button/route rendering guard
- Working on: Completed focused Reports page controls and technical details label guard without hardware access.
- Blocked by: none
- Ready to hand off: Reports page controls are covered by focused route/template assertions; focused tests and compile check pass.
- Files claimed:
  - templates/partials/pages/reports.html
  - tests/test_reports.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
- Last changed:
  - 2026-05-25 22:48 EDT - Claimed narrow Reports page controls and technical details label slice for the 14-hour quality run.
  - 2026-05-25 22:52 EDT - Added Reports page control route guard, aligned the technical details heading, and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is a focused History page detail/log clutter audit.

### Session: windows-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Windows setup page last-action status consistency
- Working on: Completed focused Windows setup last-action status consistency without hardware access.
- Blocked by: none
- Ready to hand off: Windows page now keeps its latest saved/planned action visible; focused tests and compile check pass.
- Files claimed:
  - templates/partials/pages/windows.html
  - tests/test_windows_page.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
- Last changed:
  - 2026-05-25 22:39 EDT - Claimed narrow Windows setup last-action status slice for the 14-hour quality run.
  - 2026-05-25 22:40 EDT - Added Windows last-action status card and focused render coverage.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is a focused Reports or History button/route audit.

### Session: ovf-templates-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: OVF Templates register-directory feedback audit
- Working on: Completed OVF Templates failed-registration feedback cleanup without hardware access.
- Blocked by: none
- Ready to hand off: OVF Templates register-directory button route is covered; failures now show once in the shared action receipt.
- Files claimed:
  - app/modules/ovf_templates/routes.py
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - tests/test_app.py
- Last changed:
  - 2026-05-25 22:31 EDT - Claimed narrow OVF Templates failed-registration feedback slice for the 14-hour quality run.
  - 2026-05-25 22:32 EDT - Routed failed OVF directory registration through shared action feedback and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is a focused Reports or History button/route audit.

### Session: kits-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Kits page button/route rendering guard
- Working on: Completed focused Kits page visible-action template guard without hardware access.
- Blocked by: none
- Ready to hand off: Kits page create, load, clean, and delete controls are covered by focused render assertions; focused tests and compile check pass.
- Files claimed:
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - tests/test_app.py
- Last changed:
  - 2026-05-25 22:23 EDT - Claimed narrow Kits page button/route test slice for the 14-hour quality run.
  - 2026-05-25 22:24 EDT - Added focused Kits page action-route rendering guard and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed page button/route audit.

### Session: esxi-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: ESXi setup page latest-run log button audit
- Working on: Completed ESXi latest-run Open log route fix without hardware access.
- Blocked by: none
- Ready to hand off: ESXi latest-run Open log now opens the saved report; focused test, operator-flow contract test, and compile check pass.
- Files claimed:
  - templates/partials/pages/esxi.html
  - tests/test_esxi.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
- Last changed:
  - 2026-05-25 22:16 EDT - Claimed narrow ESXi Open log route/template slice for the 14-hour quality run.
  - 2026-05-25 22:18 EDT - Routed ESXi latest-run Open log to the saved-report handler and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed setup page button/route audit.

### Session: storage-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Storage setup page latest-run log button audit
- Working on: Completed Storage latest-run Open log route fix without hardware access.
- Blocked by: none
- Ready to hand off: Storage latest-run receipts now find mode-specific apply scopes and Open log opens the saved report; focused tests and compile check pass.
- Files claimed:
  - app/main.py
  - templates/partials/pages/storage.html
  - tests/test_storage.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - app/main.py
- Last changed:
  - 2026-05-25 22:07 EDT - Claimed narrow Storage Open log route/template slice for the 14-hour quality run.
  - 2026-05-25 22:08 EDT - Expanded the same Storage log-button slice to include the shared latest receipt scope matcher.
  - 2026-05-25 22:09 EDT - Routed Storage Open log to the saved-report handler, matched mode-specific storage apply scopes, and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is the ESXi latest receipt Open log route/template audit.

### Session: qnap-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: QNAP setup page status/button audit
- Working on: Completed persistent QNAP last-action status without changing hardware behavior
- Blocked by: none
- Ready to hand off: QNAP page now keeps its latest saved-action status visible; focused tests and operator-flow contract tests pass
- Files claimed:
  - templates/partials/pages/qnap.html
  - tests/test_qnap.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
- Last changed:
  - 2026-05-25 21:59 EDT - Claimed narrow QNAP page status consistency slice for the 14-hour quality run.
  - 2026-05-25 22:00 EDT - Added QNAP last-action status from existing history events and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed setup page button/route audit.

### Session: cisco
- Status: active
- Branch: experience/operator-companion
- Scope owner: Cisco workflow, Cisco setup UI, serial-console bootstrap, and Cisco validation
- Working on: Cisco operator-flow setup round after serial-console bootstrap changes
- Blocked by: none
- Ready to hand off: Current Cisco console-bootstrap slice is implemented, tested, and ready to commit
- Files claimed:
  - app/cisco.py
  - app/modules/cisco/**
  - templates/partials/pages/cisco.html
  - tests/test_cisco_*.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - AGENTS.md
  - docs/README.md
  - docs/operator-flow-contract.md
  - docs/workflow-session-scopes.md
  - app/main.py
  - app/core/config.py
  - app/modules/configs/routes.py
  - tests/test_app.py
  - tests/test_operator_flow_contract.py
- Last changed:
  - 2026-05-25 America/Toronto - Read operator-flow session docs, claimed Cisco workflow scope, and prepared current Cisco bootstrap/operator-contract changes for commit.
- Next intended change:
  - Align Cisco page sections explicitly to Context, Targets, Credentials, Current State, Preflight, Plan, Execute, Monitor, Evidence, and Next Step.

### Session: netapp
- Status: active
- Branch: unknown
- Scope owner: NetApp workflow, NetApp module UI, NetApp planning/validation
- Working on: Final NetApp page completion, protocol-object editing, and richer dry-run review
- Blocked by: none
- Ready to hand off: Page-complete NetApp review slice can hand off after template and route parsing updates are recorded here
- Files claimed:
  - app/modules/netapp/**
  - app/netapp.py
  - tests/test_netapp_module.py
  - templates/partials/pages/netapp.html
- Shared files touched with caution:
  - app/main.py
  - app/core/config.py
  - app/modules/configs/routes.py
  - templates/partials/pages/configuration.html
  - templates/partials/pages/execution.html
  - templates/partials/pages/dashboard.html
  - static/js/live-job.js
  - tests/test_app.py
  - tests/test_netapp_module.py
- Last changed:
  - 2026-05-12 America/Toronto - Session declared from coordination template.
  - 2026-05-12 America/Toronto - Claimed ONTAP adapter and NetApp tests for fallback-read and capability cleanup.
  - 2026-05-12 America/Toronto - Added adaptive REST field fallback in app/netapp.py and made NetApp validation capability-aware for unverifiable NTP/users/autosupport/subnet checks.
  - 2026-05-12 America/Toronto - Added read-only export-policy, igroup, portset, LUN, and LUN-map discovery so older ONTAP can still describe current NFS/iSCSI posture when service endpoints are sparse.
  - 2026-05-12 America/Toronto - Added protocol-specific validation for NFS export policy/volume and iSCSI igroup/portset/LUN mappings, and tied plan action statuses to those checks.
  - 2026-05-12 America/Toronto - Added protocol LIF name/IP/node/port comparison against discovered interfaces and tied NFS/iSCSI LIF plan actions to that validation.
  - 2026-05-12 America/Toronto - Completed the NetApp page review surface with capability status, validation findings, protocol object inventory, and editable iSCSI or NFS LIF or volume form fields.
  - 2026-05-12 America/Toronto - Reworked NetApp into a bootstrap-first compact page with generated manual checklist, derived SP/node/cluster IPs, connectivity tests, and a reduced post-bootstrap snapshot focused on controllers, disks, and current port IPs.
  - 2026-05-13 America/Toronto - Shifted NetApp defaults to the real .45/.46/.47/.48 management convention, added legacy .40/.41/.42/.43 warning plus one-click update, and relabeled the bootstrap plan with controller and port names.
  - 2026-05-13 America/Toronto - Enabled the first NetApp safe-apply slice for create-only API actions (subnets, SVM, LIFs, services, export policy, igroup, portset, NFS volume) with in-page execution logs and manual blocks for the remaining actions.
  - 2026-05-19 America/Toronto - Fixed stale ONTAP current-release display by refreshing live NetApp upgrade inventory before ONTAP planning/runs, caching successful NetApp page discovery into the shared upgrade gate, and surfacing the current release on NetApp and Upgrade Helper pages.
  - 2026-05-19 America/Toronto - Made sidebar NetApp navigation render from saved/cached state instead of running synchronous live ONTAP discovery; explicit NetApp actions still perform live reads.
  - 2026-05-19 America/Toronto - Fixed ONTAP upgrade runner so validation-completed pending-version state is not mistaken for an already-running software update; runner now sends the actual start request after validation.
  - 2026-05-19 America/Toronto - Tightened ONTAP upgrade completion detection so target-version visibility during an in-progress takeover/giveback phase does not mark Lab Builder complete; corrected current Lab-Uplands-G10 activity back to running while ONTAP reports in_progress.
- Next intended change:
  - Use operator feedback from the next manual test pass to tighten layout, wording, and any remaining protocol-detail validations.

### Session: vcenter-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: vCenter setup page button/route audit
- Working on: Completed vCenter visible-form action fix for Generate install spec and Run vCenter install
- Blocked by: none
- Ready to hand off: vCenter form actions include current visible values; focused tests and operator-flow contract tests pass
- Files claimed:
  - templates/partials/pages/vcenter.html
  - tests/test_vcenter.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - app/main.py
- Last changed:
  - 2026-05-25 19:34 EDT - Claimed narrow vCenter form-action route slice for the 14-hour quality run.
  - 2026-05-25 19:34 EDT - Added shared vCenter form-state application, wired generate/run buttons to include the form, and verified focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed setup page button/route audit.

### Session: windows-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: Windows setup page button/route audit
- Working on: Completed Windows visible-form action fix for probe and dry-run plan buttons
- Blocked by: none
- Ready to hand off: Windows probe and plan actions include current visible setup values; focused tests and compile check pass
- Files claimed:
  - app/modules/windows/routes.py
  - templates/partials/pages/windows.html
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - tests/test_app.py
- Last changed:
  - 2026-05-25 21:32 EDT - Claimed narrow Windows visible-form action slice for the 14-hour quality run.
  - 2026-05-25 21:36 EDT - Wired Windows probe and dry-run plan actions to visible form values and verified with focused tests.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is another unclaimed setup page button/route audit.

### Session: ilo-quality-cycle
- Status: done
- Branch: codex/14h-quality-run
- Scope owner: iLO setup page latest-run log button audit
- Working on: Completed iLO receipt Open log route fix.
- Blocked by: none
- Ready to hand off: iLO latest-run Open log now opens the saved report on the iLO page; focused tests and compile check pass.
- Files claimed:
  - templates/partials/pages/ilo.html
  - tests/test_app.py
- Shared files touched with caution:
  - SESSION_COORDINATION.md
  - tests/test_app.py
- Last changed:
  - 2026-05-25 21:43 EDT - Claimed narrow iLO Open log route/template slice for the 14-hour quality run.
  - 2026-05-25 21:45 EDT - Routed the iLO latest-run Open log form to the saved-report handler and added focused route/template coverage.
- Next intended change:
  - Continue with a separate backlog cycle; suggested next target is auditing the same saved-log route mismatch on another setup page.

### Session: rest-of-app
- Status: paused
- Branch: experience/operator-companion
- Scope owner: non-NetApp app work
- Working on: Paused after experimental operator companion and app-wide lens polish
- Blocked by: none
- Ready to hand off: Branch is pushed, full regression passed, and `SESSION_CATCHUP_NEXT_CODEX.md` has the next-session handoff
- Files claimed:
  - SESSION_COORDINATION.md
  - templates/index.html
  - templates/partials/pages/dashboard.html
  - templates/partials/components/precheck_summary.html
  - templates/partials/sidebar.html
  - docs/ux-product-principles.md
  - docs/ux-experimental-operator-companion.md
  - tests/test_app.py
- Shared files touched with caution:
  - app/main.py
  - app/core/config.py
  - app/modules/configs/routes.py
  - templates/partials/pages/configuration.html
  - templates/partials/pages/execution.html
  - templates/partials/pages/dashboard.html
  - static/js/live-job.js
  - tests/test_app.py
- Last changed:
  - 2026-05-12 America/Toronto - Added blocked-by and ready-to-hand-off fields and confirmed shared-caution rules.
  - 2026-05-12 America/Toronto - Claimed sidebar template for setup-group navigation cleanup.
  - 2026-05-12 America/Toronto - Claimed app-wide sidebar test for navigation regrouping coverage.
  - 2026-05-12 America/Toronto - Claimed NetApp snapshot UI and discovery detail slice in this session.
  - 2026-05-12 America/Toronto - Moved Windows, Cisco, and NetApp links into the main Setup group and verified sidebar coverage.
  - 2026-05-12 America/Toronto - Renamed the remaining sidebar Modules section to Setup Modules.
  - 2026-05-12 America/Toronto - Claimed shared pre-check summary slice for dashboard and setup pages, excluding the active NetApp page file.
  - 2026-05-12 America/Toronto - Added reusable operations-style pre-check summaries to dashboard plus iLO, Storage, ESXi, Windows, and QNAP pages.
  - 2026-05-12 America/Toronto - Claimed shared layout CSS to reduce oversized cards and finish the NetApp page in the denser UI style.
  - 2026-05-12 America/Toronto - Tightened shared card spacing, removed forced full-height cards, and rebuilt the NetApp page into a denser operations-style layout.
  - 2026-05-12 America/Toronto - Replaced remaining large pre-check and setup tiles with denser PRTG-style strips and status rows.
  - 2026-05-12 America/Toronto - Widened the setup rail, added command-bar hero metadata, and regrouped Global Settings with advanced templates collapsed.
  - 2026-05-12 America/Toronto - Claimed compact setup-strip component and mission-control/dashboard tightening slice for iLO, ESXi, Windows, QNAP, and NetApp inventory views.
  - 2026-05-12 America/Toronto - Replaced remaining setup mini-dashboards with compact strips, tightened the dashboard mission-control block, and converted NetApp inventory areas to denser operator rows.
  - 2026-05-12 America/Toronto - Claimed Storage and Run Center templates for the next compact-layout pass.
  - 2026-05-12 America/Toronto - Fixed the shared setup-strip component, removed stray bottom-of-page CSS output, and compacted Storage planner plus Run Center technical-detail layouts.
  - 2026-05-12 America/Toronto - Added shared truncation/table-fit helpers and applied them to reports, history, and storage artifact/detail surfaces.
  - 2026-05-12 America/Toronto - Finished the remaining page-fit pass for ESXi, iLO, Windows, QNAP, and Cisco, including legacy input styling cleanup and long-value truncation.
  - 2026-05-15 America/Toronto - Added Cisco console failure classification, visible probe results, and focused diagnostics tests for no-adapter, permission, and no-prompt cases.
  - 2026-05-15 America/Toronto - Tightened Cisco serial discovery to verify exec prompts with read-only show version output and downgrade generic non-Cisco prompts before auto-selection.
  - 2026-05-15 America/Toronto - Added Cisco operator findings for weak secrets, IP-plan overrides, missing management VLANs, unexpected connected-port VLANs, and bootstrap port selection choices.
  - 2026-05-15 America/Toronto - Added Cisco current-version button with console fallback and introduced local per-card HTMX action feedback so button results appear near the initiating workflow.
  - 2026-05-15 America/Toronto - Claimed dashboard/sidebar command-center guidance polish slice.
  - 2026-05-15 America/Toronto - Added guided dashboard build path, operator model card, and sidebar kit-state meter; full regression passed.
  - 2026-05-15 America/Toronto - Claimed follow-up dashboard duplicate-readiness cleanup slice.
  - 2026-05-15 America/Toronto - Removed duplicate dashboard module-readiness panel and promoted kit/job widgets into a compact two-column workspace block.
  - 2026-05-15 America/Toronto - Claimed app-wide command palette, density, and accessibility polish slice.
  - 2026-05-15 America/Toronto - Added command palette, compact-view toggle, skip link, and UX product-principles notes.
  - 2026-05-15 America/Toronto - Claimed app-wide readiness issue drawer slice.
  - 2026-05-15 America/Toronto - Added global readiness issue drawer with blocker summaries and page navigation; full regression passed.
  - 2026-05-15 America/Toronto - Created experience/operator-companion branch for experimental calm/adaptive operator experience work.
  - 2026-05-15 America/Toronto - Added universal operator companion, dashboard living kit map, and experimental UX branch notes.
  - 2026-05-15 America/Toronto - Full regression passed for operator companion branch.
  - 2026-05-15 America/Toronto - Added experience lens and proof ledger experimental layer.
  - 2026-05-15 America/Toronto - Full regression passed after lens/proof layer wording fix.
  - 2026-05-15 America/Toronto - Claimed opt-in cosmic/psychedelic visual transformation slice.
  - 2026-05-15 America/Toronto - Added opt-in Cosmic mode visual atmosphere with local preference storage and reduced-motion-safe CSS.
  - 2026-05-15 America/Toronto - Full regression passed for Cosmic mode slice.
  - 2026-05-15 America/Toronto - Added Reality engine controls for cosmic intensity, drift, orbit, cursor aura, presets, and emergency normal mode.
  - 2026-05-15 America/Toronto - Full regression passed for Reality engine slice.
  - 2026-05-16 America/Toronto - Reworked experience lens into app-wide Calm, Normal, and Expert behavior for detail/log visibility.
  - 2026-05-16 America/Toronto - Full regression passed for app-wide lens alignment slice.
  - 2026-05-16 America/Toronto - Added lens cockpit visuals, shortcuts, and safe Expert keep-closed handling for destructive panels.
  - 2026-05-16 America/Toronto - Full regression passed for lens cockpit slice.
  - 2026-05-16 America/Toronto - Stopped app servers and wrote `SESSION_CATCHUP_NEXT_CODEX.md` handoff note.
- Next intended change:
  - Resume from `SESSION_CATCHUP_NEXT_CODEX.md`, then decide whether to merge, refine, or cherry-pick the experimental branch.

## Shared File Ledger

Use this section only for files that more than one session may need.

```md
- path/to/file
  - Current owner: <session-name>
  - Reason: <why this file is shared>
  - Safe touch window: <optional note>
```

Current entries:

- app/main.py
  - Current owner: unassigned/shared-caution
  - Reason: cross-cutting routing and page context
  - Safe touch window: coordinate here before edits

- app/core/config.py
  - Current owner: unassigned/shared-caution
  - Reason: shared config defaults and shape
  - Safe touch window: coordinate here before edits

- app/modules/configs/routes.py
  - Current owner: unassigned/shared-caution
  - Reason: shared settings persistence
  - Safe touch window: coordinate here before edits

- templates/partials/pages/configuration.html
  - Current owner: unassigned/shared-caution
  - Reason: shared settings UI
  - Safe touch window: coordinate here before edits

- templates/partials/pages/execution.html
  - Current owner: unassigned/shared-caution
  - Reason: shared run-center UI
  - Safe touch window: coordinate here before edits

- templates/partials/pages/dashboard.html
  - Current owner: unassigned/shared-caution
  - Reason: shared dashboard UI
  - Safe touch window: coordinate here before edits

- static/js/live-job.js
  - Current owner: unassigned/shared-caution
  - Reason: shared live run-center behavior
  - Safe touch window: coordinate here before edits

- tests/test_app.py
  - Current owner: unassigned/shared-caution
  - Reason: shared app-wide regression coverage
  - Safe touch window: coordinate here before edits
- 2026-05-12 America/Toronto - Fixed iLO save persistence by allowing base iLO credentials to save even when optional policy secrets are incomplete, preserving blank-posted secrets in legacy save-config, and correcting NetApp IP alias handling in calc_ip_plan().
- 2026-05-12 America/Toronto - Decoupled setup-page/sidebar readiness from stale run history, removed the storage probe action, and reduced duplicate page-level target summary cards.
- 2026-05-12 America/Toronto - Flattened page-level pre-check rows to remove duplicate left-side title/detail rendering in setup pre-check sections.
- 2026-05-12 America/Toronto - Removed the unused iLO discovery action/route/test and added a shared HTMX request overlay with busy-button state so actions visibly show in-progress work.
- 2026-05-12 America/Toronto - Brought the remaining older setup/history/run-center templates onto the newer soft-card and strip layout patterns across Global, iLO, ESXi, Windows, QNAP, History, and Execution.
- 2026-05-13 America/Toronto - Rebuilt the Dashboard into a generic deployment cockpit with readiness score, blocker signals, module readiness map, compact kit management, and generic dashboard header stats.
- 2026-05-13 America/Toronto - Wired NetApp into the shared Run Center real-execution path with a stage plugin, safe-apply launch option, NetApp prechecks, and background runner support in app/main.py.
- 2026-05-13 America/Toronto - Made NetApp safe apply capability-aware for missing ONTAP API surfaces so unsupported writes (first hit: /api/network/ip/subnets on 9.9.1P2) are blocked/manual instead of failing the whole run.
- 2026-05-13 America/Toronto - Fixed NetApp runner log plumbing and reran the live NetApp stage through main.py; the stage now completes cleanly and records blocked/manual ONTAP actions instead of failing hard on older API surfaces.
  - 2026-05-13 America/Toronto - Cleaned stale operator-facing scaffold/placeholder wording, aligned duplicate NetApp .45 default, made NetApp discovered management IPs persist through reload, and tightened NetApp profile defaults to the configured subnet/netmask.
- 2026-05-13 America/Toronto - Cleaned another app-wide polish slice: shortened Reports wording, collapsed detailed pre-check rows, widened setup page content, fixed mobile sidebar/content crushing, fixed tablet table overflow, removed generated __pycache__ folders, and verified with browser render checks.
- 2026-05-13 America/Toronto - Added broadcast-domain adoption from discovered protocol LIF placement; Lab-Uplands-G10 now resolves NFS_BD instead of flagging a fake missing Data domain, and safe apply skips that step cleanly.
- 2026-05-13 America/Toronto - Added concrete VMware NFS datastore planning from discovered NetApp state (SVM, NFS LIF IPs, export path, datastore name, per-ESXi mount plan) and passed it through the NetApp planner for UI consumption.
- 2026-05-13 America/Toronto - Refined VMware NFS datastore planning to assign a preferred server per ESXi host, alternate path, validate mount inputs, and emit candidate PowerCLI New-Datastore commands from discovered NetApp state.
- 2026-05-13 America/Toronto - Switched VMware NFS planning to standalone ESXi mode when no vCenter is configured; Lab-Uplands-G10 now validates against ESXi 10.10.8.111 with saved root credentials and emits a single direct datastore mount command.

- 2026-05-13 America/Toronto - Added a standalone ESXi/NFS probe action to the NetApp page that tests ESXi management reachability and TCP/2049 on discovered NFS LIFs, persisting the latest probe result for operator review.

- 2026-05-13 America/Toronto - Added standalone ESXi NFS datastore automation work: discovered NetApp export-policy mismatch, now creates export rules/volume binding and is being tuned to fall back from NFS 4.1 to NFS v3 when the ESXi host cannot bring up the 4.1 mount.
- 2026-05-13 America/Toronto - Ran overall health/sanity pass: added requirements.txt, added scripts/health-check, fixed Cisco direct route compatibility, added stage package markers, documented cleanup lessons, removed regenerated caches, and replaced real-looking test password literals with dummy test values.

- 2026-05-13 America/Toronto - Added a shared Upgrade Helper inventory path: scans /media, normalizes current vs available versions for iLO/ONTAP/Cisco, and surfaces per-device upgrade posture in Global Settings and setup prechecks.

2026-05-13 America/Toronto - Wired Cisco version discovery into Upgrade Helper: SSH show version parsing, cached upgrade inventory, Global Settings Cisco access, and direct read-version actions.

2026-05-13 America/Toronto - Upgrade Helper now resolves repo-local media under media/, recognizes real ONTAP q_image and compact iLO firmware filenames, and exposes an Upgrade planner drill-down in Global Settings.

2026-05-13 America/Toronto - Added dedicated /upgrade-helper page, promoted upgrade gates into recommended-next-step routing, and made Global Settings + Upgrade Helper render the same planner state from repo-local media.

2026-05-13 America/Toronto - Added Windows local OVA/OVF path registration with sidecar validation, compact source inventory UI, dry-run plan source summaries, and Windows OVF tests/docs.

2026-05-13 America/Toronto - Extended Windows dry-run planning with OVF hardware metadata parsing, deployment preview UI, target placement summary, and OVF network mismatch warnings.

2026-05-13 America/Toronto - Split OVF handling into a reusable OVF Templates module: register full local template directories, validate sidecars, and let Windows select a registered template for planning.

2026-05-13 America/Toronto - Added upgrade gate policies (block/warn/ignore), enforced them in validate_execution_scope and Run Center readiness, isolated app tests from live media by default, and added dedicated /upgrade-helper policy save flow.

2026-05-13 America/Toronto - Surfaced upgrade policy state earlier: dashboard and setup prechecks now use policy-aware blocker text from Upgrade Helper, and per-device upgrade detail cards now show raw version/source, policy, and matched media path.

2026-05-13 America/Toronto - Added device-specific compatibility notes for Upgrade Helper: ONTAP baseline/media details, Cisco model/platform/media hints, and concise policy-aware blocker text propagated to dashboard and Run Center.
2026-05-13 America/Toronto - Added first iLO firmware-upgrade workflow: family-safe media matching between ilo5/ilo6 `.fwpkg` files, iLO upgrade planning/execution routes, Redfish HttpPush upload support in `app/ilo.py`, and operator actions on Upgrade Helper + iLO pages with targeted upgrade tests.
2026-05-13 America/Toronto - Ran the first live iLO firmware upgrade on Lab-Uplands-G10: detected iLO 5 v3.03 on 10.10.8.110, matched `media/ilo5_319.fwpkg`, observed UpdateService `Updating -> Complete -> Idle`, and verified final live firmware `iLO 5 v3.19`.
2026-05-13 America/Toronto - Added matching planner/executor scaffolding for ONTAP and Cisco upgrades: ONTAP image upload/validate/start/poll helpers plus NetApp UI/routes, and Cisco SSH/SCP planner/executor plus UI/routes. Tested planners/UI with focused pytest; live ONTAP/Cisco execution still unproven.
2026-05-15 America/Toronto - Resumed the upgrade-helper/Cisco/iLO slice, restored iLO page upgrade actions plus live status through the upgrade gate panel, and verified the full pytest suite: 382 passed.
2026-05-15 America/Toronto - Decluttered the Cisco page into upgrade/access/findings/Run Center steps, added clearer next-blocker hero text, and expanded console verification output for switch IP/SSH/SCP proof.
2026-05-15 America/Toronto - Removed Cisco step number badges and changed Cisco action feedback from a large result card into compact inline text under the relevant workflow button group.
2026-05-15 America/Toronto - Routed Cisco action feedback to local inline messages across workflow, permission, advanced, and factory-reset blocks; suppressed the default top feedback message.
2026-05-15 America/Toronto - Disabled the shared receipt banner for Cisco pages so console/action feedback appears only inline under the relevant Cisco controls.
2026-05-15 America/Toronto - Simplified Cisco hero and workflow: removed Current step/most-important-finding/verify buttons, split Console access beside Current console config, and moved approval into a lower Run Center approval block.
2026-05-15 America/Toronto - Updated Cisco no-config flow: access credentials now appear before upgrade checks, version discovery tries console before SSH, console verification captures full show running-config, and Run Center approval no longer shows Preview plan.
2026-05-15 America/Toronto - Moved Cisco Switch-side proof to the bottom and reduced it to expandable show-run evidence only, removing route/status tiles and route diagnostic text from that section.
2026-05-15 America/Toronto - Regrouped Cisco Ports/config with Run Center approval and moved the Approve plan action to the bottom of the config section.
2026-05-15 America/Toronto - Reworked Cisco Switch Config and Run Approval section: removed baud/network/apply controls, added SNMP fields sourced from global config, limited section actions to Save to config and Approve config, and moved Findings to the bottom.
2026-05-15 America/Toronto - Updated Cisco access/current-config wording: added Apply Access Configs to Access Settings, renamed Switch-side proof to Current Switch Config, and standardized Cisco running-config reads to `show run`.
2026-05-15 America/Toronto - Scoped the top hero/status bar to the active setup page so Cisco no longer shows unrelated ONTAP/NetApp protocol blockers in its page header.
2026-05-15 America/Toronto - Made Cisco Approve config visibly report approved/blocked state inline, including first blocker text and explicit busy text on the approve action.
2026-05-15 America/Toronto - Fixed Cisco approval gate mismatch so Approve config accepts a non-blocking Upgrade Helper Cisco gate instead of requiring a stale local Cisco upgrade plan.
2026-05-15 America/Toronto - Deployed the local Windows Server 2022 OVF to standalone ESXi 192.168.1.202 as VM `win2022-01` on `datastore1`, added ESXi SSH OVF deployment fallback code, and left the VM powered off for first-boot review.
2026-05-15 America/Toronto - Working on app-wide simplification/polish slice. Claimed: `templates/index.html`, `templates/partials/main_content.html`, `templates/partials/components/precheck_summary.html`, `templates/partials/components/setup_strip.html`, `templates/partials/pages/storage.html`, `templates/partials/pages/ilo.html`, `templates/partials/pages/esxi.html`, `templates/partials/pages/windows.html`, `app/windows.py`, and related focused UI tests.
2026-05-15 America/Toronto - Simplified shared page hero/action feedback, collapsed bulky storage technical/readiness sections by default, surfaced Windows VM deployment status, and kept the Windows VMX PCI bridge hardening tweak.
2026-05-19 America/Toronto - Added NetApp upgrade activity reconciliation: when Lab Builder still shows ONTAP running, the activity endpoint checks `/api/cluster/software` and closes the saved job at 100% once ONTAP reports the target release completed.
2026-05-19 America/Toronto - Refined the ONTAP upgrade monitor UI: added compact panels/key-value cards, contained raw-output scrolling, silent background polling, structured ONTAP status parsing for upload/upgrade/giveback/mismatch states, and private CLI monitor command helpers.
