"""With bear_park null the trader's observable output must be identical to the code before bear_park existed
(commit 5ef6721): planner output, the monthly rebalance flag, the order list, the shadow record and the daily
summary text. tests/golden_bear_park_null.json was produced by running this very scenario on 5ef6721
(GOLDEN_DUMP=<path> writes the current output instead of comparing)."""
import json
import os

import robinhood_trader as rt
from robinhood_trader import MomentumStrategy as MS, FinancialData, MarketHours, _momentum_rebalance_pulse
from trader import shadow, summary
from test_trader_pulses import ACC, HIST, at, bars, broker  # noqa: F401
from test_trader_shadow import FakeBroker

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_bear_park_null.json")


def _scenario(broker, monkeypatch):
    out = {}
    pos = {"TSM": {"quantity": 1.0, "price": 100.0}, "VTI": {"quantity": 1.0, "price": 100.0}}
    out["plan_bear"] = MS.plan_rebalance(1000.0, 100.0, pos, [])
    out["plan_bull"] = MS.plan_rebalance(1000.0, 100.0, pos, ["NVDA", "PLTR"], {"LLY"})
    spy_bear = bars(600, 450)
    monkeypatch.setattr(FinancialData, "fetch_historical",
                        lambda sym, range_period="1y", interval="1d", **kw: spy_bear if sym == "SPY" else HIST.get(sym, []))
    monkeypatch.setattr(MarketHours, "now_et", lambda: at(10, 5))
    _momentum_rebalance_pulse(ACC, dry_run=False)
    _momentum_rebalance_pulse(ACC, dry_run=False)
    out["orders"] = [list(o) for o in broker.orders]
    out["flag"] = json.load(open(rt._rebalance_flag_path("2026-09")))
    fb = FakeBroker()
    out["shadow"] = shadow.run_shadow_pulse(at(10, 30), {"shadow": {"strategy": {"sleeve_rank_buffer": 0}}},
                                            broker=fb, account_number="ACC1")
    out["shadow_mutations"] = fb.mutations
    out["summary"] = summary.build_daily_summary("2026-09-08")
    return json.loads(json.dumps(out, default=str))


def test_null_bear_park_output_equals_pre_bear_park_code(broker, monkeypatch):
    got = _scenario(broker, monkeypatch)
    dump = os.environ.get("GOLDEN_DUMP")
    if dump:
        json.dump(got, open(dump, "w"), indent=1, sort_keys=True)
        return
    want = json.load(open(GOLDEN))
    assert got == want
