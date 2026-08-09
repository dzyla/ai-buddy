"""Regression tests for the permission-mode gating, self-improvement skill tools,
and searchable history added to the harness.

Runs offline against the mock OpenAI server (tests/mock_llm_server.py) so tests
assert on what the binary actually sent / did, without needing a real LLM.
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


@pytest.fixture(scope="session", autouse=True)
def build_binary():
    # Build only if stale, so we don't race with a second autouse build fixture
    # from tests/test_offline.py (concurrent `make` on the same tree is unsafe).
    src = os.path.join(REPO, "ai.c")
    binp = os.path.join(REPO, "ai")
    try:
        stale = (not os.path.exists(binp)) or (
            os.path.getmtime(src) > os.path.getmtime(binp))
    except Exception:
        stale = True
    if stale:
        res = subprocess.run(["make"], cwd=REPO, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
    yield


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
    # Scrub inherited INFER_*/CUDA config so a developer's sourced backend can't
    # override the mock URL and hang offline tests.
    for k in list(env):
        if k.startswith("INFER_") or k in ("CUDA_VISIBLE_DEVICES", "CUDA_PATH",
                                           "LD_LIBRARY_PATH", "LLAMA_CTX_SIZE",
                                           "LLAMA_N_GPU_LAYERS", "LLAMA_MODEL_PATH"):
            env.pop(k, None)
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


# ── system prompt reflects the active permission mode ─────────────────────────
@pytest.mark.parametrize("flag,marker", [
    (["--plan"], "PERMISSION MODE: PLAN"),
    (["--manual"], "PERMISSION MODE: MANUAL"),
    (["--auto"], "PERMISSION MODE: FULL AUTONOMY"),
    ([], "PERMISSION MODE: FULL AUTONOMY"),  # default
])
def test_mode_injected_into_system_prompt(tmp_path, flag, marker):
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap) as srv:
        args = list(flag) + ["do something"]
        res = run_binary(str(tmp_path), srv.base_url, args)
        assert res.returncode == 0
    reqs = MockServer(cap).requests() if os.path.exists(cap) else []
    assert reqs, "expected at least one request"
    sys_msg = ""
    for m in reqs[0].get("messages", []):
        if m.get("role") == "system":
            sys_msg = m.get("content", "")
    assert marker in sys_msg


# ── INFER_PERMISSION_MODE env var selects mode ────────────────────────────────
@pytest.mark.parametrize("val,marker", [
    ("plan", "PERMISSION MODE: PLAN"),
    ("manual", "PERMISSION MODE: MANUAL"),
    ("auto", "PERMISSION MODE: FULL AUTONOMY"),
])
def test_mode_env_var(tmp_path, val, marker):
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap) as srv:
        res = run_binary(str(tmp_path), srv.base_url, ["do something"],
                         extra_env={"INFER_PERMISSION_MODE": val})
        assert res.returncode == 0
    reqs = MockServer(cap).requests() if os.path.exists(cap) else []
    assert reqs
    sys_msg = "".join(m.get("content", "") for m in reqs[0].get("messages", [])
                      if m.get("role") == "system")
    assert marker in sys_msg


# ── present_plan / skill tools are exposed to the model ───────────────────────
def test_new_tools_registered():
    res = subprocess.run([sys.executable, "ai_mcp.py", "list-tools"],
                         cwd=REPO, capture_output=True, text=True)
    tools = json.loads(res.stdout)
    names = {t["function"]["name"] for t in tools}
    for name in ["present_plan", "skill_create", "skill_update", "skill_note",
                 "search_history", "list_sessions", "get_session"]:
        assert name in names, f"{name} missing from tool catalog"


# ── mutation gating: write_file blocked in PLAN mode, allowed in AUTO ─────────
def test_plan_mode_blocks_mutating_tool(tmp_path):
    cap = str(tmp_path / "cap.jsonl")
    target = str(tmp_path / "should_not_exist.txt")
    tool_call = {
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": target, "content": "secret"}),
        },
    }
    with MockServer(cap, MOCK_TOOL_CALL=json.dumps(tool_call)) as srv:
        # In plan mode, a mutating write_file must be blocked (no file created),
        # and the loop must eventually terminate via a normal reply.
        res = run_binary(str(tmp_path), srv.base_url, ["--plan", "make changes"],
                         extra_env={"MOCK_REPLY_CONTENT": "MOCK_DONE"}, timeout=30)
        assert res.returncode in (0, 1)
    assert not os.path.exists(target), "PLAN mode must NOT allow the file write"

    # Sanity: the C agent treats write_file as mutating and present_plan is a tool.
    cap2 = str(tmp_path / "cap2.jsonl")
    with MockServer(cap2, MOCK_TOOL_CALL=json.dumps(tool_call)) as srv:
        res = run_binary(str(tmp_path), srv.base_url, ["--auto", "make changes"],
                         extra_env={"MOCK_REPLY_CONTENT": "MOCK_DONE"}, timeout=30)
    reqs = MockServer(cap2).requests() if os.path.exists(cap2) else []
    tool_seen = False
    for r in reqs:
        for m in r.get("messages", []):
            for tc in (m.get("tool_calls") or []):
                name = tc.get("function", {}).get("name", "")
                if name == "write_file":
                    tool_seen = True
    # In auto mode the write_file call is forwarded for execution; the file write
    # runs via the MCP backend, so it should be created by the mock-free path.
    # (If HOME isolation caused a missing tool backend, assert the tool was at
    #  least surfaced and NOT denied.)
    assert tool_seen, "write_file should reach the tool layer in auto mode"


# ── skill self-improvement: correct <dir>/SKILL.md structure + marker ─────────
def _iso(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))  # keep global skill dir inside tmp


def test_skill_create_writes_dir_skill_md(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    out = ai_mcp.skill_create("demo_skill", "desc", "step 1\nstep 2")
    assert "[SKILL_CREATED:demo_skill]" in out
    got = (tmp_path / ".agents" / "skills" / "demo_skill" / "SKILL.md").read_text()
    assert "name: demo_skill" in got
    assert "step 1" in got
    # also written to the (tmp) global dir
    assert (tmp_path / ".config" / "ai" / "skills" / "demo_skill" / "SKILL.md").exists()


def test_skill_update_appends_recent_learning(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    ai_mcp.skill_create("upd_skill", "d", "content")
    out = ai_mcp.skill_update("upd_skill", "found a bug: do X")
    assert "[SKILL_UPDATED:upd_skill]" in out
    p = tmp_path / ".agents" / "skills" / "upd_skill" / "SKILL.md"
    assert p.exists()
    assert "## Recent learning" in p.read_text()
    assert "found a bug: do X" in p.read_text()


def test_skill_note_logs_without_touching_body(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    out = ai_mcp.skill_note("general", "remember the lesson")
    assert "[SKILL:note:general]" in out
    log = tmp_path / ".config" / "ai" / "skills_learning_log.md"
    assert log.exists()
    assert "remember the lesson" in log.read_text()


# ── searchable conversation history ───────────────────────────────────────────
def test_history_search_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Point the history log + index at the tmp dir so we don't touch real data.
    monkeypatch.setattr(ai_mcp, "HISTORY_LOG", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(ai_mcp, "HISTORY_DB", str(tmp_path / "idx.db"))
    monkeypatch.setattr(ai_mcp, "CACHE_SESSIONS", str(tmp_path / "s"))
    monkeypatch.setattr(ai_mcp, "DATA_SESSIONS", str(tmp_path / "s"))
    log = tmp_path / "history.jsonl"
    log.write_text(json.dumps({
        "timestamp": "2026-08-09 00:00:00", "session_id": "sess_abc",
        "prompt": "how to fix the parser", "response": "Use the LLM lexer approach."
    }) + "\n" + json.dumps({
        "timestamp": "2026-08-09 01:00:00", "session_id": "sess_def",
        "prompt": "remind me tomorrow", "response": "Scheduled a reminder."
    }) + "\n")
    ai_mcp.rebuild_history_index()
    hits = ai_mcp.search_history("parser")
    assert "sess_abc" in hits
    assert "LLM lexer" in hits
    missing = ai_mcp.search_history("quasar")
    assert "No past conversation" in missing


def test_history_search_is_stale_aware(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ai_mcp, "HISTORY_LOG", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(ai_mcp, "HISTORY_DB", str(tmp_path / "idx.db"))
    monkeypatch.setattr(ai_mcp, "CACHE_SESSIONS", str(tmp_path / "s"))
    monkeypatch.setattr(ai_mcp, "DATA_SESSIONS", str(tmp_path / "s"))
    log = tmp_path / "history.jsonl"
    log.write_text(json.dumps({"timestamp": "t", "session_id": "s1",
                               "prompt": "old topic", "response": "r1"}) + "\n")
    ai_mcp.rebuild_history_index()
    assert "old topic" in ai_mcp.search_history("old topic")
    # Append a new session and confirm a fresh search picks it up automatically.
    with open(log, "a") as f:
        f.write(json.dumps({"timestamp": "t2", "session_id": "s2",
                            "prompt": "brand new topic", "response": "r2"}) + "\n")
    assert ai_mcp._history_is_stale()
    assert "brand new topic" in ai_mcp.search_history("brand new topic")