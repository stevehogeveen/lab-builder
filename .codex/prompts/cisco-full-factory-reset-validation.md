You are fixing and validating the Cisco full factory-reset onboarding flow in Lab Builder.

Hard scope boundary:
- Work only on Cisco-related files unless a shared helper is absolutely required.
- Do not edit NetApp, QNAP, vCenter, ESXi, Windows, iLO, storage, reports, dashboard, upgrade helper, or global workflow files unless a Cisco test proves it is required.
- Do not start broad 14-hour backlog work.
- Do not merge or cherry-pick other branches.
- Do not delete .codex files, session files, artifacts, or non-Cisco tests.
- Current active lab network is 192.168.1.0/24.

Real-world issue:
The available Cisco switch may be fully factory reset. The app must be able to drive the full console onboarding path from factory-reset state back to completed Cisco Access Settings.

Observed Cisco behavior:
- After factory reset, Cisco may show:
  - "Would you like to enter the initial configuration dialog? [yes/no]:"
  - "Would you like to terminate autoinstall? [yes]:"
  - "Press RETURN to get started!"
- On IOS XE 17.7.1 and later, even after answering "no" to the initial configuration dialog, Cisco may still require an enable secret before entering user EXEC mode.
- Password policy observed on the available switch:
  - at least 10 characters
  - at least 1 uppercase letter
  - at least 1 lowercase letter
  - at least 1 digit
- Cisco may then show the final setup menu:
  - "0" = Go to the IOS command prompt without saving this config
  - "1" = Return back to setup without saving
  - "2" = Save this configuration to NVRAM and exit

Required behavior:
1. Detect factory-reset/setup wizard states over console.
2. If asked "Would you like to enter the initial configuration dialog? [yes/no]:", send "no".
3. If Cisco still requires "Enter enable secret:" after "no", treat that as a known IOS XE forced-secret path.
4. Use operator-provided Cisco setup/fallback credentials from the page.
5. Validate the setup/fallback secret before touching the switch:
   - reject fewer than 10 characters
   - reject missing uppercase
   - reject missing lowercase
   - reject missing digit
6. Do not log real passwords or secrets.
7. When the final setup menu appears, choose "0" to go to IOS command prompt without saving the temporary wizard config.
8. Never choose "2" during the setup wizard fallback.
9. Once at IOS prompt, continue normal Lab Builder Cisco Access Settings configuration using CLI commands.
10. Save the final intended Lab Builder config only after normal CLI configuration succeeds.
11. Verify the switch reaches completed Access Settings state.
12. The page should clearly show:
    - Discovered/current switch state
    - Saved Lab Builder kit config
    - Values ready to apply
    - Last action result/log
13. Fix the misleading UI where IP information appears in one place but another area says nothing is set.
14. If discovered IP exists but saved config is missing, say "Discovered, not saved to this kit yet."
15. Provide or fix a button/action like "Use discovered values in this kit."

Manual test requirement:
- Codex should create/update a clear manual test checklist for a real factory-reset switch.
- The checklist must cover:
  1. fully factory-reset switch
  2. initial config dialog appears
  3. no-dialog path
  4. forced enable secret after no
  5. final wizard menu 0/1/2 appears
  6. choose 0
  7. normal CLI access reached
  8. Access Settings applied
  9. SSH verified
  10. final config saved
  11. page shows completed Access Settings

Automated tests:
- Use fake console/session objects only.
- Never touch real serial hardware from pytest.
- Add or update tests for:
  1. sends "no" to initial configuration dialog
  2. handles forced "Enter enable secret:" after "no"
  3. validates Cisco password policy
  4. rejects invalid fallback secret before hardware action
  5. sends "0" at final wizard menu
  6. never sends "2" during wizard fallback
  7. continues normal CLI bootstrap after choosing "0"
  8. discovered IP and saved kit config are shown separately
  9. saved config missing state does not contradict discovered values
  10. no real serial/hardware calls in tests

Before editing:
- Inspect only Cisco-related routes/templates/services/tests.
- Report which files control:
  - Cisco page
  - console bootstrap
  - Access Settings status
  - saved/discovered IP display
  - tests

After editing:
- Run focused Cisco tests.
- Run python -m pytest -q.
- Run python -m compileall app.
- Do not commit.
