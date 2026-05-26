You are running an overnight Lab Builder preparation and hardening pass until 6:00 AM local time.

Important current condition:
- The operator is not connected to physical lab equipment tonight.
- Do not run real physical hardware actions tonight.
- Do not attempt to contact Cisco, NetApp, iLO, ESXi, or any real device tonight.
- All testing tonight must be mocks, fake clients, fake console sessions, dry-runs, route tests, template tests, unit tests, contract tests, and documentation/checklist work.
- The home/lab network for later testing will be 192.168.1.0/24.
- There will be no NetApp physically available tonight, but NetApp must be prepared for real testing tomorrow.

Primary prep scope:
- Cisco switch workflow
- NetApp workflow
- iLO workflow
- ESXi install/configuration workflow
- OVF/OVA workflow

Primary product goal:
Prepare the app so tomorrow's real hardware testing is clean, guided, and consistent.

The Cisco setup page is the model:
- clear guided setup flow
- current/discovered state separated from saved kit config
- clear next step
- clear last result
- consistent logs/status placement
- advanced details moved to Debug Mode/details
- minimal Operator Mode

Two UI modes:
1. Operator Mode:
   - pretty
   - simple
   - minimal
   - least amount of information needed to complete the job
   - clear next step
   - clear last result
   - consistent structure across Cisco, NetApp, iLO, ESXi, and OVF

2. Debug Mode:
   - detailed
   - useful for troubleshooting
   - logs
   - raw detected state
   - command/API output when safe
   - artifacts
   - test history
   - recovery suggestions

Hard UI consistency requirement:
Each physical setup page should clearly show:
1. What this page is for
2. What to do next
3. What happened last
4. Current completion state
5. A consistent place for logs/status
6. A clear way to open Debug Mode/details

Network note:
- Tomorrow/home lab network will be 192.168.1.0/24.
- Use 192.168.1.0/24 in examples, suggested defaults, docs, dry-run fixtures, and test scenarios where appropriate.
- Do not globally overwrite kit values if the saved kit explicitly uses another network.
- Clearly separate saved config, discovered/current state, and suggested values.

Cisco prep requirements:
- Prepare the factory-reset switch workflow for tomorrow.
- Use fake console/session tests only tonight.
- Support:
  - initial config dialog
  - forced enable secret after answering no
  - password policy validation
  - final setup menu choosing 0, never 2
  - normal CLI bootstrap after wizard fallback
  - completed Access Settings state
- Make manual real-switch test checklist clear for tomorrow.

NetApp prep requirements:
- No physical NetApp available tonight.
- Do not attempt real NetApp API, SSH, SP, or console access.
- Prepare the NetApp page for tomorrow's physical testing.
- Make NetApp workflow similar to Cisco operator flow:
  1. initial access/status
  2. SP/e0M/cluster/SVM management IP plan
  3. apply/verify management IPs
  4. verify SSH/API access
  5. discover controllers/nodes/interfaces/version
  6. validate readiness
  7. configure required settings
  8. upgrade readiness/action if available
  9. completed state
- Remove, hide, consolidate, or move redundant NetApp controls to Debug Mode.
- Do not delete useful diagnostics. Move them to Debug Mode/details.
- Use mocks/dry-runs and tests to prepare everything possible for tomorrow.
- Add/update a NetApp manual test checklist for tomorrow.

iLO prep requirements:
- Do not contact real iLO tonight.
- Prepare mock/dry-run coverage and UI consistency.
- Operator Mode clean.
- Debug Mode includes Redfish details/recovery guidance when available.

ESXi prep requirements:
- Do not run a real ESXi install tonight.
- Prepare dry-run/mock/template tests for ISO, kickstart, iLO virtual media, boot override, and install status clarity.
- Operator Mode clean.
- Debug Mode detailed.

OVF/OVA prep requirements:
- Prepare OVF/OVA template registration, selected template display, file/path validation, and deployment prep.
- If deployment requires unavailable infrastructure, use dry-run/mocks and show limitation clearly.

Testing rules:
- Automated pytest tests must never touch real hardware.
- No real serial, SSH, Redfish, ONTAP, ESXi, or vCenter calls tonight.
- Use fake clients and monkeypatching for all external calls.
- Keep tests green.

Code quality rules:
- Work in small focused changes.
- Prefer shared components/helpers for repeated page patterns.
- Remove functionality where it is not useful for completing the physical job.
- Do not delete useful debug capability. Move it into Debug Mode/details.
- Preserve existing working behavior unless clearly broken.
- Add/update tests for changed behavior.
- Commit only after pytest and compileall pass.
- Do not push automatically unless explicitly asked.

Time management:
- First priority: Cisco factory reset prep and checklist.
- Second priority: NetApp Cisco-style operator flow prep for tomorrow.
- Third priority: iLO consistency/mock prep.
- Fourth priority: ESXi/OVF prep.
- Near the end, stop starting big changes and focus on tests, stability, and handoff notes.

Before editing each cycle:
- Pick one focused task.
- State which page/flow is being worked on.
- State that tonight is mock/dry-run/test-only, not real hardware.
- List files expected to change.

After each cycle:
- Run focused tests where practical.
- Run python -m pytest -q.
- Run python -m compileall app.
- Commit if green.
- Write cycle notes into artifacts/codex-runs.
