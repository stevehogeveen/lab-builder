# Lab Builder Failure Guide and Lessons Learned

This document summarizes the major failures we have already hit in this project, what was fixed, and how to debug quickly when it happens again.

Audience:
- Operators who want plain language.
- Engineers who need exact files/functions to inspect.


## 1) Big Picture: How This App Works

The app is a FastAPI server with page templates and background run jobs.

Core flow:
1. Save kit settings (Global, iLO, Storage, ESXi).
2. Run from Run Center using a scope (`ilo`, `storage`, `esxi`, or `multi__...`).
3. Background runner writes state into `artifacts/jobs/<kit>_job.yml` and run bundles in `artifacts/runs/<kit>/<run-id>/`.
4. Live page streams job updates over WebSocket.

Key code:
- Main orchestration: `app/main.py`
- iLO/Redfish client and storage methods: `app/ilo.py`
- ESXi ISO build and kickstart: `app/esxi/builder.py`, `app/esxi/kickstart.py`
- Live job frontend stream: `static/js/live-job.js`
- Run Center UI: `templates/partials/pages/execution.html`
- Tests: `tests/test_app.py`


## 2) Failures We Saw and What Fixed Them

## A. Whole run selected, but ESXi did not run

What users saw:
- Run looked complete after iLO/storage.
- ESXi never launched.

Root cause:
- `included` scope was collapsing into `ilo` only in execution launch logic.

Fix:
- Whole-run execution now expands to multi-stage scope (for example `multi__ilo__storage__esxi`) based on included stages.

Where fixed:
- `app/main.py` (run-scope normalization and launch option building).
- `templates/partials/pages/execution.html` (review now shows included stages clearly).

How to detect:
- In run summary, check `scope` in `artifacts/runs/.../summary.yml`.
- If it is `ilo` when you expected full run, scope resolution is wrong.


## B. One-time boot override did not stick reliably

What users saw:
- ISO mounted, but server boot did not follow expected one-time target.

Root cause:
- App previously treated PATCH attempt as success without enough readback proof.

Fix:
- Boot override now captures before/after state and boot option inventory.
- Later changed to best-effort (not hard blocker) on known hardware where ISO can still boot.

Where fixed:
- `app/ilo.py` (boot override helper and readback details).
- `app/main.py` (ESXi real-run boot orchestration and persistence into run bundle).

How to detect:
- In ESXi run bundle check fields like:
  - `after_target`
  - `after_uefi_target`
  - `selected_boot_option_reference`
  - boot option inventory metadata


## C. ESXi run failed due ISO URL/path confusion

What users saw:
- “Built ESXi ISO not found”
- HTTP path mismatches during virtual media mount.

Root cause:
- Mismatch between built ISO file location and URL mapping/assumptions.

Fix:
- Hardened ISO self-checks and virtual media verification.
- Improved URL/path handling and HEAD/access behavior around built ISO route.

Where fixed:
- `app/main.py` (ESXi build + mount path checks).
- ESXi-related route behavior (same file).

How to detect:
- Compare in run summary:
  - local built path
  - virtual media URL used
- Validate URL manually with `curl` GET (not only `HEAD`, since some routes allow only GET).


## C2. ESXi virtual media POST disconnects

What users saw:
- ESXi run stopped at `EjectMedia` or `InsertMedia`.
- Error looked like:
  - `Connection aborted`
  - `RemoteDisconnected('Remote end closed connection without response')`
- Follow-on error after continuing too early:
  - `iLO.2.25.MaxVirtualMediaConnectionEstablished`

Root cause:
- Some iLO virtual media actions can accept the Redfish POST and close the HTTP connection before returning a normal response.
- The app treated the transport disconnect as a hard failure before checking live virtual media state.
- If eject did not actually remove the previous image, a later `InsertMedia` could fail because iLO still had an active virtual media connection.

