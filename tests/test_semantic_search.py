"""Semantic memory (Task 5b): embedder client, vectors in the state db, hybrid search_memory,
backfill, auto-recall in the system prompt, the search_memory tool, ai-backend embed, install.

All offline: tests/fixtures/fake_embedder.py serves deterministic hashed bag-of-words vectors
(cosine = word-stem overlap) on a random port; conftest points AI_EMBED_URL at a dead port by
default, so nothing here ever reaches a real embedder.
"""
import json
import math
import os
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests", "fixtures"))

import fake_embedder  # noqa: E402
from aitools import embed, history, registry, search, state  # noqa: E402

AI_MCP = os.path.join(REPO, "ai_mcp.py")
AI_BIN = os.path.join(REPO, "ai")


@pytest.fixture
def fake(monkeypatch):
    srv = fake_embedder.start()
    monkeypatch.setenv("AI_EMBED_URL", srv.url)
    yield srv
    srv.stop()


@pytest.fixture(autouse=True)
def hist_db(tmp_path, monkeypatch):
    d = tmp_path / "home" / ".local" / "share" / "ai"
    d.mkdir(parents=True, exist_ok=True)
    p = str(d / "history_index.db")
    monkeypatch.setattr(history, "HISTORY_DB", p)
    return p


def _dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d" % port


