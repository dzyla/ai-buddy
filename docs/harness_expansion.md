# Harness Expansion — Core Tools, Browser, Self-Improve Upgrades, Benchmark

What changed and why. Companion to `docs/self_improvement_chains.md` and
`docs/agent_harness_review.md`. (AGENTS.md is gated for agent edits — this file
carries the canonical description until it is merged in by hand.)

## 1. Core productivity tools (52 → 72 tools)

`ai_mcp.py list-tools` now exposes the everyday agent toolset, parity-checked
against the Hermes tool list:

| Tool | What it does |
|------|--------------|
| `search_files` | Regex content search (`file:line:`, skips `.git`/`__pycache__`/build dirs, capped at `limit`, `context=N` lines) and find-by-glob (`target="files"`, mtime-sorted). `output_mode` = content / files_only / count; `file_glob` filters filenames. Invalid regex falls back to fixed-string. |
| `todo` | Session-scoped task list. No args = read. `todos=[{id,content,status}]` creates (replace) or, with `merge=true`, updates by id — status-only updates (`{"id":"a","status":"completed"}`) are allowed. One file per session: `~/.config/ai/todo_<INFER_SESSION_ID>.json` (global `todo.json` when no session id). |
| `clarify` | Numbered options or open question on the controlling tty. Interactive only when `INFER_INTERACTIVE=1` (set by `ai.c` when a human is on stdin) or stdin is a tty. In bridges/schedulers/cron it returns `[CLARIFY NON-INTERACTIVE]` — pick the first (recommended) option, proceed, state the assumption. Never blocks a headless run. |
| `browser` | Persistent headless Chromium via a unix-socket Playwright daemon (`~/.cache/ai/browser.sock`). State (URL, forms, JS context) survives across tool calls; daemon auto-starts, idles out after 10 min, logs every action to `~/.cache/ai/browser_actions.log`. Actions: `goto, content, html, links, click, fill, select, press, js, wait, back, screenshot, status, shutdown`. For JS-rendered/interactive pages — `fetch_webpage` for plain ones. Requires `python3 -m playwright install chromium`. |
| `append_to_context_pool` | Shared cross-session fact pool (`~/.config/ai/context_pool.json`) — was implemented but previously unreachable (see §4). |
| `spawn_agent` / `resume_agent` / `list_agents` | Multi-agent workflows (were already dispatched; now deduplicated — see §4). |
| `get_context_snippet` / `search_context` / `structured_query` / `session_report` | Context-pool reads and structured filter/transform/aggregate queries over files, command output, or plain text. |

C side: icons for all new tools, read-only vs mutating allowlists updated
(deny-by-default preserved), `INFER_SESSION_ID` and `INFER_INTERACTIVE` exported
to tool subprocesses.

## 2. Self-improvement upgrades (tool-health closed loop)

- **`log_metric` now records the truth.** The `call-tool` handler tees the
  tool's stdout and, at exit, classifies the result with the same error markers
  the C loop uses (`Error…`, `failed (exit N)`, `[SYSTEM WARNING:…`, uncaught
  exception → failure). Previously every call was logged `success=true` with no
  error text, which is why `[FLAKY]` detection and metrics-driven lessons never
  fired. `ai_mcp.py show-metrics` flags `[FLAKY]` (≥5 calls, ≥30% failed) and
  prints top recurring errors per tool.
- **Metrics → PITFALL auto-sync.** `ai_mcp.py sync-metrics-lessons [n]` turns
  every tool+error signature seen ≥ n (default 3) failing times into a `##
  PITFALL` lesson in the self-improve lesson store. It also runs automatically
  inside `session-recap` (gated by `INFER_METRICS_LESSON_SYNC`, default on), so
  the flaky-tools section of every session-start recap is backed by an
  actionable lesson.
- **MASTER → skill note.** When an error chain is promoted to `## MASTER`, a
  learning note is appended to `~/.config/ai/skills_learning_log.md` suggesting
  `skill_update` to fold the proven fix into a durable skill — the closed loop
  from one recurring error to permanent knowledge (gated by
  `INFER_MASTER_SKILL_NOTE`, default on).
- **`call-tool` crash fix.** An unparseable, unrepairable args JSON used to print
  an error and then fall through to `normalize_tool_arguments` with an undefined
  variable → `NameError` traceback. It now exits cleanly with a
  `{"error": "Failed to parse arguments JSON even after repair: …"}` payload
  (exit 0, model-visible error).

## 3. Bug fixes found while testing

