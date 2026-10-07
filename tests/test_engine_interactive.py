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
                return plain[:k]  # the output before the match
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


def _slow_cmd(marker, seconds=3):
    code = "open(%r, 'w').close(); import time; time.sleep(%d)" % (str(marker), seconds)
    return "python3 -c \"%s\"" % code


def _wait_file(p, path, timeout=30):
    deadline = time.time() + timeout
    while not path.exists() and time.time() < deadline:
        p._read(0.1)
    assert path.exists(), ANSI.sub("", p.buf)[-2000:]


def test_ctrl_o_redraws_mid_task(tmp_path, engine):
    if engine == "c":
        pytest.skip("the C loop reads only ESC from the terminal while a command runs")
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    marker = tmp_path / "running"
    seq = [{"id": "o1", "type": "function", "function": {"name": "execute_command",
                                                          "arguments": json.dumps({"command": _slow_cmd(marker)})}}]
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TASK_COMPLETE="REDRAW_DONE") as srv:
        p = Pty(["-y", "slow job"], _env(home, srv.base_url, INFER_AUTO_APPROVE="1"), str(work))
        _wait_file(p, marker)
        p.send("\x0f")
        p.expect("details: COLLAPSED (Ctrl+O to toggle)")
        p.expect("REDRAW_DONE")
        assert p.wait() == 0


def test_shift_tab_mid_task_leaves_manual_mode(tmp_path, engine):
    if engine == "c":
        pytest.skip("the C loop treats ESC [ Z during a command as ESC (interrupt)")
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    marker, made = tmp_path / "running", tmp_path / "second.txt"
    seq = [{"id": "t1", "type": "function", "function": {"name": "execute_command",
                                                          "arguments": json.dumps({"command": _slow_cmd(marker)})}},
           {"id": "t2", "type": "function", "function": {"name": "execute_command",
                                                          "arguments": json.dumps({"command": "touch %s" % made})}}]
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TASK_COMPLETE="MODE_DONE") as srv:
        p = Pty(["--manual", "two commands"], _env(home, srv.base_url), str(work))
        p.expect("Confirm? (b=background)")
        p.send("y")
        _wait_file(p, marker)
        p.send("\x1b[Z")
        p.expect("permission mode: auto")
        between = p.expect("MODE_DONE")
        assert "Confirm? (b=background)" not in between, "the second command was still prompted"
        assert p.wait() == 0
    assert made.exists()


def test_background_approval(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    seq = [{"id": "g1", "type": "function", "function": {"name": "execute_command",
                                                          "arguments": json.dumps({"command": "echo BG_OUTPUT"})}}]
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TASK_COMPLETE="BG_DONE") as srv:
        p = Pty(["--manual", "run it in the background"], _env(home, srv.base_url), str(work))
        p.expect("Confirm? (b=background)")
        p.send("b")
        p.expect("BG_DONE")
        assert p.wait() == 0
        reqs = srv.requests()
    tool = [m["content"] for m in reqs[1]["messages"] if m["role"] == "tool"][0]
    assert "Command launched in background" in tool
    logs = list((home / ".config" / "ai" / "logs").glob("bg_proc_*.log"))
    assert logs
    for _ in range(50):
        if "BG_OUTPUT" in logs[0].read_text():
            break
        time.sleep(0.1)
    assert "BG_OUTPUT" in logs[0].read_text()


def test_step_limit_continue_prompt(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    seq = [{"id": "s%d" % i, "type": "function", "function": {"name": "list_directory",
                                                               "arguments": json.dumps({"path": ".", "n": i})}}
           for i in range(3)]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TASK_COMPLETE="STEPS_DONE") as srv:
        p = Pty(["-y", "walk"], _env(home, srv.base_url, INFER_AUTO_APPROVE="1", INFER_STEP_LIMIT="2"), str(work))
        p.expect("2 steps completed. Continue for 30 more?")
        p.send("y\r")
        p.expect("STEPS_DONE")
        assert p.wait() == 0
        reqs = srv.requests()
    assert len(reqs) >= 3


def _git(repo, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=True).stdout


