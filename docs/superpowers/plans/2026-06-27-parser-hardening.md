# Parser Hardening & Behavioral Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three silent parser bugs in `ai.c`, add `INFER_TOOL_CHOICE` env var and `finish_reason:length` recovery, add trailing-whitespace fuzzy fallback to `edit_file`, and update README/CLAUDE.md to reflect current code behavior.

**Architecture:** All C changes are in the inner agentic loop inside `main()` in `ai.c`. The Python change is a self-contained function replacement in `ai_mcp.py`. Documentation changes are independent edits to `README.md` and `CLAUDE.md`. Each task compiles and tests independently.

**Tech Stack:** C99, gcc, libcurl, jsmn (vendored `jsmn.h`), Python 3, cJSON (vendored `cJSON.c`/`cJSON.h`)

## Global Constraints

- Build command: `gcc -o ai ai.c cJSON.c -lcurl` — must compile cleanly with no warnings after each C task
- No external dependencies may be added
- Do not change function signatures or public contracts of existing helpers
- All C changes are inside `main()` unless noted; no new source files
- `JSMN_ERROR_NOMEM = -1`, `JSMN_ERROR_INVAL = -2`, `JSMN_ERROR_PART = -3` (from `jsmn.h:56-60`)

---

### Task 1: Increase jsmn token buffer and improve overflow error message

**Files:**
- Modify: `ai.c:1726` (buffer size)
- Modify: `ai.c:1730-1735` (error branch)

**Interfaces:**
- Produces: nothing consumed by later tasks — isolated fix

- [ ] **Step 1: Change `tok[2048]` to `tok[4096]`**

At `ai.c:1726`, replace:
```c
jsmntok_t tok[2048];
```
with:
```c
jsmntok_t tok[4096];
```

- [ ] **Step 2: Improve NOMEM error message**

At `ai.c:1730-1735`, replace:
```c
                if (r < 0) {
                    fprintf(stderr, "Failed to parse JSON response: %d\n", r);
                    free(payload);
                    free(chunk.data);
                    break;
                }
```
with:
```c
                if (r < 0) {
                    if (r == JSMN_ERROR_NOMEM)
                        fprintf(stderr, "[ai] Error: response JSON exceeds token buffer "
                                        "(>4096 tokens). Increase jsmntok_t array in ai.c.\n");
                    else
                        fprintf(stderr, "Failed to parse JSON response: %d\n", r);
                    free(payload);
                    free(chunk.data);
                    break;
                }
```

- [ ] **Step 3: Compile**

```bash
gcc -o ai ai.c cJSON.c -lcurl
```
Expected: no errors, no warnings.

- [ ] **Step 4: Commit**

```bash
git add ai.c
git commit -m "fix: increase jsmn token buffer to 4096, improve NOMEM error message"
```

---

### Task 2: Replace VLA stack allocations with heap for render_cmd

**Files:**
- Modify: `ai.c:1913-1916` (task_complete path)
- Modify: `ai.c:2170-2174` (text output path)

**Interfaces:**
- Produces: nothing consumed by later tasks — isolated safety fix

- [ ] **Step 1: Fix task_complete render_cmd (line 1913)**

At `ai.c:1913-1916`, replace:
```c
                                       char *escaped_summary = shell_escape(summary);
                                       char render_cmd[4096 + strlen(escaped_summary)];
                                       snprintf(render_cmd, sizeof(render_cmd), "python3 %s render-markdown %s", mcp_script, escaped_summary);
                                       char *rendered = run_shell_command(render_cmd, NULL);
```
with:
```c
                                       char *escaped_summary = shell_escape(summary);
                                       size_t rcmd1_len = strlen(mcp_script) + strlen(escaped_summary) + 32;
                                       char *render_cmd = malloc(rcmd1_len);
                                       snprintf(render_cmd, rcmd1_len, "python3 %s render-markdown %s", mcp_script, escaped_summary);
                                       char *rendered = run_shell_command(render_cmd, NULL);
                                       free(render_cmd);
```

- [ ] **Step 2: Fix text output render_cmd (line 2170)**

