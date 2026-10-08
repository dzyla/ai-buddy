"""Daily trader summary: built from local state files only (fake numbers, fake account 000000001),
delivered once per trading day to Zulip (mocked), with notify-send / log fallbacks."""
import datetime
import json
import os
import re
import sys
import types

import pytest

import robinhood_trader as rt
from robinhood_trader import MarketHours, ET_ZONE
from trader import summary

DAY = "2026-09-08"
FAKE_ACC = "000000001"


@pytest.fixture
def journal(monkeypatch):
    """Fixture state for one trading day: circuit-breaker baseline, close equity, ledger, rebalance flag, shadow line."""
    monkeypatch.setattr(MarketHours, "now_et", lambda: datetime.datetime(2026, 9, 8, 16, 5, tzinfo=ET_ZONE))
    with open(rt.CIRCUIT_BREAKER_FILE, "w") as fh:
        json.dump({f"{FAKE_ACC}_{DAY}": {"baseline": 1000.0, "pending_tier": 0, "pending_count": 0},
                   f"{FAKE_ACC}_2026-09-07": {"baseline": 990.0, "pending_tier": 0, "pending_count": 0}}, fh)
    summary.record_equity_snapshot(DAY, {"equity": 1012.5, "cash": 101.25, "pos_map": {
        "VTI": {"quantity": 1.0, "price": 300.0}, "QQQ": {"quantity": 0.5, "price": 600.0},
        "NVDA": {"quantity": 0.25, "price": 200.0}, "PLTR": {"quantity": 0.5, "price": 100.0}}})
    led = os.path.join(rt.VAULT_DIR, "retrospectives")
    os.makedirs(led, exist_ok=True)
    with open(os.path.join(led, "trade_ledger.jsonl"), "w") as fh:
        for rec in ({"timestamp": "2026-09-07T10:05:00-04:00", "ticker": "OLD", "action": "BUY", "dollar_amount": 9.0},
                    {"timestamp": "2026-09-08T10:05:00-04:00", "ticker": "AMD", "action": "SELL", "qty": 0.13, "price": 500.0,
                     "reason": "ROTATE_OUT", "strategy": "MOMENTUM_SLEEVE"},
                    {"timestamp": "2026-09-08T10:06:00-04:00", "ticker": "NVDA", "action": "BUY", "dollar_amount": 50.0,
                     "reason": "SLEEVE_TARGET", "strategy": "MOMENTUM_SLEEVE"}):
            fh.write(json.dumps(rec) + "\n")
    os.makedirs(rt.RISK_MONITOR_FLAGS, exist_ok=True)
    with open(rt._rebalance_flag_path("2026-09"), "w") as fh:
        json.dump({"month": "2026-09", "date": DAY, "regime": {"regime": "BULL", "close": 500.0, "sma200": 450.0},
                   "targets": ["NVDA", "PLTR", "TSM"], "done": True}, fh)
    os.makedirs(os.path.join(rt.CACHE_DIR, "shadow"), exist_ok=True)
    with open(os.path.join(rt.CACHE_DIR, "shadow", "2026-09.jsonl"), "w") as fh:
        fh.write(json.dumps({"date": DAY, "status": "ok", "targets": ["NVDA", "PLTR", "AMD"],
                             "planned_orders": [{"side": "buy", "symbol": "AMD", "dollar_amount": 20.0}]}) + "\n")


def test_summary_contains_equity_orders_regime_and_shadow_diff(journal):
    text = summary.build_daily_summary(DAY)
    assert DAY in text
    assert "$1,000.00" in text and "$1,012.50" in text and "+$12.50" in text and "+1.25%" in text
    assert "SELL AMD" in text and "BUY NVDA" in text and "OLD" not in text      # only that day's fills
    assert "BULL" in text
    assert "NVDA" in text and "PLTR" in text                                     # sleeve holdings
    assert "circuit breaker" in text.lower() and "normal" in text.lower()
    assert "shadow" in text.lower() and "AMD" in text and "TSM" in text            # shadow-vs-live diff
    assert not re.findall(r"\d{9,}", text), "account numbers must never appear in the summary"


def test_summary_reports_tier3_halt(journal):
    with open(rt.SESSION_HALT_FLAG.format(date=DAY), "w") as fh:
        json.dump({"tier": 3}, fh)
    assert "halt" in summary.build_daily_summary(DAY).lower()


def test_summary_without_any_state_does_not_crash(monkeypatch):
    text = summary.build_daily_summary("2026-09-09")
    assert "2026-09-09" in text and "n/a" in text


def test_send_goes_to_zulip_once_per_day(journal, monkeypatch):
    sent = []
    fake = types.ModuleType("zulip_mcp_server")
    fake.do_send_message = lambda args: sent.append(args) or "Successfully sent message (ID: 1) to x."
    monkeypatch.setitem(sys.modules, "zulip_mcp_server", fake)
    monkeypatch.setenv("AI_TRADER_ZULIP_TO", "owner@example.com")
    monkeypatch.setattr(summary, "_notify_send", lambda title, body: pytest.fail("notify-send must not run"))
    text = summary.build_daily_summary(DAY)
    assert summary.send_daily_summary(text, DAY) == "zulip"
    assert summary.send_daily_summary(text, DAY) == "suppressed"
    assert len(sent) == 1
    assert sent[0]["to"] == "owner@example.com" and sent[0]["content"] == text
    assert os.path.exists(os.path.join(rt.RISK_MONITOR_FLAGS, f"summary_{DAY}.flag"))


