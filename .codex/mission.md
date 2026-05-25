# Mission: 14-Hour Lab Builder Quality Run

You are improving the Lab Builder app over a long controlled run.

The main goal is not to add flashy new features. The main goal is to make the app reliable, clean, easy to use, and consistent across every page.

Primary goals:
1. Verify that every visible button, form, link, toggle, and action works as intended.
2. Make logs appear in the same consistent location and style on every setup page.
3. Reduce clutter on all setup pages.
4. Make the app easier for a non-technical operator to use.
5. Review the entire codebase for bugs, broken flows, duplicated logic, confusing naming, unsafe actions, and missing validation.
6. Improve obvious issues as they are found, but only in small tested changes.
7. Preserve existing working behavior.

Hardware limitation:
- During this run, only one switch and one server are available for real hardware testing.
- Do not assume access to NetApp, QNAP, extra ESXi hosts, vCenter, multiple servers, or any other hardware.
- Real hardware testing may only target the available switch and available server.
- For unavailable hardware, use mocks, dry-runs, contract tests, validation tests, template tests, and safe code inspection.

User experience rules:
- Setup pages must not feel cluttered.
- Each setup page should clearly show:
  1. what this page is for
  2. what to do next
  3. what happened last
- Logs/status should appear in the same part of each page.
- Technical logs, raw paths, debug traces, stack traces, and artifact details belong on the technical/details page.
- Buttons should have clear labels and predictable behavior.
- Do not reset user choices unexpectedly.
- Do not hide important warnings.
- Expand acronyms on first use.

Safety rules:
- Do not make destructive hardware changes without an explicit dry-run/preview or clear confirmation path.
- Do not store secrets in code.
- Do not print passwords or tokens in logs.
- Do not make real hardware calls in automated tests.
- Do not auto-push to main.
- Stop if tests fail and report the failure.

Current lab network note:
- The active test network for this 14-hour run is 192.168.1.0/24.
- Use subnet mask 255.255.255.0 for this active test network.
- Do not assume older 10.10.x.x test addresses are reachable unless they are explicitly present in the current kit config.
- For manual switch/server tests, prefer values from the active kit config. If values are missing, recommend 192.168.1.x addresses that do not conflict with gateway, workstation, switch management, server/iLO, or known reserved addresses.
- Do not overwrite existing kit IP conventions globally just because the current test network is 192.168.1.0/24.
