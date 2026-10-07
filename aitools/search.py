"""Hybrid semantic search over memories, lessons, chats, history and notes, plus auto-recall.

search_memory(query, sources=None, since=None, k=8) -> [dict(item_type, item_id, text, score,
source_ts, match)] is reciprocal-rank fusion (RRF, k=60) of
  (a) SQLite FTS5 BM25: item_fts (memory/lesson/chat rows, trigger-maintained in the state db),
      memories + vault_fts (notes, state db) and history_fts (the history_index.db cache);
  (b) vectors: cosine on the 128-d Matryoshka prefix over every candidate, the top 50 reranked
      with the full 768-d vector (one document = up to MAX_CHUNKS chunks; best chunk wins).
Pure stdlib (struct + math.sumprod); numpy is used only for very large candidate sets (its import
costs more than the scan below ~20k vectors). Without the embedder it is FTS only. Read-only:
it never takes the write lock, so it is safe on the ai startup path.

Item types: memory (state.memory rows), lesson (approved, unexpired lessons only), chat
(chat_audit), history_chunk (history_fts rows: turns and sessions), note (remember/recall
`memories` rows as r<rowid>, vault notes as v:<title>). A chunk past the first has the id
<id>#<n>; results report the document id plus the best chunk's text.

index_items(limit=None) drains embed_queue and (re-)embeds every item whose content hash changed
(`ai_mcp.py embed-backfill [--limit N]`). recall_section(query) renders the system-prompt memory
section: pinned memories (<= 1 KB) + the top INFER_RECALL_K (default 6, 0 disables) results;
None means "fall back to the full memory render" (disabled, the embedder is down, or nothing has
been embedded yet).
"""
import contextlib
import datetime
import hashlib
import heapq
import math
import os
import re
import sqlite3
import struct
import time

from . import embed, history, state

TYPES = ("memory", "lesson", "chat", "history_chunk", "note")
_SOURCE_ALIASES = {"memory": "memory", "memories": "memory", "lesson": "lesson", "lessons": "lesson",
                   "chat": "chat", "chats": "chat", "zulip": "chat", "history": "history_chunk",
                   "history_chunk": "history_chunk", "sessions": "history_chunk", "note": "note",
                   "notes": "note", "vault": "note"}
CHUNK_CHARS = 2000
MAX_CHUNKS = 4
RERANK_TOP = 50
FTS_TOP = 50
RRF_K = 60
BATCH = 8
NUMPY_MIN_ROWS = 20000
MIN_SIM_DEFAULT = 0.2  # vector hits below this cosine are noise, not recall ($INFER_SEARCH_MIN_SIM)
RECALL_K_DEFAULT = 6
RECALL_BUDGET_BYTES = 4096
PINNED_BUDGET_BYTES = 1024
RECALL_ITEM_CHARS = 300
TEXT_CHARS = 1500


# ── helpers ──────────────────────────────────────────────────────────────────
def parse_sources(sources):
    """None/'' -> all types; accepts a list or a comma string of names/aliases."""
    if not sources:
        return list(TYPES)
    if isinstance(sources, str):
        sources = re.split(r"[,\s]+", sources)
    out = []
    for s in sources:
        t = _SOURCE_ALIASES.get(str(s).strip().lower())
        if t and t not in out:
            out.append(t)
    return out or list(TYPES)


def parse_since(since, now=None):
    """Epoch seconds from a number, 'Nd'/'Nh'/'Nw' (ago) or an ISO date/datetime; None if unset."""
    if since is None or since == "":
        return None
    if isinstance(since, (int, float)):
        return float(since)
    s = str(since).strip().lower()
    now = time.time() if now is None else now
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdw])", s)
    if m:
        return now - float(m.group(1)) * {"h": 3600, "d": 86400, "w": 7 * 86400}[m.group(2)]
    try:
        return float(s)
    except ValueError:
        pass
    ts = _iso_ts(s)
    if ts is None:
        raise ValueError("since must be an ISO date, epoch seconds or e.g. '7d'")
    return ts


