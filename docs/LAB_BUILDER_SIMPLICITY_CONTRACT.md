# Lab Builder Simplicity Contract

## Purpose

Lab Builder may contain complex orchestration, provider integrations, diagnostics, logs, recovery tools, and dependency handling.

The default operator experience must remain task-focused, calm, and immediately understandable.

Complexity belongs in the engine room. The operator should see only what is needed to understand the current state and take the next safe action.

## Operator Home information budget

Operator Home answers only these four questions:

1. What kit am I working on?
2. What was found?
3. What is ready or blocked?
4. What should I do next?

These four questions are a ceiling, not a suggestion.

The four questions must not automatically become four large dashboard cards. The whole screen should answer them collectively with the least possible visual weight.

If an element does not help answer one of these questions, it does not belong on Operator Home.

## One primary action

Every normal operator screen has one visually dominant action.

Examples:

- Configure Switch
- Confirm Console Connected
- Retry Verification
- Continue Build

Secondary actions must not compete with the primary action.

The operator should be able to identify the next action within five seconds without reading the entire screen.

## Information tiers

Every UI element must be assigned to one of these tiers.

### Operator

Information required to understand or perform the current task.

Examples:

- selected kit
- current phase
- plain-language state
- actionable blocker
- next action
- compact progress

### Details

Useful explanation, device information, verification evidence, and context that supports the operator but is not required at first glance.

### Advanced

Technical and diagnostic information.

Examples:

- raw logs
- provider configuration
- API payloads
- console commands
- internal dependency states
- capability keys
- retry history
- debug controls
- manual overrides

New information defaults to Advanced unless a new operator would be blocked without seeing it immediately.

Untagged UI elements must not ship.

## One fact, one owner, one location

Each business fact has one canonical source and one primary operator-facing display location.

Readiness, device state, blockers, progress, provider mode, and next action must not be independently recalculated or reinterpreted by multiple pages.

A page may link to the canonical fact. It must not create another competing version of it.

Examples of duplication to remove:

- different readiness totals on different pages
- the same blocker shown as both plain language and an environment variable
- the same console action exposed in multiple places
- repeated device-state cards that communicate the same outcome

## Operator vocabulary

Normal operator screens use plain-language outcomes and actions.

Internal vocabulary does not appear in normal operator mode.

Do not show terms such as:

- `PROVIDER_MODE=local-readonly`
- unresolved capability keys
- internal dependency-node states
- raw API or provider errors
- implementation-specific environment variables

Translate them into statements such as:

> The switch must be configured before the servers can be reached.

Technical details remain available through Details or Advanced views.

## Dependency complexity stays behind the interface

Lab Builder must enforce the real technical build order, including network, iLO, storage, ESXi, vCenter, and workload dependencies.

The dependency engine may be sophisticated. Operator Home must not become a visible rendering of the entire dependency graph.

The engine determines:

- what is ready
- what is blocked
- why it is blocked
- what action is safest next

Operator Home receives only a small projection of that state.

Recommended operator-facing model:

- KitName
- CurrentPhase
- DisplayState
- Headline
- SupportingMessage
- DeviceSummary
- AttentionItems
- NextAction
- Progress

The full dependency graph, provider states, logs, evidence, and internal capability information belong outside this model.

## Exception-driven presentation

Healthy items are summarized.

Items requiring attention are expanded.

Do not create a wall of green success cards.

Prefer:

> 7 devices found · No blockers

Or:

> 1 item needs attention

Then show only the exception that requires action.

## Replace, do not add

A new operator surface must remove or demote the surface it supersedes in the same change.

A new dashboard, readiness card, console action, blocker message, or status display may not ship beside an older version of the same information.

Simplification work must replace old surfaces, not place a clean layer on top of them.

## Reveal on demand

Normal mode shows only what is actionable now.

Additional information should be available through one clear route such as:

- View details
- Technical details
- Advanced tools

Logs and diagnostics must remain available, but they must not occupy the default operator experience.

## Five-second boring test

A new operator must be able to identify the next safe action within five seconds.

The screen should feel almost boring during the happy path.

If a reviewer must scan multiple cards, compare competing status totals, decode technical terminology, or hunt through several actions, the change fails this test.

A failed five-second test means the change does not ship.

## Architectural boundary

Normal operator components and advanced diagnostic components must remain separate.

Recommended structure:

```text
ui/operator
ui/details
ui/advanced
```

Operator components should not directly import:

- raw provider configuration components
- debug consoles
- API inspectors
- raw log viewers
- internal orchestration-state components

The normal operator interface consumes the small operator-facing contract. Advanced tooling consumes the full technical model.

## Pull request requirements

Every operator-facing UI pull request must answer:

1. Which of the four operator questions does this change answer?
2. What is the single primary action?
3. What existing element did this change remove or demote?
4. What information tier does every new element belong to?
5. Did the screen pass the five-second boring test?

A pull request that adds a new surface without identifying what it replaces must not merge.

## Initial implementation direction

Before adding more operator-facing workflow features:

1. Inventory current operator surfaces.
2. Identify duplicate readiness displays, blocker messages, console controls, device summaries, and provider terminology.
3. Remove or demote duplicates.
4. Create one canonical operator-facing state model.
5. Keep the dependency engine and technical evidence behind Details and Advanced views.
6. Add tests proving the operator experience stays within this contract.

## Decision

Lab Builder will preserve full diagnostic and provider capability while presenting a minimal, dependency-aware operator experience.

## Next Best Task

Perform a focused UI deduplication and operator-state-contract slice before adding more top-level workflow surfaces.

## Watch For

- clean new screens that leave old equivalents in place
- the dependency graph becoming the default dashboard
- internal provider terminology leaking into normal mode
- multiple pages displaying different readiness totals
- logs and technical controls reappearing on Operator Home

## Do Not Do

- do not remove diagnostic capability
- do not expose all capability on the default screen
- do not add new top-level navigation during the simplicity pass
- do not preserve superseded operator surfaces for convenience
- do not allow UI changes to ship without an information tier and replacement statement
