"""trader/deploy.sh: versioned releases under $AI_TRADER_HOME (default ~/.local/share/ai-trader) with an
atomic `current` symlink.

Runs against a temp clone of this repo (the working-tree trader package is overlaid and committed in the
clone, so uncommitted changes are exercised too) with HOME and AI_TRADER_HOME pointing into tmp_path. Also proves the deployed
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
    # trader code AND the tests/conftest that gate it, so the gate sees a consistent tree
    for rel in ("trader", "robinhood_trader.py", "tests", "conftest.py", "pytest.ini"):
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


@pytest.fixture
def base(tmp_path):
    """Non-default deploy base (required by --skip-tests)."""
    return tmp_path / "ai-trader-test"


def _deploy(clone, home, *args, base=None, nested_gate=True):
    env = {**os.environ, "HOME": str(home)}
    env.pop("AI_TRADER_HOME", None)
    # A gate run here leaves tests/test_trader_deploy.py out (as it does when deploy.sh runs from
    # within a real gate), so these tests don't re-run themselves recursively.
    env.pop("AI_TRADER_DEPLOY_GATE", None)
    if nested_gate:
        env["AI_TRADER_DEPLOY_GATE"] = "1"
    if base is not None:
        env["AI_TRADER_HOME"] = str(base)
    return subprocess.run(["bash", str(clone / "trader" / "deploy.sh"), *args], cwd=str(home),
                          capture_output=True, text=True, env=env, timeout=300)


def _release(r):
    return r.stdout.strip().splitlines()[-1]


def test_skip_tests_builds_release_and_current_symlink(clone, home, base):
    r = _deploy(clone, home, "--skip-tests", base=base)
    assert r.returncode == 0, r.stdout + r.stderr
    sha = _git(clone, "rev-parse", "--short", "HEAD")
    release = _release(r)
    assert os.path.dirname(release) == str(base / "releases")
    assert os.path.basename(release).endswith("-" + sha)
    for rel in ("robinhood_trader.py", "VERSION", "trader/__init__.py", "trader/legacy.py", "trader/daemon.py",
                "trader/strategy.py", "trader/shadow.py", "trader/summary.py", "trader/deploy.sh"):
        assert os.path.isfile(os.path.join(release, rel)), rel
    assert not any("__pycache__" in d for d, _, _ in os.walk(release))
    assert sha in open(os.path.join(release, "VERSION")).read()
    cur = base / "current"
    assert cur.is_symlink() and os.path.realpath(cur) == os.path.realpath(release)
    assert not (home / ".local" / "share" / "ai-trader").exists()
    # the deployed shim runs on its own (cwd and HOME outside any checkout)
    h = subprocess.run([sys.executable, str(cur / "robinhood_trader.py"), "help"], cwd=str(home),
                       capture_output=True, text=True, env={**os.environ, "HOME": str(home)}, timeout=60)
    assert h.returncode == 0 and "monitor" in h.stdout


def test_skip_tests_refused_for_the_default_base(clone, home):
    for base in (None, home / ".local" / "share" / "ai-trader"):
        r = _deploy(clone, home, "--skip-tests", base=base)
        assert r.returncode != 0
        assert "AI_TRADER_HOME" in r.stdout + r.stderr
    assert not (home / ".local" / "share" / "ai-trader" / "current").exists()


def test_old_dry_run_flag_is_rejected(clone, home, base):
    r = _deploy(clone, home, "--dry-run", base=base)
    assert r.returncode != 0 and not (base / "current").exists()


def test_keeps_last_five_releases(clone, home, base):
    rels = base / "releases"
    rels.mkdir(parents=True)
    for i in range(6):
        (rels / f"2020010{i}T000000Z-old{i}").mkdir()
    r = _deploy(clone, home, "--skip-tests", base=base)
    assert r.returncode == 0, r.stdout + r.stderr
    left = sorted(os.listdir(rels))
    assert len(left) == 5
    assert os.path.basename(_release(r)) in left
    assert "20200100T000000Z-old0" not in left


def test_previous_release_survives_pruning(clone, home, base):
    # The running daemon still executes the release `current` pointed at before the deploy.
    rels = base / "releases"
    rels.mkdir(parents=True)
    for i in range(6):
        (rels / f"2020010{i}T000000Z-old{i}").mkdir()
    os.symlink("releases/20200100T000000Z-old0", base / "current")
    r = _deploy(clone, home, "--skip-tests", base=base)
    assert r.returncode == 0, r.stdout + r.stderr
    left = sorted(os.listdir(rels))
    assert "20200100T000000Z-old0" in left and "20200101T000000Z-old1" not in left
    assert len(left) == 6
    assert os.path.realpath(base / "current") == os.path.realpath(_release(r))


def test_dirty_tree_is_refused(clone, home, base):
    with open(clone / "robinhood_trader.py", "a") as fh:
        fh.write("\n# local edit\n")
    r = _deploy(clone, home, "--skip-tests", base=base)
    assert r.returncode != 0
    assert "dirty" in (r.stdout + r.stderr).lower()
    assert not (base / "current").exists()


def test_failing_tests_are_refused(clone, home, base):
    with open(clone / "trader" / "strategy.py", "a") as fh:
        fh.write("\nraise RuntimeError('broken release')\n")
    _git(clone, "commit", "-q", "--no-verify", "-am", "break it")
    r = _deploy(clone, home, base=base)
    assert r.returncode != 0
    assert "tests failed" in (r.stdout + r.stderr).lower()
    assert not (base / "current").exists()


def test_gate_lists_every_trader_test_file():
    import glob
    import re
    src = open(os.path.join(REPO, "trader", "deploy.sh")).read()
    listed = set(re.findall(r"tests/test_[a-z_]+\.py", src[src.index("TRADER_TESTS=("):]))
    want = {os.path.relpath(p, REPO) for p in glob.glob(os.path.join(REPO, "tests", "test_trader_*.py"))}
    want |= {"tests/test_robinhood_trader.py", "tests/test_momentum_strategy.py"}
    assert want <= listed, sorted(want - listed)


def test_ref_deploys_a_commit_despite_a_dirty_tree(clone, home, base):
    old_sha = _git(clone, "rev-parse", "HEAD")
    with open(clone / "trader" / "shadow.py", "a") as fh:
        fh.write("\nRELEASE_MARKER = 'second'\n")
    _git(clone, "commit", "-q", "--no-verify", "-am", "second")
    with open(clone / "robinhood_trader.py", "a") as fh:   # dirty, uncommitted edit
        fh.write("\n# local edit\n")
    (clone / "untracked.txt").write_text("x")
    r = _deploy(clone, home, "--skip-tests", "--ref", old_sha, base=base)
    assert r.returncode == 0, r.stdout + r.stderr
    rel = _release(r)
    assert os.path.basename(rel).endswith("-" + _git(clone, "rev-parse", "--short", old_sha))
    assert "commit=" + old_sha in open(os.path.join(rel, "VERSION")).read()
    assert "RELEASE_MARKER" not in open(os.path.join(rel, "trader", "shadow.py")).read()
    assert "# local edit" not in open(os.path.join(rel, "robinhood_trader.py")).read()
    # without --ref the same dirty tree is still refused
    r2 = _deploy(clone, home, "--skip-tests", base=base)
    assert r2.returncode != 0 and "dirty" in (r2.stdout + r2.stderr).lower()


def test_ref_rejects_a_non_commit(clone, home, base):
    r = _deploy(clone, home, "--skip-tests", "--ref", "no-such-ref", base=base)
    assert r.returncode != 0 and "not a commit" in r.stderr
    assert not (base / "current").exists()


def test_gate_tests_the_deployed_commit_not_the_worktree(clone, home, base):
    """--ref <broken commit> with a fixed (dirty) working tree is refused: the gate runs on a
    checkout of the commit. And a good commit passes the gate while the worktree is broken."""
    good = _git(clone, "rev-parse", "HEAD")
    with open(clone / "trader" / "strategy.py", "a") as fh:
        fh.write("\nraise RuntimeError('broken release')\n")
    _git(clone, "commit", "-q", "--no-verify", "-am", "break it")
    bad = _git(clone, "rev-parse", "HEAD")
    _git(clone, "checkout", "-q", good, "--", "trader/strategy.py")      # worktree fixed, uncommitted
    r = _deploy(clone, home, "--ref", bad, base=base)
    assert r.returncode != 0 and "tests failed" in (r.stdout + r.stderr).lower()
    assert not (base / "current").exists()
    with open(clone / "trader" / "strategy.py", "a") as fh:            # worktree broken instead
        fh.write("\nraise RuntimeError('broken worktree')\n")
    r = _deploy(clone, home, "--ref", good, base=base)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "commit=" + good in open(os.path.join(_release(r), "VERSION")).read()


REPOINT_PROBE = textwrap.dedent("""
    import importlib, os, runpy, sys
    base, new_target = sys.argv[1], sys.argv[2]
    # Load the shim by its symlinked path, exactly as `python3 <base>/current/robinhood_trader.py` does.
    runpy.run_path(os.path.join(base, "current", "robinhood_trader.py"), run_name="rh_probe")
    import trader
    old_release = os.path.realpath(os.path.join(base, "current"))
    sys.modules.pop("trader.shadow", None)
    sys.modules.pop("trader.summary", None)
    tmp = os.path.join(base, ".probe_link")
    os.symlink(new_target, tmp)
    os.replace(tmp, os.path.join(base, "current"))          # deploy.sh repoints `current` mid-run
    shadow = importlib.import_module("trader.shadow")     # a lazy import after the repoint
    print("PATH", trader.__path__[0])
    print("FILE", os.path.realpath(shadow.__file__))
    print("OLD", old_release)
    print("MARKER", getattr(shadow, "RELEASE_MARKER", None))
