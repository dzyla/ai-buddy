---
name: search_history
description: Search past conversations and command histories for context, code snippets, or previously found solutions. Use this skill when the user asks "how did we do X last time?", or you need to recall an earlier session.
---

# `search_history`

Recalls what happened in earlier, closed sessions so you can learn from them instead of
re-solving problems from scratch. Every conversation is backed up locally, searchable by a
full-text index, and retrievable in full by session id.

## Storage locations

1. **Global append-only log:** `~/.cache/ai/history.jsonl` — every turn (prompt → response) across all sessions, each with a `session_id` and timestamp.
2. **Session backups (two copies for durability):**
   - `~/.cache/ai/sessions/<session_id>.json` (fast cache)
   - `~/.local/share/ai/sessions/<session_id>.json` (persistent local user data — survives cache clears)
3. **Full-text index:** `~/.local/share/ai/history_index.db` (SQLite FTS5), built automatically.

## Preferred workflow (use the tools, not raw grep)

1. **Broad search** — call `search_history(query="keywords")`. It returns the matching
   snippets plus the owning session IDs. (Uses the fast FTS index; rebuilds it automatically.)
2. **See recent work** — call `list_sessions(limit=10)` to list the most recent backed-up
   conversations and find a session id.
3. **Deep context** — call `get_session(session_id="sess_...")` to read the full prior
   conversation as a transcript before continuing or synthesising.
4. **Fine-grained** — if the tools are under-powered, `execute_command` a constrained grep:
   ```bash
   grep -h "keyword" ~/.cache/ai/history.jsonl | tail -40
   ```
   or read a specific session file directly.

If a search looks stale (returns nothing you expect), call `rebuild_history_index()` and retry.

## Rules for history retrieval

- **Protect secrets:** never echo API keys / passwords from history back to the user unless explicitly requested.
- **Context matters:** a solution from a week ago may not apply today if the project changed.
  Always verify current state before blindly re-applying old commands.
- **Synthesise:** don't dump raw JSON. Present the relevant commands, code, or conclusions cleanly, and cite the session you drew from.
- **Learn:** when you find or derive something reusable, persist it with `skill_create` /
  `skill_update` / `skill_note` so later sessions start from it.