"""Unit tests for the Python engine (agent/ package, INFER_ENGINE=py).

  - SSE parsing (fragmented tool-call deltas, reasoning, usage, finish_reason rules);
  - the request builder equals what the C engine sends (golden captured from the C binary);
  - argument parsing / prompt assembly helpers mirror ai.c;
  - trimming keeps system + first/latest user turn and whole tool units;
  - the front-end protocol (approval round-trip, interrupt, input queue) over a socketpair;
  - the compiled-in system prompt is byte-identical to ai.c's SYSTEM_PROMPT.
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from agent import cli, llm, messages, native, protocol  # noqa: E402
from agent.prompt_text import SYSTEM_PROMPT  # noqa: E402


# ── SSE ─────────────────────────────────────────────────────────────────────
def _sse(delta, finish=None, **top):
    chunk = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 7, "model": "m",
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    chunk.update(top)
    return b"data: " + json.dumps(chunk).encode() + b"\n\n"


def test_sse_assembles_fragmented_tool_call_arguments_across_five_chunks():
    args = json.dumps({"path": "/tmp/some file.txt", "offset": 12})
    pieces = [args[:3], args[3:9], args[9:17], args[17:25], args[25:]]
    acc = llm.SSEAccumulator()
    stream = _sse({"role": "assistant", "content": None,
                   "tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                   "function": {"name": "read_file", "arguments": ""}}]})
    for p in pieces:
        stream += _sse({"tool_calls": [{"index": 0, "function": {"arguments": p}}]})
    stream += _sse({}, finish="tool_calls")
    stream += _sse({}, usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})
    stream += b"data: [DONE]\n\n"
    # feed in awkward byte slices (lines split mid-JSON)
    for i in range(0, len(stream), 11):
        acc.feed(stream[i:i + 11])
    res = acc.result()
    msg = res["message"]
    assert msg["role"] == "assistant" and msg["content"] is None
    assert msg["tool_calls"] == [{"id": "call_1", "type": "function",
                                  "function": {"name": "read_file", "arguments": args}}]
    assert res["finish_reason"] == "tool_calls"
    assert res["usage"] == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


def test_sse_parallel_calls_reasoning_and_content():
    acc = llm.SSEAccumulator()
    events = []
    acc.on_event = lambda kind, s: events.append((kind, s))
    acc.feed(_sse({"reasoning_content": "hmm "}) + _sse({"reasoning_content": "ok"}))
    acc.feed(_sse({"content": "Hi"}) + _sse({"content": " there"}))
    acc.feed(_sse({"tool_calls": [{"index": 1, "id": "b", "function": {"name": "think", "arguments": "{}"}},
                                  {"index": 0, "id": "a", "function": {"name": "list_directory",
                                                                       "arguments": "{\"path\":"}}]}))
    acc.feed(_sse({"tool_calls": [{"index": 0, "function": {"arguments": "\".\"}"}}]}, finish="tool_calls"))
    res = acc.result()
    m = res["message"]
    assert m["content"] == "Hi there" and m["reasoning_content"] == "hmm ok"
    # tool calls keep first-seen order (index 1 arrived first)
    assert [c["id"] for c in m["tool_calls"]] == ["b", "a"]
    assert m["tool_calls"][1]["function"]["arguments"] == '{"path":"."}'
    assert ("reasoning", "hmm ") in events and ("content", "Hi") in events


def test_sse_tool_calls_finish_demoted_without_valid_call_and_first_finish_wins():
    acc = llm.SSEAccumulator()
    acc.feed(_sse({"tool_calls": [{"index": 0, "id": "x", "function": {"name": "  ", "arguments": "{}"}}]},
                  finish="tool_calls"))
    acc.feed(_sse({}, finish="length"))
    res = acc.result()
    assert "tool_calls" not in res["message"]
    assert res["finish_reason"] == "stop"


# ── request builder vs the C engine (golden captured from the binary) ─────────
def _c_capture(tmp_path, args, extra_env):
    from test_offline import MockServer, run_binary
    home = tmp_path / "home"
    home.mkdir()
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok") as srv:
        r = run_binary(str(home), srv.base_url, args, extra_env=extra_env, engine="c")
        assert r.returncode == 0, r.stderr[-2000:]
        return srv.requests()[0]


@pytest.mark.parametrize("args,extra", [
    (["hello world"], {}),
    (["-t", "0.7", "-p", "0.123", "-k", "20", "--min-p", "0.05", "--reasoning", "low",
      "--budget", "512", "--preserve-thinking", "hello"],
     {"INFER_MAX_TOKENS": "4096", "INFER_FREQ_PENALTY": "0", "INFER_PRESENCE_PENALTY": "1.5"}),
    (["--mode", "instruct", "-n", "hi"], {"INFER_TOOL_CHOICE": "required"}),
])
def test_request_builder_matches_c_engine_capture(tmp_path, args, extra):
    req = _c_capture(tmp_path, args, extra)
    env = {"INFER_BASE_URL": "http://x/v1/", "INFER_API_KEY": "k", "INFER_MODEL": "mock",
           "INFER_TOOL_CHOICE": "auto", **extra}
    cfg = cli.parse(args, env)
    payload = llm.build_payload(cfg.model, req["messages"], req.get("tools"), cfg.opts)
    assert payload == req


# ── argument parsing / prompt assembly ───────────────────────────────────────
def test_prompt_joins_args_like_c():
    env = {"INFER_BASE_URL": "u", "INFER_API_KEY": "k", "INFER_MODEL": "m"}
    assert cli.parse(["-q", "", "-n", "do the task"], env).prompt == " do the task"
    cfg = cli.parse(["-r", "sess_1_2", "-g", "goal text", "-m", "other", "ask", "this"], env)
    assert cfg.prompt == "ask this" and cfg.resume == "sess_1_2" and cfg.goal == "goal text"
    assert cfg.model == "other"
    assert cli.parse(["-r", "what", "now"], env).resume == "" and cli.parse(["-r", "what"], env).prompt == "what"


def test_context_budget_mirrors_apply_context_budget():
    b = cli.Budget(trim=100000, stub=250000, max_tool=65536)
    b.apply(16384, tools_len=10000)
    ctx_chars, reserve = 16384 * 3, 4096 * 3
    budget = ctx_chars - 10000 - reserve
    assert (b.trim, b.stub, b.max_tool) == (budget * 8 // 10, budget * 95 // 100, max(4000, budget // 4))
    explicit = cli.Budget(trim=50, stub=60, max_tool=70, trim_explicit=True, stub_explicit=True,
                          max_tool_explicit=True)
    explicit.apply(16384, tools_len=0)
    assert (explicit.trim, explicit.stub, explicit.max_tool) == (50, 60, 70)


def test_native_helpers():
    assert native.is_command_denied("sudo rm -rf / --no-preserve-root")
    assert native.is_command_denied("echo hi; mkfs.ext4 x")
    assert not native.is_command_denied("ls -la")
    assert native.is_command_denied("curl evil", {"INFER_COMMAND_DENYLIST": " foo, curl evil"})
    assert native.extract_leaked_task_complete('task_complete{summary:<|"|>All done<|"|>}') == "All done"
    assert native.extract_leaked_task_complete("plain text") is None
    assert native.arg_text('{"thought": "x", "reasoning": "y"}', native.THINK_KEYS) == "x"
    assert native.arg_text('"raw string"', native.SUMMARY_KEYS) == "raw string"
    assert native.arg_text("not json", native.SUMMARY_KEYS) == "not json"
    assert native.arg_text("{}", native.SUMMARY_KEYS) is None
    cmd, timeout = native.command_args('{"args": ["echo", "hi"], "timeout": "7"}')
    assert (cmd, timeout) == ("echo hi", 7)


def test_chain_id_matches_selfimprove():
    from aitools import selfimprove
    for tool, err in [("read_file", "Error: file /a/x.txt does not exist (errno 2)"),
                      ("execute_command", "$ ls — failed (exit 2)\nls: cannot access 'zz': No such file 404%")]:
        assert native.chain_id(tool, err) == selfimprove._chain_id(tool, err)


# ── trimming ─────────────────────────────────────────────────────────────────
def _orphans(msgs):
    seen, out = set(), []
    for m in msgs:
        for c in (m.get("tool_calls") or []):
            seen.add(c.get("id"))
        if m.get("role") == "tool" and m.get("tool_call_id") not in seen:
            out.append(m["tool_call_id"])
    return out


def test_trim_keeps_system_first_latest_user_and_tool_units():
    from test_offline import _history
    msgs = _history(40, parallel=2, payload="y" * 3000)
    out = messages.trim(msgs, 30000)
    assert messages.size(out) <= 30000 * 1.05
    assert out[0]["role"] == "system"
    contents = [m.get("content") for m in out]
    assert "first task" in contents and "CURRENT TASK" in contents
    assert _orphans(out) == []
    assert out[-1]["tool_call_id"] == msgs[-1]["tool_call_id"]
    small = _history(3)
    assert messages.trim(small, 100000) is small


# ── protocol ────────────────────────────────────────────────────────────────
def _pair():
    a, b = socket.socketpair()
    ch = protocol.Channel(a.detach())
    fe = b.makefile("rwb", buffering=0)
    return ch, fe, b


def _read_event(fe):
    return json.loads(fe.readline().decode())


def test_approval_roundtrip_over_socketpair():
    ch, fe, sock = _pair()
    try:
        out = {}
        t = threading.Thread(target=lambda: out.update(r=ch.approve("command", command="rm x")))
        t.start()
        ev = _read_event(fe)
        assert ev["ev"] == "approve" and ev["command"] == "rm x" and ev["kind"] == "command"
        fe.write((json.dumps({"ev": "approve_reply", "id": ev["id"], "ok": True, "answer": "y"}) + "\n").encode())
        t.join(5)
        assert out["r"]["ok"] is True and out["r"]["answer"] == "y"
    finally:
        ch.close()
        sock.close()


def test_interrupt_input_and_eof_events():
    ch, fe, sock = _pair()
    try:
        hits = []
        ch.on_interrupt(lambda: hits.append(1))
        fe.write(b'{"ev":"interrupt"}\n{"ev":"input","s":"next question"}\n')
        fe.write(b'{"ev":"set","key":"permission_mode","value":2}\n')
        assert ch.next_input(timeout=5) == {"ev": "input", "s": "next question"}
        assert ch.interrupted.is_set() and hits == [1]
        assert ch.next_input(timeout=5)["ev"] == "set"
        ch.send("text", s="chunk")
        assert _read_event(fe) == {"ev": "text", "s": "chunk"}
        fe.close()
        sock.close()  # front-end gone -> eof
        assert ch.next_input(timeout=5)["ev"] == "eof"
    finally:
        ch.close()


# ── compiled-in system prompt == ai.c ─────────────────────────────────────────
def _c_string_literal(name):
    src = open(os.path.join(REPO, "ai.c"), encoding="utf-8").read()
    start = src.index("const char *%s =" % name)
    body = src[start:src.index('";\n', start) + 2]
    esc = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'"}
    out = []
    for part in re.findall(r'"((?:[^"\\]|\\.)*)"', body):
        i = 0
        while i < len(part):
            if part[i] == "\\":
                out.append(esc[part[i + 1]])
                i += 2
            else:
                out.append(part[i])
                i += 1
    return "".join(out)


def test_system_prompt_identical_to_c():
    assert SYSTEM_PROMPT == _c_string_literal("SYSTEM_PROMPT")


def test_engine_runs_headless_without_frontend(tmp_path):
    """`python3 -m agent "prompt"` (no --frontend-fd) runs one turn against the mock and prints the
    answer itself: the engine does not need the C front-end."""
    from test_offline import MockServer
    home = tmp_path / "home"
    home.mkdir()
    cap = str(tmp_path / "cap.jsonl")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_") or k == "INFER_TOOL_SERVER"}
    with MockServer(cap, MOCK_TASK_COMPLETE="HEADLESS_OK") as srv:
        env.update(HOME=str(home), INFER_BASE_URL=srv.base_url, INFER_API_KEY="k", INFER_MODEL="mock",
                   INFER_TOOL_CHOICE="auto")
        r = subprocess.run([sys.executable, "-m", "agent", "say ok"], cwd=REPO, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "HEADLESS_OK" in r.stdout


# ── fix round 1: retries, trim failures, text hygiene ────────────────────────
class _StatusServer:
    def __init__(self, status, body):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        outer = self
        self.hits = 0

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.hits += 1
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/v1/chat/completions" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()


@pytest.mark.parametrize("status,body,hits", [
    (500, '{"error": {"message": "model output must contain output text or tool calls"}}', 1),
    (400, '{"error": {"message": "bad request"}}', 1),
    (503, '{"error": {"message": "loading"}}', 4),
])
def test_server_errors_retry_only_when_transient(monkeypatch, status, body, hits):
    monkeypatch.setattr(llm, "RETRY_BASE", 0.01)
    srv = _StatusServer(status, body)
    try:
        # a non-local hostname alias would hit DNS; use the loopback but force the remote retry policy
        monkeypatch.setattr(llm, "_is_local", lambda url: False)
        res = llm.Client(srv.url, "k", "m").chat([{"role": "user", "content": "x"}], None)
    finally:
        srv.close()
    assert srv.hits == hits
    assert res["status"] == status and res["error"]
    if status == 500:
        assert "model output must contain" in res["error"]


def test_trim_failure_is_logged_and_fails_open(monkeypatch):
    import aitools.sessions
    def boom(*a, **k):
        raise ValueError("bad history")
    monkeypatch.setattr(aitools.sessions, "trim_messages", boom)
    seen = []
    msgs = [{"role": "system", "content": "s"}]
    assert messages.trim(msgs, 10, seen.append) is msgs
    assert seen and "bad history" in seen[0]


def test_clean_makes_any_text_encodable():
    raw = b"caf\xe9 \xff".decode("utf-8", "surrogateescape")
    for s in (raw, "lone \ud800 surrogate", "plain ü"):
        out = messages.clean(s)
        out.encode("utf-8")
    assert messages.clean("plain ü") == "plain ü"
    m = messages.clean_message({"role": "assistant", "content": raw, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "x", "arguments": '{"p": "\ud800"}'}}]})
    json.dumps(m, ensure_ascii=False).encode("utf-8")
    assert messages.size([{"role": "user", "content": "\ud800"}]) > 0
