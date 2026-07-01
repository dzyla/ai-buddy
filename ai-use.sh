#!/bin/bash
# ai-use — switch the active AI backend
# Usage:
#   ai-use                          show current backend + local models
#   ai-use llama                    local llama-server (auto-picks largest GGUF)
#   ai-use gemma4                   gemma4 snap
#   ai-use qwen3                    Qwen3-6B via llama-server (downloads if missing)
#   ai-use qwen3-30                 Qwen3-30B-A3B via llama-server (downloads if missing)
#   ai-use gguf <hf_repo> [file]    download any GGUF from HuggingFace and switch
#                                   (omit file to list available GGUFs in the repo)
#   ai-use local <path>             switch to an already-downloaded GGUF
#   ai-use models                   list downloaded models
#   ai-use ctx <size|auto>          set explicit context size or reset to auto

CONFIG="${HOME}/.local/share/ai/env"
MODEL_DIR="${HOME}/.local/share/ai/models"
PORT=8080
PUBMED_KEY="myapp_kZnDpemyN9z43CqNrOYEE-LhAH9_UsxhWTavLkWv22Y"

list_local_models() {
    find "$MODEL_DIR" -name "*.gguf" \
        ! -name "mmproj-*.gguf" \
        ! -path "*/MTP/*" \
        ! -name "*-MTP.gguf" \
        2>/dev/null | sort
}

show_status() {
    [ -f "$CONFIG" ] && source "$CONFIG"
    echo "Current backend:"
    echo "  INFER_BASE_URL    = ${INFER_BASE_URL:-<not set>}"
    echo "  INFER_MODEL       = ${INFER_MODEL:-<not set>}"
    echo "  LLAMA_MODEL_PATH  = ${LLAMA_MODEL_PATH:-<not set>}"
    [ -n "${LLAMA_CTX_SIZE:-}" ] && echo "  LLAMA_CTX_SIZE    = ${LLAMA_CTX_SIZE} (explicit override)"
    [ -n "${LLAMA_N_GPU_LAYERS:-}" ] && echo "  LLAMA_N_GPU_LAYERS= ${LLAMA_N_GPU_LAYERS}"
    echo ""
    echo "Available commands:"
    echo "  ai-use llama                    local llama-server on localhost:${PORT}"
    echo "  ai-use gemma4                   gemma4 snap"
    echo "  ai-use qwen3                    Qwen3-6B via llama-server"
    echo "  ai-use qwen3-30                 Qwen3-30B-A3B via llama-server"
    echo "  ai-use gguf <hf_repo> [file]    any HuggingFace GGUF (list files if omitted)"
    echo "  ai-use local <path>             use a local GGUF file"
    echo "  ai-use models                   list downloaded models"
    echo "  ai-use ctx <size|auto>          set/clear explicit context size"
    echo ""
    echo "Downloaded models:"
    local models
    models=$(list_local_models)
    if [ -z "$models" ]; then
        echo "  (none found in ${MODEL_DIR})"
    else
        while IFS= read -r m; do
            local size
            size=$(du -h "$m" 2>/dev/null | cut -f1)
            if [ "$m" = "${LLAMA_MODEL_PATH:-}" ]; then
                echo "  * $m  [${size}]  ← active"
            else
                echo "    $m  [${size}]"
            fi
        done <<< "$models"
    fi
}

write_config() {
    local url="$1" model="$2" llama_model_path="${3:-}"
    # Read current ctx/gpu overrides so we preserve them across backend switches
    local current_ctx="" current_gpu_layers=""
    [ -f "$CONFIG" ] && {
        current_ctx=$(grep '^export LLAMA_CTX_SIZE=' "$CONFIG" 2>/dev/null | cut -d'"' -f2 || true)
        current_gpu_layers=$(grep '^export LLAMA_N_GPU_LAYERS=' "$CONFIG" 2>/dev/null | cut -d'"' -f2 || true)
    }

    mkdir -p "$(dirname "$CONFIG")"
    {
        echo "# ai backend config — managed by ai-use, do not edit manually"
        echo "export INFER_BASE_URL=\"${url}\""
        echo "export INFER_API_KEY=\"not-needed\""
        echo "export INFER_MODEL=\"${model}\""
        echo "export INFER_TOOL_CHOICE=auto"
        [ -n "$llama_model_path" ] && echo "export LLAMA_MODEL_PATH=\"${llama_model_path}\""
        [ -n "$current_ctx" ] && echo "export LLAMA_CTX_SIZE=\"${current_ctx}\""
        [ -n "$current_gpu_layers" ] && echo "export LLAMA_N_GPU_LAYERS=\"${current_gpu_layers}\""
        echo "export PUBMED_API_KEY=\"${PUBMED_KEY}\""
    } > "$CONFIG"

    echo "Switched to ${model} (${url})"
}

