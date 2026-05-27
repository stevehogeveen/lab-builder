You are building an overnight real-hardware automation run for Lab Builder.

Goal:
Create a controlled Overnight Hardware Run that can safely experiment with:
- iLO at 192.168.1.200
- a Cisco switch connected through local serial console

The run should be detailed, visually impressive in the React UI, safe by default, and ready for review in the morning.

Hard requirements:
1. Do not wipe storage.
2. Do not factory reset anything unless an explicit config flag is enabled.
3. Do not install ESXi unless an explicit config flag is enabled.
4. Do not continue hardware actions after the finalization window starts.
5. Stop hardware experiments before 5:30 AM local time.
6. Before 6:00 AM, create a morning-ready report, run tests, commit, and push.
7. Do not commit secrets, passwords, tokens, API keys, raw authorization headers, or config files containing credentials.
8. If a secret scan finds possible secrets, do not auto-commit. Write the reason clearly in the morning report.
9. Preserve existing Lab Builder functionality.
10. Do not do broad unrelated rewrites.

Main feature:
Add an Overnight Hardware Run mode in the app.

Modes:
- discovery_only
- guided_setup
- full_overnight

Default mode must be discovery_only.

Default destructive flags must all be false:
- allow_power_cycle: false
- allow_virtual_media_mount: false
- allow_boot_override: false
- allow_esxi_install: false
- allow_cisco_config_changes: false
- allow_cisco_factory_reset: false
- allow_cisco_write_memory: false

Artifacts:
Create a run folder under:

artifacts/runs/overnight/<timestamp>-ilo-cisco/

Write:
- config-snapshot.yml
- live-job.log
- trace.yml
- summary.yml
- MORNING_READY.md
- ilo/discovery.json
- ilo/power-state-before.json
- ilo/boot-options.json
- ilo/virtual-media.json
- ilo/final-state.json
- cisco/console-detect.txt
- cisco/initial-session.txt
- cisco/show-version.txt
- cisco/running-config-before.txt
- cisco/setup-transcript.txt
- cisco/running-config-after.txt

iLO discovery:
- Connect to Redfish service root at https://192.168.1.200/redfish/v1/
- Support credentials from existing safe config/environment patterns only.
- Do not hard-code credentials.
- Collect service root, system information, power state, boot options, virtual media status, and iLO/manager info where available.
- Save raw JSON artifacts.
- Add friendly live-job.log entries.
- Add structured trace.yml events with timestamp, stage, status, progress, and message.

Cisco console discovery:
- List likely serial devices such as /dev/ttyUSB* and /dev/ttyACM*.
- Use saved/selected console port if one exists.
- Open console safely.
- Capture prompt.
- Run non-destructive commands first:
  - terminal length 0
  - show version
  - show running-config
- Save transcripts.
- Do not write memory unless allow_cisco_write_memory is true.
- Do not change config unless allow_cisco_config_changes is true.
- Do not factory reset unless allow_cisco_factory_reset is true.

UI:
Create or update a Run Center / Overnight Hardware Run page with premium dark mission-control styling.

Operator Mode should show:
- what this run is for
- current mode
- targets
- what to do next
- what happened last
- current completion state
- one clear action area
- live job panel
- timeline of steps
- final morning-ready status

Debug Mode should show:
- raw paths
- logs
- traces
- API output
- console transcripts
- artifact links
- detailed errors

The UI should clearly separate:
- discovered/current state
- saved kit config
- values ready to apply
- last action result
- finalization result

Add a safety confirmation sheet before starting anything beyond discovery_only.

Finalization:
Add a required finalization stage.

At or before 5:30 AM:
- Stop all new hardware actions.
- Finish only the current safe read if already running.
- Write summary.yml.
- Start finalization.

Before 6:00 AM:
- Write MORNING_READY.md in the run folder.
- Run focused tests if available.
- Run python -m pytest -q.
- Run python -m compileall app.
- Capture git status.
- Scan staged and run artifacts for obvious secrets before committing.
- If possible secrets are found, do not commit or push. Write Needs attention in MORNING_READY.md.
- If tests fail, still write MORNING_READY.md and mark Needs attention.
- If tests pass and no secrets are found, git add relevant code, templates, static files, tests, docs, config changes if safe, and run artifacts.
- Commit with a clear message.
- Push the active branch to origin.
- Record branch, commit SHA, test result, push result, and run folder path in MORNING_READY.md.

Suggested commit message:
Add overnight iLO and Cisco hardware run automation

Tests:
Add or update tests for:
- mode validation
- destructive flags defaulting to false
- 5:30 AM hardware stop behavior
- finalization before 6:00 AM
- secret scan blocks auto-commit
- UI exposes Operator Mode controls
- Debug Mode contains raw logs/details
- mocked iLO discovery
- mocked Cisco console discovery

Before editing:
- Inspect existing run center, Cisco, iLO, ESXi, OVF, artifact, and job/logging patterns.
- Reuse existing helpers where practical.
- Pick a small vertical slice first:
  1. backend run model/config
  2. artifact writer
  3. UI panel
  4. tests
  5. finalization

After editing:
- Run focused tests.
- Run python -m pytest -q.
- Run python -m compileall app.
- Commit and push only if tests pass and secret scan is clean.
