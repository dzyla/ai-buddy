"""End-to-end test: the `ai` binary auto-learns from tool errors.

Two runs against the mock LLM:
  RUN 1 (INFER_SELF_IMPROVE_RECURRENCE=1):
      read_file <missing>  -> error  -> harness records failure, writes a PITFALL lesson
      read_file <ok>       -> success-> harness records the FIX (recovery)
  RUN 2 (fresh process, SAME HOME):
      read_file <missing>  -> error  -> harness surfaces the lesson learned in RUN 1
                                       ("[REMEMBERED FROM PAST SESSIONS]")

This proves the agent no longer forgets the same mistake across sessions,
WITHOUT depending on the model volunteering to persist.
Run: python3 -m pytest tests/test_self_improve_e2e.py -v -s
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
PORT = 8813


@pytest.fixture(scope="module", autouse=True)
def build():
    # Rebuild only if the binary is stale relative to its C sources.
    def stale():
        if not os.path.exists(AI_BIN):
            return True
        return any(os.path.getmtime(os.path.join(REPO, s)) > os.path.getmtime(AI_BIN)
                   for s in ("ai.c", "ai_git.c", "ai_terminal.c", "ai_session.c", "Makefile")
                   if os.path.exists(os.path.join(REPO, s)))
    if stale():
        res = subprocess.run(["make"], cwd=REPO, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr


def _readiness(port, env):
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def _run_agent(home, seq, missing, ok, capture=None):
    server = subprocess.Popen(
        [sys.executable, MOCK, str(PORT)],
        env=os.environ | {"MOCK_TOOL_SEQ": json.dumps(seq),
                          "MOCK_TASK_COMPLETE": "DONE",
                          **({"MOCK_CAPTURE": capture} if capture else {})},
        cwd=REPO)
    try:
        assert _readiness(PORT, os.environ)
        r = subprocess.run(
            [AI_BIN, "-q", "", "-n", "do the task"],
            env=os.environ | {
                "HOME": home,
                "INFER_BASE_URL": f"http://127.0.0.1:{PORT}/v1/",
                "INFER_API_KEY": "x", "INFER_MODEL": "mock", "INFER_TOOL_CHOICE": "auto",
                # Low recurrence so a single failure already writes a lesson.
                "INFER_SELF_IMPROVE_RECURRENCE": "1",
                "INFER_STEP_LIMIT": "40",
            },
            capture_output=True, text=True, timeout=90, cwd=REPO)
        return r
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


def _make_seq(missing, ok):
    return [
        {"id": "c1", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": missing})}},
        {"id": "c2", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": ok})}},
    ]


def test_binary_learns_from_tool_error_across_sessions():
    home = tempfile.mkdtemp(prefix="ai-si-e2e-")
    ok_file = os.path.join(home, "data.txt")
    missing = os.path.join(home, "nope_missing.txt")
    with open(ok_file, "w") as f:
        f.write("hello from the local agent\n")

    # RUN 1: error then success -> should write ledger + PITFALL + FIX.
    r1 = _run_agent(home, _make_seq(missing, ok_file), missing, ok_file)
    assert r1.returncode == 0, r1.stderr
    ledger = os.path.join(home, ".config", "ai", "self_improve", "ledger.jsonl")
    lessons = os.path.join(home, ".config", "ai", "self_improve", "lessons.md")
    assert os.path.exists(ledger), "ledger.jsonl not written by harness"
    with open(ledger) as f:
        kinds = [json.loads(l)["kind"] for l in f if l.strip()]
    assert "failure" in kinds
    assert "recovery" in kinds, "harness did not auto-record the recovery/fix"
    with open(lessons) as f:
        txt = f.read()
    assert "## PITFALL " in txt and "## FIX " in txt, lessons

    # RUN 2: same error again (fresh process, same HOME)
    # -> the harness must surface the lesson learned in RUN 1.
    seq2 = [{"id": "d1", "type": "function",
             "function": {"name": "read_file", "arguments": json.dumps({"path": missing})}}]
    capture = os.path.join(home, "requests.jsonl")
    r2 = _run_agent(home, seq2, missing, ok_file, capture=capture)
    out = r2.stdout + r2.stderr
    assert "surfaced past lesson for 'read_file'" in out, \
        "harness did not surface the cross-session lesson in run 2:\n" + out[-2000:]
    # Strongest proof: the lesson actually reached the MODEL's context (the tool
    # message the binary sent to the LLM endpoint contains the remembered text).
    captured = ""
    if os.path.exists(capture):
        captured = open(capture, encoding="utf-8", errors="replace").read()
    assert "REMEMBERED FROM PAST SESSIONS" in captured, \
        "lesson text was not injected into the model context:\n" + captured[-3000:]
    assert "succeeded with approach" in captured, \
        "run 2 did not include the FIX lesson learned in run 1:\n" + captured[-3000:]

    shutil.rmtree(home, ignore_errors=True)