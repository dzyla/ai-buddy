#!/usr/bin/env python3
"""Honest daily-bar backtest of the live core + monthly momentum sleeve (robinhood_trader.py).

Differences from `robinhood_trader.py backtest` (which is a quick 5-year sanity check):
- full Yahoo history (range=max), dividend-adjusted closes (total return), default start 2005-01;
- signal on the PREVIOUS close, fills at the next open +/- slippage (live acts at 10:00 ET);
- disaster stop on the day's low vs. average cost, filled at min(open, stop) - slippage;
- daily core top-up exactly as plan_core_topup; monthly cadence = first trading day of the month;
- the baseline calls the live MomentumStrategy functions (regime, rank, select_sleeve, plan_rebalance,
  plan_core_topup); variants only change the knobs the live code exposes (see Variant below);
- Sharpe/Sortino use the 13-week T-bill (^IRX) as the risk-free rate; idle cash earns nothing
  unless --cash-yield is given (a Robinhood cash balance in the agentic account earns ~0).
- three universes: `live` (the curated 2026 list - survivorship-biased, hindsight winners),
  `sectors` (SPDR sector ETFs + SMH/IBB/IYR, no hindsight), `lc2004` (the ~30 largest US stocks at
  the end of 2004, chosen ex ante; includes later laggards such as GE, C, AIG, INTC).

Network: read-only Yahoo chart API. Bars are cached in $BT_CACHE_DIR (default ~/.cache/ai/backtest_bars);
it never touches ~/.cache/ai/trading/. Places no orders and needs no credentials.

Usage:
  python3 dev/backtest_momentum.py                     # baseline vs. all candidate variants, 3 universes
  python3 dev/backtest_momentum.py --universe live --variants base,skip_6_1
  python3 dev/backtest_momentum.py --list
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import math
import os
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import robinhood_trader as rt  # noqa: E402

MS = rt.MomentumStrategy
CACHE = os.environ.get("BT_CACHE_DIR", os.path.expanduser("~/.cache/ai/backtest_bars"))

UNIVERSES = {
    "live": None,  # MomentumStrategy.universe()
    "sectors": ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "IYR", "SMH", "IBB"],
    "lc2004": ["GE", "XOM", "MSFT", "C", "WMT", "PFE", "BAC", "JNJ", "AIG", "IBM", "INTC", "PG", "MO", "JPM",
               "CSCO", "VZ", "WFC", "KO", "CVX", "HD", "PEP", "T", "AMGN", "ORCL", "MRK", "QCOM", "UPS",
               "CMCSA", "MDT", "TXN"],
}


# ----------------------------------------------------------------------------- data
def fetch(sym: str) -> List[Dict[str, Any]]:
    """Daily bars, full history, OHLC scaled by adjclose/close (dividends + splits)."""
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, f"{sym.replace('^', '_')}_max_{datetime.date.today().isoformat()}.json")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as fh:
            return json.load(fh)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?interval=1d&period1=0&period2={int(time.time())}&events=div%2Csplit")  # range=max downsamples to monthly
    raw = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode())
            break
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    if raw is None:
        return []
    r = raw["chart"]["result"][0]
    ts = r.get("timestamp", [])
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    out = []
    for i, t in enumerate(ts):
        c, a = q["close"][i], adj[i]
        if c is None or a is None or c <= 0:
            continue
        f = a / c
        o = q["open"][i] or c
        h = q["high"][i] or c
        lo = q["low"][i] or c
        out.append({"datetime": datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%d"),
                    "open": o * f, "high": h * f, "low": lo * f, "close": a, "volume": int(q["volume"][i] or 0)})
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    return out


# ----------------------------------------------------------------------------- variants
@dataclasses.dataclass
class Variant:
    name: str = "base"
    top_n: int = 3
    lookback: int = 126            # bars of the momentum window
    skip: int = 0                  # most recent bars excluded (21 = classic "skip-month")
    score: str = "ret"             # ret | riskadj | blend
    sizing: str = "equal"          # equal | invvol
    buffer: int = 0                # incumbents kept while ranked within top_n + buffer
    abs_mom: bool = False          # per-name 12m return must beat T-bills
    regime: str = "sma200"         # sma200 | band | sma10m | none
    band_pct: float = 2.0
    park: Optional[str] = None     # ETF the sleeve sits in while the regime is BEAR
    stop_pct: Optional[float] = 20.0
    sector_cap: int = 0            # max names per SECTOR_UNIVERSES bucket (0 = off)
    live_rank: bool = False        # select via MomentumStrategy.select_sleeve(incumbents, buffer) - the live path


VARIANTS = {
    "base": Variant("base"),
    "skip_6_1": Variant("skip_6_1", lookback=126, skip=21),
    "mom_12_1": Variant("mom_12_1", lookback=252, skip=21),
    "riskadj_6m": Variant("riskadj_6m", score="riskadj"),
    "blend_3_6_12": Variant("blend_3_6_12", score="blend"),
    "invvol": Variant("invvol", sizing="invvol"),
    "buffer_1": Variant("buffer_1", buffer=1),
    "buffer_2": Variant("buffer_2", buffer=2),
    "buffer_3": Variant("buffer_3", buffer=3),
    "live_default": Variant("live_default", buffer=rt.MomentumStrategy.SLEEVE_RANK_BUFFER, live_rank=True),
    "abs_mom": Variant("abs_mom", abs_mom=True),
    "regime_band2": Variant("regime_band2", regime="band", band_pct=2.0),
    "regime_10m": Variant("regime_10m", regime="sma10m"),
    "regime_none": Variant("regime_none", regime="none"),
    "park_SHY": Variant("park_SHY", park="SHY"),
    "park_IEF": Variant("park_IEF", park="IEF"),
    "park_BIL": Variant("park_BIL", park="BIL"),
    # The live `bear_park` option (trader.json strategy.bear_park): BND -> AGG -> SGOV -> cash while SPY < SMA200.
    "park_BND": Variant("park_BND", park="BND"),
    "no_stop": Variant("no_stop", stop_pct=None),
    "stop_15": Variant("stop_15", stop_pct=15.0),
    "top_5": Variant("top_5", top_n=5),
    "sector_cap2": Variant("sector_cap2", sector_cap=2),
}


def combine(spec: str) -> Variant:
    """'a+b' merges the non-default fields of variants a and b (later wins)."""
    default = dataclasses.asdict(Variant())
    merged = dict(default)
    for part in spec.split("+"):
        for k, val in dataclasses.asdict(VARIANTS[part]).items():
            if k != "name" and val != default[k]:
                merged[k] = val
    merged["name"] = spec
    return Variant(**merged)


# GICS-style buckets for the ex-ante universes, so the sector cap can be tested off the live list.
EXTRA_SECTORS = {
    "TECH": ["MSFT", "IBM", "INTC", "CSCO", "ORCL", "QCOM", "TXN"],
    "FIN": ["C", "BAC", "AIG", "JPM", "WFC"],
    "HEALTH": ["PFE", "JNJ", "AMGN", "MRK", "MDT"],
    "ENERGY": ["XOM", "CVX"],
    "STAPLES": ["WMT", "PG", "MO", "KO", "PEP"],
    "COMM": ["VZ", "T", "CMCSA"],
    "INDUSTRIAL": ["GE", "UPS"],
    "DISCRETIONARY": ["HD"],
}


def _sector_of() -> Dict[str, str]:
    out = {s: k for k, syms in EXTRA_SECTORS.items() for s in syms}
    out.update({s: k for k, syms in rt.SECTOR_UNIVERSES.items() for s in syms})
    return out


# ----------------------------------------------------------------------------- engine
class Sim:
    def __init__(self, bars: Dict[str, List[Dict[str, Any]]], universe: List[str], v: Variant,
                 start: str, end: Optional[str], start_cash: float, slip_bps: float, cash_yield: bool,
                 rf: Dict[str, float], offset: int = 1):
        self.bars, self.v = bars, v
        self.offset = offset   # rebalance on the offset-th trading day of the month (live: 1)
        self.univ = [s for s in universe if s in bars]
        self.idx = {s: {b["datetime"]: i for i, b in enumerate(bars[s])} for s in bars}
        self.dates = [b["datetime"] for b in bars["SPY"] if b["datetime"] >= start and (end is None or b["datetime"] <= end)]
        self.slip = slip_bps / 1e4
        self.cash = float(start_cash)
        self.cash_yield = cash_yield
        self.rf = rf
        self.pos: Dict[str, Dict[str, float]] = {}  # sym -> qty, avg
        self.curve: List[Tuple[str, float]] = []
        self.sleeve_traded = 0.0
        self.n_trades = 0
        self.stops = 0
        self.core = list(MS.CORE_SYMBOLS)
        self.sector = _sector_of()

    # helpers --------------------------------------------------------------
    def px(self, s: str, d: str, k: str = "close") -> Optional[float]:
        i = self.idx[s].get(d)
        return self.bars[s][i][k] if i is not None else None

    def last_close(self, s: str, d: str) -> Optional[float]:
        i = self.idx[s].get(d)
        if i is None:
            return None
        return self.bars[s][i]["close"]

    def equity(self, prices: Dict[str, float]) -> float:
        return self.cash + sum(p["qty"] * prices.get(s, p["mark"]) for s, p in self.pos.items())

    def buy(self, s: str, amt: float, price: float, sleeve: bool) -> None:
        if amt <= 0:
            return
        fill = price * (1 + self.slip)
        q = amt / fill
        self.cash -= amt
        p = self.pos.get(s)
        if p:
            p["avg"] = (p["avg"] * p["qty"] + fill * q) / (p["qty"] + q)
            p["qty"] += q
        else:
            self.pos[s] = {"qty": q, "avg": fill, "mark": price}
        self.n_trades += 1
        if sleeve:
            self.sleeve_traded += amt

    def sell(self, s: str, price: float, sleeve: bool = True) -> None:
        p = self.pos.pop(s)
        proceeds = p["qty"] * price * (1 - self.slip)
        self.cash += proceeds
        self.n_trades += 1
        if sleeve:
            self.sleeve_traded += proceeds

    # signal (computed on bars up to and including `d_sig`) -------------------
    def regime(self, d_sig: str) -> str:
        v = self.v
        i = self.idx["SPY"][d_sig]
        spy = self.bars["SPY"]
        if v.regime == "none":
            return "BULL"
        if v.regime == "sma200":
            return MS.regime(spy[max(0, i - 400):i + 1])["regime"]  # live function
        closes = [b["close"] for b in spy[:i + 1]]
        if v.regime == "band":
            sma = sum(closes[-200:]) / 200
            return "BEAR" if closes[-1] < sma * (1 - v.band_pct / 100) else "BULL"
        if v.regime == "sma10m":
            # month-end closes: last bar of each calendar month in the window, current bar counts as latest
            me: Dict[str, float] = {}
            for b in spy[max(0, i - 260):i + 1]:
                me[b["datetime"][:7]] = b["close"]
            vals = list(me.values())[-10:]
            return "BULL" if closes[-1] > sum(vals) / len(vals) else "BEAR"
        raise ValueError(v.regime)

    def targets(self, d_sig: str, regime: str) -> Tuple[List[str], Dict[str, float]]:
        v = self.v
        if regime != "BULL":
            return [], {}
        window: Dict[str, List[Dict[str, Any]]] = {}
        for s in self.univ:
            i = self.idx[s].get(d_sig)
            if i is None:
                continue
            window[s] = self.bars[s][max(0, i - 299):i + 1]   # rank() needs <= 253 bars; slicing is equivalent
        if v.live_rank:
            ranked = MS.rank(window)
            incumbents = MS.sleeve_incumbents({s: {"quantity": p["qty"]} for s, p in self.pos.items()})
            tg = MS.select_sleeve(ranked, regime, v.top_n, incumbents=incumbents, buffer=v.buffer)
            return tg, {s: 1.0 / len(tg) for s in tg} if tg else {}
        ranked = MS.rank(window)  # live eligibility gates: SMA200, liquidity, price, history
        elig = [r["symbol"] for r in ranked if r["eligible"]]
        scores: Dict[str, float] = {}
        vols: Dict[str, float] = {}
        rf12 = self.rf_cum(d_sig, 252)
        for s in elig:
            c = [b["close"] for b in window[s]]
            sc = self.score(c)
            if sc is None:
                continue
            if v.abs_mom:
                if len(c) < 253 or c[-1] / c[-253] - 1 <= rf12:
                    continue
            scores[s] = sc
            r = [c[k] / c[k - 1] - 1 for k in range(len(c) - 63, len(c))]
            m = sum(r) / len(r)
            vols[s] = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) or 1e-6
        order = sorted(scores, key=lambda s: -scores[s])
        if v.sector_cap:
            kept, cnt = [], {}
            for s in order:
                k = self.sector.get(s, s)
                if cnt.get(k, 0) < v.sector_cap:
                    kept.append(s)
                    cnt[k] = cnt.get(k, 0) + 1
            order = kept
        if v.buffer:
            inc = [s for s in order[:v.top_n + v.buffer] if s in self.pos]
            tg = inc[:v.top_n]
            for s in order:
                if len(tg) >= v.top_n:
                    break
                if s not in tg:
                    tg.append(s)
            tg = [s for s in order if s in tg]   # rank order, as MomentumStrategy.select_sleeve
        else:
            tg = order[:v.top_n]
        if not tg:
            return [], {}
        if v.sizing == "invvol":
            w = {s: 1 / vols[s] for s in tg}
        else:
            w = {s: 1.0 for s in tg}
        tot = sum(w.values())
        return tg, {s: x / tot for s, x in w.items()}

    def score(self, c: List[float]) -> Optional[float]:
        v = self.v

        def ret(lb: int, skip: int = 0) -> Optional[float]:
            if len(c) <= lb:
                return None
            return c[-1 - skip] / c[-1 - lb] - 1
        if v.score == "ret":
            return ret(v.lookback, v.skip)
        if v.score == "riskadj":
            r0 = ret(v.lookback, v.skip)
            if r0 is None:
                return None
            r = [c[k] / c[k - 1] - 1 for k in range(len(c) - v.lookback, len(c))]
            m = sum(r) / len(r)
            sd = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252)
            return r0 / sd if sd > 0 else None
        if v.score == "blend":
            rs = [ret(63), ret(126), ret(252)]
            if any(x is None for x in rs):
                return None
            return sum(rs) / 3
        raise ValueError(v.score)

    def rf_cum(self, d: str, n: int) -> float:
        i = self.idx["SPY"][d]
        tot = 0.0
        for b in self.bars["SPY"][max(0, i - n + 1):i + 1]:
            tot += self.rf.get(b["datetime"], 0.0)
        return tot

    # main loop --------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        v = self.v
        last_month = None
        prev_d = None
        day_in_month = 0
        all_syms = list(self.bars)
        for d in self.dates:
            opens = {s: self.px(s, d, "open") for s in all_syms}
            opens = {s: p for s, p in opens.items() if p}
            closes = {s: self.px(s, d) for s in all_syms}
            closes = {s: p for s, p in closes.items() if p}
            if self.cash_yield and self.cash > 0:
                self.cash *= 1 + self.rf.get(d, 0.0)
            # 1) disaster stop (sleeve names only, every pulse => day's low)
            if v.stop_pct:
                for s in list(self.pos):
                    if s in self.core or s in MS.park_candidates(v.park) or s not in opens:
                        continue
                    stop = self.pos[s]["avg"] * (1 - v.stop_pct / 100)
                    if self.px(s, d, "low") <= stop:
                        self.sell(s, min(opens[s], stop))
                        self.stops += 1
            # 2) monthly rebalance, signal on the previous close, fills at today's open
            month = d[:7]
            if month != last_month:
                last_month, day_in_month = month, 0
            day_in_month += 1
            if day_in_month == self.offset and prev_d is not None:
                regime = self.regime(prev_d)
                tg, w = self.targets(prev_d, regime)
                park = None
                if not tg and v.park and regime != "BULL":   # parking is a BEAR-regime feature (as live)
                    chain = MS.park_candidates(v.park)   # hold-first, as live: a held park stays; the chain only picks a buy
                    park = next((c for c in chain if c in self.pos), None) or next((c for c in chain if c in opens), None)
                    if park:
                        tg, w = [park], {park: 1.0}
                eq = self.equity(opens)
                if v.sizing == "equal":
                    pos_map = {s: {"quantity": p["qty"], "price": opens.get(s, p["mark"])} for s, p in self.pos.items()}
                    plan = MS.plan_rebalance(eq, self.cash, pos_map, [] if park else tg, park=park)   # live planner (equal weight)
                    for sl in plan["sells"]:
                        if sl["symbol"] in opens:
                            self.sell(sl["symbol"], opens[sl["symbol"]])
                    for b in plan["buys"]:
                        if b["symbol"] in opens:
                            self.buy(b["symbol"], b["dollar_amount"], opens[b["symbol"]], True)
                else:
                    self.plan_weighted(eq, tg, w, opens)
            # 3) daily core top-up (one order per day, as live)
            eq = self.equity(opens)
            pos_map = {s: {"quantity": p["qty"], "price": opens.get(s, p["mark"])} for s, p in self.pos.items()}
            t = MS.plan_core_topup(eq, self.cash, pos_map)
            if t and t["symbol"] in opens:
                self.buy(t["symbol"], t["dollar_amount"], opens[t["symbol"]], False)
            for s, p in self.pos.items():
                if s in closes:
                    p["mark"] = closes[s]
            self.curve.append((d, self.equity(closes)))
            prev_d = d
        return {"curve": self.curve, "sleeve_traded": self.sleeve_traded, "trades": self.n_trades, "stops": self.stops}

    def plan_weighted(self, eq: float, tg: List[str], w: Dict[str, float], opens: Dict[str, float]) -> None:
        """Same rules as plan_rebalance (sell rotated-out, buy up to target, never breach buffer) with weights."""
        for s in list(self.pos):
            if s in self.core or s in tg:
                continue
            if s in opens:
                self.sell(s, opens[s])
        buffer = eq * MS.CASH_BUFFER_PCT
        for s in tg:
            if s not in opens:
                continue
            cur = self.pos.get(s, {}).get("qty", 0.0) * opens[s]
            amt = round(min(eq * MS.SLEEVE_TARGET_PCT * w[s] - cur, self.cash - buffer), 2)
            if amt >= MS.MIN_ORDER_DOLLARS:
                self.buy(s, amt, opens[s], True)


# ----------------------------------------------------------------------------- metrics
def metrics(curve: List[Tuple[str, float]], rf: Dict[str, float], lo: str, hi: str,
            sleeve_traded: Optional[float] = None) -> Dict[str, float]:
    pts = [(d, e) for d, e in curve if lo <= d <= hi]
    if len(pts) < 30:
        return {}
    rets = [(pts[k][0], pts[k][1] / pts[k - 1][1] - 1) for k in range(1, len(pts))]
    yrs = len(rets) / 252
    cagr = (pts[-1][1] / pts[0][1]) ** (1 / yrs) - 1
    r = [x for _, x in rets]
    ex = [x - rf.get(d, 0.0) for d, x in rets]
    m = sum(r) / len(r)
    vol = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252)
    me = sum(ex) / len(ex)
    sde = math.sqrt(sum((x - me) ** 2 for x in ex) / (len(ex) - 1))
    dn = math.sqrt(sum(min(0.0, x) ** 2 for x in ex) / len(ex))
    peak, mdd = pts[0][1], 0.0
    for _, e in pts:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    out = {"cagr": cagr * 100, "vol": vol * 100, "sharpe": me / sde * math.sqrt(252) if sde else 0.0,
           "sortino": me / dn * math.sqrt(252) if dn else 0.0, "mdd": mdd * 100,
           "calmar": cagr / abs(mdd) if mdd else 0.0}
    return out


def turnover(sim_out: Dict[str, Any]) -> float:
    """One-way sleeve turnover per year as a fraction of average equity."""
    c = sim_out["curve"]
    avg = sum(e for _, e in c) / len(c)
    return sim_out["sleeve_traded"] / 2 / avg / (len(c) / 252) * 100


# ----------------------------------------------------------------------------- driver
def load(universe: List[str], extra: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    out = {}
    for s in dict.fromkeys(universe + extra):
        b = fetch(s)
        if len(b) >= 260:
            out[s] = b
        else:
            print(f"  (no usable history for {s}: {len(b)} bars)", file=sys.stderr)
    return out


def rf_daily() -> Dict[str, float]:
    irx = fetch("^IRX")  # 13-week T-bill yield in percent (close field; adj factor is 1)
    return {b["datetime"]: b["close"] / 100 / 252 for b in irx}


PERIODS = {"IS": ("2005-01-01", "2015-12-31"), "OOS": ("2016-01-01", "2099-12-31"),
           "FULL": ("2005-01-01", "2099-12-31")}
CRISES = {"GFC 07-10..09-03": ("2007-10-09", "2009-03-09"), "COVID 20-02..03": ("2020-02-19", "2020-03-23"),
          "2022 bear": ("2022-01-03", "2022-10-12")}


def run_one(bars, universe, v, args, rf) -> Dict[str, Any]:
    old = (MS.CORE_TARGET_PCT, MS.SLEEVE_TARGET_PCT)
    if args.sleeve_only:   # amplify the sleeve's differences: 90% sleeve, no core
        MS.CORE_TARGET_PCT, MS.SLEEVE_TARGET_PCT = 0.0, 0.9
    try:
        outs = []
        for off in args.offsets:
            sim = Sim(bars, universe, v, args.start, args.end, args.cash, args.slippage_bps, args.cash_yield, rf, off)
            outs.append(sim.run())
    finally:
        MS.CORE_TARGET_PCT, MS.SLEEVE_TARGET_PCT = old
    if len(outs) == 1:
        out = outs[0]
    else:   # average equity curve over rebalance-day offsets (removes "timing luck")
        n = min(len(o["curve"]) for o in outs)
        out = {"curve": [(outs[0]["curve"][k][0], sum(o["curve"][k][1] for o in outs) / len(outs)) for k in range(n)],
               "sleeve_traded": sum(o["sleeve_traded"] for o in outs) / len(outs),
               "trades": sum(o["trades"] for o in outs) // len(outs), "stops": sum(o["stops"] for o in outs) // len(outs)}
    res = {"variant": v.name, "turnover": turnover(out), "stops": out["stops"], "trades": out["trades"]}
    for k, (lo, hi) in PERIODS.items():
        res[k] = metrics(out["curve"], rf, lo, hi)
    for k, (lo, hi) in CRISES.items():
        pts = [e for d, e in out["curve"] if lo <= d <= hi]
        res[k] = (pts[-1] / pts[0] - 1) * 100 if len(pts) > 1 else float("nan")
    res["curve"] = out["curve"]
    return res


def fmt(res: Dict[str, Any]) -> str:
    cells = [f"{res['variant']:<22}"]
    for k in ("IS", "OOS"):
        m = res[k]
        cells.append(f"{m['cagr']:6.1f} {m['vol']:5.1f} {m['sharpe']:5.2f} {m['sortino']:5.2f} {m['mdd']:6.1f} {m['calmar']:5.2f}"
                     if m else " " * 40)
    cells.append(f"{res['turnover']:6.0f}% {res['stops']:3d}")
    cells.append(" ".join(f"{res[k]:6.1f}" for k in CRISES))
    return " | ".join(cells)


HEADER = (f"{'variant':<22} | {'IS 2005-15: CAGR   vol  Shrp  Sort    MDD  Calm':<40} | "
          f"{'OOS 2016-: CAGR   vol  Shrp  Sort    MDD  Calm':<40} | turn/yr stp | GFC   COVID  2022")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", default="live,sectors,lc2004")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--slippage-bps", type=float, default=10.0)
    ap.add_argument("--sleeve-only", action="store_true", help="90%% sleeve / 0%% core (isolates the sleeve rules)")
    ap.add_argument("--offsets", default="1", help="comma list of rebalance trading-day offsets to average (live: 1)")
    ap.add_argument("--cash-yield", action="store_true", help="idle cash earns the T-bill rate")
    ap.add_argument("--json", help="write per-variant metrics (no curves) to this file")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    args.offsets = [int(x) for x in args.offsets.split(",")]
    if args.list:
        for k, v in VARIANTS.items():
            print(k, v)
        return
    rf = rf_daily()
    report = {}
    for uname in args.universe.split(","):
        universe = UNIVERSES[uname] or MS.universe()
        bars = load(universe, ["SPY"] + list(MS.CORE_SYMBOLS) + ["SHY", "IEF", "BIL", "BND", "AGG", "SGOV"])
        print(f"\n=== universe {uname} ({len([s for s in universe if s in bars])} names), "
              f"start ${args.cash:,.0f}, slippage {args.slippage_bps:g} bps ===")
        print(HEADER)
        bench = {"variant": "VTI b&h"}
        report[uname] = []
        for name in args.variants.split(","):
            try:
                v = combine(name)
            except KeyError as exc:
                print(f"unknown variant {exc}", file=sys.stderr)
                continue
            res = run_one(bars, universe, v, args, rf)
            print(fmt(res), flush=True)
            report[uname].append({k: x for k, x in res.items() if k != "curve"})
        vti = [(b["datetime"], b["close"]) for b in bars["VTI"] if b["datetime"] >= args.start]
        bench.update({k: metrics(vti, rf, lo, hi) for k, (lo, hi) in PERIODS.items()})
        bench.update({"turnover": 0.0, "stops": 0})
        for k, (lo, hi) in CRISES.items():
            pts = [e for d, e in vti if lo <= d <= hi]
            bench[k] = (pts[-1] / pts[0] - 1) * 100
        print(fmt(bench))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)


if __name__ == "__main__":
    main()
