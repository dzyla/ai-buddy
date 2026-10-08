"""aitools/memory.py remember / vault_write: one BEGIN IMMEDIATE transaction through the state
store (lock retry instead of failing on a briefly held write lock) and the connection is closed
even when the write fails. State db: tmp $AI_STATE_DB (tests/conftest.py)."""
import os
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from aitools import memory, state  # noqa: E402


def _hold_lock(seconds):
    holder = state.connect()
    holder.execute("BEGIN IMMEDIATE")
    t = threading.Timer(seconds, lambda: (holder.execute("ROLLBACK"), holder.close()))
    t.start()
    return t


def test_remember_waits_for_a_briefly_held_write_lock():
    state.connect().close()
    t = _hold_lock(0.5)
    try:
        out = memory.remember("the lab printer is on floor 3")
    finally:
        t.join()
    assert out.startswith("Remembered"), out
    assert "floor 3" in memory.recall("printer")


def test_vault_write_waits_for_a_briefly_held_write_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "VAULT_DIR", str(tmp_path / "vault"))
    state.connect().close()
    t = _hold_lock(0.5)
    try:
        out = memory.vault_write("Note A", "body text")
    finally:
        t.join()
    assert out.startswith("Successfully"), out
    assert "Note A" in memory.vault_search("body")


class _Conn:
    """Proxy over a real connection that fails the INSERT and records close()."""

    def __init__(self, real, closed):
        self._real, self._closed = real, closed

    def execute(self, sql, *a):
        if sql.lstrip().upper().startswith("INSERT"):
            raise RuntimeError("disk I/O error")
        return self._real.execute(sql, *a)

    def close(self):
        self._closed.append(1)
        self._real.close()

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_failed_writes_still_close_the_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "VAULT_DIR", str(tmp_path / "vault"))
    closed = []
    real_connect = state.connect
    monkeypatch.setattr(memory, "_state_conn", lambda: _Conn(real_connect(), closed))
    assert memory.remember("x").startswith("Error")
    assert memory.vault_write("T", "y").startswith("Error")
    assert len(closed) == 2
    # no transaction was left open: a fresh writer gets the lock at once
    c = real_connect()
    t0 = time.time()
    c.execute("BEGIN IMMEDIATE")
    c.execute("ROLLBACK")
    c.close()
    assert time.time() - t0 < 1
