#!/bin/bash
# install.sh — build and install the ai CLI
#
# Usage:
#   ./install.sh          Build and install ai CLI to ~/.local/bin
#   ./install.sh llama    Also set up a local llama.cpp inference server
#   ./install.sh snap     Also detect and configure an installed AI snap
#
# Everything installs to ~/.local/bin — no sudo required.
# To uninstall: rm ~/.local/bin/{ai,ai_mcp.py,ai-backend,llama-server,llama-server-wrapper.sh,pubmed_mcp_server.py}

set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
DATA_DIR="${HOME}/.local/share/ai"
MODEL_DIR="${DATA_DIR}/models"
LLAMA_SRC="${DATA_DIR}/llama.cpp"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
SKILLS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.agents/skills"
SKILLS_DST="${HOME}/.config/ai/skills"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8080

mkdir -p "$BIN_DIR" "$DATA_DIR" "$MODEL_DIR" "$SYSTEMD_DIR" "$SKILLS_DST"

# ── Ensure ~/.local/bin is in PATH ────────────────────────────────────────────
if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
    echo ""
    echo "NOTE: ${BIN_DIR} is not in your PATH."
    echo "Add this to ~/.bashrc or ~/.zshrc, then re-open your terminal:"
    echo "  export PATH=\"\${HOME}/.local/bin:\${PATH}\""
    echo ""
fi

# ── 1. Build ai binary ────────────────────────────────────────────────────────
echo "==> Building ai..."
missing=()
for cmd in gcc python3; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
done
if ! pkg-config --exists libcurl 2>/dev/null && ! dpkg -l libcurl4-openssl-dev &>/dev/null 2>&1; then
    missing+=(libcurl4-openssl-dev)
fi
if [ "${#missing[@]}" -gt 0 ]; then
    echo "==> Installing missing packages: ${missing[*]}"
    sudo apt-get install -y "${missing[@]}"
fi

gcc -O2 -o "${SCRIPT_DIR}/ai" "${SCRIPT_DIR}/ai.c" "${SCRIPT_DIR}/cJSON.c" -lcurl
echo "==> Built: ${SCRIPT_DIR}/ai"

# ── 2. Install to ~/.local/bin ────────────────────────────────────────────────
echo "==> Installing to ${BIN_DIR}..."
cp "${SCRIPT_DIR}/ai"             "${BIN_DIR}/ai"
cp "${SCRIPT_DIR}/ai_mcp.py"      "${BIN_DIR}/ai_mcp.py"
cp "${SCRIPT_DIR}/ai-backend"     "${BIN_DIR}/ai-backend"
cp "${SCRIPT_DIR}/pubmed_mcp_server.py" "${BIN_DIR}/pubmed_mcp_server.py"
cp "${SCRIPT_DIR}/deep_research.py"   "${BIN_DIR}/deep_research.py"
chmod +x "${BIN_DIR}/ai" "${BIN_DIR}/ai_mcp.py" "${BIN_DIR}/ai-backend" "${BIN_DIR}/pubmed_mcp_server.py" "${BIN_DIR}/deep_research.py"
echo "==> Installed: ai  ai_mcp.py  ai-backend  pubmed_mcp_server.py  deep_research.py"

# ── 3. Python optional deps ───────────────────────────────────────────────────
echo "==> Installing optional Python deps (curl-cffi, playwright-stealth)..."
pip install --quiet "curl-cffi>=0.7" playwright-stealth 2>/dev/null || true

# ── 4. Sync skills ────────────────────────────────────────────────────────────
if [ -d "$SKILLS_SRC" ]; then
    cp -r "${SKILLS_SRC}/." "${SKILLS_DST}/"
    count=$(ls "$SKILLS_SRC" | wc -l)
    echo "==> Synced ${count} skill(s) to ${SKILLS_DST}"
fi

# ── 4. Ensure ~/.bashrc / ~/.zshrc sources ~/.config/ai/env ──────────────────
for profile in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
    [ -f "$profile" ] || continue
    if grep -q 'config/ai/env' "$profile"; then
        echo "==> ${profile}: already sources ~/.config/ai/env"
    else
        printf '\n# ai backend — switch with: ai-backend [qwen3-6|gemma4|llama|auto]\n[ -f "${HOME}/.config/ai/env" ] && source "${HOME}/.config/ai/env"\n' >> "$profile"
        echo "==> Patched ${profile}"
    fi
done

# ── Subcommand: snap ──────────────────────────────────────────────────────────
if [ "${1:-}" = "snap" ]; then
    echo ""
    echo "==> Detecting active AI snap..."
    "${BIN_DIR}/ai-backend" auto
    echo ""
    echo "Apply: source ~/.config/ai/env"
    exit 0
fi

