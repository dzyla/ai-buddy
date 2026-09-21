# Robinhood trader redesign: core + monthly momentum sleeve

Date: 2026-09-20. Account: agentic sandbox 517198354 (~$630).

## Problem

The live daemon (`monitor --interval 60 --auto-trade --live`) runs an intraday breakout
system: buy when the multi-factor score is >= 80, 2x ATR stop, sell half at +8%, then
"profit-lock" floors (+4% peak -> exit at +0.5%, +6% peak -> exit at +2.5%) and a trailing
stop. Realised P&L since 2026-08-25 is about -$12: four full-size stop-outs versus three
winners that were cut to quarter size before they ran.

A 5-year daily-bar backtest of the same rules on the same watchlist (scratchpad, Yahoo bars):

| Strategy | Jul 2022 - Sep 2026 | Last 2y | Satellite P&L (5y, $600 start) |
|---|---|---|---|
| Current rules | +70% | +21% | +$14 on 252 trades |
| Current minus profit-lock floors | +84% | +23% | +$97 |
| Core only (90% VTI/QQQ) | +95% | +36% | n/a |
| 60% core + 30% monthly top-3 momentum | +152% | +57% | +$359 on 43 trades |
| Buy and hold VTI | +88% | +33% | n/a |

Conclusion: the satellite churn has zero-to-negative expectancy; all growth came from the
VTI/QQQ ballast. The momentum-sleeve figure is inflated by survivorship (watchlist chosen in
2026), so it is directional evidence, not a forecast.

Safety defects found: (1) the test suite writes fake trades into the live ledger, PDT tracker
and risk log; (2) on 2026-08-25 a flaky portfolio read tripped the Tier 3 emergency
liquidation at a phantom -57% drawdown; (3) the local-LLM "risk officer" returned EXECUTE in
851 of 851 calls and adds 25-60 s latency per order.

## Decision (approved by owner 2026-09-20)

Replace the intraday breakout system with a once-a-day core + monthly momentum sleeve.

### Target book
- Core 60%: VTI and QQQ, topped up on any day cash exceeds the buffer. Never sold by the bot.
- Sleeve 30%: top 3 names from the momentum universe by 6-month (126 bar) total return,
  eligible only if close > SMA200 and the liquidity gate passes (avg 20d vol >= 500k, price >= $5).
  Equal weight. Rebalanced on the first trading day of each month.
- Regime gate: if SPY close < SMA200 on rebalance day, the sleeve goes to cash (which flows
  into core top-ups only up to the 60% target; the rest stays cash).
- Cash 10%: buffer, never breached by buys.
- Disaster stop: any sleeve position at <= -20% from average cost is sold immediately
  (checked every pulse). No partial take-profits, no profit-lock floors, no trailing stops.
- Symbols listed in `~/.config/ai/trading_hold.txt` (one per line) are never sold by the bot
  and are excluded from sleeve weight accounting.

### Execution
- `_momentum_rebalance_pulse(account, dry_run)` runs every pulse during RTH but acts only
  after 10:00 ET. Daily: core top-up. Monthly (flag `rebalance_YYYY-MM.json` in
  `.monitor_flags`): compute target, sell rotated-out names, then buy targets. The flag stores
  targets and progress so partial fills (cash lag) complete on later pulses.
- `_risk_monitor_pulse` becomes the disaster-stop pass only. Core ETFs exempt.
- No LLM call in the order path. `AgentAdvisor` remains for pre-market/close notes.
- Circuit breaker: Tier 1 (-3%) halts buys for the day. Tier 2/3 no longer tighten stops or
  liquidate; Tier 3 (-10%) halts all bot activity for the day and logs loudly. Any tier
  requires the drawdown to be observed on two consecutive pulses, and a single-pulse equity
  reading more than 30% away from the baseline is treated as a data glitch and ignored.
- `MomentumStrategy` holds pure functions (rank, regime, target book, order plan) shared by
  the live pulse and the new `backtest` CLI subcommand, so future rule changes are measured
  with the same code that trades.

### Tests
- `tests/conftest.py` autouse fixture redirects every state path (PDT tracker, circuit
  breaker file, monitor flags, risk log, vault dir, trading state, cache dir, journal) to
  `tmp_path`. Existing fake `WINNER`/`TEST_ACC` records are purged from live state (backed up).
- Unit tests for `MomentumStrategy` (ranking, regime, target book, order plan, hold list),
  the rebalance pulse (monthly gating, partial-fill completion, dry run), the disaster stop,
  and the circuit-breaker confirmation logic.

### Out of scope
- Primary and Roth accounts (read-only, unchanged).
- Tax-lot / wash-sale optimisation for sleeve rotations.
