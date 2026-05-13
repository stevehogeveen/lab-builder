# Module: windows

## Purpose
Supports Windows-related setup, validation probes, and staged execution planning.

The module selects a registered OVF template for install planning. Folder-based OVF templates are owned by the OVF Templates module so the descriptor, VMDK, NVRAM, and other referenced files are validated as one reusable directory. The Windows dry-run plan includes a deployment preview with source metadata, target placement, network mapping, and future import steps.

## Key Files
- `app/modules/windows/manifest.yml`
- `app/modules/windows/routes.py`
- `app/modules/windows/service.py`
- `app/modules/windows/schemas.py`

## Update Guide
- Page handlers and probe actions: `routes.py`
- Platform/service integration and OVF source inspection: `service.py`, `app/windows.py`
- Schema changes: `schemas.py`

## Validation
Verify local OVF registration, missing sidecar warnings, probe endpoints, and Windows execution review output. Windows execution is still safe/dry-run and does not deploy a VM.
