"""Live loops: lifecycle daemon, strategy / risk pulses, risk monitor and backtest."""

import os
import json
import time
import re
import datetime
from typing import List, Dict, Any, Optional, Tuple

from .config import DEFAULT_WATCHLIST, RISK_MONITOR_FLAGS, RISK_MONITOR_LOG, SESSION_HALT_FLAG
from .journal import ObsidianTradingVault
from .market_hours import MarketHours
from .data import FinancialData
from .advisor import TradingStrategyEngine
from .strategy import MomentumStrategy, _fmt_qty
from .broker import OrderNotConfirmed, RobinhoodExecutor
from .risk import ComplianceAndRiskGuard


# ==============================================================================
# 7. Autonomous Scheduled Trading Lifecycle & Monitor
# ==============================================================================

def run_scheduled_trading_lifecycle(interval_seconds: int = 900, auto_trade: bool = False, dry_run: bool = True, watchlist: Optional[List[str]] = None):
    """
    Automated full-day trading schedule:
    1. Pre-Market Phase (09:20 ET): Generates briefing and stages daily trade decisions.
    2. Market Open Phase (09:30 ET): Executes / reviews staged orders.
    3. Active Hours Pulse (09:30 - 16:00 ET): Periodic pulse checks for trailing stops & take-profits.
    4. Market Close Phase (16:05 ET): Produces closing report & logs to journal.
    5. Off-Hours Sleep: Sleeps until 09:20 ET of next trading day.
    """
    watchlist = watchlist or DEFAULT_WATCHLIST
    print("=" * 75)
    print("  ROBINHOOD AUTONOMOUS SCHEDULED TRADING LIFECYCLE")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'} | Auto-Trade: {auto_trade} | Interval: {interval_seconds}s")
    print("=" * 75)

    has_run_premarket = False
    has_run_closing = False

    while True:
        now = MarketHours.now_et()
        session = MarketHours.get_market_session(now)
        time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        t_cur = now.time()

        # Phase 1: Pre-Market Briefing (09:20 - 09:30 ET on a Trading Day)
        if MarketHours.is_trading_day(now.date()) and datetime.time(9, 20) <= t_cur < datetime.time(9, 30):
            if not has_run_premarket:
                print(f"\n[🌅 09:20 AM ET PRE-MARKET BRIEFING] Generating daily trade plan...")
                try:
                    briefing = TradingStrategyEngine.generate_premarket_briefing(watchlist)
                    print(json.dumps(briefing, indent=2, default=str))
                except Exception as e:  # noqa: BLE001 - a notes failure must not kill the trading daemon
                    print(f"Pre-market briefing failed: {e}", flush=True)
                has_run_premarket = True
                has_run_closing = False
            
            sec_to_open = MarketHours.seconds_until_next_open(now)
            print(f"Staged daily plan. Market opens in {int(sec_to_open)} seconds. Waiting for opening bell...")
            time.sleep(min(sec_to_open, 30.0))
            continue

        # Phase 2: Active Trading Session (09:30 - 16:00 ET)
        elif session == "REGULAR":
            has_run_closing = False
            print(f"\n[{time_str}] ⚡ REGULAR SESSION PULSE (every {interval_seconds}s) | core + monthly momentum sleeve", flush=True)
            try:
                acc_num = RobinhoodExecutor.get_agentic_account_number()
                _risk_monitor_pulse(acc_num, dry_run)          # circuit breaker + disaster stop, every pulse
                if auto_trade:
                    _momentum_rebalance_pulse(acc_num, dry_run)  # core top-up daily, sleeve rebalance monthly
            except Exception as e:
                print(f"Trading pulse notice: {e}", flush=True)
            _shadow_hook(now)   # read-only what-if pulse, once a day after the live pulse (if enabled)

            time_left = MarketHours.seconds_until_close(now)
            print(f"Session active: {int(time_left // 60)} minutes remaining. Sleeping {interval_seconds}s until next check...", flush=True)
            time.sleep(interval_seconds)

        # Phase 3: Post-Market Closing Summary (16:00 - 16:15 ET)
        elif MarketHours.is_trading_day(now.date()) and datetime.time(16, 0) <= t_cur < datetime.time(16, 15):
            if not has_run_closing:
                print(f"\n[🔔 04:05 PM ET MARKET CLOSE SUMMARY] Compiling daily performance...")
                try:
                    summary = TradingStrategyEngine.generate_daily_closing_summary()
                    print(json.dumps(summary, indent=2, default=str))
                except Exception as e:  # noqa: BLE001
                    print(f"Closing summary failed: {e}", flush=True)
                has_run_closing = True
                has_run_premarket = False
            _daily_summary_hook(now)
            
            print("Session closed. Preparing off-hours power saving...")
            time.sleep(60.0)

        # Phase 4: Off-Hours Sleep (Nights, Weekends, Holidays)
        else:
            has_run_premarket = False
            _daily_summary_hook(now)   # catches up if the daemon was down during the close window
            sec_to_premarket = MarketHours.seconds_until_premarket(now)
            hours_to_premarket = sec_to_premarket / 3600.0
            next_premarket = MarketHours.next_premarket_time(now)
            
            print(f"🌙 Off-Hours ({session}). Next Pre-Market Briefing: {next_premarket.strftime('%Y-%m-%d %H:%M %Z')} (in {hours_to_premarket:.1f} hours).")
            
            # Sleep in chunks (max 300s or until premarket)
            sleep_chunk = min(sec_to_premarket, 300.0)
            if sleep_chunk > 0:
                print(f"Power saving sleep ({int(sleep_chunk)}s)...")
                time.sleep(sleep_chunk)


