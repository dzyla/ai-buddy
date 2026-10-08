"""Python engine: an unexpected exception inside a task (an engine bug) must not lose the
conversation: the error is reported, the exit code is 1 and the session is still saved
(Engine.run's except + finally -> _finish -> session.save)."""
import json
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_offline import MockServer  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRIVER = textwrap.dedent("""
    import sys
    sys.path.insert(0, sys.argv[1])
    from agent import loop

    real_task = loop.Engine._task

    def crashing_task(self):
        self.messages.append({"role": "user", "content": "CRASH_MARKER the task in progress"})
        raise RuntimeError("simulated engine bug")
    loop.Engine._task = crashing_task
    sys.exit(loop.main(["-y", "do something"]))
""")


def test_unexpected_task_exception_still_saves_the_session(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_") or k in ("INFER_TOOL_SERVER",)}
    with MockServer(str(tmp_path / "cap.jsonl")) as srv:
        env.update(HOME=str(home), INFER_BASE_URL=srv.base_url, INFER_API_KEY="test", INFER_MODEL="mock",
                   INFER_SESSION_ID="sess_crash_test", INFER_TOOL_CHOICE="auto")
        r = subprocess.run([sys.executable, str(driver), REPO], cwd=str(work), env=env,
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert r.returncode == 1, (r.stdout[-2000:], r.stderr[-2000:])
    assert "internal error in the Python engine" in r.stdout + r.stderr
    assert "simulated engine bug" in r.stdout + r.stderr
    saved = home / ".cache" / "ai" / "sessions" / "sess_crash_test.json"
    assert saved.exists(), list((home / ".cache").rglob("*"))
    msgs = json.loads(saved.read_text())
    assert any("CRASH_MARKER" in str(m.get("content")) for m in msgs)
    last = home / ".cache" / "ai" / "sessions" / "last.json"
    assert last.exists() and "CRASH_MARKER" in last.read_text()
