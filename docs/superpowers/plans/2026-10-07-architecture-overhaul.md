# Architecture Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn ai-buddy from three monolithic scripts glued by per-call subprocesses into a modular, testable app: isolated live trader, persistent tool server, one state store, reviewable learning, Python agent loop behind a thin C terminal front-end, and CI.

**Architecture:** Work lands in four phases ordered by risk and file ownership. Phase 0 isolates the live-money trader from the working tree before anything else is touched. Phase 1 is pure, behaviour-preserving module splits (ai_mcp.py → `aitools/`, zulip_ai_bridge.py → `zulip_bridge/`) plus CI and config templates. Phase 2 builds new capability on the split code (SQLite state store, learning queue, persistent tool server). Phase 3 ports the agent loop to Python behind `INFER_ENGINE=py`, proves parity on the whole offline suite, then flips the default and turns on `-Werror`.

**Tech Stack:** C11 + libcurl (front-end), Python 3.12 stdlib (`sqlite3`, `socketserver`, `json`, `urllib`), pytest, GitHub Actions, systemd user units.

**Spec:** The eight recommendations in the 2026-10-07 session summary (tool server; Python loop with C front-end; module splits; one state store; config out of repo; separate trader with versioned deploy, shadow mode and daily summary; CI with tests/privacy/-Werror plus eval set; reviewable learning with expiry). This plan is the spec of record.

## Global Constraints

- The GitHub repo is public. No account numbers, balances, holdings, private hosts/IPs, real e-mails or `/home/<user>` paths in tracked files. `scripts/privacy_check.py` (pre-commit hook) must pass on every commit; never use `--no-verify`.
- Never place, cancel or modify real orders; never call the real Robinhood MCP from tests or agents; never restart `robinhood-trader.service` (the lead does live switches).
- Tests are offline: mock LLM (`tests/mock_llm_server.py`), mocked Zulip/Google/broker clients, isolated `HOME` (`tmp_path`), binary runs with `cwd` outside the repo.
- Backwards-compatible entry points stay working for the whole plan: `python3 ai_mcp.py <subcommand> …`, `python3 robinhood_trader.py <cmd> …`, `python3 zulip_ai_bridge.py …`, every existing `ai` flag and `INFER_*` variable.
- Python 3.12 (both `/usr/bin/python3` 3.12.3 and miniconda 3.12.9); stdlib only for new core code (no new pip deps).
- Installed layout: `install.sh` copies scripts to `~/.local/bin`. Any new package dir must also be installed (to `~/.local/share/ai/lib/`) and found by the shim via `sys.path` (repo checkout first, then installed copy).
- Every task ends with `make && python3 -m pytest tests/ -q` green (443 passing at plan start) and its own new tests.
- Commit per task with a conventional message ending in the session's co-author line.

## Review Focus

- **Live trader mid-session:** switching the systemd unit to the deployed release while the market is open must not repeat today's top-up or the month's rebalance. Test: deployed release reads the same `~/.config/ai/.monitor_flags` and `trader.json`; pulse with an existing `rebalance_<month>.json` plans nothing (Task 1).
- **Tool server dies or is stale:** a crashed/killed server, a server started from an older checkout, or a stuck MCP child must not hang `ai`. Expected: per-call timeout, version handshake, transparent fallback to the subprocess path. Tests in Task 7.
- **State migration on a real, messy HOME:** existing `memory.txt` (any encoding, >4 KB), `lessons.md` with hand edits, a partly corrupt `ledger.jsonl` line. Expected: import what parses, keep originals as `*.migrated.bak`, never crash startup. Tests in Task 5.
- **Concurrent writers:** the Zulip bridge, scheduler and interactive `ai` write the store at the same time. Expected: WAL mode + busy timeout, no `database is locked` surfacing to the user. Test in Task 5.
- **Engine parity edge cases:** Ctrl-C mid-stream, `finish_reason: length`, image arguments, `-r` resume, piped stdin. Expected: Python engine behaves like the C engine. Covered by running the whole offline suite under both engines in Task 8.

---

## File Structure

```
trader/                      # Task 1 – live-money package, deployed separately
  __init__.py  config.py  market_hours.py  data.py  indicators.py  strategy.py
  broker.py  risk.py  journal.py  daemon.py  cli.py  summary.py  shadow.py
  deploy.sh                  # versioned release + `current` symlink
robinhood_trader.py          # shim: from trader.cli import main
aitools/                     # Task 2 – split of ai_mcp.py
  __init__.py  registry.py (schemas + dispatch table)  cli.py (subcommand main)
  web.py  files.py  shell.py  memory.py  scheduler.py  render.py  mcp_client.py
  calendar_tools.py  research.py  selfimprove.py  sessions.py  browser.py  misc.py
  state.py (Task 5)  learning.py (Task 6)  server.py (Task 7)
ai_mcp.py                    # shim: sys.path setup + aitools.cli.main()
zulip_bridge/                # Task 3 – split of zulip_ai_bridge.py
  __init__.py  config.py  owner.py  files.py  memory.py  runner.py  bridge.py  context.py  cli.py
zulip_ai_bridge.py           # shim
config/                      # Task 4 – templates only
  mcp.example.json  trader.example.json  private_patterns.example.txt  zuliprc.example
agent/                       # Task 8 – Python agent loop
  __init__.py  loop.py  llm.py (HTTP+SSE)  messages.py (build/trim)  tools.py (dispatch)
  native.py (think/task_complete/execute_command)  session.py  protocol.py (front-end IPC)
ai_frontend.c / .h           # Task 8 – C side of the front-end protocol
.github/workflows/ci.yml     # Task 4, -Werror in Task 9
evals/                       # Task 4 – agent task set + runner
```

