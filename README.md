# ai

A minimal, agentic CLI that pipes anything into an LLM and executes work in the terminal — written in C and Python with no external library dependencies beyond `libcurl`.

```bash
ps aux | ai "what's eating memory"
git diff | ai "summarize my changes"
ai "what's the current Bitcoin price?"
ai -i   # interactive REPL
```

---

## Quick Start

### 1. Install dependencies

```bash
sudo apt install gcc libcurl4-openssl-dev python3   # Debian/Ubuntu
brew install curl python                             # macOS
```

### 2. Build and install

```bash
git clone https://github.com/dzyla/ai.git
cd ai
./install.sh
```

Everything goes to `~/.local/bin` — no sudo required. To uninstall (this will also cleanly stop and remove systemd service and socket files if you installed llama):

```bash
./install.sh uninstall
```

### 3. Point it at a model

```bash
# If you have a Canonical AI snap (qwen3-6, gemma4) installed:
ai-backend snap

# Or set env vars manually for any OpenAI-compatible endpoint:
cat > ~/.config/ai/env <<'EOF'
export INFER_BASE_URL="http://localhost:8080/v1/"
export INFER_API_KEY="your-key"
export INFER_MODEL="your-model-name"
EOF

source ~/.config/ai/env
ai "hello"
```

---

## Backends

`ai-backend` manages which LLM server `ai` talks to. Config lives in `~/.config/ai/env` and is sourced by your shell on startup.

```bash
ai-backend status          # show active backend and what's available
ai-backend auto            # switch to whatever is currently running
ai-backend qwen3-6         # switch to qwen3-6 snap (reads its live port)
ai-backend gemma4          # switch to gemma4 snap
ai-backend llama           # switch to local llama-server
ai-backend llama /path/to/model.gguf   # switch + set model file
ai-backend llama --list    # list downloaded models
```

### Local llama.cpp server

To set up a local inference server with GPU acceleration and on-demand auto-start:

```bash
./install.sh llama
```

This builds llama.cpp (auto-detects CUDA/ROCm/Vulkan), downloads a model from HuggingFace (interactive), and creates a systemd user service that starts on the first `ai` call and shuts down after 120 s of idle.

```bash
# Logs
journalctl --user -u llama-server -f

# Force restart with a different model
ai-backend llama ~/.local/share/ai/models/my-model.gguf
systemctl --user restart llama-server
```

---

## Configuration

All config lives in `~/.config/ai/env` (managed by `ai-backend`) and is sourced from `~/.bashrc`.

| Variable | Description | Default |
|----------|-------------|---------|
| `INFER_BASE_URL` | API endpoint — must end in `/v1/` | required |
| `INFER_API_KEY` | API key | required |
| `INFER_MODEL` | Model name sent in each request | required |
| `INFER_AUTO_APPROVE=1` | Skip `[Y/n]` prompts for shell commands | off |
| `INFER_QUIET=1` | Suppress `[thinking]` output | off |
| `INFER_TOOL_CHOICE` | `required` (force tool call) or `auto` | `required` |
| `INFER_DEBUG` | Dump raw JSON payloads to stderr | off |
| `INFER_MAX_TOOL_OUTPUT` | Cap individual tool output (bytes) | 65536 |
| `INFER_TRIM_THRESHOLD` | Trim conversation when context exceeds this | 100000 |
| `INFER_STUB_THRESHOLD` | Stub tool results once context exceeds this | 250000 |
| `INFER_TASK_TIMEOUT` | Force `task_complete` after N seconds | 300 |

### MCP servers

Register additional tool servers in `mcp.json` (project-local) or `~/.config/ai/mcp.json` (global):

```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["~/.local/bin/my-mcp-server.js"],
      "env": { "API_KEY": "..." }
    }
  }
}
```

Paths in `args` support `~` and `$HOME`. Tools appear as `my_server__tool_name` in the model's catalog.

Config search order: `./mcp.json` → `./mcp_config.json` → `~/.config/ai/mcp.json` → `~/.config/ai/mcp_config.json` → `~/.gemini/config/mcp_config.json` → `~/.lmstudio/mcp.json`

---

## Usage

### One-shot queries

```bash
ai "what's the tar command to extract .tar.gz?"
ai how do I exit vim
```

### Interactive REPL

