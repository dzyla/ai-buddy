"""Command-line dispatch for `robinhood_trader.py <command>`."""

import os
import sys
import json
import subprocess
from typing import List, Optional

from .config import DEFAULT_WATCHLIST
from .journal import ObsidianTradingVault
from .market_hours import MarketHours
from .advisor import NewsSentimentEngine, TradingStrategyEngine
from .strategy import MomentumStrategy
from .broker import RobinhoodExecutor, authenticate_robinhood_mcp
from .risk import ComplianceAndRiskGuard
from .portfolio import PortfolioAuditor, TradingDataManager
from .daemon import run_backtest, run_market_monitor, run_risk_monitor, run_scheduled_trading_lifecycle

# Checkout (or release) root: the directory holding robinhood_trader.py and this package.
CHECKOUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOYED_CURRENT = os.path.expanduser("~/.local/share/ai-trader/current")


# ==============================================================================
# 8. Command Line Interface (CLI) Dispatch
# ==============================================================================

def print_help():
    help_text = """
Robinhood Agentic Trading & Market Analysis CLI
================================================
Usage:
  robinhood_trader.py <command> [arguments]

Commands:
  summary [account|all]      Compact, token-efficient executive summary (<200 tokens) for AI models.
  portfolio [account]        Query live positions (supports --summary, --filter, --top, --json, --csv).
  orders [account]           View recent equity orders, executions, and statuses.
  pnl [account]              View realized and unrealized profit & loss performance.
  trades [account]           View closed trade-by-trade P&L history.
  buy <TICKER> [DOLLAR]      Execute market buy order on Agentic account (e.g. buy SMCI 25).
  sell <TICKER> [QTY|all]    Execute market sell order on Agentic account (e.g. sell AVGO all).
  accounts                   List all authorized Robinhood brokerage accounts & permissions.
  audit [account]            Full quantitative risk audit, concentration checks & health score (0-100).
  harvest-losses [account]   Tax-loss harvesting candidate breakdown with dollar savings & wash-sale guidance.
  rebalance-plan [account]   Concrete 4-step rebalance plan (cut dead money, clean dust, trim winners).
  export [account] [dir]     Export full portfolio & audit datasets to disk (JSON & CSV).
  status                     Display market status, Eastern time, Mountain / local time, and session.
  analyze <TICKER...>        Deep technical analysis, indicators, news sentiment & risk levels.
  scan [TICKERS...]          Scan watchlist for highest conviction buy/sell opportunities.
  news <TICKER/QUERY>        Fetch latest financial news headlines and calculate sentiment score.
  rebalance                  Analyze portfolio allocation and generate optimal profit trades.
  monitor [--auto-trade]     Autonomous loop: 60% VTI/QQQ core, 30% monthly top-3 momentum sleeve, 10% cash.
  risk-monitor [--live]      Deterministic safety pass (circuit breaker + -20% disaster stop).
  backtest [--years N]       Simulate the core + monthly momentum sleeve rules on Yahoo daily bars
                             (--start YYYY-MM-DD, --top N, --cash X, --no-core-only).
  service <start|stop|...>   Manage background systemd service.
  auth                       Verify stored credentials status (cached and active).

Options for 'portfolio':
  --summary, -s              Output concise executive summary instead of 70+ position table.
  --filter <type>            Filter positions by: losers, winners, dust, dead-money.
  --top <N>                  Limit output to top N positions by dollar value.
  --json                     Output raw structured JSON to stdout.
  --csv                      Output clean CSV to stdout.

Examples:
  ./robinhood_trader.py summary
  ./robinhood_trader.py summary agentic
  ./robinhood_trader.py orders agentic
  ./robinhood_trader.py pnl agentic
  ./robinhood_trader.py accounts
  ./robinhood_trader.py portfolio --summary
  ./robinhood_trader.py portfolio --filter losers
  ./robinhood_trader.py audit
  ./robinhood_trader.py harvest-losses
  ./robinhood_trader.py rebalance-plan
  ./robinhood_trader.py status
"""
    print(help_text.strip())