""")


def test_repointing_current_does_not_change_lazy_imports(clone, home, base, tmp_path):
    r1 = _deploy(clone, home, "--skip-tests", base=base)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    with open(clone / "trader" / "shadow.py", "a") as fh:
        fh.write("\nRELEASE_MARKER = 'second'\n")
    _git(clone, "commit", "-q", "--no-verify", "-am", "second release")
    r2 = _deploy(clone, home, "--skip-tests", base=base)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    os.remove(base / "current")
    os.symlink(os.path.relpath(_release(r1), base), base / "current")   # daemon started on release 1
    probe = tmp_path / "probe.py"
    probe.write_text(REPOINT_PROBE)
    p = subprocess.run([sys.executable, str(probe), str(base), os.path.relpath(_release(r2), base)],
                       cwd=str(home), capture_output=True, text=True, timeout=60,
                       env={**os.environ, "HOME": str(home), "INFER_TEST_MODE": "1"})
    assert p.returncode == 0, p.stdout + p.stderr
    out = dict(line.split(" ", 1) for line in p.stdout.splitlines() if " " in line)
    assert out["MARKER"] == "None", "lazy import loaded the newer release"
    assert out["FILE"].startswith(os.path.realpath(_release(r1)))
    assert out["PATH"] == os.path.join(os.path.realpath(_release(r1)), "trader")


PULSE_SCRIPT = textwrap.dedent("""
    import datetime, json, os, sys
    sys.path.insert(0, sys.argv[1])
    import robinhood_trader as rt
    assert os.path.realpath(rt.__file__).startswith(sys.argv[1]), rt.__file__
    home = os.environ["HOME"]
    assert rt.RISK_MONITOR_FLAGS == os.path.join(home, ".config/ai/.monitor_flags"), rt.RISK_MONITOR_FLAGS
    assert rt.trader_config_path() == os.path.join(home, ".config/ai/trader.json")
    assert rt.CACHE_DIR == os.path.join(home, ".cache/ai/trading")
    orders, fetches = [], []
    E = rt.RobinhoodExecutor
    E.get_live_portfolio = classmethod(lambda c, a: {"total_value": 500.0, "equity_value": 400.0, "cash": 100.0})
    E.get_equity_positions = classmethod(lambda c, a: [{"symbol": "VTI", "quantity": "1", "average_buy_price": "300"},
                                                       {"symbol": "NVDA", "quantity": "0.5", "average_buy_price": "200"}])
    E.get_equity_quotes = classmethod(lambda c, s: {x: {"last_trade_price": "200"} for x in s})
    E.execute_market_order = classmethod(lambda c, *a, **k: orders.append((a, k)) or {"ok": True})
    # Good, rising bars for every symbol: if the month were (re)planned, the sleeve would be bought.
    BARS = [{"datetime": f"d{i}", "open": 100 + i, "high": 100 + i, "low": 100 + i, "close": 100.0 + i,
             "volume": 1_000_000} for i in range(260)]
    rt.FinancialData.fetch_historical = classmethod(lambda c, sym, *a, **k: fetches.append(sym) or list(BARS))
    rt.MarketHours.now_et = staticmethod(lambda: datetime.datetime(2026, 9, 22, 10, 5, tzinfo=rt.ET_ZONE))
    rt._momentum_rebalance_pulse("ACC1", dry_run=False)
    print("ORDERS", len(orders))
    print("FETCHES", len(fetches))
