# ESXi Section

## What It Does
Covers ESXi install prep and ESXi post-install host configuration in one setup area.

## How It Works
Install and config paths use separate modules (`esxi_install`, `esxi_config`) but share section UI and execution context.

## How To Update
- UI: `templates/partials/pages/esxi.html`
- Install behavior: `app/modules/esxi_install/*`, `app/stages/esxi/runtime.py`
- Config behavior: `app/modules/esxi_config/*`
- ISO/kickstart logic: `app/esxi/*`

## Validate
Verify preview content, media URL checks, and config save/apply behavior for both install and config flows.

