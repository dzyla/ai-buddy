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
        for src in ("ai.c", "ai_session.c", "ai_git.c", "ai_terminal.c", "ai_toolclient.c", "Makefile"):
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
    with MockServer(cap, MOCK_TOOL_CALL=json.dumps(tool_call)) as srv:
        # NOTE: pass ONLY the flag as extra_env. run_binary() scrubs INFER_*
        # and injects the mock URL; passing the whole os.environ here would
        # re-inject the developer's real INFER_BASE_URL/MODEL over the mock and
        # silently hit the live server (hangs only when it's slow).
        res = run_binary(home, srv.base_url, ["plan steps"], extra_env={"INFER_HIDE_DETAILS": "1"})
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


# ═══════════════ Context management & self-learning regressions ═══════════════
# These run the binary from a throwaway cwd (with ai_mcp.py symlinked in) so the
# repo's AGENTS.md / mcp.json are not loaded and auto-commit can't touch the repo.

def _sandbox_run(tmp_path, base_url, args, extra_env=None, timeout=120):
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    for f in ("ai_mcp.py", "deep_research.py"):
        if not (work / f).exists():
            os.symlink(os.path.join(REPO, f), work / f)
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("INFER_") or k in ("LLAMA_CTX_SIZE",):
            env.pop(k, None)
    env.update({"HOME": str(home), "INFER_BASE_URL": base_url, "INFER_API_KEY": "t",
                "INFER_MODEL": "mock", "INFER_TOOL_CHOICE": "auto", "INFER_AUTO_APPROVE": "1"})
    if extra_env:
        env.update(extra_env)
    return subprocess.run([AI_BIN] + args, cwd=str(work), env=env,
                          stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout)


def _orphan_tool_ids(msgs):
    seen, orphans = set(), []
    for m in msgs:
        for c in (m.get("tool_calls") or []):
            seen.add(c.get("id"))
        if m.get("role") == "tool" and m.get("tool_call_id") not in seen:
            orphans.append(m.get("tool_call_id"))
    return orphans


def _trim_cli(messages, tmp_path, max_bytes):
    f = tmp_path / "msgs.json"
    f.write_text(json.dumps(messages))
    r = subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py"), "trim-messages",
                        str(f), str(max_bytes)], capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout)


def _history(n_units, parallel=2, payload="x" * 50):
    msgs = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": "first task"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "CURRENT TASK"}]
    for i in range(n_units):
        calls = [{"id": "c%d_%d" % (i, j), "type": "function",
                  "function": {"name": "read_file", "arguments": "{}"}} for j in range(parallel)]
        msgs.append({"role": "assistant", "content": None, "tool_calls": calls})
        for c in calls:
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": payload})
    return msgs


def test_trim_is_noop_under_budget(tmp_path):
    """Old trim cut to 'first 2 + last 20 messages' on EVERY iteration regardless
    of size; a 3 KB history must come back unchanged."""
    msgs = _history(9)
    assert _trim_cli(msgs, tmp_path, 100000) == msgs


def test_trim_keeps_latest_user_turn_and_never_orphans_tools(tmp_path):
    msgs = _history(40, parallel=2, payload="y" * 3000)
    out = _trim_cli(msgs, tmp_path, 30000)
    assert len(json.dumps(out)) <= 30000 * 1.05
    assert out[0]["role"] == "system"
    contents = [m.get("content") for m in out]
    assert "CURRENT TASK" in contents, "latest user turn was dropped"
    assert "first task" in contents
    assert _orphan_tool_ids(out) == []
    # every kept assistant tool_call still has all of its replies
    replies = {m["tool_call_id"] for m in out if m["role"] == "tool"}
    for m in out:
        for c in (m.get("tool_calls") or []):
            assert c["id"] in replies
    # the newest unit is intact
    assert out[-1]["tool_call_id"] == msgs[-1]["tool_call_id"]


def test_binary_trim_threshold_respected_and_protocol_valid(tmp_path):
    """E2E: with a small INFER_TRIM_THRESHOLD and parallel tool calls producing
    big outputs, every request must keep the system prompt + the user's task,
    contain no orphaned tool messages, and stay bounded."""
    big = tmp_path / "big.txt"
    big.write_text("Z" * 6000)
    seq = [[{"id": "r%d_%d" % (i, j), "type": "function",
             "function": {"name": "read_file",
                          "arguments": json.dumps({"path": str(big), "offset": i * 10 + j})}}
            for j in range(2)] for i in range(10)]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["TASK_MARKER_42 read the file"],
                         {"INFER_TRIM_THRESHOLD": "30000", "INFER_STATE_CONTEXT": "0"})
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert len(reqs) >= 8
    for q in reqs:
        msgs = q["messages"]
        assert msgs[0]["role"] == "system"
        assert any("TASK_MARKER_42" in str(m.get("content")) for m in msgs if m["role"] == "user")
        assert _orphan_tool_ids(msgs) == []
    sys_len = len(json.dumps(reqs[-1]["messages"][0]))
    assert len(json.dumps(reqs[-1]["messages"])) < 30000 + sys_len


