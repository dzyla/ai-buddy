# Agent Harness Review — Why Local Agents "Go Forever" and How to Fix It

Investigation of `ai-buddy` (a.k.a. the `ai` harness): `ai.c` agent loop,
`ai_mcp.py` tool backend, `zulip_ai_bridge.py`, and the `.agents/skills/`
that shape behavior. App works great, but the agent is biased *hard* toward
autonomous execution and has almost no "investigate → plan → ask" gate. This
doc explains the root causes and proposes concrete changes.

---

## 1. Root causes: why the agent just goes and fixes things

### 1.1 The system prompt is explicitly "fully autonomous"

`ai.c:900` SYSTEM_PROMPT opens with:

> "You are a **fully autonomous CLI agent**... Once all requested operations
> succeed and are empirically verified, call task_complete."

And repeats it throughout:

- "NEVER describe what the user can do themselves. If a tool can get the
  answer, use it."
- "Never tell the user to 'visit a link' or 'run a command themselves' — do it
  yourself."
- "If a library is missing, install it non-interactively"
- "At least 3 attempts before giving up"
- "Always employ a multi-turn review process: generate a draft, verify it
  against memory maps... **before delivering the final output**."

There is literally **no instruction anywhere** telling the model to stop
after investigating, present a plan, or ask the user for permission to make
changes. The default posture is: run the loop until the user's original ask is
"done," doing whatever the model decides along the way.

### 1.2 The permission modes barely constrain anything

`g_permission_mode` has 3 states: `0=auto`, `1=plan`, `2=manual`
(`ai.c:514`). But look at what they actually control:

- **Only `execute_command` is gated.** The approval prompt (`ai.c:5436`) is
  reached ONLY in the `execute_command` handler, and only when
  `g_permission_mode != 0`. `approved = (g_permission_mode == 0)`.
- **`write_file`, `edit_file`, and every MCP tool are NEVER gated.** They
  dispatch straight through (`ai.c:5699`) to `ai_mcp.py`. So the agent can
  rewrite files, edit code, save memory, vault-write, schedule tasks,
  delegate subagents, spawn background processes — all with zero confirmation,
  regardless of mode.
- The three modes map to "don't ask / ask only for shell / ask for shell too,"
  but "ask" still means a Y/n prompt on the *next* `execute_command`, not a
  plan-level checkpoint.

Net effect: even in "plan" mode the only prompt you'll ever see is "confirm
`$ command`" on a shell invocation. File edits and all other mutations sail
through untouched.

### 1.3 The loop has no natural stopping discipline

`ai.c:4632`:

```c
int step_limit = (g_continue_until_done || g_permission_mode || !isatty(STDIN_FILENO))
                 ? 999999 : 30;
```

- Interactive + auto (default TTY): 30 steps.
- `-c/--continue`, any non-auto permission mode, or **any piped/non-TTY stdin
  (i.e. every Zulip bridge call and every scripted invocation): 999999 steps.**

So the moment the agent is invoked non-interactively — exactly the "task me
with something" scenario — the turn cap becomes effectively infinite. The
only brakes that remain are `INFER_TASK_TIMEOUT` (default 600s via the bridge)
and a repeated-same-tool-call detector (`ai.c:5060`), but those don't stop a
model that is *successfully* changing files/commands one after another.

Also note `ai.c:5590-5591`: every successful `execute_command` triggers
`git_commit("command")` automatically. So it doesn't just change files — it
**commits them** on its own, reinforcing "go and implement, and show what I
did" rather than proposing first.

### 1.4 `think` is capped at one call, so real planning can't happen

`ai.c:5064-5065`:

```c
if (think_count > 1) {
    tool_output = strdup("Error: You have already called the 'think' tool
        once to plan... Calling 'think' repeatedly causes infinite loops.");
}
```

So after a single `think`, the harness *actively blocks* the model from
reasoning/planning again for the rest of the task. That's the exact opposite
of "investigate, check, learn, adjust." Once the budget's spent, the model's
only forward path is to call action tools or `task_complete`. This is a major
structural driver of "just go and do it."

### 1.5 The skills are written in the same autonomous voice

- `autonomous_troubleshooting/SKILL.md`: "You must solve tasks completely
  **without asking the user to run commands, write code, or execute scripts**
  for you."
- `small-model-harness/SKILL.md §5`: "Do not ask follow-up questions or
  perform unnecessary verification steps unless explicitly requested."
- `planning/SKILL.md` tells it to "execute exactly one step at a time" but
  never to report back or hand a checkpoint to the user before mutating.

The skills reinforce the loop: plan silently, execute continuously,
`task_complete` at the end.

### 1.6 No tool exists to present a plan or request approval

Scanning the tool catalog in `ai_mcp.py` (list-tools): there is `think`, all
the action tools, `task_complete`... but **no `propose</think>_plan`, no
`request_approval`, no `report_findings`**. The model physically has no way to
say "here's what I found, here's my plan — may I proceed?" to the user
mid-run. Combined with 1.1–1.5, it just... continues.

---

## 2. Proposed changes (in order of impact)

### A. Rebalance the system prompt (highest-leverage, cheapest)

Rewrite `SYSTEM_PROMPT` in `ai.c` to add a mandatory **Investigate → Plan →
Present → Execute** discipline:

- Change the opening from "fully autonomous CLI agent" to an **"agentic
  assistant that investigates first and asks before mutating"**.
- Add an explicit rule: **at least one `think`-backed investigation phase
  before ANY mutation**; surface findings and a concrete plan (files, exact
  edits, commands) **before** the first `write_file`/`edit_file`/`execute_command`
  that changes state.
