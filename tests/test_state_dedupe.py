"""Legacy re-import must be idempotent for the append-only tables (ledger, chat_audit).

Incident: a restored copy of ledger.jsonl and zulip_chats.jsonl was re-imported by the second
migration pass and doubled both tables (no uniqueness). Imported rows now carry `import_key`
(a hash over every non-id column, unique index) and the importers skip rows already present.
Schema v4 dedupes the exact duplicates an older pass left behind (keeping the lowest id) before
adding the index. Rows written live (ledger_append / chat_audit_add) carry no key and are never
deduplicated.
"""
import json
import os
import shutil
import sqlite3
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from aitools import state  # noqa: E402
from test_state_store import _legacy_home  # noqa: E402

LEDGER = ".config/ai/self_improve/ledger.jsonl"
CHATS = (".local/share/ai/zulip_chats.jsonl", ".cache/ai/zulip_chats.jsonl")


def _count(table):
    with state.connect(migrate=False) as c:
        return c.execute("SELECT count(*) FROM %s" % table).fetchone()[0]


def _restore(home, rel):
    """Copy <rel>.migrated.bak back to <rel> (= the owner restoring a backup)."""
    shutil.copy(str(home / (rel + ".migrated.bak")), str(home / rel))


def test_reimport_of_restored_legacy_files_adds_zero_rows(tmp_path):
    home = _legacy_home(tmp_path)
    first = state.migrate_legacy(str(home))
    assert first["ledger"] == 3 and first["chat_audit"] == 2
    n_ledger, n_chat = _count("ledger"), _count("chat_audit")
    for rel in (LEDGER,) + CHATS:
        _restore(home, rel)
    again = state.migrate_legacy(str(home))
    assert _count("ledger") == n_ledger and _count("chat_audit") == n_chat
    assert again["ledger"] == 0 and again["chat_audit"] == 0
    assert again["ledger_duplicates"] == 3 and again["chat_audit_duplicates"] == 2
    # the restored copies were consumed (renamed) like any imported file
    assert not (home / LEDGER).exists() and (home / (LEDGER + ".migrated.bak.1")).exists()


def test_reimport_adds_only_the_new_lines(tmp_path):
    home = _legacy_home(tmp_path)
    state.migrate_legacy(str(home))
    n = _count("ledger")
    _restore(home, LEDGER)
    with open(str(home / LEDGER), "a") as f:
        f.write(json.dumps({"ts": "2026-10-01 10:00:00", "kind": "failure", "tool": "late", "error": "x",
                            "chain_id": "c"}) + "\n")
    again = state.migrate_legacy(str(home))
    assert again["ledger"] == 1 and again["ledger_duplicates"] == 3
    assert _count("ledger") == n + 1


def test_live_rows_are_never_deduplicated():
    for _ in range(2):
        state.ledger_append(tool="t", ok=False, error="boom", ts=5.0)
        state.chat_audit_add("zulip", "a", "same question", "same reply", ts=7.0)
    assert state.ledger_count() == 2
    assert len(state.chat_audit_recent(limit=10)) == 2


def _downgrade_to_v3(path):
    """Strip the v4 additions so the db looks like one an older version wrote."""
    c = sqlite3.connect(path)
    for t in ("ledger", "chat_audit"):
        c.execute("DROP INDEX IF EXISTS %s_import_key" % t)
        c.execute("ALTER TABLE %s DROP COLUMN import_key" % t)
    c.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    c.commit()
    c.close()
    state._ready.clear()


def test_schema_migration_dedupes_existing_duplicates_and_reimport_is_then_a_noop(tmp_path):
    home = _legacy_home(tmp_path)
    state.migrate_legacy(str(home))
    state.ledger_append(tool="live", ok=True, kind="recovery", ts=9.0)        # a live row, kept
    path = state.db_path()
    _downgrade_to_v3(path)
    # the incident: an old second pass imported the restored files again (exact copies)
    c = sqlite3.connect(path)
    c.execute("INSERT INTO ledger(ts, chain_id, tool, args_hash, ok, error, kind, signature, phase) "
              "SELECT ts, chain_id, tool, args_hash, ok, error, kind, signature, phase FROM ledger "
              "WHERE tool != 'live'")
    c.execute("INSERT INTO chat_audit(ts, source, sender, prompt, reply, session_id, status, meta_json) "
              "SELECT ts, source, sender, prompt, reply, session_id, status, meta_json FROM chat_audit")
    c.commit()
    ids_before = [r[0] for r in c.execute("SELECT id FROM chat_audit ORDER BY id")]
    c.close()
    assert _count_raw(path, "ledger") == 7 and _count_raw(path, "chat_audit") == 4

    with state.connect(migrate=False) as conn:          # opens -> schema v4 upgrade
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "4"
        kept = [r[0] for r in conn.execute("SELECT id FROM chat_audit ORDER BY id")]
        idx = {r[1] for r in conn.execute("PRAGMA index_list(ledger)") if r[2]}
        fts = conn.execute("SELECT count(*) FROM item_fts WHERE item_type='chat'").fetchone()[0]
    assert _count("ledger") == 4 and _count("chat_audit") == 2
    assert kept == ids_before[:2]                         # lowest ids kept
    assert "ledger_import_key" in idx
    assert fts == 2                                       # deleted rows left the search index too

    for rel in (LEDGER,) + CHATS:
        _restore(home, rel)
    again = state.migrate_legacy(str(home))
    assert again["ledger"] == 0 and again["chat_audit"] == 0
    assert _count("ledger") == 4 and _count("chat_audit") == 2


def _count_raw(path, table):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
    finally:
        c.close()


def test_upgrade_keeps_distinct_rows_that_differ_in_one_column(tmp_path):
    state.ledger_append(tool="t", ok=False, error="a", ts=1.0)
    state.ledger_append(tool="t", ok=False, error="b", ts=1.0)
    path = state.db_path()
    _downgrade_to_v3(path)
    with state.connect(migrate=False):
        pass
    assert _count("ledger") == 2
    t0 = time.time()
    with state.connect(migrate=False):                    # already v4: no second upgrade
        pass
    assert time.time() - t0 < 1.0
