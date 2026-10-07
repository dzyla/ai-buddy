"""Single SQLite state store (aitools/state.py) and the legacy-file migration.

Every test runs against a tmp HOME / tmp $AI_STATE_DB (see the autouse fixture in conftest.py);
the real ~/.config/ai, ~/.cache/ai and ~/.local/share/ai are never touched.
Run: python3 -m pytest tests/test_state_store.py -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from aitools import state  # noqa: E402

DAY = 86400.0


# ── fixture HOME with every legacy file ──────────────────────────────────────
LESSONS_MD = """# My lessons (hand-edited)

## PITFALL 2026-09-01 10:00:00
tool: read_file
signature: read_file no such file or directory f#.txt
> Recurring pitfall (2x and counting): `read_file` failed with:
> No such file or directory
Before retrying the identical call, look up the '## FIX' lessons.

## FIX 2026-09-01 10:05:00
tool: read_file
signature: read_file no such file or directory f#.txt
> `read_file` failed (No such file) then succeeded with approach: `{"path": "/real"}`.

## NOTE added by hand
Always prefer rg over grep -r in big trees.

## FIX 2026-09-02 11:00:00
tool: read_file
signature: read_file no such file or directory f#.txt
> duplicate of the FIX above (same kind/tool/signature)
"""


def _legacy_home(root):
    """Build a HOME holding every legacy state file the migration knows about."""
    home = root / "home"
    cfg = home / ".config" / "ai"
    si = cfg / "self_improve"
    tasks = cfg / "scheduled_tasks"
    data = home / ".local" / "share" / "ai"
    cache = home / ".cache" / "ai"
    for d in (si, tasks, data, cache):
        d.mkdir(parents=True)

    # memory.txt: ~6 KB, one line carries a raw latin-1 byte (not valid UTF-8).
    lines = [("fact %03d " % i + "m" * 50).encode() for i in range(100)]
    lines.insert(3, b"caf\xe9 au lait is the preferred drink")
    blob = b"\n".join(lines) + b"\n"
    assert len(blob) > 6000
    (cfg / "memory.txt").write_bytes(blob)

    (si / "lessons.md").write_text(LESSONS_MD, encoding="utf-8")

    recs = [
        {"ts": "2026-09-01 10:00:00", "kind": "failure", "tool": "read_file", "args": "{}",
         "error": "No such file", "signature": "read_file no such file", "chain_id": "chain_a",
         "phase": "execution"},
        {"ts": "2026-09-01 10:01:00", "kind": "failure", "tool": "read_file", "args": "{}",
         "error": "No such file", "signature": "read_file no such file", "chain_id": "chain_a",
         "phase": "execution"},
        {"ts": "2026-09-01 10:02:00", "kind": "recovery", "tool": "read_file", "approach": "{\"p\":1}",
         "prior_error": "No such file", "signature": "read_file no such file", "chain_id": "chain_a",
         "phase": "execution"},
    ]
    with open(si / "ledger.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(recs[0]) + "\n")
        f.write('{"ts": "2026-09-01 10:00:30", "kind": "fail\n')   # corrupt (torn) line
        f.write(json.dumps(recs[1]) + "\n")
        f.write(json.dumps(recs[2]) + "\n")

    now = time.time()
    (tasks / "daily_news.json").write_text(json.dumps({
        "task_id": "daily_news", "prompt": "summarise the news", "interval_seconds": 3600,
        "run_once": False, "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 60)),
        "last_run": "never", "run_count": 0, "env": {}}))
    (tasks / "reminder_x.json").write_text(json.dumps({
        "task_id": "reminder_x", "prompt": "[reminder] stretch", "interval_seconds": 600,
        "run_once": True, "kind": "reminder", "message": "stretch", "zulip_to": "me@example.com",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "env": {}}))

    chats = [
        {"session_id": "zulip_1", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.localtime(now - 3600)),
         "sender_email": "owner@example.com", "query": "how is the gpu box", "status": "success",
         "reply_snippet": "all good"},
        {"session_id": "zulip_2", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.localtime(now - 60)),
         "sender_email": "owner@example.com", "query": "disk usage?", "status": "error",
         "reply_snippet": ""},
    ]
    body = "".join(json.dumps(c) + "\n" for c in chats)
    (data / "zulip_chats.jsonl").write_text(body)
    (cache / "zulip_chats.jsonl").write_text(body)   # the bridge mirrored every line here too

    # Legacy FTS memory store (remember/recall + vault index).
    conn = sqlite3.connect(str(cfg / "memory.db"))
    conn.execute("CREATE VIRTUAL TABLE memories USING fts5(content, metadata)")
    conn.execute("INSERT INTO memories(content, metadata) VALUES ('the cluster uses slurm', 'auto')")
    conn.execute("CREATE VIRTUAL TABLE vault_fts USING fts5(title, content)")
    conn.execute("INSERT INTO vault_fts(title, content) VALUES ('setup.md', 'install notes')")
    conn.commit()
    conn.close()
    return home


def test_migrate_legacy_imports_renames_and_is_idempotent(tmp_path):
    home = _legacy_home(tmp_path)
    cfg = home / ".config" / "ai"

    counts = state.migrate_legacy(str(home))

    assert counts["memory"] == 101
    assert counts["lessons"] == 4          # PITFALL, FIX, hand NOTE, preamble note; dup FIX merged
    assert counts["ledger"] == 3
    assert counts["ledger_skipped"] == 1
    assert counts["scheduled_tasks"] == 2
    assert counts["chat_audit"] == 2       # the cache mirror's duplicates are not re-imported
    assert counts["memories"] == 1
    assert counts["vault"] == 1

    # originals renamed, nothing deleted
    for p in ("memory.txt", "memory.db", "self_improve/lessons.md", "self_improve/ledger.jsonl",
              "scheduled_tasks/daily_news.json", "scheduled_tasks/reminder_x.json"):
        assert not (cfg / p).exists(), p
        assert (cfg / (p + ".migrated.bak")).exists(), p
    assert (home / ".local/share/ai/zulip_chats.jsonl.migrated.bak").exists()
    assert (home / ".cache/ai/zulip_chats.jsonl.migrated.bak").exists()

    mem = state.memory_list()
    assert len(mem) == 101
    assert "café au lait is the preferred drink" in mem       # latin-1 byte decoded, not dropped
    rendered = state.memory_render()
    assert len(rendered.encode("utf-8")) <= 4096
    assert "fact 099" in rendered and "fact 000" not in rendered   # newest kept

    lessons = state.lessons_render()
    assert "## PITFALL" in lessons and "## FIX" in lessons
    assert "Always prefer rg over grep" in lessons
    assert "My lessons (hand-edited)" in lessons
    fix = [r for r in state.lessons_list() if r["kind"] == "FIX"]
    assert len(fix) == 1 and fix[0]["hits"] == 2

    led = state.ledger_rows()
    assert [r["kind"] for r in led] == ["failure", "failure", "recovery"]
    assert led[2]["ok"] == 1 and led[0]["ok"] == 0 and led[0]["chain_id"] == "chain_a"

    t = state.task_get("reminder_x")
    assert t["kind"] == "reminder" and t["run_once"] is True and t["message"] == "stretch"
    assert {x["task_id"] for x in state.task_list()} == {"daily_news", "reminder_x"}

    recent = state.chat_audit_recent(limit=10)
    assert [r["session_id"] for r in recent] == ["zulip_2", "zulip_1"]
    assert recent[1]["prompt"] == "how is the gpu box"

    conn = state.connect()
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(state.SCHEMA_VERSION)
    assert conn.execute("SELECT value FROM meta WHERE key='migrated_v1'").fetchone()[0]
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
    conn.close()

    # second run: no-op, nothing duplicated
    assert state.migrate_legacy(str(home)) == {}
    assert len(state.memory_list()) == 101
    # a legacy file that re-appears (old-code process still running) gets a second pass
    (cfg / "memory.txt").write_text("written after the migration\n")
    again = state.migrate_legacy(str(home))
    assert again["memory"] == 1 and again["renamed"] == 1
    assert len(state.memory_list()) == 102
    assert not (cfg / "memory.txt").exists()
    assert (cfg / "memory.txt.migrated.bak.1").exists()
    assert state.migrate_legacy(str(home)) == {}


def test_lazy_migration_on_first_connect(tmp_path, monkeypatch):
    home = _legacy_home(tmp_path)
    monkeypatch.delenv("AI_STATE_DB", raising=False)
    monkeypatch.setenv("HOME", str(home))
    assert state.db_path() == str(home / ".local" / "share" / "ai" / "state.db")
    assert "café au lait is the preferred drink" in state.memory_list()
    assert (home / ".config/ai/memory.txt.migrated.bak").exists()
    assert oct(os.stat(state.db_path()).st_mode & 0o777) == "0o600"


def test_migration_reports_unreadable_files_and_never_raises(tmp_path):
    home = tmp_path / "home"
    tasks = home / ".config" / "ai" / "scheduled_tasks"
    tasks.mkdir(parents=True)
    (tasks / "broken.json").write_text("{not json")
    (home / ".config" / "ai" / "self_improve").mkdir()
    (home / ".config" / "ai" / "self_improve" / "lessons.md").mkdir()   # a dir, not a file
    counts = state.migrate_legacy(str(home))
    assert counts["scheduled_tasks"] == 0 and counts["scheduled_tasks_skipped"] == 1
    assert counts.get("errors", 0) >= 1
    assert (tasks / "broken.json.migrated.bak").exists()


def test_explicit_db_override_never_migrates_home(tmp_path, monkeypatch):
    """With $AI_STATE_DB set (tests, tools) connect() must not touch HOME's legacy files."""
    home = _legacy_home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AI_STATE_DB", str(tmp_path / "other.db"))
    state.connect().close()
    assert state.memory_list() == []
    assert (home / ".config/ai/memory.txt").exists()


