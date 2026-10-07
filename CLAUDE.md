# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`ai` is a minimal, agentic CLI that pipes stdin/prompts into an OpenAI-compatible LLM endpoint and runs an autonomous tool-calling loop in the terminal. Despite the repo name `infer`, the binary and command are `ai`. It is a fork of [chethanreddy1/infer](https://github.com/chethanreddy1/infer).

## Build, install, run

```bash
gcc -o ai ai.c cJSON.c -lcurl  # build only (dependencies: libcurl + jsmn.h + cJSON.h/cJSON.c, vendored)
./install.sh                   # build + install to ~/.local/bin + sync skills (no sudo)
./install.sh llama             # also set up local llama.cpp server + model download
./install.sh snap              # also detect and configure a running AI snap (qwen3-6/gemma4)
```

Backend and model deployment are managed by `ai-backend` (single tool, installed to `~/.local/bin/ai-backend`, aliased as `ai-model` and `ai-use`). Active config lives in `~/.local/share/ai/env`.
```bash
ai-backend status                     # show active backend, model, and server status
ai-backend use <hf_uri|path|preset>   # download (if HF URI) and switch active model
                                      # e.g.: ai-backend use hf://AtomicChat/Ling-3.0-flash-GGUF/AD-IQ3_M/Ling-3.0-flash-AD-IQ3_M-00001-of-00002.gguf
ai-backend list                       # list downloaded GGUF models
ai-backend auto                       # switch to whatever is currently running
ai-backend ctx <size|auto>            # set or clear explicit context window
ai-backend gpu-layers <n|auto>        # set GPU offload layers
ai-backend serve                      # run llama-server with auto context sizing (used by systemd)
```

Tests (pytest):
- `test_ai.py` — integration tests; several need a **live backend** reachable at the active `INFER_BASE_URL`.
- `tests/test_offline.py` — **offline** suite (no backend). Drives the binary against a mock OpenAI server (`tests/mock_llm_server.py`, supports both `stream:false` and SSE) and asserts on the exact request the binary sent. Also unit-tests `ai_mcp.py` pure functions (fetch routing, `session-transcript`). Run: `python3 -m pytest tests/test_offline.py -v`.
- The mock server keys off `MOCK_TASK_COMPLETE` (return a `task_complete` tool call so the agent loop ends in one turn) and `MOCK_CAPTURE` (append each received request as one JSON line). Integration tests use an isolated `HOME` so `load_env_file()` can't override the mock `INFER_*` vars.

- `tests/test_config_templates.py` — `config/*.example*` templates parse and hold no personal data; `load_config` order and `"disabled"`; `install.sh` seeding. `tests/test_evals_runner.py` — the eval set (below) runs under `--mock`.

Agent eval set (`evals/`): `evals/tasks/NN_<id>.json` are realistic CLI-assistant tasks (file edits, shell, multi-step tool use, recovering from a failing command, refusing a destructive script, ...). Each has `prompt`, optional `files`/`env`, `checks` (`answer_contains`, `file_exists`, `file_contains`, `tool_used`, `tool_result_contains`, `max_steps`; any check takes `"not": true`) and a `mock` script (`steps` of `{tool, args}`, a list = parallel calls; `final` = the task_complete summary). `python3 evals/run_evals.py --mock [--json out.json]` runs every task offline against `tests/mock_llm_server.py` (tools really run in a temp workdir with an isolated HOME; exit 1 on any failure). Against a model: `python3 evals/run_evals.py [--engine c|py] --base-url URL --model M --json out.json` (defaults to `INFER_*` / `~/.local/share/ai/env`; `--min-pass` sets the exit threshold, default 0; `--engine py` exits 2 until the `agent/` Python engine exists). In `--mock` mode answers and tool calls are scripted, so a task also fails when a scripted tool call returns an error unless the step has `"expect_error": true` (which must then really error); every task needs an observable check (`file_*`, possibly `"in": "home"`, or `tool_result_contains`). `tool_used` alone does not count.

CI (`.github/workflows/ci.yml`, ubuntu-latest): `test` (apt `libcurl4-openssl-dev libssl-dev`, pip `pytest requests google-api-python-client google-auth google-auth-oauthlib`, `make`, `pytest tests/ -q`), `privacy` (`privacy_check.py --all` with `AI_PRIVATE_PATTERNS=/dev/null`, i.e. generic patterns only), `evals-mock`. The suite must not depend on this machine: no `zulip`, `rich`, `node`, local model, real `~/.config/ai` or `~/.local/bin/ai` on the runner (tests needing optional tools skip with a reason).

