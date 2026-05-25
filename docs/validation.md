# Validation Commands

Use the smallest command that covers the files you changed, then broaden before a commit that touches shared flow, routing, or state handling.

## Focused Checks

```bash
.venv/bin/python -m pytest tests/test_operator_flow_contract.py
.venv/bin/python -m compileall app
```

## Broader Checks

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Use `python3` only when the virtual environment is unavailable. `ruff` is optional unless the local environment has it installed. The pytest and compile commands are the required baseline for shared operator-flow changes.