# ── Subcommand: llama ─────────────────────────────────────────────────────────
if [ "${1:-}" = "llama" ]; then
    echo ""
    echo "==> Setting up local llama.cpp inference server..."

    # Build dependencies
    missing_llama=()
    for cmd in cmake git curl python3; do
        command -v "$cmd" &>/dev/null || missing_llama+=("$cmd")
    done
    if [ "${#missing_llama[@]}" -gt 0 ]; then
        echo "==> Installing: ${missing_llama[*]}"
        sudo apt-get install -y cmake git curl python3 build-essential
    fi

    # Clone or update llama.cpp
    if [ ! -d "${LLAMA_SRC}/.git" ]; then
        echo "==> Cloning llama.cpp..."
        git clone --depth=1 https://github.com/ggml-org/llama.cpp "$LLAMA_SRC"
    else
        echo "==> llama.cpp already cloned — skipping."
    fi

    # Build with best available GPU backend
    if [ ! -f "${BIN_DIR}/llama-server" ]; then
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
        echo "==> Building llama-server (this takes a few minutes)..."
        cmake -B "${LLAMA_SRC}/build" -S "$LLAMA_SRC" \
            -DCMAKE_BUILD_TYPE=Release $GPU_FLAGS
        cmake --build "${LLAMA_SRC}/build" --config Release \
            --target llama-server -j"$(nproc)"
        cp "${LLAMA_SRC}/build/bin/llama-server" "${BIN_DIR}/"
        chmod +x "${BIN_DIR}/llama-server"
        echo "==> llama-server installed to ${BIN_DIR}/llama-server"
    else
        echo "==> llama-server already built — skipping. Remove ${BIN_DIR}/llama-server to force rebuild."
    fi

    # Install wrapper
    cp "${SCRIPT_DIR}/llama-server-wrapper.sh" "${BIN_DIR}/llama-server-wrapper.sh"
    chmod +x "${BIN_DIR}/llama-server-wrapper.sh"

    # Find or download model
    EXISTING_MODEL=$(find "$MODEL_DIR" -name "*.gguf" \
        ! -name "mmproj-*.gguf" ! -path "*/MTP/*" 2>/dev/null | sort | head -1)

    if [ -n "$EXISTING_MODEL" ]; then
        MODEL_PATH="$EXISTING_MODEL"
        echo "==> Using existing model: $MODEL_PATH"
    else
        echo ""
        echo "Select a model to download:"
        PRESET_REPOS=(
            "unsloth/gemma-4-E4B-it-qat-GGUF"
            "unsloth/gemma-4-12b-it-GGUF"
            "Qwen/Qwen3.6-35B-A3B"
        )
        for i in "${!PRESET_REPOS[@]}"; do
            echo "  $((i+1))) ${PRESET_REPOS[$i]}"
        done
        echo "  $((${#PRESET_REPOS[@]}+1))) Enter a custom HuggingFace repo"
        read -rp "Choice [1-$((${#PRESET_REPOS[@]}+1))]: " MODEL_CHOICE

        if [ "$MODEL_CHOICE" -le "${#PRESET_REPOS[@]}" ] 2>/dev/null; then
            CHOSEN_REPO="${PRESET_REPOS[$((MODEL_CHOICE-1))]}"
        else
            read -rp "HuggingFace repo (e.g. user/repo-GGUF): " CHOSEN_REPO
        fi

        echo "==> Fetching file list for: ${CHOSEN_REPO}..."
        HF_TOKEN_HEADER=""
        [ -n "${HF_TOKEN:-}" ] && HF_TOKEN_HEADER="Authorization: Bearer ${HF_TOKEN}"

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
            echo "For gated repos, set HF_TOKEN and retry."
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
    raise SystemExit('Invalid selection')
print(lines[idx].split(') ', 1)[1])
")
        MODEL_PATH="${MODEL_DIR}/${CHOSEN_FILE}"
        echo "==> Downloading ${CHOSEN_FILE}..."
        curl -L --progress-bar \
            ${HF_TOKEN_HEADER:+-H "$HF_TOKEN_HEADER"} \
            "https://huggingface.co/${CHOSEN_REPO}/resolve/main/${CHOSEN_FILE}" \
            -o "$MODEL_PATH"
        echo "==> Model ready: $MODEL_PATH"
    fi

    # Write systemd units
    cat > "${SYSTEMD_DIR}/llama-server.socket" <<SOCKET_EOF
[Unit]
Description=llama-server on-demand socket

[Socket]
ListenStream=127.0.0.1:${PORT}
Accept=no

[Install]
WantedBy=sockets.target
SOCKET_EOF

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

    systemctl --user daemon-reload
    systemctl --user enable --now llama-server.socket
    echo "==> systemd socket enabled — llama-server starts on first connection"

    # Configure backend
    "${BIN_DIR}/ai-backend" llama "$MODEL_PATH"

    echo ""
    echo "========================================"
    echo "llama.cpp setup complete!"
    echo "  Model:   $MODEL_PATH"
    echo "  Server:  http://localhost:${PORT}/v1/ (auto-starts on first 'ai' call)"
    echo "  Logs:    journalctl --user -u llama-server -f"
    echo ""
    echo "Apply:  source ~/.bashrc"
    echo "Test:   ai \"hello\""
    echo "========================================"
    exit 0
fi

# ── Default: CLI only ─────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "ai CLI installed to ${BIN_DIR}"
echo ""
echo "Set your LLM backend:"
echo "  ai-backend snap     # use qwen3-6 or gemma4 snap (auto-detected)"
echo "  ai-backend auto     # same, picks whatever is running"
echo "  ai-backend status   # show what's available"
echo ""
echo "Or run './install.sh llama' to set up a local llama.cpp server."
echo ""
echo "Apply:  source ~/.bashrc && source ~/.config/ai/env"
echo "Test:   ai \"hello\""
echo "========================================"