def _shadow_hook(now: datetime.datetime) -> None:
    """Never lets shadow mode disturb the live loop (lazy import: shadow imports this module)."""
    try:
        from . import shadow
        shadow.maybe_run_shadow(None, now)
    except Exception as exc:  # noqa: BLE001
        print(f"Shadow pulse notice: {exc}", flush=True)


def _daily_summary_hook(now: datetime.datetime) -> None:
    """Once per trading day after the close: build and send the daily summary (flag summary_<day>.flag)."""
    try:
        from . import summary
        summary.maybe_send_daily_summary(now)
    except Exception as exc:  # noqa: BLE001
        print(f"Daily summary notice: {exc}", flush=True)


def run_market_monitor(interval_seconds: int = 300, auto_trade: bool = False, dry_run: bool = True, watchlist: Optional[List[str]] = None):
    """Backwards-compatible alias for run_scheduled_trading_lifecycle."""
    run_scheduled_trading_lifecycle(interval_seconds=interval_seconds, auto_trade=auto_trade, dry_run=dry_run, watchlist=watchlist)


# ==============================================================================
# 7.6. Deterministic Risk Monitor (no-LLM loop)
# ==============================================================================
#
# The risk-management half of the daily trading cycle, deliberately NOT an AI
# loop: a plain Python loop that enforces the deterministic rules from today's
# plan file (written by the AI pre-market unit at 08:30 ET) against live
# positions during regular trading hours:
#   stop-loss   : price <= avg cost * (1 - stop_loss_pct/100)  -> sell full (market)
#   take-profit : price >= avg cost * (1 + take_profit_pct/100) -> sell 50%, then full
# Flag files in ~/.config/ai/.monitor_flags/ guard against repeat triggers per day.
# Log: ~/.cache/ai/risk_monitor.log
#
# Invoked by the rh-risk systemd unit (rh-risk.timer, Mon..Fri 09:25 ET) which
# loops until 16:00 ET; the loop is bounded (session gate + 16:05 ET backstop).

