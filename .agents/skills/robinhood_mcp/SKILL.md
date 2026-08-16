---
name: robinhood-mcp
description: CRITICAL — when the user asks to buy or sell stocks, rebalance a portfolio, analyze market trends, track financial news, or write automated stock trading agent loops: Guidelines and tools for Robinhood's official Model Context Protocol (MCP) server, market hours scheduling, technical indicators, and strict financial safety limits.
---

# Robinhood Stock Trading, Market Hours Analysis & Profit Maximization

Use this skill when the user requests to trade equities, query portfolio holdings, fetch real-time market quotes, search financial news, analyze technical indicators, or run automated trading loops during US market hours.

---

## 1. Official Robinhood Agentic MCP Setup

Robinhood supports official Model Context Protocol (MCP) integrations for automated agentic trading:
*   **MCP Server Endpoint**: `https://agent.robinhood.com/mcp/trading`
*   **Config Entry (`mcp.json`)**:
    ```json
    {
      "mcpServers": {
        "robinhood": {
          "command": "npx",
          "args": ["-y", "mcp-remote", "https://agent.robinhood.com/mcp/trading", "--silent"],
          "env": {
            "BROWSER": "none"
          }
        }
      }
    }
    ```
*   **Stored Credentials & Autonomous Execution**: OAuth credentials are already stored and active. The agent MUST ALWAYS execute silently with `BROWSER=none` and NEVER trigger manual browser authentication (`./robinhood_trader.py auth`) or ask the user to re-login during automated or background workflows.
*   **Default Account**: `837546068` (Agentic Sandbox Account).
*   **Security Principle**: All trading actions run against dedicated **Agentic Sandbox Accounts** completely segregated from primary investment or retirement accounts.
*   **Safety Limits**: Never execute trades exceeding the allocated sandbox budget.

---

## 2. Built-in Tools & CLI (`robinhood_trader.py`)

The harness includes a complete local financial analysis, news sentiment, and algorithmic trading suite in [`robinhood_trader.py`](file:///home/dzyla/ai-buddy/robinhood_trader.py):

| Command | Description | Example |
| :--- | :--- | :--- |
| `status` | Checks US market open/closed status, session, Eastern time | `./robinhood_trader.py status` |
| `portfolio` | Queries live portfolio holdings, cost basis, and unrealized P&L using stored credentials | `./robinhood_trader.py portfolio` |
| `analyze <tickers...>` | Multi-factor analysis: RSI, SMA (20/50/200), MACD, Bollinger Bands, ATR, news sentiment, stop loss & take-profit targets | `./robinhood_trader.py analyze NVDA AAPL MSFT` |
| `scan [watchlist]` | Scans ticker universe and ranks highest-conviction opportunities | `./robinhood_trader.py scan` |
| `news <ticker/query>` | Searches latest financial news and computes sentiment score (-1.0 to +1.0) | `./robinhood_trader.py news TSLA` |
| `rebalance` | Evaluates portfolio holdings against risk limits and stop-loss targets | `./robinhood_trader.py rebalance` |
| `monitor [--auto-trade]` | Autonomous market hours daemon (9:30 AM - 4:00 PM ET) | `./robinhood_trader.py monitor --interval 60` |
| `service <start\|stop\|restart\|status\|logs>` | Manages the systemd user background service | `./robinhood_trader.py service status` |

---

## 3. US Market Hours & Calendar Rules

Regular Trading Hours on NYSE and NASDAQ are:
*   **Regular Trading Hours (RTH)**: Monday through Friday, 9:30 AM to 4:00 PM US Eastern Time (ET).
*   **Pre-Market**: 4:00 AM to 9:30 AM ET.
*   **After-Hours**: 4:00 PM to 8:00 PM ET.
*   **Market Holidays**: New Year's Day, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, July 4, Labor Day, Thanksgiving, Christmas Day.

When executing or scheduling autonomous workflows:
- Only execute active high-frequency rebalancing during **Regular Hours** (`09:30 - 16:00 ET`).
- Outside regular hours, schedule deferred checks using `schedule_task` or let `robinhood_trader.py monitor` sleep until 09:30 ET of the next trading day.

---

## 4. Profit Maximization & Risk Management Rules

> [!CAUTION]
> Trading involves real financial risk. Adhere strictly to the following rules:

1. **Stop-Loss Enforcement**:
   - Every purchase MUST have a defined stop loss (default: -5.0% from entry, or 2x ATR).
   - If an asset hits its stop-loss level, exit immediately without hesitation.
2. **Tiered Take-Profit & Trailing Stop**:
   - Take 50% profit when position reaches +8% to +10%.
   - Trail remaining 50% with a 3.5% trailing stop above entry to ride prolonged momentum waves.
3. **Portfolio Diversification & Cash Buffer**:
   - Max single-stock allocation: 15% of total portfolio value.
   - Maintain at least 15% cash reserve buffer for market drawdowns and dip buying.
4. **Order Execution & Slippage**:
   - Always use **Limit Orders** (with limit price set at or near bid/ask) rather than unconstrained Market Orders to prevent slippage.
5. **Human Verification (Plan / Manual Modes)**:
   - When running under `--plan` or `--manual`, present the proposed trades (Ticker, Action, Shares, Limit Price, Estimated Total, Risk Target) to the user and obtain confirmation before calling mutating trade endpoints.
