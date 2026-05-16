# Experimental Operator Companion Branch

Branch: `experience/operator-companion`

This branch explores a more distinctive experience layer for Lab Builder. The goal is not decoration. The goal is to make the app feel like it is carrying operational context for the user: current state, next action, proof, risk, and control.

## Research Inputs

- Visibility of system status: keep users informed with timely, understandable status.
  Source: Nielsen Norman Group heuristic summary, https://media.nngroup.com/media/articles/attachments/Heuristic_Summary1-compressed.pdf
- Calm technology: move non-critical information to the periphery and avoid stealing attention.
  Source: Calm Tech Institute, https://www.calmtech.institute/
- Progressive disclosure: reveal complexity when the user needs it, not before.
  Source: Nielsen Norman Group, https://www.nngroup.com/articles/progressive-disclosure/
- Task-list workflows: make multi-step progress visible and stable.
  Source: GOV.UK Design System task list, https://design-system.service.gov.uk/components/task-list/
- Cognitive inclusion: reduce memory burden, avoid forcing users to infer state, and support different working styles.
  Source: Microsoft Inclusive Design for Cognition, https://inclusive.microsoft.design/tools-and-activities/InclusiveDesignForCognitionGuidebook.pdf
- Human-centered AI/HCI: adaptive interfaces should augment the operator and preserve control.
  Source: Apple Machine Learning Research on computer-use agent UX, https://machinelearning.apple.com/research/mapping

## What This Branch Adds

- Universal `Operator companion` strip under the page hero.
- `Human-readable next move` copy using the existing readiness and blocker model.
- Persistent promises: certainty, control, proof before apply, and no hidden steps.
- Dashboard `Living kit map`, a spatial representation of setup modules around the run path.
- Session-local `Experience lens` with Calm, Explain, and Expert modes.
- Global `Proof ledger` drawer that collects readiness, page signal, latest run signal, and module proof path.
- `data-navigate-href` button navigation so experimental UI can route without adding duplicate sidebar links.

## Design Rules

1. The app should speak in operational language, not widget language.
2. Every recommendation must keep a visible operator-controlled path.
3. Proof and safety must be visible before destructive or real apply actions.
4. Spatial UI is allowed only when it clarifies workflow state; it must collapse back to a simple list on small screens.
5. Experimental surfaces must be backed by existing data and routes, not new hidden state.

## Next Experiments

- Add exact field anchors for issue drawer rows.
- Let the command palette expose safe read-only actions, not only page jumps.
- Feed the proof ledger with richer artifacts as more modules expose stable proof summaries.
- Allow the experience lens to be set per kit when user preferences need to move between browsers.
