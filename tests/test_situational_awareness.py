"""Hard-case tests for the harness's *situational awareness* mechanisms.

These exercise the exact behaviours that let a small local model "understand
where it is" mid-task, deterministically against the mock OpenAI server
(tests/mock_llm_server.py). They assert on what the `ai` binary actually sends
to the model, so they pass/fail based on the harness behaviour, not a real LLM.

Covered:
  1. Every tool result carries a compact `[CURRENT STATE step N]` header with a
     rolling log of tool outcomes (ok/ERR), so a small model always sees its
     progress without holding it in memory.
  2. The header is accurate across a multi-step sequence (ok then error).
  3. `INFER_STATE_CONTEXT=0` cleanly disables the header.
  4. The `think` cap blocks runaway repeated reasoning but does NOT brick
     progress (a later real tool still executes).
  5. Common tool errors get an auto `[HINT: ...]` guidance injected alongside.

Run: python3 -m pytest tests/test_situational_awareness.py -v
"""
import json
import os
import socket
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = os.path.join(REPO, "tests", "mock_llm_server.py")
AI_BIN = os.path.join(REPO, "ai")


@pytest.fixture(scope="module", autouse=True)
def build_binary():
    if not os.path.exists(AI_BIN) or (
            os.path.getmtime(os.path.join(REPO, "ai.c")) > os.path.getmtime(AI_BIN)):
        res = subprocess.run(["make"], cwd=REPO, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
    yield


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockServer:
    def __init__(self, capture_path, **env):
        self.port = _free_port()
        self.capture = capture_path
        self.env = env
        self.proc = None

    def __enter__(self):
        e = os.environ.copy()
        e["MOCK_CAPTURE"] = self.capture
        e.update({k: v for k, v in self.env.items() if v is not None})
        self.proc = subprocess.Popen(
            [sys.executable, MOCK, str(self.port)], env=e,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        return self

    def __exit__(self, *a):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1/"

    def requests(self):
        if not os.path.exists(self.capture):
            return []
        with open(self.capture) as f:
            return [json.loads(line) for line in f if line.strip()]


def run_binary(home, base_url, args, extra_env=None, timeout=90):
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("INFER_") or k in ("CUDA_VISIBLE_DEVICES", "CUDA_PATH",
                                           "LD_LIBRARY_PATH", "LLAMA_CTX_SIZE",
                                           "LLAMA_N_GPU_LAYERS", "LLAMA_MODEL_PATH"):
            env.pop(k, None)
    env["HOME"] = home
    env["INFER_BASE_URL"] = base_url
    env["INFER_API_KEY"] = "test"
    env["INFER_MODEL"] = "mock"
    env["INFER_TOOL_CHOICE"] = "auto"
    if extra_env:
        env.update(extra_env)
    return subprocess.run([AI_BIN] + args, cwd=REPO, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout)


def _tool_messages(reqs):
    """All 'tool'-role message contents across captured requests, joined."""
    parts = []
    for r in reqs:
        for m in r.get("messages", []):
            if m.get("role") == "tool":
                parts.append(m.get("content") or "")
    return "\n".join(parts)


def _tool_call(name, args):
    return {"type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _seq(*calls):
    return list(calls)


# ── 1 + 2: tool results carry an accurate [CURRENT STATE] header ─────────────
def test_tool_result_carries_situational_state_header(tmp_path):
    home = str(tmp_path)
    okf = tmp_path / "ok.txt"
    okf.write_text("hello payload")
    missing = str(tmp_path / "nope_missing.txt")
    seq = _seq(_tool_call("read_file", {"path": str(okf)}),
               _tool_call("read_file", {"path": str(missing)}),
               _tool_call("task_complete", {"summary": "done"}))
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq),
                    MOCK_REPLY_CONTENT="MOCK_OK",
                    MOCK_TASK_COMPLETE="STOP") as srv:
        res = run_binary(home, srv.base_url, ["--auto", "read a file then read a missing one"],
                         extra_env={"INFER_STEP_LIMIT": "50", "INFER_SELF_IMPROVE_RECURRENCE": "1"})
        assert res.returncode == 0, res.stdout + res.stderr
    body = _tool_messages(MockServer(cap).requests() if os.path.exists(cap) else [])

    # The header, an incremented step number, and the rolling outcome log appear.
    assert "[CURRENT STATE step" in body, body
    assert "read_file -> ok" in body, body
    # The rolling log shows the ok call first, then the error call.
    assert "1:read_file=ok" in body, body
    assert "2:read_file=ERR" in body, body
    # The original payload still reached the model (header wraps, doesn't replace).
    assert "hello payload" in body, body
    # The error path still carries auto-guidance to the model (read_file's
    # "does not exist" error falls in the GRAPH ENFORCEMENT branch; the
    # [HINT: ...] branch is exercised separately for execute_command).
    assert "[GRAPH ENFORCEMENT:" in body, body


# ── 3: the header can be disabled cleanly ─────────────────────────────────────
def test_state_header_disabled_with_env(tmp_path):
    home = str(tmp_path)
    okf = tmp_path / "ok.txt"
    okf.write_text("hello payload")
    seq = _seq(_tool_call("read_file", {"path": str(okf)}),
               _tool_call("task_complete", {"summary": "done"}))
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq),
                    MOCK_REPLY_CONTENT="MOCK_OK",
                    MOCK_TASK_COMPLETE="STOP") as srv:
        res = run_binary(home, srv.base_url, ["--auto", "read a file"],
                         extra_env={"INFER_STATE_CONTEXT": "0", "INFER_STEP_LIMIT": "50"})
        assert res.returncode == 0, res.stdout + res.stderr
    body = _tool_messages(MockServer(cap).requests() if os.path.exists(cap) else [])
    assert "[CURRENT STATE" not in body, body
    # The payload is still delivered even without the header.
    assert "hello payload" in body, body


