"""Tool registry: native schemas, argument normalisation/validation and the dispatch table.

TOOL_SCHEMAS            the native tool catalog, in `list-tools` order (MCP tools are appended by the CLI).
HANDLERS                tool name -> handler `(arguments) -> str`, in the former call-tool if/elif order.
prepare(tool, args)     normalise + validate arguments -> (arguments, error_text or None).
invoke(server, tool, args, mcp_servers=None) -> (text, exit_code)   run one prepared call.
dispatch(server, tool, args) -> str     prepare + invoke; returns exactly what `call-tool` prints.

A call routes to the first HANDLERS entry whose name equals the tool name OR the server name
(ai.c passes `<server> <tool>`; native tools use the tool name for both), else to the MCP
server named `server` from the MCP config.
"""
import json
import re

from . import calendar_tools, handlers
from .mcp_client import call_tool, load_config, mcp_result_to_text
from .schemas_part1 import SCHEMAS_PART1
from .schemas_part2 import SCHEMAS_PART2

TOOL_SCHEMAS = SCHEMAS_PART1 + SCHEMAS_PART2

def normalize_tool_arguments(tool_name, arguments):
    if isinstance(arguments, str):
        if tool_name in ("execute_command", "execute_remote_command"):
            return {"command": arguments}
        elif tool_name in ("read_file", "write_file", "edit_file", "list_directory"):
            return {"path": arguments}
        elif tool_name in ("web_search", "recall", "search_context", "arxiv_search", "search_memory"):
            return {"query": arguments}
        elif tool_name in ("fetch_webpage", "fetch_smart"):
            return {"url": arguments}
        elif tool_name == "think":
            return {"reasoning": arguments}
        elif tool_name == "task_complete":
            return {"summary": arguments}
        elif tool_name == "save_memory":
            return {"content": arguments}
        return arguments

    if not isinstance(arguments, dict):
        return arguments

    alias_map = {
        "execute_command": ("command", ["cmd", "command_line", "CommandLine", "args", "script", "code", "c"]),
        "execute_remote_command": ("command", ["cmd", "command_line", "CommandLine", "args", "script", "code", "c"]),
        "read_file": ("path", ["file", "filepath", "filename", "file_path", "p"]),
        "write_file": ("path", ["file", "filepath", "filename", "file_path", "p"]),
        "edit_file": ("path", ["file", "filepath", "filename", "file_path", "p"]),
        "list_directory": ("path", ["dir", "directory", "folder", "path_name", "p"]),
        "web_search": ("query", ["q", "search", "prompt", "term", "keywords"]),
        "arxiv_search": ("query", ["q", "search", "prompt", "term", "keywords"]),
        "fetch_webpage": ("url", ["uri", "link", "address", "u"]),
        "fetch_smart": ("url", ["uri", "link", "address", "u"]),
        "think": ("reasoning", ["thought", "thoughts", "plan", "reason"]),
        "task_complete": ("summary", ["text", "response", "result", "message", "final_answer", "answer"]),
        "save_memory": ("content", ["memory", "text", "entry"]),
        "search_memory": ("query", ["q", "search", "prompt", "term", "keywords", "text"]),
    }

    if tool_name in alias_map:
        target, aliases = alias_map[tool_name]
        if target not in arguments or not arguments[target]:
            for alias in aliases:
                if alias in arguments and arguments[alias]:
                    val = arguments[alias]
                    if isinstance(val, list):
                        val = " ".join(str(x) for x in val)
                    arguments[target] = val
                    break

    if tool_name in ("execute_command", "execute_remote_command") and isinstance(arguments.get("command"), list):
        arguments["command"] = " ".join(str(x) for x in arguments["command"])

    return arguments


TOOL_REQUIRED_ARGS = {
    "execute_command": ["command"],
    "web_search":      ["query"],
    "arxiv_search":    ["query"],
    "fetch_webpage":   ["url"],
    "fetch_smart":     ["url"],
    "read_file":       ["path"],
    "write_file":      ["path", "content"],
    "edit_file":       ["path", "search_content", "replace_content"],
    "save_memory":     ["content"],
    "remember":        ["content"],
    "recall":          ["query"],
    "search_memory":   ["query"],
    "delegate_task":   ["tasks"],
    "parallel_fetch":  ["urls"],
    "think":           ["reasoning"],
    "task_complete":   ["summary"],
    "computer_control": ["action"],
    "learn_rule":             ["rule_text"],
    "reset_context":          [],
    "vault_write":            ["title", "content"],
    "vault_read":             ["title"],
    "vault_search":           ["query"],
    "vault_backlinks":        ["title"],
    "pubmed_search":          ["query"],
    "pubmed_research_round":  ["query"],
    "start_background_process": ["command"],
    "check_process_status": [],
    "stop_process": ["pid"],
    "search_files": ["pattern"],
    "clarify": ["question"],
    "browser": ["action"],
    "spawn_agent": ["name", "prompt"],
    "resume_agent": ["agent_id", "message"],
    "get_context_snippet": ["index"],
    "search_context": ["query"],
    "structured_query": ["target"],
    "schedule_task":   ["task_id", "prompt", "interval_seconds"],
    "unschedule_task": ["task_id"],
    "list_scheduled_tasks": [],
    "get_system_status": [],
    "get_clipboard": [],
    "list_processes":   [],
    "remote_exec":      ["action"],
}

