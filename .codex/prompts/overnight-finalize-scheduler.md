You are adding the missing final safety scheduler for the Overnight Hardware Run.

Goal:
Make sure the app can be left running overnight and still stop, summarize, test, commit, and push before 6:00 AM.

Requirements:
1. Add a local finalization script or app command that can be called by cron/at/systemd.
2. It must stop hardware work before 5:30 AM.
3. It must write MORNING_READY.md.
4. It must run:
   - python -m pytest -q
   - python -m compileall app
5. It must capture git status before and after.
6. It must scan for obvious secrets before committing.
7. It must not commit if secrets are suspected.
8. It must commit and push only when safe.
9. It must record branch name, commit SHA, push result, test result, and artifact folder.
10. Add clear docs showing the exact command to schedule it with at or cron.
11. Add tests for the finalization decision logic.
12. Do not touch real hardware in this prompt.
13. Do not do unrelated rewrites.

After editing:
- Run focused tests.
- Run python -m pytest -q.
- Run python -m compileall app.
- Commit and push if safe.
