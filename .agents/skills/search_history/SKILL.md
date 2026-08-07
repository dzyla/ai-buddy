---
name: search_history
description: Search past conversations and command histories for context, code snippets, or previously found solutions.
---

# `search_history`

This skill provides instructions on how to effectively search the agent's past conversation history. This is extremely useful when the user asks "how did we do X last time?", "what was that command?", or if you need to recall context from an earlier, closed session.

## Storage Locations

The `ai-buddy` agent stores its history in two primary locations:

1. **Global Append-Only Log:** `~/.cache/ai/history.jsonl`
   - Contains a structured, sequential log of ALL turns across all sessions.
   - Ideal for searching for specific shell commands, code snippets, or error messages.
2. **Detailed Session Files:** `~/.cache/ai/sessions/*.json`
   - Each session is saved as a complete JSON dump of the conversation context.
   - Ideal for reading an entire conversation in context once you have the session ID.

## Standard Search Workflow

### 1. Broad Search
Use `execute_command` with `grep` (or similar tools if available) to search the global append-only log for keywords.

```bash
grep -i "keyword" ~/.cache/ai/history.jsonl
```

### 2. Extracting Session IDs
If you find a relevant entry in `history.jsonl`, extract its `session_id`. (You can usually see the session ID in the metadata or by looking at the timestamp and then matching it to the session files).

### 3. Deep Context Retrieval
To view the full context of a past conversation, look at the corresponding session file:

```bash
cat ~/.cache/ai/sessions/<session_id>.json
```
*(Use python to format the JSON if it's hard to read: `python3 -m json.tool ~/.cache/ai/sessions/<session_id>.json | less`)*

## Rules for History Retrieval

- **Protect Secrets:** If you find API keys or passwords in the history, do NOT output them back to the user unless explicitly requested.
- **Context matters:** A command that worked a week ago might not work today if the project has changed. Always verify the current state before blindly re-applying old solutions.
- **Synthesize:** Don't just dump the raw JSON log to the user. Extract the relevant commands, code, or context and present it cleanly.
