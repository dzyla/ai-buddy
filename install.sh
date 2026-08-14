#!/bin/bash
# install.sh — build and install the ai CLI
#
# Usage:
#   ./install.sh              Build and install ai CLI to ~/.local/bin
#   ./install.sh --update-llama Pull latest llama.cpp and rebuild llama-server + llama-cli
#   ./install.sh llama         Also set up a local llama.cpp inference server
#   ./install.sh snap          Also detect and configure an installed AI snap
#   ./install.sh uninstall     Uninstall the CLI, systemd services, and wrapper scripts
#
# The llama build compiles llama-server (serving) and llama-cli (interactive) by
# default and installs both. To also build other llama.cpp tools, export
# LLAMA_EXTRA_TARGETS (space-separated cmake targets), e.g.:
#   LLAMA_EXTRA_TARGETS="llama-perplexity llama-bench llama-quantize llama-gguf-split" ./install.sh llama
#
# Everything installs to ~/.local/bin — no sudo required.
# To uninstall: ./install.sh uninstall

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

# ── Subcommand: uninstall ─────────────────────────────────────────────────────
if [ "${1:-}" = "uninstall" ]; then
    echo "==> Uninstalling ai CLI and llama-server..."

    # 1. Stop and disable systemd service and socket
    if systemctl --user is-active llama-server.socket &>/dev/null || systemctl --user is-failed llama-server.socket &>/dev/null; then
        echo "--> Stopping and disabling llama-server.socket..."
        systemctl --user disable --now llama-server.socket || true
    fi
    if systemctl --user is-active llama-server.service &>/dev/null || systemctl --user is-failed llama-server.service &>/dev/null; then
        echo "--> Stopping llama-server.service..."
        systemctl --user stop llama-server.service || true
    fi

    # 2. Remove systemd unit files
    if [ -f "${SYSTEMD_DIR}/llama-server.service" ] || [ -f "${SYSTEMD_DIR}/llama-server.socket" ]; then
        echo "--> Removing systemd unit files..."
        rm -f "${SYSTEMD_DIR}/llama-server.service" "${SYSTEMD_DIR}/llama-server.socket"
        systemctl --user daemon-reload
    fi

    # 3. Remove binaries and scripts
    echo "--> Removing binaries and wrapper scripts from ${BIN_DIR}..."
    for f in ai ai_mcp.py gcal.py zulip_mcp_server.py ai-backend ai-model ai-use ai-use.sh pubmed_mcp_server.py deep_research.py llama-server-wrapper.sh llama-server; do
        rm -f "${BIN_DIR}/$f"
    done

    # 4. Remove custom skills
    if [ -d "${SKILLS_DST}" ]; then
        echo "--> Removing custom skills from ${SKILLS_DST}..."
        rm -rf "${SKILLS_DST}"
    fi

    echo ""
    echo "Uninstallation complete!"
    echo "Note: Downloaded models at ${MODEL_DIR} and configuration in ~/.config/ai/ were preserved."
    echo "To remove them manually, run:"
    echo "  rm -rf ${DATA_DIR} ~/.config/ai"
    echo "Also, remember to remove any 'config/ai/env' sourcing lines from your ~/.bashrc or ~/.zshrc."
    exit 0
fi

