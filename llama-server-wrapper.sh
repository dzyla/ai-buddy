#!/bin/bash
set -euo pipefail

MODEL_PATH="${LLAMA_MODEL_PATH:?LLAMA_MODEL_PATH must be set}"
IDLE_TIMEOUT="${LLAMA_IDLE_TIMEOUT:-120}"
PORT=8080
CHECK_INTERVAL=10

echo "[llama-wrapper] Starting llama-server — model: $MODEL_PATH"
llama-server --model "$MODEL_PATH" --host 127.0.0.1 --port "$PORT" --ctx-size 4096 &
LLAMA_PID=$!

cleanup() {
    echo "[llama-wrapper] Shutting down llama-server (PID $LLAMA_PID)..."
    kill "$LLAMA_PID" 2>/dev/null || true
    wait "$LLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT TERM INT

echo "[llama-wrapper] Waiting for server ready..."
for _ in $(seq 1 60); do
    curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 && break
    sleep 2
done
echo "[llama-wrapper] Server ready. Monitoring idle (timeout: ${IDLE_TIMEOUT}s)..."

LAST_SEEN=$(date +%s)

while kill -0 "$LLAMA_PID" 2>/dev/null; do
    sleep "$CHECK_INTERVAL"
    CONNS=$(ss -tn | grep ":${PORT}.*ESTAB" 2>/dev/null | wc -l || echo 0)
    if [ "$CONNS" -gt 0 ]; then
        LAST_SEEN=$(date +%s)
    fi
    NOW=$(date +%s)
    IDLE=$(( NOW - LAST_SEEN ))
    if [ "$IDLE" -gt "$IDLE_TIMEOUT" ]; then
        echo "[llama-wrapper] Idle ${IDLE}s — stopping server."
        exit 0
    fi
done