def repair_json(s):
    """Best-effort repair of common small-model JSON mistakes."""
    if not s:
        return "{}"
    s = s.strip()

    # Remove markdown code block markers if model wrapped JSON in ```json ... ```
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    # Repair raw unescaped newlines/tabs inside JSON string literals
    out = []
    in_string = False
    escape = False
    for c in s:
        if in_string:
            if escape:
                out.append(c)
                escape = False
            elif c == '\\':
                out.append(c)
                escape = True
            elif c == '"':
                out.append(c)
                in_string = False
            elif c == '\n':
                out.append('\\n')
            elif c == '\r':
                out.append('\\r')
            elif c == '\t':
                out.append('\\t')
            else:
                out.append(c)
        else:
            if c == '"':
                in_string = True
            out.append(c)

    if in_string:
        out.append('"')

    fixed_str = "".join(out)

    # Remove trailing commas before } or ]
    fixed_str = re.sub(r',\s*([}\]])', r'\1', fixed_str)

    # Balance unclosed braces/brackets
    in_str = False
    esc = False
    stack = []
    for c in fixed_str:
        if esc:
            esc = False
        elif c == '\\':
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == '{' or c == '[':
                stack.append('}' if c == '{' else ']')
            elif c == '}' or c == ']':
                if stack and stack[-1] == c:
                    stack.pop()
    while stack:
        fixed_str += stack.pop()

    return fixed_str


# Tools kept by `list-tools` under INFER_TOOL_PROFILE=core (small-context models).
CORE_TOOL_PROFILE = (
    "think", "execute_command", "read_file", "write_file", "edit_file",
    "list_directory", "search_files", "web_search", "fetch_webpage",
    "save_memory", "search_memory", "load_skill", "todo", "present_plan",
    "start_background_process", "check_process_status", "task_complete",
)


HANDLERS = {
    "think": handlers.h_think,
    "task_complete": handlers.h_task_complete,
    "execute_remote_command": handlers.h_execute_remote_command,
    "remote_exec": handlers.h_remote_exec,
    "check_time": handlers.h_check_time,
    "schedule_task": handlers.h_schedule_task,
    "set_reminder": handlers.h_set_reminder,
    "unschedule_task": handlers.h_unschedule_task,
    "list_scheduled_tasks": handlers.h_list_scheduled_tasks,
    "arxiv_search": handlers.h_arxiv_search,
    "list_directory": handlers.h_list_directory,
    "web_search": handlers.h_web_search,
    "fetch_webpage": handlers.h_fetch_webpage,
    "fetch_smart": handlers.h_fetch_smart,
    "fetch_webpage_js": handlers.h_fetch_webpage_js,
    "save_memory": handlers.h_save_memory,
    "remember": handlers.h_remember,
    "recall": handlers.h_recall,
    "search_memory": handlers.h_search_memory,
    "execute_command": handlers.h_execute_command,
    "get_context_snippet": handlers.h_get_context_snippet,
    "search_context": handlers.h_search_context,
    "structured_query": handlers.h_structured_query,
    "spawn_agent": handlers.h_spawn_agent,
    "resume_agent": handlers.h_resume_agent,
    "list_agents": handlers.h_list_agents,
    "session_report": handlers.h_session_report,
    "list_processes": handlers.h_list_processes,
    "start_background_process": handlers.h_start_background_process,
    "check_process_status": handlers.h_check_process_status,
    "stop_process": handlers.h_stop_process,
    "read_file": handlers.h_read_file,
    "write_file": handlers.h_write_file,
    "edit_file": handlers.h_edit_file,
    "patch_file": handlers.h_patch_file,
    "get_environment": handlers.h_get_environment,
    "delegate_task": handlers.h_delegate_task,
    "parallel_fetch": handlers.h_parallel_fetch,
    "load_skill": handlers.h_load_skill,
    "computer_control": handlers.h_computer_control,
    "pubmed_research_round": handlers.h_pubmed_research_round,
    "get_system_status": handlers.h_get_system_status,
    "get_clipboard": handlers.h_get_clipboard,
    "learn_rule": handlers.h_learn_rule,
    "reset_context": handlers.h_reset_context,
    "vault_write": handlers.h_vault_write,
    "vault_read": handlers.h_vault_read,
    "vault_search": handlers.h_vault_search,
    "vault_backlinks": handlers.h_vault_backlinks,
    "pubmed_search": handlers.h_pubmed_search,
    "gcal_list_events": calendar_tools.h_gcal_list_events,
    "gcal_create_event": calendar_tools.h_gcal_create_event,
    "gcal_check_availability": calendar_tools.h_gcal_check_availability,
    "gcal_quick_add": calendar_tools.h_gcal_quick_add,
    "gcal_update_event": calendar_tools.h_gcal_update_event,
    "gcal_delete_event": calendar_tools.h_gcal_delete_event,
    "present_plan": handlers.h_present_plan,
    "search_files": handlers.h_search_files,
    "todo": handlers.h_todo,
    "clarify": handlers.h_clarify,
    "browser": handlers.h_browser,
    "append_to_context_pool": handlers.h_append_to_context_pool,
    "search_history": handlers.h_search_history,
    "list_sessions": handlers.h_list_sessions,
    "get_session": handlers.h_get_session,
    "rebuild_history_index": handlers.h_rebuild_history_index,
    "search_zulip_chats": handlers.h_search_zulip_chats,
    "get_zulip_chat": handlers.h_get_zulip_chat,
    "skill_create": handlers.h_skill_create,
    "skill_update": handlers.h_skill_update,
    "skill_note": handlers.h_skill_note,
    "python_playground": handlers.h_python_playground,
    "js_playground": handlers.h_js_playground,
}

