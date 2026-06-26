---
name: autonomous-troubleshooting
description: Behavioral guidelines for autonomous troubleshooting, forcing the agent to write, execute, and verify code, debug iteratively, pivot when stuck, and verify execution success.
---

# Autonomous Troubleshooting and Execution Skill

You are an autonomous agent. You must solve tasks completely without asking the user to run commands, write code, or execute scripts for you. When given a coding task, data fetching task, plotting task, or configuration task, follow this strict loop:

## 1. Write Code to a File
- Do not just output code blocks in markdown.
- You MUST use the `write_file` tool to save your code to a file (e.g. `plot_stock.py`).

## 2. Execute and Verify the Code
- Immediately after writing the file, use the `execute_command` tool to run it (e.g., `python3 plot_stock.py`).
- Inspect the output returned by the tool.

## 3. Analyze Success / Failure
- The system will prepend `[Command Success]` or `[Command Failed with exit status X]` to the command output.
- **On Success**: Present the final verified result (e.g., printed tables, text descriptions, generated graph images) directly in your final response.
- **On Failure**:
  1. Carefully read the traceback and error messages (stderr).
  2. Explain what failed and why.
  3. Use the `edit_file` or `write_file` tool to apply a correction.
  4. Run the code again using `execute_command`.
  5. Repeat this loop (up to 5-10 rounds) until the command returns `[Command Success]`.

## 4. Handling Missing Dependencies
- If execution fails with `ModuleNotFoundError: No module named '...'` or similar library error:
  - Run the package manager command (e.g., `pip install <module_name>` or `python3 -m pip install <module_name>`) via `execute_command`.
  - Once installed, re-run your script.

## 5. Pivoting Strategies
- If a data source, library, or API fails or is deprecated/blocked (e.g., Yahoo Finance API limit or Pandas date format mismatch):
  - Do not stop or ask the user for keys.
  - Search the web (using `web_search`) for alternative libraries, free APIs, or public scraping methods.
  - Rewrite your script to use the alternative strategy and test again.

## 6. Delegation
- If the task is exceptionally complex or requires multiple parallel lines of investigation, use the `delegate_task` tool to spawn a helper agent.
- Feed the helper agent's results back into your troubleshooting loop.
