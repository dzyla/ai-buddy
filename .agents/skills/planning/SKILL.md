---
name: planning
description: CRITICAL — Master Planner: Step-by-step task decomposition, subagent orchestration, and long context management.
---

# Master Planner Guidelines

When confronted with complex or multi-step requests, you must adopt the role of a Master Planner.

## 1. Task Decomposition
- **Order of Execution**: Always break the objective into a strict, sequential list of discrete steps. Do not attempt multiple complex operations simultaneously.
- **Dependencies**: Explicitly note dependencies between steps. If Step B requires data from Step A, ensure Step A includes a validation check before proceeding.
- **The `think` Tool**: Use the `think` tool to write down your plan before starting the first step.

## 2. Orchestrating Subagents
- For parallelizable tasks (e.g., researching multiple separate codefiles, running independent searches), use the `delegate_task` tool.
- Pass an array of precise sub-tasks to `delegate_task`. Each subagent operates independently in its own context.
- Wait for subagents to complete and synthesize their output. Do not micromanage them; give them clear goals.

## 3. Long Context Management
- **Avoid Context Bloat**: Never `cat` or read massive files in full if you only need a small section. Use `grep` or search tools.
- **Save State**: If the conversation is getting long, use `save_memory` to persist key findings, conclusions, or architecture details. 
- **Summarize**: When completing a major phase of your plan, summarize the current state explicitly.

## 4. Execution Discipline
- Execute exactly one step of your plan at a time.
- After a tool call returns, check it against your plan. If it failed, reflect on the error, consult documentation (via `web_search`), and try an alternative approach.
- Once all steps are complete, call `task_complete`.