---

## Phase 0 — protect the live trader

### Task 1: `trader/` package, versioned deploy, shadow mode, daily summary

**Files:**
- Create: `trader/*.py` as listed above, `trader/deploy.sh`, `tests/test_trader_deploy.py`, `tests/test_trader_shadow.py`, `tests/test_trader_summary.py`
- Modify: `robinhood_trader.py` (becomes shim), `services/robinhood-trader.service`, `install.sh`, `docs/robinhood_trader.md`, existing trader tests' imports only if needed
- Test: existing `tests/test_robinhood_trader.py`, `tests/test_momentum_strategy.py`, `tests/test_trader_pulses.py` must pass unchanged (they import `robinhood_trader`; the shim re-exports every public name)

**Interfaces:**
- Produces: `trader.cli.main(argv: list[str] | None) -> int`; `robinhood_trader` module re-exports all names it exports today (`MomentumStrategy`, `RobinhoodExecutor`, `MarketHours`, `load_trader_config`, `_floor_qty`, …) so `monkeypatch.setattr(robinhood_trader, X, …)` in tests still hits the code path (implement the shim as `sys.modules[__name__] = trader.legacy` where `trader/legacy.py` imports * from submodules and functions look up collaborators via module globals of `trader.legacy` — or keep moved code calling through `trader.legacy` names; verify with the pulse tests that monkeypatching still works).
- Produces: `trader/deploy.sh [--dry-run]` → creates `~/.local/share/ai-trader/releases/<UTC-ts>-<shortsha>/` (copy of `trader/`, `robinhood_trader.py`, `VERSION`), atomically repoints `~/.local/share/ai-trader/current`, keeps last 5 releases, prints the release path. Refuses if the git tree is dirty or tests fail (`python3 -m pytest tests/test_robinhood_trader.py tests/test_momentum_strategy.py tests/test_trader_pulses.py -q`).
- Produces: unit `ExecStart=/usr/bin/python3 %h/.local/share/ai-trader/current/robinhood_trader.py monitor --interval 60 --auto-trade --live`, `WorkingDirectory=%h/.local/share/ai-trader/current`.
- Produces: `trader.shadow.run_shadow_pulse(now, config) -> dict` — runs the same planning code with `strategy_overrides` from `trader.json["shadow"]`, never calls broker mutators (enforced: broker object is a `ReadOnlyBroker` wrapper whose mutating methods raise), appends planned orders to `~/.cache/ai/trading/shadow/<YYYY-MM>.jsonl`. Daemon calls it once per day after the live pulse when `trader.json["shadow"]["enabled"]` is true.
- Produces: `trader.summary.build_daily_summary(day) -> str` (markdown: equity change, orders filled, sleeve holdings, regime, circuit-breaker state, shadow-vs-live diff) and `send_daily_summary(text)` → Zulip via `zulip_mcp_server`'s send function if `AI_TRADER_ZULIP_TO` (or `AI_REMINDER_ZULIP_TO`) is set, else `notify-send`, else log only. Daemon sends once per trading day after close (flag `summary_<day>.flag`).

- [ ] **Step 1:** Write `tests/test_trader_deploy.py`: run `trader/deploy.sh --dry-run` with `HOME=tmp_path` on a clean temp clone (`git clone` the repo into tmp) → asserts release dir layout, `current` symlink, `VERSION` contains the short sha, and that a dirty tree is refused (exit code ≠ 0, message contains "dirty"). Run: FAIL (script missing).
- [ ] **Step 2:** Write `tests/test_trader_shadow.py`: a fake broker whose `place_equity_order` raises `AssertionError`; `run_shadow_pulse` with `{"shadow": {"enabled": true, "strategy": {"sleeve_rank_buffer": 0}}}` writes one JSONL line with `planned_orders` and never calls the mutator. Run: FAIL.
- [ ] **Step 3:** Write `tests/test_trader_summary.py`: given a fixture journal (fake numbers, accounts `000000001`), `build_daily_summary` output contains equity delta, each filled order, regime, and no 9-digit numbers other than the fake ones; `send_daily_summary` with `AI_TRADER_ZULIP_TO` set calls the mocked Zulip send once; second call same day is suppressed by the flag. Run: FAIL.
- [ ] **Step 4:** Move code into `trader/` modules by class (mapping: ObsidianTradingVault→journal.py, MarketHours→market_hours.py, FinancialData→data.py, TechnicalIndicators→indicators.py, NewsSentimentEngine/AgentAdvisor/TradingStrategyEngine→advisor.py, MomentumStrategy/_floor_qty/_fmt_qty→strategy.py, RobinhoodExecutor/authenticate_robinhood_mcp/AccountResolutionError/OrderNotConfirmed/config helpers→broker.py+config.py, ComplianceAndRiskGuard→risk.py, TradingDataManager/PortfolioAuditor→portfolio.py, run_* loops and pulse functions→daemon.py, argparse main→cli.py). Make `robinhood_trader.py` the shim. Run the three existing trader test files: PASS unchanged.
- [ ] **Step 5:** Implement `deploy.sh`, `shadow.py`, `summary.py`, daemon hooks; update unit file and `install.sh` (install copies the unit but does not restart it); add "Deploying" + "Shadow mode" + "Daily summary" sections to `docs/robinhood_trader.md`; add `"shadow": {"enabled": false, "strategy": {}}` to `config/trader.example.json` (created in Task 4 — if Task 4 has not landed, create the file here). Run new tests: PASS. Full suite: PASS.
- [ ] **Step 6:** Commit `feat(trader): package split, versioned deploy, shadow mode, daily summary`.
- [ ] **Step 7 (lead only):** `bash trader/deploy.sh`, `cp services/robinhood-trader.service ~/.config/systemd/user/`, `systemctl --user daemon-reload && systemctl --user restart robinhood-trader`, confirm two clean pulses in `journalctl`. From now on working-tree edits no longer affect the live daemon.