# ── memory ───────────────────────────────────────────────────────────────────
def test_memory_add_dedupes_case_insensitively_and_render_keeps_newest():
    assert state.memory_add("User prefers metric units") == "added"
    assert state.memory_add("  user PREFERS metric units ") == "duplicate"
    assert state.memory_add("") == "empty"
    assert state.memory_list() == ["User prefers metric units"]
    for i in range(120):
        state.memory_add("fact number %03d " % i + "p" * 60)
    out = state.memory_render(max_bytes=4096)
    assert len(out.encode("utf-8")) <= 4096
    assert out.splitlines()[-1].startswith("fact number 119")
    assert "metric units" not in out
    for line in out.splitlines():
        assert line.startswith("fact number")       # whole lines only
    evicted = state.memory_evict(cap=4000)
    assert "User prefers metric units" in evicted
    assert len("\n".join(state.memory_list())) <= 4000


def test_save_memory_tool_keeps_old_contract():
    import ai_mcp
    ai_mcp.save_memory("User prefers metric units")
    ai_mcp.save_memory("Lab server is gpu-box")
    text = "\n".join(state.memory_list())
    assert "metric units" in text and "gpu-box" in text
    assert "no duplicate" in ai_mcp.save_memory("user prefers metric units")
    for i in range(80):
        r = ai_mcp.save_memory("fact number %03d " % i + "p" * 60)
    assert "evicted" in r
    text = "\n".join(state.memory_list())
    assert len(text) <= ai_mcp.MEMORY_CAP
    assert "fact number 079" in text and "metric units" not in text
    ai_mcp.save_memory("only this", replace=True)
    assert state.memory_list() == ["only this"]


