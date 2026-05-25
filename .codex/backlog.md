# 14-Hour Codex Backlog

## Priority 1: Full UI button and flow audit
- Inspect every page template.
- Identify every button, link, form submit, HTMX action, toggle, and JavaScript-triggered control.
- Verify each one has a valid backend route or frontend handler.
- Fix broken or dead buttons.
- Make button labels clearer where needed.
- Do not change large areas at once.

## Priority 2: Consistent logs/status placement
- Standardize where logs, last action, progress, and status appear on every setup page.
- Keep the location consistent across iLO, ESXi, storage, Cisco, QNAP, Windows, vCenter, configuration, and reports pages.
- Move long technical details to the technical/details page.
- Keep setup pages short and operator-friendly.

## Priority 3: Reduce clutter and improve ease of use
- Remove duplicate helper text.
- Move raw paths, stack traces, debug blocks, and oversized explanations away from setup pages.
- Make each page answer:
  1. what is this page for?
  2. what should I do next?
  3. what happened last?
- Improve spacing, grouping, and visual hierarchy without doing a full redesign in one pass.

## Priority 4: Safe codebase inspection
- Review the whole codebase for:
  - broken imports
  - stale routes
  - duplicated helpers
  - inconsistent module names
  - unsafe writes
  - missing validation
  - confusing state behavior
  - buttons that reset choices unexpectedly
  - logs that do not show up consistently
- Fix small safe issues as they are found.
- Create review notes for bigger risky issues instead of rewriting them immediately.

## Priority 5: Hardware-limited validation
- Only one switch and one server are available.
- Real hardware testing may only target:
  - the available switch
  - the available server
- For all other hardware modules, use:
  - dry-run tests
  - mocks
  - contract tests
  - page rendering tests
  - route tests
  - static inspection
- Do not invent unavailable hardware conditions.

## Priority 6: Tests and guardrails
- Add or update tests for changed logic.
- Add template/route tests for buttons where practical.
- Run focused tests after each change.
- Run the full test suite at stable checkpoints.
- Stop and report if tests fail.

## Priority 7: Documentation and run summary
- Keep a running summary of changes.
- Document what was tested with real hardware.
- Document what was only inspected, mocked, or dry-run tested.
- Document remaining risks clearly.
