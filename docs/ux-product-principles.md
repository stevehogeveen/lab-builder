# Lab Builder UX Product Principles

This file captures the UI techniques being folded into Lab Builder so future cleanup work stays consistent instead of becoming one-off decoration.

## Principles

1. Keep status visible.
   - Every page should make the current kit state, blockers, and next action obvious without requiring the operator to hunt for it.
   - Source pattern: Nielsen Norman Group visibility-of-system-status heuristic and Apple feedback guidance.

2. Use task-list navigation for long workflows.
   - Setup modules are a task list: each item should have a clear name, short status, whole-row navigation, and no mystery next step.
   - Source pattern: GOV.UK task-list component.

3. Give feedback where the action happened.
   - Button results should appear in the same block as the button, with plain-language status and concise next steps.
   - Source pattern: Apple feedback guidance and Atlassian information-message guidance.

4. Make empty states useful.
   - Empty cards should say what will appear there, why it is empty, and the one best action to take next.
   - Source pattern: IBM Carbon empty-state guidance.

5. Support expert speed without hurting beginners.
   - Great operational apps provide keyboard shortcuts and fast navigation while preserving visible buttons and labels.
   - Applied pattern: `Ctrl+K` command palette built from the visible sidebar navigation.

6. Let density adapt to the workspace.
   - Operators move between browsers, laptop screens, remote consoles, and large monitors. The app should support a compact view without hiding required information.
   - Applied pattern: local `Compact view` toggle stored in browser local storage.

7. Accessibility is product quality.
   - Keyboard access, visible focus, skip links, status text, and non-color-only status cues are part of the app's operational reliability.

## Applied In This Slice

- Added a global `Skip to content` link.
- Added sidebar `Quick jump` with `Ctrl+K` command palette.
- Added a browser-local `Compact view` density toggle.
- Added an app-wide `Open issues` drawer so blockers and fixes stay findable from every page.
- Kept all commands sourced from existing sidebar links so route names and module behavior stay unchanged.

## Next Candidates

- Standardize empty-state copy across every module using one reusable component.
- Add command-palette actions for the safest read-only operations, such as opening current config or reading current versions.
- Link issue-drawer rows to exact fields once setup pages expose stable field anchors.
- Evaluate the `experience/operator-companion` branch for a more spatial, human-readable operator guidance layer before merging it into `main`.
- Evaluate the experimental `Experience lens` and `Proof ledger` patterns for broad adoption if they reduce operator confusion during live runs.
- Keep psychedelic or theatrical visuals opt-in only; operational trust comes before spectacle during real infrastructure work.
- Experience lenses should change the whole app, not only copy: Calm should minimize, Normal should guide, and Expert should expose logs, proof, and technical detail.
- Verbose modes must still respect safety boundaries: expanding proof should not automatically expose destructive controls.
