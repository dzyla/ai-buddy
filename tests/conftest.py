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