def _iso_ts(s):
    try:
        d = datetime.datetime.fromisoformat(str(s).strip().replace("z", "+00:00").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.timestamp()


def _history_ts(ts):
    s = str(ts or "").strip()
    try:
        v = float(s)
        return v if v > 1e8 else None  # small ints are history.jsonl line numbers
    except ValueError:
        return _iso_ts(s)


def chunks(text):
    """Split into <= CHUNK_CHARS pieces at whitespace, at most MAX_CHUNKS of them."""
    text = str(text or "").strip()
    out = []
    while text and len(out) < MAX_CHUNKS:
        if len(text) <= CHUNK_CHARS:
            out.append(text)
            break
        cut = text.rfind(" ", CHUNK_CHARS // 2, CHUNK_CHARS)
        cut = cut if cut > 0 else CHUNK_CHARS
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    return out


def _chunk_id(doc_id, i):
    return str(doc_id) if i == 0 else "%s#%d" % (doc_id, i)


def _doc_of(item_id):
    doc, _, n = str(item_id).rpartition("#")
    return (doc, int(n)) if doc and n.isdigit() else (str(item_id), 0)


def _hash(title, text):
    return hashlib.sha1(("%s\0%s\0%s" % (embed.model_id(), title or "", text)).encode("utf-8", "replace")).hexdigest()


def _fts_query(query):
    toks = []
    for t in re.findall(r"\w+", str(query or "").lower()):
        if t not in toks:
            toks.append(t)
    return " OR ".join('"%s"' % t for t in toks[:24])


def _history_db():
    return history.HISTORY_DB


@contextlib.contextmanager
def _history_conn():
    """Read-only connection to the history index cache, or None when it does not exist."""
    p = _history_db()
    c = None
    if os.path.exists(p):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=0.5)
            c.execute("SELECT 1 FROM history_fts LIMIT 1")
        except sqlite3.Error:
            if c is not None:
                c.close()
            c = None
    try:
        yield c
    finally:
        if c is not None:
            c.close()


# ── items ────────────────────────────────────────────────────────────────────
def _lesson_text(kind, tool, body):
    return "%s %s: %s" % (kind or "NOTE", tool or "", body or "")


def _chat_text(prompt, reply):
    return "%s\n%s" % (prompt or "", reply or "")


def _iter_docs(c, types):
    """(item_type, doc_id, title, text, source_ts) for every indexable item of `types`, plus the
    set of types whose source was unavailable (their vectors must not be treated as orphans)."""
    docs, unavailable = [], set()
    if "memory" in types:
        docs += [("memory", str(r[0]), None, r[1], r[2]) for r in c.execute("SELECT id, text, created FROM memory")]
    if "lesson" in types:
        docs += [("lesson", str(r[0]), None, _lesson_text(r[1], r[2], r[3]), r[4])
                 for r in c.execute("SELECT id, kind, tool, body, created FROM lessons")]
    if "chat" in types:
        docs += [("chat", str(r[0]), None, _chat_text(r[1], r[2]), r[3])
                 for r in c.execute("SELECT id, prompt, reply, ts FROM chat_audit")]
    if "note" in types:
        try:
            docs += [("note", "r%d" % r[0], None, r[1], None) for r in c.execute("SELECT rowid, content FROM memories")]
            docs += [("note", "v:%s" % r[0], r[0], r[1], None) for r in c.execute("SELECT title, content FROM vault_fts")]
        except sqlite3.OperationalError:
            unavailable.add("note")
    if "history_chunk" in types:
        with _history_conn() as h:
            if h is None:
                unavailable.add("history_chunk")
            else:
                docs += [("history_chunk", str(r[0]), None, r[2], _history_ts(r[1]))
                         for r in h.execute("SELECT rowid, ts, body FROM history_fts")]
    return [d for d in docs if str(d[3] or "").strip()], unavailable


def _hydrate(c, h, item_type, doc_id, now):
    """(text, source_ts) of one live item, or None if it is gone or must not surface."""
    try:
        if item_type == "memory":
            r = c.execute("SELECT text, created FROM memory WHERE id=?", (int(doc_id),)).fetchone()
            return (r[0], r[1]) if r else None
        if item_type == "lesson":
            r = c.execute("SELECT kind, tool, body, created FROM lessons WHERE id=? AND status='approved' "
                          "AND (expires IS NULL OR expires > ?)", (int(doc_id), now)).fetchone()
            return (_lesson_text(r[0], r[1], r[2]), r[3]) if r else None
        if item_type == "chat":
            r = c.execute("SELECT prompt, reply, ts FROM chat_audit WHERE id=?", (int(doc_id),)).fetchone()
            return (_chat_text(r[0], r[1]).strip(), r[2]) if r else None
        if item_type == "note":
            if doc_id.startswith("r"):
                r = c.execute("SELECT content FROM memories WHERE rowid=?", (int(doc_id[1:]),)).fetchone()
            else:
                r = c.execute("SELECT content FROM vault_fts WHERE title=? LIMIT 1", (doc_id[2:],)).fetchone()
            return (r[0], None) if r else None
        if item_type == "history_chunk" and h is not None:
            r = h.execute("SELECT body, ts FROM history_fts WHERE rowid=?", (int(doc_id),)).fetchone()
            return (r[0], _history_ts(r[1])) if r else None
    except (sqlite3.Error, ValueError):
        return None
    return None


# ── search ───────────────────────────────────────────────────────────────────
def _fts_hits(c, h, query, types):
    q = _fts_query(query)
    if not q:
        return []
    hits = []

    def run(conn, sql, params, mk):
        try:
            hits.extend((s, mk(r)) for r in conn.execute(sql, params) for s in (r[-1],))
        except sqlite3.Error:
            pass

    plain = [t for t in types if t in ("memory", "lesson", "chat")]
    if plain:
        run(c, "SELECT item_type, item_id, bm25(item_fts) FROM item_fts WHERE item_fts MATCH ? AND item_type IN "
            "(%s) ORDER BY 3 LIMIT %d" % (",".join("?" * len(plain)), FTS_TOP), [q] + plain,
            lambda r: (r[0], str(r[1])))
    if "note" in types:
        run(c, "SELECT rowid, bm25(memories) FROM memories WHERE memories MATCH ? ORDER BY 2 LIMIT %d" % FTS_TOP,
            [q], lambda r: ("note", "r%d" % r[0]))
        run(c, "SELECT title, bm25(vault_fts) FROM vault_fts WHERE vault_fts MATCH ? ORDER BY 2 LIMIT %d" % FTS_TOP,
            [q], lambda r: ("note", "v:%s" % r[0]))
    if "history_chunk" in types and h is not None:
        run(h, "SELECT rowid, bm25(history_fts) FROM history_fts WHERE history_fts MATCH ? ORDER BY 2 LIMIT %d"
            % FTS_TOP, ["{prompt response body} : (%s)" % q], lambda r: ("history_chunk", str(r[0])))
    hits.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _, key in hits:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:FTS_TOP]