---

## Phase 1 — behaviour-preserving splits, CI, config templates (Tasks 2, 3, 4 run in parallel; disjoint files)

### Task 2: split `ai_mcp.py` into `aitools/`

**Files:**
- Create: `aitools/` modules listed in File Structure, `tests/test_aitools_layout.py`
- Modify: `ai_mcp.py` (→ ~30-line shim), `install.sh` (install `aitools/` to `~/.local/share/ai/lib/aitools`, shim adds repo dir then that lib dir to `sys.path`), `CLAUDE.md` architecture section
- Test: whole existing suite unchanged (`tests/test_offline.py`, `test_core_tools.py`, `test_integrations.py`, `test_self_improve*.py`, `test_situational_awareness.py`, `test_history_index.py`, …)

**Interfaces:**
- Produces: `aitools.registry.TOOL_SCHEMAS: list[dict]` (exact current `list-tools` output order), `aitools.registry.dispatch(server: str, tool: str, args: dict) -> str` (exactly what `call-tool` prints today), `aitools.cli.main(argv) -> int`.
- Produces: `import ai_mcp` keeps exposing every top-level name tests use today (`fetch_webpage`, `fetch_smart`, `fetch_webpage_basic`, `save_memory`, `MEMORY_PATH`, `trim_messages`, `session_transcript`, `schedule_task`, …). Implement `ai_mcp.py` as `from aitools.compat import *` plus module-attribute forwarding (`__getattr__`/`__setattr__` via a `ModuleType` subclass) so `monkeypatch.setattr(ai_mcp, "MEMORY_PATH", …)` changes the value the moved code reads. Verify with the existing monkeypatching tests.

- [ ] **Step 1:** Write `tests/test_aitools_layout.py`: (a) `python3 ai_mcp.py list-tools` output is byte-identical to a golden file captured from HEAD *before* the split (`tests/golden/list_tools.json`, generate it in this step from the unsplit file); (b) `ai_mcp.py` is < 80 lines; (c) no module in `aitools/` exceeds 1500 lines; (d) `monkeypatch.setattr(ai_mcp, "MEMORY_PATH", p)` then `ai_mcp.save_memory("x")` writes to `p`. Run: (a)(d) PASS, (b)(c) FAIL.
- [ ] **Step 2:** Move functions by concern (web/fetch/search → web.py; read/write/edit/list → files.py; memory + context pool → memory.py; schedule_task/run_scheduler_loop/reminders → scheduler.py; render-markdown → render.py; MCP client + load_config → mcp_client.py; gcal routing → calendar_tools.py; deep research/pubmed → research.py; self-improve/ledger/lessons → selfimprove.py; session-transcript/trim-messages → sessions.py; browser daemon/computer_control → browser.py). Replace the ~2,700-line if/elif in `main()` with a dict in `registry.py` mapping tool name → handler `(args) -> str`, preserving every alias (`tool_name == X or server_name == X`). Run full suite after each moved group.
- [ ] **Step 3:** Update `install.sh` and `CLAUDE.md`. Run `./install.sh` into `HOME=tmp` (test `test_install_layout`: installed `~/.local/bin/ai_mcp.py list-tools` works from `/`). Full suite PASS.
- [ ] **Step 4:** Commit `refactor(tools): split ai_mcp.py into aitools package`.

### Task 3: split `zulip_ai_bridge.py` into `zulip_bridge/`

**Files:**
- Create: `zulip_bridge/` modules (config.py: load_env_file/apply_env_file_defaults/zuliprc_path/resolve_ai_bin; owner.py: lookup_bot_owner/detect_owner/describe_owner_value; files.py: FileParser/_safe_upload_name; runner.py: run_ai_subprocess/_kill_process_group/extract_session_fallback; memory.py: ZulipMemoryManager; context.py: ContextWindowManager; bridge.py: ZulipAiBridge; cli.py: main/run_check)
- Modify: `zulip_ai_bridge.py` (shim with the same module-forwarding trick as Task 2), `services/zulip-ai-bridge.service` unchanged path, `install.sh` (install package), `docs/zulip_setup.md` (one line)
- Test: `tests/test_zulip_ai_bridge.py`, `tests/test_integrations.py` unchanged + `tests/test_zulip_layout.py` (shim < 60 lines, no module > 800 lines, `python3 zulip_ai_bridge.py --check` exit code/output unchanged vs golden)