No linter or package manifest. For quick manual checks, run the binary directly (e.g. `INFER_* env vars set; echo "hi" | ./ai "say hello"`).

Required environment variables (the binary exits early without all three):
- `INFER_BASE_URL` — must end in `/v1/`; the C code appends `chat/completions`
- `INFER_API_KEY`
- `INFER_MODEL`
Optional environment variables:
- `INFER_AUTO_APPROVE=1` — auto-approve all `execute_command` calls without prompting.
- `INFER_DEBUG` — dump raw request/response payloads to stderr on every loop iteration.
- `INFER_QUIET=1` — suppress `[thinking]` output from the `think` tool (same as `-q`).
- `INFER_TOOL_CHOICE` — `required` (default) or `auto`; controls the `tool_choice` field sent in every request.
- `INFER_MAX_TOOL_OUTPUT` — caps individual tool output (default: 65536).
- `INFER_CONTEXT_WINDOW` — model context in tokens (else `LLAMA_CTX_SIZE`, else detected from `/props` / `/v1/models` `n_ctx`). When known, `apply_context_budget` derives tighter trim/stub/tool-output limits (~3 chars/token, minus tool catalog and reply reserve); explicitly set thresholds are never overridden.
- `INFER_TOOL_PROFILE=core` — send only the core tool subset (~10 KB instead of ~45 KB). Set automatically for windows ≤32k tokens and `--short-ctx`.
- `INFER_TRIM_THRESHOLD` — triggers message trimming if context exceeds this size (default: 100000). Trimming only runs above it, cuts to ~60%, always keeps the system prompt and first/latest real user turns, keeps assistant `tool_calls` with their tool replies, and shortens old tool output before dropping messages.
- `INFER_STUB_THRESHOLD` — stubs subsequent tool results once context size exceeds this (default: 250000).
- `INFER_RESUME=1` — resume the previous conversation (same as `-r`/`--resume`). See "Session persistence" below.
- `INFER_SYSTEM_PROMPT_FILE` — path to a system-prompt override file. Defaults to `~/.config/ai/system_prompt.md` if that exists; otherwise the compiled-in `SYSTEM_PROMPT` is used. Lets you tune agent behavior without recompiling.
- `INFER_AUTO_COMMIT=1` — opt into git auto-commit after successful commands (same as `--git`; `--no-git` wins). Off by default; it only stages files git already tracks (`git add -u`) and restores the index if the commit is blocked.
- `INFER_FETCH_BASIC=1` — force `fetch_webpage` to use the plain urllib+trafilatura path instead of the default robust `fetch_smart` cascade (curl_cffi TLS impersonation → Playwright+stealth → urllib).

## Architecture: two cooperating processes

The system is split across two files that talk to each other by **shell-invoking each other as subprocesses** — there is no shared library or IPC beyond `popen`/argv/stdout.

**`ai.c` — the agent loop (C).** Owns the conversation. Responsibilities:
- Builds the `messages` JSON array by hand with `snprintf`/`json_escape` (no JSON library for *generating* requests).
- Parses LLM responses with the vendored **jsmn** tokenizer (`jsmn.h`) — token-index walking, not a DOM. Most response-handling bugs live here.
- Runs the agentic loop: POST via libcurl → parse `tool_calls` → execute each → append `tool` messages → repeat (capped at **30** iterations per turn). Sends `tool_choice: required` by default (overridable via `INFER_TOOL_CHOICE=auto` for servers that do not support it).
- Handles `think`, `task_complete`, and `execute_command` **natively in C**:
  - `think`: prints `[thinking] …` to stdout (suppressed by quiet mode); returns `{"ok":true}`.
  - `task_complete`: renders the `summary` argument via `render-markdown`, logs the job, and exits the loop.
  - `execute_command`: opens `/dev/tty` for a `[Y/n]` confirmation prompt (bypassed when `g_auto_approve` is set), runs the command with `2>&1` capture, and wraps the result in `[Command Success]` / `[Command Failed with exit status N]`.