def _dot_scores(blobs, qv, dim):
    """Cosine (dot of unit vectors) of qv against float16 blobs."""
    if len(blobs) >= NUMPY_MIN_ROWS:
        try:
            import numpy as np
            m = np.frombuffer(b"".join(blobs), dtype="<f2").reshape(len(blobs), dim).astype(np.float32)
            return (m @ np.asarray(qv, dtype=np.float32)).tolist()
        except Exception:
            pass
    fmt = struct.Struct("<%de" % dim)
    sp = getattr(math, "sumprod", None) or (lambda a, b: sum(x * y for x, y in zip(a, b)))
    return [sp(fmt.unpack(b), qv) for b in blobs]


def _vector_hits(c, qvec, types, since):
    """Ranked [(doc key, best chunk item_id)] by cosine: coarse v128 over all, rerank top v768."""
    q128 = embed.truncate(qvec, embed.COARSE_DIM)
    q768 = embed.truncate(qvec, embed.DIM)
    sql = ("SELECT item_type, item_id, v128 FROM vectors WHERE model=? AND item_type IN (%s)"
           % ",".join("?" * len(types)))
    params = [embed.model_id()] + list(types)
    if since is not None:
        sql += " AND source_ts >= ?"
        params.append(since)
    try:
        rows = [r for r in c.execute(sql, params) if r[2] and len(r[2]) == embed.COARSE_DIM * 2]
    except sqlite3.Error:
        return []
    if not rows:
        return []
    coarse = _dot_scores([r[2] for r in rows], q128, embed.COARSE_DIM)
    top = heapq.nlargest(RERANK_TOP, range(len(rows)), key=coarse.__getitem__)
    best = {}
    for i in top:
        t, iid = rows[i][0], rows[i][1]
        r = c.execute("SELECT v768 FROM vectors WHERE item_type=? AND item_id=?", (t, iid)).fetchone()
        if r and r[0] and len(r[0]) == len(q768) * 2:
            s = _dot_scores([r[0]], q768, len(q768))[0]
        else:
            s = coarse[i]
        key = (t, _doc_of(iid)[0])
        if key not in best or s > best[key][0]:
            best[key] = (s, iid)
    floor = min_sim()
    return [(k, v[1]) for k, v in sorted(best.items(), key=lambda kv: -kv[1][0]) if v[0] >= floor]


