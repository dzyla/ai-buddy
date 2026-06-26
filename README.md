# ai 🤖

A minimal, agentic CLI tool for piping anything into an LLM and executing terminal work, written in pure C and Python with zero external library dependencies.

```bash
ps aux | ai "what's eating memory"
ai "tell me what is taking most space"
ai "what are the coldest and hottest places in US right now?"
```

It reads from stdin, sends to an LLM, runs tools dynamically (such as shell commands, web searches, webpage crawling, or delegating tasks to sub-agents), maintains persistent memory, and outputs beautifully formatted markdown text directly in your terminal.

---

## Features

- **Interactive Chat/REPL**: Run `ai` with no arguments, or with `-i` / `--interactive`, to start an interactive multi-turn conversation shell where chat history is preserved.
- **Safety Confirmations**: Protects your machine by prompting for confirmation before running any shell command natively.
- **Auto-Approve Flag**: Bypass safety prompts for automated scripts or trusted tasks using `-y` / `--yes` or setting `INFER_AUTO_APPROVE=1`.
- **System-Context Awareness**: Automatically collects and provides OS, current directory, shell, user, and local time info to the LLM for high-accuracy local context.
- **Native Directory Listing**: Safe directory navigation using a native tool (`list_directory`) to explore the project layout without running shell processes.
- **Code Syntax Highlighting**: Terminal output code blocks are dynamically syntax-highlighted in real time for common programming languages.
- **Automatic Job History Logging**: Automatically appends execution histories, prompts, and final responses to a local cache file at `~/.cache/ai/history.jsonl` for audits and references.
- **Automatic Skill Loading**: Automatically scans and loads local workspace rules from `./.agents/skills/` and global guidelines from `~/.config/ai/skills/` directly into the LLM system prompt context.
- **Agentic Loop**: The CLI executes tools dynamically (multiple calls in parallel or sequence) based on what the LLM requests.
- **Native Web Search**: Scrapes web results from DuckDuckGo Lite natively without requiring any third-party APIs or search tokens.
- **Webpage Fetching**: Downloads, cleans, and converts HTML pages into readable text so the model can inspect details on URLs.
- **Persistent Memory**: Automatically stores key preferences, facts, or context across invocations in `~/.config/ai/memory.txt`.
- **Recursive Agent Delegation**: Can spawn separate, independent sub-agents (running separate child processes of `ai`) to handle sub-tasks and compile findings.
- **Rich Terminal Rendering**: Renders standard markdown output (bold, italic, list bullets, code blocks, and colored headers) directly in the terminal using ANSI escape codes.
- **Multimodal Support**: Simply pass an image path (`.png`, `.jpg`, `.jpeg`, `.webp`) in the arguments; `ai` base64-encodes it and sends it directly to the model alongside your text.
- **File Modification & Creation**: Safely write new files (automatically creating parent directories) or apply precise search-and-replace text edits to existing files using dedicated native tools.

---

## Installation

### Prerequisites

- `libcurl`
- A C compiler (`gcc`/`clang`)
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
gcc -o ai ai.c -lcurl

# Install system-wide
sudo cp ai /usr/local/bin/
sudo cp ai_mcp.py /usr/local/bin/
sudo chmod +x /usr/local/bin/ai
sudo chmod +x /usr/local/bin/ai_mcp.py
```

### Configuration

Set environment variables in your shell profile (`~/.bashrc`, `~/.zshrc`, etc):

```bash
export INFER_BASE_URL="http://localhost:8080/v1/"
export INFER_API_KEY="not-needed"
export INFER_MODEL="gemma4"
```

Reload your shell or run `source ~/.bashrc` to apply changes.

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

### Command Approval & Flags
```bash
# Safely prompt before executing shell commands (default)
ai "what kernel version is running?"

# Auto-approve commands for script usage
ai -y "create a backup of my ~/.bashrc"
```

### Command Outputs & Logs
```bash
# Analyze memory hogs
ps aux | head -n 20 | ai "what's using the most memory?"

# Analyze disk space
df -h | ai "am I running out of space anywhere?"

# Git review
git diff | ai "summarize my changes"
```

### Real-Time Web Queries
```bash
# Search and fetch actual content automatically
ai "who won the latest Formula 1 race?"
```

### Multimodal Queries (Images)
```bash
# Describe an image
ai "what is in this picture?" path/to/image.png
```

### Persistent Memory
```bash
# Set preference
ai "remember my name is Bob and I use Ubuntu. Save to memory."

# Query it later
ai "what is my name?"
```

---

## Configuration Lookup

`ai` reads configuration from environment variables:
- `INFER_BASE_URL` - Base API URL (ends with `/v1/`; `ai` appends `chat/completions`)
- `INFER_API_KEY` - Your API key  
- `INFER_MODEL` - Model name

You can register standard Model Context Protocol (MCP) servers in `~/.config/ai/mcp.json` to extend the toolset further.

## Acknowledgements

This project is a fork of the original [infer](https://github.com/chethanreddy1/infer) repository created by [chethanreddy1](https://github.com/chethanreddy1). The original was a minimal C-based CLI tool for piping content to LLMs. This fork expands on it by adding tool calling capabilities, recursive sub-agents, terminal markdown rendering, persistent memory, and multimodal image input.

---

## License

MIT
