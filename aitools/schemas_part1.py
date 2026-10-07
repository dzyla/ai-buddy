"""Native tool schemas, part 1 of 2 (OpenAI function format, list-tools order)."""

SCHEMAS_PART1 = [

    # 1. think — first so small models see it first
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Plan, reflect, or analyze before taking action. Use this tool before complex steps, to verify your previous actions, or to correct course. Highly recommended for multi-step tasks. Keep reasoning concise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Brief plan or reflection (≤100 words): what you are analyzing and what steps you will take next."
                    }
                },
                "required": ["reasoning"]
            }
        }
    },


    # 2. search_files — locate code/symbols without grep/rg via execute_command
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search file contents with a regex pattern (target='content', default) or find files by name (target='files', glob pattern like '*.py'). Returns matches as file:line: text, or a file list / match counts. Far more token-efficient than running grep/rg/find via execute_command. Constrained to 'path' (default '.'); never search from /.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex to search for (content) or filename glob (files target, e.g. '*.md')."},
                    "path": {"type": "string", "description": "Directory or file to search in (default current dir)."},
                    "target": {"type": "string", "enum": ["content", "files"], "description": "'content' searches inside files (default); 'files' finds files by name."},
                    "file_glob": {"type": "string", "description": "Only search files matching this glob (content target), e.g. '*.py'."},
                    "limit": {"type": "integer", "description": "Max results (default 50)."},
                    "context": {"type": "integer", "description": "Context lines around each match (default 0)."},
                    "output_mode": {"type": "string", "enum": ["content", "files_only", "count"], "description": "'content' (default) shows matching lines; 'files_only' lists files; 'count' shows per-file match counts."}
                },
                "required": ["pattern"]
            }
        }
    },

    # 2b. todo — session task list the model keeps fresh (file-backed)
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Maintain a task list for multi-step work (3+ steps). Call with NO arguments to read the current list. Pass a 'todos' array of {id, content, status} objects to create it (merge=false, default) or update existing items by id (merge=true). status is one of: pending, in_progress, completed, cancelled. Mark items completed immediately when done; only ONE item in_progress at a time. The list persists to disk so it survives interruptions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Task items. Omit entirely to read the current list.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Unique item id."},
                                "content": {"type": "string", "description": "What the step is."},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]}
                            },
                            "required": ["id", "content"]
                        }
                    },
                    "merge": {"type": "boolean", "description": "true: update existing items by id and add new ones. false (default): replace the whole list."}
                }
            }
        }
    },

    # 2c. clarify — ask the user when the task is ambiguous
    {
        "type": "function",
        "function": {
            "name": "clarify",
            "description": "Ask the user a question and wait for their answer before continuing. Use when the task is ambiguous and a wrong guess wastes real work: give 2-4 numbered 'choices' (first = recommended; the user can also type their own). Omit choices for an open-ended question. In non-interactive runs (bridges, schedulers) it returns a note telling you to pick a sensible default and proceed — never block waiting for a human.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question itself (no options inside the text)."},
                    "choices": {"type": "array", "items": {"type": "string"}, "description": "Up to 4 options as separate strings; the user picks one or types their own. Put the recommended option FIRST."},
                    "multi_select": {"type": "boolean", "description": "true: user may pick several options."},
                    "timeout_seconds": {"type": "integer", "description": "Optional: give up waiting after N seconds and proceed with a default."}
                },
                "required": ["question"]
            }
        }
    },

    # 2d. browser — persistent headless Chromium (state survives across calls)
    {
        "type": "function",
        "function": {
            "name": "browser",
            "description": "Drive a persistent headless Chromium browser for JS-only sites, forms and interactive pages (fetch_webpage cannot do these). Actions: goto(url), content(), html(), links(), click(selector), fill(selector, value), select(selector, value), press(key), js(expr), wait(selector?), back(), screenshot(path?), status(), shutdown. Page state (current URL, inputs, JS context) persists across browser calls. For a plain static page prefer fetch_webpage — it is faster.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["goto", "content", "html", "links", "click", "fill", "select", "press", "js", "wait", "back", "screenshot", "status", "shutdown"]},
                    "url": {"type": "string", "description": "For goto."},
                    "selector": {"type": "string", "description": "CSS selector for click/fill/select/wait."},
                    "value": {"type": "string", "description": "For fill/select."},
                    "key": {"type": "string", "description": "For press: Enter, Tab, Escape, ..."},
                    "expr": {"type": "string", "description": "For js: a JS expression to evaluate."},
                    "wait_ms": {"type": "integer", "description": "Timeout in ms for the action."},
                    "max_chars": {"type": "integer", "description": "Cap for content/html output."},
                    "path": {"type": "string", "description": "For screenshot: where to save the PNG."}
                },
                "required": ["action"]
            }
        }
    },

    # 2e. delegation & context pool (were hidden before — now first-class)
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": "Register a named persistent agent (stored under ~/.config/ai/agents). Use together with resume_agent to run a recurring helper agent that keeps its own conversation history across invocations. For one-shot parallel work prefer delegate_task instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short name for the agent (e.g. 'researcher')."},
                    "prompt": {"type": "string", "description": "What the agent is for (stored as its mission)."},
                    "tools": {"type": "array", "items": {"type": "string"}, "description": "Optional allowed tool list."},
                    "budget": {"type": "integer", "description": "Optional thinking token budget for reasoning (e.g. 2048, 4096, 8192)."}
                },
                "required": ["name", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resume_agent",
            "description": "Send a message to a previously spawned agent (agent_id from spawn_agent/list_agents) and return its response. The agent's history persists on disk between resumes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "message": {"type": "string", "description": "The user message for this turn."},
                    "budget": {"type": "integer", "description": "Optional reasoning thinking token budget override for this turn."}
                },
                "required": ["agent_id", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "List all registered agents (id, name, status, mission). Read-only.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_context_pool",
            "description": "Store a snippet of context in the shared pool that persists across agents/sessions (e.g. a key finding, a decision, a path). Other agents and future sessions can retrieve it with search_context / get_context_snippet.",
            "parameters": {
                "type": "object",
                "properties": {"entry": {"type": "string", "description": "The text to store."}},
                "required": ["entry"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_context",
            "description": "Search the shared context pool (from append_to_context_pool and command output) for an entry containing the query. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_context_snippet",
            "description": "Fetch one stored context-pool entry by its numeric index. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {"index": {"type": "string"}},
                "required": ["index"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "structured_query",
            "description": "Query a file or command output like a pipeline: target='file:/path' or 'command:ls -la', plus filter (regex), transform (head:N | tail:N | sort | unique), aggregate (count | first | last). Use to count/grep/summarise without writing a script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "'file:<path>', 'command:<cmd>', or literal text."},
                    "filter": {"type": "string", "description": "Regex — keep only matching lines."},
                    "transform": {"type": "string", "description": "head:N, tail:N, sort, unique."},
                    "aggregate": {"type": "string", "description": "count, first, last."}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "session_report",
            "description": "Log the outcome of this session (success bool, failure modes, notes) to ~/.config/ai/session_outcomes.json. Call near task_complete for non-trivial sessions so the self-improvement loop has outcome data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "Did the session achieve its goal?"},
                    "failure_modes": {"type": "array", "items": {"type": "string"}, "description": "What went wrong (empty if none)."},
                    "notes": {"type": "string", "description": "One-line summary of the outcome."}
                }
            }
        }
    },

    # 3. execute_command
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Run a shell command on the host system and return its stdout and stderr. Use for any system task, file inspection, or quick scripts. IMPORTANT: For long-running tasks (e.g. training, servers, heavy builds), DO NOT use this tool as it blocks the main thread and locks the GUI. Instead, use `start_background_process` to detach the job so you can monitor or stop it if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to execute."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional timeout in seconds. Defaults to 120s. For tasks requiring more than a few minutes, use `start_background_process` instead."
                    }
                },
                "required": ["command"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "python_playground",
            "description": "Execute a snippet of Python code in an isolated interactive sandbox. Returns evaluated expressions, print outputs, and exceptions without leaving scratch files on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code or expression to run in the playground."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional timeout in seconds (default 30s)."
                    }
                },
                "required": ["code"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "js_playground",
            "description": "Execute a snippet of JavaScript / Node.js in an isolated sandbox. Returns console outputs and evaluated expressions without leaving scratch files on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The JavaScript code or expression to run in the playground."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional timeout in seconds (default 30s)."
                    }
                },
                "required": ["code"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "execute_remote_command",
            "description": "Run a command on a remote host via SSH. You must have passwordless SSH access (e.g. key-based) already configured for the host.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "The SSH host (e.g., user@hostname or just hostname)."
                    },
                    "command": {
                        "type": "string",
                        "description": "The bash command to run remotely."
                    }
                },
                "required": ["host", "command"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "remote_exec",
            "description": "Remote Server Control Tool. Connect, discover, execute commands, monitor resources, submit jobs on a remote server cluster. Supports connection lifecycle management, auto-discovery of compute nodes, HPC job submission (Slurm/PBS/Torque), and resource monitoring.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action to perform: connect, disconnect, discover, status, exec, mount, jobs, submit",
                        "enum": ["connect", "disconnect", "discover", "status", "exec", "mount", "jobs", "submit"]
                    },
                    "host": {
                        "type": "string",
                        "description": "Remote server hostname or IP (required for connect)"
                    },
                    "port": {
                        "type": "integer",
                        "description": "SSH port (default: 22)"
                    },
                    "user": {
                        "type": "string",
                        "description": "SSH username (required for connect)"
                    },
                    "pass": {
                        "type": "string",
                        "description": "SSH password (optional - prefer key-based auth)"
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute (required for exec, submit)"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Command timeout in seconds (default: 120)"
                    }
                },
                "required": ["action"]
            }
        }
    },

    # 3. web_search
    {
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
    },

    # 3b. arxiv_search
    {
        "type": "function",
        "function": {
            "name": "arxiv_search",
            "description": "Search the arXiv API for scientific papers. Returns metadata and abstracts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. e.g. 'electron' or 'au:smith'"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max number of results to return (default 5, max 10)."
                    }
                },
                "required": ["query"]
            }
        }
    },

    # 4. fetch_webpage
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "Download and read any URL. This is the robust default: it uses a browser TLS fingerprint (curl_cffi) to get past Cloudflare and bot-walls, and auto-escalates to a headless browser for JS-rendered pages when needed. Use it for essentially every page. Required before task_complete if search returned URLs — never present links without reading them.",
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
    },

    # 4b. fetch_smart
    {
        "type": "function",
        "function": {
            "name": "fetch_smart",
            "description": (
                "Preferred fetch tool. Downloads a URL using browser TLS fingerprint impersonation "
                "(curl_cffi) to bypass Cloudflare and similar bot protections. Automatically escalates "
                "to Playwright+stealth for JS-rendered pages if the fast path is blocked. "
                "Use this for any URL that might be protected, JS-heavy, or from a news/media site. "
                "Falls back to fetch_webpage if curl_cffi is not installed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch."
                    }
                },
                "required": ["url"]
            }
        }
    },

    # 5. read_file
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file. Supports text files, PDFs (extracts text), "
                "and image files (PNG, JPG, JPEG, WEBP) which are shown in context. "
                "For large text files an outline + head/tail is returned automatically. "
                "Use start_line and end_line to read a specific section of a large file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path to the file to read."
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to return (1-based, inclusive). Omit to start from the beginning."
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to return (1-based, inclusive). Omit to read to the end."
                    }
                },
                "required": ["path"]
            }
        }
    },

    # 6. write_file
    {
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
    },

    # 7. edit_file
    {
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
    },

    # 7b. patch_file
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": "Atomically apply one or multiple search-and-replace hunks to a file with fuzzy whitespace matching. If any hunk fails to match, no changes are written.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path to the file to patch."
                    },
                    "hunks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "search": {"type": "string", "description": "Text chunk to search for."},
                                "replace": {"type": "string", "description": "Text chunk to replace with."}
                            },
                            "required": ["search", "replace"]
                        },
                        "description": "Array of {search, replace} objects to apply in order."
                    },
                    "search": {"type": "string", "description": "Single search chunk if not using hunks."},
                    "replace": {"type": "string", "description": "Single replace chunk if not using hunks."}
                },
                "required": ["path"]
            }
        }
    },

    # 7c. get_environment
    {
        "type": "function",
        "function": {
            "name": "get_environment",
            "description": "Get detailed runtime environment info: OS, Python version, working directory, git branch/status, GPU model and memory usage, and backend model settings.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },

    # 8. list_directory
    {
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
    },

    # 9. save_memory
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Add key facts, user preferences, or context to persistent memory (appended; duplicates skipped; oldest lines evicted past 4 KB). Pinned lines are loaded into every later run; the rest are recalled when relevant to the request (and via search_memory).",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact(s) to add, one per line. Keep it concise."
                    },
                    "replace": {
                        "type": "boolean",
                        "description": "Rewrite the whole memory with content instead of appending (use only to clean up or correct it)."
                    },
                    "pinned": {
                        "type": "boolean",
                        "description": "Core fact/preference to inject into EVERY prompt (keep pinned memory under 1 KB). Unpinned memories are recalled only when relevant."
                    }
                },
                "required": ["content"]
            }
        }
    },

    # 10. remember — save to FTS5 memory database
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Save key facts, user preferences, or context to long-term persistent memory using SQLite FTS5 full-text search. Memories can be later retrieved via the 'recall' tool.",
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
    },

    # 10b. recall — search memories
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search long-term memories using natural language full-text search (SQLite FTS5). Returns matching saved memories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query — can be a word, phrase, or FTS5 boolean expression."
                    }
                },
                "required": ["query"]
            }
        }
    },

    # 10c. list_processes — top 10 running processes
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "Return the top 10 running processes sorted by CPU usage.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },

    # 10d. start_background_process
    {
        "type": "function",
        "function": {
            "name": "start_background_process",
            "description": "Start a command in the background, writing stdout/stderr output to a log file for status & health monitoring.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command line string to run asynchronously in background."
                    },
                    "log_file": {
                        "type": "string",
                        "description": "Optional log file path. Defaults to ~/.config/ai/logs/proc_<timestamp>.log if omitted."
                    }
                },
                "required": ["command"]
            }
        }
    },

    # 10e. check_process_status
    {
        "type": "function",
        "function": {
            "name": "check_process_status",
            "description": "Inspect a background process state, read recent log output, evaluate health status, and receive a decision summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "integer",
                        "description": "PID of the background process to inspect."
                    },
                    "log_file": {
                        "type": "string",
                        "description": "Path to the log file to inspect."
                    }
                },
                "required": []
            }
        }
    },

    # 10f. stop_process
    {
        "type": "function",
        "function": {
            "name": "stop_process",
            "description": "Terminate a running background process by PID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "integer",
                        "description": "PID of the process to terminate."
                    }
                },
                "required": ["pid"]
            }
        }
    },
]
