# Small-Model Robustness Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `ai` CLI work reliably with small local models by forcing tool-chaining, adding visible chain-of-thought reasoning, capping context size, and fixing broken delegation.

**Architecture:** All changes live in two files: `ai.c` (C agent loop) and `ai_mcp.py` (Python tool backend). The key structural change is replacing "exit on non-tool-call response" with a mandatory `task_complete` tool enforced by `tool_choice: required` in every API payload. A new `think` tool provides visible chain-of-thought. Context is bounded by a per-output 3000-char cap and a 40KB total messages-size guard. Both new tools (`think`, `task_complete`) are handled natively in C — no Python subprocess.

**Tech Stack:** C (gcc), Python 3, libcurl, jsmn (vendored in `jsmn.h`). Build: `gcc -o ai ai.c -lcurl`. No test framework — verification is manual via running the binary with real prompts. Requires `INFER_BASE_URL`, `INFER_API_KEY`, `INFER_MODEL` env vars set.

---

## File Map

| File | What changes |
|------|-------------|
| `ai.c` | System prompt rewrite; `-q`/`INFER_QUIET` flag; `tool_choice:required` in payload; loop cap 20→30; native `think` + `task_complete` handlers; 3000-char output cap; 40KB total-size guard |
| `ai_mcp.py` | Fix `delegate_task` lookup bug; add `think` + `task_complete` schemas to `list-tools`; reorder tool list; improve descriptions; add safety fallbacks in `call-tool` |

---

## Task 1: Fix delegate_task MCP lookup bug

**Files:**
- Modify: `ai_mcp.py:958-968`

The loop at line ~962 does `for k in k` which shadows the outer variable and iterates over characters of the string instead of dict keys. MCP servers are never matched by their cleaned name.

- [ ] **Open `ai_mcp.py` and find the broken block** (around line 958, inside the `else:` branch of `call-tool` that handles unknown tools):

```python
            if not cfg:
                # Try matching clean server name
                for k in mcp_servers.keys():
                    clean_k = "".join(c if c.isalnum() or c == "_" else "_" for k in k)
                    if clean_k == server_name:
                        cfg = mcp_servers[k]
                        break
```

- [ ] **Replace it with the corrected version** (fix `for k in k` → `for c in k`):

```python
            if not cfg:
                # Try matching clean server name
                for k in mcp_servers.keys():
                    clean_k = "".join(c if c.isalnum() or c == "_" else "_" for c in k)
                    if clean_k == server_name:
                        cfg = mcp_servers[k]
                        break
```

- [ ] **Verify the fix is syntactically correct:**

```bash
python3 -c "import ai_mcp"
```

Expected: no output (clean import).

- [ ] **Commit:**

```bash
git add ai_mcp.py
git commit -m "fix: correct delegate_task MCP server name lookup loop variable"
```

---

## Task 2: Add -q / INFER_QUIET flag to ai.c

**Files:**
- Modify: `ai.c` (main function — flag parsing and env check sections)

This task only adds the flag and variable. The `quiet_mode` variable will be used by the `think` handler added in Task 6.

- [ ] **In `ai.c`, find the variable declarations at the start of `main()` (around line 676):**

```c
    int is_stdin_tty = isatty(STDIN_FILENO);
    int interactive_mode = 0;
    int auto_approve = 0;
```

Add `quiet_mode` on the next line:

```c
    int is_stdin_tty = isatty(STDIN_FILENO);
    int interactive_mode = 0;
    int auto_approve = 0;
    int quiet_mode = 0;
```

- [ ] **Find the help text block (around line 684) and add the `-q` line:**

```c
            printf("Options:\n");
            printf("  -i, --interactive    Start an interactive multi-turn chat session.\n");
            printf("  -y, --yes            Auto-approve all command execution requests without prompting.\n");
            printf("  -h, --help           Display this help screen.\n\n");
```

Replace with:

```c
            printf("Options:\n");
            printf("  -i, --interactive    Start an interactive multi-turn chat session.\n");
            printf("  -y, --yes            Auto-approve all command execution requests without prompting.\n");
            printf("  -q, --quiet          Suppress think tool reasoning output.\n");
            printf("  -h, --help           Display this help screen.\n\n");
```

- [ ] **Find the flag parsing loop (around line 711) that handles `-i` and `-y`:**

```c
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) {
            interactive_mode = 1;
        }
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) {
            auto_approve = 1;
        }
    }
```

Replace with:

```c
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) {
            interactive_mode = 1;
        }
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) {
            auto_approve = 1;
        }
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) {
            quiet_mode = 1;
        }
    }
```

- [ ] **Find the `INFER_AUTO_APPROVE` env check (around line 721) and add `INFER_QUIET` check immediately after it:**

```c
    char *env_approve = getenv("INFER_AUTO_APPROVE");
    if (env_approve && (strcmp(env_approve, "1") == 0 || strcasecmp(env_approve, "true") == 0)) {
        auto_approve = 1;
    }
```

Add after:

```c
    char *env_approve = getenv("INFER_AUTO_APPROVE");
    if (env_approve && (strcmp(env_approve, "1") == 0 || strcasecmp(env_approve, "true") == 0)) {
        auto_approve = 1;
    }

    char *env_quiet = getenv("INFER_QUIET");
    if (env_quiet && (strcmp(env_quiet, "1") == 0 || strcasecmp(env_quiet, "true") == 0)) {
        quiet_mode = 1;
    }
```

- [ ] **Also find every place `-i` and `-y` are skipped in the arg-scanning loops** (there are three such loops that skip flags when building the prompt string, around lines 779, 789, 799). Add `-q` skips to all three. Find blocks like:

```c
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
```

In each occurrence, add the `-q` skip line:

```c
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
```

- [ ] **Build and verify it compiles:**

```bash
gcc -o ai ai.c -lcurl
```

Expected: no errors or warnings.

- [ ] **Verify the flag appears in help:**

```bash
./ai --help
```

Expected: see `-q, --quiet` in the output.

- [ ] **Commit:**

```bash
git add ai.c
git commit -m "feat: add -q/--quiet flag and INFER_QUIET env var"
```

---

## Task 3: Add think + task_complete schemas, reorder tools, improve descriptions

**Files:**
- Modify: `ai_mcp.py` (the `list-tools` block and the `call-tool` routing block)

This task makes the new tools visible to the model and improves all tool descriptions. `think` goes first in the list; `task_complete` goes last.

- [ ] **In `ai_mcp.py`, find the `list-tools` block (around line 680). It starts with `if action == "list-tools":` and builds `openai_tools = []`.**

Replace the entire `openai_tools` list (from `openai_tools = []` through the `for server_name, cfg in mcp_servers.items():` loop) with the following. This reorders tools and improves descriptions:

```python
    if action == "list-tools":
        openai_tools = []

        # 1. think — first so small models see it first
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "think",
                "description": "Use before any task requiring more than one tool call. Write your plan: what you know, what you need to find, and which tools you will call in order. This reasoning is shown to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reasoning": {
                            "type": "string",
                            "description": "Your step-by-step plan for completing the task."
                        }
                    },
                    "required": ["reasoning"]
                }
            }
        })

        # 2. execute_command
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Run a shell command on the host system and return its stdout and stderr. Use for any system task, file inspection, running scripts, installing packages, or verification. Prefer this over describing commands to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to execute."
                        }
                    },
                    "required": ["command"]
                }
            }
        })

        # 3. web_search
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web using DuckDuckGo to find current information, prices, news, documentation, or facts you don't know. Always follow with fetch_webpage on at least one result URL before calling task_complete.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query."
                        }
                    },
                    "required": ["query"]
                }
            }
        })

        # 4. fetch_webpage
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "fetch_webpage",
                "description": "Download and read the text content of a URL. Use after web_search to read actual page content. Required before task_complete if search returned URLs — never present links without reading them.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL of the webpage to fetch."
                        }
                    },
                    "required": ["url"]
                }
            }
        })

        # 5. read_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of a file. Supports text files, PDFs (extracts text), and image files (PNG, JPG, JPEG, WEBP) which are shown directly in context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to read."
                        }
                    },
                    "required": ["path"]
                }
            }
        })

        # 6. write_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file, creating it and any parent directories if needed. After writing a script, always run it with execute_command to verify it works.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to write to."
                        },
                        "content": {
                            "type": "string",
                            "description": "The exact content to write to the file."
                        }
                    },
                    "required": ["path", "content"]
                }
            }
        })

        # 7. edit_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Apply a search-and-replace edit to an existing file. The search_content must match exactly including whitespace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to edit."
                        },
                        "search_content": {
                            "type": "string",
                            "description": "The exact text block to search for and replace."
                        },
                        "replace_content": {
                            "type": "string",
                            "description": "The replacement text block."
                        }
                    },
                    "required": ["path", "search_content", "replace_content"]
                }
            }
        })

        # 8. list_directory
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List the contents of a directory on the host system. Use to explore project structure before reading specific files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the directory to list. Defaults to '.' if not specified."
                        }
                    }
                }
            }
        })

        # 9. save_memory
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": "Save key facts, user preferences, or context to persistent memory. This memory is automatically loaded in subsequent runs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The exact content to store in memory. Keep it concise."
                        }
                    },
                    "required": ["content"]
                }
            }
        })

        # 10. delegate_task
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "delegate_task",
                "description": "Run a self-contained sub-task in a parallel helper agent that has full tool access. Use for independent parallel work. Give complete, standalone instructions — the agent has no memory of this conversation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The detailed, self-contained instructions for the helper agent."
                        }
                    },
                    "required": ["task"]
                }
            }
        })

        # 11. task_complete — last so model only sees it as exit
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "task_complete",
                "description": "Call this ONLY when you have the verified answer from tools. Write the full result in summary — this is the only output the user sees. Do not call this if you still have URLs to fetch, commands to run, or scripts to verify.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "The complete answer or result for the user, in markdown. Include all relevant data you gathered from tools."
                        }
                    },
                    "required": ["summary"]
                }
            }
        })

        for server_name, cfg in mcp_servers.items():
            tools = list_tools(server_name, cfg)
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {
                            "type": "object",
                            "properties": {}
                        })
                    }
                })

        print(json.dumps(openai_tools))
```

