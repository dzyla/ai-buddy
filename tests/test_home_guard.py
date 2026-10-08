"""The root conftest.py isolates HOME/XDG_* for the whole run and fails the session if the real
HOME's ~/.config/ai, ~/.cache/ai or ~/.local/share/ai changed. Self-test: run a tiny pytest
session (with a fake "real" HOME) whose test writes into the original HOME, and check the guard
turns it into a failure; a clean session passes."""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_home_is_isolated_for_the_suite():
    orig = os.environ["AI_TEST_ORIG_HOME"]
    assert os.path.expanduser("~") != orig
    for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"):
        assert not os.environ[var].startswith(orig + os.sep), var


def _run_probe(tmp_path, body):
    fake_home = tmp_path / "realhome"
    (fake_home / ".cache" / "ai").mkdir(parents=True)
    (fake_home / ".cache" / "ai" / "history.jsonl").write_text("x\n")
    probe = tmp_path / "test_probe.py"
    probe.write_text("import os\n\ndef test_probe():\n" + body)
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(fake_home), AI_TEST_HOME_GUARD="1")
    env.pop("AI_TEST_HOME_GUARD_IGNORE", None)
    env.pop("AI_TEST_ORIG_HOME", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "conftest", "-p", "no:cacheprovider",
         "--rootdir", str(tmp_path), "-c", os.devnull, str(probe)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120)


def test_guard_fails_session_that_writes_real_home(tmp_path):
    r = _run_probe(tmp_path, "    p = os.path.join(os.environ['AI_TEST_ORIG_HOME'], '.cache/ai/history.jsonl')\n"
                             "    open(p, 'a').write('leak\\n')\n")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "HOME GUARD" in r.stdout and "history.jsonl" in r.stdout


def test_guard_passes_clean_session(tmp_path):
    r = _run_probe(tmp_path, "    p = os.path.expanduser('~/.cache/ai/history.jsonl')\n"
                             "    os.makedirs(os.path.dirname(p), exist_ok=True)\n"
                             "    open(p, 'a').write('fine\\n')\n")
    assert r.returncode == 0, r.stdout + r.stderr
