# ai-buddy Improvement Roadmap

## Architecture Overview

```
User → ai.c (CLI, API caller, agent loop) → OpenAI-compatible API
                                      ↓
                              ai_mcp.py (tool implementations)
                                      ↓
                              OS shell, files, web, git, etc.
```

- **ai.c**: ~4,566 lines — argument parsing, SSE streaming, agent loop, interactive mode, session management, context trimming/compaction
- **ai_mcp.py**: ~1,400 lines — JSON-RPC tool server exposing ~50 tools
- System prompt from `~/.config/ai/system_prompt.txt` or compiled-in template

---

## Current Agent Loop (the bottleneck)

```
ask model → execute tools → feed results back → repeat
```

This works for large models (GPT-4, Claude) but breaks with smaller ones:
- Small models don't plan — they jump straight to tool calls
- Small models don't recover from errors — they repeat the same failed tool call
- Small models lose track of goals — they forget what they're trying to accomplish

---

## Priority 0: Critical Fixes (bugs / crashes)

### [P0] Fix JSON escaping in system_prompt (`ai.c` ~line 2744)

```c
snprintf(system_prompt_json, plen, "{\"content\":\"%s\"}", system_prompt);
```

If `system_prompt` contains quotes, backslashes, or newlines, the JSON is malformed and the API call fails silently or returns garbage.

**Fix**: Use `escape_json_string()` (already defined in the file) before embedding in JSON.

---

### [P0] Fix JSON escaping in user_content (`ai.c` ~lines 3249–3289)

```c
sprintf(user_content, "%s\n\nContext (output of command `%s`):\n%s", safe_prompt, safe_writer, safe_pipe);
```

The format string uses `%s` with user-controlled input. If any input contains `%` characters, this is a format-string vulnerability. Even without that, unescaped quotes/backslashes break the JSON payload.

**Fix**: Use a format-safe approach — build strings via concatenation of pre-escaped segments, never `%s`-format raw input into JSON.

---

### [P0] Add try/except around all tool calls in `ai_mcp.py`

The `call_tool` function currently executes tools with no error wrapping. If a tool raises an exception, the raw traceback ends up in the JSON-RPC response, which `ai.c` doesn't parse well and the agent loop can get stuck.

**Fix**: Wrap every tool call in:
```python
try:
    result = tool_func(*args, **kwargs)
except Exception as e:
    result = f"Error executing {tool_name}: {type(e).__name__}: {str(e)}"
```

---

### [P0] Fix `load_from_profiles` uninitialized pointer usage (`ai.c` ~line 2963)

```c
char *prof_url = NULL;
char *prof_key = NULL;
char *prof_model = NULL;
load_from_profiles(&prof_url, &prof_key, &prof_model);
// ... later:
if (prof_url && strlen(prof_url) > 0) { ... }
```

If the profile file doesn't exist or is malformed, the pointers stay NULL. The `strlen(prof_url)` check is safe, but if `load_from_profiles` fails to allocate memory (OOM, etc.), the pointers could be uninitialized garbage. Verify that `load_from_profiles` always sets all three pointers to NULL on failure.

---

## Priority 1: High-Impact Missing Features

### [P1] No conversation state machine

The agent is treated as stateless — a flat message array with no tracking of *what stage* it's in.

**What's missing:**
- `planning` → `executing` → `verifying` → `complete` states
- Current subtask being worked on
- What has been attempted and failed

**Why it matters:** Small models get confused mid-task and don't know whether to keep trying the same approach or pivot.

**Implementation:**
- Add a C struct `agent_state` with fields: `state`, `current_task`, `attempted_actions[]`, `failed_actions[]`
- Before each agent iteration, inject the current state into the system prompt
- Update state after each tool call
- When transitioning from `executing` → `verifying`, add a "review" step

---

### [P1] No structured goal decomposition

The prompt says "decompose into subtasks" but there's no mechanism to enforce it. The model just gets told to do it.

**What's missing:**
- Accept a top-level goal at session start
- Maintain a visible task list (JSON)
- Mark tasks as done/failed/active
- Only advance to the next task when the current one succeeds

**Implementation:**
- Add `--goal <string>` CLI flag
- Parse into JSON: `{"goal": "...", "subtasks": [{"id": 1, "description": "...", "status": "pending"}]}`
- Store in `messages_json` as a special system message that's never trimmed
- After each tool result, update the subtask status
- If a subtask fails twice, auto-mark it as failed and prompt the model to work around it

---

### [P1] Tool result caching / deduplication

