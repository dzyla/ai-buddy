# Robinhood Agentic Trading & Market Hours Suite

A production-grade, autonomous financial analysis, risk management, and trading subsystem integrated into `ai-buddy`. It pairs Robinhood's official Model Context Protocol (MCP) server with a zero-LLM deterministic Python daemon for sub-second quote fetching, technical indicator math, portfolio audits, and automated trade execution.

---

## 1. Architecture Overview

```
 ┌─────────────────────────────────────────────────────────────┐
 │                      AI Agent / User CLI                    │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │                     robinhood_trader.py                     │
 │  • MarketHours Engine (NYSE/NASDAQ calendar & sessions)     │
 │  • FinancialData & Quotes (MCP primary + Yahoo fallback)    │
 │  • TechnicalIndicators (Pure Python: RSI, SMA, MACD, ATR)   │
 │  • PortfolioAuditor (Health score 0-100, dead money, dust)  │
 │  • ObsidianTradingVault (Daily notes, ticker theses, logs)  │
 │  • RobinhoodExecutor (MCP bridge & sub-second execution)    │
 └──────────────┬──────────────────────────────┬───────────────┘
                │                              │
                ▼                              ▼
 ┌───────────────────────────┐  ┌──────────────────────────────┐
 │ ~/.cache/ai/trading/      │  │ Robinhood Agentic MCP Server │
 │  • portfolio_latest.json  │  │  https://agent.robinhood.com │
 │  • portfolio_latest.csv   │  │  • OAuth token caching       │
 │  • audit_latest.json      │  │  • Real-time quotes & orders │
 └───────────────────────────┘  └──────────────────────────────┘
```

### Core Design Principles
1. **Zero-LLM Daemon**: The background monitoring loop runs purely in deterministic Python. It uses **0 GPU VRAM** and consumes no LLM tokens during its 24/7 lifecycle.
2. **Context-Optimized Token Offloading**: Full portfolio datasets (60+ positions, balances, cost bases) are offloaded to disk (`~/.cache/ai/trading/`). CLI commands like `./robinhood_trader.py summary` output compact digests (<200 tokens) so LLMs maintain situational awareness without context overflow.
3. **Multi-Factor Quantitative Scoring**: Tickers are evaluated on a 0–100 scale combining trend (SMA 20/50/200), momentum (RSI-14), MACD histogram, and real-time news sentiment.
4. **Obsidian Knowledge Vault**: Trade logs, pre-market briefings, and ticker theses persist in standard Markdown notes (`~/.config/ai/trading_vault/`).

---

## 2. Account Segregation & Safety Boundary

The engine automatically enumerates all authorized accounts via MCP and enforces safety boundaries:

| Account Number | Type / Nickname | Permission | Usage |
| :--- | :--- | :---: | :--- |
| **`517198354`** | `Agentic` (Limited Margin) | `agentic_allowed: true` | **Dedicated Agentic Sandbox**: Real/simulated automated order placement, stop-loss and take-profit triggers. |
| **`837546068`** | Primary Margin (Default) | `agentic_allowed: false` | **Protected Primary Account**: Read-only portfolio analysis, tax-loss harvesting audits, and executive summaries. |
| **`422982744`** | Roth IRA | `agentic_allowed: false` | **Retirement Account**: Read-only inspection. |

> [!IMPORTANT]
> The harness prohibits automated order placement against any account where `agentic_allowed` is `false`. Automated orders target `517198354` only.

---

## 3. Daily Schedule & Timezone Conversion (MT vs ET)

US Equities (NYSE / NASDAQ) trade on **US Eastern Time (ET)**. For operators in **Mountain Time (MT)**:

| Daily Phase | Mountain Time (MT) | Eastern Time (ET) | Engine Action |
| :--- | :---: | :---: | :--- |
| **🌅 Pre-Market Briefing** | **07:20 AM MT** | 09:20 AM ET | Scans watchlist, analyzes overnight news sentiment, calculates staged setups, updates Obsidian daily note & ticker theses. |
| **🔔 Market Open Bell** | **07:30 AM MT** | 09:30 AM ET | Verifies opening quotes and begins the 60-second active session pulse. |
| **⚡ Regular Trading Hours** | **07:30 AM – 02:00 PM MT** | 09:30 AM – 04:00 PM ET | Active market monitoring pulse (every 60s); monitors stop-losses (-5%), take-profits (+8%), and breakout candidates. |
| **🔕 Market Close Summary** | **02:00 PM – 02:15 PM MT** | 04:00 PM – 04:15 PM ET | Compiles daily closing summary, writes to `trading_journal.json`, and updates Obsidian daily note. |
| **🌙 Off-Hours Power Save** | **02:15 PM – 07:20 AM MT** (Nights/Weekends/Holidays) | 04:15 PM – 09:20 AM ET | Sleeps until 07:20 AM MT of the next trading day. |

Check live session and time offsets anytime:
```bash
./robinhood_trader.py status
```
Output:
```
======================================================================
  US STOCK MARKET & ROBINHOOD STATUS
======================================================================
  Eastern Time (ET) : 2026-08-18 11:50:30 AM EDT
  Local Time (MT)   : 2026-08-18 09:50:30 AM MDT
  Market Session    : REGULAR
  Regular Hours     : OPEN (09:30 - 16:00 ET / 07:30 - 14:00 MT)
  Time to Close     : 250 minutes
======================================================================
```

