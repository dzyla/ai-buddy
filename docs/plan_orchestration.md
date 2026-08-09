# Reliable Plan Orchestration (Claude-Code-like) for the `ai` Harness

## Goal

Make the harness stop "going and modifying stuff for many minutes" in PLAN mode and
instead behave like Claude Code's plan mode: **investigate → present a concrete plan →
get approval → execute step-by-step, validated by the main agent, with checkpoints**
— all while being reliable on small (32–35B) LLMs.

This doc records what was changed, how the pieces fit, and the recommended follow-up
work (some of it intentionally left as a spec rather than autonomous edits to a
harness the user is wary of).

## Why it currently fails (root causes, verified in the code)

Two structural causes combine to produce the bad behavior, plus a persuasive-prompt
problem:

1. **One approval unlocked the whole task.** `present_plan` approval set a single flag
   (`g_plan_approved=1`) that stayed set until "the next question", letting the agent
   perform an unbounded number of mutating actions after a single `y`. Exactly the
   "approve a small plan, watch it run for minutes and break things" experience.

2. **Non-interactive (piped / bridge / scripted) auto runs had an infinite step
   budget.** `ai.c` set `step_limit = 999999` for `auto + !isatty(stdin)`. Any
   "task me with X" invocation had effectively no brake.

3. **The system prompt and skills are written in a loud autonomous voice**
   ("fully autonomous CLI agent", "do it yourself", "install it non-interactively",
   "without asking the user"). Small models follow the loudest instruction they see;
   the gating only works if the model *chooses* to call `present_plan` first.

## What was changed (implemented + tested)

### 1. Plan approvals are now bounded: `INFER_PLAN_STEP_BUDGET` (default 8)
- `plan_budget_consume()` discounts one credit per executed mutation
  (`execute_command` and mutating MCP tools) while a plan is approved.
- When the budget hits zero the harness clears the approval and injects a
  `[PLAN BUDGET EXHAUSTED]` note into the tool result telling the model to
  `present_plan` again (or `task_complete`). The *next* mutation is then blocked by the
  normal plan gate, forcing a checkpoint instead of one long unattended run.
- `0` disables the budget (restores old one-approval behavior) for users who want it.

### 2. Non-interactive runs are finite: `INFER_STEP_LIMIT` (default 60)
- `auto + non-tty` no longer gets `999999`; it defaults to a finite cap you can raise.
- `--continue` still gets unlimited steps (explicit opt-in remains).

### 3. The persuasive layer now teaches the discipline
- System prompt opening reframed to be mode-aware while keeping the
  `fully autonomous CLI agent` marker (a regression test depends on it).
- Added a **CHECKPOINT & VALIDATE** section (do one step, validate it, re-present when
  the budget is spent). Softened "install it non-interactively" / "do it yourself" to
  propose-first in PLAN/MANUAL.
- `present_plan` tool description + the injected PLAN-mode context both now state the
  bounded-budget protocol and step-by-step validation.
- `planning` and `small-model-harness` skills rewritten to the
  plan → approve → step → validate → re-present discipline.

### 4. Gating hardening
- Added missing mutating tools to the C allowlist (`spawn_agent`, `resume_agent`,
  `append_context_pool`) so agent-spawning can't slip past plan/manual approval.
- **Deny-by-default**: in PLAN/MANUAL the MCP gate now treats any tool that is
  **not** on an explicit read-only allowlist (`tool_is_readonly`) as state-changing,
  so unknown/new MCP tools are gated too — not just the known mutators. Read-only
  investigation (`read_file`, `web_search`, `think`, …) stays free.
- `INFER_PLAN_AUTOAPPROVE=1` opt-in lets trusted/harnessed/bridge runs auto-approve
  `present_plan` in non-interactive mode (never on by default). This also makes the
  budget enforceable/testable offline.

### 5. Silenced the spurious "committed / nothing to commit" message
`git_commit()` in `ai_git.c` had inverted logic: it committed whenever there were
**no** staged changes, so it ran an empty `git commit` whose "nothing to commit,
working tree clean" stderr leaked into the model's view on every task. It now returns
silently when nothing actually changed and prints `[ai] committed: <what>` **once**
only when a real commit happens.

### Tests added (`tests/test_modes_history.py`, `tests/mock_llm_server.py`)
- `MOCK_TOOL_SEQ` lets tests drive a deterministic tool sequence offline.
- non-tty auto uses `INFER_STEP_LIMIT`;
- system prompt advertises the plan budget;
- **budget test**: with `INFER_PLAN_STEP_BUDGET=2`, driving
  `present_plan → 2× write_file → present_plan → write_file → task_complete` succeeds
  (all three files written) and the capture shows two `present_plan` calls — proving a
  re-approval was forced between chunks.

Run `make test` (one pre-existing `test_offline.py::test_hide_details_flag` timeout is
unrelated — it fails on pristine `main` as well).

## The target architecture (planner / executor / verifier) — recommended next steps

The native gating above is the *safety net*. The *workflow* that makes plans actually
reliable is an orchestration pattern the main agent runs. Recommended, in order:

### A. Planner (main loop runs this role)
In PLAN mode the main agent: investigate read-only → emit an ordered, numbered step
list → `present_plan(steps=[...])` → wait. Dependencies between steps are declared up
front, and each step carries its own validation check (compile / test / read output).

### B. Executor (subagents do the work)
Assign **one step per sub-task** and pass a hard "no scope-creep / stop when the single
deliverable is done" instruction. Reuse `delegate_task` / `spawn_agent`, which already
exist in `ai_mcp.py`. A subagent gets its own context window (better context hygiene on
small models) and a bounded mandate.

### C. Verifier (main agent validates every step result)
After each subagent returns, the main loop checks the actual artifact (`git diff`,
test output, file contents) before starting the next step. **The subagent is not the
authority on "done"; the main loop (and ultimately the user) is.** If a step fails
validation, the main loop stops, re-plans that step, and re-presents — it does not
barrel on.

The two skills (`planning`, `small-model-harness`) already instruct this pattern; it is
driven by prompting because the harness's native step budget already enforces the
periodic checkpoint the pattern needs.

### Recommended follow-ups (remaining)
1. **Bridge approval round-trip.** `zulip_ai_bridge.py` currently delivers plan-mode
   approval by reporting the plan and stopping (no `/dev/tty`). Add a held-run mailbox:
   the bridge posts the plan, waits for the owner's reply (`approve`/`reject`/`revise`),
   then resumes the run — making chat a first-class approval surface. Pair with
   `AI_PLAN_MODE=1` so the bot defaults to investigate-first.
2. **Per-step approval (opt-in `INFER_PLAN_STEP_BUDGET=1`)** for the strictest
   Claude-Code-like cadence: the agent presents, does exactly one step, and re-presents.
3. **Native orchestrator action** (larger): a `run_plan(plan_json)` client-side driver
   in `ai_mcp.py` that spawns one subagent per approved step and pipes each result back
   to the main loop for validation, so the pattern is enforced by code rather than only
   by prompt. This is the biggest change; prototype the prompt-driven version first.

## Files touched
- `ai.c` — step limit, plan budget, auto-approve, deny-by-default gating, read-only
  allowlist, system prompt, PLAN-mode injected context.
- `ai_git.c` — `git_commit()` only commits (and reports) when something actually
  changed; no more empty/spurious commits.
- `ai_mcp.py` — `present_plan` description.
- `.agents/skills/{planning,small-model-harness}/SKILL.md`.
- `tests/mock_llm_server.py`, `tests/test_modes_history.py`.
- `README.md`, `AGENTS.md`.