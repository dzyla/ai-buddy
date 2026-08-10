# Hard-Case Harness Test: "Develop a PDB chain-interaction tool from scratch"

This records a live hard-case test of the `ai` small-model harness against the
local 35B model (endless-frontier BigBang-v1, Q4_K_M on llama.cpp), plus the
harness changes that turned a total failure into a working, verified deliverable.

## The challenge
Ask the local model, through the harness, to *develop a program from scratch*:
a fast, PISA-style PDB chain-interaction analyzer (parse a PDB, group by chain,
report per-chain-pair contacts, interacting residue pairs, and buried interface
surface area) in Go, then build and verify it on a real PDB (1BRS, barnase/barstar).

## Baseline: complete failure (zero artifacts)
Command: `ai -y "<develop prompt>"` against the stock harness.

- The model emitted its **entire implementation design as one huge native
  `reasoning_content` block** (674 lines / ~8k tokens), then got truncated at
  `max_tokens=8192` **before it ever emitted a tool call**.
- The harness nudged "call task_complete", the model re-looped into `thinking`,
  and the session **ended having written no file and built nothing**.
- Two runs reproduced this identically: the model's reasoning alone consumed the
  whole completion budget, so no action tool call ever arrived.

### Root cause (evidence-backed)
Not an infinite-reasoning *loop* — an oversized **single** reasoning burst:

1. `max_tokens_val = 8192` (ai.c). The 35B model's native `reasoning_content`
   counts against this budget. It routinely writes several thousand tokens of
   reasoning before its first tool call, so the stream is cut off mid-reasoning.
2. There was **no progress/productivity watchdog** for reasoning-only turns, and
   no guidance that reasoning must be brief before acting.
3. No directive to **reuse existing libraries**, so the model was planning to
   hand-write a PDB parser from scratch.

## Harness changes (ai.c)
1. **`max_tokens` 8192 → 32768** (default; still `INFER_MAX_TOKENS`-overridable).
   The single most impactful fix: gives the model room to finish its reasoning
   AND emit its action tool call, so tasks no longer die mid-thought. Comment
   documents why 8192 was too small.
2. **Think-length cap** (`g_think_max_chars`, default 2200, `INFER_THINK_MAX_CHARS`):
   an oversized `think`'s reasoning is truncated and the tool result becomes an
   "Error: ... take your next concrete action (write_file/execute_command)" nudge,
   so a single monster think can't stall the task.
3. **Productivity watchdog** (`g_think_since_action`, limit via
   `INFER_THINK_ACTION_LIMIT`, default 3): N consecutive think/read-only turns
   with no action tool force an "STOP thinking, take ONE concrete action" error.
   Incremented on `think`, reset on any non-readonly tool (reuses the existing
   `tool_is_readonly()`).
4. **Reasoning-aware truncation nudge**: a token-limited response is told it
   "wrote an over-long chain of thought ... emit ONE action tool call now", not
   just "call task_complete".
5. **THINK DISCIPLINE** prompt directive: keep `think` to 2–3 short sentences;
   never dump the plan/code into thinking; zero write/build calls after a few
   turns = failing, stop and ACT.
6. **REUSE BEFORE WRITING** prompt directive (added after user steer): before
   implementing any parser/algorithm, search pkg.go.dev/GitHub/PyPI/crates.io and
   build AROUND an existing library; name it in the summary.

## New deterministic hard-case tests (tests/test_situational_awareness.py)
- `test_oversized_think_capped_and_nudges_to_act` — a >2200-char `think` is capped
  and turns into an act-now error; the monolithic reasoning is not replayed.
- `test_consecutive_think_without_action_triggers_productivity_watchdog` —
  repeated think-only turns fire "STOP thinking/act now" without bricking a later
  real action.
Full suite: **115 tests pass** (113 prior + 2 new).

## Rerun (improved harness, same dev task): SUCCESS
The model:
- wrote `main.go` (PDB parsing) — note: in the first improved run it still
  hand-wrote the parser; after the user steered "don't reinvent the wheel", a
  second run reused the existing **`github.com/tikz/bio/pdb`** package
  (`import pdblib "github.com/tikz/bio/pdb"`) and only wrote the interaction
  analysis itself — exactly the requested reuse.
- built `./pdbtool`, ran it on `1brs.pdb`, `1brs.pdb --cutoff 3.5`, and `4hhb.pdb`.
- wrote 5 Go unit tests (`TestBuriedSurfaceArea`, `TestRadiusFor`,
  `TestRunOn1brs`, `TestRunOn1brsCutoff`, `TestRunOnMissingFile`), debugged and
  fixed two (a wrong BSA test case + `exec.Output()`→`CombinedOutput()`), all pass.
- called `task_complete` with a summary that names the reused package.

Verified output (top pairs = barnase/barstar interfaces, correct):
```
B-E 541 contacts, A-D 518, C-F 403   (1brs.pdb, cutoff 4.5 Å)
```
Artifact: `/home/dzyla/Code/ai-buddy/pdbtool/` (`main.go`, `main_test.go`, `pdbtool`,
`go.mod`, `go.sum`; prior hand-written version kept as `main_reinvent.go`).

During the run the harness's own self-improvement surfaced a past
`execute_command` fix on a failure, and `[CURRENT STATE step N]` headers tracked
the whole trajectory — both mechanisms visible in the session transcript.

## Findings / further work
- The single highest-leverage setting for this 35B local model was the completion
  budget; a "reasoning-only truncation" detector could also cap on the *stream*
  (stop accepting new reasoning once a budget is spent) rather than just nudging
  the next turn.
- `max_tokens` should ideally scale with the model's context window / known
  reasoning length rather than a fixed constant.
- The recurring `conda` error trace in background-run stdout is environmental
  (unrelated to these changes) and non-blocking; worth isolating separately.
- Consider a "no write/build within N turns" hard fail-fast for *code-generation*
  tasks specifically, distinct from the general productivity watchdog.

## Files touched
- `ai.c` — max_tokens default, think-length cap, productivity watchdog,
  reasoning-aware truncation nudge, THINK DISCIPLINE + REUSE BEFORE WRITING prompt
  directives.
- `tests/test_situational_awareness.py` — 2 new hard-case tests.
- `.agents/skills/go_pdb_chain_analysis/SKILL.md` — new reuse skill.
- `pdbtool/` — the working deliverable and experiment logs (baseline/rerun/reuse
  prompts + run logs).
