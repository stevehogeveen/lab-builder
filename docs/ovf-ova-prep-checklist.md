# OVF/OVA Prep Manual Checklist

Use this checklist for OVF/OVA template registration and deployment-prep work tied to the available server. Registration and validation can be tested with local files, route tests, template tests, mocks, and dry-runs. Real deployment must stay manual/operator-triggered and only proceed when the required ESXi/vSphere target infrastructure is available.

Automated pytest tests for this flow must use local fixture files, fake clients, mocks, dry-runs, route tests, or template tests. They must not deploy VMs, mount media, call vSphere, or change ESXi state.

## Preconditions

- Select the intended kit and confirm the VM workflow that will consume the template.
- Confirm the source directory contains the `.ovf` descriptor and every referenced sidecar file, such as `.vmdk` and `.nvram`.
- Confirm whether the source is local Lab Builder storage, NetApp-backed storage, or another future source type.
- If the deployment target is unavailable, keep the workflow in dry-run/prep mode and show that limitation clearly.

## Operator Mode Checkpoint

Before starting real deployment prep, confirm OVF/OVA Operator Mode shows:

- `Operator Mode`
- `Next step`
- `Completion state`
- `Last result`
- `Logs/status`
- `Open Debug Mode/details`

## Operator Flow Checklist

Use this exact sequence: `Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step`.

- [ ] Context: confirm the page states what OVF/OVA setup is for, the selected kit, included state, current blocker, and next action.
- [ ] Targets: confirm selected template, source location, target VM workflow, and deployment target limitation are visible and separated.
- [ ] Credentials: confirm no vSphere/ESXi/NetApp credentials are required or displayed by template registration itself.
- [ ] Current state: confirm the selected template, descriptor path, file count, total size, readiness state, and last validation summary are visible.
- [ ] Preflight: confirm blockers identify missing descriptor, missing sidecar file, invalid path, unreadable directory, unsupported source location, or unavailable deployment target.
- [ ] Plan: confirm the page distinguishes local registration, source validation, VM workflow selection, dry-run deployment prep, blocked deployment, and future real deployment.
- [ ] Execute: register or revalidate the template only from explicit operator controls. Do not start real deployment from template registration.
- [ ] Monitor: confirm logs/status show registration result, validation summary, discovered file list, and artifact identifiers where available.
- [ ] Evidence: confirm final evidence records selected template, descriptor, discovered files, readiness blockers, validation errors, and dry-run/deployment limitation.
- [ ] Next step: confirm the page points to the consuming VM workflow or the exact source-file fix.

## Template Validation Checks

- [ ] Selected template display shows the registered name/id and source location.
- [ ] File/path validation catches nonexistent directories, missing `.ovf`, multiple descriptors when unsupported, missing referenced disks, and unreadable files.
- [ ] Discovered files include descriptor and referenced sidecar files.
- [ ] Validation errors are visible in Debug Mode/details without hiding the operator next step.
- [ ] NetApp-backed source remains blocked or dry-run when no real NetApp/infrastructure is available.
- [ ] Local server source is ready when all referenced files are readable.

## Debug Mode Checklist

- [ ] Debug Mode/details shows discovered files, descriptor path, referenced files, validation errors, readiness blockers, and source policy.
- [ ] Artifacts and test history are linked or named clearly when created.
- [ ] Recovery suggestions explain which file/path/source issue to fix next.
- [ ] No secrets appear in logs, page output, artifacts, or raw summaries.

## Automated Coverage Rules

- [ ] Route/template tests render selected-template, next step, last result, completion state, logs/status, and Debug Mode/details without deployment.
- [ ] Fake local folders cover valid template, missing descriptor, missing sidecar, and invalid path.
- [ ] Dry-run tests clearly show deployment limitations when target infrastructure is unavailable.
- [ ] Tests assert NetApp-backed source does not attempt real NetApp API, SSH, SP, serial, or console access.

## Evidence To Capture

- Registration result and selected template.
- Descriptor path and discovered sidecar files.
- Readiness summary and validation errors, if any.
- Source location policy and deployment-prep limitation.
- Link or reference to the consuming VM workflow.
- Final last-result/logs/status screenshot or artifact reference.
