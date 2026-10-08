"""Engine parity: the same scripted conversations through the C loop (INFER_ENGINE=c) and the Python
engine behind the C front-end (INFER_ENGINE=py) must send equivalent requests (whole payloads:
sampling fields, tool catalog, message roles/contents/tool_calls — modulo call ids, timestamps,
session ids and whitespace) and print the same stdout once ANSI escapes are stripped.

Both engines run in the same work dir / HOME paths (wiped in between) with their own state db, so
nothing one run learns can leak into the other's prompt.
"""
import base64
import copy
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
from test_offline import AI_BIN, MockServer  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[78]")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?(?: [A-Za-z+0-9]+)?")
SID = re.compile(r"sess_\d+_\d+")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _norm_text(s):
    s = STAMP.sub("<TS>", s)
    s = SID.sub("<SID>", s)
    return " ".join(s.split())


def _norm_request(req):
    req = copy.deepcopy(req)
    for m in req.get("messages", []):
        c = m.get("content")
        if isinstance(c, str):
            m["content"] = _norm_text(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = _norm_text(part["text"])
        if "tool_call_id" in m:
            m["tool_call_id"] = "<ID>"
        for tc in m.get("tool_calls") or []:
            tc["id"] = "<ID>"
    return req


def _strip(s):
    return ANSI.sub("", s)


class Root:
    """One fixed tmp location reused by both engines (paths appear in prompts and tool output)."""

    def __init__(self, tmp_path):
        self.base = tmp_path / "run"
        self.home = self.base / "home"
        self.work = self.base / "work"

    def reset(self, setup=None):
        shutil.rmtree(self.base, ignore_errors=True)
        self.home.mkdir(parents=True)
        self.work.mkdir(parents=True)
        for f in ("ai_mcp.py", "deep_research.py"):
            os.symlink(os.path.join(REPO, f), self.work / f)
        if setup:
            setup(self)

    def run(self, engine, args, mock_env, stdin=None, extra_env=None):
        cap = str(self.base / ("cap_%d.jsonl" % len(list(self.base.glob("cap_*.jsonl")))))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("INFER_", "MOCK_")) or k == "INFER_TOOL_SERVER"}
        with MockServer(cap, **mock_env) as srv:
            env.update(HOME=str(self.home), INFER_BASE_URL=srv.base_url, INFER_API_KEY="t", INFER_MODEL="mock",
                       INFER_TOOL_CHOICE="auto", INFER_AUTO_APPROVE="1", INFER_ENGINE=engine,
                       INFER_ENGINE_STRICT="1", AI_STATE_DB=str(self.base / "state.db"))
            env.update(extra_env or {})
            r = subprocess.run([AI_BIN] + args, cwd=str(self.work), env=env, capture_output=True, text=True,
                               timeout=120, **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}))
            reqs = srv.requests()
        assert r.returncode == 0, (engine, r.stderr[-3000:])
        assert "using the C engine" not in r.stderr
        return r, reqs


def _both(tmp_path, scenario):
    out = {}
    root = Root(tmp_path)
    for engine in ("c", "py"):
        out[engine] = scenario(root, engine)
    (c_runs, py_runs) = out["c"], out["py"]
    assert len(c_runs) == len(py_runs)
    for (rc, qc), (rp, qp) in zip(c_runs, py_runs):
        assert qc, "C engine sent no request"
        assert len(qc) == len(qp), (len(qc), len(qp))
        for a, b in zip(qc, qp):
            assert _norm_request(a) == _norm_request(b)
        assert _strip(rc.stdout) == _strip(rp.stdout)
    return out


