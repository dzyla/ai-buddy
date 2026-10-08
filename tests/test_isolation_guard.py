"""Test-run isolation from the developer's live model server and systemd (root conftest.py).

A test must never reach the live llama-proxy (:8080) or llama-server (:8081): the proxy starts
the on-demand server, which rewrites ~/.local/share/ai/env. The root conftest therefore
  * refuses (and records) every in-process TCP connect to 127.0.0.1/localhost :8080/:8081 -- a
    recorded attempt fails the session; AI_TEST_ALLOW_LIVE_PORTS=1 opts out explicitly;
  * points INFER_BASE_URL (environment and the scratch-HOME env file) at an unroutable
    http://127.0.0.1:9/v1/, so code that falls back to "the configured server" hits nothing;
  * puts a no-op logging `systemctl` first on PATH, so no subprocess restarts a real unit;
  * refuses (and records) os.kill of a llama-server/llama-proxy the run did not start.
"""
import os
import shutil
import socket
import subprocess

import pytest


def test_live_ports_are_refused_and_recorded(monkeypatch):
    monkeypatch.delenv("AI_TEST_ALLOW_LIVE_PORTS", raising=False)
    log = socket._ai_test_live_port_violations
    n = len(log)
    try:
        for host, port in (("127.0.0.1", 8080), ("localhost", 8081)):
            with pytest.raises(ConnectionRefusedError):
                socket.create_connection((host, port), timeout=0.5)
        assert [v[1] for v in log[n:]] == [8080, 8081]
    finally:
        del log[n:]          # deliberate attempts: don't fail the session


def test_requests_to_the_live_proxy_are_refused(monkeypatch):
    requests = pytest.importorskip("requests")
    monkeypatch.delenv("AI_TEST_ALLOW_LIVE_PORTS", raising=False)
    log = socket._ai_test_live_port_violations
    n = len(log)
    try:
        with pytest.raises(requests.ConnectionError):
            requests.get("http://localhost:8080/v1/models", timeout=1)
        assert len(log) > n
    finally:
        del log[n:]


def test_other_local_ports_still_work():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        c = socket.create_connection(srv.getsockname(), timeout=1)
        c.close()
    finally:
        srv.close()


def test_default_model_url_is_unroutable():
    assert os.environ["INFER_BASE_URL"] == "http://127.0.0.1:9/v1/"
    env_file = os.path.join(os.environ["HOME"], ".local", "share", "ai", "env")
    assert 'INFER_BASE_URL="http://127.0.0.1:9/v1/"' in open(env_file).read()


def test_systemctl_is_a_logging_shim():
    path = shutil.which("systemctl")
    assert path and path.startswith(os.environ["AI_TEST_SCRATCH_HOME"]), path
    r = subprocess.run(["systemctl", "--user", "restart", "llama-server.service"], capture_output=True, text=True)
    assert r.returncode == 0
    log = os.path.join(os.environ["AI_TEST_SCRATCH_HOME"], "systemctl_calls.log")
    assert "--user restart llama-server.service" in open(log).read()


def test_zulip_debug_does_not_reach_the_live_server(tmp_path):
    """The leak the guard was added for: /debug probed http://localhost:8080/v1/models."""
    from zulip_bridge.memory import ZulipMemoryManager
    log = socket._ai_test_live_port_violations
    n = len(log)
    ZulipMemoryManager(storage_dir=str(tmp_path / "z")).get_diagnostics()
    assert log[n:] == []


def test_signals_to_a_foreign_llama_server_are_refused_and_recorded(monkeypatch, tmp_path):
    """ai-backend's kill_llama_server() SIGKILLs every llama-server on the host; in a test run it
    may only reach processes the run started itself."""
    import signal
    import sys
    import time
    monkeypatch.delenv("AI_TEST_ALLOW_LIVE_KILL", raising=False)
    pidfile = tmp_path / "pid"
    # setsid -f: the sleeper runs in its own session/process group, reparented away from pytest
    # (like a live server); it writes its own pid once it runs there.
    code = "import os, time; open(%r, 'w').write(str(os.getpid())); time.sleep(30)" % str(pidfile)
    subprocess.run(["setsid", "-f", sys.executable, "-c", code, "llama-server", "--model", "x"], check=True)
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.05)
    pid = int(pidfile.read_text())
    # never let a broken guard turn the killpg below into SIGKILL of the test run's own group
    assert os.getpgid(pid) == pid != os.getpgid(0)
    log = os._ai_test_live_kill_violations
    n = len(log)
    try:
        with pytest.raises(PermissionError):
            os.kill(pid, signal.SIGKILL)
        assert log[n:] and log[n][0] == pid
        with pytest.raises(PermissionError):
            os.killpg(pid, signal.SIGKILL)
        with pytest.raises(PermissionError):
            os.kill(-1, signal.SIGTERM)                    # "every process I may signal"
        os.kill(pid, 0)                                    # still alive; probes are allowed
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "llama-server"])
        os.kill(child.pid, signal.SIGKILL)                 # our own child: allowed
        child.wait(5)
    finally:
        del log[n:]          # deliberate attempt: don't fail the session
        monkeypatch.setenv("AI_TEST_ALLOW_LIVE_KILL", "1")
        os.kill(pid, signal.SIGKILL)