# ── Flag: --update-llama — pull latest llama.cpp and rebuild ──────────────────
if [ "${1:-}" = "--update-llama" ]; then
    echo "==> Updating llama.cpp to the latest version..."

    # Ensure build deps
    missing_ullama=()
    for cmd in cmake git; do
        command -v "$cmd" &>/dev/null || missing_ullama+=("$cmd")
    done
    if [ "${#missing_ullama[@]}" -gt 0 ]; then
        echo "==> Installing: ${missing_ullama[*]}"
        sudo apt-get install -y "${missing_ullama[@]}"
    fi

    if [ -d "${LLAMA_SRC}/.git" ]; then
        echo "--> Fetching latest from upstream..."
        cd "${LLAMA_SRC}"
        FLAVOR="og"
        [ -f "${DATA_DIR}/llama_flavor" ] && FLAVOR=$(cat "${DATA_DIR}/llama_flavor")
        CURR_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")
        git fetch origin "$CURR_BRANCH" --depth=1 || git fetch --all --tags --prune
        git checkout -B "$CURR_BRANCH" FETCH_HEAD 2>/dev/null || git reset --hard FETCH_HEAD
        echo "--> Pull complete (${FLAVOR} flavor, branch: ${CURR_BRANCH})."
    else
        echo "--> llama.cpp not found at ${LLAMA_SRC} — nothing to update."
        echo "   Run './install.sh llama' (or './install.sh llama unsloth') first to clone and build it."
        exit 0
    fi

    # Re-detect GPU backend (same logic as the llama subcommand)
    NVCC_BIN=""
    CUDA_BIN_DIR=""
    CUDA_ROOT=""
    CUDA_LIB_DIR=""
    GPU_FLAGS="-DGGML_CUDA=OFF -DGGML_HIP=OFF -DGGML_VULKAN=OFF"

    if command -v nvcc &>/dev/null; then
        NVCC_BIN=$(command -v nvcc)
    else
        for path in /usr/local/cuda/bin/nvcc /usr/local/cuda-13.3/bin/nvcc /usr/local/cuda-12.8/bin/nvcc /usr/local/cuda-12.5/bin/nvcc /usr/local/cuda-12.4/bin/nvcc /usr/local/cuda-12.2/bin/nvcc /usr/local/cuda-12.1/bin/nvcc /usr/local/cuda-12.0/bin/nvcc /usr/local/cuda-11.*/bin/nvcc; do
            if [ -x "$path" ]; then
                NVCC_BIN="$path"
                break
            fi
        done
    fi

    if [ -n "$NVCC_BIN" ]; then
        echo "==> CUDA detected at ${NVCC_BIN} — configuring CUDA paths."
        CUDA_BIN_DIR="$(dirname "$NVCC_BIN")"
        CUDA_ROOT="$(dirname "$CUDA_BIN_DIR")"
        if [ -d "${CUDA_ROOT}/lib64" ]; then
            CUDA_LIB_DIR="${CUDA_ROOT}/lib64"
        fi
        if [ -d "${CUDA_ROOT}/targets/x86_64-linux/lib" ]; then
            if [ -n "$CUDA_LIB_DIR" ]; then
                CUDA_LIB_DIR="${CUDA_LIB_DIR}:${CUDA_ROOT}/targets/x86_64-linux/lib"
            else
                CUDA_LIB_DIR="${CUDA_ROOT}/targets/x86_64-linux/lib"
            fi
        fi
        export PATH="${CUDA_BIN_DIR}:$PATH"
        export CUDACXX="$NVCC_BIN"
        export CUDA_PATH="$CUDA_ROOT"
        if [ -n "$CUDA_LIB_DIR" ]; then
            export LD_LIBRARY_PATH="${CUDA_LIB_DIR}:${LD_LIBRARY_PATH:-}"
        fi
        GPU_FLAGS="-DGGML_CUDA=ON -DCUDAToolkit_ROOT=${CUDA_ROOT}"
    elif command -v hipcc &>/dev/null; then
        echo "==> ROCm detected — building with HIP support."
        GPU_FLAGS="-DGGML_HIP=ON"
    elif pkg-config --exists vulkan 2>/dev/null || [ -f /usr/include/vulkan/vulkan.h ]; then
        if find /usr/share/cmake /usr/lib/cmake /usr/local/share/cmake /usr/local/lib/cmake -name "*SPIRV-HeadersConfig.cmake*" -o -name "*spirv-headers-config.cmake*" 2>/dev/null | grep -q .; then
            echo "==> Vulkan and SPIRV-Headers detected — building with Vulkan support."
            GPU_FLAGS="-DGGML_VULKAN=ON"
        else
            echo "==> Vulkan detected, but SPIRV-Headers CMake package is missing. Falling back to CPU."
        fi
    else
        echo "==> No GPU backend found — building CPU-only."
    fi

    echo "==> Rebuilding llama.cpp tools..."
    LLAMA_TOOLS="llama-server llama-cli llama-mtmd-cli llama-gguf-split"
    [ -n "${LLAMA_EXTRA_TARGETS:-}" ] && LLAMA_TOOLS="${LLAMA_TOOLS} ${LLAMA_EXTRA_TARGETS}"
    rm -rf "${LLAMA_SRC}/build"
    cmake -B "${LLAMA_SRC}/build" -S "${LLAMA_SRC}" \
        -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF $GPU_FLAGS
    cmake --build "${LLAMA_SRC}/build" --config Release \
        --target ${LLAMA_TOOLS} -j"$(nproc)"

    # Stop existing llama-server if running to avoid "Text file busy" on copy
    if pgrep -x llama-server &>/dev/null; then
        echo "--> Stopping existing llama-server process..."
        pkill -x llama-server || true
        sleep 1
    fi

    for tool in ${LLAMA_TOOLS}; do
        if [ -f "${LLAMA_SRC}/build/bin/${tool}" ]; then
            cp "${LLAMA_SRC}/build/bin/${tool}" "${BIN_DIR}/"
            chmod +x "${BIN_DIR}/${tool}"
            echo "==> ${tool} updated and installed to ${BIN_DIR}/${tool}"
        else
            echo "==> WARNING: ${tool} not built (missing ${LLAMA_SRC}/build/bin/${tool})"
        fi
    done
    exit 0
