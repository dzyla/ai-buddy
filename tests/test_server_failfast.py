"""Both engines fail fast, with one clear line and a non-zero exit, when the model server is down.

Incident: llama-server failed its VRAM preflight while the proxy kept the request open forever and
`ai` hung without a word. Now the proxy answers 503 {type: backend_unavailable, message: reason};
the engines must not retry that, must print the reason plus the fix (`ai-backend status`) and
exit non-zero. Connection refused (nothing listening) gives up after INFER_SERVER_WAIT seconds
(default 30: a local server that is still starting gets that long), and a server that accepts the
request but sends no first byte within INFER_FIRST_BYTE_TIMEOUT (default 300) is abandoned.
"""
import json
import os
import socket
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from agent import llm  # noqa: E402
from test_offline import MockServer, _sandbox_run, build_binary  # noqa: E402,F401

UNAVAILABLE = {"status": 503, "body": {"error": {
    "type": "backend_unavailable", "code": 503,
    "message": "llama-server is unavailable: ERROR: model will not fit in VRAM. | short by: ~6.6 GiB. "
               "Check `ai-backend status` and `journalctl --user -u llama-server.service -n 50`.",
    "log": ["[llama-wrapper] ERROR: model will not fit in VRAM."]}}}


def _chat_requests(reqs):
    return [r for r in reqs if "messages" in r]


def test_backend_unavailable_503_is_reported_once_and_exits_nonzero(tmp_path, engine):
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_HTTP_ERROR=json.dumps(UNAVAILABLE)) as srv:
        t0 = time.monotonic()
        r = _sandbox_run(tmp_path, srv.base_url, ["say hi"], {"INFER_ENGINE": engine}, timeout=120)
        elapsed = time.monotonic() - t0
        reqs = srv.requests()
    assert r.returncode != 0, r.stderr[-2000:]
    assert elapsed < 30, elapsed
    assert len(_chat_requests(reqs)) == 1, "a backend_unavailable 503 must not be retried"
    lines = [x for x in r.stderr.splitlines() if "will not fit" in x]
    assert lines, r.stderr[-2000:]
    assert "ai-backend status" in lines[0]


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_connection_refused_gives_up_after_the_server_wait(tmp_path, engine):
    url = "http://127.0.0.1:%d/v1/" % _closed_port()
    t0 = time.monotonic()
    r = _sandbox_run(tmp_path, url, ["say hi"], {"INFER_ENGINE": engine, "INFER_SERVER_WAIT": "1"}, timeout=120)
    elapsed = time.monotonic() - t0
    assert r.returncode != 0, r.stderr[-2000:]
    assert elapsed < 20, elapsed
    line = [x for x in r.stderr.splitlines() if "cannot reach the model server" in x]
    assert line, r.stderr[-2000:]
    assert "ai-backend status" in line[0] and url in line[0]


def test_no_first_byte_within_the_timeout_aborts_the_request(tmp_path, engine):
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_STALL="60", MOCK_TASK_COMPLETE="done") as srv:
        t0 = time.monotonic()
        r = _sandbox_run(tmp_path, srv.base_url, ["say hi"],
                         {"INFER_ENGINE": engine, "INFER_FIRST_BYTE_TIMEOUT": "2", "INFER_PREFILL_TPS": "1000000"},
                         timeout=120)
        elapsed = time.monotonic() - t0
        reqs = srv.requests()
    assert r.returncode != 0, r.stderr[-2000:]
    assert elapsed < 30, elapsed
    assert len(_chat_requests(reqs)) == 1, "a first-byte timeout must not be retried"
    line = [x for x in r.stderr.splitlines() if "INFER_IDLE_TIMEOUT" in x]
    assert line and "ai-backend status" in line[0], r.stderr[-2000:]


def test_slow_first_byte_inside_the_timeout_still_works(tmp_path, engine):
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_STALL="1.5", MOCK_TASK_COMPLETE="all done") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["say hi"],
                         {"INFER_ENGINE": engine, "INFER_FIRST_BYTE_TIMEOUT": "10"}, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "all done" in r.stdout


def test_generic_503_is_still_retried_and_reported(monkeypatch):
    """Overload 503s (no backend_unavailable type) keep the 3 retries; the result names the fix."""
    from test_agent_unit import _StatusServer
    monkeypatch.setattr(llm, "RETRY_BASE", 0.01)
    monkeypatch.setattr(llm, "_is_local", lambda url: False)
    srv = _StatusServer(503, '{"error": {"message": "loading"}}')
    try:
        res = llm.Client(srv.url, "k", "m").chat([{"role": "user", "content": "x"}], None)
    finally:
        srv.close()
    assert srv.hits == 4 and res["status"] == 503
    assert "ai-backend status" in llm.failure_line(res, srv.url)


