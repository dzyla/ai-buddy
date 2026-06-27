# llama.cpp Local Server Install — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `setup_llama.sh` and `ai --install-llama` to download, compile llama.cpp, pick a GGUF model from HuggingFace, and run it as a systemd socket-activated user service that auto-unloads after configurable idle time.

**Architecture:** Shared install logic lives in `llama-install.sh` (repo root, copied to `~/.local/bin/`). An idle-watchdog wrapper script is the systemd service's `ExecStart`; it starts `llama-server`, monitors TCP connections via `ss`, and self-exits after `LLAMA_IDLE_TIMEOUT` seconds idle — returning the port to the socket unit. `ai.c` gains an `--install-llama` flag that execs the shared script.

**Tech Stack:** bash, cmake, llama.cpp (github.com/ggml-org/llama.cpp), systemd user services, HuggingFace REST API (no auth required for public repos), C (existing `ai.c`), libcurl (existing)

## Global Constraints

- llama.cpp source: `https://github.com/ggml-org/llama.cpp`
- Build dir: `~/.local/share/ai/llama.cpp/`
- Models dir: `~/.local/share/ai/models/`
- Runtime scripts: `~/.local/bin/`
- Systemd units: `~/.config/systemd/user/`
- Default port: `8080`
- Default idle timeout: `120` seconds — env var `LLAMA_IDLE_TIMEOUT` in the service unit
- Model path: env var `LLAMA_MODEL_PATH` in the service unit, set at install time
- Preset HF repos (exact strings): `unsloth/gemma-4-12b-it-GGUF`, `Qwen/Qwen3.6-35B-A3B`, `unsloth/gemma-4-E4B-it-qat-GGUF`
- Env vars written to shell profiles: `INFER_BASE_URL="http://localhost:8080/v1/"`, `INFER_API_KEY="not-needed"`, `INFER_MODEL="llama"`
- All bash scripts: `#!/bin/bash` + `set -euo pipefail`
- `ai.c` model name special-cased: `"llama"` and `"llama-server"` → `http://localhost:8080/v1/`

---

### Task 1: `llama-server-wrapper.sh` — idle watchdog

**Files:**
- Create: `llama-server-wrapper.sh` (repo root; installed to `~/.local/bin/` by Task 2)

**Interfaces:**
- Consumes: `LLAMA_MODEL_PATH` (env, required), `LLAMA_IDLE_TIMEOUT` (env, default `120`)
- Produces: runs `llama-server` as a child process; exits cleanly when idle → systemd returns port 8080 to the socket unit; service can be restarted by the next connection

- [ ] **Step 1: Create `llama-server-wrapper.sh`**

```bash
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
```

- [ ] **Step 2: Make executable**

```bash
chmod +x llama-server-wrapper.sh
```

- [ ] **Step 3: Syntax check**

```bash
bash -n llama-server-wrapper.sh && echo "Syntax OK"
```

Expected: `Syntax OK`

- [ ] **Step 4: Commit**

```bash
git add llama-server-wrapper.sh
git commit -m "feat: add llama-server idle watchdog wrapper"
```

---

### Task 2: `llama-install.sh` — shared install logic

**Files:**
- Create: `llama-install.sh` (repo root; also installed to `~/.local/bin/` by Task 3)

**Interfaces:**
- Consumes: optional `$1` = HF repo string (skips model menu if supplied); `llama-server-wrapper.sh` must exist alongside this script
- Produces: `~/.local/bin/llama-server`, `~/.local/bin/llama-server-wrapper.sh`, `~/.local/share/ai/models/<file>.gguf`, `~/.config/systemd/user/llama-server.{socket,service}`, updated `~/.bashrc` and `~/.zshrc`

- [ ] **Step 1: Write script header, dep check, GPU detection, and llama.cpp build**

```bash
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
```

- [ ] **Step 2: Append model selection menu + HF file picker**

```bash
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

CHOSEN_FILE=$(echo "$GGUF_FILES" | python3 -c "
import sys
lines = sys.stdin.read().strip().split('\n')
idx = int('${FILE_CHOICE}') - 1
if idx < 0 or idx >= len(lines):
    raise SystemExit('Invalid selection')
print(lines[idx].split(') ', 1)[1])
")

echo "==> Selected: $CHOSEN_FILE"

# ── 6. Download model ─────────────────────────────────────────────────────────
MODEL_PATH="${MODEL_DIR}/${CHOSEN_FILE}"
if [ -f "$MODEL_PATH" ]; then
    echo "==> Model already exists at ${MODEL_PATH}, skipping download."
else
    echo "==> Downloading ${CHOSEN_FILE}..."
    curl -L --progress-bar \
        ${HF_TOKEN_HEADER:+-H "$HF_TOKEN_HEADER"} \
        "https://huggingface.co/${CHOSEN_REPO}/resolve/main/${CHOSEN_FILE}" \
        -o "$MODEL_PATH"
fi
echo "==> Model ready: $MODEL_PATH"
```