- Replace "install it non-interactively" / "do it yourself" with "propose the
  install/changes and ask."
- Keep the powerful skill-loading, fetch, and research abilities — the goal is
  *checkpoint-before-execute*, not crippling autonomy.

### B. Remove the `think` single-call cap (or raise it per-phase)

Change the `think` handler at `ai.c:5064` so the model can reason across
phases. Either:
- allow an unlimited number of `think` calls (simplest; it's cheap and the
  same-tool detector already prevents pathological loops), or
- raise the cap to e.g. one `think` per investigation/execution **phase**,
  tracked separately.

The current "one and done" rule is the single clearest structural blocker to
iterative investigate/learn/adjust.

### C. Gate mutations, not just shell commands

Introduce a real permission boundary that separates **read/investigate tools**
(always allowed: read_file, list_directory, web_search, fetch, think, etc.)
from **mutating tools** (require approval: write_file, edit_file,
execute_command, save_memory, vault_write, schedule_task, delegate_task that
mutates, git-blessed changes).

In `ai.c` / `ai_mcp.py`, add an approval checkpoint before dispatching any
member of the mutating set — analogous to the existing `execute_command` gate — 
that:
1. presents the intended change (path + a diff/summary),
2. blocks until yes/no,
3. **does not** silently retry or proceed on `n`.

This gives the user a real "I'm about to change X — ok?" that works for file
edits, not just shells.

### D. Make the permission modes pan the whole toolchain, not just shell

Today the 3 modes only affect shell. Redefine so that **mode is the gate**:
- `auto` → all mutations auto-approved (for trusted/harnessed runs).
- `plan` → agent must investigate & present a plan; mutations ask for approval
  (the new default for interactive + Zulip).
- `manual` → every tool call (read included) is individually confirmed.

And crucially, wire the prompt so a non-auto mode is reflected in the
*messages* the model sees ("you are in plan mode — present a plan and await
approval before mutating"), not just a flag some C branch reads.

### E. Fix the infinite step budget for non-interactive runs

`ai.c:4632`: a non-TTY run should **not** automatically get 999999 steps.
Make non-interactive default to a finite, generous cap (e.g. 40–60 steps),
and reserve `999999` for an explicit `-c/--continue` **or** a real
`INFER_TASK_TIMEOUT` that the caller knowingly sets. The Zulip bridge
("give the bot a job") should itself pass a sane step cap.

### F. Don't auto-commit behind the user's back

`ai.c:5590` auto-commits on every successful command. Add a `--no-commit`
default for non-interactive / plan mode, or move git staging/committing into
the approved-mutation gate. "Show what you did" should be a *proposal of
changes* (diff), not an unrequested commit history.

### G. Add a "report findings / request approval" affordance

Give the model a real way to pause for the user:
- A `present_plan(plan, changes[])` tool that surfaces the plan in the TUI /
  Zulip and **blocks the loop** until the user approves/denies. In interactive
  TUI this is a prompt; in Zulip, the bridge holds the run and asks.
- This is the missing coupling: right now there's no mechanism for
  "investigate, show discoveries, ask." A dedicated tool makes it the model's
  *natural* next call instead of a prompt-contradiction.

### H. Update the skill files to the new discipline

Rewrite the three most actively-loaded skills so their voice matches the new
behavior:
- `autonomous_troubleshooting/SKILL.md` — change "without asking the user" to
  "investigate and diagnose; when a fix requires mutating the system or repo,
  present the diagnosis and proposed fix, then ask."
- `small-model-harness/SKILL.md §5` — replace "Do not ask follow-up questions"
  with "after investigation, present findings + plan and await go-ahead before
  mutating." Keep the excellent plan-one-step-at-a-time and doc-search advice.
- `planning/SKILL.md` — add an explicit checkpoint: "call
  present_plan/report before the first mutation; wait for approval."

### I. Optionally add a harnessed "consult" mode for Zulip

Since the Zulip bridge is the primary throttle-less path, consider a bridge
flag (e.g. `AI_PLAN_MODE=1`) that makes the bot default to plan mode so
"investigate, show discoveries, ask" is the *expected* behavior for messages
through chat, while keeping full-auto available when explicitly requested.

---

## 3. Priorities

| Priority | Change | Effort | Impact |
|---|---|---|---|
| P0 | B — remove `think` cap | small | unblocks investigate/learn/adjust |
| P0 | A — rebalance system prompt | small | unblocks plan-then-do postures |
| P0 | C — gate all mutations (esp. write/edit) | medium | real "ask before changing" |
| P1 | D — modes gate whole toolchain | medium | consistent permission model |
| P1 | E — finite non-TTY step cap | small | stops "go forever" |
| P1 | F — stop hidden auto-commit | small | no unrequested commits |
| P1 | G — present_plan/report tool | medium | model can explicitly ask |
| P2 | H — skill files | small | aligns loaded-skill voice with new rules |
| P2 | I — Zulip plan-mode default | small | bridge becomes investigate-first |

## 4. Files to touch

- `ai.c` — SYSTEM_PROMPT (A), `think` handler (B), mutation gate (C/D),
  step budget (E), auto-commit (F), present_plan handling (G).
- `ai_mcp.py` — register `present_plan`/`report_findings` tool + approve/deny
  plumbing (G).
- `zulip_ai_bridge.py` — hold runs awaiting approval, plan-mode default (G,I).
- `.agents/skills/{planning,autonomous_troubleshooting,small-model-harness}/SKILL.md` (H).
- `tests/` — new tests for: mutation gating, think reuse, non-TTY step cap,
  approval round-trip through the bridge.