# Patch a single key in the config file (add/update/remove)
patch_config_key() {
    local key="$1" value="${2:-}"
    [ ! -f "$CONFIG" ] && { echo "No config found. Run: ai-use llama"; exit 1; }
    # Remove existing line for this key
    sed -i "/^export ${key}=/d" "$CONFIG"
    if [ -n "$value" ]; then
        echo "export ${key}=\"${value}\"" >> "$CONFIG"
    fi
}

kill_llama_server() {
    local pid
    pid=$(pgrep -f "llama-server.*--model" 2>/dev/null | head -1)
    if [ -n "$pid" ]; then
        echo "Stopping running llama-server (PID $pid)..."
        kill "$pid" 2>/dev/null || true
        sleep 1
    fi
}

find_local_gguf() {
    list_local_models | tail -1
}

download_model() {
    local repo="$1" file="$2"
    local dest="${MODEL_DIR}/${file}"
    mkdir -p "$(dirname "$dest")"
    # Remove broken symlinks (e.g. from huggingface-cli blob cache) before downloading
    if [ -L "$dest" ] && [ ! -f "$dest" ]; then
        echo "Removing broken symlink: $dest" >&2
        rm "$dest"
    fi
    if [ -f "$dest" ]; then
        echo "Model already present: $dest" >&2
    else
        echo "Downloading ${file} from ${repo}..." >&2
        local tmp="${dest}.part"
        curl -L --progress-bar --fail \
            ${HF_TOKEN:+-H "Authorization: Bearer ${HF_TOKEN}"} \
            "https://huggingface.co/${repo}/resolve/main/${file}" \
            -o "$tmp" || { echo "Download failed." >&2; rm -f "$tmp"; return 1; }
        mv "$tmp" "$dest"
        echo "Downloaded: $dest" >&2
    fi
    echo "$dest"
}

list_hf_gguf_files() {
    local repo="$1"
    echo "Fetching file list for ${repo}..." >&2
    local api_url="https://huggingface.co/api/models/${repo}/tree/main"
    curl -sf \
        ${HF_TOKEN:+-H "Authorization: Bearer ${HF_TOKEN}"} \
        "$api_url" 2>/dev/null \
    | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception as e:
    print(f'Error parsing response: {e}', file=sys.stderr)
    sys.exit(1)
files = [f['path'] for f in data
         if isinstance(f, dict)
         and f.get('path','').endswith('.gguf')
         and not f.get('path','').startswith('mmproj')]
if not files:
    print('No GGUF files found in this repo.', file=sys.stderr)
    sys.exit(1)
for f in files:
    size_mb = f.get('size', 0) / 1024 / 1024 if isinstance(f, dict) else 0
    print(f)
"
}