# ── lessons ──────────────────────────────────────────────────────────────────
def test_lesson_upsert_dedupes_and_counts_hits():
    assert state.lesson_upsert("FIX", "read_file", "sig a", "body 1") is True
    assert state.lesson_upsert("FIX", "read_file", "sig a", "body 2") is False
    assert state.lesson_upsert("FIX", "read_file", "sig b", "body 3") is True
    rows = state.lessons_list()
    assert [r["hits"] for r in rows] == [2, 1]
    assert state.lesson_exists("FIX", signature="sig a")
    assert state.lesson_exists("FIX", tool="read_file")
    assert not state.lesson_exists("PITFALL", signature="sig a")
    state.lesson_upsert("FIX", "web", "sig c", "pending one", status="pending")
    state.lesson_upsert("FIX", "web", "sig d", "expired one", expires=time.time() - 1)
    out = state.lessons_render()
    assert "body 1" in out and "body 3" in out
    assert "pending one" not in out and "expired one" not in out
    assert "pending one" in state.lessons_render(status="pending")
    assert state.lessons_render(limit=1).count("## FIX") == 1


# ── ledger ───────────────────────────────────────────────────────────────────
def test_concurrent_ledger_append_from_threads():
    state.connect().close()
    errors = []

    def worker(n):
        try:
            for i in range(200):
                state.ledger_append(tool="t%d" % n, ok=False, error="boom %d" % i,
                                    chain_id="c%d" % n, kind="failure")
        except Exception as e:  # pragma: no cover - the assertion reports it
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert state.ledger_count() == 800


