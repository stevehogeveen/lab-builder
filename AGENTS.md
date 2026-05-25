# Lab Builder Session Rules

Before editing code, every session must read:

1. `SESSION_COORDINATION.md`
2. `docs/workflow-session-scopes.md`
3. `docs/operator-flow-contract.md`
4. `docs/automation-principles.md`
5. `docs/ux-product-principles.md`

## Quick Start Trigger

If the user says a sentence like:

`I am going to be working with Cisco this round. Use the operator flow contract.`

treat it as a workflow-session start. Identify the named workflow, read `docs/workflow-session-scopes.md`, run `git status --short --branch`, then claim or create that workflow session entry in `SESSION_COORDINATION.md` before editing.

If no active coordination entry exists for the named workflow, create one from the session template. If an active entry already exists for a different branch or owner, stop and ask before editing that workflow's files.

Every setup workflow must follow the shared operator flow:

`Context -> Targets -> Credentials -> Current State -> Preflight -> Plan -> Execute -> Monitor -> Evidence -> Next Step`

Do not invent a new way to collect IPs, show logs, show readiness, show run status, or present evidence. If a workflow needs a new pattern, update `docs/operator-flow-contract.md` first and explain why the shared pattern is insufficient.

Before editing shared files, claim the exact files in `SESSION_COORDINATION.md`. Keep ownership narrow, leave the app testable after each commit, and do not edit unrelated workflow modules.
