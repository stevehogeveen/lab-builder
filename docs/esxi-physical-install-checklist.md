# ESXi Physical Install/Prep Manual Checklist

Use this checklist only when the real iLO/server tied to the ESXi flow is available and the operator explicitly starts each action from Lab Builder. Automated pytest tests must keep using mocks, fake iLO inventory, dry-runs, route tests, and template tests.

The active home/lab network for examples is `192.168.1.0/24`. Use it for suggested defaults only when the selected kit has no explicit override.

## Preconditions

- Select the intended kit and confirm ESXi setup is included.
- Confirm iLO current access is saved and current iLO state has been read recently.
- Confirm the base ESXi ISO exists, the generated ISO output path is writable, and the public media URL is reachable from the Lab Builder host.
- Confirm ESXi root password and kickstart inputs satisfy the app's validation before build or install prep.
- Confirm the operator understands that physical install, virtual media changes, boot override, and power changes are manual/operator-triggered.

## Operator Mode Checkpoint

Before starting real ESXi install prep, confirm ESXi Operator Mode shows:

- `Operator Mode`
- `Next step`
- `Completion state`
- `Last result`
- `Logs/status`
- `Open Debug Mode/details`
- Media readiness only; detailed generated ISO paths, serving URLs, and virtual-media URLs belong in Debug Mode/details.

## Operator Flow Checklist

Use this exact sequence: `Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step`.

- [ ] Context: confirm the page states what ESXi setup is for, the selected kit, included state, workflow state, blocker, and next action.
- [ ] Targets: confirm ESXi management IP, hostname, ISO source, media readiness, iLO execution endpoint, and discovered iLO reality are shown separately in Operator Mode.
- [ ] Credentials: confirm saved ESXi root credentials and iLO credentials are represented without rendering raw secrets.
- [ ] Current state: confirm latest iLO read shows PowerState, virtual media state, boot override, manager/system paths, and source timestamp.
- [ ] Preflight: confirm blockers identify missing ISO, unreachable media URL, invalid password/kickstart, missing iLO access, unavailable virtual media action, unsupported boot override, or unsafe power state.
- [ ] Plan: preview build/mount/boot/power steps before changing anything. The plan must distinguish create, update, skip, manual, blocked, destructive, and read-only actions.
- [ ] Execute: start build, virtual media mount, boot override, power action, and install only from explicit operator controls.
- [ ] Monitor: confirm status/logs show normalized phase, latest message, progress where available, event rows, raw safe output, and artifact identifiers.
- [ ] Evidence: confirm final evidence records generated ISO path, media URL validation, virtual media mount proof, boot override readback, power transition, install reachability, and logs/artifacts.
- [ ] Next step: confirm the page points to post-install ESXi config or the exact fix if install readiness is incomplete.

## Physical Checks

- [ ] ISO selection/build path points to the intended base ISO and generated custom ISO.
- [ ] Generated kickstart uses the saved ESXi management IP, hostname, gateway, DNS, and root password policy without logging the password.
- [ ] Lab Builder can fetch the generated ISO URL before touching iLO virtual media or boot settings.
- [ ] iLO virtual media devices show insert/eject actions and current inserted media.
- [ ] Existing virtual media is ejected or left alone only according to the explicit plan and readback.
- [ ] Generated ISO is mounted and read back from iLO virtual media before boot override.
- [ ] Boot override is set to one-time CD/DVD or the supported equivalent and read back successfully.
- [ ] Power state handling uses discovered allowed reset types and is manually triggered.
- [ ] ESXi management reachability is monitored after boot, with likely causes shown if the host does not appear.

## Debug Mode Checklist

- [ ] Artifact details show base ISO, generated ISO path, virtual media URL, serving URL validation, build log, kickstart summary, and artifact identifiers.
- [ ] Latest iLO debug details show virtual media path, insert/eject counts, boot override, reset target, allowed reset types, and PowerState.
- [ ] Logs/status include safe command/API summaries and redact secrets.
- [ ] Recovery suggestions cover unreachable ISO URL, no virtual media action, stuck old media, unsupported boot override, failed power transition, and missing ESXi reachability.
- [ ] Raw output is available only when safe and useful.

## Automated Coverage Rules

- [ ] Route/template tests render Operator Mode and Debug Mode/details without touching iLO or ESXi.
- [ ] Fake inventory covers virtual media available, virtual media missing actions, boot override unavailable, server off/on, and unreachable media URL.
- [ ] Dry-runs validate ISO paths, kickstart inputs, password policy, and deployment limitations.
- [ ] Tests assert no ESXi root password or iLO credential appears in rendered pages, logs, artifacts, or raw output.
- [ ] Tests assert real install and power actions remain manual/operator-triggered.

## Evidence To Capture

- ISO selection/build result.
- Generated ISO path and serving URL validation.
- Kickstart/password validation result without secrets.
- Virtual media insert/eject readback.
- Boot override readback.
- Power state and reset-type readback.
- Install monitor result or explicit limitation/failure reason.
- Final last-result/logs/status screenshot or artifact reference.
