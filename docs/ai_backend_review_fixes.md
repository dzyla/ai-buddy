# ai-backend review fixes — working plan

Safety rule for this session: **do not restart, stop, or kill the live llama-server**
(serves the current agent session). All verification is read-only unit tests, `status`/`list`
(subcommands that never touch the server), or `--help`-style invocations. Never run
`serve`, `stop`, `tune`, or `persist_env_changes` live during this work.

| # | Step | Status |
|---|------|--------|
| 1 | Pubmed key: no hardcoded secret; inherit from env; `AI_BACKEND_ENV_FILE` override | done |
| 2 | `cmd_probe`: define it, exec `dev/probe_mtp.py` (was NameError) | done |
| 3 | Port coupling: `LLAMA_PORT` pin, serve prefers stored port, conditional env write, tune pins child | pending |
| 4 | Stop safety: pidfile-first kill; curl resume/retry for HF downloads | pending |
| 5 | Cleanup: gguf-py import dedup (portable path), `status` draft-cache line, log prefix unify | pending |
| 6 | Tests: `tests/test_ai_backend.py`; run pytest suite | pending |

Per-step: implement -> test -> mark done here -> commit.
