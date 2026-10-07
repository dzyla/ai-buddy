"""`ai_mcp.py <subcommand>` CLI: list-tools, call-tool, render-markdown, trim-messages,
session-transcript, run-scheduler, the self-improvement/ledger subcommands and friends.

main(argv) -> int takes the arguments after the program name. Output (stdout bytes, exit codes)
is identical to the former single-file ai_mcp.py main().
"""
import io
import json
import os
import sqlite3
import sys
import time

from .browser import _browser_daemon_main
from .history import HISTORY_DB, _ensure_history_schema, _prune_cache_sessions, get_zulip_chat, search_zulip_chats
from .mcp_client import list_tools, load_config
from .memory import recall, remember
from .metrics import count_tokens, log_metric, show_metrics
from .registry import CORE_TOOL_PROFILE, TOOL_SCHEMAS, invoke, prepare, repair_json, EMPTY_TOOL_NAME_ERROR
from .render import render_markdown
from .scheduler import run_scheduler_loop
from .selfimprove import (lessons_for, metrics_lesson_sync, record_failure, record_recovery, self_improve_status,
                          session_recap)
from .sessions import session_transcript, trim_messages


def main(argv=None):
    """Run one subcommand; returns the process exit code."""
    argv = sys.argv if argv is None else [sys.argv[0] if sys.argv else "ai_mcp.py"] + list(argv)
    try:
        _main(argv)
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def _main(argv):
    # A pipe-writing backend must not raise a BrokenPipeError traceback when the
    # parent (ai.c) closes the pipe on interrupt/exit. Restore default SIGPIPE so
    # the process dies silently instead of dumping "Exception ignored on flushing
    # sys.stdout: BrokenPipeError" at interpreter shutdown.
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except Exception:
        pass
    if len(argv) < 2:
        print("Usage: ai_mcp.py [list-tools | call-tool | render-markdown | trim-messages]", file=sys.stderr)
        sys.exit(1)

    action = argv[1]
    mcp_servers = load_config()

    if action == "browser-daemon":
        _browser_daemon_main()
        sys.exit(0)

    if action == "run-scheduler":
        if len(argv) < 3:
            sys.exit(1)
        task_id = argv[2]
        run_scheduler_loop(task_id)
        sys.exit(0)

    if action == "render-markdown":
        if len(argv) < 3:
            sys.exit(0)
        text = argv[2]
        if os.environ.get("INFER_RAW_OUTPUT") == "1":
            print(text)
        else:
            print(render_markdown(text))
        sys.exit(0)

    if action == "rag-memories":
        if len(argv) < 3:
            sys.exit(0)
        query = argv[2]
        try:
            res = recall(query)
            if "No memories found" not in res and not res.startswith("Error"):
                print(res)
        except:
            pass
        sys.exit(0)

    if action == "save-memories":
        if len(argv) < 3:
            sys.exit(0)
        content = argv[2]
        try:
            memories = json.loads(content)
            if isinstance(memories, list):
                for mem in memories:
                    if isinstance(mem, str):
                        remember(mem, "Auto-compacted memory map")
        except:
            # Fallback if not proper JSON
            remember(content, "Auto-compacted memory map")
        sys.exit(0)

    # ── Automatic failure-learning ledger (harness-internal; not exposed as model tools) ──
    if action == "record-failure":
        rec = json.loads(argv[2]) if len(argv) > 2 else {}
        _, lesson = record_failure(rec.get("tool", ""), rec.get("args", ""),
                                   rec.get("error", ""), rec.get("phase", "execution"),
                                   chain_id=rec.get("chain_id") or None)
        if lesson:
            print(lesson)
        sys.exit(0)

    if action == "record-recovery":
        rec = json.loads(argv[2]) if len(argv) > 2 else {}
        lesson = record_recovery(rec.get("tool", ""), rec.get("args", ""),
                                 rec.get("prior_error", ""), rec.get("phase", "execution"),
                                 chain_id=rec.get("chain_id") or None,
                                 failed_args=rec.get("failed_args"))
        if lesson:
            print(lesson)
        sys.exit(0)

    if action == "lessons-for":
        rec = json.loads(argv[2]) if len(argv) > 2 else {}
        print(lessons_for(rec.get("tool", ""), rec.get("error", "")), end="")
        sys.exit(0)

    if action == "self-improve-status":
        print(self_improve_status())
        sys.exit(0)

    if action == "sync-metrics-lessons":
        mf = 3
        if len(argv) > 2:
            try:
                mf = max(2, int(argv[2]))
            except Exception:
                pass
        print(metrics_lesson_sync(min_fails=mf))
        sys.exit(0)

    if action == "session-recap":
        print(session_recap(), end="")
        sys.exit(0)

    if action == "zulip-history":
        lim = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 10
        print(search_zulip_chats(limit=lim))
        sys.exit(0)

    if action == "zulip-log":
        sid = argv[2] if len(argv) > 2 else ""
        print(get_zulip_chat(session_id=sid))
        sys.exit(0)

    if action == "zulip-debug":
        try:
            from zulip_ai_bridge import ZulipMemoryManager
            mem = ZulipMemoryManager()
            diag = mem.get_diagnostics()
            print(json.dumps(diag, indent=2, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"error": str(e)}))
        sys.exit(0)

    if action == "trim-messages":
        if len(argv) < 3:
            sys.exit(1)
        try:
            with open(argv[2]) as f:
                messages = json.load(f)
            # Optional target size in JSON chars (ai.c passes its budget).
            max_bytes = 60000
            if len(argv) > 3:
                try:
                    max_bytes = max(2000, int(argv[3]))
                except Exception:
                    pass
            print(json.dumps(trim_messages(messages, max_bytes)))
        except Exception:
            print("[]")
        sys.exit(0)

    if action == "prune-sessions":
        # Bounded cache retention (archive-first): safe to run at every exit —
        # it only removes cache session files already preserved in the
        # persistent mirror or fully archived in the index.
        try:
            os.makedirs(os.path.dirname(HISTORY_DB), exist_ok=True)
            conn = sqlite3.connect(HISTORY_DB)
            _ensure_history_schema(conn)
            _prune_cache_sessions(conn)
            conn.close()
        except Exception:
            pass
        sys.exit(0)

    if action == "session-transcript":
        # Convert a saved raw messages array into a clean user/assistant transcript,
        # one compact JSON message object per line (JSONL), so the C caller can
        # append each via its existing append_message().
        if len(argv) < 3:
            sys.exit(0)
        try:
            with open(argv[2]) as f:
                messages = json.load(f)
        except Exception:
            sys.exit(0)
        for m in session_transcript(messages):
            print(json.dumps(m, ensure_ascii=False))
        sys.exit(0)

    if action == "list-tools":
        _list_tools(mcp_servers)

    elif action == "show-metrics" or action == "metrics":
        show_metrics()

    elif action == "count-tokens":
        model = argv[2] if len(argv) > 2 else "default"
        text = argv[3] if len(argv) > 3 else ""
        print(count_tokens(model, text))

    elif action == "call-tool":
        _call_tool(argv, mcp_servers)


