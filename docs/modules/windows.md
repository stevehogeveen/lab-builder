# Module: windows

## Purpose
Supports Windows-related setup, validation probes, and staged execution planning.

The module can register local OVA/OVF sources for install planning. Folder-based OVF templates should be registered by path so the descriptor, VMDK, NVRAM, and other referenced files stay in place and are validated before a dry-run plan is built.

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
