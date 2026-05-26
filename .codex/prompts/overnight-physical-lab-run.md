You are running an overnight Lab Builder physical-equipment hardening pass until 6:00 AM local time.

Primary physical test scope:
- Cisco switch
- NetApp
- HPE iLO
- ESXi install/configuration flow
- OVF/OVA template handling and deployment prep

Primary product goal:
Make the physical setup pages consistent, clean, and useful.

The Cisco setup page is the model:
- clear guided access/setup flow
- current state separated from saved kit config
- clear next step
- clear last result
- logs/status in a consistent place
- advanced details available in Debug Mode/details
- no clutter on the operator path

The NetApp page must be redesigned to follow the same operator pattern as Cisco:
- Get initial access
- Assign/verify management IPs
- Verify SSH/API access
- Discover current state
- Configure required settings
- Prepare/perform upgrade if needed
- Show completed state

NetApp-specific intent:
- The NetApp page currently has too much redundant/cluttered functionality.
- Remove, hide, consolidate, or move redundant NetApp controls into Debug Mode.
- Do not delete useful troubleshooting capability; move it to Debug Mode/details.
- Operator Mode should show the least amount of information needed to complete setup.
- Debug Mode should show raw discovery, API responses, logs, routes, artifacts, and troubleshooting detail.
- NetApp setup should feel similar to Cisco: guide the operator from first access to completed configuration.
- If NetApp initial setup requires serial/SP/cluster setup rather than a Cisco-style console, represent that honestly in the UI.
- Do not pretend NetApp is Cisco, but make the workflow experience consistent.

Two UI modes:
1. Operator Mode:
   - pretty
   - simple
   - minimal
   - least amount of information needed to complete the job
   - clear next step
   - clear last result
   - consistent structure across Cisco, NetApp, iLO, ESXi, OVF

2. Debug Mode:
   - detailed
   - useful for troubleshooting
   - logs
   - raw detected state
   - command/API output when safe
   - artifacts
   - test history
   - recovery suggestions

UX rules:
- Every physical setup page should clearly show:
  1. What this page is for
  2. What to do next
  3. What happened last
  4. Current completion state
  5. A consistent place for logs/status
  6. A clear way to open Debug Mode/details
- Use progressive disclosure: hide advanced/debug detail until needed.
- Remove duplicate buttons, repeated explanations, and conflicting status blocks.
- Separate discovered/current state from saved kit config from values ready to apply.
- Do not show contradictory messages like "IP found" and "nothing set" without explaining the difference.
- Make destructive actions clearly labeled and manual/operator-triggered.

Physical testing rules:
- Real physical tests are allowed only for the available Cisco switch, NetApp, iLO/server, ESXi target, and OVF/OVA workflow tied to that equipment.
- Destructive real hardware actions are allowed only when manual/operator-triggered and clearly labeled.
- Automated pytest tests must never touch real hardware.
- Automated tests must use fake clients, fake console sessions, mocks, dry-runs, route tests, or template tests.
- Do not log passwords, secrets, tokens, or private keys.

Cisco requirements:
- Fully test factory-reset switch onboarding.
- Handle the Cisco initial setup dialog.
- Handle IOS XE forced enable secret path after answering no.
- Validate Cisco password policy.
- At the final setup wizard menu, choose 0, never 2.
- End at completed Access Settings.

NetApp requirements:
- Simplify the NetApp operator page so it mirrors the Cisco guided setup structure.
- Identify redundant/stupid/duplicated NetApp controls and either remove them, consolidate them, or move them to Debug Mode.
- NetApp Operator Mode should guide:
  1. Physical/management access status
  2. SP/e0M/cluster/SVM management IP plan
  3. Apply or verify management IPs
  4. Verify SSH/API access
  5. Discover controllers/nodes/interfaces/version
  6. Validate readiness
  7. Configure required settings
  8. Upgrade readiness/upgrade action if available
  9. Completed state
- Use existing Lab Builder NetApp IP conventions where available:
  - Controller A SP offset .13
  - Controller B SP offset .14
  - cluster management .45
  - Controller A e0M/node management .46
  - Controller B e0M/node management .47
  - SVM management .48
  - iSCSI LIFs commonly .51-.54
- Do not globally hard-code those conventions if kit config overrides them.
- Show conventions as suggestions/defaults, not hidden magic.
- Debug Mode should include raw ONTAP/API/SSH details, route info, discovered state, and recovery guidance.
- If a NetApp action cannot be tested physically tonight, create dry-run/contract/template tests and clearly mark it as not physically tested.

iLO requirements:
- Test physical iLO connection, credentials, current state read, DNS/network settings, SNMP/settings if present, virtual media readiness, power state detection, and safe reset/power flow.
- Operator Mode should be clean.
- Debug Mode should include Redfish details and recovery guidance.

ESXi requirements:
- Test ESXi preparation/install workflow against the available server.
- Validate iLO virtual media mount, boot override, power state handling, ISO selection/build path, kickstart/password validation, and install status/log clarity.
- Physical install must be manual/operator-triggered and clearly logged.

OVF/OVA requirements:
- Test OVF/OVA template registration, selected template display, file/path validation, and deployment prep.
- If deployment requires unavailable infrastructure, use dry-run/mocks and show limitation clearly.

Code quality rules:
- Work in small focused changes.
- Prefer shared components/helpers for repeated page patterns.
- Remove functionality where it is not useful for completing the physical job.
- Do not delete useful debug capability; move it into Debug Mode/details.
- Preserve existing working behavior unless it is clearly broken.
- Add/update tests for changed behavior.
- Keep tests green.
- Commit only after pytest and compileall pass.
- Do not push automatically.

Time management:
- First priority: Cisco factory reset to completed Access Settings.
- Second priority: NetApp page simplification and guided setup structure.
- Third priority: iLO physical flow consistency.
- Fourth priority: ESXi/OVF physical/prep flow consistency.
- Near the end of the run, stop starting big changes and focus on tests, stability, and handoff notes.

Before editing each cycle:
- Pick one focused task.
- State which page/flow is being worked on.
- State whether real hardware, dry-run, mock, or template/route tests will be used.
- List files expected to change.

After each cycle:
- Run focused tests where practical.
- Run python -m pytest -q.
- Run python -m compileall app.
- Commit if green.
- Write cycle notes into artifacts/codex-runs.
