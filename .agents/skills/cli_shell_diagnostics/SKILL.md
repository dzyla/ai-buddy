---
name: cli-shell-diagnostics
description: Behavioral guidelines for diagnosing terminal command failures, interpreting shell exit codes, and reading system logs.
---

# Shell Diagnostics Guidelines

When executing command-line instructions, things will occasionally fail. Use these diagnostic steps to investigate:

## 1. Capture and Analyze Stderr
- Shell commands executed via `execute_command` automatically redirect stderr to stdout (e.g. `2>&1`). Pay close attention to the end of the command output, where error traces are usually printed.
- Do not ignore warning lines or compilation flags.

## 2. Check the Environment
- If a command fails because a dependency or utility is missing (e.g. `command not found`), run `which <command>` or check the system path to see if it is installed or named differently.
- Use the host system context (injected in your system prompt) to know if you are on Ubuntu, macOS, etc., and recommend the correct system packages.

## 3. Step-by-Step Diagnostic Execution
- If a piped command chain fails (e.g. `cat log.txt | grep error | awk '{print $2}'`), run the stages of the pipe sequentially to isolate which utility or search pattern caused the failure.

## 4. Log Inspection
- When debugging server-side or compile errors, look for logs inside typical project or system directories (like `logs/`, `build/`, `/var/log/`, or `dmesg`).
- Use tools like `head`, `tail`, or `grep` to read only the relevant log ranges instead of dumping entire files.
