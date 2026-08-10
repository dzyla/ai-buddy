#!/bin/bash
# Rerun with the IMPROVED harness (think-length cap, productivity watchdog, truncation nudge,
# THINK DISCIPLINE prompt directive). Same task/prompt as baseline for a fair comparison.
set +e
cd /home/dzyla/Code/ai-buddy
export INFER_STEP_LIMIT=300
export INFER_TASK_TIMEOUT=3600
export INFER_THINK_ACTION_LIMIT=3
PROMPT="$(cat pdbtool/BASELINE_PROMPT.txt)"
echo "=== RERUN START $(date -Is) ===" >> pdbtool/rerun_run.log
time ./ai -y "$PROMPT" >> pdbtool/rerun_run.log 2>&1
echo "=== RERUN END exit=$? $(date -Is) ===" >> pdbtool/rerun_run.log
