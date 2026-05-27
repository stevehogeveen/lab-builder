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
   - Source pattern: shadcn-style admin dashboards using persistent navigation, command palettes, tables, forms, and route-level app surfaces.

6. Let density adapt to the workspace.
   - Operators move between browsers, laptop screens, remote consoles, and large monitors. The app should support a compact view without hiding required information.
   - Applied pattern: local `Compact view` toggle stored in browser local storage.

7. Accessibility is product quality.
   - Keyboard access, visible focus, skip links, status text, and non-color-only status cues are part of the app's operational reliability.

8. Reduce interpretation time before adding detail.
   - Dashboard rows should expose the status, next blocker, and completion signal first; deeper evidence belongs in collapsed details or secondary drawers.
   - Applied pattern: dashboard build-path lenses for `All`, `Needs attention`, and `Ready`, with per-row progress bars.
   - Source pattern: dashboard UX analysis that treats dashboards as a way to expose key data and actionable insight without forcing users out of context.

9. Use motion as feedback, not decoration.
   - Microinteractions should confirm user action, clarify selected state, or make a transition understandable. Motion must respect reduced-motion preferences.
   - Applied pattern: small press/selection feedback on dashboard lens buttons and build-path rows using CSS transitions only.
   - Source pattern: Motion/Framer Motion examples for buttons, accordions, modals, segmented buttons, and state transitions.

10. Hide complexity until it helps.
   - Advanced checks, raw logs, artifacts, and troubleshooting detail should stay available without dominating the main setup path.
   - Applied pattern: app-wide issue drawer, command palette, collapsed detailed checks, and technical drawers.
   - Source pattern: progressive disclosure in enterprise UX.

## Applied In This Slice

- Added a global `Skip to content` link.
- Added sidebar `Quick jump` with `Ctrl+K` command palette.
- Added a browser-local `Compact view` density toggle.
- Added an app-wide `Open issues` drawer so blockers and fixes stay findable from every page.
- Kept all commands sourced from existing sidebar links so route names and module behavior stay unchanged.
- Added dashboard build-path lenses so operators can switch between all rows, blockers, and ready rows without leaving the cockpit.
- Added compact progress indicators to dashboard path rows.

## Visual Research Inputs

- Admin dashboard template patterns: persistent sidebar, command palette, dense app pages, tables/forms, responsive page shells, and manageable secondary navigation.
  - https://adminlte.io/blog/shadcn-admin-dashboard-templates/
- Motion / Framer Motion microinteraction patterns: press feedback, segmented controls, accordions, modals, and state transitions.
  - https://ics.media/en/entry/251204/
- Dashboard UX patterns: keep key data and actionable insight visible without requiring operators to leave the current context.
  - https://www.pencilandpaper.io/articles/ux-pattern-analysis-data-dashboards
- Progressive disclosure for enterprise UX: reveal complexity when it becomes relevant instead of showing every advanced control by default.
  - https://medium.com/@theuxarchitect/progressive-disclosure-in-enterprise-design-less-is-more-until-it-isnt-01c8c6b57da9

## Next Candidates

- Standardize empty-state copy across every module using one reusable component.
- Add command-palette actions for the safest read-only operations, such as opening current config or reading current versions.
- Link issue-drawer rows to exact fields once setup pages expose stable field anchors.
