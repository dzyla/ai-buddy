---
name: robinhood-mcp
description: CRITICAL — when the user asks to buy or sell stocks, rebalance a portfolio, analyze market trends, track financial news, or write automated stock trading agent loops: Guidelines and tools for Robinhood's official Model Context Protocol (MCP) server, market hours scheduling, technical indicators, and strict financial safety limits.
---

# Robinhood Stock Trading, Market Hours Analysis & Portfolio Management

Use this skill when the user requests to trade equities, query portfolio holdings, fetch real-time market quotes, search financial news, analyze technical indicators, or run automated trading loops during US market hours.

---

## 1. Official Robinhood MCP & Account Architecture

Robinhood provides official Model Context Protocol (MCP) integrations:
*   **MCP Server Endpoint**: `https://agent.robinhood.com/mcp/trading`
*   **Config (`mcp.json`)**: Configured via `npx -y mcp-remote https://agent.robinhood.com/mcp/trading --silent` with `BROWSER=none`.
*   **Stored Credentials**: OAuth credentials are authenticated and cached locally. The agent executes silently without browser popups.

### User Account Layout:
When prompting or querying the user's account, call `robinhood__get_accounts` (or `./robinhood_trader.py accounts`) to inspect the user's brokerage accounts:
1.  **Agentic Sandbox Account**: `517198354` (nickname: `Agentic`, type: `limited_margin`, `agentic_allowed: true`). **ALL automated orders (`place_equity_order`, `review_equity_order`) MUST target this account.**
2.  **Primary Individual Margin Account**: `837546068` (type: `margin`, default account, `agentic_allowed: false`). Read-only inspection (`get_portfolio`, `get_equity_positions`).
3.  **Roth IRA Account**: `422982744` (type: `cash`, `brokerage_account_type: ira_roth`, `agentic_allowed: false`). Read-only inspection.

---

## 2. Direct MCP Tools & Local CLI Suite

### Available Robinhood MCP Tools:
In `ai`, official Robinhood tools are prefixed with `robinhood__`:

| MCP Tool | Purpose | Key Parameters |
| :--- | :--- | :--- |
| `robinhood__get_accounts` | List user accounts & discover agentic vs primary accounts | `{}` |
| `robinhood__get_portfolio` | Query balance, equity value, cash, buying power | `{"account_number": "517198354"}` |
| `robinhood__get_equity_positions` | Fetch active equity holdings & quantities | `{"account_number": "517198354"}` |
| `robinhood__get_equity_quotes` | Live real-time market quotes, bid/ask, previous close | `{"symbols": ["AAPL", "NVDA"]}` |
| `robinhood__get_equity_historicals` | Fetch historical OHLCV daily/intraday bars | `{"symbols": ["AAPL"], "interval": "day", "start_time": "2026-05-01T00:00:00Z"}` |
| `robinhood__get_equity_technical_indicators` | Server-side calculated RSI, MACD, SMA, Bollinger Bands | `{"symbol": "AAPL", "type": "rsi", "interval": "day", "start_time": "..."}` |
| `robinhood__review_equity_order` | Simulate a stock order without placing | `{"account_number": "517198354", "symbol": "AAPL", "side": "buy", "type": "market", "dollar_amount": "50"}` |
| `robinhood__place_equity_order` | Execute real equity order on agentic account | `{"account_number": "517198354", "symbol": "AAPL", "side": "buy", "type": "market", "dollar_amount": "50"}` |

