#!/bin/bash
# Rerun with the improved harness (think cap, productivity watchdog, max_tokens=32768,
# reasoning-aware nudge, REUSE BEFORE WRITING) AND a prompt that directs building around
# the existing github.com/tikz/bio/pdb parser instead of reinventing it.
set +e
cd /home/dzyla/Code/ai-buddy
export INFER_STEP_LIMIT=200
export INFER_TASK_TIMEOUT=3600
export INFER_THINK_ACTION_LIMIT=3
export GOFLAGS=-mod=mod
PROMPT="$(cat pdbtool/REUSE_PROMPT.txt)"
echo "=== REUSE RUN START $(date -Is) ===" >> pdbtool/reuse_run.log
time ./ai -y "$PROMPT" >> pdbtool/reuse_run.log 2>&1
echo "=== REUSE RUN END exit=$? $(date -Is) ===" >> pdbtool/reuse_run.log
