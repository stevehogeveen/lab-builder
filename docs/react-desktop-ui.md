# React Desktop UI

The desktop interface is served from `GET /`. FastAPI and the Python device services remain in place; older HTML routes are now compatibility endpoints for actions that have not been given dedicated JSON APIs yet.

## How It Is Wired

- Shell route: `app/main.py::home`
- HTML host template: `templates/react_preview.html`
- React bundle: `static/js/react-desktop-ui.js`
- React data APIs: `app/main.py` under `/api/ui/*`
- Compatibility HTML action routes remain available while hardware workflows are moved behind JSON APIs.

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

Port 8001 is the React UI port:

```bash
./runreact
```

Open:

```text
http://localhost:8001/
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

The dashboard also has buttons that call existing HTML action routes:

- `POST /prepare-execute`
- `POST /execute-preview`

Those routes still return HTML; React only treats them as backend actions and then refreshes JSON state.

## Real vs Shell Pages

- Dashboard / Run Center: real `/api/ui/app-state`, job polling, recent activity, module readiness, and existing run action routes.
- iLO setup: real `/api/ui/ilo` state and real `/api/ui/ilo/settings` save path using server-side iLO logic.
- NetApp setup: shell page with real `/modules/netapp/status`.
- ESXi setup: shell page with readiness summary and mapped compatibility actions.
- Cisco setup: shell page with readiness summary and mapped compatibility actions.
- Configuration / Kit management: real current-kit and kit-list state from `/api/ui/app-state`; some writes still use compatibility action routes.
- Reports / run history: real state from `/api/ui/app-state` and `/api/ui/run-history`.
- Action catalog: real generated route inventory from `/api/ui/action-catalog`.
- Technical details/logs: real `/api/ui/technical-events` plus the global technical drawer.

## Migration Status By Module

- Dashboard / Run Center: first-pass migrated.
- iLO: partially migrated with real JSON save.
- ESXi: inventory and shell only; some forms/actions still use compatibility routes.
- NetApp: status is real; save/plan/apply actions are listed but not fully integrated in React controls.
- Cisco: inventory and shell only; actions still use compatibility routes.
- Configuration / Kit management: kit state, load, create, import, and downloads use React APIs; older save routes remain available.
- Reports / history: read-only state is migrated; detailed report viewers still use compatibility routes.
- Action catalog: migrated as a read-only control surface for route coverage and migration planning.
- Technical details/logs: read-only diagnostics migrated.

## Safety Notes

- Existing Jinja/HTMX templates are retained only as compatibility views until their actions are fully represented by React controls.
- Existing backend routes remain available.
- Hardware/config/job business logic stays server-side.
- React pages expose the mapped action inventory so missing JSON endpoints are visible instead of hidden.