def min_sim():
    try:
        return float(os.environ.get("INFER_SEARCH_MIN_SIM", MIN_SIM_DEFAULT))
    except ValueError:
        return MIN_SIM_DEFAULT


def search(query, sources=None, since=None, k=8, use_vectors=True, exclude=()):
    """search_memory plus info: returns (results, {"vector": bool, "fts": int, "vec": int})."""
    types = parse_sources(sources)
    since_ts = parse_since(since)
    k = max(1, min(int(k or 8), 50))
    qvec = None
    if use_vectors and str(query or "").strip():
        got = embed.embed([str(query)], "query")
        qvec = got[0] if got else None
    now = time.time()
    with state._conn(read=True) as c, _history_conn() as h:
        fts = _fts_hits(c, h, query, types)
        vec = _vector_hits(c, qvec, types, since_ts) if qvec else []
        fused, chunk_of, via = {}, {}, {}
        for rank, key in enumerate(fts, 1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
            via.setdefault(key, []).append("fts")
        for rank, (key, iid) in enumerate(vec, 1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
            via.setdefault(key, []).append("vec")
            chunk_of[key] = iid
        out = []
        for key in sorted(fused, key=lambda x: -fused[x]):
            if key in exclude:
                continue
            got = _hydrate(c, h, key[0], key[1], now)
            if got is None:
                continue
            text, ts = got
            if since_ts is not None and (ts is None or ts < since_ts):
                continue
            piece = chunks(text)
            n = _doc_of(chunk_of.get(key, key[1]))[1]
            text = piece[n] if n < len(piece) else (piece[0] if piece else str(text))
            out.append({"item_type": key[0], "item_id": key[1], "text": text[:TEXT_CHARS],
                        "score": round(fused[key], 6), "source_ts": ts, "match": via[key]})
            if len(out) >= k:
                break
        try:
            indexed = c.execute("SELECT 1 FROM vectors LIMIT 1").fetchone() is not None
        except sqlite3.Error:
            indexed = False
    return out, {"vector": qvec is not None, "indexed": indexed, "fts": len(fts), "vec": len(vec)}


def search_memory(query, sources=None, since=None, k=8, use_vectors=True):
    """Hybrid (FTS5 + vector) search; FTS only when the embedder is down. Never raises on an
    unreachable embedder; raises ValueError on a malformed `since`."""
    return search(query, sources=sources, since=since, k=k, use_vectors=use_vectors)[0]


def _date(ts):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts))) if ts else "undated"
    except (TypeError, ValueError, OverflowError):
        return "undated"


_LABEL = {"history_chunk": "history"}


def format_results(results, vector_ok=True):
    """The search_memory tool's text output."""
    if not results:
        return "No matching memories, lessons, chats, history or notes."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append("[%d] %s %s (%s) score=%.4f via %s\n%s" % (
            i, _LABEL.get(r["item_type"], r["item_type"]), r["item_id"], _date(r["source_ts"]), r["score"],
            "+".join(r.get("match") or []), r["text"][:800]))
    if not vector_ok:
        lines.append("(semantic index unavailable: keyword matches only)")
    return "\n\n".join(lines)


