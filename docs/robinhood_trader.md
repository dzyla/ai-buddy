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
 │  robinhood_trader.py (shim) -> trader/ package              │
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

### Package layout (`trader/`)

`robinhood_trader.py` is a thin shim: run as a script it calls `trader.cli.main()`; imported, it returns
`trader.legacy`, a flat namespace with every name the old single-file module had (so
`import robinhood_trader` and `monkeypatch.setattr(robinhood_trader, ...)` keep working).

| Module | Contents |
| :--- | :--- |
| `config.py` | State paths (`~/.config/ai`, `~/.cache/ai/trading`, monitor flags, PDT / circuit-breaker files), universes, `trader.json` loading |
| `market_hours.py` | `MarketHours` (NYSE calendar, sessions) |
| `data.py` / `indicators.py` | `FinancialData` (quotes, daily bars) / `TechnicalIndicators` |
| `strategy.py` | `MomentumStrategy`, `_floor_qty`, `_fmt_qty` (pure planning functions) |
| `broker.py` | `RobinhoodExecutor` (MCP bridge, order mutator), account resolution, `OrderNotConfirmed` |
| `risk.py` | `ComplianceAndRiskGuard` (PDT, cooldowns, circuit breaker) |
| `journal.py` | `ObsidianTradingVault` (notes, trade ledger) |
| `advisor.py` | `NewsSentimentEngine`, `AgentAdvisor`, `TradingStrategyEngine` (notes only, never orders) |
| `portfolio.py` | `TradingDataManager`, `PortfolioAuditor` |
| `daemon.py` | lifecycle loop, `_momentum_rebalance_pulse`, `_risk_monitor_pulse`, risk monitor, backtest |
| `shadow.py` / `summary.py` | shadow mode and the daily summary (below) |
| `cli.py` | `main(argv) -> int` |
| `deploy.sh` | versioned release + `current` symlink |

