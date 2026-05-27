# React Desktop UI

The experimental desktop interface is served from `GET /react-preview`. It keeps FastAPI, Python services, and all existing Jinja/HTMX pages in place.

## How It Is Wired

- Shell route: `app/main.py::react_preview_page`
- HTML host template: `templates/react_preview.html`
- React bundle: `static/js/react-desktop-ui.js`
- React data APIs: `app/main.py` under `/api/ui/*`
- Legacy fallback pages remain available from every React page.

The frontend uses CDN React and ReactDOM. There is no npm package, bundler, or frontend build step in this pass.

## Visual Research Inputs

This pass uses established admin-console patterns as references, without copying template code or adding a frontend build system:

- PatternFly page/card patterns: persistent masthead/sidebar/page sections and cards for dashboard state.
- Carbon and Clarity form guidance: grouped, labeled form controls with setup context separated from diagnostics.
- Clarity alert guidance: warnings stay contextual and concise; technical logs stay outside the main setup flow.
- Flowbite Admin Dashboard: sidebar plus top navigation, data cards, tables, and drawer-style affordances as a practical admin template reference.
- Fluent 2 navigation guidance: high-level navigation should stay short, scannable, and always reachable.

Reference URLs:

- https://pf-react-staging.patternfly.org/components/page/
- https://www.patternfly.org/components/card/
- https://carbondesignsystem.com/components/form/usage/
- https://core.clarity.design/core-components/form/
- https://core.clarity.design/core-components/alert/
- https://github.com/themesberg/flowbite-admin-dashboard
- https://fluent2.microsoft.design/components/web/react/core/nav/usage

## Run

Port 8001 is the experimental UI port:

```bash
PORT=8001 ./scripts/start-app-dev
```

Open:

```text
http://localhost:8001/react-preview
```

## Backend Endpoints Used

- `GET /api/ui/app-state` - full shell state, current kit, dashboard, modules, actions, recent activity, and job summary.
- `GET /api/ui/current-kit` - current kit metadata.
- `GET /api/ui/job-status` - live job status for polling.
- `GET /api/ui/recent-activity` - recent operator and run events.
- `GET /api/ui/modules` - module summaries plus mapped action routes.
- `GET /api/ui/action-catalog` - generated route/action catalog from FastAPI's registered routes.
- `GET /api/ui/run-history` - run history display records.
- `GET /api/ui/technical-events` - job logs, trace events, and artifact paths.
- `GET /api/ui/ilo` - iLO setup state and validation.
- `POST /api/ui/ilo/settings` - saves iLO settings through the existing iLO module service.
- `GET /modules/netapp/status` - existing NetApp JSON status endpoint reused by the React NetApp page.

The dashboard also has buttons that call existing legacy HTML routes:

- `POST /prepare-execute`
- `POST /execute-preview`

Those routes still return HTML; React only treats them as backend actions and then refreshes JSON state.

## Real vs Shell Pages

- Dashboard / Run Center: real `/api/ui/app-state`, job polling, recent activity, module readiness, and existing run action routes.
- iLO setup: real `/api/ui/ilo` state and real `/api/ui/ilo/settings` save path using server-side iLO logic.
- NetApp setup: shell page with real `/modules/netapp/status`.
- ESXi setup: shell page with readiness summary and mapped legacy actions.
- Cisco setup: shell page with readiness summary and mapped legacy actions.
- Configuration / Kit management: real current-kit and kit-list state from `/api/ui/app-state`; form writes still use the legacy page.
- Reports / run history: real state from `/api/ui/app-state` and `/api/ui/run-history`.
- Action catalog: real generated route inventory from `/api/ui/action-catalog`.
- Technical details/logs: real `/api/ui/technical-events` plus the global technical drawer.

## Migration Status By Module

- Dashboard / Run Center: first-pass migrated.
- iLO: partially migrated with real JSON save.
- ESXi: inventory and shell only; forms/actions still use legacy page.
- NetApp: status is real; save/plan/apply actions are listed but not fully integrated in React controls.
- Cisco: inventory and shell only; actions still use legacy page.
- Configuration / Kit management: read-only kit state migrated; save/load/import actions still use legacy page.
- Reports / history: read-only state is migrated; detailed report viewers still use legacy routes.
- Action catalog: migrated as a read-only control surface for route coverage and migration planning.
- Technical details/logs: read-only diagnostics migrated.

## Safety Notes

- Existing Jinja/HTMX templates are not deleted or replaced.
- Existing production routes remain available.
- Hardware/config/job business logic stays server-side.
- React pages link back to the legacy page for full coverage while migration continues.
