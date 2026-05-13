# Windows Section

## What It Does
Stages Windows post-install/vSphere-related settings and validation steps.

## How It Works
Windows routes gather settings, perform probes, and feed execution review for Windows scope.

Windows source media supports two safe paths:

- Register a local `.ovf` or `.ova` path for large templates already on disk. This is the preferred flow for folder-based OVF exports because the app validates referenced sidecar files such as `.vmdk` and `.nvram` without copying multi-GB media into artifacts.
- Upload a small single-file `.ova` or `.ovf` through the browser when appropriate.

Current execution remains a dry-run/safe path. The Windows stage records and validates install inputs but does not deploy or modify a VM yet.

## How To Update
- UI: `templates/partials/pages/windows.html`
- Route handlers: `app/modules/windows/routes.py`
- Service and platform calls: `app/modules/windows/service.py`, `app/windows.py`
- Schema: `app/modules/windows/schemas.py`

## Validate
Test local OVF registration, missing sidecar handling, probe endpoints, and Windows preview to confirm expected staged actions and validations.