def _dispatch():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print_help()
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "status":
        now_et = MarketHours.now_et()
        now_loc = MarketHours.now_local()
        session = MarketHours.get_market_session(now_et)
        is_open = MarketHours.is_market_open(now_et)
        next_open = MarketHours.next_market_open(now_et)
        
        print("=" * 70)
        print("  US STOCK MARKET & ROBINHOOD STATUS")
        print("=" * 70)
        print(f"  Eastern Time (ET) : {now_et.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")
        print(f"  Local Time (MT)   : {now_loc.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")
        print(f"  Market Session    : {session}")
        print(f"  Regular Hours     : {'OPEN (09:30 - 16:00 ET / 07:30 - 14:00 MT)' if is_open else 'CLOSED'}")
        if is_open:
            rem = MarketHours.seconds_until_close(now_et)
            print(f"  Time to Close     : {int(rem // 60)} minutes")
        else:
            next_open_loc = next_open.astimezone()
            print(f"  Next Market Open  : {next_open.strftime('%Y-%m-%d %I:%M %p %Z')} ({next_open_loc.strftime('%I:%M %p %Z')})")
            print(f"  Countdown         : in {MarketHours.seconds_until_next_open(now_et)/3600:.1f} hours")
        print("=" * 70)

    elif cmd in ("summary", "brief", "overview"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        if acc and acc.lower() == "all":
            accounts = RobinhoodExecutor.get_accounts()
            if not accounts:
                audit_res = PortfolioAuditor.audit()
                print(PortfolioAuditor.format_executive_summary(audit_res))
            else:
                for a in accounts:
                    a_num = a.get("account_number")
                    if a_num:
                        audit_res = PortfolioAuditor.audit(account_number=a_num)
                        print(PortfolioAuditor.format_executive_summary(audit_res))
                        print()
        else:
            resolved_acc = RobinhoodExecutor.resolve_account(acc) if acc else None
            audit_res = PortfolioAuditor.audit(account_number=resolved_acc)
            print(PortfolioAuditor.format_executive_summary(audit_res))

    elif cmd in ("audit", "health", "health-check"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        audit_res = PortfolioAuditor.audit(account_number=acc)
        s = audit_res["summary"]
        cb = audit_res["cash_buffer"]
        semi = audit_res["semi_overlap"]
        dead = audit_res["dead_money"]
        dust = audit_res["dust_positions"]
        tlh = audit_res["tax_loss_harvesting"]

        print("=" * 80)
        print(f"  QUANTITATIVE PORTFOLIO AUDIT & RISK REPORT (Account: {audit_res['account_number']})")
        print("=" * 80)
        print(f"  Health Score      : {audit_res['health_score']}/100")
        print(f"  Total Value       : ${s['total_value']:,.2f} (Equities: ${s['equity_value']:,.2f}, Cash: ${s['cash']:,.2f})")
        print(f"  Cash Buffer       : {cb['current_pct']:.1f}% (Target: >={cb['target_pct']}%, Status: {cb['status']})")
        if cb['deficit_dollars'] > 0:
            print(f"  Cash Deficit      : ${cb['deficit_dollars']:,.2f} required to restore 15% safety buffer")
        print(f"  Cost Basis & P&L  : ${s['total_cost_basis']:,.2f} -> Unrealized P&L: {'+' if s['net_unrealized_pnl']>=0 else ''}${s['net_unrealized_pnl']:,.2f} ({s['net_unrealized_pnl_pct']:+.2f}%)")
        print(f"  Active Positions  : {s['active_position_count']} symbols")
        print("-" * 80)
        print("  CONCENTRATION & SECTOR RISKS:")
        if audit_res.get("concentration_alerts"):
            for c in audit_res["concentration_alerts"]:
                print(f"    ⚠️ {c['symbol']}: {c['weight_pct']:.1f}% weight (${c['current_value']:,.2f}) - {c['status']}")
        else:
            print("    ✓ No single stock breaches the 15% concentration cap.")
        print(f"    Semiconductors  : {semi['weight_pct']:.1f}% of equities across {len(semi['symbols'])} single stocks ({', '.join(semi['symbols'][:6])}...)")
        print("-" * 80)
        print("  OPPORTUNITIES & OPTIMIZATIONS:")
        print(f"    Dead Money (<$50, down >40%) : {dead['count']} positions | Value: ${dead['total_value']:,.2f} | Harvestable Loss: ${dead['harvestable_loss']:,.2f}")
        print(f"    Dust Positions (<$10)        : {dust['count']} positions | Value: ${dust['total_value']:,.2f}")
        print(f"    Total Harvestable Tax Losses : ${tlh['total_harvestable_loss']:,.2f} across {tlh['loser_count']} losing positions")
        print("-" * 80)
        print("  ACTIONABLE RECOMMENDATIONS:")
        for idx, act in enumerate(audit_res.get("action_plan", []), 1):
            print(f"    {idx}. {act}")
        print("=" * 80)
        print(f"  Full Audit JSON: {TradingDataManager.get_cache_dir()}/audit_{audit_res['account_number']}.json")
        print("=" * 80)

    elif cmd in ("harvest-losses", "tax-loss", "tax-losses"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        audit_res = PortfolioAuditor.audit(account_number=acc)
        print(PortfolioAuditor.format_harvest_losses(audit_res))

    elif cmd in ("rebalance-plan", "action-plan", "plan"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        audit_res = PortfolioAuditor.audit(account_number=acc)
        print(PortfolioAuditor.format_rebalance_plan(audit_res))

    elif cmd in ("export", "dump"):
        acc = None
        out_dir = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                if acc is None:
                    acc = a
                elif out_dir is None:
                    out_dir = a
        acc = acc or RobinhoodExecutor.get_default_account_number()
        print(f"Exporting full portfolio and audit datasets for account {acc}...")
        audit_res = PortfolioAuditor.audit(account_number=acc)
        json_path = audit_res["saved_files"]["json"]
        csv_path = audit_res["saved_files"]["csv"]
        audit_path = os.path.join(out_dir or TradingDataManager.get_cache_dir(), f"audit_{acc}.json")
        
        print(f"✓ Export complete:")
        print(f"  • JSON Portfolio : {json_path} ({os.path.getsize(json_path):,} bytes)")
        print(f"  • CSV Portfolio  : {csv_path} ({os.path.getsize(csv_path):,} bytes, {audit_res['summary']['active_position_count']} rows)")
        print(f"  • Audit JSON     : {audit_path} ({os.path.getsize(audit_path):,} bytes)")

    elif cmd in ("portfolio", "holdings", "account"):
        # Check flags
        is_summary = "--summary" in sys.argv or "-s" in sys.argv
        is_json = "--json" in sys.argv
        is_csv = "--csv" in sys.argv
        
        filter_type = None
        if "--filter" in sys.argv:
            idx = sys.argv.index("--filter")
            if idx + 1 < len(sys.argv):
                filter_type = sys.argv[idx + 1].lower()

        top_n = None
        if "--top" in sys.argv:
            idx = sys.argv.index("--top")
            if idx + 1 < len(sys.argv):
                try:
                    top_n = int(sys.argv[idx + 1])
                except Exception:
                    pass

        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-") and a != filter_type and str(top_n) != a:
                acc = a
                break
        acc = acc or RobinhoodExecutor.get_default_account_number()

        if is_summary:
            audit_res = PortfolioAuditor.audit(account_number=acc)
            print(PortfolioAuditor.format_executive_summary(audit_res))
            sys.exit(0)

        print(f"Fetching live Robinhood portfolio for account {acc} using stored credentials...\n")
        port_data = RobinhoodExecutor.get_live_portfolio(account_number=acc)
        positions = RobinhoodExecutor.get_equity_positions(account_number=acc)
        
        pos_active = [p for p in positions if float(p.get("quantity", 0)) > 0]
        symbols = [p["symbol"] for p in pos_active]
        quotes = RobinhoodExecutor.get_equity_quotes(symbols) if symbols else {}

        # Automatically export full datasets to disk
        export_res = TradingDataManager.export_portfolio(port_data, pos_active, quotes, str(acc))
        proc_pos = export_res["processed_positions"]
        summary = export_res["full_payload"]["summary"]

        if is_json:
            print(json.dumps(export_res["full_payload"], indent=2))
            sys.exit(0)

        if is_csv:
            with open(export_res["csv"], "r", encoding="utf-8") as f:
                print(f.read().strip())
            sys.exit(0)

        # Apply filtering if requested
        displayed_pos = proc_pos
        filter_label = ""
        if filter_type == "losers":
            displayed_pos = [p for p in proc_pos if p["unrealized_pnl"] < 0]
            displayed_pos.sort(key=lambda x: x["unrealized_pnl"])
            filter_label = " [Filter: Losers by $ Loss]"
        elif filter_type == "winners":
            displayed_pos = [p for p in proc_pos if p["unrealized_pnl"] > 0]
            displayed_pos.sort(key=lambda x: x["unrealized_pnl_pct"], reverse=True)
            filter_label = " [Filter: Winners by % Gain]"
        elif filter_type == "dust":
            displayed_pos = [p for p in proc_pos if p["current_value"] < 10.0]
            filter_label = " [Filter: Dust < $10]"
        elif filter_type in ("dead-money", "deadmoney"):
            displayed_pos = [p for p in proc_pos if p["unrealized_pnl_pct"] <= -40.0 and p["current_value"] <= 50.0]
            displayed_pos.sort(key=lambda x: x["unrealized_pnl_pct"])
            filter_label = " [Filter: Dead Money (down >40%, <$50)]"

        if top_n and top_n > 0:
            displayed_pos = displayed_pos[:top_n]
            filter_label += f" [Top {top_n}]"

        tot_val = summary["total_value"]
        eq_val = summary["equity_value"]
        cash_val = summary["cash"]
        bp_val = summary["buying_power"]

        print("=" * 80)
        print(f"  ROBINHOOD LIVE PORTFOLIO (Account: {acc}){filter_label}")
        print("=" * 80)
        print(f"  Total Portfolio Value : ${tot_val:,.2f}")
        print(f"  Equity Holdings Value : ${eq_val:,.2f}")
        print(f"  Cash Balance          : ${cash_val:,.2f}")
        print(f"  Buying Power          : ${bp_val:,.2f}")
        print(f"  Showing Positions     : {len(displayed_pos)} of {len(proc_pos)} symbols\n")

        if displayed_pos:
            print(f"{'Symbol':<8} | {'Qty':<8} | {'Avg Cost':<10} | {'Cur Price':<10} | {'Value':<10} | {'Weight':<7} | {'Unrealized P&L'}")
            print("-" * 80)
            tot_cost_disp = 0.0
            tot_val_disp = 0.0
            for p in displayed_pos:
                sym = p["symbol"]
                qty = p["quantity"]
                avg_cost = p["average_buy_price"]
                cur_price = p["current_price"]
                val = p["current_value"]
                cost = p["cost_basis"]
                tot_cost_disp += cost
                tot_val_disp += val
                weight = p["weight_pct"]
                pnl = p["unrealized_pnl"]
                pnl_pct = p["unrealized_pnl_pct"]
                pnl_sign = "+" if pnl >= 0 else ""
                print(f"{sym:<8} | {qty:<8.3f} | ${avg_cost:<9.2f} | ${cur_price:<9.2f} | ${val:<9.2f} | {weight:>5.1f}% | ${pnl_sign}{pnl:<7.2f} ({pnl_sign}{pnl_pct:.2f}%)")

            print("=" * 80)
            net_pnl = tot_val_disp - tot_cost_disp
            net_pct = (net_pnl / tot_cost_disp * 100.0) if tot_cost_disp > 0 else 0.0
            pnl_sign = "+" if net_pnl >= 0 else ""
            print(f"  Display Total: Cost Basis: ${tot_cost_disp:,.2f} | Value: ${tot_val_disp:,.2f} | Net P&L: ${pnl_sign}{net_pnl:,.2f} ({pnl_sign}{net_pct:.2f}%)")
            print(f"  Saved Datasets: JSON: {export_res['json']} | CSV: {export_res['csv']}")
            print("=" * 80)
        else:
            print("  No positions match the selected filter.")
            print("=" * 80)

    elif cmd == "analyze":
        tickers = sys.argv[2:] if len(sys.argv) > 2 else ["SPY", "QQQ", "NVDA", "AAPL"]
        print(f"Analyzing tickers: {', '.join(tickers)}...\n")
        for t in tickers:
            res = TradingStrategyEngine.analyze_ticker(t)
            ind = res["indicators"]
            risk = res["risk_targets"]
            news = res["news_sentiment"]
            
            print("=" * 70)
            print(f"  {res['ticker']} | Price: ${res['price']:.2f} ({res['change_pct']:>+5.2f}% ) | Score: {res['score']}/100 -> {res['recommendation']}")
            print("=" * 70)
            print(f"  • Technical Indicators : RSI(14)={ind['rsi']:.1f}, SMA20=${ind['sma20']:.2f}, SMA50=${ind['sma50']:.2f}, SMA200=${ind['sma200']:.2f}, MACD_Hist={ind['macd_histogram']:+.3f}")
            print(f"  • News Sentiment       : {news['label']} (Score: {news['score']:+.2f})")
            if news['headlines']:
                print(f"    - \"{news['headlines'][0]}\"")
            print(f"  • Risk Controls        : Stop Loss: ${risk['stop_loss']:.2f} ({risk['stop_loss_pct']}%) | Target 1: ${risk['take_profit_1']:.2f} (+8%) | Target 2: ${risk['take_profit_2']:.2f} (+15%)")
            print()

    elif cmd == "scan":
        tickers = sys.argv[2:] if len(sys.argv) > 2 else DEFAULT_WATCHLIST
        print(f"Scanning market universe ({len(tickers)} tickers)...\n")
        results = []
        for t in tickers:
            try:
                results.append(TradingStrategyEngine.analyze_ticker(t))
            except Exception as e:
                print(f"Error analyzing {t}: {e}")
        
        results.sort(key=lambda x: x["score"], reverse=True)
        print(f"{'Ticker':<7} | {'Price':<9} | {'Chg%':<7} | {'RSI':<6} | {'Score':<6} | {'Signal':<12} | {'Stop Loss':<10} | {'Target'}")
        print("-" * 80)
        for r in results:
            print(f"{r['ticker']:<7} | ${r['price']:<8.2f} | {r['change_pct']:>+5.2f}% | {r['indicators']['rsi']:<6.1f} | {r['score']:<6.1f} | {r['recommendation']:<12} | ${r['risk_targets']['stop_loss']:<9.2f} | ${r['risk_targets']['take_profit_1']:.2f}")

    elif cmd == "news":
        query = sys.argv[2] if len(sys.argv) > 2 else "SPY"
        news = NewsSentimentEngine.analyze_news_sentiment(query)
        print(f"Financial News & Sentiment for '{query}':")
        print(f"Sentiment: {news['sentiment_label']} (Score: {news['sentiment_score']:+.2f} on {news['article_count']} articles)\n")
        for i, h in enumerate(news["headlines"], 1):
            print(f"  {i}. {h}")

    elif cmd in ("orders", "order-history", "recent-orders", "todays-orders"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        resolved_acc = RobinhoodExecutor.resolve_account(acc)
        orders = RobinhoodExecutor.get_orders(resolved_acc)
        print("=" * 95)
        print(f"  ROBINHOOD EQUITY ORDERS (Account: {resolved_acc})")
        print("=" * 95)
        if not orders:
            print("  No recent equity orders found.")
        else:
            print(f"{'Date/Time (UTC)':<20} | {'Symbol':<7} | {'Side':<5} | {'State':<10} | {'Qty/Amount':<12} | {'Avg Price':<10} | {'Agent'}")
            print("-" * 95)
            for o in orders[:25]:
                dt = (o.get("last_transaction_at") or o.get("created_at") or "")[:19].replace("T", " ")
                sym = o.get("symbol", "")
                side = (o.get("side") or "").upper()
                state = (o.get("state") or "").upper()
                qty = o.get("cumulative_quantity") or o.get("quantity")
                if not qty and o.get("dollar_based_amount"):
                    qty = f"${float(o['dollar_based_amount']['amount']):.2f}"
                elif qty:
                    try:
                        qty = f"{float(qty):.4f}"
                    except Exception:
                        pass
                avg_p = o.get("average_price") or o.get("price")
                avg_p_str = f"${float(avg_p):.2f}" if avg_p else "-"
                agent = o.get("placed_agent") or "user"
                print(f"{dt:<20} | {sym:<7} | {side:<5} | {state:<10} | {str(qty):<12} | {avg_p_str:<10} | {agent}")
        print("=" * 95)

    elif cmd in ("pnl", "realized-pnl", "gains"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        resolved_acc = RobinhoodExecutor.resolve_account(acc)
        pnl_data = RobinhoodExecutor.get_realized_pnl(resolved_acc)
        port_data = RobinhoodExecutor.get_live_portfolio(resolved_acc)
        print("=" * 80)
        print(f"  ROBINHOOD PROFIT & LOSS REPORT (Account: {resolved_acc})")
        print("=" * 80)
        tot_ret = pnl_data.get("total_returns", "0.00")
        tot_rate = pnl_data.get("total_rate_of_return", "0.00")
        try:
            tot_ret_f = float(tot_ret)
            tot_rate_f = float(tot_rate) * 100.0
            pnl_s = "+" if tot_ret_f >= 0 else ""
            print(f"  Realized P&L (Cumulative) : {pnl_s}${tot_ret_f:,.2f} ({pnl_s}{tot_rate_f:.2f}%)")
        except Exception:
            print(f"  Realized P&L (Cumulative) : ${tot_ret}")
        
        eq_val = port_data.get("equity", port_data.get("market_value", "0.00"))
        print(f"  Portfolio Equity Value    : ${float(eq_val):,.2f}" if eq_val else "  Portfolio Equity Value    : -")
        print("=" * 80)

    elif cmd in ("trades", "trade-history"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        resolved_acc = RobinhoodExecutor.resolve_account(acc)
        trades = RobinhoodExecutor.get_pnl_trade_history(resolved_acc)
        print("=" * 90)
        print(f"  ROBINHOOD TRADE HISTORY (Account: {resolved_acc})")
        print("=" * 90)
        if not trades:
            print("  No recent closed trade records found.")
        else:
            print(f"{'Date/Time':<20} | {'Symbol':<7} | {'Side':<5} | {'Quantity':<10} | {'Price':<10} | {'Realized P&L'}")
            print("-" * 90)
            for t in trades[:25]:
                dt = (t.get("execution_time") or t.get("date") or "")[:19].replace("T", " ")
                sym = t.get("symbol", "")
                side = (t.get("side") or "").upper()
                qty = t.get("quantity", "")
                price = t.get("price", "")
                gain = t.get("realized_pnl", t.get("gain", "0.00"))
                try:
                    gain_f = float(gain)
                    gsign = "+" if gain_f >= 0 else ""
                    gstr = f"{gsign}${gain_f:,.2f}"
                except Exception:
                    gstr = str(gain)
                print(f"{dt:<20} | {sym:<7} | {side:<5} | {str(qty):<10} | ${float(price):<9.2f} | {gstr}")
        print("=" * 90)

    elif cmd in ("accounts", "account-list", "whoami"):
        print("Querying Robinhood accounts using stored credentials...\n")
        accounts = RobinhoodExecutor.get_accounts()
        if not accounts:
            print("No accounts found or stored credentials not authorized.")
        else:
            print("=" * 85)
            print("  ROBINHOOD AUTHORIZED BROKERAGE ACCOUNTS")
            print("=" * 85)
            print(f"{'Account Number':<16} | {'Nickname/Type':<18} | {'Account Type':<16} | {'Trading Type':<14} | {'Agentic Allowed':<15}")
            print("-" * 85)
            for acc in accounts:
                acc_num = acc.get("account_number", "")
                nick = acc.get("nickname") or "-"
                b_type = acc.get("brokerage_account_type", "")
                t_type = acc.get("type", "")
                agentic = "✓ YES (Active)" if acc.get("agentic_allowed") else "✗ NO"
                is_def = " [Default]" if acc.get("is_default") else ""
                disp_name = f"{nick}{is_def}"
                print(f"{acc_num:<16} | {disp_name:<18} | {b_type:<16} | {t_type:<14} | {agentic:<15}")
            print("=" * 85)
            print("  Note: Automated trading orders must target an account with 'Agentic Allowed: ✓ YES'.\n")

    elif cmd in ("rebalance", "rebalance-execute"):
        acc = None
        for a in sys.argv[2:]:
            if not a.startswith("-"):
                acc = a
                break
        resolved_acc = RobinhoodExecutor.resolve_account(acc or "agentic")
        do_execute = "--execute" in sys.argv or "--live" in sys.argv or cmd == "rebalance-execute"
        
        port = RobinhoodExecutor.get_live_portfolio(resolved_acc)
        bp_dict = port.get("buying_power", {})
        bp = float(bp_dict.get("buying_power", 0.0) if isinstance(bp_dict, dict) else (bp_dict or 0.0))
        cash = float(port.get("cash", bp) or bp or 0.0)
        total_val = float(port.get("total_value", 0.0) or (float(port.get("equity_value", 0.0) or 0.0) + cash) or 0.0)
        if total_val <= 0:
            total_val = cash

        positions = RobinhoodExecutor.get_equity_positions(resolved_acc)
        active_pos = [p for p in positions if float(p.get("quantity", 0) or 0) > 0]
        syms = [str(p.get("symbol", "")).upper() for p in active_pos]
        quotes = RobinhoodExecutor.get_equity_quotes(list(set(syms + ["VTI", "QQQ"]))) if syms else RobinhoodExecutor.get_equity_quotes(["VTI", "QQQ"])

        # Breakdown holdings
        core_holdings = {"VTI": 0.0, "QQQ": 0.0}
        satellite_holdings = {}
        for p in active_pos:
            s = str(p.get("symbol", "")).upper()
            qty = float(p.get("quantity", 0) or 0)
            q = quotes.get(s, {})
            pr = float(q.get("last_trade_price") or q.get("price") or p.get("average_buy_price") or 0.0)
            val = qty * pr
            if s in core_holdings:
                core_holdings[s] = val
            else:
                satellite_holdings[s] = val

        core_total = sum(core_holdings.values())
        sat_total = sum(satellite_holdings.values())
        core_pct = (core_total / total_val * 100.0) if total_val > 0 else 0.0
        sat_pct = (sat_total / total_val * 100.0) if total_val > 0 else 0.0
        cash_pct = (cash / total_val * 100.0) if total_val > 0 else 0.0

        target_core_val = total_val * 0.52
        target_sat_val = total_val * 0.33
        target_cash_val = total_val * 0.15

        surplus_cash = max(0.0, cash - target_cash_val)
        core_deficit = max(0.0, target_core_val - core_total)

        print("=" * 80)
        print(f"  CORE-SATELLITE BALLAST REBALANCER (Account: {resolved_acc})")
        print("=" * 80)
        print(f"  Portfolio Total Value : ${total_val:,.2f}")
        print(f"  Cash Buffer           : ${cash:,.2f} ({cash_pct:.1f}%)  [Target: 15.0% = ${target_cash_val:,.2f}]")
        print(f"  Core Ballast (ETFs)   : ${core_total:,.2f} ({core_pct:.1f}%)  [Target: 52.0% = ${target_core_val:,.2f}]")
        print(f"    • VTI : ${core_holdings['VTI']:,.2f} (Target 26%)")
        print(f"    • QQQ : ${core_holdings['QQQ']:,.2f} (Target 26%)")
        print(f"  Satellites (Stocks)   : ${sat_total:,.2f} ({sat_pct:.1f}%)  [Target: 33.0% = ${target_sat_val:,.2f}]")
        if satellite_holdings:
            for s_sym, s_val in sorted(satellite_holdings.items(), key=lambda x: x[1], reverse=True):
                print(f"    • {s_sym:<6} : ${s_val:,.2f} ({s_val/total_val*100:.1f}%)")
        print("-" * 80)

        deploy_amount = min(surplus_cash, core_deficit)
        if deploy_amount >= 10.0:
            half_deploy = round(deploy_amount / 2.0, 2)
            vti_buy = half_deploy
            qqq_buy = round(deploy_amount - half_deploy, 2)
            if core_holdings["VTI"] < core_holdings["QQQ"]:
                diff = core_holdings["QQQ"] - core_holdings["VTI"]
                vti_buy = round(min(deploy_amount, max(half_deploy, diff)), 2)
                qqq_buy = round(max(0.0, deploy_amount - vti_buy), 2)
            elif core_holdings["QQQ"] < core_holdings["VTI"]:
                diff = core_holdings["VTI"] - core_holdings["QQQ"]
                qqq_buy = round(min(deploy_amount, max(half_deploy, diff)), 2)
                vti_buy = round(max(0.0, deploy_amount - qqq_buy), 2)

            print(f"  PROPOSED REBALANCE ACTIONS (Surplus Deployable Cash: ${surplus_cash:.2f}):")
            if vti_buy >= 5.0:
                print(f"    ✓ BUY ${vti_buy:.2f} of VTI (Total Market ETF)")
            if qqq_buy >= 5.0:
                print(f"    ✓ BUY ${qqq_buy:.2f} of QQQ (Nasdaq 100 Tech ETF)")
            print(f"  Projected Post-Rebalance Cash : ${cash - deploy_amount:.2f} ({(cash - deploy_amount)/total_val*100:.1f}%)")
            print(f"  Projected Core Ballast Weight : {(core_total + deploy_amount)/total_val*100:.1f}%")
            print("-" * 80)

            if do_execute:
                if not RobinhoodExecutor.is_agentic_account(resolved_acc):
                    print(f"  ⚠️  Execution blocked: Account {resolved_acc} is NOT flagged as 'agentic_allowed: true'. Use the agentic account.")
                else:
                    print(f"  EXECUTING REBALANCE ORDERS ON ACCOUNT {resolved_acc}...")
                    now_et = MarketHours.now_et()
                    is_open = MarketHours.is_market_open(now_et)
                    if not is_open and "--force" not in sys.argv:
                        print("  ℹ️  Market is currently CLOSED. Robinhood orders placed outside market hours will queue for the next open (09:30 ET).")
                    for etf_sym, amt in [("VTI", vti_buy), ("QQQ", qqq_buy)]:
                        if amt >= 5.0:
                            try:
                                res = RobinhoodExecutor.execute_market_order(resolved_acc, etf_sym, "buy", dollar_amount=str(amt))
                                print(f"    ✓ Placed ${amt:.2f} BUY for {etf_sym}! Response: {str(res)[:120]}")
                                ComplianceAndRiskGuard.record_trade(resolved_acc, etf_sym, "BUY", qty=None)
                                ObsidianTradingVault.log_trade_execution({"ticker": etf_sym, "action": "BUY", "dollar_amount": amt, "reason": "MANUAL_REBALANCE"})
                            except Exception as exc:
                                print(f"    ✗ Failed to place {etf_sym} order: {exc}")
                    print("  ✓ Rebalance orders submitted.")
            else:
                print("  Dry-run preview mode. To execute these orders live, run:")
                print(f"    ./robinhood_trader.py rebalance {resolved_acc} --execute")
        else:
            print(f"  ✓ Portfolio is well-balanced. Surplus deployable cash is ${surplus_cash:.2f} (< $10 threshold). No action required.")
        print("=" * 80)

    elif cmd == "backtest":
        years = 5
        start = None
        top_n = None
        for i, a in enumerate(sys.argv):
            if a == "--years" and i + 1 < len(sys.argv):
                years = int(sys.argv[i + 1])
            elif a == "--start" and i + 1 < len(sys.argv):
                start = sys.argv[i + 1]
            elif a == "--top" and i + 1 < len(sys.argv):
                top_n = int(sys.argv[i + 1])
        cash = 600.0
        for i, a in enumerate(sys.argv):
            if a == "--cash" and i + 1 < len(sys.argv):
                cash = float(sys.argv[i + 1])
        print(f"Fetching {years}y of daily bars from Yahoo for {len(MomentumStrategy.universe())} names + core + SPY...")
        run_backtest(years=years, start=start, start_cash=cash, top_n=top_n, sleeve=True)
        if "--no-core-only" not in sys.argv:
            run_backtest(years=years, start=start, start_cash=cash, sleeve=False)

    elif cmd == "monitor":
        interval = 60
        auto_trade = "--auto-trade" in sys.argv
        dry_run = "--live" not in sys.argv
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            if idx + 1 < len(sys.argv):
                interval = int(sys.argv[idx + 1])
        run_market_monitor(interval_seconds=interval, auto_trade=auto_trade, dry_run=dry_run)

    elif cmd in ("risk-monitor", "risk", "rh-risk"):
        interval = 900
        dry_run = "--live" not in sys.argv
        once = "--once" in sys.argv
        force_session = "--force" in sys.argv
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            if idx + 1 < len(sys.argv):
                try:
                    interval = int(sys.argv[idx + 1])
                except ValueError:
                    pass
        run_risk_monitor(interval_seconds=interval, dry_run=dry_run, once=once, force_session=force_session)

    elif cmd in ("pre-market", "premarket", "briefing"):
        tickers = sys.argv[2:] if len(sys.argv) > 2 else DEFAULT_WATCHLIST
        print("Generating Pre-Market Briefing & Trade Action Plan...\n")
        briefing = TradingStrategyEngine.generate_premarket_briefing(tickers)
        print(json.dumps(briefing, indent=2))

    elif cmd in ("close-summary", "close"):
        print("Generating Daily Market Close Summary...\n")
        summary = TradingStrategyEngine.generate_daily_closing_summary()
        print(json.dumps(summary, indent=2))

    elif cmd in ("autopilot", "lifecycle"):
        interval = 900
        auto_trade = "--auto-trade" in sys.argv
        dry_run = "--live" not in sys.argv
        if "--interval" in sys.argv:
            idx = sys.argv.index("--interval")
            if idx + 1 < len(sys.argv):
                interval = int(sys.argv[idx + 1])
        run_scheduled_trading_lifecycle(interval_seconds=interval, auto_trade=auto_trade, dry_run=dry_run)

    elif cmd in ("discover", "breakout", "screener"):
        print("Dynamically searching live market for high-momentum breakout candidates...\n")
        discovered = TradingStrategyEngine.discover_breakout_candidates()
        print(f"Discovered {len(discovered)} vetted breakout opportunities:\n")
        print(f"{'Ticker':<7} | {'Price':<9} | {'Chg%':<7} | {'RSI':<6} | {'Score':<6} | {'Signal':<12} | {'Stop Loss':<10} | {'Target'}")
        print("-" * 80)
        for r in discovered:
            print(f"{r['ticker']:<7} | ${r['price']:<8.2f} | {r['change_pct']:>+5.2f}% | {r['indicators']['rsi']:<6.1f} | {r['score']:<6.1f} | {r['recommendation']:<12} | ${r['risk_targets']['stop_loss']:<9.2f} | ${r['risk_targets']['take_profit_1']:.2f}")

    elif cmd == "buy":
        if len(sys.argv) < 3:
            print("Usage: robinhood_trader.py buy <TICKER> [DOLLAR_AMOUNT] [account] [--force]")
            sys.exit(1)
        sym = sys.argv[2].upper().strip()
        dollar_amt = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else "25"
        acc = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith("-") else None
        resolved_acc = RobinhoodExecutor.resolve_account(acc or "agentic")
        
        in_cd, cd_msg = ComplianceAndRiskGuard.is_in_cooldown(resolved_acc, sym)
        if in_cd and "--force" not in sys.argv:
            print("=" * 70)
            print(f"  ⚠️  TRADE BLOCKED BY RISK GUARD (WASH-SALE / RE-ENTRY COOLDOWN)")
            print("=" * 70)
            print(f"  {cd_msg}")
            print(f"  Pass --force to explicitly override this risk block.")
            print("=" * 70)
            sys.exit(1)

        print("=" * 70)
        print(f"  EXECUTING ROBINHOOD BUY ORDER")
        print("=" * 70)
        print(f"  Symbol        : {sym}")
        print(f"  Dollar Amount : ${float(dollar_amt):.2f}")
        print(f"  Target Account: {resolved_acc} (Agentic Sandbox)")
        print("-" * 70)
        try:
            res = RobinhoodExecutor.execute_market_order(resolved_acc, sym, "buy", dollar_amount=str(dollar_amt))
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ Order not placed/confirmed: {exc}")
            sys.exit(1)
        print("Response:", json.dumps(res, indent=2))
        ComplianceAndRiskGuard.record_trade(resolved_acc, sym, "BUY", reason="MANUAL_CLI")
        try:
            ObsidianTradingVault.log_trade_execution({
                "ticker": sym, "action": "BUY", "dollar_amount": float(dollar_amt),
                "reason": "MANUAL_CLI", "account": resolved_acc
            })
        except Exception:
            pass
        print("=" * 70)

    elif cmd == "sell":
        if len(sys.argv) < 3:
            print("Usage: robinhood_trader.py sell <TICKER> [QUANTITY|all] [account]")
            sys.exit(1)
        sym = sys.argv[2].upper().strip()
        qty_arg = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else "all"
        acc = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith("-") else None
        resolved_acc = RobinhoodExecutor.resolve_account(acc or "agentic")
        
        if qty_arg.lower() == "all":
            positions = RobinhoodExecutor.get_equity_positions(resolved_acc)
            matching = [p for p in positions if str(p.get("symbol", "")).upper() == sym]
            if not matching or float(matching[0].get("quantity", 0)) <= 0:
                print(f"No open position found for {sym} in account {resolved_acc}.")
                sys.exit(1)
            qty = str(matching[0].get("quantity"))
        else:
            qty = str(qty_arg)

        print("=" * 70)
        print(f"  EXECUTING ROBINHOOD SELL ORDER")
        print("=" * 70)
        print(f"  Symbol        : {sym}")
        print(f"  Quantity      : {qty} shares")
        print(f"  Target Account: {resolved_acc} (Agentic Sandbox)")
        print("-" * 70)
        try:
            res = RobinhoodExecutor.execute_market_order(resolved_acc, sym, "sell", quantity=qty)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ Order not placed/confirmed: {exc}")
            sys.exit(1)
        print("Response:", json.dumps(res, indent=2))
        ComplianceAndRiskGuard.record_trade(resolved_acc, sym, "SELL", qty=float(qty) if qty else None, reason="MANUAL_CLI")
        try:
            ObsidianTradingVault.log_trade_execution({
                "ticker": sym, "action": "SELL", "quantity": float(qty),
                "reason": "MANUAL_CLI", "account": resolved_acc
            })
        except Exception:
            pass
        print("=" * 70)

    elif cmd in ("service", "systemd"):
        sub = sys.argv[2] if len(sys.argv) > 2 else "status"
        service_file = os.path.expanduser("~/.config/systemd/user/robinhood-trader.service")
        # The unit runs the deployed release (trader/deploy.sh) when one exists, else this checkout.
        repo_dir = DEPLOYED_CURRENT if os.path.isdir(DEPLOYED_CURRENT) else CHECKOUT_DIR
        
        def _write_unit(live=False):
            os.makedirs(os.path.expanduser("~/.config/systemd/user"), exist_ok=True)
            mode_args = "--interval 60 --auto-trade --live" if live else "--interval 60"
            unit_content = f"""[Unit]
Description=Robinhood Agentic Trading & Market Hours Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={repo_dir}
Environment=PATH={os.path.expanduser('~/.local/bin')}:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
Environment=BROWSER=none
EnvironmentFile=-%h/.config/ai/trader.env
ExecStart=/usr/bin/python3 {os.path.join(repo_dir, 'robinhood_trader.py')} monitor {mode_args}
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
            with open(service_file, "w") as f:
                f.write(unit_content)
            subprocess.run(["systemctl", "--user", "daemon-reload"])

        if sub in ("install", "setup"):
            _write_unit(live=False)
            print(f"Installed {service_file} in monitor mode.")
            print("To start: python3 robinhood_trader.py service start")

        elif sub in ("live", "live-trading", "enable-live"):
            _write_unit(live=True)
            subprocess.run(["systemctl", "--user", "restart", "robinhood-trader.service"])
            print("Switched robinhood-trader.service to LIVE AUTONOMOUS TRADING mode.")
            subprocess.run(["systemctl", "--user", "status", "robinhood-trader.service"])

        elif sub in ("dry-run", "dryrun", "monitor-only"):
            _write_unit(live=False)
            subprocess.run(["systemctl", "--user", "restart", "robinhood-trader.service"])
            print("Switched robinhood-trader.service to DRY-RUN MONITOR mode.")
            subprocess.run(["systemctl", "--user", "status", "robinhood-trader.service"])

        elif sub in ("start", "enable"):
            subprocess.run(["systemctl", "--user", "daemon-reload"])
            subprocess.run(["systemctl", "--user", "enable", "--now", "robinhood-trader.service"])
            subprocess.run(["systemctl", "--user", "status", "robinhood-trader.service"])

        elif sub == "stop":
            subprocess.run(["systemctl", "--user", "stop", "robinhood-trader.service"])
            print("robinhood-trader.service stopped.")

        elif sub == "restart":
            subprocess.run(["systemctl", "--user", "daemon-reload"])
            subprocess.run(["systemctl", "--user", "restart", "robinhood-trader.service"])
            subprocess.run(["systemctl", "--user", "status", "robinhood-trader.service"])

        elif sub == "status":
            subprocess.run(["systemctl", "--user", "status", "robinhood-trader.service"])

        elif sub == "logs":
            lines = sys.argv[3] if len(sys.argv) > 3 else "50"
            subprocess.run(["journalctl", "--user", "-u", "robinhood-trader.service", "-n", lines, "--no-pager"])

        else:
            print(f"Unknown service command: {sub}")
            print("Usage: robinhood_trader.py service [start|stop|restart|status|logs|live|dry-run]")

    elif cmd == "auth":
        authenticate_robinhood_mcp()

    else:
        print(f"Unknown command: {cmd}")
        print_help()
        sys.exit(1)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. `argv` excludes the program name (None = sys.argv[1:]); returns the exit code."""
    saved = sys.argv
    if argv is not None:
        sys.argv = [saved[0] if saved else "robinhood_trader.py", *argv]
    try:
        _dispatch()
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    finally:
        sys.argv = saved
    return 0
