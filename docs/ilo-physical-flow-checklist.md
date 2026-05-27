# iLO Physical Flow Manual Checklist

Use this checklist only when the real HPE iLO/server is available and the operator intentionally starts each action from the Lab Builder UI. Automated pytest tests must keep using fake clients, fake inventory, route tests, and mocks.

The active home/lab network for examples is `192.168.1.0/24`. Suggested addresses are hints only; do not overwrite saved kit values when the kit explicitly uses another network.

## Preconditions

- Select the intended kit and confirm iLO/server setup is included.
- Confirm saved iLO access values are present or intentionally empty before using suggested values.
- Keep credentials in password fields only. Do not paste passwords, tokens, cookies, or private keys into logs or artifacts.
- Confirm the operator understands which actions are read-only and which actions can change power, reset iLO, or affect mounted media.

## Operator Mode Checkpoint

Before starting a real iLO action, confirm iLO Operator Mode shows:

- `Operator Mode`
- `Next step`
- `Completion state`
- `Last result`
- `Logs/status`
- `Open Debug Mode/details`

## Operator Flow Checklist

Use this exact sequence: `Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step`.

- [ ] Context: confirm the page states what iLO setup is for, the selected kit, included state, workflow state, current blocker, and next page/action.
- [ ] Targets: confirm current access, desired final address, execution endpoint, and discovered reality are shown with separate labels.
- [ ] Credentials: confirm saved username/password presence is visible without rendering raw secrets, and live access proof is separate from saved values.
- [ ] Current state read: manually run the iLO current-state/read action. Capture hostname, firmware/model, manager path, system path, PowerState, network state, DNS, and source timestamp.
- [ ] Preflight: confirm blockers and warnings are specific, including unreachable iLO, authentication failure, missing address, unsupported Redfish endpoint, or stale discovery.
- [ ] Plan: preview desired DNS, network, SNMP/settings if present, user/account policy, virtual media readiness, and power/reset expectations before applying anything.
- [ ] Execute: start real changes only by explicit operator action. Confirm destructive or disruptive actions are labeled before reset, reboot, power off, or virtual-media changes.
- [ ] Monitor: confirm logs/status show normalized phase, latest message, event rows, safe response summaries, and artifact identifiers.
- [ ] Evidence: confirm final evidence records what changed, what was skipped, what was blocked, what was verified, and where artifacts are stored.
- [ ] Next step: confirm the page points clearly to ESXi prep/install only after iLO connection, current state, virtual media, boot/power readiness, and required settings are acceptable.

## Hardware Checks

- [ ] Physical connection reaches the saved current iLO address from the Lab Builder host.
- [ ] Credentials authenticate successfully without secrets appearing in page output or artifacts.
- [ ] DNS/network settings read back separately from saved intent and suggested values.
- [ ] SNMP/settings read or preview appears when supported; unsupported optional settings are marked skipped with a recovery suggestion.
- [ ] Virtual media inventory shows available devices, insert/eject support, current inserted state, and Redfish path.
- [ ] Power state detection shows current `PowerState`, reset target, allowed reset types, and whether the requested action is safe.
- [ ] Safe reset/power flow is operator-triggered, clearly logged, and verified after reconnect or polling.

## Debug Mode Checklist

- [ ] Redfish endpoint details are available for manager, system, network, virtual media, boot override, and reset targets when discovered.
- [ ] Response summaries omit Authorization headers, cookies, session IDs, tokens, and raw passwords.
- [ ] Artifacts and test history are linked or named clearly.
- [ ] Recovery suggestions explain what was attempted, what was discovered, safe options, and the next manual fix.
- [ ] Raw detected state is available only where useful and safe.

## Automated Coverage Rules

- [ ] Route/template tests render Operator Mode and Debug Mode/details without contacting real iLO.
- [ ] Fake clients cover success, unreachable target, authentication failure, missing virtual media actions, unsupported DNS/SNMP, and stale discovery.
- [ ] Tests assert no secrets appear in logs, rendered pages, artifacts, or command/API summaries.
- [ ] Tests assert power/reset flows use discovered allowed reset types and stay manual/operator-triggered.

## Evidence To Capture

- Connection and credential result.
- Current-state read summary with Redfish source paths.
- DNS/network and optional SNMP/settings readback or skip reason.
- Virtual media readiness summary.
- Power state and allowed reset-type summary.
- Safe reset/power action result, only if manually triggered.
- Final last-result/logs/status screenshot or artifact reference.
