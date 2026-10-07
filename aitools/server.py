"""Persistent per-user tool server: `ai_mcp.py serve [--idle-exit 900] [--drain-idle 20] [--drain-limit 16]`.

The `ai` binary (ai_toolclient.c) sends list-tools / call-tool / render-markdown here instead of
starting a Python interpreter per call, and falls back to `python3 ai_mcp.py <subcommand>` whenever
the server is unavailable.

Socket: $XDG_RUNTIME_DIR/ai/tools.sock (fallback ~/.cache/ai/tools.sock); dir 0700, socket 0600,
connections from another uid are dropped (SO_PEERCRED). Pid file ~/.cache/ai/tools.pid.

Protocol: newline-delimited JSON, one response line per request line.
  request  {"id": n, "op": "hello"|"list_tools"|"call"|"render"|"shutdown",
            "server": s, "tool": t, "args": "<raw JSON text>"|{...}, "text": "...", "timeout": secs,
            "cwd": caller's cwd, "env": caller's environment, "script": caller's ai_mcp.py path}
  response {"id": n, "ok": bool, "result": str, "error": str, "exit": int, "stderr": str}
  hello adds "version" (sha256 of aitools/*.py concatenated, first 12 hex) and "pid".
  Errors: "stale" (aitools/*.py changed since start, or the caller's ai_mcp.py resolves to another
  aitools package: the server stops listening at once and exits when idle; the client respawns),
  "mismatch" (caller's HOME is not the server's: the client uses the subprocess path), "timeout"
  (result = partial stdout), "interrupted", "bad request".

Execution model. Every native tool call, list_tools and render runs in a child forked from this
pre-imported process (no interpreter start, no imports): the child chdir()s to the caller's cwd,
replaces os.environ with the caller's environment, has fd 1/2 on pipes, and exits with os._exit.
So a call sees exactly what `ai_mcp.py call-tool` saw (relative paths, INFER_* flags, PATH), prints
and subprocess output land in the result byte for byte, a tool that raises / calls sys.exit /
mutates module globals cannot affect the server or other calls, concurrent calls with different
cwds are independent (os.chdir is process-global, so threads could not do this), and a timed-out
call's whole process tree is SIGKILLed (threads cannot be killed). MCP tools run in the server
process on pooled stdio sessions (mcp_client.McpPool, one per server name), since a forked child
would not keep a session alive; their timeout is the JSON-RPC wait (min(timeout, 60 s), as
`call-tool`'s 60 s), after which the session is restarted.

Idle work: after `--drain-idle` seconds without requests (and with INFER_EMBED_AUTODRAIN != "0" in
the server's environment) a forked child drains the embed queue with
`search.backfill_locked(limit=--drain-limit, queued_only=True)` (the `embed-backfill --queued-only`
path); never started while a request is in flight. Exits after `--idle-exit` seconds idle.
"""
import ctypes
import hashlib
import io
import json
import os
import select
import signal
import socket
import socketserver
import struct
import sys
import threading
import time
import traceback
import warnings

from . import metrics, registry
from .cli import parse_args_json
from .mcp_client import McpPool, load_config

DEFAULT_IDLE_EXIT = 900
DEFAULT_TIMEOUT = 120.0
DEFAULT_DRAIN_IDLE = 20.0
DEFAULT_DRAIN_LIMIT = 16
MCP_CALL_TIMEOUT = 60.0  # call-tool's tools/call wait (mcp_client.call_tool)
PKG_DIR = os.path.dirname(os.path.realpath(__file__))

# Ops the C binary keeps native; refused so a confused client falls back instead.
C_NATIVE = ("execute_command",)

# Python 3.12 warns on fork() in a multi-threaded process. The children here only run aitools code
# with fresh stdio objects and exit with os._exit; the server's threads hold no locks the tool code
# needs (MCP pipes, sockets), and the embed drain also runs in a child, so no sqlite state crosses.
warnings.filterwarnings("ignore", message=r".*use of fork\(\) may lead to deadlocks.*",
                        category=DeprecationWarning)

