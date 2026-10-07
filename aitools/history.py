"""Conversation-history index (SQLite FTS over history.jsonl + saved sessions) and Zulip chat logs.

history_index.db is a derived, rebuildable CACHE (rebuild_history_index), not state; durable state
lives in the state db (aitools/state.py), whose `sessions` table is updated as files are indexed."""
import os
import json
import re
import time
import sqlite3

# ── Searchable conversation history (backup + FTS index) ────────────────────
# Every conversation is backed up to BOTH the cache (fast, ~/.cache/ai/sessions)
# and the persistent local user-data dir (~/.local/share/ai/sessions), and each
# turn is appended to ~/.cache/ai/history.jsonl with a session_id. This module
# builds a fast SQLite FTS5 index over all of it so the agent can search and
# learn from past conversations quickly.

CACHE_SESSIONS = os.path.expanduser("~/.cache/ai/sessions")
DATA_SESSIONS = os.path.expanduser("~/.local/share/ai/sessions")
HISTORY_LOG = os.path.expanduser("~/.cache/ai/history.jsonl")
HISTORY_DB = os.path.expanduser("~/.local/share/ai/history_index.db")


def _session_dirs():
    """Session dirs that exist, de-duplicated (cache + persistent may alias)."""
    seen = set()
    out = []
    for d in (CACHE_SESSIONS, DATA_SESSIONS):
        if os.path.isdir(d) and d not in seen:
            seen.add(d)
            out.append(d)
    return out


# FTS rowid base for session rows: turn rows use small seq rowids (1..N);
# session rows use BASE + history_sessions.rowid so the two never collide.
_SESSION_ROWID_BASE = 1_000_000


