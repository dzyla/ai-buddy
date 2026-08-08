# AGENTS.md

## Project Overview

`ai` is a minimal, agentic CLI that pipes input to an LLM and executes work in the terminal. It combines a C-based agent loop with a Python tool backend, communicating via subprocess calls — no shared library or IPC required.

**Two-process architecture:**
- **`ai.c`** — Main agent loop. Owns conversation state, calls the LLM, handles `think` / `task_complete` / `execute_command` natively, and delegates all other tool calls to `ai_mcp.py`.
- **`ai_mcp.py`** — Tool backend. Implements 12 native tools and acts as a generic MCP client for any server defined in `mcp.json`.

**Build outputs:**
- `ai` — main CLI binary
- `libremote_harness.so` — remote harness shared library

---

## Building and Running

### Prerequisites

```bash
sudo apt install gcc libcurl4-openssl-dev python3   # Debian/Ubuntu
brew install curl python                             # macOS
```

### Build

```bash
make                    # builds ai and libremote_harness.so
make clean              # remove build artifacts
make test               # run C test + pytest suite
```

### Install

```bash
./install.sh            # installs to ~/.local/bin
./install.sh uninstall  # remove all installed artifacts
```

### Run

```bash
ai "what's the current Bitcoin price?"
ai -i                   # interactive REPL
ps aux | ai "what's eating memory?"
```

### Configure

Set your model endpoint in `~/.local/share/ai/env`:

```bash
export INFER_BASE_URL="http://localhost:8080/v1/"
export INFER_API_KEY="your-key"
export INFER_MODEL="your-model-name"
```

---

## Architecture

```
├── ai.c                  # Agent loop — conversation, LLM calls, native tools
├── ai_mcp.py             # Tool backend — 12 native tools + MCP client
├── ai_session.c/h        # Session persistence (save/resume conversations)
├── ai_terminal.c/h       # TUI, prompt handling, color output
├── ai_git.c/h            # Git integration (diff, status, log)
├── cJSON.c/h             # Vendored JSON parser
├── jsmn.h                # Vendored lightweight JSON parser
├── remote_harness.c/h    # Remote execution library
├── Makefile              # Build system
├── install.sh            # Install/uninstall script
├── mcp.json              # Local MCP server registry
├── gcal.py               # Google Calendar integration
├── pubmed_mcp_server.py  # PubMed MCP server
├── deep_research.py      # Deep research tool
├── tests/                # C and Python test suite
├── dev/                  # Benchmarks, utilities, test harnesses
└── docs/                 # Architecture docs
```

**Key conventions:**
- C code uses `-O2 -Wall -Wextra -fPIC` and links against `libcurl`, `libssl`, `libcrypto`, `libpthread`, `libm`.
- Python tool backend runs as a subprocess of the C agent loop.
- Session state lives in `~/.cache/ai/sessions/` and `~/.cache/ai/history.jsonl`.
- MCP server config is searched in order: `./mcp.json` → `./mcp_config.json` → `~/.config/ai/mcp.json` → `~/.config/ai/mcp_config.json` → `~/.gemini/config/mcp_config.json` → `~/.lmstudio/mcp.json`.

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
