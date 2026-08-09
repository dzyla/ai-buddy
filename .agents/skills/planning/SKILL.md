---
name: planning
description: CRITICAL — Master Planner: Step-by-step task decomposition, subagent orchestration, and long context management.
---

# Master Planner Guidelines

When confronted with complex or multi-step requests, adopt the role of a Master Planner
and respect the CURRENT PERMISSION MODE (injected in your system context).

## 0. Mode-aware protocols (override older advice below when they conflict)
- **FULL AUTONOMY**: investigate, plan mentally, execute, verify, then `task_complete`.
- **PLAN mode**: investigate and READ freely, but DO NOT change anything. When you are
  ready to act, call `present_plan(plan="...")` with your findings, the EXACT changes
  (files + edits / commands), and rationale. Wait for the user's approval. Once approved,
  work autonomously until you have another question, then `present_plan` again.
- **MANUAL mode**: read/investigate freely; obtain explicit approval for every
  state-changing action. If an action is denied, adjust your approach — do not retry
  the same action until permitted.

## 1. Task Decomposition
- **Order of Execution**: Always break the objective into a strict, sequential list of
  discrete steps. Do not attempt multiple complex operations simultaneously.
- **Dependencies**: Explicitly note dependencies between steps. If Step B requires data
  from Step A, ensure Step A includes a validation check before proceeding.
- **The `think` Tool**: Use `think` to write down your plan before starting, and use it
  again between phases to reflect/adjust. Repeated `think` calls are allowed (allows
  investigate → check → learn → adjust loops).

## 2. Orchestrating Subagents
- For parallelizable tasks (e.g., researching multiple separate codefiles, running
  independent searches), use the `delegate_task` tool.
- Pass an array of precise sub-tasks to `delegate_task`. Each subagent operates
  independently in its own context.
- Wait for subagents to complete and synthesize their output. Do not micromanage them.

## 3. Long Context Management
- **Avoid Context Bloat**: Never `cat` or read massive files in full if you only need a
  section. Use grep/search tools.
- **Save State**: Use `save_memory` to persist key findings/conclusions.
- **Summarize**: When completing a major phase, summarize the current state explicitly.

## 4. Execution Discipline
- Execute exactly one step of your plan at a time.
- After a tool call returns, check it against your plan. If it failed, reflect on the
  error (`think`), consult documentation via `web_search`, and try an alternative.
- Once all steps are complete, call `task_complete`.

## 5. Self-improvement
- After completing a non-trivial task, if you discovered a reusable technique, a
  workaround, a pitfall, or a wrong instruction, persist it:
  - New technique → `skill_create(name, description, content)`.
  - Correction / good-to-know on a skill you already loaded → `skill_update(name, note)`.
  - Minor or standalone insight → `skill_note(note=...)`.
- The user is notified automatically when a skill is created/updated.