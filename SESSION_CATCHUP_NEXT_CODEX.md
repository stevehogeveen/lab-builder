# Next Codex Session Catch-Up

Date: 2026-05-16 America/Toronto

## Current State

- Repo: `/home/administrator/lab-builder`
- Branch: `experience/operator-companion`
- Git state at shutdown: clean, branch pushed to `origin/experience/operator-companion`
- Latest pushed commit: `0582cec Add lens cockpit experience polish`
- Previous related commit: `1043d40 Make experience lens app-wide`
- Full regression after latest slice: `400 passed in 237.24s`

## Server Shutdown

- Stopped the user-owned Uvicorn reload server.
- Stopped the Docker container named `lab-builder`.
- Verified no listener on host ports `8000` or `8001` after shutdown.
- To bring the Docker app back: `docker start lab-builder`
- To run from the workspace instead: `.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

## What Was Just Added

- The experimental operator companion branch now has app-wide `Calm`, `Normal`, and `Expert` lenses.
- `Calm` minimizes the interface, closes details, hides logs/proof-heavy surfaces, and keeps the next action prominent.
- `Normal` is the default guided mode with proof one click away.
- `Expert` opens details and expands logs/proof surfaces, but explicitly dangerous panels can opt out.
- Added a visible lens cockpit with mode descriptions and shortcuts:
  - `Alt+1` Calm
  - `Alt+2` Normal
  - `Alt+3` Expert
- Added `data-lens-keep-closed` to the Cisco factory reset details panel so Expert mode does not auto-open destructive controls.
- Updated UX docs and `SESSION_COORDINATION.md`.

## Files Recently Touched

- `templates/index.html`
- `templates/partials/pages/cisco.html`
- `tests/test_app.py`
- `docs/ux-experimental-operator-companion.md`
- `docs/ux-product-principles.md`
- `SESSION_COORDINATION.md`

## Coordination Notes

- NetApp session ownership is still listed separately in `SESSION_COORDINATION.md`.
- The rest-of-app session owns non-NetApp UX work on `experience/operator-companion`.
- Before touching shared files, update `SESSION_COORDINATION.md`.
- Shared caution files still include:
  - `app/main.py`
  - `app/core/config.py`
  - `app/modules/configs/routes.py`
  - `templates/partials/pages/configuration.html`
  - `templates/partials/pages/execution.html`
  - `templates/partials/pages/dashboard.html`
  - `static/js/live-job.js`
  - `tests/test_app.py`

## Recommended Next Move

1. Start by reading this file and `SESSION_COORDINATION.md`.
2. Run `git status --short --branch` and confirm the branch is clean.
3. Decide whether to merge `experience/operator-companion`, refine it further, or cherry-pick specific UX pieces.
4. If continuing polish, good next slices are:
   - Add stable semantic classes for module technical/log/detail surfaces so lens behavior is less selector-based.
   - Add safe read-only command-palette actions, such as opening current config or checking current versions.
   - Create a per-page safe Expert allowlist so Expert can expand proof without exposing destructive controls.
   - Review mobile behavior of the lens cockpit and operator companion.

## Do Not Forget

- The app was intentionally shut down.
- The latest branch is pushed.
- Tests were green at shutdown.
- No backend route or workflow behavior was intentionally changed in the last slice.
