"""Retrieval eval for search_memory (aitools/search.py): `run_evals.py --retrieval`.

pairs.json holds synthetic items (memory / lesson / chat / note / history, no personal data) and
>= 40 query -> target-item pairs. The items are loaded into a throw-away state db + history index
(temp HOME), indexed with index_items(), and every query is run through search_memory (k=5) and,
as a baseline, keyword-only (FTS5). Reports recall@1, recall@5 and MRR.

Embedder: the real one (AI_EMBED_URL / --embed-url, default 127.0.0.1:8091) when it answers
/health, else tests/fixtures/fake_embedder.py (stem-overlap vectors: CI only proves the pipeline).
--fake forces the fake one.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PAIRS = os.path.join(HERE, "pairs.json")


def _load(state, history, items, hist_db):
    """Insert the items; returns {item id: (item_type, doc_id)}."""
    key = {}
    h = sqlite3.connect(hist_db)
    history._ensure_history_schema(h)
    seq = 0
    c = state.connect()
    try:
        for it in items:
            t, text = it["type"], it["text"]
            if t == "memory":
                state.memory_add(text, conn=c)
                rid = c.execute("SELECT id FROM memory WHERE text=?", (text,)).fetchone()[0]
                key[it["id"]] = ("memory", str(rid))
            elif t == "lesson":
                tool, _, body = text.partition(": ")
                state.lesson_upsert("PITFALL", tool, "eval:" + it["id"], body, conn=c)
                rid = c.execute("SELECT id FROM lessons WHERE signature=?", ("eval:" + it["id"],)).fetchone()[0]
                key[it["id"]] = ("lesson", str(rid))
            elif t == "chat":
                prompt, _, reply = text.partition("\n")
                key[it["id"]] = ("chat", str(state.chat_audit_add("eval", "user", prompt, reply, conn=c)))
            elif t == "note":
                with state.transaction(c):
                    cur = c.execute("INSERT INTO memories(content, metadata) VALUES (?, '')", (text,))
                key[it["id"]] = ("note", "r%d" % cur.lastrowid)
            elif t == "history":
                seq += 1
                user, _, asst = text.partition("\n")
                history._index_turn_row(h, seq, {"prompt": user.replace("USER: ", "", 1),
                                                 "response": asst.replace("ASSISTANT: ", "", 1),
                                                 "timestamp": "2026-01-%02dT12:00:00" % seq,
                                                 "session_id": "eval"}, seq)
                key[it["id"]] = ("history_chunk", str(seq))
            else:
                raise ValueError("unknown item type %r" % t)
        h.commit()
    finally:
        c.close()
        h.close()
    return key


def _score(search, pairs, key, use_vectors):
    r1 = r5 = mrr = 0.0
    misses = []
    for p in pairs:
        res = search.search_memory(p["query"], k=5, use_vectors=use_vectors)
        got = [(r["item_type"], r["item_id"]) for r in res]
        want = key[p["target"]]
        if want in got:
            rank = got.index(want) + 1
            r1 += rank == 1
            r5 += 1
            mrr += 1.0 / rank
        else:
            misses.append(p["query"])
    n = float(len(pairs))
    return {"recall@1": round(r1 / n, 3), "recall@5": round(r5 / n, 3), "mrr": round(mrr / n, 3)}, misses


def run(fake=False, embed_url=None, json_path=None, quiet=False):
    sys.path.insert(0, REPO)
    sys.path.insert(0, os.path.join(REPO, "tests", "fixtures"))
    with open(PAIRS, encoding="utf-8") as f:
        data = json.load(f)
    tmp = tempfile.mkdtemp(prefix="ai-retrieval-eval-")
    os.environ["AI_STATE_DB"] = os.path.join(tmp, "state.db")
    os.environ["INFER_EMBED_AUTODRAIN"] = "0"
    if embed_url:
        os.environ["AI_EMBED_URL"] = embed_url
    from aitools import embed, history, search, state
    history.HISTORY_DB = os.path.join(tmp, "history_index.db")
    srv = None
    kind = "real"
    if fake or not embed.health(timeout=1.0):
        import fake_embedder
        srv = fake_embedder.start()
        os.environ["AI_EMBED_URL"] = srv.url
        kind = "fake"
    try:
        key = _load(state, history, data["items"], history.HISTORY_DB)
        t0 = time.monotonic()
        stats = search.index_items()
        index_s = time.monotonic() - t0
        t0 = time.monotonic()
        hybrid, misses = _score(search, data["pairs"], key, True)
        query_ms = (time.monotonic() - t0) * 1000.0 / max(1, len(data["pairs"]))
        fts, _ = _score(search, data["pairs"], key, False)
    finally:
        if srv is not None:
            srv.stop()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    report = dict(hybrid, embedder=kind, url=os.environ.get("AI_EMBED_URL"), n=len(data["pairs"]),
                  items=len(data["items"]), fts_only=fts, index_stats=stats, index_seconds=round(index_s, 2),
                  ms_per_query=round(query_ms, 1), misses=misses)
    if not quiet:
        print("retrieval eval (%s embedder, %d queries over %d items)" % (kind, report["n"], report["items"]))
        print("  hybrid:   R@1 %.3f  R@5 %.3f  MRR %.3f   (%.1f ms/query)" % (
            hybrid["recall@1"], hybrid["recall@5"], hybrid["mrr"], query_ms))
        print("  FTS only: R@1 %.3f  R@5 %.3f  MRR %.3f" % (fts["recall@1"], fts["recall@5"], fts["mrr"]))
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    return report
