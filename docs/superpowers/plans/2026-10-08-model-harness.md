# Model Harness Hardening Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the `ai` agent loop behave well with local reasoning models (Qwen3.8-27B, Ornith-1.5-35B-A3B) at long context: no runaway generations, no stale-thinking context bloat, sane sampling per model, fast failure when the model server is down, trustworthy benchmarks.

**Architecture:** Python engine (`agent/`) is the default and gets every feature; the deprecated C loop (`ai.c`) gets only the cheap safety parts (step token cap, sampling env). Shared logic lives in `aitools/` (trim, state). Model-serving pieces live in `ai-backend` and a tracked `services/llama-proxy.py`.

**Spec / evidence (2026-10-07/08 session):**
- Eval stalls: 3/4 runs of `collect_todos`/`inventory_txt_files` finished their tool work in ~5 ms calls, then the final request generated ~9,600 tokens until the 300 s timeout killed it (llama-server log: n_tokens 16743 → 26329, cancelled). 9/9 direct runs passed → intermittent runaway generation. `max_tokens` is 32768 per step and there is no repetition guard.
- Server penalties are 0 (`LLAMA_PRESENCE_PENALTY=0.0`, repeat 1.0) while sampling otherwise matches Qwen's thinking-mode recipe (temp 1.0, top_p 0.95, top_k 20, min_p 0) whose recommended presence_penalty is 1.5. The Python engine only sends penalties when > 0; the C engine defaults to frequency 0.10 / presence 0.05.
- Both engines store every step's `reasoning_content` and resend it; the Ornith/Qwen3.5-family chat template renders reasoning for ALL past assistant turns → up to 4,096 stale thinking tokens per step re-processed each request, and trim drops tool output before old thinking.
- `llama-proxy.py` (untracked, ~/.local/bin) waits forever when the on-demand backend fails to start (llama-server.service failed on VRAM preflight) → `ai` hangs with no error.
- Second-pass legacy import duplicated every `ledger` and `chat_audit` row (no uniqueness) — 210 rows deduped by hand.
- `evals/run_evals.py` prints nothing until the end and captures only truncated stderr; failures can't be diagnosed after the fact.

## Global Constraints
- Public repo: privacy hook must pass; no personal data; never `--no-verify`.
- Offline tests only (mock LLM, isolated HOME — root conftest isolates HOME). Never call the live model at localhost:8080/8081 from tests; never touch ~/.local/bin, ~/.local/share/ai, ~/.config/ai, systemd units, or run install.sh against the real HOME.
- Explicit user settings (INFER_* env, CLI flags) always win over new defaults.
- `make ci` (warnings as errors), full suite on both engines, `evals/run_evals.py --mock` green at the end of each task.
- Commit per task, conventional message, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; do not push.

## Review Focus
- A long but legitimate answer (e.g. a generated 300-line file in a write_file call, a long code block) must not trip the repetition guard.
- Stripping old reasoning must keep tool_calls/tool results paired and must not break `-r` resume (saved session keeps full reasoning).
- Sampling profiles must not override values the user set in the env file or on the CLI.
- Proxy fail-fast must not break the normal cold-start path (backend takes 15–60 s to load).

---

