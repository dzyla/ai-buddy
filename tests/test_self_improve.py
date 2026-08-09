"""Unit tests for the automatic failure-learning ledger in ai_mcp.py.

Uses a throwaway HOME so the real ~/.config/ai is never touched.
Run: python3 -m pytest tests/test_self_improve.py -v
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


@pytest.fixture
def si(tmp_path, monkeypatch):
    # Point HOME at an isolated dir for every test.
    import ai_mcp
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("INFER_SELF_IMPROVE_RECURRENCE", "2")
    # Clear module-level caches if any (none currently, kept for safety).
    return ai_mcp


def test_err_signature_treats_paths_and_ids_as_equal():
    import ai_mcp
    # Numbers / ids collapse so the same mistake is recognised across paths.
    a = ai_mcp._err_signature("read_file", "No such file or directory: file_123.csv")
    b = ai_mcp._err_signature("read_file", "No such file or directory: file_456.csv")
    assert a == b, "numbers/paths should be normalised away"
    c = ai_mcp._err_signature("read_file", "Permission denied")
    assert a != c


def test_failure_then_recurrence_creates_pitfall(si):
    _, lesson1 = si.record_failure("read_file", '{"path":"/a/1.txt"}', "No such file or directory: f1.txt")
    assert lesson1 == ""  # first occurrence: just logged
    ok, lesson2 = si.record_failure("read_file", '{"path":"/b/2.txt"}', "No such file or directory: f2.txt")
    assert ok is True
    assert "[RECURRING FAILURE]" in lesson2
    # ledger has 2 failure entries; lessons.md now has a PITFALL
    ledger = open(si._ledger_path(), encoding="utf-8").read()
    assert ledger.count('"kind": "failure"') == 2
    lessons = open(si._lessons_path(), encoding="utf-8").read()
    assert "## PITFALL" in lessons


def test_recovery_persists_fix_and_lessons_for_finds_it(si):
    # Seed the pitfall so both a PITFALL and a FIX exist.
    si.record_failure("read_file", '{"path":"/nope.txt"}', "No such file or directory: nope.txt")
    si.record_failure("read_file", '{"path":"/nope2.txt"}', "No such file or directory: nope.txt")
    lesson = si.record_recovery("read_file", '{"path":"/real/data.txt"}', "No such file or directory: nope.txt")
    assert "succeeded" in lesson
    lessons = open(si._lessons_path(), encoding="utf-8").read()
    assert "## FIX" in lessons
    # On a future error for the SAME tool, we surface the lessons.
    ret = si.lessons_for("read_file", "No such file or directory: whatever_789.txt")
    assert "## FIX" in ret
    assert "## PITFALL" in ret


def test_lessons_for_empty_when_no_match(si):
    si.record_failure("execute_command", '{"command":"ls /missing"}', "ls: cannot access")
    ret = si.lessons_for("web_search", "some unrelated error")
    assert ret == ""


def test_sig_count_scoped_to_dir(si, monkeypatch):
    # Same signature across sessions (different HOME paths) is NOT counted together
    # when HOME changes — but within one HOME it is. We rely on HOME staying stable
    # in production (same user), so just assert in-HOME recurrence works.
    si.record_failure("x", "", "boom code 7")
    si.record_failure("x", "", "boom code 8")
    assert si._ledger_signature_count(si._err_signature("x", "boom code 9")) == 2


def test_cli_actions(si):
    import subprocess
    env = os.environ.copy()
    env["HOME"] = os.environ["HOME"]
    r = subprocess.run(
        [sys.executable, os.path.join(REPO, "ai_mcp.py"), "record-failure",
         json.dumps({"tool": "grep", "args": "", "error": "grep: no match: xyz"})],
        capture_output=True, text=True, env=env, cwd=REPO)
    assert r.returncode == 0
    r2 = subprocess.run(
        [sys.executable, os.path.join(REPO, "ai_mcp.py"), "lessons-for",
         json.dumps({"tool": "grep", "error": "no match: xyz"})],
        capture_output=True, text=True, env=env, cwd=REPO)
    assert r2.returncode == 0