def test_concurrent_writers_from_processes(tmp_path):
    """Separate processes (bridge, scheduler, interactive ai) writing at once: no 'database is locked'."""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from aitools import state\n"
            "for i in range(100):\n"
            "    state.ledger_append(tool='p', ok=True, kind='recovery')\n"
            "    state.chat_audit_add('zulip', 's', 'q%%d' %% i, 'r')\n" % REPO)
    procs = [subprocess.Popen([sys.executable, "-c", code], stderr=subprocess.PIPE, text=True)
             for _ in range(4)]
    errs = [p.communicate(timeout=120)[1] for p in procs]
    assert all(p.returncode == 0 for p in procs), errs
    assert state.ledger_count() == 400
    assert len(state.chat_audit_recent(limit=1000)) == 400


def test_ledger_trim_keeps_newest():
    for i in range(30):
        state.ledger_append(tool="t", ok=False, error="e%d" % i, kind="failure")
    assert state.ledger_trim(max_rows=10) == 20
    rows = state.ledger_rows()
    assert len(rows) == 10 and rows[-1]["error"] == "e29" and rows[0]["error"] == "e20"


def test_ledger_append_caps_at_5000(monkeypatch):
    conn = state.connect()
    conn.executemany("INSERT INTO ledger(ts, tool, ok, kind) VALUES (?, 't', 0, 'failure')",
                     [(float(i),) for i in range(5000)])
    conn.close()
    state.ledger_append(tool="t", ok=False, error="newest", kind="failure")
    assert state.ledger_count() <= 5000
    assert state.ledger_rows()[-1]["error"] == "newest"


# ── chat audit ───────────────────────────────────────────────────────────────
def test_chat_audit_retention_deletes_rows_older_than_90_days():
    now = time.time()
    state.chat_audit_add("zulip", "a", "ancient", "r", ts=now - 120 * DAY)
    state.chat_audit_add("zulip", "a", "old", "r", ts=now - 91 * DAY)
    state.chat_audit_add("zulip", "a", "recent", "r", ts=now - 89 * DAY)
    conn = state.connect()
    conn.execute("DELETE FROM meta WHERE key='chat_audit_pruned_at'")
    conn.close()
    state.connect().close()                 # retention runs on open
    assert [r["prompt"] for r in state.chat_audit_recent(limit=10)] == ["recent"]
    # at most once per day: a fresh old row survives the next open today
    state.chat_audit_add("zulip", "a", "old again", "r", ts=now - 100 * DAY)
    state.connect().close()
    assert len(state.chat_audit_recent(limit=10)) == 2
    assert state.chat_audit_prune(force=True) == 1


# ── sessions / tasks ─────────────────────────────────────────────────────────
def test_sessions_and_tasks_roundtrip():
    state.session_upsert("s1", started=1.0, path="/x/s1.json")
    state.session_upsert("s1", ended=2.0, summary="did things")
    s = state.sessions_list()
    assert s == [{"id": "s1", "started": 1.0, "ended": 2.0, "path": "/x/s1.json", "summary": "did things"}]

    state.task_put("t1", {"task_id": "t1", "prompt": "p", "interval_seconds": 60, "run_once": True,
                          "kind": "reminder"}, next_run=123.0)
    assert state.task_get("t1")["prompt"] == "p"
    conn = state.connect()
    row = conn.execute("SELECT next_run, run_once, kind FROM scheduled_tasks WHERE id='t1'").fetchone()
    conn.close()
    assert tuple(row) == (123.0, 1, "reminder")
    assert state.task_delete("t1") is True
    assert state.task_get("t1") is None
    assert state.task_delete("t1") is False


# ── CLI ──────────────────────────────────────────────────────────────────────
def _cli(*args, env=None):
    return subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py"), *args],
                          capture_output=True, text=True, timeout=60, env=env or os.environ.copy())


def test_cli_state_export_stats_and_migrate(tmp_path):
    state.memory_add("likes tea")
    state.lesson_upsert("FIX", "read_file", "sig", "use the absolute path")
    r = _cli("state-export", "memory")
    assert r.returncode == 0 and r.stdout.strip() == "likes tea"
    r = _cli("state-export", "lessons")
    assert "use the absolute path" in r.stdout
    state.task_put("tk", {"task_id": "tk", "interval_seconds": 60}, next_run=1234.5)
    r = _cli("state-export", "tasks")
    assert r.stdout == "tk\t1234\t\n"
    stats = json.loads(_cli("state-stats").stdout)
    assert stats["tables"]["memory"] == 1 and stats["path"] == state.db_path()
    home = _legacy_home(tmp_path)
    out = json.loads(_cli("state-migrate", str(home)).stdout)
    assert out["memory"] == 101
    assert json.loads(_cli("state-migrate", str(home)).stdout) == {}


