"""PDT tracking, re-entry cooldowns and the intraday circuit breaker."""

import os
import json
import datetime
from typing import List, Dict, Any, Optional, Tuple

from .config import CIRCUIT_BREAKER_FILE, ET_ZONE, PDT_TRACKER_FILE
from .market_hours import MarketHours
from .data import FinancialData
from .indicators import TechnicalIndicators


# ==============================================================================
# 6.4. Regulatory Compliance (PDT FINRA Rule 4210) & Intraday Circuit Breakers
# ==============================================================================

class ComplianceAndRiskGuard:
    """Enforces FINRA PDT rules, wash-sale & re-entry cooldowns, and intraday portfolio circuit breakers."""

    @classmethod
    def record_trade(cls, account_number: str, symbol: str, action: str,
                     price: Optional[float] = None, qty: Optional[float] = None,
                     pnl_pct: Optional[float] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        """Tracks day trades and recent exits (for PDT and Wash-Sale / Re-entry cooldown)."""
        today = MarketHours.now_et().strftime("%Y-%m-%d")
        os.makedirs(os.path.dirname(PDT_TRACKER_FILE), exist_ok=True)
        data = {"trades": []}
        if os.path.exists(PDT_TRACKER_FILE):
            try:
                with open(PDT_TRACKER_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {"trades": []}

        # Keep rolling 45 calendar days (~30+ business days to support full IRS 30-day wash sale tracking)
        cutoff = (MarketHours.now_et() - datetime.timedelta(days=45)).strftime("%Y-%m-%d")
        recent = [t for t in data.get("trades", []) if t.get("date", "") >= cutoff]

        entry = {
            "account": str(account_number),
            "symbol": symbol.upper(),
            "action": action.upper(),
            "date": today,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        if price is not None:
            entry["price"] = round(float(price), 4)
        if qty is not None:
            entry["qty"] = round(float(qty), 6)
        if pnl_pct is not None:
            entry["pnl_pct"] = round(float(pnl_pct), 2)
        if reason is not None:
            entry["reason"] = str(reason)

        recent.append(entry)
        data["trades"] = recent
        try:
            with open(PDT_TRACKER_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
        return data

    @classmethod
    def get_recent_exits(cls, account_number: str, symbol: Optional[str] = None, days: int = 30) -> List[Dict[str, Any]]:
        """Returns all SELL trades for an account (and optional symbol) within rolling `days` calendar days."""
        if not os.path.exists(PDT_TRACKER_FILE):
            return []
        try:
            with open(PDT_TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cutoff = (MarketHours.now_et() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            exits = [
                t for t in data.get("trades", [])
                if t.get("action") == "SELL" and str(t.get("account")) == str(account_number)
                and t.get("date", "") >= cutoff
            ]
            if symbol:
                exits = [t for t in exits if t.get("symbol", "").upper() == symbol.upper()]
            return exits
        except Exception:
            return []

    @classmethod
    def is_in_cooldown(cls, account_number: str, symbol: str,
                       cooldown_days_loss: int = 7,
                       cooldown_days_profit: int = 3) -> Tuple[bool, str]:
        """
        Enforces strict re-entry lockout rules to prevent wash sales, whipsaws, and wasteful churn:
        1. Same-Day Lockout: Any symbol sold today CANNOT be re-bought today under any circumstances.
        2. Loss / Stop-Loss Cooldown: Any symbol sold at a loss / stop-loss is locked for `cooldown_days_loss` (default 7 days).
        3. Take-Profit Cooldown: Any symbol sold at take-profit / rebalance is locked for `cooldown_days_profit` (default 3 days).
        """
        today = MarketHours.now_et().strftime("%Y-%m-%d")
        now_dt = MarketHours.now_et()
        exits = cls.get_recent_exits(account_number, symbol, days=max(cooldown_days_loss, cooldown_days_profit, 30))
        if not exits:
            return False, "No recent exits (clean for entry)"

        for ex in reversed(exits):
            ex_date = ex.get("date", "")
            if ex_date == today:
                return True, f"SAME-DAY RE-ENTRY LOCKOUT: {symbol} was sold earlier today ({ex.get('ts', today)}). Buying back on the same day is strictly prohibited."

            try:
                ex_dt = datetime.datetime.strptime(ex_date, "%Y-%m-%d").replace(tzinfo=ET_ZONE)
                days_since = (now_dt.date() - ex_dt.date()).days
            except Exception:
                days_since = 0

            pnl = ex.get("pnl_pct", 0.0)
            reason = ex.get("reason", "")
            is_loss = (pnl < 0) or ("STOP" in reason.upper()) or ("LOSS" in reason.upper())

            required_cooldown = cooldown_days_loss if is_loss else cooldown_days_profit
            if days_since < required_cooldown:
                loss_str = f"loss/stop-loss ({pnl:+.1f}%, reason: {reason or 'stop-loss'})" if is_loss else f"profit/exit ({pnl:+.1f}%)"
                return True, (f"WASH-SALE / RE-ENTRY COOLDOWN: {symbol} sold {days_since}d ago at {loss_str}. "
                              f"Required lockout is {required_cooldown}d ({required_cooldown - days_since}d remaining).")

        return False, "Cooldown period expired (clean for entry)"

    @classmethod
    def get_day_trades_count(cls, account_number: str) -> int:
        """Counts round-trip day trades (same symbol BUY + SELL on same date) in rolling 5 days."""
        if not os.path.exists(PDT_TRACKER_FILE):
            return 0
        try:
            with open(PDT_TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cutoff = (MarketHours.now_et() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
            trades = [t for t in data.get("trades", []) if t.get("date", "") >= cutoff and str(t.get("account")) == str(account_number)]
            
            by_day_sym: Dict[Tuple[str, str], List[str]] = {}
            for t in trades:
                key = (t["date"], t["symbol"])
                by_day_sym.setdefault(key, []).append(t["action"])
            
            day_trades = 0
            for (dt, sym), actions in by_day_sym.items():
                buys = actions.count("BUY")
                sells = actions.count("SELL")
                day_trades += min(buys, sells)
            return day_trades
        except Exception:
            return 0

    @classmethod
    def is_same_day_position(cls, account_number: str, symbol: str) -> bool:
        """Determines if a position was purchased during today's session (round-trip day trade risk)."""
        today = MarketHours.now_et().strftime("%Y-%m-%d")
        if not os.path.exists(PDT_TRACKER_FILE):
            return False
        try:
            with open(PDT_TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            buys = [t for t in data.get("trades", [])
                    if t.get("date") == today and t.get("symbol") == symbol.upper() and
                    t.get("action") == "BUY" and str(t.get("account")) == str(account_number)]
            return len(buys) > 0
        except Exception:
            return False

    @classmethod
    def can_day_trade(cls, account_number: str, total_equity: float) -> Tuple[bool, str]:
        """FINRA Rule 4210: Accounts < $25k are limited to 3 day trades in rolling 5 business days."""
        if total_equity >= 25000.0:
            return True, "Equity >= $25k (Exempt from PDT limit)"
        
        count = cls.get_day_trades_count(account_number)
        if count >= 3:
            return False, f"PDT Limit Reached ({count}/3 day trades used in 5-day window). Blocked from same-day round-trips."
        return True, f"PDT Compliant ({count}/3 day trades used in 5-day window)"

    CB_GLITCH_PCT = 30.0   # a single reading this far from baseline is a data error, not a market move

    @classmethod
    def check_daily_circuit_breaker(cls, account_number: str, current_equity: float) -> Tuple[int, str]:
        """
        Intraday drawdown tiers versus the first equity reading of the day:
        Tier 0: > -3.0%   normal operation
        Tier 1: <= -3.0%  halt new buys for the day
        Tier 2: <= -6.0%  (reserved; same effect as Tier 1)
        Tier 3: <= -10.0% halt all bot activity for the day (no forced liquidation)

        Two guards against bad API reads (2026-08-25 a phantom -57% tripped Tier 3):
        - a reading more than CB_GLITCH_PCT away from baseline is ignored;
        - any tier must be observed on two consecutive pulses before it is reported.
        File format: {acc_date: {"baseline": x, "pending_tier": t, "pending_count": n}};
        legacy float values are upgraded in place.
        """
        today = MarketHours.now_et().strftime("%Y-%m-%d")
        os.makedirs(os.path.dirname(CIRCUIT_BREAKER_FILE), exist_ok=True)
        data: Dict[str, Any] = {}
        if os.path.exists(CIRCUIT_BREAKER_FILE):
            try:
                with open(CIRCUIT_BREAKER_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        def _save():
            try:
                with open(CIRCUIT_BREAKER_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass

        acc_key = f"{account_number}_{today}"
        rec = data.get(acc_key)
        if isinstance(rec, (int, float)):
            rec = {"baseline": float(rec), "pending_tier": 0, "pending_count": 0}
            data[acc_key] = rec
            _save()
        if not isinstance(rec, dict) or not rec.get("baseline") or rec["baseline"] <= 0:
            if current_equity <= 0:
                return 0, "No equity reading yet; baseline not set"
            data[acc_key] = {"baseline": current_equity, "pending_tier": 0, "pending_count": 0}
            _save()
            return 0, f"Starting daily baseline set at ${current_equity:,.2f}"

        start_equity = float(rec["baseline"])
        drawdown_pct = (current_equity - start_equity) / start_equity * 100.0
        if current_equity <= 0 or abs(drawdown_pct) > cls.CB_GLITCH_PCT:
            return 0, (f"Circuit breaker ignored implausible equity reading ${current_equity:,.2f} "
                       f"({drawdown_pct:+.1f}% vs baseline ${start_equity:,.2f}); treating as data glitch")

        if drawdown_pct <= -10.0:
            raw_tier, label = 3, "halting all bot activity for today"
        elif drawdown_pct <= -6.0:
            raw_tier, label = 2, "new buy entries halted for today"
        elif drawdown_pct <= -3.0:
            raw_tier, label = 1, "new buy entries halted for today"
        else:
            raw_tier, label = 0, ""

        if raw_tier == 0:
            if rec.get("pending_tier"):
                rec["pending_tier"], rec["pending_count"] = 0, 0
                data[acc_key] = rec
                _save()
            return 0, f"Intraday P&L: {drawdown_pct:+.2f}% vs open ${start_equity:,.2f} (Within risk limits)"

        if int(rec.get("pending_tier", 0)) >= raw_tier and int(rec.get("pending_count", 0)) >= 1:
            return raw_tier, (f"CIRCUIT BREAKER TIER {raw_tier} TRIPPED: Drawdown is {drawdown_pct:.2f}% "
                              f"(confirmed on consecutive pulses); {label}.")
        rec["pending_tier"], rec["pending_count"] = raw_tier, 1
        data[acc_key] = rec
        _save()
        return 0, (f"Circuit breaker Tier {raw_tier} observed once ({drawdown_pct:.2f}%); "
                   f"awaiting confirmation on next pulse")

    @classmethod
    def get_ticker_risk_parameters(cls, symbol: str, current_price: float, default_stop_pct: float = 5.0) -> Dict[str, float]:
        """
        Calculates volatility-adjusted risk thresholds tailored to asset volatility:
        - Broad Index ETFs (VTI, QQQ, SPY, SMH, etc.): 4.5% stop, 3.5% trailing stop.
        - High-Beta / Single Equities: 2.0x ATR_pct bounded between 6.0% and 14.0% to prevent noise whipsaws.
        - Stage 1 Take-Profit: +8.0%
        - Stage 2 Take-Profit: +15.0%
        - Trailing stop distance: 1.5x ATR_pct bounded between 4.0% and 8.0%.
        """
        sym = symbol.upper().strip()
        is_broad_etf = sym in ("VTI", "QQQ", "SPY", "SMH", "IWM", "DIA")
        
        atr_pct = 1.0 if is_broad_etf else 3.5
        try:
            bars = FinancialData.fetch_historical(sym, range_period="1mo", interval="1d")
            if len(bars) >= 5:
                highs = [b["high"] for b in bars]
                lows = [b["low"] for b in bars]
                closes = [b["close"] for b in bars]
                atr_series = TechnicalIndicators.atr(highs, lows, closes, period=min(14, len(closes) - 1))
                if atr_series and atr_series[-1] is not None and current_price > 0:
                    atr_pct = (atr_series[-1] / current_price) * 100.0
        except Exception:
            pass

        if is_broad_etf:
            stop_loss = 4.5
            trailing_stop = 3.5
        else:
            # Scaled to 2.0x ATR so normal intraday high-beta market noise does not whipsaw out positions
            stop_loss = round(max(6.0, min(14.0, 2.0 * atr_pct)), 2)
            trailing_stop = round(max(4.0, min(8.0, 1.5 * atr_pct)), 2)

        return {
            "stop_loss_pct": stop_loss,
            "take_profit_1_pct": 8.0,
            "take_profit_2_pct": 15.0,
            "trailing_stop_pct": trailing_stop,
            "atr_pct": round(atr_pct, 2)
        }
