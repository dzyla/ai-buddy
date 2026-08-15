# AGENTS.md

## Project Overview

`ai` is a minimal, agentic CLI that pipes input to an LLM and executes work in the terminal. It combines a C-based agent loop with a Python tool backend, communicating via subprocess calls — no shared library or IPC required.

**Two-process architecture:**
- **`ai.c`** — Main agent loop. Owns conversation state, calls the LLM, handles `think` / `task_complete` / `execute_command` natively, and delegates all other tool calls to `ai_mcp.py`.
- **`ai_mcp.py`** — Tool backend. Implements a large set of native tools (file I/O, web search, fetch, scheduling, background processes, memory/vault, search APIs, agent spawning) and acts as a generic MCP client for any server defined in `mcp.json`. It has a `#!/usr/bin/env python3` shebang and is directly executable via `./ai_mcp.py`.

**Build outputs:**
- `ai` — main CLI binary
- `libremote_harness.so` — remote harness shared library

---

## Building and Running

### Prerequisites

```bash
sudo apt install gcc libcurl4-openssl-dev libssl-dev python3   # Debian/Ubuntu
brew install curl python                                        # macOS
```

### Build

```bash
make                    # builds ai and libremote_harness.so
make clean              # remove build artifacts
make test               # run C test + pytest suite
```

### Install

```bash
./install.sh                  # installs to ~/.local/bin
./install.sh llama unsloth    # build & set up Unsloth llama.cpp (iq1-narrow) inference server
./install.sh llama og         # build & set up standard ggml-org/llama.cpp inference server
./install.sh --update-llama   # update & rebuild llama.cpp for active flavor
./install.sh uninstall        # remove all installed artifacts
```

### Run

```bash
ai "what's the current Bitcoin price?"
ai -i                   # interactive REPL
ps aux | ai "what's eating memory?"
ai --plan "refactor the auth module"     # investigate first, ask before changing state
ai --manual "tidy the scripts dir"       # you approve every state-changing action
```

### Permission modes (manual / plan / auto)

`ai` has three permission modes, selected by flag or `INFER_PERMISSION_MODE`:

- **`--manual`** (`INFER_PERMISSION_MODE=manual`): the agent asks for explicit approval
  before EVERY state-changing action (commands, write/edit, memory, scheduling).
  Read-only investigation tools run freely.
- **`--plan`** (`INFER_PERMISSION_MODE=plan`): the agent investigates, reads, searches,
  and runs read-only commands, but makes NO changes until it calls `present_plan(...)`
  and the user approves. Each approval grants a **bounded** number of state-changing
  actions (`INFER_PLAN_STEP_BUDGET`, default 8); when that budget is spent the harness
  blocks further changes and the agent must `present_plan` again to be re-approved.
  Steps are executed one at a time and validated before proceeding.
- **`--auto`** / default (`INFER_PERMISSION_MODE=auto`, `-y`, `INFER_AUTO_APPROVE=1`):
  full autonomy — the agent changes state freely until the task is finished.

The plan/manual gating lives in `ai.c` (the `tool_is_mutating()` classification and the
`present_plan` handler in the agent loop); read-only tools are never gated. Env/CLI
selections are honoured so the Zulip bridge and schedulers can set a mode (`BRIDGE_AI_MODE`,
`INFER_PERMISSION_MODE`).

### Situational state header

