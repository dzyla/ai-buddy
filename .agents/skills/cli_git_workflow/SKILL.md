---
name: cli-git-workflow
description: Guidelines for managing Git repository workflows inside a CLI session. Instructs the model on safe staging, commit message syntax, and checking diffs before commits.
---

# Git Workflow Guidelines for CLI Assistants

When the user asks you to perform Git operations or manage their repository, follow these standard practices:

## 1. Inspect Status and Diffs First
- Always run `git status` before performing any git additions or commits to see the exact state of the working tree.
- Run `git diff` on target files before staging them to confirm what changes are being committed.

## 2. Surgical Staging
- Do not run `git add .` or stage everything blindly unless the user explicitly asks to commit "all changes" or "everything".
- Stage files individually (`git add <file>`) to prevent staging temporary build products, log files, or scratch pads.

## 3. Commit Message Standards
- Write clean, imperative-style commit messages (e.g., `git commit -m "Fix file reading binary check"` instead of `git commit -m "fixed binary reading"`).
- Keep the title under 50 characters, and explain the *why* instead of just restating the *what* if a detailed description is necessary.

## 4. Safety First
- Do not perform destructive git actions (like `git reset --hard` or `git clean -fd`) without getting explicit confirmation from the user first.
- If you create a new branch, use descriptive, lowercase names separated by hyphens (e.g. `feature/binary-read-safeguard`).