def _f16(blob):
    return struct.unpack("<%de" % (len(blob) // 2), blob)


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def _vec_rows():
    c = state.connect(read=True)
    try:
        return [dict(r) for r in c.execute("SELECT * FROM vectors ORDER BY item_type, item_id")]
    finally:
        c.close()


# ── (a) storage ──────────────────────────────────────────────────────────────
def test_vectors_stored_float16_and_normalised(fake):
    state.memory_add("The lab printer is on the third floor")
    stats = search.index_items()
    assert stats["embedded"] == 1 and stats["embedder"] == "up"
    rows = _vec_rows()
    assert len(rows) == 1
    r = rows[0]
    assert (r["item_type"], r["item_id"]) == ("memory", "1")
    assert r["dim"] == 768 and r["model"]
    assert len(r["v128"]) == 128 * 2 and len(r["v768"]) == 768 * 2  # float16
    assert abs(_norm(_f16(r["v128"])) - 1.0) < 1e-2
    assert abs(_norm(_f16(r["v768"])) - 1.0) < 1e-2
    # documents are sent with the model-card document prefix
    assert fake.texts() == ["title: none | text: The lab printer is on the third floor"]


def test_query_prefix_and_embed_contract(fake):
    v = embed.embed(["printer location"], "query")
    assert len(v) == 1 and len(v[0]) == 768
    assert fake.texts() == ["task: search result | query: printer location"]
    assert embed.embed([], "query") == []


# ── (b) hybrid fusion ────────────────────────────────────────────────────────
def _corpus():
    for t in ("Project codename XK42 lives in the lab wiki",
              "Ansible deploys every server nightly from the ops repo",
              "Lunch on Fridays is pizza in the break room",
              "The cryo-EM microscope booking sheet is shared on the drive",
              "Coffee machine descaling happens monthly"):
        state.memory_add(t)


def test_hybrid_fuses_fts_keyword_and_vector_overlap(fake):
    _corpus()
    search.index_items()
    res = search.search_memory("XK42 deploying servers", k=3)
    texts = [r["text"] for r in res]
    assert any("XK42" in t for t in texts[:2]), res
    assert any("Ansible" in t for t in texts[:2]), res
    by = {r["text"].split()[0]: r for r in res}
    assert "fts" in by["Project"]["match"]          # exact keyword hit via FTS5
    assert by["Ansible"]["match"] == ["vec"]        # stem overlap only the vectors see
    for r in res:
        assert set(r) >= {"item_type", "item_id", "text", "score", "source_ts"}
    # keyword-only search cannot find the paraphrase
    fts_only = search.search_memory("deploying servers", k=5, use_vectors=False)
    assert not any("Ansible" in r["text"] for r in fts_only)


# ── (c) embedder down ────────────────────────────────────────────────────────
def test_embedder_down_falls_back_to_fts(monkeypatch):
    _corpus()
    monkeypatch.setenv("AI_EMBED_URL", _dead_url())
    t0 = time.monotonic()
    res = search.search_memory("XK42 deploying servers", k=5)
    assert time.monotonic() - t0 < 0.6
    assert [r["text"] for r in res] == ["Project codename XK42 lives in the lab wiki"]
    assert embed.embed(["x"], "query") is None


def test_embedder_hung_times_out_fast(monkeypatch):
    _corpus()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)  # accepts into the backlog but never answers
    try:
        monkeypatch.setenv("AI_EMBED_URL", "http://127.0.0.1:%d" % s.getsockname()[1])
        t0 = time.monotonic()
        res = search.search_memory("XK42", k=5)
        assert time.monotonic() - t0 < 0.6
        assert res and "XK42" in res[0]["text"]
    finally:
        s.close()


# ── (d) re-embedding on change only ──────────────────────────────────────────
def test_changed_content_reembeds_unchanged_does_not(fake):
    state.memory_add("alpha fact one")
    state.memory_add("beta fact two")
    assert search.index_items()["embedded"] == 2
    n = len(fake.texts())
    assert search.index_items()["embedded"] == 0
    assert len(fake.texts()) == n
    before = {r["item_id"]: r["hash"] for r in _vec_rows()}
    c = state.connect()
    with state.transaction(c):
        c.execute("UPDATE memory SET text='beta fact two, revised' WHERE id=2")
    c.close()
    assert search.index_items()["embedded"] == 1
    assert fake.texts()[n:] == ["title: none | text: beta fact two, revised"]
    after = {r["item_id"]: r["hash"] for r in _vec_rows()}
    assert after["1"] == before["1"] and after["2"] != before["2"]
    # a deleted item loses its vector (trigger) and its FTS row
    state.memory_clear()
    assert _vec_rows() == []
    assert search.search_memory("alpha", use_vectors=False) == []


def test_writes_enqueue_and_queue_drains(fake):
    state.memory_add("queued memory line")
    state.lesson_upsert("PITFALL", "execute_command", "sig1", "use rsync -a not cp -r")
    state.chat_audit_add("zulip", "someone", "how do I book the scope", "use the sheet")
    c = state.connect(read=True)
    q = sorted(tuple(r) for r in c.execute("SELECT item_type, item_id FROM embed_queue"))
    c.close()
    assert q == [("chat", "1"), ("lesson", "1"), ("memory", "1")]
    search.index_items()
    c = state.connect(read=True)
    assert c.execute("SELECT count(*) FROM embed_queue").fetchone()[0] == 0
    c.close()


def test_long_documents_are_chunked(fake):
    long = " ".join("word%d" % i for i in range(1200))  # ~9 KB
    state.memory_add(long)
    search.index_items()
    ids = [r["item_id"] for r in _vec_rows()]
    assert ids[0] == "1" and all(i.startswith("1#") for i in ids[1:])
    assert 2 <= len(ids) <= search.MAX_CHUNKS


# ── (e) backfill of legacy items ─────────────────────────────────────────────
def _legacy_items(hist_db):
    state.memory_add("legacy memory about the backup NAS")
    state.lesson_upsert("FIX", "read_file", "sig2", "binary files need pdftotext")
    state.chat_audit_add("zulip", "a", "where is the backup NAS", "rack 3")
    c = state.connect()
    with state.transaction(c):
        c.execute("INSERT INTO memories(content, metadata) VALUES ('remembered: NAS password rotates', '')")
        c.execute("INSERT INTO vault_fts(title, content) VALUES ('nas.md', 'NAS runbook notes')")
        c.execute("DELETE FROM embed_queue")  # legacy rows were never queued
    c.close()
    h = sqlite3.connect(hist_db)
    history._ensure_history_schema(h)
    history._index_turn_row(h, 1, {"prompt": "check NAS disk usage", "response": "92% full",
                                   "timestamp": "2026-09-01T10:00:00", "session_id": "s1"}, 1)
    h.commit()
    h.close()


def test_backfill_cli_indexes_all_legacy_sources(fake, hist_db, tmp_path):
    _legacy_items(hist_db)
    env = dict(os.environ, AI_EMBED_URL=fake.url, HOME=str(tmp_path / "home"))
    r = subprocess.run([sys.executable, AI_MCP, "embed-backfill"], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["embedded"] >= 6 and out["embedder"] == "up"
    types = {row["item_type"] for row in _vec_rows()}
    assert types == {"memory", "lesson", "chat", "note", "history_chunk"}
    # sources filter + history results through the hybrid search
    res = search.search_memory("NAS disk usage", sources=["history"], k=3)
    assert res and res[0]["item_type"] == "history_chunk" and "92%" in res[0]["text"]
    assert res[0]["source_ts"] > 0


def test_backfill_limit_and_embedder_down(fake, hist_db, monkeypatch):
    _legacy_items(hist_db)
    assert search.index_items(limit=2)["embedded"] == 2
    monkeypatch.setenv("AI_EMBED_URL", _dead_url())
    st = search.index_items()
    assert st["embedder"] == "down" and st["embedded"] == 0


def test_since_and_lesson_status_filters(fake):
    state.lesson_upsert("NOTE", "", "s-old", "zeppelin hangar rules", status="proposed")
    state.memory_add("zeppelin flights are on tuesdays")
    c = state.connect()
    with state.transaction(c):
        c.execute("UPDATE memory SET created=? WHERE id=1", (time.time() - 30 * 86400,))
    c.close()
    search.index_items()
    res = search.search_memory("zeppelin", k=5)
    assert [r["item_type"] for r in res] == ["memory"]  # proposed lessons never surface
    assert search.search_memory("zeppelin", since="7d") == []
    assert len(search.search_memory("zeppelin", since="2000-01-01")) == 1


# ── (f) auto-recall ──────────────────────────────────────────────────────────
def _recall_corpus():
    state.memory_add("User prefers concise answers in British English", pinned=True)
    for i in range(40):
        state.memory_add("unrelated filler fact number %d about gardening tomatoes" % i)
    state.memory_add("The GPU box hostname alias is buildbox and needs VPN")


def test_auto_recall_has_pinned_and_relevant_within_budget(fake):
    _recall_corpus()
    search.index_items()
    out = search.recall_section("ssh to the gpu box please")
    assert out is not None
    assert len(out.encode()) <= search.RECALL_BUDGET_BYTES
    assert "User prefers concise answers" in out
    assert "buildbox" in out
    assert out.count("tomatoes") < 6  # top-k, not a dump
    assert "[memory " in out  # rendered with source/date


def test_auto_recall_k_zero_and_embedder_down_fall_back(fake, monkeypatch):
    _recall_corpus()
    search.index_items()
    monkeypatch.setenv("INFER_RECALL_K", "0")
    assert search.recall_section("gpu box") is None
    monkeypatch.delenv("INFER_RECALL_K")
    monkeypatch.setenv("AI_EMBED_URL", _dead_url())
    assert search.recall_section("gpu box") is None


def test_auto_recall_falls_back_before_first_backfill(fake):
    _recall_corpus()  # embedder up, but nothing embedded yet
    assert search.recall_section("gpu box") is None


def _export(query, env_extra, cwd):
    env = dict(os.environ, **env_extra)
    args = [sys.executable, AI_MCP, "state-export", "memory"] + (["--query", query] if query else [])
    return subprocess.run(args, env=env, cwd=cwd, capture_output=True, text=True, timeout=30)


def test_state_export_memory_query_cli(fake, tmp_path):
    _recall_corpus()
    search.index_items()
    r = _export("ssh to the gpu box", {"AI_EMBED_URL": fake.url}, str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "buildbox" in r.stdout and "User prefers" in r.stdout
    assert r.stdout.count("tomatoes") < 6
    # embedder down -> the old full render (every line)
    r = _export("ssh to the gpu box", {"AI_EMBED_URL": _dead_url()}, str(tmp_path))
    assert r.stdout.count("tomatoes") == 40 and "buildbox" in r.stdout
    # no query -> unchanged full render
    r = _export(None, {"AI_EMBED_URL": fake.url}, str(tmp_path))
    assert r.stdout.count("tomatoes") == 40


def test_auto_recall_latency_small(fake):
    _recall_corpus()
    for i in range(400):
        state.chat_audit_add("zulip", "u", "question %d about pipelines and plots" % i, "answer %d" % i)
    search.index_items()
    search.recall_section("warm up imports")
    t0 = time.monotonic()
    search.recall_section("plot the pipeline results")
    assert time.monotonic() - t0 < 0.5  # measured ~30 ms here; generous for loaded CI


def test_startup_path_takes_no_write_lock(fake):
    _recall_corpus()
    search.index_items()
    c = state.connect()
    c.execute("BEGIN IMMEDIATE")  # another process holds the write lock
    try:
        t0 = time.monotonic()
        with state.lock_budget(1.5):
            out = search.recall_section("gpu box")
        assert time.monotonic() - t0 < 1.0 and "buildbox" in out
    finally:
        c.execute("ROLLBACK")
        c.close()


# ── (g) tool ─────────────────────────────────────────────────────────────────
def test_search_memory_tool_registered_and_dispatchable(fake):
    names = [t["function"]["name"] for t in registry.TOOL_SCHEMAS]
    assert "search_memory" in names
    assert names[-1] == "task_complete"
    assert "search_memory" in registry.CORE_TOOL_PROFILE
    assert "search_memory" in registry.HANDLERS
    _corpus()
    search.index_items()
    out = registry.dispatch("search_memory", "search_memory",
                            {"query": "deploying servers", "sources": "memory", "k": 2})
    assert "Ansible" in out and "memory" in out
    assert "Missing required" in registry.dispatch("search_memory", "search_memory", {})
    out = registry.dispatch("search_memory", "search_memory", {"query": "zzzqqq nothing"})
    assert "No matching" in out


def test_save_memory_pinned():
    from aitools.memory import save_memory
    save_memory("always answer in metric units", pinned=True)
    save_memory("transient fact")
    assert state.memory_pinned() == ["always answer in metric units"]
    out = registry.dispatch("save_memory", "save_memory", {"content": "transient fact", "pinned": True})
    assert "pinned" in out.lower()
    assert state.memory_pinned() == ["always answer in metric units", "transient fact"]


# ── ai binary: the memory section of the system prompt ──────────────────────
def _system_prompt(tmp_path, prompt, embed_url):
    sys.path.insert(0, os.path.join(REPO, "tests"))
    from test_offline import MockServer, run_binary
    cap = str(tmp_path / "cap.jsonl")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    with MockServer(cap, MOCK_TASK_COMPLETE="1") as srv:
        r = run_binary(str(home), srv.base_url, [prompt],
                       extra_env={"AI_EMBED_URL": embed_url, "INFER_SESSION_RECAP": "0"})
        reqs = srv.requests()
    assert reqs, r.stderr
    return reqs[0]["messages"][0]["content"]


def test_ai_binary_injects_recalled_memory(fake, tmp_path):
    if not os.path.exists(AI_BIN):
        pytest.skip("ai binary not built")
    _recall_corpus()
    search.index_items()
    marker = tmp_path / "pwned"
    sysmsg = _system_prompt(tmp_path, "what's the gpu box's name? $(touch %s); `touch %s`" % (marker, marker),
                            fake.url)
    assert "buildbox" in sysmsg and "User prefers concise" in sysmsg
    assert sysmsg.count("tomatoes") < 6
    assert not marker.exists()  # the query was shell-escaped
    assert any("gpu box" in t for t in fake.texts())


def test_ai_binary_full_memory_when_embedder_down(tmp_path):
    if not os.path.exists(AI_BIN):
        pytest.skip("ai binary not built")
    _recall_corpus()
    sysmsg = _system_prompt(tmp_path, "ssh to the gpu box", _dead_url())
    assert sysmsg.count("tomatoes") == 40 and "buildbox" in sysmsg


# ── ai-backend embed + service unit + install.sh ────────────────────────────
def _backend(args, env_extra, tmp_path):
    env = dict(os.environ, HOME=str(tmp_path / "home"), **env_extra)
    return subprocess.run([sys.executable, os.path.join(REPO, "ai-backend")] + args, env=env,
                          cwd=str(tmp_path), capture_output=True, text=True, timeout=30)


def test_ai_backend_embed_status_and_serve_dry_run(fake, tmp_path):
    r = _backend(["embed", "status"], {"AI_EMBED_URL": fake.url}, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "up" in r.stdout and fake.url in r.stdout
    r = _backend(["embed", "status"], {"AI_EMBED_URL": _dead_url()}, tmp_path)
    assert "down" in r.stdout
    r = _backend(["embed", "serve", "--dry-run"], {"AI_EMBED_URL": "http://127.0.0.1:8091"}, tmp_path)
    assert r.returncode == 0, r.stderr
    cmd = r.stdout
    for part in ("--embeddings", "--pooling mean", "-c 8192", "--port 8091", "--host 127.0.0.1",
                 "-ngl 0", "embeddinggemma-2-UD-Q4_K_XL.gguf", "llama.cpp-embed/build/bin/llama-server",
                 "CUDA_VISIBLE_DEVICES="):
        assert part in cmd, part
    threads = min(12, max(1, (os.cpu_count() or 2) // 2))
    assert "-t %d" % threads in cmd


def test_service_unit():
    p = os.path.join(REPO, "services", "ai-embed.service")
    with open(p) as f:
        text = f.read()
    assert "Restart=on-failure" in text and "Nice=10" in text and "CPUQuota" not in text
    assert "ai-backend embed serve" in text
    assert "CUDA_VISIBLE_DEVICES=" in text


def _stub(d, name, body):
    p = d / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def test_install_embed_builds_cpu_only_with_stubs(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"
    _stub(fake_bin, "git", 'echo "git $*" >> %s\nfor a; do last="$a"; done\n'
                           'mkdir -p "$last" && touch "$last/CMakeLists.txt"' % log)
    _stub(fake_bin, "cmake", 'echo "cmake $*" >> %s\n'
                             'case "$*" in *--build*) mkdir -p "$2/bin" && printf "#!/bin/sh\\n" > "$2/bin/llama-server"'
                             ' && chmod +x "$2/bin/llama-server";; esac' % log)
    _stub(fake_bin, "curl", 'echo "curl $*" >> %s\nwhile [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                            'echo gguf > "$out"' % log)
    for n in ("systemctl", "make", "pip", "sudo", "apt-get", "nvidia-smi", "nvcc"):
        _stub(fake_bin, n, 'echo "%s $*" >> %s' % (n, log))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("INFER_", "AI_EMBED", "LLAMA_"))}
    env.update(HOME=str(home), PATH="%s:/usr/bin:/bin" % fake_bin)
    r = subprocess.run(["bash", os.path.join(REPO, "install.sh"), "embed"], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    calls = log.read_text()
    src = home / ".local/share/ai/llama.cpp-embed"
    assert "git clone" in calls and "llama.cpp" in calls and str(src) in calls
    assert "-DGGML_CUDA=OFF" in calls and "-DLLAMA_CURL=OFF" in calls
    assert "--target llama-server" in calls
    assert (home / ".local/share/ai/models/embedding/embeddinggemma-2-UD-Q4_K_XL.gguf").exists()
    unit = home / ".config/systemd/user/ai-embed.service"
    assert unit.exists() and "ai-backend embed serve" in unit.read_text()
    assert "systemctl --user enable" not in calls and "systemctl --user start" not in calls
    assert not (home / ".local/share/ai/llama.cpp").exists()  # main build untouched
    # second run: everything present -> no clone, no build, no download
    log.write_text("")
    r = subprocess.run(["bash", os.path.join(REPO, "install.sh"), "embed"], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0
    calls = log.read_text()
    assert "git clone" not in calls and "cmake" not in calls and "curl" not in calls
    assert "ai-embed.service" in r.stdout


# ── retrieval eval set ───────────────────────────────────────────────────────
def test_retrieval_eval_set_runs_with_fake_embedder(tmp_path):
    with open(os.path.join(REPO, "evals", "retrieval", "pairs.json")) as f:
        data = json.load(f)
    assert len(data["pairs"]) >= 40
    out = tmp_path / "r.json"
    r = subprocess.run([sys.executable, os.path.join(REPO, "evals", "run_evals.py"), "--retrieval", "--fake",
                        "--json", str(out)], capture_output=True, text=True, timeout=180, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    rep = json.loads(out.read_text())
    assert rep["embedder"] == "fake" and rep["n"] == len(data["pairs"])
    for key in ("recall@1", "recall@5", "mrr"):
        assert 0.0 <= rep[key] <= 1.0
    assert rep["recall@5"] >= 0.5