`ai.c` prepends a compact `[CURRENT STATE step N] <tool> -> ok|error | <rolling log>`
header to every tool result, so small local models can always see where they are in a task
(step number, this call's status, recent trajectory) without holding it in memory. The
rolling log is bounded and per-task. Disable with `INFER_STATE_CONTEXT=0`. Tool-error
`[HINT: ...]` / `[GRAPH ENFORCEMENT: ...]` guidance is injected into the model-visible
tool message (not just the terminal), and failed `execute_command` output
(`— failed (exit N)`) is correctly labelled `error`. See `docs/situational_awareness.md`.

### Continuous self-improvement (skills)

`ai_mcp.py` provides `skill_create`, `skill_update`, and `skill_note` so the agent can
persist what it learns into skills — written to `.agents/skills/` (checked into the repo)
and synced to `~/.config/ai/skills/` (global). A learning log lives at
`~/.config/ai/skills_learning_log.md`. `ai.c` surfaces a user notification when a skill is
created/updated. The `self_improvement` skill instructs the agent when to persist learnings.

### Searchable conversation history

Every conversation is backed up to both `~/.cache/ai/sessions/` (fast) and the persistent
`~/.local/share/ai/sessions/` (survives cache clears), and every turn appends to
`~/.cache/ai/history.jsonl` with its `session_id`. A kept-fresh SQLite FTS5 index
(`~/.local/share/ai/history_index.db`) powers three agent-facing tools: `search_history`,
`list_sessions`, and `get_session`, which let the agent recall and learn from past
conversations (documented in the `search_history` skill).

Index design (see `ai_mcp.py`, the "Searchable conversation history" section):
- **Incremental by default**: `search_history` only appends new log lines / changed
  session files (offset + size/mtime fingerprints in `hmeta`). A full wipe-and-rebuild
  happens only when the DB is missing, the schema changed, or the log shrank. A no-op
  fast path skips all work when the sources are provably unchanged, so warm searches
  are ~1 ms.
- **Dedup by session id**: a session present in both the cache and the persistent dir
  is indexed once. The index also stores each session's raw messages JSON
  (`history_sessions`), making it a durable archive: `get_session` and `list_sessions`
  still serve a session whose on-disk files were pruned (marked `[archive]`).
- **Atomic writes**: `save_session` (ai_session.c) writes to `<file>.tmp.<pid>` + fsync
  + rename, so a crash/SIGINT can't leave a torn session JSON.
- **Resume fallback**: `ai -r <id>` falls back to the persistent mirror when the cache
  file is gone.
- **Bounded cache (archive-first retention)**: at run exit the binary calls
  `ai_mcp.py prune-sessions`, which deletes only the OLDEST cache session files once
  they are preserved in the persistent mirror or fully archived in the index. Keep the
  newest `INFER_SESSION_RETENTION` files (default 400; `0` disables). Never touches
  `last.json` or un-archived sessions. Opt-in archive aging: `INFER_ARCHIVE_RETENTION=<days>`.
- WAL journal mode is enabled on the index DB. Tests: `tests/test_history_index.py`
  (unit + binary integration, all offline).

### Configure

Set your model endpoint in `~/.local/share/ai/env`:

```bash
export INFER_BASE_URL="http://localhost:8080/v1/"
export INFER_API_KEY="your-key"
export INFER_MODEL="your-model-name"
```

### Server-level sampling penalties & Qwen3.8 settings (llama.cpp / Unsloth)

`ai-backend serve` forwards sampling penalties directly to `llama-server`. These apply
globally to every request while the process is running:

| Variable | Description | Default |
|----------|-------------|---------|
| `LLAMA_REPEAT_PENALTY` | llama.cpp repeat penalty multiplier (1.0 = neutral) | `1.0` |
| `LLAMA_PRESENCE_PENALTY` | presence penalty (additive; 0.0 = neutral) | `0.0` |
| `LLAMA_FREQUENCY_PENALTY` | frequency penalty (additive; 0.0 = neutral) | `0.0` |
| `LLAMA_REPEAT_LAST_N` | recent tokens considered for repeat penalty | `64` |
| `LLAMA_MTP` | Multi-Token Prediction (MTP) speculative decoding (1/0) | `0` |
| `LLAMA_SPEC_DRAFT_N_MAX` | Speculative tokens draft count | `3` (MTP/dspark) |

**Qwen3.8 and Unsloth Integration:**

- **Unsloth llama.cpp flavor:** `./install.sh llama unsloth` installs the `iq1-narrow`
  Unsloth fork supporting dynamic quants (`IQ1_XXXS` / `Q1_0`, `TQ1_0`, etc.), fast decode,
  and native MTP speculative decoding (`--spec-type draft-mtp`).
- **Qwen3.8 Presets & Mode commands:**
  - `ai-backend use qwen3.8` (or `qwen3.8-27b`, `qwen3.8-2.4t`) automatically routes and downloads Unsloth GGUFs.
  - `ai-backend mode <preset>`: `thinking`/`xhigh` (effort xhigh), `normal` (medium), `low` (low), `instruct` (none). Each applies the full preset (`temp`, `top_p`, `top_k`, `min_p`, `presence`, `repeat`, `reasoning_effort`) to the env file.
  - `ai-backend yarn <on|off|<scale>>`: enables YaRN RoPE scaling to extend context beyond the model's native 256K (on → scale 4 → ~1M). Persists `LLAMA_ROPE_SCALING`/`LLAMA_ROPE_SCALE`/`LLAMA_YARN_ORIG_CTX`; `serve` emits `--rope-scaling yarn --rope-scale 4.0 --yarn-orig-ctx 262144`.
  - `ai-backend mtp on`: enables MTP speculative decoding (head ships in the GGUF, `blk.64.nextn.*`); pins `--parallel 1` (the draft ctx is single-sequence — override with `LLAMA_N_PARALLEL`).
  - `ai-backend probe [url]`: streaming decode-speed A/B probe (`dev/probe_mtp.py`); run baseline vs MTP, the delta is the number.
  - `ai` CLI flags: `--mode <preset>` (one-shot sampling preset, same as `:mode` in the REPL), `:mode [preset]` (live per-session REPL command), `-p/--top-p`, `-k/--top-k`, `--min-p`, `--reasoning`, `--preserve-thinking`.

**How they compare to the AI-level penalties (`ai`):**

- `ai` sends `frequency_penalty` (default `INFER_FREQ_PENALTY=0.10`) and
  `presence_penalty` (default `INFER_PRESENCE_PENALTY=0.05`) in each OpenAI-style
  request. These are per-request and can differ across sessions.
- The server-level settings apply to *all* requests equally and are fixed at server
  start. With neutral defaults (`repeat_penalty=1.0`, `presence=0.0`, `frequency=0.0`)
  the active loop-breaking mechanism is the per-request AI-level penalties.
- A good loop-breaking combo (reported by XDA Developers) is
  `LLAMA_REPEAT_PENALTY=1.05` + `INFER_PRESENCE_PENALTY=0.6`, but these values are
  model/task-specific — always start neutral and increase gradually.
- **Repeat penalty** is a multiplier that grows with each reuse of a token;
  **presence/frequency penalty** is a flat per-token penalty. They are different
  mechanisms and can be combined.

---

## Architecture

```
├── ai.c                  # Agent loop — conversation, LLM calls, native tools
├── ai_mcp.py             # Tool backend — many native tools + MCP client
├── ai_session.c/h        # Session persistence (save/resume conversations)
├── ai_terminal.c/h       # TUI, prompt handling, color output
├── ai_git.c/h            # Git integration (diff, status, log)
├── cJSON.c/h             # Vendored JSON parser
├── jsmn.h                # Vendored lightweight JSON parser
├── remote_harness.c/h    # Remote execution library
├── Makefile              # Build system
├── install.sh            # Install/uninstall script
├── mcp.json              # Local MCP server registry
├── gcal.py               # Google Calendar CLI integration (list/create/update/delete/availability)
├── pubmed_mcp_server.py  # PubMed MCP server
├── deep_research.py      # Deep research tool
├── zulip_mcp_server.py   # Zulip MCP server
├── zulip_ai_bridge.py    # Zulip bot bridge (file parsing, reconnection, ContextWindowManager)
├── .agents/skills/       # Domain skills directory (loaded via load_skill)
│   ├── autonomous_troubleshooting
│   ├── bio_structure_analysis
│   ├── bioinformatics_sequences
│   ├── boltzgen-ops
│   ├── brainstorming
│   ├── cli_git_workflow
│   ├── cli_shell_diagnostics
│   ├── deep_research
│   ├── email_assistant
│   ├── google-calendar
│   ├── karpathy_guidelines
│   ├── mcp_explorer_guide
│   ├── md_prep_openmm
│   ├── planning
│   ├── pubmed_search
│   ├── robinhood_mcp
│   ├── scientific_writing
│   ├── small_model_reasoning
│   ├── small-model-harness
│   ├── smart_web_fetch
│   ├── structure_based_design
│   ├── subagents
│   ├── uniprot_fetcher
│   └── search_history
├── tests/                # C and Python test suite
├── dev/                  # Benchmarks, utilities, test harnesses
└── docs/                 # Architecture docs
```

**Key conventions:**
- C code uses `-O2 -Wall -Wextra -fPIC` and links against `libcurl`, `libssl`, `libcrypto`, `libpthread`, `libm`.
- Python tool backend runs as a subprocess of the C agent loop.
- Session state lives in `~/.cache/ai/sessions/` and `~/.cache/ai/history.jsonl`.
- MCP server config is searched in order: `./mcp.json` → `./mcp_config.json` → `~/.config/ai/mcp.json` → `~/.config/ai/mcp_config.json` → `~/.gemini/config/mcp_config.json` → `~/.lmstudio/mcp.json`.
- Domain skills live in `.agents/skills/<name>/` and are loaded via the `load_skill` tool.

### Zulip Bridge
- `zulip_ai_bridge.py` — Zulip bot bridge that pipes messages to the `ai` CLI.
- **Permission mode:** defaults to `auto` (full autonomy) over Zulip — the bot investigates AND executes. Override with `BRIDGE_AI_MODE=plan|manual` for restricted access. The bridge is already gated to the owner via `ZULIP_USER` / detected owner, so auto is safe here and actually useful (plan mode over Zulip just posts plans and halts — no interactive approve flow). Built-in `/ping` command for liveness checks.
- **File parsing:** automatically downloads uploaded files (PDFs, images, spreadsheets, code, etc.) and extracts their text content before passing to the agent. Supports text, PDF (pdfplumber/pypdfium2), image OCR (tesseract), CSV/Excel (openpyxl), and archives.
- **ContextWindowManager:** manages conversation context to stay within the AI model's context window, truncating messages as needed.
- **Automatic reconnection:** the bridge uses exponential backoff to reconnect on connection errors.
- Cache directory: `~/.cache/zulip_ai_uploads/`
- Privacy: only responds to the owner or explicitly configured `ZULIP_USER`.

### Agent Spawning & Context Pool
- `ai_mcp.py` provides `spawn_agent`, `resume_agent`, and `list_agents` for multi-agent workflows.
- `append_to_context_pool`, `get_context_snippet`, and `search_context` provide a shared context pool that persists across agent sessions.
- `session_report` aggregates results from spawned agents.

### Scheduling & Background Tasks
- `schedule_task`, `set_reminder`, `unschedule_task`, and `list_scheduled_tasks` manage deferred and recurring tasks.
- `start_background_process`, `check_process_status`, and `stop_process` manage detached background processes.
- `delegate_task` spawns N helper agents that run in parallel and return combined results.

---

## Rules

### Code Quality
- Always use the `think` tool before major actions — plan before acting.
- For C/C++: never `free()` stack memory; every `free()` must match a `malloc`/`calloc`/`strdup`. Check string bounds before pointer arithmetic.
- After writing any script or code, run it and verify output. Never assume correctness.
- Update build files (Makefile) and test suites when adding new source files.

### Scientific Tasks
- Write in long, cohesive paragraphs. No markdown tables, emojis, or bullet points for scientific output.
- Cite every source whose content you use (track `[Source: ...]` lines from `fetch_webpage` and `read_file`).
- Use `pubmed_search` or arXiv API for literature queries — never rely on web search snippets for structured data.
- For protein/structure files (.pdb, .cif, .mmcif, etc.), load `bio_structure_analysis` skill first.

### System Engineering
- Never embed raw single-quoted strings directly into shell commands — write to files or use stdin.
- Never run `find /` — constrain searches to specific directories.
- Never describe what the user can do themselves — use tools to do it.
- **NEVER run long-running scripts synchronously (e.g. scrapers, downloads, servers, training loops) via `execute_command`. It locks up the user's terminal UI!** You MUST append `&` to run them in the background, or use `schedule_task` to check back on them.
- If you launch a detached process, you MUST autonomously follow up using `check_process_status` (often via `schedule_task` polling) until it completes!
- Prefer `schedule_task` or `set_reminder` over `sleep` for deferred work.
- Use `parallel_fetch` or `delegate_task` when fetching multiple independent URLs.

### Testing
- Run `make test` to verify C tests and pytest suite pass.
- For new features, add a corresponding test in `tests/` or `dev/test_*.py`.
- `tests/test_offline.py` validates the full tool backend, including `test_ai_mcp_directly_runnable` which checks that `ai_mcp.py` is executable via its shebang.
- If a command fails, read the error, fix the root cause, and retry (at least 3 attempts before giving up).

---

## Workflow

1. **Analyze** — Read the request, form a hypothesis about what's needed.
2. **Plan** — Call `think` to outline steps. Identify which files/tools need to change.
3. **Execute** — Use tools systematically. Verify each step before moving on.
4. **Test** — Build, run tests, check output. If something breaks, debug before proceeding.
5. **Document** — Update AGENTS.md, README.md, or docs/ if the change affects conventions.
6. **Complete** — Call `task_complete` with a clear summary when verified.

### When Modifying C Code
1. Edit the source file.
2. Run `make` to compile.
3. Run `make test` to verify nothing broke.
4. If adding a new `.c` file, update `SRCS` in the Makefile.
5. If adding a new header, ensure it's referenced correctly in dependent files.

### When Modifying Python Code
1. Edit the Python file.
2. Run `pytest` or the relevant test script.
3. If adding a new tool, update `ai_mcp.py` and document it in README.md.
4. Verify end-to-end behavior by running `ai` with a test prompt.

### When Adding New Features
1. Update AGENTS.md with the new capability.
2. Update README.md with usage examples.
3. Add tests in `tests/` or `dev/`.
4. If it affects the agent loop, update the system prompt in `ai.c` or `CLAUDE.md`.

### Skills
- Domain skills live in `.agents/skills/<name>/` and are loaded via `load_skill(name)`.
- Each skill directory contains guidance documents that shape the agent's behavior for specific domains.
- The `load_skill` tool reads the skill's markdown file and injects it into the system context.
- CRITICAL triggers in the system prompt ensure domain-specific skills are loaded before relevant operations.