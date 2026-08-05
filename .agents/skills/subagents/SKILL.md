---
name: subagents
description: CRITICAL — when the task requires exploring multiple files, researching broadly, or advanced planning. Use this to save context and avoid instruction overflow.
---

# Subagents and Context Optimization

To prevent your context from overflowing with instructions and file contents, you MUST use subagents for complex or broad tasks.

## 1. Using Subagents (delegate_task)
- If you need to search the web multiple times, read multiple large files, or perform independent research, DO NOT do it yourself.
- Instead, use the `delegate_task` tool to spawn subagents.
- Pass self-contained instructions in the `tasks` array (e.g., `["Read docs/api.md and extract the authentication endpoints", "Search the codebase for usages of the Auth class"]`).
- This keeps your context small and direct, while the subagents do the heavy lifting and return only the summaries.

## 2. Advanced Planning
- For tasks involving 3 or more steps, do not just start executing commands.
- Use the `think` tool to formulate a clear, step-by-step plan.
- If the plan is extremely complex, write it out to a file called `PLAN.md` in the current directory, and read from it as you progress.
- This serves as your external memory and prevents you from losing track of the goal.

## 3. Collect Descriptions Efficiently
- When exploring a new codebase, do not read entire files. Use `execute_command` with `grep`, `head`, `tail`, or `ls -la` to collect descriptions and metadata first.
- Only use `read_file` when you are absolutely certain the file contains the exact snippet you need to modify.