def _call(name, args, cid=None):
    c = {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    if cid:
        c["id"] = cid
    return c


def test_parity_plain_answer(tmp_path):
    def scenario(root, engine):
        root.reset()
        return [root.run(engine, ["what is 2+2?"],
                         {"MOCK_REPLY_CONTENT": "# Answer\n\nIt is **4**.\n\n- one\n- `two`"})]
    out = _both(tmp_path, scenario)
    assert "It is 4." in _strip(out["py"][0][0].stdout)


def test_parity_tool_chain(tmp_path):
    def setup(root):
        (root.work / "notes.txt").write_text("alpha\nbeta\n")

    def scenario(root, engine):
        root.reset(setup)
        seq = [_call("think", {"reasoning": "read then write"}),
               _call("read_file", {"path": "notes.txt"}),
               _call("list_directory", {"path": "."}),
               _call("write_file", {"path": "out.txt", "content": "ALPHA\n"}),
               _call("read_file", {"path": "out.txt"}),
               _call("task_complete", {"summary": "Copied **alpha** to out.txt"})]
        return [root.run(engine, ["copy the first line"], {"MOCK_TOOL_SEQ": json.dumps(seq)})]
    out = _both(tmp_path, scenario)
    tool_msgs = [m["content"] for m in out["py"][0][1][-1]["messages"] if m["role"] == "tool"]
    assert any("alpha" in t for t in tool_msgs) and any("ALPHA" in t for t in tool_msgs)


def test_parity_failed_command_loop(tmp_path):
    def scenario(root, engine):
        root.reset()
        seq = [_call("execute_command", {"command": "cat missing_file.txt"}),
               _call("execute_command", {"command": "ls nope_dir"}),
               _call("execute_command", {"command": "echo recovered > fixed.txt && cat fixed.txt"}),
               _call("execute_command", {"command": "cat missing_file.txt"}),
               _call("task_complete", {"summary": "recovered"})]
        return [root.run(engine, ["fix it"], {"MOCK_TOOL_SEQ": json.dumps(seq)},
                         extra_env={"INFER_SELF_IMPROVE_RECURRENCE": "1"})]
    out = _both(tmp_path, scenario)
    last = out["py"][0][1][-1]["messages"]
    body = "\n".join(m["content"] for m in last if m["role"] == "tool")
    assert "failed (exit" in body and "[HINT: File or path not found" in body and "recovered" in body


def test_parity_length_finish_recovery(tmp_path):
    def scenario(root, engine):
        root.reset()
        seq = [{"content": "I will now think for a very long ti", "finish_reason": "length"},
               {"content": "still too lo", "finish_reason": "length"},
               _call("task_complete", {"summary": "short answer"})]
        return [root.run(engine, ["explain"], {"MOCK_TOOL_SEQ": json.dumps(seq)},
                         extra_env={"INFER_NUDGE_CAP": "2"})]
    out = _both(tmp_path, scenario)
    msgs = out["py"][0][1][-1]["messages"]
    assert any("[STALL]" in (m.get("content") or "") for m in msgs if m["role"] == "user")
    assert "short answer" in out["py"][0][0].stdout


def test_parity_resume(tmp_path):
    def scenario(root, engine):
        root.reset()
        first = root.run(engine, ["Remember the codeword is PLATYPUS"],
                         {"MOCK_TASK_COMPLETE": "Understood, the codeword is PLATYPUS."})
        second = root.run(engine, ["-r", "What was the codeword?"], {"MOCK_TASK_COMPLETE": "It was PLATYPUS."})
        return [first, second]
    out = _both(tmp_path, scenario)
    roles = [m["role"] for m in out["py"][1][1][0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_parity_piped_stdin_and_image(tmp_path):
    def setup(root):
        (root.work / "pic.png").write_bytes(PNG)

    def scenario(root, engine):
        root.reset(setup)
        return [root.run(engine, ["describe", "pic.png"], {"MOCK_TASK_COMPLETE": "A **tiny** image."},
                         stdin="log line 1\nlog line 2\n")]
    out = _both(tmp_path, scenario)
    user = out["py"][0][1][0]["messages"][1]
    assert isinstance(user["content"], list)
    assert user["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "log line 2" in user["content"][0]["text"]