- [ ] **Step 1:** Write `tests/test_zulip_layout.py` with a golden `--check` output captured before the split (HOME=tmp, no zulip package → deterministic). Run: size asserts FAIL.
- [ ] **Step 2:** Move code; keep `zulip_ai_bridge.<name>` patchable (existing tests patch `zulip_ai_bridge.run_ai_subprocess`). Run Zulip tests after each move.
- [ ] **Step 3:** Full suite PASS. Commit `refactor(zulip): split bridge into zulip_bridge package`.

### Task 4: config out of repo, CI, eval set

**Files:**
- Create: `config/mcp.example.json` (robinhood + zulip + pubmed entries, no secrets), `config/trader.example.json`, `config/private_patterns.example.txt` (comments only + generic examples), `config/zuliprc.example`, `.github/workflows/ci.yml`, `evals/tasks/*.json` (≥ 12 tasks), `evals/run_evals.py`, `tests/test_config_templates.py`, `tests/test_evals_runner.py`
- Modify: delete tracked `mcp.json` (move content to `config/mcp.example.json`); `.gitignore` add `/mcp.json`; `install.sh` copies each `config/*.example.*` to `~/.config/ai/` *only if the target is missing*; `CLAUDE.md` config section; the MCP config search in Python (`load_config`, currently `./mcp.json` first) — after Task 2 lands it lives in `aitools/mcp_client.py`; if Task 2 is not merged yet, make this one-line change in `ai_mcp.py` and let Task 2 carry it
- Test: new tests below

**Interfaces:**
- `load_config()` search order becomes: `$INFER_MCP_CONFIG`, `~/.config/ai/mcp.json`, `~/.config/ai/mcp_config.json`, `./mcp.json` (still honoured for dev, but no longer tracked), then the legacy paths.
- `evals/run_evals.py --engine {c,py} --base-url URL --model M --json out.json` → runs each task in a temp dir, scores with the task's checks (`answer_contains`, `file_exists`, `file_contains`, `tool_used`, `max_steps`), prints a table, exits 0. Offline mode `--mock` uses `tests/mock_llm_server.py` scripted sequences stored in each task's `mock` field so CI can run it.
- CI: jobs `test` (ubuntu-latest, `apt-get install libcurl4-openssl-dev`, `make`, `pytest tests/ -q`), `privacy` (`python3 scripts/privacy_check.py --all` with an empty personal list), `evals-mock` (`python3 evals/run_evals.py --mock`). `-Werror` is added in Task 9.

- [ ] **Step 1:** `tests/test_config_templates.py`: every `config/*.example.*` parses (json/ini), contains no 9-digit numbers / real e-mails (run `privacy_check.scan_line` over each line); `install.sh` run with `HOME=tmp` creates `~/.config/ai/mcp.json` from the template and does **not** overwrite an existing one (pre-create it with sentinel content). FAIL.
- [ ] **Step 2:** `tests/test_evals_runner.py`: `run_evals.py --mock --json out.json` exits 0, out.json has one result per task with `passed` bool and `steps` int. FAIL.
- [ ] **Step 3:** Implement templates, install logic, load_config order, eval tasks + runner, CI workflow (validate YAML with `python3 -c "import yaml…"` if available else by `act`-less lint: just ensure `ci.yml` loads with `json`-compatible yaml subset via a tiny test). PASS. Full suite PASS.
- [ ] **Step 4 (lead):** before deleting the tracked `mcp.json`, copy it to `~/.config/ai/mcp.json` if that file has no `robinhood` entry, so the live setup keeps its servers. Commit `feat: config templates out of repo, CI workflow, eval set`.

---

## Phase 2 — new capability on the split code (Task 5 → Task 6 sequential; Task 7 parallel to both)

### Task 5: SQLite state store

**Files:**
- Create: `aitools/state.py`, `tests/test_state_store.py`
- Modify: `aitools/memory.py`, `aitools/selfimprove.py`, `aitools/scheduler.py`, `aitools/sessions.py`, `zulip_bridge/memory.py` (audit log), `ai.c` only where it reads `memory.txt`/`lessons.md` directly (switch to `ai_mcp.py state-export memory|lessons` output, cached once per process), `CLAUDE.md` runtime-state section