def test_resumed_session_keeps_new_prompt_after_many_tool_calls(tmp_path):
    """-r splices old turns in front of the new prompt; the old count-based trim
    then kept the OLDEST user turn and dropped the new task after ~10 calls."""
    home = tmp_path / "home"
    sess = home / ".cache" / "ai" / "sessions"
    sess.mkdir(parents=True)
    old = [{"role": "system", "content": "S"}]
    for i in range(12):
        old += [{"role": "user", "content": "old question %d" % i},
                {"role": "assistant", "content": "old answer %d " % i + "w" * 2000}]
    (sess / "last.json").write_text(json.dumps(old))
    seq = [{"id": "l%d" % i, "type": "function",
            "function": {"name": "list_directory", "arguments": json.dumps({"path": "/tmp", "n": i})}}
           for i in range(14)]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["-r", "NEW_TASK_77"],
                         {"INFER_TRIM_THRESHOLD": "25000"})
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert len(reqs) >= 10
    for q in reqs:
        assert any(m["role"] == "user" and "NEW_TASK_77" in str(m.get("content"))
                   for m in q["messages"]), "current prompt lost from context"
        assert _orphan_tool_ids(q["messages"]) == []


def test_small_ctx_detected_from_props_uses_core_tools(tmp_path):
    """A 16k-ctx server (n_ctx from /props) must not be sent the ~45 KB full
    catalog (≈12k tokens, before any message)."""
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok", MOCK_N_CTX="16384") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["hi"])
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    names = {t["function"]["name"] for t in reqs[0]["tools"]}
    assert names <= set(ai_mcp.CORE_TOOL_PROFILE), names - set(ai_mcp.CORE_TOOL_PROFILE)
    assert {"execute_command", "read_file", "task_complete"} <= names
    assert len(json.dumps(reqs[0]["tools"])) < 15000


def test_large_ctx_keeps_full_catalog(tmp_path):
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok", MOCK_N_CTX="131072") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["hi"])
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert len(reqs[0]["tools"]) > len(ai_mcp.CORE_TOOL_PROFILE) + 10