def _list_tools(mcp_servers):
    openai_tools = list(TOOL_SCHEMAS)

    if os.environ.get("INFER_NO_MCP") not in ("1", "true") and mcp_servers:
        mcp_cache_dir = os.path.expanduser("~/.cache/ai")
        os.makedirs(mcp_cache_dir, exist_ok=True)
        import hashlib
        cfg_str = json.dumps(mcp_servers, sort_keys=True)
        cfg_hash = hashlib.sha256(cfg_str.encode()).hexdigest()[:16]
        mcp_cache_file = os.path.join(mcp_cache_dir, f"mcp_tools_{cfg_hash}.json")

        cached_mcp_tools = None
        if os.path.exists(mcp_cache_file) and os.environ.get("INFER_REFRESH_MCP") not in ("1", "true"):
            try:
                with open(mcp_cache_file, "r") as f:
                    cached_mcp_tools = json.load(f)
            except Exception:
                pass

        if cached_mcp_tools is not None:
            openai_tools.extend(cached_mcp_tools)
        else:
            new_mcp_tools = []
            for server_name, cfg in mcp_servers.items():
                tools = list_tools(server_name, cfg)
                for t in tools:
                    new_mcp_tools.append({
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
            openai_tools.extend(new_mcp_tools)
            try:
                with open(mcp_cache_file, "w") as f:
                    json.dump(new_mcp_tools, f)
            except Exception:
                pass

    # Small-context profile: the full catalog is ~45 KB (~12k tokens) and is
    # sent with EVERY request, which alone overflows a 16k-ctx local model.
    # INFER_TOOL_PROFILE=core (set by ai.c for --short-ctx or a detected
    # context window <= 32k) keeps only the everyday tools; load_skill and
    # execute_command still reach everything else.
    if os.environ.get("INFER_TOOL_PROFILE", "").strip().lower() == "core":
        extra = {x.strip() for x in os.environ.get("INFER_CORE_TOOLS_EXTRA", "").split(",") if x.strip()}
        keep = set(CORE_TOOL_PROFILE) | extra
        openai_tools = [t for t in openai_tools
                        if t.get("function", {}).get("name") in keep]

    print(json.dumps(openai_tools))


def _call_tool(argv, mcp_servers):
    if len(argv) < 5:
        print("Usage: ai_mcp.py call-tool <server_name> <tool_name> <arguments_json>", file=sys.stderr)
        sys.exit(1)

    server_name = argv[2]
    tool_name = argv[3]
    args_json = argv[4]

    if not tool_name or not tool_name.strip():
        print(EMPTY_TOOL_NAME_ERROR)
        sys.exit(0)

    try:
        arguments = json.loads(args_json)
    except Exception:
        try:
            arguments = json.loads(repair_json(args_json))
        except Exception as e:
            print(json.dumps({"error": f"Failed to parse arguments JSON even after repair: {e}"}))
            sys.exit(0)

    arguments, error = prepare(tool_name, arguments)
    if error is not None:
        print(error)
        sys.exit(0)

    _t0 = time.time()
    # Capture this tool's stdout through a tee so the metric records REAL
    # success + error text (previously every call was logged success=True
    # with no error, which is why [FLAKY] detection and metrics-driven
    # lessons never fired). Error markers mirror the harness's own is_err
    # detection in ai.c so the ledger and the metrics agree.
    _buf = io.StringIO()
    _real_out = sys.stdout

    class _TeeOut:
        def write(self, s):
            try:
                _real_out.write(s)
            except Exception:
                pass
            try:
                _buf.write(s)
            except Exception:
                pass
            return len(s)

        def flush(self):
            try:
                _real_out.flush()
            except Exception:
                pass
    sys.stdout = _TeeOut()
    _crashed = {"v": False}
    _prev_hook = sys.excepthook

    def _crash_hook(exc_type, exc, tb):
        _crashed["v"] = True
        if _prev_hook is not None:
            _prev_hook(exc_type, exc, tb)
        else:
            import traceback
            traceback.print_exception(exc_type, exc, tb)
    sys.excepthook = _crash_hook
    import atexit

    def _finish_metric():
        try:
            sys.stdout = _real_out
        except Exception:
            pass
        out = _buf.getvalue()
        head = out.lstrip()[:200]
        success = True
        err = None
        if _crashed["v"]:
            success = False
            err = "uncaught exception in tool"
        elif (head.startswith("Error") or head.startswith('{"error"')
                or "failed (exit" in out or "[SYSTEM WARNING:" in out
                or "[Command Failed" in out):
            success = False
            err = head
        try:
            log_metric(tool_name, (time.time() - _t0) * 1000,
                       success=success, error=err)
        except Exception:
            pass
    atexit.register(_finish_metric)

    text, code = invoke(server_name, tool_name, arguments, mcp_servers)
    sys.stdout.write(text)
    sys.stdout.flush()
    if code:
        sys.exit(code)
