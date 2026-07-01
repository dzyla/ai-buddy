#!/bin/bash
set -eo pipefail

if [ -f "${HOME}/.local/share/ai/env" ]; then
    set +u
    source "${HOME}/.local/share/ai/env"
    set -u
fi
set -u

MODEL_PATH="${LLAMA_MODEL_PATH:?LLAMA_MODEL_PATH must be set}"
IDLE_TIMEOUT="${LLAMA_IDLE_TIMEOUT:-120}"
PORT=8080
CHECK_INTERVAL=10

# ── Auto context-size calculation ─────────────────────────────────────────────
# Priority:
#   1. LLAMA_CTX_SIZE set explicitly → use as-is
#   2. Auto-detect from GPU VRAM (+ optionally CPU RAM for KV offload)
#
# Tune: LLAMA_CTX_SIZE_FACTOR=1.0   (float multiplier, e.g. 1.2 to go higher)
#       LLAMA_CTX_SIZE_MAX=131072   (hard cap, defaults to model's trained max)
# ─────────────────────────────────────────────────────────────────────────────
if [ -n "${LLAMA_CTX_SIZE:-}" ]; then
    CTX_SIZE="$LLAMA_CTX_SIZE"
    EXTRA_ARGS=""
    echo "[llama-wrapper] Using explicit ctx: ${CTX_SIZE}"