try:
    _LIBC = ctypes.CDLL(None, use_errno=True)
except OSError:  # pragma: no cover
    _LIBC = None


# ── paths ───────────────────────────────────────────────────────────────────
def _home(environ=None):
    environ = os.environ if environ is None else environ
    return environ.get("HOME") or os.path.expanduser("~")


def socket_path(environ=None):
    """$XDG_RUNTIME_DIR/ai/tools.sock when XDG_RUNTIME_DIR is an existing dir, else
    ~/.cache/ai/tools.sock (ai_toolclient.c resolves the same way)."""
    environ = os.environ if environ is None else environ
    xdg = environ.get("XDG_RUNTIME_DIR")
    if xdg and os.path.isdir(xdg):
        return os.path.join(xdg, "ai", "tools.sock")
    return os.path.join(_home(environ), ".cache", "ai", "tools.sock")


def pid_path(environ=None):
    return os.path.join(_home(environ), ".cache", "ai", "tools.pid")


# ── code identity ───────────────────────────────────────────────────────────
def _code_files(pkg_dir=PKG_DIR):
    try:
        return sorted(f for f in os.listdir(pkg_dir) if f.endswith(".py"))
    except OSError:
        return []


def fingerprint(pkg_dir=PKG_DIR):
    """(name, mtime_ns, size) of every aitools/*.py: cheap to re-check on each request."""
    out = []
    for f in _code_files(pkg_dir):
        try:
            st = os.stat(os.path.join(pkg_dir, f))
            out.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((f, 0, -1))
    return tuple(out)


