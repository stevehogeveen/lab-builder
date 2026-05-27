# Cisco Factory-Reset Onboarding Manual Checklist

Use this checklist only with a real Cisco switch that is expected to be reset and rebuilt through the Lab Builder Cisco page. The active lab network for this run is `192.168.1.0/24`.

Automated pytest tests for this flow must use fake console sessions, fake clients, mocks, dry-runs, route tests, or template tests. They must not open real serial ports, start SSH sessions, or touch the physical switch.

## Preconditions

- Select the intended kit and confirm Cisco is included.
- Connect the Lab Builder host to the Cisco console adapter.
- Use operator-provided Cisco Access Settings on the page. Do not paste real secrets into logs or artifacts.
- Use a switch password and enable secret that satisfy the observed Cisco setup wizard policy:
  - at least 10 characters
  - at least 1 uppercase letter
  - at least 1 lowercase letter
  - at least 1 digit
- Confirm the desired switch IP, subnet mask, gateway, VLAN, domain, console port, baud, and management port mode are shown in Access Settings.

## Operator Mode Checkpoint

Before starting a real switch action, confirm Cisco Operator Mode shows:

- `Operator Mode`
- `Next step`
- `Completion state`
- `Last result`
- `Logs/status`
- `Open Debug Mode/details`

## Shared Operator Flow

Use this exact sequence for the factory-reset onboarding path: `Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step`.

## Factory-Reset Onboarding Path

- [ ] Start with a fully factory-reset switch, or issue the Cisco page factory reset action only after typing `FACTORY RESET`.
- [ ] When the switch shows `Press RETURN to get started!`, run Setup Console and confirm Lab Builder proceeds past it.
- [ ] When the switch shows `Would you like to enter the initial configuration dialog? [yes/no]:`, confirm Lab Builder sends `no`.
- [ ] Confirm the no-dialog path is handled when the switch proceeds directly to a normal IOS prompt.
- [ ] On IOS XE 17.7.1 or later, if the switch asks `Enter enable secret:` after `no`, confirm Lab Builder sends the page-provided fallback enable secret and does not show the secret in page logs.
- [ ] If the final setup wizard menu appears with choices `0`, `1`, and `2`, confirm Lab Builder chooses `0`.
- [ ] Confirm Lab Builder never chooses `2` during this temporary wizard fallback.
- [ ] Confirm the switch reaches normal IOS CLI access (`Switch>` followed by enable, or `Switch#`).
- [ ] Confirm Access Settings are applied through normal CLI commands after the wizard fallback exits.
- [ ] Confirm SSH is verified from the Lab Builder host to the saved Cisco management IP.
- [ ] Confirm the final intended Lab Builder config is saved only after CLI configuration succeeds.
- [ ] Confirm the Cisco page shows completed Access Settings with separate sections for discovered/current switch state, saved Lab Builder kit config, planned/suggested values, and last action result.

## Debug Mode Checklist

- [ ] Logs/status appear in Debug Mode/details, not as raw console output in Operator Mode.
- [ ] Raw console excerpts, command output, and setup wizard diagnostics are redacted before display.
- [ ] Artifacts and test history are linked or named clearly when created.
- [ ] Recovery suggestions explain the detected prompt, the last safe action, and the next manual fix.
- [ ] Passwords, enable secrets, SNMP secrets, tokens, cookies, and private keys do not appear in page logs, artifacts, command output, or test output.

## Evidence To Capture

- Console/bootstrap action result shows success without raw passwords or enable secrets.
- Current console config shows the expected management VLAN, switch IP, gateway, SSH, and SCP state.
- SSH test shows reachable for the saved Cisco management IP.
- Last action result shows the latest setup or verification result; raw log excerpts stay in Debug Mode/details.
- If a discovered IP exists while saved config is missing, the page says `Discovered, not saved to this kit yet.` and offers `Use discovered values in this kit`.