**Interfaces:**
- DB: `~/.local/share/ai/state.db` (`$AI_STATE_DB` override). `PRAGMA journal_mode=WAL; busy_timeout=5000`. Schema version table `meta(key PRIMARY KEY, value)`; `schema_version=1`.
- Tables: `memory(id INTEGER PK, text TEXT UNIQUE, created REAL, last_used REAL)`, `lessons(id PK, kind TEXT, tool TEXT, signature TEXT, body TEXT, status TEXT DEFAULT 'approved', created REAL, last_seen REAL, hits INT DEFAULT 1, expires REAL NULL, UNIQUE(kind, tool, signature))`, `ledger(id PK, ts REAL, chain_id TEXT, tool TEXT, args_hash TEXT, ok INT, error TEXT)`, `sessions(id TEXT PK, started REAL, ended REAL, path TEXT, summary TEXT)`, `scheduled_tasks(id TEXT PK, spec_json TEXT, next_run REAL, run_once INT, kind TEXT)`, `chat_audit(id PK, ts REAL, source TEXT, sender TEXT, prompt TEXT, reply TEXT)` with retention (`DELETE … WHERE ts < now-90d` on open, at most once per day).
- API: `state.connect() -> sqlite3.Connection` (creates + migrates), `state.migrate_legacy(home) -> dict[str,int]` (imports `memory.txt`, `self_improve/lessons.md`, `self_improve/ledger.jsonl`, `scheduled_tasks/*.json`, `zulip_chats.jsonl`; renames originals to `*.migrated.bak`; idempotent via `meta.migrated_v1`), `memory_add(text)->str`, `memory_list()->list[str]`, `memory_render(max_bytes=4096)->str`, `lesson_upsert(...)`, `lessons_render(status='approved', limit=…)->str`, `ledger_append(...)`, `ledger_trim(max_rows=5000)`.
- CLI: `ai_mcp.py state-export memory|lessons`, `ai_mcp.py state-migrate`, `ai_mcp.py state-stats`.

- [ ] **Step 1:** `tests/test_state_store.py` (isolated HOME): migration of a fixture HOME containing a 6 KB `memory.txt` with a latin-1 byte, a hand-edited `lessons.md`, a `ledger.jsonl` with one corrupt line, two scheduled tasks → counts returned, originals renamed, second run is a no-op; `memory_add` dedupes case-insensitively and `memory_render` stays ≤ 4096 bytes keeping newest; 4 threads × 200 `ledger_append` concurrently → 800 rows, no exception; `chat_audit` retention deletes rows older than 90 days. FAIL.
- [ ] **Step 2:** Implement `state.py`. PASS.
- [ ] **Step 3:** Switch memory/lessons/ledger/scheduler/sessions/chat-audit callers to the store (call `migrate_legacy` lazily on first `connect()`); switch `ai.c` reads to `state-export`. Existing tests that assert on `memory.txt`/`lessons.md` files: update them to assert through the API (keep the behaviours they test). Full suite PASS.
- [ ] **Step 4:** Commit `feat(state): single SQLite state store with legacy migration`.

### Task 6: reviewable learning queue with expiry

**Files:**
- Create: `aitools/learning.py`, `tests/test_learning_queue.py`
- Modify: `aitools/selfimprove.py` (new lessons → `status='proposed'`), `aitools/cli.py` (subcommand `lessons`), `ai.c` (`ai lessons …` passthrough: if `argv[1]=="lessons"`, exec `python3 ai_mcp.py lessons …`), `.agents/skills/self_improvement/SKILL.md`, `CLAUDE.md`

**Interfaces:**
- Statuses: `proposed` → (`approved` | `rejected`); `approved` → `expired` when `now > expires`. Only `approved` is injected into the system prompt.
- `learning.propose(kind, tool, signature, body) -> int` (dedupe → bumps `hits`, `last_seen`), `learning.review(interactive: bool, decide: Callable | None) -> dict` , `learning.approve(id, ttl_days=60)`, `learning.reject(id)`, `learning.expire(now) -> int` (also: approved lessons with `hits` reinforced in the last ttl window get `expires` pushed out by ttl on reinforcement), `learning.auto_policy()` reading `INFER_LEARNING_AUTO_APPROVE` (`0` default = human review; `1` = old behaviour).
- CLI: `ai lessons list [--status S]`, `ai lessons review` (TTY: shows each proposed lesson with evidence count, keys `a`pprove / `r`eject / `s`kip / `e`dit-in-$EDITOR / `q`uit), `ai lessons approve ID…`, `ai lessons reject ID…`, `ai lessons prune` (expire + delete rejected older than 30 days).

- [ ] **Step 1:** `tests/test_learning_queue.py`: proposing the same lesson twice → one row, hits=2; proposed lesson not in `lessons_render()`; approve → rendered; time-travel past `expires` + `expire()` → not rendered; reinforcement before expiry extends `expires`; `review` with a scripted `decide` approves/rejects as told; `INFER_LEARNING_AUTO_APPROVE=1` → `propose` stores approved. CLI test: `python3 ai_mcp.py lessons list` prints the table. FAIL.
- [ ] **Step 2:** Implement; wire selfimprove; add `ai lessons` passthrough in `ai.c` with an offline test running `./ai lessons list` under isolated HOME. PASS. Full suite PASS.
- [ ] **Step 3:** Commit `feat(learning): human-reviewed lesson queue with expiry`.

### Task 5b: semantic memory — local embedder, hybrid search, auto-recall

