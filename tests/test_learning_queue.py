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


# ── fix round 1 ──────────────────────────────────────────────────────────────
def _row(kind, tool="read_file"):
    return [r for r in state.lessons_list(status=None, include_expired=True) if r["kind"] == kind][0]


def _fail(n=3, tool="read_file", err="No such file or directory: /x/y.txt"):
    for _ in range(n):
        selfimprove.record_failure(tool, "{}", err)


def test_default_mode_fix_master_metrics_are_proposed(monkeypatch):
    monkeypatch.setenv("INFER_CHAIN_MASTERED", "1")
    _fail(1)
    selfimprove.record_recovery("read_file", '{"path": "/ok"}', "No such file or directory: /x/y.txt",
                                failed_args='{"path": "/x"}')
    kinds = {r["kind"]: r["status"] for r in state.lessons_list(status=None, include_expired=True)}
    assert kinds.get("FIX") == "proposed" and kinds.get("MASTER") == "proposed"
    assert state.lessons_render() == ""


def test_record_failure_reinforces_approved_pitfall(monkeypatch):
    monkeypatch.setenv("INFER_SELF_IMPROVE_RECURRENCE", "2")
    _fail(2)
    row = _row("PITFALL")
    learning.approve(row["id"], ttl_days=10)
    before = _status(row["id"])
    _fail(2)
    after = _status(row["id"])
    assert after["hits"] >= before["hits"] + 2
    assert after["expires"] >= before["expires"] + 59 * DAY - 10 * DAY  # pushed out to ~now+60d
    assert after["expires"] > before["expires"]
    assert len([r for r in state.lessons_list(status=None, include_expired=True) if r["kind"] == "PITFALL"]) == 1


def test_record_failure_surfaces_only_for_new_row(monkeypatch):
    monkeypatch.setenv("INFER_SELF_IMPROVE_RECURRENCE", "2")
    r1 = [selfimprove.record_failure("read_file", "{}", "No such file or directory: /a") for _ in range(2)]
    assert r1[0] == (False, "") and r1[1][0] is True
    assert selfimprove.record_failure("read_file", "{}", "No such file or directory: /a") == (False, "")


def test_expired_pitfall_returns_to_proposed_on_recurrence(monkeypatch):
    monkeypatch.setenv("INFER_SELF_IMPROVE_RECURRENCE", "2")
    _fail(2)
    lid = _row("PITFALL")["id"]
    learning.approve(lid, ttl_days=1, now=time.time() - 5 * DAY)
    assert learning.expire() == 1
    _fail(1)
    assert _status(lid)["status"] == "proposed"
    # lapsed but not yet swept by expire() behaves the same
    learning.approve(lid, ttl_days=1, now=time.time() - 5 * DAY)
    _fail(1)
    assert _status(lid)["status"] == "proposed"


def test_expired_lesson_reapproved_under_auto_policy(monkeypatch):
    lid = learning.propose("FIX", "t", "s", "b")
    learning.approve(lid, ttl_days=1, now=time.time() - 5 * DAY)
    monkeypatch.setenv("INFER_LEARNING_AUTO_APPROVE", "1")
    learning.propose("FIX", "t", "s", "b")
    row = _status(lid)
    assert row["status"] == "approved" and row["expires"] > time.time() + 59 * DAY


def test_record_recovery_reinforces_fix_and_master(monkeypatch):
    monkeypatch.setenv("INFER_CHAIN_MASTERED", "1")
    err = "No such file or directory: /x/y.txt"
    selfimprove.record_failure("read_file", "{}", err)
    selfimprove.record_recovery("read_file", '{"p": 1}', err, failed_args="{}")
    for k in ("FIX", "MASTER"):
        learning.approve(_row(k)["id"], ttl_days=5)
    before = {k: _status(_row(k)["id"]) for k in ("FIX", "MASTER")}
    selfimprove.record_recovery("read_file", '{"p": 2}', err, failed_args="{}")
    for k in ("FIX", "MASTER"):
        after = _status(before[k]["id"])
        assert after["hits"] == before[k]["hits"] + 1 and after["expires"] > before[k]["expires"]