At `ai.c:2170-2174`, replace:
```c
                          char *escaped_content = shell_escape(unescaped_content);

                          char render_cmd[4096 + strlen(escaped_content)];
                          snprintf(render_cmd, sizeof(render_cmd), "python3 %s render-markdown %s", mcp_script, escaped_content);
                          char *rendered_output = run_shell_command(render_cmd, NULL);
```
with:
```c
                          char *escaped_content = shell_escape(unescaped_content);
                          size_t rcmd2_len = strlen(mcp_script) + strlen(escaped_content) + 32;
                          char *render_cmd2 = malloc(rcmd2_len);
                          snprintf(render_cmd2, rcmd2_len, "python3 %s render-markdown %s", mcp_script, escaped_content);
                          char *rendered_output = run_shell_command(render_cmd2, NULL);
                          free(render_cmd2);
```

- [ ] **Step 3: Compile**

```bash
gcc -o ai ai.c cJSON.c -lcurl
```
Expected: no errors, no warnings.

- [ ] **Step 4: Smoke test — verify markdown rendering still works**

```bash
echo "hi" | INFER_AUTO_APPROVE=1 ./ai "say hello in one sentence"
```
Expected: a rendered markdown response prints normally.

- [ ] **Step 5: Commit**

```bash
git add ai.c
git commit -m "fix: replace VLA render_cmd with heap allocation to prevent stack overflow on large summaries"
```

---

### Task 3: Fix tool calls silently ignored when finish_reason is "stop"

**Files:**
- Modify: `ai.c:1816-1824`

**Interfaces:**
- Produces: nothing consumed by later tasks — isolated bug fix

**Context:** Some local model servers return `"finish_reason": "stop"` AND a populated `tool_calls` array in the same response. The current code only calls `should_call_tools = 1` when `finish_reason == "tool_calls"` or when `finish_reason` is absent entirely. When it's `"stop"` with tool_calls, the tool_calls are silently skipped.

- [ ] **Step 1: Add the override check after the existing finish_reason block**

At `ai.c:1816-1824`, replace:
```c
                int should_call_tools = 0;
                if (finish_reason_tok != -1) {
                    int len = tok[finish_reason_tok].end - tok[finish_reason_tok].start;
                    if (len == 10 && strncmp(chunk.data + tok[finish_reason_tok].start, "tool_calls", 10) == 0) {
                        should_call_tools = 1;
                    }
                } else if (tool_calls_tok != -1) {
                    should_call_tools = 1;
                }
```
with:
```c
                int should_call_tools = 0;
                if (finish_reason_tok != -1) {
                    int len = tok[finish_reason_tok].end - tok[finish_reason_tok].start;
                    if (len == 10 && strncmp(chunk.data + tok[finish_reason_tok].start, "tool_calls", 10) == 0) {
                        should_call_tools = 1;
                    }
                } else if (tool_calls_tok != -1) {
                    should_call_tools = 1;
                }
                /* Always honour tool_calls if present and non-empty, regardless of finish_reason */
                if (!should_call_tools && tool_calls_tok != -1
                        && tok[tool_calls_tok].type == JSMN_ARRAY
                        && tok[tool_calls_tok].size > 0) {
                    should_call_tools = 1;
                }
```

- [ ] **Step 2: Compile**

```bash
gcc -o ai ai.c cJSON.c -lcurl
```
Expected: no errors, no warnings.

- [ ] **Step 3: Commit**

```bash
git add ai.c
git commit -m "fix: honour tool_calls when finish_reason is stop — silent drop on some local model servers"
```

---

### Task 4: Handle args_tok as JSMN_OBJECT (raw JSON, not string-encoded)

**Files:**
- Modify: `ai.c:1871`

**Interfaces:**
- Produces: `char *unescaped_args` — consumed immediately in the same scope; no change to callers

**Context:** OpenAI spec says `function.arguments` is a JSON-encoded string (double-encoded). Some local servers return it as a raw JSON object. jsmn reports the token type as `JSMN_OBJECT` in that case. Calling `unescape_json_string` on a raw object produces garbled output.

- [ ] **Step 1: Replace unconditional unescape with type-checked branch**

