"""Single SQLite state store: memory, lessons, failure ledger, sessions index, scheduled tasks,
chat audit, plus the FTS stores behind remember/recall and the vault index.

Path: $AI_STATE_DB, else $HOME/.local/share/ai/state.db, both read at CALL time (db_path()), so
tests and later code can redirect it by changing the environment; nothing caches a by-value copy.

Concurrency: WAL journal + busy_timeout=5000. Every write runs in a BEGIN IMMEDIATE transaction
(no read->write lock upgrade, so no instant SQLITE_BUSY) and lock waits are retried well past the
busy timeout, so concurrent writers (Zulip bridge, scheduler daemons, interactive ai, ai_mcp.py
subprocesses) never surface "database is locked".

Legacy migration: the first connect() of a process on the default path imports the old files
(memory.txt, memory.db, self_improve/lessons.md + ledger.jsonl, scheduled_tasks/*.json,
zulip_chats.jsonl) in ONE transaction that also sets meta.migrated_v1, then renames each original
to *.migrated.bak. Unparsable records/files are skipped and counted; migration never raises out
of connect(). With $AI_STATE_DB set there is no lazy migration (tests can never touch the real
HOME); `ai_mcp.py state-migrate [HOME]` / migrate_legacy(home) run it explicitly.

~/.local/share/ai/history_index.db is NOT state: it is a rebuildable FTS cache over the session
files and history.jsonl (aitools/history.py) and stays a separate file.
"""
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time

SCHEMA_VERSION = 4  # 2: pinned memories, vectors + embed_queue, item_fts + triggers (Task 5b);
                    # 3: embed_queue.attempts, triggers recreated (named columns, PK range delete)
                    # 4: ledger/chat_audit import_key (unique): legacy re-import is idempotent;
                    #    the upgrade dedupes exact duplicate rows (lowest id kept)
BUSY_TIMEOUT_MS = 5000
LEDGER_MAX_ROWS = 5000
CHAT_AUDIT_RETENTION_DAYS = 90
MEMORY_RENDER_BYTES = 4096
_LOCK_RETRY_SECONDS = 60.0

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS memory(id INTEGER PRIMARY KEY, text TEXT UNIQUE, created REAL, "
    "last_used REAL, pinned INT DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY, kind TEXT, tool TEXT, signature TEXT, "
    "body TEXT, status TEXT DEFAULT 'approved', created REAL, last_seen REAL, hits INT DEFAULT 1, "
    "expires REAL NULL, UNIQUE(kind, tool, signature))",
    # kind/signature/phase extend the brief's ledger columns: the error-chain logic needs the
    # failure|recovery kind and the normalised signature (recurrence counting) per row.
    "CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, ts REAL, chain_id TEXT, tool TEXT, "
    "args_hash TEXT, ok INT, error TEXT, kind TEXT, signature TEXT, phase TEXT, import_key TEXT)",
    "CREATE INDEX IF NOT EXISTS ledger_signature ON ledger(signature)",
    "CREATE INDEX IF NOT EXISTS ledger_chain ON ledger(chain_id)",
    "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, started REAL, ended REAL, path TEXT, "
    "summary TEXT)",
    "CREATE TABLE IF NOT EXISTS scheduled_tasks(id TEXT PRIMARY KEY, spec_json TEXT, next_run REAL, "
    "run_once INT, kind TEXT)",
    # session_id/status/meta_json extend the brief's chat_audit columns: the bridge's diagnostics
    # filter on status and look records up by session id; meta_json keeps the full summary record.
    "CREATE TABLE IF NOT EXISTS chat_audit(id INTEGER PRIMARY KEY, ts REAL, source TEXT, sender TEXT, "
    "prompt TEXT, reply TEXT, session_id TEXT, status TEXT, meta_json TEXT, import_key TEXT)",
    "CREATE INDEX IF NOT EXISTS chat_audit_ts ON chat_audit(ts)",
    # Semantic memory (aitools/search.py). Embeddings as float16 blobs, each L2-normalised at its
    # own dim (Matryoshka 128-d prefix for the coarse pass, full 768-d for the rerank). hash
    # (content+model, decides re-embedding) and source_ts (the item's own date, for `since`)
    # extend the brief's columns. A document longer than one chunk has rows <id>, <id>#1, ...
    "CREATE TABLE IF NOT EXISTS vectors(item_type TEXT, item_id TEXT, model TEXT, dim INT, v128 BLOB, "
    "v768 BLOB, updated REAL, hash TEXT, source_ts REAL, PRIMARY KEY(item_type, item_id))",
    # Items written since the last index pass (filled by the triggers below and by the notes
    # writers in aitools/memory.py); drained by `ai_mcp.py embed-backfill`, never at ai startup.
    # attempts: failed embedding tries; a row at 3+ is skipped (never wedges the queue) until its
    # content changes (the trigger replaces the row, resetting it).
    "CREATE TABLE IF NOT EXISTS embed_queue(item_type TEXT, item_id TEXT, enqueued REAL, "
    "attempts INT DEFAULT 0, PRIMARY KEY(item_type, item_id))",
)
# import_key: set only on rows imported from the legacy files (a hash over every non-id column,
# _row_key), NULL on rows written live. The unique index makes a re-import of a restored legacy
# file a no-op (INSERT OR IGNORE) without ever merging two live events.
_KEYED = {
    "ledger": ("ts", "chain_id", "tool", "args_hash", "ok", "error", "kind", "signature", "phase"),
    "chat_audit": ("ts", "source", "sender", "prompt", "reply", "session_id", "status", "meta_json"),
}
_NOW_SQL = "((julianday('now') - 2440587.5) * 86400.0)"
# The former ~/.config/ai/memory.db tables (remember/recall FTS and the vault note index). They
# hold long free-text documents searched by FTS, which the 4 KB always-injected `memory` table
# cannot hold, so they move over as-is.
_FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(content, metadata)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS vault_fts USING fts5(title, content)",
    # Keyword side of search_memory for the plain tables (memory, lessons, chat_audit). Kept in
    # sync by triggers, so every writer (learning.py's raw SQL included) maintains it for free.
    # rowid = id * 4 + {memory: 1, lesson: 2, chat: 3}.
    "CREATE VIRTUAL TABLE IF NOT EXISTS item_fts USING fts5(text, item_type UNINDEXED, item_id UNINDEXED)",
)
# (item_type, table, rowid code, SQL text expression over NEW/OLD as {r}, columns whose update
# changes the text)
_INDEXED = (
    ("memory", "memory", 1, "{r}.text", "text"),
    ("lesson", "lessons", 2, "{r}.kind || ' ' || coalesce({r}.tool, '') || ': ' || coalesce({r}.body, '')",
     "kind, tool, body"),
    ("chat", "chat_audit", 3, "coalesce({r}.prompt, '') || char(10) || coalesce({r}.reply, '')",
     "prompt, reply"),
)


