# Windows Section

## What It Does
Stages Windows post-install/vSphere-related settings and validation steps.

## How It Works
Windows routes gather settings, perform probes, and feed execution review for Windows scope.

Windows source media now comes from the shared OVF Templates module. Register the full local OVF directory there first, then select the registered template on the Windows page.

Current execution remains a dry-run/safe path. The Windows stage records and validates install inputs but does not deploy or modify a VM yet.

The install planner stores a deployment preview that summarizes the source template, detected hardware metadata, sidecar count, target vSphere/ESXi endpoint, datastore, VM network, guest address, and the future import steps. Network mismatches between the OVF descriptor and saved VM network are warnings, not live changes.

## How To Update
- UI: `templates/partials/pages/windows.html`
- Route handlers: `app/modules/windows/routes.py`
- Service and platform calls: `app/modules/windows/service.py`, `app/windows.py`
- Schema: `app/modules/windows/schemas.py`

## Validate
Test local OVF registration, missing sidecar handling, probe endpoints, and Windows preview to confirm expected staged actions and validations.