else
    VRAM_FREE_MB=0
    RAM_FREE_MB=0
    if command -v nvidia-smi >/dev/null 2>&1; then
        VRAM_FREE_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
    fi
    RAM_FREE_MB=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
    MODEL_MB=$(du -m "$MODEL_PATH" 2>/dev/null | cut -f1 || echo 4096)
    FACTOR="${LLAMA_CTX_SIZE_FACTOR:-1.0}"
    CTX_MAX="${LLAMA_CTX_SIZE_MAX:-131072}"

    CTX_SIZE=$(python3 - <<PYEOF
vram_free  = ${VRAM_FREE_MB:-0}
ram_free   = ${RAM_FREE_MB:-0}
model_mb   = ${MODEL_MB:-4096}
factor     = float("${FACTOR}")
ctx_max    = int("${CTX_MAX}")

# Empirical constants (measured on Gemma-4 7B Q4_K_XL, RTX 5080):
#   fixed_overhead = model_file_size × 0.703  (GPU: weights + activation buffers)
#   kv_gpu_rate    = 0.01518 MB/token         (15.5 KB/tok: GQA 2-head × 24 global layers × fp16)
fixed_mb   = int(model_mb * 0.703)
kv_gpu     = 0.01518    # MB per token (GPU)
kv_ram     = 0.00400    # MB per token for KV offloaded to CPU RAM
safety_gpu = 2048       # MiB reserved for display/OS on GPU
safety_ram = 4096       # MiB reserved for OS on CPU

gpu_budget   = max(0, vram_free - fixed_mb - safety_gpu)
ctx_gpu_only = int(gpu_budget / kv_gpu)

ram_budget     = max(0, ram_free - safety_ram)
ctx_ram_extend = int(ram_budget / kv_ram)

ctx_base = min(ctx_max, ctx_gpu_only + ctx_ram_extend)
ctx      = int(ctx_base * factor)
ctx      = min(ctx_max, max(4096, (ctx // 512) * 512))

source = "GPU" if ctx <= ctx_gpu_only else "GPU+RAM"
print(f"{ctx}|{source}|{ctx_gpu_only}|{ctx_ram_extend}")
PYEOF
)

    IFS='|' read -r CTX_SIZE CTX_SOURCE CTX_GPU _CTX_RAM <<< "$CTX_SIZE"
    EXTRA_ARGS=""
    if [ "$CTX_SOURCE" = "GPU+RAM" ]; then
        echo "[llama-wrapper] GPU-only fits ${CTX_GPU} tokens — extending to ${CTX_SIZE} via CPU RAM KV offload"
    fi
    echo "[llama-wrapper] Auto ctx: ${CTX_SIZE} [${CTX_SOURCE}] (GPU free: ${VRAM_FREE_MB} MiB, RAM free: ${RAM_FREE_MB} MiB, factor: ${FACTOR})"
fi
# ──────────────────────────────────────────────────────────────────────────────

if [[ "$(basename "$MODEL_PATH")" =~ [Mm][Tt][Pp] ]]; then
    EXTRA_ARGS="${EXTRA_ARGS} --spec-type draft-mtp"
    echo "[llama-wrapper] Enabling Multi-Token Prediction (MTP) speculative decoding."
fi

echo "[llama-wrapper] Starting llama-server — model: $(basename "$MODEL_PATH") ctx: ${CTX_SIZE}"
# shellcheck disable=SC2086
llama-server --model "$MODEL_PATH" --host 127.0.0.1 --port "$PORT" \
    --ctx-size "$CTX_SIZE" --n-gpu-layers 99 --flash-attn on --metrics $EXTRA_ARGS &
LLAMA_PID=$!

cleanup() {
    echo "[llama-wrapper] Shutting down llama-server (PID $LLAMA_PID)..."
    kill "$LLAMA_PID" 2>/dev/null || true
    wait "$LLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT TERM INT

echo "[llama-wrapper] Waiting for server ready..."
for _ in $(seq 1 60); do
    curl -sf --connect-timeout 2 --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
    sleep 2
done
echo "[llama-wrapper] Server ready. Monitoring idle (timeout: ${IDLE_TIMEOUT}s)..."

LAST_SEEN=$(date +%s)
LAST_TOKENS=0
METRICS_SUPPORTED=true

while kill -0 "$LLAMA_PID" 2>/dev/null; do
    sleep "$CHECK_INTERVAL"
    
    ACTIVITY_DETECTED=false
    
    if [ "$METRICS_SUPPORTED" = true ]; then
        METRICS=$(curl -sf --connect-timeout 2 --max-time 5 "http://127.0.0.1:${PORT}/metrics" 2>/dev/null || true)
        if [ -n "$METRICS" ] && echo "$METRICS" | grep -q "llamacpp:prompt_tokens_total"; then
            PROMPT_TOKENS=$(echo "$METRICS" | grep "^llamacpp:prompt_tokens_total" | awk '{print $2}')
            PRED_TOKENS=$(echo "$METRICS" | grep "^llamacpp:tokens_predicted_total" | awk '{print $2}')
            BUSY_CONNS=$(echo "$METRICS" | grep "^llamacpp:requests_processing" | awk '{print $2}')
            
            PROMPT_TOKENS="${PROMPT_TOKENS:-0}"
            PRED_TOKENS="${PRED_TOKENS:-0}"
            BUSY_CONNS="${BUSY_CONNS:-0}"
            
            TOTAL_TOKENS=$(( PROMPT_TOKENS + PRED_TOKENS ))
            
            # First run: initialize LAST_TOKENS with current count
            if [ "$LAST_TOKENS" -eq 0 ] && [ "$TOTAL_TOKENS" -gt 0 ]; then
                LAST_TOKENS="$TOTAL_TOKENS"
            fi
            
            if [ "$TOTAL_TOKENS" -gt "$LAST_TOKENS" ] || [ "$BUSY_CONNS" -gt 0 ]; then
                ACTIVITY_DETECTED=true
                LAST_TOKENS="$TOTAL_TOKENS"
            fi
        else
            # If metrics failed or didn't contain expected strings, fallback to ss check
            METRICS_SUPPORTED=false
        fi
    fi
    
    if [ "$METRICS_SUPPORTED" = false ]; then
        CONNS=$(ss -tn | { grep ":${PORT}.*ESTAB" 2>/dev/null || true; } | wc -l)
        if [ "$CONNS" -gt 0 ]; then
            ACTIVITY_DETECTED=true
        fi
    fi
    
    if [ "$ACTIVITY_DETECTED" = true ]; then
        LAST_SEEN=$(date +%s)
    fi
    
    NOW=$(date +%s)
    IDLE=$(( NOW - LAST_SEEN ))
    if [ "$IDLE" -gt "$IDLE_TIMEOUT" ]; then
        echo "[llama-wrapper] Idle ${IDLE}s — stopping server."
        exit 0
    fi
done