- [ ] **Step 3: Append wrapper install + systemd unit generation + env var writing**

```bash
# ── 7. Install wrapper script ─────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/llama-server-wrapper.sh" "${BIN_DIR}/"
chmod +x "${BIN_DIR}/llama-server-wrapper.sh"

# ── 8. Write systemd socket unit ──────────────────────────────────────────────
cat > "${SYSTEMD_DIR}/llama-server.socket" <<SOCKET_EOF
[Unit]
Description=llama-server on-demand socket

[Socket]
ListenStream=${PORT}
Accept=no

[Install]
WantedBy=sockets.target
SOCKET_EOF

# ── 9. Write systemd service unit ─────────────────────────────────────────────
cat > "${SYSTEMD_DIR}/llama-server.service" <<SERVICE_EOF
[Unit]
Description=llama-server (on-demand, idle-unload)
Requires=llama-server.socket

[Service]
Type=simple
Environment=LLAMA_MODEL_PATH=${MODEL_PATH}
Environment=LLAMA_IDLE_TIMEOUT=120
ExecStart=${BIN_DIR}/llama-server-wrapper.sh
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
```

- [ ] **Step 4: Make executable and syntax-check**

```bash
chmod +x llama-install.sh
bash -n llama-install.sh && echo "Syntax OK"
```

Expected: `Syntax OK`

- [ ] **Step 5: Commit**

```bash
git add llama-install.sh
git commit -m "feat: add llama-install.sh — build, download, and configure llama-server"
```

---

### Task 3: `setup_llama.sh` — standalone entry point

**Files:**
- Create: `setup_llama.sh` (repo root)

**Interfaces:**
- Consumes: `llama-install.sh` and `llama-server-wrapper.sh` in the same directory
- Produces: copies both to `~/.local/bin/`, then `exec`s `llama-install.sh` with all args forwarded

- [ ] **Step 1: Create `setup_llama.sh`**

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"

echo "==> Installing llama scripts to ${BIN_DIR}/..."
mkdir -p "$BIN_DIR"
cp "${SCRIPT_DIR}/llama-install.sh"         "${BIN_DIR}/"
cp "${SCRIPT_DIR}/llama-server-wrapper.sh"  "${BIN_DIR}/"
chmod +x "${BIN_DIR}/llama-install.sh" "${BIN_DIR}/llama-server-wrapper.sh"

echo "==> Launching llama-install.sh..."
exec "${BIN_DIR}/llama-install.sh" "${@}"
```

- [ ] **Step 2: Make executable and syntax-check**

```bash
chmod +x setup_llama.sh
bash -n setup_llama.sh && echo "Syntax OK"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add setup_llama.sh
git commit -m "feat: add setup_llama.sh standalone entry point"
```

---

### Task 4: `ai.c` — `--install-llama` flag, `detect_model_url` special case, help text

**Files:**
- Modify: `ai.c:966` — `detect_model_url` function start
- Modify: `ai.c:1160` — early-exit arg parsing loop
- Modify: `ai.c:1182` — `--help` output block

**Interfaces:**
- Consumes: `~/.local/bin/llama-install.sh` (produced by Tasks 2/3)
- Produces: `ai --install-llama [REPO]` execs the script; `ai -m llama` resolves URL to `http://localhost:8080/v1/` without calling `llama status`

- [ ] **Step 1: Add llama special case to `detect_model_url` at `ai.c:966`**

Find this exact text at line 966:
```c
static int detect_model_url(const char *model_name, char *url_out, size_t max_len) {
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "%s status 2>/dev/null", model_name);
```

Replace with:
```c
static int detect_model_url(const char *model_name, char *url_out, size_t max_len) {
    if (strcmp(model_name, "llama") == 0 || strcmp(model_name, "llama-server") == 0) {
        strncpy(url_out, "http://localhost:8080/v1/", max_len - 1);
        url_out[max_len - 1] = '\0';
        return 1;
    }
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "%s status 2>/dev/null", model_name);
```

- [ ] **Step 2: Add `--install-llama` to the early-exit loop at `ai.c:1160`**

Find this exact text at line 1160:
```c
    // Parse set-default option first
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--set-default") == 0 || strcmp(argv[i], "-s") == 0) {
```

