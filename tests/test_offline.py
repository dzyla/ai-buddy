"""Offline test suite for the `ai` CLI — runs with no live LLM backend.

Two kinds of tests:
  1. Pure-Python unit tests of ai_mcp.py logic (fetch routing, session transcript).
  2. End-to-end tests that drive the compiled `ai` binary against a local mock
     OpenAI server (tests/mock_llm_server.py), asserting on the exact request the
     binary sent — the fragile request-building / streaming path that CLAUDE.md
     flags as the main source of bugs.

Run: pytest tests/test_offline.py -v
"""
import json
import os
import socket
import subprocess
import sys
import time
import importlib

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = os.path.join(REPO, "tests", "mock_llm_server.py")
AI_BIN = os.path.join(REPO, "ai")
sys.path.insert(0, REPO)
ai_mcp = importlib.import_module("ai_mcp")


# ── build fixture ─────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def build_binary():
    # Build only if the binary is missing or older than its sources, so that when
    # multiple test modules share this session we don't issue concurrent `make`
    # calls (racing on the output binary) just because they each auto-build.
    def _stale():
        binp = os.path.join(REPO, "ai")
        if not os.path.exists(binp):
            return True
        bin_mtime = os.path.getmtime(binp)
        for src in ("ai.c", "ai_session.c", "ai_git.c", "ai_terminal.c", "Makefile"):
            p = os.path.join(REPO, src)
            try:
                if os.path.getmtime(p) > bin_mtime:
                    return True
            except OSError:
                pass
        return False

    if _stale():
        res = subprocess.run(
            ["make"],
            cwd=REPO, capture_output=True, text=True,
        )
        assert res.returncode == 0, f"Build failed:\n{res.stderr}"
    yield


# ── mock server helper ────────────────────────────────────────────────────────
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockServer:
    def __init__(self, capture_path, **env):
        self.port = _free_port()
        self.capture = capture_path
        self.env = env
        self.proc = None

    def __enter__(self):
        e = os.environ.copy()
        e["MOCK_CAPTURE"] = self.capture
        e.update({k: v for k, v in self.env.items() if v is not None})
        self.proc = subprocess.Popen(
            [sys.executable, MOCK, str(self.port)], env=e,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait generously (up to ~20s) so the port is definitely listening before
        # the binary runs; under heavy load a slow interpreter spawn used to make
        # the binary's first connection retry and occasionally blow the timeout.
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        return self

    def __exit__(self, *a):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1/"

    def requests(self):
        if not os.path.exists(self.capture):
            return []
        with open(self.capture) as f:
            return [json.loads(line) for line in f if line.strip()]


def run_binary(home, base_url, args, extra_env=None, timeout=90):
    env = os.environ.copy()
    # Scrub any INFER_*/CUDA vars the developer may have sourced from
    # ~/.local/share/ai/env, so they can't override the mock URL below and hang
    # offline tests. Then apply the variables we explicitly control.
    for k in list(env):
        if k.startswith("INFER_") or k in ("CUDA_VISIBLE_DEVICES", "CUDA_PATH",
                                           "LD_LIBRARY_PATH", "LLAMA_CTX_SIZE",
                                           "LLAMA_N_GPU_LAYERS", "LLAMA_MODEL_PATH"):
            env.pop(k, None)
    # Isolate: a fresh HOME means load_env_file() finds no config to override
    # our INFER_* vars, so the mock URL actually takes effect.
    env["HOME"] = home
    env["INFER_BASE_URL"] = base_url
    env["INFER_API_KEY"] = "test"
    env["INFER_MODEL"] = "mock"
    env["INFER_TOOL_CHOICE"] = "auto"
    if extra_env:
        env.update(extra_env)
    import os as _os
    _leak = {k: v for k, v in env.items() if k in ("INFER_BASE_URL","INFER_MODEL","INFER_CONTEXT_WINDOW")}
    open("/tmp/run_binary_env.txt","a").write(f"port_leak_base={env.get("INFER_BASE_URL")} ctx={env.get("INFER_CONTEXT_WINDOW")} leak={_leak}\n")
    return subprocess.run([AI_BIN] + args, cwd=REPO, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout)


# ── pure-Python: fetch routing ────────────────────────────────────────────────
def test_fetch_webpage_routes_to_smart(monkeypatch):
    """Default fetch_webpage must delegate to the robust fetch_smart cascade."""
    monkeypatch.delenv("INFER_FETCH_BASIC", raising=False)
    monkeypatch.setattr(ai_mcp, "fetch_smart", lambda url: f"SMART:{url}")
    monkeypatch.setattr(ai_mcp, "fetch_webpage_basic", lambda url: f"BASIC:{url}")
    assert ai_mcp.fetch_webpage("http://x") == "SMART:http://x"


def test_fetch_webpage_basic_override(monkeypatch):
    """INFER_FETCH_BASIC=1 forces the plain path (escape hatch)."""
    monkeypatch.setenv("INFER_FETCH_BASIC", "1")
    monkeypatch.setattr(ai_mcp, "fetch_smart", lambda url: f"SMART:{url}")
    monkeypatch.setattr(ai_mcp, "fetch_webpage_basic", lambda url: f"BASIC:{url}")
    assert ai_mcp.fetch_webpage("http://x") == "BASIC:http://x"


def test_fetch_smart_fallbacks_do_not_recurse():
    """fetch_smart's fallback rungs must call fetch_webpage_basic, never
    fetch_webpage — otherwise a curl_cffi-less box infinite-loops."""
    import inspect
    src = inspect.getsource(ai_mcp.fetch_smart)
    # The final urllib fallback and the ImportError branch call the *basic* fn.
    assert "fetch_webpage_basic" in src
    # It must not call the public fetch_webpage (which routes back to fetch_smart).
    assert "return fetch_webpage(" not in src


# ── pure-Python: session transcript ───────────────────────────────────────────
def _transcript(messages, tmp_path):
    f = tmp_path / "session.json"
    f.write_text(json.dumps(messages))
    res = subprocess.run(
        [sys.executable, "ai_mcp.py", "session-transcript", str(f)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    return [json.loads(l) for l in res.stdout.splitlines() if l.strip()]


def test_transcript_extracts_task_complete_summary(tmp_path):
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hello there"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "task_complete",
                "arguments": json.dumps({"summary": "the answer is 42"})}}]},
    ]
    out = _transcript(messages, tmp_path)
    assert out == [
        {"role": "user", "content": "hello there"},
        {"role": "assistant", "content": "the answer is 42"},
    ]


