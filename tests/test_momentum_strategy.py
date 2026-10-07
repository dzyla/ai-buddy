"""Unit tests for the core + monthly momentum sleeve strategy (pure functions) and the
hardened circuit breaker. No network, no live state (see tests/conftest.py)."""
import datetime
import json
import os

import pytest

import robinhood_trader as rt
from robinhood_trader import MomentumStrategy as MS, ComplianceAndRiskGuard, MarketHours, ET_ZONE


def bars(prices, volume=1_000_000):
    return [{"datetime": f"d{i}", "open": p, "high": p, "low": p, "close": p, "volume": volume} for i, p in enumerate(prices)]


def trending(start, end, n=260, volume=1_000_000):
    step = (end - start) / (n - 1)
    return bars([start + step * i for i in range(n)], volume)


class TestRanking:
    def test_universe_excludes_core_etfs_and_dupes(self):
        u = MS.universe()
        assert "VTI" not in u and "QQQ" not in u and "SPY" not in u
        assert len(u) == len(set(u))
        assert "NVDA" in u

    def test_rank_orders_by_momentum_and_flags_ineligible(self):
        data = {
            "FAST": trending(100, 200),          # +100% over the file, strongly above SMA200
            "SLOW": trending(100, 120),
            "DOWN": trending(200, 100),          # below SMA200
            "THIN": trending(100, 150, volume=100_000),   # liquidity gate
            "PENNY": trending(1.0, 4.0),         # price gate
            "SHORT": trending(100, 150, n=50),   # not enough history
        }
        ranked = MS.rank(data)
        by = {r["symbol"]: r for r in ranked}
        assert [r["symbol"] for r in ranked if r["eligible"]] == ["FAST", "SLOW"]
        assert by["FAST"]["momentum_pct"] > by["SLOW"]["momentum_pct"] > 0
        assert not by["DOWN"]["eligible"] and "below SMA200" in by["DOWN"]["reason"]
        assert not by["THIN"]["eligible"] and "avg vol" in by["THIN"]["reason"]
        assert not by["PENNY"]["eligible"] and "price" in by["PENNY"]["reason"]
        assert not by["SHORT"]["eligible"] and "history" in by["SHORT"]["reason"]
        # eligible rows come first
        assert ranked[0]["eligible"] and not ranked[-1]["eligible"]

    def test_rank_as_of_uses_only_past_bars(self):
        data = {"X": trending(100, 200, n=300)}
        full = MS.rank(data)[0]
        past = MS.rank(data, as_of={"X": 250})[0]
        assert past["price"] < full["price"]
        assert past["momentum_pct"] is not None

    def test_regime(self):
        assert MS.regime(trending(100, 200))["regime"] == "BULL"
        assert MS.regime(trending(200, 100))["regime"] == "BEAR"
        assert MS.regime(trending(100, 200, n=50))["regime"] == "BULL"  # no gate without history

    def test_select_sleeve_top_n_and_bear(self):
        ranked = MS.rank({"A": trending(100, 300), "B": trending(100, 200), "C": trending(100, 150), "D": trending(100, 110)})
        assert MS.select_sleeve(ranked, "BULL") == ["A", "B", "C"]
        assert MS.select_sleeve(ranked, "BULL", top_n=2) == ["A", "B"]
        assert MS.select_sleeve(ranked, "BEAR") == []