def test_reminder_recipient_is_the_fallback(journal, monkeypatch):
    sent = []
    fake = types.ModuleType("zulip_mcp_server")
    fake.do_send_message = lambda args: sent.append(args) or "Successfully sent message (ID: 1) to x."
    monkeypatch.setitem(sys.modules, "zulip_mcp_server", fake)
    monkeypatch.delenv("AI_TRADER_ZULIP_TO", raising=False)
    monkeypatch.setenv("AI_REMINDER_ZULIP_TO", "owner@example.com")
    assert summary.send_daily_summary("hello", DAY) == "zulip"
    assert sent[0]["to"] == "owner@example.com"


def test_zulip_failure_falls_back_to_notify_send_then_log(journal, monkeypatch):
    fake = types.ModuleType("zulip_mcp_server")

    def broken(args):
        raise Exception("The 'zulip' Python package is not installed.")
    fake.do_send_message = broken
    monkeypatch.setitem(sys.modules, "zulip_mcp_server", fake)
    monkeypatch.setenv("AI_TRADER_ZULIP_TO", "owner@example.com")
    notes = []
    monkeypatch.setattr(summary, "_notify_send", lambda title, body: notes.append(body) or True)
    assert summary.send_daily_summary("hello", DAY) == "notify-send"
    assert notes == ["hello"]
    monkeypatch.setattr(summary, "_notify_send", lambda title, body: False)
    assert summary.send_daily_summary("hello", "2026-09-09") == "log"


def test_no_recipient_skips_zulip(journal, monkeypatch):
    monkeypatch.delenv("AI_TRADER_ZULIP_TO", raising=False)
    monkeypatch.delenv("AI_REMINDER_ZULIP_TO", raising=False)
    monkeypatch.setitem(sys.modules, "zulip_mcp_server", None)    # any import attempt would raise
    monkeypatch.setattr(summary, "_notify_send", lambda title, body: False)
    assert summary.send_daily_summary("hello", DAY) == "log"


def test_daemon_hook_sends_once_after_close_on_trading_days(journal, monkeypatch):
    sent = []
    monkeypatch.setattr(summary, "send_daily_summary", lambda text, day=None: sent.append((day, text)) or "log")
    monkeypatch.setattr(summary, "_close_snapshot", lambda account_number=None: None)
    at = lambda d, h, m: datetime.datetime(2026, 9, d, h, m, tzinfo=ET_ZONE)
    assert summary.maybe_send_daily_summary(at(8, 15, 30)) is None           # before the close
    assert summary.maybe_send_daily_summary(at(12, 18, 0)) is None           # Saturday
    assert summary.maybe_send_daily_summary(at(8, 16, 5)) == "log"
    assert sent and sent[0][0] == DAY


def test_flag_save_failure_after_send_does_not_resend(journal, monkeypatch):
    """A failed flag write after a successful Zulip send must not make every daemon loop resend."""
    sent = []
    fake = types.ModuleType("zulip_mcp_server")
    fake.do_send_message = lambda args: sent.append(args) or "Successfully sent message (ID: 1) to x."
    monkeypatch.setitem(sys.modules, "zulip_mcp_server", fake)
    monkeypatch.setenv("AI_TRADER_ZULIP_TO", "owner@example.com")

    def disk_full(path, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(summary, "_save_json", disk_full)
    monkeypatch.setattr(summary, "_close_snapshot", lambda account_number=None: None)
    now = datetime.datetime(2026, 9, 8, 16, 5, tzinfo=ET_ZONE)
    assert summary.maybe_send_daily_summary(now) == "zulip"
    for _ in range(3):  # later daemon loops
        assert summary.maybe_send_daily_summary(now) is None
    assert summary.send_daily_summary("again", DAY) == "suppressed"
    assert len(sent) == 1
    assert not os.path.exists(os.path.join(rt.RISK_MONITOR_FLAGS, f"summary_{DAY}.flag"))


def test_hook_appends_one_close_snapshot_per_day(journal, monkeypatch):
    """While the summary is still pending (no flag yet), repeated loops must not append a new
    equity-journal line (nor take a new broker snapshot) every iteration."""
    day = "2026-09-09"
    snaps = []
    monkeypatch.setattr(summary, "send_daily_summary", lambda text, day=None: "log")   # never writes the flag
    monkeypatch.setattr(summary, "_close_snapshot",
                        lambda account_number=None: snaps.append(1) or {"equity": 1000.0, "cash": 10.0, "pos_map": {}})
    now = datetime.datetime(2026, 9, 9, 16, 5, tzinfo=ET_ZONE)
    for _ in range(4):
        summary.maybe_send_daily_summary(now)
    lines = [r for r in summary._read_jsonl(summary.equity_journal_path()) if r.get("date") == day]
    assert len(lines) == 1 and len(snaps) == 1
    # a restarted daemon (fresh in-memory guard) still sees the day in the journal
    monkeypatch.setattr(summary, "_SNAPSHOT_KEYS", set())
    summary.maybe_send_daily_summary(now)
    assert len(snaps) == 1
