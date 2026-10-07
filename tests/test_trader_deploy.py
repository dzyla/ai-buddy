"""trader/deploy.sh: versioned releases under $HOME/.local/share/ai-trader with an atomic `current` symlink.

Runs against a temp clone of this repo (the working-tree trader package is overlaid and committed in the
clone, so uncommitted changes are exercised too) with HOME pointing at tmp_path. Also proves the deployed
release reads the same default state paths (~/.config/ai/...) and does not re-plan a finished month."""
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


@pytest.fixture
def clone(tmp_path):
    dst = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", REPO, str(dst))
    for rel in ("trader", "robinhood_trader.py"):
        src, out = os.path.join(REPO, rel), dst / rel
        if os.path.isdir(src):
            shutil.rmtree(out, ignore_errors=True)
            shutil.copytree(src, out, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, out)
    _git(dst, "add", "-A")
    if _git(dst, "status", "--porcelain"):
        _git(dst, "commit", "-q", "--no-verify", "-m", "overlay working tree")
    return dst


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def _deploy(clone, home, *args):
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(["bash", str(clone / "trader" / "deploy.sh"), *args], cwd=str(home),
                          capture_output=True, text=True, env=env, timeout=300)


def test_dry_run_builds_release_and_current_symlink(clone, home):
    r = _deploy(clone, home, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    sha = _git(clone, "rev-parse", "--short", "HEAD")
    base = home / ".local" / "share" / "ai-trader"
    release = r.stdout.strip().splitlines()[-1]
    assert os.path.dirname(release) == str(base / "releases")
    assert os.path.basename(release).endswith("-" + sha)
    for rel in ("robinhood_trader.py", "VERSION", "trader/__init__.py", "trader/legacy.py", "trader/daemon.py",
                "trader/strategy.py", "trader/shadow.py", "trader/summary.py", "trader/deploy.sh"):
        assert os.path.isfile(os.path.join(release, rel)), rel
    assert not any("__pycache__" in d for d, _, _ in os.walk(release))
    assert sha in open(os.path.join(release, "VERSION")).read()
    cur = base / "current"
    assert cur.is_symlink() and os.path.realpath(cur) == os.path.realpath(release)
    # the deployed shim runs on its own (cwd and HOME outside any checkout)
    h = subprocess.run([sys.executable, str(cur / "robinhood_trader.py"), "help"], cwd=str(home),
                       capture_output=True, text=True, env={**os.environ, "HOME": str(home)}, timeout=60)
    assert h.returncode == 0 and "monitor" in h.stdout


def test_keeps_last_five_releases(clone, home):
    rels = home / ".local" / "share" / "ai-trader" / "releases"
    rels.mkdir(parents=True)
    for i in range(6):
        (rels / f"2020010{i}T000000Z-old{i}").mkdir()
    r = _deploy(clone, home, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    left = sorted(os.listdir(rels))
    assert len(left) == 5
    assert os.path.basename(r.stdout.strip().splitlines()[-1]) in left
    assert "20200100T000000Z-old0" not in left


def test_dirty_tree_is_refused(clone, home):
    with open(clone / "robinhood_trader.py", "a") as fh:
        fh.write("\n# local edit\n")
    r = _deploy(clone, home, "--dry-run")
    assert r.returncode != 0
    assert "dirty" in (r.stdout + r.stderr).lower()
    assert not (home / ".local" / "share" / "ai-trader" / "current").exists()


def test_failing_tests_are_refused(clone, home):
    with open(clone / "trader" / "strategy.py", "a") as fh:
        fh.write("\nraise RuntimeError('broken release')\n")
    _git(clone, "commit", "-q", "--no-verify", "-am", "break it")
    r = _deploy(clone, home)
    assert r.returncode != 0
    assert "tests failed" in (r.stdout + r.stderr).lower()
    assert not (home / ".local" / "share" / "ai-trader" / "current").exists()


PULSE_SCRIPT = textwrap.dedent("""
    import datetime, json, os, sys
    sys.path.insert(0, sys.argv[1])
    import robinhood_trader as rt
    assert rt.__file__.startswith(sys.argv[1]), rt.__file__
    home = os.environ["HOME"]
    assert rt.RISK_MONITOR_FLAGS == os.path.join(home, ".config/ai/.monitor_flags"), rt.RISK_MONITOR_FLAGS
    assert rt.trader_config_path() == os.path.join(home, ".config/ai/trader.json")
    assert rt.CACHE_DIR == os.path.join(home, ".cache/ai/trading")
    orders = []
    E = rt.RobinhoodExecutor
    E.get_live_portfolio = classmethod(lambda c, a: {"total_value": 500.0, "equity_value": 400.0, "cash": 100.0})
    E.get_equity_positions = classmethod(lambda c, a: [{"symbol": "VTI", "quantity": "1", "average_buy_price": "300"},
                                                       {"symbol": "NVDA", "quantity": "0.5", "average_buy_price": "200"}])
    E.get_equity_quotes = classmethod(lambda c, s: {x: {"last_trade_price": "200"} for x in s})
    E.execute_market_order = classmethod(lambda c, *a, **k: orders.append((a, k)) or {"ok": True})
    rt.FinancialData.fetch_historical = classmethod(lambda c, *a, **k: (_ for _ in ()).throw(AssertionError("no re-plan")))
    rt.MarketHours.now_et = staticmethod(lambda: datetime.datetime(2026, 9, 22, 10, 5, tzinfo=rt.ET_ZONE))
    rt._momentum_rebalance_pulse("ACC1", dry_run=False)
    print("ORDERS", len(orders))
""")


def test_deployed_release_reads_live_state_and_plans_nothing(clone, home, tmp_path):
    r = _deploy(clone, home, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    flags = home / ".config" / "ai" / ".monitor_flags"
    flags.mkdir(parents=True)
    (flags / "rebalance_2026-09.json").write_text(
        '{"month": "2026-09", "date": "2026-09-01", "dry_run": false, "targets": ["NVDA"], "sells_pending": [], '
        '"buys_pending": [], "sells_done": [], "buys_done": [], "done": true}')
    (flags / "core_topup_2026-09-22.flag").write_text('{"ts": "2026-09-22T10:01:00-04:00", "symbol": "VTI"}')
    script = tmp_path / "pulse.py"
    script.write_text(PULSE_SCRIPT)
    cur = str(home / ".local" / "share" / "ai-trader" / "current")
    p = subprocess.run([sys.executable, str(script), os.path.realpath(cur)], cwd=str(home), capture_output=True,
                       text=True, env={**os.environ, "HOME": str(home), "INFER_TEST_MODE": "1"}, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ORDERS 0" in p.stdout