Fix:
- ESXi virtual media eject/insert now treats transport disconnects as uncertain, not immediately fatal.
- After a disconnect, the app reconnects to iLO and reads back virtual media state.
- If eject readback shows media removed, eject is treated as successful.
- If insert readback shows the generated ISO mounted, mount is treated as successful.
- If eject readback still shows inserted media, the app retries eject once with a fresh iLO connection.
- If `InsertMedia` returns `MaxVirtualMediaConnectionEstablished`, the app ejects stale media and retries `InsertMedia` once.
- If eject actions still leave media inserted, the app tries the observed iLO-compatible clear operation:
  `PATCH VirtualMedia/N {"Image": null, "Inserted": false}`.
- If state still does not match, readback validation blocks the run with a clear error.

Where fixed:
- `app/main.py` (ESXi real-run virtual media orchestration).

How to detect:
- In live logs, look for:
  - `iLO closed the Eject media connection without a response`
  - `iLO closed EjectMedia without a response, but virtual media readback shows it ejected`
  - `iLO closed InsertMedia without a response, but virtual media readback matches the generated ISO`
  - `maximum virtual media connection is already established`
  - `Previous virtual media cleared with Redfish PATCH fallback`


## D. WebSocket crashes on partial job YAML writes

What users saw:
- Live page crashes with YAML parser exception.
- Stack traces from `yaml.safe_load` during live updates.

Root cause:
- WebSocket reader hit partially written job/history files.

Fix:
- Hardened live-job reads and error handling.
- Added resilience around partial writes and cancellation on shutdown.

Where fixed:
- `app/main.py` (job/history load and websocket streaming).
- `static/js/live-job.js` (client-side resiliency behavior).

How to detect:
- If traceback shows scanner/parser error in `artifacts/jobs/<kit>_job.yml`, this class of issue is active.


## E. Gen11 / Gen10+ storage controller not detected correctly

What users saw:
- Drives visible, controller missing or wrong path used.
- Storage apply failed deep in flow.

Root cause:
- Storage normalization assumed specific older controller shapes.
- SmartStorageConfig path guessed as writable even when it was not.

Fix:
- Better standard Redfish storage normalization.
- Fallback controller extraction from storage members when controller arrays are empty.
- Verified-path backend selection: do not use SmartStorageConfig unless writable settings path is actually verified.
- Added standard Redfish `Volumes` apply backend for MR controller families.

Where fixed:
- `app/ilo.py` (normalization and storage backend helpers).
- `app/main.py` (backend selection and run-time platform decision).

How to detect:
- In discovery artifacts, inspect:
  - `probed_paths`
  - controller paths
  - whether writable settings URI exists
- If writable SmartStorageConfig settings URI is absent, backend must not choose that path.


## F. Storage 401 `NoValidSession` errors

What users saw:
- Storage read/apply fails with:
  - `Base.1.18.NoValidSession`
  - 401 on `/redfish/v1/Managers` or related GET calls.

Root cause:
- Expired/invalid Redfish session token with no transparent recovery for reads.

Fix:
- `ILOClient` now manages a `requests.Session`.
- On GET 401 `NoValidSession`, client re-authenticates with Redfish SessionService and retries read once.

Where fixed:
- `app/ilo.py` (`ILOClient` request/session lifecycle).

How to detect:
- Look for 401 + `NoValidSession` in run logs.
- If present, inspect whether re-auth happened and second GET succeeded.


## G. Wrong machine identity shown on Storage page

What users saw:
- Storage page showing Gen11 snapshot while working on Gen10+ kit.

Root cause:
- Fallback logic pulled latest global export instead of kit/host-matched snapshot.

Fix:
- Snapshot loading now constrained to current kit/current host match.
- No cross-kit leak for storage/live inventory identity.

Where fixed:
- `app/main.py` (snapshot load and identity building helpers).

How to detect:
- Compare displayed model/serial/IP with active kit iLO host in config.
- If mismatch appears, check snapshot matching logic first.


## H. ESXi hostname/password failures discovered late

What users saw:
- Save succeeds, later run fails due invalid server name/password policy mismatch.

Root cause:
- Validation happened too late.

Fix:
- Added save-time validation and run-time gating.
- Added inline per-field error state (red field + reason above input).

