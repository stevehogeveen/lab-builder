# NetApp Tomorrow Manual Test Checklist

Use this checklist when a real NetApp is physically available. Do not run any real NetApp API, SSH, SP, serial, or console action during tonight's no-NetApp window. Tonight's preparation should stay limited to mocks, dry-runs, route tests, template tests, and checklist review.

Automated pytest tests for NetApp must use fake clients, mocks, dry-runs, route tests, template tests, or static contract tests. They must not call real ONTAP APIs, SSH, SP, serial, console, or storage endpoints.

The active home/lab network for examples is `192.168.1.0/24`. Treat the values below as suggestions only. Do not overwrite a saved kit that intentionally uses another network.

## Value Separation

Before any real action, confirm the page keeps these values separate:

- Saved kit config: values already persisted in Lab Builder for the selected kit.
- Discovered/current state: values read from ONTAP, SP, console, or API during the latest live check.
- Suggested values: helper defaults derived from the active lab network when saved kit config is missing.

## Shared Operator Flow Contract

Use the same flow as Cisco, iLO, ESXi, and OVF/OVA. Do not introduce a NetApp-only path for collecting targets, showing logs, reporting readiness, or presenting evidence:

Use this exact sequence: `Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step`.

1. Context: identify the selected kit, the no-real-NetApp boundary for tonight, and the real-hardware condition required tomorrow.
2. Targets: show SP, e0M/node management, cluster management, SVM management, and data LIF targets with saved, discovered/current, and suggested values separated.
3. Credentials: show whether required credentials are saved without rendering passwords, tokens, cookies, private keys, or SNMP communities.
4. Current State: read ONTAP, SP, serial, console, or API state only after the operator starts the real-hardware action tomorrow.
5. Preflight: verify reachability, authentication, required interfaces, ONTAP version, and upgrade prerequisites before planning changes.
6. Plan: preview management IP, protocol, NFS/iSCSI, required settings, and upgrade actions as create, update, skip, manual, blocked, destructive, or read-only.
7. Execute: require an explicit operator action for every real state-changing or destructive operation.
8. Monitor: keep logs/status in the consistent Debug Mode/details area and summarize only the current run state in Operator Mode.
9. Evidence: capture what changed, what was verified, what was skipped, what was blocked, and where artifacts are stored.
10. Next Step: show the single best next operator action after every success, warning, blocker, or failure.

## Operator Mode Checkpoint

Before using real hardware tomorrow, confirm NetApp Operator Mode shows the same compact checkpoint labels as the other physical pages:

- `Operator Mode`
- `Next step`
- `Completion state`
- `Last result`
- `Logs/status`
- `Open Debug Mode/details`

Use Operator Mode only for the least information needed to complete the job. Keep raw ONTAP state, command/API response detail, artifacts, test history, and recovery suggestions in Debug Mode/details.

## Suggested Lab IP Plan

For `192.168.1.0/24`, use the established Lab Builder NetApp conventions only as defaults when the kit has no explicit override:

| Role | Suggested value |
| --- | --- |
| Controller A SP | `192.168.1.13` |
| Controller B SP | `192.168.1.14` |
| Cluster management | `192.168.1.45` |
| Controller A e0M/node management | `192.168.1.46` |
| Controller B e0M/node management | `192.168.1.47` |
| SVM management | `192.168.1.48` |
| iSCSI LIF 1 | `192.168.1.51` |
| iSCSI LIF 2 | `192.168.1.52` |
| iSCSI LIF 3 | `192.168.1.53` |
| iSCSI LIF 4 | `192.168.1.54` |

## Cisco-Style Guided NetApp Flow

Tomorrow's NetApp page should guide the operator through these steps in order:

1. Initial access/status
2. SP/e0M/cluster/SVM management IP plan
3. Apply or verify management IPs
4. Verify SSH/API access
5. Discover controllers/nodes/interfaces/version
6. Validate readiness
7. Configure required settings
8. Upgrade readiness/action if available
9. Completed state

## Operator Flow Checklist

- [ ] Initial access/status: confirm the NetApp page states what the page is for, the selected kit, whether NetApp is included, the best next step, the current completion state, and the latest result.
- [ ] IP plan: confirm saved kit values, discovered/current values, and suggested `192.168.1.0/24` defaults are visibly separate for SP, e0M/node management, cluster management, SVM management, and iSCSI LIFs.
- [ ] Apply or verify management IPs: confirm destructive or state-changing actions are manual/operator-triggered, clearly labeled, and not expanded automatically by Debug Mode.
- [ ] SSH/API access: verify the page can test SSH and ONTAP API access only after target and credentials are explicit. Confirm no password, token, cookie, or private key appears in logs or artifacts.
- [ ] Discover: run live discovery only when the real NetApp is available. Capture controllers, nodes, interfaces, ONTAP version, management endpoints, and source timestamp.
- [ ] Readiness: confirm the readiness panel shows blockers, warnings, skipped optional items, and the exact next fix.
- [ ] Required settings: preview planned settings first. The plan must distinguish create, update, skip, manual, blocked, destructive, and read-only actions.
- [ ] Upgrade readiness/action: verify upgrade controls show the selected image/version, prerequisites, current ONTAP version, and manual approval boundary.
- [ ] Completed state: confirm final evidence shows what changed, what was skipped, what was blocked, what was verified, and where logs/artifacts are stored.

## Debug Mode Checklist

- [ ] Logs/status appear in a consistent Debug Mode/details area.
- [ ] Raw detected ONTAP state is available when safe and redacted where needed.
- [ ] Command/API response summaries are available without secrets.
- [ ] Artifacts and test history are linked or named clearly.
- [ ] Recovery suggestions explain the discovered problem, safe options, and the recommended next step.
- [ ] Redundant operator controls are hidden, consolidated, or moved into Debug Mode without deleting useful diagnostics.

## Dry-Run Work Allowed Tonight

- [ ] Route/template tests render the NetApp operator flow without contacting hardware.
- [ ] Fake clients cover SSH/API success, authentication failure, unreachable target, partial discovery, and readiness blockers.
- [ ] Dry-run fixtures use `192.168.1.0/24` suggestions only when saved kit values are absent.
- [ ] Tests assert saved kit config is not globally overwritten by suggested values.
- [ ] Tests assert no secrets appear in page logs, debug output, artifacts, or test output.

## Evidence To Capture Tomorrow

- Initial access/status result.
- SP/e0M/cluster/SVM/iSCSI IP plan with saved, discovered, and suggested values separated.
- SSH/API verification result.
- Discovery summary for controllers, nodes, interfaces, and ONTAP version.
- Readiness result and blockers, if any.
- Required settings plan and apply/verify result.
- Upgrade readiness result or explicit reason upgrade action was skipped.
- Final completed-state screenshot or artifact reference.
