"""Core + monthly momentum sleeve: pure planning functions shared by the live loop and backtest."""

import math
import datetime
import re
from typing import Callable, Iterable, List, Dict, Any, Optional, Tuple

from .config import SECTOR_UNIVERSES, TRADING_HOLD_FILE, load_trader_config


# ==============================================================================
# 5.5. Core + Monthly Momentum Sleeve Strategy (pure functions, shared by live loop & backtest)
# ==============================================================================
#
# Target book (see docs/superpowers/specs/2026-09-20-momentum-sleeve-trader-design.md):
#   Core   60%  VTI + QQQ, topped up whenever cash exceeds the buffer; never sold by the bot.
#   Sleeve 30%  top-N names by 6-month total return (close > SMA200, liquidity gate), equal
#               weight, rebalanced on the first trading day the daemon sees each month.
#   Regime      SPY close < SMA200 on rebalance day -> sleeve goes to cash.
#   Cash   10%  buffer, never breached by buys.
#   Risk        disaster stop -20% from average cost on sleeve names, checked every pulse.
#
def _floor_qty(qty: float, places: int = 6) -> float:
    """Round a share quantity DOWN to `places` decimals (never sell more than is held)."""
    scale = 10 ** places
    return math.floor(float(qty) * scale + 1e-6) / scale


def _fmt_qty(qty: float) -> str:
    """Order quantity string: floored to 6 decimals, no float artefacts or exponent notation."""
    return (f"{_floor_qty(qty):.6f}".rstrip("0").rstrip(".")) or "0"


