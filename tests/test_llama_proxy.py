"""services/llama-proxy.py: the on-demand trigger proxy in front of llama-server.

The proxy listens on the client port, starts the backend unit on the first inference request,
waits for its /health, then forwards. These tests run the real proxy script on private ports with
a fake `systemctl` / `journalctl` first on PATH and a fake backend (a tiny HTTP server the fake
`systemctl start` launches), so nothing touches the live :8080/:8081 or a real systemd unit.

Covered: cold start unchanged; fail fast (503 + reason) when the unit fails (VRAM preflight) or
does not become healthy within LLAMA_PROXY_START_TIMEOUT; /health reports the backend state
without starting it; a client disconnect closes the upstream connection (llama-server then
cancels the generation); SSE is forwarded as it arrives; install.sh installs the script.
"""
import http.client
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PROXY = os.path.join(REPO, "services", "llama-proxy.py")

PREFLIGHT_LOG = """\
Starting llama-server (on-demand, idle-unload)...
[llama-wrapper] ERROR: model will not fit in VRAM.
[llama-wrapper]   model:        some-model-Q4_K_M.gguf
[llama-wrapper]   estimated:    30.1 GiB (weights 20.0 + KV/state + overhead) at ctx 262144
[llama-wrapper]   available:    23.5 GiB free on GPU 0
[llama-wrapper]   short by:     ~6.6 GiB
[llama-wrapper] Fixes (pick one):
[llama-wrapper]   - lower the context:           ai-backend ctx 131072
[llama-wrapper] Aborting before launching llama-server (it would have OOM'd mid-load).
llama-server.service: Main process exited, code=exited, status=1/FAILURE
llama-server.service: Failed with result 'exit-code'.
"""

