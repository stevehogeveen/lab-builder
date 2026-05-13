# Configuration (Global) Section

## What It Does
Captures shared kit identity, network/IP plan, included components, and baseline credentials.

## How It Works
Form submits map to handlers in `app/modules/configs/routes.py`; data is merged with defaults and persisted in kit YAML.

## How To Update
- Edit UI fields in `templates/partials/pages/configuration.html`.
- Update form parsing/validation/save behavior in `app/modules/configs/routes.py`.
- Adjust defaulting/normalization rules in `app/core/config.py`.

## Validate
Save a kit, reload it, and confirm values persist and derived IP plan behavior remains correct.

