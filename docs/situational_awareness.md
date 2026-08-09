# Situational Awareness in the `ai` Harness — Changes, Hard-Case Tests, Findings

Small local models (7B–35B) drop context and lose track of where they are
mid-task. They can't reliably "remember" the step number, what already
succeeded, or what just failed — so they repeat mistakes, jump steps, or act on
a stale picture of the world. The harness compensates by *externalizing* state
into the prompt (see `docs/agent_loop_engineering.md` → "Externalize memory").

This doc records the changes made so the `ai` harness gives the local model an
explicit, always-present picture of its own situation, plus a hard-case test
suite that proves (deterministically, against the mock LLM) that these
mechanisms actually reach the model.

---

## 1. What I changed (`ai.c`)

### 1.1 `[CURRENT STATE]` header on every tool result
A bounded, per-task rolling log of tool outcomes (`stepN:tool=ok/ERR;`) is
maintained automatically as tools execute. Each tool result is prepended with:

```
[CURRENT STATE step N] <tool> -> ok|error  |  <rolling log>
<tool>
<original output>
```

- The model always sees its **step number**, the **status of this call**, and its
  **recent trajectory** — without having to hold it in working memory.
- The log is **bounded** (newest entries survive; oldest are trimmed) so it
  can't bloat context.
- It is skipped for control tools (`think`, `task_complete`), and can be
  disabled entirely with `INFER_STATE_CONTEXT=0`.

### 1.2 Error-guidance now reaches the *model*, not just the terminal
`[HINT: ...]` and `[GRAPH ENFORCEMENT: ...]` auto-guidance was computed for tool
errors but was only appended to the terminal display (`full_content`), never to
the actual tool message sent to the model. The small model that actually needs
the guidance never saw it. These are now appended to the model-visible tool
output too.

### 1.3 Fixed: failed commands were labelled `ok`
The harness's `is_err` check didn't recognize the real command-failure format
`$ <cmd> — failed (exit N)` (em-dash), so a failed `execute_command` was
recorded/displayed as **success**. `is_err` now also matches `failed (exit`
and `[SYSTEM WARNING:`, which:
- fixes the state header + box label for command failures,
- fixes the self-improvement failure detector (`si_failed`) for the same
  reason (it previously only looked for the double-hyphen `-- failed (exit`).

---

## 2. Hard-case test suite (`tests/test_situational_awareness.py`)

Deterministic, offline (mock LLM), asserts what the binary *actually sends* to
the model. **6 tests, all passing:**

| Test | Hard case it covers |
|------|---------------------|
| `test_tool_result_carries_situational_state_header` | Header appears, step increments, rolling log shows `1:read_file=ok` then `2:read_file=ERR`, payload intact |
| `test_state_header_disabled_with_env` | `INFER_STATE_CONTEXT=0` disables the header without dropping output |
| `test_think_cap_blocks_runaway_reasoning_but_allows_progress` | Repeating `think` is capped (>12) but does **not** brick the run; a later real tool still executes |
| `test_tool_error_guidance_reaches_model` | GRAPH ENFORCEMENT guidance is injected into the model's tool message (not just the terminal) |
| `test_execute_command_error_hint_reaches_model` | `[HINT: File or path not found. ...]` reaches the model for command failures, labelled `error` |
| `test_state_header_not_added_to_control_messages` | `think`/`task_complete` results are not wrapped in a state header |

The suite reuses the existing `MockServer`/mock-LLM conventions, so it's fast
and safe (no real model needed, runs in an isolated HOME).

**Regression status:** the new suite plus all existing permission-mode, history,
self-improvement, offline, and dev-local-verification tests pass
(`32 + 23 = 55` tests green). The two known pre-existing environment failures
(Google API tests needing `pyparsing`; `delegate_task` needing a live LLM
endpoint) are unrelated to these changes.

---

## 3. Live validation

The binary was rebuilt and smoke-tested against the running local llama.cpp
server (BigBang v1, Q4_K_M). The agent loop fires, streams at ~24 tok/s, and
completes cleanly — no crash. The deterministic offline tests are the primary
proof that the state header and guidance reach the model's context.

---

## 4. Findings & recommended next steps (priority order)

1. **The thinking-model repetition guard is the next big win.** The `frequency
   penalty` / token-stall nudges exist, but a model that keeps emitting
   `think`-then-tool without converging isn't fully covered. Consider a
   "still producing new work but no new tool state" detector (progress-aware
   termination) rather than only identical-call detection.

2. **Token-aware trimming, not character-based.** The docs recommend token (not
   char) thresholds for `INFER_TRIM_THRESHOLD`/`INFER_STUB_THRESHOLD`. If a real
   tokenizer is unavailable cheaply, at least document the heuristic.

3. **Consolidate the two failure-detection paths.** `is_err` and
   `si_failed` had divergent logic (one missed `— failed (exit`) — the
   broadening in this change aligns them, but a single shared "did this tool
   fail?" helper would prevent future drift.

4. **`[CURRENT STATE]` could embed plan/progress.** The header currently tracks
   step + outcome trajectory. Feeding the approved `present_plan` steps and
   checked-off progress into the same block would give small models a
   first-class "what's left in my approved plan" view in plan mode.

5. **Skip the `[STATE]` header for already-`[CURRENT STATE]`-prefixed cached
   results** to avoid a double wrap (minor).

6. **Auto-commit is a footgun** (this session's work got committed with a
   generic message automatically). Consider gating `git_commit("command")`
   behind the approved-mutation gate or a `--no-commit` default, per
   `agent_harness_review.md` §F.

---

## 5. Files touched

- `ai.c` — state-log globals + reset, rolling-log append, `[CURRENT STATE]`
  header injection, err-hint/graph-enforcement routing to the model, `is_err`
  broadening.
- `tests/test_situational_awareness.py` — new hard-case suite (6 tests).
- `Makefile` — unchanged (feature is inside `ai.c`).