case "${1:-}" in
    # ── Named presets ────────────────────────────────────────────────────────
    llama)
        MODEL_PATH=$(find_local_gguf)
        if [ -z "$MODEL_PATH" ]; then
            echo "No GGUF model found in ${MODEL_DIR}."
            echo "Run setup_llama.sh to download one, or: ai-use gguf <hf_repo> <file>"
            exit 1
        fi
        echo "Model: $MODEL_PATH"
        write_config "http://localhost:${PORT}/v1/" "llama" "$MODEL_PATH"
        ;;

    gemma4)
        ENDPOINT=$(gemma4 status 2>/dev/null | grep -i 'openai:' | awk '{print $2}')
        [ -z "$ENDPOINT" ] && ENDPOINT="http://127.0.0.1:8336/v1/"
        [[ "$ENDPOINT" != */ ]] && ENDPOINT="${ENDPOINT}/"
        write_config "$ENDPOINT" "gemma4"
        ;;

    qwen3|qwen3-6)
        MODEL_PATH=$(download_model "unsloth/Qwen3-6B-GGUF" "Qwen3-6B-UD-Q4_K_XL.gguf")
        kill_llama_server
        write_config "http://localhost:${PORT}/v1/" "llama" "$MODEL_PATH"
        ;;

    qwen3-30)
        MODEL_PATH=$(download_model "unsloth/Qwen3-30B-A3B-GGUF" "Qwen3-30B-A3B-UD-Q4_K_XL.gguf")
        kill_llama_server
        write_config "http://localhost:${PORT}/v1/" "llama" "$MODEL_PATH"
        ;;

    # ── Generic HuggingFace GGUF ─────────────────────────────────────────────
    gguf)
        REPO="${2:-}"
        FILE="${3:-}"
        if [ -z "$REPO" ]; then
            echo "Usage: ai-use gguf <hf_repo> [file]"
            echo "  e.g. ai-use gguf yuxinlu1/gemma-4-12B-coder-fable5-composer2.5-v1-GGUF"
            exit 1
        fi
        # Strip full HuggingFace URL if pasted
        REPO="${REPO#https://huggingface.co/}"
        REPO="${REPO%/}"

        if [ -z "$FILE" ]; then
            echo "Available GGUF files in ${REPO}:"
            FILES=$(list_hf_gguf_files "$REPO") || exit 1
            echo "$FILES" | nl -ba -w3 -s") "
            echo ""
            read -rp "Enter filename (or number): " SELECTION
            if [[ "$SELECTION" =~ ^[0-9]+$ ]]; then
                FILE=$(echo "$FILES" | sed -n "${SELECTION}p")
            else
                FILE="$SELECTION"
            fi
            [ -z "$FILE" ] && { echo "No file selected."; exit 1; }
        fi

        MODEL_PATH=$(download_model "$REPO" "$FILE")
        kill_llama_server
        write_config "http://localhost:${PORT}/v1/" "llama" "$MODEL_PATH"
        echo ""
        echo "Tip: if ctx is too large for this model, run: ai-use ctx <size>"
        echo "     e.g. ai-use ctx 8192"
        ;;

    # ── Use an existing local GGUF ───────────────────────────────────────────
    local)
        PATH_ARG="${2:-}"
        if [ -z "$PATH_ARG" ]; then
            echo "Usage: ai-use local <path-to-gguf>"
            echo ""
            echo "Downloaded models:"
            list_local_models | nl -ba -w3 -s") "
            echo ""
            read -rp "Enter path (or number from list): " SELECTION
            if [[ "$SELECTION" =~ ^[0-9]+$ ]]; then
                PATH_ARG=$(list_local_models | sed -n "${SELECTION}p")
            else
                PATH_ARG="$SELECTION"
            fi
        fi
        [ -z "$PATH_ARG" ] && { echo "No path given."; exit 1; }
        [ ! -f "$PATH_ARG" ] && { echo "File not found: $PATH_ARG"; exit 1; }
        kill_llama_server
        write_config "http://localhost:${PORT}/v1/" "llama" "$PATH_ARG"
        ;;

    # ── List downloaded models ───────────────────────────────────────────────
    models)
        echo "Downloaded models in ${MODEL_DIR}:"
        [ -f "$CONFIG" ] && source "$CONFIG"
        ACTIVE="${LLAMA_MODEL_PATH:-}"
        models=$(list_local_models)
        if [ -z "$models" ]; then
            echo "  (none)"
        else
            while IFS= read -r m; do
                size=$(du -h "$m" 2>/dev/null | cut -f1)
                if [ "$m" = "$ACTIVE" ]; then
                    echo "  * $(basename "$m")  [${size}]  ← active"
                    echo "    $m"
                else
                    echo "    $(basename "$m")  [${size}]"
                    echo "    $m"
                fi
            done <<< "$models"
        fi
        ;;

    # ── Set/clear explicit context size ─────────────────────────────────────
    ctx)
        SIZE="${2:-}"
        if [ -z "$SIZE" ]; then
            echo "Usage: ai-use ctx <size|auto>"
            echo "  size  — explicit token count, e.g. 8192 or 32768"
            echo "  auto  — remove override, let wrapper auto-calculate from VRAM"
            exit 1
        fi
        if [ "$SIZE" = "auto" ]; then
            patch_config_key "LLAMA_CTX_SIZE" ""
            echo "Context size reset to auto (calculated from available VRAM at startup)."
        else
            if ! [[ "$SIZE" =~ ^[0-9]+$ ]]; then
                echo "Invalid size '${SIZE}'. Use a number (e.g. 8192) or 'auto'."
                exit 1
            fi
            patch_config_key "LLAMA_CTX_SIZE" "$SIZE"
            echo "Context size locked to ${SIZE} tokens."
            echo "Restart llama-server to apply: kill \$(pgrep -f llama-server)"
        fi
        echo "Apply now: source ${CONFIG}"
        ;;

    # ── Set GPU layer count (for large models that don't fully fit in VRAM) ─
    gpu-layers)
        N="${2:-}"
        if [ -z "$N" ]; then
            echo "Usage: ai-use gpu-layers <n|auto>"
            echo "  n     — number of layers to offload to GPU (e.g. 24)"
            echo "  auto  — remove override (default: 99 = all layers)"
            exit 1
        fi
        if [ "$N" = "auto" ]; then
            patch_config_key "LLAMA_N_GPU_LAYERS" ""
            echo "GPU layers reset to auto (99 = all layers on GPU)."
        else
            if ! [[ "$N" =~ ^[0-9]+$ ]]; then
                echo "Invalid value '${N}'. Use a number or 'auto'."
                exit 1
            fi
            patch_config_key "LLAMA_N_GPU_LAYERS" "$N"
            echo "GPU layers set to ${N}."
            echo "Restart llama-server to apply: kill \$(pgrep -f llama-server)"
        fi
        echo "Apply now: source ${CONFIG}"
        ;;

    ""|status)
        show_status
        ;;

    *)
        echo "Unknown command: $1"
        echo "Usage: ai-use [llama|gemma4|qwen3|qwen3-30|gguf|local|models|ctx|gpu-layers|status]"
        exit 1
        ;;
esac