fi

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
for cmd in gcc python3 make; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
done
if ! pkg-config --exists libcurl 2>/dev/null && ! dpkg -l libcurl4-openssl-dev &>/dev/null 2>&1; then
    missing+=(libcurl4-openssl-dev)
fi
# libssl-dev provides -lssl and -lcrypto (required by the Makefile); without it
# the build fails with "cannot find -lssl / -lcrypto".
if ! pkg-config --exists openssl 2>/dev/null && ! dpkg -l libssl-dev &>/dev/null 2>&1; then
    missing+=(libssl-dev)
fi
if [ "${#missing[@]}" -gt 0 ]; then
    echo "==> Installing missing packages: ${missing[*]}"
    sudo apt-get install -y "${missing[@]}"
fi

(cd "${SCRIPT_DIR}" && make clean && make)
echo "==> Built: ${SCRIPT_DIR}/ai and libremote_harness.so"

# ── 2. Install to ~/.local/bin and ~/.local/lib ─────────────────────────────
echo "==> Installing to ${BIN_DIR}..."
mkdir -p "${BIN_DIR}"
mkdir -p "${HOME}/.local/lib"
rm -f "${BIN_DIR}/ai" "${BIN_DIR}/ai_mcp.py" "${BIN_DIR}/gcal.py" "${BIN_DIR}/zulip_mcp_server.py" "${BIN_DIR}/ai-backend" "${BIN_DIR}/ai-model" "${BIN_DIR}/ai-use" "${BIN_DIR}/ai-use.sh" "${BIN_DIR}/pubmed_mcp_server.py" "${BIN_DIR}/deep_research.py" "${BIN_DIR}/llama-server-wrapper.sh"
cp "${SCRIPT_DIR}/ai"             "${BIN_DIR}/ai"
cp "${SCRIPT_DIR}/libremote_harness.so" "${HOME}/.local/lib/libremote_harness.so"
cp "${SCRIPT_DIR}/libremote_harness.so" "${BIN_DIR}/libremote_harness.so" 2>/dev/null || true
cp "${SCRIPT_DIR}/ai_mcp.py"      "${BIN_DIR}/ai_mcp.py"
cp "${SCRIPT_DIR}/gcal.py"        "${BIN_DIR}/gcal.py"
cp "${SCRIPT_DIR}/zulip_mcp_server.py" "${BIN_DIR}/zulip_mcp_server.py"
cp "${SCRIPT_DIR}/ai-backend"     "${BIN_DIR}/ai-backend"
cp "${SCRIPT_DIR}/ai-backend"     "${BIN_DIR}/ai-model"
ln -sf "${BIN_DIR}/ai-backend"    "${BIN_DIR}/ai-use"
ln -sf "${BIN_DIR}/ai-backend"    "${BIN_DIR}/ai-use.sh"
ln -sf "${BIN_DIR}/ai-backend"    "${BIN_DIR}/llama-server-wrapper.sh"
cp "${SCRIPT_DIR}/pubmed_mcp_server.py" "${BIN_DIR}/pubmed_mcp_server.py"
cp "${SCRIPT_DIR}/deep_research.py"   "${BIN_DIR}/deep_research.py"
# The MTP probe tool: ai-backend probe looks here when dev/ isn't alongside the binary.
mkdir -p "${HOME}/.local/share/ai"
cp "${SCRIPT_DIR}/dev/probe_mtp.py" "${HOME}/.local/share/ai/probe_mtp.py" 2>/dev/null || true
chmod +x "${BIN_DIR}/ai" "${BIN_DIR}/ai_mcp.py" "${BIN_DIR}/gcal.py" "${BIN_DIR}/zulip_mcp_server.py" "${BIN_DIR}/ai-backend" "${BIN_DIR}/ai-model" "${BIN_DIR}/pubmed_mcp_server.py" "${BIN_DIR}/deep_research.py"
echo "==> Installed: ai  libremote_harness.so  ai_mcp.py  gcal.py  zulip_mcp_server.py  ai-backend (single model tool)  pubmed_mcp_server.py  deep_research.py  probe_mtp.py"

