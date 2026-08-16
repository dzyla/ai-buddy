import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import datetime
from robinhood_trader import (
    MarketHours,
    TechnicalIndicators,
    NewsSentimentEngine,
    TradingStrategyEngine,
    ET_ZONE
)


class TestMarketHours:
    def test_weekend_detection(self):
        # 2026-08-15 is Saturday, 2026-08-16 is Sunday, 2026-08-17 is Monday
        sat = datetime.datetime(2026, 8, 15, 12, 0, tzinfo=ET_ZONE)
        sun = datetime.datetime(2026, 8, 16, 12, 0, tzinfo=ET_ZONE)
        mon = datetime.datetime(2026, 8, 17, 12, 0, tzinfo=ET_ZONE)

        assert MarketHours.is_weekend(sat) is True
        assert MarketHours.is_weekend(sun) is True
        assert MarketHours.is_weekend(mon) is False

    def test_holidays_calculation(self):
        # 2026 holidays: July 4 is Saturday -> observed Friday July 3
        # Christmas 2026-12-25 (Friday)
        # New Year's 2026-01-01 (Thursday)
        holidays_2026 = MarketHours.get_market_holidays(2026)
        assert datetime.date(2026, 1, 1) in holidays_2026
        assert datetime.date(2026, 12, 25) in holidays_2026
        assert datetime.date(2026, 7, 3) in holidays_2026

        assert MarketHours.is_market_holiday(datetime.date(2026, 12, 25)) is True
        assert MarketHours.is_market_holiday(datetime.date(2026, 8, 17)) is False

    def test_trading_day_check(self):
        # Mon Aug 17, 2026 is a normal trading day
        assert MarketHours.is_trading_day(datetime.date(2026, 8, 17)) is True
        # Sat Aug 15 is not
        assert MarketHours.is_trading_day(datetime.date(2026, 8, 15)) is False
        # Dec 25 is holiday
        assert MarketHours.is_trading_day(datetime.date(2026, 12, 25)) is False

    def test_market_session_regular(self):
        # Wednesday at 10:30 AM ET
        dt = datetime.datetime(2026, 8, 19, 10, 30, tzinfo=ET_ZONE)
        assert MarketHours.get_market_session(dt) == "REGULAR"
        assert MarketHours.is_market_open(dt) is True

    def test_market_session_pre_market(self):
        # Wednesday at 07:00 AM ET
        dt = datetime.datetime(2026, 8, 19, 7, 0, tzinfo=ET_ZONE)
        assert MarketHours.get_market_session(dt) == "PRE_MARKET"
        assert MarketHours.is_market_open(dt) is False

    def test_market_session_after_hours(self):
        # Wednesday at 17:30 ET
        dt = datetime.datetime(2026, 8, 19, 17, 30, tzinfo=ET_ZONE)
        assert MarketHours.get_market_session(dt) == "AFTER_HOURS"
        assert MarketHours.is_market_open(dt) is False

    def test_market_session_closed_weekend(self):
        # Saturday at 11:00 AM ET
        dt = datetime.datetime(2026, 8, 15, 11, 0, tzinfo=ET_ZONE)
        assert MarketHours.get_market_session(dt) == "CLOSED"
        assert MarketHours.is_market_open(dt) is False

    def test_next_market_open_from_weekend(self):
        # From Sat Aug 15, next open is Mon Aug 17 09:30 ET
        dt = datetime.datetime(2026, 8, 15, 14, 0, tzinfo=ET_ZONE)
        nxt = MarketHours.next_market_open(dt)
        assert nxt == datetime.datetime(2026, 8, 17, 9, 30, tzinfo=ET_ZONE)


class TestTechnicalIndicators:
    def test_sma_calculation(self):
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        sma3 = TechnicalIndicators.sma(prices, 3)
        assert sma3[0] is None
        assert sma3[1] is None
        assert sma3[2] == pytest.approx(20.0)
        assert sma3[3] == pytest.approx(30.0)
        assert sma3[4] == pytest.approx(40.0)

    def test_ema_calculation(self):
        prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        ema3 = TechnicalIndicators.ema(prices, 3)
        assert len(ema3) == len(prices)
        assert ema3[2] == pytest.approx(11.0)
        # Verify subsequent values are smoothed
        assert ema3[-1] > ema3[2]

    def test_rsi_calculation(self):
        # Uptrending sequence should result in high RSI
        uptrend = [float(i) for i in range(1, 30)]
        rsi_up = TechnicalIndicators.rsi(uptrend, 14)
        assert rsi_up[-1] is not None
        assert rsi_up[-1] > 80.0

        # Downtrending sequence should result in low RSI
        downtrend = [float(100 - i) for i in range(1, 30)]
        rsi_down = TechnicalIndicators.rsi(downtrend, 14)
        assert rsi_down[-1] is not None
        assert rsi_down[-1] < 20.0

    def test_macd_calculation(self):
        series = [100.0 + i * 1.5 for i in range(50)]
        macd_res = TechnicalIndicators.macd(series)
        assert "macd" in macd_res
        assert "signal" in macd_res
        assert "histogram" in macd_res
        assert len(macd_res["histogram"]) == len(series)

    def test_bollinger_bands(self):
        series = [100.0 + (i % 5) for i in range(30)]
        bb = TechnicalIndicators.bollinger_bands(series, period=20)
        assert bb["upper"][-1] > bb["middle"][-1]
        assert bb["lower"][-1] < bb["middle"][-1]
        assert 0.0 <= bb["percent_b"][-1] <= 1.0

    def test_atr(self):
        highs = [105.0 + i for i in range(30)]
        lows = [95.0 + i for i in range(30)]
        closes = [100.0 + i for i in range(30)]
        atr_vals = TechnicalIndicators.atr(highs, lows, closes, 14)
        assert atr_vals[-1] == pytest.approx(10.0, abs=0.5)


