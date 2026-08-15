# Implementation Plan for ai-buddy Improvements

## Overview
Implement changes from `/home/dzyla/Code/ai-buddy/improvements.md` (18 tasks), verify each step compiles and passes unit tests, and create comprehensive test suites for local AI functionality.

## Task Roadmap

1. [ ] **Task 8: Add Pattern Denylist for Auto-Commit**
   - Check staged files against denylist (`.env`, `*.key`, `*.pem`, `.ssh/`, etc.) in `git_commit()`.
2. [ ] **Task 12: Add `--no-copy` Validation**
   - Verify `g_copy_enabled` before invoking clipboard copy on `task_complete`.
3. [ ] **Task 13: Validate Memory File on Read**
   - Check JSON start symbol `{` or `[` and file size limit in `read_memory_file()`.
4. [ ] **Task 4: Make `trim_threshold` Configurable**
   - Replace `#define TRIM_THRESHOLD` with runtime variable, check `INFER_TRIM_THRESHOLD` env var and `--trim-threshold` CLI flag.
5. [ ] **Task 1: Unify Tool Dispatch (Remove Hardcoded C Handlers)**
   - Remove C `execute_command` and `remote_exec` inline handlers in `ai.c`. Route through Python MCP server.
6. [ ] **Task 2: Fix Session Leak on Interrupt During Tool Call**
   - Append `[INTERRUPTED...]` tool response message to `messages_json` when `g_esc_requested` is caught.
7. [ ] **Task 3: Add Fallback on `compact_session` Failure**
   - Validate length and structure of compacted output. Retain original session on malformed output.
8. [ ] **Task 14: Inject Current Time Into Every Session**
   - Prepend ISO current time system prompt context on session init; preserve time in compacting.
9. [ ] **Task 10: Update `install.sh` to Use Makefile**
   - Change `install.sh` build logic to invoke `make clean && make`.
10. [ ] **Task 11: Move `remote_harness.c` to a Shared Library**
    - Build `libremote_harness.so` in `Makefile` and link dynamically.
11. [ ] **Task 15: RLM Phase 1 (Context Pool)**
    - Implement `get_context_snippet` and `search_context` tools in `ai_mcp.py`.
12. [ ] **Task 16: Native Programmatic Tool Calling (`tool_chain`, `structured_query`)**
    - Add `tool_chain` and `structured_query` tools to `ai_mcp.py`.
13. [ ] **Task 17: Persistent Multi-Agent Orchestration Phase 1**
    - Add `spawn_agent`, `resume_agent`, `list_agents` tools to `ai_mcp.py`.
14. [ ] **Task 18: Self-Improving Continual Harness Phase 1**
    - Add failure logging and `session_report` tool in `ai_mcp.py`.
15. [ ] **Task 7 & 9: Token Counting & `--tokenizer` Flag**
    - Implement heuristic/Python token counter in `ai.c` and `count-tokens` command in `ai_mcp.py`.
16. [ ] **Task 6: Circuit Breaker for Stalled Agent Loops**
    - Add repetition detector in agent loop to force `task_complete` if 3+ identical responses occur.
17. [ ] **Task 5: Split `ai.c` Into Modules**
    - Refactor `ai.c` into `ai_session.c`, `ai_terminal.c`, `ai_http.c`, `ai_git.c`, `ai_agent.c`. Update Makefile.
18. [ ] **Comprehensive Test Suite & Final Verification**
    - Create/update unit and integration tests for all new MCP tools and C features.
    - Run `make clean && make`, run pytest, verify with local AI test.
