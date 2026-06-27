# Design: Parser Hardening & Behavioral Fixes

**Date:** 2026-06-27
**Status:** Approved
**Target:** Large-context local models (256K) — Gemma 4 27B, Qwen 3, Llama 3.x class

---

## Problem

Three categories of failure observed with large local models:

1. **Tool call format issues** — model emits tool calls that the C parser silently drops or mangles, causing the agent to appear to misbehave when the model was correct.
2. **Loop behavior** — model hits token limit and output is rendered as garbled partial text; model stops without calling `task_complete` and plain-text response exits the loop.
3. **Specific tool reliability** — `edit_file` fails on trailing-whitespace mismatches; documentation doesn't match current code behavior.

---

## Section 1: C Parser Bug Fixes (`ai.c`)

### 1a. `finish_reason: "stop"` + `tool_calls` present → tool calls silently ignored

**Root cause:** The `should_call_tools` flag is set only when `finish_reason == "tool_calls"`. The fallback (`else if (tool_calls_tok != -1)`) only fires when `finish_reason_tok == -1` (no finish_reason at all). If a model returns `"finish_reason": "stop"` but also includes a `tool_calls` array (common in some local model servers), the tool calls are never dispatched.

**Fix:** If `tool_calls_tok` is present and points to a non-empty JSMN_ARRAY, set `should_call_tools = 1` regardless of `finish_reason`. The existing path is preserved as a fallback.

```c
// Before
if (finish_reason_tok != -1) {
    if (len == 10 && strncmp(..., "tool_calls", 10) == 0)
        should_call_tools = 1;
} else if (tool_calls_tok != -1) {
    should_call_tools = 1;
}

// After
if (finish_reason_tok != -1) {
    if (len == 10 && strncmp(..., "tool_calls", 10) == 0)
        should_call_tools = 1;
} else if (tool_calls_tok != -1) {
    should_call_tools = 1;
}
// Always honour tool_calls if present and non-empty, regardless of finish_reason
if (!should_call_tools && tool_calls_tok != -1
        && tok[tool_calls_tok].type == JSMN_ARRAY
        && tok[tool_calls_tok].size > 0) {
    should_call_tools = 1;
}
```

### 1b. `args_tok` as `JSMN_OBJECT` → mangled arguments

**Root cause:** The OpenAI spec says `function.arguments` is a JSON-encoded string (double-encoded). `unescape_json_string` is called unconditionally on the token content. Some local model servers return `arguments` as a raw JSON object, not a string. jsmn then reports `tok[args_tok].type == JSMN_OBJECT`, and `unescape_json_string` is called on raw JSON, producing garbled output.

**Fix:** Check `tok[args_tok].type` before unescaping. If `JSMN_STRING`, unescape as before. If `JSMN_OBJECT` or `JSMN_ARRAY`, extract the raw bytes directly (no unescaping needed — the content is already valid JSON).

```c
char *unescaped_args;
if (tok[args_tok].type == JSMN_STRING) {
    unescaped_args = unescape_json_string(
        chunk.data + tok[args_tok].start,
        tok[args_tok].end - tok[args_tok].start);
} else {
    // Raw object/array — copy bytes directly
    int alen = tok[args_tok].end - tok[args_tok].start;
    unescaped_args = malloc(alen + 1);
    memcpy(unescaped_args, chunk.data + tok[args_tok].start, alen);
    unescaped_args[alen] = '\0';
}
```

### 1c. jsmn fixed token buffer overflow

**Root cause:** `jsmntok_t tok[2048]` is stack-allocated. A response with many tool calls, deeply nested schemas, or long arrays can exhaust this. `jsmn_parse` returns `JSMN_ERROR_NOMEM` and the entire response is silently dropped (the existing error path only prints "Failed to parse JSON response: -1" and breaks).

**Fix:** Increase to `tok[4096]`. Add a specific check for `JSMN_ERROR_NOMEM` in the error branch that prints a human-readable message.

```c
jsmntok_t tok[4096];
// ...
if (r == JSMN_ERROR_NOMEM) {
    fprintf(stderr, "[ai] Error: response JSON too large for token buffer (> 4096 tokens). "
                    "Increase jsmntok_t buffer size.\n");
} else if (r < 0) {
    fprintf(stderr, "Failed to parse JSON response: %d\n", r);
}
```

### 1d. VLA stack allocation for `render_cmd` and `task_complete`

**Root cause:** Two places use C99 VLAs sized with `strlen()` of user-controlled content:
- `char render_cmd[4096 + strlen(escaped_content)]` (line ~2172, text output path)
- `char render_cmd[4096 + strlen(escaped_summary)]` (line ~1915, task_complete path)

A large `task_complete` summary can produce a multi-hundred-KB stack frame.

**Fix:** Replace both with `malloc` + `snprintf`. Free after use.

```c
size_t rcmd_len = 4096 + strlen(escaped_summary);
char *render_cmd = malloc(rcmd_len);
snprintf(render_cmd, rcmd_len, "python3 %s render-markdown %s", mcp_script, escaped_summary);
char *rendered = run_shell_command(render_cmd, NULL);
free(render_cmd);
```

---

## Section 2: Behavioral Hardening

### 2a. `INFER_TOOL_CHOICE` environment variable

**Problem:** `tool_choice: "auto"` allows the model to respond with plain text instead of calling a tool, which exits the agentic loop prematurely. `"required"` is the correct default for agentic use but breaks on older llama.cpp servers that don't support it.