def test_transcript_drops_system_tool_and_nudges(tmp_path):
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "real question"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file contents"},
        {"role": "user", "content": "Please call task_complete with your final answer."},
        {"role": "assistant", "content": "done"},
    ]
    out = _transcript(messages, tmp_path)
    roles = [m["role"] for m in out]
    contents = [m["content"] for m in out]
    assert "SYS" not in contents           # system dropped
    assert "file contents" not in contents  # tool result dropped
    assert not any("task_complete" in c for c in contents)  # nudge dropped
    assert out[0] == {"role": "user", "content": "real question"}
    assert out[-1] == {"role": "assistant", "content": "done"}


def test_transcript_handles_multimodal_user(tmp_path):
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]},
        {"role": "assistant", "content": "a cat"},
    ]
    out = _transcript(messages, tmp_path)
    assert out[0] == {"role": "user", "content": "describe this"}
    assert out[1] == {"role": "assistant", "content": "a cat"}


def test_transcript_empty_file(tmp_path):
    f = tmp_path / "missing.json"
    res = subprocess.run(
        [sys.executable, "ai_mcp.py", "session-transcript", str(f)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == ""


# ── binary integration: system prompt externalization ─────────────────────────
def test_custom_system_prompt(tmp_path):
    home = str(tmp_path / "home")
    os.makedirs(os.path.join(home, ".config", "ai"))
    with open(os.path.join(home, ".config", "ai", "system_prompt.md"), "w") as f:
        f.write("You are a test agent. Token: ZEBRA-PROMPT-42.")
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok") as srv:
        run_binary(home, srv.base_url, ["do a thing"])
        req = srv.requests()[0]
    sysmsg = next(m["content"] for m in req["messages"] if m["role"] == "system")
    assert "ZEBRA-PROMPT-42" in sysmsg
    assert "fully autonomous CLI agent" not in sysmsg


def test_default_system_prompt_without_file(tmp_path):
    home = str(tmp_path / "home")
    os.makedirs(home)
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok") as srv:
        run_binary(home, srv.base_url, ["do a thing"])
        req = srv.requests()[0]
    sysmsg = next(m["content"] for m in req["messages"] if m["role"] == "system")
    assert "fully autonomous CLI agent" in sysmsg


# ── binary integration: session resume ────────────────────────────────────────
def test_session_resume_roundtrip(tmp_path):
    home = str(tmp_path / "home")
    os.makedirs(home)
    cap = str(tmp_path / "cap.jsonl")

    # Turn 1: establish context; the mock answers via task_complete.
    with MockServer(cap, MOCK_TASK_COMPLETE="Understood, the codeword is PLATYPUS.") as srv:
        run_binary(home, srv.base_url, ["Remember the codeword is PLATYPUS"])
    assert os.path.exists(os.path.join(home, ".cache", "ai", "sessions", "last.json"))

    # Turn 2: resume and ask a follow-up; assert prior turns are in the request.
    cap2 = str(tmp_path / "cap2.jsonl")
    with MockServer(cap2, MOCK_TASK_COMPLETE="It was PLATYPUS.") as srv:
        run_binary(home, srv.base_url, ["-r", "What was the codeword?"])
        req = srv.requests()[0]
    msgs = req["messages"]
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "user"]
    assert "PLATYPUS" in msgs[1]["content"] or "PLATYPUS" in msgs[2]["content"]
    assert msgs[3]["content"] == "What was the codeword?"


def test_no_resume_starts_fresh(tmp_path):
    home = str(tmp_path / "home")
    os.makedirs(home)
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok") as srv:
        run_binary(home, srv.base_url, ["Remember the codeword is PLATYPUS"])
    cap2 = str(tmp_path / "cap2.jsonl")
    with MockServer(cap2, MOCK_TASK_COMPLETE="ok") as srv:
        run_binary(home, srv.base_url, ["What was the codeword?"])  # no -r
        req = srv.requests()[0]
    roles = [m["role"] for m in req["messages"]]
    assert roles == ["system", "user"]  # no prior context leaked in


def test_goal_mission_board(tmp_path):
    """--goal / -g flag must inject MISSION BOARD into system prompt."""
    home = str(tmp_path / "home")
    os.makedirs(home)
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok") as srv:
        run_binary(home, srv.base_url, ["-g", "Refactor agent loop", "do work"])
        req = srv.requests()[0]
    sysmsg = next(m["content"] for m in req["messages"] if m["role"] == "system")
    assert "MISSION BOARD:" in sysmsg
    assert "Top Goal: Refactor agent loop" in sysmsg


def test_metrics_subcommand():
    """ai_mcp.py show-metrics must run cleanly without error."""
    res = subprocess.run(
        [sys.executable, "ai_mcp.py", "show-metrics"],
        cwd=REPO, capture_output=True, text=True
    )
    assert res.returncode == 0


def test_ai_mcp_directly_runnable():
    """ai_mcp.py must be directly executable via its shebang (./ai_mcp.py)."""
    mcp = os.path.join(REPO, "ai_mcp.py")
    assert os.access(mcp, os.X_OK), "ai_mcp.py must have execute permission"
    res = subprocess.run(
        ["./ai_mcp.py", "show-metrics"],
        cwd=REPO, capture_output=True, text=True
    )
    assert res.returncode == 0


def test_invalid_arg_type():
    """Passing invalid argument type to read_file must return structured error."""
    res = subprocess.run(
        [sys.executable, "ai_mcp.py", "call-tool", "read_file", "read_file", '{"path": 12345}'],
        cwd=REPO, capture_output=True, text=True
    )
    assert res.returncode == 0
    assert "Invalid argument type for 'path'" in res.stdout


def test_command_denylist(tmp_path):
    """Dangerous commands matching denylist must be intercepted in C."""
    home = str(tmp_path / "home")
    os.makedirs(home)
    cap = str(tmp_path / "cap.jsonl")
    # Mock LLM calls execute_command with 'rm -rf /'
    tool_call = {
        "id": "c1", "type": "function",
        "function": {"name": "execute_command", "arguments": json.dumps({"command": "rm -rf /"})}
    }
    with MockServer(cap, MOCK_TOOL_CALL=json.dumps(tool_call)) as srv:
        run_binary(home, srv.base_url, ["-y", "do destructive thing"])
        reqs = srv.requests()
        assert len(reqs) >= 2
        tool_msg = next(m["content"] for m in reqs[1]["messages"] if m.get("role") == "tool")
        assert "INFER_COMMAND_DENYLIST" in tool_msg


def test_hide_details_flag(tmp_path):
    """Setting INFER_HIDE_DETAILS=1 suppresses thinking and tool box headers in output."""
    home = str(tmp_path / "home")
    os.makedirs(home)
    cap = str(tmp_path / "cap.jsonl")
    tool_call = {
        "id": "c1", "type": "function",
        "function": {"name": "think", "arguments": json.dumps({"reasoning": "secret plan"})},
    }
    env = dict(os.environ, INFER_HIDE_DETAILS="1")
    with MockServer(cap, MOCK_TOOL_CALL=json.dumps(tool_call)) as srv:
        res = run_binary(home, srv.base_url, ["plan steps"], extra_env=env)
        assert "secret plan" not in res.stderr


def test_execute_command_argument_aliases():
    """execute_command must handle parameter aliases like cmd, CommandLine, args list, and raw string."""
    # Test 'cmd' alias
    res = subprocess.run(
        [sys.executable, "ai_mcp.py", "call-tool", "execute_command", "execute_command", '{"cmd": "echo hello_cmd"}'],
        cwd=REPO, capture_output=True, text=True
    )
    assert res.returncode == 0
    assert "hello_cmd" in res.stdout

    # Test 'CommandLine' alias
    res = subprocess.run(
        [sys.executable, "ai_mcp.py", "call-tool", "execute_command", "execute_command", '{"CommandLine": "echo hello_cmdline"}'],
        cwd=REPO, capture_output=True, text=True
    )
    assert res.returncode == 0
    assert "hello_cmdline" in res.stdout

    # Test list of args
    res = subprocess.run(
        [sys.executable, "ai_mcp.py", "call-tool", "execute_command", "execute_command", '{"args": ["echo", "hello_args"]}'],
        cwd=REPO, capture_output=True, text=True
    )
    assert res.returncode == 0
    assert "hello_args" in res.stdout

    # Test raw string argument
    res = subprocess.run(
        [sys.executable, "ai_mcp.py", "call-tool", "execute_command", "execute_command", '"echo hello_raw"'],
        cwd=REPO, capture_output=True, text=True
    )
    assert res.returncode == 0
    assert "hello_raw" in res.stdout


def test_execute_command_alias_in_c(tmp_path):
    """C agent binary must accept 'cmd' argument alias in execute_command."""
    home = str(tmp_path / "home")
    os.makedirs(home)
    cap = str(tmp_path / "cap.jsonl")
    tool_call = {
        "id": "c1", "type": "function",
        "function": {"name": "execute_command", "arguments": json.dumps({"cmd": "echo c_alias_ok"})}
    }
    with MockServer(cap, MOCK_TOOL_CALL=json.dumps(tool_call)) as srv:
        run_binary(home, srv.base_url, ["-y", "run echo"])
        reqs = srv.requests()
        assert len(reqs) >= 2
        tool_msg = next(m["content"] for m in reqs[1]["messages"] if m.get("role") == "tool")
        assert "c_alias_ok" in tool_msg



def test_background_process_system_prompt_guidance(tmp_path):
    """Verify that the system prompt explicitly advises using start_background_process."""
    cap = tmp_path / "cap.jsonl"
    with MockServer(str(cap), MOCK_LLM_RESPONSE='{"choices":[{"delta":{"content":"done"}}]}') as s:
        res = run_binary(str(tmp_path), s.base_url, ["hello"])
    assert res.returncode == 0
    reqs = s.requests()
    assert reqs, "Expected at least one request"
    sys_msg = reqs[0]["messages"][0]["content"]
    assert "start_background_process" in sys_msg
    assert "execute_command" in sys_msg
    assert "blocks the main thread" in sys_msg
