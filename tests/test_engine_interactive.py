"""Interactive mode through a real pseudo-terminal, for both engines: the C line editor reads the
turn, the engine (C loop, or Python behind the C front-end) answers, colon-commands work, ESC
interrupts a running command, and the session is saved on :quit.

The mock LLM and an isolated HOME keep it offline (see tests/test_offline.py)."""
import json
import os
import pty
import re
import select
import signal
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
from test_offline import AI_BIN, MockServer  # noqa: E402

pytestmark = pytest.mark.usefixtures("engine")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[78]")


class Pty:
    def __init__(self, args, env, cwd, shell_parent=False):
        """shell_parent: run ai under `sh` (the session leader, like a login shell) and keep the
        shell alive a few seconds after ai exits, so ai's own exit does not SIGHUP the group."""
        import warnings
        with warnings.catch_warnings():  # the child execs at once (forkpty in a threaded pytest)
            warnings.simplefilter("ignore", DeprecationWarning)
            self.pid, self.fd = pty.fork()
        if self.pid == 0:  # pragma: no cover (child)
            os.chdir(cwd)
            if shell_parent:
                os.execve("/bin/sh", ["sh", "-c", 'trap "" INT; "$@"; echo "AI_RC=$?"; sleep 4', "sh", AI_BIN] + args, env)
            os.execve(AI_BIN, [AI_BIN] + args, env)
        self.buf = ""

    def _read(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return False
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            return False
        if not data:
            return False
        self.buf += data.decode("utf-8", "replace")
        return True

    def expect(self, text, timeout=60):
        """Wait until `text` shows up in the ANSI-stripped output after the last match."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            plain = ANSI.sub("", self.buf)
            k = plain.find(text)
            if k >= 0:
                self.buf = plain[k + len(text):]
                return True
            if not self._read(0.2) and self.exited():
                break
        raise AssertionError("did not see %r; output so far:\n%s" % (text, ANSI.sub("", self.buf)[-3000:]))

    def send(self, s):
        os.write(self.fd, s.encode())

    def exited(self):
        try:
            pid, st = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid:
            self.status = st
            return True
        return False

    def wait(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._read(0.1)
            if self.exited():
                return os.waitstatus_to_exitcode(self.status)
        os.kill(self.pid, signal.SIGKILL)
        raise AssertionError("ai did not exit:\n" + ANSI.sub("", self.buf)[-2000:])


def _env(home, base_url, **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("INFER_") or k in ("INFER_TOOL_SERVER", "INFER_ENGINE", "INFER_ENGINE_STRICT")}
    env.update(HOME=str(home), INFER_BASE_URL=base_url, INFER_API_KEY="t", INFER_MODEL="mock",
               INFER_TOOL_CHOICE="auto", TERM="xterm", COLUMNS="120")
    env.update(extra)
    return env


def _work(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    for f in ("ai_mcp.py", "deep_research.py"):
        os.symlink(os.path.join(REPO, f), work / f)
    return work


PROMPT = "details:"   # part of the interactive prompt line (ai.c / ai_frontend.c)


def test_interactive_turns_colon_commands_and_quit(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="PTY_ANSWER_OK") as srv:
        p = Pty(["-i"], _env(home, srv.base_url), str(work))
        p.expect("type :help for commands")
        p.expect(PROMPT)
        p.send("first question\r")
        p.expect("PTY_ANSWER_OK")
        p.expect(PROMPT)
        p.send(":status\r")
        p.expect("Session status:")
        p.expect("Model          : mock")
        p.expect(PROMPT)
        p.send("second question\r")
        p.expect("PTY_ANSWER_OK")
        p.expect(PROMPT)
        for cmd, shows in ((":help", "Interactive Commands"), (":memory", "[no persistent memory saved yet]"),
                           (":mode instruct", "Mode Switched"), (":budget 512", "Thinking Budget Updated"),
                           (":auto", "permission mode: plan"), (":auto", "permission mode: manual"),
                           (":auto", "permission mode: auto"), (":details", "Details Visibility Toggled")):
            p.send(cmd + "\r")
            p.expect(shows)
            p.expect(PROMPT)
        p.send(":clear\r")
        p.expect("Conversation Cleared")
        p.expect(PROMPT)
        p.send(":not-a-command\r")          # unknown colon input is an ordinary user message
        p.expect("PTY_ANSWER_OK")
        p.expect(PROMPT)
        p.send(":quit\r")
        assert p.wait() == 0
        reqs = srv.requests()
    assert len(reqs) == 3
    users = [m["content"] for m in reqs[1]["messages"] if m["role"] == "user"]
    assert users == ["first question", "second question"]
    # after :clear only the system prompt + the new message; :mode instruct / :budget took effect
    assert [m["role"] for m in reqs[2]["messages"]] == ["system", "user"]
    assert reqs[2]["messages"][1]["content"] == ":not-a-command"
    assert reqs[2]["temperature"] == 0.7 and reqs[2]["reasoning_effort"] == "none"
    assert reqs[2]["reasoning_budget"] == 512
    assert (home / ".cache" / "ai" / "sessions" / "last.json").exists()
    hist = (home / ".cache" / "ai" / "input_history").read_text()
    assert "first question" in hist and "second question" in hist


def test_interactive_escape_interrupts_running_command(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    marker = tmp_path / "started"
    seq = [{"id": "s1", "type": "function", "function": {"name": "execute_command", "arguments": json.dumps(
        {"command": "touch %s && python3 -c 'import time; time.sleep(30)'" % marker})}}]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TASK_COMPLETE="AFTER_ESC") as srv:
        p = Pty(["-i", "-y", "run the slow thing"], _env(home, srv.base_url, INFER_AUTO_APPROVE="1"), str(work))
        deadline = time.time() + 30
        while not marker.exists() and time.time() < deadline:
            p._read(0.1)
        assert marker.exists(), ANSI.sub("", p.buf)[-2000:]
        t0 = time.time()
        p.send("\x1b")
        p.expect(PROMPT, timeout=20)
        assert time.time() - t0 < 15, "ESC did not stop the 30 s command"
        p.send(":quit\r")
        assert p.wait() == 0
        reqs = srv.requests()
    assert len(reqs) == 1  # the task stopped after the interrupted command


def test_ctrl_c_exits_and_takes_the_running_command_down(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    pidfile = tmp_path / "cmd.pid"
    code = "import os, time; open(%r, 'w').write(str(os.getpid())); time.sleep(60)" % str(pidfile)
    seq = [{"id": "s1", "type": "function", "function": {"name": "execute_command", "arguments": json.dumps(
        {"command": "python3 -c \"%s\"" % code})}}]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as srv:
        p = Pty(["-y", "run the slow thing"], _env(home, srv.base_url, INFER_AUTO_APPROVE="1"), str(work),
                shell_parent=True)
        deadline = time.time() + 30
        while not (pidfile.exists() and pidfile.read_text()) and time.time() < deadline:
            p._read(0.1)
        assert pidfile.exists(), ANSI.sub("", p.buf)[-2000:]
        cmd_pid = int(pidfile.read_text())
        p.send("\x03")
        p.expect("AI_RC=130", timeout=20)
    for _ in range(20):  # checked while the shell (session leader) is still alive: no SIGHUP help
        try:
            os.kill(cmd_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(cmd_pid, signal.SIGKILL)
        raise AssertionError("the running command survived Ctrl-C")
    p.wait(timeout=20)


@pytest.mark.parametrize("answer,ran", [("y", True), ("n", False)])
def test_manual_mode_command_approval_on_the_terminal(tmp_path, answer, ran):
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    target = tmp_path / "made_by_command.txt"
    seq = [{"id": "m1", "type": "function", "function": {"name": "execute_command", "arguments": json.dumps(
        {"command": "echo approved > %s" % target})}}]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TASK_COMPLETE="MANUAL_DONE") as srv:
        p = Pty(["--manual", "make the file"], _env(home, srv.base_url), str(work))
        p.expect("Confirm? (b=background)")
        p.send(answer)
        p.expect("MANUAL_DONE")
        assert p.wait() == 0
        reqs = srv.requests()
    assert target.exists() == ran
    tool = [m["content"] for m in reqs[1]["messages"] if m["role"] == "tool"][0]
    assert ("approved" in tool) if ran else ("cancelled/denied by the user" in tool)


def test_plan_mode_present_plan_approval_on_the_terminal(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    target = tmp_path / "planned.txt"
    seq = [{"id": "p1", "type": "function", "function": {"name": "present_plan", "arguments": json.dumps(
               {"plan": "write the planned file"})}},
           {"id": "p2", "type": "function", "function": {"name": "write_file", "arguments": json.dumps(
               {"path": str(target), "content": "ok"})}}]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TASK_COMPLETE="PLAN_DONE") as srv:
        p = Pty(["--plan", "do the planned work"], _env(home, srv.base_url), str(work))
        p.expect("APPROVE PLAN")
        p.send("y")
        p.expect("PLAN_DONE")
        assert p.wait() == 0
        reqs = srv.requests()
    assert target.read_text() == "ok"
    tools = [m["content"] for m in reqs[-1]["messages"] if m["role"] == "tool"]
    assert any("PLAN APPROVED" in t for t in tools)
