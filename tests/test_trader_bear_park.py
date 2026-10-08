"""bear_park: park the momentum sleeve in a bond ETF (instead of cash) while SPY < SMA200.
Off by default (live behaviour unchanged); shadow-only candidate. All broker calls are mocked."""
import datetime
import json

import pytest

import robinhood_trader as rt
from robinhood_trader import (MomentumStrategy as MS, RobinhoodExecutor, FinancialData, MarketHours,
                              _momentum_rebalance_pulse, _risk_monitor_pulse)
from trader import shadow, summary
from test_trader_pulses import ACC, HIST, at, bars, broker  # noqa: F401  (broker is a fixture)
from test_trader_shadow import FakeBroker, market, _lines  # noqa: F401  (market is a fixture)

BEAR_SPY = bars(600, 450)


def _bear_hist(extra=None):
    h = dict(HIST, SPY=BEAR_SPY)
    h.update(extra or {})
    return lambda sym, range_period="1y", interval="1d", **kw: h.get(sym, [])


def _cfg(monkeypatch, tmp_path, data):
    p = tmp_path / "trader.json"
    p.write_text(json.dumps(data))
    monkeypatch.setenv("RH_TRADER_CONFIG", str(p))


# ---------------------------------------------------------------- pure planner
class TestPlanner:
    POS = {"TSM": {"quantity": 1.0, "price": 100.0}, "VTI": {"quantity": 1.0, "price": 100.0}}

    def test_park_none_is_the_original_plan(self):
        a = MS.plan_rebalance(1000.0, 100.0, self.POS, [])
        b = MS.plan_rebalance(1000.0, 100.0, self.POS, [], park=None)
        assert a == b and a["buys"] == [] and [s["symbol"] for s in a["sells"]] == ["TSM"]

    def test_bear_buys_park_with_full_sleeve_allocation(self):
        plan = MS.plan_rebalance(1000.0, 100.0, self.POS, [], park="BND")
        assert [s["symbol"] for s in plan["sells"]] == ["TSM"] and plan["sells"][0]["reason"] == "REGIME_EXIT"
        assert plan["buys"] == [{"symbol": "BND", "dollar_amount": 100.0, "reason": "PARK_TARGET"}]  # cash 100+100 sold - 10% buffer
        assert plan["projected_cash"] == 100.0

    def test_park_already_held_is_kept_and_topped_up_only_to_target(self):
        pos = {"BND": {"quantity": 2.0, "price": 100.0}}
        plan = MS.plan_rebalance(1000.0, 300.0, pos, [], park="BND")
        assert plan["sells"] == []
        assert plan["buys"][0]["dollar_amount"] == 100.0   # target 300 - held 200

    def test_bull_return_sells_park_first_then_buys_sleeve(self):
        pos = {"BND": {"quantity": 3.0, "price": 100.0}}
        plan = MS.plan_rebalance(1000.0, 50.0, pos, ["NVDA", "PLTR", "TSM"])
        assert [(s["symbol"], s["reason"]) for s in plan["sells"]] == [("BND", "ROTATE_OUT")]
        assert {b["symbol"] for b in plan["buys"]} == {"NVDA", "PLTR", "TSM"}
        assert all(b["reason"] == "SLEEVE_TARGET" for b in plan["buys"])

    def test_park_ignored_when_sleeve_has_targets(self):
        plan = MS.plan_rebalance(1000.0, 400.0, {}, ["NVDA"], park="BND")
        assert [b["symbol"] for b in plan["buys"]] == ["NVDA"]

    def test_core_and_invalid_park_rejected(self):
        for bad in ("VTI", "QQQ", "SPY", "", "rm -rf", 5, None):
            assert MS.plan_rebalance(1000.0, 400.0, {}, [], park=bad)["buys"] == []

    def test_hold_listed_park_is_never_sold(self):
        pos = {"BND": {"quantity": 3.0, "price": 100.0}}
        assert MS.plan_rebalance(1000.0, 50.0, pos, ["NVDA"], hold={"BND"})["sells"] == []


