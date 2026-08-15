---
name: daily_digest
description: CRITICAL — when the user asks for a daily digest, morning briefing, "what's up today", or a status roundup: gather calendar, tasks, system health, recent sessions and new literature into one compact briefing.
---

# `daily_digest`

Builds a scientist's daily briefing from the tools already wired into this harness.
The goal is ONE compact message the user can read in 30 seconds, not a data dump.

## What to gather (parallelize; skip a section when it yields nothing)

1. **Today's calendar** — `gcal_list_events` for the next 24–48 h. Keep event title,
   time, location. Flag conflicts.
2. **Pending work** — `list_scheduled_tasks` (recurring/deferred tasks) and, if a
   `todo` list exists for this session, its open items.
3. **System health** — `get_system_status` (disk, memory, load). If disk is above
   ~80% or a background job is stuck, say so in one line with the fix
   (see `system_maintenance`).
4. **What was done recently** — `list_sessions(limit=5)`; summarize the last 1–2
   sessions in one line each ("yesterday: finished the BoltzGen campaign, 3 structures
   to review"). Use `get_session` only if a specific follow-up is needed.
5. **New literature** (opt-in: only if the user maintains research topics — check
   `search_history` for the user's recurring topics first). Run `arxiv_search` or
   `pubmed_search` for the last 24 h on those topics; keep at most 3 items,
   title + one-line why-it-matters. Never invent a topic the user never mentioned.

## Format rules

- Lead with the one thing that needs a decision today (if any).
- Sections in the order above, each 1–3 lines. Omit empty sections silently.
- End with a suggested next action.
- Cite sources for literature (arXiv/PubMed id). Do not pad.

## Pitfalls

- Do not schedule this yourself — if the user wants it every morning, offer
  `schedule_task` (see `scheduling_reminders`) and let them confirm the time.
- Calendar times: always show the user's local timezone; `gcal_*` returns UTC.
- If a section errors (e.g. calendar unreachable), note it in one line and continue
  — one dead source must not kill the digest.
