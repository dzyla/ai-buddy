#!/bin/bash
set -euo pipefail

LLAMA_SOURCE="${HOME}/.local/share/ai/llama.cpp"
MODEL_DIR="${HOME}/.local/share/ai/models"
BIN_DIR="${HOME}/.local/bin"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
PORT=8080

# ── 1. Build dependencies ─────────────────────────────────────────────────────
echo "==> Checking build dependencies..."
MISSING=()
for cmd in cmake git curl wget python3; do
    command -v "$cmd" &>/dev/null || MISSING+=("$cmd")
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "==> Installing: ${MISSING[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y cmake git curl wget python3 build-essential
fi

# ── 2. GPU detection ──────────────────────────────────────────────────────────
GPU_FLAGS=""
if command -v nvcc &>/dev/null; then
    echo "==> CUDA detected — building with -DGGML_CUDA=ON"
    GPU_FLAGS="-DGGML_CUDA=ON"
elif command -v hipcc &>/dev/null; then
    echo "==> ROCm detected — building with -DGGML_HIP=ON"
    GPU_FLAGS="-DGGML_HIP=ON"
else
    echo "==> No GPU compiler found — CPU-only build"
fi

# ── 3. Clone / update llama.cpp ───────────────────────────────────────────────
mkdir -p "$LLAMA_SOURCE" "$MODEL_DIR" "$BIN_DIR" "$SYSTEMD_DIR"

if [ ! -d "${LLAMA_SOURCE}/.git" ]; then
    echo "==> Cloning llama.cpp..."
    git clone https://github.com/ggml-org/llama.cpp "$LLAMA_SOURCE"
else
    echo "==> llama.cpp already present — pulling latest..."
    git -C "$LLAMA_SOURCE" pull --ff-only
fi

echo "==> Building llama-server (may take several minutes)..."
cmake -B "${LLAMA_SOURCE}/build" -S "$LLAMA_SOURCE" \
    -DCMAKE_BUILD_TYPE=Release \
    ${GPU_FLAGS:+"$GPU_FLAGS"}
cmake --build "${LLAMA_SOURCE}/build" --config Release \
    --target llama-server -j"$(nproc)"

cp "${LLAMA_SOURCE}/build/bin/llama-server" "$BIN_DIR/"
chmod +x "${BIN_DIR}/llama-server"
echo "==> llama-server installed to ${BIN_DIR}/llama-server"

# ── 4. Model selection ────────────────────────────────────────────────────────
PRESET_REPOS=(
    "unsloth/gemma-4-12b-it-GGUF"
    "Qwen/Qwen3.6-35B-A3B"
    "unsloth/gemma-4-E4B-it-qat-GGUF"
)

if [ -n "${1:-}" ]; then
    CHOSEN_REPO="$1"
    echo "==> Using repo from argument: $CHOSEN_REPO"
else
    echo ""
    echo "Select a model to download:"
    for i in "${!PRESET_REPOS[@]}"; do
        echo "  $((i+1))) ${PRESET_REPOS[$i]}"
    done
    echo "  4) Enter a custom HuggingFace repo"
    read -rp "Choice [1-4]: " MODEL_CHOICE
    case "$MODEL_CHOICE" in
        1) CHOSEN_REPO="${PRESET_REPOS[0]}" ;;
        2) CHOSEN_REPO="${PRESET_REPOS[1]}" ;;
        3) CHOSEN_REPO="${PRESET_REPOS[2]}" ;;
        4) read -rp "HuggingFace repo (e.g. user/repo-GGUF): " CHOSEN_REPO ;;
        *) echo "Invalid choice."; exit 1 ;;
    esac
fi

# ── 5. List .gguf files from HF API ──────────────────────────────────────────
echo "==> Fetching file list for: ${CHOSEN_REPO}..."
HF_TOKEN_HEADER=""
if [ -n "${HF_TOKEN:-}" ]; then
    HF_TOKEN_HEADER="Authorization: Bearer ${HF_TOKEN}"
fi

