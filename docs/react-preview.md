# React UI Merge Note

The React desktop UI is now the primary app at `GET /`.

The current expanded wiring is documented in [react-desktop-ui.md](react-desktop-ui.md). This note remains as the first-pass preview record.

## Wiring

- Route: `app/main.py::home`
- Template: `templates/react_preview.html`
- Older HTML setup routes remain available as compatibility endpoints for actions that still post to server-rendered handlers.

## Frontend Shape

The current UI uses CDN-loaded React and ReactDOM from the standalone Jinja template. There is no npm package, bundler, or frontend build step yet.

The removed first-pass prototype used mock Run Center data in the template script. The live UI now loads state from `/api/ui/*` and the existing hardware services.

- left sidebar navigation
- top status bar
- large live job panel
- progress timeline
- kit summary
- warnings
- next recommended step
- recent activity
- separated technical details and logs

## Run

Use the normal FastAPI app command:

```bash
./runreact
```

Then open:

```text
http://localhost:8001/
```

`/react-preview` is retained as a redirect to `/` for bookmarks.

## Tests

The route is covered by a focused test in `tests/test_app.py`. No separate frontend build test is needed until this experiment grows beyond the CDN prototype.