def test_small_ctx_caps_tool_output(tmp_path):
    """max_tool_output defaults to 64 KB (~20k tokens) — bigger than a 16k model's
    whole window. With a known small window it must be derived from it."""
    big = tmp_path / "huge.txt"
    big.write_text("Q" * 40000)
    seq = [{"id": "h1", "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps({"path": str(big)})}}]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq), MOCK_N_CTX="16384") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["read it"])
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    tool_msg = next(m["content"] for m in reqs[1]["messages"] if m["role"] == "tool")
    assert len(tool_msg) < 16000


# ── save_memory ──
def test_save_memory_appends_dedups_and_evicts():
    # memory lives in the state db ($AI_STATE_DB from conftest); read it back through the API
    from aitools import state

    def mem():
        return "\n".join(state.memory_list())
    ai_mcp.save_memory("User prefers metric units")
    ai_mcp.save_memory("Lab server is gpu-box")
    text = mem()
    assert "metric units" in text and "gpu-box" in text, "second save erased the first"
    r = ai_mcp.save_memory("user prefers metric units")
    assert "no duplicate" in r
    assert mem().lower().count("metric units") == 1
    for i in range(80):
        ai_mcp.save_memory("fact number %03d " % i + "p" * 60)
    text = mem()
    assert len(text) <= ai_mcp.MEMORY_CAP + 1
    assert "fact number 079" in text            # newest kept
    assert "metric units" not in text            # oldest whole lines evicted
    for line in text.splitlines():               # never a mid-line cut
        assert line.startswith("fact number") or line == ""
    ai_mcp.save_memory("only this", replace=True)
    assert mem().strip() == "only this"


# ── self-improvement ──
@pytest.fixture
def si_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("INFER_PRIVATE_MODE", raising=False)
    return tmp_path


def test_second_distinct_fix_for_same_tool_is_learned(si_home):
    """_has_lesson matched 'any FIX block exists' + 'tool: X anywhere', so after
    the first lesson for a tool every later distinct lesson was dropped."""
    ai_mcp.record_failure("read_file", "{}", "Error: file /a/x.txt does not exist")
    ai_mcp.record_recovery("read_file", '{"path":"/b/x.txt"}', "Error: file /a/x.txt does not exist")
    ai_mcp.record_failure("read_file", "{}", "Error: binary file rejected, use pdftotext")
    ai_mcp.record_recovery("read_file", '{"path":"x.txt"}', "Error: binary file rejected, use pdftotext")
    blocks = [b for b in ai_mcp._lesson_blocks() if b.startswith("## FIX")]
    assert len(blocks) == 2, blocks
    # and a repeat of the same lesson is still deduplicated
    ai_mcp.record_recovery("read_file", '{"path":"/c"}', "Error: binary file rejected, use pdftotext")
    assert len([b for b in ai_mcp._lesson_blocks() if b.startswith("## FIX")]) == 2


def test_harness_guard_errors_are_not_learned(si_home):
    err = ("Error: You are stuck in a loop calling the exact same tool with the exact "
           "same arguments. Stop and try a different approach, or call task_complete.")
    for _ in range(3):
        ai_mcp.record_failure("list_directory", '{"path":"."}', err)
    assert ai_mcp.record_recovery("list_directory", '{"path":"."}', err) == ""
    assert ai_mcp._lesson_blocks() == []
    assert ai_mcp._read_ledger() == []


def test_identical_args_recovery_is_not_a_fix(si_home):
    e = "Error: connection reset by peer"
    ai_mcp.record_failure("web_search", '{"q":"a"}', e)
    assert ai_mcp.record_recovery("web_search", '{"q":"a"}', e, failed_args='{"q":"a"}') == ""
    assert not [b for b in ai_mcp._lesson_blocks() if b.startswith("## FIX")]


def test_cli_passes_chain_id_through(si_home):
    env = dict(os.environ, HOME=str(si_home))
    pay = {"tool": "read_file", "args": "{}", "error": "Error: boom 1", "chain_id": "chain_FROM_C"}
    subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py"), "record-failure",
                    json.dumps(pay)], env=env, check=True, timeout=60)
    assert ai_mcp._read_ledger()[-1]["chain_id"] == "chain_FROM_C"


def test_binary_loop_guard_does_not_poison_lessons(tmp_path):
    """E2E repro: identical list_directory calls trip the loop guard; the old
    harness saved PITFALL + a bogus FIX ('stuck in a loop' -> same args)."""
    seq = [{"id": "c%d" % i, "type": "function",
            "function": {"name": "list_directory", "arguments": json.dumps({"path": "/tmp"})}}
           for i in range(8)]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["list"])
    assert r.returncode == 0, r.stderr[-2000:]
    from aitools import state
    assert "stuck in a loop" not in state.lessons_render(status=None, limit=None)


def test_binary_records_failure_with_huge_output(tmp_path):
    """An escaped 20 KB error overflowed the 9 KB payload buffer; the truncated
    JSON made record-failure crash and the failure was never learned."""
    cmd = "python3 -c \"print('E'*20000); raise SystemExit(3)\""
    seq = [{"id": "x1", "type": "function",
            "function": {"name": "execute_command", "arguments": json.dumps({"command": cmd})}}]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["run it"])
    assert r.returncode == 0, r.stderr[-2000:]
    from aitools import state
    assert "execute_command" in {r["tool"] for r in state.ledger_rows()}


def test_task_timeout_forces_exactly_one_more_iteration(tmp_path):
    """The timeout/stall paths set loop_count = 28 ("one more iteration"), which
    only holds for a 30-step limit: non-tty runs (limit 60) got 32 more steps and
    --continue (limit 999999) never stopped."""
    seq = [{"id": "s0", "type": "function", "function": {"name": "execute_command",
            "arguments": json.dumps({"command": "python3 -c 'import time; time.sleep(1.5)'"})}}]
    seq += [{"id": "l%d" % i, "type": "function", "function": {"name": "list_directory",
             "arguments": json.dumps({"path": "/tmp", "i": i})}} for i in range(15)]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["go"], {"INFER_TASK_TIMEOUT": "1"})
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert len(reqs) <= 4, "timeout did not stop the loop (%d requests)" % len(reqs)


def test_agents_md_injection_is_capped(tmp_path):
    """AGENTS.md (up to 100 KB) was injected whole into every request."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "AGENTS.md").write_text("# Project rules\n" + ("rule line " * 9 + "\n") * 400)
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["hi"])
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    sysm = reqs[0]["messages"][0]["content"]
    assert "# Project rules" in sysm
    assert "AGENTS.md truncated" in sysm
    assert sysm.count("rule line") < 1500
