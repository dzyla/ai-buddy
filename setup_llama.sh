#!/bin/bash
set -euo pipefail

# ── Defaults (override with env vars before running) ─────────────────────────
# LLAMA_REPO / LLAMA_FILE  — which model to download if none already exists
# HF_TOKEN                 — Bearer token for gated HuggingFace repos
LLAMA_REPO="${LLAMA_REPO:-unsloth/gemma-4-E4B-it-qat-GGUF}"
LLAMA_FILE="${LLAMA_FILE:-gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf}"

LLAMA_SOURCE="${HOME}/.local/share/ai/llama.cpp"
MODEL_DIR="${HOME}/.local/share/ai/models"
BIN_DIR="${HOME}/.local/bin"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
PORT=8080
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Build dependencies ─────────────────────────────────────────────────────
echo "==> Checking build dependencies..."
MISSING=()
for cmd in cmake git curl python3 gcc; do
    command -v "$cmd" &>/dev/null || MISSING+=("$cmd")
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "==> Installing: ${MISSING[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y cmake git curl python3 build-essential gcc libcurl4-openssl-dev
fi

mkdir -p "$LLAMA_SOURCE" "$MODEL_DIR" "$BIN_DIR" "$SYSTEMD_DIR"

# ── 2. Clone llama.cpp (skip if already present) ─────────────────────────────
if [ ! -d "${LLAMA_SOURCE}/.git" ]; then
    echo "==> Cloning llama.cpp..."
    git clone --depth=1 https://github.com/ggml-org/llama.cpp "$LLAMA_SOURCE"
else
    echo "==> llama.cpp already cloned — skipping."
fi

# ── 3. Build llama-server with best available GPU backend ────────────────────
if [ -f "${BIN_DIR}/llama-server" ]; then
    echo "==> llama-server already built — skipping build."
    echo "    (Remove ${BIN_DIR}/llama-server to force a rebuild.)"
else
    GPU_FLAGS="-DGGML_CUDA=OFF -DGGML_HIP=OFF -DGGML_VULKAN=OFF"
    if command -v nvcc &>/dev/null; then
        echo "==> CUDA detected — building with CUDA support."
        GPU_FLAGS="-DGGML_CUDA=ON"
    elif command -v hipcc &>/dev/null; then
        echo "==> ROCm detected — building with HIP support."
        GPU_FLAGS="-DGGML_HIP=ON"
    elif pkg-config --exists vulkan 2>/dev/null || [ -f /usr/include/vulkan/vulkan.h ]; then
        echo "==> Vulkan detected — building with Vulkan support."
        GPU_FLAGS="-DGGML_VULKAN=ON"
    else
        echo "==> No GPU backend found — building CPU-only."
    fi

    echo "==> Building llama-server (may take a few minutes)..."
    cmake -B "${LLAMA_SOURCE}/build" -S "$LLAMA_SOURCE" \
        -DCMAKE_BUILD_TYPE=Release \
        $GPU_FLAGS
    cmake --build "${LLAMA_SOURCE}/build" --config Release \
        --target llama-server -j"$(nproc)"
    cp "${LLAMA_SOURCE}/build/bin/llama-server" "$BIN_DIR/"
    chmod +x "${BIN_DIR}/llama-server"
    echo "==> llama-server installed to ${BIN_DIR}/llama-server"
fi

# ── 4. Build and install the ai CLI ───────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/ai.c" ] && [ -f "${SCRIPT_DIR}/cJSON.c" ]; then
    echo "==> Building ai binary..."
    gcc -O2 -o "${SCRIPT_DIR}/ai" "${SCRIPT_DIR}/ai.c" "${SCRIPT_DIR}/cJSON.c" -lcurl
    sudo cp "${SCRIPT_DIR}/ai" /usr/local/bin/ai
    sudo chmod +x /usr/local/bin/ai
    sudo cp "${SCRIPT_DIR}/ai_mcp.py" /usr/local/bin/ai_mcp.py
    sudo chmod +x /usr/local/bin/ai_mcp.py
    echo "==> ai binary installed to /usr/local/bin/ai"
fi

# ── 5. Find or download model ─────────────────────────────────────────────────
# Prefer an existing regular (non-MTP, non-mmproj) GGUF
EXISTING_MODEL=$(find "$MODEL_DIR" -name "*.gguf" \
    ! -name "mmproj-*.gguf" \
    ! -path "*/MTP/*" \
    ! -name "*-MTP.gguf" \
    2>/dev/null | sort | head -1)

if [ -n "$EXISTING_MODEL" ]; then
    MODEL_PATH="$EXISTING_MODEL"
    echo "==> Using existing model: $MODEL_PATH"
