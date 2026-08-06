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
- `INFER_TRIM_THRESHOLD` — triggers message trimming if context exceeds this size (default: 100000).
- `INFER_STUB_THRESHOLD` — stubs subsequent tool results once context size exceeds this (default: 250000).
- `INFER_RESUME=1` — resume the previous conversation (same as `-r`/`--resume`). See "Session persistence" below.
- `INFER_SYSTEM_PROMPT_FILE` — path to a system-prompt override file. Defaults to `~/.config/ai/system_prompt.md` if that exists; otherwise the compiled-in `SYSTEM_PROMPT` is used. Lets you tune agent behavior without recompiling.
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
- `compact_session`: sends the full conversation to the LLM for summarisation, prints progress dots via the libcurl progress callback while waiting, and only replaces the conversation history if the LLM returns a usable summary (≥20 chars).
- **Session persistence:** at the end of every run the full `messages_json` is written to `~/.cache/ai/sessions/last.json`. `-r`/`--resume` (or `INFER_RESUME=1`) reloads it: `ai.c` shells to `python3 ai_mcp.py session-transcript <file>`, which converts the raw array into a clean user/assistant transcript (JSONL, one message per line) — old system message, tool churn, and internal nudges are dropped, and `task_complete` summaries become assistant text. Each line is spliced in via `append_message` between the fresh system message and the new user turn, avoiding dangling-tool_call API errors.
- **System prompt** is assembled from `load_system_prompt()` (file override → compiled-in `SYSTEM_PROMPT` fallback) plus live context, memory, and skills.

**`ai_mcp.py` — the tool backend (Python).** Subcommands matching how `ai.c` calls it: `list-tools`, `call-tool`, `render-markdown`, `trim-messages`, `session-transcript`, `run-scheduler`. It:
- Defines **12 native tools** as OpenAI function schemas in `list-tools` (in schema order): `think`, `execute_command`, `web_search`, `fetch_webpage`, `read_file`, `write_file`, `edit_file`, `list_directory`, `save_memory`, `delegate_task`, `computer_control`, `task_complete`.
- Implements the actual logic for each (DuckDuckGo Lite scraping, HTML→text, PDF extraction via pdftotext/pypdf/pdfplumber fallback chain, binary-file heuristic rejection, etc.). `edit_file`: search-and-replace on an existing file. Falls back to a trailing-whitespace-tolerant fuzzy match if the exact string is not found.
- **Web fetching:** the public `fetch_webpage` tool (and `parallel_fetch`, and the search auto-fetch of the top result) route through `fetch_smart` — a cascade of curl_cffi TLS impersonation → Playwright+stealth → plain urllib (`fetch_webpage_basic`). `fetch_webpage_basic` is the plain rung and the only thing `fetch_smart` falls back to (never the public `fetch_webpage`, which would recurse). `INFER_FETCH_BASIC=1` forces the plain path.
- Acts as a generic **MCP client**: any server in `mcp.json` is started over stdio JSON-RPC and its tools are namespaced `<server>__<tool>`. `ai.c` splits on `__` to route calls back.
- **Google Calendar** (`gcal.py`, lazily imported in `call-tool`): `gcal_list_events`, `gcal_check_availability`, `gcal_quick_add` (natural-language event via Google's parser), `gcal_create_event`, `gcal_update_event` (patch semantics), `gcal_delete_event`. Timed events attach an explicit IANA `timeZone` (arg or `local_tz_name()`) so naive datetimes are unambiguous; bare `YYYY-MM-DD` values become all-day events. OAuth token at `~/.config/ai/gcal_token.json`; run `python3 gcal.py auth` once.
- **Zulip** is an MCP server (`zulip_mcp_server.py`) exposing `zulip_send_message`, `zulip_get_messages`, `zulip_add_reaction`, `zulip_edit_message`. `zulip_ai_bridge.py` is the inbound side: listens on Zulip and shells out to the `ai` binary per message (owner-restricted, threaded); it exports `AI_REMINDER_ZULIP_TO=<sender>` so reminders default back to the requester.
- **Reminders:** `set_reminder(message, when|delay_seconds, zulip_to|zulip_stream)` schedules a `kind:"reminder"` one-shot task (via `schedule_task`'s `extra` dict). `run_scheduler_loop` delivers reminders **deterministically** through `_deliver_reminder` (direct Zulip send, falls back to `notify-send`) — it does NOT spawn an LLM agent for these, unlike ordinary scheduled tasks. The model converts natural phrases ("tomorrow 9am") to an ISO `when` itself.
- Calendar/Zulip logic is unit-tested offline in `tests/test_integrations.py` by mocking the Google service object / Zulip client and asserting on the request bodies built.
- `render-markdown` does all terminal ANSI rendering: headers, ordered/unordered lists, fenced code blocks with per-language syntax highlighting, bordered tables with column alignment, inline bold/italic/code, and LaTeX→Unicode math symbols with super/subscript conversion.

### Adding or changing a tool

A native tool requires edits in **both** files, kept in sync:
1. `ai_mcp.py` `list-tools`: append the OpenAI function schema.
2. `ai_mcp.py` `call-tool`: add a routing branch (matched by `tool_name`).
3. Usually nothing in `ai.c` — it routes any non-native tool to Python automatically. Only touch `ai.c` for tools needing native handling (e.g. `think`, `task_complete`, `execute_command`, or the `[IMAGE_DATA_SUCCESS:...]` sentinel intercept).

`think` and `task_complete` have Python fallback branches in `call-tool` (safety net if C routing misses them), but their real handling is in the C loop.

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
- `~/.config/ai/memory.txt` — persistent memory (capped 4KB), injected into every system prompt.
- `mcp.json` / `~/.config/ai/mcp.json` (and several other paths checked in `CONFIG_PATHS` order) — MCP server registry. Full search order: `./mcp.json`, `./mcp_config.json`, `~/.config/ai/mcp.json`, `~/.config/ai/mcp_config.json`, `~/.gemini/config/mcp_config.json`, `~/.lmstudio/mcp.json`.
