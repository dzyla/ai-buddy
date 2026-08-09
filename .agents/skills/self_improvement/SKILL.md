---
name: self_improvement
description: CRITICAL — continuous learning. After completing tasks, persist what you learned (new techniques, corrections, good-to-know) into skills so future sessions inherit it. Use skill_create / skill_update / skill_note.
---

# Self-Improvement & Continuous Learning

You learn from the past. At the end of (or during) a task, persist insights into the
skill system so future sessions — including your own — start from today's discoveries.
This is how the harness improves over time.

## How the harness learns automatically (you don't have to opt in)
Every tool failure is recorded to `~/.config/ai/self_improve/ledger.jsonl`. When the
SAME failure signature is seen repeatedly, an auto-generated "recurring pitfall"
lesson is persisted to `lessons.md`. When a tool that failed earlier in a task later
SUCCEEDS, the working approach is auto-learned as a FIX lesson. On any future error,
the harness injects matching past lessons into the tool result as
"[REMEMBERED FROM PAST SESSIONS (self-improvement)]". So even a small model that never
volunteers to persist still inherits what past sessions learned.

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