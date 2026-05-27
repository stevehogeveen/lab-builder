# React Preview Experiment

The desktop UI experiment is isolated behind `GET /react-preview`.

The current expanded wiring is documented in [react-desktop-ui.md](react-desktop-ui.md). This note remains as the first-pass preview record.

## Wiring

- Route: `app/main.py::react_preview_page`
- Template: `templates/react_preview.html`
- Current production Run Center route remains `GET /execution`.
- Existing Jinja and HTMX templates are not removed or replaced.

## Frontend Shape

This first pass uses CDN-loaded React and ReactDOM from the standalone Jinja template. There is no npm package, bundler, or frontend build step yet.

The preview uses mock Run Center data in the template script. It demonstrates:

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
PORT=8001 ./scripts/start-app-dev
```

Then open:

```text
http://localhost:8001/react-preview
```

## Tests

The route is covered by a focused test in `tests/test_app.py`. No separate frontend build test is needed until this experiment grows beyond the CDN prototype.
