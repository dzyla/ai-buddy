---
name: robinhood-mcp
description: CRITICAL — when the user asks to buy or sell stocks, rebalance a portfolio, analyze market trends, track financial news, or write automated stock trading agent loops: Guidelines and tools for Robinhood's official Model Context Protocol (MCP) server, market hours scheduling, technical indicators, and strict financial safety limits.
---

# Robinhood Stock Trading, Market Hours Analysis & Portfolio Management

Use this skill when the user requests to trade equities, query portfolio holdings, fetch real-time market quotes, search financial news, analyze technical indicators, inspect trading reports/history, or run automated trading loops during US market hours.

---

## 1. Official Robinhood MCP & Account Architecture

Robinhood provides official Model Context Protocol (MCP) integrations:
*   **MCP Server Endpoint**: `https://agent.robinhood.com/mcp/trading`
*   **Config (`mcp.json`)**: Configured via `npx -y mcp-remote https://agent.robinhood.com/mcp/trading` with `BROWSER=none`.
*   **Stored Credentials**: OAuth credentials are authenticated and cached locally. The agent executes silently without browser popups.

### User Account Layout & Financial Safety:
When prompting or querying the user's account, call `robinhood__get_accounts` (or `./robinhood_trader.py accounts`) to inspect the user's brokerage accounts:
1.  **Agentic Sandbox Account**: `517198354` (nickname: `Agentic`, type: `limited_margin`, `agentic_allowed: true`). **ALL automated orders (`place_equity_order`, `review_equity_order`) MUST target this account.** You can specify `agentic` or `517198354` in CLI commands (e.g. `./robinhood_trader.py summary agentic`, `./robinhood_trader.py orders agentic`).
2.  **Primary Individual Margin Account**: `837546068` (type: `margin`, default account, `agentic_allowed: false`). Protected against automated mutation; read-only inspection (`get_portfolio`, `get_equity_positions`).
3.  **Roth IRA Account**: `422982744` (type: `cash`, `brokerage_account_type: ira_roth`, `agentic_allowed: false`). Read-only inspection.

---

## 2. Schedule & Timezone Breakdown (Mountain Time MT vs Eastern Time ET)

The US Stock Market (NYSE / NASDAQ) operates on **US Eastern Time (ET)**. For users in **Mountain Time (MT: MDT / MST)**:

| Daily Phase | Mountain Time (MT) | Eastern Time (ET) | Autonomous Engine Action |
| :--- | :---: | :---: | :--- |
| **Pre-Market Briefing** | **07:20 AM MT** | 09:20 AM ET | Scans watchlist, checks macro sentiment, generates staged trade setups & creates Obsidian daily note |
| **Market Open** | **07:30 AM MT** | 09:30 AM ET | Opening bell; reviews staged orders and opens live session pulse |
| **Regular Trading Hours (RTH)** | **07:30 AM – 02:00 PM MT** | 09:30 AM – 04:00 PM ET | Active market monitoring (every 60s); monitors stop-losses, take-profits, and breakout opportunities |
| **Market Close Summary** | **02:00 PM – 02:15 PM MT** | 04:00 PM – 04:15 PM ET | Compiles daily closing summary, updates Obsidian daily note, and logs to `trading_journal.json` |
| **Off-Hours / Power Save** | **02:15 PM – 07:20 AM MT** (Nights/Weekends/Holidays) | 04:15 PM – 09:20 AM ET | Sleeps until the next trading day's pre-market briefing |

`MarketHours.now_et()` internally converts to Eastern Time (`America/New_York`), so all scheduling logic automatically adjusts for Daylight Saving Time and local timezone offsets.

---

## 3. Autonomous Execution & Background Service Management

The automated trader runs as a systemd user daemon (`robinhood-trader.service`):

### How It Executes:
- **Continuous Lifecycle**: The service runs `python3 robinhood_trader.py monitor --interval 60`.
- **Automatic Transitions**: Automatically transitions between Pre-Market -> Regular Hours Pulse -> Market Close -> Off-Hours Sleep without requiring manual intervention.
- **Fail-safe Auto-Restart**: If network connectivity drops or a subprocess fails, systemd automatically restarts the service after 15 seconds.

