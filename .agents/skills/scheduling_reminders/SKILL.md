---
name: scheduling_reminders
description: CRITICAL — when the user asks to remind, schedule, defer, or repeat something ("remind me at 3", "every morning", "check back later", "in an hour"): pick the right tool (set_reminder vs schedule_task vs gcal_*) and verify it landed.
---

# `scheduling_reminders`

Deferred work in this harness is done through tools, never through `sleep`.
Pick the right mechanism, then verify.

## Decision guide

- **"Remind me at <time> / in <duration> about X"** (one-shot, personal) →
  `set_reminder`. Pass an explicit time; do not guess "in a bit".
- **Recurring or task-like** ("every morning at 9", "check the job every hour") →
  `schedule_task` with a repeat. The task prompt must be SELF-CONTAINED: it runs in
  a fresh session with no chat context, so include paths, thresholds, and what "done"
  looks like.
- **"Add a meeting / event"** → `gcal_create_event` (or `gcal_quick_add` for a
  one-liner like "lunch with X tomorrow 1pm"). Use `gcal_check_availability` to
  find a slot; `gcal_update_event` / `gcal_delete_event` to change/cancel.
- **Poll a long-running job you just launched** → `schedule_task` that checks
  `check_process_status` for the pid and reports when it finishes. Prefer this to
  blocking in the current session.

## Always verify

After creating, call `list_scheduled_tasks` and confirm the entry is present with
the right time/repeat. A silent scheduling failure is the classic trap.

## Rules

- State-changing (create/update/delete): in manual/plan modes these are gated —
  present the exact reminder/event first if approval is required.
- Never run `sleep N` in a command to wait for a reminder; that locks the terminal.
- Timezones: the user's local zone unless they say otherwise; gcal events are
  stored UTC — convert before displaying.
- Recurring task prompts: write them so a cold session can execute them (absolute
  paths, explicit success criteria, "stop when Y").
