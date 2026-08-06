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
cat > ~/.local/share/ai/env <<'EOF'
export INFER_BASE_URL="http://localhost:8080/v1/"
export INFER_API_KEY="your-key"
export INFER_MODEL="your-model-name"
EOF

ai "hello"
```

---

## Backends & Model Deployment

`ai-backend` is the single, unified tool that manages model loading, downloading, setting, and serving. Active config lives in `~/.local/share/ai/env` and is loaded directly by the `ai` binary.

```bash
ai-backend status                             # Show active backend, model, and server status
ai-backend use <hf_uri|model_path|preset>     # Download (if needed) & switch active model
                                              # Supports hf:// URIs, split GGUFs, local files
ai-backend list                               # List all downloaded GGUF models
ai-backend auto                               # Switch to best available running backend
ai-backend ctx <size|auto>                    # Set or clear explicit context window
ai-backend gpu-layers <n|auto>                # Set GPU offload layer count
ai-backend serve                              # Run llama-server with auto-VRAM context sizing
```

### Loading & Downloading Models

`ai-backend` natively supports direct downloading and auto-switching from Hugging Face via `hf://` URIs, URLs, or repository names, including automatic multi-part split GGUF detection:

```bash
# 1. Download and use a multi-part split GGUF model via hf:// URI
ai-backend use hf://AtomicChat/Ling-3.0-flash-GGUF/AD-IQ3_M/Ling-3.0-flash-AD-IQ3_M-00001-of-00002.gguf

# 2. Download from Hugging Face repo with interactive file selection
ai-backend download unsloth/gemma-4-12b-it-GGUF

# 3. Switch to an existing local model
ai-backend use /path/to/model.gguf
# Or select from downloaded models list by number
ai-backend use

# 4. For gated or private Hugging Face repositories
export HF_TOKEN="hf_your_token_here"
ai-backend use hf://meta-llama/Llama-3.2-3B-Instruct-GGUF
```

### Context & GPU Tuning

```bash
ai-backend ctx 8192         # Set explicit context window (or 'auto')
ai-backend gpu-layers 24    # Set GPU layer offload count (or 'auto')
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

All config lives in `~/.local/share/ai/env` (managed by `ai-backend`) and is loaded directly by the `ai` binary.

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
| `-c` | `--continue` | `INFER_CONTINUE=1` | Run without turn limit until done |
| `-r` | `--resume` | `INFER_RESUME=1` | Resume the previous conversation |
| `-q` | `--quiet` | `INFER_QUIET=1` | Suppress thinking output |
| `-n` | `--no-tools` | | Direct answer, skip the agent loop |
| `-h` | `--help` | | Print help |

### Resume a conversation

Every run is saved to `~/.cache/ai/sessions/<session_id>.json` and also as `last.json`. The session ID is printed when you exit (`[ai] Session ended. To resume: ai -r sess_123456789`). Continue it later from any terminal:

```bash
ai "clone github.com/foo/bar and summarise its architecture"
# ... later ...
ai -r "now add a Dockerfile for it"     # resumes the last turn
ai -r sess_123456789 "continue this"  # resumes a specific past session
```

### Customize the system prompt

Drop a `~/.config/ai/system_prompt.md` file to override the built-in agent prompt without recompiling (or point `INFER_SYSTEM_PROMPT_FILE` at any path). Delete the file to revert to the default.

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

## Google Calendar

With Google Calendar credentials configured (`python3 gcal.py auth`), the agent can manage your schedule end-to-end:

- `gcal_list_events` — read your schedule across calendars.
- `gcal_check_availability` — free/busy lookup.
- `gcal_quick_add` — create an event from a natural-language phrase ("lunch with Sam tomorrow 1pm"); Google parses the date/time.
- `gcal_create_event` — create with explicit times. Timezone offsets are optional — naive times use your local timezone (or a `time_zone` arg), and bare dates (`2026-07-05`) create all-day events.
- `gcal_update_event` — reschedule or modify an event (only the fields you pass change).
- `gcal_delete_event` — cancel an event.

```bash
ai "am I free Thursday afternoon? if so, book a 1h focus block at 2pm"
ai "move my 3pm dentist appointment to Friday same time"
```

## Zulip Integration

You can integrate `ai` with Zulip to chat with the local agent directly from your mobile device or desktop. The Zulip MCP server exposes `zulip_send_message`, `zulip_get_messages`, `zulip_add_reaction`, and `zulip_edit_message`.

**Reminders.** Ask the agent (in Zulip chat or the terminal) to remind you later, and it schedules a one-shot reminder delivered straight back to your Zulip DM at the requested time — no LLM runs at delivery, so it can't be garbled:

```
you:  hey, remind me tomorrow at 9am to submit the grant report
ai:   ⏰ Reminder set for 2026-07-04 09:00 (in ~840 min) → DM to you@…: "submit the grant report"
```

From Zulip the recipient is auto-filled (the requester). In the terminal, pass a recipient (`zulip_to` email, or a `zulip_stream`). Under the hood this is the `set_reminder` tool.

For complete configuration instructions, bot creation, and setting up the systemd user service background daemon, see [docs/zulip-setup.md](docs/zulip-setup.md).

---

## Runtime state

| Path | Purpose |
|------|---------|
| `~/.local/share/ai/env` | Active backend config (INFER_* vars) |
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