def _risk_log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(RISK_MONITOR_LOG), exist_ok=True)
        with open(RISK_MONITOR_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _read_plan_risk_rules(today: str) -> Dict[str, float]:
    """Read stop_loss_pct / take_profit_pct from today's AI plan or daily notes (defaults 5.0 / 8.0)."""
    rules = {"stop_loss_pct": 5.0, "take_profit_pct": 8.0}
    candidates = [
        os.path.join(os.path.expanduser("~/.config/ai/trading_vault/daily_notes"), f"{today}.md"),
        os.path.join(os.path.expanduser("~/.config/ai/trading_vault/plan"), f"{today}.md"),
        os.path.join(os.path.expanduser("~/.config/ai"), "robinhood_trading_state.json"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if path.endswith(".json"):
                data = json.loads(text)
                if isinstance(data, dict):
                    if "stop_loss_pct" in data:
                        rules["stop_loss_pct"] = float(data["stop_loss_pct"])
                    if "take_profit_pct" in data:
                        rules["take_profit_pct"] = float(data["take_profit_pct"])
                continue
            # Check YAML frontmatter or key = value / key: value
            for key in ("stop_loss_pct", "take_profit_pct"):
                m = re.search(rf"^{key}\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*$", text, re.IGNORECASE | re.MULTILINE)
                if m:
                    rules[key] = float(m.group(1))
            # Also check shorthand stop_loss: X% or stop_loss: X
            m_sl = re.search(r"stop_loss(?:_pct)?\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%?", text, re.IGNORECASE)
            if m_sl:
                try:
                    val = float(m_sl.group(1))
                    if 0.5 <= val <= 50.0:
                        rules["stop_loss_pct"] = val
                except ValueError:
                    pass
            break
        except Exception:
            continue
    return rules


def _account_snapshot(account_number: str, broker: Any = None) -> Dict[str, Any]:
    """Live equity, cash, positions and quotes for one account, in the shapes the strategy needs.
    `broker` defaults to RobinhoodExecutor (shadow mode passes a read-only wrapper)."""
    broker = RobinhoodExecutor if broker is None else broker
    port = broker.get_live_portfolio(account_number) or {}
    raw_positions = broker.get_equity_positions(account_number) or []
    bp_dict = port.get("buying_power", {})
    bp = float(bp_dict.get("buying_power", 0.0) if isinstance(bp_dict, dict) else (bp_dict or 0.0))
    cash = float(port.get("cash", bp) or bp or 0.0)
    positions = [p for p in raw_positions if float(p.get("quantity", 0) or 0) > 0]
    symbols = [str(p.get("symbol", "")).upper() for p in positions if p.get("symbol")]
    quotes = broker.get_equity_quotes(symbols) if symbols else {}
    pos_map: Dict[str, Dict[str, float]] = {}
    for p in positions:
        sym = str(p.get("symbol", "")).upper()
        qty = float(p.get("quantity", 0) or 0)
        avg = float(p.get("average_buy_price", 0) or 0)
        q = quotes.get(sym, {})
        price = float(q.get("last_trade_price") or q.get("price") or avg or 0.0)
        pos_map[sym] = {"quantity": qty, "price": price, "average_buy_price": avg}
    equity = float(port.get("total_value", 0.0) or 0.0)
    if equity <= 0:
        equity = float(port.get("equity_value", 0.0) or 0.0) + cash
    if equity <= cash + 0.01 and pos_map:
        equity = cash + sum(v["quantity"] * v["price"] for v in pos_map.values())
    # A failed MCP read comes back as {} / [] and is indistinguishable from "zero". Flag snapshots that
    # cannot be trusted so the pulses skip them instead of trading on a phantom empty book.
    problem = ""
    if not port or not any(k in port for k in ("total_value", "equity_value", "cash", "buying_power")):
        problem = "portfolio read failed"
    elif not positions and float(port.get("equity_value", 0.0) or 0.0) > 1.0:
        problem = "positions read empty but portfolio reports equity holdings"
    elif any(sym not in quotes for sym in symbols):
        problem = f"missing quotes for {sorted(s for s in symbols if s not in quotes)}"
    return {"equity": equity, "cash": cash, "positions": positions, "pos_map": pos_map, "quotes": quotes,
            "ok": not problem, "problem": problem}


STALE_BARS_DAYS = 7   # daily history whose last bar is older than this is not used to trade


def _bar_date(bar: Dict[str, Any]) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(str(bar.get("datetime", ""))[:10])
    except ValueError:
        return None


def _fetch_strategy_bars(symbols: List[str], range_period: str = "1y", broker: Any = None) -> Dict[str, List[Dict[str, Any]]]:
    """Daily bars for the strategy (Robinhood MCP first, Yahoo fallback). Symbols with < 30 bars are dropped.
    `broker` (shadow mode: the read-only wrapper) is the Robinhood source; default RobinhoodExecutor."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    kw: Dict[str, Any] = {} if broker is None else {"broker": broker}
    for s in symbols:
        try:
            b = FinancialData.fetch_historical(s, range_period=range_period, interval="1d", **kw)
            last = _bar_date(b[-1]) if b else None
            if last is not None and (MarketHours.now_et().date() - last).days > STALE_BARS_DAYS:
                _risk_log(f"STRATEGY: {s} skipped (stale history, last bar {last})")
            elif len(b) >= 30:
                out[s] = b
            else:
                _risk_log(f"STRATEGY: {s} skipped ({len(b)} bars)")
        except Exception as exc:  # noqa: BLE001
            _risk_log(f"STRATEGY: {s} history fetch failed: {exc}")
    return out


def _rebalance_flag_path(month: str) -> str:
    return os.path.join(RISK_MONITOR_FLAGS, f"rebalance_{month}.json")


def _save_json(path: str, data: Dict[str, Any]) -> None:
    """Atomic write (temp file + rename): a crash mid-write must not leave a corrupt state flag."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _momentum_rebalance_pulse(account_number: str, dry_run: bool) -> None:
    """Once-a-day strategy pulse: monthly sleeve rebalance (sells, then buys on a later pulse
    once cash has settled), then a daily core top-up. Idempotent via flag files; no LLM."""
    now = MarketHours.now_et()
    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    if now.time() < MomentumStrategy.REBALANCE_EARLIEST_ET:
        return  # never trade the opening auction; the disaster-stop pass still runs

    halt_flag = SESSION_HALT_FLAG.format(date=today)
    if os.path.exists(halt_flag):
        _risk_log("STRATEGY: session halt flag present (circuit breaker Tier 3 today); no strategy orders.")
        return

    snap = _account_snapshot(account_number)
    equity, cash, pos_map = snap["equity"], snap["cash"], snap["pos_map"]
    if not snap.get("ok", True):
        _risk_log(f"STRATEGY: account snapshot incomplete ({snap.get('problem')}); skipping pulse.")
        return
    if equity <= 0:
        _risk_log("STRATEGY: no equity reading; skipping pulse.")
        return

    tier, cb_msg = ComplianceAndRiskGuard.check_daily_circuit_breaker(account_number, equity)
    buys_allowed = tier == 0
    if tier >= 3:
        _save_json(halt_flag, {"tier": tier, "reason": cb_msg, "ts": now.isoformat()})
        _risk_log(f"🚨 STRATEGY: {cb_msg} -> halting all strategy orders for today (no forced liquidation).")
        return

    hold = MomentumStrategy.load_hold_list()
    os.makedirs(RISK_MONITOR_FLAGS, exist_ok=True)
    flag = _rebalance_flag_path(month)
    state: Optional[Dict[str, Any]] = None
    if os.path.exists(flag):
        try:
            with open(flag, encoding="utf-8") as fh:
                state = json.load(fh)
            if not isinstance(state, dict):
                raise ValueError("flag is not a JSON object")
        except (OSError, ValueError) as exc:
            # Re-planning here could repeat orders already placed this month: fail closed.
            _risk_log(f"STRATEGY: rebalance flag {flag} unreadable ({exc}); no strategy orders until it is fixed or removed.")
            return
        if state.get("dry_run") and not dry_run:
            _risk_log(f"STRATEGY: {month} flag came from a dry run; planning the live rebalance.")
            state = None

    if state is None:
        # First strategy pulse this month: rank the universe and plan the sleeve.
        universe = MomentumStrategy.universe()
        bars = _fetch_strategy_bars(universe + ["SPY"])
        spy_bars = bars.pop("SPY", [])
        # Missing data must not read as "no eligible names / no regime" (that would liquidate the sleeve).
        if len(spy_bars) < MomentumStrategy.REGIME_SMA_BARS or len(bars) < max(1, len(universe) // 2):
            _risk_log(f"STRATEGY: insufficient market data (SPY {len(spy_bars)} bars, {len(bars)}/{len(universe)} "
                      f"universe symbols); rebalance deferred to a later pulse.")
            return
        regime = MomentumStrategy.regime(spy_bars)
        ranked = MomentumStrategy.rank(bars)
        incumbents = MomentumStrategy.sleeve_incumbents(pos_map, hold)
        rank_buffer = MomentumStrategy.rank_buffer()
        targets = MomentumStrategy.select_sleeve(ranked, regime["regime"], incumbents=incumbents, buffer=rank_buffer)
        park = MomentumStrategy.choose_park(regime["regime"], targets, _fetch_strategy_bars)
        plan = MomentumStrategy.plan_rebalance(equity, cash, pos_map, targets, hold, park=park)
        state = {
            "month": month, "date": today, "dry_run": dry_run,
            "equity": round(equity, 2), "cash": round(cash, 2),
            "regime": regime, "targets": targets, "hold": sorted(hold),
            "incumbents": incumbents, "rank_buffer": rank_buffer,
            "park": park, "park_requested": MomentumStrategy.bear_park(),
            "ranking": ranked[:10],
            "sells_pending": plan["sells"], "buys_pending": plan["buys"],
            "sells_done": [], "buys_done": [], "done": False,
        }
        _save_json(flag, state)
        _risk_log(f"STRATEGY REBALANCE {month}: regime={regime['regime']} (SPY {regime.get('close')} vs SMA200 {regime.get('sma200')}) "
                  f"targets={targets or (f'PARK {park}' if park else 'CASH')} sells={[s['symbol'] for s in plan['sells']]} "
                  f"buys={[(b['symbol'], b['dollar_amount']) for b in plan['buys']]} equity=${equity:,.2f} cash=${cash:,.2f}")
        try:
            ObsidianTradingVault.save_rebalance_note(month, state)
        except Exception:
            pass

    _just_completed = False
    if not state.get("done"):
        # ---- sells first ----
        if state.get("sells_pending"):
            still: List[Dict[str, Any]] = []
            executed_live = False
            for s in state["sells_pending"]:
                sym = s["symbol"]
                if sym in hold:
                    continue
                qty = float(s["quantity"])
                avg = float(pos_map.get(sym, {}).get("average_buy_price", 0) or 0)
                price = float(pos_map.get(sym, {}).get("price", 0) or 0)
                pnl_pct = ((price - avg) / avg * 100.0) if avg > 0 and price > 0 else 0.0
                if sym not in pos_map:
                    _risk_log(f"STRATEGY SELL {sym}: no longer held; dropping from plan.")
                    continue
                if dry_run:
                    _risk_log(f"STRATEGY [DRY RUN] SELL {sym} {qty} sh @ ~${price:.2f} ({pnl_pct:+.1f}%) reason={s['reason']}")
                    state["sells_done"].append(s)
                    state["dry_run_proceeds"] = round(float(state.get("dry_run_proceeds", 0.0)) + qty * price, 2)
                    continue
                try:
                    res = RobinhoodExecutor.execute_market_order(account_number, sym, "sell", quantity=_fmt_qty(qty))
                    _risk_log(f"STRATEGY SELL {sym} {qty} sh @ ~${price:.2f} ({pnl_pct:+.1f}%) reason={s['reason']} response={str(res)[:160]}")
                except Exception as exc:  # noqa: BLE001
                    # Includes OrderNotConfirmed: safe to retry, the next pulse drops names no longer held.
                    _risk_log(f"STRATEGY SELL {sym} FAILED: {exc}")
                    still.append(s)
                    continue
                state["sells_done"].append(s)
                executed_live = True
                try:
                    ComplianceAndRiskGuard.record_trade(account_number, sym, "SELL", price=price, qty=qty, pnl_pct=pnl_pct, reason=s["reason"])
                    ObsidianTradingVault.log_trade_execution({"ticker": sym, "action": "SELL", "qty": qty, "price": price,
                                                              "pnl_pct": pnl_pct, "reason": s["reason"], "strategy": "MOMENTUM_SLEEVE"})
                except Exception as exc:  # noqa: BLE001
                    _risk_log(f"STRATEGY SELL {sym}: bookkeeping failed after order: {exc}")
            state["sells_pending"] = still
            _save_json(flag, state)
            if executed_live:
                _risk_log("STRATEGY: sells placed; buys resume on the next pulse once cash settles.")
                return
        # ---- then buys ----
        if state.get("buys_pending"):
            if not buys_allowed:
                _risk_log(f"STRATEGY: buys deferred ({cb_msg})")
                _save_json(flag, state)
                return
            deployable = cash - equity * MomentumStrategy.CASH_BUFFER_PCT
            if dry_run:
                deployable += float(state.get("dry_run_proceeds", 0.0))  # simulated sells never produced real cash
            still = []
            pending = list(state["buys_pending"])
            for i, b in enumerate(pending):
                sym = b["symbol"]
                amt = round(min(float(b["dollar_amount"]), deployable), 2)
                if amt < MomentumStrategy.MIN_ORDER_DOLLARS:
                    still.append(b)  # wait for cash
                    continue
                if dry_run:
                    _risk_log(f"STRATEGY [DRY RUN] BUY {sym} ${amt:.2f} reason={b['reason']}")
                    state["buys_done"].append({**b, "dollar_amount": amt})
                    deployable -= amt
                    continue
                done_rec = {**b, "dollar_amount": amt}
                try:
                    res = RobinhoodExecutor.execute_market_order(account_number, sym, "buy", dollar_amount=str(amt))
                    _risk_log(f"STRATEGY BUY {sym} ${amt:.2f} reason={b['reason']} response={str(res)[:160]}")
                except OrderNotConfirmed as exc:
                    # It may have been placed: re-sending could double the position. Not retried.
                    _risk_log(f"STRATEGY BUY {sym} ${amt:.2f} UNCONFIRMED ({exc}); not retried - check open orders.")
                    done_rec["unconfirmed"] = True
                except Exception as exc:  # noqa: BLE001
                    _risk_log(f"STRATEGY BUY {sym} FAILED: {exc}")
                    still.append(b)
                    continue
                # Persist before any bookkeeping so a crash or ledger error cannot re-queue a placed order.
                state["buys_done"].append(done_rec)
                state["buys_pending"] = still + pending[i + 1:]
                _save_json(flag, state)
                deployable -= amt
                try:
                    ComplianceAndRiskGuard.record_trade(account_number, sym, "BUY")
                    ObsidianTradingVault.log_trade_execution({"ticker": sym, "action": "BUY", "dollar_amount": amt,
                                                              "reason": b["reason"], "strategy": "MOMENTUM_SLEEVE"})
                except Exception as exc:  # noqa: BLE001
                    _risk_log(f"STRATEGY BUY {sym}: bookkeeping failed after order: {exc}")
            state["buys_pending"] = still
            if still:
                _risk_log(f"STRATEGY: {len(still)} buy(s) waiting for cash (deployable ${deployable:.2f}).")
        if not state.get("sells_pending") and not state.get("buys_pending"):
            state["done"] = True
            state["completed"] = today
            _just_completed = True
            _risk_log(f"STRATEGY REBALANCE {month}: complete.")
        _save_json(flag, state)
        if not state["done"] or _just_completed:
            # Rebalance still in flight, or its orders were placed on this pulse (the cash snapshot from
            # the top of the pulse is stale): the daily core top-up waits for the next pulse.
            return

    # ---- daily core top-up (one per day) ----
    if not buys_allowed:
        _risk_log(f"STRATEGY: core top-up skipped ({cb_msg})")
        return
    core_flag = os.path.join(RISK_MONITOR_FLAGS, f"core_topup_{today}.flag")
    if os.path.exists(core_flag):
        try:
            with open(core_flag, encoding="utf-8") as fh:
                from_dry_run = bool(json.load(fh).get("dry_run"))
        except (OSError, ValueError, AttributeError):
            from_dry_run = False   # unreadable: treat as a real top-up (fail closed)
        if dry_run or not from_dry_run:
            return
    topup = MomentumStrategy.plan_core_topup(equity, cash, pos_map)
    if not topup:
        return
    sym, amt = topup["symbol"], topup["dollar_amount"]
    if dry_run:
        _risk_log(f"STRATEGY [DRY RUN] CORE TOP-UP BUY {sym} ${amt:.2f}")
        _save_json(core_flag, {"ts": now.isoformat(), "dry_run": True, **topup})
        return
    try:
        res = RobinhoodExecutor.execute_market_order(account_number, sym, "buy", dollar_amount=str(amt))
        _risk_log(f"STRATEGY CORE TOP-UP BUY {sym} ${amt:.2f} response={str(res)[:160]}")
    except OrderNotConfirmed as exc:
        _risk_log(f"STRATEGY CORE TOP-UP {sym} ${amt:.2f} UNCONFIRMED ({exc}); not retried today - check open orders.")
        _save_json(core_flag, {"ts": now.isoformat(), "unconfirmed": True, **topup})
        return
    except Exception as exc:  # noqa: BLE001
        _risk_log(f"STRATEGY CORE TOP-UP {sym} FAILED: {exc}")
        return
    _save_json(core_flag, {"ts": now.isoformat(), **topup})
    try:
        ComplianceAndRiskGuard.record_trade(account_number, sym, "BUY")
        ObsidianTradingVault.log_trade_execution({"ticker": sym, "action": "BUY", "dollar_amount": amt,
                                                  "reason": "CORE_TOPUP", "strategy": "MOMENTUM_SLEEVE"})
    except Exception as exc:  # noqa: BLE001
        _risk_log(f"STRATEGY CORE TOP-UP {sym}: bookkeeping failed after order: {exc}")


def _risk_monitor_pulse(account_number: str, dry_run: bool,
                        stop_loss_pct: Optional[float] = None, take_profit_pct: Optional[float] = None) -> None:
    """Every-pulse safety pass: circuit breaker (halt only, never liquidates) and the -20%
    disaster stop on sleeve names. Core ETFs and hold-listed symbols are never sold.
    `stop_loss_pct` / `take_profit_pct` are accepted for backwards compatibility and ignored."""
    now = MarketHours.now_et()
    today = now.strftime("%Y-%m-%d")
    snap = _account_snapshot(account_number)
    equity, positions, quotes, pos_map = snap["equity"], snap["positions"], snap["quotes"], snap["pos_map"]
    os.makedirs(RISK_MONITOR_FLAGS, exist_ok=True)
    if not snap.get("ok", True) and not positions:
        # Nothing trustworthy to act on, and feeding a phantom equity into the circuit breaker could arm it.
        _risk_log(f"RISK pulse skipped: account snapshot incomplete ({snap.get('problem')})")
        return

    if snap.get("ok", True):
        tier, cb_msg = ComplianceAndRiskGuard.check_daily_circuit_breaker(account_number, equity)
    else:
        tier, cb_msg = 0, f"circuit breaker not updated: snapshot incomplete ({snap.get('problem')})"
    if tier >= 3:
        halt_flag = SESSION_HALT_FLAG.format(date=today)
        if not os.path.exists(halt_flag):
            _save_json(halt_flag, {"tier": tier, "reason": cb_msg, "ts": now.isoformat()})
            _risk_log(f"🚨 {cb_msg} -> session halt flag written. Positions are HELD (no forced liquidation).")

    hold = MomentumStrategy.load_hold_list()
    hits = MomentumStrategy.disaster_stop_hits(positions, quotes, hold,
                                              exempt=MomentumStrategy.park_candidates(MomentumStrategy.bear_park()))
    _, pdt_reason = ComplianceAndRiskGuard.can_day_trade(account_number, equity)
    for h in hits:
        sym, qty, price, pnl_pct = h["symbol"], h["quantity"], h["price"], h["pnl_pct"]
        stop_flag = os.path.join(RISK_MONITOR_FLAGS, f"stop_loss_{today}_{sym}.flag")
        if os.path.exists(stop_flag):
            continue
        is_same_day = ComplianceAndRiskGuard.is_same_day_position(account_number, sym)
        can_dt, _ = ComplianceAndRiskGuard.can_day_trade(account_number, equity)
        if is_same_day and not can_dt:
            _risk_log(f"PDT GUARD {sym}: bought today and day-trade budget exhausted; disaster stop deferred to next session.")
            continue
        _risk_log(f"RISK {sym}: DISASTER_STOP {qty} sh @ ${price:.2f} ({pnl_pct:+.1f}% vs avg ${h['avg_cost']:.2f}, limit -{MomentumStrategy.DISASTER_STOP_PCT:.0f}%)")
        if dry_run:
            _risk_log(f"RISK [DRY RUN] {sym}: would sell {qty} sh.")
            continue
        try:
            res = RobinhoodExecutor.execute_market_order(account_number, sym, "sell", quantity=_fmt_qty(qty))
            _risk_log(f"RISK {sym}: order response={str(res)[:200]}")
        except Exception as exc:  # noqa: BLE001
            _risk_log(f"RISK {sym}: order FAILED: {exc}")
            continue
        _save_json(stop_flag, {"ts": now.isoformat(), "price": price, "qty": qty, "pnl_pct": pnl_pct, "reason": "DISASTER_STOP"})
        try:
            ComplianceAndRiskGuard.record_trade(account_number, sym, "SELL", price=price, qty=qty, pnl_pct=pnl_pct, reason="DISASTER_STOP")
            ObsidianTradingVault.log_trade_execution({"ticker": sym, "action": "SELL", "qty": qty, "price": price,
                                                      "pnl_pct": pnl_pct, "reason": "DISASTER_STOP", "strategy": "MOMENTUM_SLEEVE"})
        except Exception as exc:  # noqa: BLE001
            _risk_log(f"RISK {sym}: bookkeeping failed after order: {exc}")

    summary = ", ".join(
        f"{sym} {((v['price'] - v['average_buy_price']) / v['average_buy_price'] * 100.0):+.1f}%"
        for sym, v in sorted(pos_map.items()) if v["average_buy_price"] > 0
    ) or "no positions"
    _risk_log(f"RISK pulse: equity ${equity:,.2f} | {summary} | {cb_msg} | {pdt_reason}")


def run_risk_monitor(interval_seconds: int = 900, dry_run: bool = False,
                     once: bool = False, force_session: bool = False) -> None:
    """Deterministic risk-management loop (no LLM). Bounded: REGULAR session + 16:05 ET backstop."""
    _risk_log(f"RISK-MONITOR start mode={'DRY-RUN (no orders)' if dry_run else 'LIVE (real market orders)'} "
              f"interval={interval_seconds}s once={once}")
    while True:
        now = MarketHours.now_et()
        session = "REGULAR" if force_session else MarketHours.get_market_session(now)
        if session != "REGULAR":
            if once:
                _risk_log(f"RISK-MONITOR {session} - outside RTH, exiting")
                return
            secs_open = MarketHours.seconds_until_next_open(now)
            if secs_open and secs_open < 6 * 3600:  # pre-market: wait for the open
                time.sleep(min(interval_seconds, max(10, int(secs_open))))
                continue
            _risk_log(f"RISK-MONITOR {session} - exiting")
            return
        today = MarketHours.now_et().strftime("%Y-%m-%d")
        rules = _read_plan_risk_rules(today)
        try:
            account_number = RobinhoodExecutor.get_agentic_account_number()
            _risk_log(f"RISK-MONITOR pulse REGULAR account={account_number} "
                      f"stop_loss={rules['stop_loss_pct']}% take_profit={rules['take_profit_pct']}% dry_run={dry_run}")
            _risk_monitor_pulse(account_number, dry_run, rules["stop_loss_pct"], rules["take_profit_pct"])
        except Exception as exc:  # noqa: BLE001
            _risk_log(f"RISK-MONITOR pulse error: {exc}")
        if once:
            return
        if MarketHours.now_et().strftime("%H%M") >= "1605":  # backstop past market close
            _risk_log("RISK-MONITOR past-close backstop reached - exiting")
            return
        time.sleep(interval_seconds)


# ==============================================================================
# 7.7. Backtest (same MomentumStrategy functions the live loop trades with)
# ==============================================================================

def run_backtest(years: int = 5, start: Optional[str] = None, start_cash: float = 600.0,
                 top_n: Optional[int] = None, sleeve: bool = True, universe: Optional[List[str]] = None,
                 slippage_pct: float = 0.05, verbose: bool = True, core_target: Optional[float] = None,
                 bars_override: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """Daily-bar simulation of the core + monthly momentum sleeve book.

    Rebalance on the first trading day of each month at the close (signal and fill on the same
    close, i.e. slightly optimistic by one day); disaster stop checked against daily lows; core
    topped up daily. Reports total return, CAGR, max drawdown and buy-and-hold benchmarks.
    `sleeve=False` runs the core-only variant for comparison (core target defaults to 90% there,
    i.e. the sleeve's 30% goes to core instead of sitting in cash).
    """
    if core_target is None:
        core_target = MomentumStrategy.CORE_TARGET_PCT if sleeve else MomentumStrategy.CORE_TARGET_PCT + MomentumStrategy.SLEEVE_TARGET_PCT
    syms = list(universe or MomentumStrategy.universe())
    core = list(MomentumStrategy.CORE_SYMBOLS)
    if bars_override is not None:
        bars = {s: b for s, b in bars_override.items() if len(b) >= 2}
    else:
        bars = {}
        for s in syms + core + ["SPY"]:
            b = FinancialData.fetch_historical_yahoo(s, range_period=f"{years}y")
            if len(b) >= 260:
                bars[s] = b
            elif verbose:
                print(f"  (skipping {s}: {len(b)} bars)")
    if "SPY" not in bars or not all(c in bars for c in core):
        raise RuntimeError("backtest needs SPY and core ETF history")
    syms = [s for s in syms if s in bars]
    idx = {s: {b["datetime"]: i for i, b in enumerate(bars[s])} for s in bars}
    dates = [b["datetime"] for b in bars["SPY"]]
    warm = min(MomentumStrategy.REGIME_SMA_BARS + 10, max(0, len(dates) - 2))
    dates = dates[warm:]
    if start:
        dates = [d for d in dates if d >= start]
    if len(dates) < 2:
        raise RuntimeError("not enough history for the requested window")

    slip = slippage_pct / 100.0
    cash = float(start_cash)
    pos: Dict[str, Dict[str, float]] = {}
    trades: List[Dict[str, Any]] = []
    curve: List[Tuple[str, float]] = []
    last_month = None

    def _px(s: str, d: str, key: str = "close") -> Optional[float]:
        i = idx[s].get(d)
        return float(bars[s][i][key]) if i is not None else None

    def _buy(s: str, amt: float, price: float, d: str, reason: str) -> None:
        nonlocal cash
        fill = price * (1 + slip)
        qty = amt / fill
        cash -= amt
        p = pos.get(s)
        if p:
            tot = p["qty"] + qty
            p["entry"] = (p["entry"] * p["qty"] + fill * qty) / tot
            p["qty"] = tot
        else:
            pos[s] = {"qty": qty, "entry": fill}
        trades.append({"date": d, "symbol": s, "side": "BUY", "amount": round(amt, 2), "reason": reason})

    def _sell(s: str, price: float, d: str, reason: str) -> None:
        nonlocal cash
        p = pos.pop(s)
        fill = price * (1 - slip)
        cash += p["qty"] * fill
        trades.append({"date": d, "symbol": s, "side": "SELL", "pnl_pct": round((fill - p["entry"]) / p["entry"] * 100, 2),
                       "pnl": round(p["qty"] * (fill - p["entry"]), 2), "reason": reason})

    for d in dates:
        prices = {s: _px(s, d) for s in bars}
        prices = {s: p for s, p in prices.items() if p}
        # disaster stop on the day's low (sleeve only)
        for s in list(pos):
            if s in core or d not in idx[s]:
                continue
            stop = pos[s]["entry"] * (1 - MomentumStrategy.DISASTER_STOP_PCT / 100.0)
            if _px(s, d, "low") <= stop:
                _sell(s, min(_px(s, d, "open"), stop), d, "DISASTER_STOP")
        eq = cash + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in pos.items())
        month = d[:7]
        if month != last_month:
            last_month = month
            as_of = {s: idx[s][d] for s in syms if d in idx[s]}
            regime = MomentumStrategy.regime(bars["SPY"], as_of=idx["SPY"][d])
            ranked = MomentumStrategy.rank({s: bars[s] for s in as_of}, as_of=as_of)
            incumbents = [s for s in pos if s not in core]
            targets = (MomentumStrategy.select_sleeve(ranked, regime["regime"], top_n, incumbents=incumbents,
                                                      buffer=MomentumStrategy.rank_buffer()) if sleeve else [])
            pos_map = {s: {"quantity": p["qty"], "price": prices.get(s, p["entry"])} for s, p in pos.items()}
            plan = MomentumStrategy.plan_rebalance(eq, cash, pos_map, targets)
            for sl in plan["sells"]:
                if sl["symbol"] in prices:
                    _sell(sl["symbol"], prices[sl["symbol"]], d, sl["reason"])
            for b in plan["buys"]:
                if b["symbol"] in prices:
                    _buy(b["symbol"], b["dollar_amount"], prices[b["symbol"]], d, b["reason"])
        pos_map = {s: {"quantity": p["qty"], "price": prices.get(s, p["entry"])} for s, p in pos.items()}
        topup = MomentumStrategy.plan_core_topup(eq, cash, pos_map, core_target=core_target)
        if topup and topup["symbol"] in prices:
            _buy(topup["symbol"], topup["dollar_amount"], prices[topup["symbol"]], d, "CORE_TOPUP")
        eq = cash + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in pos.items())
        curve.append((d, eq))

    start_eq, end_eq = curve[0][1], curve[-1][1]
    peak, max_dd = start_eq, 0.0
    for _, e in curve:
        peak = max(peak, e)
        max_dd = min(max_dd, (e - peak) / peak * 100.0)
    years_span = max(len(curve) / 252.0, 1e-9)
    cagr = ((end_eq / start_eq) ** (1.0 / years_span) - 1.0) * 100.0 if start_eq > 0 else 0.0
    by_year: Dict[str, float] = {}
    first_of_year: Dict[str, float] = {}
    for d, e in curve:
        y = d[:4]
        first_of_year.setdefault(y, e)
        by_year[y] = (e / first_of_year[y] - 1.0) * 100.0
    bench = {}
    for s in core + ["SPY"]:
        p0, p1 = _px(s, dates[0]), _px(s, dates[-1])
        if p0 and p1:
            bench[s] = round((p1 / p0 - 1.0) * 100.0, 1)
    sleeve_trades = [t for t in trades if t["side"] == "SELL"]
    wins = [t for t in sleeve_trades if t["pnl"] > 0]
    result = {
        "period": [dates[0], dates[-1]], "days": len(curve), "start": round(start_eq, 2), "end": round(end_eq, 2),
        "total_return_pct": round((end_eq / start_eq - 1.0) * 100.0, 1), "cagr_pct": round(cagr, 1),
        "max_drawdown_pct": round(max_dd, 1), "by_year_pct": {y: round(v, 1) for y, v in by_year.items()},
        "benchmarks_buy_hold_pct": bench, "sleeve_round_trips": len(sleeve_trades),
        "sleeve_win_rate_pct": round(len(wins) / len(sleeve_trades) * 100.0, 0) if sleeve_trades else None,
        "sleeve_pnl": round(sum(t["pnl"] for t in sleeve_trades), 2), "trades": trades, "curve": curve,
        "final_positions": {s: round(p["qty"] * prices.get(s, p["entry"]), 2) for s, p in pos.items()}, "final_cash": round(cash, 2),
    }
    if verbose:
        label = f"core {int(core_target*100)}% + sleeve {int(MomentumStrategy.SLEEVE_TARGET_PCT*100)}% top-{top_n or MomentumStrategy.SLEEVE_TOP_N}" if sleeve else f"core only {int(core_target*100)}%"
        print(f"\n=== BACKTEST [{label}] {dates[0]} .. {dates[-1]} ({len(curve)} trading days, ${start_cash:,.0f} start) ===")
        print(f"  end ${end_eq:,.0f}  total {result['total_return_pct']:+.1f}%  CAGR {cagr:+.1f}%  max drawdown {max_dd:.1f}%")
        print("  by year: " + "  ".join(f"{y} {v:+.1f}%" for y, v in result["by_year_pct"].items()))
        print("  buy & hold: " + "  ".join(f"{s} {v:+.1f}%" for s, v in bench.items()))
        if sleeve:
            print(f"  sleeve round trips {len(sleeve_trades)}  win rate {result['sleeve_win_rate_pct']}%  sleeve P&L ${result['sleeve_pnl']:+,.0f}")
            from collections import Counter
            print("  exit reasons: " + ", ".join(f"{k} {v}" for k, v in Counter(t['reason'] for t in sleeve_trades).items()))
        print(f"  final: cash ${cash:,.0f} " + " ".join(f"{s} ${v:,.0f}" for s, v in result["final_positions"].items()))
    return result