def code_version(pkg_dir=PKG_DIR):
    h = hashlib.sha256()
    for f in _code_files(pkg_dir):
        try:
            with open(os.path.join(pkg_dir, f), "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
    return h.hexdigest()[:12]


def package_for(script, home):
    """The aitools package the ai_mcp.py shim at `script` would import (see ai_mcp.py)."""
    here = os.path.dirname(os.path.realpath(script))
    if os.path.isfile(os.path.join(here, "aitools", "__init__.py")):
        return os.path.realpath(os.path.join(here, "aitools"))
    return os.path.realpath(os.path.join(home, ".local", "share", "ai", "lib", "aitools"))


# ── forked execution ────────────────────────────────────────────────────────
_fork_lock = threading.Lock()


def _set_pdeathsig():
    if _LIBC is not None:
        try:
            _LIBC.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG: die with the forking server thread
        except Exception:
            pass


def _descendants(pid):
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        try:
            tasks = os.listdir("/proc/%d/task" % p)
        except OSError:
            continue
        for t in tasks:
            try:
                with open("/proc/%d/task/%s/children" % (p, t)) as f:
                    kids = [int(x) for x in f.read().split()]
            except (OSError, ValueError):
                continue
            out += kids
            todo += kids
    return out


def _kill_tree(pid):
    for p in _descendants(pid) + [pid]:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _child_main(fn, cwd, env, out_w, err_w, st_w, close_fds):
    code, crashed = 1, False
    try:
        try:
            os.setpgid(0, 0)
        except OSError:
            pass
        _set_pdeathsig()
        for fd in close_fds:
            os.close(fd)
        nul = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nul, 0)
        os.dup2(out_w, 1)
        os.dup2(err_w, 2)
        for fd in (nul, out_w, err_w):
            os.close(fd)
        for sig in (signal.SIGPIPE, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        sys.stdin = sys.__stdin__ = io.TextIOWrapper(io.FileIO(0, "r", closefd=False))
        sys.stdout = sys.__stdout__ = io.TextIOWrapper(io.FileIO(1, "w", closefd=False),
                                                       encoding="utf-8", errors="surrogateescape")
        sys.stderr = sys.__stderr__ = io.TextIOWrapper(io.FileIO(2, "w", closefd=False), encoding="utf-8",
                                                       errors="backslashreplace", line_buffering=True)
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
        if cwd:
            try:
                os.chdir(cwd)
            except OSError:
                pass
        code = fn()
        code = 0 if code is None else code
    except SystemExit as e:  # same exit-code rules as cli.main
        if e.code is None:
            code = 0
        elif isinstance(e.code, int):
            code = e.code
        else:
            try:
                print(e.code, file=sys.stderr)
            except Exception:
                pass
            code = 1
    except BaseException:
        crashed, code = True, 1
        try:
            traceback.print_exc()
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    if not isinstance(code, int):
        code = 1
    try:
        os.write(st_w, (json.dumps({"code": code, "crashed": crashed}) + "\n").encode())
    except Exception:
        pass
    os._exit(code & 0xFF)


def _drain_fd(fd, buf):
    """Read what is available; False on EOF."""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            return True
        except OSError:
            return False
        if not chunk:
            return False
        buf += chunk


def _peer_closed(sock):
    try:
        return sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
    except BlockingIOError:
        return False
    except OSError:
        return True


def run_forked(fn, cwd, env, timeout, watch=None):
    """Run fn() in a forked child with the caller's cwd/env, capturing fd 1/2.

    Returns {out, err, code, crashed, timed_out, interrupted, ms}. On timeout (or when the `watch`
    socket's peer hangs up) the child's whole process tree is SIGKILLed. The caller's process
    state (cwd, env, stdio, module globals) is never touched."""
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    st_r, st_w = os.pipe()
    t0 = time.monotonic()
    with _fork_lock:
        pid = os.fork()
    if pid == 0:  # pragma: no cover (child)
        _child_main(fn, cwd, env, out_w, err_w, st_w, (out_r, err_r, st_r))
    for fd in (out_w, err_w, st_w):
        os.close(fd)
    try:
        os.setpgid(pid, pid)
    except OSError:
        pass
    bufs = {out_r: bytearray(), err_r: bytearray(), st_r: bytearray()}
    open_fds = set(bufs)
    for fd in bufs:
        os.set_blocking(fd, False)
    res = {"code": None, "crashed": False, "timed_out": False, "interrupted": False}
    deadline = t0 + timeout if timeout and timeout > 0 else None
    status = None
    try:
        while True:
            fds = list(open_fds) + ([watch] if watch is not None else [])
            ready, _, _ = select.select(fds, [], [], 0.05)
            for fd in ready:
                if fd is watch:
                    if _peer_closed(watch):
                        res["interrupted"] = True
                    watch = None  # closed, or the client sent more: stop watching either way
                elif not _drain_fd(fd, bufs[fd]):
                    open_fds.discard(fd)
            if b"\n" in bufs[st_r]:  # the child's last act before _exit
                status = os.waitpid(pid, 0)[1]
                break
            wpid, st = os.waitpid(pid, os.WNOHANG)
            if wpid:
                status = st
                break
            if res["interrupted"] or (deadline is not None and time.monotonic() > deadline):
                res["timed_out"] = not res["interrupted"]
                _kill_tree(pid)
                status = os.waitpid(pid, 0)[1]
                break
        for fd in bufs:
            _drain_fd(fd, bufs[fd])
    finally:
        for fd in bufs:
            os.close(fd)
    try:
        info = json.loads(bytes(bufs[st_r]).split(b"\n")[0])
        res["code"], res["crashed"] = int(info["code"]), bool(info["crashed"])
    except (ValueError, KeyError, TypeError):
        res["code"] = os.waitstatus_to_exitcode(status) if status is not None else -1
        res["crashed"] = not (res["timed_out"] or res["interrupted"])
    res["out"] = bytes(bufs[out_r]).decode("utf-8", "surrogateescape")
    res["err"] = bytes(bufs[err_r]).decode("utf-8", "surrogateescape")
    res["ms"] = (time.monotonic() - t0) * 1000
    return res


# ── child entry points ──────────────────────────────────────────────────────
def _child_invoke(server, tool, arguments):
    text, code = registry.invoke(server, tool, arguments, {})
    sys.stdout.write(text)
    return code


def _child_list_tools():
    from .cli import _list_tools
    _list_tools(load_config())
    return 0


def _child_render(text):
    from .render import render_markdown
    if os.environ.get("INFER_RAW_OUTPUT") == "1":
        print(text)
    else:
        print(render_markdown(text))
    return 0


def _child_drain(limit):
    from . import search, state
    try:
        c = state.connect(read=True)
        try:
            n = c.execute("SELECT count(*) FROM embed_queue WHERE coalesce(attempts, 0) < ?",
                          (search.MAX_ATTEMPTS,)).fetchone()[0]
        finally:
            c.close()
    except Exception:
        n = 0
    if not n:
        print(json.dumps({"embedded": 0, "queue_left": 0, "embedder": "idle"}))
        return 0
    print(json.dumps(search.backfill_locked(limit=limit, queued_only=True)))
    return 0


# ── server ──────────────────────────────────────────────────────────────────
class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        app = self.server.app
        if not app.peer_ok(self.connection):
            return
        for line in iter(self.rfile.readline, b""):
            try:
                req = json.loads(line.decode("utf-8", "surrogateescape"))
                if not isinstance(req, dict):
                    raise ValueError("not an object")
            except ValueError:
                resp = {"id": None, "ok": False, "error": "bad request"}
            else:
                resp = app.handle(req, self.connection)
            stop = resp.pop("_shutdown", False) or resp.get("error") == "stale"
            data = json.dumps(resp, ensure_ascii=False).encode("utf-8", "surrogateescape") + b"\n"
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except OSError:
                return
            finally:
                if stop:
                    app.begin_shutdown()


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False


class ToolServer:
    def __init__(self, sock_path, idle_exit=DEFAULT_IDLE_EXIT, drain_idle=DEFAULT_DRAIN_IDLE,
                 drain_limit=DEFAULT_DRAIN_LIMIT):
        self.sock_path = sock_path
        self.idle_exit = idle_exit
        self.drain_idle = drain_idle
        self.drain_limit = drain_limit
        self.autodrain = os.environ.get("INFER_EMBED_AUTODRAIN", "1") != "0"
        self.home = os.path.realpath(_home())
        self.fp = fingerprint()
        self.version = code_version()
        self.pool = McpPool()
        self.httpd = None
        self.sock_id = None
        self._lock = threading.Lock()
        self._in_flight = 0
        self._last = time.monotonic()
        self._closing = threading.Event()
        self._drain_thread = None
        self._next_drain = 0.0

    # -- request checks --
    def peer_ok(self, conn):
        try:
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            return struct.unpack("3i", creds)[1] == os.getuid()
        except OSError:
            return False

    def check(self, req):
        if self._closing.is_set() or fingerprint() != self.fp:
            return "stale"
        script = req.get("script")
        if script and package_for(script, self.home) != PKG_DIR:
            return "stale"
        env = req.get("env")
        if isinstance(env, dict) and env.get("HOME") and os.path.realpath(env["HOME"]) != self.home:
            return "mismatch"
        return None

    # -- dispatch --
    def handle(self, req, conn):
        rid, op = req.get("id"), req.get("op")
        if op == "shutdown":
            return {"id": rid, "ok": True, "result": "", "_shutdown": True}
        err = self.check(req)
        if err:
            return {"id": rid, "ok": False, "error": err}
        if op == "hello":
            return {"id": rid, "ok": True, "result": "", "version": self.version, "pid": os.getpid()}
        env = req.get("env") if isinstance(req.get("env"), dict) else None
        cwd = req.get("cwd") if isinstance(req.get("cwd"), str) else None
        try:
            timeout = float(req.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        with self._lock:
            self._in_flight += 1
            self._last = time.monotonic()
        try:
            if op == "list_tools":
                return self._reply(rid, run_forked(_child_list_tools, cwd, env, timeout, conn))
            if op == "render":
                text = req.get("text") or ""
                return self._reply(rid, run_forked(lambda: _child_render(text), cwd, env, timeout, conn))
            if op == "call":
                return self._call(rid, req, cwd, env, timeout, conn)
            return {"id": rid, "ok": False, "error": "bad request"}
        except Exception as e:  # a server bug must not kill the connection thread silently
            traceback.print_exc()
            return {"id": rid, "ok": False, "error": "server error: %s" % e}
        finally:
            with self._lock:
                self._in_flight -= 1
                self._last = time.monotonic()

    @staticmethod
    def _reply(rid, r):
        if r["timed_out"]:
            return {"id": rid, "ok": False, "error": "timeout", "result": r["out"], "stderr": r["err"]}
        if r["interrupted"]:
            return {"id": rid, "ok": False, "error": "interrupted", "result": r["out"], "stderr": r["err"]}
        return {"id": rid, "ok": True, "result": r["out"], "exit": r["code"], "stderr": r["err"]}

    def _metric(self, tool, ms, out, crashed, env):
        if (env or {}).get("INFER_PRIVATE_MODE") in ("1", "true"):
            return
        try:
            metrics.record_tool_metric(tool, ms, out, crashed=crashed)
        except Exception:
            pass

    def _call(self, rid, req, cwd, env, timeout, conn):
        server, tool, args = req.get("server") or "", req.get("tool") or "", req.get("args", "{}")
        if tool in C_NATIVE:
            return {"id": rid, "ok": False, "error": "unsupported"}

        def done(text, code=0):
            return {"id": rid, "ok": True, "result": text, "exit": code, "stderr": ""}
        if not tool.strip():
            return done(registry.EMPTY_TOOL_NAME_ERROR + "\n")
        if isinstance(args, str):
            arguments, error = parse_args_json(args)
            if error is not None:
                return done(error + "\n")
        else:
            arguments = args
        arguments, error = registry.prepare(tool, arguments)
        if error is not None:
            return done(error + "\n")

        if registry.native_handler(server, tool) is not None:
            r = run_forked(lambda: _child_invoke(server, tool, arguments), cwd, env, timeout, conn)
            if not (r["timed_out"] or r["interrupted"]):
                self._metric(tool, r["ms"], r["out"], r["crashed"], env)
            return self._reply(rid, r)

        t0 = time.monotonic()
        cfg = registry.mcp_config_for(server, load_config(cwd or "/", env))
        if not cfg:
            text, code = registry.mcp_not_found(server), 1
        else:
            code = 0
            wait = min(timeout, MCP_CALL_TIMEOUT)
            try:
                result = self.pool.call_tool(server, cfg, tool, arguments, timeout=wait, environ=env)
            except TimeoutError as e:
                if wait < MCP_CALL_TIMEOUT:
                    return {"id": rid, "ok": False, "error": "timeout", "result": "", "stderr": ""}
                result = {"error": str(e)}
            text = f"{registry._tool_text(result)}\n"
        self._metric(tool, (time.monotonic() - t0) * 1000, text, False, env)
        return done(text, code)

    # -- lifecycle --
    def bind(self):
        """Create the socket (dir 0700, socket 0600) unless a live server already answers there.
        Returns False when another server owns the path."""
        import fcntl
        d = os.path.dirname(self.sock_path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        st = os.stat(d)
        if st.st_uid != os.getuid():
            raise PermissionError("socket dir %s is not owned by this user" % d)
        os.chmod(d, 0o700)
        with open(self.sock_path + ".lock", "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(self.sock_path)
                return False  # a live server answers
            except OSError:
                pass
            finally:
                probe.close()
            try:
                os.unlink(self.sock_path)
            except FileNotFoundError:
                pass
            old = os.umask(0o177)
            try:
                self.httpd = _UnixServer(self.sock_path, _Handler)
            finally:
                os.umask(old)
            os.chmod(self.sock_path, 0o600)
            st = os.stat(self.sock_path)
            self.sock_id = (st.st_dev, st.st_ino)
        self.httpd.app = self
        return True

    def _socket_is_ours(self):
        try:
            st = os.stat(self.sock_path)
        except OSError:
            return False
        return (st.st_dev, st.st_ino) == self.sock_id

    def _unlink_socket(self):
        if self._socket_is_ours():
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass

    def begin_shutdown(self):
        """Stop accepting (unlink the socket now, so a new server can start), finish in-flight
        calls, then stop serve_forever."""
        if self._closing.is_set():
            return
        self._closing.set()
        self._unlink_socket()

        def stop():
            end = time.monotonic() + DEFAULT_TIMEOUT + 10
            while time.monotonic() < end:
                with self._lock:
                    if self._in_flight == 0:
                        break
                time.sleep(0.05)
            if self.httpd is not None:
                self.httpd.shutdown()
        threading.Thread(target=stop, daemon=True).start()

    def _monitor(self):
        while not self._closing.wait(0.25):
            if not self._socket_is_ours():
                self.begin_shutdown()  # removed or taken over by another server
                return
            with self._lock:
                busy, idle = self._in_flight > 0, time.monotonic() - self._last
            if not busy and idle >= self.idle_exit:
                self.begin_shutdown()
                return
            if self.autodrain and not busy and idle >= self.drain_idle:
                self._maybe_drain()

    def _maybe_drain(self):
        if self._drain_thread is not None and self._drain_thread.is_alive():
            return
        if time.monotonic() < self._next_drain:
            return

        def drain():
            r = run_forked(lambda: _child_drain(self.drain_limit), "/", None, 300)
            try:
                stats = json.loads(r["out"].strip().splitlines()[-1])
            except (ValueError, IndexError):
                stats = None
            now = time.monotonic()
            if not isinstance(stats, dict):
                self._next_drain = now + 30  # another backfill holds the lock, or the child failed
            elif stats.get("embedder") == "down":
                self._next_drain = now + 300
            elif stats.get("queue_left") and stats.get("embedded"):
                self._next_drain = now  # progress and more queued: next batch on the next tick
            else:
                self._next_drain = now + 60
        self._drain_thread = threading.Thread(target=drain, name="embed-drain", daemon=True)
        self._drain_thread.start()

    def serve(self):
        mon = threading.Thread(target=self._monitor, name="monitor", daemon=True)
        mon.start()
        try:
            self.httpd.serve_forever(poll_interval=0.25)
        finally:
            self._closing.set()
            self._unlink_socket()
            self.httpd.server_close()
            if self._drain_thread is not None:
                self._drain_thread.join(timeout=30)
            self.pool.close()


def _parse(argv):
    opts = {"idle_exit": DEFAULT_IDLE_EXIT, "drain_idle": DEFAULT_DRAIN_IDLE, "drain_limit": DEFAULT_DRAIN_LIMIT}
    flags = {"--idle-exit": ("idle_exit", float), "--drain-idle": ("drain_idle", float),
             "--drain-limit": ("drain_limit", int)}
    i = 0
    while i < len(argv):
        if argv[i] in flags and i + 1 < len(argv):
            key, conv = flags[argv[i]]
            opts[key] = conv(argv[i + 1])
            i += 2
        else:
            raise ValueError("unknown argument: %s" % argv[i])
    return opts


def main(argv):
    try:
        opts = _parse(list(argv))
    except ValueError as e:
        print("Usage: ai_mcp.py serve [--idle-exit SECS] [--drain-idle SECS] [--drain-limit N] (%s)" % e,
              file=sys.stderr)
        return 1
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)  # a vanished client is an OSError, not death
    # The server's own environment only drives idle work; each request carries the caller's.
    os.environ.pop("INFER_PRIVATE_MODE", None)
    # Pre-import what the forked children use, so no call pays for imports.
    from . import cli, render, search  # noqa: F401
    srv = ToolServer(socket_path(), **opts)
    if not srv.bind():
        return 0
    pidf = pid_path()
    try:
        os.makedirs(os.path.dirname(pidf), exist_ok=True)
        with open(pidf, "w") as f:
            f.write("%d\n" % os.getpid())
    except OSError:
        pidf = None
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: srv.begin_shutdown())
    try:
        os.chdir("/")
    except OSError:
        pass
    try:
        srv.serve()
    finally:
        if pidf:
            try:
                with open(pidf) as f:
                    mine = f.read().strip() == str(os.getpid())
                if mine:
                    os.unlink(pidf)
            except OSError:
                pass
    return 0