GGUF_FILES=$(curl -sf \
    ${HF_TOKEN_HEADER:+-H "$HF_TOKEN_HEADER"} \
    "https://huggingface.co/api/models/${CHOSEN_REPO}" | \
    python3 -c "
import json, sys
data = json.load(sys.stdin)
files = [s['rfilename'] for s in data.get('siblings', []) if s['rfilename'].lower().endswith('.gguf')]
for i, f in enumerate(files, 1):
    print(f'{i}) {f}')
")

if [ -z "$GGUF_FILES" ]; then
    echo "Error: no .gguf files found in ${CHOSEN_REPO}."
    echo "For gated repos, set the HF_TOKEN environment variable and retry."
    exit 1
fi

echo ""
echo "Available GGUF files:"
echo "$GGUF_FILES"
TOTAL=$(echo "$GGUF_FILES" | wc -l)
read -rp "Pick a file [1-${TOTAL}]: " FILE_CHOICE

CHOSEN_FILE=$(echo "$GGUF_FILES" | FILE_CHOICE="${FILE_CHOICE}" python3 -c "
import sys, os
lines = sys.stdin.read().strip().split('\n')
idx = int(os.environ['FILE_CHOICE']) - 1
if idx < 0 or idx >= len(lines):
    raise SystemExit('Invalid selection: ' + os.environ['FILE_CHOICE'])
print(lines[idx].split(') ', 1)[1])
")

echo "==> Selected: $CHOSEN_FILE"

# ── 6. Download model ─────────────────────────────────────────────────────────
MODEL_PATH="${MODEL_DIR}/${CHOSEN_FILE}"
if [ -f "$MODEL_PATH" ]; then
    echo "==> Model already exists at ${MODEL_PATH}, skipping download."
else
    echo "==> Downloading ${CHOSEN_FILE}..."
    mkdir -p "$(dirname "$MODEL_PATH")"
    curl -L --progress-bar \
        ${HF_TOKEN_HEADER:+-H "$HF_TOKEN_HEADER"} \
        "https://huggingface.co/${CHOSEN_REPO}/resolve/main/${CHOSEN_FILE}" \
        -o "$MODEL_PATH"
fi
echo "==> Model ready: $MODEL_PATH"

# ── 7. Install wrapper script ─────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/llama-server-wrapper.sh" "${BIN_DIR}/"
chmod +x "${BIN_DIR}/llama-server-wrapper.sh"

# ── 8. Write systemd socket unit ──────────────────────────────────────────────
cat > "${SYSTEMD_DIR}/llama-server.socket" <<SOCKET_EOF
[Unit]
Description=llama-server on-demand socket

[Socket]
ListenStream=127.0.0.1:${PORT}
Accept=no

[Install]
WantedBy=sockets.target
SOCKET_EOF

# ── 9. Write systemd service unit ─────────────────────────────────────────────
cat > "${SYSTEMD_DIR}/llama-server.service" <<SERVICE_EOF
[Unit]
Description=llama-server (on-demand, idle-unload)
Requires=llama-server.socket
After=llama-server.socket

[Service]
Type=simple
Environment=LLAMA_MODEL_PATH=${MODEL_PATH}
Environment=LLAMA_IDLE_TIMEOUT=120
ExecStartPre=/bin/bash -c 'systemctl --user stop llama-server.socket || true'
ExecStart=${BIN_DIR}/llama-server-wrapper.sh
ExecStopPost=/bin/bash -c 'systemctl --user start llama-server.socket || true'
Restart=no
StandardOutput=journal
StandardError=journal
SERVICE_EOF

# ── 10. Enable socket ─────────────────────────────────────────────────────────
systemctl --user daemon-reload
systemctl --user enable --now llama-server.socket
echo "==> Socket unit enabled — llama-server starts on first connection."

# ── 11. Write INFER_* env vars to shell profiles ──────────────────────────────
INFER_BLOCK="
# llama.cpp local inference (added by llama-install.sh)
export INFER_BASE_URL=\"http://localhost:${PORT}/v1/\"
export INFER_API_KEY=\"not-needed\"
export INFER_MODEL=\"llama\"
"

for PROFILE in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    if [ -f "$PROFILE" ] && ! grep -q "INFER_BASE_URL.*localhost:${PORT}" "$PROFILE"; then
        printf '%s\n' "$INFER_BLOCK" >> "$PROFILE"
        echo "==> Updated $PROFILE"
    fi
done

echo ""
echo "========================================"
echo "llama.cpp setup complete!"
echo "  Model:  $MODEL_PATH"
echo "  Server: http://localhost:${PORT}/v1/"
echo "  Idle:   120s default (edit LLAMA_IDLE_TIMEOUT in ${SYSTEMD_DIR}/llama-server.service)"
echo ""
echo "Apply env:  source ~/.bashrc"
echo "Test:       ai \"hello from llama\""
echo "Logs:       journalctl --user -u llama-server -f"
echo "========================================"
