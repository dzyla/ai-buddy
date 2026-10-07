"""Market data: quotes and daily history (Robinhood MCP first, Yahoo fallback)."""

import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import datetime
from typing import List, Dict, Any

from .config import CACHE_DIR
from .broker import RobinhoodExecutor


# ==============================================================================
# 2. Financial Data & Quotes Engine
# ==============================================================================

class FinancialData:
    """Fetches real-time quotes, fundamentals, and historical OHLCV bars with multi-source fallback."""

    USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    @classmethod
    def _http_get(cls, url: str, timeout: int = 10) -> str:
        req = urllib.request.Request(url, headers={
            "User-Agent": cls.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    @classmethod
    def fetch_quote(cls, ticker: str) -> Dict[str, Any]:
        """Fetches real-time quote, day statistics, and fundamentals for a ticker."""
        ticker = ticker.strip().upper()

        # 1. Primary source: Robinhood MCP Live Quotes
        try:
            rh_quotes = RobinhoodExecutor.get_equity_quotes([ticker])
            if ticker in rh_quotes:
                q = rh_quotes[ticker]
                price = float(q.get("last_trade_price") or q.get("last_non_reg_trade_price") or q.get("price") or 0.0)
                prev_close = float(q.get("adjusted_previous_close") or q.get("previous_close") or price)
                if price > 0:
                    change = price - prev_close if prev_close else 0.0
                    change_pct = (change / prev_close * 100.0) if prev_close else 0.0
                    bid = float(q.get("bid_price") or price)
                    ask = float(q.get("ask_price") or price)
                    return {
                        "ticker": ticker,
                        "price": round(price, 2),
                        "previous_close": round(prev_close, 2),
                        "change": round(change, 2),
                        "change_percent": round(change_pct, 2),
                        "day_high": round(max(price, ask), 2),
                        "day_low": round(min(price, bid), 2),
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "volume": int(q.get("volume", 0) or 1000000),
                        "currency": "USD",
                        "exchange": "US",
                        "source": "robinhood_mcp",
                        "timestamp": datetime.datetime.now().isoformat()
                    }
        except Exception:
            pass

        # 2. Secondary source: Yahoo Finance chart API
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        try:
            raw = cls._http_get(url)
            data = json.loads(raw)
            meta = data["chart"]["result"][0]["meta"]
            
            price = meta.get("regularMarketPrice") or meta.get("chartPreviousClose", 0.0)
            prev_close = meta.get("chartPreviousClose") or meta.get("previousClose", price)
            change = price - prev_close if price and prev_close else 0.0
            change_pct = (change / prev_close * 100.0) if prev_close else 0.0

            high = meta.get("regularMarketDayHigh", 0.0)
            low = meta.get("regularMarketDayLow", 0.0)
            vol = meta.get("regularMarketVolume", 0)

            return {
                "ticker": ticker,
                "price": round(price, 2),
                "previous_close": round(prev_close, 2),
                "change": round(change, 2),
                "change_percent": round(change_pct, 2),
                "day_high": round(high, 2) if high else round(price, 2),
                "day_low": round(low, 2) if low else round(price, 2),
                "volume": vol,
                "fifty_two_week_high": meta.get("fiftyTwoWeekHigh", 0.0),
                "fifty_two_week_low": meta.get("fiftyTwoWeekLow", 0.0),
                "currency": meta.get("currency", "USD"),
                "exchange": meta.get("exchangeName", "NASDAQ"),
                "source": "yahoo_finance",
                "timestamp": datetime.datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "ticker": ticker,
                "price": 100.0,
                "previous_close": 100.0,
                "change": 0.0,
                "change_percent": 0.0,
                "day_high": 100.0,
                "day_low": 100.0,
                "volume": 1000000,
                "fifty_two_week_high": 120.0,
                "fifty_two_week_low": 80.0,
                "currency": "USD",
                "exchange": "UNKNOWN",
                "source": "fallback",
                "error": str(e),
                "timestamp": datetime.datetime.now().isoformat()
            }

    @classmethod
    def fetch_historical(cls, ticker: str, range_period: str = "6mo", interval: str = "1d") -> List[Dict[str, Any]]:
        """Fetches historical OHLCV bars (open, high, low, close, volume)."""
        ticker = ticker.strip().upper()

        span_days = 90
        if "mo" in range_period:
            try:
                span_days = int(range_period.replace("mo", "")) * 30
            except ValueError:
                span_days = 90
        elif "y" in range_period:
            try:
                span_days = int(range_period.replace("y", "")) * 365
            except ValueError:
                span_days = 365
        elif "d" in range_period:
            try:
                span_days = int(range_period.replace("d", ""))
            except ValueError:
                span_days = 30

        # 1. Primary source: Robinhood MCP Historical Bars
        try:
            rh_hist = RobinhoodExecutor.get_equity_historicals([ticker], interval="day", span_days=span_days)
            if ticker in rh_hist and len(rh_hist[ticker]) > 0:
                bars = []
                for b in rh_hist[ticker]:
                    try:
                        begins = b.get("begins_at", "")
                        dt_str = begins[:10] if len(begins) >= 10 else datetime.datetime.now().strftime("%Y-%m-%d")
                        c_p = float(b.get("close_price") or 0.0)
                        o_p = float(b.get("open_price") or c_p)
                        h_p = float(b.get("high_price") or max(o_p, c_p))
                        l_p = float(b.get("low_price") or min(o_p, c_p))
                        vol = int(b.get("volume") or 0)
                        if c_p > 0:
                            bars.append({
                                "datetime": dt_str,
                                "open": round(o_p, 2),
                                "high": round(h_p, 2),
                                "low": round(l_p, 2),
                                "close": round(c_p, 2),
                                "volume": vol
                            })
                    except Exception:
                        pass
                if len(bars) >= 5:
                    return bars
        except Exception:
            pass

        # 2. Secondary source: Yahoo Finance API
        return cls.fetch_historical_yahoo(ticker, range_period=range_period, interval=interval)

    @classmethod
    def fetch_historical_yahoo(cls, ticker: str, range_period: str = "6mo", interval: str = "1d") -> List[Dict[str, Any]]:
        """Yahoo Finance chart API only (no Robinhood MCP). Used as fallback and by `backtest`.

        Yahoo accepts ranges such as 1mo, 6mo, 1y, 2y, 5y, max. Returns [] on any failure.
        """
        ticker = ticker.strip().upper()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_period}"
        # Daily on-disk cache: repeated backtests must not hammer Yahoo (it rate-limits with HTTP 429).
        cache_dir = os.path.join(CACHE_DIR, "bars")
        cache_fp = os.path.join(cache_dir, f"{ticker}_{interval}_{range_period}_{datetime.date.today().isoformat()}.json")
        if os.path.exists(cache_fp):
            try:
                with open(cache_fp, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                pass
        try:
            raw = None
            for attempt in range(3):
                try:
                    # Yahoo answers a bare Mozilla UA but 429s the full browser header set used by _http_get.
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        raw = resp.read().decode("utf-8", errors="replace")
                    break
                except urllib.error.HTTPError as he:
                    if he.code == 429 and attempt < 2:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    raise
            data = json.loads(raw)
            result = data["chart"]["result"][0]
            timestamps = result.get("timestamp", [])
            quote = result["indicators"]["quote"][0]

            opens = quote.get("open", [])
            highs = quote.get("high", [])
            lows = quote.get("low", [])
            closes = quote.get("close", [])
            volumes = quote.get("volume", [])

            bars = []
            for i, ts in enumerate(timestamps):
                if i < len(closes) and closes[i] is not None:
                    bars.append({
                        "timestamp": ts,
                        "datetime": datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d"),
                        "open": round(opens[i] or closes[i], 2),
                        "high": round(highs[i] or closes[i], 2),
                        "low": round(lows[i] or closes[i], 2),
                        "close": round(closes[i], 2),
                        "volume": int(volumes[i] or 0)
                    })
            if bars:
                try:
                    os.makedirs(cache_dir, exist_ok=True)
                    with open(cache_fp, "w", encoding="utf-8") as fh:
                        json.dump(bars, fh)
                except OSError:
                    pass
            return bars
        except Exception:
            return []
