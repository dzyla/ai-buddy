"""Persistent tool server (`ai_mcp.py serve`, aitools/server.py) and its C client (ai_toolclient.c).

Every server here runs with an isolated HOME and a private XDG_RUNTIME_DIR (a short mkdtemp dir:
unix socket paths are limited to 108 bytes) and is shut down at teardown; nothing touches the real
runtime dir. Offline: the MCP server is tests/fixtures/fake_mcp.py, the embedder
tests/fixtures/fake_embedder.py, the LLM tests/mock_llm_server.py.
"""
import json
import os
import shutil
import signal
import socket
import stat
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.path.join(REPO, "ai_mcp.py")
AI_BIN = os.path.join(REPO, "ai")
FAKE_MCP = os.path.join(REPO, "tests", "fixtures", "fake_mcp.py")
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, os.path.join(REPO, "tests", "fixtures"))

from test_aitools_layout import CLI_CASES, HAVE_RICH, RICH_CASES, SAMPLE_MD, _golden_bytes, _normalise  # noqa: E402
from test_offline import MockServer  # noqa: E402

CALL_CASES = [(n, a) for n, a in CLI_CASES if a and a[0] == "call-tool"]


# ── helpers ─────────────────────────────────────────────────────────────────
def _base_env(home, run):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("INFER_") and k not in ("PYTEST_CURRENT_TEST", "XDG_RUNTIME_DIR")}
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(run), PYTHONDONTWRITEBYTECODE="1",
               INFER_EMBED_AUTODRAIN="0")
    return env


def rpc(path, req, timeout=60):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    try:
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    return json.loads(buf.decode("utf-8", "surrogateescape"))


class Srv:
    """One `ai_mcp.py serve` process in an isolated HOME + XDG_RUNTIME_DIR."""

    def __init__(self, tmp_path, idle=60, extra_args=(), extra_env=None, script=SHIM):
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self.run = tempfile.mkdtemp(prefix="ais")
        self.env = _base_env(self.home, self.run)
        if extra_env:
            self.env.update(extra_env)
        self.sock = os.path.join(self.run, "ai", "tools.sock")
        self.script = script
        self.proc = subprocess.Popen([sys.executable, script, "serve", "--idle-exit", str(idle), *extra_args],
                                     env=self.env, cwd=str(tmp_path), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("server died: " + self.proc.stderr.read().decode())
            try:
                rpc(self.sock, {"id": 0, "op": "hello"}, timeout=2)
                return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("server did not come up")

    def req(self, op, cwd=None, env=None, **kw):
        r = {"id": 1, "op": op, "cwd": str(cwd or self.home), "env": env or self.env, "script": self.script}
        r.update(kw)
        return rpc(self.sock, r, timeout=kw.get("timeout", 60) + 30)

    def call(self, server, tool, args, cwd=None, env=None, timeout=60):
        if not isinstance(args, str):
            args = json.dumps(args)
        return self.req("call", cwd=cwd, env=env, server=server, tool=tool, args=args, timeout=timeout)

    def stop(self):
        if self.proc.poll() is None:
            try:
                rpc(self.sock, {"id": 9, "op": "shutdown"}, timeout=5)
            except OSError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        shutil.rmtree(self.run, ignore_errors=True)


@pytest.fixture
def srv(tmp_path):
    servers = []

    def make(**kw):
        s = Srv(tmp_path, **kw)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.stop()


def _workdir(root):
    work = root / "work"
    fx = work / "fixture"
    (fx / "sub").mkdir(parents=True)
    (fx / "hello.py").write_text("def f():\n    return 1\n\n\nclass A:\n    pass\n")
    (fx / "notes.md").write_text("# notes\nline two\n")
    return work


def _write_mcp_config(home, pidfile, disabled=False):
    cfg = home / ".config" / "ai"
    cfg.mkdir(parents=True, exist_ok=True)
    entry = {"command": sys.executable, "args": [FAKE_MCP], "env": {"FAKE_MCP_PIDFILE": str(pidfile)}}
    if disabled:
        entry["disabled"] = True
    (cfg / "mcp.json").write_text(json.dumps({"mcpServers": {"fake": entry}}))


def _pids(pidfile):
    return [int(x) for x in pidfile.read_text().split()] if pidfile.exists() else []


def _cli(args, env, cwd):
    return subprocess.run([sys.executable, SHIM] + args, env=env, cwd=str(cwd),
                          capture_output=True, text=True, timeout=120)


# ── Step 1: server protocol ─────────────────────────────────────────────────
def test_hello_perms_and_pidfile(srv):
    s = srv()
    r = rpc(s.sock, {"id": 7, "op": "hello"})
    assert r["ok"] is True and r["id"] == 7
    assert r["pid"] == s.proc.pid
    assert len(r["version"]) == 12 and all(c in "0123456789abcdef" for c in r["version"])
    assert stat.S_IMODE(os.stat(s.sock).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(s.sock)).st_mode) == 0o700
    assert (s.home / ".cache" / "ai" / "tools.pid").read_text().strip() == str(s.proc.pid)


