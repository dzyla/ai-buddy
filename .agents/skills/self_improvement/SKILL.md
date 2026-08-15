---
name: self_improvement
description: CRITICAL — continuous learning. After completing tasks, persist what you learned (new techniques, corrections, good-to-know) into skills so future sessions inherit it. Use skill_create / skill_update / skill_note.
---

# Self-Improvement & Continuous Learning

You learn from the past. At the end of (or during) a task, persist insights into the
skill system so future sessions — including your own — start from today's discoveries.
This is how the harness improves over time.

## How the harness learns automatically (you don't have to opt in)
Every tool failure is recorded to `~/.config/ai/self_improve/ledger.jsonl`, linked to an
**error chain** — a stable id for that one mistake, however many times it recurs. When the
SAME failure signature is seen repeatedly, an auto-generated "recurring pitfall" (PITFALL)
lesson is persisted to `lessons.md`. When a tool that failed earlier in a task later
SUCCEEDS, the working approach is auto-learned as a FIX lesson in the SAME chain. Once a
chain has been recovered ≥ INFER_CHAIN_MASTERED times (default 2) and hasn't failed since,
it is promoted to a MASTER lesson. A new failure on a mastered chain de-masters it until
it is recovered again.

Two ways past learning reaches you:
1. **On error**: the harness injects matching lessons into the failing tool result as
   "[REMEMBERED FROM PAST SESSIONS (self-improvement)]".
2. **At session start**: a [SESSION RECAP] block is injected into your system prompt —
   mastered error chains (proven fixes), recent lessons, the recent sessions (with ids),
   and flaky tools. So even a small model that never volunteers to persist still inherits
   what past sessions learned and did.

Use the recap actively: when a MASTERED chain matches an error you're hitting, apply its
recorded approach FIRST. When the recap references a recent session you need in detail,
call `get_session(<id>)` (or `search_history`).

You still add value on top by using skill_create / skill_update / skill_note for
techniques the harness can't deduce (multi-step workflows, domain facts, tool quirks).

## When to persist
Do this after any non-trivial task where you learned something reusable:
- A technique, workflow, or non-obvious command that worked.
- A pitfall / footgun you hit (and the workaround).
- A skill you loaded that was WRONG or OUTDATED (fix it).
- New API endpoints, tool quirks, or environment facts specific to this repo/system.

## Which tool
- **New, self-contained technique** → `skill_create(name, description, content)`.
  - `name`: short, kebab-case (e.g. `pandas_url_encoding`).
  - `description`: one line stating WHEN to use it (first ~57 chars are the trigger).
  - `content`: numbered steps, exact commands, pitfalls, verification, and sources.
- **Correction / good-to-know on an existing skill** → `skill_update(name, note)`.
  Use when you loaded a skill and found it incomplete, wrong, or you can add a tip.
- **Minor / standalone insight** → `skill_note(name?, note)`. Low-commitment.

Both `skill_create` and `skill_update` write to the repo `.agents/skills` AND to
`~/.config/ai/skills`, so the skill is checked into the project and persists globally.
All writes are recorded in `~/.config/ai/skills_learning_log.md` (your memory of what
you've learned). The user is notified whenever a skill is created or updated.

## Before finishing a task
Prefer to persist BEFORE calling `task_complete`. In your task_complete summary, briefly
note any skills you created or updated so the user knows the agent improved itself.

## Good skill body template
```markdown
---
name: <skill>
description: Use when <trigger>. <one-line behaviour>.
---
# <skill>
## When to use
...
## Steps
1. ...
## Pitfalls
- ...
## Verification
- run: <cmd> ; expect: <result>
## Sources
- [Source: <url>]
```
Keep it actionable and concise. Prefer documented, verified facts over guesses.