def _ensure_history_schema(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    # Session archive: one row per session id (dedup across cache + persistent
    # dirs). `raw` keeps the original messages JSON so the index doubles as a
    # durable archive — get_session can serve a transcript even after the
    # on-disk session files were pruned.
    conn.execute("""CREATE TABLE IF NOT EXISTS history_sessions(
        session_id TEXT PRIMARY KEY, raw TEXT, body TEXT, turns INTEGER,
        size INTEGER, mtime REAL, fts_rowid INTEGER, prune_source INTEGER)""")
    # Regular (content-storing) FTS5 table: one row per turn (rowid=seq) and
    # one row per session (rowid=BASE+archive rowid).
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
        source, session_id, ts, prompt, response, body)""")
    conn.execute("CREATE TABLE IF NOT EXISTS hmeta(k TEXT PRIMARY KEY, v TEXT)")
    conn.commit()


def _assistant_text(m):
    """Text of an assistant message. Tool-call messages have content=null, so
    fall back to the task_complete summary when one is present."""
    txt = m.get("content", "")
    if isinstance(txt, list):
        txt = " ".join(str(c.get("text", "")) for c in txt if isinstance(c, dict))
    txt = str(txt or "").strip()
    if txt:
        return txt
    for tc in (m.get("tool_calls") or []):
        fn = (tc or {}).get("function", {}) if isinstance(tc, dict) else {}
        if fn.get("name") == "task_complete":
            try:
                a = json.loads(fn.get("arguments") or "{}")
                for k in ("summary", "text", "response", "result", "final_answer", "message", "answer"):
                    v = a.get(k)
                    if v and str(v).strip():
                        return str(v).strip()
            except Exception:
                continue
    return ""


def _transcript_body(messages):
    """Readable USER/ASSISTANT text of a raw messages array (for the FTS body)."""
    parts = []
    last_user = ""
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        if role == "user":
            txt = m.get("content", "")
            if isinstance(txt, list):
                txt = " ".join(str(c.get("text", "")) for c in txt if isinstance(c, dict))
            txt = str(txt or "").strip()
            if txt:
                last_user = txt
                parts.append("USER: " + txt)
        elif role == "assistant":
            txt = _assistant_text(m)
            if txt:
                parts.append("ASSISTANT: " + txt)
    return "\n".join(parts), last_user


def _hmeta_int(conn, k, default=0):
    try:
        row = conn.execute("SELECT v FROM hmeta WHERE k=?", (k,)).fetchone()
        return int(row[0]) if row else default
    except Exception:
        return default


def _hmeta_set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO hmeta(k, v) VALUES (?, ?)", (k, str(v)))


def _log_line_count():
    """Fast absolute line count of history.jsonl (bytes scan, no JSON parse)."""
    if not os.path.isfile(HISTORY_LOG):
        return 0
    try:
        with open(HISTORY_LOG, "rb") as f:
            data = f.read()
        return data.count(b"\n")
    except Exception:
        return 0


def _iter_history_log_lines():
    """Parsed records from history.jsonl (skip-blank, skip-unparseable)."""
    records = []
    if not os.path.isfile(HISTORY_LOG):
        return records
    try:
        with open(HISTORY_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return records


def _index_turn_row(conn, seq, rec, line_no):
    """Insert one history.jsonl record as an FTS turn row. Returns True if inserted."""
    prompt = str(rec.get("prompt", "") or "")
    response = str(rec.get("response", "") or "")
    if not prompt and not response:
        return False
    sid = rec.get("session_id", "") or "unknown"
    ts = str(rec.get("timestamp", "") or "") or str(line_no)
    conn.execute(
        "INSERT INTO history_fts(rowid, source, session_id, ts, prompt, response, body) "
        "VALUES (?, 'turn', ?, ?, ?, ?, ?)",
        (seq, sid, ts, prompt, response, prompt + "\n" + response))
    return True


def _upsert_session_row(conn, sid, raw, body, prompt, turns, size, mtime,
                        prune_source=1):
    """Insert/replace the archive row + its FTS row for one session id.
    prune_source: 1 = row came from a disk file (age-prunable if opt-in set),
    0 = preserved archive copy (never age-pruned). Replaces any prior FTS row
    for the same id so a session is indexed once."""
    if len(raw) > 120_000:
        raw = raw[:120_000] + "...[raw archive truncated]"
    old = conn.execute(
        "SELECT fts_rowid FROM history_sessions WHERE session_id=?", (sid,)).fetchone()
    if old and old[0]:
        conn.execute("DELETE FROM history_fts WHERE rowid=?", (old[0],))
        conn.execute("DELETE FROM history_sessions WHERE session_id=?", (sid,))
    cur = conn.execute(
        "INSERT INTO history_sessions(session_id, raw, body, turns, size, mtime, fts_rowid, prune_source) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (sid, raw, body[:8000], turns, size, float(mtime), 0, prune_source))
    fts_rowid = _SESSION_ROWID_BASE + cur.lastrowid
    conn.execute(
        "UPDATE history_sessions SET fts_rowid=? WHERE session_id=?", (fts_rowid, sid))
    conn.execute(
        "INSERT INTO history_fts(rowid, source, session_id, ts, prompt, response, body) "
        "VALUES (?, 'session', ?, ?, ?, ?, ?)",
        (fts_rowid, sid, str(mtime), prompt, body[:240], body))
    return fts_rowid


def _index_session_file(conn, path):
    """(Re)index one session file into the archive + FTS. Returns True when the
    session is present (a no-op when its size/mtime fingerprint is unchanged)."""
    sid = os.path.basename(path)[:-5]
    st = os.stat(path)
    row = conn.execute(
        "SELECT size, mtime FROM history_sessions WHERE session_id=?", (sid,)).fetchone()
    if row is not None and row[0] == st.st_size and abs((row[1] or 0) - st.st_mtime) < 1e-6:
        return True  # unchanged
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        messages = json.loads(content)
        if not isinstance(messages, list):
            raise ValueError("not a messages array")
    except Exception:
        return row is not None  # keep archive copy if the file is mid-rewrite
    body, last_user = _transcript_body(messages)
    _upsert_session_row(conn, sid, content, body, last_user, len(messages), st.st_size, st.st_mtime)
    from .sessions import record_session  # sessions index in the state db (this file is a cache)
    record_session(sid, path=path, ended=st.st_mtime, summary=last_user)
    return True


def _sync_history_index(conn):
    """Incrementally bring the index up to date (cheap path): append new log
    lines past the recorded offset, upsert only changed session files.
    Returns (n_new_turns, n_sessions)."""
    n_new = 0
    log_off = _hmeta_int(conn, "log_off")
    seq = _hmeta_int(conn, "seq")
    if os.path.isfile(HISTORY_LOG):
        try:
            with open(HISTORY_LOG, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if i <= log_off:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    seq += 1
                    if _index_turn_row(conn, seq, rec, i):
                        n_new += 1
        except Exception:
            pass
    _hmeta_set(conn, "log_off", _log_line_count())
    _hmeta_set(conn, "seq", seq)

    n_sessions = 0
    for d in _session_dirs():
        try:
            entries = sorted(os.listdir(d))
        except Exception:
            continue
        for fn in entries:
            if not fn.endswith(".json"):
                continue
            if _index_session_file(conn, os.path.join(d, fn)):
                n_sessions += 1
    _hmeta_set(conn, "sess_count", n_sessions)
    return n_new, n_sessions


def _history_needs_full_rebuild():
    """A wipe-and-rebuild is required only when: no DB, the index predates the
    current schema (no fingerprint keys), or the log shrank (truncated/rotated).
    Pure appends and session-file churn are handled incrementally instead."""
    if not os.path.isfile(HISTORY_DB):
        return True
    try:
        conn = sqlite3.connect(HISTORY_DB)
        try:
            if not conn.execute("SELECT v FROM hmeta WHERE k='log_size'").fetchone():
                return True
            if _hmeta_int(conn, "log_off", -1) < 0:
                return True
        finally:
            conn.close()
    except Exception:
        return True
    try:
        log_size = os.path.getsize(HISTORY_LOG) if os.path.isfile(HISTORY_LOG) else 0
    except Exception:
        log_size = 0
    try:
        conn = sqlite3.connect(HISTORY_DB)
        prev = int(conn.execute("SELECT v FROM hmeta WHERE k='log_size'").fetchone()[0])
        conn.close()
    except Exception:
        return True
    return log_size < prev


def _archive_preserved(conn):
    """Snapshot archive rows (so a rebuild can keep sessions whose on-disk
    files were pruned — the index is the durable copy of history)."""
    try:
        return {sid: (raw, body, turns, size, mtime, ps)
                for sid, raw, body, turns, size, mtime, ps in conn.execute(
                    "SELECT session_id, raw, body, turns, size, mtime, "
                    "COALESCE(prune_source, 1) FROM history_sessions")}
    except Exception:
        return {}


def _prune_archive_sessions(conn, on_disk_ids):
    """Opt-in bounded archive: with INFER_ARCHIVE_RETENTION=<days> set, drop
    archive-only rows (prune_source=1, i.e. originally from a disk file) whose
    mtime is older than the cutoff. Preserved archive copies (prune_source=0)
    and on-disk sessions are never pruned here. Default: keep everything."""
    env = os.environ.get("INFER_ARCHIVE_RETENTION", "").strip()
    if not env:
        return 0
    try:
        days = float(env)
    except Exception:
        return 0
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    try:
        for sid, fts_rowid, mt, ps in conn.execute(
                "SELECT session_id, fts_rowid, mtime, prune_source FROM history_sessions").fetchall():
            if sid in on_disk_ids or (ps or 1) != 1:
                continue
            if (mt or 0) >= cutoff:
                continue
            if fts_rowid:
                conn.execute("DELETE FROM history_fts WHERE rowid=?", (fts_rowid,))
            conn.execute("DELETE FROM history_sessions WHERE session_id=?", (sid,))
            removed += 1
    except Exception:
        pass
    return removed


def rebuild_history_index():
    """(Re)build the full-text conversation index from history.jsonl + session
    files. Wipes and reindexes everything in one transaction — the expensive
    path, paid only when the index is missing or a source shrank; day-to-day
    growth goes through _sync_history_index. Returns a summary string."""
    try:
        os.makedirs(os.path.dirname(HISTORY_DB), exist_ok=True)
        conn = sqlite3.connect(HISTORY_DB)
        try:
            preserved = _archive_preserved(conn)  # keep before we wipe
            on_disk_ids = set()
            for d in _session_dirs():
                try:
                    on_disk_ids.update(fn[:-5] for fn in os.listdir(d) if fn.endswith(".json"))
                except Exception:
                    pass
            conn.execute("DROP TABLE IF EXISTS history_sessions")
            conn.execute("DROP TABLE IF EXISTS history_fts")
            _ensure_history_schema(conn)
            n_turns = 0
            for i, rec in enumerate(_iter_history_log_lines(), 1):
                n_turns += 1 if _index_turn_row(conn, n_turns, rec, i) else 0
            n_sessions = 0
            for d in _session_dirs():
                try:
                    entries = sorted(os.listdir(d))
                except Exception:
                    continue
                for fn in entries:
                    if fn.endswith(".json") and _index_session_file(conn, os.path.join(d, fn)):
                        n_sessions += 1
            # Restore sessions whose on-disk files were pruned (durable archive).
            n_preserved = 0
            for sid, (raw, body, turns, size, mtime, ps) in preserved.items():
                if sid in on_disk_ids:
                    continue
                if conn.execute("SELECT 1 FROM history_sessions WHERE session_id=?", (sid,)).fetchone():
                    continue
                _upsert_session_row(conn, sid, raw, body, "", turns, size, mtime, prune_source=ps)
                n_preserved += 1
            n_pruned = _prune_archive_sessions(conn, on_disk_ids)
            _hmeta_set(conn, "log_size", os.path.getsize(HISTORY_LOG) if os.path.isfile(HISTORY_LOG) else 0)
            _hmeta_set(conn, "log_off", _log_line_count())
            _hmeta_set(conn, "seq", n_turns)
            _hmeta_set(conn, "sess_count", n_sessions + n_preserved)
            conn.commit()
            if n_pruned:
                return (f"[history] Indexed {n_turns} turn(s) / {n_sessions} on-disk session(s)"
                        f" (+{n_preserved} archive-only, pruned {n_pruned} stale): {HISTORY_DB}")
        finally:
            conn.close()
        return f"[history] Indexed {n_turns} turn(s) / {n_sessions} session(s): {HISTORY_DB}"
    except Exception as e:
        return f"Error building history index: {e}"


def _prune_cache_sessions(conn):
    """Bounded cache retention, archive-FIRST: delete old cache session files
    only once they are durably preserved — present in the persistent mirror
    (~/.local/share/ai/sessions) OR fully archived in the index (raw not
    truncated). Keep the newest INFER_SESSION_RETENTION files (default 400).
    last.json and in-flight .tmp files are never touched. Returns removed count."""
    env = os.environ.get("INFER_SESSION_RETENTION", "").strip()
    try:
        keep = int(env) if env else 400
    except Exception:
        keep = 400
    if keep <= 0:
        return 0
    # Throttle: at most one prune scan per 10 min (this runs on the search hot
    # path, so the directory scan must stay rare).
    try:
        row = conn.execute("SELECT v FROM hmeta WHERE k='prune_ts'").fetchone()
        if row and time.time() - float(row[0]) < 600:
            return 0
    except Exception:
        pass
    d = CACHE_SESSIONS
    if not os.path.isdir(d):
        return 0
    entries = []
    try:
        for fn in os.listdir(d):
            if not fn.endswith(".json") or fn == "last.json":
                continue
            p = os.path.join(d, fn)
            try:
                st = os.stat(p)
            except Exception:
                continue
            entries.append((st.st_mtime, fn, p, st.st_size))
    except Exception:
        return 0
    if len(entries) <= keep:
        return 0
    entries.sort(reverse=True)  # newest first
    removed = 0
    for _mt, fn, p, size in entries[keep:]:
        sid = fn[:-5]
        mirrored = os.path.isfile(os.path.join(DATA_SESSIONS, fn))
        if not mirrored:
            try:
                row = conn.execute(
                    "SELECT size FROM history_sessions WHERE session_id=?", (sid,)).fetchone()
            except Exception:
                row = None
            if not (row and row[0] == size and size <= 120_000):
                continue  # not durably preserved — keep the file
        try:
            os.unlink(p)
            removed += 1
        except Exception:
            pass
    try:
        _hmeta_set(conn, "prune_ts", time.time())
    except Exception:
        pass
    return removed


def _history_is_stale():
    """Back-compat predicate: True when the index needs a rebuild (missing,
    shrunken source, old schema) or an incremental sync (sources changed)."""
    if _history_needs_full_rebuild():
        return True
    try:
        conn = sqlite3.connect(HISTORY_DB)
        try:
            return not _sources_unchanged(conn)
        finally:
            conn.close()
    except Exception:
        return True


def _sources_unchanged(conn):
    """Fast staleness probe: log byte-size and session-file count both match
    the recorded fingerprints. Session files are only ever appended (or
    rewritten with the same count), so an unchanged count means the per-file
    fingerprint scan in _sync_history_index has nothing new to do either."""
    try:
        log_size = os.path.getsize(HISTORY_LOG) if os.path.isfile(HISTORY_LOG) else 0
    except Exception:
        log_size = 0
    prev_log = _hmeta_int(conn, "log_size", -1)
    if log_size != prev_log:
        return False
    sess_count = 0
    for d in _session_dirs():
        try:
            sess_count += len([f for f in os.listdir(d) if f.endswith(".json")])
        except Exception:
            pass
    return sess_count == _hmeta_int(conn, "sess_count", -1)


def _ensure_history_ready():
    """Make the index current: full rebuild when needed, incremental sync
    otherwise, no-op when the sources are provably unchanged. Then enforce
    bounded cache retention (archive-first — never deletes a session the index
    and persistent mirror do not already hold). Called by search_history so
    searches are always fresh but stay ~O(1)."""
    if _history_needs_full_rebuild():
        rebuild_history_index()
        conn = sqlite3.connect(HISTORY_DB)
        try:
            _prune_cache_sessions(conn)
            conn.commit()
        finally:
            conn.close()
        return
    conn = sqlite3.connect(HISTORY_DB)
    try:
        _ensure_history_schema(conn)
        if _sources_unchanged(conn):
            _prune_cache_sessions(conn)
            conn.commit()
            return
        _sync_history_index(conn)
        _hmeta_set(conn, "log_size", os.path.getsize(HISTORY_LOG) if os.path.isfile(HISTORY_LOG) else 0)
        conn.commit()
        _prune_cache_sessions(conn)
        conn.commit()
    finally:
        conn.close()


def search_history(query, limit=8):
    """FTS search across all past conversations. Returns matched turns/sessions
    with snippet + session_id, so the agent can learn from earlier sessions and
    then load the full session if needed. Keeps the index incrementally fresh."""
    if not query:
        return "Error: query required"
    try:
        limit = int(limit)
        _ensure_history_ready()
        conn = sqlite3.connect(HISTORY_DB)
        words = re.findall(r"\w+", query)
        rows = []
        try:
            def _try(q, what):
                if what == "fts":
                    return conn.execute(
                        "SELECT source, session_id, ts, prompt, response, "
                        "snippet(history_fts, 5, '[', ']', ' … ', 16) AS snip "
                        "FROM history_fts WHERE history_fts MATCH ? ORDER BY rank LIMIT ?",
                        (q, max(limit, 1))).fetchall()
                return conn.execute(
                    "SELECT source, session_id, ts, substr(body,1,240) "
                    "FROM history_fts WHERE body LIKE ? OR prompt LIKE ? LIMIT ?",
                    (f"%{q}%", f"%{q}%", max(limit, 1))).fetchall()

            if words:
                # Prefer exact phrase, then all-terms (AND), then any-term (OR),
                # then a LIKE fallback for terms FTS5 can't tokenize well.
                phrase = '"' + " ".join(words) + '"'
                rows = _try(phrase, "fts") or _try(" AND ".join(words), "fts") or _try(" OR ".join(words), "fts")
            if not rows and words:
                rows = []
                for w in words[:5]:
                    rows = _try(w, "like")
                    if rows:
                        break
        finally:
            conn.close()

        if not rows:
            return (f"No past conversation matches '{query}'. Try a broader term, "
                    f"or use list_sessions to see recent history.")
        seen = set()
        shown = []
        for source, sid, ts, prompt, response, snip in rows:
            key = (source, sid, (snip or "")[:40])
            if key in seen:
                continue
            seen.add(key)
            text = snip or (response or prompt or "")
            shown.append(f"- {source} {sid or '?'} ({ts or '?'}): {str(text)[:200]}")
        if not shown:
            return (f"No past conversation matches '{query}'. Try a broader term, "
                    f"or use list_sessions to see recent history.")
        out = [f"[history] {len(shown)} match(es) for '{query}':\n"]
        out.extend(shown)
        return "\n".join(out)
    except Exception as e:
        return f"Error searching history: {e}"


def list_sessions(limit=12):
    """List the most recent backed-up conversations (session id, mtime, bytes).
    Merges on-disk session files with the durable index archive, so sessions
    whose cache files were pruned still show up (marked [archive])."""
    try:
        items = {}
        for d in _session_dirs():
            for fn in os.listdir(d):
                if fn.endswith(".json"):
                    p = os.path.join(d, fn)
                    try:
                        st = os.stat(p)
                        items[fn[:-5]] = (st.st_mtime, st.st_size, p, "")
                    except Exception:
                        continue
        # Durable archive rows (only for ids not present on disk).
        if os.path.isfile(HISTORY_DB):
            try:
                conn = sqlite3.connect(HISTORY_DB)
                for sid, size, mt in conn.execute(
                        "SELECT session_id, size, mtime FROM history_sessions").fetchall():
                    if sid not in items:
                        items[sid] = (mt or 0, size or 0, f"(archive: {HISTORY_DB})", " [archive]")
                conn.close()
            except Exception:
                pass
        rows = sorted(items.items(), key=lambda kv: kv[1][0], reverse=True)[: int(limit)]
        if not rows:
            return "No backed-up sessions found."
        lines = ["Recent backed-up sessions:"]
        for sid, (mt, size, p, tag) in rows:
            lines.append(f"- {sid}{tag}  ({size} B)  -> {p}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing sessions: {e}"


def get_session(session_id, max_chars=4000):
    """Load a full backed-up conversation by session_id and return a readable
    transcript. On-disk files win; if they are gone (cache pruned), the
    durable archive copy in the index is used."""
    if not session_id:
        return "Error: session_id required (use list_sessions or search_history to find one)."
    candidates = [os.path.join(d, f"{session_id}.json") for d in _session_dirs()]
    for p in candidates:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    messages = json.load(f)
                body, _ = _transcript_body(messages)
                if len(body) > int(max_chars):
                    body = body[: int(max_chars)] + "\n... [truncated — read the file for the full transcript]"
                return f"[session {session_id}] from {p}\n\n{body}"
            except Exception as e:
                return f"Error reading session {session_id}: {e}"
    # Durable archive fallback.
    if os.path.isfile(HISTORY_DB):
        try:
            conn = sqlite3.connect(HISTORY_DB)
            row = conn.execute(
                "SELECT raw, body FROM history_sessions WHERE session_id=?",
                (session_id,)).fetchone()
            conn.close()
            if row:
                raw, body = row[0] or "", row[1] or ""
                text = ""
                if raw:
                    try:
                        messages = json.loads(raw)
                        if isinstance(messages, list):
                            text, _ = _transcript_body(messages)
                    except Exception:
                        text = ""
                if not text:
                    text = body  # raw was truncated for a very long session
                if text:
                    if len(text) > int(max_chars):
                        text = text[: int(max_chars)] + "\n... [truncated — archive copy]"
                    return f"[session {session_id}] from archive {HISTORY_DB}\n\n{text}"
        except Exception:
            pass
    return f"Session '{session_id}' not found."


def search_zulip_chats(query="", limit=10, error_only=False):
    """Search or list backed-up Zulip chat interactions from memory."""
    try:
        from zulip_ai_bridge import ZulipMemoryManager
        mem = ZulipMemoryManager()
        records = mem.get_recent(limit=int(limit) * 3, error_only=bool(error_only))
        if query and str(query).strip():
            q_lower = str(query).strip().lower()
            records = [
                r for r in records
                if q_lower in (r.get("query") or "").lower()
                or q_lower in (r.get("reply_snippet") or "").lower()
                or q_lower in (r.get("session_id") or "").lower()
            ]
        records = records[:int(limit)]
        if not records:
            return "No matching Zulip chat records found in memory."
        lines = [f"Found {len(records)} Zulip chat record(s):", ""]
        for r in records:
            st = r.get("status", "ok")
            dur = f"{r.get('duration_s', 0)}s"
            lines.append(f"- **ID:** `{r.get('session_id')}` | **Time:** {r.get('timestamp')} | **Status:** `{st}` ({dur})")
            lines.append(f"  *User query:* {r.get('query')}")
            if r.get("reply_snippet"):
                lines.append(f"  *Reply:* {r.get('reply_snippet')[:120]}...")
            if r.get("stderr_snippet"):
                lines.append(f"  *Stderr:* {r.get('stderr_snippet')[:100]}...")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching Zulip chats: {e}"


def get_zulip_chat(session_id=""):
    """Retrieve full details of a specific Zulip chat interaction."""
    if not session_id:
        return "Error: session_id is required."
    try:
        from zulip_ai_bridge import ZulipMemoryManager
        mem = ZulipMemoryManager()
        data = mem.get_chat(session_id.strip())
        if not data:
            return f"Error: Zulip chat session '{session_id}' not found."
        return json.dumps(data, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error getting Zulip chat details: {e}"
