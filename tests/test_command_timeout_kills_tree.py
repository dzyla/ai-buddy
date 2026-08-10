"""Regression test: `execute_command` timeouts must kill the WHOLE process tree.

The `execute_command` tool (ai_mcp.py) runs the command via
`subprocess.run(cmd, shell=True, ...)`, which spawns `/bin/sh -c "<cmd>"`. The
real command (e.g. `python3 ...`) is a GRANDCHILD of the shell. Historically a
timeout only SIGKILLed the direct shell, so the real command (and anything IT
spawned) survived as an orphan and kept burning CPU.

This crashed in a real scenario: an `openmmator fit` timed out at 120s and left
3 orphaned python processes running at >2600% CPU. This test reproduces that
shape and proves the grandchildren are actually killed on timeout.

Run: python3 -m pytest tests/test_command_timeout_kills_tree.py -v
"""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import ai_mcp  # noqa: E402


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def test_execute_command_timeout_kills_grandchildren(tmp_path):
    # python is itself running inside the shell; it spawns a `sleep 120`
    # child, records its pid, then sleeps so the harness hits the timeout
    # while the process tree is still alive.
    cmd = (
        "python3 -c \"import subprocess,time;"
        f"open('{tmp_path}/gc.pid','w').write(str(subprocess.Popen(['sleep','120']).pid));"
        "time.sleep(120)\""
    )
    # tiny timeout so the test is fast
    result = ai_mcp.execute_command(cmd, timeout=2)
    assert "timed out" in result.lower(), result

    pidfile = tmp_path / "gc.pid"
    assert pidfile.exists(), "command never ran -> no grandchild spawned"
    gpid = int(pidfile.read_text().strip())
    # give the harness a moment to reap
    for _ in range(50):
        if not _alive(gpid):
            break
        time.sleep(0.1)
    assert not _alive(gpid), (
        f"grandchild pid {gpid} is STILL ALIVE after timeout — execute_command "
        "only killed the /bin/sh wrapper, not the process tree. "
        "This is the orphaned-subprocess leak."
    )


def test_execute_command_normal_commands_still_work(tmp_path):
    res = ai_mcp.execute_command("echo hello_harness")
    assert "hello_harness" in res
    assert "success" in res.lower()

    # multi-line shell script still works under start_new_session
    res = ai_mcp.execute_command("x=1; y=2; echo $((x+y))")
    assert "3" in res


def test_execute_command_respects_infer_command_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("INFER_COMMAND_TIMEOUT", "2")
    cmd = ("python3 -c \"import subprocess,time;"
           f"open('{tmp_path}/gc2.pid','w').write(str(subprocess.Popen(['sleep','120']).pid));"
           "time.sleep(120)\"")
    result = ai_mcp.execute_command(cmd)  # no explicit timeout -> env default
    assert "timed out" in result.lower(), result
    pidfile = tmp_path / "gc2.pid"
    if pidfile.exists():
        gpid = int(pidfile.read_text().strip())
        for _ in range(50):
            if not _alive(gpid):
                break
            time.sleep(0.1)
        assert not _alive(gpid)
