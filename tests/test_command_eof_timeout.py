"""A command that closes its stdout/stderr but keeps running (it "daemonises") must still be bound
by the command timeout and the ESC interrupt, in both engines: run_shell (agent/native.py) and
run_shell_command_timeout (ai.c) used to wait unboundedly after stdout EOF."""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_offline import MockServer, run_binary  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from agent import native  # noqa: E402

# the shell itself drops the stdout/stderr pipe (EOF for the reader), then keeps running
# (`sleep` itself is refused by the agent)
DAEMONISING = "exec >/dev/null 2>&1; tail -f /dev/null"


def test_run_shell_times_out_after_stdout_eof():
    t0 = time.time()
    out, status = native.run_shell(DAEMONISING + " 2>&1", 2, threading.Event(), lambda s: None)
    took = time.time() - t0
    assert status == 124, (out, status)
    assert "timed out" in out
    assert took < 10, took


def test_run_shell_interrupt_after_stdout_eof():
    ev = threading.Event()
    threading.Timer(0.7, ev.set).start()
    t0 = time.time()
    out, status = native.run_shell(DAEMONISING + " 2>&1", 60, ev, lambda s: None)
    assert status == 130 and time.time() - t0 < 10


def test_run_shell_still_returns_output_and_status_after_eof():
    out, status = native.run_shell("echo hi; exec >/dev/null 2>&1; timeout 0.3 tail -f /dev/null; exit 3", 10, threading.Event(), lambda s: None)
    assert out.strip() == "hi" and status == 3


def test_binary_command_timeout_bounds_a_daemonising_command(tmp_path, engine):
    home = str(tmp_path / "home")
    os.makedirs(home)
    cap = str(tmp_path / "cap.jsonl")
    tool_call = {"id": "c1", "type": "function",
                 "function": {"name": "execute_command", "arguments": json.dumps({"command": DAEMONISING, "timeout": 2})}}
    # one scripted call (MOCK_TOOL_SEQ): the failure nudge must not make the mock repeat it
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps([tool_call])) as srv:
        t0 = time.time()
        res = run_binary(home, srv.base_url, ["-y", "run it"], timeout=60)
        took = time.time() - t0
        reqs = srv.requests()
    assert took < 20, (took, res.stderr[-2000:])
    assert len(reqs) >= 2, res.stderr[-2000:]
    tool_msgs = [str(m["content"]) for m in reqs[1]["messages"] if m.get("role") == "tool"]
    assert any("timed out" in t.lower() for t in tool_msgs), tool_msgs