At `ai.c:1871`, replace:
```c
                              char *unescaped_args = unescape_json_string(chunk.data + tok[args_tok].start, tok[args_tok].end - tok[args_tok].start);
```
with:
```c
                              char *unescaped_args;
                              if (tok[args_tok].type == JSMN_STRING) {
                                  unescaped_args = unescape_json_string(
                                      chunk.data + tok[args_tok].start,
                                      tok[args_tok].end - tok[args_tok].start);
                              } else {
                                  int alen = tok[args_tok].end - tok[args_tok].start;
                                  unescaped_args = malloc(alen + 1);
                                  memcpy(unescaped_args, chunk.data + tok[args_tok].start, alen);
                                  unescaped_args[alen] = '\0';
                              }
```

- [ ] **Step 2: Compile**

```bash
gcc -o ai ai.c cJSON.c -lcurl
```
Expected: no errors, no warnings.

- [ ] **Step 3: Commit**

```bash
git add ai.c
git commit -m "fix: handle args_tok as JSMN_OBJECT for servers that return raw JSON instead of string-encoded arguments"
```

---

### Task 5: Add INFER_TOOL_CHOICE env var, default "required"

**Files:**
- Modify: `ai.c:1315` (env var parsing, after existing env block)
- Modify: `ai.c:1619-1628` (primary payload builder)
- Modify: `ai.c:1686-1696` (fallback payload builder inside connection retry)

**Interfaces:**
- Produces: `tool_choice_val` (const char*) — a string used in both payload builders inside the loop. Must be declared in `main()` before the `while (keep_going)` loop so it is visible in both payload build sites.

**Context:** `tool_choice: "auto"` lets the model respond with plain text and exit the loop without calling any tool. `"required"` forces a tool call every iteration, which is the correct behavior for an agentic loop. Some older llama.cpp servers don't support `"required"` and return an API error — those users can set `INFER_TOOL_CHOICE=auto`.

- [ ] **Step 1: Add env var parsing at line 1315 (after the stub_threshold block)**

At `ai.c:1315`, after the line:
```c
    if (env_stub && *env_stub) stub_threshold = atoi(env_stub);
```
add:
```c
    const char *tool_choice_val = "required";
    char *env_tool_choice = getenv("INFER_TOOL_CHOICE");
    if (env_tool_choice && (strcmp(env_tool_choice, "auto") == 0
                         || strcmp(env_tool_choice, "required") == 0)) {
        tool_choice_val = env_tool_choice;
    }
```

- [ ] **Step 2: Update primary payload builder (line 1623)**

At `ai.c:1623`, replace:
```c
                    snprintf(payload, plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"auto\"}",
                             model, opt_fields, messages_json, tools_json);
```
with:
```c
                    snprintf(payload, plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"%s\"}",
                             model, opt_fields, messages_json, tools_json, tool_choice_val);
```

- [ ] **Step 3: Update fallback payload builder inside connection retry (line 1691)**

At `ai.c:1691`, replace:
```c
                                snprintf(payload, new_plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"auto\"}",
                                         model, opt_fields, messages_json, tools_json);
```
with:
```c
                                snprintf(payload, new_plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"%s\"}",
                                         model, opt_fields, messages_json, tools_json, tool_choice_val);
```

- [ ] **Step 4: Compile**

```bash
gcc -o ai ai.c cJSON.c -lcurl
```
Expected: no errors, no warnings.

- [ ] **Step 5: Verify default is "required" in request payload**

```bash
echo "hi" | INFER_DEBUG=1 INFER_AUTO_APPROVE=1 ./ai "say hi" 2>&1 | grep tool_choice | head -2
```
Expected output contains: `"tool_choice":"required"`

- [ ] **Step 6: Verify INFER_TOOL_CHOICE=auto override works**

```bash
echo "hi" | INFER_TOOL_CHOICE=auto INFER_DEBUG=1 INFER_AUTO_APPROVE=1 ./ai "say hi" 2>&1 | grep tool_choice | head -2
```
Expected output contains: `"tool_choice":"auto"`