class TestResolve:
    def good(self, n=40):
        return bars(80, 82, n=n)

    def test_chain_order_and_dedupe(self):
        assert MS.park_candidates("BND") == ["BND", "AGG", "SGOV"]
        assert MS.park_candidates("shy") == ["SHY", "BND", "AGG", "SGOV"]
        assert MS.park_candidates(None) == [] and MS.park_candidates("VTI") == []

    def test_fallback_chain(self):
        assert MS.resolve_park("BND", {"BND": self.good(), "AGG": self.good()}) == "BND"
        assert MS.resolve_park("BND", {"AGG": self.good(), "SGOV": self.good()}) == "AGG"
        assert MS.resolve_park("BND", {"SGOV": self.good()}) == "SGOV"
        assert MS.resolve_park("BND", {}) is None            # -> cash
        assert MS.resolve_park("BND", {"BND": [], "AGG": bars(0, 0, n=5)}) is None

    def test_choose_park_only_in_bear_without_targets(self, tmp_path, monkeypatch):
        _cfg(monkeypatch, tmp_path, {"strategy": {"bear_park": "BND"}})
        fetch = lambda syms: {s: self.good() for s in syms}   # noqa: E731
        assert MS.choose_park("BEAR", [], fetch) == "BND"
        assert MS.choose_park("BULL", [], fetch) is None
        assert MS.choose_park("BEAR", ["NVDA"], fetch) is None
        assert MS.choose_park("BEAR", [], lambda s: 1 / 0) is None   # fetch error -> cash

    def test_default_off(self):
        assert MS.bear_park() is None
        assert MS.choose_park("BEAR", [], lambda s: {"BND": self.good()}) is None

    def test_stop_exemption(self):
        pos = [{"symbol": "BND", "quantity": 1, "average_buy_price": 100}, {"symbol": "TSM", "quantity": 1, "average_buy_price": 100}]
        q = {"BND": {"last_trade_price": "70"}, "TSM": {"last_trade_price": "70"}}
        assert {h["symbol"] for h in MS.disaster_stop_hits(pos, q)} == {"BND", "TSM"}
        assert {h["symbol"] for h in MS.disaster_stop_hits(pos, q, exempt={"BND"})} == {"TSM"}


# ---------------------------------------------------------------- live pulse (opt-in config)
def _flag():
    return json.load(open(rt._rebalance_flag_path("2026-09")))


def test_live_default_off_is_unchanged_in_bear(broker, monkeypatch):
    broker.quotes["BND"] = 75.0
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    monkeypatch.setattr(FinancialData, "fetch_historical", _bear_hist({"BND": bars(70, 75)}))
    _momentum_rebalance_pulse(ACC, dry_run=False)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert not any(o[1] == "buy" and o[0] == "BND" for o in broker.orders)
    st = _flag()
    assert st["targets"] == [] and st.get("park") is None


def test_live_bear_park_sells_sleeve_then_buys_bond_on_next_pulse(broker, monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, {"strategy": {"bear_park": "BND"}})
    broker.quotes["BND"] = 75.0
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    monkeypatch.setattr(FinancialData, "fetch_historical", _bear_hist({"AGG": bars(95, 100), "BND": []}))
    broker.quotes["AGG"] = 100.0
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert {o[0] for o in broker.orders if o[1] == "sell"} == {"LLY", "TSM", "MSFT"}
    assert not any(o[1] == "buy" for o in broker.orders)           # sells-before-buys, buys wait for settlement
    st = _flag()
    assert st["park"] == "AGG" and st["park_requested"] == "BND"   # BND had no bars -> fell back to AGG
    _momentum_rebalance_pulse(ACC, dry_run=False)
    buys = [o for o in broker.orders if o[1] == "buy"]
    assert [b[0] for b in buys] == ["AGG"]
    equity = broker.portfolio(ACC)["total_value"]
    assert float(buys[0][2]) == pytest.approx(equity * MS.SLEEVE_TARGET_PCT, rel=0.1)
    assert _flag()["done"]
    ledger = open(rt.VAULT_DIR + "/retrospectives/trade_ledger.jsonl").read()
    assert "PARK_TARGET" in ledger


def test_live_bear_park_no_data_at_all_falls_back_to_cash(broker, monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, {"strategy": {"bear_park": "BND"}})
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    monkeypatch.setattr(FinancialData, "fetch_historical", _bear_hist())
    _momentum_rebalance_pulse(ACC, dry_run=False)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert not any(o[1] == "buy" for o in broker.orders)
    assert _flag()["park"] is None


def test_live_bull_return_sells_park_before_buying_sleeve(broker, monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, {"strategy": {"bear_park": "BND"}})
    broker.positions = {"VTI": broker.positions["VTI"], "QQQ": broker.positions["QQQ"],
                        "BND": {"quantity": 1.5, "average_buy_price": 74.0}}
    broker.quotes["BND"] = 75.0
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))   # SPY bullish (default HIST)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert [(o[0], o[1]) for o in broker.orders] == [("BND", "sell")]
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert {o[0] for o in broker.orders if o[1] == "buy"} == {"NVDA", "PLTR", "TSM"}


def test_idempotent_month_flag_and_no_midmonth_replan(broker, monkeypatch, tmp_path):
    # Month planned with bear_park OFF; enabling it afterwards must not re-plan or trade (next month only).
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    monkeypatch.setattr(FinancialData, "fetch_historical", _bear_hist({"BND": bars(70, 75)}))
    _momentum_rebalance_pulse(ACC, dry_run=False)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    n = len(broker.orders)
    state_before = _flag()
    _cfg(monkeypatch, tmp_path, {"strategy": {"bear_park": "BND"}})
    broker.quotes["BND"] = 75.0
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 30, day=9))
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert _flag() == state_before
    assert all(o[0] in MS.CORE_SYMBOLS for o in broker.orders[n:])
    assert not any(o[0] == "BND" for o in broker.orders)