def _trigger_sql():
    out = []
    for typ, table, code, expr, cols in _INDEXED:
        out += ["DROP TRIGGER IF EXISTS %s_fts_%s" % (table, w) for w in ("ai", "au", "ad")]
        ins = ("INSERT OR REPLACE INTO item_fts(rowid, text, item_type, item_id) VALUES "
               "(new.id * 4 + %d, %s, '%s', new.id); INSERT OR REPLACE INTO embed_queue(item_type, item_id, "
               "enqueued) VALUES ('%s', new.id, %s);" % (code, expr.format(r="new"), typ, typ, _NOW_SQL))
        out.append("CREATE TRIGGER IF NOT EXISTS %s_fts_ai AFTER INSERT ON %s BEGIN %s END" % (table, table, ins))
        out.append("CREATE TRIGGER IF NOT EXISTS %s_fts_au AFTER UPDATE OF %s ON %s BEGIN %s END"
                   % (table, cols, table, ins))
        out.append("CREATE TRIGGER IF NOT EXISTS %s_fts_ad AFTER DELETE ON %s BEGIN "
                   "DELETE FROM item_fts WHERE rowid = old.id * 4 + %d; "
                   # chunk ids are '<id>#<n>': a PK range ('#' < '$') instead of LIKE
                   "DELETE FROM vectors WHERE item_type = '%s' AND (item_id = CAST(old.id AS TEXT) "
                   "OR (item_id >= old.id || '#' AND item_id < old.id || '$')); "
                   "DELETE FROM embed_queue WHERE item_type = '%s' AND item_id = CAST(old.id AS TEXT); END"
                   % (table, table, code, typ, typ))
    return out


def _row_key(table, values):
    """import_key of a row: hash of the table name and its non-id column values (as stored)."""
    blob = json.dumps([table] + list(values), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:32]


def _key_and_dedupe(conn):
    """Schema v4 upgrade: give every existing ledger/chat_audit row its import_key, deleting exact
    duplicates (same key; the lowest id is kept), then add the unique index. Existing rows cannot
    be told apart into imported and live ones, so all of them are keyed."""
    removed = {}
    for table, cols in _KEYED.items():
        conn.execute("DROP INDEX IF EXISTS %s_import_key" % table)
        seen, dupes, keys = set(), [], []
        for row in conn.execute("SELECT id, %s FROM %s ORDER BY id" % (", ".join(cols), table)).fetchall():
            k = _row_key(table, row[1:])
            if k in seen:
                dupes.append((row[0],))
            else:
                seen.add(k)
                keys.append((k, row[0]))
        conn.executemany("DELETE FROM %s WHERE id = ?" % table, dupes)
        conn.executemany("UPDATE %s SET import_key = ? WHERE id = ?" % table, keys)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS %s_import_key ON %s(import_key)" % (table, table))
        removed[table] = len(dupes)
    return removed


def _item_fts_fill(conn):
    """(Re)build item_fts rows for every existing memory/lesson/chat row (idempotent)."""
    for typ, table, code, expr, _ in _INDEXED:
        conn.execute("INSERT OR REPLACE INTO item_fts(rowid, text, item_type, item_id) SELECT id * 4 + %d, %s, "
                     "'%s', id FROM %s r" % (code, expr.format(r="r"), typ, table))

_ready = set()            # (path, dev, inode) whose schema is known to exist in this process
_migrate_tried = set()    # db paths the lazy migration already ran (or failed) for
_guard = threading.Lock()
# Seconds a caller may wait on the write lock (busy_timeout + retries). Read at open time;
# lowered with lock_budget() on latency-critical paths (ai startup subcommands).
_lock_budget = [_LOCK_RETRY_SECONDS]
READ_BUDGET_SECONDS = 1.0


class _Conn(sqlite3.Connection):
    """sqlite3 connection carrying its lock budget (seconds) and read-only flag."""
    lock_budget = _LOCK_RETRY_SECONDS
    read_only = False


@contextlib.contextmanager
def lock_budget(seconds):
    """Within the block, connections opened here wait at most `seconds` for the write lock."""
    old = _lock_budget[0]
    _lock_budget[0] = float(seconds)
    try:
        yield
    finally:
        _lock_budget[0] = old


# ── connection ───────────────────────────────────────────────────────────────
def _home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def db_path():
    """Current state-db path ($AI_STATE_DB or $HOME/.local/share/ai/state.db), read at call time."""
    p = os.environ.get("AI_STATE_DB", "").strip()
    if p:
        return os.path.abspath(os.path.expanduser(p))
    return os.path.join(_home(), ".local", "share", "ai", "state.db")


def _is_lock_error(e):
    s = str(e).lower()
    return "locked" in s or "busy" in s


def _retry(fn, budget=_LOCK_RETRY_SECONDS):
    """Run fn() retrying lock errors past the busy timeout, for at most `budget` seconds."""
    deadline = time.monotonic() + budget
    delay = 0.05
    while True:
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if not _is_lock_error(e) or time.monotonic() + delay > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


def _commit(conn):
    conn.execute("COMMIT")


def _rollback(conn):
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    except Exception:
        pass


@contextlib.contextmanager
def _tx(conn):
    """BEGIN IMMEDIATE ... COMMIT. Takes the write lock up front. On any error, in the body OR in
    COMMIT, the transaction is rolled back and the error re-raised, so the connection is never
    left inside an open transaction. Nested use (an API call made with the conn of an open
    transaction, e.g. during migration) joins the outer transaction."""
    if conn.in_transaction:
        yield conn
        return
    budget = getattr(conn, "lock_budget", _LOCK_RETRY_SECONDS)
    _retry(lambda: conn.execute("BEGIN IMMEDIATE"), budget)
    try:
        yield conn
        _retry(lambda: _commit(conn), budget)
    except BaseException:
        _rollback(conn)
        raise


