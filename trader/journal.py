"""Obsidian-compatible trading notes and the trade ledger."""

import os
import json
from typing import List, Dict, Any, Optional

from .config import VAULT_DIR
from .market_hours import MarketHours


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
        """Creates or updates an Obsidian Daily Trading Note, preserving existing content."""
        cls.init_vault()
        filepath = os.path.join(VAULT_DIR, "daily_notes", f"{date_str}.md")
        
        existing_content = ""
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing_content = f.read()
            except Exception:
                existing_content = ""

        # If file already exists and we are only appending / updating close_summary or intraday_events
        if existing_content and not briefing:
            close_header = "## 🔔 Market Close Summary & P&L Review"
            if close_header in existing_content:
                base_content = existing_content.split(close_header)[0].rstrip()
            else:
                base_content = existing_content.rstrip()

            lines = [base_content]
            if intraday_events:
                lines.append("\n## ⚡ Intraday Executions & Trailing Stop Updates")
                for ev in intraday_events:
                    lines.append(f"- {ev}")
            if close_summary:
                lines.append("\n## 🔔 Market Close Summary & P&L Review")
                lines.append(f"- **Status:** `{close_summary.get('status', 'COMPLETED')}`")
                lines.append(f"- **Timestamp:** {close_summary.get('timestamp', '')}")
                if close_summary.get("ai_close_review"):
                    lines.append("\n### 🤖 AI Market Close Retrospective")
                    lines.append(close_summary["ai_close_review"])
            
            content = "\n".join(lines).strip() + "\n"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return filepath

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
        
        sleeve = briefing.get("momentum_sleeve") or {}
        if sleeve and not sleeve.get("error"):
            regime = sleeve.get("regime", {}) or {}
            lines.append("\n### 🧲 Momentum Sleeve (live strategy)")
            lines.append(f"- **Regime:** `{regime.get('regime', '?')}` (SPY {regime.get('close', '?')} vs SMA200 {regime.get('sma200', '?')})")
            lines.append(f"- **Sleeve targets this month:** {', '.join('[[' + t + ']]' for t in sleeve.get('targets', [])) or 'CASH (regime gate)'}")
            lines.append("| # | Ticker | Price | 6m return | Eligible |")
            lines.append("| :---: | :--- | ---: | ---: | :--- |")
            for i, r in enumerate(sleeve.get("top", []), 1):
                mom = f"{r['momentum_pct']:+.1f}%" if r.get("momentum_pct") is not None else "n/a"
                lines.append(f"| {i} | [[{r['symbol']}]] | ${r.get('price', 0):.2f} | {mom} | {r.get('reason', '')} |")

        if briefing.get("ai_agent_validation"):
            lines.append("\n## 🤖 AI Strategist Pre-Market Validation & Plan")
            lines.append(briefing["ai_agent_validation"])

        if intraday_events:
            lines.append("\n## ⚡ Intraday Executions & Trailing Stop Updates")
            for ev in intraday_events:
                lines.append(f"- {ev}")
                
        if close_summary:
            lines.append("\n## 🔔 Market Close Summary & P&L Review")
            lines.append(f"- **Status:** `{close_summary.get('status', 'COMPLETED')}`")
            lines.append(f"- **Timestamp:** {close_summary.get('timestamp', '')}")
            if close_summary.get("ai_close_review"):
                lines.append("\n### 🤖 AI Market Close Retrospective")
                lines.append(close_summary["ai_close_review"])
            
        content = "\n".join(lines) + "\n"
        with open(filepath, "w", encoding="utf-8") as f:
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
    def save_rebalance_note(cls, month: str, state: Dict[str, Any]) -> str:
        """Records the monthly momentum-sleeve rebalance decision (regime, ranking, orders)."""
        cls.init_vault()
        filepath = os.path.join(VAULT_DIR, "retrospectives", f"rebalance_{month}.md")
        regime = state.get("regime", {}) or {}
        lines = [
            "---", f"month: {month}", f"date: {state.get('date', '')}", f"regime: {regime.get('regime', '?')}",
            f"dry_run: {state.get('dry_run', False)}", "type: momentum-rebalance", "tags: [trading, rebalance, robinhood-agentic]", "---\n",
            f"# 🧲 Momentum Sleeve Rebalance: {month}\n",
            f"- **Regime:** `{regime.get('regime', '?')}` (SPY {regime.get('close', '?')} vs SMA200 {regime.get('sma200', '?')})",
            f"- **Equity / Cash at decision:** ${state.get('equity', 0):,.2f} / ${state.get('cash', 0):,.2f}",
            f"- **Sleeve targets:** {', '.join('[[' + t + ']]' for t in state.get('targets', [])) or (state['park'] + ' (bear park)' if state.get('park') else 'CASH (regime gate)')}",
            f"- **Hold list (never sold):** {', '.join(state.get('hold', [])) or 'none'}\n",
            "## Ranking (6-month momentum)",
            "| # | Ticker | Price | 6m return | SMA200 | Eligible |",
            "| :---: | :--- | ---: | ---: | ---: | :--- |",
        ]
        for i, r in enumerate(state.get("ranking", []), 1):
            mom = f"{r['momentum_pct']:+.1f}%" if r.get("momentum_pct") is not None else "n/a"
            lines.append(f"| {i} | [[{r['symbol']}]] | ${r.get('price', 0):.2f} | {mom} | {r.get('sma200') or 'n/a'} | {r.get('reason', '')} |")
        lines.append("\n## Planned orders")
        for sl in state.get("sells_pending", []) + state.get("sells_done", []):
            lines.append(f"- SELL {sl['symbol']} {sl['quantity']} sh ({sl['reason']})")
        for b in state.get("buys_pending", []) + state.get("buys_done", []):
            lines.append(f"- BUY {b['symbol']} ${b['dollar_amount']:.2f} ({b['reason']})")
        if not (state.get("sells_pending") or state.get("sells_done") or state.get("buys_pending") or state.get("buys_done")):
            lines.append("- none (book already on target)")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
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