else
    MODEL_PATH="${MODEL_DIR}/${LLAMA_FILE}"
    if [ -f "$MODEL_PATH" ]; then
        echo "==> Model already present: $MODEL_PATH"
    else
        echo "==> Downloading ${LLAMA_FILE} from ${LLAMA_REPO}..."
        mkdir -p "$(dirname "$MODEL_PATH")"
        HF_TOKEN_HEADER=""
        [ -n "${HF_TOKEN:-}" ] && HF_TOKEN_HEADER="Authorization: Bearer ${HF_TOKEN}"
        curl -L --progress-bar \
            ${HF_TOKEN_HEADER:+-H "$HF_TOKEN_HEADER"} \
            "https://huggingface.co/${LLAMA_REPO}/resolve/main/${LLAMA_FILE}" \
            -o "$MODEL_PATH"
        echo "==> Downloaded: $MODEL_PATH"
    fi
fi

# ── 6. Install wrapper and on-demand ai launcher ─────────────────────────────
cp "${SCRIPT_DIR}/llama-server-wrapper.sh" "${BIN_DIR}/llama-server-wrapper.sh"
chmod +x "${BIN_DIR}/llama-server-wrapper.sh"

cat > "${BIN_DIR}/ai" <<AI_EOF
#!/bin/bash
# On-demand llama-server launcher — starts the server if not running,
# then delegates to the real ai binary.
PORT=${PORT}
MODEL_PATH="${MODEL_PATH}"
WRAPPER="${BIN_DIR}/llama-server-wrapper.sh"
LOG="/tmp/llama-server.log"

if ! curl -sf "http://127.0.0.1:\${PORT}/health" >/dev/null 2>&1; then
    echo "[ai] Starting llama-server..." >&2
    LLAMA_MODEL_PATH="\$MODEL_PATH" nohup "\$WRAPPER" >"\$LOG" 2>&1 &
    disown
    for i in \$(seq 1 60); do
        curl -sf "http://127.0.0.1:\${PORT}/health" >/dev/null 2>&1 && break
        sleep 2
    done
    if ! curl -sf "http://127.0.0.1:\${PORT}/health" >/dev/null 2>&1; then
        echo "[ai] Server failed to start. Check \$LOG" >&2
        exit 1
    fi
    echo "[ai] Server ready." >&2
fi

exec /usr/local/bin/ai "\$@"
AI_EOF
chmod +x "${BIN_DIR}/ai"
echo "==> On-demand ai launcher installed to ${BIN_DIR}/ai"

# ── 7. Write systemd service unit (for manual management) ────────────────────
cat > "${SYSTEMD_DIR}/llama-server.service" <<SERVICE_EOF
[Unit]
Description=llama-server (on-demand, idle-unload)

[Service]
Type=simple
Environment=LLAMA_MODEL_PATH=${MODEL_PATH}
Environment=LLAMA_IDLE_TIMEOUT=120
ExecStart=${BIN_DIR}/llama-server-wrapper.sh
Restart=no
TimeoutStartSec=120
StandardOutput=journal
StandardError=journal
SERVICE_EOF

systemctl --user daemon-reload
echo "==> systemd service unit written (start manually with: systemctl --user start llama-server)"

# ── 8. Patch shell profiles (idempotent) ─────────────────────────────────────
INFER_BLOCK="# llama.cpp local inference (managed by setup_llama.sh)
export INFER_BASE_URL=\"http://localhost:${PORT}/v1/\"
export INFER_API_KEY=\"not-needed\"
export INFER_MODEL=\"llama\"
export INFER_TOOL_CHOICE=auto
export PUBMED_API_KEY=\"myapp_kZnDpemyN9z43CqNrOYEE-LhAH9_UsxhWTavLkWv22Y\""

for PROFILE in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    [ -f "$PROFILE" ] || continue
    sed -i '/^# .*\(gemma4\|inference\|ai configuration\)/d' "$PROFILE"
    sed -i '/^export INFER_BASE_URL=/d' "$PROFILE"
    sed -i '/^export INFER_API_KEY=/d' "$PROFILE"
    sed -i '/^export INFER_MODEL=/d' "$PROFILE"
    sed -i '/^export INFER_TOOL_CHOICE=/d' "$PROFILE"
    sed -i '/^export PUBMED_API_KEY=/d' "$PROFILE"
    printf '\n%s\n' "$INFER_BLOCK" >> "$PROFILE"
    echo "==> Patched $PROFILE"
done

echo ""
echo "========================================"
echo "Setup complete!"
echo "  Model:   $MODEL_PATH"
echo "  Server:  http://localhost:${PORT}/v1/ (auto-starts on first 'ai' call)"
echo "  Idle:    120s auto-shutdown (restart is automatic on next call)"
echo ""
echo "Run now:"
echo "  source ~/.bashrc && ai \"hello\""
echo ""
echo "Server logs: /tmp/llama-server.log"
echo "========================================"
