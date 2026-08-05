---
name: small-model-harness
description: Best practices and agent loop guidelines specifically tuned for small (32-35B) models.
---

# Small Model Harness Guidelines

When executing tasks, follow these strict guidelines to maintain focus and prevent context bloat:

## 1. Plan and Execute
- **For multi-step tasks**, always use the `think` tool first to generate a numbered, step-by-step plan.
- Execute exactly **one step per turn**. Do not combine multiple unrelated tool calls in a single turn.
- If you lose track, review your plan and check off completed steps before proceeding.

## 2. Reflexion and Error Recovery
- If a command fails (e.g., `execute_command` returns non-zero), **stop and reflect**.
- Read the error output carefully to understand the root cause.
- State what went wrong and how you plan to fix it before making another tool call. Do not blindly retry the exact same command.
- **CRITICAL: Search for Documentation First**. If you encounter an API error, missing method, or library usage problem that you don't immediately know how to fix, **DO NOT GUESS** and iterate by brute force (e.g., repeatedly calling `dir()` or `help()`). Instead, immediately use web search tools (`web_search`, `curl`, etc.) to find the official documentation or examples online.

## 3. Context Management
- Avoid calling tools that produce massive output unless absolutely necessary.
- If you need to search a large file, use `execute_command` with `grep` rather than `read_file`.
- Do not repeat information that is already in the context.

## 4. Termination
- Once the task is complete, call `task_complete` immediately.
- Do not ask follow-up questions or perform unnecessary verification steps unless explicitly requested.
