# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`ai` is a minimal, agentic CLI that pipes stdin/prompts into an OpenAI-compatible LLM endpoint and runs an autonomous tool-calling loop in the terminal. Despite the repo name `infer`, the binary and command are `ai`. It is a fork of [chethanreddy1/infer](https://github.com/chethanreddy1/infer).

## Build, install, run

```bash
gcc -o ai ai.c cJSON.c -lcurl  # build (only dependencies are libcurl + jsmn.h + cJSON.h/cJSON.c, vendored)
./compile_and_install.sh       # build, copy to /usr/bin, and sync .agents/skills/ → ~/.config/ai/skills/
./setup.sh                     # full install: apt deps, build, copy to /usr/local/bin, set up llama.cpp + env
sudo cp ai ai_mcp.py /usr/local/bin/   # manual install of both halves (skills not synced)
```

There is no test suite, linter, or package manifest. Verify changes by running the binary directly (e.g. `INFER_* env vars set; echo "hi" | ./ai "say hello"`).

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
- In interactive mode handles `:compact`, `:clear`, `:status`, `:memory`, `:auto`, and `:help` colon-commands, and Shift-Tab (`ESC [ Z`) to toggle `g_auto_approve` both at the prompt (cooked mode) and during agent execution (raw mode via the libcurl progress callback). The `ai>` prompt changes to `ai(auto)>` while auto-approve is active.
- `compact_session`: sends the full conversation to the LLM for summarisation, prints progress dots via the libcurl progress callback while waiting, and only replaces the conversation history if the LLM returns a usable summary (≥20 chars).

**`ai_mcp.py` — the tool backend (Python).** Three subcommands matching how `ai.c` calls it: `list-tools`, `call-tool`, `render-markdown`. It:
- Defines **12 native tools** as OpenAI function schemas in `list-tools` (in schema order): `think`, `execute_command`, `web_search`, `fetch_webpage`, `read_file`, `write_file`, `edit_file`, `list_directory`, `save_memory`, `delegate_task`, `computer_control`, `task_complete`.
- Implements the actual logic for each (DuckDuckGo Lite scraping, HTML→text, PDF extraction via pdftotext/pypdf/pdfplumber fallback chain, binary-file heuristic rejection, etc.). `edit_file`: search-and-replace on an existing file. Falls back to a trailing-whitespace-tolerant fuzzy match if the exact string is not found.
- Acts as a generic **MCP client**: any server in `mcp.json` is started over stdio JSON-RPC and its tools are namespaced `<server>__<tool>`. `ai.c` splits on `__` to route calls back.
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