If the model calls `read_file("foo.txt")` twice, both results go into context. This wastes tokens and confuses the model.

**What's missing:**
- Cache tool results by `(tool_name, args_hash)`
- Return "already executed, same args, result unchanged" instead of re-running
- Critical for `web_search`, `fetch_webpage`, `read_file`

**Implementation:**
- Add a hash map in C: `typedef struct { char *key; char *value; struct hash_entry *next; } tool_cache;`
- Before executing a tool, check the cache
- If hit, return cached result (still append to messages so model sees it)
- If miss, execute, cache, return
- Eviction: LRU with max 100 entries

---

### [P1] Smart context trimming (sliding window + summarization)

Current trimming is brute-force: "if context > threshold, send all messages to the model and ask it to summarize." This is fragile:
- The model often ignores the instruction
- Summarization loses structured data (file contents, command outputs)
- No tiered approach

**What's missing:**
- Tiered trimming: recent messages preserved, old tool results summarized, very old messages dropped
- Sliding window of last N messages
- Summarize older tool calls into bullet-point summaries

**Implementation:**
- Define tiers: `Tier 0` = last 3 messages (never trim), `Tier 1` = recent tool results (summarize if > 2KB), `Tier 2` = old messages (drop entirely)
- Before trimming, classify each message into a tier
- For Tier 1, replace with a 2-3 line summary of the tool call + key output
- For Tier 2, drop entirely but log what was dropped

---

### [P1] Error recovery taxonomy

When a tool fails, the harness just appends "Error: ..." and hopes the model recovers.

**What's missing:**
- **Transient** (network timeout, rate limit) → retry with exponential backoff (no model involvement)
- **Permanent** (file not found, permission denied) → try alternative approach, log to model
- **Semantic** (model gave bad args) → reformulate and retry with corrected args

**Implementation:**
- After each tool call, classify the error:
  ```c
  if (strstr(output, "timed out") || strstr(output, "connection refused")) {
      // transient — retry 3 times with backoff before telling model
  } else if (strstr(output, "No such file") || strstr(output, "Permission denied")) {
      // permanent — log to model with suggestion
  } else {
      // semantic — model needs to fix its approach
  }
  ```
- Transient errors: auto-retry up to 3 times before surfacing to model
- Permanent errors: append a "suggested fix" hint to the tool output
- Semantic errors: add a "tool schema reminder" to the next model prompt

---

## Priority 2: Medium-Impact Architectural Improvements

### [P2] Model routing / fallback chain

If the primary model fails (network, quota, etc.), there's a crude fallback to a profile default.

**What's missing:**
- Automatic downgrade to a smaller/cheaper model
- Task-type routing (simple questions → small model, complex coding → large model)
- Cost tracking against a budget

**Implementation:**
- Add `--fallback-model` and `--fallback-url` CLI flags
- On API failure, try fallback before giving up
- Add cost tracking: count tokens per session, warn at 80% of budget
- Future: classify task complexity and auto-select model

---

### [P2] Structured memory system

The `save_memory` / `recall` tools write to a flat text file with no structure.

**What's missing:**
- Vector search for relevant memories
- Memory relevance scoring (recency + importance)
- Memory lifecycle (prune old/irrelevant memories)
- Structured metadata (when saved, what task it relates to)

**Current implementation:**
```python
def save_memory(content):
    filepath = os.path.expanduser("~/.config/ai/memory.txt")
    with open(filepath, "a") as f:
        f.write(f"{datetime.now().isoformat()} | {content}\n")
    return f"Memory saved: {content}"
```

**Implementation:**
- Replace flat file with SQLite (already used for vault)
- Add embeddings column (use OpenAI `text-embedding-3-small` or local sentence-transformers)
- Add metadata: `created_at`, `task_id`, `importance` (1-5)
- `recall` query: embed query → nearest-neighbor search → return top-K memories
- Auto-prune: delete memories older than 30 days with importance < 2

---

### [P2] Tool schema validation

Tools accept raw JSON arguments with no validation. If the model passes `{"path": 123}` instead of a string, the tool crashes.

**What's missing:**
- Define JSON Schema for each tool
- Validate arguments before execution
- Return structured validation errors the model can fix

**Implementation:**
- Add a `tool_schemas.json` file with schemas for all tools
- In `call_tool`, load schema and validate with `jsonschema` library
- On validation error, return: `"Error: invalid arguments. Expected: {schema}. Got: {actual}"`
- This gives the model actionable feedback to fix its call

