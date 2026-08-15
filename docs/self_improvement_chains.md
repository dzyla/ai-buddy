# Self-Improvement Error Chains & Session Recap

What each error's own fix loop looks like, and how relevant memory is
recapitulated at session start. Builds on `docs/situational_awareness.md`
and the earlier failure-ledger work (`self_improvement_harness.md`).

## The loop

```
failure ──▶ ledger (chain_id) ──▶ recurrence ≥ N ──▶ ## PITFALL lesson
    │                                                    │
    └── later success of the same tool ──▶ ledger (same chain_id) ──▶ ## FIX lesson
                                                                            │
                            recovery ≥ INFER_CHAIN_MASTERED (default 2),
                            last event = recovery  ─────────────▶  ## MASTER lesson
                                                                            │
                            a new failure on the same chain ──▶ de-mastered
                                                                            │
                    session start ──▶ [SESSION RECAP] injected into system
                                      prompt (MASTER + recent lessons + recent
                                      sessions + flaky tools)
```

Key design points:

1. **One chain per mistake, not per occurrence.** `ai_mcp._chain_id(tool, error)`
   normalises the error (numbers collapsed, like `_err_signature`) into a stable
   `chain_<…>` id. Every failure and every recovery of that mistake carries the
   id, so "how often did this happen, how often was it fixed, and is the fix
   still holding?" is answerable from the ledger alone.

2. **The id is computed once, in C, at failure time** (`si_chain_id` in `ai.c` —
   an exact mirror of the Python normalisation, verified by
   `tests/test_self_improve_chains.py`). The recovery reuses the *stored* id from
   the per-task tracker, so a failure and its fix can never drift into
   different chains even if the error text is truncated differently on the two
   sides.

3. **MASTER is a promotion, not a one-way rite.** A chain is "mastered" only
   while its most recent ledger event is a recovery. The moment the same error
   recurs, it drops back to unmastered until it is recovered again — a stale fix
   is never blindly trusted, and the recap reflects that.

4. **Recap, not just recall.** Lessons used to surface only *when* the error
   happened. Now every session starts with a bounded (≤1600 char) `[SESSION
   RECAP]` digest: mastered chains first, then the two newest FIX/PITFALL
   lessons, the three most recent sessions from the searchable history index
   (with ids for `get_session`), and the flakiest tools from
   `~/.cache/ai/metrics.jsonl`. Disabled with `INFER_SESSION_RECAP=0`.

5. **MCP tools are first-class citizens of the loop.** `call_tool` previously
   returned the raw `tools/call` dict; `mcp_result_to_text` now normalises it to
   text and prefixes `Error: …` when the server reported `isError: true`. The C
   loop's `is_err` detection already matches the `Error:` prefix, so MCP
   failures are labelled `error`, get hints, and enter the ledger exactly like
   native tools. (Previously an MCP error came back as a JSON blob the model
   saw as "ok".)

6. **Metrics tell the truth.** `log_metric` records the real success flag and
   the error text on failure. `show-metrics` flags tools with ≥5 calls and ≥30%
   failures as `[FLAKY]` and prints the top recurring error strings per tool —
   the "what to fix first" queue for the self-improvement loop.

## Files touched

- `ai_mcp.py` — `_chain_id`, `_read_ledger`, `_chain_stats`,
  `_chain_is_mastered`, rewritten `record_failure` / `record_recovery`
  (chain-linked, MASTER promotion), `_lesson_blocks`, `session_recap`,
  `_recent_sessions_brief`, `_tool_health_brief`, `mcp_result_to_text`,
  `call_tool` (isError + JSON-RPC error handling), `log_metric` (success +
  error), `show-metrics` (flaky flag + recurring errors), `session-recap`
  CLI action; removed the dead first `_resolve_ai_bin` (it referenced
  `shutil` without importing it and was shadowed by the later definition).
- `ai.c` — `si_chain_id` (+ `chain_id` in the per-task failure tracker),
  chain id passed to `record-failure` / `record-recovery`, `session-recap`
  fetch + injection into the system prompt at session start, system-prompt
  line teaching the model what the recap is.
- `tests/test_self_improve_chains.py` — 11 tests: chain id stability and
  distinctness, MASTER promotion/demotion, failure/recovery share one chain,
  recap section assembly, empty recap, metrics failure capture, MCP result
  normalisation, a fake MCP server over real JSON-RPC returning `isError`,
  and three C-binary e2e tests (chain linking in the ledger, recap in the
  model's system message, `INFER_SESSION_RECAP=0` off-switch).

## Knobs

| Env var | Default | Meaning |
|---|---|---|
| `INFER_SELF_IMPROVE_RECURRENCE` | 2 | failures of one signature before a PITFALL lesson |
| `INFER_CHAIN_MASTERED` | 2 | recoveries of one chain before MASTER promotion |
| `INFER_SESSION_RECAP` | on | set `0` to skip the session-start recap injection |

## Manual inspection

```bash
python3 ai_mcp.py self-improve-status   # failures/recoveries/chains + [MASTERED] flags + lessons
python3 ai_mcp.py session-recap         # the exact digest the next session will be injected with
python3 ai_mcp.py show-metrics          # per-tool counts, [FLAKY] flag, top recurring errors
```

## Recommended follow-ups (not implemented)

- Gate the MASTER promotion behind a held-out regression check (Self-Harness
  style: a candidate fix is accepted only if it doesn't regress other chains).
  The de-master-on-new-failure rule is a cheaper approximation already in place.
- Feed the top recurring *metrics* errors into `record-failure`-style
  signatures automatically, so flaky tools get PITFALL lessons even when the
  model "succeeds" at working around them.
- Persist `AGENTS.md` updates for the new env vars and the recap (protected
  file — needs the owner's explicit approval).
