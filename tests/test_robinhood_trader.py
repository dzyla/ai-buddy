import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import datetime
from robinhood_trader import (
    MarketHours,
    TechnicalIndicators,
    NewsSentimentEngine,
    TradingStrategyEngine,
    TradingDataManager,
    PortfolioAuditor,
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
        from robinhood_trader import RobinhoodExecutor, FinancialData
        
        def fake_mcp_call(tool_name, arguments, timeout=15):
            if tool_name == 'get_portfolio':
                return {'structuredContent': {'data': {'total_value': '8230.28', 'equity_value': '8025.95', 'cash': '200.00'}}}
            elif tool_name == 'get_equity_positions':
                return {'structuredContent': {'data': {'positions': [{'symbol': 'VTI', 'quantity': '3.012', 'average_buy_price': '329.76'}]}}}
            elif tool_name == 'get_equity_quotes':
                return {'structuredContent': {'data': {'results': [{'quote': {'symbol': 'VTI', 'last_trade_price': '384.30', 'previous_close': '380.00'}}]}}}
            elif tool_name == 'get_accounts':
                return {'structuredContent': {'data': {'accounts': [
                    {'account_number': '837546068', 'is_default': True, 'agentic_allowed': False},
                    {'account_number': '517198354', 'nickname': 'Agentic', 'agentic_allowed': True}
                ]}}}
            elif tool_name == 'get_equity_historicals':
                return {'structuredContent': {'data': {'results': [{
                    'symbol': 'VTI',
                    'bars': [
                        {'begins_at': '2026-08-10T00:00:00Z', 'open_price': '378.00', 'high_price': '382.00', 'low_price': '377.00', 'close_price': '380.00', 'volume': 1500000},
                        {'begins_at': '2026-08-11T00:00:00Z', 'open_price': '380.00', 'high_price': '383.00', 'low_price': '379.00', 'close_price': '381.50', 'volume': 1400000},
                        {'begins_at': '2026-08-12T00:00:00Z', 'open_price': '381.00', 'high_price': '384.00', 'low_price': '380.00', 'close_price': '383.00', 'volume': 1600000},
                        {'begins_at': '2026-08-13T00:00:00Z', 'open_price': '383.00', 'high_price': '385.00', 'low_price': '382.00', 'close_price': '384.00', 'volume': 1700000},
                        {'begins_at': '2026-08-14T00:00:00Z', 'open_price': '384.00', 'high_price': '386.00', 'low_price': '383.50', 'close_price': '384.30', 'volume': 1800000}
                    ]
                }]}}}
            elif tool_name == 'get_equity_technical_indicators':
                return {'structuredContent': {'data': {'results': [{
                    'indicators': [{'begins_at': '2026-08-14T00:00:00Z', 'value': 65.4}]
                }]}}}
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
        assert len(accs) == 2
        assert accs[0]["account_number"] == "837546068"

        agentic_acc = RobinhoodExecutor.get_agentic_account()
        assert agentic_acc is not None
        assert agentic_acc["account_number"] == "517198354"
        assert RobinhoodExecutor.get_agentic_account_number() == "517198354"

        hist = RobinhoodExecutor.get_equity_historicals(["VTI"])
        assert "VTI" in hist
        assert len(hist["VTI"]) == 5
        assert float(hist["VTI"][-1]["close_price"]) == pytest.approx(384.30)

        ti = RobinhoodExecutor.get_equity_technical_indicators("VTI", "rsi")
        assert len(ti) == 1
        assert ti[0]["value"] == pytest.approx(65.4)

        # Verify FinancialData seamlessly routes through RobinhoodExecutor
        quote = FinancialData.fetch_quote("VTI")
        assert quote["price"] == pytest.approx(384.30)
        assert quote["source"] == "robinhood_mcp"

        bars = FinancialData.fetch_historical("VTI")
        assert len(bars) == 5
        assert bars[-1]["close"] == pytest.approx(384.30)


class TestTradingDataManagerAndAuditor:
    def test_export_portfolio_and_audit(self, tmp_path):
        port_data = {
            "total_value": "8230.28",
            "equity_value": "8025.95",
            "cash": "200.00",
            "buying_power": "200.00"
        }
        positions = [
            {"symbol": "VTI", "quantity": "3.012", "average_buy_price": "329.76"},
            {"symbol": "INTC", "quantity": "2.327", "average_buy_price": "131.49"},
            {"symbol": "AEHR", "quantity": "1.574", "average_buy_price": "13.40"},
            {"symbol": "SES", "quantity": "10.0", "average_buy_price": "3.20"},
            {"symbol": "SERV", "quantity": "2.0", "average_buy_price": "14.47"}
        ]
        quotes = {
            "VTI": {"last_trade_price": "383.85"},
            "INTC": {"last_trade_price": "102.54"},
            "AEHR": {"last_trade_price": "134.05"},
            "SES": {"last_trade_price": "0.60"},
            "SERV": {"last_trade_price": "4.98"}
        }

        # 1. Test TradingDataManager export
        exp = TradingDataManager.export_portfolio(port_data, positions, quotes, "837546068", output_dir=str(tmp_path))
        assert os.path.exists(exp["json"])
        assert os.path.exists(exp["csv"])
        assert len(exp["processed_positions"]) == 5

        # 2. Test PortfolioAuditor audit
        audit = PortfolioAuditor.audit(
            account_number="837546068",
            port_data=port_data,
            positions=positions,
            quotes=quotes
        )
        assert audit["account_number"] == "837546068"
        assert audit["summary"]["total_value"] == pytest.approx(8230.28)
        assert audit["cash_buffer"]["status"] == "CRITICAL_DEFICIT"
        assert audit["dead_money"]["count"] >= 2  # SES (-81%), SERV (-65%)
        assert audit["tax_loss_harvesting"]["loser_count"] >= 3  # INTC, SES, SERV
        assert audit["big_winners"]["count"] >= 1  # AEHR (+900%)

        # 3. Test Formatted Summary Outputs
        summary_text = PortfolioAuditor.format_executive_summary(audit)
        assert "ROBINHOOD PORTFOLIO EXECUTIVE SUMMARY" in summary_text
        assert "VTI" in summary_text
        assert "Saved Files" in summary_text

        harvest_text = PortfolioAuditor.format_harvest_losses(audit)
        assert "TAX-LOSS HARVESTING CANDIDATES" in harvest_text
        assert "INTC" in harvest_text

        plan_text = PortfolioAuditor.format_rebalance_plan(audit)
        assert "PORTFOLIO ACTIONABLE REBALANCING PLAN" in plan_text
        assert "STEP 1: LIQUIDATE DEAD MONEY" in plan_text


class TestRiskMonitorAndHelpers:
    def test_now_local(self):
        loc = MarketHours.now_local()
        assert loc is not None
        assert loc.tzinfo is not None

    def test_robinhood_api_alias(self):
        from robinhood_trader import RobinhoodAPI, RobinhoodExecutor
        assert RobinhoodAPI is RobinhoodExecutor

    def test_read_plan_risk_rules(self, tmp_path, monkeypatch):
        from robinhood_trader import _read_plan_risk_rules
        import robinhood_trader
        
        # Test default rules when no file exists
        rules = _read_plan_risk_rules("2099-01-01")
        assert rules["stop_loss_pct"] == pytest.approx(5.0)
        assert rules["take_profit_pct"] == pytest.approx(8.0)

        # Test reading from daily notes with custom values
        notes_dir = tmp_path / "daily_notes"
        notes_dir.mkdir(parents=True)
        note_file = notes_dir / "2099-01-01.md"
        note_file.write_text("""---
stop_loss_pct: 6.5
take_profit_pct: 12.0
---
# Daily Note
""")
        vault_dir = tmp_path
        monkeypatch.setattr(robinhood_trader, "VAULT_DIR", str(vault_dir))
        
        custom_rules = _read_plan_risk_rules("2099-01-01")
        assert custom_rules["stop_loss_pct"] >= 5.0


class TestAgentAdvisor:
    def test_find_ai_binary(self):
        from robinhood_trader import find_ai_binary
        bin_path = find_ai_binary()
        assert bin_path is not None
        assert len(bin_path) > 0

    def test_validate_trade_decision_mock(self):
        from robinhood_trader import AgentAdvisor
        decision = AgentAdvisor.validate_trade_decision(
            ticker="NVDA",
            action="sell",
            current_price=220.0,
            avg_cost=235.0,
            pnl_pct=-6.38,
            reason="Stop-Loss (-5%)"
        )
        assert "verdict" in decision
        assert decision["verdict"] in ("EXECUTE", "WAIT")
        assert "agent_response" in decision

    def test_review_premarket_briefing_mock(self):
        from robinhood_trader import AgentAdvisor
        briefing_mock = {
            "macro_sentiment": {"label": "BULLISH", "score": 0.35, "key_headlines": ["Tech rallies"]},
            "top_buy_candidates": [{"ticker": "NVDA", "price": 220.0, "score": 85.0}]
        }
        res = AgentAdvisor.review_premarket_briefing(briefing_mock)
        assert res is not None
        assert len(res) > 0


class TestWashSaleAndCooldown:
    def test_cooldown_same_day_lockout(self, tmp_path, monkeypatch):
        import robinhood_trader
        from robinhood_trader import ComplianceAndRiskGuard, MarketHours
        
        pdt_file = str(tmp_path / "pdt_tracker.json")
        monkeypatch.setattr(robinhood_trader, "PDT_TRACKER_FILE", pdt_file)

        # Record a SELL today
        ComplianceAndRiskGuard.record_trade("TEST_ACC", "SMCI", "SELL", price=35.0, qty=1.0, pnl_pct=-5.0, reason="STOP_LOSS")
        
        # Verify same-day re-entry is strictly blocked
        in_cd, msg = ComplianceAndRiskGuard.is_in_cooldown("TEST_ACC", "SMCI")
        assert in_cd is True
        assert "SAME-DAY RE-ENTRY LOCKOUT" in msg

        # Another ticker that was not sold should NOT be in cooldown
        in_cd_other, _ = ComplianceAndRiskGuard.is_in_cooldown("TEST_ACC", "NVDA")
        assert in_cd_other is False

    def test_cooldown_past_loss_and_profit(self, tmp_path, monkeypatch):
        import json
        import robinhood_trader
        from robinhood_trader import ComplianceAndRiskGuard, MarketHours
        
        pdt_file = tmp_path / "pdt_tracker.json"
        monkeypatch.setattr(robinhood_trader, "PDT_TRACKER_FILE", str(pdt_file))

        today_dt = MarketHours.now_et()
        two_days_ago = (today_dt - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        four_days_ago = (today_dt - datetime.timedelta(days=4)).strftime("%Y-%m-%d")
        ten_days_ago = (today_dt - datetime.timedelta(days=10)).strftime("%Y-%m-%d")

        pdt_file.write_text(json.dumps({
            "trades": [
                {"account": "ACC1", "symbol": "LOSS_TICKER", "action": "SELL", "date": two_days_ago, "pnl_pct": -6.0, "reason": "STOP_LOSS"},
                {"account": "ACC1", "symbol": "PROFIT_TICKER", "action": "SELL", "date": four_days_ago, "pnl_pct": 10.0, "reason": "TAKE_PROFIT"},
                {"account": "ACC1", "symbol": "OLD_LOSS_TICKER", "action": "SELL", "date": ten_days_ago, "pnl_pct": -5.0, "reason": "STOP_LOSS"}
            ]
        }))

        # LOSS_TICKER sold 2 days ago (< 7 day loss cooldown) -> BLOCKED
        in_cd, msg = ComplianceAndRiskGuard.is_in_cooldown("ACC1", "LOSS_TICKER", cooldown_days_loss=7, cooldown_days_profit=3)
        assert in_cd is True
        assert "WASH-SALE / RE-ENTRY COOLDOWN" in msg

        # PROFIT_TICKER sold 4 days ago (> 3 day profit cooldown) -> ALLOWED
        in_cd, _ = ComplianceAndRiskGuard.is_in_cooldown("ACC1", "PROFIT_TICKER", cooldown_days_loss=7, cooldown_days_profit=3)
        assert in_cd is False

        # OLD_LOSS_TICKER sold 10 days ago (> 7 day loss cooldown) -> ALLOWED
        in_cd, _ = ComplianceAndRiskGuard.is_in_cooldown("ACC1", "OLD_LOSS_TICKER", cooldown_days_loss=7, cooldown_days_profit=3)
        assert in_cd is False


class TestDynamicVolatilityAndRunnerRisk:
    def test_etf_vs_high_beta_risk_parameters(self):
        from robinhood_trader import ComplianceAndRiskGuard
        # Test ETF risk parameters (tight stop, conservative trailing stop)
        vti_params = ComplianceAndRiskGuard.get_ticker_risk_parameters("VTI", 380.0)
        assert vti_params["stop_loss_pct"] <= 5.0
        assert vti_params["trailing_stop_pct"] <= 4.0
        assert vti_params["take_profit_1_pct"] == 8.0
        assert vti_params["take_profit_2_pct"] == 15.0

        # Test single stock dynamic scaling
        stock_params = ComplianceAndRiskGuard.get_ticker_risk_parameters("NVDA", 220.0)
        assert stock_params["stop_loss_pct"] >= 5.0
        assert stock_params["take_profit_1_pct"] == 8.0

    def test_balanced_lifecycle_watchlist(self):
        from robinhood_trader import BALANCED_LIFECYCLE_WATCHLIST
        # Verify Core Ballast ETFs are included
        assert "VTI" in BALANCED_LIFECYCLE_WATCHLIST
        assert "QQQ" in BALANCED_LIFECYCLE_WATCHLIST
        # Verify cross-sector diversity
        assert any(t in BALANCED_LIFECYCLE_WATCHLIST for t in ("NVDA", "AVGO", "PLTR"))
        assert any(t in BALANCED_LIFECYCLE_WATCHLIST for t in ("MSFT", "GOOGL", "CRWD"))
        assert any(t in BALANCED_LIFECYCLE_WATCHLIST for t in ("LLY", "NVO"))

    def test_anti_chasing_filter(self, monkeypatch):
        from robinhood_trader import TradingStrategyEngine, FinancialData
        # Mock quote with big intraday spike (+5%) and high RSI
        monkeypatch.setattr(FinancialData, "fetch_quote", lambda t: {"price": 100.0, "change_percent": 5.0})
        # Mock historical with upward run
        prices = [float(100 + i * 2) for i in range(30)]
        mock_bars = [{"close": p, "high": p + 1, "low": p - 1, "volume": 1_000_000} for p in prices]
        monkeypatch.setattr(FinancialData, "fetch_historical", lambda *args, **kwargs: mock_bars)
        
        analysis = TradingStrategyEngine.analyze_ticker("TEST")
        # Anti-chasing filter should have applied penalty and NOT be STRONG_BUY
        assert "anti_chase" in analysis.get("factor_breakdown", {})
        assert analysis["recommendation"] != "STRONG_BUY"

    def test_secular_uptrend_pullback_dip_buying(self, monkeypatch):
        import math
        from robinhood_trader import TradingStrategyEngine, FinancialData
        # Mock oscillating upward trend over 80 days then 7-day pullback to SMA20
        prices = [100.0 + i * 1.0 + 3.0 * math.sin(i * 0.5) for i in range(80)]
        prices += [178.0, 176.5, 175.0, 173.5, 172.0, 171.0, 169.0]
        mock_bars = [{"close": p, "high": p + 1.5, "low": p - 1.5, "volume": 1_000_000} for p in prices]
        monkeypatch.setattr(FinancialData, "fetch_quote", lambda t: {"price": 169.0, "change_percent": -1.1})
        monkeypatch.setattr(FinancialData, "fetch_historical", lambda *args, **kwargs: mock_bars)
        
        analysis = TradingStrategyEngine.analyze_ticker("PULLBACK_TEST")
        factors = analysis.get("factor_breakdown", {})
        # Pullback factor should be active because price > SMA200 and testing SMA20
        assert "pullback_support" in factors
        assert factors["pullback_support"] >= 8.0


class TestAgentAdvisorWealthGuard:
    def test_invoke_local_llm_parsing(self, monkeypatch):
        import json
        import urllib.request
        import io
        from robinhood_trader import AgentAdvisor

        fake_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "verdict": "EXECUTE",
                        "action": "BUY",
                        "confidence": 0.88,
                        "reasoning": "Strong trend above 200 SMA and neutral RSI."
                    }),
                    "reasoning_content": "Detailed reasoning trace here."
                }
            }]
        }

        class FakeHTTPResponse:
            status = 200
            def read(self):
                return json.dumps(fake_resp).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: FakeHTTPResponse())

        res = AgentAdvisor.invoke_local_llm([{"role": "user", "content": "test"}])
        assert res is not None
        assert "confidence" in res["content"]
        assert "Detailed reasoning" in res["reasoning"]

    def test_strict_buy_rejection_on_low_confidence_or_pass(self, monkeypatch):
        from robinhood_trader import AgentAdvisor

        # 1. Test LLM returning PASS (overbought spike)
        monkeypatch.setattr(AgentAdvisor, "invoke_agent", lambda *args, **kwargs: json.dumps({
            "verdict": "WAIT",
            "action": "PASS",
            "confidence": 0.95,
            "reasoning": "Parabolic gap up overextended."
        }))
        dec = AgentAdvisor.validate_trade_decision("SMCI", "buy", 40.0, 40.0, 0.0, "Breakout")
        assert dec["verdict"] == "WAIT"

        # 2. Test LLM returning EXECUTE but low confidence (0.50 < 0.65 threshold)
        monkeypatch.setattr(AgentAdvisor, "invoke_agent", lambda *args, **kwargs: json.dumps({
            "verdict": "EXECUTE",
            "action": "BUY",
            "confidence": 0.50,
            "reasoning": "Uncertain edge."
        }))
        dec2 = AgentAdvisor.validate_trade_decision("SMCI", "buy", 40.0, 40.0, 0.0, "Breakout")
        assert dec2["verdict"] == "WAIT"

        # 3. Test LLM returning truncated / broken text -> MUST default to WAIT for BUY
        monkeypatch.setattr(AgentAdvisor, "invoke_agent", lambda *args, **kwargs: "are the kill switch.")
        dec3 = AgentAdvisor.validate_trade_decision("SMCI", "buy", 40.0, 40.0, 0.0, "Breakout")
        assert dec3["verdict"] == "WAIT"

    def test_strict_buy_approval_on_high_confidence(self, monkeypatch):
        from robinhood_trader import AgentAdvisor

        monkeypatch.setattr(AgentAdvisor, "invoke_agent", lambda *args, **kwargs: json.dumps({
            "verdict": "EXECUTE",
            "action": "BUY",
            "confidence": 0.82,
            "reasoning": "Solid pullback to 50 SMA with positive risk/reward."
        }))
        dec = AgentAdvisor.validate_trade_decision("NVDA", "buy", 220.0, 220.0, 0.0, "Secular Pullback")
        assert dec["verdict"] == "EXECUTE"
        assert dec["confidence"] == 0.82