Replace with:
```c
    // Parse set-default and install-llama options first (both exit early)
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--install-llama") == 0) {
            char *home = getenv("HOME");
            if (!home) { fprintf(stderr, "Error: HOME not set.\n"); return 1; }
            char script[1024];
            snprintf(script, sizeof(script), "%s/.local/bin/llama-install.sh", home);
            char *repo = (i + 1 < argc && argv[i+1][0] != '-') ? argv[i+1] : NULL;
            if (repo)
                execl("/bin/bash", "bash", script, repo, (char *)NULL);
            else
                execl("/bin/bash", "bash", script, (char *)NULL);
            perror("execl: could not run llama-install.sh");
            fprintf(stderr, "Run ./setup_llama.sh first to install the script.\n");
            return 1;
        }
        if (strcmp(argv[i], "--set-default") == 0 || strcmp(argv[i], "-s") == 0) {
```

- [ ] **Step 3: Add `--install-llama` line to `--help` output at `ai.c:1182`**

Find this exact text at line 1182:
```c
            printf("  -s, --set-default M  Set the global default model in shell configs.\n");
            printf("  -h, --help           Display this help screen.\n\n");
```

Replace with:
```c
            printf("  -s, --set-default M  Set the global default model in shell configs.\n");
            printf("  --install-llama [R]  Download, build llama.cpp and start a local server.\n");
            printf("                       R: optional HuggingFace repo (e.g. unsloth/gemma-4-12b-it-GGUF).\n");
            printf("                       Omit R to show an interactive model selection menu.\n");
            printf("  -h, --help           Display this help screen.\n\n");
```

- [ ] **Step 4: Build and verify**

```bash
gcc -o ai ai.c -lcurl
```

Expected: zero errors, zero warnings.

```bash
./ai --help | grep -A3 install-llama
```

Expected:
```
  --install-llama [R]  Download, build llama.cpp and start a local server.
                       R: optional HuggingFace repo (e.g. unsloth/gemma-4-12b-it-GGUF).
                       Omit R to show an interactive model selection menu.
```

```bash
# Verify detect_model_url special case does not call "llama status" (would error)
# Use strace to confirm no exec of "llama":
strace -e trace=execve ./ai -m llama --help 2>&1 | grep -v 'llama status' | grep execve | head -5
```

Expected: no line containing `llama status` in the strace output.

- [ ] **Step 5: Commit**

```bash
git add ai.c
git commit -m "feat: add --install-llama flag and llama shortcut in detect_model_url"
```

---

### Task 5: End-to-end verification

No new files. Verifies the full install + use cycle works correctly.

- [ ] **Step 1: Run `setup_llama.sh` (or `ai --install-llama`) and pick a model**

```bash
# Option A — standalone script:
./setup_llama.sh

# Option B — via ai flag:
./ai --install-llama
```

Walk through the interactive menus. Select `unsloth/gemma-4-12b-it-GGUF`, then pick a Q4_K_M file from the list.

Expected: build completes, model downloads, systemd units are written, socket is enabled.

- [ ] **Step 2: Verify systemd units**

```bash
systemctl --user status llama-server.socket
systemctl --user is-enabled llama-server.socket
cat ~/.config/systemd/user/llama-server.service
```

Expected: socket is `active (listening)` and `enabled`; service unit contains the correct `LLAMA_MODEL_PATH`.

- [ ] **Step 3: Trigger first connection (cold start)**

```bash
source ~/.bashrc
time curl -s http://localhost:8080/health
```

Expected: server starts (watch `journalctl --user -u llama-server -f` in another terminal), `/health` returns `{"status":"ok"}` after model loads.

- [ ] **Step 4: Verify idle shutdown**

Set a short timeout to test without waiting 2 min:
```bash
# Edit the service unit temporarily
sed -i 's/LLAMA_IDLE_TIMEOUT=120/LLAMA_IDLE_TIMEOUT=15/' \
    ~/.config/systemd/user/llama-server.service
systemctl --user daemon-reload
systemctl --user restart llama-server.service
```

Wait 15+ seconds with no requests, then:
```bash
systemctl --user is-active llama-server.service
```

Expected: `inactive` (service stopped itself).

```bash
# Restore
sed -i 's/LLAMA_IDLE_TIMEOUT=15/LLAMA_IDLE_TIMEOUT=120/' \
    ~/.config/systemd/user/llama-server.service
systemctl --user daemon-reload
```

- [ ] **Step 5: Verify socket re-activates after idle shutdown**

```bash
curl -s http://localhost:8080/health
```

Expected: service starts again (model loads), `/health` returns `{"status":"ok"}`.

- [ ] **Step 6: Test `ai -m llama`**

```bash
echo "say hello" | ./ai -m llama "say hello in one word"
```

Expected: runs against the local llama-server, produces a response, no error about missing `llama status`.

- [ ] **Step 7: Test passing repo directly**

```bash
# Dry-run: just verify argument is forwarded (cancel at model file picker)
./ai --install-llama unsloth/gemma-4-E4B-it-qat-GGUF
```

Expected: skips model menu, goes directly to the GGUF file list for that repo.