transaction = _tx  # public: group several API calls (each given conn=...) into one write transaction


def _writable(path):
    d = os.path.dirname(path) or "."
    if os.path.exists(path):
        return os.access(path, os.W_OK) and os.access(d, os.W_OK)
    return os.access(d, os.W_OK) if os.path.isdir(d) else True


def _open(path, budget=None, read_only=False):
    budget = _lock_budget[0] if budget is None else budget
    busy_ms = int(min(BUSY_TIMEOUT_MS, budget * 1000))
    if read_only:
        conn = None
        for uri in ("file:%s?mode=ro", "file:%s?immutable=1"):
            try:
                conn = sqlite3.connect(uri % path, uri=True, timeout=busy_ms / 1000.0,
                                       isolation_level=None, check_same_thread=False, factory=_Conn)
                conn.execute("SELECT count(*) FROM meta").fetchone()
                break
            except sqlite3.Error:
                if conn is not None:
                    conn.close()
                conn = None
        if conn is None:
            raise sqlite3.OperationalError("state db not readable: %s" % path)
        conn.read_only = True
    else:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, mode=0o700, exist_ok=True)
        if not os.path.exists(path):
            try:  # create private before sqlite does (WAL/SHM files inherit the db file's mode)
                os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
            except OSError:
                pass
        conn = sqlite3.connect(path, timeout=busy_ms / 1000.0, isolation_level=None,
                               check_same_thread=False, factory=_Conn)
    conn.lock_budget = budget
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=%d" % busy_ms)
    return conn


def _schema_current(conn):
    """Plain read (no lock): is the schema already created at this version?"""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return False
    try:
        return bool(row) and int(row[0]) >= SCHEMA_VERSION
    except (TypeError, ValueError):
        return False


def _schema_any(conn):
    """Plain read: does a schema (any version) exist? Lets a read path use an older schema when it
    cannot take the write lock to upgrade it."""
    try:
        return conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() is not None
    except sqlite3.OperationalError:
        return False


def _init_schema(conn, path):
    try:
        st = os.stat(path)
        key = (path, st.st_dev, st.st_ino)
    except OSError:
        key = None
    if key is not None and key in _ready:
        return
    if _schema_current(conn):  # the common case: no write lock taken just to open the db
        if key is not None:
            _ready.add(key)
        return
    _retry(lambda: conn.execute("PRAGMA journal_mode=WAL").fetchone(), conn.lock_budget)
    with _tx(conn):
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            old_version = int(row[0]) if row else 0
        except (sqlite3.OperationalError, TypeError, ValueError):
            old_version = 0
        for stmt in _SCHEMA:
            conn.execute(stmt)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memory)")}
        if "pinned" not in cols:  # v1 -> v2
            conn.execute("ALTER TABLE memory ADD COLUMN pinned INT DEFAULT 0")
        if "attempts" not in {r[1] for r in conn.execute("PRAGMA table_info(embed_queue)")}:  # v2 -> v3
            conn.execute("ALTER TABLE embed_queue ADD COLUMN attempts INT DEFAULT 0")
        for table in _KEYED:                                 # v3 -> v4
            if "import_key" not in {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}:
                conn.execute("ALTER TABLE %s ADD COLUMN import_key TEXT" % table)
        for stmt in _FTS_SCHEMA:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:  # pragma: no cover - sqlite built without FTS5
                pass
        try:
            for stmt in _trigger_sql():
                conn.execute(stmt)
            _item_fts_fill(conn)
        except sqlite3.OperationalError:  # pragma: no cover - sqlite built without FTS5
            pass
        if old_version < 4:
            # after the triggers exist: deleting a duplicate also drops its search-index rows
            removed = _key_and_dedupe(conn)
            if any(removed.values()):
                _meta_set(conn, "dedupe_v4", json.dumps(dict(removed, at=time.time())))
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
    if key is not None:
        _ready.add(key)


def connect(path=None, migrate=True, read=False):
    """Open the state db (creating the schema when missing). On the default path ($AI_STATE_DB
    unset, no explicit path) the first connect() of a process also imports pending legacy files
    (see migrate_legacy; only a stat per source when nothing is pending); it never raises from
    there. Writer opens apply chat_audit retention (at most once per day).

    read=True is the degraded-mode open used by read paths (state-export, renders, lists): it
    waits at most READ_BUDGET_SECONDS on any lock (WAL readers are not blocked by a writer),
    skips retention, and opens a db it cannot write via a `mode=ro` URI (`immutable=1` as the
    last resort)."""
    explicit = path is not None or bool(os.environ.get("AI_STATE_DB", "").strip())
    path = path or db_path()
    budget = min(_lock_budget[0], READ_BUDGET_SECONDS) if read else None
    if read and os.path.exists(path) and not _writable(path):
        return _open(path, budget=budget, read_only=True)
    conn = _open(path, budget=budget)
    try:
        _init_schema(conn, path)
    except Exception:
        if not (read and _schema_any(conn)):
            conn.close()
            raise
    if migrate and not explicit:
        with _guard:
            first = path not in _migrate_tried
            _migrate_tried.add(path)
        if first:
            try:
                migrate_legacy(_home(), conn=conn)
            except Exception:
                pass
            _rollback(conn)  # never hand out a connection stuck in a failed transaction
    if not read:
        try:
            chat_audit_prune(conn=conn)
        except Exception:
            _rollback(conn)
    return conn


@contextlib.contextmanager
def _conn(conn=None, read=False):
    if conn is not None:
        yield conn
        return
    c = connect(read=read)
    try:
        yield c
    finally:
        c.close()


def _meta_get(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))


# ── memory (the always-injected persistent memory) ───────────────────────────
def memory_add(text, pinned=False, conn=None):
    """Add one memory entry. Returns 'added', 'duplicate' (case/whitespace-insensitive) or 'empty'.
    pinned=True marks it (also an existing duplicate) as a core memory that auto-recall always
    injects (see memory_pinned)."""
    t = str(text or "").strip()
    if not t:
        return "empty"
    key = t.lower()
    now = time.time()
    with _conn(conn) as c, _tx(c):
        for row in c.execute("SELECT id, text FROM memory"):
            if (row["text"] or "").strip().lower() == key:
                if pinned:
                    c.execute("UPDATE memory SET last_used=?, pinned=1 WHERE id=?", (now, row["id"]))
                else:
                    c.execute("UPDATE memory SET last_used=? WHERE id=?", (now, row["id"]))
                return "duplicate"
        c.execute("INSERT INTO memory(text, created, last_used, pinned) VALUES (?, ?, ?, ?)",
                  (t, now, now, 1 if pinned else 0))
    return "added"