---

## 4. Background Service Management

The automated trader runs as a systemd user daemon (`robinhood-trader.service`):

### CLI Service Controls
```bash
./robinhood_trader.py service status     # Check live service status & uptime
./robinhood_trader.py service logs 50     # View the last 50 log lines
./robinhood_trader.py service stop        # Stop the background daemon
./robinhood_trader.py service start       # Enable and start the background daemon
./robinhood_trader.py service restart     # Restart the daemon process
```

### Direct Systemd Commands
```bash
systemctl --user status robinhood-trader.service
journalctl --user -u robinhood-trader.service -f
systemctl --user stop robinhood-trader.service
```

---

## 5. CLI Command Reference

All commands are accessible directly via [`./robinhood_trader.py`](file:///home/dzyla/ai-buddy/robinhood_trader.py):

| Command | Arguments | Description | Example |
| :--- | :--- | :--- | :--- |
| `summary` | `[account]` | Compact executive summary (<200 tokens) with top holdings, winners/losers, and health score | `./robinhood_trader.py summary` |
| `portfolio` | `[account] [--flags]` | Full holdings table. Flags: `--summary`, `--filter <losers\|winners\|dust\|dead-money>`, `--top <N>`, `--json`, `--csv` | `./robinhood_trader.py portfolio --filter losers` |
| `audit` | `[account]` | Quantitative health score (0–100), concentration cap breaches, semi overlap, dead-money and dust audit | `./robinhood_trader.py audit` |
| `harvest-losses`| `[account]` | Tax-loss harvesting candidates with dollar savings and wash-sale guidance | `./robinhood_trader.py harvest-losses` |
| `rebalance-plan`| `[account]` | Concrete 4-step rebalance plan (dead-money liquidation, dust cleanup, winner trims, cash buffer) | `./robinhood_trader.py rebalance-plan` |
| `export` | `[account] [dir]` | Exports full JSON and CSV datasets to `~/.cache/ai/trading/` | `./robinhood_trader.py export` |
| `analyze` | `<tickers...>` | Multi-factor analysis: live price, RSI, SMA 20/50/200, MACD, sentiment & risk levels | `./robinhood_trader.py analyze NVDA AAPL MSFT` |
| `scan` | `[tickers...]` | Scans watchlist and ranks highest-conviction buy/sell opportunities | `./robinhood_trader.py scan` |
| `news` | `<ticker/query>` | Searches latest financial news and calculates sentiment score (-1.0 to +1.0) | `./robinhood_trader.py news TSLA` |
| `discover` | — | Searches market for high-momentum breakout candidates | `./robinhood_trader.py discover` |
| `risk-monitor` | `[--live] [--once]`| Runs deterministic risk monitor enforcing stop-loss and take-profit rules | `./robinhood_trader.py risk-monitor --live` |
| `monitor` | `[--auto-trade]`| Starts the autonomous lifecycle loop in the foreground | `./robinhood_trader.py monitor --interval 60` |
| `accounts` | — | Lists authorized brokerage accounts and agentic permissions | `./robinhood_trader.py accounts` |
| `auth` | — | Verifies stored MCP OAuth credentials | `./robinhood_trader.py auth` |

---

## 6. Reports & Obsidian Trading Vault

The suite automatically records all activity into an Obsidian-compatible Markdown vault at `~/.config/ai/trading_vault/`:

### Directory Structure
```
~/.config/ai/trading_vault/
├── daily_notes/
│   └── 2026-08-18.md          # Pre-market briefing, macro outlook, staged setups & close summary
├── tickers/
│   ├── NVDA.md                # Ticker thesis, RSI, SMA levels, MACD histogram, catalyst news
│   ├── TSM.md
│   └── ASML.md
├── playbooks/                 # Trading rules and strategies
└── retrospectives/
    └── trade_ledger.jsonl     # Append-only chronological execution ledger
```

### Cached Portfolio Datasets (`~/.cache/ai/trading/`)
- `portfolio_latest.json` / `portfolio_<account>.json`: Complete JSON dataset including balances, active positions, and cost basis.
- `portfolio_latest.csv` / `portfolio_<account>.csv`: Tabular CSV dataset for CLI utilities (`grep`, `awk`, Python).
- `audit_latest.json`: Quantitative audit report with concentration alerts and recommendations.

---

## 7. AI Agent Integration

Agents operating in `ai-buddy` leverage the `robinhood-mcp` skill (`.agents/skills/robinhood_mcp/SKILL.md`):

1. **Load the Skill**:
   ```bash
   ai "load_skill robinhood-mcp; check my portfolio health score and top holdings"
   ```
2. **Context Efficiency**:
   The agent calls `./robinhood_trader.py summary` to get an executive summary without loading large JSON tables into context.
3. **Interactive Plan Mode**:
   When rebalancing or trading under `--plan` or `--manual`, the agent drafts a structured order plan and awaits user approval before invoking `place_equity_order`.