```bash
ai          # start conversation shell
ai -i "let's look at this project"   # start with an initial prompt
```

| Command | Effect |
|---------|--------|
| `:compact` | Summarise conversation and reset context |
| `:clear` | Wipe conversation history entirely |
| `:status` | Show context size, model, auto-approve state |
| `:memory` | Show persistent memory |
| `:auto` | Toggle auto-approve for shell commands |
| `:help` | Show command list |

| Key | Effect |
|-----|--------|
| `Shift-Tab` | Toggle auto-approve (at prompt or mid-execution) |
| `ESC` | Interrupt the running agent turn |
| `↑ / ↓` | Navigate input history |

### Pipe anything in

```bash
ps aux | head -20 | ai "what's using the most memory?"
df -h | ai "am I running out of space anywhere?"
git diff | ai "summarize my changes"
dmesg | tail -20 | ai "any hardware warnings?"
```

### Flags

| Flag | Long | Env var | Effect |
|------|------|---------|--------|
| `-i` | `--interactive` | | Start REPL |
| `-y` | `--yes` | `INFER_AUTO_APPROVE=1` | Auto-approve shell commands |
| `-q` | `--quiet` | `INFER_QUIET=1` | Suppress thinking output |
| `-h` | `--help` | | Print help |

### Images

```bash
ai "what's in this picture?" path/to/image.png
ai "describe the chart" screenshot.webp
```

### Persistent memory

```bash
ai "remember my name is Alice and I prefer TypeScript. Save to memory."
ai "what's my name?"   # recalled in a fresh session
```

---

## Tools

| Tool | What it does |
|------|-------------|
| `think` | Model writes a step-by-step plan before acting |
| `execute_command` | Runs a shell command with `[Y/n]` confirmation |
| `web_search` | DuckDuckGo Lite — no API key needed |
| `fetch_webpage` | Downloads and cleans a URL to readable text |
| `read_file` | Text, PDF (pdftotext/pypdf/pdfplumber), images (vision) |
| `write_file` | Write a file, creating parent dirs as needed |
| `edit_file` | Search-and-replace edit; fuzzy-matches trailing whitespace |
| `list_directory` | Directory listing with sizes |
| `save_memory` | Persist text to `~/.config/ai/memory.txt` (4 KB cap) |
| `delegate_task` | Spawn a child `ai` process for independent sub-tasks |
| `computer_control` | Screenshot, mouse, keyboard via xdotool/scrot |
| `task_complete` | Signal completion; `summary` rendered as markdown |

---

## Skills

Drop a `SKILL.md` into `.agents/skills/<name>/` (project-local) or `~/.config/ai/skills/<name>/` (global) and `ai` will load it into every system prompt automatically.

Re-run `./install.sh` after adding skills to sync project skills to the global location.

---

## Runtime state

| Path | Purpose |
|------|---------|
| `~/.config/ai/env` | Active backend config (INFER_* vars) |
| `~/.config/ai/memory.txt` | Persistent memory, injected into every prompt |
| `~/.config/ai/mcp.json` | Global MCP server registry |
| `~/.config/ai/skills/` | Global skills directory |
| `~/.cache/ai/history.jsonl` | Append-only job log |
| `~/.cache/ai/input_history` | Interactive REPL input history |
| `~/.local/share/ai/models/` | Downloaded GGUF model files |
| `~/.local/share/ai/llama.cpp/` | llama.cpp source and build |

---

## Architecture

Two cooperating processes talk via subprocess calls — no shared library or IPC:

- **`ai.c`** — the agent loop. Owns the conversation, calls the LLM, handles `think` / `task_complete` / `execute_command` natively, delegates all other tool calls to `ai_mcp.py`.
- **`ai_mcp.py`** — the tool backend. Implements 12 native tools and acts as a generic MCP client for any server in `mcp.json`.

For details on the architecture, adding tools, and cross-process contracts, see [`CLAUDE.md`](CLAUDE.md).

---

## Acknowledgements

Fork of [infer](https://github.com/chethanreddy1/infer) by chethanreddy1. Extended with: agentic tool loop, shell execution, web search, file ops, persistent memory, sub-agent delegation, multimodal images, MCP integration, skill loading, rich terminal markdown rendering, context guards, and interactive REPL.

**License:** MIT
