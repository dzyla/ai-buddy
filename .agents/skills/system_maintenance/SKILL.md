---
name: system_maintenance
description: CRITICAL — when the user asks to clean disk, check system health, kill a stuck process, free memory, or maintain the computer ("disk is full", "why is it slow", "clean up", "what's eating RAM"): diagnose with read-only commands first, then make bounded, reversible changes.
---

# `system_maintenance`

Keep the machine fast and uncluttered. The rule is: **diagnose read-only first,
change second, and every change must be reversible or clearly safe.**

## Diagnose (read-only — always safe)

- `get_system_status` — the quick view: disk, memory, load, top processes.
- Disk usage, bounded (NEVER `find /`):
  ```bash
  df -h /
  du -xh --max-depth=1 ~ 2>/dev/null | sort -rh | head -15
  ```
- What is eating RAM/CPU:
  ```bash
  ps aux --sort=-%mem | head -8
  ps aux --sort=-%cpu | head -8
  ```
- Stuck/orphaned processes: `list_processes` (harness tool) or
  `ps aux | grep -E '<name>[<name>]'` (the bracket trick avoids matching your own
  grep).

## Common fixes (bounded, reversible)

- **Disk full from caches** — before deleting, show the user what will be freed:
  - `~/.cache` (safe: regenerable), pip/conda caches (`pip cache purge`),
    `~/.cache/ai/sessions/` is already pruned by the harness — leave it.
  - Package-manager caches. Show `du -sh` of each target, list them, then delete
    only what was shown. Never `rm -rf` a path you have not just measured.
- **Stuck process** — identify pid + what it is. Prefer graceful
  `stop_process <pid>` (harness tool) or `kill <pid>`; only `kill -9` after the
  graceful attempt failed and the process is confirmed unresponsive. Never kill
  something you cannot name.
- **High memory** — report the top consumer and let the user decide. Do not kill
  a user's running job (GPU training, editor) to free RAM without asking.

## Safety rails

- **Never** run `find /`, `rm -rf /`, or delete outside `~/.cache`-class dirs
  without explicit user confirmation of the exact path.
- **Never** clear `~/.local/share/ai/` (persistent session archive) — that is
  the history index's backup and would lose searchable history.
- Make changes one at a time and re-run `get_system_status` to confirm the effect
  before moving to the next.
- If the fix is a one-off command, persist it with `skill_note` under this skill
  so next time it is a known fix, not a re-diagnosis.

## Verification

After any change, re-run the read-only diagnosis and show the before/after
(numbers: GB freed, % CPU/mem, load). A maintenance task is done when the metric
it targeted actually moved.