- Delegates every **other** tool call to `ai_mcp.py` by shelling out: `python3 ai_mcp.py call-tool <server> <tool> <json-args>`.
- Fetches the tool catalog at startup via `python3 ai_mcp.py list-tools`.
- Renders final assistant text via `python3 ai_mcp.py render-markdown <text>`.
- Assembles the system prompt from: hardcoded `SYSTEM_PROMPT`, live system context (`get_system_context`), persistent memory, and loaded skills.
- Caps each tool result to `INFER_MAX_TOOL_OUTPUT` (default: 64 KB) and stubs any result once `messages_json` exceeds `INFER_STUB_THRESHOLD` (default: 250 KB) to prevent context blowup. Trims messages when context exceeds `INFER_TRIM_THRESHOLD` (default: 100 KB).
- Detects pipe-writer via `/proc` inspection and includes the originating command name in the user message.
- Handles image file arguments: detects `.png`/`.jpg`/`.jpeg`/`.webp` paths, base64-encodes them, and injects a `image_url` content block into the first user message.
- Intercepts `[IMAGE_DATA_SUCCESS:<path>]` returned by `read_file` and similarly injects the image into conversation context.
- Detects `finish_reason: "length"` (model hit token limit) and injects a recovery nudge instead of rendering truncated output.
- In interactive mode handles `:compact`, `:clear`, `:status`, `:memory`, `:auto`, and `:help` colon-commands, and Shift-Tab (`ESC [ Z`) to toggle `g_auto_approve` both at the prompt and during agent execution (raw mode via the libcurl progress callback). The `ai>` prompt changes to `ai(auto)>` while auto-approve is active.
- The interactive prompt uses a self-contained line editor (`read_line_interactive` / `lineed_*` in `ai.c`): arrow keys navigate history (up/down) and move the cursor (left/right), with Ctrl+A/E (line start/end), Ctrl+K/U (kill to end/start), Ctrl+W (kill word), Ctrl+L (clear screen), Home/End, Delete. History is persisted to `~/.cache/ai/input_history` across sessions.
- `:compact` trims the conversation towards a target size with the same protocol-safe rules as automatic trimming (it does not ask the LLM for a summary).
- **Session persistence:** at the end of every run the full `messages_json` is written to `~/.cache/ai/sessions/last.json`. `-r`/`--resume` (or `INFER_RESUME=1`) reloads it: `ai.c` shells to `python3 ai_mcp.py session-transcript <file>`, which converts the raw array into a clean user/assistant transcript (JSONL, one message per line) — old system message, tool churn, and internal nudges are dropped, and `task_complete` summaries become assistant text. Each line is spliced in via `append_message` between the fresh system message and the new user turn, avoiding dangling-tool_call API errors.
- **System prompt** is assembled from `load_system_prompt()` (file override → compiled-in `SYSTEM_PROMPT` fallback) plus live context, memory, and skills.

**`ai_mcp.py` / `aitools/` — the tool backend (Python).** `ai_mcp.py` is a ~30-line shim: it puts the directory of its realpath (a checkout) and then `~/.local/share/ai/lib` (where `install.sh` copies the package) on `sys.path`, runs `aitools.cli.main()` as a script, and makes `import ai_mcp` return `aitools.compat` — a flat namespace re-exporting every name of the old single-file module; rebinding a name on it (`monkeypatch.setattr(ai_mcp, "MEMORY_PATH", …)`) is forwarded to every submodule holding that object (same trick as `trader/legacy.py`). Subcommands matching how `ai.c` calls it: `list-tools`, `call-tool`, `render-markdown`, `trim-messages`, `session-transcript`, `run-scheduler`. Module map:
  - `cli.py` — subcommand `main(argv) -> int`; `call-tool` = parse/repair JSON → `registry.prepare` → metrics tee → `registry.invoke`.
  - `registry.py` — `TOOL_SCHEMAS` (native catalog, list-tools order, from `schemas_part1/2.py`), `HANDLERS` (tool name → `h_<tool>(args) -> str`; a call matches on tool name **or** server name, first entry wins, else the MCP server), `prepare` (alias normalisation, required/type checks), `invoke`, `dispatch(server, tool, args) -> str` (exactly what `call-tool` prints), `repair_json`, `CORE_TOOL_PROFILE`.
  - `handlers.py` (tool handlers), `calendar_tools.py` (gcal_* handlers), `web.py` (search + fetch cascade), `files.py` (read/write/edit/patch/list/search_files, PDF), `shell.py` (execute_command, playgrounds, background processes), `state.py` (the SQLite state store + legacy migration, see "Runtime state"), `memory.py` (save_memory, FTS memories, vault, context pool), `history.py` (history index cache, Zulip chat-log tools), `sessions.py` (trim-messages, session-transcript, sessions index), `scheduler.py` (schedule_task, reminders, run-scheduler loop), `selfimprove.py` (skills, failure ledger, lessons, recap), `metrics.py` (log_metric, `classify_tool_output`), `mcp_client.py` (load_config, stdio JSON-RPC client), `render.py`, `browser.py` (browser daemon, computer_control), `research.py` (arXiv/PubMed), `misc.py`.
  - Golden guards: `tests/test_aitools_layout.py` (list-tools bytes, call-tool outputs, shim < 80 lines, modules ≤ 1500 lines, install layout).

