# Automation Principles

Lab Builder automation should be discovery-driven, intent-based, state-aware, and self-diagnosing.

Core rule:

> Never blindly execute. Never blindly fail. Always discover, compare, correct if safe, explain if not safe.

## Intent vs Live Discovery

Desired intent is the approved outcome, not a temporary API path. Examples:

- iLO hostname, network, DNS, SNMP, and user intent.
- Storage RAID layout by controller identity, bay, drive serial when available, drive size, RAID type, and spare intent.
- ESXi management network, hostname, DNS, root password policy, ISO URL, and boot target.
- Power state intent such as "server must be On before storage apply" or "server must be Off before ESXi boot prep".

Live discovery is the current state read from Redfish, iLO APIs, files, and local app state immediately before execution. Redfish paths can change between boots or firmware states, so paths are options to discover, not durable intent.

## Safe Auto-Remediation

The app may correct drift automatically only when it can prove the approved intent still targets the same hardware and outcome.

Safe examples:

- A storage controller Redfish path changed, but the server serial, controller model, selected bays, and drive identities still match.
- A server is Off before storage apply, and Redfish says ResetType `On` is allowed.
- A server is already Off before ESXi boot prep, so power-off is skipped.
- Virtual media already has old media inserted, and the manager exposes a supported eject action.

Unsafe examples:

- A selected storage bay now contains a different drive serial.
- The approved controller is missing and more than one plausible controller is present.
- Only inventory-only SmartStorageConfig paths are visible for a destructive storage apply.
- Firmware does not expose a writable setting that the user marked required.

## When To Block

Block when continuing could modify the wrong hardware, destroy stale data, or hide a platform limitation. A blocked diagnostic must include:

- What the app wanted to do.
- What it discovered.
- What options were available.
- Why no option was safe.
- What the user should do next.

## Stage Preflight Rules

Power:

- Discover current PowerState.
- Discover reset target and allowed ResetType values.
- Use only allowed ResetType values.
- If a reset POST disconnects, poll for the expected state, retry once with a fresh connection if needed, then fail with observed state and available options.

Storage:

- Ensure server PowerState is On.
- Run fresh discovery before destructive apply.
- Compare approved intent against live server, controller, bay, drive identity, RAID, and spare intent.
- Remap Redfish paths only when hardware intent still matches.
- Never use inventory-only SmartStorageConfig for destructive apply.

ESXi:

- Verify base ISO, generated ISO, and serving URL.
- Discover virtual media devices and insert/eject actions.
- Eject old media, insert the generated ISO, verify insertion, discover boot override options, then power On.
- Wait for ESXi management reachability and explain likely causes if it does not appear.

iLO configuration:

- Discover current settings.
- Compare current vs desired.
- Apply only differences.
- Verify after apply.
- Log unsupported optional settings as skipped; fail required unsupported settings clearly.

## Diagnostic Log Format

Use structured technician-style logs:

- `[DISCOVER]` options found.
- `[COMPARE]` desired intent vs live state.
- `[REMAP]` safe corrections attempted.
- `[DECISION]` selected action and reason.
- `[BLOCKED]` rejection reason and recommended fix.

Example:

```text
[DISCOVER] Storage preflight options discovered: controllers=[...], writable_volume_paths=[...]
[COMPARE] Storage preflight differences: Controller Redfish path changed from /Storage/DE009000 to /Storage/DE00A000.
[REMAP] Storage preflight safe corrections attempted: Remapped controller path to /Storage/DE00A000.
[DECISION] Storage preflight selected: Use live controller path /Storage/DE00A000 for storage apply.
```

Blocked example:

```text
[BLOCKED] Storage preflight rejected: Bay 3 drive serial changed from OLD123 to NEW456.
[BLOCKED] Storage preflight recommended fix: Run storage discovery again, review the new controller/drive layout, and re-approve storage before applying.
```

## Debug Bundles

Failure bundles should include the structured diagnosis, desired intent, discovered state, options discovered, attempted corrections, rejection reasons, recommended next steps, redacted config, and recent logs.

Never include passwords, tokens, Authorization headers, cookies, session IDs, or raw secrets.
