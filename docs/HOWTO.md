# HOWTO: Use Lab Builder End To End

## 1) Install Dependencies

Lab Builder has three dependency layers:

1. OS tools used by automation workflows.
2. Python packages listed in `requirements.txt`.
3. Local runtime media and generated artifacts that stay out of git.

On Ubuntu/Debian-style hosts:

```bash
sudo apt update
sudo apt install -y python3 python3-venv xorriso sshpass
```

Python environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Optional but recommended for browser/UI sanity checks:

```bash
.venv/bin/playwright install chromium
```

Dependency notes:

- `xorriso` is required by ESXi ISO customization.
- `sshpass` is used by the ESXi live SSH transport when password-based root login is configured.
- Python packages such as FastAPI, Requests, Paramiko, pyVmomi, pywinrm, pytest, and Playwright are pinned in `requirements.txt`.
- Local ESXi ISOs, OVA/OVF files, and VMDK files belong under `media/`; that directory is intentionally ignored by git.
- Runtime outputs under `artifacts/` and kit configs under `config/kits/` are intentionally ignored because they are operator/local state.

## 2) Start The App

```bash
./scripts/start-app-dev
```

Open `http://localhost:8000`.

Use `./scripts/start-app` for non-reload mode.

## 3) Create Or Load A Kit

1. Go to Global/Configuration.
2. Create a new kit or load an existing one.
3. Save global settings (site, subnet, included systems, credentials).

Kit files are stored in `config/kits/`.

## 4) Complete Setup Sections

Work through enabled setup pages in order:

1. iLO
2. Storage
3. ESXi
4. Windows (if enabled)
5. Extended modules (NetApp/Cisco/QNAP) if enabled

For each section: save settings, run preview/check actions, resolve validation warnings.

### Windows OVF/OVA Deployment Notes

- Register the full OVF directory from OVF Templates first; ESXi needs the descriptor plus referenced VMDK/NVRAM sidecars.
- For standalone ESXi hosts, `scripts/deploy_windows_ovf_to_esxi.py` can deploy the selected Windows OVF source using the current kit ESXi settings.
- The script first tries VMware NFC import through pyVmomi. If the host rejects that upload path, it falls back to SSH/SCP upload, `vmkfstools -i` conversion, VMX creation, and `vim-cmd solo/registervm`.
- The fallback leaves the VM powered off unless the caller explicitly requests power-on in code.

## 5) Run From Run Center

1. Open Run Center (`/execution`).
2. Select a scope (single stage or included multi-stage path).
3. Use preview first and review planned actions.
4. For real execution: check confirmation checkbox and type `EXECUTE`.
5. Start execution and monitor live job logs/status.

## 6) Review Outputs And Reports

- Use Reports page for config snapshots, run summaries, and exports.
- Key artifact areas:
  - `artifacts/runs/`
  - `artifacts/history/`
  - `artifacts/exports/`
  - `artifacts/generated/`

## 7) Troubleshoot Failures

1. Check Run Center job logs and latest report output.
2. Open debug bundle:
   - `artifacts/debug-bundles/latest-failure.txt`
3. Review operation-specific docs:
   - `docs/esxi-operations.md`
   - `docs/automation-principles.md`
   - `docs/debugging.md`

## 8) How To Update The App Safely

### Add/Update A Module

1. Edit `app/modules/<name>/routes.py` for endpoint and handler behavior.
2. Edit `service.py` for domain logic and external API interactions.
3. Edit `schemas.py` when request/response contracts change.
4. Update `manifest.yml` for navigation/enablement metadata.
5. Confirm module is loadable via `register_module_routes(app)`.

### Add/Update A Section UI

1. Edit page partial in `templates/partials/pages/`.
2. Update route handler context in module routes or `app/main.py` helper wiring.
3. Verify sidebar/nav state remains accurate.

## 9) Validation Checklist After Changes

1. Start app and load a test kit.
2. Confirm modified section renders and saves correctly.
3. Confirm preview path runs with expected output.
4. Confirm execution gating still requires explicit confirmation.
5. Run tests:

```bash
pytest -q
```

For a broader local sanity pass:

```bash
./scripts/health-check
```