**Fix:** Add `INFER_TOOL_CHOICE` env var (values: `"required"` or `"auto"`). Change the default to `"required"`. Document that users on older servers should set `INFER_TOOL_CHOICE=auto`.

Parse alongside existing env vars in `main()`:
```c
char *env_tool_choice = getenv("INFER_TOOL_CHOICE");
const char *tool_choice_val = "required";  // new default
if (env_tool_choice && (strcmp(env_tool_choice, "auto") == 0
                     || strcmp(env_tool_choice, "required") == 0)) {
    tool_choice_val = env_tool_choice;
}
```

Substitute into payload builder:
```c
snprintf(payload, plen, "... \"tool_choice\":\"%s\"}", tool_choice_val);
```

### 2b. `finish_reason: "length"` recovery

**Problem:** When a model hits its output token limit, `finish_reason` is `"length"`. The current code treats this identically to `"stop"` — renders whatever partial content is in the response and exits the loop. Partial tool call JSON renders as garbage.

**Fix:** Detect `finish_reason == "length"`, emit a stderr warning, and inject a recovery nudge instead of rendering the truncated content:

```c
int is_length_truncated = 0;
if (finish_reason_tok != -1) {
    int len = tok[finish_reason_tok].end - tok[finish_reason_tok].start;
    if (len == 6 && strncmp(chunk.data + tok[finish_reason_tok].start, "length", 6) == 0)
        is_length_truncated = 1;
}

if (is_length_truncated) {
    fprintf(stderr, "\033[1;33m[ai] Warning: model hit token limit — response truncated. "
                    "Nudging to complete.\033[0m\n");
    messages_json = append_message(messages_json,
        "{\"role\":\"user\",\"content\":\"Your last response was cut off by the token limit. "
        "Call task_complete now with your current best answer.\"}");
    has_more = 1;
}
```

### 2c. `edit_file` trailing-whitespace tolerance (`ai_mcp.py`)

**Problem:** `edit_file` requires byte-exact match of `search_content`. Local models frequently emit trailing spaces on code lines that match the intent but not the bytes, producing spurious "search content not found" errors.

**Fix:** In `edit_file()`, add a fuzzy retry that strips trailing whitespace from each line before comparing. If the fuzzy match succeeds, apply the replacement using the original file's line endings. Report in the return message when fuzzy matching was used.

```python
def edit_file(path, search_content, replace_content):
    # ... existing exact match ...
    if search_content not in content:
        # Fuzzy retry: strip trailing whitespace per line
        def strip_trailing(s):
            return "\n".join(line.rstrip() for line in s.splitlines())
        stripped_content = strip_trailing(content)
        stripped_search = strip_trailing(search_content)
        if stripped_search in stripped_content:
            # Reconstruct replacement in the original file
            new_content = content.replace(
                # Find the original span matching the stripped search
                _find_original_span(content, search_content),
                replace_content
            )
            # ... write and return with note
            return f"File successfully edited at {path} (fuzzy whitespace match used)"
        return f"Error: search content not found ..."
```

Implementation detail: the fuzzy path rebuilds a replacement by finding each line of `search_content` in the corresponding line of `content` via `rstrip()` comparison, then replaces only the matched region. Concretely: split both `content` and `search_content` into lines; find the first line index in `content` where `content_lines[i].rstrip() == search_lines[0].rstrip()`; verify all subsequent lines match the same way; extract the original substring `"\n".join(content_lines[i:i+n])` and call `content.replace(original_span, replace_content, 1)`. If no such run of lines is found, return the original "not found" error.

---

## Section 3: Documentation Updates

### README.md

| Location | Change |
|----------|--------|
| Features — "Agentic Tool Loop" | Remove "Sends `tool_choice: required`" claim; say default is `"required"`, overridable via `INFER_TOOL_CHOICE=auto` |
| Environment Variables table | Add `INFER_TOOL_CHOICE` row |
| Native Tool Reference table | Add `computer_control` row (currently missing) |
| `read_file` tool entry | Add `start_line` / `end_line` optional parameters |
| Tool count | Fix "11 native tools" → "12 native tools" in `ai_mcp.py` description |

### CLAUDE.md

| Location | Change |
|----------|--------|
| Architecture — "Runs the agentic loop" | Fix `tool_choice: required` comment; mention `INFER_TOOL_CHOICE` |
| Optional environment variables list | Add `INFER_TOOL_CHOICE` |
| `ai_mcp.py` native tools list | Add `computer_control` (12th tool) |
| Behavioral notes | Add `finish_reason: "length"` recovery behavior |
| Tool descriptions | Note `edit_file` fuzzy trailing-whitespace fallback |

---

## Files Changed

| File | Section |
|------|---------|
| `ai.c` | 1a (finish_reason + tool_calls), 1b (args_tok type), 1c (jsmn buffer), 1d (VLA→heap), 2a (INFER_TOOL_CHOICE), 2b (finish_reason:length) |
| `ai_mcp.py` | 2c (edit_file fuzzy match) |
| `README.md` | 3 (documentation) |
| `CLAUDE.md` | 3 (documentation) |

## Implementation Order

1. **1c** — jsmn buffer increase (1 line, zero risk)
2. **1d** — VLA→heap for render_cmd (pure safety fix)
3. **1a** — finish_reason + tool_calls override (core bug)
4. **1b** — args_tok type check (core bug)
5. **2a** — INFER_TOOL_CHOICE env var (additive)
6. **2b** — finish_reason:length recovery (additive)
7. **2c** — edit_file fuzzy match in Python (isolated)
8. **3** — README + CLAUDE.md documentation