def memory_pinned(conn=None):
    """Pinned (core) memory entries, oldest first."""
    with _conn(conn, read=True) as c:
        try:
            return [r[0] for r in c.execute("SELECT text FROM memory WHERE pinned=1 ORDER BY id")]
        except sqlite3.OperationalError:  # v1 schema a read path could not upgrade
            return []


def memory_list(conn=None):
    """All memory entries, oldest first."""
    with _conn(conn, read=True) as c:
        return [r[0] for r in c.execute("SELECT text FROM memory ORDER BY id")]


def memory_clear(conn=None):
    with _conn(conn) as c, _tx(c):
        c.execute("DELETE FROM memory")


def memory_evict(cap=4000, conn=None):
    """Delete the oldest whole entries until the joined text fits `cap` chars; returns the evicted
    texts (oldest first). A single oversized entry is cut to `cap` rather than dropped."""
    evicted = []
    with _conn(conn) as c, _tx(c):
        rows = [(r["id"], r["text"]) for r in c.execute("SELECT id, text FROM memory ORDER BY id")]
        total = len("\n".join(t for _, t in rows))
        while rows and total > cap:
            if len(rows) == 1:
                c.execute("UPDATE memory SET text=? WHERE id=?", (rows[0][1][:cap], rows[0][0]))
                break
            rid, t = rows.pop(0)
            c.execute("DELETE FROM memory WHERE id=?", (rid,))
            evicted.append(t)
            total = len("\n".join(t for _, t in rows))
    return evicted


def memory_render(max_bytes=MEMORY_RENDER_BYTES, conn=None):
    """Memory as injected into the system prompt: newest whole lines that fit max_bytes (UTF-8),
    in chronological order."""
    kept, used = [], 0
    for t in reversed(memory_list(conn)):
        b = len(t.encode("utf-8")) + (1 if kept else 0)
        if used + b > max_bytes:
            if not kept:  # the newest entry alone is too big: cut it at a char boundary
                kept.append(t.encode("utf-8")[:max_bytes].decode("utf-8", "ignore"))
            break
        kept.append(t)
        used += b
    return "\n".join(reversed(kept))