### Service Control Commands:
| Action | CLI Command | Direct Systemd Command |
| :--- | :--- | :--- |
| **Check Status** | `./robinhood_trader.py service status` | `systemctl --user status robinhood-trader.service` |
| **View Live Logs** | `./robinhood_trader.py service logs 50` | `journalctl --user -u robinhood-trader.service -f` |
| **Stop Daemon** | `./robinhood_trader.py service stop` | `systemctl --user stop robinhood-trader.service` |
| **Start Daemon** | `./robinhood_trader.py service start` | `systemctl --user enable --now robinhood-trader.service` |
| **Restart Daemon** | `./robinhood_trader.py service restart` | `systemctl --user restart robinhood-trader.service` |

---

## 4. Reports, History & Agent Memory Architecture

To prevent context bloat while giving the agent full visibility into past trades, theses, and portfolio changes:

### 1. Cached Real-Time Data (`~/.cache/ai/trading/`)
- `portfolio_latest.json` / `portfolio_<account>.json`: Complete balances, active holdings, cost bases, and weights.
- `portfolio_latest.csv` / `portfolio_<account>.csv`: Compact CSV for rapid filtering via grep/awk.
- `audit_<account>.json`: Quantitative risk analysis, concentration alerts, dead money / dust candidates, and tax-loss harvesting breakdown.

### 2. Obsidian Trading Knowledge Vault (`~/.config/ai/trading_vault/`)
- **Daily Notes** (`daily_notes/YYYY-MM-DD.md`): Pre-market outlook, macro sentiment, staged setups, intraday actions, and close review.
- **Ticker Theses** (`tickers/<TICKER>.md`): Multi-factor scores, RSI, SMA 20/50/200, MACD, catalyst sentiment, and risk levels.
- **Trade Ledger** (`retrospectives/trade_ledger.jsonl`): Chronological JSONL log of every execution.

### 3. Historical Journal (`~/.config/ai/trading_journal.json`)
- Stores a 60-day rolling log of daily market closing summaries and performance.

### How the Agent Reads Past History:
- **Fast Status Check**: Run `./robinhood_trader.py summary [agentic|all]` (<200 tokens) to inspect portfolio health and top positions.
- **Check Recent Orders**: Run `./robinhood_trader.py orders [agentic]`.
- **Check Profit & Loss**: Run `./robinhood_trader.py pnl [agentic]`.
- **Inspect Past Day**: Read `~/.config/ai/trading_vault/daily_notes/<YYYY-MM-DD>.md`.
- **Inspect Ticker History**: Read `~/.config/ai/trading_vault/tickers/<TICKER>.md`.

---

## 5. Direct MCP Tools & CLI Command Reference

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
| `robinhood__get_equity_orders` | Fetch recent stock orders & statuses | `{"account_number": "517198354"}` |
| `robinhood__get_realized_pnl` | Fetch realized gains/losses performance metrics | `{"account_number": "517198354"}` |
| `robinhood__review_equity_order` | Simulate a stock order without placing | `{"account_number": "517198354", "symbol": "AAPL", "side": "buy", "type": "market", "dollar_amount": "50"}` |
| `robinhood__place_equity_order` | Execute real equity order on agentic account | `{"account_number": "517198354", "symbol": "AAPL", "side": "buy", "type": "market", "dollar_amount": "50"}` |