class TestNewsSentiment:
    def test_bullish_scoring(self):
        text = "Nvidia surges to record high after massive earnings beat and strong revenue growth outlook with Wall Street upgrade"
        score = NewsSentimentEngine.score_text(text)
        assert score > 0.3

    def test_bearish_scoring(self):
        text = "Stock plunges following fraud probe, massive quarterly loss, and deep price target downgrade warning"
        score = NewsSentimentEngine.score_text(text)
        assert score < -0.3

    def test_neutral_scoring(self):
        text = "Company scheduled to host investor conference on Thursday at 2 PM"
        score = NewsSentimentEngine.score_text(text)
        assert abs(score) < 0.15


class TestTradingStrategyEngine:
    def test_analyze_ticker_structure(self):
        analysis = TradingStrategyEngine.analyze_ticker("AAPL")
        assert "ticker" in analysis
        assert "price" in analysis
        assert "score" in analysis
        assert "recommendation" in analysis
        assert "indicators" in analysis
        assert "risk_targets" in analysis
        assert analysis["risk_targets"]["stop_loss"] < analysis["price"]
        assert analysis["risk_targets"]["take_profit_1"] > analysis["price"]

    def test_portfolio_rebalance_stop_loss_trigger(self):
        # AAPL purchased at 200, current mock price is 100 -> P&L = -50% -> should trigger SELL stop loss
        portfolio = {
            "cash": 1000.0,
            "holdings": {
                "AAPL": {"shares": 10, "entry_price": 200.0}
            }
        }
        res = TradingStrategyEngine.evaluate_portfolio_rebalance(portfolio)
        assert res["portfolio_value"] > 0
        assert len(res["proposed_trades"]) >= 1
        trade = res["proposed_trades"][0]
        assert trade["ticker"] == "AAPL"
        assert trade["action"] == "SELL"
        assert "Stop Loss" in trade["reason"]

    def test_premarket_timing_and_briefing(self):
        next_pre = MarketHours.next_premarket_time()
        assert next_pre is not None
        assert next_pre.time() == datetime.time(9, 20)
        assert MarketHours.seconds_until_premarket() >= 0.0

        # Test premarket briefing generation
        briefing = TradingStrategyEngine.generate_premarket_briefing(watchlist=["SPY", "QQQ"])
        assert "briefing_time" in briefing
        assert "macro_sentiment" in briefing

    def test_daily_closing_summary(self):
        summary = TradingStrategyEngine.generate_daily_closing_summary()
        assert summary["status"] == "COMPLETED"
        assert summary["session"] == "REGULAR_CLOSE"


class TestRobinhoodExecutor:
    def test_executor_methods_mocked(self, monkeypatch):
        """Verify executor formats requests properly without making live external auth requests."""
        from robinhood_trader import RobinhoodExecutor
        
        # Mock call_mcp_tool to avoid spamming the remote auth endpoint during CI/test runs
        def fake_mcp_call(tool_name, arguments, timeout=15):
            if tool_name == 'get_portfolio':
                return {'structuredContent': {'data': {'total_value': '8230.28', 'equity_value': '8025.95', 'cash': '200.00'}}}
            elif tool_name == 'get_equity_positions':
                return {'structuredContent': {'data': {'positions': [{'symbol': 'VTI', 'quantity': '3.012', 'average_buy_price': '329.76'}]}}}
            elif tool_name == 'get_equity_quotes':
                return {'structuredContent': {'data': {'results': [{'quote': {'symbol': 'VTI', 'last_trade_price': '384.30'}}]}}}
            elif tool_name == 'get_accounts':
                return {'structuredContent': {'data': {'accounts': [{'account_number': '837546068'}]}}}
            return {}

        monkeypatch.setattr(RobinhoodExecutor, "call_mcp_tool", fake_mcp_call)

        port = RobinhoodExecutor.get_live_portfolio("837546068")
        assert float(port["total_value"]) == pytest.approx(8230.28)

        pos = RobinhoodExecutor.get_equity_positions("837546068")
        assert len(pos) == 1
        assert pos[0]["symbol"] == "VTI"

        quotes = RobinhoodExecutor.get_equity_quotes(["VTI"])
        assert "VTI" in quotes
        assert float(quotes["VTI"]["last_trade_price"]) == pytest.approx(384.30)

        accs = RobinhoodExecutor.get_accounts()
        assert len(accs) == 1
        assert accs[0]["account_number"] == "837546068"
