"""Pulse-level tests for the live loop: monthly rebalance (sells, then buys, then core top-up),
hold list, dry run, bear regime, disaster stop, and Tier-3 halt without liquidation.
All broker calls are mocked; state lives in tmp_path (tests/conftest.py)."""
import datetime
import json
import os

import pytest

import robinhood_trader as rt
from robinhood_trader import (MomentumStrategy as MS, RobinhoodExecutor, FinancialData, MarketHours, ET_ZONE,
                              _momentum_rebalance_pulse, _risk_monitor_pulse)

ACC = "ACC1"


def bars(start, end, n=260, volume=1_000_000):
    step = (end - start) / (n - 1)
    return [{"datetime": f"d{i}", "open": start + step * i, "high": start + step * i, "low": start + step * i,
             "close": start + step * i, "volume": volume} for i in range(n)]


# Universe histories: three strong names, the rest weak/ineligible, SPY bullish by default.
HIST = {"NVDA": bars(100, 300), "PLTR": bars(100, 250), "TSM": bars(100, 200), "AMD": bars(100, 120), "SPY": bars(400, 500)}


class Broker:
    """Minimal fake Robinhood: fills market orders instantly at the quoted price."""

    def __init__(self, cash, positions, quotes):
        self.cash = cash
        self.positions = {k: dict(v) for k, v in positions.items()}   # sym -> {quantity, average_buy_price}
        self.quotes = dict(quotes)
        self.orders = []

    def price(self, sym):
        return float(self.quotes[sym])

    def portfolio(self, acc):
        eq = sum(p["quantity"] * self.price(s) for s, p in self.positions.items())
        return {"total_value": self.cash + eq, "equity_value": eq, "cash": self.cash, "buying_power": {"buying_power": self.cash}}

    def equity_positions(self, acc):
        return [{"symbol": s, "quantity": str(p["quantity"]), "average_buy_price": str(p["average_buy_price"])} for s, p in self.positions.items()]

    def equity_quotes(self, syms):
        return {s: {"last_trade_price": str(self.quotes[s])} for s in syms if s in self.quotes}

    def order(self, acc, sym, side, dollar_amount=None, quantity=None):
        self.orders.append((sym, side, dollar_amount, quantity))
        px = self.price(sym)
        if side == "sell":
            q = float(quantity)
            self.positions[sym]["quantity"] -= q
            if self.positions[sym]["quantity"] <= 1e-9:
                del self.positions[sym]
            self.cash += q * px
        else:
            amt = float(dollar_amount)
            q = amt / px
            p = self.positions.setdefault(sym, {"quantity": 0.0, "average_buy_price": px})
            tot = p["quantity"] + q
            p["average_buy_price"] = (p["average_buy_price"] * p["quantity"] + px * q) / tot
            p["quantity"] = tot
            self.cash -= amt
        return {"ok": True}


@pytest.fixture
def broker(monkeypatch):
    b = Broker(
        cash=107.0,
        positions={"VTI": {"quantity": 0.33, "average_buy_price": 380}, "QQQ": {"quantity": 0.16, "average_buy_price": 720},
                   "LLY": {"quantity": 0.077, "average_buy_price": 1178}, "TSM": {"quantity": 0.2157, "average_buy_price": 436},
                   "MSFT": {"quantity": 0.0873, "average_buy_price": 481}},
        quotes={"VTI": 376, "QQQ": 715, "LLY": 1115, "TSM": 433, "MSFT": 495, "NVDA": 220, "PLTR": 170, "AMD": 500, "SPY": 500},
    )
    monkeypatch.setattr(RobinhoodExecutor, "get_live_portfolio", b.portfolio)
    monkeypatch.setattr(RobinhoodExecutor, "get_equity_positions", b.equity_positions)
    monkeypatch.setattr(RobinhoodExecutor, "get_equity_quotes", b.equity_quotes)
    monkeypatch.setattr(RobinhoodExecutor, "execute_market_order", b.order)
    monkeypatch.setattr(FinancialData, "fetch_historical", lambda sym, range_period="1y", interval="1d": HIST.get(sym, []))
    monkeypatch.setattr(MS, "universe", classmethod(lambda cls: ["NVDA", "PLTR", "TSM", "AMD"]))
    return b


