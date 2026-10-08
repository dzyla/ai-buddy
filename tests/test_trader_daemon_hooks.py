"""Daemon hook wiring: run_scheduled_trading_lifecycle calls the shadow hook after the live pulse in
the regular session, and the daily-summary hook in the close window and in off-hours. Everything
the loop touches is mocked (no broker, no network); time.sleep ends the loop after the scripted
iterations."""
import datetime

import pytest

from trader import daemon, shadow, summary
from trader.market_hours import MarketHours
from trader.config import ET_ZONE


class _Stop(Exception):
    pass


def _run(monkeypatch, times, auto_trade=True):
    events = []
    it = iter(times)
    current = {"now": None}

    def now_et():
        return current["now"]

    def sleep(_s):
        events.append(("sleep",))
        try:
            current["now"] = next(it)
        except StopIteration:
            raise _Stop()

    current["now"] = next(it)
    monkeypatch.setattr(MarketHours, "now_et", staticmethod(now_et))
    monkeypatch.setattr(daemon.time, "sleep", sleep)
    monkeypatch.setattr(daemon.RobinhoodExecutor, "get_agentic_account_number", classmethod(lambda cls: "ACC1"))
    monkeypatch.setattr(daemon, "_risk_monitor_pulse", lambda acc, dry: events.append(("risk", acc)))
    monkeypatch.setattr(daemon, "_momentum_rebalance_pulse", lambda acc, dry: events.append(("momentum", acc)))
    monkeypatch.setattr(shadow, "maybe_run_shadow", lambda acc, now: events.append(("shadow", now)))
    monkeypatch.setattr(summary, "maybe_send_daily_summary", lambda now: events.append(("summary", now)))
    monkeypatch.setattr(daemon.TradingStrategyEngine, "generate_premarket_briefing",
                        classmethod(lambda cls, wl: events.append(("premarket",)) or {}))
    monkeypatch.setattr(daemon.TradingStrategyEngine, "generate_daily_closing_summary",
                        classmethod(lambda cls: events.append(("closing",)) or {}))
    with pytest.raises(_Stop):
        daemon.run_scheduled_trading_lifecycle(interval_seconds=60, auto_trade=auto_trade, dry_run=True)
    return events


def _at(day, h, m):
    return datetime.datetime(2026, 9, day, h, m, tzinfo=ET_ZONE)


def test_regular_session_runs_shadow_after_the_live_pulse(monkeypatch):
    events = _run(monkeypatch, [_at(8, 10, 30)])
    names = [e[0] for e in events]
    assert names == ["risk", "momentum", "shadow", "sleep"], names
    assert events[2][1] == _at(8, 10, 30)
    assert "summary" not in names


def test_shadow_hook_runs_even_when_the_live_pulse_fails(monkeypatch):
    real_run = daemon.run_scheduled_trading_lifecycle

    def failing_pulse(*a, **k):
        raise RuntimeError("broker down")

    def run_with_failing_pulse(**kw):
        monkeypatch.setattr(daemon, "_risk_monitor_pulse", failing_pulse)  # after _run's own mock
        return real_run(**kw)
    monkeypatch.setattr(daemon, "run_scheduled_trading_lifecycle", run_with_failing_pulse)
    names = [e[0] for e in _run(monkeypatch, [_at(8, 10, 30)])]
    assert names == ["shadow", "sleep"], names


def test_close_window_and_off_hours_run_the_summary_hook(monkeypatch):
    # Tue 16:05 (close window) -> Tue 20:00 (off-hours) -> Sat 12:00 (off-hours, weekend)
    events = _run(monkeypatch, [_at(8, 16, 5), _at(8, 20, 0), _at(12, 12, 0)])
    names = [e[0] for e in events]
    assert names == ["closing", "summary", "sleep", "summary", "sleep", "summary", "sleep"], names
    assert [e[1] for e in events if e[0] == "summary"] == [_at(8, 16, 5), _at(8, 20, 0), _at(12, 12, 0)]
    assert "shadow" not in names and "momentum" not in names
