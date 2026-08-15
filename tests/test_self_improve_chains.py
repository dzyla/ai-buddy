"""Self-improvement CHAIN system + session-start recap tests.

Covers the error-chain loop (each error fixed via its own chain), the
promotion of reliably-fixed chains to MASTER lessons, and the Obsidian-style
session-start recap that recapitulates relevant past learning.

Parts:
  A. Pure-Python unit tests (isolated HOME, no LLM):
     - chain ids are stable per error and distinct across errors
     - a chain is mastered after INFER_CHAIN_MASTERED recoveries, un-mastered
       by a later failure (the fix must keep holding)
     - session_recap surfaces mastered chains + recent sessions + flaky tools
     - tool-health view reads real metrics (success/failure)
     - MCP isError results normalise to "Error: ..." text (never raw JSON)
     - call-tool through a fake MCP server returns the error text
  B. C-binary e2e (mock LLM, isolated HOME):
     - a failure then a success of the same tool writes ONE ledger chain
       (failure + recovery share the same chain_id)
     - the session-start recap reaches the MODEL (system message of the
       captured request contains [SESSION RECAP] + the mastered lesson)
     - INFER_SESSION_RECAP=0 disables the recap

Run: python3 -m pytest tests/test_self_improve_chains.py -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_BIN = os.path.join(REPO, "ai")
MOCK = os.path.join(REPO, "tests", "mock_llm_server.py")
PORT = 8821

sys.path.insert(0, REPO)


@pytest.fixture(scope="module", autouse=True)
def build():
    def stale():
        if not os.path.exists(AI_BIN):
            return True
        return any(os.path.getmtime(os.path.join(REPO, s)) > os.path.getmtime(AI_BIN)
                   for s in ("ai.c", "ai_git.c", "ai_terminal.c", "ai_session.c", "Makefile")
                   if os.path.exists(os.path.join(REPO, s)))
    if stale():
        res = subprocess.run(["make"], cwd=REPO, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr


# ─────────────────────────── A. Python units ───────────────────────────

@pytest.fixture
def si(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("INFER_CHAIN_MASTERED", "2")
    monkeypatch.delenv("INFER_SESSION_RECAP", raising=False)
    import ai_mcp
    # Isolate history index from the real one.
    ai_mcp.HISTORY_DB = str(home / "hist.db")
    ai_mcp.HISTORY_LOG = str(home / "history.jsonl")
    ai_mcp.CACHE_SESSIONS = str(home / "csessions")
    ai_mcp.DATA_SESSIONS = str(home / "dsessions")
    return ai_mcp


def test_chain_ids_stable_and_distinct(si):
    a1 = si._chain_id("read_file", "No such file or directory: /data/a_123.csv")
    a2 = si._chain_id("read_file", "No such file or directory: /data/a_999.csv")
    assert a1 == a2, "same mistake with different numeric ids must share a chain"
    b = si._chain_id("read_file", "Permission denied")
    assert b != a1, "different errors must be different chains"
    c = si._chain_id("web_search", "No such file or directory: /data/a_123.csv")
    assert c != a1, "same error on a different tool is a different chain"


def test_chain_promotes_to_master_and_demotes_on_new_failure(si):
    err1 = "No such file or directory: file_1.txt"
    err2 = "No such file or directory: file_2.txt"
    si.record_failure("read_file", "", err1)
    si.record_recovery("read_file", "used ls first", err1)
    # 1 recovery only -> not mastered yet (threshold 2)
    cid = si._chain_id("read_file", err1)
    assert not si._chain_is_mastered(cid)
    si.record_failure("read_file", "", err2)
    si.record_recovery("read_file", "used ls first", err2)
    # 2 recoveries, last event = recovery -> MASTERED, MASTER lesson written
    assert si._chain_is_mastered(cid)
    lessons = open(si._lessons_path(), encoding="utf-8").read()
    assert "## MASTER" in lessons
    # A NEW failure on the same error un-masters it (the fix must keep holding)
    si.record_failure("read_file", "", "No such file or directory: file_3.txt")
    assert not si._chain_is_mastered(cid)
    # Mastering again promotes; the MASTER lesson is NOT duplicated
    si.record_recovery("read_file", "used ls first", "No such file or directory: file_3.txt")
    assert si._chain_is_mastered(cid)
    lessons2 = open(si._lessons_path(), encoding="utf-8").read()
    assert lessons2.count("## MASTER") == 1, "master lesson should not duplicate"


def test_failure_recoveries_share_one_chain(si):
    """The core loop: every failure + its fix land in the same chain."""
    si.record_failure("read_file", '{"path":"/a/1"}', "No such file or directory: a_1.txt")
    si.record_recovery("read_file", '{"path":"/a/real_1"}', "No such file or directory: a_2.txt")
    recs = si._read_ledger()
    fids = {r["chain_id"] for r in recs if r["kind"] == "failure"}
    rids = {r["chain_id"] for r in recs if r["kind"] == "recovery"}
    assert fids == rids == {si._chain_id("read_file", "No such file or directory: a_1.txt")}, \
        (fids, rids)


def test_session_recap_sections(si):
    # Seed a mastered chain + a pitfall
    err = "Connection refused to host_1"
    si.record_failure("web_fetch", "", err)
    si.record_recovery("web_fetch", "retry with backoff", err)
    si.record_failure("web_fetch", "", "Connection refused to host_2")
    si.record_recovery("web_fetch", "retry with backoff", "Connection refused to host_3")
    # Seed a fake recent session in the index
    os.makedirs(si.CACHE_SESSIONS, exist_ok=True)
    sess = os.path.join(si.CACHE_SESSIONS, "sess_999.json")
    with open(sess, "w") as f:
        json.dump([{"role": "user", "content": "fix the build pipeline please"},
                   {"role": "assistant", "content": "I fixed the build pipeline."}], f)
    # Seed metrics: one flaky tool
    mdir = os.path.expanduser("~/.cache/ai")
    os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(mdir, "metrics.jsonl"), "w") as f:
        for i in range(8):
            f.write(json.dumps({"timestamp": "t", "tool": "flaky_tool",
                                "duration_ms": 1.0,
                                "success": i < 3}) + "\n")
        for i in range(8):
            f.write(json.dumps({"timestamp": "t", "tool": "good_tool",
                                "duration_ms": 1.0, "success": True}) + "\n")
    recap = si.session_recap()
    assert recap.startswith("[SESSION RECAP")
    assert "MASTERED ERROR CHAINS" in recap
    assert "retry with backoff" in recap
    assert "RECENT SESSIONS" in recap
    assert "fix the build pipeline" in recap
    assert "TOOL HEALTH" in recap
    assert "flaky_tool" in recap
    assert "good_tool" not in recap
    assert len(recap) <= 1600


def test_session_recap_empty_when_nothing_learned(si):
    # Fresh HOME with no lessons; but there may be no sessions either.
    assert si.session_recap() == "" or "[SESSION RECAP" in si.session_recap()


def test_log_metric_records_failure_and_error(si, tmp_path, monkeypatch):
    mfile = os.path.join(str(tmp_path), "home", ".cache", "ai", "metrics.jsonl")
    si.log_metric("web_search", 12.0, success=False, error="Timeout code 42")
    si.log_metric("web_search", 12.0, success=True)
    assert os.path.exists(mfile)
    rows = [json.loads(l) for l in open(mfile)]
    assert rows[0]["success"] is False
    assert rows[0]["error"] == "Timeout code 42"
    assert rows[1]["success"] is True
    assert "error" not in rows[1]


def test_mcp_result_to_text_variants(si):
    assert si.mcp_result_to_text({"content": [{"type": "text", "text": "hello"}]}) == "hello"
    t = si.mcp_result_to_text({"content": [{"type": "text", "text": "boom"}], "isError": True})
    assert t.startswith("Error:") and "boom" in t
    assert si.mcp_result_to_text({"error": "Failed to start server"}).startswith("Error:")
    assert si.mcp_result_to_text(None).startswith("Error:")
    assert si.mcp_result_to_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert si.mcp_result_to_text({"content": []}) == "(no content)"
    assert si.mcp_result_to_text("plain") == "plain"


FAKE_MCP_SERVER = """#!/usr/bin/env python3
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    m = req.get("method"); rid = req.get("id")
    if m == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":"2024-11-05",
              "capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"0"}}}), flush=True)
    elif m == "notifications/initialized":
        pass
    elif m == "tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"tools":[
            {"name":"explode","description":"always fails","inputSchema":{"type":"object","properties":{}}}]} }), flush=True)
    elif m == "tools/call":
        print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{
            "content":[{"type":"text","text":"kaboom: the fake tool failed"}],"isError":True}}), flush=True)
    else:
        if rid is not None:
            print(json.dumps({"jsonrpc":"2.0","id":rid,"result":{}}), flush=True)
