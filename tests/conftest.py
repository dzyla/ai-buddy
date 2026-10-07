"""Test isolation for robinhood_trader: never let a test touch live trading state.

Every path the trader persists to (PDT tracker, circuit-breaker baseline, monitor flags,
risk log, Obsidian vault, trading state, cache dir, journal) is redirected to tmp_path.
"""
import os
import pytest


@pytest.fixture(autouse=True)
def isolate_trader_state(tmp_path, monkeypatch):
    try:
        import robinhood_trader as rt
    except Exception:  # pragma: no cover - module not importable in this test run
        yield
        return
    root = tmp_path / "trader_state"
    cfg = root / "config"
    cache = root / "cache"
    flags = cfg / ".monitor_flags"
    vault = cfg / "trading_vault"
    for d in (cfg, cache, flags, vault):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rt, "CONFIG_DIR", str(cfg))
    monkeypatch.setattr(rt, "CACHE_DIR", str(cache))
    monkeypatch.setattr(rt, "TRADING_STATE_FILE", str(cfg / "robinhood_trading_state.json"))
    monkeypatch.setattr(rt, "VAULT_DIR", str(vault))
    monkeypatch.setattr(rt, "PDT_TRACKER_FILE", str(cfg / "pdt_tracker.json"))
    monkeypatch.setattr(rt, "CIRCUIT_BREAKER_FILE", str(cfg / "daily_circuit_breaker.json"))
    monkeypatch.setattr(rt, "SESSION_HALT_FLAG", str(flags / "tier3_halt_{date}.flag"))
    monkeypatch.setattr(rt, "DEFERRED_EXIT_FLAG_DIR", str(flags))
    monkeypatch.setattr(rt, "RISK_MONITOR_FLAGS", str(flags))
    monkeypatch.setattr(rt, "RISK_MONITOR_LOG", str(cache / "risk_monitor.log"))
    if hasattr(rt, "TRADING_HOLD_FILE"):
        monkeypatch.setattr(rt, "TRADING_HOLD_FILE", str(cfg / "trading_hold.txt"))
    yield


@pytest.fixture(autouse=True)
def isolate_state_db(tmp_path, monkeypatch):
    """Point the SQLite state store (aitools/state.py) at a per-test file.

    With $AI_STATE_DB set, state.connect() never runs the lazy legacy migration, so no test can
    import from (or rename files in) the real HOME. Subprocesses inherit the variable."""
    monkeypatch.setenv("AI_STATE_DB", str(tmp_path / "state.db"))
    yield


@pytest.fixture(autouse=True)
def auto_approve_lessons(monkeypatch):
    """Capture/recall tests predate the review queue; keep them on the old auto-approve behaviour.
    tests/test_learning_queue.py deletes this variable to exercise the human-review default."""
    monkeypatch.setenv("INFER_LEARNING_AUTO_APPROVE", "1")


@pytest.fixture(autouse=True)
def isolate_embedder(monkeypatch):
    """Never reach a real embedder (aitools/embed.py) from a test: AI_EMBED_URL points at a port
    nothing listens on (connection refused at once); tests/test_semantic_search.py starts a fake
    one. INFER_EMBED_AUTODRAIN=0: no idle-time queue drain by a tool server a test starts."""
    monkeypatch.setenv("AI_EMBED_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("INFER_EMBED_AUTODRAIN", "0")
    monkeypatch.delenv("INFER_RECALL_K", raising=False)
    yield


@pytest.fixture(autouse=True)
def no_tool_server(monkeypatch):
    """Never let a test's `ai` run reach (or spawn) the persistent tool server in the real runtime
    dir: INFER_TOOL_SERVER=0 for every test (the root conftest.py also sets it session-wide, for
    module-scoped fixtures). Helpers that scrub INFER_* keep INFER_TOOL_SERVER.
    tests/test_tool_server.py opts in with a private XDG_RUNTIME_DIR and HOME."""
    monkeypatch.setenv("INFER_TOOL_SERVER", "0")


@pytest.fixture(params=["c", "py"])
def engine(request, monkeypatch):
    """Run a binary-driving test once per agent engine: the C loop (`INFER_ENGINE=c`) and the
    Python engine behind the C front-end (`INFER_ENGINE=py`, agent/ package). Subprocesses inherit
    the variables; every test env scrubber keeps INFER_ENGINE and INFER_ENGINE_STRICT (like
    INFER_TOOL_SERVER)."""
    monkeypatch.setenv("INFER_ENGINE", request.param)
    # never let a py variant silently fall back to the C loop when agent/ is not found
    monkeypatch.setenv("INFER_ENGINE_STRICT", "1")
    return request.param
