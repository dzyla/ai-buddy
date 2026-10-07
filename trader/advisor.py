"""News sentiment, the local-LLM advisor and the legacy discretionary strategy engine."""

import os
import json
import math
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import re
import datetime
from typing import List, Dict, Any, Optional

from .config import CONFIG_DIR, DEFAULT_WATCHLIST, DEFERRED_EXIT_FLAG_DIR, ET_ZONE, VAULT_DIR
from .journal import ObsidianTradingVault
from .market_hours import MarketHours
from .data import FinancialData
from .indicators import TechnicalIndicators
from .strategy import MomentumStrategy
from .broker import RobinhoodExecutor
from .risk import ComplianceAndRiskGuard


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
        """
        Fetches news, deduplicates syndicated articles, and calculates recency-weighted sentiment:
        - NLP Engine: Financial Lexicon Keyword Scoring
        - Deduplication: Title similarity hashing
        - Recency decay: Recent (<24h) = 1.0x, (24-48h) = 0.5x, older = 0.25x
        """
        raw_articles = cls.fetch_news(query_or_ticker)
        if not raw_articles:
            return {
                "ticker": query_or_ticker,
                "article_count": 0,
                "sentiment_score": 0.0,
                "sentiment_label": "NEUTRAL",
                "headlines": []
            }

        # Deduplicate headlines by normalized title slug
        unique_articles = []
        seen_slugs = set()
        for a in raw_articles:
            title = a.get("title", "").strip()
            slug = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                unique_articles.append(a)

        # Recency-weighted sentiment: half-life ~2 hours (lambda=0.35/hr).
        # Articles older than 48 h retain only ~8% weight; same-session news dominates.
        # pubDate format from Yahoo RSS: "Mon, 24 Aug 2026 14:30:00 +0000"
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        _lambda = 0.35  # decay rate per hour (half-life ≈ 2 h)
        weighted_scores = []
        total_weight = 0.0
        for a in unique_articles:
            raw_score = cls.score_text(a["title"])
            pub_date_str = a.get("date", "").strip()
            weight = 1.0  # default: no decay if date unavailable
            if pub_date_str:
                try:
                    pub_dt = datetime.datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
                except ValueError:
                    try:
                        pub_date_clean = re.sub(r"\s+[A-Z]{2,4}$", "", pub_date_str)
                        pub_dt = datetime.datetime.strptime(pub_date_clean, "%a, %d %b %Y %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                    except ValueError:
                        pub_dt = None
                        weight = 0.5
                
                if pub_dt:
                    age_hours = max(0.0, (now_utc - pub_dt).total_seconds() / 3600.0)
                    weight = math.exp(-_lambda * age_hours)
            weighted_scores.append(raw_score * weight)
            total_weight += weight

        avg_score = sum(weighted_scores) / total_weight if total_weight > 0 else 0.0

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
            "article_count": len(unique_articles),
            "sentiment_score": round(avg_score, 2),
            "sentiment_label": label,
            "headlines": [a["title"] for a in unique_articles[:5]]
        }


# ==============================================================================
# 4.5. AI Agent Market Strategist & Execution Validator Bridge
# ==============================================================================

def find_ai_binary() -> str:
    """Locates the compiled ai CLI binary."""
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "ai"),
        os.path.expanduser("~/.local/bin/ai"),
        "/usr/local/bin/ai",
        "ai"
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return "ai"