### Built-in Analysis CLI (`robinhood_trader.py`):
| Command | Description | Example |
| :--- | :--- | :--- |
| `summary [account\|all]` | Compact executive summary (<200 tokens), top holdings, winners/losers, risk score | `./robinhood_trader.py summary agentic` |
| `orders [account]` | View recent equity orders, fill status, and executing agent | `./robinhood_trader.py orders agentic` |
| `pnl [account]` | Realized and unrealized P&L report | `./robinhood_trader.py pnl agentic` |
| `trades [account]` | Closed trade-by-trade P&L history | `./robinhood_trader.py trades agentic` |
| `accounts` | Queries all authorized brokerage accounts & agentic permissions | `./robinhood_trader.py accounts` |
| `portfolio [account]` | Live positions table (supports `--summary`, `--filter <losers\|winners\|dust\|dead-money>`, `--top <N>`, `--json`, `--csv`) | `./robinhood_trader.py portfolio agentic --filter losers` |
| `audit [account]` | Quantitative health score (0-100), concentration cap checks, semi overlap, dead-money & dust audit | `./robinhood_trader.py audit agentic` |
| `harvest-losses [account]` | Step-by-step tax-loss harvesting candidates, harvestable dollar savings, wash-sale guidance | `./robinhood_trader.py harvest-losses` |
| `rebalance-plan [account]` | Concrete 4-step rebalance plan: dead money liquidation, dust cleanup, winner trims, cash buffer | `./robinhood_trader.py rebalance-plan` |
| `export [account]` | Explicitly exports portfolio JSON, CSV, and audit datasets to `~/.cache/ai/trading/` | `./robinhood_trader.py export` |
| `analyze <tickers...>` | Multi-factor analysis: live price, RSI, SMA (20/50/200), MACD, sentiment & risk targets | `./robinhood_trader.py analyze NVDA AAPL MSFT` |
| `status` | Checks US market open/closed status, session, Eastern & Mountain times | `./robinhood_trader.py status` |
| `scan [watchlist]` | Scans ticker universe and ranks highest-conviction opportunities | `./robinhood_trader.py scan` |
| `news <ticker/query>` | Searches latest financial news and computes sentiment score (-1.0 to +1.0) | `./robinhood_trader.py news TSLA` |
| `monitor [--auto-trade]` | Autonomous market hours daemon (9:30 AM - 4:00 PM ET / 7:30 AM - 2:00 PM MT) | `./robinhood_trader.py monitor --interval 60` |
| `risk-monitor [--live]` | Deterministic risk monitor enforcing stop-losses and take-profit targets | `./robinhood_trader.py risk-monitor` |
| `service <cmd>` | Manage background systemd service (`status`, `logs`, `start`, `stop`, `restart`) | `./robinhood_trader.py service status` |

---

## 6. Financial Safety & Risk Management Rules

> [!CAUTION]
> Trading involves real financial risk. Adhere strictly to the following rules:

1. **Multi-Tier Intraday Circuit Breakers**:
   - **Tier 1 ($\le -3.0\%$ Drawdown from Open):** Halts all automated buy orders for the rest of the day.
   - **Tier 2 ($\le -6.0\%$ Drawdown from Open):** Tightens active stop-losses to $\min(4.0\%, 1.0\times\text{ATR})$.
   - **Tier 3 ($\le -10.0\%$ Drawdown from Open):** Triggers Emergency Capital Liquidation (cancels open orders, exits positions in loss-ranked order overriding PDT deferral, disables auto-trade).
2. **FINRA PDT Rule 4210 Compliance**:
   - Small sandbox accounts ($<\$25,000$) are limited to 3 day trades in rolling 5 business days (`pdt_tracker.json`).
   - If day trades used reaches $3/3$, same-day round-trip buys/sells are blocked; stop-losses on same-day buys defer to the next 09:30 ET market open (`deferred_exit.flag`).
   - Positions opened on prior days (overnight/swing) are NOT day trades and can always be exited freely.
3. **Dynamic Volatility Trailing Stop & Take-Profit**:
   - Stop-loss: $\max(5.0\%, 2.0\times\text{ATR}_{\text{pct}})$.
   - Stage 1 Take-Profit: Exit 50% shares at $+8.0\%$.
   - Stage 2 Runner: Trail remaining 50% shares with dynamic trailing stop $\max(4.5\%, 1.5\times\text{ATR}_{\text{pct}})$ up to $+15.0\%$.
4. **Portfolio Diversification, Sizing & Cash Buffer**:
   - Dynamic Growth-Scaled Position Sizing: $\text{Size} = \text{round}\big(\min(\text{Cash}, \max(\$15.00, 0.08\times\text{Equity}), 0.15\times\text{Equity}), 2\big)$.
   - Max single-stock allocation: 15% of total portfolio value.
   - Maintain at least 15% cash reserve buffer for market pullbacks.
5. **Account Permission Boundary**:
   - Automated orders MUST target `517198354` (`agentic_allowed: true`).
   - Non-agentic accounts (`837546068`, `422982744`) are protected against automated mutation.
6. **Human Verification in Plan / Manual Modes**:
   - When running under `--plan` or `--manual`, present the proposed trades (Ticker, Action, Shares/Dollar, Limit Price, Estimated Total, Risk Target) to the user and obtain confirmation before placing orders.