# ── 3. Python optional deps ───────────────────────────────────────────────────
echo "==> Installing optional Python deps (curl-cffi, playwright-stealth)..."
pip install --quiet "curl-cffi>=0.7" playwright-stealth 2>/dev/null || true

# ── 4. Sync skills ────────────────────────────────────────────────────────────
if [ -d "$SKILLS_SRC" ]; then
    cp -r "${SKILLS_SRC}/." "${SKILLS_DST}/"
    count=$(ls "$SKILLS_SRC" | wc -l)
    echo "==> Synced ${count} skill(s) to ${SKILLS_DST}"
fi

# Configuration is loaded directly by the ai binary from ~/.local/share/ai/env

# ── Subcommand: snap ──────────────────────────────────────────────────────────
if [ "${1:-}" = "snap" ]; then
    echo ""
    echo "==> Detecting active AI snap..."
    "${BIN_DIR}/ai-backend" auto
    echo ""
    exit 0
fi


# ── Subcommand: llama / unsloth ──────────────────────────────────────────────
if [ "${1:-}" = "llama" ] || [ "${1:-}" = "unsloth" ] || [ "${1:-}" = "llama-unsloth" ]; then
    echo ""
    echo "==> Setting up local llama.cpp inference server..."

    # Determine flavor: unsloth vs og
    CHOSEN_FLAVOR="${LLAMA_FLAVOR:-}"
    if [ "${1:-}" = "unsloth" ] || [ "${1:-}" = "llama-unsloth" ] || [ "${2:-}" = "unsloth" ] || [ "${2:-}" = "--unsloth" ]; then
        CHOSEN_FLAVOR="unsloth"
    elif [ "${2:-}" = "og" ] || [ "${2:-}" = "--og" ] || [ "${2:-}" = "standard" ]; then
        CHOSEN_FLAVOR="og"
    fi

    if [ -z "$CHOSEN_FLAVOR" ]; then
        if [ -f "${DATA_DIR}/llama_flavor" ]; then
            CHOSEN_FLAVOR=$(cat "${DATA_DIR}/llama_flavor")
        fi
    fi

    if [ -z "$CHOSEN_FLAVOR" ]; then
        echo "Choose llama.cpp backend flavor:"
        echo "  1) Unsloth llama.cpp (branch: iq1-narrow) — SOTA dynamic quants & fast inference for Qwen3.8/DeepSeek"
        echo "  2) Original ggml-org/llama.cpp (branch: master) — standard upstream release"
        read -rp "Flavor [1/2, default: 1 (unsloth)]: " FLAVOR_CHOICE
        if [ "$FLAVOR_CHOICE" = "2" ] || [ "$FLAVOR_CHOICE" = "og" ]; then
            CHOSEN_FLAVOR="og"
        else
            CHOSEN_FLAVOR="unsloth"
        fi
    fi

    if [ "$CHOSEN_FLAVOR" = "unsloth" ]; then
        LLAMA_REPO="https://github.com/unslothai/llama.cpp"
        LLAMA_BRANCH="${LLAMA_BRANCH:-iq1-narrow}"
        echo "==> Selected Unsloth llama.cpp (${LLAMA_BRANCH})"
    else
        LLAMA_REPO="https://github.com/ggml-org/llama.cpp"
        LLAMA_BRANCH="${LLAMA_BRANCH:-master}"
        echo "==> Selected upstream ggml-org/llama.cpp (${LLAMA_BRANCH})"
    fi
    echo "$CHOSEN_FLAVOR" > "${DATA_DIR}/llama_flavor"

    # Build dependencies
    missing_llama=()
    for cmd in cmake git curl python3; do
        command -v "$cmd" &>/dev/null || missing_llama+=("$cmd")
    done
    if [ "${#missing_llama[@]}" -gt 0 ]; then
        echo "==> Installing: ${missing_llama[*]}"
        sudo apt-get install -y cmake git curl python3 build-essential
    fi

    # Clone or switch llama.cpp
    if [ ! -d "${LLAMA_SRC}/.git" ]; then
        echo "==> Cloning llama.cpp from ${LLAMA_REPO} (${LLAMA_BRANCH})..."
        git clone --branch "$LLAMA_BRANCH" --depth=1 "$LLAMA_REPO" "$LLAMA_SRC"
    else
        cd "$LLAMA_SRC"
        REMOTE_URL=$(git config --get remote.origin.url || echo "")
        CURR_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
        if [ "$REMOTE_URL" != "$LLAMA_REPO" ] || [ "$CURR_BRANCH" != "$LLAMA_BRANCH" ]; then
            echo "==> Switching llama.cpp remote/branch to ${LLAMA_REPO} (${LLAMA_BRANCH})..."
            git remote set-url origin "$LLAMA_REPO" 2>/dev/null || git remote add origin "$LLAMA_REPO"
            git fetch origin "$LLAMA_BRANCH" --depth=1
            git checkout -B "$LLAMA_BRANCH" FETCH_HEAD
            git reset --hard FETCH_HEAD
        else
            echo "==> llama.cpp already cloned from ${LLAMA_REPO} (${LLAMA_BRANCH}) — skipping clone."
        fi
    fi

    # Robust CUDA / GPU detection
    NVCC_BIN=""
    CUDA_BIN_DIR=""
    CUDA_ROOT=""
    CUDA_LIB_DIR=""
    GPU_FLAGS="-DGGML_CUDA=OFF -DGGML_HIP=OFF -DGGML_VULKAN=OFF"
    
    if command -v nvcc &>/dev/null; then
        NVCC_BIN=$(command -v nvcc)
    else
        for path in /usr/local/cuda/bin/nvcc /usr/local/cuda-13.3/bin/nvcc /usr/local/cuda-12.8/bin/nvcc /usr/local/cuda-12.5/bin/nvcc /usr/local/cuda-12.4/bin/nvcc /usr/local/cuda-12.2/bin/nvcc /usr/local/cuda-12.1/bin/nvcc /usr/local/cuda-12.0/bin/nvcc /usr/local/cuda-11.*/bin/nvcc; do
            if [ -x "$path" ]; then
                NVCC_BIN="$path"
                break
            fi
        done
    fi
    
    if [ -n "$NVCC_BIN" ]; then
        echo "==> CUDA detected at ${NVCC_BIN} — configuring CUDA paths."
        CUDA_BIN_DIR="$(dirname "$NVCC_BIN")"
        CUDA_ROOT="$(dirname "$CUDA_BIN_DIR")"
        
        # Build library path list
        if [ -d "${CUDA_ROOT}/lib64" ]; then
            CUDA_LIB_DIR="${CUDA_ROOT}/lib64"
        fi
        if [ -d "${CUDA_ROOT}/targets/x86_64-linux/lib" ]; then
            if [ -n "$CUDA_LIB_DIR" ]; then
                CUDA_LIB_DIR="${CUDA_LIB_DIR}:${CUDA_ROOT}/targets/x86_64-linux/lib"
            else
                CUDA_LIB_DIR="${CUDA_ROOT}/targets/x86_64-linux/lib"
            fi
        fi
        
        # Set environment variables for the compile process
        export PATH="${CUDA_BIN_DIR}:$PATH"
        export CUDACXX="$NVCC_BIN"
        export CUDA_PATH="$CUDA_ROOT"
        if [ -n "$CUDA_LIB_DIR" ]; then
            export LD_LIBRARY_PATH="${CUDA_LIB_DIR}:${LD_LIBRARY_PATH:-}"
        fi
        
        GPU_FLAGS="-DGGML_CUDA=ON -DCUDAToolkit_ROOT=${CUDA_ROOT}"
    elif command -v hipcc &>/dev/null; then
        echo "==> ROCm detected — building with HIP support."
        GPU_FLAGS="-DGGML_HIP=ON"
    elif pkg-config --exists vulkan 2>/dev/null || [ -f /usr/include/vulkan/vulkan.h ]; then
        if find /usr/share/cmake /usr/lib/cmake /usr/local/share/cmake /usr/local/lib/cmake -name "*SPIRV-HeadersConfig.cmake*" -o -name "*spirv-headers-config.cmake*" 2>/dev/null | grep -q .; then
            echo "==> Vulkan and SPIRV-Headers detected — building with Vulkan support."
            GPU_FLAGS="-DGGML_VULKAN=ON"
        else
            echo "==> Vulkan detected, but SPIRV-Headers CMake package is missing."
            echo "    To build with Vulkan support, please install 'spirv-headers'."
            echo "    Falling back to CPU-only build."
        fi
    else
        echo "==> No GPU backend found — building CPU-only."
    fi

    # Build with best available GPU backend
    if [ ! -f "${BIN_DIR}/llama-server" ] || [ "${FORCE_REBUILD:-0}" = "1" ]; then
        echo "==> Building llama.cpp tools (this takes a few minutes)..."
        LLAMA_TOOLS="llama-server llama-cli llama-mtmd-cli llama-gguf-split"
        [ -n "${LLAMA_EXTRA_TARGETS:-}" ] && LLAMA_TOOLS="${LLAMA_TOOLS} ${LLAMA_EXTRA_TARGETS}"
        rm -rf "${LLAMA_SRC}/build"
        cmake -B "${LLAMA_SRC}/build" -S "$LLAMA_SRC" \
            -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF $GPU_FLAGS
        cmake --build "${LLAMA_SRC}/build" --config Release \
            --target ${LLAMA_TOOLS} -j"$(nproc)"
        for tool in ${LLAMA_TOOLS}; do
            if [ -f "${LLAMA_SRC}/build/bin/${tool}" ]; then
                cp "${LLAMA_SRC}/build/bin/${tool}" "${BIN_DIR}/"
                chmod +x "${BIN_DIR}/${tool}"
                echo "==> ${tool} installed to ${BIN_DIR}/${tool}"
            fi
        done
    else
        echo "==> llama-server already built — skipping. Remove ${BIN_DIR}/llama-server to force rebuild."
    fi

    # Sync ai-backend as wrapper
    ln -sf "${BIN_DIR}/ai-backend" "${BIN_DIR}/llama-server-wrapper.sh"

    # ── Auto-fix: ensure models dir points to /mnt/scratch where weights live ─
    SCRATCH_MODEL_DIR="/mnt/scratch/dzyla/.local/share/ai/models"
    if [ ! -e "$MODEL_DIR" ]; then
        # Models dir doesn't exist at all — create a symlink to scratch
        if [ -d "$SCRATCH_MODEL_DIR" ]; then
            mkdir -p "$(dirname "$MODEL_DIR")"
            ln -s "$SCRATCH_MODEL_DIR" "$MODEL_DIR"
            echo "==>.local/share/ai/models symlinked to $SCRATCH_MODEL_DIR"
        else
            mkdir -p "$MODEL_DIR"
        fi
    elif [ ! -L "$MODEL_DIR" ] && [ -d "$MODEL_DIR" ]; then
        # It's a real directory — check if scratch has weights we should use
        if [ -d "$SCRATCH_MODEL_DIR" ] && ls "$SCRATCH_MODEL_DIR"/*.gguf &>/dev/null; then
            echo "==>.local/share/ai/models is a real directory, but weights found on scratch."
            echo "===> Recreating as symlink to $SCRATCH_MODEL_DIR"
            rm -rf "$MODEL_DIR"
            ln -s "$SCRATCH_MODEL_DIR" "$MODEL_DIR"
        fi
    elif [ -L "$MODEL_DIR" ]; then
        # It's a symlink — make sure it points to the right place
        REAL_TARGET=$(readlink "$MODEL_DIR")
        if [ "$REAL_TARGET" != "$SCRATCH_MODEL_DIR" ]; then
            echo "==>.local/share/ai/models points to $REAL_TARGET (expected $SCRATCH_MODEL_DIR)"
            if [ -d "$SCRATCH_MODEL_DIR" ] && ls "$SCRATCH_MODEL_DIR"/*.gguf &>/dev/null; then
                ln -sfn "$SCRATCH_MODEL_DIR" "$MODEL_DIR"
                echo "===> Fixed: symlink now points to $SCRATCH_MODEL_DIR"
            else
                echo "===> Kept existing symlink (no weights found on scratch to use)"
            fi
        fi
    fi

    # Try to ensure /mnt/scratch is accessible (autofs trigger)
    if [ ! -d "$SCRATCH_MODEL_DIR" ] && [ -d "$(dirname "$SCRATCH_MODEL_DIR")" ]; then
        ls "$SCRATCH_MODEL_DIR" &>/dev/null || echo "==>.local/share/ai/models (on /mnt/scratch) not yet accessible — continuing anyway"
    fi

    # Check if a model is already configured in env
    CONFIGURED_MODEL=""
    if [ -f "${DATA_DIR}/env" ]; then
        CONFIGURED_MODEL=$(grep -E '^export LLAMA_MODEL_PATH=' "${DATA_DIR}/env" | cut -d'"' -f2 | cut -d"'" -f2 | tail -n1)
    fi

    if [ -n "$CONFIGURED_MODEL" ] && [ -f "$CONFIGURED_MODEL" ]; then
        EXISTING_MODEL="$CONFIGURED_MODEL"
        echo "==> Preserving currently configured model from env: $EXISTING_MODEL"
    else
        # Find first available model
        EXISTING_MODEL=$(find -L "$MODEL_DIR" -name "*.gguf" \
            ! -name "mmproj-*.gguf" ! -path "*/MTP/*" 2>/dev/null | sort | head -1)
    fi

    if [ -n "$EXISTING_MODEL" ]; then
        MODEL_PATH="$EXISTING_MODEL"
        echo "==> Using existing model: $MODEL_PATH"
    else
        echo ""
        echo "Select a model to download:"
        PRESET_REPOS=(
            "unsloth/Qwen3.8-27B-GGUF"
            "unsloth/Qwen3.8-2.4T-A95B-GGUF"
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

    # Stop existing socket/service before writing new configs to avoid "Socket unit configuration has changed" issues
    systemctl --user stop llama-server.service llama-server.socket 2>/dev/null || true

    # Prepare environment lines for systemd
    # Do NOT pin a specific GPU index into the unit: serve resolves the biggest
    # card that fits the active model at startup (and re-resolves it), so a baked
    # index would go stale if the topology or the model changes.

    # Prepare environment lines for systemd
    SYSTEMD_ENV=""
    if [ -n "${CUDA_BIN_DIR:-}" ]; then
        SYSTEMD_ENV="Environment=PATH=${BIN_DIR}:${CUDA_BIN_DIR}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
Environment=CUDA_PATH=${CUDA_ROOT}
Environment=LLAMA_CTX_SIZE=131072"
        if [ -n "${CUDA_LIB_DIR:-}" ]; then
            SYSTEMD_ENV="${SYSTEMD_ENV}
Environment=LD_LIBRARY_PATH=${CUDA_LIB_DIR}"
        fi
    else
        SYSTEMD_ENV="Environment=PATH=${BIN_DIR}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
Environment=LLAMA_CTX_SIZE=131072"
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
After=llama-server.socket

[Service]
Type=simple
${SYSTEMD_ENV}
Environment=LLAMA_MODEL_PATH=${MODEL_PATH}
Environment=LLAMA_IDLE_TIMEOUT=120
ExecStartPre=/bin/bash -c 'systemctl --user stop llama-server.socket || true'
ExecStart=${BIN_DIR}/ai-backend serve
ExecStopPost=/bin/bash -c '/usr/bin/systemd-run --user /bin/bash -c "for i in {1..10}; do systemctl --user is-active -q llama-server.service || { systemctl --user start llama-server.socket; exit 0; }; sleep 0.5; done" || true'
Restart=no
StandardOutput=journal
StandardError=journal
SERVICE_EOF

    systemctl --user daemon-reload
    systemctl --user enable --now llama-server.socket
    echo "==> systemd socket enabled — llama-server starts on first connection"

    # Configure backend
    "${BIN_DIR}/ai-backend" llama "$MODEL_PATH"

    # Append CUDA environment variables to env file if CUDA was detected
    if [ -n "${CUDA_BIN_DIR:-}" ]; then
        if ! grep -q "CUDA_VISIBLE_DEVICES" "${DATA_DIR}/env" 2>/dev/null; then
            cat >> "${DATA_DIR}/env" <<ENV_EOF

# GPU selection: "auto" = serve picks the biggest card that fits the active model
# at startup (override with a specific index via `ai-backend gpus <sel>`).
export CUDA_VISIBLE_DEVICES="auto"
export LLAMA_CTX_SIZE="131072"
# CUDA Environment Paths
export PATH="${CUDA_BIN_DIR}:${PATH}"
export CUDA_PATH="${CUDA_ROOT}"
ENV_EOF
            if [ -n "${CUDA_LIB_DIR:-}" ]; then
                if [ -n "${LD_LIBRARY_PATH:-}" ]; then
                    echo "export LD_LIBRARY_PATH=\"${CUDA_LIB_DIR}:${LD_LIBRARY_PATH}\"" >> "${DATA_DIR}/env"
                else
                    echo "export LD_LIBRARY_PATH=\"${CUDA_LIB_DIR}\"" >> "${DATA_DIR}/env"
                fi
            fi
        fi
    fi

    echo ""
    echo "========================================"
    echo "llama.cpp setup complete!"
    echo "  Model:   $MODEL_PATH"
    echo "  Server:  http://localhost:${PORT}/v1/ (auto-starts on first 'ai' call)"
    echo "  Logs:    journalctl --user -u llama-server -f"
    echo ""
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
echo "Test:   ai \"hello\""
echo "========================================"