def test_history_indexer_records_sessions_index(tmp_path):
    from aitools import history
    p = tmp_path / "20261007_120000_abcd.json"
    p.write_text(json.dumps([{"role": "user", "content": "plot the gel image"},
                             {"role": "assistant", "content": "done"}]))
    conn = sqlite3.connect(":memory:")
    history._ensure_history_schema(conn)
    assert history._index_session_file(conn, str(p))
    rows = state.sessions_list()
    assert rows[0]["id"] == "20261007_120000_abcd" and rows[0]["summary"] == "plot the gel image"
    assert rows[0]["path"] == str(p) and rows[0]["ended"] == pytest.approx(os.path.getmtime(p))


# ── the binary reads memory through `state-export memory` ────────────────────
def test_binary_injects_state_memory_not_memory_txt(tmp_path):
    from test_offline import MockServer, _sandbox_run
    if subprocess.run(["make"], cwd=REPO, capture_output=True).returncode != 0:
        pytest.skip("build failed")
    home = tmp_path / "home"
    (home / ".config" / "ai").mkdir(parents=True)
    (home / ".config" / "ai" / "memory.txt").write_text("STALE FILE MEMORY\n")
    state.memory_add("prefers SI units (from the state db)")
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["hello"])
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    system = next(m["content"] for m in reqs[0]["messages"] if m["role"] == "system")
    assert "Persistent Memory/Preferences:\nprefers SI units (from the state db)" in system
    assert "STALE FILE MEMORY" not in system


# ── fix round 1: lock-free reads, safe commit, retryable/second-pass migration ──
def test_export_memory_not_blocked_by_a_writer_holding_the_lock():
    state.memory_add("likes oolong")
    holder = state.connect()
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO ledger(ts, tool, ok, kind) VALUES (1, 't', 0, 'failure')")
    try:
        t0 = time.monotonic()
        r = _cli("state-export", "memory")
        took = time.monotonic() - t0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "likes oolong"
    assert took < 2.0, took


def test_export_memory_from_a_read_only_db():
    state.memory_add("likes oolong")
    state.lesson_upsert("FIX", "t", "s", "the fix")
    path = state.db_path()
    os.chmod(path, 0o444)
    try:
        r = _cli("state-export", "memory")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "likes oolong"
        assert "the fix" in _cli("state-export", "lessons").stdout
        assert state.memory_list() == ["likes oolong"]     # in-process read path too
    finally:
        os.chmod(path, 0o600)


def test_commit_failure_rolls_back_and_next_write_is_durable(monkeypatch):
    state.connect().close()
    real = state._commit
    calls = []

    def flaky(conn):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error")
        real(conn)
    monkeypatch.setattr(state, "_commit", flaky)
    conn = state.connect()
    with pytest.raises(sqlite3.OperationalError):
        state.memory_add("lost", conn=conn)
    assert not conn.in_transaction
    assert state.memory_add("kept", conn=conn) == "added"
    conn.close()
    assert state.memory_list() == ["kept"]


def test_failed_lazy_migration_returns_a_usable_connection(tmp_path, monkeypatch):
    home = _legacy_home(tmp_path)
    monkeypatch.delenv("AI_STATE_DB", raising=False)
    monkeypatch.setenv("HOME", str(home))
    real = state._commit
    calls = []

    def fail_second(conn):     # commit #1 creates the schema, #2 is the migration
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        real(conn)
    monkeypatch.setattr(state, "_commit", fail_second)
    conn = state.connect()
    assert not conn.in_transaction
    assert state.memory_add("durable", conn=conn) == "added"
    conn.close()
    assert state.memory_list() == ["durable"]              # nothing half-imported
    assert (home / ".config/ai/memory.txt").exists()       # originals untouched -> retried later
    monkeypatch.setattr(state, "_migrate_tried", set())
    assert "café au lait is the preferred drink" in state.memory_list()
    assert (home / ".config/ai/memory.txt.migrated.bak").exists()


