# Improvements

A structured review of the `ai-buddy` repository, organized by priority.

---

## 1. Build System — Broken Makefile

**Priority: Critical**

The Makefile only compiles `ai.c`, omitting `remote_harness.c` and `cJSON.c`. Running `make` produces a non-functional binary. The test suite builds correctly (it hardcodes all three sources), which masks the issue.

**Fix:**

```makefile
SRCS = ai.c remote_harness.c cJSON.c
```

The test suite should either invoke `make` or the Makefile should match the test build exactly.

---

## 2. Memory Safety Issues

### `remote_harness.c`

- **`run_command` accepts `timeout_sec` but never enforces it.** The parameter is silently ignored. Either implement timeout enforcement (via `alarm()`/`SIGALRM`) or remove the parameter.

- **GPU `cuda_version` truncation.** `snprintf(..., "%s.%s", cc, cc+2)` with `cc` being a 32-char buffer can exceed the destination size. The compiler warns:
  ```
  remote_harness.c:207: warning: 'snprintf' output may be truncated
  ```
  Fix with explicit size limits or `strncat` with bounds.

- **`remote_connect` uses `strncpy` which does not null-terminate on truncation.** The `-1` guard on the count makes this correct in practice, but `strncpy` is error-prone. Prefer:
  ```c
  snprintf(server->hostname, sizeof(server->hostname), "%s", host);
  ```

### `ai.c`

- **`print_think_box` and friends are declared but unused.** This inflates the binary and causes CI warnings.

- **`g_last_response_len` and `reasoning_content_tok` are set but never used.** These are dead state.

---

## 3. Dead Code in `ai.c`

**Priority: High**

At least 11 unused functions and 2 unused variables confirmed by `-Wunused-*` warnings:

| Unused Function | Purpose |
|---|---|
| `print_markdown_table` | Table rendering |
| `print_think_box` | Thinking box UI |
| `print_warning_box` | Warning UI |
| `print_info_box` | Info box UI |
| `save_session_state` | Session persistence |
| `print_thinking_spinner` | Spinner animation |
| `print_token_stats` | Token counting |
| `print_json_string_unescaped` | JSON string printing |
| `notify_completion` | OS notification |
| `load_skills_from_dir` | Skill loading |
| `is_command_denied` | Command denylist |
| `g_last_response_len` | Unused variable |
| `reasoning_content_tok` | Unused variable |

**Fix:** Remove dead code, or gate with `#ifdef DEBUG` / `__attribute__((unused))` to silence warnings while preserving for potential future use.

---

## 4. Code Structure — `ai.c` (5,934 lines)

**Priority: Medium**

The file mixes CLI parsing, HTTP streaming, JSON handling, tool execution, git integration, skill loading, session management, system prompt generation, markdown rendering, and UI formatting. Key concerns:

- The `SYSTEM_PROMPT` string literal is ~4,000 characters. If the agent prompt needs to change, this string dominates the file. Consider externalizing it (already partially done via `system_prompt.md`).

- No clear boundary between tool execution and tool formatting — both live in the same function.

- The session resume logic uses file I/O with no error handling if the file is corrupt.

---

## 5. Code Structure — `ai_mcp.py` (6,183 lines)

**Priority: Highest**

This is the single biggest improvement opportunity. It contains:

- Web fetching (`fetch_webpage`, `fetch_smart`, `fetch_webpage_basic`)
- Tool routing and validation
- Google Calendar integration
- Zulip integration
- Skills loader
- Scheduler/reminders
- Session transcript parser
- Metrics collection

**All in one file.**

**Extract into separate modules:**

| Module | Contents |
|---|---|
| `web_fetch.py` | Web fetching logic (fetch_webpage, fetch_smart, fetch_webpage_basic) |
| `gcal.py` | Google Calendar integration |
| `zulip_mcp_server.py` | Zulip integration |
| `scheduler.py` | set_reminder, _deliver_reminder, run_scheduler_loop |
| `session.py` | Session transcript logic |

The current file is ~6,000 lines with no section headers beyond comments, making it very difficult to navigate or review. This is the module where most future work will happen (new tools, new integrations).

---

## 6. Testing

**Current state:** 16 Python tests, all passing, plus 1 skipped (gcal/zulip tests that need optional deps).

**Gaps:**

- **No C-level unit tests.** There's no `test_ai.c` or `test_remote_harness.c`. The integration tests exercise the binary end-to-end via subprocess, which is valuable but doesn't cover edge cases in isolation (e.g., `remote_exec` with malformed output, `build_ssh_cmd` edge cases, `remote_discover` parsing).

- **No `pytest.ini` or `pyproject.toml`.** The test configuration is implicit.

- **No `requirements-dev.txt`.** Dependencies (pytest, google-api-python-client, zulip) are only imported optionally.

**Improvements:**

1. Add a C test suite (at minimum, unit tests for `remote_harness.c` — `build_ssh_cmd`, `remote_exec` error paths, `remote_discover` parsing).
2. Add `requirements-dev.txt` with pytest, google-api-python-client, zulip.
3. Add `pytest.ini` with `norecursedirs` to exclude `__pycache__` and other artifacts.

---

## 7. Configuration & `.gitignore`

The `.gitignore` is cluttered with project-scratch files (individual test scripts, analysis outputs, one-off data files). While these should stay ignored, the pattern suggests the repo is accumulating technical debt. Consider a clean `.gitignore` with only build artifacts and a separate section for "project scratch" patterns.

Also: no `requirements.txt` or `pyproject.toml`. Dependencies (pytest, google-api-python-client, zulip) are only imported optionally. Add a `requirements-dev.txt` at minimum.

---

## 8. Documentation

**Priority: High**

- **No `README.md`.** The project is a CLI tool with no getting-started guide, no build instructions, no usage examples.
- **No `CHANGELOG.md`.**
- **No CONTRIBUTING guide.**

---

## 9. High-Priority Action Plan

| Priority | Task | Estimated Time |
|---|---|---|
| Critical | Fix the Makefile (add `remote_harness.c` and `cJSON.c` to `SRCS`) | 10 min |
| High | Remove dead code in `ai.c` (11 unused functions/variables) | 30 min |
| High | Fix `remote_harness.c` issues (unused param, truncation, strncpy) | 15 min |
| Highest | Split `ai_mcp.py` into separate modules (web_fetch, gcal, zulip, scheduler, session) | 2-3 hours |
| High | Add `README.md` (build instructions, usage, configuration) | 30 min |
| Medium | Add a C test suite (at minimum, unit tests for `remote_harness.c`) | Ongoing |
| Low | Add `requirements-dev.txt` and `pytest.ini` | 10 min |

The #1 leverage point is splitting `ai_mcp.py`. At 6,183 lines it's the hardest file to safely modify, and it's the module where most future work will happen (new tools, new integrations).
