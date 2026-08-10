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

### Server-level sampling penalties

`ai-backend serve` passes sampling penalties directly to `llama-server` via
`--repeat-penalty`, `--presence-penalty`, `--frequency-penalty`, and `--repeat-last-n`.
These are global for every request served by the process. You configure them with
environment variables in `~/.local/share/ai/env`:

```bash
export LLAMA_REPEAT_PENALTY="1.05"        # 1.0 = neutral; >1 scales penalty with reuse
export LLAMA_PRESENCE_PENALTY="0.6"       # >0 pushes away from tokens already used
export LLAMA_FREQUENCY_PENALTY="0.0"      # additive frequency penalty (0.0 = neutral)
export LLAMA_REPEAT_LAST_N="64"           # tokens considered for repeat penalty
```

**How these compare to the AI-level (`ai`) penalties:**

- `ai` also sends `frequency_penalty` (default `INFER_FREQ_PENALTY=0.10`) and
  `presence_penalty` (default `INFER_PRESENCE_PENALTY=0.05`) in every OpenAI-style
  API request. These are *per-request* and can differ across agents/sessions.
- The server-level settings apply to *all* requests equally and are set once at
  server start. With the neutral defaults (`repeat_penalty=1.0`, `presence=0.0`,
  `frequency=0.0`) the active loop-breaking mechanism is the per-request AI-level
  penalties (`INFER_FREQ_PENALTY` / `INFER_PRESENCE_PENALTY`).
- For breaking out of thinking/reply loops, a good combination is
  `LLAMA_REPEAT_PENALTY=1.05` plus `INFER_PRESENCE_PENALTY=0.6` (as reported by
  XDA Developers), but these values are model/task-specific — always start at
  neutral and increase gradually.
- **Repeat penalty** (llama.cpp multiplier) applies a scaling penalty that grows
  with each reuse of a token; **presence/frequency penalty** (additive) applies a
  flat penalty per distinct token that has appeared. They are different mechanisms
  and can be combined.

See `docs/situational_awareness.md` for how the agent detects and breaks loops.

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
| `INFER_STEP_LIMIT` | Cap on agent-loop tool iterations for a task (piped/auto runs are finite now) | 60 (non-tty), 30 (tty) |
| `INFER_PLAN_STEP_BUDGET` | State-changing actions allowed per approved `present_plan` (PLAN mode); `0` = unlimited | 8 |
| `INFER_PLAN_AUTOAPPROVE=1` | Auto-approve `present_plan` in PLAN mode (opt-in, for trusted/harnessed/bridge runs only) | off |
| `LLAMA_REPEAT_PENALTY` | Server-level repeat penalty (llama.cpp multiplier, 1.0 = neutral) | `1.0` |
| `LLAMA_PRESENCE_PENALTY` | Server-level presence penalty (additive, 0.0 = neutral) | `0.0` |
| `LLAMA_FREQUENCY_PENALTY` | Server-level frequency penalty (additive, 0.0 = neutral) | `0.0` |
| `LLAMA_REPEAT_LAST_N` | Number of recent tokens considered for repeat penalty | `64` |

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
| `--auto` | `--auto-approve` | `INFER_PERMISSION_MODE=auto` | FULL AUTONOMY: change state freely until done |
| `--plan` | | `INFER_PERMISSION_MODE=plan` | PLAN: investigate & report, but no changes until `present_plan` is approved, then work autonomously |
| `--manual` | | `INFER_PERMISSION_MODE=manual` | MANUAL: ask for approval before every state-changing action |
| `-y` | `--yes` | `INFER_AUTO_APPROVE=1` | Auto-approve shell commands (a.k.a. `--auto`) |
| `-c` | `--continue` | `INFER_CONTINUE=1` | Run without turn limit until done |
| `-r` | `--resume` | `INFER_RESUME=1` | Resume the previous conversation |
| `-q` | `--quiet` | `INFER_QUIET=1` | Suppress thinking output |
| `-n` | `--no-tools` | | Direct answer, skip the agent loop |
| `-h` | `--help` | | Print help |

### Permission modes (manual / plan / auto)

The harness has three permission modes that control how much autonomy the agent has.
They address the problem of the agent "running away and changing things" without
checking with you. Set a mode with a flag (above) or `INFER_PERMISSION_MODE=
{manual|plan|auto}`, or cycle it live with `Shift-Tab` while the agent runs.

| Mode | Behaviour |
|------|-----------|
| **MANUAL** (`--manual`) | Asks you for explicit approval before EVERY state-changing action (commands, file writes/edits, memory, scheduling). Read-only investigation runs freely. Nothing changes without your say-so. |
| **PLAN** (`--plan`) | Investigates, reads, searches, and runs read-only commands on its own, but makes **no changes** until it calls `present_plan(plan="...")` and you approve. Each approval grants a **bounded** number of state-changing actions (`INFER_PLAN_STEP_BUDGET`, default 8); once that budget is spent the harness blocks further changes and forces the agent to present an updated plan and be re-approved before continuing. It must validate each step before the next. |
| **FULL AUTONOMY** (`--auto`, default) | Investigates and changes state freely until the task is finished, then reports. |