- [ ] **Step 7: Commit**

```bash
git add ai.c
git commit -m "feat: add INFER_TOOL_CHOICE env var, default to required for agentic loop reliability"
```

---

### Task 6: Recover from finish_reason:length instead of rendering truncated output

**Files:**
- Modify: `ai.c:1737-1742` (add `finish_reason_length` variable declaration)
- Modify: `ai.c:1758` (set `finish_reason_length` after the token scan loop)
- Modify: `ai.c:2137-2138` (else branch — wrap existing code in `finish_reason_length` guard)

**Interfaces:**
- Produces: nothing consumed by later tasks — isolated behavioral fix

**Context:** When a model hits its output token limit, `finish_reason` is `"length"`. Currently this falls through to the text-output path, rendering whatever partial (possibly mid-JSON) content the model produced. The fix detects `"length"`, emits a warning, injects a user nudge, and keeps the loop alive for one more iteration.

- [ ] **Step 1: Declare finish_reason_length alongside other token indices (line 1737)**

At `ai.c:1737-1742`, replace:
```c
                int finish_reason_tok = -1;
                int message_tok = -1;
                int tool_calls_tok = -1;
                int usage_tok = -1;

                int error_tok = -1;
```
with:
```c
                int finish_reason_tok = -1;
                int message_tok = -1;
                int tool_calls_tok = -1;
                int usage_tok = -1;
                int finish_reason_length = 0;

                int error_tok = -1;
```

- [ ] **Step 2: Set finish_reason_length after the token scan loop (line 1758)**

At `ai.c:1758`, after the closing `}` of the `for (int i = 1; i < r; i++)` scan loop, add:
```c
                if (finish_reason_tok != -1) {
                    int flen = tok[finish_reason_tok].end - tok[finish_reason_tok].start;
                    if (flen == 6 && strncmp(chunk.data + tok[finish_reason_tok].start, "length", 6) == 0)
                        finish_reason_length = 1;
                }
```

- [ ] **Step 3: Wrap the else branch to skip rendering on length truncation (line 2137)**

At `ai.c:2137-2138`, replace:
```c
                  } else {
                      has_more = 0;
```
with:
```c
                  } else {
                      if (finish_reason_length) {
                          fprintf(stderr, "\033[1;33m[ai] Warning: model hit token limit — "
                                          "response truncated. Nudging to complete.\033[0m\n");
                          messages_json = append_message(messages_json,
                              "{\"role\":\"user\",\"content\":\"Your last response was cut off "
                              "by the token limit. Call task_complete now with your current "
                              "best answer.\"}");
                          has_more = 1;
                      } else {
                      has_more = 0;
```

Then find the closing `}` of the existing else block (just before the usage/stats line, around line 2210) and add an extra `}` to close the new `else {` added above. The closing brace placement: the existing else block ends before the `/* Usage / speed stats line */` comment. Add the closing `}` immediately before that comment:

```c
                      } /* end !finish_reason_length else */
                  }
```

- [ ] **Step 4: Compile**

```bash
gcc -o ai ai.c cJSON.c -lcurl
```
Expected: no errors, no warnings.

- [ ] **Step 5: Commit**

```bash
git add ai.c
git commit -m "fix: detect finish_reason:length and inject recovery nudge instead of rendering truncated output"
```

---

### Task 7: edit_file trailing-whitespace fuzzy fallback

**Files:**
- Modify: `ai_mcp.py:781-795` (the `edit_file` function)

**Interfaces:**
- Produces: nothing consumed by other tasks — isolated Python fix

**Context:** `edit_file` requires byte-exact match of `search_content`. Local models frequently emit trailing spaces on code lines, causing false "not found" errors. The fix adds a second pass that strips trailing whitespace per line from both sides before comparing.

- [ ] **Step 1: Replace the edit_file function**