EMPTY_TOOL_NAME_ERROR = "Error: empty tool name provided"


def prepare(tool_name, arguments):
    """Normalise argument aliases and validate required args / core arg types.

    Returns (arguments, None) when the call may run, else (arguments, error_text) where
    error_text is the exact line `call-tool` prints (without newline) before exiting 0."""
    if not tool_name or not tool_name.strip():
        return arguments, EMPTY_TOOL_NAME_ERROR
    arguments = normalize_tool_arguments(tool_name, arguments)

    # Validate required arguments before dispatch
    required = TOOL_REQUIRED_ARGS.get(tool_name, [])
    if tool_name == "delegate_task":
        if "task" in arguments or "tasks" in arguments:
            required = []
    missing = [k for k in required if k not in arguments]
    if missing:
        return arguments, json.dumps({"error": f"Missing required argument(s): {', '.join(missing)}"})

    # Validate argument types for core tools
    if tool_name in ("read_file", "write_file", "edit_file", "list_directory"):
        p = arguments.get("path")
        if p is not None and not isinstance(p, str):
            return arguments, json.dumps({"error": f"Invalid argument type for 'path': expected string, got {type(p).__name__}"})
    elif tool_name in ("web_search", "fetch_webpage"):
        q = arguments.get("query") if tool_name == "web_search" else arguments.get("url")
        if q is not None and not isinstance(q, str):
            return arguments, json.dumps({"error": f"Invalid argument type: expected string, got {type(q).__name__}"})
    return arguments, None


def _tool_text(res):
    if isinstance(res, dict) and ("content" in res or "isError" in res or "error" in res):
        return mcp_result_to_text(res)
    return res


def invoke(server_name, tool_name, arguments, mcp_servers=None):
    """Run one prepared tool call. Returns (stdout_text, exit_code) exactly as `call-tool`
    printed/exited: handler output + newline; unknown MCP server -> error JSON, exit 1."""
    for name, handler in HANDLERS.items():
        if tool_name == name or server_name == name:
            return f"{handler(arguments)}\n", 0

    # Route to MCP server
    if mcp_servers is None:
        mcp_servers = load_config()
    cfg = mcp_servers.get(server_name)
    if not cfg:
        # Try matching clean server name
        for k in mcp_servers.keys():
            clean_k = "".join(c if c.isalnum() or c == "_" else "_" for c in k)
            if clean_k == server_name:
                cfg = mcp_servers[k]
                break

    if not cfg:
        return json.dumps({"error": f"MCP server '{server_name}' not found in config"}) + "\n", 1

    result = call_tool(server_name, cfg, tool_name, arguments)
    return f"{_tool_text(result)}\n", 0


def dispatch(server_name, tool_name, arguments):
    """Run one tool call in-process and return exactly what `ai_mcp.py call-tool` prints."""
    arguments, error = prepare(tool_name, arguments)
    if error is not None:
        return error + "\n"
    return invoke(server_name, tool_name, arguments)[0]
