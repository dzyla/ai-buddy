"""MCP client: config discovery (load_config), stdio JSON-RPC servers, schema cache, call_tool."""
import sys
import os
import json
import subprocess
import threading
import time

def config_paths(cwd=None, environ=None):
    """MCP config search order (first non-empty file wins), resolved at call time so HOME/cwd/env
    changes are honoured: $INFER_MCP_CONFIG, the user's ~/.config/ai (seeded by install.sh from
    config/mcp.example.json), then ./mcp.json for development (no longer tracked), then the
    legacy locations of other tools. `cwd`/`environ` resolve for another process (the tool
    server answers for its callers); the default is this process's."""
    environ = os.environ if environ is None else environ
    cwd = os.getcwd() if cwd is None else cwd
    home = environ.get("HOME") or os.path.expanduser("~")

    def user(p):
        return os.path.join(home, p[2:]) if p.startswith("~/") else os.path.expanduser(p)
    paths = []
    override = environ.get("INFER_MCP_CONFIG")
    if override:
        paths.append(user(override))
    paths += [
        user("~/.config/ai/mcp.json"),
        user("~/.config/ai/mcp_config.json"),
        os.path.join(cwd, "mcp.json"),
        os.path.join(cwd, "mcp_config.json"),
        user("~/.gemini/config/mcp_config.json"),
        user("~/.lmstudio/mcp.json"),
    ]
    return paths


# Snapshot for callers that read the list directly; load_config() always re-resolves.
CONFIG_PATHS = config_paths()


def _enabled(servers):
    """Drop entries marked "disabled": true (the shipped template disables every server)."""
    if not isinstance(servers, dict):
        return servers
    return {name: cfg for name, cfg in servers.items()
            if not (isinstance(cfg, dict) and cfg.get("disabled") is True)}


def load_config(cwd=None, environ=None):
    for path in config_paths(cwd, environ):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    if "mcpServers" in data:
                        return _enabled(data["mcpServers"])
                    return _enabled(data)
            except Exception as e:
                print(f"Warning: failed to load config from {path}: {e}", file=sys.stderr)
    return {}

def run_jsonrpc(proc, method, params, req_id, timeout=10.0):
    req = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params
    }
    req_str = json.dumps(req) + "\n"
    try:
        proc.stdin.write(req_str)
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        raise Exception(f"Failed to send request to MCP server: {e}")

    import select
    start_t = time.time()
    while True:
        elapsed = time.time() - start_t
        remaining = max(0.1, timeout - elapsed)
        if elapsed >= timeout:
            raise TimeoutError(f"Timed out after {timeout}s waiting for MCP response to {method}")
        r, _, _ = select.select([proc.stdout], [], [], remaining)
        if not r:
            raise TimeoutError(f"Timed out after {timeout}s waiting for MCP response to {method}")
        line = proc.stdout.readline()
        if not line:
            raise Exception("Connection closed by server")
        try:
            resp = json.loads(line)
            if resp.get("id") == req_id:
                return resp
        except json.JSONDecodeError:
            pass

def send_notification(proc, method, params=None):
    req = {
        "jsonrpc": "2.0",
        "method": method
    }
    if params is not None:
        req["params"] = params
    req_str = json.dumps(req) + "\n"
    try:
        proc.stdin.write(req_str)
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass

def _sync_mcp_auth_tokens():
    """Sync stored OAuth tokens across different mcp-remote package versions in ~/.mcp-auth/."""
    auth_dir = os.path.expanduser("~/.mcp-auth")
    if not os.path.isdir(auth_dir):
        return
    try:
        remote_dirs = [os.path.join(auth_dir, d) for d in os.listdir(auth_dir) if d.startswith("mcp-remote-") and os.path.isdir(os.path.join(auth_dir, d))]
        if not remote_dirs:
            return
        
        tokens_map = {}
        for rd in remote_dirs:
            for f in os.listdir(rd):
                if f.endswith("_tokens.json") or f.endswith("_client_info.json"):
                    fpath = os.path.join(rd, f)
                    try:
                        mtime = os.path.getmtime(fpath)
                        with open(fpath, "r", encoding="utf-8") as tf:
                            td = json.load(tf)
                        if td:
                            if f not in tokens_map or mtime > tokens_map[f][0]:
                                tokens_map[f] = (mtime, fpath, td)
                    except Exception:
                        pass
        
        for f, (_, src_path, td) in tokens_map.items():
            for rd in remote_dirs:
                dest_path = os.path.join(rd, f)
                if not os.path.exists(dest_path) or os.path.getsize(dest_path) == 0:
                    try:
                        with open(dest_path, "w", encoding="utf-8") as df:
                            json.dump(td, df, indent=2)
                        os.chmod(dest_path, 0o600)
                    except Exception:
                        pass

        for rd in remote_dirs:
            for f in os.listdir(rd):
                if f.endswith("_lock.json") or "_code_verifier" in f:
                    lock_file = os.path.join(rd, f)
                    try:
                        if time.time() - os.path.getmtime(lock_file) > 30:
                            os.remove(lock_file)
                    except Exception:
                        pass
    except Exception:
        pass