# ── lessons ──────────────────────────────────────────────────────────────────
def lesson_upsert(kind, tool, signature, body, status="approved", expires=None, ts=None, conn=None):
    """Insert a lesson, or (same kind+tool+signature) bump its hits/last_seen keeping the first
    body and status. Returns True when a new row was created."""
    kind = str(kind or "NOTE").strip().upper()
    tool = str(tool or "").strip()
    signature = str(signature or "").strip()
    now = time.time() if ts is None else float(ts)
    with _conn(conn) as c, _tx(c):
        row = c.execute("SELECT id FROM lessons WHERE kind=? AND tool=? AND signature=?",
                        (kind, tool, signature)).fetchone()
        if row:
            c.execute("UPDATE lessons SET hits=hits+1, last_seen=max(coalesce(last_seen, 0), ?) "
                      "WHERE id=?", (now, row[0]))
            return False
        c.execute("INSERT INTO lessons(kind, tool, signature, body, status, created, last_seen, hits, "
                  "expires) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                  (kind, tool, signature, str(body or ""), status or "approved", now, now, expires))
    return True


def lesson_exists(kind, tool="", signature="", conn=None):
    """True if a lesson of `kind` exists for this signature (or, with no signature, this tool)."""
    kind = str(kind or "").strip().upper()
    sig = str(signature or "").strip().lower()
    t = str(tool or "").strip().lower()
    with _conn(conn, read=True) as c:
        if sig:
            row = c.execute("SELECT 1 FROM lessons WHERE kind=? AND lower(signature)=? LIMIT 1",
                            (kind, sig)).fetchone()
        elif t:
            row = c.execute("SELECT 1 FROM lessons WHERE kind=? AND lower(tool)=? LIMIT 1",
                            (kind, t)).fetchone()
        else:
            return False
    return row is not None


def lessons_list(status="approved", kinds=None, limit=None, include_expired=False, conn=None):
    """Lessons as dicts, oldest first. status=None means any status; limit keeps the newest."""
    where, params = [], []
    if status is not None:
        where.append("status=?")
        params.append(status)
    if kinds:
        kinds = [str(k).upper() for k in kinds]
        where.append("kind IN (%s)" % ",".join("?" * len(kinds)))
        params.extend(kinds)
    if not include_expired:
        where.append("(expires IS NULL OR expires > ?)")
        params.append(time.time())
    sql = "SELECT * FROM lessons" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT %d" % max(0, int(limit))
    with _conn(conn, read=True) as c:
        rows = [dict(r) for r in c.execute(sql, params)]
    return list(reversed(rows))


def lesson_block(row):
    """One lesson as the legacy lessons.md markdown block (what lessons_for/session_recap match)."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row.get("created") or 0))
    lines = ["## %s %s" % (row.get("kind") or "NOTE", ts)]
    if row.get("tool"):
        lines.append("tool: %s" % row["tool"])
    sig = row.get("signature") or ""
    if sig and not sig.startswith("manual:"):
        lines.append("signature: %s" % sig)
    lines.append("> %s" % (row.get("body") or ""))
    return "\n".join(lines)


def lessons_render(status="approved", limit=50, conn=None):
    """Approved, unexpired lessons rendered as markdown blocks (newest `limit`, oldest first)."""
    return "\n\n".join(lesson_block(r) for r in lessons_list(status=status, limit=limit, conn=conn))


# ── failure ledger ───────────────────────────────────────────────────────────
def _args_hash(args):
    return hashlib.sha256(str(args).encode("utf-8", "replace")).hexdigest()[:16] if args else ""


def ledger_append(tool, ok, error="", chain_id="", args="", kind=None, signature="", phase="",
                  ts=None, max_rows=LEDGER_MAX_ROWS, conn=None, imported=False):
    """Append one ledger row (args are stored only as a hash). Keeps the ledger bounded: past
    max_rows the oldest rows are dropped down to 80% of max_rows. Returns the new row id.
    imported=True (legacy import): the row gets its import_key and an identical imported row
    already present makes this a no-op returning None."""
    kind = kind or ("success" if ok else "failure")
    ts = time.time() if ts is None else float(ts)
    vals = (ts, chain_id or "", tool or "", _args_hash(args), 1 if ok else 0, (error or "")[:300], kind,
            signature or "", phase or "")
    with _conn(conn) as c, _tx(c):
        cur = c.execute("INSERT OR IGNORE INTO ledger(ts, chain_id, tool, args_hash, ok, error, kind, "
                        "signature, phase, import_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        vals + (_row_key("ledger", vals) if imported else None,))
        if not cur.rowcount:
            return None
        rid = cur.lastrowid
        if max_rows and c.execute("SELECT count(*) FROM ledger").fetchone()[0] > max_rows:
            _ledger_keep_newest(c, max(1, max_rows * 8 // 10))
    return rid


def _ledger_keep_newest(c, keep):
    return c.execute("DELETE FROM ledger WHERE id NOT IN (SELECT id FROM ledger ORDER BY id DESC "
                     "LIMIT ?)", (keep,)).rowcount


def ledger_trim(max_rows=LEDGER_MAX_ROWS, conn=None):
    """Keep only the newest max_rows ledger rows; returns how many were deleted."""
    with _conn(conn) as c, _tx(c):
        return _ledger_keep_newest(c, int(max_rows))


def _ledger_where(chain_id, signature, kind):
    where, params = [], []
    for col, val in (("chain_id", chain_id), ("signature", signature), ("kind", kind)):
        if val is not None:
            where.append("%s=?" % col)
            params.append(val)
    return (" WHERE " + " AND ".join(where) if where else ""), params


def ledger_rows(chain_id=None, signature=None, kind=None, conn=None):
    """Ledger rows as dicts, oldest first, optionally filtered."""
    w, p = _ledger_where(chain_id, signature, kind)
    with _conn(conn, read=True) as c:
        return [dict(r) for r in c.execute("SELECT * FROM ledger" + w + " ORDER BY id", p)]


def ledger_count(chain_id=None, signature=None, kind=None, conn=None):
    w, p = _ledger_where(chain_id, signature, kind)
    with _conn(conn, read=True) as c:
        return c.execute("SELECT count(*) FROM ledger" + w, p).fetchone()[0]


# ── sessions index ───────────────────────────────────────────────────────────
def session_upsert(session_id, started=None, ended=None, path=None, summary=None, conn=None):
    """Create/update a sessions row; None fields keep their stored value."""
    if not session_id:
        return
    with _conn(conn) as c, _tx(c):
        c.execute("INSERT INTO sessions(id, started, ended, path, summary) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT(id) DO UPDATE SET started=coalesce(excluded.started, started), "
                  "ended=coalesce(excluded.ended, ended), path=coalesce(excluded.path, path), "
                  "summary=coalesce(excluded.summary, summary)",
                  (str(session_id), started, ended, path, summary))


def sessions_list(limit=None, conn=None):
    """Sessions newest first (by ended, else started)."""
    sql = "SELECT * FROM sessions ORDER BY coalesce(ended, started, 0) DESC, id DESC"
    if limit is not None:
        sql += " LIMIT %d" % max(0, int(limit))
    with _conn(conn, read=True) as c:
        return [dict(r) for r in c.execute(sql)]


# ── scheduled tasks ──────────────────────────────────────────────────────────
def task_put(task_id, spec, next_run=None, conn=None):
    """Insert or replace a scheduled task. `spec` is the task dict (old task-file format)."""
    spec = dict(spec or {})
    spec.setdefault("task_id", task_id)
    with _conn(conn) as c, _tx(c):
        if next_run is None:
            row = c.execute("SELECT next_run FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
            next_run = row[0] if row else None
        c.execute("INSERT OR REPLACE INTO scheduled_tasks(id, spec_json, next_run, run_once, kind) "
                  "VALUES (?, ?, ?, ?, ?)",
                  (task_id, json.dumps(spec), next_run, 1 if spec.get("run_once") else 0,
                   spec.get("kind") or None))


def task_update(task_id, spec, conn=None):
    """Replace the spec of an EXISTING task (never resurrects a cancelled one). True if updated."""
    spec = dict(spec or {})
    spec.setdefault("task_id", task_id)
    with _conn(conn) as c, _tx(c):
        return c.execute("UPDATE scheduled_tasks SET spec_json=?, run_once=?, kind=? WHERE id=?",
                         (json.dumps(spec), 1 if spec.get("run_once") else 0, spec.get("kind") or None,
                          task_id)).rowcount > 0


def task_get(task_id, conn=None):
    with _conn(conn, read=True) as c:
        row = c.execute("SELECT spec_json FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return None
    try:
        spec = json.loads(row[0] or "{}")
    except ValueError:
        spec = {}
    spec.setdefault("task_id", task_id)
    return spec


def task_exists(task_id, conn=None):
    with _conn(conn, read=True) as c:
        return c.execute("SELECT 1 FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone() is not None


def task_delete(task_id, conn=None):
    with _conn(conn) as c, _tx(c):
        return c.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,)).rowcount > 0


def task_set_next_run(task_id, next_run, conn=None):
    with _conn(conn) as c, _tx(c):
        return c.execute("UPDATE scheduled_tasks SET next_run=? WHERE id=?",
                         (next_run, task_id)).rowcount > 0


def task_rows(conn=None):
    """[(id, next_run, kind)] sorted by id (what `state-export tasks` prints for ai.c)."""
    with _conn(conn, read=True) as c:
        return [tuple(r) for r in c.execute("SELECT id, next_run, kind FROM scheduled_tasks ORDER BY id")]


def task_list(conn=None):
    """Every task spec, sorted by id."""
    with _conn(conn, read=True) as c:
        rows = list(c.execute("SELECT id, spec_json FROM scheduled_tasks ORDER BY id"))
    out = []
    for tid, sj in rows:
        try:
            spec = json.loads(sj or "{}")
        except ValueError:
            spec = {}
        spec.setdefault("task_id", tid)
        out.append(spec)
    return out


# ── chat audit ───────────────────────────────────────────────────────────────
def chat_audit_add(source, sender, prompt, reply, ts=None, session_id=None, status=None, meta=None,
                   conn=None, imported=False):
    """Append one chat audit row (e.g. one Zulip exchange); returns its id. imported=True (legacy
    import): keyed like ledger_append, None when the identical imported row is already present."""
    ts = time.time() if ts is None else float(ts)
    vals = (ts, source, sender, prompt, reply, session_id, status,
            json.dumps(meta, ensure_ascii=False) if meta is not None else None)
    with _conn(conn) as c, _tx(c):
        cur = c.execute("INSERT OR IGNORE INTO chat_audit(ts, source, sender, prompt, reply, session_id, "
                        "status, meta_json, import_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        vals + (_row_key("chat_audit", vals) if imported else None,))
        return cur.lastrowid if cur.rowcount else None


def chat_audit_recent(limit=10, exclude_status=(), session_id=None, source=None, conn=None):
    """Newest-first audit rows as dicts (columns + parsed `meta`)."""
    where, params = [], []
    if exclude_status:
        where.append("coalesce(status, '') NOT IN (%s)" % ",".join("?" * len(exclude_status)))
        params.extend(exclude_status)
    if session_id is not None:
        where.append("session_id=?")
        params.append(session_id)
    if source is not None:
        where.append("source=?")
        params.append(source)
    sql = ("SELECT * FROM chat_audit" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY ts DESC, id DESC LIMIT ?")
    params.append(max(0, int(limit)))
    with _conn(conn, read=True) as c:
        rows = [dict(r) for r in c.execute(sql, params)]
    for r in rows:
        try:
            r["meta"] = json.loads(r.pop("meta_json") or "null") or {}
        except ValueError:
            r["meta"] = {}
    return rows


def chat_audit_prune(now=None, force=False, days=CHAT_AUDIT_RETENTION_DAYS, conn=None):
    """Delete chat_audit rows older than `days`; unless forced, at most once per day (stamped in
    meta.chat_audit_pruned_at). Returns the number of rows deleted."""
    now = time.time() if now is None else float(now)
    with _conn(conn) as c:
        if not force:
            last = _meta_get(c, "chat_audit_pruned_at")
            try:
                if last is not None and now - float(last) < 86400:
                    return 0
            except ValueError:
                pass
        with _tx(c):
            n = c.execute("DELETE FROM chat_audit WHERE ts < ?", (now - days * 86400.0,)).rowcount
            _meta_set(c, "chat_audit_pruned_at", now)
    return n


# ── semantic-index queue ─────────────────────────────────────────────────────
def embed_enqueue(item_type, item_id, conn=None):
    """Queue one item for (re-)embedding; memory/lessons/chat_audit rows are queued by triggers."""
    with _conn(conn) as c, _tx(c):
        c.execute("INSERT OR REPLACE INTO embed_queue(item_type, item_id, enqueued) VALUES (?, ?, ?)",
                  (str(item_type), str(item_id), time.time()))


# ── stats / export ───────────────────────────────────────────────────────────
_TABLES = ("memory", "lessons", "ledger", "sessions", "scheduled_tasks", "chat_audit", "memories",
           "vault_fts", "vectors", "embed_queue")


def stats(conn=None):
    with _conn(conn, read=True) as c:
        tables = {}
        for t in _TABLES:
            try:
                tables[t] = c.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
            except sqlite3.OperationalError:
                tables[t] = None
        report = _meta_get(c, "migration_report")
        return {"path": db_path() if conn is None else None,
                "schema_version": _meta_get(c, "schema_version"),
                "migrated_v1": _meta_get(c, "migrated_v1"),
                "migration_report": json.loads(report) if report else None,
                "tables": tables}


# ── legacy migration ─────────────────────────────────────────────────────────
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


def _parse_ts(s, default):
    m = _TS_RE.match(str(s or "").strip())
    if m:
        try:
            return time.mktime(time.strptime(m.group(1).replace("T", " "), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            pass
    return default


def _decode(raw):
    """UTF-8 per line, falling back to latin-1 for a line that is not valid UTF-8 (hand edits)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        out = []
        for line in raw.split(b"\n"):
            try:
                out.append(line.decode("utf-8"))
            except UnicodeDecodeError:
                out.append(line.decode("latin-1"))
        return "\n".join(out)


def _read_text(path):
    with open(path, "rb") as f:
        return _decode(f.read())


def _mig_memory_txt(c, home, counts, renames):
    path = os.path.join(home, ".config", "ai", "memory.txt")
    if not os.path.isfile(path):
        return
    lines = [l.rstrip() for l in _read_text(path).splitlines() if l.strip()]
    base = os.path.getmtime(path) - len(lines)
    for i, line in enumerate(lines):
        if memory_add(line, conn=c) == "added":
            c.execute("UPDATE memory SET created=?, last_used=? WHERE id=last_insert_rowid()",
                      (base + i, base + i))
            counts["memory"] += 1
    renames.append(path)


def _mig_memory_db(c, home, counts, renames):
    path = os.path.join(home, ".config", "ai", "memory.db")
    if not os.path.isfile(path):
        return
    src = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=BUSY_TIMEOUT_MS / 1000.0)
    try:
        names = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "memories" in names:
            cols = {r[1] for r in src.execute("PRAGMA table_info(memories)")}
            sel = "SELECT content, %s FROM memories" % ("metadata" if "metadata" in cols else "''")
            for content, meta in src.execute(sel):
                c.execute("INSERT INTO memories(content, metadata) VALUES (?, ?)", (content, meta or ""))
                counts["memories"] += 1
        if "vault_fts" in names:
            for title, content in src.execute("SELECT title, content FROM vault_fts"):
                c.execute("INSERT INTO vault_fts(title, content) VALUES (?, ?)", (title, content))
                counts["vault"] += 1
    finally:
        src.close()
    renames.append(path)