def at(hour, minute, day=8):
    return datetime.datetime(2026, 9, day, hour, minute, 0, tzinfo=ET_ZONE)


def test_no_orders_before_ten_et(broker, monkeypatch):
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(9, 45))
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert broker.orders == []
    assert not os.path.exists(rt._rebalance_flag_path("2026-09"))


def test_monthly_rebalance_sells_then_buys_then_core_topup(broker, monkeypatch):
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    # pulse 1: plan + sells only (buys wait for cash to settle)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    state = json.load(open(rt._rebalance_flag_path("2026-09")))
    assert state["regime"]["regime"] == "BULL"
    assert state["targets"] == ["NVDA", "PLTR", "TSM"]
    assert {o[0] for o in broker.orders if o[1] == "sell"} == {"LLY", "MSFT"}
    assert not any(o[1] == "buy" for o in broker.orders)
    assert state["buys_pending"] and not state["done"]
    # pulse 2: buys
    _momentum_rebalance_pulse(ACC, dry_run=False)
    buys = {o[0]: float(o[2]) for o in broker.orders if o[1] == "buy"}
    assert set(buys) == {"NVDA", "PLTR"}                     # TSM already above its target weight
    equity = broker.portfolio(ACC)["total_value"]
    assert buys["NVDA"] == pytest.approx(equity * MS.SLEEVE_TARGET_PCT / 3, rel=0.05)
    state = json.load(open(rt._rebalance_flag_path("2026-09")))
    assert state["done"] and state["completed"] == "2026-09-08"
    assert broker.cash >= equity * MS.CASH_BUFFER_PCT - 1.0
    # pulse 3 same day (fresh cash snapshot): core top-up once, into the lesser-weighted ETF, cash stays >= buffer
    _momentum_rebalance_pulse(ACC, dry_run=False)
    last = broker.orders[-1]
    assert last[1] == "buy" and last[0] == "QQQ"
    assert broker.cash >= broker.portfolio(ACC)["total_value"] * MS.CASH_BUFFER_PCT - 1.0
    # pulse 4 same day: idle (one core top-up per day)
    n = len(broker.orders)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert len(broker.orders) == n
    # next day: at most one more core top-up, never a sleeve order
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5, day=9))
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert len(broker.orders) <= n + 1
    assert all(o[0] in MS.CORE_SYMBOLS for o in broker.orders[n:])
    # a rebalance note was written to the vault
    assert os.path.exists(os.path.join(rt.VAULT_DIR, "retrospectives", "rebalance_2026-09.md"))
    ledger = open(os.path.join(rt.VAULT_DIR, "retrospectives", "trade_ledger.jsonl")).read()
    assert '"ROTATE_OUT"' in ledger and '"SLEEVE_TARGET"' in ledger and '"CORE_TOPUP"' in ledger


def test_hold_list_is_never_sold(broker, monkeypatch):
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    with open(rt.TRADING_HOLD_FILE, "w") as fh:
        fh.write("LLY\n")
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert {o[0] for o in broker.orders if o[1] == "sell"} == {"MSFT"}
    assert "LLY" in broker.positions


def test_dry_run_places_no_orders_but_completes(broker, monkeypatch):
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    _momentum_rebalance_pulse(ACC, dry_run=True)
    assert broker.orders == []
    state = json.load(open(rt._rebalance_flag_path("2026-09")))
    assert state["done"] and state["dry_run"]
    assert {s["symbol"] for s in state["sells_done"]} == {"LLY", "MSFT"}
    assert {b["symbol"] for b in state["buys_done"]} == {"NVDA", "PLTR"}