In **plan** mode the agent is strongly guided (via injected system context) to
investigate first, report its discoveries, present a concrete plan with exact changes
and rationale, and wait for your approval before mutating anything. If you reject the
plan, it revises and asks again. Read-only tools (`think`, `read_file`,
`list_directory`, `web_search`, `fetch_*`, searches, `load_skill`) are never gated, so
the agent can always investigate.

### Continuous self-improvement (skills)

The harness learns from the past. After completing a task the agent can persist what it
learned into reusable skills, checked into the repo (`.agents/skills/`) and synced to
your global skills directory (`~/.config/ai/skills/`), so future sessions start from
today's discoveries:

- **`skill_create(name, description, content)`** — save a new reusable technique/workflow.
- **`skill_update(name, note)`** — append a "good to know" fix or correction to a skill
  the agent loaded and found wrong/outdated.
- **`skill_note(name?, note)`** — append a standalone insight to the learning log
  (`~/.config/ai/skills_learning_log.md`).

The agent is prompted (via the `self_improvement` skill and system context) to persist
learnings after non-trivial tasks and to notify you whenever a skill is created or
updated. Run `ai "load_skill(self_improvement)"` to read the guidance the agent follows.

### Searchable conversation history (backup + learn)

Every conversation is backed up and searchable so the agent can learn from the past:

- Each session is saved to **two places** for durability: `~/.cache/ai/sessions/` (fast
  cache) and `~/.local/share/ai/sessions/` (persistent local user data, survives cache
  clears). Every turn also appends to the global log `~/.cache/ai/history.jsonl`.
- A fast full-text (SQLite FTS5) index at `~/.local/share/ai/history_index.db` is kept
  automatically up to date.
- The agent can search and read past conversations through three tools the harness
  exposes and the `search_history` skill documents:
  - **`search_history(query)`** — full-text search over all past conversations; returns
    matching snippets + session IDs, rebuilt automatically when history changes.
  - **`list_sessions(limit)`** — list the most recent backed-up conversations.
  - **`get_session(session_id)`** — read a full prior conversation as a transcript.

Ask the agent "how did we do X last time?" or "recall the session where we fixed Y" and
it will search its own history, then verify against the current state before reusing an
old approach.

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
- **`ai_mcp.py`** — the tool backend. Implements a large set of native tools (file I/O, web search, fetch, scheduling, background processes, memory/vault, search APIs, agent spawning) and acts as a generic MCP client for any server in `mcp.json`. It has a `#!/usr/bin/env python3` shebang and is directly executable via `./ai_mcp.py`.

Additional processes and services:

- **`gcal.py`** — Google Calendar authentication and CLI helper.
- **`pubmed_mcp_server.py`** — PubMed MCP server for literature search.
- **`deep_research.py`** — Deep research tool (multi-hop, iterative retrieval).
- **`zulip_ai_bridge.py`** — Zulip bot bridge that pipes messages to the `ai` CLI and downloads/extracts file attachments before passing them to the agent.
- **`zulip_mcp_server.py`** — Zulip MCP server exposing `zulip_send_message`, `zulip_get_messages`, `zulip_add_reaction`, `zulip_edit_message`.
- **`ContextWindowManager`** — monitors context-window budget across calls; on overflow it auto-splits the response and continues the conversation so work proceeds even when a single LLM call would exceed the token limit.
- **Scheduling/background processes** — `schedule_task` and `set_reminder` run deferred work in detached background processes with explicit termination guards (`max_runs`, `ttl_hours`); `start_background_process` / `check_process_status` / `stop_process` manage long-running jobs.

For details on the architecture, adding tools, and cross-process contracts, see [`CLAUDE.md`](CLAUDE.md).

---

## Project Structure

```
├── ai.c                  # Main agent loop (C)
├── ai_mcp.py             # Tool backend (Python)
├── ai-backend            # Backend manager (installed to ~/.local/bin)
├── ai_session.c/h        # Session management
├── ai_terminal.c/h       # Terminal handling
├── ai_git.c/h            # Git integration
├── cJSON.c/h             # JSON parsing library (vendored)
├── jsmn.h                # Lightweight JSON parser (vendored)
├── remote_harness.c/h    # Remote harness library
├── Makefile              # Build system
├── install.sh            # Install script
├── mcp.json              # MCP server configuration
├── gcal.py               # Google Calendar integration
├── pubmed_mcp_server.py  # PubMed MCP server
├── deep_research.py      # Deep research tool
├── zulip_ai_bridge.py    # Zulip AI bridge
├── zulip_mcp_server.py   # Zulip MCP server
├── tests/                # Test suite
├── docs/                 # Documentation
├── .agents/              # Project skills
├── build/                # Compiled binaries (not tracked)
│   ├── ai                # Compiled CLI binary
│   └── libremote_harness.so
└── dev/                  # Development utilities
    ├── benchmark*.py     # Benchmarking scripts
    ├── test_*.py         # Standalone test scripts
    └── *.py              # Debugging and reporting utilities
```

---

## Acknowledgements

Fork of [infer](https://github.com/chethanreddy1/infer) by chethanreddy1. Extended with: agentic tool loop, shell execution, web search, file ops, persistent memory, sub-agent delegation, multimodal images, MCP integration, skill loading, rich terminal markdown rendering, context guards, and interactive REPL.

**License:** MIT