class TestPlanning:
    POS = {
        "VTI": {"quantity": 0.33, "price": 376.0},    # ~124
        "QQQ": {"quantity": 0.16, "price": 715.0},    # ~114
        "LLY": {"quantity": 0.077, "price": 1115.0},  # ~86
        "TSM": {"quantity": 0.2157, "price": 433.0},  # ~93
        "MSFT": {"quantity": 0.0873, "price": 495.0}, # ~43
    }

    def test_plan_rebalance_sells_rotated_out_and_buys_equal_weight(self):
        equity, cash = 650.0, 107.0
        plan = MS.plan_rebalance(equity, cash, self.POS, targets=["TSM", "NVDA", "PLTR"])
        sold = {s["symbol"] for s in plan["sells"]}
        assert sold == {"LLY", "MSFT"}                  # core never sold, TSM kept
        assert all(s["reason"] == "ROTATE_OUT" for s in plan["sells"])
        buys = {b["symbol"]: b["dollar_amount"] for b in plan["buys"]}
        per_name = equity * MS.SLEEVE_TARGET_PCT / 3   # $65
        assert buys["NVDA"] == pytest.approx(per_name, abs=0.01)
        assert buys["PLTR"] == pytest.approx(per_name, abs=0.01)
        assert "TSM" not in buys                        # already above its $65 target
        assert plan["projected_cash"] >= equity * MS.CASH_BUFFER_PCT - 0.01

    def test_plan_rebalance_respects_hold_list(self):
        plan = MS.plan_rebalance(650.0, 107.0, self.POS, targets=["NVDA"], hold={"lly"})
        assert {s["symbol"] for s in plan["sells"]} == {"TSM", "MSFT"}

    def test_plan_rebalance_bear_regime_liquidates_sleeve_only(self):
        plan = MS.plan_rebalance(650.0, 107.0, self.POS, targets=[])
        assert {s["symbol"] for s in plan["sells"]} == {"LLY", "TSM", "MSFT"}
        assert all(s["reason"] == "REGIME_EXIT" for s in plan["sells"])
        assert plan["buys"] == []

    def test_plan_rebalance_never_breaches_cash_buffer(self):
        # tiny cash, nothing to sell -> buys limited to what is above the 10% buffer
        pos = {"VTI": {"quantity": 1.0, "price": 560.0}}
        plan = MS.plan_rebalance(650.0, 70.0, pos, targets=["A", "B", "C"])
        total_buys = sum(b["dollar_amount"] for b in plan["buys"])
        assert total_buys <= 70.0 - 65.0 + 0.01
        assert plan["projected_cash"] >= 65.0 - 0.01

    def test_plan_core_topup(self):
        # core = 124 VTI + 114 QQQ of 650; each ETF targets 195 -> QQQ is furthest below, shortfall 80.6
        plan = MS.plan_core_topup(650.0, 300.0, self.POS)
        assert plan["symbol"] == "QQQ"
        assert plan["dollar_amount"] == pytest.approx(650 * 0.30 - 0.16 * 715, abs=0.01)
        # empty book: first top-up buys one ETF up to its half share only, never the whole core target
        first = MS.plan_core_topup(600.0, 600.0, {})
        assert first["dollar_amount"] == pytest.approx(600 * 0.30, abs=0.01)
        assert MS.plan_core_topup(650.0, 65.0, self.POS) is None       # no cash above buffer
        capped = MS.plan_core_topup(650.0, 300.0, self.POS, max_dollars=50.0)
        assert capped["dollar_amount"] == 50.0
        full = {"VTI": {"quantity": 1.0, "price": 400.0}, "QQQ": {"quantity": 0.0, "price": 700.0}}
        assert MS.plan_core_topup(650.0, 300.0, full) is None          # already at 60%

    def test_disaster_stop_hits(self):
        positions = [
            {"symbol": "VTI", "quantity": 1, "average_buy_price": 400},
            {"symbol": "BAD", "quantity": 2, "average_buy_price": 100},
            {"symbol": "OK", "quantity": 2, "average_buy_price": 100},
            {"symbol": "HELD", "quantity": 2, "average_buy_price": 100},
        ]
        quotes = {"VTI": {"last_trade_price": "300"}, "BAD": {"last_trade_price": "79"},
                  "OK": {"last_trade_price": "81"}, "HELD": {"last_trade_price": "50"}}
        hits = MS.disaster_stop_hits(positions, quotes, hold={"HELD"})
        assert [h["symbol"] for h in hits] == ["BAD"]
        assert hits[0]["pnl_pct"] == -21.0

    def test_hold_list_file(self, tmp_path, monkeypatch):
        f = tmp_path / "hold.txt"
        f.write_text("# keep these\nlly\nMSFT\n\n")
        monkeypatch.setattr(rt, "TRADING_HOLD_FILE", str(f))
        assert MS.load_hold_list() == {"LLY", "MSFT"}
        monkeypatch.setattr(rt, "TRADING_HOLD_FILE", str(tmp_path / "missing.txt"))
        assert MS.load_hold_list() == set()