def test_bear_regime_exits_sleeve_and_buys_nothing(broker, monkeypatch):
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    monkeypatch.setattr(FinancialData, "fetch_historical",
                        lambda sym, range_period="1y", interval="1d": bars(600, 450) if sym == "SPY" else HIST.get(sym, []))
    _momentum_rebalance_pulse(ACC, dry_run=False)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert {o[0] for o in broker.orders if o[1] == "sell"} == {"LLY", "TSM", "MSFT"}
    assert not any(o[1] == "buy" for o in broker.orders)
    state = json.load(open(rt._rebalance_flag_path("2026-09")))
    assert state["targets"] == [] and state["done"]


def test_rebalance_runs_once_per_month(broker, monkeypatch):
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5, day=8))
    _momentum_rebalance_pulse(ACC, dry_run=True)
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5, day=22))
    _momentum_rebalance_pulse(ACC, dry_run=True)
    assert len([f for f in os.listdir(rt.RISK_MONITOR_FLAGS) if f.startswith("rebalance_")]) == 1


def test_disaster_stop_sells_sleeve_not_core(broker, monkeypatch):
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(11, 0))
    broker.quotes["LLY"] = 900.0    # -23.6%
    broker.quotes["VTI"] = 280.0    # -26% on core: must be held
    _risk_monitor_pulse(ACC, dry_run=False)
    assert [(o[0], o[1]) for o in broker.orders] == [("LLY", "sell")]
    # flag prevents a repeat within the day
    _risk_monitor_pulse(ACC, dry_run=False)
    assert len(broker.orders) == 1
    log = open(rt.RISK_MONITOR_LOG).read()
    assert "DISASTER_STOP" in log and "RISK pulse: equity" in log


def test_tier3_halts_without_liquidating(broker, monkeypatch):
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(11, 0))
    _risk_monitor_pulse(ACC, dry_run=False)                       # sets baseline
    for s in ("VTI", "QQQ", "LLY", "TSM", "MSFT"):
        broker.quotes[s] = broker.quotes[s] * 0.85                 # -15% day, all positions
    _risk_monitor_pulse(ACC, dry_run=False)                       # observed once
    _risk_monitor_pulse(ACC, dry_run=False)                       # confirmed -> halt
    assert broker.orders == []                                    # nothing liquidated (no sleeve name is at -20% vs cost)
    assert os.path.exists(rt.SESSION_HALT_FLAG.format(date="2026-09-08"))
    _momentum_rebalance_pulse(ACC, dry_run=False)
    assert broker.orders == []                                    # strategy orders blocked by the halt flag


def test_backtest_runs_on_synthetic_bars():
    n = 700
    def mk(start, end, dates):
        step = (end - start) / (n - 1)
        return [{"datetime": d, "open": start + step * i, "high": start + step * i, "low": start + step * i,
                 "close": start + step * i, "volume": 2_000_000} for i, d in enumerate(dates)]
    d0 = datetime.date(2024, 1, 1)
    dates = []
    d = d0
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += datetime.timedelta(days=1)
    hist = {"SPY": mk(400, 600, dates), "VTI": mk(200, 300, dates), "QQQ": mk(300, 500, dates),
            "AAA": mk(50, 200, dates), "BBB": mk(50, 120, dates), "CCC": mk(50, 40, dates)}
    res = rt.run_backtest(start_cash=600.0, universe=["AAA", "BBB", "CCC"], bars_override=hist, verbose=False)
    assert res["end"] > res["start"]
    assert res["sleeve_round_trips"] >= 0
    assert set(res["benchmarks_buy_hold_pct"]) == {"VTI", "QQQ", "SPY"}
    assert "AAA" in res["final_positions"] and "CCC" not in res["final_positions"]
    core_only = rt.run_backtest(start_cash=600.0, universe=["AAA", "BBB", "CCC"], bars_override=hist, sleeve=False, verbose=False)
    assert core_only["sleeve_round_trips"] == 0
