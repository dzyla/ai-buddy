"""Human-reviewed lesson queue (aitools/learning.py) and `ai lessons ...`.

Offline: isolated state db ($AI_STATE_DB from conftest) and isolated HOME for subprocesses.
Run: python3 -m pytest tests/test_learning_queue.py -v
"""
import io
import os
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from aitools import learning, selfimprove, state  # noqa: E402

DAY = 86400.0


@pytest.fixture(autouse=True)
def _no_auto(monkeypatch):
    monkeypatch.delenv("INFER_LEARNING_AUTO_APPROVE", raising=False)


def _status(lid):
    return [r for r in state.lessons_list(status=None, include_expired=True) if r["id"] == lid][0]


def test_propose_twice_one_row_two_hits():
    a = learning.propose("PITFALL", "read_file", "sig one", "body")
    b = learning.propose("pitfall", "read_file", "sig one", "other body")
    assert a == b
    rows = state.lessons_list(status=None, include_expired=True)
    assert len(rows) == 1 and rows[0]["hits"] == 2 and rows[0]["status"] == "proposed"
    assert rows[0]["body"] == "body"


def test_proposed_not_rendered_until_approved():
    lid = learning.propose("FIX", "t", "s", "do the thing")
    assert "do the thing" not in state.lessons_render()
    learning.approve(lid)
    assert "do the thing" in state.lessons_render()
    assert _status(lid)["expires"] > time.time() + 59 * DAY


def test_expire_time_travel():
    lid = learning.propose("FIX", "t", "s", "temp lesson")
    learning.approve(lid, ttl_days=10)
    assert learning.expire(now=time.time() + 5 * DAY) == 0
    assert learning.expire(now=time.time() + 11 * DAY) == 1
    assert _status(lid)["status"] == "expired"
    assert "temp lesson" not in state.lessons_render()


def test_expiry_filtered_on_read_without_expire():
    lid = learning.propose("FIX", "t", "s", "stale")
    learning.approve(lid, ttl_days=1, now=time.time() - 3 * DAY)
    assert "stale" not in state.lessons_render()  # read path alone hides it


def test_reinforcement_extends_expiry():
    lid = learning.propose("FIX", "t", "s", "b")
    learning.approve(lid, ttl_days=10)
    first = _status(lid)["expires"]
    learning.propose("FIX", "t", "s", "b", now=time.time() + 8 * DAY, ttl_days=10)
    row = _status(lid)
    assert row["expires"] > first + 7 * DAY and row["hits"] == 2
    assert learning.expire(now=time.time() + 11 * DAY) == 0


def test_reject_and_prune():
    lid = learning.propose("FIX", "t", "s", "bad")
    learning.reject(lid)
    assert "bad" not in state.lessons_render()
    assert learning.prune()["deleted"] == 0
    assert learning.prune(now=time.time() + 31 * DAY)["deleted"] == 1
    assert state.lessons_list(status=None, include_expired=True) == []


def test_review_scripted_decide():
    ids = [learning.propose("FIX", "t", "s%d" % i, "body%d" % i) for i in range(3)]
    plan = {ids[0]: "a", ids[1]: "r", ids[2]: ("e", "edited body")}
    res = learning.review(interactive=False, decide=lambda r: plan[r["id"]])
    assert res["approved"] == 2 and res["rejected"] == 1
    assert _status(ids[0])["status"] == "approved"
    assert _status(ids[1])["status"] == "rejected"
    assert _status(ids[2])["body"] == "edited body" and _status(ids[2])["status"] == "approved"


def test_review_quit_leaves_pending():
    ids = [learning.propose("FIX", "t", "s%d" % i, "b") for i in range(3)]
    seq = iter(["a", "q"])
    res = learning.review(interactive=False, decide=lambda r: next(seq))
    assert res["approved"] == 1 and res["pending"] == 2
    assert _status(ids[2])["status"] == "proposed"


def test_review_piped_input():
    a = learning.propose("FIX", "t", "x", "b1")
    b = learning.propose("FIX", "t", "y", "b2")
    answers = iter(["r", "a"])
    out = io.StringIO()
    assert learning.cli(["review"], input_fn=lambda p: next(answers), out=out) == 0
    assert _status(a)["status"] == "rejected" and _status(b)["status"] == "approved"
    assert "evidence=1" in out.getvalue()


def test_auto_approve_env(monkeypatch):
    monkeypatch.setenv("INFER_LEARNING_AUTO_APPROVE", "1")
    assert learning.auto_policy() is True
    lid = learning.propose("FIX", "t", "s", "auto body")
    assert _status(lid)["status"] == "approved"
    assert "auto body" in state.lessons_render()


def test_selfimprove_proposes_not_approves(monkeypatch):
    monkeypatch.setenv("INFER_PITFALL_THRESHOLD", "1")
    for _ in range(4):
        selfimprove.record_failure("read_file", "{}", "No such file or directory: /x/y.txt")
    rows = state.lessons_list(status=None, include_expired=True)
    assert rows and all(r["status"] == "proposed" for r in rows)
    assert state.lessons_render() == ""


def _env(tmp_path):
    env = dict(os.environ)
    env.update(HOME=str(tmp_path), AI_STATE_DB=str(tmp_path / "state.db"))
    return env


def test_cli_list_python(tmp_path):
    learning.propose("FIX", "mytool", "s", "listed body")
    r = subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py"), "lessons", "list"],
                       capture_output=True, text=True, env=_env(tmp_path), cwd=str(tmp_path), timeout=30)
    assert r.returncode == 0, r.stderr
    assert "STATUS" in r.stdout and "proposed" in r.stdout and "listed body" in r.stdout
    r = subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py"), "lessons", "approve", "1"],
                       capture_output=True, text=True, env=_env(tmp_path), cwd=str(tmp_path), timeout=30)
    assert r.returncode == 0 and state.lessons_list()[0]["body"] == "listed body"


def test_ai_binary_lessons_passthrough(tmp_path):
    learning.propose("FIX", "mytool", "s", "binary body")
    env = _env(tmp_path)
    for k in ("INFER_BASE_URL", "INFER_API_KEY", "INFER_MODEL"):
        env.pop(k, None)
    (tmp_path / "bin").mkdir()
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    os.symlink(os.path.join(REPO, "ai_mcp.py"), tmp_path / ".local" / "bin" / "ai_mcp.py")
    os.symlink(os.path.join(REPO, "aitools"), tmp_path / ".local" / "bin" / "aitools")
    r = subprocess.run([os.path.join(REPO, "ai"), "lessons", "list", "--status", "proposed"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=30)
    assert r.returncode == 0, r.stderr
    assert "binary body" in r.stdout
