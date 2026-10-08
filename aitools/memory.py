"""Persistent memory (memory.txt + FTS memories), vault notes and the RLM context pool."""
import os
import json
import re
import time
import sqlite3

from . import state

VAULT_DIR = os.path.expanduser("~/.config/ai/vault")

# --- Helper functions for improvements ---
CONTEXT_POOL_FILE = os.path.expanduser("~/.config/ai/context_pool.json")

def _load_context_pool():
    if os.path.exists(CONTEXT_POOL_FILE):
        try:
            with open(CONTEXT_POOL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def _save_context_pool(pool):
    os.makedirs(os.path.dirname(CONTEXT_POOL_FILE), exist_ok=True)
    with open(CONTEXT_POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, indent=2)

def append_to_context_pool(entry):
    pool = _load_context_pool()
    pool.append({"id": len(pool), "timestamp": time.time(), "content": str(entry)})
    _save_context_pool(pool)

def get_context_snippet(index):
    pool = _load_context_pool()
    try:
        idx = int(index)
        if 0 <= idx < len(pool):
            return json.dumps(pool[idx])
        return f"Error: Context snippet index {idx} out of range (0-{len(pool)-1})."
    except Exception as e:
        return f"Error: {e}"

def search_context(query):
    pool = _load_context_pool()
    if not query:
        return "Error: query required"
    results = []
    q_lower = query.lower()
    for item in pool:
        content = item.get("content", "")
        if q_lower in content.lower():
            results.append(f"[{item['id']}]: {content[:400]}")
    if not results:
        return f"No context entries matching query '{query}'."
    return "\n".join(results)


# Legacy locations, kept only as names: memory.txt and memory.db are imported into the state store
# (aitools/state.py) by its one-time migration and renamed to *.migrated.bak. Nothing reads them.
MEMORY_PATH = os.path.expanduser("~/.config/ai/memory.txt")
MEMORY_DB = os.path.expanduser("~/.config/ai/memory.db")

MEMORY_CAP = 4000


def _state_conn():
    """The state db: the `memories` (remember/recall) and `vault_fts` FTS tables live there now."""
    return state.connect()


def save_memory(content, replace=False, pinned=False):
    """Add facts to persistent memory (injected into every system prompt).

    Appends by default. Lines already present (case-insensitive) are not duplicated; when the
    4 KB cap is exceeded the OLDEST whole lines are evicted (never a mid-line cut) and the result
    says so. replace=True rewrites. pinned=True marks the lines (new or already present) as core
    memories that auto-recall always injects. Stored in the state db's `memory` table."""
    try:
        new_lines = [l.rstrip() for l in str(content or "").splitlines() if l.strip()]
        if not new_lines:
            return "Error: save_memory needs non-empty content."
        conn = state.connect()
        try:
            with state.transaction(conn):
                if replace:
                    state.memory_clear(conn=conn)
                added = [l for l in new_lines if state.memory_add(l, pinned=pinned, conn=conn) == "added"]
                evicted = state.memory_evict(MEMORY_CAP, conn=conn)
                lines = state.memory_list(conn=conn)
        finally:
            conn.close()
        if not added and not replace:
            if pinned:
                return "Memory already contains that; now pinned (always injected)."
            return "Memory already contains that (no duplicate added)."
        msg = "Memory updated successfully (%d line(s) added, %d/%d chars used)." % (
            len(added) if not replace else len(lines), len("\n".join(lines)), MEMORY_CAP)
        if pinned:
            msg += " Pinned (always injected)."
        if evicted:
            msg += " Memory was full: evicted %d oldest line(s): %s" % (
                len(evicted), " | ".join(e[:60] for e in evicted[:3]))
        return msg
    except Exception as e:
        return f"Error saving memory: {e}"

def _enqueue(conn, note_id):
    """Queue a note for the semantic index (aitools/search.py); never fails the write."""
    try:
        state.embed_enqueue("note", note_id, conn=conn)
    except Exception:
        pass


def _ensure_memories_schema(conn):
    """Ensure the memories FTS5 table has a metadata column.
    FTS5 virtual tables cannot be ALTERED, so we rebuild if needed."""
    # First, make sure the table exists at all (old schema with just content)
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(content)")
    # Check if metadata column exists
    try:
        cursor = conn.execute("PRAGMA table_info(memories)")
        columns = {row[1] for row in cursor.fetchall()}
    except Exception:
        columns = set()
    if "metadata" in columns:
        return  # Already has the column
    # Rebuild: backup content, drop table, recreate with metadata, reinsert
    old_rows = conn.execute("SELECT content FROM memories").fetchall()
    conn.execute("DROP TABLE IF EXISTS memories")
    conn.execute(
        "CREATE VIRTUAL TABLE memories USING fts5(content, metadata)"
    )
    for (content,) in old_rows:
        conn.execute(
            "INSERT INTO memories(content, metadata) VALUES (?, ?)",
            (content, ""),
        )
    conn.commit()


def _ensure_vault_schema(conn):
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vault_fts USING fts5(title, content);"
    )