def test_failed_source_stays_retryable(tmp_path):
    home = _legacy_home(tmp_path)
    lessons = home / ".config/ai/self_improve/lessons.md"
    os.chmod(lessons, 0)              # transiently unreadable
    try:
        counts = state.migrate_legacy(str(home))
    finally:
        os.chmod(lessons, 0o600)
    assert counts["errors"] == 1 and counts["lessons"] == 0 and counts["memory"] == 101
    assert lessons.exists()
    conn = state.connect()
    assert "PermissionError" in conn.execute(
        "SELECT value FROM meta WHERE key='migrate_failed:lessons.md'").fetchone()[0]
    conn.close()
    assert state.migrate_legacy(str(home)) == {}           # not retried implicitly
    out = json.loads(_cli("state-migrate", "--retry", str(home)).stdout)
    assert out["lessons"] == 4 and out["errors"] == 0
    assert not lessons.exists() and (home / ".config/ai/self_improve/lessons.md.migrated.bak").exists()
    conn = state.connect()
    assert conn.execute("SELECT 1 FROM meta WHERE key='migrate_failed:lessons.md'").fetchone() is None
    conn.close()


def test_lazy_second_pass_imports_lines_written_by_old_processes(tmp_path, monkeypatch):
    home = _legacy_home(tmp_path)
    monkeypatch.delenv("AI_STATE_DB", raising=False)
    monkeypatch.setenv("HOME", str(home))
    assert state.ledger_count() == 3
    si = home / ".config/ai/self_improve"
    (si / "ledger.jsonl").write_text(json.dumps({"ts": "2026-10-01 10:00:00", "kind": "failure",
                                                 "tool": "late", "error": "x", "chain_id": "c"}) + "\n")
    (home / ".cache/ai/zulip_chats.jsonl").write_text(json.dumps(
        {"session_id": "zulip_late", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
         "query": "late q", "status": "success"}) + "\n")
    monkeypatch.setattr(state, "_migrate_tried", set())   # = a new process
    assert state.ledger_count() == 4
    assert state.chat_audit_recent(limit=1)[0]["session_id"] == "zulip_late"
    assert (si / "ledger.jsonl.migrated.bak.1").exists() and not (si / "ledger.jsonl").exists()


def test_scheduler_backs_off_when_the_state_db_is_unusable(tmp_path, monkeypatch):
    import ai_mcp
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def broken(task_id):
        calls.append(task_id)
        if len(calls) <= 12:
            raise sqlite3.OperationalError("unable to open database file")
        return None                 # then the task is gone -> loop exits
    monkeypatch.setattr(state, "task_get", broken)
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))   # fake clock
    ai_mcp.run_scheduler_loop("t_backoff")
    assert sleeps == [1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300, 300]
    log = (tmp_path / ".cache/ai/scheduler.log").read_text()
    assert log.count("State db unavailable") == 12        # one line per backoff step


def test_session_index_writes_stop_after_first_lock_failure(monkeypatch):
    from aitools import sessions
    monkeypatch.setattr(sessions, "_session_index_down", [False])
    calls = []

    def locked(*a, **k):
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(state, "session_upsert", locked)
    assert [sessions.record_session("s%d" % i) for i in range(5)] == [False] * 5
    assert len(calls) == 1


def test_session_recap_stays_fast_while_a_writer_holds_the_lock(tmp_path):
    """session-recap runs synchronously at ai startup and re-indexes changed session files
    (each one a sessions-index write) and may write metric-derived lessons."""
    home = tmp_path / "home"
    sess = home / ".local/share/ai/sessions"
    sess.mkdir(parents=True)
    for i in range(30):
        (sess / ("s%02d.json" % i)).write_text(json.dumps([{"role": "user", "content": "task %d" % i}]))
    cache = home / ".cache/ai"
    cache.mkdir(parents=True)
    (cache / "metrics.jsonl").write_text("".join(
        json.dumps({"tool": "t%d" % (i % 10), "duration_ms": 1, "success": False, "error": "boom %d" % (i % 10)})
        + "\n" for i in range(30)))
    state.connect().close()
    holder = state.connect()
    holder.execute("BEGIN IMMEDIATE")
    try:
        t0 = time.monotonic()
        r = _cli("session-recap", env=dict(os.environ, HOME=str(home)))
        took = time.monotonic() - t0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert r.returncode == 0, r.stderr
    assert took < 6.0, took
