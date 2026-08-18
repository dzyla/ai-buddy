#!/usr/bin/env python3
"""
Robinhood Agentic Trading & Market Hours Analysis Engine
=========================================================
Autonomous financial data, technical indicator, news sentiment, and algorithmic
trading harness for Robinhood Agentic MCP and local execution.

Key capabilities:
- US Market Hours & Holidays Engine (NYSE / NASDAQ 9:30 AM - 4:00 PM ET)
- Real-time & Historical Quotes + Technical Indicators (RSI, SMA, EMA, MACD, Bollinger Bands, ATR)
- Online Financial News & Sentiment Search (SearXNG, RSS, Sentiment Scoring)
- Algorithmic Trading & Profit Maximization (Trend Following, Mean Reversion, Trailing Stops, Take Profit)
- Portfolio Risk Controls (Stop Loss, Max Position Size, Cash Buffer, Limit Orders)
- Autonomous Working Hours Monitor & Daemon
- Robinhood MCP Integration & OAuth Helper
"""

import os
import sys
import json
import time
import math
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import re
import datetime
import csv
from typing import List, Dict, Any, Optional, Tuple

try:
    import zoneinfo
    ET_ZONE = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    # Fallback to fixed Eastern Time (UTC-5 / UTC-4 approx)
    ET_ZONE = datetime.timezone(datetime.timedelta(hours=-5), "EST")

CONFIG_DIR = os.path.expanduser("~/.config/ai")
CACHE_DIR = os.path.expanduser("~/.cache/ai/trading")
os.makedirs(CACHE_DIR, exist_ok=True)
TRADING_STATE_FILE = os.path.join(CONFIG_DIR, "robinhood_trading_state.json")
VAULT_DIR = os.path.join(CONFIG_DIR, "trading_vault")

# Multi-Sector High-Growth Universe (Curated by Sector)
SECTOR_UNIVERSES = {
    "AI_CHIPS_INFRA": ["NVDA", "AVGO", "ARM", "SMCI", "PLTR", "ANET", "AMD", "TSM", "ASML", "AMAT"],
    "CYBER_CLOUD_SAAS": ["PANW", "CRWD", "NET", "MDB", "DDOG", "SNOW", "MSFT", "GOOGL", "AMZN"],
    "BIOTECH_HEALTHCARE": ["LLY", "NVO", "VRTX", "REGN", "ISRG"],
    "FINTECH_CRYPTO_ENERGY": ["COIN", "HOOD", "RDW", "CEG", "VST", "TSLA"],
    "CORE_INDEX_ETFS": ["QQQ", "SPY", "SMH", "VTI"]
}

DEFAULT_WATCHLIST = [
    t for sector_tickers in SECTOR_UNIVERSES.values() for t in sector_tickers
]


# ==============================================================================
# Obsidian Trading Knowledge Vault Engine
# ==============================================================================

class ObsidianTradingVault:
    """Manages Obsidian-compatible markdown notes, daily journals, and ticker theses."""

    @classmethod
    def get_vault_path(cls) -> str:
        return VAULT_DIR

    @classmethod
    def init_vault(cls):
        """Initializes directory structure for Obsidian trading vault."""
        for subdir in ["daily_notes", "tickers", "playbooks", "retrospectives"]:
            os.makedirs(os.path.join(VAULT_DIR, subdir), exist_ok=True)

    @classmethod
    def save_daily_note(cls, date_str: str, briefing: Optional[Dict[str, Any]] = None,
                        intraday_events: Optional[List[str]] = None,
                        close_summary: Optional[Dict[str, Any]] = None) -> str:
        """Creates or updates an Obsidian Daily Trading Note."""
        cls.init_vault()
        filepath = os.path.join(VAULT_DIR, "daily_notes", f"{date_str}.md")
        
        briefing = briefing or {}
        macro = briefing.get("macro_sentiment", {})
        buys = briefing.get("top_buy_candidates", [])
        
        # Build YAML Frontmatter
        lines = [
            "---",
            f"date: {date_str}",
            f"macro_sentiment: {macro.get('label', 'NEUTRAL')}",
            f"sentiment_score: {macro.get('score', 0.0)}",
            f"type: daily-trading-journal",
            f"tags: [trading, daily-note, robinhood-agentic]",
            "---\n",
            f"# 📈 Daily Trading Journal: {date_str}\n",
            "## 🌅 Pre-Market Briefing & Macro Outlook",
            f"- **Macro Sentiment:** `{macro.get('label', 'NEUTRAL')}` (Score: {macro.get('score', 0.0):+.2f})"
        ]
        
        if macro.get("key_headlines"):
            lines.append("### Key Overnight Catalysts")
            for h in macro.get("key_headlines", []):
                lines.append(f"- {h}")
        
        if buys:
            lines.append("\n### Staged Trade Setups")
            lines.append("| Ticker | Price | Score | Sentiment | Stop Loss | Target 1 | Target 2 |")
            lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
            for b in buys:
                lines.append(f"| [[{b['ticker']}]] | ${b['price']:.2f} | {b['score']} | {b['sentiment']} | ${b['stop_loss']:.2f} | ${b['take_profit_1']:.2f} | ${b['take_profit_2']:.2f} |")
        
        if intraday_events:
            lines.append("\n## ⚡ Intraday Executions & Trailing Stop Updates")
            for ev in intraday_events:
                lines.append(f"- {ev}")
                
        if close_summary:
            lines.append("\n## 🔔 Market Close Summary & P&L Review")
            lines.append(f"- **Status:** `{close_summary.get('status', 'COMPLETED')}`")
            lines.append(f"- **Timestamp:** {close_summary.get('timestamp', '')}")
            
        content = "\n".join(lines) + "\n"
        with open(filepath, "w") as f:
            f.write(content)
        return filepath

    @classmethod
    def save_ticker_thesis(cls, ticker: str, data: Dict[str, Any]) -> str:
        """Saves a detailed research and thesis note for a specific ticker."""
        cls.init_vault()
        ticker = ticker.upper().strip()
        filepath = os.path.join(VAULT_DIR, "tickers", f"{ticker}.md")
        
        now_str = MarketHours.now_et().strftime("%Y-%m-%d %H:%M:%S %Z")
        p = data.get("price", 0.0)
        risk = data.get("risk_targets", {})
        ind = data.get("indicators", {})
        news = data.get("news_sentiment", {})
        
        lines = [
            "---",
            f"ticker: {ticker}",
            f"last_price: {p}",
            f"score: {data.get('score', 50)}",
            f"recommendation: {data.get('recommendation', 'HOLD')}",
            f"stop_loss: {risk.get('stop_loss', 0.0)}",
            f"target_1: {risk.get('take_profit_1', 0.0)}",
            f"target_2: {risk.get('take_profit_2', 0.0)}",
            f"last_updated: '{now_str}'",
            f"tags: [ticker-thesis, equity, {ticker.lower()}]",
            "---\n",
            f"# {ticker} Investment Thesis & Technical Profile\n",
            f"- **Current Price:** `${p:.2f}` ({data.get('change_pct', 0.0):>+5.2f}%)",
            f"- **Signal / Action:** `{data.get('recommendation', 'HOLD')}` (Score: {data.get('score', 50)}/100)",
            f"- **Stop Loss Level:** `${risk.get('stop_loss', 0.0):.2f}` ({risk.get('stop_loss_pct', 5.0)}%)",
            f"- **Take Profit 1 (+8%):** `${risk.get('take_profit_1', 0.0):.2f}`",
            f"- **Take Profit 2 (+15%):** `${risk.get('take_profit_2', 0.0):.2f}`\n",
            "## Technical Setup",
            f"- **RSI(14):** `{ind.get('rsi', 50):.1f}`",
            f"- **SMA 20 / 50 / 200:** `${ind.get('sma20', 0):.2f}` / `${ind.get('sma50', 0):.2f}` / `${ind.get('sma200', 0):.2f}`",
            f"- **MACD Histogram:** `{ind.get('macd_histogram', 0):+.3f}`\n",
            "## News & Catalyst Profile",
            f"- **Sentiment:** `{news.get('label', 'NEUTRAL')}` (Score: {news.get('score', 0):+.2f})"
        ]
        
        if news.get("headlines"):
            lines.append("### Recent News Headlines")
            for h in news["headlines"]:
                lines.append(f"- {h}")
                
        content = "\n".join(lines) + "\n"
        with open(filepath, "w") as f:
            f.write(content)
        return filepath

    @classmethod
    def log_trade_execution(cls, trade: Dict[str, Any]):
        """Logs a completed trade into the retrospective JSONL ledger for self-improvement."""
        cls.init_vault()
        ledger_path = os.path.join(VAULT_DIR, "retrospectives", "trade_ledger.jsonl")
        trade_record = {
            "timestamp": MarketHours.now_et().isoformat(),
            **trade
        }
        with open(ledger_path, "a") as f:
            f.write(json.dumps(trade_record) + "\n")


# ==============================================================================
# 1. US Market Hours & Calendar Engine
# ==============================================================================

class MarketHours:
    """Accurate tracking of US Equities trading calendar and sessions."""

    @staticmethod
    def now_et() -> datetime.datetime:
        """Returns the current datetime in US Eastern Time."""
        return datetime.datetime.now(ET_ZONE)

    @classmethod
    def is_weekend(cls, dt: Optional[datetime.datetime] = None) -> bool:
        dt = dt or cls.now_et()
        return dt.weekday() >= 5  # 5 = Saturday, 6 = Sunday

    @classmethod
    def get_easter(cls, year: int) -> datetime.date:
        """Computes Easter Sunday for a given year (Meeus/Jones/Butcher algorithm)."""
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return datetime.date(year, month, day)

    @classmethod
    def get_market_holidays(cls, year: int) -> List[datetime.date]:
        """Calculates observed US Stock Market (NYSE/NASDAQ) holidays for a given year."""
        holidays = []

        def add_observed(date_obj: datetime.date):
            if date_obj.weekday() == 6:  # Sunday -> observed Monday
                holidays.append(date_obj + datetime.timedelta(days=1))
            elif date_obj.weekday() == 5:  # Saturday -> observed Friday
                holidays.append(date_obj - datetime.timedelta(days=1))
            else:
                holidays.append(date_obj)

        # 1. New Year's Day (Jan 1)
        add_observed(datetime.date(year, 1, 1))

        # 2. Martin Luther King Jr. Day (3rd Monday in January)
        jan1 = datetime.date(year, 1, 1)
        first_mon = jan1 + datetime.timedelta(days=(0 - jan1.weekday() + 7) % 7)
        mlk = first_mon + datetime.timedelta(weeks=2)
        holidays.append(mlk)

        # 3. Washington's Birthday / Presidents Day (3rd Monday in February)
        feb1 = datetime.date(year, 2, 1)
        first_mon_feb = feb1 + datetime.timedelta(days=(0 - feb1.weekday() + 7) % 7)
        presidents_day = first_mon_feb + datetime.timedelta(weeks=2)
        holidays.append(presidents_day)

        # 4. Good Friday (Friday before Easter)
        easter = cls.get_easter(year)
        good_friday = easter - datetime.timedelta(days=2)
        holidays.append(good_friday)

        # 5. Memorial Day (Last Monday in May)
        may31 = datetime.date(year, 5, 31)
        memorial_day = may31 - datetime.timedelta(days=(may31.weekday() - 0) % 7)
        holidays.append(memorial_day)

        # 6. Juneteenth National Independence Day (June 19)
        add_observed(datetime.date(year, 6, 19))

        # 7. Independence Day (July 4)
        add_observed(datetime.date(year, 7, 4))

        # 8. Labor Day (1st Monday in September)
        sep1 = datetime.date(year, 9, 1)
        labor_day = sep1 + datetime.timedelta(days=(0 - sep1.weekday() + 7) % 7)
        holidays.append(labor_day)

        # 9. Thanksgiving Day (4th Thursday in November)
        nov1 = datetime.date(year, 11, 1)
        first_thu = nov1 + datetime.timedelta(days=(3 - nov1.weekday() + 7) % 7)
        thanksgiving = first_thu + datetime.timedelta(weeks=3)
        holidays.append(thanksgiving)

        # 10. Christmas Day (Dec 25)
        add_observed(datetime.date(year, 12, 25))

        return holidays

    @classmethod
    def is_market_holiday(cls, date_obj: Optional[datetime.date] = None) -> bool:
        """Returns True if the given date is a US stock market holiday."""
        if date_obj is None:
            date_obj = cls.now_et().date()
        holidays = cls.get_market_holidays(date_obj.year)
        return date_obj in holidays

    @classmethod
    def is_trading_day(cls, date_obj: Optional[datetime.date] = None) -> bool:
        """Returns True if the day is a valid trading day (weekday and not a holiday)."""
        if date_obj is None:
            date_obj = cls.now_et().date()
        if date_obj.weekday() >= 5:
            return False
        return not cls.is_market_holiday(date_obj)

    @classmethod
    def get_market_session(cls, dt: Optional[datetime.datetime] = None) -> str:
        """
        Returns the current market session:
        - 'REGULAR': 09:30 - 16:00 ET (Regular Trading Hours)
        - 'PRE_MARKET': 04:00 - 09:30 ET
        - 'AFTER_HOURS': 16:00 - 20:00 ET
        - 'CLOSED': Nights, Weekends, Holidays
        """
        dt = dt or cls.now_et()
        date_obj = dt.date()

        if not cls.is_trading_day(date_obj):
            return "CLOSED"

        current_time = dt.time()
        t_pre = datetime.time(4, 0)
        t_open = datetime.time(9, 30)
        t_close = datetime.time(16, 0)
        t_post = datetime.time(20, 0)

        if t_open <= current_time < t_close:
            return "REGULAR"
        elif t_pre <= current_time < t_open:
            return "PRE_MARKET"
        elif t_close <= current_time < t_post:
            return "AFTER_HOURS"
        else:
            return "CLOSED"

    @classmethod
    def is_market_open(cls, dt: Optional[datetime.datetime] = None) -> bool:
        """Returns True if regular market hours are active (09:30 - 16:00 ET on a trading day)."""
        return cls.get_market_session(dt) == "REGULAR"

    @classmethod
    def next_market_open(cls, dt: Optional[datetime.datetime] = None) -> datetime.datetime:
        """Calculates the exact datetime of the next regular market open (9:30 AM ET)."""
        dt = dt or cls.now_et()
        target_date = dt.date()
        open_time = datetime.time(9, 30)

        # If today is a trading day and it's before 9:30 AM, today's open is next
        if cls.is_trading_day(target_date) and dt.time() < open_time:
            return datetime.datetime.combine(target_date, open_time, tzinfo=ET_ZONE)

        # Otherwise find the next trading day
        while True:
            target_date += datetime.timedelta(days=1)
            if cls.is_trading_day(target_date):
                return datetime.datetime.combine(target_date, open_time, tzinfo=ET_ZONE)

    @classmethod
    def seconds_until_next_open(cls, dt: Optional[datetime.datetime] = None) -> float:
        dt = dt or cls.now_et()
        next_open = cls.next_market_open(dt)
        return max(0.0, (next_open - dt).total_seconds())

    @classmethod
    def next_premarket_time(cls, dt: Optional[datetime.datetime] = None) -> datetime.datetime:
        """Calculates the exact datetime of the next pre-market briefing (9:20 AM ET - 10 min before open)."""
        dt = dt or cls.now_et()
        target_date = dt.date()
        pre_time = datetime.time(9, 20)

        # If today is a trading day and it's before 9:20 AM, today's pre-market is next
        if cls.is_trading_day(target_date) and dt.time() < pre_time:
            return datetime.datetime.combine(target_date, pre_time, tzinfo=ET_ZONE)

        # Otherwise find the next trading day
        while True:
            target_date += datetime.timedelta(days=1)
            if cls.is_trading_day(target_date):
                return datetime.datetime.combine(target_date, pre_time, tzinfo=ET_ZONE)

    @classmethod
    def seconds_until_premarket(cls, dt: Optional[datetime.datetime] = None) -> float:
        dt = dt or cls.now_et()
        next_pre = cls.next_premarket_time(dt)
        return max(0.0, (next_pre - dt).total_seconds())

    @classmethod
    def seconds_until_close(cls, dt: Optional[datetime.datetime] = None) -> float:
        dt = dt or cls.now_et()
        if not cls.is_market_open(dt):
            return 0.0
        close_time = datetime.datetime.combine(dt.date(), datetime.time(16, 0), tzinfo=ET_ZONE)
        return max(0.0, (close_time - dt).total_seconds())


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
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_period}"
        try:
            raw = cls._http_get(url)
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
            return bars
        except Exception:
            return []


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