def _parse_lesson_blocks(text, default_ts):
    """Parse lessons.md into (kind, tool, signature, body, created) tuples + a skipped count.
    Tolerates hand edits: preamble text, headers without a timestamp, missing tool/signature."""
    blocks, cur, preamble = [], None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if cur is not None:
                blocks.append(cur)
            cur = [line]
        elif cur is None:
            preamble.append(line)
        else:
            cur.append(line)
    if cur is not None:
        blocks.append(cur)
    out, skipped = [], 0
    pre = "\n".join(preamble).strip()
    if pre:
        out.append(("NOTE", "", "", pre, default_ts))
    for b in blocks:
        header = b[0][3:].strip()
        m = re.match(r"([A-Za-z_]+)\s*(.*)$", header)
        kind, rest = (m.group(1).upper(), m.group(2).strip()) if m else ("NOTE", header)
        created = _parse_ts(rest, None)
        tool = sig = ""
        body_lines = []
        for line in b[1:]:
            s = line.strip()
            if not body_lines and s.lower().startswith("tool:") and not tool:
                tool = s.split(":", 1)[1].strip()
            elif not body_lines and s.lower().startswith("signature:") and not sig:
                sig = s.split(":", 1)[1].strip()
            elif body_lines or s:
                body_lines.append(line)
        if body_lines and body_lines[0].startswith("> "):
            body_lines[0] = body_lines[0][2:]
        body = "\n".join(body_lines).strip()
        if created is None:
            created = default_ts
            if rest:
                body = (rest + "\n" + body).strip()
        if not body and not tool and not sig:
            skipped += 1
            continue
        out.append((kind, tool, sig, body, created))
    return out, skipped