def start_server(cfg, environ=None):
    """Start one stdio MCP server process (own session/process group). `environ` is the caller's
    environment (default: this process's)."""
    _sync_mcp_auth_tokens()
    cmd = []
    if "command" in cfg:
        cmd.append(cfg["command"])
    if "args" in cfg:
        for a in cfg["args"]:
            expanded = os.path.expandvars(os.path.expanduser(a))
            if expanded == "--silent" and ("mcp-remote" in str(cmd) or any("mcp-remote" in str(x) for x in cfg.get("args", []))):
                continue
            cmd.append(expanded)
    
    if not cmd:
        return None

    # In test environments or when headless, block remote MCP servers that open interactive auth browsers
    base = os.environ if environ is None else environ
    if base.get("PYTEST_CURRENT_TEST") or base.get("INFER_TEST_MODE"):
        if any("agent.robinhood.com" in str(x) or "mcp-remote" in str(x) for x in cmd):
            return None

    env = dict(base)
    env["BROWSER"] = ":"
    env["NO_BROWSER"] = "1"
    env["CI"] = "1"
    env["DISPLAY"] = ""
    env.pop("WSL_DISTRO_NAME", None)
    if "env" in cfg:
        for k, v in cfg["env"].items():
            env[k] = str(v)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        bufsize=1,
        start_new_session=True
    )
    return proc

def init_server(proc, timeout=15.0):
    init_params = {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "ai", "version": "1.0"}
    }
    resp = run_jsonrpc(proc, "initialize", init_params, req_id=1, timeout=timeout)
    send_notification(proc, "notifications/initialized")
    return resp

def _cleanup_proc(proc):
    if not proc:
        return
    import signal
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=0.5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

MCP_CACHE_FILE = os.path.expanduser("~/.cache/ai/mcp_schema_cache.json")