# ── auto-recall (system prompt memory section) ───────────────────────────────
def recall_k():
    try:
        return int(os.environ.get("INFER_RECALL_K", RECALL_K_DEFAULT))
    except ValueError:
        return RECALL_K_DEFAULT


def _one_line(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _fit(lines, budget):
    """Leading lines whose joined UTF-8 size fits budget."""
    out, used = [], 0
    for ln in lines:
        b = len(ln.encode("utf-8")) + (1 if out else 0)
        if used + b > budget:
            break
        out.append(ln)
        used += b
    return out


def recall_section(query, k=None, budget=RECALL_BUDGET_BYTES):
    """Pinned memories + the top-k recalled items for `query`, or None to fall back to the full
    memory render (recall disabled, empty query, embedder down, or any error)."""
    k = recall_k() if k is None else int(k)
    if k <= 0 or not str(query or "").strip():
        return None
    try:
        with state._conn(read=True) as c:
            try:
                pinned_rows = [(str(r[0]), r[1]) for r in c.execute("SELECT id, text FROM memory WHERE pinned=1 "
                                                                    "ORDER BY id")]
            except sqlite3.OperationalError:
                pinned_rows = []
        results, info = search(query, k=k, exclude={("memory", i) for i, _ in pinned_rows})
    except Exception:
        return None
    if not info["vector"] or not info["indexed"]:
        return None  # embedder down, or nothing embedded yet (before the first embed-backfill)
    pinned = list(reversed(_fit([t for _, t in reversed(pinned_rows)], PINNED_BUDGET_BYTES)))
    parts = []
    if pinned:
        parts += ["Pinned:"] + ["- " + _one_line(t, PINNED_BUDGET_BYTES) for t in pinned]
    recalled = ["- [%s %s] %s" % (_LABEL.get(r["item_type"], r["item_type"]), _date(r["source_ts"]),
                                  _one_line(r["text"], RECALL_ITEM_CHARS)) for r in results]
    if recalled:
        head = "Recalled for this request (source, date; use search_memory for more):"
        used = len("\n".join(parts).encode("utf-8")) + (1 if parts else 0) + len(head.encode("utf-8")) + 1
        fitted = _fit(recalled, max(0, budget - used))
        if fitted:
            parts += [head] + fitted
    return "\n".join(_fit(parts, budget))


# ── indexing ─────────────────────────────────────────────────────────────────
def queue_size():
    try:
        with state._conn(read=True) as c:
            return c.execute("SELECT count(*) FROM embed_queue").fetchone()[0]
    except Exception:
        return 0


def index_items(limit=None, types=None):
    """Drain embed_queue and (re-)embed every item whose content changed, queued items first.

    Unchanged chunks (same content+model hash) are skipped; a chunk whose hash already exists
    under another id (a rebuilt history index) is copied instead of re-embedded; vectors of items
    that no longer exist are removed. `limit` caps the number of chunks embedded in this call.
    Returns stats: embedded, copied, unchanged, removed, queue_left, embedder ('up'|'down')."""
    types = parse_sources(types)
    stats = {"embedded": 0, "copied": 0, "unchanged": 0, "removed": 0, "queue_left": 0, "embedder": "up"}
    model = embed.model_id()
    c = state.connect()
    try:
        existing = {(r[0], r[1]): r[2] for r in c.execute(
            "SELECT item_type, item_id, hash FROM vectors WHERE model=?", (model,))}
        by_hash = {h: key for key, h in existing.items() if h}
        queued = {(r[0], str(r[1])) for r in c.execute("SELECT item_type, item_id FROM embed_queue")}
        docs, unavailable = _iter_docs(c, types)
        docs.sort(key=lambda d: (d[0], d[1]) not in queued)
        doc_chunks = {}
        for key in existing:
            d, n = _doc_of(key[1])
            doc_chunks.setdefault((key[0], d), []).append((n, key))
        live, todo, stale = set(), [], []
        for t, did, title, text, ts in docs:
            live.add((t, did))
            pieces = chunks(text)
            for i, piece in enumerate(pieces):
                key = (t, _chunk_id(did, i))
                h = _hash(title, piece)
                if existing.get(key) == h:
                    stats["unchanged"] += 1
                else:
                    todo.append((key, title, piece, h, ts))
            stale += [key for n, key in doc_chunks.get((t, did), ()) if n >= len(pieces)]
        stale += [key for dk, lst in doc_chunks.items() if dk[0] in types and dk[0] not in unavailable
                  and dk not in live for _, key in lst]
        # vectors from another model are stale too (re-embedded above via the hash/model filter)
        with state.transaction(c):
            for key in set(stale):
                c.execute("DELETE FROM vectors WHERE item_type=? AND item_id=?", key)
            c.execute("DELETE FROM vectors WHERE model IS NOT ? AND item_type IN (%s)"
                      % ",".join("?" * len(types)), [model] + list(types))
        stats["removed"] = len(set(stale))

        def write(rows):
            with state.transaction(c):
                for key, h, ts, v in rows:
                    c.execute("INSERT OR REPLACE INTO vectors(item_type, item_id, model, dim, v128, v768, updated, "
                              "hash, source_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (key[0], key[1], model, len(v), embed.to_blob(embed.truncate(v, embed.COARSE_DIM)),
                               embed.to_blob(embed.truncate(v, embed.DIM)), time.time(), h, ts))

        pending = set()
        need = []
        for item in todo:
            src = by_hash.get(item[3])
            if src is not None and src != item[0]:
                r = c.execute("SELECT v128, v768, dim FROM vectors WHERE item_type=? AND item_id=?", src).fetchone()
                if r:
                    with state.transaction(c):
                        c.execute("INSERT OR REPLACE INTO vectors(item_type, item_id, model, dim, v128, v768, "
                                  "updated, hash, source_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                  (item[0][0], item[0][1], model, r[2], r[0], r[1], time.time(), item[3], item[4]))
                    stats["copied"] += 1
                    continue
            need.append(item)
        if limit is not None:
            for item in need[max(0, int(limit)):]:
                pending.add((item[0][0], _doc_of(item[0][1])[0]))
            need = need[:max(0, int(limit))]
        if need and not embed.health(timeout=2.0):
            stats["embedder"] = "down"
            pending.update((it[0][0], _doc_of(it[0][1])[0]) for it in need)
            need = []
        for i in range(0, len(need), BATCH):
            batch = need[i:i + BATCH]
            vecs = embed.embed([b[2] for b in batch], "document", titles=[b[1] for b in batch])
            if vecs is None:
                stats["embedder"] = "down"
                pending.update((it[0][0], _doc_of(it[0][1])[0]) for it in need[i:])
                break
            write([(b[0], b[3], b[4], v) for b, v in zip(batch, vecs)])
            stats["embedded"] += len(batch)
        with state.transaction(c):
            for key in queued:
                if key not in pending:
                    c.execute("DELETE FROM embed_queue WHERE item_type=? AND item_id=?", key)
        stats["queue_left"] = c.execute("SELECT count(*) FROM embed_queue").fetchone()[0]
    finally:
        c.close()
    return stats


def spawn_drain(limit=64):
    """Start a detached `ai_mcp.py embed-backfill --limit N` when items are queued (called after
    the auto-recall output is printed, so it never delays startup). INFER_EMBED_AUTODRAIN=0
    disables it; the backfill itself is serialised by a lock file."""
    if os.environ.get("INFER_EMBED_AUTODRAIN", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    if not queue_size():
        return False
    import subprocess
    import sys
    from . import entry_script
    try:
        subprocess.Popen([sys.executable, entry_script(), "embed-backfill", "--limit", str(int(limit)), "--quiet"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
        return True
    except Exception:
        return False


def backfill_locked(limit=None):
    """index_items under an exclusive non-blocking lock next to the state db. Returns the stats,
    or None when another backfill is already running."""
    import fcntl
    lock = state.db_path() + ".embed.lock"
    os.makedirs(os.path.dirname(lock) or ".", exist_ok=True)
    with open(lock, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        return index_items(limit=limit)
