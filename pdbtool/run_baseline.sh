#!/bin/bash
# Baseline run: local 35B model develops the PDB chain-interaction tool via the harness.
set +e
cd /home/dzyla/Code/ai-buddy
export INFER_STEP_LIMIT=300
export INFER_TASK_TIMEOUT=3600
PROMPT="$(cat pdbtool/BASELINE_PROMPT.txt)"
echo "=== BASELINE RUN START $(date -Is) ===" >> pdbtool/baseline_run.log
time ./ai -y "$PROMPT" >> pdbtool/baseline_run.log 2>&1
echo "=== BASELINE RUN END exit=$? $(date -Is) ===" >> pdbtool/baseline_run.log
