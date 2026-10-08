"""`ai-backend status` reads the llama-proxy /health body: the proxy answers 200 whatever the
backend does, so RUNNING is printed only when the backend is "ready" (the incident: the backend
had failed its VRAM preflight while status said RUNNING / HTTP 200 (OK))."""
import importlib.util
import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-backend")


@pytest.fixture(scope="module")
def ab(tmp_path_factory):
    dst = tmp_path_factory.mktemp("abmod_status") / "ai_backend_mod.py"
    shutil.copyfile(SRC, dst)
    spec = importlib.util.spec_from_file_location("ai_backend_mod_status", dst)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAILED = {"status": "ok", "proxy": "ok", "backend": "failed",
          "reason": "llama-server is unavailable: ERROR: model will not fit in VRAM."}


def test_describe_server_states(ab):
    assert ab.describe_server(None, None) == (False, "STOPPED", "Inactive / Not running")
    ok, state, health = ab.describe_server(8080, FAILED, active_pid="123")
    assert not ok and "RUNNING" not in state
    assert state.startswith("PROXY UP") and "backend failed: " in state and "will not fit" in state
    assert "HTTP 200 (OK)" not in health and "backend failed" in health
    for backend in ("down", "starting"):
        ok, state, _ = ab.describe_server(8080, {"proxy": "ok", "backend": backend})
        assert not ok and state == "PROXY UP :8080 · backend %s" % backend
    ok, state, health = ab.describe_server(8080, {"proxy": "ok", "backend": "ready"}, active_pid="77")
    assert ok and state.startswith("RUNNING (PID: 77)") and "backend ready" in health
    # a plain llama-server /health
    assert ab.describe_server(8081, {"status": "ok"}, "9") == (True, "RUNNING (PID: 9)", "HTTP 200 (OK)")


def _serve(body):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_probe_health_parses_the_proxy_body(ab):
    srv = _serve(FAILED)
    try:
        port, body = ab.probe_health([srv.server_address[1]])
    finally:
        srv.shutdown()
    assert port == srv.server_address[1] and body["backend"] == "failed"


def test_status_shows_proxy_up_backend_failed(ab, monkeypatch, capsys, tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text('export INFER_BASE_URL="http://localhost:8080/v1/"\n')
    monkeypatch.setattr(ab, "ENV_FILE", env_file)
    monkeypatch.setattr(ab, "probe_health", lambda ports: (8080, dict(FAILED)))
    monkeypatch.setattr(ab, "is_systemd_service_active", lambda *a, **k: False)
    monkeypatch.setattr(ab, "list_gpu_info", lambda *a, **k: [])
    monkeypatch.setattr(ab, "list_local_models", lambda *a, **k: [])
    monkeypatch.delenv("LLAMA_MODEL_PATH", raising=False)
    ab.cmd_status()
    out = capsys.readouterr().out
    state = [x for x in out.splitlines() if x.strip().startswith("State:")][0]
    health = [x for x in out.splitlines() if x.strip().startswith("Health:")][0]
    assert "PROXY UP" in state and "backend failed" in state and "will not fit" in state
    assert "RUNNING" not in state and "HTTP 200 (OK)" not in health
