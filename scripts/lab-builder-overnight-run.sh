#!/usr/bin/env bash
set -u

cd "$HOME/lab-builder" || exit 1
source .venv/bin/activate

mkdir -p artifacts/codex-runs

END_TS=$(python - <<'PY'
from datetime import datetime, timedelta
now = datetime.now()
end = now.replace(hour=6, minute=0, second=0, microsecond=0)
if end <= now:
    end += timedelta(days=1)
print(int(end.timestamp()))
PY
)

echo "Overnight Lab Builder run started at $(date)"
echo "Target stop time: 6:00 AM local"

cycle_file="artifacts/codex-runs/overnight-cycle-counter.txt"
if [ -f "$cycle_file" ]; then
  i=$(cat "$cycle_file")
else
  i=1
fi

while [ "$(date +%s)" -lt "$END_TS" ]; do
  cycle=$(printf "%03d" "$i")
  echo "$((i + 1))" > "$cycle_file"

  header="artifacts/codex-runs/overnight-physical-cycle-$cycle-header.txt"
  mainlog="artifacts/codex-runs/overnight-physical-cycle-$cycle.txt"

  echo "=== Overnight physical cycle $cycle started at $(date) ===" | tee "$header"

  codex exec --dangerously-bypass-approvals-and-sandbox "$(cat .codex/prompts/overnight-physical-lab-run.md)" 2>&1 | tee "$mainlog"
  CODEX_EXIT=${PIPESTATUS[0]}

  if [ "$CODEX_EXIT" -ne 0 ]; then
    echo "Codex exited with $CODEX_EXIT on cycle $cycle. Trying one continuation/repair pass." | tee -a "$header"

    codex exec --dangerously-bypass-approvals-and-sandbox "The previous overnight physical Lab Builder cycle exited unexpectedly. Read $mainlog and the current git diff. Continue or repair only the focused Cisco, iLO, ESXi, OVF, or NetApp-prep work. Do not start unrelated work. Do not commit." 2>&1 \
      | tee "artifacts/codex-runs/overnight-physical-cycle-$cycle-codex-repair.txt"
  fi

  repair=0

  while true; do
    pytest_log="artifacts/codex-runs/overnight-physical-cycle-$cycle-pytest-$repair.txt"

    python -m pytest -q 2>&1 | tee "$pytest_log"
    TEST_EXIT=${PIPESTATUS[0]}

    if [ "$TEST_EXIT" -eq 0 ]; then
      echo "Pytest passed for cycle $cycle after $repair repair attempt(s)." | tee -a "$header"
      break
    fi

    repair=$((repair + 1))
    echo "Pytest failed for cycle $cycle. Repair attempt $repair at $(date)." | tee -a "$header"

    codex exec --dangerously-bypass-approvals-and-sandbox "Pytest failed during overnight physical Lab Builder cycle $cycle.

Read:
- $pytest_log
- current git diff
- .codex/prompts/overnight-physical-lab-run.md

Fix only the failing tests or production bug causing them.
Do not start new features.
Do not touch unrelated modules.
Keep focus on Cisco, iLO, ESXi, OVF/OVA, and NetApp prep only.
Do not commit.
Report files changed and exact issue fixed." 2>&1 \
      | tee "artifacts/codex-runs/overnight-physical-cycle-$cycle-repair-$repair.txt"
  done

  compile_repair=0

  while true; do
    compile_log="artifacts/codex-runs/overnight-physical-cycle-$cycle-compileall-$compile_repair.txt"

    python -m compileall app 2>&1 | tee "$compile_log"
    COMPILE_EXIT=${PIPESTATUS[0]}

    if [ "$COMPILE_EXIT" -eq 0 ]; then
      echo "Compileall passed for cycle $cycle after $compile_repair repair attempt(s)." | tee -a "$header"
      break
    fi

    compile_repair=$((compile_repair + 1))
    echo "Compileall failed for cycle $cycle. Compile repair attempt $compile_repair at $(date)." | tee -a "$header"

    codex exec --dangerously-bypass-approvals-and-sandbox "python -m compileall app failed during overnight physical Lab Builder cycle $cycle.

Read:
- $compile_log
- current git diff

Fix only syntax/import/compile errors.
Do not start new features.
Do not commit." 2>&1 \
      | tee "artifacts/codex-runs/overnight-physical-cycle-$cycle-compile-repair-$compile_repair.txt"
  done

  git status | tee "artifacts/codex-runs/overnight-physical-cycle-$cycle-git-status.txt"
  git diff --stat | tee "artifacts/codex-runs/overnight-physical-cycle-$cycle-diffstat.txt"

  git add .
  git commit -m "Overnight physical cycle $cycle Cisco iLO ESXi OVF NetApp prep" || echo "No changes to commit for cycle $cycle"

  echo "=== Overnight physical cycle $cycle finished at $(date) ===" | tee -a "$header"
  i=$((i + 1))
done

echo "Overnight Lab Builder run finished at $(date)"
