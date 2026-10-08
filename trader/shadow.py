"""Shadow mode: run the strategy planning code with alternative parameters, never trade.

Once per trading day (after the live pulse, when trader.json {"shadow": {"enabled": true}}) the daemon
calls `maybe_run_shadow`, which runs `run_shadow_pulse`: it reads the live account through a
`ReadOnlyBroker` (every mutating method raises PermissionError), ranks the universe with
`MomentumStrategy` overridden by trader.json["shadow"]["strategy"], plans the sleeve rebalance and
core top-up the shadow parameters would make *today*, and appends one JSON line to
~/.cache/ai/trading/shadow/<YYYY-MM>.jsonl. No flag, ledger or vault file of the live strategy is
written. The daily summary diffs the shadow targets against the live month's targets.
"""

import datetime
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from . import config as _cfg
from .market_hours import MarketHours
from .strategy import MomentumStrategy
from .broker import RobinhoodExecutor
from .daemon import _account_snapshot, _fetch_strategy_bars, _rebalance_flag_path, _risk_log, _save_json

# trader.json["shadow"]["strategy"] key -> MomentumStrategy attribute (validated: (type, min, max)).
SHADOW_OVERRIDES: Dict[str, Tuple[str, type, float, float]] = {
    "sleeve_rank_buffer": ("SLEEVE_RANK_BUFFER", int, 0, 10),
    "sleeve_top_n": ("SLEEVE_TOP_N", int, 1, 20),
    "core_target_pct": ("CORE_TARGET_PCT", float, 0.0, 1.0),
    "sleeve_target_pct": ("SLEEVE_TARGET_PCT", float, 0.0, 1.0),
    "cash_buffer_pct": ("CASH_BUFFER_PCT", float, 0.0, 1.0),
    "lookback_bars": ("LOOKBACK_BARS", int, 20, 400),
    "regime_sma_bars": ("REGIME_SMA_BARS", int, 20, 400),
    "disaster_stop_pct": ("DISASTER_STOP_PCT", float, 1.0, 90.0),
    "min_avg_volume": ("MIN_AVG_VOLUME", int, 0, 1e12),
    "min_price": ("MIN_PRICE", float, 0.0, 1e6),
}

# Broker methods shadow mode may call; anything else (orders, cancels, raw MCP calls) raises.
READ_ONLY_METHODS = frozenset({"resolve_account", "is_agentic_account", "configured_account"})


class ReadOnlyBroker:
    """Wraps a broker (RobinhoodExecutor or a fake) and exposes only its read methods.

    Allowed: `get_*` and READ_ONLY_METHODS. Everything else - execute_market_order,
    place_equity_order, cancel_*, call_mcp_tool, ... - raises PermissionError without touching
    the wrapped broker. The wrapped broker is held only in a closure (no attribute) and read
    methods are handed out as plain wrapper functions (no `__self__` pointing at the broker), so
    shadow code cannot reach the real executor through the wrapper by accident."""

    __slots__ = ("_lookup",)

    def __init__(self, inner: Any):
        def _lookup(name: str) -> Any:
            if not (name.startswith("get_") or name in READ_ONLY_METHODS):
                raise PermissionError(f"shadow mode: broker method {name!r} is blocked (read-only broker)")
            fn = getattr(inner, name)

            def _read(*a: Any, **kw: Any) -> Any:
                return fn(*a, **kw)
            _read.__name__ = name
            return _read
        object.__setattr__(self, "_lookup", _lookup)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return object.__getattribute__(self, "_lookup")(name)
        except PermissionError as exc:
            msg = str(exc)

            def _blocked(*_a: Any, **_kw: Any) -> Any:
                raise PermissionError(msg)
            return _blocked

    def __setattr__(self, name: str, value: Any) -> None:
        raise PermissionError("ReadOnlyBroker is immutable")


def shadow_strategy(overrides: Optional[Dict[str, Any]]) -> Tuple[type, Dict[str, Any], List[str]]:
    """A MomentumStrategy subclass carrying the overrides (the live class is never modified).

    Returns (strategy_class, applied_overrides, ignored_keys)."""
    attrs: Dict[str, Any] = {}
    applied: Dict[str, Any] = {}
    ignored: List[str] = []
    for key, val in (overrides or {}).items():
        spec = SHADOW_OVERRIDES.get(key)
        if key == "bear_park":
            continue   # string ticker, validated below
        if spec is None or isinstance(val, bool):
            ignored.append(key)
            continue
        attr, typ, lo, hi = spec
        try:
            v = typ(val)
        except (TypeError, ValueError):
            ignored.append(key)
            continue
        if not lo <= v <= hi:
            ignored.append(key)
            continue
        attrs[attr] = v
        applied[key] = v
    if "bear_park" in (overrides or {}):
        sym = MomentumStrategy.clean_park((overrides or {})["bear_park"])
        if sym:
            applied["bear_park"] = sym
            attrs["bear_park"] = classmethod(lambda cls, _s=sym: _s)
        else:
            ignored.append("bear_park")
    if "SLEEVE_RANK_BUFFER" in attrs:
        buf = attrs["SLEEVE_RANK_BUFFER"]
        attrs["rank_buffer"] = classmethod(lambda cls: buf)
    return type("ShadowMomentumStrategy", (MomentumStrategy,), attrs), applied, sorted(ignored)


def shadow_dir() -> str:
    return os.path.join(_cfg.CACHE_DIR, "shadow")