**Files:**
- Create: `aitools/embed.py` (embedder client), `aitools/search.py` (hybrid search + auto-recall), `services/ai-embed.service`, `tests/test_semantic_search.py`, `tests/fixtures/fake_embedder.py`, `evals/retrieval/` (offline retrieval set)
- Modify: `aitools/state.py` (vectors table + hooks on writes), `aitools/schemas_part*.py` + `aitools/registry.py` (new `search_memory` tool), system-prompt memory injection (wherever Task 5 left it: `state-export memory` consumer in `ai.c`/`ai_session.c`), `install.sh` (build `~/.local/share/ai/llama.cpp-embed` CPU-only from upstream if missing, download model, install unit — never touch the main llama-server build), `ai-backend` (`ai-backend embed status|start|stop`), `CLAUDE.md`

**Interfaces:**
- Embedder: llama.cpp `llama-server --embeddings --pooling mean -c 8192` on `127.0.0.1:8091` (`$AI_EMBED_URL` override), model `~/.local/share/ai/models/embedding/embeddinggemma-2-UD-Q4_K_XL.gguf` (`$AI_EMBED_MODEL`), CPU only (`-ngl 0`, `CUDA_VISIBLE_DEVICES=`), threads = min(12, nproc/2). Measured on the user's data: 12 ms/query, 151 ms per 2 KB doc; Q4 ≈ Q8 quality.
- Prompt prefixes (model card): query `task: search result | query: {q}`; document `title: {title or "none"} | text: {content}`. Mean pooling + L2 normalisation; Matryoshka truncation re-normalises.
- `embed.embed(texts: list[str], kind: "query"|"document") -> list[list[float]] | None` — None when the embedder is unreachable (short connect timeout 0.5 s; never raises to callers).
- State: table `vectors(item_type TEXT, item_id TEXT, model TEXT, dim INT, v128 BLOB, v768 BLOB, updated REAL, PRIMARY KEY(item_type, item_id))`; blobs are float16 (`array('e')`), L2-normalised per dim. Item types: `memory`, `lesson`, `chat`, `history_chunk`, `note`. Content changes re-embed (hash check).
- Indexing: writes enqueue rows in `embed_queue(item_type, item_id, enqueued)`; `ai_mcp.py embed-backfill [--limit N]` drains it and indexes all legacy items; the tool server (Task 7) drains it in the background when idle. No embedding on the hot path of `ai` startup.
- Search: `search.search_memory(query, sources=None, since=None, k=8) -> list[dict(item_type,item_id,text,score,source_ts)]` = reciprocal-rank fusion of (a) SQLite FTS5 BM25 over the same items and (b) vector search: coarse cosine on v128 over all candidates, rerank top 50 with v768. Pure Python (stdlib `array`), numpy optional fast path if importable. Falls back to FTS only when the embedder is down.
- Tool `search_memory(query, sources?, since?, k?)` added to the registry (core profile too).
- Auto-recall: system-prompt memory section = pinned core memories (≤ 1 KB, `memory_add(..., pinned=True)`) + top-k (default 6, `INFER_RECALL_K`, 0 disables) results of `search_memory(<user request>)` rendered compactly with source/date; replaces the whole 4 KB memory dump. Must add < 150 ms to startup when the embedder is up, and fall back to the old full-memory render when it is down.

- [ ] **Step 1:** `tests/fixtures/fake_embedder.py`: tiny OpenAI-compatible `/v1/embeddings` server returning deterministic hashed bag-of-words vectors (768-d) so semantic tests are offline. `tests/test_semantic_search.py`: (a) vectors stored as float16, 128/768 both L2-normalised; (b) `search_memory` ranks a paraphrase-free exact keyword hit via FTS and a word-overlap hit via vectors, fused; (c) embedder down → FTS-only results, no exception, no delay > 0.6 s; (d) content change re-embeds, unchanged content doesn't; (e) backfill indexes legacy memory/lessons/chats/history; (f) auto-recall output ≤ budget, contains pinned memory + relevant items, and falls back to full render when embedder is down; (g) `search_memory` tool registered and dispatchable. FAIL.
- [ ] **Step 2:** Implement `embed.py`, vectors/queue tables, `search.py`, tool, auto-recall hook, backfill CLI. PASS.
- [ ] **Step 3:** `services/ai-embed.service` (Restart=on-failure, `Nice=10`, `CPUQuota` not set), `install.sh` build/download/install steps (tested with HOME=tmp and stubbed git/cmake/curl), `ai-backend embed …`. `evals/retrieval/`: ≥ 40 synthetic query→item pairs (no personal data) + `run_evals.py --retrieval` reporting R@1/R@5/MRR against the real embedder when available, fake embedder in CI. Full suite PASS.
- [ ] **Step 4:** Commit `feat(memory): local EmbeddingGemma-2 embedder, hybrid search_memory, auto-recall`.
- [ ] **Step 5 (lead):** `systemctl --user enable --now ai-embed`, `ai_mcp.py embed-backfill`, spot-check `search_memory` on real data.

### Task 7: persistent tool server

**Files:**
- Create: `aitools/server.py`, `ai_toolclient.c` / `ai_toolclient.h`, `tests/test_tool_server.py`
- Modify: `ai.c` (list-tools / call-tool / render-markdown call sites → `toolclient_call()` with subprocess fallback), `Makefile` (add `ai_toolclient.c`), `aitools/mcp_client.py` (session pool), `CLAUDE.md`