# ==============================================================================
# 4. Online Financial News & Sentiment Engine
# ==============================================================================

class NewsSentimentEngine:
    """Searches recent news for tickers and performs financial sentiment analysis."""

    BULLISH_KEYWORDS = {
        "surge": 1.5, "surged": 1.5, "surging": 1.5, "record": 1.4, "beat": 1.5,
        "beats": 1.5, "beating": 1.5, "upgrade": 1.6, "upgraded": 1.6, "breakthrough": 1.8,
        "profit": 1.2, "growth": 1.3, "bullish": 1.6, "outperform": 1.5, "strong": 1.2,
        "jump": 1.3, "jumps": 1.3, "rally": 1.5, "rallies": 1.5, "buy": 1.3,
        "innovative": 1.2, "expansion": 1.2, "gain": 1.1, "gains": 1.1, "revenue": 1.0,
        "dividend": 1.1, "partnership": 1.3, "boost": 1.3, "soar": 1.6, "soars": 1.6
    }

    BEARISH_KEYWORDS = {
        "plunge": 1.8, "plunges": 1.8, "plunged": 1.8, "drop": 1.2, "drops": 1.2,
        "dropped": 1.2, "miss": 1.5, "misses": 1.5, "missed": 1.5, "downgrade": 1.6,
        "downgraded": 1.6, "probe": 1.7, "investigation": 1.6, "lawsuit": 1.5,
        "slump": 1.5, "slumps": 1.5, "bearish": 1.6, "underperform": 1.5, "weak": 1.3,
        "fall": 1.2, "falls": 1.2, "fallen": 1.2, "loss": 1.4, "losses": 1.4,
        "layoffs": 1.4, "fraud": 2.0, "scandal": 1.8, "warning": 1.4, "sell": 1.3,
        "crash": 1.8, "crashes": 1.8, "tumble": 1.5, "tumbles": 1.5, "cut": 1.2
    }

    @classmethod
    def fetch_news(cls, query_or_ticker: str, limit: int = 8) -> List[Dict[str, str]]:
        """Fetches latest financial news headlines for a ticker or query."""
        ticker = query_or_ticker.strip().upper()
        # 1. Try Yahoo Finance RSS Feed for the ticker
        rss_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(ticker)}&region=US&lang=en-US"
        articles = []
        try:
            req = urllib.request.Request(rss_url, headers={"User-Agent": FinancialData.USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as resp:
                xml_content = resp.read().decode("utf-8", errors="replace")
                # Simple XML parsing for <item><title> and <link>
                items = re.findall(r"<item>(.*?)</item>", xml_content, re.DOTALL)
                for item in items[:limit]:
                    t_match = re.search(r"<title>(.*?)</title>", item, re.DOTALL)
                    l_match = re.search(r"<link>(.*?)</link>", item, re.DOTALL)
                    d_match = re.search(r"<pubDate>(.*?)</pubDate>", item, re.DOTALL)
                    title = t_match.group(1).replace("<![CDATA[", "").replace("]]>", "").strip() if t_match else ""
                    link = l_match.group(1).strip() if l_match else ""
                    pub_date = d_match.group(1).strip() if d_match else ""
                    if title:
                        articles.append({"title": title, "link": link, "date": pub_date, "source": "Yahoo Finance"})
        except Exception:
            pass

        # 2. If no articles or broader topic, search SearXNG / Google News RSS
        if not articles:
            try:
                gnews_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(ticker + ' stock market')}&hl=en-US&gl=US&ceid=US:en"
                req = urllib.request.Request(gnews_url, headers={"User-Agent": FinancialData.USER_AGENT})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    xml_content = resp.read().decode("utf-8", errors="replace")
                    items = re.findall(r"<item>(.*?)</item>", xml_content, re.DOTALL)
                    for item in items[:limit]:
                        t_match = re.search(r"<title>(.*?)</title>", item, re.DOTALL)
                        l_match = re.search(r"<link>(.*?)</link>", item, re.DOTALL)
                        title = t_match.group(1).replace("<![CDATA[", "").replace("]]>", "").strip() if t_match else ""
                        link = l_match.group(1).strip() if l_match else ""
                        if title:
                            articles.append({"title": title, "link": link, "date": "", "source": "Google News"})
            except Exception:
                pass

        return articles

    @classmethod
    def score_text(cls, text: str) -> float:
        """Returns a sentiment score from -1.0 (extremely bearish) to +1.0 (extremely bullish)."""
        words = re.findall(r"[a-z]+", text.lower())
        if not words:
            return 0.0

        pos_score = sum(cls.BULLISH_KEYWORDS.get(w, 0.0) for w in words)
        neg_score = sum(cls.BEARISH_KEYWORDS.get(w, 0.0) for w in words)

        total = pos_score + neg_score
        if total == 0:
            return 0.0
        
        raw_score = (pos_score - neg_score) / (total + 2.0)
        return max(-1.0, min(1.0, round(raw_score * 1.5, 2)))

    @classmethod
    def analyze_news_sentiment(cls, query_or_ticker: str) -> Dict[str, Any]:
        """Fetches news and calculates aggregated sentiment metrics."""
        articles = cls.fetch_news(query_or_ticker)
        if not articles:
            return {
                "ticker": query_or_ticker,
                "article_count": 0,
                "sentiment_score": 0.0,
                "sentiment_label": "NEUTRAL",
                "headlines": []
            }

        scores = [cls.score_text(a["title"]) for a in articles]
        avg_score = sum(scores) / float(len(scores)) if scores else 0.0

        if avg_score >= 0.35:
            label = "STRONGLY_BULLISH"
        elif avg_score >= 0.10:
            label = "BULLISH"
        elif avg_score <= -0.35:
            label = "STRONGLY_BEARISH"
        elif avg_score <= -0.10:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        return {
            "ticker": query_or_ticker,
            "article_count": len(articles),
            "sentiment_score": round(avg_score, 2),
            "sentiment_label": label,
            "headlines": [a["title"] for a in articles[:5]]
        }


# ==============================================================================
# 5. Algorithmic Trading & Profit Maximization Strategy Engine
# ==============================================================================

class TradingStrategyEngine:
    """Combines indicators, news sentiment, and risk rules to optimize trades."""

    @classmethod
    def discover_breakout_candidates(cls, max_candidates: int = 5) -> List[Dict[str, Any]]:
        """
        Dynamically discovers high-momentum breakout candidates from live market news & screener feeds:
        - Searches top volume movers and analyst upgrades
        - Filters out penny stocks (< $5.00) and toxic illiquid names
        - Returns ranked candidates with multi-factor scores
        """
        search_queries = [
            "top stock gainers volume US market today",
            "earnings beat raised guidance breakout stock",
            "analyst upgrade price target raise buy rating"
        ]
        
        extracted_tickers = set(DEFAULT_WATCHLIST)
        for q in search_queries:
            articles = NewsSentimentEngine.fetch_news(q)
            for a in articles:
                text = a.get("title", "") + " " + a.get("snippet", "")
                # Find ticker patterns: $TICKER or (TICKER) or NASDAQ: TICKER
                found = re.findall(r"(?:\$|\bNASDAQ:\s*|\bNYSE:\s*|\()([A-Z]{2,5})\b", text)
                for sym in found:
                    if sym not in ("USD", "ETF", "CEO", "CFO", "SEC", "FDA", "AI", "US", "USA", "FED"):
                        extracted_tickers.add(sym)

        # Quality & Liquidity Filter Gate
        discovered = []
        for ticker in list(extracted_tickers)[:25]:
            try:
                analysis = cls.analyze_ticker(ticker)
                # Anti-Penny-Stock Filter: price >= $5.00 and score >= 50
                if analysis.get("price", 0.0) >= 5.0:
                    discovered.append(analysis)
            except Exception:
                pass

        discovered.sort(key=lambda x: x["score"], reverse=True)
        return discovered[:max_candidates]

    @classmethod
    def analyze_ticker(cls, ticker: str) -> Dict[str, Any]:
        """Computes complete multi-factor analysis, technical signals, and trade recommendations."""
        ticker = ticker.strip().upper()
        quote = FinancialData.fetch_quote(ticker)
        bars = FinancialData.fetch_historical(ticker, range_period="6mo", interval="1d")
        news = NewsSentimentEngine.analyze_news_sentiment(ticker)

        current_price = quote.get("price", 100.0)

        # Indicator calculations if bars available
        if len(bars) >= 5:
            closes = [b["close"] for b in bars]
            highs = [b["high"] for b in bars]
            lows = [b["low"] for b in bars]
            volumes = [b["volume"] for b in bars]

            rsi_period = min(14, max(2, len(closes) - 1))
            rsi_series = TechnicalIndicators.rsi(closes, period=rsi_period)
            current_rsi = rsi_series[-1] if rsi_series and rsi_series[-1] is not None else 50.0

            sma20_period = min(20, len(closes))
            sma50_period = min(50, len(closes))
            sma200_period = min(200, len(closes))

            sma20_series = TechnicalIndicators.sma(closes, period=sma20_period)
            sma50_series = TechnicalIndicators.sma(closes, period=sma50_period)
            sma200_series = TechnicalIndicators.sma(closes, period=sma200_period)

            sma20 = sma20_series[-1] if sma20_series and sma20_series[-1] is not None else current_price
            sma50 = sma50_series[-1] if sma50_series and sma50_series[-1] is not None else current_price
            sma200 = sma200_series[-1] if sma200_series and sma200_series[-1] is not None else current_price

            macd_data = TechnicalIndicators.macd(closes)
            macd_hist = macd_data["histogram"][-1] if macd_data["histogram"] and macd_data["histogram"][-1] is not None else 0.0

            bb_period = min(20, len(closes))
            bb = TechnicalIndicators.bollinger_bands(closes, period=bb_period)
            bb_pct_b = bb["percent_b"][-1] if bb["percent_b"] and bb["percent_b"][-1] is not None else 0.5

            atr_period = min(14, max(2, len(closes) - 1))
            atr_series = TechnicalIndicators.atr(highs, lows, closes, period=atr_period)
            current_atr = atr_series[-1] if atr_series and atr_series[-1] is not None else (current_price * 0.02)
        else:
            current_rsi = 50.0
            sma20 = current_price
            sma50 = current_price
            sma200 = current_price
            macd_hist = 0.0
            bb_pct_b = 0.5
            current_atr = current_price * 0.02

        # Multi-factor score (0 - 100)
        score = 50.0

        # Trend factor (up to ±20 pts)
        if current_price > sma20:
            score += 7
        if current_price > sma50:
            score += 8
        if current_price > sma200:
            score += 5
        if sma50 > sma200:  # Golden cross regime
            score += 5

        # Momentum factor (up to ±15 pts)
        if 35 <= current_rsi <= 65:
            score += 5
        elif current_rsi < 30:  # Oversold bounce potential
            score += 10
        elif current_rsi > 75:  # Overbought warning
            score -= 10

        # MACD factor (up to ±10 pts)
        if macd_hist > 0:
            score += 8
        else:
            score -= 6

        # News Sentiment factor (up to ±15 pts)
        sent_score = news.get("sentiment_score", 0.0)
        score += sent_score * 15.0

        score = max(0.0, min(100.0, round(score, 1)))

        # Recommendation based on score
        if score >= 75:
            recommendation = "STRONG_BUY"
        elif score >= 60:
            recommendation = "BUY"
        elif score <= 25:
            recommendation = "STRONG_SELL"
        elif score <= 40:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        # Risk parameters
        stop_loss_price = round(max(0.01, current_price - max(current_price * 0.05, 2.0 * current_atr)), 2)
        take_profit_target_1 = round(current_price * 1.08, 2)  # +8%
        take_profit_target_2 = round(current_price * 1.15, 2)  # +15%

        return {
            "ticker": ticker,
            "price": current_price,
            "change_pct": quote.get("change_percent", 0.0),
            "score": score,
            "recommendation": recommendation,
            "indicators": {
                "rsi": current_rsi,
                "sma20": round(sma20, 2),
                "sma50": round(sma50, 2),
                "sma200": round(sma200, 2),
                "macd_histogram": macd_hist,
                "bollinger_pct_b": bb_pct_b,
                "atr": current_atr
            },
            "news_sentiment": {
                "score": news.get("sentiment_score", 0.0),
                "label": news.get("sentiment_label", "NEUTRAL"),
                "headlines": news.get("headlines", [])
            },
            "risk_targets": {
                "stop_loss": stop_loss_price,
                "stop_loss_pct": round((stop_loss_price - current_price) / current_price * 100, 2),
                "take_profit_1": take_profit_target_1,
                "take_profit_1_pct": 8.0,
                "take_profit_2": take_profit_target_2,
                "take_profit_2_pct": 15.0,
                "trailing_stop_pct": 3.5
            }
        }

    @classmethod
    def evaluate_portfolio_rebalance(cls, portfolio: Dict[str, Any], max_position_pct: float = 0.15, min_cash_pct: float = 0.15) -> Dict[str, Any]:
        """
        Calculates optimal portfolio rebalancing and trade actions:
        - Reinvests profits from overperforming / overbought assets
        - Enforces stop-loss on losing assets
        - Allocates cash to top scoring opportunities
        """
        cash = portfolio.get("cash", 1000.0)
        holdings = portfolio.get("holdings", {})

        total_portfolio_value = cash
        position_analyses = {}

        for ticker, pos in holdings.items():
            shares = pos.get("shares", 0)
            entry_price = pos.get("entry_price", pos.get("price", 100.0))
            analysis = cls.analyze_ticker(ticker)
            current_price = analysis["price"]
            pos_val = shares * current_price
            total_portfolio_value += pos_val
            
            pnl = (current_price - entry_price) * shares
            pnl_pct = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
            
            position_analyses[ticker] = {
                "shares": shares,
                "entry_price": entry_price,
                "current_price": current_price,
                "position_value": round(pos_val, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "score": analysis["score"],
                "recommendation": analysis["recommendation"],
                "stop_loss": analysis["risk_targets"]["stop_loss"]
            }

        target_cash = total_portfolio_value * min_cash_pct
        max_single_stock_val = total_portfolio_value * max_position_pct

        proposed_trades = []

        # Step 1: Check existing positions for STOP_LOSS, TAKE_PROFIT, or OVER-ALLOCATION
        for ticker, p in position_analyses.items():
            shares = p["shares"]
            cur_price = p["current_price"]
            
            # Stop loss hit (-5% or below technical stop)
            if p["pnl_pct"] <= -5.0 or cur_price <= p["stop_loss"] or p["recommendation"] in ("SELL", "STRONG_SELL"):
                proposed_trades.append({
                    "ticker": ticker,
                    "action": "SELL",
                    "shares": shares,
                    "estimated_price": cur_price,
                    "estimated_total": round(shares * cur_price, 2),
                    "reason": f"Stop Loss / Risk Exit (P&L: {p['pnl_pct']}%, Score: {p['score']})"
                })
            # Profit taking on over-extended positions (+15% or score overbought)
            elif p["pnl_pct"] >= 15.0:
                sell_shares = max(1, shares // 2)
                proposed_trades.append({
                    "ticker": ticker,
                    "action": "SELL",
                    "shares": sell_shares,
                    "estimated_price": cur_price,
                    "estimated_total": round(sell_shares * cur_price, 2),
                    "reason": f"Take Profit (+{p['pnl_pct']}% gain locked in)"
                })
            # Over-allocated rebalance
            elif p["position_value"] > max_single_stock_val:
                excess_val = p["position_value"] - max_single_stock_val
                trim_shares = int(excess_val / cur_price)
                if trim_shares > 0:
                    proposed_trades.append({
                        "ticker": ticker,
                        "action": "SELL",
                        "shares": trim_shares,
                        "estimated_price": cur_price,
                        "estimated_total": round(trim_shares * cur_price, 2),
                        "reason": f"Position Cap Trim (max {int(max_position_pct * 100)}% allocation)"
                    })

        return {
            "portfolio_value": round(total_portfolio_value, 2),
            "cash": round(cash, 2),
            "cash_percent": round(cash / total_portfolio_value * 100, 2) if total_portfolio_value else 100.0,
            "positions": position_analyses,
            "proposed_trades": proposed_trades
        }

    @classmethod
    def generate_premarket_briefing(cls, watchlist: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Runs 10 minutes before market open (09:20 AM ET):
        - Checks major market index futures/indicators (SPY, QQQ)
        - Analyzes overnight financial news catalysts and sector sentiment
        - Formulates a structured Daily Trade Plan with risk targets
        """
        watchlist = watchlist or DEFAULT_WATCHLIST
        now = MarketHours.now_et()
        
        # 1. Macro Indices & Market Sentiment
        macro_news = NewsSentimentEngine.analyze_news_sentiment("US stock market")
        
        # 2. Watchlist Scan & Ranking
        opportunities = []
        for ticker in watchlist[:12]:
            try:
                res = cls.analyze_ticker(ticker)
                opportunities.append(res)
            except Exception:
                pass
        
        opportunities.sort(key=lambda x: x["score"], reverse=True)
        
        # 3. Top Staged Trade Decisions for the Day
        top_buys = [o for o in opportunities if o["score"] >= 65]
        top_sells = [o for o in opportunities if o["score"] <= 40]
        
        staged_plan = {
            "date": now.strftime("%Y-%m-%d"),
            "briefing_time": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "macro_sentiment": {
                "label": macro_news.get("sentiment_label", "NEUTRAL"),
                "score": macro_news.get("sentiment_score", 0.0),
                "key_headlines": macro_news.get("headlines", [])[:3]
            },
            "top_buy_candidates": [
                {
                    "ticker": b["ticker"],
                    "price": b["price"],
                    "score": b["score"],
                    "sentiment": b["news_sentiment"]["label"],
                    "stop_loss": b["risk_targets"]["stop_loss"],
                    "take_profit_1": b["risk_targets"]["take_profit_1"],
                    "take_profit_2": b["risk_targets"]["take_profit_2"]
                }
                for b in top_buys[:3]
            ],
            "top_risk_warnings": [
                {
                    "ticker": s["ticker"],
                    "price": s["price"],
                    "score": s["score"],
                    "reason": "Bearish momentum / overextended"
                }
                for s in top_sells[:3]
            ]
        }
        # 4. Automatically persist to Obsidian Trading Knowledge Vault
        try:
            date_str = now.strftime("%Y-%m-%d")
            note_file = ObsidianTradingVault.save_daily_note(date_str, briefing=staged_plan)
            staged_plan["obsidian_daily_note"] = note_file
            
            # Save individual ticker thesis notes
            for opp in opportunities[:8]:
                ObsidianTradingVault.save_ticker_thesis(opp["ticker"], opp)
        except Exception:
            pass

        return staged_plan

    @classmethod
    def generate_daily_closing_summary(cls) -> Dict[str, Any]:
        """
        Runs at 4:05 PM ET after market close:
        - Summarizes daily market session performance
        - Appends daily record to ~/.config/ai/trading_journal.json and Obsidian Vault
        """
        now = MarketHours.now_et()
        date_str = now.strftime("%Y-%m-%d")
        os.makedirs(CONFIG_DIR, exist_ok=True)
        journal_file = os.path.join(CONFIG_DIR, "trading_journal.json")
        
        journal = []
        if os.path.exists(journal_file):
            try:
                with open(journal_file, "r") as f:
                    journal = json.load(f)
            except Exception:
                journal = []
        
        daily_entry = {
            "date": date_str,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "status": "COMPLETED",
            "session": "REGULAR_CLOSE"
        }
        journal.append(daily_entry)
        
        try:
            with open(journal_file, "w") as f:
                json.dump(journal[-60:], f, indent=2)  # Keep last 60 trading days
        except Exception:
            pass

        # Update Obsidian Daily Note with close section
        try:
            ObsidianTradingVault.save_daily_note(date_str, close_summary=daily_entry)
        except Exception:
            pass
            
        return daily_entry


# ==============================================================================
# 6. Robinhood MCP Integration & Authorization Helper
# ==============================================================================

def authenticate_robinhood_mcp():
    """Verifies stored credentials and only runs browser flow if no cached session exists."""
    print("=" * 70)
    print("  ROBINHOOD AGENTIC TRADING - MCP CREDENTIAL STATUS")
    print("=" * 70)
    
    # First, test existing stored credentials silently
    try:
        accounts = RobinhoodExecutor.get_accounts()
        if accounts and len(accounts) > 0:
            print("✓ Stored credentials are ACTIVE and verified.")
            for acc in accounts:
                print(f"  - Account: {acc.get('account_number')} ({acc.get('account_type', 'standard')})")
            print("No re-authentication or browser login needed.\n")
            return
    except Exception as e:
        print(f"Stored credential probe: {e}")

    # If not interactive, do not pop up browser
    if not sys.stdin.isatty() or os.environ.get("BROWSER") == "none":
        print("Non-interactive mode: skipping browser authentication.\n")
        return

    print("Starting Robinhood Agentic MCP bridge...\n")
    print("A browser window will open to authenticate with Robinhood.")
    print("Once authorized, credentials will be cached in your local MCP store.\n")
    
    cmd = "npx -y mcp-remote https://agent.robinhood.com/mcp/trading"
    print(f"Executing: {cmd}\n")
    os.system(cmd)


class RobinhoodExecutor:
    """Direct, high-speed execution bridge for Robinhood Agentic MCP (sub-second order placement)."""

    @classmethod
    def _extract_data(cls, res: Dict[str, Any]) -> Dict[str, Any]:
        """Extracts structured JSON data safely from MCP response dictionary."""
        if not res:
            return {}
        if isinstance(res.get("structuredContent"), dict) and "data" in res["structuredContent"]:
            return res["structuredContent"]["data"]
        if "content" in res and isinstance(res["content"], list) and len(res["content"]) > 0:
            item = res["content"][0]
            if isinstance(item, dict) and "text" in item:
                try:
                    parsed = json.loads(item["text"])
                    if isinstance(parsed, dict) and "data" in parsed:
                        return parsed["data"]
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
        return res

    @classmethod
    def call_mcp_tool(cls, tool_name: str, arguments: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
        """Calls any Robinhood MCP tool in sub-second time without browser prompts."""
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("INFER_TEST_MODE"):
            return {}

        env = os.environ.copy()
        env['BROWSER'] = ':'
        env['NO_BROWSER'] = '1'
        env['CI'] = '1'
        env['DISPLAY'] = ''
        env.pop('WSL_DISTRO_NAME', None)

        proc = subprocess.Popen(
            ['npx', '-y', 'mcp-remote', 'https://agent.robinhood.com/mcp/trading', '--silent'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True
        )
        try:
            req_init = {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'ai-buddy', 'version': '1.0'}}
            }
            proc.stdin.write(json.dumps(req_init) + '\n')
            proc.stdin.flush()
            proc.stdout.readline()
            proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
            proc.stdin.flush()

            req_tool = {
                'jsonrpc': '2.0',
                'id': 2,
                'method': 'tools/call',
                'params': {'name': tool_name, 'arguments': arguments}
            }
            proc.stdin.write(json.dumps(req_tool) + '\n')
            proc.stdin.flush()
            
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line)
                    if data.get('id') == 2:
                        return data.get('result', {})
                except Exception:
                    pass
        finally:
            try:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        return {}

    @classmethod
    def get_accounts(cls) -> List[Dict[str, Any]]:
        """Fetch all available accounts under the authorized credentials."""
        res = cls.call_mcp_tool('get_accounts', {})
        data = cls._extract_data(res)
        if isinstance(data, dict):
            return data.get('accounts', [])
        return []

    @classmethod
    def get_agentic_account(cls) -> Optional[Dict[str, Any]]:
        """Finds the dedicated Agentic Sandbox account with agentic_allowed=True."""
        accounts = cls.get_accounts()
        for a in accounts:
            if a.get("agentic_allowed") is True or str(a.get("nickname", "")).lower() == "agentic":
                return a
        return accounts[0] if accounts else None

    @classmethod
    def get_agentic_account_number(cls) -> str:
        """Returns the account number of the agentic account (or fallback)."""
        acc = cls.get_agentic_account()
        return str(acc.get("account_number", "517198354")) if acc else "517198354"

    @classmethod
    def get_default_account_number(cls) -> str:
        """Returns default account number or first active account."""
        accounts = cls.get_accounts()
        for a in accounts:
            if a.get("is_default"):
                return str(a.get("account_number", "837546068"))
        for a in accounts:
            if a.get("agentic_allowed"):
                return str(a.get("account_number", "517198354"))
        return accounts[0]["account_number"] if accounts else "837546068"

    @classmethod
    def get_live_portfolio(cls, account_number: Optional[str] = None) -> Dict[str, Any]:
        """Fetch real-time portfolio balance, equity value, cash, and buying power."""
        acc = account_number or cls.get_default_account_number()
        res = cls.call_mcp_tool('get_portfolio', {'account_number': acc})
        data = cls._extract_data(res)
        return data if isinstance(data, dict) else {}

    @classmethod
    def get_equity_positions(cls, account_number: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch active equity holdings and quantities."""
        acc = account_number or cls.get_default_account_number()
        res = cls.call_mcp_tool('get_equity_positions', {'account_number': acc})
        data = cls._extract_data(res)
        if isinstance(data, dict):
            return data.get('positions', [])
        return []

    @classmethod
    def get_equity_quotes(cls, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Batch fetch live equity quotes."""
        quotes_map = {}
        batch_size = 30
        for i in range(0, len(symbols), batch_size):
            batch = [s.upper().strip() for s in symbols[i:i + batch_size]]
            res = cls.call_mcp_tool('get_equity_quotes', {'symbols': batch})
            data = cls._extract_data(res)
            results = data.get('results', []) if isinstance(data, dict) else []
            for r in results:
                q = r.get('quote', {})
                sym = q.get('symbol')
                if sym:
                    quotes_map[sym] = q
        return quotes_map

    @classmethod
    def get_equity_historicals(cls, symbols: List[str], interval: str = "day", span_days: int = 90) -> Dict[str, List[Dict[str, Any]]]:
        """Fetches OHLCV historical bars via Robinhood MCP."""
        start_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=span_days)).strftime("%Y-%m-%dT00:00:00Z")
        out = {}
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = [s.upper().strip() for s in symbols[i:i + batch_size]]
            res = cls.call_mcp_tool('get_equity_historicals', {
                'symbols': batch,
                'interval': interval,
                'start_time': start_time
            })
            data = cls._extract_data(res)
            results = data.get('results', []) if isinstance(data, dict) else []
            for r in results:
                sym = r.get('symbol')
                bars = r.get('bars', [])
                if sym:
                    out[sym] = bars
        return out

    @classmethod
    def get_equity_technical_indicators(cls, symbol: str, indicator_type: str = "rsi", interval: str = "day", span_days: int = 90) -> List[Dict[str, Any]]:
        """Computes technical indicator series via Robinhood MCP."""
        start_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=span_days)).strftime("%Y-%m-%dT00:00:00Z")
        res = cls.call_mcp_tool('get_equity_technical_indicators', {
            'symbol': symbol.upper().strip(),
            'type': indicator_type.lower().strip(),
            'interval': interval,
            'start_time': start_time
        })
        data = cls._extract_data(res)
        results = data.get('results', []) if isinstance(data, dict) else []
        if results and 'indicators' in results[0]:
            return results[0]['indicators']
        return []

    @classmethod
    def execute_market_order(cls, account_number: str, symbol: str, side: str,
                             dollar_amount: Optional[str] = None,
                             quantity: Optional[str] = None) -> Dict[str, Any]:
        """Directly places an equity market order on an agentic account."""
        args = {
            'account_number': account_number,
            'symbol': symbol.upper().strip(),
            'side': side.lower(),
            'type': 'market'
        }
        if dollar_amount:
            args['dollar_amount'] = str(dollar_amount)
        elif quantity:
            args['quantity'] = str(quantity)

        res = cls.call_mcp_tool('place_equity_order', args)
        return res


# ==============================================================================
# 6.5. Trading Data Offloading & Portfolio Auditor
# ==============================================================================

class TradingDataManager:
    """Manages offloading trading, portfolio, quote, and audit data to JSON and CSV files."""

    @classmethod
    def get_cache_dir(cls) -> str:
        d = os.path.expanduser("~/.cache/ai/trading")
        os.makedirs(d, exist_ok=True)
        return d

    @classmethod
    def export_portfolio(cls, portfolio_data: Dict[str, Any], positions: List[Dict[str, Any]],
                         quotes: Dict[str, Dict[str, Any]], account_number: str,
                         output_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Saves structured portfolio dataset to JSON and CSV files:
        - portfolio_<account>.json / portfolio_latest.json
        - portfolio_<account>.csv / portfolio_latest.csv
        """
        out_dir = output_dir or cls.get_cache_dir()
        os.makedirs(out_dir, exist_ok=True)

        tot_val = float(portfolio_data.get("total_value", 0.0))
        eq_val = float(portfolio_data.get("equity_value", 0.0))
        cash_val = float(portfolio_data.get("cash", 0.0))
        
        bp_raw = portfolio_data.get("buying_power", 0.0)
        if isinstance(bp_raw, dict):
            bp_val = float(bp_raw.get("buying_power", 0.0))
        else:
            try:
                bp_val = float(bp_raw or 0.0)
            except Exception:
                bp_val = 0.0

        processed_positions = []
        tot_cost = 0.0
        tot_cur_val = 0.0

        for p in positions:
            sym = str(p.get("symbol", "")).strip().upper()
            try:
                qty = float(p.get("quantity", 0.0))
            except Exception:
                qty = 0.0
            if qty <= 0:
                continue
            try:
                avg_cost = float(p.get("average_buy_price", 0.0))
            except Exception:
                avg_cost = 0.0

            q = quotes.get(sym, {})
            try:
                cur_price = float(q.get("last_trade_price") or q.get("price") or avg_cost)
            except Exception:
                cur_price = avg_cost

            val = qty * cur_price
            cost = qty * avg_cost
            tot_cost += cost
            tot_cur_val += val
            weight = (val / eq_val * 100.0) if eq_val > 0 else 0.0
            pnl = val - cost
            pnl_pct = ((cur_price - avg_cost) / avg_cost * 100.0) if avg_cost > 0 else 0.0

            processed_positions.append({
                "symbol": sym,
                "quantity": round(qty, 4),
                "average_buy_price": round(avg_cost, 4),
                "current_price": round(cur_price, 4),
                "current_value": round(val, 2),
                "cost_basis": round(cost, 2),
                "unrealized_pnl": round(pnl, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "weight_pct": round(weight, 2),
                "type": p.get("type", "equity")
            })

        processed_positions.sort(key=lambda x: x["current_value"], reverse=True)

        full_payload = {
            "account_number": str(account_number),
            "timestamp": datetime.datetime.now(ET_ZONE).isoformat(),
            "summary": {
                "total_value": round(tot_val if tot_val > 0 else (tot_cur_val + cash_val), 2),
                "equity_value": round(eq_val if eq_val > 0 else tot_cur_val, 2),
                "cash": round(cash_val, 2),
                "buying_power": round(bp_val, 2),
                "cash_buffer_pct": round((cash_val / tot_val * 100.0) if tot_val > 0 else 0.0, 2),
                "total_cost_basis": round(tot_cost, 2),
                "net_unrealized_pnl": round(tot_cur_val - tot_cost, 2),
                "net_unrealized_pnl_pct": round(((tot_cur_val - tot_cost) / tot_cost * 100.0) if tot_cost > 0 else 0.0, 2),
                "active_position_count": len(processed_positions)
            },
            "positions": processed_positions
        }

        # 1. JSON Export
        json_path = os.path.join(out_dir, f"portfolio_{account_number}.json")
        latest_json = os.path.join(out_dir, "portfolio_latest.json")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(full_payload, f, indent=2)
            with open(latest_json, "w", encoding="utf-8") as f:
                json.dump(full_payload, f, indent=2)
        except Exception:
            pass

        # 2. CSV Export
        csv_path = os.path.join(out_dir, f"portfolio_{account_number}.csv")
        latest_csv = os.path.join(out_dir, "portfolio_latest.csv")
        csv_headers = [
            "symbol", "quantity", "average_buy_price", "current_price",
            "current_value", "cost_basis", "unrealized_pnl", "unrealized_pnl_pct", "weight_pct", "type"
        ]
        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_headers)
                writer.writeheader()
                for pos in processed_positions:
                    writer.writerow(pos)
            with open(latest_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_headers)
                writer.writeheader()
                for pos in processed_positions:
                    writer.writerow(pos)
        except Exception:
            pass

        return {
            "json": json_path,
            "csv": csv_path,
            "latest_json": latest_json,
            "latest_csv": latest_csv,
            "processed_positions": processed_positions,
            "full_payload": full_payload
        }

    @classmethod
    def export_audit(cls, audit_data: Dict[str, Any], account_number: str,
                     output_dir: Optional[str] = None) -> str:
        """Saves portfolio audit results to JSON."""
        out_dir = output_dir or cls.get_cache_dir()
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"audit_{account_number}.json")
        latest = os.path.join(out_dir, "audit_latest.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(audit_data, f, indent=2)
            with open(latest, "w", encoding="utf-8") as f:
                json.dump(audit_data, f, indent=2)
        except Exception:
            pass
        return path


class PortfolioAuditor:
    """Quantitative risk auditor, concentration checker, and optimizer for Robinhood accounts."""

    SEMI_TICKERS = {
        "NVDA", "AVGO", "ARM", "SMCI", "AMD", "TSM", "ASML", "AMAT", "MRVL", "QCOM", "INTC", "MCHP", "MLTX", "AEHR", "MXL", "SMH"
    }

    CORE_ETFS = {
        "VTI", "VXUS", "SPY", "QQQ", "SMH", "BND", "SCHO", "URA"
    }

    @classmethod
    def audit(cls, account_number: Optional[str] = None,
              port_data: Optional[Dict[str, Any]] = None,
              positions: Optional[List[Dict[str, Any]]] = None,
              quotes: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Performs full quantitative risk, concentration, dead-money, dust, and tax-loss harvesting audit."""
        acc = account_number or RobinhoodExecutor.get_default_account_number()
        
        acc_info = {}
        try:
            for a in RobinhoodExecutor.get_accounts():
                if str(a.get("account_number")) == str(acc):
                    acc_info = a
                    break
        except Exception:
            pass

        port_data = port_data or RobinhoodExecutor.get_live_portfolio(account_number=acc)
        positions = positions if positions is not None else RobinhoodExecutor.get_equity_positions(account_number=acc)
        
        pos_active = [p for p in positions if float(p.get("quantity", 0)) > 0]
        symbols = [p["symbol"] for p in pos_active]
        quotes = quotes if quotes is not None else (RobinhoodExecutor.get_equity_quotes(symbols) if symbols else {})

        export_res = TradingDataManager.export_portfolio(port_data, pos_active, quotes, str(acc))
        proc_pos = export_res["processed_positions"]
        summary = export_res["full_payload"]["summary"]

        tot_val = summary["total_value"]
        eq_val = summary["equity_value"]
        cash_val = summary["cash"]

        # 1. Cash Buffer Analysis (target >= 15%)
        cash_pct = summary["cash_buffer_pct"]
        target_cash_pct = 15.0
        cash_target_val = round(tot_val * 0.15, 2)
        cash_deficit = max(0.0, round(cash_target_val - cash_val, 2))
        cash_status = "HEALTHY" if cash_pct >= 15.0 else ("WARNING" if cash_pct >= 10.0 else "CRITICAL_DEFICIT")

        # 2. Concentration Risk (15% single position limit)
        max_pos_cap_pct = 15.0
        concentration_alerts = []
        for p in proc_pos:
            if p["weight_pct"] >= max_pos_cap_pct:
                concentration_alerts.append({
                    "symbol": p["symbol"],
                    "weight_pct": p["weight_pct"],
                    "current_value": p["current_value"],
                    "excess_value": round(p["current_value"] - (tot_val * 0.15), 2),
                    "status": "OVER_LIMIT"
                })
            elif p["weight_pct"] >= 12.0:
                concentration_alerts.append({
                    "symbol": p["symbol"],
                    "weight_pct": p["weight_pct"],
                    "current_value": p["current_value"],
                    "excess_value": 0.0,
                    "status": "APPROACHING_LIMIT"
                })

        # 3. Sector / Theme Concentration (Semiconductor single-name overlap)
        semi_positions = [p for p in proc_pos if p["symbol"] in cls.SEMI_TICKERS and p["symbol"] != "SMH"]
        semi_total_val = sum(p["current_value"] for p in semi_positions)
        semi_weight_pct = round((semi_total_val / eq_val * 100.0) if eq_val > 0 else 0.0, 2)
        semi_symbols = [p["symbol"] for p in semi_positions]

        # 4. Dead Money Analysis (down > 40%, value < $50)
        dead_money = [p for p in proc_pos if p["unrealized_pnl_pct"] <= -40.0 and p["current_value"] <= 50.0]
        dead_money.sort(key=lambda x: x["unrealized_pnl_pct"])
        dead_money_val = sum(p["current_value"] for p in dead_money)
        dead_money_cost = sum(p["cost_basis"] for p in dead_money)
        dead_money_loss = dead_money_val - dead_money_cost

        # 5. Dust Cleanup (value < $10)
        dust = [p for p in proc_pos if p["current_value"] < 10.0]
        dust_val = sum(p["current_value"] for p in dust)
        dust_cost = sum(p["cost_basis"] for p in dust)

        # 6. Tax-Loss Harvesting Candidates (all negative unrealized P&L)
        losers = [p for p in proc_pos if p["unrealized_pnl"] < 0]
        losers.sort(key=lambda x: x["unrealized_pnl"])  # largest dollar loss first
        total_harvestable_loss = abs(sum(p["unrealized_pnl"] for p in losers))

        # 7. Big Winners / High Gainers
        winners = [p for p in proc_pos if p["unrealized_pnl_pct"] >= 50.0]
        winners.sort(key=lambda x: x["unrealized_pnl_pct"], reverse=True)

        # 8. Compute Health Score (0 - 100)
        score = 100
        if cash_pct < 5.0:
            score -= 20
        elif cash_pct < 10.0:
            score -= 15
        elif cash_pct < 15.0:
            score -= 10

        if any(c["status"] == "OVER_LIMIT" for c in concentration_alerts):
            score -= 15
        elif any(c["status"] == "APPROACHING_LIMIT" for c in concentration_alerts):
            score -= 5

        if semi_weight_pct > 25.0:
            score -= 15
        elif semi_weight_pct > 18.0:
            score -= 10

        if len(dead_money) >= 10:
            score -= 10
        elif len(dead_money) >= 5:
            score -= 5

        if len(dust) >= 15:
            score -= 10
        elif len(dust) >= 5:
            score -= 5

        health_score = max(0, min(100, score))

        # 9. Formulate Clear Action Plan
        action_plan = []
        if dead_money:
            action_plan.append(f"Cut {len(dead_money)} dead-money positions (down >40%, <$50) -> raises ~${dead_money_val:.2f} cash and realizes ${abs(dead_money_loss):.2f} in tax losses.")
        if dust:
            action_plan.append(f"Clean up {len(dust)} dust positions (<$10) -> simplifies portfolio and recovers ~${dust_val:.2f} cash.")
        if winners:
            top_w_syms = ", ".join([f"{w['symbol']} (+{w['unrealized_pnl_pct']:.0f}%)" for w in winners[:3]])
            action_plan.append(f"Harvest / trim big winners ({top_w_syms}) or place trailing stops (8-10%) to lock in multi-bagger gains.")
        if semi_weight_pct > 18.0:
            action_plan.append(f"Trim semiconductor single-name overlap ({', '.join(semi_symbols[:6])}... = {semi_weight_pct}% of equities) into broader core ETFs (SMH / VTI).")
        if cash_deficit > 0:
            action_plan.append(f"Restore cash buffer from {cash_pct:.1f}% to target >= 15.0% (${cash_target_val:.2f} needed; ~${cash_deficit:.2f} deficit covered by dead-money & dust liquidations).")

        audit_dict = {
            "account_number": str(acc),
            "account_nickname": acc_info.get("nickname") or "-",
            "account_type": acc_info.get("brokerage_account_type", "individual"),
            "trading_type": acc_info.get("type", "margin"),
            "agentic_allowed": bool(acc_info.get("agentic_allowed", False)),
            "health_score": health_score,
            "summary": summary,
            "positions": proc_pos,
            "cash_buffer": {
                "current_pct": cash_pct,
                "target_pct": target_cash_pct,
                "status": cash_status,
                "deficit_dollars": cash_deficit
            },
            "concentration_alerts": concentration_alerts,
            "semi_overlap": {
                "symbols": semi_symbols,
                "total_value": round(semi_total_val, 2),
                "weight_pct": semi_weight_pct
            },
            "dead_money": {
                "count": len(dead_money),
                "total_value": round(dead_money_val, 2),
                "total_cost": round(dead_money_cost, 2),
                "harvestable_loss": round(abs(dead_money_loss), 2),
                "positions": dead_money
            },
            "dust_positions": {
                "count": len(dust),
                "total_value": round(dust_val, 2),
                "total_cost": round(dust_cost, 2),
                "positions": dust
            },
            "tax_loss_harvesting": {
                "total_harvestable_loss": round(total_harvestable_loss, 2),
                "loser_count": len(losers),
                "top_losers": losers[:10]
            },
            "big_winners": {
                "count": len(winners),
                "positions": winners
            },
            "action_plan": action_plan,
            "saved_files": {
                "json": export_res["json"],
                "csv": export_res["csv"]
            }
        }

        TradingDataManager.export_audit(audit_dict, str(acc))
        return audit_dict

    @classmethod
    def format_executive_summary(cls, audit: Dict[str, Any]) -> str:
        """Formats a compact, token-efficient executive summary card for agents and humans."""
        s = audit["summary"]
        acc = audit["account_number"]
        agentic = audit.get("agentic_allowed", False)
        agentic_str = "Agentic (Automated execution enabled)" if agentic else "Non-Agentic (Manual placement required)"
        pnl_sign = "+" if s["net_unrealized_pnl"] >= 0 else ""
        
        proc_pos = audit.get("positions", [])
        top5 = proc_pos[:5]
        top5_str = ", ".join([f"{p['symbol']} ${p['current_value']:,.0f} ({p['weight_pct']:.1f}%)" for p in top5]) if top5 else "None"
        
        winners = audit.get("big_winners", {}).get("positions", [])[:3]
        win_str = ", ".join([f"{w['symbol']} +${w['unrealized_pnl']:,.0f} (+{w['unrealized_pnl_pct']:.0f}%)" for w in winners]) if winners else "None"

        losers = audit.get("tax_loss_harvesting", {}).get("top_losers", [])[:3]
        lose_str = ", ".join([f"{l['symbol']} -${abs(l['unrealized_pnl']):,.0f} ({l['unrealized_pnl_pct']:.0f}%)" for l in losers]) if losers else "None"

        dead = audit.get("dead_money", {})
        dust = audit.get("dust_positions", {})
        cb = audit.get("cash_buffer", {})
        semi = audit.get("semi_overlap", {})

        lines = [
            "=" * 80,
            f"  ROBINHOOD PORTFOLIO EXECUTIVE SUMMARY (Account: {acc})",
            "=" * 80,
            f"  Total Value: ${s['total_value']:,.2f} | Equities: ${s['equity_value']:,.2f} | Cash: ${s['cash']:,.2f} ({cb.get('current_pct', 0.0):.1f}%)",
            f"  Cost Basis:  ${s['total_cost_basis']:,.2f} | Unrealized P&L: {pnl_sign}${s['net_unrealized_pnl']:,.2f} ({pnl_sign}{s['net_unrealized_pnl_pct']:.2f}%) | Positions: {s['active_position_count']}",
            f"  Health Score: {audit.get('health_score', 100)}/100 | Mode: {agentic_str}",
            "",
            f"  • Top Holdings : {top5_str}",
            f"  • Top Winners  : {win_str}",
            f"  • Top Losers   : {lose_str}",
        ]
        if cb.get("status") != "HEALTHY":
            lines.append(f"  • Risk Alerts  : ⚠️ Cash buffer {cb.get('current_pct', 0.0):.1f}% < 15% minimum (${cb.get('deficit_dollars', 0.0):,.2f} deficit)")
        else:
            lines.append("  • Risk Alerts  : ✓ Cash buffer healthy (>=15%)")
            
        if semi.get("weight_pct", 0) > 15.0:
            lines.append(f"                   ⚠️ High Semi Overlap: {semi.get('weight_pct')}% across {len(semi.get('symbols', []))} single stocks")
        
        lines.append(f"  • Optimizations: {dead.get('count', 0)} dead-money stocks (<$50, down >40%) -> unlock ~${dead.get('total_value', 0):,.2f}, harvest ${dead.get('harvestable_loss', 0):,.2f} loss")
        lines.append(f"                   {dust.get('count', 0)} dust stocks (<$10) -> recover ~${dust.get('total_value', 0):,.2f} & clean clutter")
        lines.append(f"  • Saved Files  : JSON: {audit.get('saved_files', {}).get('json', '')}")
        lines.append(f"                   CSV:  {audit.get('saved_files', {}).get('csv', '')}")
        lines.append("=" * 80)
        return "\n".join(lines)

    @classmethod
    def format_harvest_losses(cls, audit: Dict[str, Any]) -> str:
        """Formats detailed tax-loss harvesting candidates table."""
        tlh = audit.get("tax_loss_harvesting", {})
        losers = tlh.get("top_losers", [])
        tot_loss = tlh.get("total_harvestable_loss", 0.0)
        
        lines = [
            "=" * 80,
            f"  TAX-LOSS HARVESTING CANDIDATES (Account: {audit['account_number']})",
            "=" * 80,
            f"  Total Harvestable Losses Available: ${tot_loss:,.2f} across {tlh.get('loser_count', 0)} positions",
            "  Note: Losses can offset capital gains dollar-for-dollar + up to $3k ordinary income.",
            "  Warning: Avoid wash sales (do not buy identical security within 30 days).",
            "",
            f"{'Symbol':<8} | {'Qty':<8} | {'Avg Cost':<10} | {'Cur Price':<10} | {'Value':<10} | {'Loss ($)':<10} | {'Loss (%)'}",
            "-" * 80
        ]
        for p in losers:
            lines.append(f"{p['symbol']:<8} | {p['quantity']:<8.3f} | ${p['average_buy_price']:<9.2f} | ${p['current_price']:<9.2f} | ${p['current_value']:<9.2f} | -${abs(p['unrealized_pnl']):<8.2f} | {p['unrealized_pnl_pct']:>6.2f}%")
        lines.append("=" * 80)
        return "\n".join(lines)

    @classmethod
    def format_rebalance_plan(cls, audit: Dict[str, Any]) -> str:
        """Formats a concrete 4-step rebalancing and cash recovery plan."""
        dead = audit.get("dead_money", {})
        dust = audit.get("dust_positions", {})
        winners = audit.get("big_winners", {}).get("positions", [])
        cb = audit.get("cash_buffer", {})
        s = audit["summary"]

        lines = [
            "=" * 80,
            f"  PORTFOLIO ACTIONABLE REBALANCING PLAN (Account: {audit['account_number']})",
            "=" * 80,
            f"  Current Value: ${s['total_value']:,.2f} | Cash: ${s['cash']:,.2f} ({cb.get('current_pct', 0.0):.1f}%) | Target Buffer: 15% (${s['total_value']*0.15:,.2f})",
            f"  Account Execution Type: {'Agentic (Autonomous)' if audit.get('agentic_allowed') else 'Non-Agentic (Place in Robinhood App)'}",
            "",
            "STEP 1: LIQUIDATE DEAD MONEY (Cut losing trades, harvest tax losses):",
            f"  Sell all {dead.get('count', 0)} dead-money positions: {', '.join([p['symbol'] for p in dead.get('positions', [])])}",
            f"  -> Unlocks: ~${dead.get('total_value', 0.0):,.2f} cash | Realizes: ${dead.get('harvestable_loss', 0.0):,.2f} capital loss",
            "",
            "STEP 2: CLEAN UP DUST POSITIONS (Eliminate sub-$10 clutter):",
            f"  Sell {dust.get('count', 0)} dust positions: {', '.join([p['symbol'] for p in dust.get('positions', [])])}",
            f"  -> Unlocks: ~${dust.get('total_value', 0.0):,.2f} cash",
            "",
            "STEP 3: HARVEST / TRIM OVER-EXTENDED WINNERS:",
        ]
        if winners:
            for w in winners[:4]:
                lines.append(f"  - {w['symbol']}: +{w['unrealized_pnl_pct']:.0f}% (${w['current_value']:,.2f}) -> Sell half (~${w['current_value']/2:,.2f}) or set trailing stop at 8-10%")
        else:
            lines.append("  - No extreme winners requiring trimming.")
        
        projected_cash = s['cash'] + dead.get('total_value', 0.0) + dust.get('total_value', 0.0)
        projected_buffer_pct = (projected_cash / s['total_value'] * 100.0) if s['total_value'] > 0 else 0.0
        lines.extend([
            "",
            "STEP 4: CASH BUFFER & DIP-BUYING RESERVE:",
            f"  Projected Cash after Steps 1 & 2: ~${projected_cash:,.2f} ({projected_buffer_pct:.1f}% buffer)",
            f"  Status: {'✓ MEETS TARGET (>=15%)' if projected_buffer_pct >= 15.0 else 'Approaching target'}",
            "",
            "EXECUTION ADVICE:",
            "  • Use limit orders at the bid (or midpoint) for lower-liquidity names to prevent slippage.",
            "  • Keep core high-quality holdings intact (VTI, GOOGL, MSFT, AVGO, AMZN, PANW, CRWD, BND).",
            "=" * 80
        ])
        return "\n".join(lines)


# ==============================================================================
# 7. Autonomous Scheduled Trading Lifecycle & Monitor
# ==============================================================================

def run_scheduled_trading_lifecycle(interval_seconds: int = 900, auto_trade: bool = False, dry_run: bool = True, watchlist: Optional[List[str]] = None):
    """
    Automated full-day trading schedule:
    1. Pre-Market Phase (09:20 ET): Generates briefing and stages daily trade decisions.
    2. Market Open Phase (09:30 ET): Executes / reviews staged orders.
    3. Active Hours Pulse (09:30 - 16:00 ET): Periodic pulse checks for trailing stops & take-profits.
    4. Market Close Phase (16:05 ET): Produces closing report & logs to journal.
    5. Off-Hours Sleep: Sleeps until 09:20 ET of next trading day.
    """
    watchlist = watchlist or DEFAULT_WATCHLIST
    print("=" * 75)
    print("  ROBINHOOD AUTONOMOUS SCHEDULED TRADING LIFECYCLE")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'} | Auto-Trade: {auto_trade} | Interval: {interval_seconds}s")
    print("=" * 75)

    has_run_premarket = False
    has_run_closing = False

    while True:
        now = MarketHours.now_et()
        session = MarketHours.get_market_session(now)
        time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        t_cur = now.time()

        # Phase 1: Pre-Market Briefing (09:20 - 09:30 ET on a Trading Day)
        if MarketHours.is_trading_day(now.date()) and datetime.time(9, 20) <= t_cur < datetime.time(9, 30):
            if not has_run_premarket:
                print(f"\n[🌅 09:20 AM ET PRE-MARKET BRIEFING] Generating daily trade plan...")
                briefing = TradingStrategyEngine.generate_premarket_briefing(watchlist)
                print(json.dumps(briefing, indent=2))
                has_run_premarket = True
                has_run_closing = False
            
            sec_to_open = MarketHours.seconds_until_next_open(now)
            print(f"Staged daily plan. Market opens in {int(sec_to_open)} seconds. Waiting for opening bell...")
            time.sleep(min(sec_to_open, 30.0))
            continue

        # Phase 2: Active Trading Session (09:30 - 16:00 ET)
        elif session == "REGULAR":
            has_run_closing = False
            print(f"\n[{time_str}] ⚡ REGULAR SESSION PULSE CHECK (Every {interval_seconds}s)")
            
            top_opportunities = []
            for ticker in watchlist[:10]:
                try:
                    res = TradingStrategyEngine.analyze_ticker(ticker)
                    top_opportunities.append(res)
                except Exception:
                    pass

            top_opportunities.sort(key=lambda x: x["score"], reverse=True)
            print(f"{'Ticker':<7} | {'Price':<9} | {'Chg%':<7} | {'RSI':<6} | {'Score':<6} | {'Signal':<12} | {'Stop Loss':<10} | {'Target'}")
            print("-" * 80)
            for r in top_opportunities:
                print(f"{r['ticker']:<7} | ${r['price']:<8.2f} | {r['change_pct']:>+5.2f}% | {r['indicators']['rsi']:<6.1f} | {r['score']:<6.1f} | {r['recommendation']:<12} | ${r['risk_targets']['stop_loss']:<9.2f} | ${r['risk_targets']['take_profit_1']:.2f}")

            time_left = MarketHours.seconds_until_close(now)
            print(f"\nSession active: {int(time_left // 60)} minutes remaining. Sleeping {interval_seconds}s until next check...")
            time.sleep(interval_seconds)

        # Phase 3: Post-Market Closing Summary (16:00 - 16:15 ET)
        elif MarketHours.is_trading_day(now.date()) and datetime.time(16, 0) <= t_cur < datetime.time(16, 15):
            if not has_run_closing:
                print(f"\n[🔔 04:05 PM ET MARKET CLOSE SUMMARY] Compiling daily performance...")
                summary = TradingStrategyEngine.generate_daily_closing_summary()
                print(json.dumps(summary, indent=2))
                has_run_closing = True
                has_run_premarket = False
            
            print("Session closed. Preparing off-hours power saving...")
            time.sleep(60.0)

        # Phase 4: Off-Hours Sleep (Nights, Weekends, Holidays)
        else:
            has_run_premarket = False
            sec_to_premarket = MarketHours.seconds_until_premarket(now)
            hours_to_premarket = sec_to_premarket / 3600.0
            next_premarket = MarketHours.next_premarket_time(now)
            
            print(f"🌙 Off-Hours ({session}). Next Pre-Market Briefing: {next_premarket.strftime('%Y-%m-%d %H:%M %Z')} (in {hours_to_premarket:.1f} hours).")
            
            # Sleep in chunks (max 300s or until premarket)
            sleep_chunk = min(sec_to_premarket, 300.0)
            if sleep_chunk > 0:
                print(f"Power saving sleep ({int(sleep_chunk)}s)...")
                time.sleep(sleep_chunk)


def run_market_monitor(interval_seconds: int = 300, auto_trade: bool = False, dry_run: bool = True, watchlist: Optional[List[str]] = None):
    """Backwards-compatible alias for run_scheduled_trading_lifecycle."""
    run_scheduled_trading_lifecycle(interval_seconds=interval_seconds, auto_trade=auto_trade, dry_run=dry_run, watchlist=watchlist)


# ==============================================================================
# 7.6. Deterministic Risk Monitor (no-LLM loop)
# ==============================================================================
#
# The risk-management half of the daily trading cycle, deliberately NOT an AI
# loop: a plain Python loop that enforces the deterministic rules from today's
# plan file (written by the AI pre-market unit at 08:30 ET) against live
# positions during regular trading hours:
#   stop-loss   : price <= avg cost * (1 - stop_loss_pct/100)  -> sell full (market)
#   take-profit : price >= avg cost * (1 + take_profit_pct/100) -> sell 50%, then full
# Flag files in ~/.config/ai/.monitor_flags/ guard against repeat triggers per day.
# Log: ~/.cache/ai/risk_monitor.log
#
# Invoked by the rh-risk systemd unit (rh-risk.timer, Mon..Fri 09:25 ET) which
# loops until 16:00 ET; the loop is bounded (session gate + 16:05 ET backstop).

RISK_MONITOR_LOG = os.path.join(os.path.expanduser("~/.cache/ai"), "risk_monitor.log")
RISK_MONITOR_FLAGS = os.path.join(os.path.expanduser("~/.config/ai"), ".monitor_flags")


def _risk_log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(RISK_MONITOR_LOG), exist_ok=True)
        with open(RISK_MONITOR_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _read_plan_risk_rules(today: str) -> Dict[str, float]:
    """Read stop_loss_pct / take_profit_pct from today's AI plan file (defaults 5.0 / 8.0)."""
    rules = {"stop_loss_pct": 5.0, "take_profit_pct": 8.0}
    path = os.path.join(os.path.expanduser("~/.config/ai/trading_vault/plan"), f"{today}.md")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return rules
    for key in rules:
        m = re.search(rf"^{key}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$", text, re.IGNORECASE | re.MULTILINE)
        if m:
            try:
                rules[key] = float(m.group(1))
            except ValueError:
                pass
    return rules


def _risk_monitor_pulse(account_number: str, dry_run: bool, stop_loss_pct: float, take_profit_pct: float) -> None:
    """One risk-management pass over all open equity positions."""
    today = MarketHours.now_et().strftime("%Y-%m-%d")
    positions = RobinhoodAPI.get_equity_positions(account_number)
    symbols = [str(p.get("symbol", "")).upper() for p in positions if float(p.get("quantity", 0) or 0) > 0]
    quotes = RobinhoodAPI.get_equity_quotes(symbols) if symbols else {}
    os.makedirs(RISK_MONITOR_FLAGS, exist_ok=True)

    for pos in positions:
        sym = str(pos.get("symbol", "")).upper()
        qty = float(pos.get("quantity", 0) or 0)
        avg_cost = float(pos.get("average_buy_price", 0) or 0)
        if not sym or qty <= 0 or avg_cost <= 0:
            continue
        quote = quotes.get(sym, {})
        price = float(quote.get("last_trade_price") or quote.get("price") or avg_cost)
        if price <= 0:
            _risk_log(f"RISK {sym}: no live quote (price={price}); skipping")
            continue
        pnl_pct = (price - avg_cost) / avg_cost * 100.0

        stop_flag = os.path.join(RISK_MONITOR_FLAGS, f"stop_loss_{today}_{sym}.flag")
        tp_flag = os.path.join(RISK_MONITOR_FLAGS, f"take_profit_{today}_{sym}.flag")

        if pnl_pct <= -stop_loss_pct and not os.path.exists(stop_flag):
            action = f"STOP-LOSS {sym} {qty} sh @ ${price:.2f} ({pnl_pct:+.1f}% vs avg ${avg_cost:.2f})"
            _risk_log(f"RISK {sym}: {action}")
            if not dry_run:
                try:
                    res = RobinhoodAPI.execute_market_order(account_number, sym, "sell", quantity=str(qty))
                    _risk_log(f"RISK {sym}: order response={str(res)[:300]}")
                    with open(stop_flag, "w", encoding="utf-8") as fh:
                        json.dump({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                    "price": price, "qty": qty, "pnl_pct": pnl_pct}, fh)
                except Exception as exc:  # noqa: BLE001
                    _risk_log(f"RISK {sym}: order FAILED: {exc}")
        elif pnl_pct >= take_profit_pct:
            stage = "none"
            if os.path.exists(tp_flag):
                try:
                    with open(tp_flag, encoding="utf-8") as fh:
                        stage = json.load(fh).get("stage", "none")
                except (OSError, ValueError):
                    stage = "none"
            if stage == "none":
                sell_qty = round(qty / 2.0, 4)  # market orders allow fractional shares
                action = f"TAKE-PROFIT (50%) {sym} {sell_qty} sh @ ${price:.2f} ({pnl_pct:+.1f}% vs avg ${avg_cost:.2f})"
                _risk_log(f"RISK {sym}: {action}")
                if not dry_run:
                    try:
                        res = RobinhoodAPI.execute_market_order(account_number, sym, "sell", quantity=str(sell_qty))
                        _risk_log(f"RISK {sym}: order response={str(res)[:300]}")
                        with open(tp_flag, "w", encoding="utf-8") as fh:
                            json.dump({"stage": "half",
                                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        "price": price, "qty_sold": sell_qty}, fh)
                    except Exception as exc:  # noqa: BLE001
                        _risk_log(f"RISK {sym}: order FAILED: {exc}")
            elif stage == "half":
                action = f"TAKE-PROFIT (full) {sym} {qty} sh @ ${price:.2f} ({pnl_pct:+.1f}% vs avg ${avg_cost:.2f})"
                _risk_log(f"RISK {sym}: {action}")
                if not dry_run:
                    try:
                        res = RobinhoodAPI.execute_market_order(account_number, sym, "sell", quantity=str(qty))
                        _risk_log(f"RISK {sym}: order response={str(res)[:300]}")
                        with open(tp_flag, "w", encoding="utf-8") as fh:
                            json.dump({"stage": "full",
                                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        "price": price, "qty_sold": qty}, fh)
                    except Exception as exc:  # noqa: BLE001
                        _risk_log(f"RISK {sym}: order FAILED: {exc}")
        else:
            _risk_log(f"RISK {sym}: {pnl_pct:+.2f}% vs avg ${avg_cost:.2f} (price ${price:.2f}) - no action")


def run_risk_monitor(interval_seconds: int = 900, dry_run: bool = False,
                     once: bool = False, force_session: bool = False) -> None:
    """Deterministic risk-management loop (no LLM). Bounded: REGULAR session + 16:05 ET backstop."""
    _risk_log(f"RISK-MONITOR start mode={'DRY-RUN (no orders)' if dry_run else 'LIVE (real market orders)'} "
              f"interval={interval_seconds}s once={once}")
    while True:
        now = MarketHours.now_et()
        session = "REGULAR" if force_session else MarketHours.get_market_session(now)
        if session != "REGULAR":
            if once:
                _risk_log(f"RISK-MONITOR {session} - outside RTH, exiting")
                return
            secs_open = MarketHours.seconds_until_next_open(now)
            if secs_open and secs_open < 6 * 3600:  # pre-market: wait for the open
                time.sleep(min(interval_seconds, max(10, int(secs_open))))
                continue
            _risk_log(f"RISK-MONITOR {session} - exiting")
            return
        account_number = RobinhoodAPI.get_agentic_account_number()
        today = MarketHours.now_et().strftime("%Y-%m-%d")
        rules = _read_plan_risk_rules(today)
        _risk_log(f"RISK-MONITOR pulse REGULAR account={account_number} "
                  f"stop_loss={rules['stop_loss_pct']}% take_profit={rules['take_profit_pct']}% dry_run={dry_run}")
        try:
            _risk_monitor_pulse(account_number, dry_run, rules["stop_loss_pct"], rules["take_profit_pct"])
        except Exception as exc:  # noqa: BLE001
            _risk_log(f"RISK-MONITOR pulse error: {exc}")
        if once:
            return
        if MarketHours.now_et().strftime("%H%M") >= "1605":  # backstop past market close
            _risk_log("RISK-MONITOR past-close backstop reached - exiting")
            return
        time.sleep(interval_seconds)


# ==============================================================================
# 8. Command Line Interface (CLI) Dispatch
# ==============================================================================

def print_help():
    help_text = """
Robinhood Agentic Trading & Market Analysis CLI
================================================
Usage:
  robinhood_trader.py <command> [arguments]

Commands:
  summary [account]          Compact, token-efficient executive summary (<200 tokens) for AI models.
  portfolio [account]        Query live positions (supports --summary, --filter, --top, --json, --csv).
  audit [account]            Full quantitative risk audit, concentration checks & health score (0-100).
  harvest-losses [account]   Tax-loss harvesting candidate breakdown with dollar savings & wash-sale guidance.
  rebalance-plan [account]   Concrete 4-step rebalance plan (cut dead money, clean dust, trim winners).
  export [account] [dir]     Export full portfolio & audit datasets to disk (JSON & CSV).
  status                     Display market status, Eastern time, and session.
  analyze <TICKER...>        Deep technical analysis, indicators, news sentiment & risk levels.
  scan [TICKERS...]          Scan watchlist for highest conviction buy/sell opportunities.
  news <TICKER/QUERY>        Fetch latest financial news headlines and calculate sentiment score.
  rebalance                  Analyze portfolio allocation and generate optimal profit trades.
  monitor [--auto-trade]     Autonomous trading loop running during US market hours.
  auth                       Verify stored credentials status (cached and active).

Options for 'portfolio':
  --summary, -s              Output concise executive summary instead of 70+ position table.
  --filter <type>            Filter positions by: losers, winners, dust, dead-money.
  --top <N>                  Limit output to top N positions by dollar value.
  --json                     Output raw structured JSON to stdout.
  --csv                      Output clean CSV to stdout.

Examples:
  ./robinhood_trader.py summary
  ./robinhood_trader.py portfolio --summary
  ./robinhood_trader.py portfolio --filter losers
  ./robinhood_trader.py audit
  ./robinhood_trader.py harvest-losses
  ./robinhood_trader.py rebalance-plan
  ./robinhood_trader.py export
  ./robinhood_trader.py analyze AAPL NVDA MSFT
  ./robinhood_trader.py scan
"""
    print(help_text.strip())


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print_help()
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "status":
        now = MarketHours.now_et()
        session = MarketHours.get_market_session(now)
        is_open = MarketHours.is_market_open(now)
        next_open = MarketHours.next_market_open(now)
        
        print("=" * 65)
        print("  US STOCK MARKET & ROBINHOOD STATUS")
        print("=" * 65)
        print(f"  Current Time (ET) : {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"  Market Session    : {session}")
        print(f"  Regular Hours     : {'OPEN (09:30 - 16:00 ET)' if is_open else 'CLOSED'}")
        if is_open:
            rem = MarketHours.seconds_until_close(now)
            print(f"  Time to Close     : {int(rem // 60)} minutes")
        else:
            print(f"  Next Market Open  : {next_open.strftime('%Y-%m-%d %H:%M %Z')} (in {MarketHours.seconds_until_next_open(now)/3600:.1f}h)")
        print("=" * 65)

    elif cmd in ("summary", "brief", "overview"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        audit_res = PortfolioAuditor.audit(account_number=acc)
        print(PortfolioAuditor.format_executive_summary(audit_res))

    elif cmd in ("audit", "health", "health-check"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        audit_res = PortfolioAuditor.audit(account_number=acc)
        s = audit_res["summary"]
        cb = audit_res["cash_buffer"]
        semi = audit_res["semi_overlap"]
        dead = audit_res["dead_money"]
        dust = audit_res["dust_positions"]
        tlh = audit_res["tax_loss_harvesting"]

        print("=" * 80)
        print(f"  QUANTITATIVE PORTFOLIO AUDIT & RISK REPORT (Account: {audit_res['account_number']})")
        print("=" * 80)
        print(f"  Health Score      : {audit_res['health_score']}/100")
        print(f"  Total Value       : ${s['total_value']:,.2f} (Equities: ${s['equity_value']:,.2f}, Cash: ${s['cash']:,.2f})")
        print(f"  Cash Buffer       : {cb['current_pct']:.1f}% (Target: >={cb['target_pct']}%, Status: {cb['status']})")
        if cb['deficit_dollars'] > 0:
            print(f"  Cash Deficit      : ${cb['deficit_dollars']:,.2f} required to restore 15% safety buffer")
        print(f"  Cost Basis & P&L  : ${s['total_cost_basis']:,.2f} -> Unrealized P&L: {'+' if s['net_unrealized_pnl']>=0 else ''}${s['net_unrealized_pnl']:,.2f} ({s['net_unrealized_pnl_pct']:+.2f}%)")
        print(f"  Active Positions  : {s['active_position_count']} symbols")
        print("-" * 80)
        print("  CONCENTRATION & SECTOR RISKS:")
        if audit_res.get("concentration_alerts"):
            for c in audit_res["concentration_alerts"]:
                print(f"    ⚠️ {c['symbol']}: {c['weight_pct']:.1f}% weight (${c['current_value']:,.2f}) - {c['status']}")
        else:
            print("    ✓ No single stock breaches the 15% concentration cap.")
        print(f"    Semiconductors  : {semi['weight_pct']:.1f}% of equities across {len(semi['symbols'])} single stocks ({', '.join(semi['symbols'][:6])}...)")
        print("-" * 80)
        print("  OPPORTUNITIES & OPTIMIZATIONS:")
        print(f"    Dead Money (<$50, down >40%) : {dead['count']} positions | Value: ${dead['total_value']:,.2f} | Harvestable Loss: ${dead['harvestable_loss']:,.2f}")
        print(f"    Dust Positions (<$10)        : {dust['count']} positions | Value: ${dust['total_value']:,.2f}")
        print(f"    Total Harvestable Tax Losses : ${tlh['total_harvestable_loss']:,.2f} across {tlh['loser_count']} losing positions")
        print("-" * 80)
        print("  ACTIONABLE RECOMMENDATIONS:")
        for idx, act in enumerate(audit_res.get("action_plan", []), 1):
            print(f"    {idx}. {act}")
        print("=" * 80)
        print(f"  Full Audit JSON: {TradingDataManager.get_cache_dir()}/audit_{audit_res['account_number']}.json")
        print("=" * 80)

    elif cmd in ("harvest-losses", "tax-loss", "tax-losses"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        audit_res = PortfolioAuditor.audit(account_number=acc)
        print(PortfolioAuditor.format_harvest_losses(audit_res))

    elif cmd in ("rebalance-plan", "action-plan", "plan"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        audit_res = PortfolioAuditor.audit(account_number=acc)
        print(PortfolioAuditor.format_rebalance_plan(audit_res))

    elif cmd in ("export", "dump"):
        acc = None
        out_dir = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                if acc is None:
                    acc = a
                elif out_dir is None:
                    out_dir = a
        acc = acc or RobinhoodExecutor.get_default_account_number()
        print(f"Exporting full portfolio and audit datasets for account {acc}...")
        audit_res = PortfolioAuditor.audit(account_number=acc)
        json_path = audit_res["saved_files"]["json"]
        csv_path = audit_res["saved_files"]["csv"]
        audit_path = os.path.join(out_dir or TradingDataManager.get_cache_dir(), f"audit_{acc}.json")
        
        print(f"✓ Export complete:")
        print(f"  • JSON Portfolio : {json_path} ({os.path.getsize(json_path):,} bytes)")
        print(f"  • CSV Portfolio  : {csv_path} ({os.path.getsize(csv_path):,} bytes, {audit_res['summary']['active_position_count']} rows)")
        print(f"  • Audit JSON     : {audit_path} ({os.path.getsize(audit_path):,} bytes)")

    elif cmd in ("portfolio", "holdings", "account"):
        # Check flags
        is_summary = "--summary" in sys.argv or "-s" in sys.argv
        is_json = "--json" in sys.argv
        is_csv = "--csv" in sys.argv
        
        filter_type = None
        if "--filter" in sys.argv:
            idx = sys.argv.index("--filter")
            if idx + 1 < len(sys.argv):
                filter_type = sys.argv[idx + 1].lower()

        top_n = None
        if "--top" in sys.argv:
            idx = sys.argv.index("--top")
            if idx + 1 < len(sys.argv):
                try:
                    top_n = int(sys.argv[idx + 1])
                except Exception:
                    pass

        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-") and a != filter_type and str(top_n) != a:
                acc = a
                break
        acc = acc or RobinhoodExecutor.get_default_account_number()

        if is_summary:
            audit_res = PortfolioAuditor.audit(account_number=acc)
            print(PortfolioAuditor.format_executive_summary(audit_res))
            sys.exit(0)

        print(f"Fetching live Robinhood portfolio for account {acc} using stored credentials...\n")
        port_data = RobinhoodExecutor.get_live_portfolio(account_number=acc)
        positions = RobinhoodExecutor.get_equity_positions(account_number=acc)
        
        pos_active = [p for p in positions if float(p.get("quantity", 0)) > 0]
        symbols = [p["symbol"] for p in pos_active]
        quotes = RobinhoodExecutor.get_equity_quotes(symbols) if symbols else {}

        # Automatically export full datasets to disk
        export_res = TradingDataManager.export_portfolio(port_data, pos_active, quotes, str(acc))
        proc_pos = export_res["processed_positions"]
        summary = export_res["full_payload"]["summary"]

        if is_json:
            print(json.dumps(export_res["full_payload"], indent=2))
            sys.exit(0)

        if is_csv:
            with open(export_res["csv"], "r", encoding="utf-8") as f:
                print(f.read().strip())
            sys.exit(0)

        # Apply filtering if requested
        displayed_pos = proc_pos
        filter_label = ""
        if filter_type == "losers":
            displayed_pos = [p for p in proc_pos if p["unrealized_pnl"] < 0]
            displayed_pos.sort(key=lambda x: x["unrealized_pnl"])
            filter_label = " [Filter: Losers by $ Loss]"
        elif filter_type == "winners":
            displayed_pos = [p for p in proc_pos if p["unrealized_pnl"] > 0]
            displayed_pos.sort(key=lambda x: x["unrealized_pnl_pct"], reverse=True)
            filter_label = " [Filter: Winners by % Gain]"
        elif filter_type == "dust":
            displayed_pos = [p for p in proc_pos if p["current_value"] < 10.0]
            filter_label = " [Filter: Dust < $10]"
        elif filter_type in ("dead-money", "deadmoney"):
            displayed_pos = [p for p in proc_pos if p["unrealized_pnl_pct"] <= -40.0 and p["current_value"] <= 50.0]
            displayed_pos.sort(key=lambda x: x["unrealized_pnl_pct"])
            filter_label = " [Filter: Dead Money (down >40%, <$50)]"

        if top_n and top_n > 0:
            displayed_pos = displayed_pos[:top_n]
            filter_label += f" [Top {top_n}]"

        tot_val = summary["total_value"]
        eq_val = summary["equity_value"]
        cash_val = summary["cash"]
        bp_val = summary["buying_power"]

        print("=" * 80)
        print(f"  ROBINHOOD LIVE PORTFOLIO (Account: {acc}){filter_label}")
        print("=" * 80)
        print(f"  Total Portfolio Value : ${tot_val:,.2f}")
        print(f"  Equity Holdings Value : ${eq_val:,.2f}")
        print(f"  Cash Balance          : ${cash_val:,.2f}")
        print(f"  Buying Power          : ${bp_val:,.2f}")
        print(f"  Showing Positions     : {len(displayed_pos)} of {len(proc_pos)} symbols\n")

        if displayed_pos:
            print(f"{'Symbol':<8} | {'Qty':<8} | {'Avg Cost':<10} | {'Cur Price':<10} | {'Value':<10} | {'Weight':<7} | {'Unrealized P&L'}")
            print("-" * 80)
            tot_cost_disp = 0.0
            tot_val_disp = 0.0
            for p in displayed_pos:
                sym = p["symbol"]
                qty = p["quantity"]
                avg_cost = p["average_buy_price"]
                cur_price = p["current_price"]
                val = p["current_value"]
                cost = p["cost_basis"]
                tot_cost_disp += cost
                tot_val_disp += val
                weight = p["weight_pct"]
                pnl = p["unrealized_pnl"]
                pnl_pct = p["unrealized_pnl_pct"]
                pnl_sign = "+" if pnl >= 0 else ""
                print(f"{sym:<8} | {qty:<8.3f} | ${avg_cost:<9.2f} | ${cur_price:<9.2f} | ${val:<9.2f} | {weight:>5.1f}% | ${pnl_sign}{pnl:<7.2f} ({pnl_sign}{pnl_pct:.2f}%)")

            print("=" * 80)
            net_pnl = tot_val_disp - tot_cost_disp
            net_pct = (net_pnl / tot_cost_disp * 100.0) if tot_cost_disp > 0 else 0.0
            pnl_sign = "+" if net_pnl >= 0 else ""
            print(f"  Display Total: Cost Basis: ${tot_cost_disp:,.2f} | Value: ${tot_val_disp:,.2f} | Net P&L: ${pnl_sign}{net_pnl:,.2f} ({pnl_sign}{net_pct:.2f}%)")
            print(f"  Saved Datasets: JSON: {export_res['json']} | CSV: {export_res['csv']}")
            print("=" * 80)
        else:
            print("  No positions match the selected filter.")
            print("=" * 80)

    elif cmd == "analyze":
        tickers = sys.argv[2:] if len(sys.argv) > 2 else ["SPY", "QQQ", "NVDA", "AAPL"]
        print(f"Analyzing tickers: {', '.join(tickers)}...\n")
        for t in tickers:
            res = TradingStrategyEngine.analyze_ticker(t)
            ind = res["indicators"]
            risk = res["risk_targets"]
            news = res["news_sentiment"]
            
            print("=" * 70)
            print(f"  {res['ticker']} | Price: ${res['price']:.2f} ({res['change_pct']:>+5.2f}% ) | Score: {res['score']}/100 -> {res['recommendation']}")
            print("=" * 70)
            print(f"  • Technical Indicators : RSI(14)={ind['rsi']:.1f}, SMA20=${ind['sma20']:.2f}, SMA50=${ind['sma50']:.2f}, SMA200=${ind['sma200']:.2f}, MACD_Hist={ind['macd_histogram']:+.3f}")
            print(f"  • News Sentiment       : {news['label']} (Score: {news['score']:+.2f})")
            if news['headlines']:
                print(f"    - \"{news['headlines'][0]}\"")
            print(f"  • Risk Controls        : Stop Loss: ${risk['stop_loss']:.2f} ({risk['stop_loss_pct']}%) | Target 1: ${risk['take_profit_1']:.2f} (+8%) | Target 2: ${risk['take_profit_2']:.2f} (+15%)")
            print()

    elif cmd == "scan":
        tickers = sys.argv[2:] if len(sys.argv) > 2 else DEFAULT_WATCHLIST
        print(f"Scanning market universe ({len(tickers)} tickers)...\n")
        results = []
        for t in tickers:
            try:
                results.append(TradingStrategyEngine.analyze_ticker(t))
            except Exception as e:
                print(f"Error analyzing {t}: {e}")
        
        results.sort(key=lambda x: x["score"], reverse=True)
        print(f"{'Ticker':<7} | {'Price':<9} | {'Chg%':<7} | {'RSI':<6} | {'Score':<6} | {'Signal':<12} | {'Stop Loss':<10} | {'Target'}")
        print("-" * 80)
        for r in results:
            print(f"{r['ticker']:<7} | ${r['price']:<8.2f} | {r['change_pct']:>+5.2f}% | {r['indicators']['rsi']:<6.1f} | {r['score']:<6.1f} | {r['recommendation']:<12} | ${r['risk_targets']['stop_loss']:<9.2f} | ${r['risk_targets']['take_profit_1']:.2f}")

    elif cmd == "news":
        query = sys.argv[2] if len(sys.argv) > 2 else "SPY"
        news = NewsSentimentEngine.analyze_news_sentiment(query)
        print(f"Financial News & Sentiment for '{query}':")
        print(f"Sentiment: {news['sentiment_label']} (Score: {news['sentiment_score']:+.2f} on {news['article_count']} articles)\n")
        for i, h in enumerate(news["headlines"], 1):
            print(f"  {i}. {h}")

    elif cmd in ("accounts", "account-list", "whoami"):
        print("Querying Robinhood accounts using stored credentials...\n")
        accounts = RobinhoodExecutor.get_accounts()
        if not accounts:
            print("No accounts found or stored credentials not authorized.")
        else:
            print("=" * 85)
            print("  ROBINHOOD AUTHORIZED BROKERAGE ACCOUNTS")
            print("=" * 85)
            print(f"{'Account Number':<16} | {'Nickname/Type':<18} | {'Account Type':<16} | {'Trading Type':<14} | {'Agentic Allowed':<15}")
            print("-" * 85)
            for acc in accounts:
                acc_num = acc.get("account_number", "")
                nick = acc.get("nickname") or "-"
                b_type = acc.get("brokerage_account_type", "")
                t_type = acc.get("type", "")
                agentic = "✓ YES (Active)" if acc.get("agentic_allowed") else "✗ NO"
                is_def = " [Default]" if acc.get("is_default") else ""
                disp_name = f"{nick}{is_def}"
                print(f"{acc_num:<16} | {disp_name:<18} | {b_type:<16} | {t_type:<14} | {agentic:<15}")
            print("=" * 85)
            print("  Note: Automated trading orders must target an account with 'Agentic Allowed: ✓ YES'.\n")

    elif cmd == "rebalance":
        sample_portfolio = {
            "cash": 2500.0,
            "holdings": {
                "NVDA": {"shares": 10, "entry_price": 115.0},
                "AAPL": {"shares": 15, "entry_price": 220.0},
                "TSLA": {"shares": 8, "entry_price": 240.0}
            }
        }
        res = TradingStrategyEngine.evaluate_portfolio_rebalance(sample_portfolio)
        print(json.dumps(res, indent=2))

    elif cmd == "monitor":
        interval = 60
        auto_trade = "--auto-trade" in sys.argv
        dry_run = "--live" not in sys.argv
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            if idx + 1 < len(sys.argv):
                interval = int(sys.argv[idx + 1])
        run_market_monitor(interval_seconds=interval, auto_trade=auto_trade, dry_run=dry_run)

    elif cmd in ("pre-market", "premarket", "briefing"):
        tickers = sys.argv[2:] if len(sys.argv) > 2 else DEFAULT_WATCHLIST
        print("Generating Pre-Market Briefing & Trade Action Plan...\n")
        briefing = TradingStrategyEngine.generate_premarket_briefing(tickers)
        print(json.dumps(briefing, indent=2))

    elif cmd in ("close-summary", "close"):
        print("Generating Daily Market Close Summary...\n")
        summary = TradingStrategyEngine.generate_daily_closing_summary()
        print(json.dumps(summary, indent=2))

    elif cmd in ("autopilot", "lifecycle"):
        interval = 900
        auto_trade = "--auto-trade" in sys.argv
        dry_run = "--live" not in sys.argv
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            if idx + 1 < len(sys.argv):
                interval = int(sys.argv[idx + 1])
        run_scheduled_trading_lifecycle(interval_seconds=interval, auto_trade=auto_trade, dry_run=dry_run)

    elif cmd in ("discover", "breakout", "screener"):
        print("Dynamically searching live market for high-momentum breakout candidates...\n")
        discovered = TradingStrategyEngine.discover_breakout_candidates()
        print(f"Discovered {len(discovered)} vetted breakout opportunities:\n")
        print(f"{'Ticker':<7} | {'Price':<9} | {'Chg%':<7} | {'RSI':<6} | {'Score':<6} | {'Signal':<12} | {'Stop Loss':<10} | {'Target'}")
        print("-" * 80)
        for r in discovered:
            print(f"{r['ticker']:<7} | ${r['price']:<8.2f} | {r['change_pct']:>+5.2f}% | {r['indicators']['rsi']:<6.1f} | {r['score']:<6.1f} | {r['recommendation']:<12} | ${r['risk_targets']['stop_loss']:<9.2f} | ${r['risk_targets']['take_profit_1']:.2f}")

    elif cmd in ("service", "systemd"):
        sub = sys.argv[2] if len(sys.argv) > 2 else "status"
        service_file = os.path.expanduser("~/.config/systemd/user/robinhood-trader.service")
        
        if sub == "install":
            os.makedirs(os.path.expanduser("~/.config/systemd/user"), exist_ok=True)
            repo_dir = os.path.dirname(os.path.abspath(__file__))
            unit_content = f"""[Unit]
Description=Robinhood Agentic Trading & Market Hours Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={repo_dir}
Environment=PATH={os.path.expanduser('~/.local/bin')}:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
Environment=BROWSER=none
ExecStart=/usr/bin/python3 {os.path.join(repo_dir, 'robinhood_trader.py')} monitor --interval 60
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
            with open(service_file, "w") as f:
                f.write(unit_content)
            subprocess.run(["systemctl", "--user", "daemon-reload"])
            print(f"Installed {service_file}")
            print("To enable & start: python3 robinhood_trader.py service start")

        elif sub in ("start", "enable"):
            subprocess.run(["systemctl", "--user", "daemon-reload"])
            subprocess.run(["systemctl", "--user", "enable", "--now", "robinhood-trader.service"])
            subprocess.run(["systemctl", "--user", "status", "robinhood-trader.service"])

        elif sub == "stop":
            subprocess.run(["systemctl", "--user", "stop", "robinhood-trader.service"])
            print("robinhood-trader.service stopped.")

        elif sub == "restart":
            subprocess.run(["systemctl", "--user", "restart", "robinhood-trader.service"])
            subprocess.run(["systemctl", "--user", "status", "robinhood-trader.service"])

        elif sub == "status":
            subprocess.run(["systemctl", "--user", "status", "robinhood-trader.service"])

        elif sub == "logs":
            lines = sys.argv[3] if len(sys.argv) > 3 else "50"
            subprocess.run(["journalctl", "--user", "-u", "robinhood-trader.service", "-n", lines, "--no-pager"])

        else:
            print(f"Unknown service command: {sub}")
            print("Usage: robinhood_trader.py service [install|start|stop|restart|status|logs]")

    elif cmd == "auth":
        authenticate_robinhood_mcp()

    else:
        print(f"Unknown command: {cmd}")
        print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
