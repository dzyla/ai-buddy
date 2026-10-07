"""Daily trader summary: one short markdown report per trading day, sent after the close.

Built only from local state (no broker call): the circuit-breaker baseline (opening equity), the
equity journal (closing equity and holdings, written by the close snapshot), the trade ledger
(fills), the month's rebalance flag (regime, live targets), the Tier-3 halt flag and the shadow
journal. Account numbers are never included.

Delivery (`send_daily_summary`): Zulip private message to AI_TRADER_ZULIP_TO (or AI_REMINDER_ZULIP_TO)
through zulip_mcp_server.do_send_message (imported lazily; the zulip package is optional), else
notify-send, else the risk log only. A summary_<day>.flag in the monitor-flags dir makes it once a day.
"""

import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

from . import config as _cfg
from .market_hours import MarketHours
from .strategy import MomentumStrategy
from .daemon import _account_snapshot, _rebalance_flag_path, _risk_log, _save_json

SUMMARY_AFTER_ET = datetime.time(16, 0)


def equity_journal_path() -> str:
    return os.path.join(_cfg.CACHE_DIR, "equity_journal.jsonl")


def _day_str(day: Any) -> str:
    return day.strftime("%Y-%m-%d") if isinstance(day, (datetime.date, datetime.datetime)) else str(day)