def _mig_lessons(c, home, counts, renames):
    path = os.path.join(home, ".config", "ai", "self_improve", "lessons.md")
    if not os.path.exists(path):
        return
    text = _read_text(path)
    parsed, skipped = _parse_lesson_blocks(text, os.path.getmtime(path))
    counts["lessons_skipped"] += skipped
    for kind, tool, sig, body, created in parsed:
        if not sig:
            sig = "manual:" + hashlib.sha1(("%s|%s|%s" % (kind, tool, body)).encode()).hexdigest()[:12]
        # approved on migration with the standard 60-day ttl so expiry applies uniformly
        if lesson_upsert(kind, tool, sig, body, ts=created, expires=time.time() + 60 * 86400.0, conn=c):
            counts["lessons"] += 1
    renames.append(path)


def _mig_ledger(c, home, counts, renames):
    path = os.path.join(home, ".config", "ai", "self_improve", "ledger.jsonl")
    if not os.path.exists(path):
        return
    default = os.path.getmtime(path)
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except ValueError:
            counts["ledger_skipped"] += 1
            continue
        kind = rec.get("kind") or "failure"
        rid = ledger_append(tool=rec.get("tool", ""), ok=(kind == "recovery"),
                            error=rec.get("error") or rec.get("prior_error") or "",
                            chain_id=rec.get("chain_id", ""), args=rec.get("args") or rec.get("approach") or "",
                            kind=kind, signature=rec.get("signature", ""), phase=rec.get("phase", ""),
                            ts=_parse_ts(rec.get("ts"), default), max_rows=0, conn=c, imported=True)
        counts["ledger" if rid is not None else "ledger_duplicates"] += 1
    _ledger_keep_newest(c, LEDGER_MAX_ROWS)
    renames.append(path)


def task_next_run(spec, now=None):
    """When a task (old file format) is next due: (last_run or created_at) + interval."""
    now = time.time() if now is None else now
    try:
        interval = float(spec.get("interval_seconds", 300))
    except (TypeError, ValueError):
        interval = 300.0
    last = spec.get("last_run")
    base = _parse_ts(last, None) if last and last != "never" else None
    if base is None:
        base = _parse_ts(spec.get("created_at"), None)
    return (base if base is not None else now) + interval


def _mig_tasks(c, home, counts, renames, live):
    d = os.path.join(home, ".config", "ai", "scheduled_tasks")
    if not os.path.isdir(d):
        return
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(d, fn)
        try:
            spec = json.loads(_read_text(p))
            if not isinstance(spec, dict):
                raise ValueError("not an object")
        except (ValueError, OSError):
            counts["scheduled_tasks_skipped"] += 1
            renames.append(p)
            continue
        tid = "".join(ch for ch in str(spec.get("task_id") or fn[:-5]) if ch.isalnum() or ch in "_-")
        if not tid:
            counts["scheduled_tasks_skipped"] += 1
            renames.append(p)
            continue
        spec["task_id"] = tid
        task_put(tid, spec, next_run=task_next_run(spec), conn=c)
        counts["scheduled_tasks"] += 1
        renames.append(p)
        pid = _live_scheduler_pid(os.path.join(d, fn[:-5] + ".pid"), tid)
        if pid:
            live.append((tid, pid, os.path.join(d, fn[:-5] + ".pid")))


def _live_scheduler_pid(pid_file, tid):
    """PID of a running old-code `run-scheduler <tid>` daemon, verified via /proc (never a
    recycled pid that belongs to something else)."""
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            argv = f.read().split(b"\0")
    except (OSError, ValueError):
        return None
    return pid if b"run-scheduler" in argv and tid.encode() in argv else None


def _mig_chats(c, home, counts, renames):
    seen = set()
    for path in (os.path.join(home, ".local", "share", "ai", "zulip_chats.jsonl"),
                 os.path.join(home, ".cache", "ai", "zulip_chats.jsonl")):
        if not os.path.isfile(path):
            continue
        default = os.path.getmtime(path)
        for line in _read_text(path).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    raise ValueError("not an object")
            except ValueError:
                counts["chat_audit_skipped"] += 1
                continue
            key = (rec.get("session_id"), rec.get("timestamp"), rec.get("query"))
            if key in seen:
                continue
            seen.add(key)
            rid = chat_audit_add("zulip", rec.get("sender_email"), rec.get("query"), rec.get("reply_snippet"),
                                 ts=_parse_ts(rec.get("timestamp"), default), session_id=rec.get("session_id"),
                                 status=rec.get("status"), meta=rec, conn=c, imported=True)
            counts["chat_audit" if rid is not None else "chat_audit_duplicates"] += 1
        renames.append(path)


def _mig_sessions(c, home, counts):
    """Backfill the sessions index from the session transcript dirs (files stay where they are)."""
    for d in (os.path.join(home, ".local", "share", "ai", "sessions"),
              os.path.join(home, ".cache", "ai", "sessions")):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".json") or fn == "last.json" or ".tmp." in fn:
                continue
            p = os.path.join(d, fn)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            sid = fn[:-5]
            if c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is None:
                counts["sessions"] += 1
            session_upsert(sid, ended=mt, path=p, conn=c)


def _cfg(home, *parts):
    return os.path.join(home, ".config", "ai", *parts)


