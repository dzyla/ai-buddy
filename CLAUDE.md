# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`ai` is a minimal, agentic CLI that pipes stdin/prompts into an OpenAI-compatible LLM endpoint and runs an autonomous tool-calling loop in the terminal. Despite the repo name `infer`, the binary and command are `ai`. It is a fork of [chethanreddy1/infer](https://github.com/chethanreddy1/infer).

## Build, install, run

```bash
gcc -o ai ai.c -lcurl          # build (only dependency is libcurl + jsmn.h, vendored)
./setup.sh                     # full install: apt deps, build, copy to /usr/local/bin, set up gemma4 snap + env
sudo cp ai ai_mcp.py /usr/local/bin/   # manual install of both halves
```

There is no test suite, linter, or package manifest. Verify changes by running the binary directly (e.g. `INFER_* env vars set; echo "hi" | ./ai "say hello"`).

Required environment variables (the binary exits early without all three):
- `INFER_BASE_URL` — must end in `/v1/`; the C code appends `chat/completions`
- `INFER_API_KEY`
- `INFER_MODEL`
- `INFER_AUTO_APPROVE=1` and `INFER_DEBUG` are optional (auto-approve commands / dump payloads to stderr).

## Architecture: two cooperating processes

The system is split across two files that talk to each other by **shell-invoking each other as subprocesses** — there is no shared library or IPC beyond `popen`/argv/stdout.

**`ai.c` — the agent loop (C).** Owns the conversation. Responsibilities:
- Builds the `messages` JSON array by hand with `snprintf`/`json_escape` (no JSON library for *generating* requests).
- Parses LLM responses with the vendored **jsmn** tokenizer (`jsmn.h`) — token-index walking, not a DOM. Most response-handling bugs live here.
- Runs the agentic loop: POST via libcurl → parse `tool_calls` → execute each → append `tool` messages → repeat (capped at 20 iterations per turn).
- Handles `execute_command` **natively in C** (with the `/dev/tty` `[Y/n]` confirmation prompt and `2>&1` capture). This is the one tool C runs itself.
- Delegates every **other** tool call to `ai_mcp.py` by shelling out: `python3 ai_mcp.py call-tool <server> <tool> <json-args>`.
- Fetches the tool catalog at startup via `python3 ai_mcp.py list-tools`.
- Renders final assistant text via `python3 ai_mcp.py render-markdown <text>`.
- Assembles the system prompt from: hardcoded `SYSTEM_PROMPT`, live system context (`get_system_context`), persistent memory, and loaded skills.

**`ai_mcp.py` — the tool backend (Python).** Three subcommands matching how `ai.c` calls it: `list-tools`, `call-tool`, `render-markdown`. It:
- Defines all native tools inline as OpenAI function schemas in `list-tools` (`list_directory`, `web_search`, `fetch_webpage`, `save_memory`, `delegate_task`, `read_file`, `write_file`, `edit_file`).
- Implements the actual logic for each (DuckDuckGo Lite scraping, HTML→text, PDF extraction via pdftotext/pypdf/pdfplumber fallback chain, etc.).
- Acts as a generic **MCP client**: any server in `mcp.json` is started over stdio JSON-RPC and its tools are namespaced `<server>__<tool>`. `ai.c` splits on `__` to route calls back.
- `render-markdown` does all terminal ANSI rendering: headers, tables, code syntax highlighting, and LaTeX→unicode math.

### Adding or changing a tool

A native tool requires edits in **both** files, kept in sync:
1. `ai_mcp.py` `list-tools`: append the OpenAI function schema.
2. `ai_mcp.py` `call-tool`: add a routing branch (matched by `tool_name`).
3. Usually nothing in `ai.c` — it routes any non-`execute_command` tool to Python automatically. Only touch `ai.c` for tools needing native handling (like the `[IMAGE_DATA_SUCCESS:...]` sentinel, where Python returns a tag that C intercepts to load base64 image data into the vision context).

### Cross-process contracts (easy to break silently)

- Image flow: `read_file` on an image returns the literal string `[IMAGE_DATA_SUCCESS:<abspath>]`; `ai.c` detects this prefix and injects a base64 `image_url` user message.
- Command results are wrapped by `ai.c` as `[Command Success]` / `[Command Failed with exit status N]` — the system prompt instructs the model to loop on the failure marker.
- `delegate_task` recursively spawns the `ai` binary itself (`/usr/local/bin/ai` or `./ai`) with a 60s timeout.
- Both files resolve `ai_mcp.py` / `ai` from `./` first, then `/usr/local/bin/` — so the local dev copy shadows the installed one when run from the repo.

## Skills

`ai.c` auto-loads `SKILL.md` files into the system prompt from `./.agents/skills/*/` (per-project) and `~/.config/ai/skills/*/` (global). These are plain markdown guidance for the model, not executable. The `.agents/skills/` dir in this repo is the project's own skill set (e.g. `karpathy_guidelines`, `autonomous_troubleshooting`).

## Runtime state locations

- `~/.cache/ai/history.jsonl` — every job logged (prompt, pipe writer, response).
- `~/.config/ai/memory.txt` — persistent memory (capped 4KB), injected into every system prompt.
- `mcp.json` / `~/.config/ai/mcp.json` (and several other paths in `CONFIG_PATHS`) — MCP server registry.
