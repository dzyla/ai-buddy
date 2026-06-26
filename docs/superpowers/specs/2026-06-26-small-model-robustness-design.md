# Design: Small-Model Robustness Overhaul

**Date:** 2026-06-26  
**Status:** Approved  

## Problem

Small local models (gemma4-class) using the `ai` CLI fail in three repeatable ways:

1. **Wrong tool / no tool** — model responds in prose when it should call a tool (e.g. "here are some links you can visit" instead of fetching them).
2. **Doesn't chain tools** — stops after a single tool call instead of continuing until the task is actually done.
3. **Loses thread** — on longer tasks, drifts, repeats itself, or hallucinates earlier results.

Root causes: the loop can exit at any time via a plain-text response; tool outputs are uncapped and pollute context; the system prompt is too abstract; `delegate_task` is broken.

---

## Section 1: Core Loop Overhaul

### Changes in `ai.c`

**`tool_choice: required`**  
Add `"tool_choice":"required"` to every API payload. The model must call a tool on every turn — plain-text responses are structurally impossible.

**`task_complete` as the only exit**  
Add a new tool `task_complete({"summary": "..."})`. The inner `has_more` loop only exits when this tool is detected among the tool calls. The `summary` argument is passed to `render-markdown` and printed as the final response. The `finish_reason` check that currently drives exit is removed.

```
Current exit condition:  finish_reason != "tool_calls"
New exit condition:      tool name == "task_complete"
```

**Loop cap increase**  
Raise the inner loop cap from 20 to 30 iterations to give more room for multi-step tasks.

**In-C handling of `think` and `task_complete`**  
Both new tools are handled natively in `ai.c` without shelling out to Python:
- `think`: print reasoning (or suppress), add a minimal tool result `{"ok": true}` to messages, continue.
- `task_complete`: extract `summary`, render, log, set `has_more = 0`.

---

## Section 2: The `think` Tool

### Schema (added in `ai_mcp.py` `list-tools`)

```json
{
  "type": "function",
  "function": {
    "name": "think",
    "description": "Use before any task requiring more than one step. Write out your plan: what you know, what you need to find, and which tools you will call in order. This is shown to the user.",
    "parameters": {
      "type": "object",
      "properties": {
        "reasoning": {
          "type": "string",
          "description": "Your step-by-step plan for completing the task."
        }
      },
      "required": ["reasoning"]
    }
  }
}
```

### Display in `ai.c`

When `think` is the called tool:
- If **not quiet**: print `\033[2m[thinking] <reasoning>\033[0m\n` to stdout.
- If **quiet**: suppress output entirely.
- In both cases: append a minimal tool result `{"ok":true}` to `messages_json` so the model sees its plan in context.

### Quiet mode

New CLI flag `-q` / `--quiet`. New env var `INFER_QUIET=1`. When set, `think` output is suppressed. No other behaviour changes.

Parsed in `ai.c` alongside existing `-i` / `-y` flags.

---

## Section 3: Context Pruning

Two independent caps applied in `ai.c`:

### 3a. Hard cap on individual tool results

Before appending any tool response to `messages_json`, truncate the content string to **3000 characters**. Applied to all tools (execute_command, fetch_webpage, read_file, MCP tools, etc.). If truncated, append `\n... [truncated]` so the model knows there is more.

Implementation: after `tool_output` is produced and before `json_escape(tool_output)`, check `strlen(tool_output)` and `realloc`/truncate as needed.

### 3b. Total-size guard on new additions

Before appending any new tool result to `messages_json`, check `strlen(messages_json)`. If it already exceeds **40,000 bytes**, write a stub instead of the actual content:

```
[context limit reached — result omitted to preserve model focus]
```

This never modifies anything already written to `messages_json` and never parses existing JSON — it only controls what gets appended next. Combined with the 3000-char per-result cap (3a), worst-case context from tool results is bounded at ~40KB.

Implementation: one `strlen` check + one branch before the `json_escape(tool_output)` call, ~5 lines of C.

---

## Section 4: System Prompt Rewrite

Replace the current 5 abstract numbered directives with concrete one-liners. Full new prompt:

```
You are a fully autonomous CLI agent. Output in clean markdown. Follow these rules exactly:

TOOL USE:
- Use think before any task requiring more than one tool call.
- NEVER describe what the user can do themselves. If a tool can get the answer, use it.
- Only call task_complete when you have verified the result yourself using tools.
- After web_search, you MUST call fetch_webpage on at least one result URL before task_complete.
- After writing a script with write_file, you MUST run it with execute_command to verify it works.

FAILURE RECOVERY:
- If execute_command fails, read the error output, fix the root cause, and retry. Make at least 3 attempts before giving up.
- If a library is missing, install it. If an API is blocked, find an alternative.

DELEGATION:
- For tasks with independent parallel sub-tasks, use delegate_task to run them concurrently.
- delegate_task agents have full tool access. Give them specific, self-contained instructions.
```

---

## Section 5: Tool Cleanup

### 5a. Fix `delegate_task` bug in `ai_mcp.py`

Line ~962 has `for k in k` which shadows the loop variable, meaning MCP servers are never matched by their cleaned name. Fix:

```python
# Before (broken)
for k in mcp_servers.keys():
    clean_k = "".join(c if c.isalnum() or c == "_" else "_" for k in k)

# After (fixed)  
for k in mcp_servers.keys():
    clean_k = "".join(c if c.isalnum() or c == "_" else "_" for c in k)
```

### 5a-2. `task_complete` schema (added in `ai_mcp.py` `list-tools`)

```json
{
  "type": "function",
  "function": {
    "name": "task_complete",
    "description": "Call this ONLY when you have the verified answer from tools. Write the full result in summary — this is the only output the user sees. Do not call this if you still have URLs to fetch or commands to run.",
    "parameters": {
      "type": "object",
      "properties": {
        "summary": {
          "type": "string",
          "description": "The complete answer or result for the user, in markdown. Include all relevant data you gathered."
        }
      },
      "required": ["summary"]
    }
  }
}
```

Handled natively in `ai.c`: extract `summary` argument, pass to `render-markdown`, log via `log_job`, set `has_more = 0`.

### 5b. Tool ordering in `ai_mcp.py`

Reorder the `openai_tools` list so small models see tools in priority order:

1. `think` *(new, first)*
2. `execute_command`
3. `web_search`
4. `fetch_webpage`
5. `read_file`
6. `write_file`
7. `edit_file`
8. `list_directory`
9. `save_memory`
10. `delegate_task`
11. `task_complete` *(new, last)*

### 5c. Improved tool descriptions

Add **when-to-use** trigger phrases to each description:

| Tool | Added trigger phrase |
|------|---------------------|
| `web_search` | "Use when you need current info, prices, news, or facts. Always follow with fetch_webpage on at least one result." |
| `fetch_webpage` | "Use after web_search to read actual page content. Required before task_complete if search returned URLs." |
| `execute_command` | "Use for any system task, file inspection, running scripts, or verification. Prefer this over describing commands to the user." |
| `delegate_task` | "Use to run a self-contained sub-task in a parallel agent. Give complete, standalone instructions — the agent has no memory of this conversation." |
| `task_complete` | "Call this ONLY when you have the verified answer from tools. Write the full result in summary — this is the only output the user sees." |

---

## Implementation Order

1. **Fix `delegate_task` bug** — isolated, no risk, immediate payoff.
2. **Add `-q`/`INFER_QUIET` flag** — pure addition to `ai.c`.
3. **Add `think` + `task_complete` schemas** to `ai_mcp.py`.
4. **Reorder tools + improve descriptions** in `ai_mcp.py`.
5. **Rewrite system prompt** in `ai.c`.
6. **Add `tool_choice: required` + `task_complete` exit** to `ai.c` loop.
7. **Add `think` handler** to `ai.c` (native, no Python call).
8. **Add hard cap on tool output** (3000 chars) in `ai.c`.
9. **Add turn-based stub compression** in `ai.c` (hardest, do last).

Steps 1–5 are pure additions/fixes with no risk to existing behaviour. Steps 6–9 change the loop contract and should be tested together.

---

## Files Changed

| File | Changes |
|------|---------|
| `ai.c` | New flags, tool_choice, think/task_complete handlers, output cap, stub compression, loop cap, prompt rewrite |
| `ai_mcp.py` | New tool schemas (think, task_complete), reorder, better descriptions, delegate_task bug fix |
