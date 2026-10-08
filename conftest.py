import os
import shutil
import socket
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


# ---------------------------------------------------------------------------------------------
# Live model server / systemd isolation (tests/test_isolation_guard.py). A test reaching the live
# llama-proxy (:8080) starts the on-demand llama-server, which rewrites ~/.local/share/ai/env; a
# test running `systemctl --user restart llama-server.service` restarts it. So:
#  * the default model URL (environment + scratch-HOME env file) is unroutable; tests that need a
#    server set their own INFER_BASE_URL (the mock);
#  * `systemctl` is a no-op logging shim first on PATH (subprocesses included);
#  * in-process TCP connects to 127.0.0.1/localhost:8080/8081 are refused and recorded (a recorded
#    attempt fails the session); AI_TEST_ALLOW_LIVE_PORTS=1 opts out explicitly.
# ---------------------------------------------------------------------------------------------
DEAD_BASE_URL = "http://127.0.0.1:9/v1/"
_LIVE_PORTS = (8080, 8081)
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::ffff:127.0.0.1")


def _isolate_services(scratch):
    env_dir = os.path.join(scratch, ".local", "share", "ai")
    os.makedirs(env_dir, exist_ok=True)
    with open(os.path.join(env_dir, "env"), "w") as f:
        f.write("# test-run default: an unroutable model server\n"
                'export INFER_BASE_URL="%s"\n' % DEAD_BASE_URL)
    os.environ["INFER_BASE_URL"] = DEAD_BASE_URL
    bindir = os.path.join(scratch, "bin")
    os.makedirs(bindir, exist_ok=True)
    shim = os.path.join(bindir, "systemctl")
    with open(shim, "w") as f:
        f.write('#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % os.path.join(scratch, "systemctl_calls.log"))
    os.chmod(shim, 0o755)
    os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
    os.environ["AI_TEST_SCRATCH_HOME"] = scratch


def _install_live_port_guard():
    violations = socket._ai_test_live_port_violations = []
    orig_connect, orig_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _blocked(address):
        if not (isinstance(address, tuple) and len(address) >= 2 and address[1] in _LIVE_PORTS
                and str(address[0]) in _LOCAL_HOSTS):
            return False
        if os.environ.get("AI_TEST_ALLOW_LIVE_PORTS") == "1":
            return False
        violations.append((str(address[0]), address[1], os.environ.get("PYTEST_CURRENT_TEST", "?")))
        return True

    def connect(self, address):
        if _blocked(address):
            raise ConnectionRefusedError(111, "test isolation: connection to the live model server port "
                                              "%s:%s refused" % address[:2])
        return orig_connect(self, address)

    def connect_ex(self, address):
        if _blocked(address):
            return 111
        return orig_connect_ex(self, address)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex


def _descendant_of_us(pid):
    me, seen = os.getpid(), 0
    while pid > 1 and seen < 64:
        if pid == me:
            return True
        try:
            with open("/proc/%d/stat" % pid) as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
        seen += 1
    return False


def _install_live_kill_guard():
    """Refuse in-process signals (os.kill, os.killpg) to a llama-server/llama-proxy the test run did not start:
    ai-backend's kill_llama_server() SIGKILLs every llama-server on the host (the developer's live
    one included) when a serve test forgets to stub it."""
    violations = os._ai_test_live_kill_violations = []
    orig_kill, orig_killpg = os.kill, os.killpg

    def _cmdline(pid):
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def _check(pids, sig, what):
        if sig == 0 or os.environ.get("AI_TEST_ALLOW_LIVE_KILL") == "1":
            return
        for pid in pids:
            if pid <= 1 or pid == os.getpid():
                continue
            cmdline = _cmdline(pid)
            if ("llama-server" in cmdline or "llama-proxy" in cmdline) and not _descendant_of_us(pid):
                violations.append((pid, cmdline.replace("\0", " ")[:120], os.environ.get("PYTEST_CURRENT_TEST", "?")))
                raise PermissionError(1, "test isolation: refusing to %s live process %d" % (what, pid))

    def _group(pgid):
        members = []
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    if os.getpgid(int(d)) == pgid:
                        members.append(int(d))
                except OSError:
                    pass
        return members

    def kill(pid, sig):
        if isinstance(pid, int):
            _check([pid] if pid > 0 else (_group(-pid) if pid < -1 else []), sig, "signal")
        return orig_kill(pid, sig)

    def killpg(pgid, sig):
        if isinstance(pgid, int) and pgid > 1:
            _check(_group(pgid), sig, "signal the process group of")
        return orig_killpg(pgid, sig)

    os.kill, os.killpg = kill, killpg


_GUARD_ON = os.environ.get("AI_TEST_HOME_GUARD", "1") != "0"
_REAL_HOME_BEFORE = _snapshot_real_home() if _GUARD_ON else {}
_SCRATCH_HOME = _isolate_home()
_isolate_services(_SCRATCH_HOME)
_install_live_port_guard()
_install_live_kill_guard()
# also exported so tests (and the guard self-test) can see what the original HOME was
os.environ["AI_TEST_ORIG_HOME"] = _ORIG_HOME


def _real_home_changes():
    after = _snapshot_real_home()
    changed = sorted(k for k in after if k not in _REAL_HOME_BEFORE or after[k] != _REAL_HOME_BEFORE[k])
    removed = sorted(k for k in _REAL_HOME_BEFORE if k not in after)
    return changed, removed


def _report(session, msg):
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.write_line(msg, red=True)
    else:
        print(msg)


def pytest_sessionfinish(session, exitstatus):
    try:
        hits = socket._ai_test_live_port_violations
        if hits:
            _report(session, "\nLIVE-PORT GUARD: tests tried to reach the live model server:\n" +
                    "\n".join("  %s:%s  from %s" % h for h in hits[:30]))
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        kills = getattr(os, "_ai_test_live_kill_violations", [])
        if kills:
            _report(session, "\nLIVE-KILL GUARD: tests tried to signal a live llama-server/proxy:\n" +
                    "\n".join("  pid %s %s  from %s" % k for k in kills[:30]))
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        try:
            with open(os.path.join(_SCRATCH_HOME, "systemctl_calls.log")) as f:
                calls = f.read().splitlines()
        except OSError:
            calls = []
        if calls and os.environ.get("AI_TEST_SHOW_SYSTEMCTL"):
            print("\nsystemctl shim: %d call(s) intercepted" % len(calls))
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
