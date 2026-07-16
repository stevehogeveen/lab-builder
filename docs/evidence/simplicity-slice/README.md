# Operator Home Simplicity Evidence

- `operator-home-before.png`: Dashboard before the replacement pass. The same readiness, issue count, next step, module state, and run entry points appear repeatedly.
- `operator-home-after.png`: Operator Home after the replacement pass. It shows one selected kit, one state headline, one progress result, actionable exceptions, one primary action, and one View details entry point.
- `operator-home-after-mobile.png`: Narrow viewport evidence for text fit and responsive action hierarchy.

The screenshots are paired with automated assertions in `tests/test_app.py` and model tests in `tests/test_operator_home.py`.

## Validation

- `python -m compileall -q app tests`: passed.
- Focused Operator Home and navigation regression: `15 passed, 292 deselected`.
- Playwright at 1440 x 1000 and 390 x 844: one primary action, one progress display, Details closed by default, and no horizontal overflow.
- Full repository suite: `390 passed, 12 failed, 1 warning`.

The 12 full-suite failures are pre-existing and outside this slice:

- 10 ESXi prepare/execute tests expect a local ESXi 7 base ISO that is absent from `media/esxi/base`. This is the first known failure class in `docs/legacy-test-suite-triage-2026-07-09.md`.
- 2 NetApp tests assert superseded copy (`Connection Target` and `API connection test`) removed by the earlier NetApp simplification change.