def test_disaster_stop_exempts_park_only_when_configured(broker, monkeypatch, tmp_path):
    broker.positions["BND"] = {"quantity": 2.0, "average_buy_price": 100.0}
    broker.quotes["BND"] = 70.0
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(11, 0))
    _cfg(monkeypatch, tmp_path, {"strategy": {"bear_park": "BND"}})
    _risk_monitor_pulse(ACC, dry_run=False)
    assert broker.orders == []


# ---------------------------------------------------------------- shadow
def _shadow_market(monkeypatch, bnd=True):
    h = dict(HIST, SPY=BEAR_SPY)
    if bnd:
        h["BND"] = bars(70, 75)
    monkeypatch.setattr(FinancialData, "fetch_historical", lambda sym, range_period="1y", interval="1d", **kw: h.get(sym, []))


def test_shadow_bear_park_plans_bond_buy_never_mutates(market, monkeypatch):
    _shadow_market(monkeypatch)
    fb = FakeBroker()
    fb.quotes["BND"] = 75.0
    cfg = {"shadow": {"enabled": True, "strategy": {"bear_park": "BND"}}}
    rec = shadow.run_shadow_pulse(market, cfg, broker=fb, account_number="ACC1")
    assert fb.mutations == 0
    assert rec["park"] == "BND" and rec["targets"] == [] and rec["overrides"] == {"bear_park": "BND"}
    assert any(o["side"] == "buy" and o["symbol"] == "BND" and o["reason"] == "PARK_TARGET" for o in rec["planned_orders"])
    assert any(o["side"] == "sell" and o["symbol"] == "TSM" for o in rec["planned_orders"])
    # live strategy untouched
    assert MS.bear_park() is None and MS.BEAR_PARK is None


def test_shadow_bear_park_read_only_even_with_mutating_broker_methods(market, monkeypatch):
    _shadow_market(monkeypatch)
    fb = FakeBroker()
    shadow.run_shadow_pulse(market, {"shadow": {"enabled": True, "strategy": {"bear_park": "BND"}}}, broker=fb, account_number="ACC1")
    assert fb.mutations == 0


def test_shadow_invalid_bear_park_ignored(market, monkeypatch):
    _shadow_market(monkeypatch)
    for bad in ("VTI", 3, "no spaces!", True):
        rec = shadow.run_shadow_pulse(market, {"shadow": {"strategy": {"bear_park": bad}}}, broker=FakeBroker(), account_number="ACC1")
        assert rec["ignored_overrides"] == ["bear_park"] and rec["park"] is None


def test_shadow_without_bear_park_has_no_park(market, monkeypatch):
    _shadow_market(monkeypatch)
    rec = shadow.run_shadow_pulse(market, {"shadow": {}}, broker=FakeBroker(), account_number="ACC1")
    assert rec["park"] is None and not any(o["symbol"] == "BND" for o in rec["planned_orders"])


def test_summary_shows_park_in_shadow_vs_live(market, monkeypatch):
    _shadow_market(monkeypatch)
    fb = FakeBroker()
    fb.quotes["BND"] = 75.0
    shadow.run_shadow_pulse(market, {"shadow": {"strategy": {"bear_park": "BND"}}}, broker=fb, account_number="ACC1")
    text = summary.build_daily_summary("2026-09-08")
    assert "shadow targets: BND (park) | live targets: n/a" in text
    assert "BUY BND" in text


def test_backtest_harness_parks_in_bear_regime_only():
    import datetime as dt
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dev"))
    import backtest_momentum as bt

    def mk(f, n=900):
        d0 = dt.date(2020, 1, 1)
        return [{"datetime": (d0 + dt.timedelta(days=i)).isoformat(), "open": f(i), "high": f(i), "low": f(i),
                 "close": f(i), "volume": 2e6} for i in range(n)]
    data = {"SPY": mk(lambda i: 100 + i * 0.1 if i < 500 else 150 - (i - 500) * 0.1), "VTI": mk(lambda i: 100 + i * .1),
            "QQQ": mk(lambda i: 100 + i * .1), "AAA": mk(lambda i: 50 + i * .1), "BND": mk(lambda i: 70 + i * .01)}
    held = {}
    for name in ("base", "park_BND"):
        sim = bt.Sim(data, ["AAA"], bt.combine(name), "2020-10-01", None, 1000.0, 10.0, False, {}, 1)
        sim.run()
        held[name] = sorted(sim.pos)
    assert "BND" not in held["base"] and "BND" in held["park_BND"]
