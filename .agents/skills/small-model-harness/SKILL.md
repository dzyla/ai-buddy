---
name: small-model-harness
description: Best practices and agent loop guidelines specifically tuned for small (32-35B) models: plan→approve→step→validate.
---

# Small Model Harness Guidelines

When executing tasks, follow these strict guidelines to maintain focus and prevent
context bloat. Respect the CURRENT PERMISSION MODE in your system context before any
state-changing action.

## 0. Permission mode reminders
- **PLAN**: read/search are free; DO NOT change anything until `present_plan` is
  approved. Present findings + exact changes, wait, then work in **small bounded steps**
  (the harness blocks changes once the approved step budget, `INFER_PLAN_STEP_BUDGET`,
  is spent — then re-present for the next chunk).
- **MANUAL**: every state-changing action needs explicit approval.
- **FULL AUTONOMY**: investigate, execute, verify, finish.

## 1. Plan and Execute
- **For multi-step tasks**, use `think` first to generate a numbered, step-by-step plan.
  You may call `think` again between phases to reflect or adjust — repeated reasoning
  is allowed.
- Execute exactly **one step per turn**. Do not combine multiple unrelated tool calls
  in a single turn.
- **Validate each step** before the next: compile, run the relevant test, or read the
  output. Never chain edits you have not verified.
- If you lose track, review your plan and check off completed steps before proceeding.
- In PLAN mode, when the harness says the approved step budget is exhausted, call
  `present_plan` again for the next small chunk — never bulldoze past it.

## 2. Reflexion and Error Recovery
- If a command fails, **stop and reflect** (`think`).
- Read the error output carefully and state what went wrong and how you'll fix it before
  making another tool call. Do not blindly retry the exact same command.
- **CRITICAL: Search for Documentation First**. If you hit an API error, missing method,
  or library usage problem you don't immediately know how to fix, DO NOT guess and
  iterate by brute force. Use `web_search` / `curl` to find official docs or examples.

## 3. Context Management
- Avoid tools that produce massive output unless necessary. Use `grep` over `read_file`
  for large files. Don't repeat info already in context.
- Every tool result carries a `[CURRENT STATE step N] <tool> -> ok|error | <rolling log>`
  header (auto-injected by the harness; disable with `INFER_STATE_CONTEXT=0`). Read it to
  stay oriented mid-task — you don't have to hold the step map in memory. A failed tool is
  always labelled `error`, so never treat a `failed (exit N)` output as success.

## 4. Systems Engineering & Memory Hygiene (C/C++/Rust)
- Never `free()` stack memory. Match `malloc`/`calloc`/`strdup` with `free`. Never
  dereference after `free()`.
- Verify string bounds before pointer arithmetic. Never advance past `\0`.
- When adding/editing source, update all build manifests (Makefile, CMakeLists.txt) and
  test fixtures. Run build + test before `task_complete`.
- Never build shell commands via raw single-quote interpolation; write to files/stdin.

## 5. Termination & Checkpoints
- After each approved chunk, report progress by re-presenting the next chunk or by
  calling `task_complete`. Do not run for many minutes without a checkpoint in PLAN/MANUAL
  mode.
- Once the task is complete, call `task_complete` immediately.
- In AUTO mode, still verify before finishing and report.

## 6. Self-improvement
- Solved something reusable this session? Persist it:
  `skill_create` / `skill_update` / `skill_note`. You'll be notified when a skill is
  created/updated.