**Interfaces:**
- Socket: `$XDG_RUNTIME_DIR/ai/tools.sock` (fallback `~/.cache/ai/tools.sock`), mode 0600, one server per user. Protocol: newline-delimited JSON. Request `{"id": n, "op": "hello"|"list_tools"|"call"|"render"|"shutdown", "server": s, "tool": t, "args": {...}, "text": "...", "timeout": secs}`; response `{"id": n, "ok": bool, "result": str, "error": str}`. `hello` returns `{"version": <sha256 of aitools/*.py concatenated, first 12 hex>, "pid": …}`.
- Server: `python3 ai_mcp.py serve [--idle-exit 900]`; threaded (`socketserver.ThreadingUnixStreamServer`); per-call timeout (default 120 s, `execute_command` excluded — it is native in C); MCP stdio sessions kept open per server name in a pool, restarted on EOF/timeout; exits after `--idle-exit` seconds without requests; writes `~/.cache/ai/tools.pid`.
- Client (C): `int toolclient_call(const char *op, const char *server, const char *tool, const char *args_json, int timeout_s, char **out)` returns 0 on success; on connect failure it spawns `python3 <mcp_script> serve` detached (double-fork, setsid), waits up to 2 s for the socket, retries once; on version mismatch (client compiled-in expectation is not available, so compare `hello.version` against `python3 ai_mcp.py version` run once per process only when hello disagrees with the version file `~/.cache/ai/tools.version` written by install) it sends `shutdown` and restarts; on any failure returns −1 and the caller uses the existing popen path. `INFER_TOOL_SERVER=0` disables.

- [ ] **Step 1:** `tests/test_tool_server.py` (isolated HOME + XDG_RUNTIME_DIR=tmp): start `serve --idle-exit 3`; `hello` ok; `list_tools` equals `list-tools` subprocess output; `call` of `read_file` returns same text as `call-tool`; a fake MCP server (tiny stdio JSON-RPC script in `tests/fixtures/fake_mcp.py` that records its PID) is started once across 3 calls (pool reuse); killing the fake MCP child → next call restarts it and succeeds; a tool that sleeps past `timeout` returns `ok:false` with "timeout" within timeout+1 s; server exits by itself after idle; socket perms 0600. FAIL.
- [ ] **Step 2:** Implement `server.py` + pool. PASS.
- [ ] **Step 3:** Offline binary tests: (a) with the server killed mid-run (`INFER_TOOL_SERVER=1`, mock LLM drives two tool calls; test kills server PID between them) the run still completes via fallback; (b) `INFER_TOOL_SERVER=0` never creates the socket; (c) timing: 10 sequential `read_file` calls through the binary are ≥ 3× faster with the server than without (skip the timing assert on CI with `CI=1`, log it instead). Implement `ai_toolclient.c`, wire `ai.c`. PASS. Full suite PASS.
- [ ] **Step 4:** Commit `feat(tools): persistent tool server with pooled MCP sessions`.

---

## Phase 3 — Python agent loop (Task 8 → Task 9 sequential)

### Task 8: Python engine behind `INFER_ENGINE=py`, C as front-end

**Files:**
- Create: `agent/` package (File Structure), `ai_frontend.c/.h`, `tests/test_engine_parity.py`, `tests/test_agent_unit.py`
- Modify: `ai.c` (engine switch + front-end mode), `tests/test_offline.py` (`run_binary` gains an `engine` param; module-level `pytest.mark.parametrize` via a fixture so every binary-driving test runs for `c` and `py`), `tests/conftest.py`, `Makefile`, `CLAUDE.md`

**Interfaces:**
- Engine process: `python3 -m agent --frontend-fd 3 [all ai CLI args]`. Front-end protocol over fd 3, newline-delimited JSON. Engine → front-end events: `{"ev":"text","s":…}` (stream chunk), `{"ev":"thinking","s":…}`, `{"ev":"tool_start","name":…,"args":…}`, `{"ev":"tool_end","name":…,"ok":bool,"preview":…}`, `{"ev":"approve","id":n,"command":…}` (front-end replies `{"ev":"approve_reply","id":n,"ok":bool}` after its `[Y/n]` prompt), `{"ev":"final","markdown":…}`, `{"ev":"status","s":…}`, `{"ev":"done","code":int}`. Front-end → engine: `{"ev":"input","s":…}` (interactive turn), `{"ev":"interrupt"}` (Ctrl-C/ESC), `{"ev":"set","key":"auto_approve","value":bool}` (Shift-Tab), `{"ev":"command","s":":compact"}` (colon-commands), `{"ev":"eof"}`.
- C side keeps: argument parsing for front-end-only flags, line editor + history, raw-mode key handling, `[Y/n]` approval on `/dev/tty`, markdown rendering call, spinner. When `INFER_ENGINE=py`, `main()` forks the engine with fd 3 = socketpair end and runs `frontend_loop()`; no LLM/HTTP code runs in C.
- Engine reuses: `aitools.registry.dispatch` in-process (no subprocess), `aitools.sessions.trim_messages` (the protocol-safe trimmer), `aitools.state`, `aitools.learning.lessons_render`, system-prompt assembly ported from `ai.c` (`load_system_prompt`, live context, memory, skills index, AGENTS.md cap), context budget logic ported from `apply_context_budget`, guards ported (30-iteration cap / `INFER_STEP_LIMIT`, repeated-call guard, `finish_reason: length` nudge, task timeout), `execute_command` with process-group kill and output cap, image args → `image_url` blocks, `[IMAGE_DATA_SUCCESS:…]` intercept, session save to `~/.cache/ai/sessions/last.json` + per-run file, `-r` resume via `session_transcript`.
- `agent.llm.chat(messages, tools, stream: bool, on_chunk) -> dict` (OpenAI-compatible, SSE parsing incl. tool-call deltas, retries with backoff on 5xx/connection reset, honours `INFER_TOOL_CHOICE`).

