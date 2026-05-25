# Codex Safety Rules

Never:
- store secrets in code
- print passwords in logs
- make real hardware calls in tests
- delete user config without backup
- format disks
- change production boot behavior without tests
- auto-push to main
- edit unrelated modules during a focused task

Always:
- work in small steps
- prefer dry-run modes
- preserve existing behavior
- add tests for decision logic
- write clear summaries
- keep UI beginner-friendly
- expand acronyms on first use
- stop when tests fail
- clearly report changed files, tests run, and remaining risks
