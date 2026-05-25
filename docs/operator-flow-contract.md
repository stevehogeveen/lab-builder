# Operator Flow Contract

Purpose: make every Lab Builder setup page feel like the same product even when the device-specific work is different.

Every workflow must expose this sequence:

1. Context
   Current kit, included state, workflow state, next blocker, and the page's place in the full build.

2. Targets
   Show the addresses and endpoints with the same labels everywhere:
   - Current access: where Lab Builder can connect right now.
   - Desired final: where the device or service should end up after setup.
   - Execution endpoint: what Run Center or the background worker will actually use.
   - Discovered reality: what the live device or service reported during the latest read.

3. Credentials
   Show whether required sign-in values are saved, where they come from, and whether live access has been proven. Never show raw secrets in logs, evidence, previews, or artifacts.

4. Current State
   Show the latest discovered state, source timestamp or artifact, and whether it matches saved intent.

5. Preflight
   Show required checks, blockers, warnings, and the exact next fix. Use the shared precheck summary where possible.

6. Plan
   Show what Lab Builder intends to change before it changes anything. The plan must distinguish create, update, skip, manual, blocked, destructive, and read-only actions.

7. Execute
   Show safe actions only after target, credentials, preflight, and plan are clear. Destructive actions must stay behind explicit confirmations and must not be expanded automatically by expert/detail modes.

8. Monitor
   Use a shared activity shape: normalized status, phase, progress, latest message, event rows, raw output, and job/artifact identifiers.

9. Evidence
   Show what changed, what was skipped, what was blocked, what was verified, and where the artifacts or logs are stored.

10. Next Step
   Show the one best page or action to continue the build.

## Standard States

Use these state meanings across modules:

- `not_started`: required setup has not begun.
- `discovered`: current state has been captured.
- `planned`: a dry-run or plan exists.
- `approved`: the plan is approved for Run Center or a real worker.
- `running`: a background action is active.
- `waiting_for_restart`: operator or device restart is needed.
- `validating`: post-action verification is running.
- `complete`: the workflow reached its verified terminal state.
- `failed`: the workflow needs operator attention.
- `stale`: saved approval no longer matches discovered state.

## Standard Log Tags

Use structured technician-style log lines:

- `[DISCOVER]` options, inventory, endpoints, and current state found.
- `[COMPARE]` desired intent compared with live state.
- `[REMAP]` safe corrections selected because identity still matched.
- `[DECISION]` selected action and reason.
- `[APPLY]` real change attempted.
- `[VERIFY]` readback or proof after action.
- `[SKIP]` safe no-op or unsupported optional action.
- `[BLOCKED]` unsafe condition, platform limitation, or missing input.

## Shared Components

Prefer existing shared surfaces before adding page-local UI:

- `templates/partials/components/precheck_summary.html`
- `templates/partials/components/setup_strip.html`
- `templates/partials/components/upgrade_components.html`
- `build_validation_checks`
- `build_workflow_contexts`
- `build_setup_precheck_summary`
- `build_page_precheck_summary`
- `build_action_feedback`
- `append_activity_event`

## Session Rule

Do not make a page merely better by itself. Make it conform to the shared operator flow so the next page feels familiar.