- [ ] **Step 1:** Parametrize the offline suite: `run_binary(..., engine)` sets `INFER_ENGINE`; add a session-scoped fixture `engine` with params `["c", "py"]` applied to every test that drives the binary. Run: all `py` variants FAIL (engine missing), all `c` variants PASS.
- [ ] **Step 2:** `tests/test_agent_unit.py`: SSE parser assembles fragmented tool-call arguments across 5 chunks; request builder output for a fixture conversation equals the C engine's captured request (golden from `MOCK_CAPTURE`, ignoring key order); trimming keeps system + first/latest user + tool pairs (reuse fixtures from `test_offline.py` trimming tests); approval event round-trip over a socketpair. FAIL.
- [ ] **Step 3:** Implement `agent/llm.py`, `messages.py`, `tools.py`, `native.py`, `session.py`, `loop.py`, `protocol.py`. Run unit tests: PASS.
- [ ] **Step 4:** Implement `ai_frontend.c` and the engine switch in `ai.c`. Iterate until the full parametrized offline suite passes for **both** engines, plus `tests/test_engine_parity.py`: for 6 scripted mock conversations (plain answer, tool chain, failed command loop, length-finish recovery, resume, piped stdin + image arg) the captured requests from both engines are equivalent (same message roles/contents/tool_calls modulo ids and whitespace) and stdout is equivalent after ANSI stripping. PASS.
- [ ] **Step 5:** Commit `feat(agent): Python agent loop with C terminal front-end (INFER_ENGINE=py)`.

### Task 9: flip default, `-Werror`, docs

**Files:**
- Modify: `ai.c` (default engine `py`; `INFER_ENGINE=c` keeps the legacy loop for one release, prints a one-line deprecation notice), `Makefile` (`CFLAGS += -Werror` under `make ci`; fix all current warnings: unused params, ignored `write`/`fread`/`system`/`fgets` results), `.github/workflows/ci.yml` (`make ci`, run suite for both engines, run `evals --mock --engine py` and `--engine c`), `CLAUDE.md` + `README.md` (architecture diagram: front-end ↔ engine ↔ tool server ↔ MCP pool; state store; trader deployed separately)
- Test: full suite both engines; `make ci` zero warnings

- [ ] **Step 1:** Add `tests/test_build.py::test_no_warnings`: `make clean && make CFLAGS='-O2 -Wall -Wextra -fPIC -Werror' ai` succeeds. FAIL (current warnings).
- [ ] **Step 2:** Fix warnings (check return values properly — handle errors, don't just cast to void where a failure matters, e.g. `write` to the tty in the line editor retries on EINTR). PASS.
- [ ] **Step 3:** Flip default engine, update CI and docs. Full suite both engines PASS; `python3 evals/run_evals.py --mock` PASS.
- [ ] **Step 4:** Commit `feat: Python engine by default, -Werror in CI, architecture docs`.
- [ ] **Step 5 (lead):** `./install.sh`; smoke test against the real local model: `echo "list files here" | ai -q "what is in this dir"` and one interactive turn with an approval prompt and Shift-Tab; `python3 evals/run_evals.py --engine py` against the live backend and compare with `--engine c`.

---

## Execution order and parallelism

| Wave | Tasks | Why this order |
|---|---|---|
| 0 | 1 | Live money first: after Step 7 the daemon runs from a frozen release, so later refactors cannot affect it. |
| 1 | 2 ∥ 3 ∥ 4 | Disjoint files; pure refactors + templates/CI. Task 4's `load_config` edit coordinates with Task 2 as described. |
| 2 | 5 → 6 → 5b, ∥ 7 | 6 needs the store; 7 touches `server.py`/`mcp_client.py`/`ai_toolclient.c` and only the call sites in `ai.c`, disjoint from 5/6's `ai.c` read sites. |
| 3 | 8 → 9 | Engine needs `aitools` dispatch, state store, learning render and benefits from the tool server's pool. |

Each wave ends with the lead running `make && python3 -m pytest tests/ -q`, `python3 scripts/privacy_check.py --all`, and a commit; then a fresh reviewer checks the wave's diff before the next wave starts.

## Notes for the user

- The history scrub (`bash ~/ai-buddy-scrub/scrub.sh`) needs a clean tree. Run it before Wave 0 starts or after Wave 3 finishes, not in between while agents are editing.
