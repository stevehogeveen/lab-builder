You are fixing a specific Cisco onboarding UX and workflow issue in Lab Builder.

Current context:
- The active branch is codex/14h-quality-run.
- The branch experience/operator-companion may contain earlier Cisco console bootstrap work.
- Inspect that branch or its relevant files if available before rebuilding similar logic from scratch.
- Current active lab network is 192.168.1.0/24.

Problem 1: Misleading IP/status display
The Cisco page can show IP/network information in one area, but below it shows that nothing is set. This is misleading.
Likely cause: the page is mixing discovered/current/console-entered values with saved kit configuration/precheck state without clearly labeling them.

Fix goal:
- Make the Cisco page clearly distinguish:
  1. Discovered/current switch state
  2. Saved Lab Builder kit config
  3. Values ready to apply
  4. Last action result/log
- If IP information is discovered but not saved, say that clearly.
- If saved config is missing, show "Not saved yet" instead of implying the discovered values do not exist.
- Add or verify a clear action such as "Use discovered values in this kit".
- Do not show contradictory status blocks.

Problem 2: Factory-reset Cisco initial setup wizard
The available switch may be at the very beginning Cisco assisted initial configuration page after factory reset.
The app must know how to traverse this over console.

Cisco console onboarding should handle states such as:
- "Would you like to enter the initial configuration dialog? [yes/no]:"
- "Would you like to terminate autoinstall? [yes]:"
- "Press RETURN to get started!"
- user exec prompt "Switch>"
- privileged exec prompt "Switch#"
- config prompt "Switch(config)#"
- interface config prompt
- login/password prompts
- unknown or timeout state

Fix goal:
- Add or improve a Cisco console bootstrap state machine/helper that can detect these prompts safely.
- For the Cisco assisted initial config dialog, answer "no" when appropriate, then continue to CLI setup.
- Build a clear manual/operator-triggered flow for factory-reset switch onboarding.
- The flow should configure only the available switch when the operator triggers it.
- It should support setting:
  - hostname
  - management VLAN/interface
  - management IP
  - subnet mask
  - default gateway
  - SSH
  - SCP if supported
  - local username/password if the app already supports it
- It should clearly log each step in the same page location as other Cisco action logs.
- It should never run from pytest against real hardware.

Constraints:
- Do not start unrelated 14-hour backlog work.
- Do not assume NetApp, QNAP, vCenter, or other hardware is available.
- Automated tests must use fake console/session objects only.
- Real switch actions must be manual/operator-triggered only.
- Preserve existing working Cisco SSH/serial behavior.
- Keep UI uncluttered and beginner-friendly.
- Add or update tests for:
  1. assisted setup dialog detection
  2. answering no to the initial config dialog
  3. discovered IP shown separately from saved config
  4. saved config missing state not contradicting discovered values
  5. no real serial/hardware calls in tests

Before editing:
- Inspect Cisco routes/templates/services/tests.
- Inspect experience/operator-companion for Cisco console bootstrap work if available.
- Report which files control the Cisco page, console bootstrap, and status display.

After editing:
- Run focused Cisco tests.
- Run python -m pytest -q.
- Run python -m compileall app.
- Do not commit.

Additional real Cisco setup wizard behavior:
- After the forced setup wizard password prompts, Cisco may show a final menu:
  - "0" = Go to the IOS command prompt without saving this config
  - "1" = Return back to the setup without saving this config
  - "2" = Save this configuration to NVRAM and exit
- Lab Builder must choose "0" so it exits to the IOS command prompt without saving the temporary wizard config.
- Lab Builder must not choose "2" during the wizard fallback.
- After reaching the IOS command prompt, Lab Builder should apply the intended full config through normal CLI commands and save at the end only after the normal CLI configuration succeeds.

Password policy observed on the available switch:
- Wizard/enable passwords must contain at least 10 characters.
- They must include at least:
  - 1 uppercase letter
  - 1 lowercase letter
  - 1 digit
- The Cisco page should validate the fallback wizard password/secret before running the manual setup.
- If the password does not meet this policy, show a clear validation error before touching the switch.
- Do not log the actual password.
- Tests should cover password policy validation:
  - rejects fewer than 10 characters
  - rejects missing uppercase
  - rejects missing lowercase
  - rejects missing digit
  - accepts a valid example
- Tests should cover final wizard menu handling:
  - sends "0" for "go to IOS command prompt without saving config"
  - never sends "2" during forced wizard fallback
  - continues normal CLI bootstrap after choosing "0"