def test_list_tools_equals_subprocess(srv, tmp_path):
    pidfile = tmp_path / "pids"
    s = srv()
    _write_mcp_config(s.home, pidfile)
    want = _cli(["list-tools"], s.env, s.home).stdout
    r = s.req("list_tools")
    assert r["ok"], r
    assert r["result"] == want
    assert "fake__echo" in r["result"]
    env = dict(s.env, INFER_TOOL_PROFILE="core")  # per-request env reaches the tool code
    assert s.req("list_tools", env=env)["result"] == _cli(["list-tools"], env, s.home).stdout


def test_call_outputs_match_call_tool_golden(srv, tmp_path):
    s = srv()
    env = dict(s.env, INFER_NO_MCP="1")
    want = json.loads(_golden_bytes("cli_cases.json", "{}"))
    for name, argv in CALL_CASES:
        case_dir = tmp_path / "cases" / name
        work = _workdir(case_dir)
        r = s.call(argv[1], argv[2], argv[3], cwd=work, env=env)
        assert r["ok"], (name, r)
        got = {"stdout": _normalise(r["result"], case_dir), "returncode": r["exit"]}
        assert got == want[name], name


def test_render_matches_cli(srv):
    s = srv()
    want = _cli(["render-markdown", SAMPLE_MD], s.env, s.home).stdout
    r = s.req("render", text=SAMPLE_MD)
    assert r["ok"] and r["result"] == want
    raw = s.req("render", text="**x**", env=dict(s.env, INFER_RAW_OUTPUT="1"))
    assert raw["result"] == "**x**\n"
    if HAVE_RICH or "render_markdown" not in RICH_CASES:
        golden = json.loads(_golden_bytes("cli_cases.json", "{}"))["render_markdown"]["stdout"]
        assert r["result"] == golden


def test_read_file_uses_request_cwd_concurrently(srv, tmp_path):
    s = srv()
    dirs = []
    for i in range(6):
        d = tmp_path / ("d%d" % i)
        d.mkdir()
        (d / "f.txt").write_text("content-%d\n" % i)
        dirs.append(d)
    results = {}

    def go(i):
        results[i] = s.call("read_file", "read_file", {"path": "f.txt"}, cwd=dirs[i])
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for i in range(6):
        want = _cli(["call-tool", "read_file", "read_file", '{"path": "f.txt"}'], s.env, dirs[i]).stdout
        assert results[i]["result"] == want
        assert "content-%d" % i in results[i]["result"]


def test_mcp_session_pooled_and_restarted(srv, tmp_path):
    pidfile = tmp_path / "pids"
    s = srv()
    _write_mcp_config(s.home, pidfile)
    outs = [s.call("fake", "echo", {"text": "hi %d" % i}) for i in range(3)]
    assert [o["result"] for o in outs] == ["hi 0\n", "hi 1\n", "hi 2\n"]
    assert len(_pids(pidfile)) == 1  # one stdio session served all three calls
    assert s.call("fake", "pid", {})["result"].strip() == str(_pids(pidfile)[0])
    want = _cli(["call-tool", "fake", "echo", '{"text": "hi 0"}'], s.env, s.home).stdout
    assert want == outs[0]["result"]
    os.kill(_pids(pidfile)[0], signal.SIGKILL)
    time.sleep(0.2)
    r = s.call("fake", "echo", {"text": "again"})
    assert r["ok"] and r["result"] == "again\n"
    assert len(_pids(pidfile)) >= 3  # the CLI comparison above started one; the pool restarted one