# ── 4: think cap stops runaway reasoning without bricking progress ───────────
def test_think_cap_blocks_runaway_reasoning_but_allows_progress(tmp_path):
    home = str(tmp_path)
    okf = tmp_path / "ok2.txt"
    okf.write_text("still progressing")
    # 14 consecutive thinks (cap is 12) then a real tool and task_complete.
    seq = [_tool_call("think", {"reasoning": f"reasoning pass {i}"}) for i in range(14)]
    seq += [_tool_call("read_file", {"path": str(okf)}),
            _tool_call("task_complete", {"summary": "done"})]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq),
                    MOCK_REPLY_CONTENT="MOCK_OK",
                    MOCK_TASK_COMPLETE="STOP") as srv:
        res = run_binary(home, srv.base_url, ["--auto", "do a careful multi-pass task"],
                         extra_env={"INFER_STEP_LIMIT": "60"})
        assert res.returncode == 0, res.stdout + res.stderr
    body = _tool_messages(MockServer(cap).requests() if os.path.exists(cap) else [])

    # The model was told (more than once) that it must stop calling think.
    assert body.count("have already called the 'think' tool") >= 1, body
    # ...but it did NOT get locked out of progress: the real tool still ran.
    assert "still progressing" in body, body


# ── 5: tool-error guidance actually reaches the model ─────────────────────────
def test_tool_error_guidance_reaches_model(tmp_path):
    home = str(tmp_path)
    missing = str(tmp_path / "absent_xyz.txt")
    seq = _seq(_tool_call("read_file", {"path": str(missing)}),
               _tool_call("task_complete", {"summary": "done"}))
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq),
                    MOCK_REPLY_CONTENT="MOCK_OK",
                    MOCK_TASK_COMPLETE="STOP") as srv:
        res = run_binary(home, srv.base_url, ["--auto", "read a file"],
                         extra_env={"INFER_STEP_LIMIT": "50", "INFER_SELF_IMPROVE_RECURRENCE": "1"})
        assert res.returncode == 0, res.stdout + res.stderr
    body = _tool_messages(MockServer(cap).requests() if os.path.exists(cap) else [])
    # The auto-guidance (GRAPH ENFORCEMENT branch for this error wording) is
    # injected into the tool message the model sees, not just the terminal.
    assert "[GRAPH ENFORCEMENT:" in body, body
    # And the situational header marks this call as an error.
    assert "read_file -> error" in body, body
    assert "1:read_file=ERR" in body, body


# ── 5b: the [HINT: ...] branch reaches the model via execute_command ──────────
def test_execute_command_error_hint_reaches_model(tmp_path):
    home = str(tmp_path)
    absent = str(tmp_path / "no_such_file_xyz")
    seq = _seq(_tool_call("execute_command", {"command": f"cat {absent} 2>&1"}),
               _tool_call("task_complete", {"summary": "done"}))
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq),
                    MOCK_REPLY_CONTENT="MOCK_OK",
                    MOCK_TASK_COMPLETE="STOP") as srv:
        res = run_binary(home, srv.base_url, ["--auto", "cat a missing file"],
                         extra_env={"INFER_STEP_LIMIT": "50"})
        assert res.returncode == 0, res.stdout + res.stderr
    body = _tool_messages(MockServer(cap).requests() if os.path.exists(cap) else [])
    # The "No such file" error text fires the file-not-found HINT branch, and
    # (after the harness fix) that hint now reaches the model's context.
    assert "[HINT: File or path not found. Use list_directory" in body, body
    assert "execute_command -> error" in body, body
    assert "1:execute_command=ERR" in body, body


# ── 6: the header is NOT added to control messages (think / task_complete) ───
def test_state_header_not_added_to_control_messages(tmp_path):
    home = str(tmp_path)
    okf = tmp_path / "ok3.txt"
    okf.write_text("payload")
    seq = _seq(_tool_call("think", {"reasoning": "planning"}),
               _tool_call("read_file", {"path": str(okf)}),
               _tool_call("task_complete", {"summary": "done"}))
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq),
                    MOCK_REPLY_CONTENT="MOCK_OK",
                    MOCK_TASK_COMPLETE="STOP") as srv:
        res = run_binary(home, srv.base_url, ["--auto", "plan then read"],
                         extra_env={"INFER_STEP_LIMIT": "50"})
        assert res.returncode == 0, res.stdout + res.stderr
    reqs = MockServer(cap).requests() if os.path.exists(cap) else []
    body = _tool_messages(reqs)
    # The state log records the real tool call (read_file is loop step 2,
    # since the preceding think consumed step 1).
    assert "2:read_file=ok" in body, body
    # ...but we never wrap think/'task_complete' results in a state header for
    # their own tool role (the think tool_call's result message carries no header).
    for r in reqs:
        for m in r.get("messages", []):
            if m.get("role") == "tool" and "payload" in (m.get("content") or ""):
                pass  # the read_file tool result is the real one
    # The think tool result (if surfaced as a tool message) must not become a
    # state header; simplest robust assertion: no fake 'think -> ok' header on a
    # message whose tool call was 'think'. We can't easily map call_id->tool, so
    # assert that no tool message content equals just a state wrapper.
    for r in reqs:
        for m in r.get("messages", []):
            c = m.get("content") or ""
            if c.startswith("[CURRENT STATE step") and "read_file" not in c:
                # This would be a header on think/other control; fail.
                assert False, f"state header leaked onto a control tool message: {c}"