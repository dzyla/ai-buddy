"""Shadow mode: the planning code runs with trader.json["shadow"]["strategy"] overrides against a
read-only broker, and the planned orders are appended to ~/.cache/ai/trading/shadow/<YYYY-MM>.jsonl.
State paths are redirected to tmp_path by tests/conftest.py."""
import datetime
import json
import os

import pytest

import robinhood_trader as rt
from robinhood_trader import MomentumStrategy as MS, FinancialData, MarketHours, ET_ZONE
from trader import shadow

ACC = "ACC1"


def bars(start, end, n=260, volume=1_000_000):
    step = (end - start) / (n - 1)
    return [{"datetime": f"d{i}", "open": start + step * i, "high": start + step * i, "low": start + step * i,
             "close": start + step * i, "volume": volume} for i in range(n)]


HIST = {"NVDA": bars(100, 300), "PLTR": bars(100, 250), "TSM": bars(100, 200), "AMD": bars(100, 120), "SPY": bars(400, 500)}


class FakeBroker:
    """Read methods answer from fixed data; every mutator fails the test if it is ever reached."""

    def __init__(self):
        self.mutations = 0
        self.cash = 107.0
        self.positions = {"VTI": (0.33, 380), "QQQ": (0.16, 720), "TSM": (0.2157, 436), "AMD": (0.13, 480)}
        self.quotes = {"VTI": 376, "QQQ": 715, "TSM": 433, "AMD": 500, "NVDA": 220, "PLTR": 170}

    def get_live_portfolio(self, acc):
        eq = sum(q * self.quotes[s] for s, (q, _) in self.positions.items())
        return {"total_value": self.cash + eq, "equity_value": eq, "cash": self.cash}

    def get_equity_positions(self, acc):
        return [{"symbol": s, "quantity": str(q), "average_buy_price": str(a)} for s, (q, a) in self.positions.items()]

    def get_equity_quotes(self, syms):
        return {s: {"last_trade_price": str(self.quotes[s])} for s in syms if s in self.quotes}

    def get_agentic_account_number(self):
        return ACC

    def place_equity_order(self, *a, **kw):
        self.mutations += 1
        raise AssertionError("shadow mode must never place an order")

    def execute_market_order(self, *a, **kw):
        self.mutations += 1
        raise AssertionError("shadow mode must never place an order")


@pytest.fixture
def market(monkeypatch):
    monkeypatch.setattr(FinancialData, "fetch_historical", lambda sym, range_period="1y", interval="1d": HIST.get(sym, []))
    monkeypatch.setattr(MS, "universe", classmethod(lambda cls: ["NVDA", "PLTR", "TSM", "AMD"]))
    now = datetime.datetime(2026, 9, 8, 10, 30, tzinfo=ET_ZONE)
    monkeypatch.setattr(MarketHours, "now_et", lambda: now)
    return now


def _lines(month="2026-09"):
    path = os.path.join(rt.CACHE_DIR, "shadow", f"{month}.jsonl")
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def test_shadow_pulse_writes_planned_orders_and_never_mutates(market):
    fb = FakeBroker()
    cfg = {"shadow": {"enabled": True, "strategy": {"sleeve_rank_buffer": 0}}}
    rec = shadow.run_shadow_pulse(market, cfg, broker=fb, account_number=ACC)
    assert fb.mutations == 0
    lines = _lines()
    assert len(lines) == 1
    line = lines[0]
    assert line["status"] == "ok" and line["date"] == "2026-09-08"
    assert line["overrides"] == {"sleeve_rank_buffer": 0}
    # buffer 0 = strict top-3: the held AMD (rank 4) is rotated out; the live default (buffer 2) keeps it.
    assert line["targets"] == ["NVDA", "PLTR", "TSM"]
    assert line["baseline_targets"] == ["NVDA", "TSM", "AMD"]
    planned = line["planned_orders"]
    assert {"side": "sell", "symbol": "AMD"}.items() <= planned[0].items()
    assert any(o["side"] == "buy" and o["symbol"] == "PLTR" for o in planned)
    assert rec["planned_orders"] == planned
    # no live state was written: no rebalance flag, no ledger
    assert not os.path.exists(rt._rebalance_flag_path("2026-09"))
    assert not os.path.exists(os.path.join(rt.VAULT_DIR, "retrospectives", "trade_ledger.jsonl"))


def test_overrides_do_not_leak_into_live_strategy(market):
    before = (MS.SLEEVE_RANK_BUFFER, MS.SLEEVE_TOP_N, MS.rank_buffer())
    shadow.run_shadow_pulse(market, {"shadow": {"enabled": True, "strategy": {"sleeve_rank_buffer": 0, "sleeve_top_n": 1,
                                                                           "bogus_key": 3}}},
                            broker=FakeBroker(), account_number=ACC)
    assert (MS.SLEEVE_RANK_BUFFER, MS.SLEEVE_TOP_N, MS.rank_buffer()) == before
    line = _lines()[-1]
    assert line["targets"] == ["NVDA"]
    assert line["ignored_overrides"] == ["bogus_key"]


def test_read_only_broker_blocks_mutators():
    fb = FakeBroker()
    ro = shadow.ReadOnlyBroker(fb)
    assert ro.get_equity_quotes(["TSM"]) == {"TSM": {"last_trade_price": "433"}}
    for name in ("place_equity_order", "execute_market_order", "call_mcp_tool", "cancel_equity_order"):
        with pytest.raises(PermissionError):
            getattr(ro, name)("ACC1", "TSM", "buy")
    assert fb.mutations == 0


def test_failed_snapshot_is_recorded_as_skipped(market):
    fb = FakeBroker()
    fb.get_live_portfolio = lambda acc: {}
    rec = shadow.run_shadow_pulse(market, {"shadow": {"enabled": True}}, broker=fb, account_number=ACC)
    assert rec["status"] == "skipped" and rec["planned_orders"] == []
    assert _lines()[-1]["status"] == "skipped"


def test_daemon_hook_runs_once_per_day_only_when_enabled(market, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(shadow, "run_shadow_pulse",
                        lambda now, config, **kw: calls.append(config) or {"status": "ok", "targets": [], "planned_orders": []})
    cfg = tmp_path / "trader.json"
    monkeypatch.setenv("RH_TRADER_CONFIG", str(cfg))
    cfg.write_text(json.dumps({"shadow": {"enabled": False, "strategy": {}}}))
    assert shadow.maybe_run_shadow(ACC, market) is None and calls == []
    cfg.write_text(json.dumps({"shadow": {"enabled": True, "strategy": {"sleeve_rank_buffer": 0}}}))
    early = market.replace(hour=9, minute=45)
    assert shadow.maybe_run_shadow(ACC, early) is None and calls == []    # never before the live pulse window
    assert shadow.maybe_run_shadow(ACC, market) is not None
    assert shadow.maybe_run_shadow(ACC, market) is None                   # once per day
    assert len(calls) == 1
    assert os.path.exists(os.path.join(rt.RISK_MONITOR_FLAGS, "shadow_2026-09-08.flag"))


def test_daemon_hook_swallows_errors(market, monkeypatch, tmp_path):
    def boom(now, config, **kw):
        raise RuntimeError("data source down")
    monkeypatch.setattr(shadow, "run_shadow_pulse", boom)
    cfg = tmp_path / "trader.json"
    cfg.write_text(json.dumps({"shadow": {"enabled": True}}))
    monkeypatch.setenv("RH_TRADER_CONFIG", str(cfg))
    assert shadow.maybe_run_shadow(ACC, market) is None