def _get_cached_mcp_tools(server_name, cfg_hash):
    try:
        if os.path.exists(MCP_CACHE_FILE):
            with open(MCP_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                entry = data.get(server_name)
                if entry and entry.get("hash") == cfg_hash:
                    return entry.get("tools")
    except Exception:
        pass
    return None

def _save_cached_mcp_tools(server_name, cfg_hash, tools):
    try:
        os.makedirs(os.path.dirname(MCP_CACHE_FILE), exist_ok=True)
        data = {}
        if os.path.exists(MCP_CACHE_FILE):
            try:
                with open(MCP_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[server_name] = {"hash": cfg_hash, "ts": time.time(), "tools": tools}
        with open(MCP_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

def list_tools(server_name, cfg):
    import hashlib
    cfg_hash = hashlib.md5(json.dumps(cfg, sort_keys=True).encode("utf-8")).hexdigest()
    cached = _get_cached_mcp_tools(server_name, cfg_hash)
    if cached is not None and len(cached) > 0:
        return cached

    proc = start_server(cfg)
    if not proc:
        return []
    try:
        init_server(proc, timeout=15.0)
        resp = run_jsonrpc(proc, "tools/list", {}, req_id=2, timeout=15.0)
        tools = resp.get("result", {}).get("tools", [])
        namespaced_tools = []
        for t in tools:
            clean_server = "".join(c if c.isalnum() or c == "_" else "_" for c in server_name)
            t["name"] = f"{clean_server}__{t['name']}"
            namespaced_tools.append(t)
        if namespaced_tools:
            _save_cached_mcp_tools(server_name, cfg_hash, namespaced_tools)
        return namespaced_tools
    except Exception as e:
        print(f"Warning: listing tools from {server_name} failed: {e}", file=sys.stderr)
        return []
    finally:
        _cleanup_proc(proc)

def mcp_result_to_text(result):
    """Normalise an MCP tools/call result into plain text.

    MCP results are {"content": [{"type": "text", "text": ...}, ...],
    "isError": bool}. We never return the raw dict: a JSON dump of a big
    payload would confuse both the model and the harness error detectors,
    and an isError result must read as an error to the self-improvement loop.
    """
    if result is None:
        return "Error: MCP server returned no result"
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for c in result:
            if isinstance(c, dict) and c.get("type") == "text":
                t = c.get("text", "")
                if t:
                    parts.append(t)
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts) if parts else "(no content)"
    if isinstance(result, dict):
        if result.get("isError"):
            txt = (mcp_result_to_text(result.get("content")) or "MCP tool reported an error").strip()
            return "Error: " + (txt[7:] if txt.lower().startswith("error: ") else txt)
        if "content" in result:
            parts = []
            for c in result.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text":
                    t = c.get("text", "")
                    if t:
                        parts.append(t)
                elif isinstance(c, str):
                    parts.append(c)
            return "\n".join(parts) if parts else "(no content)"
        if "error" in result:
            return "Error: " + str(result["error"])
        return json.dumps(result)
    return str(result)

def call_tool(server_name, cfg, tool_name, arguments):
    proc = start_server(cfg)
    if not proc:
        return {"error": "Failed to start server"}
    try:
        init_server(proc, timeout=15.0)
        resp = run_jsonrpc(proc, "tools/call", {"name": tool_name, "arguments": arguments}, req_id=3, timeout=60.0)
        if "error" in resp:
            return {"error": str(resp["error"])}
        return resp.get("result", {})
    except Exception as e:
        return {"error": str(e)}
    finally:
        _cleanup_proc(proc)


# ── Persistent sessions (the tool server keeps one stdio session per MCP server) ──
class McpSession:
    """One running MCP server with a reader thread that routes responses by JSON-RPC id, so
    concurrent callers can share it and a dead server is noticed at once (EOF)."""

    def __init__(self, name, cfg, environ=None, init_timeout=15.0):
        self.name = name
        self.cfg_key = json.dumps(cfg, sort_keys=True)
        self.proc = start_server(cfg, environ)
        if not self.proc:
            raise ConnectionError("Failed to start server")
        self._wlock = threading.Lock()
        self._cv = threading.Condition()
        self._results = {}
        self._next = 1
        self.dead = False
        threading.Thread(target=self._read, name="mcp-%s" % name, daemon=True).start()
        try:
            self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "ai", "version": "1.0"}}, init_timeout)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                    with self._cv:
                        self._results[msg["id"]] = msg
                        self._cv.notify_all()
        except (OSError, ValueError):
            pass
        with self._cv:
            self.dead = True
            self._cv.notify_all()

    def _send(self, obj):
        with self._wlock:
            try:
                self.proc.stdin.write(json.dumps(obj) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:
                self.dead = True
                raise ConnectionError(f"Failed to send request to MCP server: {e}")

    def request(self, method, params, timeout):
        with self._wlock:
            rid = self._next
            self._next += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        with self._cv:
            while rid not in self._results:
                if self.dead:
                    raise ConnectionError("Connection closed by server")
                left = deadline - time.time()
                if left <= 0:
                    raise TimeoutError(f"Timed out after {timeout}s waiting for MCP response to {method}")
                self._cv.wait(min(left, 0.5))
            return self._results.pop(rid)

    def alive(self):
        return not self.dead and self.proc.poll() is None

    def close(self):
        self.dead = True
        _cleanup_proc(self.proc)
        try:
            self.proc.wait(timeout=2)  # reap: the tool server is long-lived, no zombies
        except Exception:
            pass


class McpPool:
    """Server name -> McpSession. A session is (re)started on first use, when its process died
    (EOF) or timed out, or when the server's config changed. Disabled servers never reach here
    (load_config drops them)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def _get(self, name, cfg, environ):
        key = json.dumps(cfg, sort_keys=True)
        with self._lock:
            sess = self._sessions.get(name)
            if sess is not None and (not sess.alive() or sess.cfg_key != key):
                sess.close()
                sess = None
            if sess is None:
                sess = McpSession(name, cfg, environ)
                self._sessions[name] = sess
                return sess, True
            return sess, False

    def _drop(self, name, sess):
        with self._lock:
            if self._sessions.get(name) is sess:
                del self._sessions[name]
        sess.close()

    def call_tool(self, name, cfg, tool_name, arguments, timeout=60.0, environ=None):
        """Like call_tool(): the MCP result dict, or {"error": ...}. Raises TimeoutError (the
        session is restarted on the next call). A reused session that died before answering is
        restarted and the call retried once."""
        for attempt in (0, 1):
            try:
                sess, fresh = self._get(name, cfg, environ)
            except Exception as e:
                return {"error": str(e)}
            try:
                resp = sess.request("tools/call", {"name": tool_name, "arguments": arguments}, timeout)
            except TimeoutError:
                self._drop(name, sess)
                raise
            except ConnectionError as e:
                self._drop(name, sess)
                if attempt == 0 and not fresh:
                    continue
                return {"error": str(e)}
            if "error" in resp:
                return {"error": str(resp["error"])}
            return resp.get("result", {})
        return {"error": "MCP call failed"}

    def close(self):
        with self._lock:
            sessions, self._sessions = list(self._sessions.values()), {}
        for s in sessions:
            s.close()
