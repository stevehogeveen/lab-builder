# Legacy Test Suite Triage - 2026-07-09

Task: `project/queue/ready/TASK-003-legacy-test-suite-triage.md`

Repository: `stevehogeveen/lab-builder`

## Summary

TASK-003 completed the safe legacy test-suite triage that followed Windows runtime compatibility work.

The two NetApp module failures were stale UI text assertions, not live provider failures. The current NetApp page renders the full operator app shell and uses newer labels:

- `Connect to ONTAP`
- `Console/bootstrap IPs`
- `Cluster management IP`
- `Test connection`
- `Last connection test`

The tests now assert those current workflow anchors.

## Verification

NetApp module lane:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_netapp_module.py -q
```

Result:

```text
31 passed, 1 warning in 6.13s
```

Broad Windows lane:

```powershell
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/test_app.py -q
```

Result:

```text
97 passed, 1 warning in 73.78s
```

Full collection:

```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q
```

Result:

```text
402 tests collected in 1.30s
```

## Test Lane Recommendation

Recommended Windows lanes:

1. Collection proof:

```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q
```

2. Broad fast-ish lane excluding the large legacy app integration file:

```powershell
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/test_app.py -q
```

3. Focused slow lane for the legacy app integration file:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_app.py -q
```

The full suite previously timed out after a 5-minute guard because `tests/test_app.py` is large. That file should run in a longer CI window or be split in a future maintenance pass.

## Safety Review

No live NetApp action was executed.

No storage apply, RAID, factory reset, rebuild, Cisco write, provider write, or destructive workflow was run.

Safety gates and destructive confirmation paths were unchanged.

## Outcome

TASK-003 is complete.

No additional ready task was created in this pass. The next Executive Router / COO heartbeat should choose future work from current Project OS and product queues rather than inheriting an automatically generated follow-up.