def test_disabled_mcp_server_is_not_started(srv, tmp_path):
    pidfile = tmp_path / "pids"
    s = srv()
    _write_mcp_config(s.home, pidfile, disabled=True)
    r = s.call("fake", "echo", {"text": "x"})
    want = _cli(["call-tool", "fake", "echo", '{"text": "x"}'], s.env, s.home)
    assert r["result"] == want.stdout and r["exit"] == want.returncode == 1
    assert _pids(pidfile) == []


class _Slow(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        time.sleep(8)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"late")


def test_native_tool_timeout(srv):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        s = srv()
        env = dict(s.env, INFER_FETCH_BASIC="1")
        t0 = time.time()
        r = s.call("fetch_webpage", "fetch_webpage", {"url": "http://127.0.0.1:%d/" % httpd.server_port},
                   env=env, timeout=2)
        took = time.time() - t0
        assert r["ok"] is False and "timeout" in r["error"]
        assert took < 3.0, took
        assert rpc(s.sock, {"id": 1, "op": "hello"})["ok"]  # the server survived
    finally:
        httpd.shutdown()


def test_mcp_tool_timeout_restarts_session(srv, tmp_path):
    pidfile = tmp_path / "pids"
    s = srv()
    _write_mcp_config(s.home, pidfile)
    t0 = time.time()
    r = s.call("fake", "sleep", {"seconds": 10}, timeout=1)
    assert r["ok"] is False and "timeout" in r["error"]
    assert time.time() - t0 < 2.0
    assert s.call("fake", "echo", {"text": "ok"})["result"] == "ok\n"
    assert len(_pids(pidfile)) == 2


def test_idle_exit(srv):
    s = srv(idle=2)
    s.proc.wait(timeout=10)
    assert not os.path.exists(s.sock)
    assert not (s.home / ".cache" / "ai" / "tools.pid").exists()


def test_home_mismatch_is_refused(srv, tmp_path):
    s = srv()
    other = tmp_path / "other"
    other.mkdir()
    r = s.call("read_file", "read_file", {"path": "x"}, env=dict(s.env, HOME=str(other)))
    assert r["ok"] is False and r["error"] == "mismatch"


