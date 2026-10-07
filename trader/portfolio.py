"""Portfolio export and the quantitative portfolio auditor."""

import os
import json
import datetime
import csv
from typing import List, Dict, Any, Optional

from .config import CACHE_DIR, ET_ZONE
from .broker import RobinhoodExecutor


# ==============================================================================
# 6.5. Trading Data Offloading & Portfolio Auditor
# ==============================================================================

class TradingDataManager:
    """Manages offloading trading, portfolio, quote, and audit data to JSON and CSV files."""

    @classmethod
    def get_cache_dir(cls) -> str:
        d = CACHE_DIR
        os.makedirs(d, exist_ok=True)
        return d

    @classmethod
    def export_portfolio(cls, portfolio_data: Dict[str, Any], positions: List[Dict[str, Any]],
                         quotes: Dict[str, Dict[str, Any]], account_number: str,
                         output_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Saves structured portfolio dataset to JSON and CSV files:
        - portfolio_<account>.json / portfolio_latest.json
        - portfolio_<account>.csv / portfolio_latest.csv
        """
        out_dir = output_dir or cls.get_cache_dir()
        os.makedirs(out_dir, exist_ok=True)

        tot_val = float(portfolio_data.get("total_value", 0.0))
        eq_val = float(portfolio_data.get("equity_value", 0.0))
        cash_val = float(portfolio_data.get("cash", 0.0))
        
        bp_raw = portfolio_data.get("buying_power", 0.0)
        if isinstance(bp_raw, dict):
            bp_val = float(bp_raw.get("buying_power", 0.0))
        else:
            try:
                bp_val = float(bp_raw or 0.0)
            except Exception:
                bp_val = 0.0

        processed_positions = []
        tot_cost = 0.0
        tot_cur_val = 0.0

        for p in positions:
            sym = str(p.get("symbol", "")).strip().upper()
            try:
                qty = float(p.get("quantity", 0.0))
            except Exception:
                qty = 0.0
            if qty <= 0:
                continue
            try:
                avg_cost = float(p.get("average_buy_price", 0.0))
            except Exception:
                avg_cost = 0.0

            q = quotes.get(sym, {})
            try:
                cur_price = float(q.get("last_trade_price") or q.get("price") or avg_cost)
            except Exception:
                cur_price = avg_cost

            val = qty * cur_price
            cost = qty * avg_cost
            tot_cost += cost
            tot_cur_val += val
            weight = (val / eq_val * 100.0) if eq_val > 0 else 0.0
            pnl = val - cost
            pnl_pct = ((cur_price - avg_cost) / avg_cost * 100.0) if avg_cost > 0 else 0.0

            processed_positions.append({
                "symbol": sym,
                "quantity": round(qty, 4),
                "average_buy_price": round(avg_cost, 4),
                "current_price": round(cur_price, 4),
                "current_value": round(val, 2),
                "cost_basis": round(cost, 2),
                "unrealized_pnl": round(pnl, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "weight_pct": round(weight, 2),
                "type": p.get("type", "equity")
            })

        processed_positions.sort(key=lambda x: x["current_value"], reverse=True)

        full_payload = {
            "account_number": str(account_number),
            "timestamp": datetime.datetime.now(ET_ZONE).isoformat(),
            "summary": {
                "total_value": round(tot_val if tot_val > 0 else (tot_cur_val + cash_val), 2),
                "equity_value": round(eq_val if eq_val > 0 else tot_cur_val, 2),
                "cash": round(cash_val, 2),
                "buying_power": round(bp_val, 2),
                "cash_buffer_pct": round((cash_val / tot_val * 100.0) if tot_val > 0 else 0.0, 2),
                "total_cost_basis": round(tot_cost, 2),
                "net_unrealized_pnl": round(tot_cur_val - tot_cost, 2),
                "net_unrealized_pnl_pct": round(((tot_cur_val - tot_cost) / tot_cost * 100.0) if tot_cost > 0 else 0.0, 2),
                "active_position_count": len(processed_positions)
            },
            "positions": processed_positions
        }

        # 1. JSON Export
        json_path = os.path.join(out_dir, f"portfolio_{account_number}.json")
        latest_json = os.path.join(out_dir, "portfolio_latest.json")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(full_payload, f, indent=2)
            with open(latest_json, "w", encoding="utf-8") as f:
                json.dump(full_payload, f, indent=2)
        except Exception:
            pass

        # 2. CSV Export
        csv_path = os.path.join(out_dir, f"portfolio_{account_number}.csv")
        latest_csv = os.path.join(out_dir, "portfolio_latest.csv")
        csv_headers = [
            "symbol", "quantity", "average_buy_price", "current_price",
            "current_value", "cost_basis", "unrealized_pnl", "unrealized_pnl_pct", "weight_pct", "type"
        ]
        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_headers)
                writer.writeheader()
                for pos in processed_positions:
                    writer.writerow(pos)
            with open(latest_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_headers)
                writer.writeheader()
                for pos in processed_positions:
                    writer.writerow(pos)
        except Exception:
            pass

        return {
            "json": json_path,
            "csv": csv_path,
            "latest_json": latest_json,
            "latest_csv": latest_csv,
            "processed_positions": processed_positions,
            "full_payload": full_payload
        }

    @classmethod
    def export_audit(cls, audit_data: Dict[str, Any], account_number: str,
                     output_dir: Optional[str] = None) -> str:
        """Saves portfolio audit results to JSON."""
        out_dir = output_dir or cls.get_cache_dir()
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"audit_{account_number}.json")
        latest = os.path.join(out_dir, "audit_latest.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(audit_data, f, indent=2)
            with open(latest, "w", encoding="utf-8") as f:
                json.dump(audit_data, f, indent=2)
        except Exception:
            pass
        return path


class PortfolioAuditor:
    """Quantitative risk auditor, concentration checker, and optimizer for Robinhood accounts."""

    SEMI_TICKERS = {
        "NVDA", "AVGO", "ARM", "SMCI", "AMD", "TSM", "ASML", "AMAT", "MRVL", "QCOM", "INTC", "MCHP", "MLTX", "AEHR", "MXL", "SMH"
    }

    CORE_ETFS = {
        "VTI", "VXUS", "SPY", "QQQ", "SMH", "BND", "SCHO", "URA"
    }

    @classmethod
    def audit(cls, account_number: Optional[str] = None,
              port_data: Optional[Dict[str, Any]] = None,
              positions: Optional[List[Dict[str, Any]]] = None,
              quotes: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Performs full quantitative risk, concentration, dead-money, dust, and tax-loss harvesting audit."""
        acc = RobinhoodExecutor.resolve_account(account_number)
        
        acc_info = {}
        try:
            for a in RobinhoodExecutor.get_accounts():
                if str(a.get("account_number")) == str(acc):
                    acc_info = a
                    break
        except Exception:
            pass

        port_data = port_data or RobinhoodExecutor.get_live_portfolio(account_number=acc)
        positions = positions if positions is not None else RobinhoodExecutor.get_equity_positions(account_number=acc)
        
        pos_active = [p for p in positions if float(p.get("quantity", 0)) > 0]
        symbols = [p["symbol"] for p in pos_active]
        quotes = quotes if quotes is not None else (RobinhoodExecutor.get_equity_quotes(symbols) if symbols else {})

        export_res = TradingDataManager.export_portfolio(port_data, pos_active, quotes, str(acc))
        proc_pos = export_res["processed_positions"]
        summary = export_res["full_payload"]["summary"]

        tot_val = summary["total_value"]
        eq_val = summary["equity_value"]
        cash_val = summary["cash"]

        # 1. Cash Buffer Analysis (target >= 15%)
        cash_pct = summary["cash_buffer_pct"]
        target_cash_pct = 15.0
        cash_target_val = round(tot_val * 0.15, 2)
        cash_deficit = max(0.0, round(cash_target_val - cash_val, 2))
        cash_status = "HEALTHY" if cash_pct >= 15.0 else ("WARNING" if cash_pct >= 10.0 else "CRITICAL_DEFICIT")

        # 2. Concentration Risk (15% single position limit)
        max_pos_cap_pct = 15.0
        concentration_alerts = []
        for p in proc_pos:
            if p["weight_pct"] >= max_pos_cap_pct:
                concentration_alerts.append({
                    "symbol": p["symbol"],
                    "weight_pct": p["weight_pct"],
                    "current_value": p["current_value"],
                    "excess_value": round(p["current_value"] - (tot_val * 0.15), 2),
                    "status": "OVER_LIMIT"
                })
            elif p["weight_pct"] >= 12.0:
                concentration_alerts.append({
                    "symbol": p["symbol"],
                    "weight_pct": p["weight_pct"],
                    "current_value": p["current_value"],
                    "excess_value": 0.0,
                    "status": "APPROACHING_LIMIT"
                })

        # 3. Sector / Theme Concentration (Semiconductor single-name overlap)
        semi_positions = [p for p in proc_pos if p["symbol"] in cls.SEMI_TICKERS and p["symbol"] != "SMH"]
        semi_total_val = sum(p["current_value"] for p in semi_positions)
        semi_weight_pct = round((semi_total_val / eq_val * 100.0) if eq_val > 0 else 0.0, 2)
        semi_symbols = [p["symbol"] for p in semi_positions]

        # 4. Dead Money Analysis (down > 40%, value < $50)
        dead_money = [p for p in proc_pos if p["unrealized_pnl_pct"] <= -40.0 and p["current_value"] <= 50.0]
        dead_money.sort(key=lambda x: x["unrealized_pnl_pct"])
        dead_money_val = sum(p["current_value"] for p in dead_money)
        dead_money_cost = sum(p["cost_basis"] for p in dead_money)
        dead_money_loss = dead_money_val - dead_money_cost

        # 5. Dust Cleanup (value < $10)
        dust = [p for p in proc_pos if p["current_value"] < 10.0]
        dust_val = sum(p["current_value"] for p in dust)
        dust_cost = sum(p["cost_basis"] for p in dust)

        # 6. Tax-Loss Harvesting Candidates (all negative unrealized P&L)
        losers = [p for p in proc_pos if p["unrealized_pnl"] < 0]
        losers.sort(key=lambda x: x["unrealized_pnl"])  # largest dollar loss first
        total_harvestable_loss = abs(sum(p["unrealized_pnl"] for p in losers))

        # 7. Big Winners / High Gainers
        winners = [p for p in proc_pos if p["unrealized_pnl_pct"] >= 50.0]
        winners.sort(key=lambda x: x["unrealized_pnl_pct"], reverse=True)

        # 8. Compute Health Score (0 - 100)
        score = 100
        if cash_pct < 5.0:
            score -= 20
        elif cash_pct < 10.0:
            score -= 15
        elif cash_pct < 15.0:
            score -= 10

        if any(c["status"] == "OVER_LIMIT" for c in concentration_alerts):
            score -= 15
        elif any(c["status"] == "APPROACHING_LIMIT" for c in concentration_alerts):
            score -= 5

        if semi_weight_pct > 25.0:
            score -= 15
        elif semi_weight_pct > 18.0:
            score -= 10

        if len(dead_money) >= 10:
            score -= 10
        elif len(dead_money) >= 5:
            score -= 5

        if len(dust) >= 15:
            score -= 10
        elif len(dust) >= 5:
            score -= 5

        health_score = max(0, min(100, score))

        # 9. Formulate Clear Action Plan
        action_plan = []
        if dead_money:
            action_plan.append(f"Cut {len(dead_money)} dead-money positions (down >40%, <$50) -> raises ~${dead_money_val:.2f} cash and realizes ${abs(dead_money_loss):.2f} in tax losses.")
        if dust:
            action_plan.append(f"Clean up {len(dust)} dust positions (<$10) -> simplifies portfolio and recovers ~${dust_val:.2f} cash.")
        if winners:
            top_w_syms = ", ".join([f"{w['symbol']} (+{w['unrealized_pnl_pct']:.0f}%)" for w in winners[:3]])
            action_plan.append(f"Harvest / trim big winners ({top_w_syms}) or place trailing stops (8-10%) to lock in multi-bagger gains.")
        if semi_weight_pct > 18.0:
            action_plan.append(f"Trim semiconductor single-name overlap ({', '.join(semi_symbols[:6])}... = {semi_weight_pct}% of equities) into broader core ETFs (SMH / VTI).")
        if cash_deficit > 0:
            action_plan.append(f"Restore cash buffer from {cash_pct:.1f}% to target >= 15.0% (${cash_target_val:.2f} needed; ~${cash_deficit:.2f} deficit covered by dead-money & dust liquidations).")

        audit_dict = {
            "account_number": str(acc),
            "account_nickname": acc_info.get("nickname") or "-",
            "account_type": acc_info.get("brokerage_account_type", "individual"),
            "trading_type": acc_info.get("type", "margin"),
            "agentic_allowed": bool(acc_info.get("agentic_allowed", False)),
            "health_score": health_score,
            "summary": summary,
            "positions": proc_pos,
            "cash_buffer": {
                "current_pct": cash_pct,
                "target_pct": target_cash_pct,
                "status": cash_status,
                "deficit_dollars": cash_deficit
            },
            "concentration_alerts": concentration_alerts,
            "semi_overlap": {
                "symbols": semi_symbols,
                "total_value": round(semi_total_val, 2),
                "weight_pct": semi_weight_pct
            },
            "dead_money": {
                "count": len(dead_money),
                "total_value": round(dead_money_val, 2),
                "total_cost": round(dead_money_cost, 2),
                "harvestable_loss": round(abs(dead_money_loss), 2),
                "positions": dead_money
            },
            "dust_positions": {
                "count": len(dust),
                "total_value": round(dust_val, 2),
                "total_cost": round(dust_cost, 2),
                "positions": dust
            },
            "tax_loss_harvesting": {
                "total_harvestable_loss": round(total_harvestable_loss, 2),
                "loser_count": len(losers),
                "top_losers": losers[:10]
            },
            "big_winners": {
                "count": len(winners),
                "positions": winners
            },
            "action_plan": action_plan,
            "saved_files": {
                "json": export_res["json"],
                "csv": export_res["csv"]
            }
        }

        TradingDataManager.export_audit(audit_dict, str(acc))
        return audit_dict

    @classmethod
    def format_executive_summary(cls, audit: Dict[str, Any]) -> str:
        """Formats a compact, token-efficient executive summary card for agents and humans."""
        s = audit["summary"]
        acc = audit["account_number"]
        agentic = audit.get("agentic_allowed", False)
        agentic_str = "Agentic (Automated execution enabled)" if agentic else "Non-Agentic (Manual placement required)"
        pnl_sign = "+" if s["net_unrealized_pnl"] >= 0 else ""
        
        proc_pos = audit.get("positions", [])
        top5 = proc_pos[:5]
        top5_str = ", ".join([f"{p['symbol']} ${p['current_value']:,.0f} ({p['weight_pct']:.1f}%)" for p in top5]) if top5 else "None"
        
        winners = audit.get("big_winners", {}).get("positions", [])[:3]
        win_str = ", ".join([f"{w['symbol']} +${w['unrealized_pnl']:,.0f} (+{w['unrealized_pnl_pct']:.0f}%)" for w in winners]) if winners else "None"

        losers = audit.get("tax_loss_harvesting", {}).get("top_losers", [])[:3]
        lose_str = ", ".join([f"{l['symbol']} -${abs(l['unrealized_pnl']):,.0f} ({l['unrealized_pnl_pct']:.0f}%)" for l in losers]) if losers else "None"

        dead = audit.get("dead_money", {})
        dust = audit.get("dust_positions", {})
        cb = audit.get("cash_buffer", {})
        semi = audit.get("semi_overlap", {})

        lines = [
            "=" * 80,
            f"  ROBINHOOD PORTFOLIO EXECUTIVE SUMMARY (Account: {acc})",
            "=" * 80,
            f"  Total Value: ${s['total_value']:,.2f} | Equities: ${s['equity_value']:,.2f} | Cash: ${s['cash']:,.2f} ({cb.get('current_pct', 0.0):.1f}%)",
            f"  Cost Basis:  ${s['total_cost_basis']:,.2f} | Unrealized P&L: {pnl_sign}${s['net_unrealized_pnl']:,.2f} ({pnl_sign}{s['net_unrealized_pnl_pct']:.2f}%) | Positions: {s['active_position_count']}",
            f"  Health Score: {audit.get('health_score', 100)}/100 | Mode: {agentic_str}",
            "",
            f"  • Top Holdings : {top5_str}",
            f"  • Top Winners  : {win_str}",
            f"  • Top Losers   : {lose_str}",
        ]
        if cb.get("status") != "HEALTHY":
            lines.append(f"  • Risk Alerts  : ⚠️ Cash buffer {cb.get('current_pct', 0.0):.1f}% < 15% minimum (${cb.get('deficit_dollars', 0.0):,.2f} deficit)")
        else:
            lines.append("  • Risk Alerts  : ✓ Cash buffer healthy (>=15%)")
            
        if semi.get("weight_pct", 0) > 15.0:
            lines.append(f"                   ⚠️ High Semi Overlap: {semi.get('weight_pct')}% across {len(semi.get('symbols', []))} single stocks")
        
        lines.append(f"  • Optimizations: {dead.get('count', 0)} dead-money stocks (<$50, down >40%) -> unlock ~${dead.get('total_value', 0):,.2f}, harvest ${dead.get('harvestable_loss', 0):,.2f} loss")
        lines.append(f"                   {dust.get('count', 0)} dust stocks (<$10) -> recover ~${dust.get('total_value', 0):,.2f} & clean clutter")
        lines.append(f"  • Saved Files  : JSON: {audit.get('saved_files', {}).get('json', '')}")
        lines.append(f"                   CSV:  {audit.get('saved_files', {}).get('csv', '')}")
        lines.append("=" * 80)
        return "\n".join(lines)

    @classmethod
    def format_harvest_losses(cls, audit: Dict[str, Any]) -> str:
        """Formats detailed tax-loss harvesting candidates table."""
        tlh = audit.get("tax_loss_harvesting", {})
        losers = tlh.get("top_losers", [])
        tot_loss = tlh.get("total_harvestable_loss", 0.0)
        
        lines = [
            "=" * 80,
            f"  TAX-LOSS HARVESTING CANDIDATES (Account: {audit['account_number']})",
            "=" * 80,
            f"  Total Harvestable Losses Available: ${tot_loss:,.2f} across {tlh.get('loser_count', 0)} positions",
            "  Note: Losses can offset capital gains dollar-for-dollar + up to $3k ordinary income.",
            "  Warning: Avoid wash sales (do not buy identical security within 30 days).",
            "",
            f"{'Symbol':<8} | {'Qty':<8} | {'Avg Cost':<10} | {'Cur Price':<10} | {'Value':<10} | {'Loss ($)':<10} | {'Loss (%)'}",
            "-" * 80
        ]
        for p in losers:
            lines.append(f"{p['symbol']:<8} | {p['quantity']:<8.3f} | ${p['average_buy_price']:<9.2f} | ${p['current_price']:<9.2f} | ${p['current_value']:<9.2f} | -${abs(p['unrealized_pnl']):<8.2f} | {p['unrealized_pnl_pct']:>6.2f}%")
        lines.append("=" * 80)
        return "\n".join(lines)

    @classmethod
    def format_rebalance_plan(cls, audit: Dict[str, Any]) -> str:
        """Formats a concrete 4-step rebalancing and cash recovery plan."""
        dead = audit.get("dead_money", {})
        dust = audit.get("dust_positions", {})
        winners = audit.get("big_winners", {}).get("positions", [])
        cb = audit.get("cash_buffer", {})
        s = audit["summary"]

        lines = [
            "=" * 80,
            f"  PORTFOLIO ACTIONABLE REBALANCING PLAN (Account: {audit['account_number']})",
            "=" * 80,
            f"  Current Value: ${s['total_value']:,.2f} | Cash: ${s['cash']:,.2f} ({cb.get('current_pct', 0.0):.1f}%) | Target Buffer: 15% (${s['total_value']*0.15:,.2f})",
            f"  Account Execution Type: {'Agentic (Autonomous)' if audit.get('agentic_allowed') else 'Non-Agentic (Place in Robinhood App)'}",
            "",
            "STEP 1: LIQUIDATE DEAD MONEY (Cut losing trades, harvest tax losses):",
            f"  Sell all {dead.get('count', 0)} dead-money positions: {', '.join([p['symbol'] for p in dead.get('positions', [])])}",
            f"  -> Unlocks: ~${dead.get('total_value', 0.0):,.2f} cash | Realizes: ${dead.get('harvestable_loss', 0.0):,.2f} capital loss",
            "",
            "STEP 2: CLEAN UP DUST POSITIONS (Eliminate sub-$10 clutter):",
            f"  Sell {dust.get('count', 0)} dust positions: {', '.join([p['symbol'] for p in dust.get('positions', [])])}",
            f"  -> Unlocks: ~${dust.get('total_value', 0.0):,.2f} cash",
            "",
            "STEP 3: HARVEST / TRIM OVER-EXTENDED WINNERS:",
        ]
        if winners:
            for w in winners[:4]:
                lines.append(f"  - {w['symbol']}: +{w['unrealized_pnl_pct']:.0f}% (${w['current_value']:,.2f}) -> Sell half (~${w['current_value']/2:,.2f}) or set trailing stop at 8-10%")
        else:
            lines.append("  - No extreme winners requiring trimming.")
        
        projected_cash = s['cash'] + dead.get('total_value', 0.0) + dust.get('total_value', 0.0)
        projected_buffer_pct = (projected_cash / s['total_value'] * 100.0) if s['total_value'] > 0 else 0.0
        lines.extend([
            "",
            "STEP 4: CASH BUFFER & DIP-BUYING RESERVE:",
            f"  Projected Cash after Steps 1 & 2: ~${projected_cash:,.2f} ({projected_buffer_pct:.1f}% buffer)",
            f"  Status: {'✓ MEETS TARGET (>=15%)' if projected_buffer_pct >= 15.0 else 'Approaching target'}",
            "",
            "EXECUTION ADVICE:",
            "  • Use limit orders at the bid (or midpoint) for lower-liquidity names to prevent slippage.",
            "  • Keep core high-quality holdings intact (VTI, GOOGL, MSFT, AVGO, AMZN, PANW, CRWD, BND).",
            "=" * 80
        ])
        return "\n".join(lines)
