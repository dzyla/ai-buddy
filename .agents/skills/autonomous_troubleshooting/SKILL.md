---
name: autonomous-troubleshooting
description: CRITICAL — when executing or troubleshooting terminal commands, running tests, or debugging code: investigate first, verify success, and respect the active permission mode before mutating state.
---

# Autonomous Troubleshooting and Execution Skill

Investigate and diagnose independently, but respect the CURRENT PERMISSION MODE
(injected into your system context). Read-only investigation is always allowed.
State-changing actions (editing files, writing new files, running mutating commands)
follow the mode's approval rule:

- **PLAN mode**: DON'T change anything until you call `present_plan(plan="...")` and
  the user approves. Present your diagnosis + the exact fix you intend, then proceed
  only after approval. Work autonomously after that until you have another question.
- **MANUAL mode**: obtain explicit approval for every state-changing action.
- **FULL AUTONOMY**: investigate, fix, and verify continuously.

## 1. Write Code to a File
- Save your code with `write_file`.

## 2. Execute and Verify the Code
- Immediately run it with `execute_command` and inspect the output.

## 3. Analyze Success / Failure
- The system prepends `[Command Success]` or `[Command Failed with exit status X]`.
- **On Success**: Present the final verified result directly in your final response.
- **On Failure**:
  1. Carefully read the traceback/errors.
  2. `think` to explain what failed and why.
  3. Apply a correction (respect permission mode before editing: in plan mode,
     present_plan what you'll change first).
  4. Re-run. Repeat (up to 5-10 rounds) until it succeeds.

## 4. Handling Missing Dependencies
- If execution fails with `ModuleNotFoundError` or similar, run the package manager
  (`pip install ...` / `python3 -m pip install ...`) via `execute_command` — but in
  plan/manual mode get approval first (this installs software / changes state).

## 5. Pivoting Strategies
- If a data source/library/API fails or is deprecated, do not give up. `web_search`
  for alternatives, rewrite your script, and test again — per the active permission
  mode's approval rule before mutating.

## 6. Delegation
- For very complex tasks, use `delegate_task` to spawn helper agents and feed results
  back into your troubleshooting loop.

## 7. Self-improvement
- After you solve something non-obvious, persist it:
  `skill_create` (new technique), `skill_update` (fix a skill you loaded that was
  wrong/outdated), or `skill_note` (standalone lesson). The user is notified on
  create/update.