""")


@pytest.mark.parametrize("month_done", [True, False])
def test_deployed_release_reads_live_state_and_plans_nothing(clone, home, base, tmp_path, month_done):
    r = _deploy(clone, home, "--skip-tests", base=base)
    assert r.returncode == 0, r.stdout + r.stderr
    flags = home / ".config" / "ai" / ".monitor_flags"
    flags.mkdir(parents=True)
    if month_done:
        (flags / "rebalance_2026-09.json").write_text(
            '{"month": "2026-09", "date": "2026-09-01", "dry_run": false, "targets": ["NVDA"], "sells_pending": [], '
            '"buys_pending": [], "sells_done": [], "buys_done": [], "done": true}')
    (flags / "core_topup_2026-09-22.flag").write_text('{"ts": "2026-09-22T10:01:00-04:00", "symbol": "VTI"}')
    script = tmp_path / "pulse.py"
    script.write_text(PULSE_SCRIPT)
    p = subprocess.run([sys.executable, str(script), os.path.realpath(base / "current")], cwd=str(home),
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "HOME": str(home), "INFER_TEST_MODE": "1"})
    assert p.returncode == 0, p.stdout + p.stderr
    if month_done:
        assert "ORDERS 0" in p.stdout and "FETCHES 0" in p.stdout, p.stdout
    else:   # control: without the done flag the same data plans and trades the month
        assert "ORDERS 0" not in p.stdout and "FETCHES 0" not in p.stdout, p.stdout