def test_stale_code_makes_server_exit(tmp_path):
    copy = tmp_path / "copy"
    copy.mkdir()
    shutil.copy(SHIM, copy / "ai_mcp.py")
    shutil.copytree(os.path.join(REPO, "aitools"), copy / "aitools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    s = Srv(tmp_path, script=str(copy / "ai_mcp.py"))
    try:
        assert s.req("hello")["ok"]
        f = copy / "aitools" / "misc.py"
        f.write_text(f.read_text() + "\n# changed\n")
        r = s.req("hello")
        assert r["ok"] is False and r["error"] == "stale"
        s.proc.wait(timeout=10)
        assert not os.path.exists(s.sock)
    finally:
        s.stop()


def test_wrong_package_is_stale(srv, tmp_path):
    s = srv()
    copy = tmp_path / "elsewhere"
    copy.mkdir()
    shutil.copy(SHIM, copy / "ai_mcp.py")
    shutil.copytree(os.path.join(REPO, "aitools"), copy / "aitools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    r = rpc(s.sock, {"id": 1, "op": "hello", "script": str(copy / "ai_mcp.py"), "env": s.env})
    assert r["ok"] is False and r["error"] == "stale"


def test_metrics_recorded(srv, tmp_path):
    s = srv()
    work = _workdir(tmp_path)
    s.call("read_file", "read_file", {"path": "fixture/hello.py"}, cwd=work)
    s.call("read_file", "read_file", {"path": "fixture/nope"}, cwd=work)
    lines = [json.loads(x) for x in (s.home / ".cache" / "ai" / "metrics.jsonl").read_text().splitlines()]
    assert [(m["tool"], m["success"]) for m in lines] == [("read_file", True), ("read_file", False)]


def test_embed_queue_drained_when_idle(tmp_path):
    import fake_embedder
    from aitools import state
    emb = fake_embedder.start()
    try:
        state.memory_add("The lab printer is on the third floor")
        c = state.connect(read=True)
        assert c.execute("SELECT count(*) FROM embed_queue").fetchone()[0] == 1
        c.close()
        s = Srv(tmp_path, extra_args=("--drain-idle", "1"),
                extra_env={"INFER_EMBED_AUTODRAIN": "0", "AI_EMBED_URL": "http://127.0.0.1:9"})
        try:
            # Drain settings come from the callers' requests, not from the server's own env.
            assert s.req("hello", env=dict(s.env, INFER_EMBED_AUTODRAIN="1", AI_EMBED_URL=emb.url))["ok"]
            deadline = time.time() + 20
            n = 0
            while time.time() < deadline:
                c = state.connect(read=True)
                n = c.execute("SELECT count(*) FROM vectors").fetchone()[0]
                q = c.execute("SELECT count(*) FROM embed_queue").fetchone()[0]
                c.close()
                if n and not q:
                    break
                time.sleep(0.3)
            assert n >= 1 and q == 0
        finally:
            s.stop()
    finally:
        emb.stop()


def test_no_drain_when_disabled(tmp_path):
    import fake_embedder
    from aitools import state
    emb = fake_embedder.start()
    try:
        state.memory_add("Something to embed")
        s = Srv(tmp_path, extra_args=("--drain-idle", "1"),
                extra_env={"INFER_EMBED_AUTODRAIN": "1", "AI_EMBED_URL": emb.url})
        try:
            time.sleep(2)  # no request yet: no drain settings, no drain
            c = state.connect(read=True)
            assert c.execute("SELECT count(*) FROM vectors").fetchone()[0] == 0
            c.close()
            assert s.req("hello", env=dict(s.env, INFER_EMBED_AUTODRAIN="0", AI_EMBED_URL=emb.url))["ok"]
            time.sleep(3)
            c = state.connect(read=True)
            assert c.execute("SELECT count(*) FROM vectors").fetchone()[0] == 0
            c.close()
        finally:
            s.stop()
    finally:
        emb.stop()


# ── per-call isolation (in-process unit tests of the forked runner) ─────────
def _raise():
    raise ValueError("boom")


def _exit_msg():
    sys.exit("bye")


def _prints():
    print("stray print")
    os.system("echo from-a-subprocess")
    return 3


def _hang():
    time.sleep(30)


def test_forked_runner_isolates_misbehaving_tools(tmp_path):
    from aitools import server
    cwd0, env0 = os.getcwd(), dict(os.environ)
    r = server.run_forked(_raise, str(tmp_path), dict(os.environ, X_CHILD="1"), timeout=10)
    assert r["code"] == 1 and r["crashed"] and "ValueError: boom" in r["err"]
    r = server.run_forked(_exit_msg, str(tmp_path), dict(os.environ), timeout=10)
    assert r["code"] == 1 and not r["crashed"] and "bye" in r["err"]
    r = server.run_forked(_prints, str(tmp_path), dict(os.environ), timeout=10)
    # Block-buffered like a piped `call-tool` process: the subprocess's line comes first.
    assert r["code"] == 3 and r["out"] == "from-a-subprocess\nstray print\n"
    t0 = time.time()
    r = server.run_forked(_hang, str(tmp_path), dict(os.environ), timeout=1)
    assert r["timed_out"] and time.time() - t0 < 2.5
    assert os.getcwd() == cwd0 and dict(os.environ) == env0  # the caller's cwd/env are never touched


# ── Step 3: the `ai` binary with the server ─────────────────────────────────
def _bin_env(home, run, base_url, **extra):
    env = _base_env(home, run)
    env.update(INFER_BASE_URL=base_url, INFER_API_KEY="t", INFER_MODEL="mock", INFER_TOOL_CHOICE="auto",
               INFER_AUTO_APPROVE="1", INFER_STATE_CONTEXT="0",
               INFER_ENGINE="c")  # the tool server is the C engine's client (agent/ runs tools in-process)
    env.update(extra)
    return env


def _read_call(i, path):
    return {"id": "r%d" % i, "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps({"path": path})}}


def _exec_call(i, cmd):
    return {"id": "e%d" % i, "type": "function",
            "function": {"name": "execute_command", "arguments": json.dumps({"command": cmd})}}


def _tool_msgs(reqs):
    return [m.get("content") or "" for m in reqs[-1]["messages"] if m.get("role") == "tool"]


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    work = tmp_path / "work"
    (home / ".local" / "bin").mkdir(parents=True)
    work.mkdir()
    os.symlink(SHIM, home / ".local" / "bin" / "ai_mcp.py")
    run = tempfile.mkdtemp(prefix="aib")
    yield home, work, run
    pidf = home / ".cache" / "ai" / "tools.pid"
    if pidf.exists():
        try:
            os.kill(int(pidf.read_text().strip()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    sock = os.path.join(run, "ai", "tools.sock")
    for _ in range(50):
        if not os.path.exists(sock):
            break
        time.sleep(0.1)
    shutil.rmtree(run, ignore_errors=True)


def _run_ai(work, env, timeout=120):
    return subprocess.run([AI_BIN, "-q", "read the files"], cwd=str(work), env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)


def _server_pid(home):
    p = home / ".cache" / "ai" / "tools.pid"
    return int(p.read_text().strip()) if p.exists() else None


def test_binary_uses_server_and_survives_server_kill(sandbox, tmp_path):
    home, work, run = sandbox
    (work / "a.txt").write_text("ALPHA\n")
    (work / "b.txt").write_text("BRAVO\n")
    seq = [_read_call(1, "a.txt"), _exec_call(2, "kill -9 $(cat $HOME/.cache/ai/tools.pid)"),
           _read_call(3, "b.txt")]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as m:
        r = _run_ai(work, _bin_env(home, run, m.base_url, INFER_TOOL_SERVER="1"))
        reqs = m.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    tools = _tool_msgs(reqs)
    assert any("ALPHA" in t for t in tools) and any("BRAVO" in t for t in tools), (tools, r.stderr[-1500:])
    pid = _server_pid(home)  # respawned after the kill
    assert pid and os.path.exists(os.path.join(run, "ai", "tools.sock"))


def test_binary_falls_back_when_server_cannot_restart(sandbox, tmp_path):
    home, work, run = sandbox
    (work / "a.txt").write_text("ALPHA\n")
    (work / "b.txt").write_text("BRAVO\n")
    sock = os.path.join(run, "ai", "tools.sock")
    # Kill the server, then put a directory where the socket goes: no new server can bind.
    sabotage = "kill -9 $(cat $HOME/.cache/ai/tools.pid); rm -f %s; mkdir %s" % (sock, sock)
    seq = [_read_call(1, "a.txt"), _exec_call(2, sabotage), _read_call(3, "b.txt")]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as m:
        r = _run_ai(work, _bin_env(home, run, m.base_url, INFER_TOOL_SERVER="1"))
        reqs = m.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    tools = _tool_msgs(reqs)
    assert any("ALPHA" in t for t in tools) and any("BRAVO" in t for t in tools)
    os.rmdir(sock)


def test_binary_server_disabled_never_creates_socket(sandbox, tmp_path):
    home, work, run = sandbox
    (work / "a.txt").write_text("ALPHA\n")
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps([_read_call(1, "a.txt")])) as m:
        r = _run_ai(work, _bin_env(home, run, m.base_url, INFER_TOOL_SERVER="0"))
        reqs = m.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert any("ALPHA" in t for t in _tool_msgs(reqs))
    assert not os.path.exists(os.path.join(run, "ai"))
    assert _server_pid(home) is None


def _timed_run(home, work, run, tmp_path, tag, server):
    seq = [_read_call(i, "f%d.txt" % i) for i in range(10)]
    cap = str(tmp_path / ("cap_%s.jsonl" % tag))
    times = tmp_path / ("times_%s" % tag)
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TIMES=str(times)) as m:
        r = _run_ai(work, _bin_env(home, run, m.base_url, INFER_TOOL_SERVER=server))
        reqs = m.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert sum("DATA-" in t for t in _tool_msgs(reqs)) == 10
    ts = [float(x) for x in times.read_text().split()]
    return ts[-1] - ts[0]  # first tool-call request -> request after the 10th tool result


def test_binary_tool_calls_faster_with_server(sandbox, tmp_path):
    home, work, run = sandbox
    for i in range(10):
        (work / ("f%d.txt" % i)).write_text("DATA-%d\n" % i)
    # Warm the server once so the measured runs do not include its start-up, then take the median
    # of 3 interleaved runs per mode (one slow outlier on a loaded machine can't flip the result).
    _timed_run(home, work, run, tmp_path, "warm", "1")
    ons, offs = [], []
    for i in range(3):
        ons.append(_timed_run(home, work, run, tmp_path, "on%d" % i, "1"))
        offs.append(_timed_run(home, work, run, tmp_path, "off%d" % i, "0"))
    with_server, without = statistics.median(ons), statistics.median(offs)
    print("10 read_file calls (median of 3): with server %.3fs, without %.3fs (%.1fx)"
          % (with_server, without, without / max(with_server, 1e-6)))
    # The root conftest forces CI=1 for every test; it keeps the caller's value in AI_REAL_CI.
    if os.environ.get("AI_REAL_CI", os.environ.get("CI")) == "1" or os.environ.get("GITHUB_ACTIONS") == "true":
        return  # shared CI runners: logged above, not asserted
    # each server-less call pays a Python start-up (~100+ ms); 2x is far below the usual ~5-10x
    assert without >= 2 * with_server, (ons, offs)


def test_binary_replaces_a_stale_server(sandbox, tmp_path):
    """A server running another aitools copy answers "stale" to the binary (whose ai_mcp.py
    resolves to this checkout), steps down, and the client respawns one with its own code."""
    home, work, run = sandbox
    copy = tmp_path / "copy"
    copy.mkdir()
    shutil.copy(SHIM, copy / "ai_mcp.py")
    shutil.copytree(os.path.join(REPO, "aitools"), copy / "aitools", ignore=shutil.ignore_patterns("__pycache__"))
    env = _base_env(home, run)
    old = subprocess.Popen([sys.executable, str(copy / "ai_mcp.py"), "serve"], env=env, cwd=str(work),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sock = os.path.join(run, "ai", "tools.sock")
    for _ in range(150):
        if os.path.exists(sock):
            break
        time.sleep(0.1)
    (work / "a.txt").write_text("ALPHA\n")
    cap = str(tmp_path / "cap.jsonl")
    try:
        with MockServer(cap, MOCK_TOOL_SEQ=json.dumps([_read_call(1, "a.txt")])) as m:
            r = _run_ai(work, _bin_env(home, run, m.base_url, INFER_TOOL_SERVER="1"))
            reqs = m.requests()
        assert r.returncode == 0, r.stderr[-2000:]
        assert any("ALPHA" in t for t in _tool_msgs(reqs))
        old.wait(timeout=15)  # the stale server exited
        new = _server_pid(home)
        assert new and new != old.pid
        assert rpc(sock, {"id": 1, "op": "hello", "script": SHIM})["ok"]
    finally:
        if old.poll() is None:
            old.kill()
            old.wait()


# ── fix round 1: no double execution, pool safety, scope spawn ──────────────
def test_unterminated_request_is_not_executed(srv, tmp_path):
    s = srv()
    target = tmp_path / "written.txt"
    req = {"id": 1, "op": "call", "server": "write_file", "tool": "write_file", "cwd": str(tmp_path),
           "env": s.env, "args": json.dumps({"path": str(target), "content": "x"})}
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(s.sock)
    c.sendall(json.dumps(req).encode())  # no newline: a client that died mid-send
    c.shutdown(socket.SHUT_WR)
    c.settimeout(5)
    assert c.recv(100) == b""
    c.close()
    time.sleep(0.3)
    assert not target.exists()


def test_mcp_call_not_resent_after_server_died_mid_call(srv, tmp_path):
    pidfile, effect = tmp_path / "pids", tmp_path / "effect"
    s = srv()
    _write_mcp_config(s.home, pidfile)
    assert s.call("fake", "echo", {"text": "warm"})["result"] == "warm\n"  # the session is reused below
    r = s.call("fake", "crash", {"file": str(effect)})
    assert r["ok"] and r["result"].startswith("Error:")
    assert effect.read_text() == "ran\n"  # executed exactly once, never re-sent
    assert s.call("fake", "echo", {"text": "after"})["result"] == "after\n"


def _two_configs(tmp_path, s):
    envs = []
    for tag in ("a", "b"):
        cfg = tmp_path / ("mcp_%s.json" % tag)
        cfg.write_text(json.dumps({"mcpServers": {"fake": {
            "command": sys.executable, "args": [FAKE_MCP],
            "env": {"FAKE_MCP_PIDFILE": str(tmp_path / ("pids_" + tag))}}}}))
        envs.append(dict(s.env, INFER_MCP_CONFIG=str(cfg)))
    return envs


def test_config_change_never_closes_a_session_under_a_call(srv, tmp_path):
    s = srv()
    env_a, env_b = _two_configs(tmp_path, s)
    out = {}
    t = threading.Thread(target=lambda: out.update(a=s.call("fake", "sleep", {"seconds": 2}, env=env_a)))
    t.start()
    time.sleep(0.8)  # A's call is in flight on config A's session
    assert s.call("fake", "echo", {"text": "b"}, env=env_b)["result"] == "b\n"  # replaces the session
    t.join()
    assert out["a"]["ok"] and out["a"]["result"] == "slept\n"


def test_slow_mcp_start_does_not_block_other_servers(srv, tmp_path):
    s = srv()
    cfg = s.home / ".config" / "ai"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "mcp.json").write_text(json.dumps({"mcpServers": {
        "slow": {"command": sys.executable, "args": [FAKE_MCP], "env": {"FAKE_MCP_START_DELAY": "3"}},
        "fast": {"command": sys.executable, "args": [FAKE_MCP]}}}))
    assert s.call("fast", "echo", {"text": "warm"})["result"] == "warm\n"
    out = {}
    t = threading.Thread(target=lambda: out.update(slow=s.call("slow", "echo", {"text": "s"})))
    t.start()
    time.sleep(0.3)
    t0 = time.time()
    assert s.call("fast", "echo", {"text": "f"})["result"] == "f\n"
    assert time.time() - t0 < 1.0
    t.join()
    assert out["slow"]["result"] == "s\n"


def test_binary_never_reruns_a_call_whose_server_died(sandbox, tmp_path):
    """C1: the server is SIGKILLed while a side-effecting call runs; the model sees the
    "connection lost" error and the call is not re-run through popen."""
    home, work, run = sandbox
    effect = tmp_path / "effect.txt"
    code = "open(%r, 'a').write('ran\\n')\nimport time\ntime.sleep(6)" % str(effect)
    seq = [{"id": "p1", "type": "function",
            "function": {"name": "python_playground", "arguments": json.dumps({"code": code})}}]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as m:
        p = subprocess.Popen([AI_BIN, "-q", "go"], cwd=str(work), stdin=subprocess.DEVNULL,
                             env=_bin_env(home, run, m.base_url, INFER_TOOL_SERVER="1"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 30
        while time.time() < deadline and not (effect.exists() and effect.read_text()):
            time.sleep(0.05)
        os.kill(_server_pid(home), signal.SIGKILL)
        _, err = p.communicate(timeout=60)
        reqs = m.requests()
    assert p.returncode == 0, err[-2000:]
    time.sleep(1)
    assert effect.read_text() == "ran\n"
    assert any("tool server connection lost" in t for t in _tool_msgs(reqs))


def test_binary_spawns_server_in_systemd_scope(sandbox, tmp_path):
    """I2: with a systemd user instance reachable, the server is started via
    `systemd-run --user --scope --collect --unit=ai-tools-<uid>-...` (stubbed here)."""
    home, work, run = sandbox
    open(os.path.join(run, "bus"), "w").close()  # "a user bus is reachable"
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    log = tmp_path / "systemd-run.log"
    stub = stub_dir / "systemd-run"
    stub.write_text('#!/bin/sh\nprintf "%%s\\n" "$@" > %s\n'
                    'while [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n' % log)
    stub.chmod(0o755)
    (work / "a.txt").write_text("ALPHA\n")
    env = _bin_env(home, run, "", INFER_TOOL_SERVER="1")
    env["PATH"] = "%s:%s" % (stub_dir, env["PATH"])
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps([_read_call(1, "a.txt")])) as m:
        env["INFER_BASE_URL"] = m.base_url
        r = _run_ai(work, env)
        reqs = m.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert any("ALPHA" in t for t in _tool_msgs(reqs))
    args = log.read_text().split("\n")
    assert args[:4] == ["--user", "--scope", "--collect", "--quiet"]
    assert args[4].startswith("--unit=ai-tools-%d-" % os.getuid())
    assert args[5:8] == ["--", "python3", os.path.realpath(SHIM)] and args[8] == "serve"
    assert _server_pid(home)
