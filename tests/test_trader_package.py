"""The trader/ package and the robinhood_trader.py shim: one flat legacy namespace, monkeypatches on the
shim reach the code in the submodules, and the CLI entry point returns exit codes."""
import datetime
import json
import os
import subprocess
import sys

import robinhood_trader as rt
import trader
from trader import cli, config, daemon, legacy, strategy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_shim_is_the_legacy_namespace():
    assert rt is legacy
    for name in ("MomentumStrategy", "RobinhoodExecutor", "MarketHours", "load_trader_config", "_floor_qty", "_fmt_qty",
                 "ComplianceAndRiskGuard", "ObsidianTradingVault", "FinancialData", "TechnicalIndicators",
                 "NewsSentimentEngine", "AgentAdvisor", "TradingStrategyEngine", "TradingDataManager", "PortfolioAuditor",
                 "AccountResolutionError", "OrderNotConfirmed", "authenticate_robinhood_mcp", "RobinhoodAPI",
                 "_momentum_rebalance_pulse", "_risk_monitor_pulse", "run_backtest", "run_risk_monitor", "main",
                 "run_scheduled_trading_lifecycle", "ET_ZONE", "DEFAULT_WATCHLIST", "trader_config_path"):
        assert hasattr(rt, name), name
    assert rt.MomentumStrategy is strategy.MomentumStrategy


def test_no_conflicting_names_between_submodules():
    seen = {}
    for mod in legacy.SUBMODULES:
        for k, v in vars(mod).items():
            if k.startswith("__"):
                continue
            if k in seen and seen[k][1] is not v:
                raise AssertionError(f"{k} differs between {seen[k][0]} and {mod.__name__}")
            seen.setdefault(k, (mod.__name__, v))


def test_monkeypatching_shim_constant_reaches_every_submodule(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "RISK_MONITOR_FLAGS", str(tmp_path / "flags"))
    assert daemon.RISK_MONITOR_FLAGS == config.RISK_MONITOR_FLAGS == str(tmp_path / "flags")
    assert daemon._rebalance_flag_path("2026-09").startswith(str(tmp_path / "flags"))


def test_monkeypatch_undo_restores_submodules(tmp_path):
    orig = daemon.RISK_MONITOR_FLAGS
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(rt, "RISK_MONITOR_FLAGS", str(tmp_path))
    mp.undo()
    assert daemon.RISK_MONITOR_FLAGS == orig == rt.RISK_MONITOR_FLAGS


def test_state_paths_are_the_live_defaults(tmp_path):
    # The conftest redirects them for tests; the unpatched defaults must be the paths the live daemon uses.
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import robinhood_trader as rt, os; "
            "print(rt.RISK_MONITOR_FLAGS); print(rt.CONFIG_DIR); print(rt.CACHE_DIR); print(rt.trader_config_path())")
    home = str(tmp_path)
    out = subprocess.run([sys.executable, "-c", code, REPO], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "HOME": home, "RH_TRADER_CONFIG": ""}).stdout.split()
    assert out == [os.path.join(home, ".config/ai/.monitor_flags"), os.path.join(home, ".config/ai"),
                   os.path.join(home, ".cache/ai/trading"), os.path.join(home, ".config/ai/trader.json")]


def test_cli_main_returns_exit_codes(capsys):
    assert cli.main(["help"]) == 0
    assert "monitor" in capsys.readouterr().out
    assert cli.main(["no-such-command"]) == 1
    assert cli.main(["buy"]) == 1


def test_script_entry_point_still_works():
    r = subprocess.run([sys.executable, os.path.join(REPO, "robinhood_trader.py"), "--help"], capture_output=True,
                       text=True, timeout=60, cwd="/")
    assert r.returncode == 0 and "risk-monitor" in r.stdout


def test_package_path_pulse_with_finished_month_and_topup_plans_nothing(monkeypatch):
    """Mid-session switch to the deployed package must not repeat the month's rebalance or today's top-up."""
    orders = []
    E = rt.RobinhoodExecutor
    monkeypatch.setattr(E, "get_live_portfolio", lambda a: {"total_value": 500.0, "equity_value": 400.0, "cash": 100.0})
    monkeypatch.setattr(E, "get_equity_positions", lambda a: [{"symbol": "VTI", "quantity": "1", "average_buy_price": "300"},
                                                              {"symbol": "NVDA", "quantity": "0.5", "average_buy_price": "200"}])
    monkeypatch.setattr(E, "get_equity_quotes", lambda s: {x: {"last_trade_price": "200"} for x in s})
    monkeypatch.setattr(E, "execute_market_order", lambda *a, **k: orders.append((a, k)) or {"ok": True})
    monkeypatch.setattr(rt.FinancialData, "fetch_historical", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-plan")))
    monkeypatch.setattr(rt.MarketHours, "now_et", lambda: datetime.datetime(2026, 9, 22, 10, 5, tzinfo=rt.ET_ZONE))
    os.makedirs(rt.RISK_MONITOR_FLAGS, exist_ok=True)
    with open(rt._rebalance_flag_path("2026-09"), "w") as fh:
        json.dump({"month": "2026-09", "date": "2026-09-01", "dry_run": False, "targets": ["NVDA"], "sells_pending": [],
                   "buys_pending": [], "sells_done": [], "buys_done": [], "done": True}, fh)
    with open(os.path.join(rt.RISK_MONITOR_FLAGS, "core_topup_2026-09-22.flag"), "w") as fh:
        json.dump({"ts": "2026-09-22T10:01:00-04:00", "symbol": "VTI", "dollar_amount": 10.0}, fh)
    daemon._momentum_rebalance_pulse("ACC1", dry_run=False)
    assert orders == []


def test_installed_lib_dir_is_on_the_shim_path():
    src = open(os.path.join(REPO, "robinhood_trader.py")).read()
    assert ".local/share/ai/lib" in src
    assert trader.__file__.startswith(REPO)
