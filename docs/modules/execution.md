# Module: execution (Internal)

## Purpose
Implements Run Center actions: preview, confirmation-gated execute, retry, and artifact download.

## Key Files
- `app/modules/execution/routes.py`
- `app/core/jobs.py`
- `app/main.py` (runtime plumbing and background thread kickoff)
- `app/stages/*` (actual stage operations)

## Update Guide
- Update scope handling and confirmation logic in `routes.py`.
- Update run lifecycle state transitions in `app/core/jobs.py`.
- Keep execution/runtime helper names synchronized in `app/main.py`.

## Validation
Test preview, blocked execute cases, successful execute start, and download endpoints.