Where fixed:
- `app/main.py` (validation and route guards).
- `templates/partials/pages/esxi.html` and shared CSS in `templates/index.html`.

How to detect:
- Invalid values should block save and show immediate inline errors.


## 3) What To Check First When A Run Fails

Use this order every time:

1. Confirm run scope:
   - Open `artifacts/runs/<kit>/<run-id>/summary.yml`
   - Verify expected scope (`multi__ilo__storage__esxi` for full run).

2. Read live log inside run bundle:
   - `artifacts/runs/<kit>/<run-id>/live-job.log`
   - Find first `[FAILED]` line and first warning before it.

3. Check active job state:
   - `artifacts/jobs/<kit>_job.yml`
   - Validate stage, progress, and last known status.

4. If failure is storage-related:
   - inspect discovery export and plan snapshot paths referenced in run summary.
   - verify controller path and backend chosen.

5. If failure is ESXi-related:
   - verify built ISO path exists.
   - verify virtual media URL is reachable from iLO point of view.
   - inspect boot evidence logs and post-power polling lines.

6. If failure is iLO/session-related:
   - look for `NoValidSession`, 401, or endpoint mismatch after iLO IP change.


## 4) “Simple Mind” Explanation of Why These Failures Happened

Most failures were not random. They came from one of four classes:

1. State mismatch:
   - App thought it was working on one server, but data came from another snapshot.

2. Capability assumption:
   - App assumed a controller or firmware supports path X without proving it.

3. Timing/atomicity:
   - Live readers touched files while they were being written.

4. Session lifecycle:
   - Long operations outlived auth tokens.

If you remember only one rule:
- Never trust assumptions; trust verified readback from the current run on the current host.


## 5) Exact Fix Surfaces by Problem Type

Run orchestration and stage ordering:
- `app/main.py`
- Search for scope normalization, execution launch options, and `run_*_real` functions.

iLO request/auth/session behavior:
- `app/ilo.py`
- Search for `ILOClient`, session creation, `_get`, and request wrappers.

Storage backend/platform selection:
- `app/main.py` (platform chooser)
- `app/ilo.py` (backend operations and discovery normalization)

ESXi build and boot behavior:
- `app/main.py` (`run_esxi_real`)
- `app/esxi/builder.py`
- `app/esxi/kickstart.py`

UI validation and error rendering:
- `templates/index.html` (shared error styles)
- `templates/partials/pages/esxi.html`
- `templates/partials/pages/ilo.html`
- `templates/partials/pages/configuration.html`
- validation builders in `app/main.py`

Live updates and run visibility:
- `static/js/live-job.js`
- websocket route and job loaders in `app/main.py`


## 6) Lessons Learned (Project Level)

1. Validate early, fail early:
   - block invalid values at save time, not in the middle of long runs.

2. Readback is mandatory:
   - after write calls, always fetch and verify resulting state.

3. Use capability detection, not generation labels:
   - “Gen10/11/12” is not enough.
   - choose backend from proven writable endpoints.

4. Keep stage boundaries explicit:
   - each stage should have a finished signal and a clear handoff condition.

5. Treat logs and bundles as first-class product features:
   - every failure fix got faster once run bundles included clear evidence.

6. Avoid hidden cross-kit fallback behavior:
   - isolation by kit and host prevents misleading UI.


## 7) Recommended Next Hardening Passes

1. Multi-controller selection and persistence:
   - ensure operator-selected controller is persisted end-to-end in discovery, plan, apply, and run review.

2. Per-step verification receipts:
   - at stage completion, persist a compact “expected vs observed” receipt for iLO, storage, ESXi.

3. Retry policy matrix:
   - document which calls are safe to retry (idempotent GET) vs unsafe (writes).
   - keep this explicit in code comments near request wrappers.

4. Add one run bundle index:
   - one summary file that points to all artifacts and key evidence lines for fastest triage.


## 8) Quick Incident Template (Use During Debug)

When filing a failure, capture this:

1. Kit name and run id.
2. Expected scope vs actual scope.
3. First failure line from `live-job.log`.
4. Last successful verification line before failure.
5. Controller/backend chosen (for storage failures).
6. Boot evidence + virtual media status (for ESXi failures).
7. Suggested fix location (file/function).


## 9) Recent Incidents Log (2026-04-29)

Use this section as the rolling high-signal log. Keep entries short and factual.

### Incident 2026-04-29 09:16:38
- Run: `Lab-Uplands-G10` / `20260429-091638-multi__ilo__storage__esxi`
- Symptom:
  - Failed at storage step `Choose storage apply path`.
  - Error: SmartStorage inventory found, but no verified writable `SmartStorageConfig/Settings` URI.
- Root cause:
  - Platform selection was correctly blocking a non-writable apply path.
  - Run state previously looked like active/running in some views.
- Fix applied:
  - Explicit failed state persisted at platform-selection step.
  - Workflow state now records `apply_failed` consistently.
- Code:
  - `app/main.py` (`run_storage_as_part_of_real_run`, `run_storage_apply`)

### Incident 2026-04-29 09:30:36
- Run: `Lab-Uplands-G10` / `20260429-093036-multi__ilo__storage__esxi`
- Symptom:
  - Failed at storage delete stage with transport error:
  - `Connection aborted / RemoteDisconnected`.
- Root cause:
  - iLO closed connection during `DELETE` of existing Redfish volume.
- Fix applied:
  - One-shot transport retry added for safe request methods (`GET`, `DELETE`, `PATCH`, `PUT`).
  - `POST` intentionally left non-auto-retry to avoid duplicate creates.
- Code:
  - `app/ilo.py` (`_request_with_transport_retry`, `_get`, `_delete`, `_patch`, `_put`)

### Incident 2026-04-29 09:38:13
- Run: `Lab-Uplands-G10` / `20260429-093813-multi__ilo__storage__esxi`
- Symptom:
  - iLO stage completed, then storage failed at volume create:
  - `POST /Storage/.../Volumes` connection aborted.
  - Run label shows `iLO error` even though failing operation is storage create.
- Root cause:
  - Response-drop risk on POST create path; write may succeed remotely while client loses response.
- Fix applied:
  - For standard Redfish volume create:
    - on connection-abort POST, perform readback matching (`RAIDType` + drive set) before retry.
    - if matching volume is observed, treat as recovered success without replaying create.
    - if not observed, perform exactly one controlled retry.
- Code:
  - `app/ilo.py` (`create_standard_storage_volume`, `_find_matching_standard_volume`)

### Incident 2026-04-29 09:45:32
- Run: `Lab-Uplands-G10` / `20260429-094532-multi__ilo__storage__esxi`
- Symptom:
  - iLO stage completed, then storage failed at delete with:
  - `DELETE .../Volumes/238 failed with HTTP 404 ResourceMissingAtURI`.
- Root cause:
  - Discovery listed a volume URI that no longer existed by the time delete executed (stale/racing inventory).
- Fix applied:
  - Treat `404 ResourceMissingAtURI` during standard Redfish volume delete as idempotent success (`already missing`).
  - Continue wipe flow instead of failing the whole run.
- Code:
  - `app/ilo.py` (`delete_standard_storage_volume`)

### Incident 2026-04-29 Test Contamination
- Symptom:
  - After running tests, active kit/artifacts could appear with test names (`Std-Storage-Kit`, etc.).
- Root cause:
  - Some tests wrote to real runtime paths.
- Fix applied:
  - Autouse pytest fixture isolates config/artifacts/exports paths to `tmp_path` for all tests.
- Code:
  - `tests/test_app.py` (`isolate_runtime_paths`)


## 10) Ongoing Use Rule For This File

Each time a new failure appears:
1. Add one entry under section 9 with exact run id and first failing line.
2. Record root cause hypothesis and whether confirmed.
3. Record fix status: `not started`, `in progress`, or `fixed`.
4. Add exact code surface changed.
5. If unresolved, add “next check” command/path so the next debug pass starts fast.
