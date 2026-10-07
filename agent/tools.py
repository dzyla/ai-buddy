"""Tool catalog and dispatch through aitools, in the engine process (no `python3 ai_mcp.py` per call).

Native tools run exactly as the persistent tool server runs them (aitools/server.py run_forked +
_child_invoke): a child forked from this pre-imported process with the caller's cwd/env, fd 1/2
captured (so prints land in the result byte for byte like `call-tool`), the whole process tree
SIGKILLed on timeout (INFER_COMMAND_TIMEOUT, default 120 s) or ESC, and a crashing / exiting tool
can never take the engine down. MCP tools go through one in-process mcp_client.McpPool (pooled
stdio sessions; a request that reached the server is never re-sent). Every call is recorded with
aitools.metrics.record_tool_metric, as `call-tool` and the tool server do.
"""
import os
import socket
import time

TIMEOUT_WARNING = ("\n[SYSTEM WARNING: Command timed out after %d seconds. Process killed. Partial output above. "
                   "Evaluate if script is stuck in an infinite loop, waiting for user input, or just "
                   "intrinsically slow.]\n")
CACHEABLE = ("read_file", "web_search", "fetch_webpage", "list_directory")
MCP_CALL_TIMEOUT = 60.0


def command_timeout():
    v = os.environ.get("INFER_COMMAND_TIMEOUT") or ""
    try:
        n = int(v.strip() or 0)
    except ValueError:
        n = 0
    return n if n > 0 else 120


def catalog():
    """`ai_mcp.py list-tools` as a list (None when it fails or is empty)."""
    try:
        from aitools.cli import tool_catalog
        from aitools.mcp_client import load_config
        tools = tool_catalog(load_config())
    except Exception:
        return None
    return tools or None


class ToolRunner:
    def __init__(self, log, interrupted):
        self.log = log                      # text -> front-end stderr
        self.interrupted = interrupted      # threading.Event (ESC)
        self.cache = {}
        self._pool = None
        self._watch = None                  # write end of the current call's interrupt socketpair

    # -- per-task cache (read_file / web_search / fetch_webpage / list_directory) --
    def cache_get(self, name, args):
        if name not in CACHEABLE:
            return None
        return self.cache.get("%s:%s" % (name, args))

    def cache_set(self, name, args, out):
        if name not in CACHEABLE or out is None:
            return
        if out.startswith("Error") or out.startswith("[Command Failed"):
            return
        self.cache["%s:%s" % (name, args)] = out

    def invalidate_files(self):
        for k in [k for k in self.cache if k.startswith(("read_file:", "list_directory:"))]:
            del self.cache[k]

    def clear(self):
        self.cache.clear()

    # -- interrupt --
    def interrupt(self):
        w = self._watch
        if w is not None:
            try:
                w.close()
            except OSError:
                pass

    # -- dispatch --
    def call(self, server, tool, raw_args):
        """`ai_mcp.py call-tool <server> <tool> <raw_args>`'s stdout. A timed-out call returns its
        partial output + the timeout warning; an interrupted one its partial output."""
        from aitools import registry
        from aitools.cli import parse_args_json
        if not tool or not tool.strip():
            return registry.EMPTY_TOOL_NAME_ERROR + "\n"
        arguments, error = parse_args_json(raw_args)
        if error is not None:
            return error + "\n"
        arguments, error = registry.prepare(tool, arguments)
        if error is not None:
            return error + "\n"
        if registry.native_handler(server, tool) is not None:
            return self._native(server, tool, arguments)
        return self._mcp(server, tool, arguments)

    def _native(self, server_name, tool, arguments):
        from aitools import server as srv
        timeout = command_timeout()
        r_sock, w_sock = socket.socketpair()
        self._watch = w_sock
        if self.interrupted.is_set():
            w_sock.close()
        try:
            r = srv.run_forked(lambda: srv._child_invoke(server_name, tool, arguments), os.getcwd(),
                               dict(os.environ), timeout, watch=r_sock)
        finally:
            self._watch = None
            for s in (r_sock, w_sock):
                try:
                    s.close()
                except OSError:
                    pass
        if r.get("err"):
            self.log(r["err"])
        if r["timed_out"]:
            self.log("\n\033[1;31m[ai] Tool execution timed out after %ds.\033[0m\n" % timeout)
            return r["out"] + TIMEOUT_WARNING % timeout
        if r["interrupted"]:
            self.log("\n\033[1;31m[ai] Tool execution interrupted by ESC key.\033[0m\n")
            return r["out"]
        self._metric(tool, r["ms"], r["out"], r["crashed"])
        return r["out"]

    def _mcp(self, server_name, tool, arguments):
        from aitools import registry
        from aitools.mcp_client import McpPool, load_config
        t0 = time.monotonic()
        cfg = registry.mcp_config_for(server_name, load_config())
        if not cfg:
            text = registry.mcp_not_found(server_name)
        else:
            if self._pool is None:
                self._pool = McpPool()
            wait = min(command_timeout(), MCP_CALL_TIMEOUT)
            try:
                result = self._pool.call_tool(server_name, cfg, tool, arguments, timeout=wait,
                                              environ=dict(os.environ))
            except TimeoutError as e:
                result = {"error": str(e) or "MCP call timed out"}
            text = "%s\n" % registry._tool_text(result)
        self._metric(tool, (time.monotonic() - t0) * 1000, text, False)
        return text

    @staticmethod
    def _metric(tool, ms, out, crashed):
        try:
            from aitools import metrics
            metrics.record_tool_metric(tool, ms, out, crashed=crashed)
        except Exception:
            pass

    def close(self):
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception:
                pass
            self._pool = None