def _source_paths(name, home):
    """Legacy file(s) one migration source reads (and renames)."""
    return {
        "memory.txt": [_cfg(home, "memory.txt")],
        "memory.db": [_cfg(home, "memory.db")],
        "lessons.md": [_cfg(home, "self_improve", "lessons.md")],
        "ledger.jsonl": [_cfg(home, "self_improve", "ledger.jsonl")],
        "zulip_chats.jsonl": [os.path.join(home, ".local", "share", "ai", "zulip_chats.jsonl"),
                              os.path.join(home, ".cache", "ai", "zulip_chats.jsonl")],
    }.get(name, [])


def _present(name, home):
    """Stat-only: does this legacy source currently exist (again) under `home`?"""
    if name == "scheduled_tasks":
        d = _cfg(home, "scheduled_tasks")
        try:
            return any(fn.endswith(".json") for fn in os.listdir(d))
        except OSError:
            return False
    return any(os.path.exists(p) for p in _source_paths(name, home))


# (source name, importer(conn, home, counts, renames, live)); per-source state in meta:
# migrated:<name> (last successful import) / migrate_failed:<name> (last error, retried only
# by migrate_legacy(retry=True) i.e. `ai_mcp.py state-migrate --retry`).
_SOURCES = (
    ("memory.txt", lambda c, h, k, r, l: _mig_memory_txt(c, h, k, r)),
    ("memory.db", lambda c, h, k, r, l: _mig_memory_db(c, h, k, r)),
    ("lessons.md", lambda c, h, k, r, l: _mig_lessons(c, h, k, r)),
    ("ledger.jsonl", lambda c, h, k, r, l: _mig_ledger(c, h, k, r)),
    ("zulip_chats.jsonl", lambda c, h, k, r, l: _mig_chats(c, h, k, r)),
    ("scheduled_tasks", lambda c, h, k, r, l: _mig_tasks(c, h, k, r, l)),
)
SOURCE_NAMES = tuple(n for n, _ in _SOURCES)
_COUNT_KEYS = ("memory", "memories", "vault", "lessons", "lessons_skipped", "ledger", "ledger_skipped",
               "ledger_duplicates", "scheduled_tasks", "scheduled_tasks_skipped", "chat_audit",
               "chat_audit_skipped", "chat_audit_duplicates", "sessions", "renamed", "errors")


def _bak_name(path):
    bak = path + ".migrated.bak"
    n = 1
    while os.path.exists(bak):
        bak = "%s.migrated.bak.%d" % (path, n)
        n += 1
    return bak


def _pending(c, home, retry):
    """Sources to import now: everything on the first pass; afterwards only legacy files that
    exist again (written by a still-running old-code process, or hand-made) - stat only - minus
    the ones whose last attempt failed unless retry."""
    if not _meta_get(c, "migrated_v1"):
        return list(SOURCE_NAMES), True
    todo = []
    for name in SOURCE_NAMES:
        if not _present(name, home):
            continue
        if not retry and _meta_get(c, "migrate_failed:" + name):
            continue
        todo.append(name)
    return todo, False


def migrate_legacy(home=None, conn=None, retry=False):
    """Import the legacy state files under `home` into the state db.

    First pass (no meta.migrated_v1): every source, plus a sessions-index backfill, in ONE
    transaction that also sets migrated_v1, so a concurrent process sees nothing or everything.
    Each source runs under its own savepoint: one that fails is rolled back, recorded as
    meta.migrate_failed:<source> (and in counts['errors'] / migration_report) and left in place,
    while the rest is imported (meta.migrated:<source>). After COMMIT the imported files are
    renamed to *.migrated.bak (.migrated.bak.N if taken); nothing is deleted.

    Later calls (each process's first connect(), `state-migrate`) are a stat per source: a legacy
    file that exists again is imported and renamed again (second pass). Failed sources are only
    re-attempted with retry=True (`state-migrate --retry`). Returns per-category counts, or {}
    when there was nothing to do."""
    home = home or _home()
    own = conn is None
    c = connect(migrate=False) if own else conn
    counts = dict.fromkeys(_COUNT_KEYS, 0)
    renames, live, errors = [], [], []
    try:
        todo, _ = _pending(c, home, retry)
        if not todo:
            return {}
        with _tx(c):
            todo, first_pass = _pending(c, home, retry)  # re-check under the write lock
            if not todo:
                return {}
            steps = [(n, fn) for n, fn in _SOURCES if n in todo]
            if first_pass:
                steps.append(("sessions", lambda c_, h, k, r, l: _mig_sessions(c_, h, k)))
            for name, fn in steps:
                before = len(renames)
                c.execute("SAVEPOINT mig")
                try:
                    fn(c, home, counts, renames, live)
                    c.execute("RELEASE mig")
                    c.execute("DELETE FROM meta WHERE key=?", ("migrate_failed:" + name,))
                    _meta_set(c, "migrated:" + name, time.time())
                except Exception as e:
                    c.execute("ROLLBACK TO mig")
                    c.execute("RELEASE mig")
                    del renames[before:]
                    counts["errors"] += 1
                    errors.append("%s: %s" % (name, e))
                    _meta_set(c, "migrate_failed:" + name, "%s: %s" % (type(e).__name__, e))
            if first_pass:
                _meta_set(c, "migrated_v1", json.dumps({"at": time.time(), "home": home}))
            _meta_set(c, "migration_report", json.dumps(dict(counts, error_details=errors)))
        for p in renames:
            try:
                os.replace(p, _bak_name(p))
                counts["renamed"] += 1
            except OSError as e:
                counts["errors"] += 1
                errors.append("rename %s: %s" % (p, e))
        _restart_schedulers(live)
        if errors or counts["renamed"]:
            # the report stored inside the transaction predates the renames: record the real count
            with _tx(c):
                _meta_set(c, "migration_report", json.dumps(dict(counts, error_details=errors)))
        return counts
    finally:
        if own:
            c.close()


def _restart_schedulers(live):
    """An old-code scheduler daemon polls its task FILE and would treat the rename as a cancel;
    replace each live one with a daemon of the current code that reads the db."""
    if not live:
        return
    import signal
    import subprocess
    import sys
    from . import entry_script
    for tid, pid, pid_file in live:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass
        try:
            subprocess.Popen([sys.executable, entry_script(), "run-scheduler", tid],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        except Exception:
            pass
