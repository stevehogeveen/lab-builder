#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${1:-$HOME/lab-builder-react}"
PROMPT_FILE="$REPO_DIR/.codex/prompts/overnight-continuous-improvement.md"
LOG_DIR="$REPO_DIR/artifacts/codex-runs"
STOP_FILE="$REPO_DIR/artifacts/codex-runs/STOP_CODEX_LOOP"
FINALIZE_SCRIPT="$REPO_DIR/scripts/finalize-overnight-run"

mkdir -p "$LOG_DIR"

cd "$REPO_DIR"

# End at the next 5:20 AM, not "any time after 05:20".
END_EPOCH="$(date -d 'tomorrow 05:20' +%s)"

echo "Starting Codex overnight improvement loop at $(date)"
echo "Repo: $REPO_DIR"
echo "Stop file: $STOP_FILE"
echo "Loop will stop at: $(date -d "@$END_EPOCH")"

cycle=1

while true; do
  now_epoch="$(date +%s)"

  if [ -f "$STOP_FILE" ]; then
    echo "STOP_CODEX_LOOP found. Stopping at $(date)."
    break
  fi

  if [ "$now_epoch" -ge "$END_EPOCH" ]; then
    echo "Reached finalization window at $(date). Stopping Codex loop."
    break
  fi

  stamp="$(date +%Y%m%d-%H%M%S)"
  cycle_log="$LOG_DIR/continuous-improvement-cycle-${cycle}-${stamp}.md"

  echo
  echo "============================================================"
  echo "Cycle $cycle started at $(date)"
  echo "Log: $cycle_log"
  echo "============================================================"

  {
    echo "# Continuous Improvement Cycle $cycle"
    echo
    echo "Started: $(date)"
    echo
    echo "## Git status before"
    echo '```'
    git status -sb || true
    echo '```'
    echo
  } > "$cycle_log"

  git fetch origin >> "$cycle_log" 2>&1 || true

  set +e
  codex exec --dangerously-bypass-approvals-and-sandbox "$(cat "$PROMPT_FILE")" 2>&1 | tee -a "$cycle_log"
  codex_rc=${PIPESTATUS[0]}
  set -e

  {
    echo
    echo "## Codex exit code"
    echo "$codex_rc"
    echo
    echo "## Git status after"
    echo '```'
    git status -sb || true
    echo '```'
    echo
    echo "Finished: $(date)"
  } >> "$cycle_log"

  if [ "$codex_rc" -ne 0 ]; then
    echo "Codex cycle $cycle exited with $codex_rc. Continuing after cooldown."
  fi

  cycle=$((cycle + 1))

  echo "Cooldown: sleeping 15 minutes before next cycle..."
  sleep 900
done

echo
echo "Running final overnight finalizer if available..."

if [ -x "$FINALIZE_SCRIPT" ]; then
  "$FINALIZE_SCRIPT" 2>&1 | tee "$LOG_DIR/finalize-from-codex-loop-$(date +%Y%m%d-%H%M%S).md" || true
else
  echo "Finalizer script not found or not executable: $FINALIZE_SCRIPT"
fi

echo "Codex overnight improvement loop ended at $(date)."
