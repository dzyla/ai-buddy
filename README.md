# ai 🤖

A minimal, agentic CLI tool for piping anything into an LLM and executing terminal work, written in pure C and Python with zero external library dependencies.

```bash
ps aux | ai "what's eating memory"
ai "tell me what is taking most space"
ai "what are the coldest and hottest places in US right now?"
```

It reads from stdin, sends to an LLM, runs tools dynamically (shell commands, web searches, webpage crawling, file reads/writes, delegated sub-agents), maintains persistent memory, and outputs beautifully formatted markdown directly in your terminal.

---

## Features

- **Interactive Chat / REPL**: Run `ai` with no arguments, or with `-i` / `--interactive`, to start a multi-turn conversation shell. Chat history is preserved across turns.
- **Agentic Tool Loop**: The agent loops up to 30 times per turn, calling tools, reading results, and calling more tools until it calls `task_complete`. Defaults to `tool_choice: required` to force a tool call every iteration. Set `INFER_TOOL_CHOICE=auto` for servers that do not support `required`.
- **Transparent Reasoning**: The `think` tool lets the model write a step-by-step plan before acting. Reasoning is shown to you in real time (suppress with `-q` / `--quiet`).
- **Shell Command Execution**: The model can run any shell command. You get a `[Y/n]` confirmation prompt before each one, with stderr and stdout both captured and fed back into context.
- **Safety Confirmation / Auto-Approve**: Command execution prompts protect you by default. Use `-y` / `--yes` or `INFER_AUTO_APPROVE=1` to bypass for scripted or trusted sessions.
- **Web Search**: Searches DuckDuckGo Lite without any API key. Results include title, URL, and snippet.
- **Webpage Fetching**: Downloads and cleans HTML to readable text (scripts/styles stripped, HTML entities decoded, truncated at 10 KB).
- **File Reading**: Reads text files (truncated at 12 KB), PDFs (via `pdftotext` → `pypdf` → `pdfplumber` fallback chain), and image files (PNG, JPG, JPEG, WEBP — injected directly into vision context). Binary files are rejected with a clear error.
- **File Writing & Editing**: Creates new files (with parent directories) via `write_file`. Makes precise search-and-replace edits to existing files via `edit_file` — the search block must match exactly.
- **Directory Listing**: Safe directory exploration via `list_directory`, showing file sizes and `[DIR]` markers.
- **Persistent Memory**: The model can call `save_memory` to store facts, preferences, or context to `~/.config/ai/memory.txt` (capped at 4 KB). Memory is injected into every system prompt automatically.
- **Recursive Agent Delegation**: `delegate_task` spawns an independent child `ai` process with full tool access and a 60-second timeout. Use it for parallel, independent sub-tasks.
- **Multimodal Image Input**: Pass an image path (`.png`, `.jpg`, `.jpeg`, `.webp`) as an argument — it is base64-encoded and sent as a vision `image_url` alongside your text prompt.
- **MCP Server Integration**: Any server listed in `mcp.json` is started as a subprocess over stdio JSON-RPC. Its tools are namespaced as `<server>__<tool>` and automatically added to the model's tool catalog.
- **System Context Injection**: OS, current working directory, user, shell, and local time are injected into every system prompt for accurate local context.
- **Pipe-Writer Detection**: When you pipe a command's output into `ai`, the originating command is identified via `/proc` and included in the user message for extra context.
- **Auto Skill Loading**: `SKILL.md` files are loaded from `./.agents/skills/*/` (project-level) and `~/.config/ai/skills/*/` (global) and injected into the system prompt.
- **Job History Logging**: Every job (prompt, pipe writer, response) is appended to `~/.cache/ai/history.jsonl`.
- **Rich Terminal Rendering**: Markdown is rendered with ANSI escape codes — colored headers, bullet/numbered lists, fenced code blocks with syntax highlighting (Python, C, Bash, Rust, JS, …), bordered tables with column alignment, inline bold/italic/code, and LaTeX → Unicode math symbols with super/subscript conversion.
- **Context Size Guards**: Individual tool results are capped at 64 KB (`INFER_MAX_TOOL_OUTPUT`). Once the conversation exceeds 250 KB, new tool results are stubbed to preserve model focus (`INFER_STUB_THRESHOLD`). Messages are trimmed when context exceeds 100 KB (`INFER_TRIM_THRESHOLD`).
- **Debug Mode**: Set `INFER_DEBUG` to dump every raw request and response payload to stderr.

