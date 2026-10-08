"""McpPool closes retired sessions OUTSIDE the global pool lock (McpSession.close() can take
~2.5 s and must not stall calls to every other server), exactly once, and never under a call
still in flight."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from aitools import mcp_client  # noqa: E402


class FakeSession:
    pool = None
    instances = []

    def __init__(self, name, cfg, environ):
        import json
        self.name, self.cfg_key = name, json.dumps(cfg, sort_keys=True)
        self.inflight, self.retired, self._closing = 0, False, False
        self.closed, self.closed_under_lock, self._alive = 0, [], True
        FakeSession.instances.append(self)

    def alive(self):
        return self._alive

    def close(self):
        self.closed += 1
        self.closed_under_lock.append(FakeSession.pool._lock.locked())


def _pool(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(mcp_client, "McpSession", FakeSession)
    pool = mcp_client.McpPool()
    FakeSession.pool = pool
    return pool


def test_dead_session_is_closed_outside_the_lock(monkeypatch):
    pool = _pool(monkeypatch)
    s1, fresh = pool._acquire("a", {"command": "x"}, None)
    assert fresh
    pool._release(s1)
    s1._alive = False
    s2, fresh = pool._acquire("a", {"command": "x"}, None)
    assert fresh and s2 is not s1
    assert s1.closed == 1 and s1.closed_under_lock == [False]


def test_dropped_session_is_closed_outside_the_lock(monkeypatch):
    pool = _pool(monkeypatch)
    s1, _ = pool._acquire("a", {"command": "x"}, None)
    pool._release(s1, drop=True)       # e.g. a timed-out call
    assert s1.closed == 1 and s1.closed_under_lock == [False]


def test_replaced_session_with_calls_in_flight_closes_once_after_the_last(monkeypatch):
    pool = _pool(monkeypatch)
    a1, _ = pool._acquire("a", {"command": "x"}, None)
    a2, _ = pool._acquire("a", {"command": "x"}, None)
    assert a1 is a2 and a1.inflight == 2
    b, fresh = pool._acquire("a", {"command": "y"}, None)    # config changed: a1 retired
    assert fresh and b is not a1 and a1.retired and a1.closed == 0
    pool._release(a1)
    assert a1.closed == 0                                     # one call still running on it
    pool._release(a1, drop=True)
    assert a1.closed == 1 and a1.closed_under_lock == [False]
