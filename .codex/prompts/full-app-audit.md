You are performing a full Lab Builder app audit before a long improvement run.

Do not edit files yet.

Inspect the codebase and produce an audit covering:
1. All pages/templates.
2. All visible buttons, forms, toggles, links, and HTMX actions.
3. Backend routes connected to those controls.
4. Missing or suspicious routes/handlers.
5. Pages where logs/status appear in inconsistent places.
6. Cluttered pages that should be simplified.
7. Places where buttons might reset user choices unexpectedly.
8. Places where hardware access is assumed but should be dry-run/mockable.
9. Tests that should be added or updated.
10. The safest order of fixes for a 14-hour run.

Hardware availability:
- Only one switch and one server are available.
- Do not assume any other hardware is available.
- Mark each finding as:
  - can test with available server
  - can test with available switch
  - can test with mock/dry-run only
  - code inspection only

Output:
- prioritized findings
- proposed fix order
- high-risk areas
- low-risk quick wins
- recommended first task

Do not make changes.
