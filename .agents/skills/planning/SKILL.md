---
name: planning
description: CRITICAL — Master Planner: plan → get approval → execute step-by-step via subagents → validate, in small bounded steps.
---

# Master Planner Guidelines

When confronted with complex or multi-step requests, adopt the role of a Master
Planner and respect the CURRENT PERMISSION MODE (injected in your system context).

## 0. Mode-aware protocols (override older advice below when they conflict)
- **FULL AUTONOMY**: investigate, plan mentally, execute step by step, verify each
  step, then `task_complete`.
- **PLAN mode**: investigate and READ freely, but DO NOT change anything. When you are
  ready to act, call `present_plan(plan="...")` with your findings, the ordered list of
  EXACT changes (files + edits / commands / steps), and rationale. Wait for the user's
  approval. Once approved you get a **BOUNDED** number of state-changing actions
  (`INFER_PLAN_STEP_BUDGET`, default 8). When the budget is exhausted the harness blocks
  further changes — call `present_plan` with the next chunk and wait for a new approval.
  Do NOT try to do the whole task in one approval.
- **MANUAL mode**: read/investigate freely; obtain explicit approval for every
  state-changing action. If an action is denied, adjust your approach — do not retry
  the same action until permitted.

## 1. Plan → Approve → Step-by-step execution (Claude-Code-like)
- **Decompose first**: break the objective into a strict, numbered, sequential list of
  discrete steps. Note dependencies (if step B needs step A, step A must include a
  validation check).
- **Investigate before you propose**: use read-only tools to understand the code/state
  first. Do not propose edits you have not grounded in the actual files.
- **Present a concrete plan**: in PLAN mode, call `present_plan` listing each step with
  its exact files/commands. Wait.
- **Execute ONE step at a time**: after approval, do one step, read its tool result,
  and validate it (compile, run the test, read the output) before starting the next.
  Never batch multiple unrelated changes into a single turn.
- **Re-present between chunks**: when the approved step budget runs out (the harness
  will tell you), present an updated plan for the next steps and wait again. Do not
  keep going past the budget.
- **Use `think`** to write down your plan before starting, and between phases to
  reflect/adjust. Repeated `think` calls are allowed.

## 2. Orchestrating Subagents (validate each step)
- For steps that are independent or that need their own tool loop, delegate the step to
  a subagent with `delegate_task` / `spawn_agent`. Give each subagent ONE precise,
  self-contained step (a single file to fix, a single test to run, a single search).
- Put a hard "do not scope-creep" instruction in each sub-task: tell it the exact
  deliverable and to stop when done.
- **You validate the subagent's output**: after a subagent returns, check its result
  (git diff, test output, file contents) before proceeding. The subagent is not the
  authority on "done" — you are, and ultimately the user is.
- Do NOT delegate the whole plan to one subagent; keep the plan lifecycle in YOUR loop
  so the user can interrupt between steps.

## 3. Long Context Management
- **Avoid Context Bloat**: never `cat` or read massive files in full if you only need a
  section. Use grep/search tools.
- **Save State**: use `save_memory` to persist key findings/conclusions.
- **Summarize**: after a major phase, summarize the current state explicitly.

## 4. Execution Discipline
- Execute exactly one step of your plan at a time.
- After a tool call returns, check it against your plan. If it failed, reflect on the
  error (`think`), consult documentation via `web_search`, and try an alternative.
- Once all steps are complete and validated, call `task_complete`.

## 5. Self-improvement
- After completing a non-trivial task, if you discovered a reusable technique, a
  workaround, a pitfall, or a wrong instruction, persist it:
  - New technique → `skill_create(name, description, content)`.
  - Correction / good-to-know on a skill you already loaded → `skill_update(name, note)`.
  - Minor or standalone insight → `skill_note(note=...)`.
- The user is notified automatically when a skill is created/updated.