# llama.cpp Local Server Install — Design Spec

**Date:** 2026-06-26  
**Status:** Approved

## Summary

Add an option for users to download, compile, and run a local `llama-server` (from llama.cpp) instead of relying on snap packages (gemma4, qwen3). Exposed as both a standalone shell script (`setup_llama.sh`) and a new `ai --install-llama` CLI flag.

---

## Components

Five new files; minimal changes to `ai.c`; `ai_mcp.py` unchanged.

| File | Location | Purpose |
|---|---|---|
| `setup_llama.sh` | repo root | Standalone entry point; installs shared script then calls it |
| `llama-install.sh` | `~/.local/bin/` | Shared install logic (called by both entry points) |
| `llama-server-wrapper.sh` | `~/.local/bin/` | Wraps `llama-server` + idle watchdog |
| `llama-server.socket` | `~/.config/systemd/user/` | Listens on port 8080; activates service on first connection |
| `llama-server.service` | `~/.config/systemd/user/` | Runs wrapper script on demand; `Restart=no` |

---

## Install Flow (`llama-install.sh`)

Executed by both `setup_llama.sh` and `ai --install-llama [repo]`.

```
1. Check / install build deps: cmake, git, build-essential, curl, wget
2. Auto-detect GPU:
     nvcc present  → cmake -DGGML_CUDA=ON
     hipcc present → cmake -DGGML_HIP=ON
     else          → CPU-only build
3. Clone llama.cpp → ~/.local/share/ai/llama.cpp  (skip if already present)
4. cmake build → install llama-server to ~/.local/bin/
5. Model selection:
     If first argument provided → use it as HF repo (skip menu)
     Else show menu:
       1) unsloth/gemma-4-12b-it-GGUF
       2) Qwen/Qwen3.6-35B-A3B
       3) unsloth/gemma-4-E4B-it-qat-GGUF
       4) Enter custom HuggingFace repo
6. Query HF API for .gguf files in chosen repo:
     curl https://huggingface.co/api/models/<repo>
     Filter files with .gguf extension → numbered list
     User picks by number
7. Download chosen file → ~/.local/share/ai/models/<filename>
8. Write systemd units and wrapper script (see below)
9. systemctl --user daemon-reload
   systemctl --user enable --now llama-server.socket
10. Append to ~/.bashrc and ~/.zshrc:
      export INFER_BASE_URL="http://localhost:8080/v1/"
      export INFER_API_KEY="not-needed"
      export INFER_MODEL="llama"
```

---

## Systemd Units

### `llama-server.socket`
```ini
[Unit]
Description=llama-server socket

[Socket]
ListenStream=8080
Accept=no

[Install]
WantedBy=sockets.target
```

### `llama-server.service`
```ini
[Unit]
Description=llama-server (on-demand, idle-unload)
After=llama-server.socket

[Service]
Type=simple
Environment=PATH=/home/<user>/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
Environment=LLAMA_MODEL_PATH=/home/<user>/.local/share/ai/models/<chosen-file>.gguf
Environment=LLAMA_IDLE_TIMEOUT=120
ExecStartPre=/bin/bash -c 'systemctl --user stop llama-server.socket || true'
ExecStart=%h/.local/bin/llama-server-wrapper.sh
ExecStopPost=/bin/bash -c 'systemctl --user start --no-block llama-server.socket || true'
Restart=no
StandardOutput=journal
StandardError=journal
```
The install script writes the actual `LLAMA_MODEL_PATH` value into this unit file at install time.

---

## Idle Watchdog (`llama-server-wrapper.sh`)

```
LLAMA_IDLE_TIMEOUT (env var, default 120 seconds)

1. Start llama-server --model <path> --port 8080 as child process
2. Wait until /health endpoint responds (server ready)
3. Loop every 10 s:
     Count established TCP connections on port 8080 via ss -tn
     If connections > 0 → update LAST_SEEN timestamp
     If (now - LAST_SEEN) > LLAMA_IDLE_TIMEOUT → kill child, exit
4. On exit, systemd returns port 8080 to the socket unit
5. Next incoming connection re-triggers the service (reloads weights)
```

Model path is read from `LLAMA_MODEL_PATH` env var, set in the systemd service unit's `Environment=` line by the install script.

Cold-start latency (first request after idle): includes full weight load time (~5–30 s depending on model and hardware).

---

## Changes to `ai.c`

### 1. New `--install-llama` flag (early-exit block, same pattern as `--set-default`)

```c
if (strcmp(argv[i], "--install-llama") == 0) {
    char script[1024];
    char *home = getenv("HOME");
    snprintf(script, sizeof(script), "%s/.local/bin/llama-install.sh", home);
    char *repo = (i + 1 < argc && argv[i+1][0] != '-') ? argv[i+1] : NULL;
    execl("/bin/bash", "bash", script, repo, NULL);
    perror("execl failed");
    return 1;
}
```

### 2. `--help` addition

```
  --install-llama [REPO]  Download, build llama.cpp and set up a local server.
                          REPO is an optional HuggingFace repo (e.g. unsloth/gemma-4-12b-it-GGUF).
                          Omit REPO to show an interactive model selection menu.
```

### 3. `detect_model_url` special-case

If `model_name` is `"llama"` or `"llama-server"`, return `http://localhost:8080/v1/` directly without shelling out. This makes `ai -m llama` and `ai --set-default llama` work without running a non-existent `llama status` command.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `LLAMA_IDLE_TIMEOUT` | `120` | Seconds of inactivity before weights are unloaded |
| `LLAMA_MODEL_PATH` | set at install time | Absolute path to the .gguf file |
| `INFER_BASE_URL` | `http://localhost:8080/v1/` | Written to shell profiles by install script |
| `INFER_MODEL` | `llama` | Written to shell profiles by install script |

---

## Out of Scope

- Multi-model support (one active model at a time)
- Windows / macOS support
- Automatic llama.cpp updates
- GPU layer count tuning (uses llama-server defaults)