It:
- Defines the native tools as OpenAI function schemas (`registry.TOOL_SCHEMAS`; `think` first, `task_complete` last), plus every MCP server's tools.
- Implements the actual logic for each (DuckDuckGo Lite scraping, HTML→text, PDF extraction via pdftotext/pypdf/pdfplumber fallback chain, binary-file heuristic rejection, etc.). `edit_file`: search-and-replace on an existing file. Falls back to a trailing-whitespace-tolerant fuzzy match if the exact string is not found.
- **Web fetching:** the public `fetch_webpage` tool (and `parallel_fetch`, and the search auto-fetch of the top result) route through `fetch_smart` — a cascade of curl_cffi TLS impersonation → Playwright+stealth → plain urllib (`fetch_webpage_basic`). `fetch_webpage_basic` is the plain rung and the only thing `fetch_smart` falls back to (never the public `fetch_webpage`, which would recurse). `INFER_FETCH_BASIC=1` forces the plain path.
- Acts as a generic **MCP client**: any server in `mcp.json` is started over stdio JSON-RPC and its tools are namespaced `<server>__<tool>`. `ai.c` splits on `__` to route calls back.
- **Google Calendar** (`gcal.py`, lazily imported by the handlers in `aitools/calendar_tools.py`): `gcal_list_events`, `gcal_check_availability`, `gcal_quick_add` (natural-language event via Google's parser), `gcal_create_event`, `gcal_update_event` (patch semantics), `gcal_delete_event`. Timed events attach an explicit IANA `timeZone` (arg or `local_tz_name()`) so naive datetimes are unambiguous; bare `YYYY-MM-DD` values become all-day events. OAuth token at `~/.config/ai/gcal_token.json`; run `python3 gcal.py auth` once.
- **Zulip** is an MCP server (`zulip_mcp_server.py`) exposing `zulip_send_message`, `zulip_get_messages`, `zulip_add_reaction`, `zulip_edit_message`. `zulip_ai_bridge.py` (a <60-line shim over the `zulip_bridge/` package, same realpath-then-`~/.local/share/ai/lib` lookup and `import zulip_ai_bridge` -> `zulip_bridge.compat` forwarding as `ai_mcp.py`) is the inbound side: listens on Zulip and shells out to the `ai` binary per message (owner-restricted, threaded); it exports `AI_REMINDER_ZULIP_TO=<sender>` so reminders default back to the requester.
  - `zulip_bridge/` module map: `config` (env file, zuliprc, `resolve_ai_bin`), `owner` (owner detection), `files` (`FileParser`), `text` (`clean_response`), `runner` (`run_ai_subprocess`, `extract_session_fallback`), `memory` (`ZulipMemoryManager`), `context` (`ContextWindowManager`), `handler` (`MessageHandlerMixin`: `_process_message`, `handle_message`), `bridge` (`ZulipAiBridge`), `cli` (`run_check`, `main`). Guards: `tests/test_zulip_layout.py` (golden `--check` output, shim < 60 lines, modules <= 800 lines, install layout).
- **Reminders:** `set_reminder(message, when|delay_seconds, zulip_to|zulip_stream)` schedules a `kind:"reminder"` one-shot task (via `schedule_task`'s `extra` dict). `run_scheduler_loop` delivers reminders **deterministically** through `_deliver_reminder` (direct Zulip send, falls back to `notify-send`) — it does NOT spawn an LLM agent for these, unlike ordinary scheduled tasks. The model converts natural phrases ("tomorrow 9am") to an ISO `when` itself.
- Calendar/Zulip logic is unit-tested offline in `tests/test_integrations.py` by mocking the Google service object / Zulip client and asserting on the request bodies built.
- `render-markdown` does all terminal ANSI rendering: headers, ordered/unordered lists, fenced code blocks with per-language syntax highlighting, bordered tables with column alignment, inline bold/italic/code, and LaTeX→Unicode math symbols with super/subscript conversion.

### Adding or changing a tool

A native tool needs, kept in sync:
1. Its OpenAI function schema in `aitools/schemas_part2.py`, before the final `task_complete` entry (list position = `list-tools` order; then re-capture `tests/golden/list_tools*.json` with `AITOOLS_REGEN_GOLDEN=1`).
2. A handler `h_<tool>(arguments) -> str` (in `aitools/handlers.py` or the domain module) registered in `registry.HANDLERS`; add required args to `TOOL_REQUIRED_ARGS` if any.
3. Usually nothing in `ai.c` — it routes any non-native tool to Python automatically. Only touch `ai.c` for tools needing native handling (e.g. `think`, `task_complete`, `execute_command`, or the `[IMAGE_DATA_SUCCESS:...]` sentinel intercept).

`think` and `task_complete` have Python fallback handlers in `registry.HANDLERS` (safety net if C routing misses them), but their real handling is in the C loop.

### Cross-process contracts (easy to break silently)

- Image flow: `read_file` on an image returns the literal string `[IMAGE_DATA_SUCCESS:<abspath>]`; `ai.c` detects this prefix and injects a base64 `image_url` user message.
- Command results are wrapped by `ai.c` as `[Command Success]` / `[Command Failed with exit status N]` — the system prompt instructs the model to loop on the failure marker.
- `delegate_task` recursively spawns the `ai` binary itself (`/usr/local/bin/ai` or `./ai`) with a 60s timeout.
- Both files resolve `ai_mcp.py` / `ai` from `./` first, then `/usr/local/bin/` — so the local dev copy shadows the installed one when run from the repo.

## Skills

`ai.c` auto-loads `SKILL.md` files into the system prompt from `./.agents/skills/*/` (per-project) and `~/.config/ai/skills/*/` (global). These are plain markdown guidance for the model, not executable. The `.agents/skills/` dir in this repo is the project's own skill set (e.g. `karpathy_guidelines`, `autonomous_troubleshooting`).

`compile_and_install.sh` copies the entire `.agents/skills/` tree to `~/.config/ai/skills/` on every install, so skills are always found regardless of the working directory when `ai` is invoked. Re-run the script (or manually `cp -r .agents/skills/. ~/.config/ai/skills/`) after adding or editing a skill.

## Runtime state locations

- `~/.cache/ai/history.jsonl` — every job logged (prompt, pipe writer, response).
- `~/.local/share/ai/state.db` (`$AI_STATE_DB` overrides) — the single SQLite state store (`aitools/state.py`; WAL, `busy_timeout=5000`, writes in `BEGIN IMMEDIATE` with lock retries, so the Zulip bridge, scheduler daemons and `ai` can write concurrently). Tables: `meta` (`schema_version=1`, `migrated_v1`, `migration_report`, `chat_audit_pruned_at`), `memory` (persistent memory, injected into every system prompt via `ai_mcp.py state-export memory`, read once per `ai` process; `save_memory` dedupes case-insensitively and evicts the oldest whole lines past 4000 chars; `memory_render` caps at 4096 bytes keeping the newest), `lessons` (PITFALL/FIX/MASTER/NOTE; `UNIQUE(kind, tool, signature)`, `status` default `approved`, `hits`, `expires`; rendered as the old lessons.md markdown blocks), `ledger` (failure/recovery rows, args stored only as a hash, capped at 5000 rows), `sessions` (index of saved conversations, updated as the history indexer sees session files), `scheduled_tasks` (task spec JSON + `next_run`; the row IS the task, deleting it cancels; only `<id>.pid` lock files stay in `~/.config/ai/scheduled_tasks/`; `ai.c` shows the countdown via `state-export tasks`), `chat_audit` (one row per Zulip exchange, rows older than 90 days deleted on open at most once a day), plus FTS5 `memories` (remember/recall) and `vault_fts` (vault note index). Guard/timeout errors are not learned from. CLI: `ai_mcp.py state-export memory|lessons|tasks`, `state-migrate [HOME]`, `state-stats`.
- **Legacy migration:** the first `state.connect()` of a process on the default path (no `$AI_STATE_DB`) imports `~/.config/ai/memory.txt` (latin-1 bytes tolerated), `memory.db` (its FTS tables), `self_improve/lessons.md` (hand edits tolerated) and `ledger.jsonl` (corrupt lines skipped), `scheduled_tasks/*.json`, and `~/.local/share/ai/zulip_chats.jsonl` + `~/.cache/ai/zulip_chats.jsonl`, in one transaction that sets `meta.migrated_v1`; then each original is renamed `*.migrated.bak` (nothing is deleted). Live old-code scheduler daemons are replaced. Errors are counted in `meta.migration_report` and never break startup. With `$AI_STATE_DB` set there is no lazy migration; `tests/conftest.py` sets it per test, so tests never touch the real HOME.
- `~/.local/share/ai/history_index.db` — NOT state: a derived, rebuildable FTS cache over `history.jsonl` and the session files (`rebuild_history_index`).
- `~/.config/ai/trader.json` — Robinhood account mapping (`{"accounts": {"agentic": ..., "primary": ..., "roth": ...}}`); env `RH_*_ACCOUNT` overrides. Trading refuses if the agentic account can't be resolved.
- `~/.config/ai/mcp.json` — MCP server registry (`aitools/mcp_client.py: load_config`). Search order, first non-empty file wins: `$INFER_MCP_CONFIG`, `~/.config/ai/mcp.json`, `~/.config/ai/mcp_config.json`, `./mcp.json`, `./mcp_config.json` (cwd; dev only, gitignored), `~/.gemini/config/mcp_config.json`, `~/.lmstudio/mcp.json`. Entries with `"disabled": true` are skipped.

### Config templates (`config/`)

Personal config is not tracked. `config/*.example*` are the templates: `mcp.example.json` (robinhood, zulip, pubmed entries, all `"disabled": true`, no secrets; pubmed reads `PUBMED_SEARCH_URL`/`PUBMED_API_KEY` from the env), `trader.example.json` (`<..._ACCOUNT>` placeholders, which `RobinhoodExecutor.configured_account` treats as unset, also in `RH_*_ACCOUNT`), `private_patterns.example.txt` (comments only), `zuliprc.example` (format reference only; not seeded; a key containing `REPLACE_WITH` is reported as "not configured" by `zulip_ai_bridge.py --check` and `zulip_mcp_server.py`). `install.sh` copies each of the others to `~/.config/ai/<name without .example>` (mode 600) **only when the target is missing**; it never overwrites. When it seeds `~/.config/ai/mcp.json` it prints a notice: **MCP servers ship disabled (robinhood included); enable one by setting `"disabled": false` in `~/.config/ai/mcp.json`**. A seeded `~/.config/ai/mcp.json` takes precedence over `./mcp.json` and the legacy paths, so a server configured only in a cwd `mcp.json` stops loading once it exists. Templates use placeholders / example.com only; `tests/test_config_templates.py` enforces that.

## Privacy (the GitHub repo is public)

- Never commit personal data: lab data files, account numbers, private hosts/IPs, real e-mails, `/home/<user>` paths. Put such values in `~/.local/share/ai/env`, `~/.config/ai/*.json`, or a gitignored file.
- `.githooks/pre-commit` runs `scripts/privacy_check.py` on every commit (enabled by `install.sh` via `git config core.hooksPath .githooks`). It checks generic leak patterns plus a personal denylist in `~/.config/ai/private_patterns.txt` (outside the repo, one regex per line). `python3 scripts/privacy_check.py --all` audits every tracked file. Mark an intentional line with `privacy-ok`.
- Machine/lab-specific skills stay local and gitignored (e.g. `.agents/skills/boltzgen-ops/`, `.agents/skills/*_private/`); `install.sh` still syncs them to `~/.config/ai/skills/`.
- Tests run the binary with `cwd` set to a temp dir or with auto-commit off — never let the binary commit inside this repo.