class TestCircuitBreaker:
    def test_requires_two_consecutive_pulses_and_ignores_glitches(self):
        acc = "ACC"
        tier, msg = ComplianceAndRiskGuard.check_daily_circuit_breaker(acc, 1000.0)
        assert tier == 0 and "baseline" in msg.lower()
        # phantom read (-57%) is ignored entirely and does not arm anything
        tier, msg = ComplianceAndRiskGuard.check_daily_circuit_breaker(acc, 430.0)
        assert tier == 0 and "glitch" in msg.lower()
        # first genuine -4% observation arms tier 1 but reports 0
        tier, msg = ComplianceAndRiskGuard.check_daily_circuit_breaker(acc, 960.0)
        assert tier == 0 and "awaiting confirmation" in msg
        # second consecutive observation confirms
        tier, msg = ComplianceAndRiskGuard.check_daily_circuit_breaker(acc, 958.0)
        assert tier == 1 and "TIER 1" in msg
        # recovery resets the pending state
        tier, _ = ComplianceAndRiskGuard.check_daily_circuit_breaker(acc, 995.0)
        assert tier == 0
        tier, msg = ComplianceAndRiskGuard.check_daily_circuit_breaker(acc, 890.0)
        assert tier == 0 and "awaiting confirmation" in msg
        tier, msg = ComplianceAndRiskGuard.check_daily_circuit_breaker(acc, 885.0)
        assert tier == 3

    def test_legacy_float_baseline_is_upgraded(self):
        today = MarketHours.now_et().strftime("%Y-%m-%d")
        os.makedirs(os.path.dirname(rt.CIRCUIT_BREAKER_FILE), exist_ok=True)
        with open(rt.CIRCUIT_BREAKER_FILE, "w") as fh:
            json.dump({f"ACC_{today}": 1000.0}, fh)
        tier, msg = ComplianceAndRiskGuard.check_daily_circuit_breaker("ACC", 990.0)
        assert tier == 0 and "Within risk limits" in msg
        data = json.load(open(rt.CIRCUIT_BREAKER_FILE))
        assert data[f"ACC_{today}"]["baseline"] == 1000.0


class TestRankBuffer:
    RANKED = [{"symbol": s, "eligible": True} for s in ["A", "B", "C", "D", "E", "F"]] + [{"symbol": "X", "eligible": False}]

    def test_no_buffer_is_strict_top_n(self):
        assert MS.select_sleeve(self.RANKED, "BULL", incumbents=["E"], buffer=0) == ["A", "B", "C"]
        assert MS.select_sleeve(self.RANKED, "BULL") == ["A", "B", "C"]

    def test_incumbent_within_buffer_is_kept_in_rank_order(self):
        assert MS.select_sleeve(self.RANKED, "BULL", incumbents=["E"], buffer=2) == ["A", "B", "E"]
        assert MS.select_sleeve(self.RANKED, "BULL", incumbents=["D", "E"], buffer=2) == ["A", "D", "E"]
        assert MS.select_sleeve(self.RANKED, "BULL", incumbents=["d"], buffer=1) == ["A", "B", "D"]

    def test_incumbent_outside_buffer_or_ineligible_rotates_out(self):
        assert MS.select_sleeve(self.RANKED, "BULL", incumbents=["F"], buffer=2) == ["A", "B", "C"]
        assert MS.select_sleeve(self.RANKED, "BULL", incumbents=["X"], buffer=5) == ["A", "B", "C"]
        assert MS.select_sleeve(self.RANKED, "BEAR", incumbents=["A"], buffer=2) == []

    def test_more_incumbents_than_slots_keeps_best_ranked(self):
        assert MS.select_sleeve(self.RANKED, "BULL", incumbents=["B", "C", "D", "E"], buffer=2) == ["B", "C", "D"]

    def test_sleeve_incumbents_excludes_core_hold_and_empty(self):
        pos = {"VTI": {"quantity": 1}, "QQQ": {"quantity": 1}, "nvda": {"quantity": 0.5},
               "LLY": {"quantity": 0.1}, "OLD": {"quantity": 0}}
        assert MS.sleeve_incumbents(pos, hold={"lly"}) == ["NVDA"]

    def test_rank_buffer_config(self, tmp_path, monkeypatch):
        cfg = tmp_path / "trader.json"
        monkeypatch.setenv("RH_TRADER_CONFIG", str(cfg))
        assert MS.rank_buffer() == MS.SLEEVE_RANK_BUFFER          # missing file -> default
        cfg.write_text(json.dumps({"strategy": {"sleeve_rank_buffer": 0}}))
        assert MS.rank_buffer() == 0
        for bad in ("x", -1, 99, True, None):
            cfg.write_text(json.dumps({"strategy": {"sleeve_rank_buffer": bad}}))
            assert MS.rank_buffer() == MS.SLEEVE_RANK_BUFFER
        cfg.write_text("not json")
        assert MS.rank_buffer() == MS.SLEEVE_RANK_BUFFER
