# Debug Bundles

When a real execution run fails, Lab Builder now auto-generates a sanitized debug bundle.

## Output location

- `artifacts/debug-bundles/latest-failure.txt`
- `artifacts/debug-bundles/debug-YYYYMMDD-HHMMSS.txt`

These files are generated runtime artifacts and are ignored by git.

## Manual collection

Run:

```bash
scripts/collect-debug
```

This generates the same sanitized bundle format used by automatic failure capture.

## What to upload to ChatGPT

Upload this file:

- `artifacts/debug-bundles/latest-failure.txt`

It includes redacted diagnostics (job state, logs, git context, environment, and kit config) without raw secrets.
