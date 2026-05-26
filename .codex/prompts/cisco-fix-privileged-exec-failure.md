You are fixing a specific Cisco console setup failure in Lab Builder.

Observed real failure:
Cisco console setup failed:
"Cisco console did not reach privileged EXEC mode. Check enable password, login prompts, or initial setup dialog state."

The app logged:
- Management IP: 10.10.8.2
- Console: /dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller-D-if00-port0
- Detected Cisco initial configuration dialog; answered no.

Important network note:
- The Management IP shown by the app may be correct for the current test network.
- Do not assume 10.10.8.2 is stale or wrong.
- Do not change IP addressing logic unless a Cisco-specific test proves the displayed/saved/discovered state is misleading.

Root problem:
The Cisco state machine answers "no" to the initial configuration dialog, then expects to reach privileged EXEC mode.
On the real switch, answering "no" is not enough. Cisco may still force setup prompts before reaching CLI.

Required Cisco console behavior:
1. Detect:
   - "Would you like to enter the initial configuration dialog? [yes/no]:"
   Action:
   - send "no"

2. After sending "no", continue reading console output. Do not immediately expect privileged EXEC.

3. Handle forced setup prompts:
   - "Enter enable secret:"
   - "Enter enable password:"
   - "Enter virtual terminal password:"
   - confirmation/re-enter password prompts
   - password policy failures
   - "Would you like to terminate autoinstall? [yes]:"
   - "Press RETURN to get started!"

4. Password policy:
   - minimum 10 characters
   - at least 1 uppercase
   - at least 1 lowercase
   - at least 1 digit
   - validate before touching the switch
   - do not log passwords or secrets

5. Final setup menu handling:
   Cisco may show:
   - 0 = Go to the IOS command prompt without saving this config
   - 1 = Return back to setup without saving
   - 2 = Save this configuration to NVRAM and exit

   Required action:
   - send "0"
   - never send "2" in the wizard fallback
   - after choosing 0, wait for IOS CLI prompt

6. CLI prompt handling:
   - If prompt is "Switch>", send "enable"
   - If a "Password:" prompt appears after enable, send the operator-provided enable secret/password
   - If prompt is "Switch#", continue normal Lab Builder Access Settings configuration
   - If prompt is "Switch(config)#", recover to privileged/config flow as appropriate

7. The flow should end at completed Cisco Access Settings:
   - normal CLI commands apply hostname, management interface/VLAN, management IP, subnet mask, gateway, SSH/SCP if supported, and local user if supported
   - save final intended config only after normal CLI config succeeds
   - verify access where possible
   - page should show completed Access Settings

8. Improve the error message:
   Instead of only saying "did not reach privileged EXEC mode", include:
   - last detected prompt/state
   - last safe action taken, without secrets
   - whether initial dialog was answered no
   - whether forced setup wizard was detected
   - whether final menu was seen
   - whether Switch> was reached
   - whether Password: after enable was reached
   - next manual recovery step

Scope:
- Work only on Cisco-related files unless a shared helper is absolutely required.
- Do not edit NetApp, QNAP, vCenter, ESXi, Windows, iLO, storage, reports, dashboard, upgrade helper, or global workflow files.
- Do not start unrelated 14-hour work.
- Do not merge or cherry-pick other branches.
- Automated tests must use fake console/session objects only.
- No pytest test may touch real serial hardware.

Tests required:
- test initial dialog sends no and then continues reading
- test forced "Enter enable secret:" after no
- test password policy validation
- test final wizard menu sends 0
- test final wizard menu never sends 2
- test Switch> sends enable
- test Password: after enable sends configured secret
- test successful path reaches completed Access Settings
- test failure message includes last detected prompt/state
- do not add a test that assumes 10.10.8.2 is stale or incorrect

Before editing:
- Inspect Cisco console bootstrap/state-machine code.
- Inspect Cisco page/template status display.
- Inspect Cisco tests.

After editing:
- Run focused Cisco tests.
- Run python -m pytest -q.
- Run python -m compileall app.
- Do not commit.