- **Dead dispatch code** — `spawn_agent`, `resume_agent`, `list_agents`,
  `get_context_snippet`, `search_context`, `structured_query`, `session_report`
  were each dispatched twice in `call-tool`; the second copy (and the new
  `append_to_context_pool` entry, which sat behind them) was unreachable. The
  duplicates were removed; `tests/test_core_tools.py::test_dispatch_no_dead_
  duplicates` guards against regression.
- **`todo` status-only updates** — merge-mode validation demanded `content` on
  every item, so `{"id":"a","status":"completed"}` was rejected. Fixed: content
  is required only when creating a new item.
- **Vault (Obsidian-style notes) literal-`\n` bug** — `vault_write` (links
  footer), `vault_search` (preview flattening + multi-result join) and
  `vault_backlinks` (result join) emitted a two-character backslash-n instead of
  real newlines, so note files and search output were mangled. Fixed; regression
  tests in `tests/test_core_tools.py`.

## 4. New skills (`.agents/skills/`)

- `core_tools_guide` — when/why to use todo / clarify / search_files / context pool.
- `browser_automation` — the `browser` tool's actions, workflow, selector and
  state-persistence pitfalls.
- `daily_digest` — assemble a scientist's morning briefing (calendar, tasks,
  system health, recent sessions, new literature) into one compact message.
- `scheduling_reminders` — set_reminder vs schedule_task vs gcal_* decision guide;
  verify-after-schedule rule; no-sleep rule.
- `system_maintenance` — diagnose read-only first (df/du/ps/get_system_status),
  bounded reversible fixes, safety rails (never `find /`, never touch
  `~/.local/share/ai`), verify the metric moved.
- `note_vault` — vault vs remember vs skills vs search_history; link/backlink
  conventions.

## 5. Harness benchmark (`make bench`)

`dev/bench_harness.py` — pass/fail job suite that runs REAL jobs through
`ai --raw-output --no-git -y "<prompt>"` against the configured local LLM and
scores each objectively:

- **answer checks** — regex/substring on the final assistant text;
- **file checks** — the file the job had to create/edit, read back from disk;
- **tool-use checks** — tool calls actually recorded in the saved session JSON;
- **harness checks** — session persisted; metrics logged (for backend tools —
  C-native tools `think`/`task_complete`/`execute_command` log none).

14 tasks cover: plain reasoning, list_directory+count, read_file extraction,
write_file, execute_command math, search_files, structured_query, context-pool
round-trip, edit_file, check_time, todo workflow, **failure recovery** (a
guaranteed-failing command, then recover the cwd), clarify non-interactive
fallback, and write-then-verify.

Robustness: each task runs in a scratch workdir with `ai_mcp.py` symlinked in
(so the benchmark always tests the repo's backend); runs happen in their own
process group so a timeout kills the whole tree (models that spawn conda or
daemons can't orphan their way past the timeout); one automatic retry on
timeout (busy-GPU stalls); session files located by mtime at/after run start.

```bash
make bench          # full suite, JSON report -> dev/bench_report.json
make bench-quick    # 4 fast tasks, CI-friendly
python3 dev/bench_harness.py --only math_tool,write_file_task
```

Exit code 0 iff score ≥ `--min-pass` (default 80%). The first full run on this
box scored 9/14 (64%) — three timeout-fails were a transient GPU stall (94%
utilised by another job) and one was a genuine model error (`structured_query`
counted unfiltered lines), which the benchmark is designed to surface.

## 6. Repo hygiene

- Root stray review docs moved to `docs/` (`agent_harness_review.md`,
  `self_improvement_harness.md`, `zulip_bridge_improvements.md`);
  `test_rprompt.sh` (TUI debug scratch) removed.
- `.gitignore`: the global `*.csv` / `*.json` / `*.txt` ignores (a data-analysis
  relic) were scoped to `data/` so app configs can be tracked.
- `Makefile`: `bench` and `bench-quick` targets.

## 7. Tests

- `tests/test_core_tools.py` (35 tests, all offline, isolated HOME):
  search_files (content/files/glob/modes/limit/context/invalid-regex/errors),
  todo (CRUD, session scoping, merge validation), clarify (non-interactive
  fallback, open-ended, validation), log_metric real-failure capture,
  show_metrics `[FLAKY]`, metrics→PITFALL sync (threshold, idempotency,
  success-only gating), session_recap sections, MASTER→skill note (+ gating),
  browser offline paths (unknown action, missing url, daemon-failure message),
  context pool / structured_query / spawn_agent / session_report, vault
  newline regression, CLI: broken-args clean exit, repaired markdown-fence
  JSON, no dead dispatch duplicates, list-tools coverage,
  `sync-metrics-lessons` CLI.
- Full suite: `make test` (C test + pytest, 211+ tests).