def record_equity_snapshot(day: Any, snap: Dict[str, Any]) -> None:
    """Append the close snapshot (equity, cash, holdings) for `day` to the equity journal."""
    rec = {"date": _day_str(day), "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "equity": round(float(snap.get("equity") or 0.0), 2), "cash": round(float(snap.get("cash") or 0.0), 2),
           "positions": {s: {"quantity": float(v.get("quantity", 0) or 0), "price": float(v.get("price", 0) or 0)}
                         for s, v in (snap.get("pos_map") or {}).items()}}
    os.makedirs(os.path.dirname(equity_journal_path()), exist_ok=True)
    with open(equity_journal_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue   # one corrupt line must not hide the rest
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        pass
    return out


def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _signed_money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"+${x:,.2f}"


def _open_equity(day: str, account_number: Optional[str]) -> Optional[float]:
    data = _read_json(_cfg.CIRCUIT_BREAKER_FILE)
    if not isinstance(data, dict):
        return None
    keys = [f"{account_number}_{day}"] if account_number else [k for k in data if str(k).endswith(f"_{day}")]
    for k in keys:
        rec = data.get(k)
        val = rec.get("baseline") if isinstance(rec, dict) else rec
        try:
            if val and float(val) > 0:
                return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _breaker_state(day: str, account_number: Optional[str]) -> str:
    if os.path.exists(_cfg.SESSION_HALT_FLAG.format(date=day)):
        halt = _read_json(_cfg.SESSION_HALT_FLAG.format(date=day)) or {}
        return f"TIER 3 HALT (no strategy orders today){': ' + str(halt.get('reason'))[:120] if isinstance(halt, dict) and halt.get('reason') else ''}"
    data = _read_json(_cfg.CIRCUIT_BREAKER_FILE)
    if isinstance(data, dict):
        keys = [f"{account_number}_{day}"] if account_number else [k for k in data if str(k).endswith(f"_{day}")]
        for k in keys:
            rec = data.get(k)
            if isinstance(rec, dict) and int(rec.get("pending_tier", 0) or 0) > 0:
                return f"tier {int(rec['pending_tier'])} observed once (unconfirmed)"
    return "normal (tier 0)"


def _fills(day: str) -> List[str]:
    ledger = os.path.join(_cfg.VAULT_DIR, "retrospectives", "trade_ledger.jsonl")
    lines = []
    for t in _read_jsonl(ledger):
        if not str(t.get("timestamp", "")).startswith(day):
            continue
        action, sym = str(t.get("action", "?")).upper(), str(t.get("ticker", "?"))
        reason = f" ({t['reason']})" if t.get("reason") else ""
        if t.get("dollar_amount") is not None:
            lines.append(f"- {action} {sym} {_money(float(t['dollar_amount']))}{reason}")
        else:
            px = f" @ {_money(float(t['price']))}" if t.get("price") is not None else ""
            lines.append(f"- {action} {sym} {t.get('qty', '?')} sh{px}{reason}")
    return lines


def build_daily_summary(day: Any, account_number: Optional[str] = None) -> str:
    """Markdown summary for `day` (YYYY-MM-DD or date): equity change, fills, sleeve holdings, regime,
    circuit-breaker state and the shadow-vs-live diff. Missing data shows as n/a; never raises on bad files."""
    day = _day_str(day)
    month = day[:7]
    out = [f"**Trader daily summary - {day}**", ""]

    open_eq = _open_equity(day, account_number)
    closes = [r for r in _read_jsonl(equity_journal_path()) if r.get("date") == day]
    close = closes[-1] if closes else None
    close_eq = float(close["equity"]) if close and close.get("equity") else None
    if open_eq and close_eq:
        delta = close_eq - open_eq
        out.append(f"- **Equity:** {_money(open_eq)} -> {_money(close_eq)} ({_signed_money(delta)}, {delta / open_eq * 100.0:+.2f}%)")
    else:
        out.append(f"- **Equity:** open {_money(open_eq) if open_eq else 'n/a'}, close {_money(close_eq) if close_eq else 'n/a'}")

    state = _read_json(_rebalance_flag_path(month))
    state = state if isinstance(state, dict) else {}
    regime = state.get("regime") if isinstance(state.get("regime"), dict) else {}
    if regime:
        out.append(f"- **Regime:** {regime.get('regime', '?')} (SPY {regime.get('close', '?')} vs SMA200 "
                   f"{regime.get('sma200', '?')}, decided {state.get('date', '?')})")
    else:
        out.append("- **Regime:** n/a (no rebalance planned this month)")
    out.append(f"- **Circuit breaker:** {_breaker_state(day, account_number)}")

    fills = _fills(day)
    out += ["", f"**Orders filled ({len(fills)})**"] + (fills or ["- none"])

    out += ["", "**Sleeve holdings**"]
    positions = (close or {}).get("positions") or {}
    sleeve = {s: p for s, p in positions.items() if s.upper() not in MomentumStrategy.CORE_SYMBOLS}
    if sleeve:
        for s, p in sorted(sleeve.items()):
            q, px = float(p.get("quantity", 0) or 0), float(p.get("price", 0) or 0)
            out.append(f"- {s} {q:g} sh ({_money(q * px)})")
    elif positions:
        out.append("- none (core only)")
    else:
        out.append(f"- n/a (no close snapshot); {month} live targets: {', '.join(state.get('targets') or []) or 'n/a'}")

    out += ["", "**Shadow vs live**"]
    shadow_recs = [r for r in _read_jsonl(os.path.join(_cfg.CACHE_DIR, "shadow", f"{month}.jsonl")) if r.get("date") == day]
    live = state.get("targets") if isinstance(state.get("targets"), list) else None
    if not shadow_recs:
        out.append("- n/a (no shadow pulse today)")
    else:
        sh = shadow_recs[-1]
        if sh.get("status") != "ok":
            out.append(f"- shadow pulse skipped: {sh.get('reason', 'unknown')}")
        else:
            st = list(sh.get("targets") or [])
            out.append(f"- shadow targets: {', '.join(st) or 'CASH'} | live targets: {', '.join(live) if live is not None else 'n/a'}")
            if live is not None:
                only_s = [s for s in st if s not in live]
                only_l = [s for s in live if s not in st]
                out.append(f"- only in shadow: {', '.join(only_s) or 'none'} | only in live: {', '.join(only_l) or 'none'}")
            planned = sh.get("planned_orders") or []
            desc = ", ".join(f"{o.get('side', '?').upper()} {o.get('symbol', '?')}" for o in planned[:8])
            out.append(f"- shadow planned orders: {len(planned)}{' (' + desc + ')' if desc else ''}")
            if sh.get("overrides"):
                out.append(f"- overrides: {json.dumps(sh['overrides'], sort_keys=True)}")
    return "\n".join(out) + "\n"


def _summary_flag(day: str) -> str:
    return os.path.join(_cfg.RISK_MONITOR_FLAGS, f"summary_{day}.flag")


def _load_zulip_sender() -> Any:
    """zulip_mcp_server, imported lazily: from sys.path, else the checkout root or ~/.local/bin."""
    try:
        import zulip_mcp_server  # type: ignore
        return zulip_mcp_server
    except ImportError:
        pass
    candidates = [os.path.dirname(os.path.dirname(os.path.realpath(__file__))), os.path.expanduser("~/.local/bin")]
    for d in candidates:
        path = os.path.join(d, "zulip_mcp_server.py")
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("zulip_mcp_server", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            sys.modules["zulip_mcp_server"] = mod
            return mod
    raise ImportError("zulip_mcp_server.py not found")


def _notify_send(title: str, body: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    try:
        return subprocess.run(["notify-send", title, body[:2000]], timeout=10, capture_output=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def send_daily_summary(text: str, day: Any = None) -> str:
    """Deliver once per day. Returns "zulip", "notify-send", "log" or "suppressed" (already sent today)."""
    day = _day_str(day) if day is not None else MarketHours.now_et().strftime("%Y-%m-%d")
    flag = _summary_flag(day)
    if os.path.exists(flag):
        return "suppressed"
    status = ""
    to = os.environ.get("AI_TRADER_ZULIP_TO") or os.environ.get("AI_REMINDER_ZULIP_TO")
    if to:
        try:
            res = str(_load_zulip_sender().do_send_message({"message_type": "private", "to": to, "content": text}))
            if res.startswith("Successfully"):
                status = "zulip"
            else:
                _risk_log(f"DAILY SUMMARY: Zulip send failed: {res[:200]}")
        except Exception as exc:  # noqa: BLE001 - zulip package missing, bad config, network
            _risk_log(f"DAILY SUMMARY: Zulip unavailable: {exc}")
    if not status and _notify_send("AI trader daily summary", text):
        status = "notify-send"
    if not status:
        status = "log"
    print(text, flush=True)
    _save_json(flag, {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "via": status})
    _risk_log(f"DAILY SUMMARY {day}: delivered via {status}")
    return status


def _close_snapshot(account_number: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read-only account snapshot for the close (None if it cannot be trusted)."""
    from .shadow import ReadOnlyBroker
    from .broker import RobinhoodExecutor
    ro = ReadOnlyBroker(RobinhoodExecutor)
    snap = _account_snapshot(account_number or ro.get_agentic_account_number(), broker=ro)
    return snap if snap.get("ok", True) and snap.get("equity", 0) > 0 else None


def maybe_send_daily_summary(now: Optional[datetime.datetime] = None,
                             account_number: Optional[str] = None) -> Optional[str]:
    """Daemon hook: on a trading day after 16:00 ET, once (flag): snapshot, build, send.
    Returns the delivery status, or None when nothing was due."""
    now = now or MarketHours.now_et()
    day = now.strftime("%Y-%m-%d")
    if not MarketHours.is_trading_day(now.date()) or now.time() < SUMMARY_AFTER_ET:
        return None
    if os.path.exists(_summary_flag(day)):
        return None
    try:
        snap = _close_snapshot(account_number)
        if snap:
            record_equity_snapshot(day, snap)
    except Exception as exc:  # noqa: BLE001 - the summary still goes out without a close reading
        _risk_log(f"DAILY SUMMARY: close snapshot failed: {exc}")
    return send_daily_summary(build_daily_summary(day, account_number), day)