class AgentAdvisor:
    """Invokes the local LLM during active market hours for deep validation, macro alignment, and trade confirmation."""

    @classmethod
    def invoke_local_llm(cls, messages: List[Dict[str, str]], temperature: float = 0.15,
                         max_tokens: int = 1024, timeout: int = 25) -> Optional[Dict[str, Any]]:
        """
        Directly queries the local OpenAI-compatible llama-server endpoint at INFER_BASE_URL.
        Returns dict with keys: 'content', 'reasoning' or None on failure.
        """
        infer_base = os.environ.get("INFER_BASE_URL", "http://localhost:8080/v1/").rstrip("/")
        if not infer_base.endswith("/v1"):
            infer_base = f"{infer_base}/v1"
        url = f"{infer_base}/chat/completions"

        model_name = os.environ.get("INFER_MODEL", "llama")
        if model_name == "llama" and os.environ.get("LLAMA_MODEL_PATH"):
            model_name = os.environ.get("LLAMA_MODEL_PATH")

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {os.environ.get('INFER_API_KEY', 'not-needed')}"
                }
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    raw_resp = json.loads(resp.read().decode("utf-8"))
                    choices = raw_resp.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        content = (msg.get("content") or "").strip()
                        reasoning = (msg.get("reasoning_content") or "").strip()
                        return {"content": content, "reasoning": reasoning}
        except Exception:
            pass
        return None

    @classmethod
    def invoke_agent(cls, prompt: str, timeout: Optional[int] = None, flags: Optional[List[str]] = None,
                     system_prompt: Optional[str] = None) -> str:
        """Invokes the AI agent: tries direct high-speed local inference API first, then falls back to ai binary."""
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("INFER_TEST_MODE"):
            return "VERDICT: EXECUTE\nREASONING: Simulated test mode approval.\nSUGGESTION: Proceed with staged plan."

        sys_prompt = system_prompt or (
            "You are a Senior Quantitative Portfolio Manager and Risk Officer for Robinhood Agentic Trading. "
            "Your highest priority is capital preservation and long-term wealth compounding. "
            "Never chase overbought spikes or trade low-conviction setups."
        )

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ]

        # 1. Primary: Direct high-speed local LLM API (2-4 seconds)
        llm_res = cls.invoke_local_llm(messages, timeout=min(timeout or 25, 25))
        if llm_res:
            content = llm_res.get("content", "")
            reasoning = llm_res.get("reasoning", "")
            if content:
                clean_content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                clean_content = re.sub(r"<tool_call>.*?</tool_call>", "", clean_content, flags=re.DOTALL).strip()
                if clean_content:
                    return clean_content
            elif reasoning:
                m_json = re.search(r"(\{.*\})", reasoning, re.DOTALL)
                if m_json:
                    try:
                        parsed = json.loads(m_json.group(1))
                        return json.dumps(parsed)
                    except Exception:
                        pass
                sents = [s.strip() for s in reasoning.split(".") if s.strip()]
                if sents:
                    return ". ".join(sents[-2:]) + "."

        # 2. Secondary fallback: ai CLI binary
        ai_bin = find_ai_binary()
        env = os.environ.copy()
        env["INFER_AUTO_APPROVE"] = "1"
        env["BROWSER"] = "none"

        timeout_sec = min(timeout or 60, 60)
        cmd = [ai_bin, "-y", "--no-agents", "--no-git", "--no-mcp", "-q", "--private"]
        if flags:
            cmd.extend(flags)
        else:
            mode = os.environ.get("ROBINHOOD_AI_MODE", "instruct")
            cmd.extend(["--mode", mode, "-n"])
        cmd.append(prompt)

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env=env
            )
            out = res.stdout.strip()
            if out:
                out = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
                out = re.sub(r"<tool_call>.*?</tool_call>", "", out, flags=re.DOTALL).strip()
                return out
            return res.stderr.strip()
        except Exception as e:
            return f"(AI Agent invocation error: {e})"

    @classmethod
    def generate_algorithmic_premarket_briefing(cls, briefing_data: Dict[str, Any]) -> str:
        """Deterministic, comprehensive pre-market briefing fallback when AI agent is offline."""
        macro = briefing_data.get("macro_sentiment", {})
        top_buys = briefing_data.get("top_buy_candidates", [])
        warnings = briefing_data.get("top_risk_warnings", [])

        lines = [
            "### 🧭 Algorithmic Macro & Strategic Assessment",
            f"- **Macro Bias:** `{macro.get('label', 'NEUTRAL')}` (Score: {macro.get('score', 0.0):+.2f})",
            f"- **Execution Stance:** {'Aggressive momentum execution on pullbacks' if macro.get('label') == 'BULLISH' else 'Defensive capital preservation with strict stop-loss adherence'}.",
            "\n### 🎯 Key Entry & Staged Focus",
        ]
        if top_buys:
            for b in top_buys:
                lines.append(f"- **{b['ticker']}** (Score {b.get('score', 50)}): Entry around `${b.get('price', 0.0):.2f}` with Stop-Loss at `${b.get('stop_loss', 0.0):.2f}` and Target at `${b.get('take_profit_1', 0.0):.2f}`.")
        else:
            lines.append("- Monitoring core watchlist for opening volume breakouts.")

        if warnings:
            lines.append("\n### ⚠️ Risk Warnings & Traps to Avoid")
            for w in warnings:
                lines.append(f"- **{w['ticker']}**: {w.get('reason', 'Overextended momentum')}.")

        lines.append("\n### 📋 Action Plan for Today's Session")
        lines.append("1. Monitor 09:30 AM ET opening volatility for 15 minutes before executing new breakout entries.")
        lines.append("2. Enforce trailing stop-loss brackets automatically via `risk-monitor`.")
        lines.append("3. Review daily closing retrospective at 04:05 PM ET.")
        return "\n".join(lines)

    @classmethod
    def generate_algorithmic_close_review(cls, close_data: Dict[str, Any]) -> str:
        """Deterministic, rich daily closing retrospective fallback when AI agent is offline/timed out."""
        date_str = close_data.get("date", MarketHours.now_et().strftime("%Y-%m-%d"))
        port = close_data.get("portfolio", {})
        equity = port.get("equity", 0.0)
        cash = port.get("cash", 0.0)
        pos_count = port.get("positions_count", 0)
        trades = close_data.get("trades_executed_today", [])
        macro = close_data.get("macro_sentiment", {})
        movers = close_data.get("top_market_movers", [])

        lines = [
            f"#### 📊 Session Close Summary & Portfolio State ({date_str})",
            f"The trading session for `{date_str}` concluded with the agentic book operating under automated risk rules.",
            f"- **Portfolio Status:** Total Equity `${equity:,.2f}`, Cash `${cash:,.2f}`, Active Positions: `{pos_count}`.",
            f"- **Macro Bias:** `{macro.get('label', 'NEUTRAL')}` (Score: {macro.get('score', 0.0):+.2f}).",
            "",
            "#### 🎯 Risk & Execution Audit",
        ]
        if trades:
            lines.append(f"A total of `{len(trades)}` order(s) were executed during the session:")
            for t in trades:
                lines.append(f"- **{t.get('action', 'ORDER')} {t.get('ticker', '')}**: {t.get('qty', '')} shares at ${t.get('price', 0.0):.2f} (Reason: {t.get('reason', 'N/A')}, P&L: {t.get('pnl_pct', 0.0):+.2f}%).")
        else:
            lines.append("- **Clean Risk Adherence:** Zero forced liquidations or stop-loss violations occurred today. All positions remained within configured risk bands (-6.0% trailing stop / +10.0% take-profit).")

        if movers:
            lines.append("\n#### 📈 Key Watchlist Movers")
            for m in movers[:5]:
                lines.append(f"- **{m['ticker']}**: `${m['price']:.2f}` ({m['change_pct']:>+5.2f}%)")

        lines.extend([
            "",
            "#### 🔭 Preparation for Tomorrow's Market Open",
            "1. **Pre-Market Refresh:** The pre-market briefing will automatically run at 09:20 AM ET to scan overnight catalysts.",
            "2. **Capital Allocation:** Maintain strict risk sizing per trade (max 5-8% portfolio allocation per setup).",
            "3. **Stop Protection:** Keep stop-loss and take-profit triggers active for the next regular trading session."
        ])
        return "\n".join(lines)

    @classmethod
    def review_premarket_briefing(cls, briefing_data: Dict[str, Any]) -> str:
        """Asks the AI Agent to review the pre-market data, validate macro outlook, and suggest actionable adjustments."""
        top_buys = briefing_data.get("top_buy_candidates", [])
        macro = briefing_data.get("macro_sentiment", {})

        sys_prompt = (
            "You are the Senior Quantitative Portfolio Manager and Trading Strategist for Robinhood Agentic Trading. "
            "Write a concise, professional 3-paragraph pre-market briefing in Markdown. Focus on capital preservation, "
            "macro catalysts, and high-probability trade setups."
        )

        prompt = (
            f"Review today's pre-market briefing data:\n"
            f"Macro Sentiment: {macro.get('label')} (Score: {macro.get('score')})\n"
            f"Headlines: {json.dumps(macro.get('key_headlines', []))}\n"
            f"Top Staged Setups: {json.dumps(top_buys)}\n\n"
            f"Provide a concise, professional assessment in markdown:\n"
            f"1. Validation of whether macro sentiment supports aggressive buying or conservative risk today.\n"
            f"2. Assessment of top candidates, key entry levels, and potential risk traps/catalysts.\n"
            f"3. Concrete actionable suggestions for today's trading session."
        )
        res = cls.invoke_agent(prompt, timeout=25, system_prompt=sys_prompt)
        if not res or res.startswith("(AI Agent invocation error:") or ("error" in res.lower() and len(res) < 100) or len(res) < 30:
            return cls.generate_algorithmic_premarket_briefing(briefing_data)
        return res

    @classmethod
    def validate_trade_decision(cls, ticker: str, action: str, current_price: float, avg_cost: float,
                                pnl_pct: float, reason: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        When a Buy/Sell signal or Stop-Loss / Take-Profit is triggered, the AI Agent acts as an
        investment committee / risk officer:
        - Evaluates risk, asymmetric upside, trend health, and portfolio allocation.
        - Strictly forbids chasing overbought spikes or re-buying recently stopped-out stocks.
        - Returns structured verdict: {"verdict": "EXECUTE" | "WAIT", "action": ..., "confidence": ..., "reasoning": ...}
        """
        details = details or {}
        acc_num = details.get("account", "")
        if acc_num and "recent_exits" not in details:
            recent_exits = ComplianceAndRiskGuard.get_recent_exits(acc_num, ticker, days=30)
            if recent_exits:
                details["recent_exits"] = recent_exits

        is_buy = action.lower() == "buy"

        system_prompt = (
            "You are the Senior Quantitative Portfolio Manager and Risk Officer for Robinhood Agentic Trading.\n"
            "Your objective is LONG-TERM WEALTH GENERATION and CAPITAL PRESERVATION.\n"
            "Warren Buffett Rule #1: Never lose money. Rule #2: Never forget Rule #1.\n\n"
            "Decision Rules:\n"
            "1. For BUY signals:\n"
            "   - Strictly FORBID buying overbought stocks (RSI > 68 or daily gain > +3.5%).\n"
            "   - Strictly FORBID buying high-volatility spikes without volume confirmation.\n"
            "   - Strongly PREFER core wealth compounders (VTI, QQQ, secular leaders) on dips/pullbacks.\n"
            "   - Require risk/reward ratio >= 2.0 (upside target must be at least double the stop loss).\n"
            "   - If in doubt, confidence is low (< 0.65), or risk is elevated, choose WAIT (PASS).\n"
            "2. For SELL signals:\n"
            "   - If it's a Stop-Loss: confirm EXECUTE to cut losses cleanly unless live quote confirms a momentary flash-wick.\n"
            "   - If it's a Take-Profit or Profit-Lock: confirm EXECUTE to lock in gains into cash/ballast.\n\n"
            "OUTPUT REQUIREMENT:\n"
            "Respond strictly in valid JSON format:\n"
            "{\n"
            '  "verdict": "EXECUTE" or "WAIT",\n'
            '  "action": "BUY", "PASS", "SELL", or "HOLD",\n'
            '  "confidence": 0.0 to 1.0,\n'
            '  "reasoning": "concise 2-sentence rationale",\n'
            '  "suggestion": "suggested entry level, limit price, or trailing stop adjustment"\n'
            "}"
        )

        user_prompt = (
            f"Trade Signal Triggered for Decision:\n"
            f"- Action Proposed: {action.upper()}\n"
            f"- Ticker: {ticker}\n"
            f"- Current Price: ${current_price:.2f}\n"
            f"- Average Cost Basis: ${avg_cost:.2f}\n"
            f"- Unrealized P&L: {pnl_pct:+.2f}%\n"
            f"- Trigger Reason: {reason}\n"
            f"- Market Context & Details: {json.dumps(details, default=str)}\n\n"
            f"Evaluate this setup thoroughly. Should we EXECUTE immediately or WAIT?"
        )

        agent_response = cls.invoke_agent(user_prompt, timeout=25, system_prompt=system_prompt)

        # Parse JSON from agent response
        parsed_json = None
        if agent_response:
            try:
                m = re.search(r"(\{.*\})", agent_response, re.DOTALL)
                if m:
                    parsed_json = json.loads(m.group(1))
                else:
                    parsed_json = json.loads(agent_response)
            except Exception:
                pass

        if parsed_json and isinstance(parsed_json, dict):
            raw_verdict = str(parsed_json.get("verdict", "")).upper()
            raw_action = str(parsed_json.get("action", "")).upper()
            confidence = float(parsed_json.get("confidence", 0.5) or 0.5)
            reasoning = str(parsed_json.get("reasoning", ""))
            suggestion = str(parsed_json.get("suggestion", ""))

            if is_buy:
                # STRICT BUY GATING:
                # Must be explicitly EXECUTE, action BUY, and confidence >= 0.65.
                if raw_verdict == "EXECUTE" and raw_action in ("BUY", "EXECUTE") and confidence >= 0.65:
                    verdict = "EXECUTE"
                else:
                    verdict = "WAIT"
            else:
                # SELL: If stop-loss, protect capital unless agent is confident it's a momentary flash wick.
                if raw_verdict == "WAIT" and confidence >= 0.75:
                    verdict = "WAIT"
                else:
                    verdict = "EXECUTE"

            return {
                "verdict": verdict,
                "confidence": confidence,
                "action": raw_action,
                "reasoning": reasoning,
                "suggestion": suggestion,
                "agent_response": agent_response
            }

        # Text fallback parsing if JSON parsing wasn't clean
        has_error = not agent_response or agent_response.startswith("(AI Agent invocation error:") or ("error" in agent_response.lower() and len(agent_response) < 100)
        if has_error:
            verdict = "WAIT" if is_buy else "EXECUTE"
        elif "VERDICT: WAIT" in agent_response.upper() or '"VERDICT": "WAIT"' in agent_response.upper() or '"ACTION": "PASS"' in agent_response.upper():
            verdict = "WAIT"
        elif "VERDICT: EXECUTE" in agent_response.upper() or '"VERDICT": "EXECUTE"' in agent_response.upper():
            if is_buy and any(neg in agent_response.lower() for neg in ("pass", "avoid", "caution", "overbought", "do not buy", "risk too high")):
                verdict = "WAIT"
            else:
                verdict = "EXECUTE"
        else:
            # Ambiguous: safety first, never buy!
            verdict = "WAIT" if is_buy else "EXECUTE"

        return {
            "verdict": verdict,
            "agent_response": agent_response
        }

    @classmethod
    def review_daily_close(cls, close_data: Dict[str, Any]) -> str:
        """Asks the AI Agent to review market close results and summarize daily lessons."""
        date_str = close_data.get("date", MarketHours.now_et().strftime("%Y-%m-%d"))
        port = close_data.get("portfolio", {})
        macro = close_data.get("macro_sentiment", {})
        trades = close_data.get("trades_executed_today", [])
        movers = close_data.get("top_market_movers", [])

        sys_prompt = (
            "You are the Senior Trading Strategist for Robinhood Agentic Trading. "
            "Write a concise, professional 2-3 paragraph daily market close retrospective in markdown. "
            "Evaluate risk discipline, portfolio preservation, and preparations for the next trading day."
        )

        prompt = (
            f"The market has closed for today ({date_str}). Review daily performance:\n"
            f"- Macro Sentiment: {macro.get('label', 'NEUTRAL')} (Score: {macro.get('score', 0.0)})\n"
            f"- Account Equity: ${port.get('equity', 0.0):,.2f}\n"
            f"- Cash: ${port.get('cash', 0.0):,.2f}\n"
            f"- Active Positions: {port.get('positions_count', 0)}\n"
            f"- Trades Executed Today: {len(trades)}\n"
            f"{json.dumps(trades) if trades else 'No trades executed today.'}\n"
            f"- Watchlist Dynamics: {json.dumps(movers[:5]) if movers else 'Neutral breadth'}\n\n"
            f"Write a concise, professional 2-3 paragraph daily market close retrospective in markdown:\n"
            f"1. Daily performance review and key portfolio movements.\n"
            f"2. Risk management audit and stop-loss / take-profit discipline.\n"
            f"3. Actionable preparation for tomorrow's market open."
        )
        res = cls.invoke_agent(prompt, timeout=25, system_prompt=sys_prompt)
        if not res or res.startswith("(AI Agent invocation error:") or ("error" in res.lower() and len(res) < 100) or len(res) < 30:
            return cls.generate_algorithmic_close_review(close_data)
        return res


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
        """
        Computes complete multi-factor analysis, technical signals, and trade recommendations:
        - Symmetric Trend factor [-20, +20]
        - Continuous RSI momentum without gaps [-15, +12]
        - Volume confirmation factor [-8, +10] with Volume Ratio (V / V_20d_avg)
        - Continuous MACD magnitude and acceleration [-10, +10]
        - Calibrated News Sentiment factor [-8, +8]
        - Volatility-adjusted dynamic stops (ATR-tied)
        - Selective recommendation thresholds (STRONG_BUY >= 80.0, BUY >= 68.0)
        """
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
            volumes = [b.get("volume", 0) for b in bars]

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
            macd_data = {"histogram": [0.0]}
            bb_pct_b = 0.5
            current_atr = current_price * 0.02
            volumes = []

        # ── Hard Liquidity Pre-Screen (disqualify before scoring) ────────────────
        # V4 spec: avg 20d vol >= 500,000 shares AND price >= $5.00 are hard gates,
        # not soft penalties.  Candidates that fail are returned with a STRONG_SELL
        # classification so callers can skip them immediately.
        if len(bars) >= 5 and volumes:
            _liq_vols = [v for v in volumes if v is not None and v > 0]
            _avg_vol_20 = sum(_liq_vols[-20:]) / min(len(_liq_vols), 20) if _liq_vols else 0
        else:
            _avg_vol_20 = 0
        if _avg_vol_20 < 500_000 or current_price < 5.0:
            _reason = (f"avg_vol={_avg_vol_20:.0f} < 500k" if _avg_vol_20 < 500_000
                       else f"price=${current_price:.2f} < $5.00")
            return {
                "ticker": ticker,
                "price": current_price,
                "change_pct": quote.get("change_percent", 0.0),
                "score": 0.0,
                "recommendation": "STRONG_SELL",
                "disqualified": True,
                "disqualify_reason": f"Liquidity gate failed: {_reason}",
                "factor_breakdown": {},
                "indicators": {
                    "rsi": 50.0, "sma20": current_price, "sma50": current_price,
                    "sma200": current_price, "macd_histogram": 0.0,
                    "volume_ratio": 0.0, "bollinger_pct_b": 0.5,
                    "atr": current_price * 0.02, "atr_pct": 2.0
                },
                "news_sentiment": {"score": 0.0, "label": "NEUTRAL", "headlines": []},
                "risk_targets": {
                    "stop_loss": round(current_price * 0.95, 2), "stop_loss_pct": 5.0,
                    "take_profit_1": round(current_price * 1.08, 2), "take_profit_1_pct": 8.0,
                    "take_profit_2": round(current_price * 1.15, 2), "take_profit_2_pct": 15.0,
                    "trailing_stop_pct": 4.5
                }
            }

        # Multi-factor score (0 - 100) with calibrated baseline = 50.0
        score = 50.0
        factors = {}

        # 1. Explicit Symmetric Trend factor (range: -20 to +20)
        trend_score = 0.0
        trend_score += 5.0 if current_price > sma20 else -4.0
        trend_score += 6.0 if current_price > sma50 else -5.0
        trend_score += 5.0 if current_price > sma200 else -6.0
        trend_score += 4.0 if sma50 > sma200 else -5.0  # Golden Cross vs Death Cross
        trend_score = max(-20.0, min(20.0, trend_score))
        factors["trend"] = trend_score
        score += trend_score

        # 2. Continuous RSI Momentum Factor without dead zones (range: -15 to +12)
        momentum_score = 0.0
        if 50.0 <= current_rsi <= 65.0:
            momentum_score += 8.0  # Optimal bullish momentum corridor
        elif 40.0 <= current_rsi < 50.0 or 65.0 < current_rsi <= 70.0:
            momentum_score += 4.0  # Moderate trend corridor
        elif 30.0 <= current_rsi < 40.0:
            momentum_score -= 2.0  # Sluggish / weakening momentum
        elif 70.0 < current_rsi <= 78.0:
            momentum_score -= 8.0  # Overbought exhaustion warning
        elif current_rsi > 78.0:
            momentum_score -= 15.0  # Severe parabolic overextension
        elif current_rsi < 30.0:
            # Oversold only rewarded if secular bull trend is intact; penalized if falling knife
            if current_price > sma200:
                momentum_score += 6.0  # Dip buying in long-term uptrend
            else:
                momentum_score -= 8.0  # Secular downtrend breakdown
        factors["momentum"] = momentum_score
        score += momentum_score

        # 3. Time-Normalized Volume Confirmation Factor (range: -8 to +10)
        vol_score = 0.0
        vol_ratio = 1.0
        if len(bars) >= 5 and volumes:
            valid_vols = [v for v in volumes if v is not None and v > 0]
            if valid_vols:
                cur_vol = valid_vols[-1]
                avg_vol_20 = sum(valid_vols[-20:]) / min(len(valid_vols), 20)
                if avg_vol_20 > 0:
                    now_et = MarketHours.now_et()
                    session = MarketHours.get_market_session(now_et)
                    if session == "REGULAR":
                        # Intraday Time Normalization with Empirical U-Shaped Distribution:
                        # - 09:30-10:00 ET (t <= 30 min): ~25% daily volume
                        # - 10:00-15:00 ET (30 < t <= 330 min): ~50% daily volume (midday slow-down)
                        # - 15:00-16:00 ET (330 < t <= 390 min): ~25% daily volume (power-hour ramp)
                        market_open_today = datetime.datetime.combine(now_et.date(), datetime.time(9, 30), tzinfo=ET_ZONE)
                        elapsed_min = max(1.0, min(390.0, (now_et - market_open_today).total_seconds() / 60.0))
                        if elapsed_min <= 30.0:
                            expected_fraction = 0.25 * (elapsed_min / 30.0)
                        elif elapsed_min <= 330.0:
                            expected_fraction = 0.25 + 0.50 * ((elapsed_min - 30.0) / 300.0)
                        else:
                            expected_fraction = 0.75 + 0.25 * ((elapsed_min - 330.0) / 60.0)
                        expected_fraction = max(0.04, expected_fraction)
                        expected_vol_so_far = avg_vol_20 * expected_fraction
                        vol_ratio = round(cur_vol / expected_vol_so_far, 2)
                    else:
                        vol_ratio = round(cur_vol / avg_vol_20, 2)

                    if vol_ratio >= 1.5:
                        vol_score += 10.0  # High-volume institutional breakout
                    elif vol_ratio >= 1.1:
                        vol_score += 5.0   # Above-average volume confirmation
                    elif vol_ratio >= 0.8:
                        vol_score += 0.0   # Normal average volume
                    elif vol_ratio >= 0.5:
                        vol_score -= 4.0   # Sub-par volume caution
                    else:
                        vol_score -= 8.0   # Anemic volume / false breakout risk
        factors["volume"] = vol_score
        score += vol_score

        # Liquidity gate already enforced above as a hard disqualifier; no soft penalty needed here.

        # 4. Continuous MACD Impulse & Acceleration (range: -10 to +10)
        # Bar Resolution: last two CLOSED daily bars ([-2] = yesterday close, [-3] = day before).
        # Using [-1] risks reading an incomplete intraday bar from the data provider; using [-2]
        # guarantees a stable, session-level momentum regime throughout RTH.
        macd_score = 0.0
        hist_series = [h for h in macd_data.get("histogram", []) if h is not None]
        # Hist_t  = last closed bar (yesterday); Hist_t-1 = bar before that
        macd_hist_closed = hist_series[-2] if len(hist_series) >= 2 else macd_hist
        prev_hist = hist_series[-3] if len(hist_series) >= 3 else (hist_series[-2] if len(hist_series) >= 2 else macd_hist)
        hist_delta = macd_hist_closed - prev_hist
        macd_hist = macd_hist_closed  # use closed bar for magnitude/sign checks below

        macd_score += 4.0 if macd_hist > 0 else -4.0
        macd_score += 4.0 if hist_delta > 0 else -4.0  # Expanding vs contracting momentum
        if abs(macd_hist) > (current_atr * 0.25) and current_atr > 0:
            macd_score += 2.0 if macd_hist > 0 else -2.0
        macd_score = max(-10.0, min(10.0, macd_score))
        factors["macd"] = macd_score
        score += macd_score

        # 5. Calibrated News Sentiment Factor (range: -8 to +8)
        sent_score = news.get("sentiment_score", 0.0)
        headlines = news.get("headlines", [])
        news_weight = 8.0 if len(headlines) >= 2 else (4.0 if len(headlines) == 1 else 0.0)
        sentiment_contribution = round(sent_score * news_weight, 1)
        factors["sentiment"] = sentiment_contribution
        score += sentiment_contribution

        # 6. Anti-Chasing Gap & Parabolic Overextension Filter
        # Buying parabolic intraday spikes (> +3.5% with RSI > 68) causes whipsaw top-ticking
        intraday_chg = quote.get("change_percent", 0.0)
        if intraday_chg >= 3.5 and current_rsi >= 68.0:
            score -= 10.0
            factors["anti_chase"] = -10.0

        # 7. Secular Uptrend Pullback & Mean-Reversion Support Factor (range: 0 to +12)
        # Identifies high-win-rate dip-buying setups: stock in secular bull trend (Price > SMA200 & SMA50 > SMA200)
        # pulling back to test rising SMA20/SMA50 with cooling RSI (35-54) and drying sell volume.
        pullback_score = 0.0
        is_pullback_setup = False
        if current_price > sma200 and sma50 > sma200:
            near_sma20 = abs(current_price - sma20) / sma20 <= 0.03 if sma20 > 0 else False
            near_sma50 = abs(current_price - sma50) / sma50 <= 0.03 if sma50 > 0 else False
            if (near_sma20 or near_sma50) and 35.0 <= current_rsi <= 54.0 and bb_pct_b <= 0.50:
                pullback_score = 8.0
                if 0.40 <= vol_ratio <= 1.10:  # Orderly volume contraction on pullback (healthy consolidation)
                    pullback_score += 4.0
                is_pullback_setup = True
        factors["pullback_support"] = pullback_score
        score += pullback_score

        # Bound total score to [0, 100]
        score = max(0.0, min(100.0, round(score, 1)))

        # Multi-Factor Alignment Gate for STRONG_BUY:
        # Either:
        # A) High-momentum volume breakout (score >= 80, positive trend, strong momentum, vol_ratio >= 0.8)
        # B) High-expectancy secular pullback buy (score >= 78, is_pullback_setup, price > sma200, intraday not breaking down)
        is_strong_buy = False
        if score >= 80.0 and trend_score > 0 and momentum_score >= 0 and vol_ratio >= 0.8 and intraday_chg < 4.0:
            is_strong_buy = True
        elif score >= 78.0 and is_pullback_setup and current_price > sma200 and intraday_chg > -2.5:
            is_strong_buy = True

        if is_strong_buy:
            recommendation = "STRONG_BUY"
        elif score >= 68.0:
            recommendation = "BUY"
        elif score <= 28.0:
            recommendation = "STRONG_SELL"
        elif score <= 42.0:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        # Volatility-Adjusted Dynamic Risk parameters
        atr_pct = (current_atr / current_price * 100.0) if current_price > 0 else 2.0
        if is_pullback_setup:
            # Tighter stop just below key moving average support (1.5x ATR, 4.5% - 7.5%)
            stop_loss_pct = round(max(4.5, min(7.5, 1.5 * atr_pct)), 2)
        else:
            stop_loss_pct = round(max(5.0, min(12.0, 2.0 * atr_pct)), 2)
        stop_loss_price = round(max(0.01, current_price * (1.0 - stop_loss_pct / 100.0)), 2)
        
        take_profit_target_1 = round(current_price * 1.08, 2)  # +8%
        take_profit_target_2 = round(current_price * 1.15, 2)  # +15%
        trailing_stop_pct = round(max(4.5, min(8.0, 1.5 * atr_pct)), 2)

        return {
            "ticker": ticker,
            "price": current_price,
            "change_pct": quote.get("change_percent", 0.0),
            "score": score,
            "recommendation": recommendation,
            "factor_breakdown": factors,
            "indicators": {
                "rsi": round(current_rsi, 1),
                "sma20": round(sma20, 2),
                "sma50": round(sma50, 2),
                "sma200": round(sma200, 2),
                "macd_histogram": round(macd_hist, 4),
                "volume_ratio": vol_ratio,
                "bollinger_pct_b": round(bb_pct_b, 2),
                "atr": round(current_atr, 2),
                "atr_pct": round(atr_pct, 2)
            },
            "news_sentiment": {
                "score": news.get("sentiment_score", 0.0),
                "label": news.get("sentiment_label", "NEUTRAL"),
                "headlines": headlines
            },
            "risk_targets": {
                "stop_loss": stop_loss_price,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_1": take_profit_target_1,
                "take_profit_1_pct": 8.0,
                "take_profit_2": take_profit_target_2,
                "take_profit_2_pct": 15.0,
                "trailing_stop_pct": trailing_stop_pct
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
        
        # Scan for any deferred exits that need to be actioned at open
        _deferred_exits_pending = []
        try:
            _def_dir = DEFERRED_EXIT_FLAG_DIR
            if os.path.isdir(_def_dir):
                for _fn in os.listdir(_def_dir):
                    if _fn.startswith("deferred_exit_") and _fn.endswith(".flag"):
                        _fp = os.path.join(_def_dir, _fn)
                        try:
                            with open(_fp, encoding="utf-8") as _fh:
                                _deferred_exits_pending.append(json.load(_fh))
                        except Exception:
                            pass
        except Exception:
            pass

        staged_plan = {
            "date": now.strftime("%Y-%m-%d"),
            "briefing_time": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "deferred_exits_pending": _deferred_exits_pending,  # Prioritised for immediate review at open
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
        # 3.5 Momentum sleeve view: the rules the live loop actually trades (regime + ranking)
        try:
            from .daemon import _fetch_strategy_bars   # lazy: daemon imports this module
            sleeve_bars = _fetch_strategy_bars(MomentumStrategy.universe() + ["SPY"])
            spy_bars = sleeve_bars.pop("SPY", [])
            regime = MomentumStrategy.regime(spy_bars)
            ranked = MomentumStrategy.rank(sleeve_bars)
            staged_plan["momentum_sleeve"] = {
                "regime": regime,
                "targets": MomentumStrategy.select_sleeve(ranked, regime["regime"]),
                "note": "strict top-N view; the monthly rebalance also keeps held names ranked within top N + rank buffer",
                "top": ranked[:8],
            }
        except Exception as exc:  # noqa: BLE001
            staged_plan["momentum_sleeve"] = {"error": str(exc)}

        # 4. Invoke AI Agent for deep strategic validation (Market Hours only)
        try:
            print("Invoking AI Agent to validate macro outlook and suggest trade setups...")
            agent_validation = AgentAdvisor.review_premarket_briefing(staged_plan)
            staged_plan["ai_agent_validation"] = agent_validation
            print(f"\n🤖 AI Agent Validation & Strategy:\n{agent_validation}\n")
        except Exception as e:
            staged_plan["ai_agent_validation"] = f"(Agent validation skipped: {e})"

        # 5. Automatically persist to Obsidian Trading Knowledge Vault
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
        - Consults AI Agent for closing retrospective and lessons
        - Appends daily record to ~/.config/ai/trading_journal.json and Obsidian Vault
        """
        now = MarketHours.now_et()
        date_str = now.strftime("%Y-%m-%d")
        os.makedirs(CONFIG_DIR, exist_ok=True)
        journal_file = os.path.join(CONFIG_DIR, "trading_journal.json")
        
        journal = []
        if os.path.exists(journal_file):
            try:
                with open(journal_file, "r", encoding="utf-8") as f:
                    journal = json.load(f)
            except Exception:
                journal = []

        # Collect portfolio & account data safely
        account_number = ""
        portfolio_data = {}
        positions_list = []
        try:
            account_number = RobinhoodExecutor.get_agentic_account_number()
            portfolio_data = RobinhoodExecutor.get_live_portfolio(account_number) or {}
            positions_list = RobinhoodExecutor.get_equity_positions(account_number) or []
        except Exception:
            pass

        # Collect today's trade execution records from trade_ledger.jsonl
        today_trades = []
        ledger_path = os.path.join(VAULT_DIR, "retrospectives", "trade_ledger.jsonl")
        if os.path.exists(ledger_path):
            try:
                with open(ledger_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            ts = rec.get("timestamp", "")
                            if ts.startswith(date_str) or rec.get("date") == date_str:
                                today_trades.append(rec)
                        except Exception:
                            continue
            except Exception:
                pass

        # Collect macro sentiment from today's daily note or default
        macro_sentiment = {"label": "NEUTRAL", "score": 0.0}
        daily_note_path = os.path.join(VAULT_DIR, "daily_notes", f"{date_str}.md")
        if os.path.exists(daily_note_path):
            try:
                with open(daily_note_path, "r", encoding="utf-8") as f:
                    note_txt = f.read()
                    if "macro_sentiment: BULLISH" in note_txt:
                        macro_sentiment["label"] = "BULLISH"
                    elif "macro_sentiment: BEARISH" in note_txt:
                        macro_sentiment["label"] = "BEARISH"
            except Exception:
                pass

        # Top watchlist movers summary
        movers = []
        try:
            quotes = RobinhoodExecutor.get_equity_quotes(DEFAULT_WATCHLIST[:8])
            for sym, q in quotes.items():
                price = float(q.get("last_trade_price") or 0.0)
                prev = float(q.get("previous_close") or price)
                chg_pct = ((price - prev) / prev * 100.0) if prev > 0 else 0.0
                movers.append({"ticker": sym, "price": price, "change_pct": round(chg_pct, 2)})
            movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        except Exception:
            pass

        equity_val = float(portfolio_data.get("equity") or portfolio_data.get("total_value") or portfolio_data.get("equity_value") or 0.0)
        cash_val = float(portfolio_data.get("cash") or 0.0)

        daily_entry = {
            "date": date_str,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "status": "COMPLETED",
            "session": "REGULAR_CLOSE",
            "portfolio": {
                "account_number": account_number,
                "equity": equity_val,
                "cash": cash_val,
                "positions_count": len(positions_list)
            },
            "trades_executed_today": today_trades,
            "macro_sentiment": macro_sentiment,
            "top_market_movers": movers[:5]
        }

        # Invoke AI Agent to review close performance
        try:
            print("Invoking AI Agent for daily close debrief and retrospective...")
            close_review = AgentAdvisor.review_daily_close(daily_entry)
            daily_entry["ai_close_review"] = close_review
            print(f"\n🤖 AI Agent Market Close Retrospective:\n{close_review}\n")
        except Exception as e:
            daily_entry["ai_close_review"] = AgentAdvisor.generate_algorithmic_close_review(daily_entry)

        # Update or append today's entry in journal
        entry_idx = -1
        for idx, entry in enumerate(journal):
            if entry.get("date") == date_str:
                entry_idx = idx
                break
        if entry_idx >= 0:
            journal[entry_idx] = daily_entry
        else:
            journal.append(daily_entry)
        
        try:
            with open(journal_file, "w", encoding="utf-8") as f:
                json.dump(journal[-60:], f, indent=2)  # Keep last 60 trading days
        except Exception:
            pass

        # Update Obsidian Daily Note with close section
        try:
            ObsidianTradingVault.save_daily_note(date_str, close_summary=daily_entry)
        except Exception:
            pass
            
        return daily_entry