At `ai_mcp.py:781-795`, replace the entire `edit_file` function:
```python
def edit_file(path, search_content, replace_content):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file {path} does not exist."
        with open(abs_path, "r") as f:
            content = f.read()
        if search_content not in content:
            return f"Error: search content not found in {path}. Make sure the search block matches exactly including whitespace."
        new_content = content.replace(search_content, replace_content)
        with open(abs_path, "w") as f:
            f.write(new_content)
        return f"File successfully edited at {path}"
    except Exception as e:
        return f"Error editing file: {e}"
```
with:
```python
def edit_file(path, search_content, replace_content):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file {path} does not exist."
        with open(abs_path, "r") as f:
            content = f.read()
        if search_content in content:
            new_content = content.replace(search_content, replace_content)
            with open(abs_path, "w") as f:
                f.write(new_content)
            return f"File successfully edited at {path}"
        # Fuzzy retry: strip trailing whitespace per line and compare
        search_lines = search_content.splitlines()
        content_lines = content.splitlines()
        n = len(search_lines)
        matched_start = -1
        for i in range(len(content_lines) - n + 1):
            if all(content_lines[i + j].rstrip() == search_lines[j].rstrip()
                   for j in range(n)):
                matched_start = i
                break
        if matched_start >= 0:
            original_span = "\n".join(content_lines[matched_start:matched_start + n])
            new_content = content.replace(original_span, replace_content, 1)
            with open(abs_path, "w") as f:
                f.write(new_content)
            return f"File successfully edited at {path} (fuzzy whitespace match used)"
        return (f"Error: search content not found in {path}. "
                f"Make sure the search block matches exactly including whitespace.")
    except Exception as e:
        return f"Error editing file: {e}"
```

- [ ] **Step 2: Test exact match still works**

```bash
echo -e "def foo():\n    return 1" > /tmp/test_edit.py
python3 ai_mcp.py call-tool edit_file edit_file '{"path":"/tmp/test_edit.py","search_content":"    return 1","replace_content":"    return 2"}'
cat /tmp/test_edit.py
```
Expected: `cat` output shows `return 2`.

- [ ] **Step 3: Test fuzzy match (trailing space in search_content)**

```bash
echo -e "def foo():\n    return 2" > /tmp/test_edit.py
python3 ai_mcp.py call-tool edit_file edit_file '{"path":"/tmp/test_edit.py","search_content":"    return 2   ","replace_content":"    return 3"}'
cat /tmp/test_edit.py
```
Expected: `cat` output shows `return 3`. Response message contains `(fuzzy whitespace match used)`.

- [ ] **Step 4: Test "not found" still returns error**

```bash
python3 ai_mcp.py call-tool edit_file edit_file '{"path":"/tmp/test_edit.py","search_content":"def bar():","replace_content":"def baz():"}'
```
Expected: output contains `Error: search content not found`.

- [ ] **Step 5: Commit**

```bash
git add ai_mcp.py
git commit -m "fix: add trailing-whitespace fuzzy fallback to edit_file for local model tolerance"
```

---

### Task 8: Update README.md and CLAUDE.md

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Produces: nothing consumed by code tasks — documentation only

- [ ] **Step 1: Update README.md — fix tool_choice claim in Features**

In `README.md` at line 19, replace:
```
**Agentic Tool Loop**: The agent loops up to 30 times per turn, calling tools, reading results, and calling more tools until it calls `task_complete`. `tool_choice: required` forces every response to use a tool.
```
with:
```
**Agentic Tool Loop**: The agent loops up to 30 times per turn, calling tools, reading results, and calling more tools until it calls `task_complete`. Defaults to `tool_choice: required` to force a tool call every iteration. Set `INFER_TOOL_CHOICE=auto` for servers that do not support `required`.
```

- [ ] **Step 2: Add INFER_TOOL_CHOICE to README.md env vars table**

In `README.md`, in the Environment Variables table, after the `INFER_QUIET=1` row, add:
```
| `INFER_TOOL_CHOICE` | Force tool call mode: `required` (default) or `auto` | `required` |
```

- [ ] **Step 3: Add computer_control to README.md Native Tool Reference table**

In `README.md`, in the Native Tool Reference table, after the `delegate_task` row and before `task_complete`, add:
```
| `computer_control` | Take screenshots, move mouse, click, type, press keys, and manage windows via xdotool/scrot. |
```

- [ ] **Step 4: Update read_file entry in README.md tool table**

