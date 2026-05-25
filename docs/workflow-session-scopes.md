# Workflow Session Scopes

Purpose: let a short user prompt start a safe, focused Lab Builder session.

## Quick Prompt

The user can start a focused session with:

```text
I am going to be working with <workflow> this round. Use the operator flow contract.
```

Examples:

```text
I am going to be working with Cisco this round. Use the operator flow contract.
I am going to be working with NetApp this round. Use the operator flow contract.
I am going to be working with vCenter this round. Use the operator flow contract.
```

## Required Session Bootstrap

When that prompt is used:

1. Read `AGENTS.md`.
2. Read `SESSION_COORDINATION.md`.
3. Read this file.
4. Read `docs/operator-flow-contract.md`.
5. Read `docs/automation-principles.md`.
6. Read `docs/ux-product-principles.md`.
7. Run `git status --short --branch`.
8. Find the named workflow below.
9. Claim the workflow files in `SESSION_COORDINATION.md`.
10. If no active session entry exists for the workflow, create one from the template.
11. If another active session already owns the same files, stop and ask before editing.

Every workflow must follow:

`Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step`

## Shared Files

Avoid these unless the session explicitly claims them first:

- `app/main.py`
- `app/core/config.py`
- `app/modules/configs/routes.py`
- `templates/index.html`
- `templates/partials/sidebar.html`
- `templates/partials/main_content.html`
- `templates/partials/pages/configuration.html`
- `templates/partials/pages/dashboard.html`
- `templates/partials/pages/execution.html`
- `static/js/live-job.js`
- `tests/test_app.py`
- shared component templates under `templates/partials/components/`

## Workflow Scopes

### Cisco

Owned files:

- `app/cisco.py`
- `app/modules/cisco/**`
- `templates/partials/pages/cisco.html`
- `tests/test_cisco_*.py`

Focus:

- Console access, current switch state, desired switch config, upgrade gate, Run Center approval, monitor, and evidence.
- Use standard labels for current access, desired final, execution endpoint, and discovered reality.
- Do not create custom readiness logic that bypasses shared workflow contexts.

### NetApp

Owned files:

- `app/modules/netapp/**`
- `app/netapp.py`
- `app/netapp_console.py`
- `app/netapp_upgrade.py`
- `templates/partials/pages/netapp.html`
- `templates/partials/components/netapp_*`
- `tests/test_netapp_*.py`

Focus:

- Bootstrap, ONTAP API access, NFS/iSCSI plan, safe apply, monitor, and evidence.
- Keep planned manual bootstrap values separate from discovered ONTAP reality.

### iLO

Owned files:

- `app/ilo.py`
- `app/ilo_upgrade.py`
- `app/modules/ilo/**`
- `templates/partials/pages/ilo.html`
- `templates/partials/components/ilo_upgrade_activity.html`
- `tests/test_ilo_*.py`

Focus:

- Current iLO address, final iLO address, credentials, live read, policy plan, firmware gate, execution, logs, and evidence.

### Storage

Owned files:

- `app/modules/storage/**`
- `templates/partials/pages/storage.html`
- storage-related tests

Focus:

- Discovery before destructive planning, target identity, controller/drive proof, approval, reboot state, and verification evidence.

### ESXi And OVF

Owned files:

- `app/esxi/**`
- `app/stages/esxi/**`
- `app/ovf.py`
- `app/modules/esxi_install/**`
- `app/modules/esxi_config/**`
- `app/modules/ovf_templates/**`
- `templates/partials/pages/esxi.html`
- `templates/partials/pages/ovf_templates.html`
- ESXi/OVF tests

Focus:

- ESXi install target, ISO source, virtual media URL, management IP, root credentials, NetApp datastore dependency, OVF source readiness, target placement, plan, monitor, and evidence.

### vCenter

Owned files:

- `app/vcenter.py`
- `templates/partials/pages/vcenter.html`
- `tests/test_vcenter.py`

Focus:

- vCenter workflow context, target, credentials, VCSA media, ESXi placement, datastore, activity panel, evidence, and next step.
- vCenter must stay visible through shared dashboard, sidebar, page precheck, and Run Center patterns.

### Windows

Owned files:

- `app/windows.py`
- `app/modules/windows/**`
- `templates/partials/pages/windows.html`
- `tests/test_windows_*.py`

Focus:

- Source template, target placement, guest identity, vSphere credentials, WinRM credentials, dry-run plan, deployment monitor, and evidence.

### Kit Orchestration

Owned files:

- kit orchestration routes and helpers
- Run Center page
- dashboard readiness
- sidebar/navigation
- shared workflow context wiring
- `tests/test_app.py`

Focus:

- Consume module workflow contexts instead of inventing module-specific state.
- Show full kit sequence, blockers, run scope, current stage, evidence, and next step.