def test_failure_line_shapes():
    url = "http://127.0.0.1:8080/v1/chat/completions"
    assert "cannot reach the model server" in llm.failure_line(
        {"status": 0, "error": "Connection refused", "connect_failed": True}, url)
    assert "INFER_IDLE_TIMEOUT" in llm.failure_line(
        {"status": 0, "error": "timed out", "first_byte_timeout": 300}, url)
    line = llm.failure_line({"status": 503, "error": "llama-server is unavailable: boom.",
                             "unavailable": True}, url)
    assert "boom" in line and "ai-backend status" in line and "\n" not in line


def test_debug_dir_keeps_the_last_request_and_raw_response(tmp_path, engine):
    """INFER_DEBUG_DIR (used by evals/run_evals.py to diagnose stalls): the last chat payload and
    the raw streamed reply, written as it arrives."""
    cap = str(tmp_path / "cap.jsonl")
    d = tmp_path / "debug"
    with MockServer(cap, MOCK_TASK_COMPLETE="debug ok") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["say hi"], {"INFER_ENGINE": engine, "INFER_DEBUG_DIR": str(d)})
    assert r.returncode == 0, r.stderr[-2000:]
    req = json.loads((d / "last_request.json").read_text())
    assert req["messages"] and req["stream"] is True
    raw = (d / "last_response.raw").read_text()
    assert "data: " in raw and "debug ok" in raw


# ── long prompt processing: llama.cpp sends nothing until the first token unless asked ──────
PREFILL = json.dumps({"seconds": 5, "interval": 0.5})


def test_progress_chunks_keep_a_long_prefill_alive(tmp_path, engine):
    """return_progress makes llama.cpp stream prompt_progress chunks during prefill: a 5 s prefill
    passes a 2 s idle timeout, and the chunks are ignored by the SSE parser."""
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_PREFILL=PREFILL, MOCK_TASK_COMPLETE="prefill ok") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["say hi"],
                         {"INFER_ENGINE": engine, "INFER_IDLE_TIMEOUT": "2", "INFER_PREFILL_TPS": "1000000"},
                         timeout=120)
        reqs = _chat_requests(srv.requests())
    assert r.returncode == 0, r.stderr[-2000:]
    assert "prefill ok" in r.stdout
    assert reqs and all(q.get("return_progress") is True for q in reqs)


def test_silent_prefill_gets_the_prompt_size_allowance(tmp_path, engine):
    """A server that ignores return_progress: the first byte may take max(idle, ~tokens/TPS)."""
    cap = str(tmp_path / "cap.jsonl")
    env = {"INFER_ENGINE": engine, "INFER_IDLE_TIMEOUT": "1", "INFER_RETURN_PROGRESS": "0"}
    with MockServer(cap, MOCK_PREFILL=PREFILL, MOCK_TASK_COMPLETE="slow ok") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["say hi"], dict(env, INFER_PREFILL_TPS="1"), timeout=120)
        reqs = _chat_requests(srv.requests())
    assert r.returncode == 0, r.stderr[-2000:]
    assert "slow ok" in r.stdout
    assert reqs and not any("return_progress" in q for q in reqs)
    # without the allowance (huge assumed prefill speed) the same silent prefill times out
    with MockServer(str(tmp_path / "cap2.jsonl"), MOCK_PREFILL=PREFILL) as srv:
        t0 = time.monotonic()
        r = _sandbox_run(tmp_path, srv.base_url, ["say hi"], dict(env, INFER_PREFILL_TPS="1000000"), timeout=120)
        elapsed = time.monotonic() - t0
    assert r.returncode != 0 and "INFER_IDLE_TIMEOUT" in r.stderr, r.stderr[-2000:]
    assert elapsed < 5 + 20


def test_return_progress_only_for_local_servers_by_default(monkeypatch):
    monkeypatch.delenv("INFER_RETURN_PROGRESS", raising=False)
    assert llm.return_progress_enabled("http://127.0.0.1:8080/v1/chat/completions")
    assert not llm.return_progress_enabled("https://api.example.com/v1/chat/completions")
    monkeypatch.setenv("INFER_RETURN_PROGRESS", "1")
    assert llm.return_progress_enabled("https://api.example.com/v1/chat/completions")
    monkeypatch.setenv("INFER_RETURN_PROGRESS", "0")
    assert not llm.return_progress_enabled("http://localhost:8080/v1/chat/completions")


def test_first_byte_allowance_scales_with_the_prompt(monkeypatch):
    monkeypatch.delenv("INFER_PREFILL_TPS", raising=False)
    assert llm.first_byte_allowance(4000, idle=300) == 300
    assert llm.first_byte_allowance(128000 * 4, idle=300) >= 420      # a cold 128K prefill
    assert llm.first_byte_allowance(128000 * 4, idle=0) == 0          # 0 = no limit
    monkeypatch.setenv("INFER_IDLE_TIMEOUT", "50")
    monkeypatch.setenv("INFER_FIRST_BYTE_TIMEOUT", "7")
    assert llm.idle_timeout() == 50                                    # the new name wins
    monkeypatch.delenv("INFER_IDLE_TIMEOUT")
    assert llm.idle_timeout() == 7                                     # old name still works
