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
    monkeypatch.setattr(FinancialData, "fetch_historical", lambda sym, range_period="1y", interval="1d", **kw: HIST.get(sym, []))
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


# ── read-only by construction ────────────────────────────────────────────────
def _rh_bars(rows):
    return [{"begins_at": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}T00:00:00Z", "open_price": r["open"],
             "high_price": r["high"], "low_price": r["low"], "close_price": r["close"], "volume": r["volume"]}
            for i, r in enumerate(rows)]


def _executor_mutators():
    E = rt.RobinhoodExecutor
    return sorted(n for n in dir(E) if not n.startswith("__") and callable(getattr(E, n))
                  and not n.startswith("get_") and n not in shadow.READ_ONLY_METHODS)


def test_read_only_broker_blocks_every_mutator_of_the_real_executor(monkeypatch):
    E = rt.RobinhoodExecutor
    calls = []
    muts = _executor_mutators()
    assert "execute_market_order" in muts and "call_mcp_tool" in muts
    for n in muts:
        monkeypatch.setattr(E, n, classmethod(lambda cls, *a, _n=n, **k: calls.append(_n)), raising=False)
    ro = shadow.ReadOnlyBroker(E)
    for n in muts:
        with pytest.raises(PermissionError):
            getattr(ro, n)("ACC1", "VTI", "buy")
    assert calls == []
    # the wrapped executor is not reachable through the wrapper
    assert ro._inner is not E
    with pytest.raises(PermissionError):
        ro._inner()
    with pytest.raises(AttributeError):
        ro.__dict__
    assert getattr(ro.get_equity_quotes, "__self__", None) is None
    with pytest.raises(PermissionError):
        ro.anything = 1


def test_shadow_pulse_on_the_real_executor_reads_only_through_the_wrapper(monkeypatch):
    """broker=None: shadow wraps the real RobinhoodExecutor. Price history must come through the
    wrapper's get_equity_historicals (not RobinhoodExecutor directly), and no mutator is ever hit."""
    E = rt.RobinhoodExecutor
    fb = FakeBroker()
    hits, mutations = [], []
    for n in _executor_mutators():
        monkeypatch.setattr(E, n, classmethod(lambda cls, *a, _n=n, **k: mutations.append(_n)), raising=False)
    monkeypatch.setattr(E, "get_live_portfolio", classmethod(lambda cls, acc=None: fb.get_live_portfolio(acc)))
    monkeypatch.setattr(E, "get_equity_positions", classmethod(lambda cls, acc=None: fb.get_equity_positions(acc)))
    monkeypatch.setattr(E, "get_equity_quotes", classmethod(lambda cls, syms: fb.get_equity_quotes(syms)))
    monkeypatch.setattr(E, "get_agentic_account_number", classmethod(lambda cls: ACC))

    def hist(cls, symbols, interval="day", span_days=90):
        hits.append(tuple(symbols))
        return {s: _rh_bars(HIST[s]) for s in symbols if s in HIST}
    monkeypatch.setattr(E, "get_equity_historicals", classmethod(hist))
    monkeypatch.setattr(FinancialData, "fetch_historical_yahoo",
                        classmethod(lambda cls, *a, **k: pytest.fail("Yahoo fallback reached")))
    monkeypatch.setattr(MS, "universe", classmethod(lambda cls: ["NVDA", "PLTR", "TSM", "AMD"]))
    # make the history fresh relative to "now" (the last synthetic bar is in 2026-10)
    now = datetime.datetime(2026, 10, 9, 10, 30, tzinfo=ET_ZONE)
    monkeypatch.setattr(MarketHours, "now_et", lambda: now)

    seen_brokers = []
    real_fetch = FinancialData.fetch_historical.__func__

    def spy_fetch(cls, ticker, range_period="6mo", interval="1d", broker=None):
        seen_brokers.append(broker)
        return real_fetch(cls, ticker, range_period, interval, broker=broker)
    monkeypatch.setattr(FinancialData, "fetch_historical", classmethod(spy_fetch))

    rec = shadow.run_shadow_pulse(now, {"shadow": {"enabled": True, "strategy": {}}}, account_number=ACC)
    assert mutations == []
    assert hits, "price history was not fetched"
    assert seen_brokers and all(isinstance(b, shadow.ReadOnlyBroker) for b in seen_brokers)
    assert rec["status"] == "ok", rec