def vault_write(title, content, links=""):
    try:
        os.makedirs(VAULT_DIR, exist_ok=True)
        title = title.replace("/", "_").replace("\\\\", "_")
        if not title.endswith(".md"):
            title += ".md"
        
        path = os.path.join(VAULT_DIR, title)
        
        full_content = content
        if links:
            # Parse links (comma separated) and format as [[Link]]
            link_list = [l.strip() for l in links.split(",") if l.strip()]
            if link_list:
                full_content += "\n\n---\n**Links:** " + ", ".join([f"[[{l}]]" for l in link_list])
                
        with open(path, "w") as f:
            f.write(full_content)
            
        # Update SQLite index: one BEGIN IMMEDIATE transaction (lock retry), connection always closed
        conn = _state_conn()
        try:
            _ensure_vault_schema(conn)
            with state.transaction(conn):
                conn.execute("DELETE FROM vault_fts WHERE title = ?", (title,))
                conn.execute("INSERT INTO vault_fts(title, content) VALUES (?, ?)", (title, full_content))
                _enqueue(conn, "v:" + title)
        finally:
            conn.close()
        return f"Successfully wrote note '{title}' to vault."
    except Exception as e:
        return f"Error writing to vault: {e}"

def vault_read(title):
    try:
        if not title.endswith(".md"):
            title += ".md"
        path = os.path.join(VAULT_DIR, title)
        if not os.path.exists(path):
            return f"Note '{title}' not found in vault."
        with open(path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading vault note: {e}"

def vault_search(query):
    try:
        conn = _state_conn()
        _ensure_vault_schema(conn)
        
        words = re.findall(r"\w+", query)
        rows = []
        try:
            def _try_vault(q, what):
                if what == "fts":
                    return conn.execute(
                        "SELECT title, content FROM vault_fts WHERE vault_fts MATCH ? LIMIT 10",
                        (q,),
                    ).fetchall()
                return conn.execute(
                    "SELECT title, content FROM vault_fts WHERE content LIKE ? OR title LIKE ? LIMIT 10",
                    (f"%{q}%", f"%{q}%"),
                ).fetchall()

            if words:
                phrase = '"' + " ".join(words) + '"'
                rows = _try_vault(phrase, "fts") or _try_vault(" AND ".join(words), "fts") or _try_vault(" OR ".join(words), "fts")
            if not rows and words:
                rows = []
                for w in words[:3]:
                    rows = _try_vault(w, "like")
                    if rows:
                        break
        finally:
            conn.close()
        
        if not rows:
            return "No matching notes found."
            
        results = []
        for t, c in rows:
            preview = c[:200].replace("\n", " ") + ("..." if len(c) > 200 else "")
            results.append(f"- **{t}**: {preview}")
        return "\n".join(results)
    except Exception as e:
        return f"Error searching vault: {e}"

def vault_backlinks(title):
    try:
        if title.endswith(".md"):
            title = title[:-3] # Remove .md for backlink search
        conn = _state_conn()
        try:
            _ensure_vault_schema(conn)
            # Search for [[title]] using exact phrase match
            safe_query = f'"{title}"'
            rows = conn.execute(
                "SELECT title FROM vault_fts WHERE content MATCH ? LIMIT 20",
                (safe_query,),
            ).fetchall()
        finally:
            conn.close()
        
        if not rows:
            return f"No backlinks found for '{title}'."
            
        results = [f"- [[{t[0]}]]" for t in rows]
        return f"Notes linking to '{title}':\n" + "\n".join(results)
    except Exception as e:
        return f"Error finding backlinks: {e}"


def remember(content, metadata=""):
    """Save a piece of information to the FTS5 memory database with optional metadata."""
    try:
        if len(content) > 4000:
            content = content[-4000:]
        if metadata:
            metadata = str(metadata)[:200]
        conn = _state_conn()
        try:
            _ensure_memories_schema(conn)
            with state.transaction(conn):   # BEGIN IMMEDIATE + lock retry, rolled back on error
                cur = conn.execute(
                    "INSERT INTO memories(content, metadata) VALUES (?, ?)",
                    (content, metadata),
                )
                _enqueue(conn, "r%d" % cur.lastrowid)
        finally:
            conn.close()
        return f"Remembered: {content[:80]}{'...' if len(content) > 80 else ''}"
    except Exception as e:
        return f"Error saving memory: {e}"


def recall(query):
    """Search memories using FTS5 full-text search with fallback to LIKE search."""
    try:
        conn = _state_conn()
        try:
            _ensure_memories_schema(conn)
        
            # Extract alphanumeric words for safe FTS query
            words = re.findall(r'\w+', query)
            fts_query = " OR ".join(words) if words else query
        
            rows = []
            if fts_query:
                try:
                    rows = conn.execute(
                        "SELECT content, metadata FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT 10",
                        (fts_query,),
                    ).fetchall()
                except sqlite3.Error:
                    rows = []

            if not rows and words:
                # Fallback to LIKE search for key words
                like_clauses = " OR ".join(["content LIKE ? OR metadata LIKE ?" for _ in words[:5]])
                like_params = []
                for w in words[:5]:
                    like_params.extend([f"%{w}%", f"%{w}%"])
                rows = conn.execute(
                    f"SELECT content, metadata FROM memories WHERE {like_clauses} LIMIT 10",
                    tuple(like_params),
                ).fetchall()

        finally:
            conn.close()
        if not rows:
            return "No memories found matching that query."
        results = []
        for content, meta in rows:
            entry = content
            if meta:
                entry += f"\n[metadata: {meta}]"
            results.append(entry)
        return "\n---\n".join(results)
    except Exception as e:
        return f"Error searching memories: {e}"
