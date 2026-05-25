You are running one controlled cycle in the 14-hour Lab Builder quality run.

Read:
- .codex/mission.md
- .codex/rules.md
- .codex/backlog.md

Current mission:
Make the app cleaner, easier to use, safer, and more reliable.
The biggest focus is:
- verify that buttons and flows work properly
- make logs/status appear consistently on every page
- reduce clutter
- inspect the whole codebase for issues and potential improvements
- only use real hardware testing for the one available switch and one available server

Hard constraints:
- Do not start broad rewrites.
- Do not add big new features unless they are required to fix a broken flow.
- Do not touch unrelated files.
- Do not make destructive hardware changes.
- Do not assume access to NetApp, QNAP, vCenter, multiple ESXi hosts, or extra servers.
- For unavailable hardware, use mocks, dry-runs, contract tests, route tests, template tests, and code inspection.
- Real hardware testing is allowed only for the available switch and the available server.
- Do not commit unless tests pass.
- Do not push.

Pick exactly one small task from the backlog.

Before editing:
1. State the page/module/flow you are inspecting.
2. List the buttons/forms/routes/handlers involved.
3. Explain the current behavior.
4. Explain the intended improvement.
5. Identify the tests or checks you will run.

During editing:
1. Make the smallest safe change.
2. Prefer shared helpers/components for repeated UI patterns.
3. Keep logs/status in a consistent page location.
4. Keep setup pages uncluttered.
5. Move technical detail to the technical/details page.
6. Preserve current behavior unless it is clearly broken.
7. Add or update tests when practical.

Validation:
1. Run focused tests for the changed area.
2. If a frontend/template change cannot be fully tested, run route/template rendering tests if available.
3. Run python -m compileall app for Python syntax safety when appropriate.
4. If tests fail, fix the failure or stop and report exactly what failed.

At the end, report:
- task chosen
- files inspected
- files changed
- buttons/routes/forms verified
- tests/checks run
- whether real hardware was used
- what was mocked/dry-run only
- remaining risks
- recommended next task

Do not continue to another task in the same cycle.

Active lab network for this run:
- Current test network: 192.168.1.0/24
- Subnet mask: 255.255.255.0
- Do not assume 10.10.x.x addresses are reachable unless the current kit config says so.
- Manual switch/server test flows should use the current kit config first, then suggest safe 192.168.1.x values only when config values are missing.
- Do not globally rewrite stored customer/site conventions unless the user explicitly requests it.
