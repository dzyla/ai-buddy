"""Pure-Python technical indicators."""

import math
from typing import List, Dict, Optional


# ==============================================================================
# 3. Technical Indicator Calculations (Pure Python)
# ==============================================================================

class TechnicalIndicators:
    """Pure Python mathematical implementation of standard trading indicators."""

    @staticmethod
    def sma(series: List[float], period: int) -> List[Optional[float]]:
        """Calculates Simple Moving Average for a series."""
        out = []
        for i in range(len(series)):
            if i + 1 < period:
                out.append(None)
            else:
                window = series[i + 1 - period : i + 1]
                out.append(sum(window) / float(period))
        return out

    @staticmethod
    def ema(series: List[float], period: int) -> List[Optional[float]]:
        """Calculates Exponential Moving Average for a series."""
        if not series or len(series) < period:
            return [None] * len(series)
        
        k = 2.0 / (period + 1)
        out: List[Optional[float]] = [None] * (period - 1)
        
        # Initial SMA
        init_sma = sum(series[:period]) / float(period)
        out.append(init_sma)
        
        current_ema = init_sma
        for price in series[period:]:
            current_ema = (price * k) + (current_ema * (1.0 - k))
            out.append(current_ema)
        return out

    @classmethod
    def rsi(cls, closes: List[float], period: int = 14) -> List[Optional[float]]:
        """
        Calculates Relative Strength Index (RSI 14-period standard).
        RSI = 100 - (100 / (1 + RS)), where RS = Avg Gain / Avg Loss.
        """
        if len(closes) <= period:
            return [None] * len(closes)

        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        out: List[Optional[float]] = [None] * period

        # First average
        avg_gain = sum(gains[:period]) / float(period)
        avg_loss = sum(losses[:period]) / float(period)

        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - (100.0 / (1.0 + rs)))

        # Smoothed moving averages
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / float(period)
            avg_loss = (avg_loss * (period - 1) + losses[i]) / float(period)

            if avg_loss == 0:
                out.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_val = 100.0 - (100.0 / (1.0 + rs))
                out.append(round(rsi_val, 2))

        return out

    @classmethod
    def macd(cls, closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, List[Optional[float]]]:
        """Calculates MACD Line, Signal Line, and MACD Histogram."""
        ema_fast = cls.ema(closes, fast)
        ema_slow = cls.ema(closes, slow)

        macd_line = []
        for f, s in zip(ema_fast, ema_slow):
            if f is not None and s is not None:
                macd_line.append(f - s)
            else:
                macd_line.append(None)

        valid_macd = [v for v in macd_line if v is not None]
        none_prefix = len(macd_line) - len(valid_macd)
        
        signal_valid = cls.ema(valid_macd, signal)
        signal_line = [None] * none_prefix + signal_valid

        hist = []
        for m, s in zip(macd_line, signal_line):
            if m is not None and s is not None:
                hist.append(round(m - s, 4))
            else:
                hist.append(None)

        return {
            "macd": [round(x, 4) if x is not None else None for x in macd_line],
            "signal": [round(x, 4) if x is not None else None for x in signal_line],
            "histogram": hist
        }

    @classmethod
    def bollinger_bands(cls, closes: List[float], period: int = 20, num_std: float = 2.0) -> Dict[str, List[Optional[float]]]:
        """Calculates Bollinger Bands (Middle, Upper, Lower, Bandwidth, %B)."""
        sma_vals = cls.sma(closes, period)
        upper = []
        lower = []
        bandwidth = []
        pct_b = []

        for i in range(len(closes)):
            mid = sma_vals[i]
            if mid is None:
                upper.append(None)
                lower.append(None)
                bandwidth.append(None)
                pct_b.append(None)
            else:
                window = closes[i + 1 - period : i + 1]
                variance = sum((x - mid) ** 2 for x in window) / float(period)
                std_dev = math.sqrt(variance)
                u = mid + (num_std * std_dev)
                l = mid - (num_std * std_dev)
                upper.append(round(u, 2))
                lower.append(round(l, 2))
                bw = (u - l) / mid if mid != 0 else 0.0
                bandwidth.append(round(bw, 4))
                pb = (closes[i] - l) / (u - l) if (u - l) != 0 else 0.5
                pct_b.append(round(pb, 4))

        return {
            "middle": sma_vals,
            "upper": upper,
            "lower": lower,
            "bandwidth": bandwidth,
            "percent_b": pct_b
        }

    @staticmethod
    def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[Optional[float]]:
        """Calculates Average True Range (ATR) volatility."""
        if len(closes) <= period:
            return [None] * len(closes)

        tr = [highs[0] - lows[0]]
        for i in range(1, len(closes)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i - 1])
            lc = abs(lows[i] - closes[i - 1])
            tr.append(max(hl, hc, lc))

        out: List[Optional[float]] = [None] * (period - 1)
        init_atr = sum(tr[:period]) / float(period)
        out.append(init_atr)

        curr_atr = init_atr
        for val in tr[period:]:
            curr_atr = (curr_atr * (period - 1) + val) / float(period)
            out.append(round(curr_atr, 2))

        return out
