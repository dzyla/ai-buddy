"""MCP client: config discovery (load_config), stdio JSON-RPC servers, schema cache, call_tool."""
import sys
import os
import json
import subprocess
import time

def config_paths():
    """MCP config search order (first non-empty file wins), resolved at call time so HOME/cwd/env
    changes are honoured: $INFER_MCP_CONFIG, the user's ~/.config/ai (seeded by install.sh from
    config/mcp.example.json), then ./mcp.json for development (no longer tracked), then the
    legacy locations of other tools."""
    paths = []
    override = os.environ.get("INFER_MCP_CONFIG")
    if override:
        paths.append(os.path.expanduser(override))
    paths += [
        os.path.expanduser("~/.config/ai/mcp.json"),
        os.path.expanduser("~/.config/ai/mcp_config.json"),
        os.path.join(os.getcwd(), "mcp.json"),
        os.path.join(os.getcwd(), "mcp_config.json"),
        os.path.expanduser("~/.gemini/config/mcp_config.json"),
        os.path.expanduser("~/.lmstudio/mcp.json"),
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


def load_config():
    for path in config_paths():
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

def start_server(cfg):
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
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("INFER_TEST_MODE"):
        if any("agent.robinhood.com" in str(x) or "mcp-remote" in str(x) for x in cmd):
            return None

    env = os.environ.copy()
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