In `README.md`, replace the `read_file` row:
```
| `read_file` | Reads text (12 KB limit), PDF (pdftotext/pypdf/pdfplumber), or image (injected into vision context). |
```
with:
```
| `read_file` | Reads text, PDF (pdftotext/pypdf/pdfplumber), or image (injected into vision context). Optional `start_line`/`end_line` for large files. |
```

- [ ] **Step 5: Update CLAUDE.md — fix tool_choice comment in architecture**

In `CLAUDE.md`, replace:
```
- Runs the agentic loop: POST via libcurl → parse `tool_calls` → execute each → append `tool` messages → repeat (capped at **30** iterations per turn). Sends `tool_choice: required` so small models always call a tool.
```
with:
```
- Runs the agentic loop: POST via libcurl → parse `tool_calls` → execute each → append `tool` messages → repeat (capped at **30** iterations per turn). Sends `tool_choice: required` by default (overridable via `INFER_TOOL_CHOICE=auto` for servers that do not support it).
```

- [ ] **Step 6: Add INFER_TOOL_CHOICE to CLAUDE.md optional env vars**

In `CLAUDE.md`, in the Optional environment variables list, after the `INFER_QUIET=1` line, add:
```
- `INFER_TOOL_CHOICE` — `required` (default) or `auto`; controls the `tool_choice` field sent in every request.
```

- [ ] **Step 7: Update CLAUDE.md native tool count and add computer_control**

In `CLAUDE.md`, replace:
```
- Defines **11 native tools** as OpenAI function schemas in `list-tools` (in schema order): `think`, `execute_command`, `web_search`, `fetch_webpage`, `read_file`, `write_file`, `edit_file`, `list_directory`, `save_memory`, `delegate_task`, `task_complete`.
```
with:
```
- Defines **12 native tools** as OpenAI function schemas in `list-tools` (in schema order): `think`, `execute_command`, `web_search`, `fetch_webpage`, `read_file`, `write_file`, `edit_file`, `list_directory`, `save_memory`, `delegate_task`, `computer_control`, `task_complete`.
```

- [ ] **Step 8: Add behavioral notes to CLAUDE.md**

At the end of the `ai.c` bullet list in the Architecture section, add:
```
- Detects `finish_reason: "length"` (model hit token limit) and injects a recovery nudge instead of rendering truncated output.
```

In the `ai_mcp.py` description, update the `edit_file` bullet to:
```
- `edit_file`: search-and-replace on an existing file. Falls back to a trailing-whitespace-tolerant fuzzy match if the exact string is not found.
```

- [ ] **Step 9: Compile to confirm no accidental C changes**

```bash
gcc -o ai ai.c cJSON.c -lcurl
```
Expected: clean compile.

- [ ] **Step 10: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: update README and CLAUDE.md — fix tool_choice claim, add INFER_TOOL_CHOICE, document computer_control and finish_reason:length recovery"
```

---

## Self-Review

**Spec coverage:**
- 1a (finish_reason stop + tool_calls) → Task 3 ✓
- 1b (args_tok as JSMN_OBJECT) → Task 4 ✓
- 1c (jsmn buffer) → Task 1 ✓
- 1d (VLA→heap) → Task 2 ✓
- 2a (INFER_TOOL_CHOICE) → Task 5 ✓
- 2b (finish_reason:length recovery) → Task 6 ✓
- 2c (edit_file fuzzy match) → Task 7 ✓
- Section 3 (README + CLAUDE.md) → Task 8 ✓

**Placeholder scan:** All steps have concrete code. No TBDs. ✓

**Type consistency:** `tool_choice_val` is `const char *`, used as `%s` in `snprintf` in both payload sites. `finish_reason_length` is `int`, declared and set before use. `unescaped_args` is `char *` in both branches. ✓

**Dependency check:** Task 6 uses `has_more = 1` to keep the loop alive; this only works correctly with `tool_choice_val = "required"` (Task 5) so the recovery turn forces a tool call rather than plain text. **Implement Task 5 before Task 6 if testing end-to-end.** The commit order already reflects this. ✓