### Built-in Analysis CLI (`robinhood_trader.py`):
[`robinhood_trader.py`](file:///home/dzyla/ai-buddy/robinhood_trader.py) provides a fast, token-efficient command suite that automatically saves full datasets to disk (`~/.cache/ai/trading/`) and returns concise digests (<200 tokens) to keep model context lean and avoid overthinking:

| Command | Description | Example |
| :--- | :--- | :--- |
| `summary [account]` | Compact executive summary (<200 tokens), top holdings, winners/losers, risk score & file links | `./robinhood_trader.py summary` |
| `audit [account]` | Quantitative health score (0-100), concentration cap checks, semi overlap, dead-money & dust audit | `./robinhood_trader.py audit` |
| `harvest-losses [account]` | Step-by-step tax-loss harvesting candidates, harvestable dollar savings, wash-sale safety | `./robinhood_trader.py harvest-losses` |
| `rebalance-plan [account]` | Concrete 4-step rebalance plan: dead money liquidation, dust cleanup, winner trims, cash buffer | `./robinhood_trader.py rebalance-plan` |
| `export [account]` | Explicitly exports portfolio JSON, CSV, and audit datasets to `~/.cache/ai/trading/` | `./robinhood_trader.py export` |
| `portfolio [account]` | Live positions table (supports `--summary`, `--filter <losers\|winners\|dust\|dead-money>`, `--top <N>`, `--json`, `--csv`) | `./robinhood_trader.py portfolio --filter losers` |
| `accounts` | Queries all authorized brokerage accounts & agentic permissions | `./robinhood_trader.py accounts` |
| `analyze <tickers...>` | Multi-factor analysis: live price, RSI, SMA (20/50/200), MACD, sentiment & risk targets | `./robinhood_trader.py analyze NVDA AAPL MSFT` |
| `status` | Checks US market open/closed status, session, Eastern time | `./robinhood_trader.py status` |
| `scan [watchlist]` | Scans ticker universe and ranks highest-conviction opportunities | `./robinhood_trader.py scan` |
| `news <ticker/query>` | Searches latest financial news and computes sentiment score (-1.0 to +1.0) | `./robinhood_trader.py news TSLA` |
| `monitor [--auto-trade]` | Autonomous market hours daemon (9:30 AM - 4:00 PM ET) | `./robinhood_trader.py monitor --interval 60` |
| `service <start\|stop\|restart\|status\|logs>` | Manages the systemd user background service | `./robinhood_trader.py service status` |

### Local Data Offloading Architecture:
To prevent context overflow, `robinhood_trader.py` automatically writes full datasets to disk on every query:
- **`~/.cache/ai/trading/portfolio_<account>.json`**: Complete structured JSON with account balances, positions, weights, and P&L.
- **`~/.cache/ai/trading/portfolio_<account>.csv`**: Tabular CSV (`symbol,quantity,average_buy_price,current_price,current_value,cost_basis,unrealized_pnl,unrealized_pnl_pct,weight_pct,type`) for high-speed parsing via `grep`, `awk`, or Python.
- **`~/.cache/ai/trading/audit_<account>.json`**: Quantitative audit findings, risk flags, and optimization targets.
Agents should call `./robinhood_trader.py summary` or `./robinhood_trader.py audit` by default, and query the CSV/JSON directly when specific row filtering is needed.

---

## 3. US Market Hours & Calendar Rules

Regular Trading Hours on NYSE and NASDAQ:
*   **Regular Trading Hours (RTH)**: Monday through Friday, 9:30 AM to 4:00 PM US Eastern Time (ET).
*   **Pre-Market**: 4:00 AM to 9:30 AM ET.
*   **After-Hours**: 4:00 PM to 8:00 PM ET.
*   **Market Holidays**: New Year's Day, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, July 4, Labor Day, Thanksgiving, Christmas Day.

When executing or scheduling autonomous workflows:
- Only execute active rebalancing/orders during **Regular Hours** (`09:30 - 16:00 ET`).
- Outside regular hours, schedule deferred checks using `schedule_task` or let `robinhood_trader.py monitor` sleep until 09:30 ET of the next trading day.

---

## 4. Profit Maximization & Risk Management Rules

> [!CAUTION]
> Trading involves real financial risk. Adhere strictly to the following rules:

1. **Stop-Loss Enforcement**:
   - Every purchase MUST have a defined stop loss (default: -5.0% from entry, or 2x ATR).
   - If an asset hits its stop-loss level, exit immediately.
2. **Tiered Take-Profit & Trailing Stop**:
   - Take 50% profit when position reaches +8% to +10%.
   - Trail remaining 50% with a 3.5% trailing stop above entry to ride prolonged momentum waves.
3. **Portfolio Diversification & Cash Buffer**:
   - Max single-stock allocation: 15% of total portfolio value.
   - Maintain at least 15% cash reserve buffer for market drawdowns and dip buying.
4. **Order Execution & Accounts**:
   - Always verify the target account has `agentic_allowed: true` (`517198354`).
   - Prefer limit orders to prevent slippage.
5. **Human Verification (Plan / Manual Modes)**:
   - When running under `--plan` or `--manual`, present the proposed trades (Ticker, Action, Shares/Dollar, Limit Price, Estimated Total, Risk Target) to the user and obtain confirmation before placing orders.