def _append(record: Dict[str, Any], month: str) -> None:
    os.makedirs(shadow_dir(), exist_ok=True)
    with open(os.path.join(shadow_dir(), f"{month}.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _live_targets(month: str) -> Optional[List[str]]:
    """The live strategy's sleeve targets for `month` (read-only), None if not planned yet."""
    try:
        with open(_rebalance_flag_path(month), encoding="utf-8") as fh:
            state = json.load(fh)
        targets = state.get("targets") if isinstance(state, dict) else None
        if not isinstance(targets, list):
            return None
        return list(targets) + ([state["park"]] if not targets and isinstance(state.get("park"), str) else [])
    except (OSError, ValueError):
        return None


def run_shadow_pulse(now: Optional[datetime.datetime] = None, config: Optional[Dict[str, Any]] = None, *,
                     broker: Any = None, account_number: Optional[str] = None) -> Dict[str, Any]:
    """Plan today's orders under the shadow parameters; append and return the record. Never trades."""
    now = now or MarketHours.now_et()
    cfg = _cfg.load_trader_config() if config is None else config
    shadow_cfg = cfg.get("shadow") if isinstance(cfg.get("shadow"), dict) else {}
    strat, applied, ignored = shadow_strategy(shadow_cfg.get("strategy") if isinstance(shadow_cfg.get("strategy"), dict) else {})
    ro = ReadOnlyBroker(RobinhoodExecutor if broker is None else broker)
    today, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    record: Dict[str, Any] = {"ts": now.isoformat(), "date": today, "month": month, "overrides": applied,
                              "ignored_overrides": ignored, "status": "skipped", "planned_orders": []}

    def _done(reason: Optional[str] = None) -> Dict[str, Any]:
        if reason:
            record["reason"] = reason
        _append(record, month)
        return record

    acc = account_number or ro.get_agentic_account_number()
    snap = _account_snapshot(acc, broker=ro)
    equity, cash, pos_map = snap["equity"], snap["cash"], snap["pos_map"]
    if not snap.get("ok", True) or equity <= 0:
        return _done(f"account snapshot unusable ({snap.get('problem') or 'no equity'})")

    hold = MomentumStrategy.load_hold_list()
    universe = strat.universe()
    bars = _fetch_strategy_bars(universe + ["SPY"], broker=ro)  # price history through the wrapper too
    spy_bars = bars.pop("SPY", [])
    if len(spy_bars) < strat.REGIME_SMA_BARS or len(bars) < max(1, len(universe) // 2):
        return _done(f"insufficient market data (SPY {len(spy_bars)} bars, {len(bars)}/{len(universe)} symbols)")

    incumbents = strat.sleeve_incumbents(pos_map, hold)
    regime = strat.regime(spy_bars)
    targets = strat.select_sleeve(strat.rank(bars), regime["regime"], incumbents=incumbents, buffer=strat.rank_buffer())
    base_regime = MomentumStrategy.regime(spy_bars)
    baseline = MomentumStrategy.select_sleeve(MomentumStrategy.rank(bars), base_regime["regime"],
                                              incumbents=incumbents, buffer=MomentumStrategy.rank_buffer())
    park_cfg = strat.bear_park()   # read once per plan
    held_park = None
    if park_cfg:   # the live month's park (previous month's flag, as live) stays the park, as live would keep it
        from .daemon import _held_park
        held_park = _held_park(month, pos_map, hold)
    park = strat.choose_park(regime["regime"], targets, lambda syms: _fetch_strategy_bars(syms, broker=ro),
                             held_park=held_park, primary=park_cfg)
    plan = strat.plan_rebalance(equity, cash, pos_map, targets, hold, park=park)
    orders: List[Dict[str, Any]] = [{"side": "sell", **s} for s in plan["sells"]]
    orders += [{"side": "buy", **b} for b in plan["buys"]]
    # Core top-up on the cash left after the sleeve plan (approximation: positions are pre-trade).
    topup = strat.plan_core_topup(equity, plan["projected_cash"], pos_map)
    if topup:
        orders.append({"side": "buy", **topup})
    record.update({"status": "ok", "regime": regime["regime"], "equity": round(equity, 2), "cash": round(cash, 2),
                   "targets": targets, "baseline_targets": baseline, "live_targets": _live_targets(month),
                   "incumbents": incumbents, "planned_orders": orders})
    if park_cfg:   # key exists only when bear_park is configured for the shadow run
        record["park"] = park
    return _done()


def maybe_run_shadow(account_number: Optional[str], now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """Daemon hook: at most one shadow pulse per day, only when enabled and after the live pulse window.

    Returns the record, or None when nothing ran. Never raises."""
    try:
        now = now or MarketHours.now_et()
        if now.time() < MomentumStrategy.REBALANCE_EARLIEST_ET:
            return None
        cfg = _cfg.load_trader_config()
        sh = cfg.get("shadow")
        if not (isinstance(sh, dict) and sh.get("enabled") is True):
            return None
        today = now.strftime("%Y-%m-%d")
        flag = os.path.join(_cfg.RISK_MONITOR_FLAGS, f"shadow_{today}.flag")
        if os.path.exists(flag):
            return None
        # Flag first: one attempt per day even if the pulse fails (no retry storm every 60 s).
        _save_json(flag, {"ts": now.isoformat()})
        rec = run_shadow_pulse(now, cfg, account_number=account_number)
        _risk_log(f"SHADOW {today}: status={rec.get('status')} targets={rec.get('targets')} "
                  f"live={rec.get('live_targets')} planned={len(rec.get('planned_orders') or [])} "
                  f"overrides={rec.get('overrides')}")
        return rec
    except Exception as exc:  # noqa: BLE001 - shadow mode must never disturb the live loop
        try:
            _risk_log(f"SHADOW pulse failed: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return None