---

## Installation

### Prerequisites

- `libcurl`
- A C compiler (`gcc` / `clang`)
- Python 3

On Ubuntu/Debian:
```bash
sudo apt install libcurl4-openssl-dev python3
```

On macOS:
```bash
brew install curl python
```

### Build & Install

```bash
# Clone and build
git clone https://github.com/dzyla/ai.git
cd ai
gcc -o ai ai.c cJSON.c -lcurl

# Install system-wide
sudo cp ai /usr/local/bin/
sudo cp ai_mcp.py /usr/local/bin/
sudo chmod +x /usr/local/bin/ai
sudo chmod +x /usr/local/bin/ai_mcp.py
```

### Quick Install with gemma4 (Linux)

`setup.sh` automates everything: apt dependencies, build, system install, and local [gemma4](https://snapcraft.io/gemma4) inference snap setup:

```bash
./setup.sh
source ~/.bashrc
```

### Configuration

Set environment variables in your shell profile (`~/.bashrc`, `~/.zshrc`, etc.):

```bash
export INFER_BASE_URL="http://localhost:8080/v1/"   # must end in /v1/
export INFER_API_KEY="not-needed"
export INFER_MODEL="gemma4"
```

Reload your shell or run `source ~/.bashrc` to apply.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `INFER_BASE_URL` | API endpoint (must end in `/v1/`; `chat/completions` appended) | — |
| `INFER_API_KEY` | API key for authentication | — |
| `INFER_MODEL` | Model name (e.g. `gemma-4-9b-it`) | — |
| `INFER_AUTO_APPROVE=1` | Auto-approve all `execute_command` prompts | disabled |
| `INFER_DEBUG` | Dump raw request/response payloads to stderr | disabled |
| `INFER_QUIET=1` | Suppress `[thinking]` output from the `think` tool | disabled |
| `INFER_TOOL_CHOICE` | Force tool call mode: `required` (default) or `auto` | `required` |
| `INFER_TEMPERATURE` | Override temperature for API requests | (model default) |
| `INFER_MAX_TOKENS` | Override max tokens for API requests | (model default) |
| `INFER_CONTEXT_WINDOW` | Override auto-detected context window size | (auto-detected) |
| `INFER_TASK_TIMEOUT` | Force `task_complete` after N seconds | 300 |
| `INFER_MAX_TOOL_OUTPUT` | Cap individual tool output in bytes | 65536 |
| `INFER_TRIM_THRESHOLD` | Trigger message trimming when context exceeds this (bytes) | 100000 |
| `INFER_STUB_THRESHOLD` | Stub tool results once context exceeds this (bytes) | 250000 |

---

## Usage

### Basic Queries
```bash
ai "what's the tar command to extract .tar.gz?"
ai how do I exit vim
```

### Interactive REPL
```bash
# Start an interactive conversation shell
ai

# Start with an initial query and stay interactive
ai -i "let's look at this project"
```

### Flags Reference

| Flag | Long form | Env var | Effect |
|------|-----------|---------|--------|
| `-i` | `--interactive` | — | Start multi-turn REPL |
| `-y` | `--yes` | `INFER_AUTO_APPROVE=1` | Auto-approve all shell commands |
| `-q` | `--quiet` | `INFER_QUIET=1` | Suppress `[thinking]` reasoning output |
| `-h` | `--help` | — | Print help and exit |
| — | — | `INFER_DEBUG` | Dump raw JSON payloads to stderr |

### Piping Command Output
```bash
# Analyze memory hogs
ps aux | head -n 20 | ai "what's using the most memory?"

# Analyze disk space
df -h | ai "am I running out of space anywhere?"

# Code review
git diff | ai "summarize my changes"

# Empty pipe — agent re-runs the command itself to inspect stderr
some-failing-command | ai "why is this failing?"
```

### Real-Time Web Queries
```bash
ai "who won the latest Formula 1 race?"
ai "what's the current Bitcoin price?"
```

### Multimodal Queries (Images)
```bash
ai "what is in this picture?" path/to/image.png
ai "describe the chart" screenshot.webp
```

### Persistent Memory
```bash
# Store a preference
ai "remember my name is Bob and I prefer Python. Save to memory."

# Recall it later
ai "what is my name?"
```

### Auto-Approved Scripted Usage
```bash
ai -y "create a backup of ~/.bashrc to ~/bashrc.bak"
INFER_AUTO_APPROVE=1 ai "install ripgrep via apt"
```

---

## MCP Server Integration

Register MCP servers in any of these locations (checked in order):

1. `./mcp.json`
2. `./mcp_config.json`
3. `~/.config/ai/mcp.json`
4. `~/.config/ai/mcp_config.json`
5. `~/.gemini/config/mcp_config.json`
6. `~/.lmstudio/mcp.json`

Example `mcp.json`:
```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["/path/to/mcp-server/index.js"],
      "env": { "API_KEY": "…" }
    }
  }
}
```

Tools from the server are namespaced as `my_server__tool_name` in the model's tool catalog.

---

## Native Tool Reference

| Tool | Description |
|------|-------------|
| `think` | Model writes a step-by-step plan before acting. Output shown in dim text (suppressed by `-q`). |
| `execute_command` | Runs a shell command with confirmation prompt. Stdout+stderr captured and returned. |
| `web_search` | DuckDuckGo Lite search. Returns up to 5 results with title, URL, snippet. |
| `fetch_webpage` | Downloads URL and converts HTML to readable text (10 KB limit). |
| `read_file` | Reads text, PDF (pdftotext/pypdf/pdfplumber), or image (injected into vision context). Optional `start_line`/`end_line` for large files. |
| `write_file` | Writes content to a file, creating parent directories as needed. |
| `edit_file` | Search-and-replace edit on an existing file. Match must be exact. |
| `list_directory` | Lists directory contents with sizes and `[DIR]` markers. |
| `save_memory` | Persists text to `~/.config/ai/memory.txt` (overwrites, 4 KB cap). |
| `delegate_task` | Spawns a child `ai` process with full tool access (60 s timeout). |
| `computer_control` | Take screenshots, move mouse, click, type, press keys, and manage windows via xdotool/scrot. |
| `task_complete` | Signals completion. The `summary` argument is rendered as markdown and printed. |

---

## Runtime State

| Path | Purpose |
|------|---------|
| `~/.cache/ai/history.jsonl` | Append-only job log (timestamp, prompt, pipe writer, interactive flag, response) |
| `~/.config/ai/memory.txt` | Persistent memory, injected into every system prompt (4 KB cap) |
| `~/.config/ai/mcp.json` | Global MCP server registry |
| `~/.config/ai/skills/*/SKILL.md` | Global skill files, loaded into system prompt |
| `./.agents/skills/*/SKILL.md` | Project-local skill files, loaded into system prompt |

---

## Acknowledgements

This project is a fork of the original [infer](https://github.com/chethanreddy1/infer) repository by [chethanreddy1](https://github.com/chethanreddy1). The original was a minimal C-based CLI for piping content to LLMs. This fork adds: agentic tool-calling loop, shell command execution with confirmation, web search and webpage fetching, file read/write/edit tools, persistent memory, recursive sub-agent delegation, multimodal image input, MCP server integration, skill loading, rich terminal markdown rendering (syntax highlighting, tables, LaTeX math), context size guards, and interactive REPL mode.

---

## License

MIT