- [ ] **In the `call-tool` routing block (around line 882), add safety fallbacks for `think` and `task_complete` BEFORE the `list_directory` check:**

Find:
```python
        # Route custom tools
        if tool_name == "list_directory" or server_name == "list_directory":
```

Replace with:
```python
        # Route custom tools
        if tool_name == "think" or server_name == "think":
            # Handled natively in C; this is a safety fallback
            print('{"ok": true}')
        elif tool_name == "task_complete" or server_name == "task_complete":
            # Handled natively in C; this is a safety fallback
            print('{"ok": true}')
        elif tool_name == "list_directory" or server_name == "list_directory":
```

Note: also change the first `if` in the original routing chain to `elif` for `list_directory` since we now have items above it. Check the rest of the routing chain uses `elif` — it already does, so only the first `if` needs changing.

- [ ] **Verify syntax:**

```bash
python3 -c "import ai_mcp"
python3 ai_mcp.py list-tools | python3 -c "import sys,json; tools=json.load(sys.stdin); print(f'{len(tools)} tools, first={tools[0][\"function\"][\"name\"]}, last={tools[-1][\"function\"][\"name\"]}')"
```

Expected output: `11 tools, first=think, last=task_complete` (plus any MCP tools if configured).

- [ ] **Commit:**

```bash
git add ai_mcp.py
git commit -m "feat: add think/task_complete schemas, reorder tools, improve descriptions"
```

---

## Task 4: Rewrite system prompt in ai.c

**Files:**
- Modify: `ai.c:26-33`

- [ ] **Find the `SYSTEM_PROMPT` constant (lines 26-33):**

```c
static const char *SYSTEM_PROMPT = 
    "You are a fully autonomous CLI agent with tool-calling capabilities. Output your response in clean markdown. "
    "Your goal is to solve tasks independently and deliver verified results. Follow these strict directives:\n"
    "1. AUTOMATIC VERIFICATION: When writing scripts (Python, Bash, JS, etc.) or generating data/plots, you MUST write the code to a file (using write_file) and immediately run it (using execute_command) to verify it runs successfully. Never just present the code and tell the user to run it themselves.\n"
    "2. ITERATIVE TROUBLESHOOTING: If a command fails (you will see '[Command Failed with exit status X]' in the tool output), read the output/stderr carefully, modify the code to fix the root cause, re-run the verification command, and repeat this loop (up to 5-10 rounds) until it succeeds. Do not give up or ask the user to do it.\n"
    "3. INDEPENDENCE & PIVOTING: If a library is missing, install it (e.g. using pip install). If a data source/API is deprecated, blocked, or fails, search the web and pivot to alternative libraries/APIs or scraping strategies immediately.\n"
    "4. DELEGATION: For complex, parallelizable, or hard tasks, use the delegate_task tool to spawn helper agents to investigate or perform sub-tasks.\n"
    "5. If you search the web and snippets lack the answer, use fetch_webpage to read URLs. Never tell the user to check a website or search themselves.";
```

- [ ] **Replace it with the new concrete prompt:**