class MomentumStrategy:
    CORE_SYMBOLS: Tuple[str, ...] = ("VTI", "QQQ")
    CORE_TARGET_PCT = 0.60
    SLEEVE_TARGET_PCT = 0.30
    CASH_BUFFER_PCT = 0.10
    SLEEVE_TOP_N = 3
    # Rank hysteresis: a held sleeve name stays while ranked within top N + buffer (and still eligible).
    # dev/backtest_momentum.py (2005-2026, 3 universes): turnover -20..-28%, Sharpe/max DD unchanged within noise.
    # 0 = the original strict top-N rotation. Override: trader.json {"strategy": {"sleeve_rank_buffer": n}}.
    SLEEVE_RANK_BUFFER = 2
    # Bear-regime parking (OFF by default = cash). When SPY < SMA200 at the monthly rebalance the sleeve's 30% is
    # bought into this bond ETF instead of staying in cash; it is sold through the normal rotate-out path once the
    # regime is BULL again, and is exempt from the per-name disaster stop. Fallback chain if the ticker has no
    # usable bars: configured -> BND -> AGG -> SGOV -> cash. dev/backtest_momentum.py `park_BND` (research: tied the
    # live strategy within noise, hence shadow-only). Override: trader.json {"strategy": {"bear_park": "BND"}};
    # read only when a month's plan is first made, so enabling it mid-month never triggers an unscheduled trade.
    BEAR_PARK: Optional[str] = None
    PARK_FALLBACKS: Tuple[str, ...] = ("BND", "AGG", "SGOV")
    LOOKBACK_BARS = 126        # ~6 months of trading days
    REGIME_SMA_BARS = 200
    DISASTER_STOP_PCT = 20.0
    MIN_AVG_VOLUME = 500_000
    MIN_PRICE = 5.0
    MIN_ORDER_DOLLARS = 5.0
    REBALANCE_EARLIEST_ET = datetime.time(10, 0)   # never trade the opening auction

    @classmethod
    def universe(cls) -> List[str]:
        """Momentum universe: every curated single name (ETF sector excluded), de-duplicated."""
        out: List[str] = []
        for sector, syms in SECTOR_UNIVERSES.items():
            if sector == "CORE_INDEX_ETFS":
                continue
            for s in syms:
                if s not in out and s not in cls.CORE_SYMBOLS:
                    out.append(s)
        return out

    @staticmethod
    def _closes(bars: List[Dict[str, Any]], upto: Optional[int]) -> List[float]:
        end = len(bars) if upto is None else upto + 1
        return [float(b["close"]) for b in bars[:end]]

    @classmethod
    def regime(cls, spy_bars: List[Dict[str, Any]], as_of: Optional[int] = None) -> Dict[str, Any]:
        """BULL when SPY close > SMA200, BEAR otherwise. Insufficient history counts as BULL (no gate)."""
        closes = cls._closes(spy_bars, as_of)
        if len(closes) < cls.REGIME_SMA_BARS:
            return {"regime": "BULL", "close": closes[-1] if closes else 0.0, "sma200": None, "note": "insufficient history"}
        sma = sum(closes[-cls.REGIME_SMA_BARS:]) / cls.REGIME_SMA_BARS
        return {"regime": "BULL" if closes[-1] > sma else "BEAR", "close": closes[-1], "sma200": round(sma, 2)}

    @classmethod
    def rank(cls, bars_by_symbol: Dict[str, List[Dict[str, Any]]],
             as_of: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
        """Rank symbols by LOOKBACK_BARS total return. Eligible rows first (momentum desc), then the rest.

        `as_of` maps symbol -> bar index to evaluate at (inclusive); default = last bar.
        Bars need "close" and "volume" keys (as FinancialData.fetch_historical returns).
        """
        rows: List[Dict[str, Any]] = []
        for sym, bars in bars_by_symbol.items():
            idx = None if as_of is None else as_of.get(sym)
            if as_of is not None and idx is None:
                continue
            end = len(bars) if idx is None else idx + 1
            window = bars[:end]
            if len(window) < 2:
                continue
            closes = [float(b["close"]) for b in window]
            vols = [float(b.get("volume") or 0) for b in window]
            price = closes[-1]
            reasons: List[str] = []
            if len(closes) <= cls.LOOKBACK_BARS:
                reasons.append(f"history {len(closes)} < {cls.LOOKBACK_BARS + 1} bars")
                momentum = None
            else:
                base = closes[-1 - cls.LOOKBACK_BARS]
                momentum = (price / base - 1.0) * 100.0 if base > 0 else None
            sma200 = (sum(closes[-cls.REGIME_SMA_BARS:]) / cls.REGIME_SMA_BARS) if len(closes) >= cls.REGIME_SMA_BARS else None
            above = (sma200 is not None and price > sma200)
            if sma200 is None:
                reasons.append("no SMA200")
            elif not above:
                reasons.append("below SMA200")
            avg_vol = sum(vols[-20:]) / min(20, len(vols)) if vols else 0.0
            if avg_vol < cls.MIN_AVG_VOLUME:
                reasons.append(f"avg vol {avg_vol:,.0f} < {cls.MIN_AVG_VOLUME:,}")
            if price < cls.MIN_PRICE:
                reasons.append(f"price ${price:.2f} < ${cls.MIN_PRICE:.2f}")
            if momentum is None:
                reasons.append("no momentum")
            rows.append({
                "symbol": sym, "price": round(price, 2),
                "momentum_pct": round(momentum, 2) if momentum is not None else None,
                "sma200": round(sma200, 2) if sma200 else None, "above_sma200": above,
                "avg_volume_20": int(avg_vol), "eligible": not reasons,
                "reason": "; ".join(reasons) if reasons else "eligible",
            })
        rows.sort(key=lambda r: (not r["eligible"], -(r["momentum_pct"] if r["momentum_pct"] is not None else -1e9)))
        return rows

    @classmethod
    def rank_buffer(cls) -> int:
        """Rank hysteresis for the sleeve: `strategy.sleeve_rank_buffer` in trader.json (0-10), else the default."""
        try:
            val = (load_trader_config().get("strategy") or {}).get("sleeve_rank_buffer")
            if val is not None and not isinstance(val, bool) and 0 <= int(val) <= 10:
                return int(val)
        except (TypeError, ValueError, AttributeError):
            pass
        return cls.SLEEVE_RANK_BUFFER

    @classmethod
    def clean_park(cls, val: Any) -> Optional[str]:
        """A valid parking ticker (upper-cased) or None. Core ETFs, SPY and non-tickers are rejected."""
        if not isinstance(val, str):
            return None
        sym = val.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym) or sym in cls.CORE_SYMBOLS or sym == "SPY":
            return None
        return sym

    @classmethod
    def bear_park(cls) -> Optional[str]:
        """Bear-regime parking ETF: `strategy.bear_park` in trader.json (a plain ticker), else the default (None = cash)."""
        try:
            val = (load_trader_config().get("strategy") or {}).get("bear_park")
        except AttributeError:
            val = None
        return cls.clean_park(val) or cls.clean_park(cls.BEAR_PARK)

    @classmethod
    def park_candidates(cls, primary: Optional[str]) -> List[str]:
        """Fallback chain for the parking asset (empty when parking is off)."""
        first = cls.clean_park(primary)
        if not first:
            return []
        return list(dict.fromkeys([first, *cls.PARK_FALLBACKS]))

    @classmethod
    def resolve_park(cls, primary: Optional[str], bars_by_symbol: Dict[str, List[Dict[str, Any]]]) -> Optional[str]:
        """First candidate with usable bars (>= 2, last close > 0); None -> stay in cash."""
        for sym in cls.park_candidates(primary):
            b = bars_by_symbol.get(sym) or []
            try:
                if len(b) >= 2 and float(b[-1]["close"]) > 0:
                    return sym
            except (KeyError, TypeError, ValueError):
                continue
        return None

    @classmethod
    def choose_park(cls, regime: str, targets: List[str],
                    fetch: Callable[[List[str]], Dict[str, List[Dict[str, Any]]]],
                    held_park: Optional[str] = None, primary: Optional[str] = None) -> Optional[str]:
        """The parking ETF for this rebalance, or None (cash). Only in a BEAR regime with no sleeve targets and
        bear_park configured (`primary`, read once by the caller; default: the config).

        `held_park`: the park the bot already holds (still held and sellable). If it is in the fallback chain it
        stays the park with no data check - the chain only decides what to BUY, a data failure never liquidates
        it. With no park held, a failed fetch means cash (today's behaviour), never an error."""
        primary = cls.clean_park(primary) if primary is not None else cls.bear_park()
        if not primary or regime != "BEAR" or targets:
            return None
        held = cls.clean_park(held_park)
        if held and held in cls.park_candidates(primary):
            return held
        try:
            return cls.resolve_park(primary, fetch(cls.park_candidates(primary)))
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def select_sleeve(cls, ranked: List[Dict[str, Any]], regime: str, top_n: Optional[int] = None,
                      incumbents: Optional[List[str]] = None, buffer: int = 0) -> List[str]:
        """Top-N eligible names. With `buffer` > 0, a name already held (`incumbents`) is kept while it is
        still eligible and ranked within top_n + buffer; the remaining slots go to the best-ranked others."""
        if regime != "BULL":
            return []
        n = top_n or cls.SLEEVE_TOP_N
        eligible = [r["symbol"] for r in ranked if r["eligible"]]
        held = {s.upper() for s in (incumbents or [])}
        keep = [s for s in eligible[:n + max(0, int(buffer))] if s in held][:n] if buffer and held else []
        chosen = set(keep + [s for s in eligible if s not in keep][:n - len(keep)])
        return [s for s in eligible if s in chosen]   # rank order (buys are placed in this order)

    @classmethod
    def sleeve_incumbents(cls, positions: Dict[str, Any], hold: Optional[set] = None) -> List[str]:
        """Currently held sleeve names: everything except the core ETFs and hold-listed symbols."""
        hold = {h.upper() for h in (hold or set())}
        return sorted(s.upper() for s, p in positions.items()
                      if s.upper() not in cls.CORE_SYMBOLS and s.upper() not in hold
                      and float((p or {}).get("quantity", 0) or 0) > 0)

    @classmethod
    def load_hold_list(cls) -> set:
        """Symbols the bot must never sell (one per line, '#' comments allowed)."""
        try:
            with open(TRADING_HOLD_FILE, encoding="utf-8") as fh:
                return {ln.strip().upper() for ln in fh if ln.strip() and not ln.strip().startswith("#")}
        except OSError:
            return set()

    @classmethod
    def plan_rebalance(cls, equity: float, cash: float, positions: Dict[str, Dict[str, float]],
                       targets: List[str], hold: Optional[set] = None,
                       park: Optional[str] = None) -> Dict[str, Any]:
        """Deterministic sleeve rebalance: sells first (rotated-out names), then equal-weight buys.

        `park` (bear regime, no `targets`): the whole sleeve allocation goes to this ETF instead of cash; it is
        ignored when there are targets. park=None is exactly the original behaviour.

        positions: {symbol: {"quantity": q, "price": p}}. Returns {"sells": [...], "buys": [...],
        "projected_cash": x}. Buys never push cash below CASH_BUFFER_PCT of equity.
        """
        hold = {h.upper() for h in (hold or set())}
        park_sym = cls.clean_park(park) if (park and not targets) else None
        wanted = [park_sym] if park_sym else targets
        sells: List[Dict[str, Any]] = []
        buys: List[Dict[str, Any]] = []
        projected_cash = float(cash)
        for sym, pos in positions.items():
            s = sym.upper()
            qty = float(pos.get("quantity", 0) or 0)
            price = float(pos.get("price", 0) or 0)
            if qty <= 0 or s in cls.CORE_SYMBOLS or s in hold or s in wanted:
                continue
            sells.append({"symbol": s, "quantity": _floor_qty(qty),
                          "reason": "REGIME_EXIT" if not targets else "ROTATE_OUT"})
            projected_cash += qty * price
        if wanted:
            per_name = equity * cls.SLEEVE_TARGET_PCT / len(wanted)
            buffer = equity * cls.CASH_BUFFER_PCT
            for s in wanted:
                pos = positions.get(s, {})
                current_val = float(pos.get("quantity", 0) or 0) * float(pos.get("price", 0) or 0)
                want = per_name - current_val
                room = projected_cash - buffer
                amt = round(min(want, room), 2)
                if amt >= cls.MIN_ORDER_DOLLARS:
                    buys.append({"symbol": s, "dollar_amount": amt, "reason": "PARK_TARGET" if park_sym else "SLEEVE_TARGET"})
                    projected_cash -= amt
        return {"sells": sells, "buys": buys, "projected_cash": round(projected_cash, 2)}

    @classmethod
    def plan_core_topup(cls, equity: float, cash: float, positions: Dict[str, Dict[str, float]],
                        max_dollars: Optional[float] = None, core_target: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """One buy toward the core target: the ETF furthest below its equal share, sized to the smaller of
        its own shortfall, the total core shortfall and the cash above the buffer. None if nothing to do."""
        target_pct = core_target if core_target is not None else cls.CORE_TARGET_PCT
        core_val = {s: float(positions.get(s, {}).get("quantity", 0) or 0) * float(positions.get(s, {}).get("price", 0) or 0)
                    for s in cls.CORE_SYMBOLS}
        total_deficit = equity * target_pct - sum(core_val.values())
        per_etf_target = equity * target_pct / len(cls.CORE_SYMBOLS)
        shortfalls = {s: per_etf_target - v for s, v in core_val.items()}
        target = max(cls.CORE_SYMBOLS, key=lambda s: shortfalls[s])
        room = cash - equity * cls.CASH_BUFFER_PCT
        amt = min(shortfalls[target], total_deficit, room)
        if max_dollars is not None:
            amt = min(amt, max_dollars)
        amt = round(amt, 2)
        if amt < cls.MIN_ORDER_DOLLARS:
            return None
        return {"symbol": target, "dollar_amount": amt, "reason": "CORE_TOPUP"}

    @classmethod
    def disaster_stop_hits(cls, positions: List[Dict[str, Any]], quotes: Dict[str, Dict[str, Any]],
                           hold: Optional[set] = None, exempt: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        """Sleeve positions at or below -DISASTER_STOP_PCT from average cost. `exempt` symbols (the bear-regime
        parking ETFs) are never stopped; they leave through the monthly rotate-out instead."""
        hold = {h.upper() for h in (hold or set())} | {e.upper() for e in (exempt or ())}
        hits = []
        for pos in positions:
            sym = str(pos.get("symbol", "")).upper()
            qty = float(pos.get("quantity", 0) or 0)
            avg = float(pos.get("average_buy_price", 0) or 0)
            if not sym or qty <= 0 or avg <= 0 or sym in cls.CORE_SYMBOLS or sym in hold:
                continue
            q = quotes.get(sym, {})
            price = float(q.get("last_trade_price") or q.get("price") or 0.0)
            if price <= 0:
                continue
            pnl = (price - avg) / avg * 100.0
            if pnl <= -cls.DISASTER_STOP_PCT:
                hits.append({"symbol": sym, "quantity": qty, "price": price, "avg_cost": avg, "pnl_pct": round(pnl, 2)})
        return hits