FAKE_BACKEND = r'''
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
port, delay, sd = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
time.sleep(delay)

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass
    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        return self._json(200, {"data": [{"id": "fake"}]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with open(os.path.join(sd, "backend_requests.log"), "a") as f:
            f.write(self.path + "\n")
        if not body.get("stream"):
            return self._json(200, {"choices": [{"message": {"role": "assistant", "content": "FAKE_OK"}}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        n = int(body.get("n_chunks", 5))
        gap = float(body.get("gap", 0.3))
        try:
            i = 0
            while n < 0 or i < n:
                ev = b"data: " + json.dumps({"i": i}).encode() + b"\n\n"
                self.wfile.write(b"%x\r\n%s\r\n" % (len(ev), ev))
                self.wfile.flush()
                i += 1
                time.sleep(gap)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            closed = False
        except (BrokenPipeError, ConnectionResetError):
            closed = True
        with open(os.path.join(sd, "disconnects.log"), "a") as f:
            f.write(json.dumps({"closed": closed, "sent": i}) + "\n")

ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''

FAKE_SYSTEMCTL = r'''#!/usr/bin/env python3
import os, subprocess, sys
sd = os.environ["FAKE_SD_DIR"]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
with open(os.path.join(sd, "calls.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
def rd(name, default=""):
    try:
        return open(os.path.join(sd, name)).read().strip()
    except OSError:
        return default
def wr(name, text):
    open(os.path.join(sd, name), "w").write(text)
cmd = args[0] if args else ""
if cmd == "start":
    mode = rd("mode", "ok")
    if mode == "ok":
        p = subprocess.Popen([sys.executable, os.path.join(sd, "fake_backend.py"), rd("port"), rd("delay", "0"), sd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        wr("backend.pid", str(p.pid))
        wr("state", "active")
    elif mode == "fail":
        wr("state", "failed")
        import time
        with open(os.path.join(sd, "journal.log"), "a") as f:
            for line in rd("fail_log").splitlines():
                f.write("%d %s\n" % (time.time(), line))
    elif mode == "start_error":
        sys.stderr.write("Failed to start llama-server.service: Unit llama-server.service not found.\n")
        sys.exit(5)
    else:  # hang: activating forever, nothing listens
        wr("state", "activating")
    sys.exit(0)
if cmd == "is-active":
    st = rd("state", "inactive")
    print(st)
    sys.exit(0 if st == "active" else 3)
if cmd == "is-failed":
    st = rd("state", "inactive")
    print(st)
    sys.exit(0 if st == "failed" else 1)
if cmd == "show":
    st = rd("state", "inactive")
    print("ActiveState=%s" % st)
    print("SubState=%s" % ("failed" if st == "failed" else "running"))
    sys.exit(0)
sys.exit(0)
'''

FAKE_JOURNALCTL = r'''#!/usr/bin/env python3
import os, sys
sd = os.environ["FAKE_SD_DIR"]
with open(os.path.join(sd, "journal_calls.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
try:
    rows = [l.split(" ", 1) for l in open(os.path.join(sd, "journal.log")).read().splitlines()]
except OSError:
    rows = []
since = 0
if "--since" in sys.argv:
    since = int(sys.argv[sys.argv.index("--since") + 1].lstrip("@"))
lines = [text for ts, text in rows if int(ts) >= since]  # journal.log: "<epoch> <message>"
n = 30
if "-n" in sys.argv:
    n = int(sys.argv[sys.argv.index("-n") + 1])
print("\n".join(lines[-n:]))
'''


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _exe(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class Proxy:
    def __init__(self, tmp_path, mode="ok", delay=0.0, start_timeout=20, fail_cache=None):
        self.sd = tmp_path / "sd"
        self.sd.mkdir()
        self.port, self.backend_port = _free_port(), _free_port()
        bindir = tmp_path / "fakebin"
        bindir.mkdir()
        _exe(bindir / "systemctl", FAKE_SYSTEMCTL)
        _exe(bindir / "journalctl", FAKE_JOURNALCTL)
        (self.sd / "fake_backend.py").write_text(FAKE_BACKEND)
        (self.sd / "port").write_text(str(self.backend_port))
        (self.sd / "delay").write_text(str(delay))
        (self.sd / "mode").write_text(mode)
        (self.sd / "fail_log").write_text(PREFLIGHT_LOG)
        (self.sd / "journal.log").write_text("1000 old run: an older unrelated line\n")
        self.env = dict(os.environ, FAKE_SD_DIR=str(self.sd),
                        PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""),
                        LLAMA_PROXY_HOST="127.0.0.1", LLAMA_PROXY_PORT=str(self.port),
                        LLAMA_PROXY_BACKEND_HOST="127.0.0.1", LLAMA_PROXY_BACKEND_PORT=str(self.backend_port),
                        LLAMA_PROXY_START_TIMEOUT=str(start_timeout), PYTHONUNBUFFERED="1")
        if fail_cache is not None:
            self.env["LLAMA_PROXY_FAIL_CACHE"] = str(fail_cache)
        self.log = tmp_path / "proxy.log"
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen([sys.executable, PROXY], env=self.env, stdout=open(self.log, "w"),
                                     stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return self
            except OSError:
                time.sleep(0.05)
        raise AssertionError("proxy did not listen: " + self.log.read_text())

    def __exit__(self, *a):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        pid = (self.sd / "backend.pid").read_text() if (self.sd / "backend.pid").exists() else ""
        if pid:
            try:
                os.killpg(int(pid), signal.SIGKILL)
            except OSError:
                pass

    def calls(self):
        p = self.sd / "calls.log"
        return p.read_text().splitlines() if p.exists() else []

    def request(self, method, path, body=None, timeout=60):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        data = json.dumps(body).encode() if body is not None else None
        c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        r = c.getresponse()
        out = r.status, r.read()
        c.close()
        return out


CHAT = "/v1/chat/completions"


def test_cold_start_starts_unit_waits_for_health_and_forwards(tmp_path):
    with Proxy(tmp_path, mode="ok", delay=1.5) as px:
        t0 = time.monotonic()
        status, body = px.request("POST", CHAT, {"model": "m", "messages": []})
        assert status == 200, body
        assert json.loads(body)["choices"][0]["message"]["content"] == "FAKE_OK"
        assert time.monotonic() - t0 >= 1.4          # it really waited for the slow backend
        assert sum("start" in c for c in px.calls()) == 1
        # a second request goes straight through (no second start)
        status, _ = px.request("POST", CHAT, {"model": "m", "messages": []})
        assert status == 200
        assert sum("start" in c for c in px.calls()) == 1


def test_unit_failure_fails_fast_with_the_preflight_reason(tmp_path):
    with Proxy(tmp_path, mode="fail", start_timeout=120) as px:
        t0 = time.monotonic()
        status, body = px.request("POST", CHAT, {"model": "m", "messages": []})
        elapsed = time.monotonic() - t0
    assert status == 503, body
    assert elapsed < 15, elapsed                      # not the 120 s start timeout
    err = json.loads(body)["error"]
    assert err["type"] == "backend_unavailable"
    assert "model will not fit in VRAM" in err["message"]
    assert "ai-backend" in err["message"]
    assert "older unrelated line" not in json.dumps(err)  # only this start attempt's journal lines
    assert any("will not fit" in line for line in err.get("log", []))


def test_systemctl_start_error_is_reported(tmp_path):
    with Proxy(tmp_path, mode="start_error") as px:
        status, body = px.request("POST", CHAT, {"model": "m", "messages": []})
    assert status == 503
    assert "not found" in json.loads(body)["error"]["message"]


def test_backend_not_healthy_within_start_timeout(tmp_path):
    with Proxy(tmp_path, mode="hang", start_timeout=2) as px:
        t0 = time.monotonic()
        status, body = px.request("POST", CHAT, {"model": "m", "messages": []})
        elapsed = time.monotonic() - t0
    assert status == 503, body
    assert 1.5 <= elapsed < 10, elapsed
    msg = json.loads(body)["error"]["message"]
    assert "2s" in msg or "2 s" in msg, msg


def test_recent_failure_is_reported_without_a_new_start_attempt(tmp_path):
    with Proxy(tmp_path, mode="fail", fail_cache=30) as px:
        assert px.request("GET", "/props")[0] == 503
        assert px.request("GET", "/v1/models")[0] == 503
        status, body = px.request("POST", CHAT, {"model": "m", "messages": []})
        assert status == 503 and "will not fit" in json.loads(body)["error"]["message"]
        assert sum("start" in c for c in px.calls()) == 1


def test_health_reports_backend_state_without_starting_it(tmp_path):
    with Proxy(tmp_path, mode="fail") as px:
        status, body = px.request("GET", "/health")
        h = json.loads(body)
        assert status == 200 and h["proxy"] == "ok" and h["backend"] == "down", h
        assert not any("start" in c for c in px.calls())
        px.request("POST", CHAT, {"model": "m", "messages": []})          # fails
        h = json.loads(px.request("GET", "/health")[1])
        assert h["backend"] == "failed" and "will not fit" in h["reason"], h
        assert sum("start" in c for c in px.calls()) == 1


def test_health_ready_after_cold_start_and_root_still_identifies_the_proxy(tmp_path):
    with Proxy(tmp_path, mode="ok") as px:
        status, body = px.request("GET", "/")
        assert status == 200 and b"llama-proxy-ready" in body      # ai-backend is_proxy_running()
        assert px.request("POST", CHAT, {"model": "m", "messages": []})[0] == 200
        h = json.loads(px.request("GET", "/health")[1])
        assert h["proxy"] == "ok" and h["backend"] == "ready", h


def test_health_reports_starting_while_the_backend_loads(tmp_path):
    import threading
    with Proxy(tmp_path, mode="ok", delay=3) as px:
        t = threading.Thread(target=px.request, args=("POST", CHAT, {"model": "m", "messages": []}))
        t.start()
        time.sleep(1.0)
        h = json.loads(px.request("GET", "/health", timeout=5)[1])
        t.join(30)
    assert h["backend"] == "starting", h


def test_sse_is_forwarded_as_it_arrives(tmp_path):
    with Proxy(tmp_path, mode="ok") as px:
        px.request("POST", CHAT, {"model": "m", "messages": []})       # warm
        c = http.client.HTTPConnection("127.0.0.1", px.port, timeout=30)
        t0 = time.monotonic()
        c.request("POST", CHAT, body=json.dumps({"stream": True, "n_chunks": 6, "gap": 0.4}),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        first = r.read1(65536) if hasattr(r, "read1") else r.read(1)
        t_first = time.monotonic() - t0
        rest = r.read()
        t_all = time.monotonic() - t0
        c.close()
    assert first.startswith(b"data: ")
    assert t_first < 1.5 and t_all > 2.0, (t_first, t_all)
    assert (first + rest).count(b"data: ") == 6


def test_client_disconnect_closes_the_upstream_connection(tmp_path):
    with Proxy(tmp_path, mode="ok") as px:
        px.request("POST", CHAT, {"model": "m", "messages": []})       # warm
        s = socket.create_connection(("127.0.0.1", px.port), timeout=10)
        body = json.dumps({"stream": True, "n_chunks": -1, "gap": 0.1}).encode()
        s.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        got = b""
        while b"data: " not in got:
            got += s.recv(4096)
        s.close()
        disc = px.sd / "disconnects.log"
        for _ in range(100):
            if disc.exists() and disc.read_text().strip():
                break
            time.sleep(0.1)
        rows = [json.loads(x) for x in disc.read_text().splitlines()] if disc.exists() else []
    assert rows and rows[0]["closed"] is True, rows


def test_non_api_paths_never_start_the_backend(tmp_path):
    with Proxy(tmp_path, mode="ok") as px:
        assert px.request("GET", "/favicon.ico")[0] == 200
        assert px.request("GET", "/wp-login.php")[0] == 404
        assert not any("start" in c for c in px.calls())


def test_tracked_proxy_has_no_personal_paths():
    src = open(PROXY, encoding="utf-8").read()
    assert "/home/" not in src and "dzyla" not in src.lower()


def test_install_copies_the_proxy_to_local_bin(tmp_path):
    if not os.path.exists(os.path.join(REPO, "ai")):
        pytest.skip("ai binary not built")
    fake = tmp_path / "fakebin"
    fake.mkdir()
    for n in ("make", "pip", "sudo", "git", "apt-get"):
        _exe(fake / n, "#!/bin/sh\nexit 0\n")
    sd_log = tmp_path / "systemctl.log"
    _exe(fake / "systemctl", '#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % sd_log)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("INFER_") or k in ("INFER_TOOL_SERVER", "INFER_ENGINE", "INFER_ENGINE_STRICT")}
    env.update(HOME=str(home), PATH="%s:%s" % (fake, os.environ.get("PATH", "")))
    r = subprocess.run(["bash", os.path.join(REPO, "install.sh")], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    dst = home / ".local" / "bin" / "llama-proxy.py"
    assert dst.exists(), r.stdout[-2000:]
    assert dst.read_bytes() == open(PROXY, "rb").read()
    assert os.access(dst, os.X_OK)
    # the running proxy is never restarted or stopped by the installer
    assert "llama-proxy" not in (sd_log.read_text() if sd_log.exists() else "")
