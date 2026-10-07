"""Native tool schemas, part 2 of 2 (OpenAI function format, list-tools order)."""

SCHEMAS_PART2 = [

    # 11. delegate_task
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": (
                "Spawn N helper agents that run IN PARALLEL and return their combined results. "
                "Use ONLY for independent sub-tasks (each agent must not depend on another's output). "
                "Always pass 'tasks' as an array — even for a single task. "
                "Example: fetch and summarise 3 papers → tasks:[\"Fetch https://... and summarise\", \"Fetch https://... and summarise\", ...]. "
                "Each task string must be fully self-contained with all context (URLs, file paths, goals). "
                "For fetching multiple URLs, prefer parallel_fetch instead — it is faster and needs no per-URL instructions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of self-contained task instructions. Each runs in its own agent concurrently."
                    },
                    "budget": {
                        "type": "integer",
                        "description": "Optional thinking token budget per helper agent (e.g. 2048, 4096)."
                    }
                },
                "required": ["tasks"]
            }
        }
    },

    # 11. parallel_fetch
    {
        "type": "function",
        "function": {
            "name": "parallel_fetch",
            "description": (
                "Fetch multiple URLs concurrently and return all page contents in one call. "
                "Use instead of multiple sequential fetch_webpage calls whenever you need 2+ pages. "
                "Example use cases: reading several search results, fetching multiple papers/docs, "
                "multi-site comparison, publication digest. "
                "Each result is capped at 10 KB and labelled with its URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of URLs to fetch concurrently."
                    }
                },
                "required": ["urls"]
            }
        }
    },

    # 12. load_skill
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Explore and load domain skills. Call with no argument (empty name) to list all available skills with descriptions. Call with a skill name to read its full guidance. Always call with no argument first if you are unsure what skills exist, then load the relevant one before starting domain work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill directory name to load (e.g. 'bio_structure_analysis'). Omit or leave empty to list all available skills."
                    }
                }
            }
        }
    },

    # 13. fetch_webpage_js — Playwright-based for JS-protected sites
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage_js",
            "description": (
                "Explicit Playwright override: renders the page with a real headless browser + stealth patches "
                "(playwright-stealth, realistic 1920×1080 viewport, lazy-load scroll). "
                "Use only when you need direct control over JS rendering or wait_for behaviour. "
                "For most cases, prefer fetch_smart which cascades automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch with a headless browser."
                    },
                    "wait_for": {
                        "type": "string",
                        "enum": ["networkidle", "load", "domcontentloaded"],
                        "description": "When to consider the page ready. 'networkidle' (default) waits for no network activity — best for SPAs. 'load' waits for the load event. 'domcontentloaded' is fastest but may miss late-rendered content."
                    }
                },
                "required": ["url"]
            }
        }
    },

    # 14. get_system_status — quick CPU/RAM/disk snapshot
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "Return a compact one-line summary of CPU usage, RAM, and disk. Useful for quickly checking system resources before running heavy operations.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },

    # 15. get_clipboard — read X11/Wayland clipboard
    {
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Read the current system clipboard content (X11 via xclip or Wayland via wl-paste). Returns the plain text currently on the clipboard, or an error if nothing is available.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },

    # Continuous Local Learning
    {
        "type": "function",
        "function": {
            "name": "learn_rule",
            "description": "Save a permanent system rule or constraint to your dynamic rules file. These rules are injected into your system prompt on all future runs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_text": {
                        "type": "string",
                        "description": "The exact text of the rule to learn."
                    }
                },
                "required": ["rule_text"]
            }
        }
    },
    # Scheduled Context Resets
    {
        "type": "function",
        "function": {
            "name": "reset_context",
            "description": "Clear your conversational history to prevent context window bloat. Use this after completing a sub-task or when transitioning to a new topic to regain full reasoning capacity.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    # Graph Vault Tools
    {
        "type": "function",
        "function": {
            "name": "vault_write",
            "description": "Create or update a Markdown note in the Obsidian Graph Vault. Used to store long-term structured knowledge, agentic state, and plans.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "The title of the note (will become the filename)."},
                    "content": {"type": "string", "description": "The Markdown content of the note."},
                    "links": {"type": "string", "description": "Optional comma-separated list of other note titles to link to (e.g. 'Project Alpha, Database Schema')."}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_read",
            "description": "Read the full Markdown content of a specific note from the Graph Vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_search",
            "description": "Perform a full-text FTS5 search across all notes in the Graph Vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vault_backlinks",
            "description": "Traverse the Knowledge Graph by finding all notes that link to (mention) a specific note title via [[WikiLinks]].",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"}
                },
                "required": ["title"]
            }
        }
    },

    # pubmed_search
    {
        "type": "function",
        "function": {
            "name": "pubmed_search",
            "description": (
                "Semantic search across 50M+ biomedical abstracts (PubMed, BioRxiv, MedRxiv, arXiv). "
                "Returns titles, authors, journals, DOIs, abstracts, and relevance scores. "
                "Use iteratively: search → digest abstracts → refine query → search again → synthesise. "
                "Always report DOIs so manuscripts can be retrieved. "
                "Requires PUBMED_API_KEY (or MSS_API_KEY) environment variable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query (3–500 chars), e.g. 'CRISPR base editing off-target effects'."
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (5–10, default 10).",
                        "default": 10
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Restrict to papers published on or after this date (YYYY-MM-DD). Optional."
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Restrict to papers published on or before this date (YYYY-MM-DD). Optional."
                    },
                    "high_quality_only": {
                        "type": "boolean",
                        "description": "Exclude papers with missing or very short abstracts (default true).",
                        "default": True
                    }
                },
                "required": ["query"]
            }
        }
    },

    # Google Calendar tools
    {
        "type": "function",
        "function": {
            "name": "gcal_list_events",
            "description": "List events from Google Calendar in a specified time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": "Start time in ISO 8601 format (e.g. '2026-07-01T00:00:00-06:00' or '2026-07-01T00:00:00Z'). Defaults to current time."
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End time in ISO 8601 format. Defaults to 7 days from start time."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return. Defaults to 20.",
                        "default": 20
                    },
                    "calendar_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of calendar IDs to search. Omit or set to ['all'] to search all of your selected Google calendars."
                    }
                }
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "gcal_create_event",
            "description": "Create a new event in Google Calendar with exact start/end times. For natural-language phrases ('lunch with Sam tomorrow 1pm'), prefer gcal_quick_add instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Title/summary of the calendar event."
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time, ISO 8601 (e.g. '2026-07-01T14:00:00'). A timezone offset is optional — without one it is interpreted in the user's local timezone (or the time_zone arg). Use a bare date ('2026-07-01') for an all-day event."
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time, ISO 8601 (e.g. '2026-07-01T15:00:00'). Offset optional. Use a bare date for all-day events."
                    },
                    "time_zone": {
                        "type": "string",
                        "description": "Optional IANA timezone name (e.g. 'America/New_York') for naive start/end times. Defaults to the system local timezone."
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional detailed description of the event."
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional location of the event."
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of attendee emails."
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Optional ID of the calendar to create the event in. Defaults to 'primary' (your main calendar)."
                    }
                },
                "required": ["summary", "start_time", "end_time"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "gcal_check_availability",
            "description": "Check free/busy time slots for Google Calendar to query availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": "Start time in ISO 8601 format (e.g. '2026-07-01T08:00:00-06:00')."
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End time in ISO 8601 format (e.g. '2026-07-01T17:00:00-06:00')."
                    },
                    "calendar_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of calendar IDs to check. Defaults to ['primary']."
                    }
                },
                "required": ["time_min", "time_max"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "gcal_quick_add",
            "description": "Create a Google Calendar event from a natural-language phrase — Google parses the date/time/title for you. Best default for casual event creation, e.g. 'Dentist appointment next Tuesday at 3pm' or 'Lunch with Sam tomorrow 12-1pm'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Natural-language description including when and what, e.g. 'Team sync Friday 10am'."
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Optional calendar ID. Defaults to 'primary'."
                    }
                },
                "required": ["text"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "gcal_update_event",
            "description": "Reschedule or modify an existing Google Calendar event. Only the fields you pass are changed. Call gcal_list_events first to get the event_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID of the event to update (from gcal_list_events)."},
                    "summary": {"type": "string", "description": "New title (optional)."},
                    "start_time": {"type": "string", "description": "New start time, ISO 8601 (optional). Offset optional; naive times use time_zone or local tz."},
                    "end_time": {"type": "string", "description": "New end time, ISO 8601 (optional)."},
                    "description": {"type": "string", "description": "New description (optional)."},
                    "location": {"type": "string", "description": "New location (optional)."},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "Replacement attendee email list (optional)."},
                    "time_zone": {"type": "string", "description": "Optional IANA timezone for naive start/end times."},
                    "calendar_id": {"type": "string", "description": "Optional calendar ID. Defaults to 'primary'."}
                },
                "required": ["event_id"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "gcal_delete_event",
            "description": "Cancel/delete a Google Calendar event by its ID. Call gcal_list_events first to get the event_id. This is irreversible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID of the event to delete (from gcal_list_events)."},
                    "calendar_id": {"type": "string", "description": "Optional calendar ID. Defaults to 'primary'."}
                },
                "required": ["event_id"]
            }
        }
    },

    # 11b. check_time
    {
        "type": "function",
        "function": {
            "name": "check_time",
            "description": "Returns the current local date, time, and day of the week.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },

    # schedule_task
    {
        "type": "function",
        "function": {
            "name": "schedule_task",
            "description": (
                "MANDATORY replacement for `sleep` delays. NEVER use execute_command with sleep for timed work — "
                "it blocks the terminal and the Zulip bridge, hanging the session with no response. "
                "Use this tool instead for ANY deferred, timed, or periodic work: desktop notifications, "
                "reminders, polling folders, checking job status, sending messages when a condition is met. "
                "This returns IMMEDIATELY — the task runs in a fully detached background process. "
                "IMPORTANT: Every polling task MUST have an explicit termination condition. "
                "Always set max_runs (e.g. 20) OR ttl_hours (e.g. 4.0) as a safety guardrail in case the "
                "sub-agent never calls unschedule_task — this prevents orphaned background tasks. "
                "For ONE-SHOT tasks: use run_once=True (guaranteed single execution, no agent needed) "
                "or end the prompt with 'then call unschedule_task(task_id)'. "
                "For POLLING tasks: include the unschedule_task call in the agent prompt AND set max_runs. "
                "After calling this, immediately call task_complete to confirm the schedule to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "A unique snake_case identifier, e.g. 'notify_go_home' or 'check_binders_done'."
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "Full self-contained instruction for the background agent. Must include everything it needs. "
                            "For polling: always end with 'If done, call unschedule_task(task_id). Else do nothing.' "
                            "Example one-shot: 'Run: execute_command(\"notify-send \\\"Go home!\\\" \\\"Time to leave 🏠\\\"\"); "
                            "then call unschedule_task(notify_go_home).' "
                            "Example polling: 'Count files in /data/results. If count >= 10000: send Zulip private message "
                            "to user@example.com saying how many passed filters; then call unschedule_task(check_binders). Else do nothing.'"
                        )
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "description": (
                            "How often to run (minimum 10s). "
                            "For one-shot reminders, set this to the full delay (e.g. 300 for 5 minutes). "
                            "For polling, set to a shorter check interval (e.g. 120 for every 2 min)."
                        )
                    },
                    "run_once": {
                        "type": "boolean",
                        "description": (
                            "If true, the task is guaranteed to run exactly once after interval_seconds and then stop — "
                            "no matter what the sub-agent does. Use this for simple one-shot reminders/notifications "
                            "instead of relying on the sub-agent to call unschedule_task. Default is false."
                        )
                    },
                    "max_runs": {
                        "type": "integer",
                        "description": (
                            "Safety cap: automatically cancel the task after this many agent invocations, "
                            "regardless of whether unschedule_task was called. Use 0 (default) for unlimited. "
                            "STRONGLY RECOMMENDED for all polling tasks — set to the maximum reasonable number "
                            "of checks (e.g. 20 for a task polling every 5min over ~2h). "
                            "Prevents orphaned tasks if the agent never calls unschedule_task."
                        )
                    },
                    "ttl_hours": {
                        "type": "number",
                        "description": (
                            "Safety expiry: automatically cancel the task after this many hours since creation, "
                            "regardless of run count. Use 0 (default) for unlimited. "
                            "STRONGLY RECOMMENDED as a backstop (e.g. 4.0 for a task expected to finish in ~2h). "
                            "Prevents orphaned tasks from surviving across reboots or multi-day runs."
                        )
                    }
                },
                "required": ["task_id", "prompt", "interval_seconds"]
            }
        }
    },


    # set_reminder
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": (
                "Set a one-shot reminder that is delivered straight to Zulip at a future time — "
                "use this for 'remind me tomorrow to ...', 'ping me in 2 hours to ...', etc. "
                "Preferred over schedule_task for reminders: delivery is deterministic (no LLM run) so it "
                "can't be garbled. Convert natural phrases like 'tomorrow 9am' into an ISO timestamp yourself "
                "using the current local time from the system context, and pass it as `when`. "
                "When the request comes from Zulip chat, the recipient is filled in automatically — you only "
                "need `message` and `when`. After calling this, call task_complete to confirm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The reminder text to send back to the user, e.g. 'Submit the grant report'."
                    },
                    "when": {
                        "type": "string",
                        "description": "Absolute delivery time as an ISO 8601 timestamp, e.g. '2026-07-05T09:00:00'. Convert 'tomorrow 9am' etc. yourself from the current time. Provide this OR delay_seconds."
                    },
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Alternative to `when`: how many seconds from now to fire (e.g. 7200 for 2 hours)."
                    },
                    "zulip_to": {
                        "type": "string",
                        "description": "Recipient email for a Zulip DM. Optional — from the Zulip bridge this defaults to the requester automatically."
                    },
                    "zulip_stream": {
                        "type": "string",
                        "description": "Alternatively, a Zulip stream/channel name to post the reminder to."
                    },
                    "zulip_topic": {
                        "type": "string",
                        "description": "Topic for the stream message (defaults to 'Reminders'). Only used with zulip_stream."
                    }
                },
                "required": ["message"]
            }
        }
    },


    # unschedule_task
    {
        "type": "function",
        "function": {
            "name": "unschedule_task",
            "description": "Cancel a previously scheduled background task and stop its recurring execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The unique identifier of the task to cancel/stop."
                    }
                },
                "required": ["task_id"]
            }
        }
    },

    # list_scheduled_tasks
    {
        "type": "function",
        "function": {
            "name": "list_scheduled_tasks",
            "description": "List all currently scheduled background tasks with their ID, prompt, interval, and last run time.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },

    # 12. present_plan — investigate → plan → ask before changing
    {
        "type": "function",
        "function": {
            "name": "present_plan",
            "description": "PLAN MODE: Investigate first, then present your findings and the ordered list of exact changes you intend to make, and wait for approval before making ANY change. Call this before modifying state (write/edit files, run state-changing commands, save memory, schedule, delegate subagents, etc.). On approval you get a BOUNDED number of state-changing actions (INFER_PLAN_STEP_BUDGET, default 8): execute those steps ONE at a time, validate each result, and when the budget is exhausted call present_plan again with the next chunk. Never start a new change without an in-force approval. The user approves or rejects; do NOT proceed with changes until approved.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": "Your findings so far, the exact changes or commands you intend to run, and your reasoning / suggestions. Be concrete."
                    }
                },
                "required": ["plan"]
            }
        }
    },

    # 13. Searchable conversation history — learn from past sessions
    {
        "type": "function",
        "function": {
            "name": "search_history",
            "description": "Search all past conversations (backed up locally) with full-text search. Use this to learn from earlier sessions — recall how a previous problem was solved, which commands worked, what the user asked before, etc. Returns matched snippets + session IDs; then call get_session to read a full conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to find in past conversations."},
                    "limit": {"type": "integer", "description": "Max results (default 8)."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_sessions",
            "description": "List the most recent backed-up conversations (session id, size, path). Use it to see what you've worked on recently or to find a session_id for get_session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of recent sessions to list (default 12)."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_session",
            "description": "Load a full backed-up conversation by session_id as a readable transcript. Use after search_history / list_sessions to read the entire prior conversation in context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session id, e.g. sess_1786247517 or 'last'."},
                    "max_chars": {"type": "integer", "description": "Cap on transcript length (default 4000)."}
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rebuild_history_index",
            "description": "Rebuild the full-text search index over all backed-up conversations. Normally automatic; call if history search seems stale or returned nothing.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_zulip_chats",
            "description": "Search and inspect past Zulip chat interactions stored in persistent Zulip memory. Returns timestamp, status, duration, query snippets, and error traces. Use this to investigate Zulip issues, recall recent Zulip discussions, or troubleshoot bridge errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional keyword or session_id to search in Zulip chat history."},
                    "limit": {"type": "integer", "description": "Max results to return (default 10)."},
                    "error_only": {"type": "boolean", "description": "If true, only return chats that ended in error, fallback, or empty output."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_zulip_chat",
            "description": "Load the complete, raw JSON log for a specific Zulip interaction by its session_id (including prompt sent to model, stdout, stderr, delivered reply, and timing).",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The Zulip session ID, e.g. 'zulip_1786934893_12345'."}
                },
                "required": ["session_id"]
            }
        }
    },

    # 14. Continuous self-improvement: skill_create / skill_update / skill_note
    {
        "type": "function",
        "function": {
            "name": "skill_create",
            "description": "SELF-IMPROVEMENT: Persist what you learned during this session into a reusable skill so future sessions inherit it. Call this after completing a non-trivial task, discovering a useful technique/workaround, or when a skill states something you found to be wrong. Saved to the repo .agents/skills AND ~/.config/ai/skills. The user is notified when a skill is created.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name (directory, e.g. 'pandas_merge_fix')."},
                    "description": {"type": "string", "description": "One-line description: when to use this skill."},
                    "content": {"type": "string", "description": "Full skill body: numbered steps, exact commands, pitfalls, verification, sources."}
                },
                "required": ["name", "description", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_update",
            "description": "SELF-IMPROVEMENT: Add a 'good to know' note or discrepancy fix to an existing skill after you loaded it and learned something new or found it wrong/outdated. The user is notified when a skill is updated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the existing skill to update."},
                    "note": {"type": "string", "description": "Concise note: what to fix / what you learned, with any exact command or correction."}
                },
                "required": ["name", "note"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_note",
            "description": "SELF-IMPROVEMENT: Append a standalone learning note to the persisted skills learning log without editing any skill body. Lower-commitment than skill_create/update.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Optional skill name to associate the note with."},
                    "note": {"type": "string", "description": "The lesson / insight to remember across sessions."}
                },
                "required": ["note"]
            }
        }
    },

    # 14. task_complete — last so model only sees it as exit
    {
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
    },
]