---

## Priority 3: Security & Observability

### [P3] Sandboxing for `execute_command`

The tool runs any shell command with the user's full permissions.

**What's missing:**
- Command allowlist/denylist
- Dry-run mode (show what would run without executing)
- Resource limits (CPU, memory, time)
- Working directory isolation

**Implementation:**
- Add `INFER_COMMAND_DENYLIST` env var (comma-separated: `rm -rf /,mkfs,dd if=`)
- Add `INFER_COMMAND_ALLOWLIST` env var (if set, only these commands allowed)
- Add `--dry-run` CLI flag for preview
- Use `prlimit` or `cgroups` for resource limits (future)
- Set `HOME` and `PATH` to controlled values

---

### [P3] Observability / metrics collection

There's no way to measure:
- How many tool calls per task
- Average time per tool
- Failure rate by tool
- Token consumption per session
- Model response quality

**What's missing:**
- Log every tool call with timestamp, duration, success/failure
- Track token usage per session
- Export metrics to a file or external service

**Implementation:**
- Add `metrics.jsonl` log file:
  ```json
  {"timestamp": "2026-08-05T22:30:00", "tool": "read_file", "args": "ai.c", "duration_ms": 45, "success": true, "output_bytes": 12345}
  ```
- Add `--metrics` CLI flag to enable
- Add `ai metrics` subcommand to read and display stats

---

### [P3] Fix process signal handling (SIGINT/SIGTERM)

The agent loop catches ESC but not process signals. If the user Ctrl+C's the process, it may leak curl handles or leave `/dev/tty` in raw mode.

**Implementation:**
- Register signal handlers for `SIGINT` and `SIGTERM`
- On signal: restore tty mode, close curl handles, clean up temp files
- Use `sigaction` with `SA_RESTART` for graceful shutdown

---

## Strategic Recommendation: The Planning Phase

**The single biggest improvement** to reliability across all model sizes: introduce an explicit **planning phase** before the tool loop.

### Current flow:
```
ask model → execute tools → feed results back → repeat
```

### Proposed flow:
```
1. PLANNING: Ask model to output structured plan (JSON list of subtasks)
2. EXECUTING: Execute subtasks one at a time, updating a visible "mission board"
3. VERIFICATION: After each subtask, check if it succeeded
4. COMPLETE: When all subtasks done, output final result
```

### Why this matters:
- Small models don't plan — they jump straight to tool calls without thinking
- Small models don't recover from errors — they repeat the same failed tool call
- Small models lose track of goals — they forget what they're trying to accomplish

### Implementation:
1. Add `--goal <string>` CLI flag
2. Parse into JSON:
   ```json
   {
     "goal": "Fix the bug in ai.c",
     "subtasks": [
       {"id": 1, "description": "Read ai.c", "status": "pending"},
       {"id": 2, "description": "Identify the bug", "status": "pending"},
       {"id": 3, "description": "Fix the bug", "status": "pending"}
     ]
   }
   ```
3. Store as a persistent system message (never trimmed)
4. After each tool result, update the subtask status
5. If a subtask fails twice, auto-mark it as failed and prompt the model to work around it
6. When all subtasks are done, call `task_complete` with a summary

### Expected impact:
- **50-70% reduction** in failed agent loops for small/medium models
- **30% reduction** in token usage (no redundant tool calls)
- **Faster task completion** (model doesn't wander)

---

## Quick Wins (Do First)

1. **Fix JSON escaping** in `system_prompt` and `user_content` — prevents crashes with special characters
2. **Add try/except** around all tool calls in `ai_mcp.py` — prevents raw tracebacks from breaking the loop
3. **Add tool caching** — saves tokens on redundant `read_file` and `web_search` calls
4. **Add mission board** — the planning phase — dramatically improves small model reliability

---

## File Map

| File | Lines | Purpose |
|------|-------|---------|
| `ai.c` | ~4,566 | CLI, API caller, agent loop, interactive mode, session management |
| `ai_mcp.py` | ~1,400 | JSON-RPC tool server (~50 tools) |
| `Makefile` | ~50 | Build system (compiles ai.c to `ai` binary) |
| `README.md` | ~100 | Project overview |

---

## References

- [Current agent loop implementation](ai.c#L3550-L4566)
- [Tool server implementation](ai_mcp.py#L100-L500)
- [Memory system](ai_mcp.py#L200-L300)
- [Context trimming](ai.c#L1500-L2000)
- [Interactive mode](ai.c#L3350-L3550)
