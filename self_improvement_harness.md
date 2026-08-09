# Self-Improvement & Learning Harness — What I Built + What's Next

This documents the changes made so the `ai` harness stops "making the same
mistake twice and forgetting it," and proposes the next improvements.

## The problem

The agent only "learned" when the model *chose* to call `skill_create` /
`skill_update` / `skill_note` / `save_memory`. Small local models rarely do that,
so every session started from zero: the same tool errors repeated forever.
Tool errors got transient hints injected into the current turn, but nothing was
persisted — no cross-session memory, no remembering of the fix.

## What I implemented (all local, no external service)

### 1. Automatic failure ledger + lesson store (`ai_mcp.py`)
New persistent state under `~/.config/ai/self_improve/`:
- `ledger.jsonl` — every tool failure and every recovery, with a normalized
  `signature` (tool + error text, numbers/paths collapsed) so the SAME mistake is
  recognized across sessions even when paths/ids differ.
- `lessons.md` — human-readable, curated-ish lessons: `## PITFALL` (recurring
  failures, auto-promoted) and `## FIX` (error → working approach).

Functions: `record_failure`, `record_recovery`, `lessons_for`, `self_improve_status`,
plus CLI actions `record-failure`, `record-recovery`, `lessons-for`, `self-improve-status`.

### 2. Harness wiring in `ai.c` (deterministic — no model discipline needed)
In the tool-result handler:
- Every tool error is logged to the ledger immediately.
- If the SAME failure signature recurs >= `INFER_SELF_IMPROVE_RECURRENCE` (default 2),
  a PITFALL lesson is auto-persisted.
- If a tool that failed earlier in the task LATER succeeds, the harness records the
  fix automatically (`record-recovery`) — the working approach becomes a FIX lesson.
- On any error, the harness queries `lessons-for` and, if a past lesson exists for
  that tool/mistake, prepends it to the tool result as
  `[REMEMBERED FROM PAST SESSIONS (self-improvement)]`, and `fprintf`s a notice.

Net effect: the agent literally cannot forget a past fix — the harness injects it
right where the error happens. This directly fixes "doesn't remember making mistakes."

### 3. Task → Plan → Execution → Tests → Solution discipline
Added a 5-phase WORKFLOW block to `SYSTEM_PROMPT` in `ai.c`:
TASK (restate via think) → PLAN (think: steps/files/risks) → EXECUTION (one step at a
time, change approach on error, never repeat the identical failing call) → TESTS
(compile+run/read output; "done" means verified, not believed) → SOLUTION (task_complete
with what/verification/lesson). Plus explicit guidance that `[REMEMBERED...]` and
`[RECURRING FAILURE]` messages are instructions to obey.

### 4. Updated `self_improvement` skill doc
Documents that the harness auto-learns; `skill_create/update/note` now add value for
things the harness can't infer (multi-step workflows, domain facts).

### Tests
- `tests/test_self_improve.py` — unit tests (signature normalization, recurrence→PITFALL,
  recovery→FIX, lessons_for matching, CLI actions). 6 tests.
- `tests/test_self_improve_e2e.py` — drives the real `ai` binary against the repo's mock
  LLM server across TWO sessions to prove cross-session memory:
  Run 1 (read_file error → success) writes PITFALL+FIX; Run 2 (same error, fresh process,
  same HOME) shows the lesson injected into the model context. 1 test.
- Ran `make test`: C unit tests PASS; `pytest` 80 passed + my 7 = the only failures are
  two **pre-existing environmental** ones (missing `pyparsing` for the Google API tests,
  and `test_ai_mcp_delegate_task` needing a live LLM endpoint that isn't running).

## Health check / suggested next improvements (priority order)

Already good: non-TTY step cap is finite (no infinite autonomy), present_plan + plan-mode
budget exist, `think` cap raised to 12, tool-loop hard-stop exists, memory/RAG exist.

1. **Remove the `think` single-call ceilings** — the harness still warns after `think_count > 12`
   in some paths and the identical-tool detector can false-positive on legitimate repeated
   reads. Prefer recurring per-phase `think` (used already for step confirmations) over a hard cap.
2. **Gate file mutations, not just shell** — today only `execute_command` + a few MCP tools are
   gated; `write_file`/`edit_file` still run freely in plan/manual mode. Route them through the
   same approval gate as `execute_command` so "ask before changing" is real.
3. **Infinite `-c` budget** — `g_continue_until_done` still allows 999999 steps; make even that
   respect a sane cap unless an explicit `INFER_TASK_TIMEOUT` is set.
4. **Auto-commit on success** (`git_commit("command")`) changes state without asking; gate or
   disable it in non-auto/plan mode, and surface a diff instead.
5. **Bridge defaults to plan mode** — Zulip is the main prepontent on the 999999-step non-interactive
   path; set `AI_PLAN_MODE=1` by default so chat-driven runs "investigate, show, ask" first.
6. **Guided `present_plan` for any mutation** — extend the existing plan gate to cover write/edit
   and delegate, so the model is structurally forced to checkpoint before mutating in plan mode.
7. **Deeper lessons**: consider feeding the top-N recent `lessons_for` hits into the system prompt
   at session start (not only on error), and optionally an embedding/FAISS index over lessons.md
   (the repo already has RAG infra) when the lesson store grows.
8. **Reprod env** — add `pyparsing` (and Google API test deps) to the test env, and provide a
   scripted way to run the two env-dependent tests, so `make test` is green end-to-end.

## How to demo (local)

```bash
make test                                  # C + offline suite
python3 -m pytest tests/test_self_improve.py tests/test_self_improve_e2e.py -v -s
# inspect the ledger/lessons accumulate under ~/.config/ai/self_improve/ after any run
python3 ai_mcp.py self-improve-status       # summary of failures/recoveries/lessons
```