def test_git_and_private_colon_commands(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.hooksPath", "/dev/null")
    (repo / "tracked.txt").write_text("v1\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "init")
    (repo / "tracked.txt").write_text("v2\n")
    os.symlink(os.path.join(REPO, "ai_mcp.py"), repo / "ai_mcp.py")
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TASK_COMPLETE="PRIVATE_ANSWER") as srv:
        p = Pty(["-i"], _env(home, srv.base_url), str(repo))
        p.expect(PROMPT)
        p.send(":commit\r")
        p.expect("Git Committed")
        p.expect(PROMPT)
        assert _git(repo, "rev-list", "--count", "HEAD").strip() == "2"
        p.send(":undo\r")
        p.expect("Git Undo")
        p.expect(PROMPT)
        p.send(":private\r")
        p.expect("Private Mode ON")
        p.expect(PROMPT)
        p.send("a private question\r")
        p.expect("PRIVATE_ANSWER")
        p.expect(PROMPT)
        p.send(":quit\r")
        assert p.wait() == 0
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"
    assert _git(repo, "diff", "--cached", "--name-only").split() == ["tracked.txt"]   # undo kept it staged
    assert "user-commit" in _git(repo, "reflog")
    assert not (home / ".cache" / "ai" / "history.jsonl").exists()
    assert not (home / ".cache" / "ai" / "sessions").exists()


def test_ctrl_o_redraw_keeps_turn_and_tool_counts(tmp_path, engine):
    """The Ctrl-O redraw replays the answer box with its turn (and, on the Python engine, tool) count."""
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TASK_COMPLETE="COUNTED_ANSWER") as srv:
        p = Pty(["-i"], _env(home, srv.base_url), str(work))
        p.expect(PROMPT)
        p.send("count me\r")
        p.expect("COUNTED_ANSWER")
        p.expect(PROMPT)
        p.send("\x0f")
        p.expect("details: COLLAPSED")
        shown = p.expect(PROMPT)
        assert "COUNTED_ANSWER" in shown and "turn 1" in shown, shown
        if engine == "py":
            assert "1 tools" in shown, shown
        p.send(":quit\r")
        assert p.wait() == 0


def _zombie_children(pid):
    """PIDs of <defunct> processes whose parent is `pid` (from /proc/<n>/stat)."""
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name) as f:
                stat = f.read()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2:].split()  # after "pid (comm) ": state ppid ...
        if fields[0] == "Z" and int(fields[1]) == pid:
            out.append(int(name))
    return out


def test_finished_background_orphans_are_reaped(tmp_path):
    """`ai` is a child subreaper, so `nohup … &` jobs a command leaves behind are reparented to it.
    Once they finish, the front-end (Python engine) or the C loop reaps them: no <defunct> children
    pile up while the session idles at the prompt; the engine itself is still handled normally."""
    home = tmp_path / "home"
    home.mkdir()
    work = _work(tmp_path)
    jobs = "for i in 1 2 3 4 5; do nohup sh -c 'sleep 0.3' >/dev/null 2>&1 & done; echo LAUNCHED"
    seq = [{"id": "z1", "type": "function",
            "function": {"name": "execute_command", "arguments": json.dumps({"command": jobs})}}]
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TOOL_SEQ=json.dumps(seq), MOCK_TASK_COMPLETE="ORPHANS_DONE") as srv:
        p = Pty(["-i", "-y"], _env(home, srv.base_url, INFER_AUTO_APPROVE="1"), str(work))
        p.expect(PROMPT)
        p.send("start the jobs\r")
        p.expect("ORPHANS_DONE")
        p.expect(PROMPT)
        time.sleep(1.0)                 # the jobs finish after ~0.3 s
        deadline = time.time() + 5      # reaping may lag one idle tick of the prompt
        zombies = _zombie_children(p.pid)
        while zombies and time.time() < deadline:
            p._read(0.2)
            zombies = _zombie_children(p.pid)
        assert zombies == [], "defunct children of ai: %s" % zombies
        p.send(":quit\r")
        assert p.wait() == 0
