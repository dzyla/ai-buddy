---
name: core_tools_guide
description: CRITICAL — when starting a multi-step task, or when you need to track progress, ask the user something, search the codebase, or share context across agents: use todo / clarify / search_files / context-pool deliberately instead of ad-hoc terminal greps.
---

# `core_tools_guide`

The everyday harness tools. Using them deliberately (instead of raw `grep`/`ls`/
mental tracking) is what keeps a small local model oriented across a long task.

## `todo` — track multi-step work

For any task with 3+ steps, maintain a `todo` list so you always know where you are.
- **Read** (no args): `todo()` → shows the current list with status marks.
- **Create** (replace): `todo(todos=[{id, content, status}, ...])` — statuses:
  `pending | in_progress | completed | cancelled`.
- **Update** (merge): `todo(todos=[{id, status:"completed"}], merge=True)` — you may
  update just the status of an existing item by id; its content is kept.
- Mark items completed **immediately** when done — don't batch. Only ONE item
  `in_progress` at a time.
- Session-scoped: parallel/resumed sessions keep separate lists.

## `clarify` — ask when a wrong guess wastes work

Use when the task is ambiguous and proceeding the wrong way costs real effort.
- Provide 2–4 numbered options (the recommended one first) or an open-ended question.
- **Non-interactive runs** (bridges, schedulers, cron) never block: `clarify` returns
  a `[CLARIFY NON-INTERACTIVE]` note telling you to pick the first (recommended)
  option yourself, proceed, and state the assumption in your final summary. Never
  hang waiting for a human.
- Do not `clarify` for low-stakes decisions you can reasonably default on — ask the
  user, don't narrate the question.

## `search_files` — codebase search (prefer over raw grep)

- **Content regex:** `search_files(pattern="def main", path=".", target="content")`
  → `file:line:` matches, capped, skips build/`__pycache__`/`.git`.
- **Find by name/glob:** `search_files(pattern="*.py", target="files")` → most-
  recently-modified first.
- **Modes:** `output_mode` = `content` (default) | `files_only` | `count`;
  `file_glob="*.py"` filters which files; `context=N` shows ±N lines around a hit.
- A plain string that isn't a valid regex falls back to fixed-string search.
- Use `read_file` for a whole file; `search_files` to locate the spot first.

## Context pool + delegation — share across agents

- `append_to_context_pool(entry)` — drop a fact/decision into the shared pool.
- `get_context_snippet(index)` / `search_context(query)` — read it back.
- `spawn_agent(name, prompt)` / `resume_agent(agent_id, message)` /
  `list_agents()` — multi-agent workflows (see `subagents`).
- `delegate_task(tasks=[...])` — run N helpers in parallel, combined results back.
- `session_report(success, notes)` — record the outcome at the end of a session so
  future sessions' recap includes it.

## When NOT to use these

- One-shot single tool call → just call the tool; no `todo` needed.
- You can see the answer in the current file you're already reading → no
  `search_files` round-trip.
- A decision you can default on sensibly → decide, don't `clarify`.
