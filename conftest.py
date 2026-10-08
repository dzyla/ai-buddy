import os
import shutil
import tempfile

import pytest

# ---------------------------------------------------------------------------------------------
# HOME isolation (applied at conftest IMPORT time, i.e. before any test module is imported and
# before any module/session-scoped fixture runs). Many modules compute paths at import time via
# os.path.expanduser("~/..."); because this runs first, those resolve into the scratch HOME.
# Every subprocess inherits the scratch HOME/XDG dirs. Tests that need their own HOME still
# monkeypatch it per test (tmp_path) on top of this.
# ---------------------------------------------------------------------------------------------
_ORIG_HOME = os.path.expanduser("~")
_GUARDED = (".config/ai", ".cache/ai", ".local/share/ai")
# Files the live services on a developer machine (trading daemon, llama server, scheduler) write
# during a test run; changes there are not attributed to the suite. Extra prefixes (relative to
# HOME): AI_TEST_HOME_GUARD_IGNORE="a/b:c/d". AI_TEST_HOME_GUARD=0 disables the check.
_GUARD_IGNORE = (
    ".cache/ai/trading", ".cache/ai/risk_monitor.log", ".cache/ai/scheduler.log",
    ".cache/ai/llama-server", ".cache/ai/backtest_bars", ".config/ai/trading_journal.json",
    ".config/ai/pdt_tracker.json", ".config/ai/daily_circuit_breaker.json",
    ".config/ai/.monitor_flags", ".config/ai/trading_vault", ".config/ai/logs",
    ".config/ai/robinhood_trading_state.json", ".local/share/ai/state.db",
    ".local/share/ai/models", ".local/share/ai/llama.cpp", ".local/share/ai/lib",
) + tuple(p for p in os.environ.get("AI_TEST_HOME_GUARD_IGNORE", "").split(":") if p)


def _ignored(rel):
    # a prefix covers itself, its subtree and siblings like state.db-wal / llama-server.log
    return any(rel == p or rel.startswith((p + "/", p + "-", p + ".")) for p in _GUARD_IGNORE)


def _snapshot_real_home():
    snap = {}
    for top in _GUARDED:
        root = os.path.join(_ORIG_HOME, top)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, _ORIG_HOME)
            dirnames[:] = [d for d in dirnames if not _ignored(os.path.join(rel_dir, d))]
            for fn in filenames:
                rel = os.path.join(rel_dir, fn)
                if _ignored(rel):
                    continue
                try:
                    st = os.lstat(os.path.join(dirpath, fn))
                except OSError:
                    continue
                snap[rel] = (st.st_mtime_ns, st.st_size)
    return snap


def _isolate_home():
    scratch = tempfile.mkdtemp(prefix="ai-test-home-")
    for sub in (".config", ".cache", ".local/share", "run"):
        os.makedirs(os.path.join(scratch, sub), exist_ok=True)
    os.chmod(os.path.join(scratch, "run"), 0o700)
    # keep pip --user packages importable in subprocesses despite the new HOME
    os.environ.setdefault("PYTHONUSERBASE", os.path.join(_ORIG_HOME, ".local"))
    os.environ["HOME"] = scratch
    os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, ".config")
    os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, ".cache")
    os.environ["XDG_DATA_HOME"] = os.path.join(scratch, ".local", "share")
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(scratch, "run")
    os.environ.setdefault("GIT_AUTHOR_NAME", "ai-test")
    os.environ.setdefault("GIT_AUTHOR_EMAIL", "ai-test@example.invalid")
    os.environ.setdefault("GIT_COMMITTER_NAME", "ai-test")
    os.environ.setdefault("GIT_COMMITTER_EMAIL", "ai-test@example.invalid")
    return scratch


_GUARD_ON = os.environ.get("AI_TEST_HOME_GUARD", "1") != "0"
_REAL_HOME_BEFORE = _snapshot_real_home() if _GUARD_ON else {}
_SCRATCH_HOME = _isolate_home()
# also exported so tests (and the guard self-test) can see what the original HOME was
os.environ["AI_TEST_ORIG_HOME"] = _ORIG_HOME


def _real_home_changes():
    after = _snapshot_real_home()
    changed = sorted(k for k in after if k not in _REAL_HOME_BEFORE or after[k] != _REAL_HOME_BEFORE[k])
    removed = sorted(k for k in _REAL_HOME_BEFORE if k not in after)
    return changed, removed


def pytest_sessionfinish(session, exitstatus):
    try:
        if _GUARD_ON and os.path.realpath(_ORIG_HOME) != os.path.realpath(_SCRATCH_HOME):
            changed, removed = _real_home_changes()
            if changed or removed:
                lines = [f"  changed/new: ~/{p}" for p in changed[:50]] + \
                        [f"  removed:     ~/{p}" for p in removed[:50]]
                msg = ("\nHOME GUARD: the test run modified the real HOME "
                       f"({_ORIG_HOME}):\n" + "\n".join(lines) +
                       "\n(set AI_TEST_HOME_GUARD_IGNORE for a path a live service writes)\n")
                tr = session.config.pluginmanager.get_plugin("terminalreporter")
                if tr is not None:
                    tr.write_line(msg, red=True)
                else:
                    print(msg)
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
    finally:
        shutil.rmtree(_SCRATCH_HOME, ignore_errors=True)


@pytest.fixture(autouse=True, scope="session")
def suppress_browser_and_remote_auth():
    """Ensure no test or subprocess triggers external auth websites or browser popups."""
    os.environ["BROWSER"] = ":"
    os.environ["NO_BROWSER"] = "1"
    os.environ.setdefault("AI_REAL_CI", os.environ.get("CI", ""))  # before forcing CI=1 (timing tests)
    os.environ["CI"] = "1"
    os.environ["DISPLAY"] = ""
    os.environ["INFER_TEST_MODE"] = "1"
    # Never reach/spawn the persistent tool server from a test (module/session-scoped fixtures run
    # before tests/conftest.py's per-test setting); tests/test_tool_server.py opts in explicitly.
    os.environ["INFER_TOOL_SERVER"] = "0"
    os.environ.pop("WSL_DISTRO_NAME", None)
    # Engine: tests that don't use the `engine` fixture (tests/conftest.py, which runs a test once
    # per engine) run the binary's default engine (Python, agent/) on purpose, never a developer's
    # sourced INFER_ENGINE, and never silently fall back to the legacy C loop when agent/ is not
    # found. AI_TEST_ENGINE=c runs them on the legacy C loop instead (CI runs the suite both ways).
    os.environ.pop("INFER_ENGINE", None)
    if os.environ.get("AI_TEST_ENGINE") in ("c", "py"):
        os.environ["INFER_ENGINE"] = os.environ["AI_TEST_ENGINE"]
    os.environ["INFER_ENGINE_STRICT"] = "1"