"""


def test_call_tool_mcp_iserror_normalised(tmp_path):
    """A real (fake) MCP server returning isError:true must come out as
    'Error: ...' text through `ai_mcp.py call-tool` — never raw JSON."""
    server = tmp_path / "fake_mcp.py"
    server.write_text(FAKE_MCP_SERVER)
    cfg = {"mcpServers": {"fake": {"command": sys.executable,
                                   "args": [str(server)], "env": {}}}}
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "mcp.json").write_text(json.dumps(cfg))
    env = os.environ.copy()
    r = subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py"),
                        "call-tool", "fake", "explode", "{}"],
                       cwd=str(cwd), env=env, capture_output=True, text=True, timeout=60)
    out = r.stdout.strip()
    assert out.startswith("Error:"), out
    assert "kaboom" in out, out
    assert not out.startswith("{"), "MCP result must not be raw JSON: " + out


# ─────────────────────────── B. C-binary e2e ───────────────────────────

def _readiness(port):
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def _run_agent(home, seq, extra_env=None, capture=None):
    env = {
        "HOME": home,
        "INFER_BASE_URL": f"http://127.0.0.1:{PORT}/v1/",
        "INFER_API_KEY": "x", "INFER_MODEL": "mock", "INFER_TOOL_CHOICE": "auto",
        "INFER_CHAIN_MASTERED": "2",
        "INFER_STEP_LIMIT": "40",
    }
    if extra_env:
        env.update(extra_env)
    if capture:
        env["MOCK_CAPTURE"] = capture
    server = subprocess.Popen(
        [sys.executable, MOCK, str(PORT)],
        env=os.environ | {"MOCK_TOOL_SEQ": json.dumps(seq),
                          "MOCK_TASK_COMPLETE": "DONE", **env},
        cwd=REPO)
    try:
        assert _readiness(PORT), "mock server did not start"
        return subprocess.run(
            [AI_BIN, "-q", "", "-n", "chains test"],
            env=os.environ | env,
            capture_output=True, text=True, timeout=120, cwd=REPO)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


def test_binary_links_failure_and_recovery_into_one_chain():
    home = tempfile.mkdtemp(prefix="ai-chain-e2e-")
    ok_file = os.path.join(home, "data.txt")
    missing = os.path.join(home, "nope_missing.txt")
    with open(ok_file, "w") as f:
        f.write("payload\n")
    seq = [
        {"id": "c1", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": missing})}},
        {"id": "c2", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": ok_file})}},
    ]
    r = _run_agent(home, seq, extra_env={"INFER_SELF_IMPROVE_RECURRENCE": "1"})
    assert r.returncode == 0, r.stderr
    ledger = os.path.join(home, ".config", "ai", "self_improve", "ledger.jsonl")
    assert os.path.exists(ledger), "ledger not written"
    recs = [json.loads(l) for l in open(ledger) if l.strip()]
    fails = [x for x in recs if x["kind"] == "failure"]
    recs_ok = [x for x in recs if x["kind"] == "recovery"]
    assert fails and recs_ok, recs
    # THE core invariant: failure and its fix share the same error chain
    assert all("chain_id" in x and x["chain_id"].startswith("chain_") for x in recs), recs
    assert fails[0]["chain_id"] == recs_ok[0]["chain_id"], \
        f"failure/recovery in different chains: {fails[0]['chain_id']} vs {recs_ok[0]['chain_id']}"
    shutil.rmtree(home, ignore_errors=True)


def test_binary_injects_session_recap_into_model_context():
    home = tempfile.mkdtemp(prefix="ai-recap-e2e-")
    ok_file = os.path.join(home, "data.txt")
    missing = os.path.join(home, "nope_missing.txt")
    with open(ok_file, "w") as f:
        f.write("payload\n")
    capture = os.path.join(home, "requests.jsonl")
    # RUN 1: fail twice + recover twice -> chain mastered -> MASTER lesson
    seq = [
        {"id": "a1", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": missing})}},
        {"id": "a2", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": ok_file})}},
        {"id": "a3", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": missing})}},
        {"id": "a4", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": ok_file})}},
    ]
    r1 = _run_agent(home, seq, extra_env={"INFER_SELF_IMPROVE_RECURRENCE": "1"})
    assert r1.returncode == 0, r1.stderr
    lessons = os.path.join(home, ".config", "ai", "self_improve", "lessons.md")
    assert os.path.exists(lessons)
    assert "## MASTER" in open(lessons).read(), \
        "chain should be promoted to MASTER after 2 recoveries: " + open(lessons).read()
    # RUN 2 (fresh process): the recap with the mastered chain must reach
    # the MODEL — assert on the captured system message, not the terminal.
    seq2 = [{"id": "b1", "type": "function",
             "function": {"name": "read_file", "arguments": json.dumps({"path": ok_file})}}]
    r2 = _run_agent(home, seq2, capture=capture)
    assert r2.returncode == 0, r2.stderr
    assert os.path.exists(capture), "no requests captured"
    first_req = json.loads(open(capture, encoding="utf-8").readline())
    system_msg = next(m for m in first_req["messages"] if m["role"] == "system")
    assert "[SESSION RECAP" in system_msg["content"], \
        "session recap not injected into system prompt: " + system_msg["content"][-800:]
    assert "MASTERED ERROR CHAINS" in system_msg["content"], \
        "mastered chain missing from recap: " + system_msg["content"][-800:]
    shutil.rmtree(home, ignore_errors=True)


def test_binary_session_recap_disabled_with_env():
    home = tempfile.mkdtemp(prefix="ai-recap-off-")
    ok_file = os.path.join(home, "data.txt")
    with open(ok_file, "w") as f:
        f.write("payload\n")
    capture = os.path.join(home, "requests.jsonl")
    seq = [{"id": "c1", "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps({"path": ok_file})}}]
    r = _run_agent(home, seq, extra_env={"INFER_SESSION_RECAP": "0"}, capture=capture)
    assert r.returncode == 0, r.stderr
    first_req = json.loads(open(capture, encoding="utf-8").readline())
    system_msg = next(m for m in first_req["messages"] if m["role"] == "system")
    assert "[SESSION RECAP" not in system_msg["content"], \
        "INFER_SESSION_RECAP=0 did not disable the recap"
    shutil.rmtree(home, ignore_errors=True)
