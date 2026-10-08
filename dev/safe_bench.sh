#!/usr/bin/env bash
# Safe local-model benchmark: ONE model load, VRAM headroom check, a watchdog that stops
# llama-server before the GPU driver can wedge, and a restore of the starting config on ANY exit.
#
#   dev/safe_bench.sh <label> <model.gguf> <ctx> <n_cpu_moe> [tiers=easy] [extra evals args...]
#   e.g. dev/safe_bench.sh qwen27b_iq2xxs_96k Qwen3.8-27B-UD-IQ2_XXS.gguf 98304 0 easy
#
# Env knobs: SAFE_IDLE_MAX_MIB (max VRAM used after load, default 14336 = ~2 GB free on 16 GB),
#            SAFE_HARD_MAX_MIB (watchdog kill line during the run, default 15360),
#            SAFE_OUT (results dir, default ~/ai-bench/<timestamp>-<label>).
# Never stacks a speculative draft: LLAMA_MTP=0 and no draft path for the run.
set -u
LABEL=${1:?label}; MODEL=${2:?model file}; CTX=${3:?ctx}; MOE=${4:?n_cpu_moe}; TIERS=${5:-easy}; shift $(( $# < 5 ? $# : 5 ))
M="$HOME/.local/share/ai/models"; ENV="$HOME/.local/share/ai/env"; UNIT="$HOME/.config/systemd/user/llama-server.service"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
IDLE_MAX=${SAFE_IDLE_MAX_MIB:-14336}; HARD_MAX=${SAFE_HARD_MAX_MIB:-15360}
OUT=${SAFE_OUT:-$HOME/ai-bench/$(date +%Y%m%d-%H%M%S)-$LABEL}; mkdir -p "$OUT"
ABORT="$OUT/ABORTED"; WD_PID=""
log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/driver.log"; }
vram(){ timeout 5 nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '; }
stop_server(){ systemctl --user stop llama-server 2>/dev/null; pkill -9 -x llama-server 2>/dev/null; true; }
setenv(){ if grep -q "^export $1=" "$ENV"; then sed -i "s|^export $1=.*|export $1=\"$2\"|" "$ENV"; else echo "export $1=\"$2\"" >> "$ENV"; fi; }

[ -f "$M/$MODEL" ] || { echo "missing $M/$MODEL"; exit 2; }
cp -p "$ENV" "$OUT/env.before"; cp -p "$UNIT" "$OUT/unit.before"

restore(){
  [ -n "$WD_PID" ] && kill "$WD_PID" 2>/dev/null
  stop_server
  cp -p "$OUT/env.before" "$ENV"; cp -p "$OUT/unit.before" "$UNIT"; systemctl --user daemon-reload
  if cmp -s "$OUT/env.before" "$ENV" && cmp -s "$OUT/unit.before" "$UNIT"; then
    log "restored starting config (server left stopped; the proxy starts it on demand)"
  else log "WARNING: restore mismatch — check $ENV and $UNIT"; fi
}
trap restore EXIT
trap 'log "interrupted"; exit 130' INT TERM

# --- configure (files only; nothing loads yet) ---
setenv LLAMA_MODEL_PATH "$M/$MODEL"; setenv LLAMA_CTX_SIZE "$CTX"; setenv LLAMA_N_CPU_MOE "$MOE"
setenv LLAMA_N_PARALLEL 1; setenv LLAMA_N_GPU_LAYERS 99; setenv LLAMA_MTP 0; setenv LLAMA_SPEC_TYPE draft-mtp
sed -i '/^export LLAMA_MTP_DRAFT_PATH=/d; /^export LLAMA_DRAFT_MODEL_PATH=/d' "$ENV"
sed -i "s|^Environment=LLAMA_MODEL_PATH=.*|Environment=LLAMA_MODEL_PATH=$M/$MODEL|; s|^Environment=LLAMA_CTX_SIZE=.*|Environment=LLAMA_CTX_SIZE=$CTX|" "$UNIT"
systemctl --user daemon-reload

# --- watchdog: stop everything if VRAM crosses HARD_MAX or nvidia-smi stops answering ---
(
  while sleep 2; do
    v=$(vram)
    if [ -z "$v" ] || [ "$v" -gt "$HARD_MAX" ]; then
      echo "[$(date +%H:%M:%S)] WATCHDOG: vram='${v:-no answer}' (limit $HARD_MAX MiB) — stopping llama-server and the run" >> "$OUT/driver.log"
      touch "$ABORT"; stop_server; pkill -f "evals/run_evals.py" 2>/dev/null; exit 0
    fi
    echo "$(date +%H:%M:%S) $v" >> "$OUT/vram.log"
  done
) & WD_PID=$!

# --- load ONCE ---
before=$(vram); log "=== $LABEL: $MODEL ctx=$CTX n_cpu_moe=$MOE (vram before load: ${before} MiB)"
systemctl --user reset-failed llama-server 2>/dev/null; systemctl --user start llama-server
ok=""
for i in $(seq 1 90); do
  [ -f "$ABORT" ] && break
  curl -s -m 2 localhost:8081/health 2>/dev/null | grep -q '"ok"' && { ok=1; break; }
  systemctl --user is-failed --quiet llama-server && break
  sleep 2
done
[ -n "$ok" ] || { log "server did not come up (or aborted):"; journalctl --user -u llama-server -n 15 --no-pager >> "$OUT/driver.log"; exit 1; }
sleep 3; idle=$(vram); log "loaded: vram used ${idle} MiB (idle limit $IDLE_MAX)"
[ "${idle:-99999}" -le "$IDLE_MAX" ] || { log "REFUSED: less than ~2 GB headroom after load"; exit 1; }

# --- run tiers one at a time ---
for tier in ${TIERS//,/ }; do
  [ -f "$ABORT" ] && break
  log "--- tier $tier"
  ( cd "$REPO" && python3 evals/run_evals.py --engine py --base-url http://localhost:8080/v1/ --model "$LABEL" \
      --tier "$tier" --json "$OUT/$tier.json" "$@" ) 2>&1 | tee -a "$OUT/$tier.txt" | grep -E '^\[[0-9]+/|^(easy|medium|hard|overall) '
  log "tier $tier: $(grep -E "^$tier " "$OUT/$tier.txt" | tail -1)  (peak vram $(sort -k2 -n "$OUT/vram.log" | tail -1 | cut -d' ' -f2) MiB)"
done
[ -f "$ABORT" ] && { log "ABORTED by watchdog"; exit 3; }
log "done — results in $OUT"