### Task H1: runaway-generation guard + per-model sampling profiles
**Files:** agent/llm.py, agent/loop.py, agent/cli.py, ai.c (cap only), ai-backend (profiles), tests/test_generation_guard.py, tests/test_ai_backend_profiles.py, tests/mock_llm_server.py (scripted repetitive stream), CLAUDE.md.
- [ ] Python engine stream guard: abort the streaming request when (a) generated completion (content + reasoning) exceeds `INFER_STEP_MAX_TOKENS` (default 16384; C engine sends it as max_tokens) or (b) a repetition loop is detected — the same ≥ 40-char window repeating ≥ 4 times in the last ~4 KB of content or reasoning, or the last 6 lines identical — tuned so long legitimate outputs (code, tables, write_file arguments) don't trigger (tests with realistic long outputs). On abort: drop the partial message, append a short user nudge ("Your previous reply got stuck repeating itself. Reply now with the next tool call or task_complete; keep reasoning brief."), retry once with presence_penalty raised to ≥ 1.5 and reasoning effort low; second abort → end the step with a clear error the model sees, not a hang. Log the event (metrics) and show a one-line notice in the front-end.
- [ ] Penalties: the Python engine sends presence/frequency penalty whenever configured (including server-profile defaults), matching C's behaviour of sending its defaults; C keeps its defaults.
- [ ] ai-backend sampling profiles: `ai-backend use` applies the model family's recommended sampling to the env file only for keys the user hasn't customised (track which keys were written by the profile, e.g. a `# ai-backend profile` marker or a sidecar JSON): Qwen3.x/Ornith (qwen35/qwen35moe archs) thinking profile = temperature 1.0, top_p 0.95, top_k 20, min_p 0.0, presence_penalty 1.5; unknown models → leave as is. `ai-backend profile [show|apply|off]`. Server penalties (LLAMA_PRESENCE_PENALTY) and client INFER_* consistent.
- [ ] Tests: mock server streams a repetition loop → guard aborts within the loop window, nudge sent, retry succeeds; long legit outputs pass; step cap enforced; profile applied on `use` but user-set keys untouched; `profile off` restores.

### Task H2: keep only recent reasoning in context
**Files:** agent/messages.py (+ loop), ai.c (request build), aitools/sessions.py (trim), tests/test_reasoning_retention.py, CLAUDE.md.
- [ ] `INFER_KEEP_REASONING` (default 2; `all` = today; `0` = none): when building each request, strip `reasoning_content` (and any inline `<think>…</think>` in content) from all assistant messages except the last N assistant messages of the current turn; never strip from the in-flight message. Saved sessions keep full reasoning (`-r` resume and debugging unaffected). Both engines identical (parity test on captured requests).
- [ ] Trim order: when over budget, first strip remaining old reasoning, then shorten old tool outputs, then drop messages (protocol-safe as today).
- [ ] Tests: 8-step tool chain → request k contains reasoning only for the last 2 assistant messages; tool_call/tool pairs intact; `all` reproduces today's requests byte-for-byte (golden); trim strips reasoning before tool output; session file still has all reasoning.

### Task H3: fail-fast serving, data dedupe, diagnosable evals
**Files:** services/llama-proxy.py (new, tracked copy of ~/.local/bin/llama-proxy.py with personal data removed), install.sh (install it), agent/llm.py + ai.c (HTTP connect/first-byte timeouts with clear error), aitools/state.py (second-pass import dedupe), evals/run_evals.py, tests.
- [ ] llama-proxy: when the backend unit fails or doesn't become healthy within `LLAMA_PROXY_START_TIMEOUT` (default 180 s), return HTTP 503 with a JSON error body containing the backend's failure reason (last lines of `journalctl --user -u llama-server` or the ai-backend preflight message), instead of waiting forever; cold start unchanged. Read the existing ~/.local/bin/llama-proxy.py (read-only) as the starting point; tracked version must pass the privacy hook.
- [ ] Engines: if the server returns 503/connection refused, print a clear one-line error naming the cause and the fix (`ai-backend status`) and exit non-zero (no hang); first-byte timeout `INFER_FIRST_BYTE_TIMEOUT` (default 300 s) for streaming requests.
- [ ] state.py second-pass import: ledger and chat_audit imports skip rows already present (natural key: all non-id columns, or a content hash column with a unique index added in a schema migration that also dedupes existing duplicates); test that re-importing a restored legacy file adds 0 rows.
- [ ] run_evals.py: print one progress line per task as it finishes (task, PASS/FAIL, steps, time); per-tier default timeouts (easy 300 s, medium 600 s, hard 1800 s; `--timeout-scale`); on failure save full stderr, the session JSON and the last raw streamed response (via INFER_DEBUG to a file in the kept workdir) and print their paths.
- [ ] Tests for each.