```c
static const char *SYSTEM_PROMPT =
    "You are a fully autonomous CLI agent. Output in clean markdown. Follow these rules exactly:\n\n"
    "TOOL USE:\n"
    "- Use think before any task requiring more than one tool call.\n"
    "- NEVER describe what the user can do themselves. If a tool can get the answer, use it.\n"
    "- Only call task_complete when you have verified the result yourself using tools.\n"
    "- After web_search, you MUST call fetch_webpage on at least one result URL before task_complete.\n"
    "- After writing a script with write_file, you MUST run it with execute_command to verify it works.\n\n"
    "FAILURE RECOVERY:\n"
    "- If execute_command fails, read the error output, fix the root cause, and retry. Make at least 3 attempts before giving up.\n"
    "- If a library is missing, install it. If an API is blocked, find an alternative.\n\n"
    "DELEGATION:\n"
    "- For tasks with independent parallel sub-tasks, use delegate_task to run them concurrently.\n"
    "- delegate_task agents have full tool access. Give them specific, self-contained instructions.";
```

- [ ] **Build:**

```bash
gcc -o ai ai.c -lcurl
```

Expected: no errors.

- [ ] **Commit:**

```bash
git add ai.c
git commit -m "feat: rewrite system prompt with concrete tool-use rules"
```

---

## Task 5: Add tool_choice:required and bump loop cap to 30

**Files:**
- Modify: `ai.c` (payload building ~line 991, loop cap ~line 987)

- [ ] **Find the inner loop cap (around line 987):**

```c
            while (has_more && loop_count < 20) {
```

Change `20` to `30`:

```c
            while (has_more && loop_count < 30) {
```

- [ ] **Find the payload building block (around line 990-997):**

```c
                if (tools_json && strlen(tools_json) > 10) {
                    sprintf(payload, "{\"model\":\"%s\",\"stream\":false,\"messages\":%s,\"tools\":%s}", model, messages_json, tools_json);
                } else {
                    sprintf(payload, "{\"model\":\"%s\",\"stream\":false,\"messages\":%s}", model, messages_json);
                }
```

Replace with (adds `"tool_choice":"required"` when tools are present):

```c
                if (tools_json && strlen(tools_json) > 10) {
                    sprintf(payload, "{\"model\":\"%s\",\"stream\":false,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"required\"}", model, messages_json, tools_json);
                } else {
                    sprintf(payload, "{\"model\":\"%s\",\"stream\":false,\"messages\":%s}", model, messages_json);
                }
```

- [ ] **Build:**

```bash
gcc -o ai ai.c -lcurl
```

Expected: no errors.

- [ ] **Smoke test — verify the model is forced to call a tool:**

```bash
./ai "what is 2+2"
```

Expected: model calls `think` or goes straight to `task_complete("2+2 = 4")`. It should NOT respond with a bare text answer. If your inference server does not support `tool_choice: required`, it will log an error from the API — in that case remove `,"tool_choice\":\"required\"` from the payload (the other changes still provide benefit).

- [ ] **Commit:**

```bash
git add ai.c
git commit -m "feat: add tool_choice:required and increase loop cap to 30"
```

---

## Task 6: Add think and task_complete handlers in ai.c

**Files:**
- Modify: `ai.c` — the tool dispatch section inside the `for (int tc = 0; tc < num_calls; tc++)` loop

This is the largest C change. The `think` handler prints reasoning (unless quiet) and returns `{"ok":true}`. The `task_complete` handler renders the summary, logs it, and exits the loop.

- [ ] **Find the `int task_done = 0;` needs to be declared before the `tc` loop. Find the start of the tc loop (around line 1100):**

```c
                    for (int tc = 0; tc < num_calls; tc++) {
```

Add `int task_done = 0;` on the line immediately before it:

```c
                    int task_done = 0;
                    for (int tc = 0; tc < num_calls; tc++) {
```

- [ ] **Find the tool dispatch block. It currently looks like (around line 1147):**

```c
                              if (strcmp(unescaped_name, "execute_command") == 0) {
```

Add the `think` and `task_complete` branches BEFORE the `execute_command` check. Replace:

```c
                              if (strcmp(unescaped_name, "execute_command") == 0) {
```

With:

```c
                              if (strcmp(unescaped_name, "think") == 0) {
                                  jsmn_parser arg_parser;
                                  jsmntok_t arg_toks[32];
                                  jsmn_init(&arg_parser);
                                  int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 32);
                                  char *reasoning = NULL;
                                  for (int a = 1; a < arg_r; a++) {
                                      if (arg_toks[a].type == JSMN_STRING &&
                                          arg_toks[a].end - arg_toks[a].start == 9 &&
                                          strncmp(unescaped_args + arg_toks[a].start, "reasoning", 9) == 0) {
                                          reasoning = unescape_json_string(unescaped_args + arg_toks[a+1].start,
                                                                           arg_toks[a+1].end - arg_toks[a+1].start);
                                          break;
                                      }
                                  }
                                  if (!quiet_mode && reasoning) {
                                      fprintf(stdout, "\033[2m[thinking] %s\033[0m\n", reasoning);
                                      fflush(stdout);
                                  }
                                  if (reasoning) free(reasoning);
                                  tool_output = strdup("{\"ok\":true}");
                              } else if (strcmp(unescaped_name, "task_complete") == 0) {
                                  jsmn_parser arg_parser;
                                  jsmntok_t arg_toks[32];
                                  jsmn_init(&arg_parser);
                                  int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 32);
                                  char *summary = NULL;
                                  for (int a = 1; a < arg_r; a++) {
                                      if (arg_toks[a].type == JSMN_STRING &&
                                          arg_toks[a].end - arg_toks[a].start == 7 &&
                                          strncmp(unescaped_args + arg_toks[a].start, "summary", 7) == 0) {
                                          summary = unescape_json_string(unescaped_args + arg_toks[a+1].start,
                                                                         arg_toks[a+1].end - arg_toks[a+1].start);
                                          break;
                                      }
                                  }
                                  if (summary) {
                                      log_job(current_prompt, pipe_writer, summary, interactive_mode);
                                      char *escaped_summary = shell_escape(summary);
                                      char render_cmd[4096 + strlen(escaped_summary)];
                                      snprintf(render_cmd, sizeof(render_cmd), "python3 %s render-markdown %s", mcp_script, escaped_summary);
                                      char *rendered = run_shell_command(render_cmd, NULL);
                                      if (rendered) {
                                          printf("%s\n", rendered);
                                          free(rendered);
                                      } else {
                                          printf("%s\n", summary);
                                      }
                                      free(escaped_summary);
                                      free(summary);
                                  }
                                  tool_output = strdup("{\"ok\":true}");
                                  has_more = 0;
                                  task_done = 1;
                              } else if (strcmp(unescaped_name, "execute_command") == 0) {
```

- [ ] **Find the end of the `if (call_id_tok != -1 && name_tok != -1 && args_tok != -1)` block. It ends with freeing variables (around line 1267-1273):**

```c
                              free(unescaped_id);
                              free(unescaped_name);
                              free(unescaped_args);
                              free(tool_output);
                              free(safe_output);
                              free(tool_resp);
                          }

                          current_tok = json_skip_token(tok, r, current_tok);
```

Add the `task_done` break AFTER the frees and BEFORE `current_tok`:

```c
                              free(unescaped_id);
                              free(unescaped_name);
                              free(unescaped_args);
                              free(tool_output);
                              free(safe_output);
                              free(tool_resp);
                              if (task_done) break;
                          }

                          current_tok = json_skip_token(tok, r, current_tok);
```

- [ ] **Build:**

```bash
gcc -o ai ai.c -lcurl
```

Expected: no errors.

- [ ] **Test think output is visible:**

```bash
./ai "what is the capital of France"
```

Expected: see `[thinking] ...` in dim grey before the result (or the model may skip think for a trivial question and go straight to task_complete).

- [ ] **Test quiet flag suppresses think:**

```bash
./ai -q "what is the capital of France"
```

Expected: no `[thinking]` line — result only.

- [ ] **Test task_complete exits cleanly:**

```bash
./ai "what is 5 times 7"
```

Expected: model calls `task_complete` with the answer, result is rendered, tool exits. No "here are some links" type responses.

- [ ] **Commit:**

```bash
git add ai.c
git commit -m "feat: add native think and task_complete handlers in C"
```

---

## Task 7: Add 3000-char hard cap on tool output

**Files:**
- Modify: `ai.c` — the point after `tool_output` is set, before `json_escape`

- [ ] **Find the block where `tool_output` is used to build the tool response (around line 1235). It currently looks like:**

```c
                              if (!tool_output) {
                                  tool_output = strdup("Error: failed to execute tool");
                              }

                              char *safe_output = json_escape(tool_output);
```

Insert the cap between the null-check and `json_escape`:

```c
                              if (!tool_output) {
                                  tool_output = strdup("Error: failed to execute tool");
                              }

                              /* Cap individual tool output to prevent context blowup */
                              if (strlen(tool_output) > 3000) {
                                  char *capped = malloc(3020);
                                  memcpy(capped, tool_output, 3000);
                                  strcpy(capped + 3000, "\n... [truncated]");
                                  free(tool_output);
                                  tool_output = capped;
                              }

                              char *safe_output = json_escape(tool_output);
```

- [ ] **Build:**

```bash
gcc -o ai ai.c -lcurl
```

Expected: no errors.

- [ ] **Test that a long page is truncated gracefully:**

```bash
./ai "fetch the content of https://en.wikipedia.org/wiki/Linux and summarize the first paragraph"
```

Expected: model fetches the page, gets truncated output (you'll see `... [truncated]` in debug mode), and summarizes what it received. Run with `INFER_DEBUG=1` to see the truncated tool result in stderr.

- [ ] **Commit:**

```bash
git add ai.c
git commit -m "feat: cap individual tool output at 3000 chars to bound context size"
```

---

## Task 8: Add 40KB total-size guard on messages

**Files:**
- Modify: `ai.c` — same location as Task 7, just after the 3000-char cap

- [ ] **Immediately after the 3000-char cap block added in Task 7, add the total-size guard:**

```c
                              /* Cap individual tool output to prevent context blowup */
                              if (strlen(tool_output) > 3000) {
                                  char *capped = malloc(3020);
                                  memcpy(capped, tool_output, 3000);
                                  strcpy(capped + 3000, "\n... [truncated]");
                                  free(tool_output);
                                  tool_output = capped;
                              }

                              /* If total context is already large, stub this result */
                              if (strlen(messages_json) > 40000) {
                                  free(tool_output);
                                  tool_output = strdup("[context limit reached — result omitted to preserve model focus]");
                              }

                              char *safe_output = json_escape(tool_output);
```

- [ ] **Build:**

```bash
gcc -o ai ai.c -lcurl
```

Expected: no errors.

- [ ] **Test a long multi-step task to confirm it doesn't blow up:**

```bash
./ai -y "list all files in /etc, read the first 5 text files you find, and summarize what each one is for"
```

Expected: completes without the model going incoherent. With `INFER_DEBUG=1` you can watch the payload sizes. After ~40KB of messages accumulates, new tool results will be replaced with the stub.

- [ ] **Commit:**

```bash
git add ai.c
git commit -m "feat: add 40KB total-size guard — stub new tool results when context is large"
```

---

## Task 9: Final rebuild and smoke test

**Files:** None — build and verify only.

- [ ] **Clean rebuild:**

```bash
gcc -o ai ai.c -lcurl 2>&1
```

Expected: clean compile, no warnings.

- [ ] **Test 1 — web task that previously failed (the motivating example):**

```bash
./ai "check nvidia stock price"
```

Expected behaviour:
1. `[thinking] I need to search for NVIDIA stock price, then fetch a result URL to get the actual data.`
2. `[ai] calling MCP tool 'web_search'...`
3. `[ai] calling MCP tool 'fetch_webpage'...` (on at least one URL)
4. Final rendered answer with the actual price — NOT "here are some links you can visit"

- [ ] **Test 2 — verify task_complete is the exit, not bare text:**

```bash
./ai "what year was Python created"
```

Expected: model calls `task_complete` with "Python was created in 1991." No plain text response.

- [ ] **Test 3 — verify thinking can be silenced:**

```bash
INFER_QUIET=1 ./ai "what is the current time"
```

Expected: result only, no `[thinking]` lines.

- [ ] **Test 4 — verify quiet flag works:**

```bash
./ai -q "list files in the current directory"
```

Expected: model runs `execute_command ls` and returns result. No thinking output.

- [ ] **Test 5 — interactive mode still works:**

```bash
./ai -i
```

Expected: interactive prompt appears, conversation works turn by turn, `exit` quits cleanly.

- [ ] **Final commit tagging the feature complete:**

```bash
git add -A
git status  # confirm only ai and ai_mcp.py have changes (nothing else)
git commit -m "feat: complete small-model robustness overhaul

- Force tool-chaining via tool_choice:required + task_complete exit
- Add visible think tool for chain-of-thought reasoning
- Cap tool output at 3000 chars; stub new results past 40KB total context
- Reorder tools, improve descriptions with when-to-use triggers
- Rewrite system prompt with concrete rules and tool chain examples
- Fix delegate_task MCP server name lookup bug
- Add -q/INFER_QUIET to suppress think output"
```