### Core Design Principles
1. **Core + Monthly Momentum Sleeve (`MomentumStrategy`)** — the only rules the live loop trades (spec: `docs/superpowers/specs/2026-09-20-momentum-sleeve-trader-design.md`):
   - **Core 60%**: `VTI` + `QQQ`, topped up once a day whenever cash exceeds the buffer. Never sold by the bot.
   - **Sleeve 30%**: top 3 names from the curated universe by 6-month (126-bar) total return, eligible only when close > SMA200, average 20-day volume >= 500k and price >= $5. Equal weight, rebalanced on the first trading day the daemon sees each month (flag `rebalance_YYYY-MM.json`). Sells go first; buys follow on the next pulse once cash has settled.
   - **Rank buffer (hysteresis)**: a sleeve name already held is kept while it is still eligible and ranked within top 3 + `SLEEVE_RANK_BUFFER` (default 2); the free slots go to the best-ranked other names. Set `{"strategy": {"sleeve_rank_buffer": 0}}` in `~/.config/ai/trader.json` for the old strict top-3 rotation. It is applied only when a month's plan is first made, so a running month's flag is never re-planned; the flag records `incumbents` and `rank_buffer`.
   - **Bear parking (`bear_park`, off by default)**: `{"strategy": {"bear_park": "BND"}}` buys the whole 30% sleeve allocation into that bond ETF instead of leaving it in cash when SPY is below its SMA200 at the monthly rebalance. Fallback chain if the ticker has no usable bars: configured ticker, then BND, AGG, SGOV, then cash (today's behaviour; a failed fetch also means cash). Buy reason `PARK_TARGET`; when the regime is BULL again the parked ETF is sold first through the normal rotate-out (sells before buys, same settlement wait) and the sleeve is re-bought. The parked ETF is exempt from the per-name -20% disaster stop (only while `bear_park` is configured; the exemption never causes a trade); the circuit breaker and the daily core top-up are unchanged. Like the rank buffer it is read only when a month's plan is first made (the flag records `park` and `park_requested`), so enabling it mid-month never triggers an unscheduled trade. Research (`dev/backtest_momentum.py --variants live_default,live_default+park_BND`) showed it ties the live rules within noise, so it is a SHADOW candidate only; keep it null live.
   - **Regime gate**: if `SPY` closes below its SMA200 on rebalance day, the sleeve goes to cash (or to the parked bond ETF when `bear_park` is set).
   - **Cash 10%**: buffer that buys never breach.
   - **Disaster stop**: any sleeve position at or below -20% from average cost is sold on the next pulse. There are no partial take-profits, profit-lock floors or trailing stops (the 5-year backtest showed those clipped winners and netted ~$0 on 250 trades).
   - **Hold list**: symbols in `~/.config/ai/trading_hold.txt` (one per line) are never sold by the bot.
   - **No LLM in the order path.** `AgentAdvisor` only writes the pre-market and close notes.
   - Evidence: `./robinhood_trader.py backtest --years 5` runs the same functions the live loop uses on Yahoo daily bars and prints returns, CAGR, max drawdown and buy-and-hold benchmarks (plus a core-only comparison).
   - Research harness: `python3 dev/backtest_momentum.py` (2005 onwards, dividend-adjusted, signal on the prior close and fill at the next open with 10 bps slippage, averaged over rebalance-day offsets 1-5 with `--offsets 1,2,3,4,5`). It runs the live universe, which was picked in 2026 and so carries survivorship bias, and two universes picked without hindsight: sector ETFs and the 30 largest US stocks at the end of 2004. Results from October 2026:
     - The rank buffer cut sleeve turnover by 20-28%. Sharpe changed by at most 0.03 and max drawdown by at most 0.5 pt in every universe and period.
     - The SMA200 regime gate is what limits drawdown: without it the 2005-15 max drawdown went from about -38% to -51% in the live universe.
     - Rejected because the result was mixed out of sample or only appeared in the biased universe: 12-1 or 6-1 skip-month, risk-adjusted and blended lookbacks, inverse-vol sizing, per-name absolute momentum, a 2% SMA band, a 10-month SMA, top-5, a sector cap, and parking BEAR cash in BIL, SHY or IEF.
     - The -20% disaster stop neither helped nor hurt in the backtest (-15% was worse). It is kept as protection against a single name collapsing.
     - Treat the absolute numbers with caution. Run on its own with no core, the sleeve trailed VTI buy-and-hold in both unbiased universes. Most of its edge in the live universe is hindsight.
2. **Zero-LLM Quiet Hours**: Outside market hours (nights, weekends, holidays), the daemon strictly sleeps in power-saving mode without invoking the AI model or using GPU VRAM.
3. **Context-Optimized Token Offloading**: Full portfolio datasets (60+ positions, balances, cost bases) are offloaded to disk (`~/.cache/ai/trading/`). CLI commands like `./robinhood_trader.py summary` output compact digests (<200 tokens) so LLMs maintain situational awareness without context overflow.
4. **Multi-Factor Quantitative Scoring**: Tickers are evaluated on a 0–100 scale combining trend (SMA 20/50/200), momentum (RSI-14), MACD histogram, and real-time news sentiment.
5. **Obsidian Knowledge Vault**: Trade logs, pre-market briefings, and ticker theses persist in standard Markdown notes (`~/.config/ai/trading_vault/`).

---

## 2. Account Segregation & Safety Boundary

The engine automatically enumerates all authorized accounts via MCP and enforces safety boundaries:

| Account Number | Type / Nickname | Permission | Usage |
| :--- | :--- | :---: | :--- |
| **`<AGENTIC_ACCOUNT>`** | `Agentic` (Limited Margin) | `agentic_allowed: true` | **Dedicated Agentic Sandbox**: Real/simulated automated order placement, stop-loss and take-profit triggers. |
| **`<PRIMARY_ACCOUNT>`** | Primary Margin (Default) | `agentic_allowed: false` | **Protected Primary Account**: Read-only portfolio analysis, tax-loss harvesting audits, and executive summaries. |
| **`<ROTH_ACCOUNT>`** | Roth IRA | `agentic_allowed: false` | **Retirement Account**: Read-only inspection. |

> [!IMPORTANT]
> The harness prohibits automated order placement against any account where `agentic_allowed` is `false`. `execute_market_order` re-checks the live account list before every order, and the daemon refuses to trade if it cannot determine the agentic account (it never falls back to another account).

### Account configuration (local, untracked)

Account numbers are not stored in the repo. They are resolved, in order, from:

1. Environment variables `RH_AGENTIC_ACCOUNT`, `RH_PRIMARY_ACCOUNT`, `RH_ROTH_ACCOUNT`.
2. `~/.config/ai/trader.json` (path overridable with `RH_TRADER_CONFIG`; keep it `chmod 600`):
   ```json
   {"accounts": {"agentic": "<AGENTIC_ACCOUNT>", "primary": "<PRIMARY_ACCOUNT>", "roth": "<ROTH_ACCOUNT>"}}
   ```
3. Discovery via `get_accounts`: the single account with `agentic_allowed: true` (agentic) and the `is_default` account (primary). Zero or several agentic accounts with no configuration means no automated trading.

CLI aliases: `agentic`, `primary` (or `default`), `roth`.

---

## 3. Daily Schedule & Timezone Conversion (MT vs ET)

US Equities (NYSE / NASDAQ) trade on **US Eastern Time (ET)**. For operators in **Mountain Time (MT)**:

| Daily Phase | Mountain Time (MT) | Eastern Time (ET) | Engine Action |
| :--- | :---: | :---: | :--- |
| **🌅 Pre-Market Briefing** | **07:20 AM MT** | 09:20 AM ET | Scans watchlist, analyzes overnight news sentiment, calculates staged setups, updates Obsidian daily note & ticker theses. |
| **🔔 Market Open Bell** | **07:30 AM MT** | 09:30 AM ET | Verifies opening quotes and begins the 60-second active session pulse. |
| **⚡ Regular Trading Hours** | **07:30 AM – 02:00 PM MT** (11:00 AM MT on early-close days) | 09:30 AM – 04:00 PM ET (1:00 PM on the day after Thanksgiving, Christmas Eve, July 3) | Safety pulse every 60s (circuit breaker, -20% disaster stop). From 10:00 ET: daily core top-up; on the first trading day of the month, the sleeve rebalance. |
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

The automated trader runs as a systemd user daemon (`robinhood-trader.service`, tracked in
`services/`). It runs the **deployed release** in `~/.local/share/ai-trader/current`, never a checkout,
so editing the repo cannot change a running trader. `./install.sh` installs the unit only once a
release exists and never restarts it.

### Deploying

```bash
bash trader/deploy.sh             # HEAD; refuses on a dirty tree or failing trader tests
bash trader/deploy.sh --ref <sha> # that commit, whatever the worktree state (still test-gated)
cp services/robinhood-trader.service ~/.config/systemd/user/   # first time / when the unit changed
systemctl --user daemon-reload && systemctl --user restart robinhood-trader
journalctl --user -u robinhood-trader -f                       # confirm two clean pulses
```

`deploy.sh` builds `~/.local/share/ai-trader/releases/<UTC-timestamp>-<shortsha>/` from the committed
tree (`trader/`, `robinhood_trader.py`, `VERSION` with the commit), atomically repoints the `current`
symlink, keeps the newest five releases plus the one `current` pointed at before (the running daemon
may still be executing it) and prints the release path. The shim resolves the symlink at start-up and
loads every module eagerly, so repointing `current` never changes the code of a running daemon; only a
restart picks up the new release. The test gate runs every trader test file against a temporary
checkout of the deployed commit (not the working tree). `AI_TRADER_HOME` overrides the base directory. `--skip-tests` skips
the test gate; it exists for the deploy tests only and is refused unless `AI_TRADER_HOME` points to a
non-default directory. Rollback: `ln -sfn releases/<older> ~/.local/share/ai-trader/current` and restart
the unit.

The release reads exactly the same state as before (`~/.config/ai/trader.json`,
`~/.config/ai/.monitor_flags/`, `~/.cache/ai/trading/`), so switching mid-session or mid-month repeats
neither the month's rebalance (`rebalance_<YYYY-MM>.json`) nor the day's core top-up
(`core_topup_<date>.flag`). Optional environment for the unit goes in `~/.config/ai/trader.env`
(`EnvironmentFile`, e.g. `AI_TRADER_ZULIP_TO=...`).

### Shadow mode

A what-if run of the strategy with different parameters, on the live account's data, that never trades.
Enable it in `~/.config/ai/trader.json` (template: `config/trader.example.json`):

```json
{"shadow": {"enabled": true, "strategy": {"sleeve_rank_buffer": 0, "sleeve_top_n": 4}}}
```

To shadow the bond-parking candidate (`bear_park`), set exactly this in `~/.config/ai/trader.json` and leave the
live `strategy` block without `bear_park`:

```json
{"shadow": {"enabled": true, "strategy": {"bear_park": "BND"}}}
```

In a BEAR regime the shadow record then has `"targets": []`, `"park": "BND"` and a planned `buy BND` (reason
`PARK_TARGET`) beside the sleeve sells; the daily summary shows `shadow targets: BND (park) | live targets: CASH`.
Without a bear regime it plans exactly what the live rules plan. A shadow `bear_park` is a plain ticker (not a core
ETF or SPY); anything else is listed in `ignored_overrides`. A shadow without its own `bear_park` inherits the live
setting (null by default).

Once per trading day, after the live pulse (from 10:00 ET), the daemon calls
`trader.shadow.run_shadow_pulse`: it reads the account through `ReadOnlyBroker` (every order, cancel or
raw MCP method raises `PermissionError`), ranks the universe with a `MomentumStrategy` subclass carrying
the overrides, and appends one line to `~/.cache/ai/trading/shadow/<YYYY-MM>.jsonl` with the shadow
`targets`, the default-parameter `baseline_targets`, the live month's `live_targets` and the
`planned_orders` (sells, buys, core top-up) it would place today. It writes no live flag, ledger or note;
`shadow_<date>.flag` limits it to one attempt a day. Accepted keys: `sleeve_rank_buffer`, `sleeve_top_n`,
`core_target_pct`, `sleeve_target_pct`, `cash_buffer_pct`, `lookback_bars`, `regime_sma_bars`,
`disaster_stop_pct`, `min_avg_volume`, `min_price`, `bear_park` (ticker string); unknown or out-of-range keys are listed as
`ignored_overrides`.

### Daily summary

After the close on each trading day (16:00 ET or the first loop after it) the daemon takes a read-only
account snapshot (appended to `~/.cache/ai/trading/equity_journal.jsonl`) and sends a short markdown
report built by `trader.summary.build_daily_summary(day)`: equity change versus the circuit-breaker
opening baseline, the day's fills from the trade ledger, sleeve holdings, the month's regime,
circuit-breaker state, and the shadow-vs-live target diff. Account numbers are never included.
Delivery: a Zulip private message to `AI_TRADER_ZULIP_TO` (else `AI_REMINDER_ZULIP_TO`) through
`zulip_mcp_server.py`; if that fails or no recipient is set, `notify-send`; otherwise the risk log only.
`summary_<date>.flag` makes it once a day.

### CLI Service Controls
```bash
./robinhood_trader.py service status     # Check live service status & uptime
./robinhood_trader.py service logs 50    # View the last 50 log lines
./robinhood_trader.py service live       # Switch to LIVE autonomous trading mode
./robinhood_trader.py service dry-run    # Switch back to monitor-only (dry-run) mode
./robinhood_trader.py service restart    # Restart daemon process
./robinhood_trader.py service stop       # Stop the background daemon
```
`service install|live|dry-run` rewrite the unit; it points at the deployed release when
`~/.local/share/ai-trader/current` exists, else at the checkout the command ran from.

### Direct Systemd Commands
```bash
systemctl --user status robinhood-trader.service
journalctl --user -u robinhood-trader.service -f
systemctl --user stop robinhood-trader.service
```

---

## 5. CLI Command Reference

All commands are accessible directly via [`./robinhood_trader.py`](../robinhood_trader.py):

| Command | Arguments | Description | Example |
| :--- | :--- | :--- | :--- |
| `summary` | `[account\|all]` | Compact executive summary (<200 tokens) with top holdings, winners/losers, and health score | `./robinhood_trader.py summary agentic` |
| `portfolio` | `[account] [--flags]` | Full holdings table. Flags: `--summary`, `--filter <losers\|winners\|dust\|dead-money>`, `--top <N>`, `--json`, `--csv` | `./robinhood_trader.py portfolio agentic --filter losers` |
| `orders` | `[account]` | View recent equity orders, fill prices, execution timestamps, and placing agent | `./robinhood_trader.py orders agentic` |
| `pnl` | `[account]` | Realized gains, total return rate, and unrealized equity performance | `./robinhood_trader.py pnl agentic` |
| `trades` | `[account]` | Closed trade-by-trade P&L retrospective history | `./robinhood_trader.py trades agentic` |
| `buy` | `<ticker> [dollar] [acc]`| Execute instant market buy order on the Agentic Sandbox account | `./robinhood_trader.py buy SMCI 25 agentic` |
| `sell` | `<ticker> [qty\|all] [acc]`| Execute market sell order on Agentic Sandbox | `./robinhood_trader.py sell AVGO all agentic` |
| `audit` | `[account]` | Quantitative health score (0–100), concentration cap breaches, semi overlap, dead-money and dust audit | `./robinhood_trader.py audit agentic` |
| `harvest-losses`| `[account]` | Tax-loss harvesting candidates with dollar savings and wash-sale guidance | `./robinhood_trader.py harvest-losses` |
| `rebalance-plan`| `[account]` | Concrete 4-step rebalance plan (dead-money liquidation, dust cleanup, winner trims, cash buffer) | `./robinhood_trader.py rebalance-plan agentic` |
| `export` | `[account] [dir]` | Exports full JSON and CSV datasets to `~/.cache/ai/trading/` | `./robinhood_trader.py export agentic` |
| `analyze` | `<tickers...>` | Multi-factor analysis: live price, RSI, SMA 20/50/200, MACD, sentiment & risk levels | `./robinhood_trader.py analyze NVDA AAPL MSFT` |
| `scan` | `[tickers...]` | Scans watchlist and ranks highest-conviction buy/sell opportunities | `./robinhood_trader.py scan` |
| `news` | `<ticker/query>` | Searches latest financial news and calculates sentiment score (-1.0 to +1.0) | `./robinhood_trader.py news TSLA` |
| `discover` | — | Searches market for high-momentum breakout candidates | `./robinhood_trader.py discover` |
| `risk-monitor` | `[--live] [--once] [--force]`| Deterministic safety pass: circuit breaker + -20% disaster stop (no LLM) | `./robinhood_trader.py risk-monitor --once --force` |
| `backtest` | `[--years N] [--start D] [--top N] [--cash X]` | Simulates the live strategy rules on Yahoo daily bars vs buy-and-hold and core-only | `./robinhood_trader.py backtest --years 5` |
| `monitor` | `[--auto-trade] [--live]`| Autonomous lifecycle loop: 60% core / 30% monthly momentum sleeve / 10% cash | `./robinhood_trader.py monitor --interval 60 --auto-trade --live` |
| `service` | `<start\|stop\|live\|...>`| Manage background systemd user service | `./robinhood_trader.py service live` |
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
    ├── trade_ledger.jsonl     # Append-only chronological execution ledger
    └── rebalance_2026-09.md   # Monthly sleeve decision: regime, ranking, planned orders
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