def _metrics(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".cache" / "ai"
    d.mkdir(parents=True)
    with open(d / "metrics.jsonl", "w") as f:
        for _ in range(3):
            f.write(json.dumps({"tool": "flaky", "success": False, "error": "boom happened"}) + "\n")


def _append_metrics(tmp_path, n):
    import json
    with open(tmp_path / ".cache" / "ai" / "metrics.jsonl", "a") as f:
        for _ in range(n):
            f.write(json.dumps({"tool": "flaky", "success": False, "error": "boom happened"}) + "\n")


def _changes():
    c = state.connect(read=True)
    try:
        return c.execute("SELECT (SELECT count(*) FROM lessons), (SELECT sum(hits) FROM lessons), "
                         "(SELECT group_concat(key||value) FROM meta)").fetchone()[:]
    finally:
        c.close()


def test_metrics_sync_reinforces_only_on_new_evidence(tmp_path, monkeypatch):
    _metrics(tmp_path, monkeypatch)
    assert len(selfimprove._metrics_lesson_sync(min_fails=3)) == 1
    row = _row("PITFALL", "flaky")
    assert row["status"] == "proposed" and state.lessons_render() == ""
    learning.approve(row["id"], ttl_days=5)
    before = _status(row["id"])
    snap = _changes()
    for _ in range(3):  # unchanged metrics: nothing happens
        assert selfimprove._metrics_lesson_sync(min_fails=3) == []
    after = _status(row["id"])
    assert (after["hits"], after["expires"]) == (before["hits"], before["expires"])
    assert _changes() == snap
    _append_metrics(tmp_path, 2)  # new evidence: reinforce exactly once
    assert selfimprove._metrics_lesson_sync(min_fails=3) == []
    assert selfimprove._metrics_lesson_sync(min_fails=3) == []
    after = _status(row["id"])
    assert after["hits"] == before["hits"] + 1 and after["expires"] > before["expires"]


def test_metrics_sync_unchanged_takes_no_write_lock(tmp_path, monkeypatch):
    _metrics(tmp_path, monkeypatch)
    selfimprove._metrics_lesson_sync(min_fails=3)
    holder = state.connect()
    holder.execute("BEGIN IMMEDIATE")  # another process holds the write lock
    try:
        t0 = time.monotonic()
        with state.lock_budget(0.3):
            assert selfimprove._metrics_lesson_sync(min_fails=3) == []
        assert time.monotonic() - t0 < 1.0
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_expired_metric_lesson_requeued_only_after_new_evidence(tmp_path, monkeypatch):
    _metrics(tmp_path, monkeypatch)
    selfimprove._metrics_lesson_sync(min_fails=3)
    lid = _row("PITFALL", "flaky")["id"]
    learning.approve(lid, ttl_days=1, now=time.time() - 5 * DAY)
    learning.expire()
    selfimprove._metrics_lesson_sync(min_fails=3)
    assert _status(lid)["status"] == "expired"
    _append_metrics(tmp_path, 1)
    selfimprove._metrics_lesson_sync(min_fails=3)
    assert _status(lid)["status"] == "proposed"


def test_legacy_null_expiry_backfilled_on_touch_and_prune():
    a = learning.propose("FIX", "t", "a", "a body")
    b = learning.propose("FIX", "t", "b", "b body")
    with state.connect() as c:
        c.execute("UPDATE lessons SET status='approved', expires=NULL")
        c.commit()
    learning.propose("FIX", "t", "a", "a body")
    assert _status(a)["expires"] > time.time() + 59 * DAY
    assert _status(b)["expires"] is None
    learning.prune()
    assert _status(b)["expires"] > time.time() + 59 * DAY


def test_auto_approved_gets_expiry(monkeypatch):
    monkeypatch.setenv("INFER_LEARNING_AUTO_APPROVE", "1")
    lid = learning.propose("FIX", "t", "s", "b")
    assert _status(lid)["expires"] > time.time() + 59 * DAY


def _editor(tmp_path, monkeypatch, script):
    p = tmp_path / "ed.sh"
    p.write_text("#!/bin/sh\n" + script + "\n")
    p.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(p))
    monkeypatch.delenv("VISUAL", raising=False)


@pytest.mark.parametrize("script", ["exit 1", ": > \"$1\"", None])
def test_editor_failure_skips_not_approves(tmp_path, monkeypatch, script):
    if script is None:
        monkeypatch.setenv("EDITOR", str(tmp_path / "no-such-editor"))
        monkeypatch.delenv("VISUAL", raising=False)
    else:
        _editor(tmp_path, monkeypatch, script)
    lid = learning.propose("FIX", "t", "s", "orig")
    out = io.StringIO()
    res = learning.review(interactive=True, input_fn=lambda p: "e", out=out)
    assert res["approved"] == 0 and _status(lid)["status"] == "proposed" and _status(lid)["body"] == "orig"
    assert "skipping" in out.getvalue()


def test_editor_success_with_args_approves(tmp_path, monkeypatch):
    _editor(tmp_path, monkeypatch, 'echo "new text" > "$2"')
    monkeypatch.setenv("EDITOR", "%s -w" % os.environ["EDITOR"])
    lid = learning.propose("FIX", "t", "s", "orig")
    learning.review(interactive=True, input_fn=lambda p: "e", out=io.StringIO())
    assert _status(lid)["body"] == "new text" and _status(lid)["status"] == "approved"


def test_review_ctrl_c_clean(capsys):
    a = learning.propose("FIX", "t", "x", "b1")
    b = learning.propose("FIX", "t", "y", "b2")
    seq = iter(["a"])

    def inp(p):
        try:
            return next(seq)
        except StopIteration:
            raise KeyboardInterrupt
    assert learning.cli(["review"], input_fn=inp) == 130
    assert _status(a)["status"] == "approved" and _status(b)["status"] == "proposed"


def test_ai_binary_lessons_prose_is_not_passthrough(tmp_path):
    env = _env(tmp_path)
    for k in ("INFER_BASE_URL", "INFER_API_KEY", "INFER_MODEL"):
        env.pop(k, None)
    r = subprocess.run([os.path.join(REPO, "ai"), "lessons", "learned", "from", "X"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=30)
    assert "STATUS" not in r.stdout and "No lessons" not in r.stdout  # normal prompt path (env error)
