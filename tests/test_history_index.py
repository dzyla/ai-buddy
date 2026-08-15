"""Tests for the rewritten conversation-history index (ai_mcp.py) and the
C-side session reliability changes (atomic writes, cache retention, resume
fallback to the persistent mirror).

Pure-Python tests monkeypatch ai_mcp's path globals to a tmp HOME so the real
history is never touched. Binary tests drive the compiled `ai` against the mock
OpenAI server (tests/mock_llm_server.py), same harness as tests/test_offline.py.
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


@pytest.fixture(scope="module", autouse=True)
def build_binary():
    binp = os.path.join(REPO, "ai")
    srcs = ("ai.c", "ai_session.c", "ai_git.c", "ai_terminal.c", "Makefile")
    stale = not os.path.exists(binp)
    if not stale:
        bm = os.path.getmtime(binp)
        for s in srcs:
            p = os.path.join(REPO, s)
            try:
                if os.path.getmtime(p) > bm:
                    stale = True
            except OSError:
                stale = True
    if stale:
        res = subprocess.run(["make"], cwd=REPO, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
    yield


@pytest.fixture
def hist(tmp_path, monkeypatch):
    """Isolate the history subsystem to a tmp HOME and return a namespace with
    the paths + a helper to (re)build."""
    home = tmp_path / "home"
    home.mkdir()
    log = home / "history.jsonl"
    db = home / "history_index.db"
    cache = home / "cache_sessions"
    data = home / "data_sessions"
    cache.mkdir()
    data.mkdir()
    monkeypatch.setattr(ai_mcp, "HISTORY_LOG", str(log))
    monkeypatch.setattr(ai_mcp, "HISTORY_DB", str(db))
    monkeypatch.setattr(ai_mcp, "CACHE_SESSIONS", str(cache))
    monkeypatch.setattr(ai_mcp, "DATA_SESSIONS", str(data))
    return types.SimpleNamespace(home=home, log=log, db=db, cache=cache, data=data)


import types  # noqa: E402  (used by the hist fixture above)


def _write_session(dirpath, sid, prompt="hello", answer="world"):
    p = os.path.join(dirpath, sid + ".json")
    with open(p, "w") as f:
        json.dump([
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ], f)
    return p


def _log_lines(logpath, records):
    with open(logpath, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ── incremental sync: a new turn is picked up without a full rebuild ─────────
def test_incremental_sync_picks_up_new_turn(hist):
    _log_lines(hist.log, [
        {"timestamp": "t0", "session_id": "s1", "prompt": "alpha beta", "response": "r1"},
    ])
    ai_mcp.rebuild_history_index()
    # Append a brand-new turn.
    with open(hist.log, "a") as f:
        f.write(json.dumps({"timestamp": "t1", "session_id": "s2",
                            "prompt": "zebra quasar", "response": "the answer"}) + "\n")
    # A full rebuild must NOT be required (log only grew, not shrank).
    assert not ai_mcp._history_needs_full_rebuild()
    out = ai_mcp.search_history("quasar")
    assert "s2" in out and "the answer" in out
    # The offset advanced so a re-search is a no-op fast path (no work).
    assert ai_mcp._history_is_stale() is False


def test_search_noop_when_sources_unchanged(hist):
    _log_lines(hist.log, [
        {"timestamp": "t0", "session_id": "s1", "prompt": "alpha beta", "response": "r1"},
    ])
    ai_mcp.rebuild_history_index()
    assert ai_mcp.search_history("alpha")
    # Nothing changed -> _ensure_history_ready takes the no-op branch.
    import sqlite3
    conn = sqlite3.connect(str(hist.db))
    assert ai_mcp._sources_unchanged(conn) is True
    conn.close()


# ── dedup: same session id in cache AND persistent is indexed once ───────────
def test_session_dedup_across_dirs(hist):
    _write_session(hist.cache, "sess_dup", prompt="c question", answer="c answer")
    _write_session(hist.data, "sess_dup", prompt="d question", answer="d answer")
    ai_mcp.rebuild_history_index()
    import sqlite3
    conn = sqlite3.connect(str(hist.db))
    n_archive = conn.execute(
        "SELECT count(*) FROM history_sessions WHERE session_id='sess_dup'").fetchone()[0]
    n_fts = conn.execute(
        "SELECT count(*) FROM history_fts WHERE source='session' AND session_id='sess_dup'"
    ).fetchone()[0]
    conn.close()
    assert n_archive == 1, "session id must be archived exactly once"
    assert n_fts == 1, "session id must have exactly one FTS row"


# ── durable archive: get_session survives on-disk deletion ───────────────────
def test_get_session_archive_fallback_after_prune(hist):
    _write_session(hist.cache, "sess_vanish", prompt="durable question",
                   answer="the durable answer")
    ai_mcp.rebuild_history_index()
    # Simulate cache pruning: delete the on-disk file.
    os.remove(os.path.join(str(hist.cache), "sess_vanish.json"))
    out = ai_mcp.get_session("sess_vanish")
    assert "from archive" in out
    assert "the durable answer" in out
    assert "durable question" in out


# ── durable archive: survives a FULL rebuild, not just a deletion ────────────
def test_archive_survives_full_rebuild(hist):
    _write_session(hist.cache, "sess_keep", prompt="keep question",
                   answer="keep answer")
    ai_mcp.rebuild_history_index()
    os.remove(os.path.join(str(hist.cache), "sess_keep.json"))
    # Force a full rebuild (it must restore the archive-only session).
    ai_mcp.rebuild_history_index()
    out = ai_mcp.get_session("sess_keep")
    assert "from archive" in out
    assert "keep answer" in out


# ── list_sessions merges on-disk + archive-only (tagged) ─────────────────────
def test_list_sessions_shows_archive_only(hist):
    _write_session(hist.cache, "sess_live", prompt="live q", answer="live a")
    _write_session(hist.data, "sess_arch", prompt="arch q", answer="arch a")
    ai_mcp.rebuild_history_index()
    os.remove(os.path.join(str(hist.data), "sess_arch.json"))
    out = ai_mcp.list_sessions(50)
    assert "sess_live" in out
    assert "sess_arch" in out
    assert "[archive]" in out


# ── retention (python-side, archive-first) prunes old, mirrored sessions ─────
def test_retention_prunes_older_sessions(hist, monkeypatch):
    import sqlite3
    monkeypatch.setenv("INFER_SESSION_RETENTION", "2")
    base = time.time() - 5000
    for i in range(1, 9):
        _write_session(hist.cache, f"sess_r{i:02d}", prompt=f"rq{i}", answer=f"ra{i}")
        _write_session(hist.data, f"sess_r{i:02d}", prompt=f"rq{i}", answer=f"ra{i}")
        p = str(hist.cache / f"sess_r{i:02d}.json")
        os.utime(p, (base + i, base + i))
        q = str(hist.data / f"sess_r{i:02d}.json")
        os.utime(q, (base + i, base + i))
    ai_mcp.rebuild_history_index()
    # Un-mirrored session created AFTER the rebuild: not in the persistent
    # mirror and not archived yet, so retention must KEEP it.
    _write_session(hist.cache, "sess_unmirrored", prompt="uq", answer="ua")
    up = str(hist.cache / "sess_unmirrored.json")
    os.utime(up, (base - 100, base - 100))  # oldest
    conn = sqlite3.connect(str(hist.db))
    removed = ai_mcp._prune_cache_sessions(conn)
    conn.close()
    remaining = {f for f in os.listdir(str(hist.cache)) if f.endswith(".json")}
    assert removed >= 5
    # Newest kept
    assert "sess_r08.json" in remaining
    # Oldest mirrored pruned
    assert "sess_r01.json" not in remaining
    # Un-mirrored AND un-archived must be kept
    assert "sess_unmirrored.json" in remaining


# ── binary helpers (shared with the resume-fallback test) ────────────────────
def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


class MockServer:
    def __init__(self, capture_path, **env):
        self.port = _free_port(); self.capture = capture_path; self.env = env; self.proc = None

    def __enter__(self):
        e = os.environ.copy(); e["MOCK_CAPTURE"] = self.capture
        e.update({k: v for k, v in self.env.items() if v is not None})
        self.proc = subprocess.Popen([sys.executable, MOCK, str(self.port)], env=e,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        return self

    def __exit__(self, *a):
        if self.proc:
            self.proc.terminate(); self.proc.wait(timeout=5)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1/"


def run_binary(home, base_url, args, extra_env=None, timeout=90):
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("INFER_") or k in (
                "CUDA_VISIBLE_DEVICES", "CUDA_PATH", "LD_LIBRARY_PATH",
                "LLAMA_CTX_SIZE", "LLAMA_N_GPU_LAYERS", "LLAMA_MODEL_PATH"):
            env.pop(k, None)
    env["HOME"] = home
    env["INFER_BASE_URL"] = base_url
    env["INFER_API_KEY"] = "test"
    env["INFER_MODEL"] = "mock"
    env["INFER_TOOL_CHOICE"] = "auto"
    if extra_env:
        env.update(extra_env)
    return subprocess.run([AI_BIN] + args, cwd=REPO, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=timeout)


# ── C: --resume falls back to the persistent mirror when the cache is cleared
def test_resume_falls_back_to_persistent_mirror(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    cap = str(tmp_path / "cap.jsonl")
    # Turn 1: establish context. The binary writes the session to BOTH the
    # cache and the persistent mirror, and prints its id on exit.
    with MockServer(cap, MOCK_TASK_COMPLETE="Understood, the codeword is OCELOT.") as srv:
        res = run_binary(str(home), srv.base_url, ["Remember the codeword is OCELOT."])
    assert res.returncode == 0
    # Recover the session id the harness printed ("resume: ai -r <id>").
    m = None
    import re
    for line in (res.stderr or "").splitlines():
        m = re.search(r"resume: ai -r (sess_\w+)", line)
    assert m, "expected a resume session id on stderr"
    sid = m.group(1)
    cache_path = home / ".cache" / "ai" / "sessions" / f"{sid}.json"
    persist_path = home / ".local" / "share" / "ai" / "sessions" / f"{sid}.json"
    assert cache_path.exists() and persist_path.exists()
    # Simulate a cache clear: remove the cache copy only.
    cache_path.unlink()
    cap2 = str(tmp_path / "cap2.jsonl")
    with MockServer(cap2, MOCK_TASK_COMPLETE="It was OCELOT.") as srv:
        res2 = run_binary(str(home), srv.base_url, ["-r", sid, "What was the codeword?"])
    assert res2.returncode == 0
    # The resumed request must contain the prior turn (OCELOT) even though only
    # the persistent mirror remained.
    req = None
    with open(cap2) as f:
        for line in f:
            if line.strip():
                req = json.loads(line)
    msgs = req["messages"]
    blob = " ".join(str(x.get("content", "")) for x in msgs)
    assert "OCELOT" in blob, "prior turn must be restored from the persistent mirror"
    assert msgs[-1]["content"] == "What was the codeword?"
