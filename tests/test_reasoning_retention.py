"""INFER_KEEP_REASONING: only the last N assistant messages of the current turn keep their
reasoning (reasoning_content and inline <think>...</think>) in the request; the history and the
saved session keep all of it. Both engines must send the same requests (parity), `all` must
reproduce the pre-feature requests (golden), the request prefix must stay stable for the
server's prompt cache, and trimming must drop old reasoning before it touches tool output.
"""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))
from test_engine_parity import Root, _both, _call, _norm_request  # noqa: E402

from aitools import sessions as S  # noqa: E402

GOLDEN = os.path.join(REPO, "tests", "golden", "reasoning_chain_all.json")


def _reason(k, words=40):
    return "REASON-%d: " % k + " ".join("w%d" % (i % 97) for i in range(words))


def _chain(words=40, inline_step=3):
    """8 assistant steps; every step carries reasoning, step `inline_step` also puts thinking
    inline in its content (models whose template leaves <think> in the text)."""
    calls = [_call("read_file", {"path": "notes.txt"}),
             _call("write_file", {"path": "out1.txt", "content": "ONE\n"}),
             _call("read_file", {"path": "out1.txt"}),
             _call("execute_command", {"command": "echo step4"}),
             _call("write_file", {"path": "out2.txt", "content": "TWO\n"}),
             _call("read_file", {"path": "out2.txt"}),
             _call("execute_command", {"command": "echo step7"}),
             _call("task_complete", {"summary": "chain done"})]
    seq = []
    for k, c in enumerate(calls, 1):
        item = {"calls": [c], "reasoning": _reason(k, words)}
        if k == inline_step:
            item["content"] = "<think>INLINE-%d secret plan</think>visible-%d" % (k, k)
        seq.append(item)
    return seq


def _setup(root):
    (root.work / "notes.txt").write_text("alpha\nbeta\n")


def _run_chain(root, engine, extra_env=None, words=40):
    root.reset(_setup)
    return root.run(engine, ["do the chain"], {"MOCK_TOOL_SEQ": json.dumps(_chain(words))},
                    extra_env=extra_env)


def _assistants(msgs):
    return [m for m in msgs if m.get("role") == "assistant"]


def _has_reasoning(m):
    c = m.get("content") if isinstance(m.get("content"), str) else ""
    return bool(m.get("reasoning_content")) or "<think>" in c or "</think>" in c


def _assert_pairs(msgs):
    """Every assistant tool_call is answered by a tool message right after it, and every tool
    message answers a call of the assistant message before it."""
    pending = set()
    for m in msgs:
        if m["role"] == "assistant":
            assert not pending, "tool_call without a tool reply: %s" % pending
            pending = {c["id"] for c in m.get("tool_calls") or []}
        elif m["role"] == "tool":
            assert m["tool_call_id"] in pending, "orphan tool message"
            pending.discard(m["tool_call_id"])
        else:
            pending = set() if m["role"] != "user" else pending


# ── end to end, both engines ─────────────────────────────────────────────────
def test_default_keeps_reasoning_of_last_two_assistant_messages(tmp_path, engine):
    root = Root(tmp_path)
    r, reqs = _run_chain(root, engine)
    assert len(reqs) == 8
    for k, req in enumerate(reqs):
        asst = _assistants(req["messages"])
        assert len(asst) == k
        with_r = [i for i, m in enumerate(asst) if _has_reasoning(m)]
        assert with_r == list(range(max(0, k - 2), k)), (k, with_r)
        for m in asst[:max(0, k - 2)]:
            assert "REASON-" not in json.dumps(m) and "INLINE-" not in json.dumps(m)
        for i, m in enumerate(asst[max(0, k - 2):], max(0, k - 2) + 1):
            assert ("REASON-%d:" % i) in (m.get("reasoning_content") or "")
        _assert_pairs(req["messages"])
    # inline thinking is stripped from the text, the visible part stays
    step3 = _assistants(reqs[-1]["messages"])[2]
    assert step3["content"] == "visible-3"


def test_request_prefix_is_stable_between_requests(tmp_path, engine):
    """Once stripped, a message stays stripped: every message of request j that is not one of
    its last two assistant messages is byte-identical (as serialised) in request j+1."""
    root = Root(tmp_path)
    r, reqs = _run_chain(root, engine)
    for a, b in zip(reqs, reqs[1:]):
        ma, mb = a["messages"], b["messages"]
        asst_idx = [i for i, m in enumerate(ma) if m["role"] == "assistant"]
        stable_upto = asst_idx[-2] if len(asst_idx) >= 2 else len(ma)
        for i in range(stable_upto):
            assert json.dumps(ma[i]) == json.dumps(mb[i]), i


def test_keep_zero_and_n(tmp_path, engine):
    root = Root(tmp_path)
    r, reqs = _run_chain(root, engine, extra_env={"INFER_KEEP_REASONING": "0"})
    assert not any(_has_reasoning(m) for q in reqs for m in _assistants(q["messages"]))
    r, reqs = _run_chain(root, engine, extra_env={"INFER_KEEP_REASONING": "3"})
    assert [sum(map(_has_reasoning, _assistants(q["messages"]))) for q in reqs] == [0, 1, 2, 3, 3, 3, 3, 3]


def test_preserve_thinking_flag_keeps_all_unless_explicit(tmp_path, engine):
    root = Root(tmp_path)
    r, reqs = _run_chain(root, engine, extra_env={"INFER_PRESERVE_THINKING": "1"})
    assert sum(map(_has_reasoning, _assistants(reqs[-1]["messages"]))) == 7
    r, reqs = _run_chain(root, engine, extra_env={"INFER_PRESERVE_THINKING": "1", "INFER_KEEP_REASONING": "1"})
    assert sum(map(_has_reasoning, _assistants(reqs[-1]["messages"]))) == 1


def _golden_view(reqs, root):
    out = []
    for q in reqs:
        q = _norm_request(q)
        msgs = json.loads(json.dumps(q["messages"][1:]).replace(str(root.base), "<ROOT>"))
        out.append(msgs)
    return out


def test_keep_all_reproduces_pre_feature_requests(tmp_path, engine):
    """Golden recorded from the engines before INFER_KEEP_REASONING existed (system prompt and
    tool catalog excluded: they vary by machine and are not touched by this feature)."""
    root = Root(tmp_path)
    r, reqs = _run_chain(root, engine, extra_env={"INFER_KEEP_REASONING": "all"})
    got = _golden_view(reqs, root)
    if os.environ.get("REGEN_GOLDEN"):
        with open(GOLDEN, "w") as f:
            json.dump(got, f, indent=1)
    with open(GOLDEN) as f:
        want = json.load(f)
    assert json.dumps(got) == json.dumps(want)


def test_session_file_keeps_all_reasoning_and_resume_unchanged(tmp_path, engine):
    root = Root(tmp_path)
    _run_chain(root, engine)
    with open(root.home / ".cache" / "ai" / "sessions" / "last.json") as f:
        saved = json.load(f)
    blob = json.dumps(saved)
    for k in range(1, 9):
        assert "REASON-%d:" % k in blob, k
    assert "INLINE-3" in blob
    r2, reqs2 = root.run(engine, ["-r", "and now?"], {"MOCK_TASK_COMPLETE": "ok"})
    roles = [m["role"] for m in reqs2[0]["messages"]]
    assert roles[0] == "system" and roles[-1] == "user" and "tool" not in roles
    assert "REASON-" not in json.dumps(reqs2[0]["messages"][1:])


def test_trim_trigger_measures_the_request_not_the_history(tmp_path, engine):
    """Old reasoning that is never sent must not trigger trimming: a trim threshold the full
    history exceeds but the request does not leaves every tool output untouched."""
    root = Root(tmp_path)
    r, reqs = _run_chain(root, engine, extra_env={"INFER_TRIM_THRESHOLD": "60000",
                                                  "INFER_STUB_THRESHOLD": "70000"}, words=3000)
    last = reqs[-1]["messages"]
    assert len(json.dumps(reqs[-1]["messages"])) < 60000
    body = json.dumps(last)
    assert "trimmed" not in body and "context limit reached" not in body
    assert "CONTEXT NOTE" not in last[0]["content"]


def test_engine_parity_with_reasoning(tmp_path):
    def scenario(root, engine):
        return [_run_chain(root, engine)]
    out = _both(tmp_path, scenario)
    assert len(out["py"][0][1]) == 8


def test_request_size_reduction_3000_token_reasoning(tmp_path, engine):
    """The measurement quoted in the task report: 8-step chain, ~3000-token reasoning per step."""
    root = Root(tmp_path)
    big = {"INFER_TRIM_THRESHOLD": "10000000", "INFER_STUB_THRESHOLD": "10000000"}   # no trimming either way
    _, full = _run_chain(root, engine, extra_env=dict(big, INFER_KEEP_REASONING="all"), words=3000)
    _, kept = _run_chain(root, engine, extra_env=big, words=3000)
    a, b = len(json.dumps(full[-1]["messages"])), len(json.dumps(kept[-1]["messages"]))
    sys.stderr.write("[measure %s] last request messages: all=%d keep2=%d (-%.0f%%)\n"
                     % (engine, a, b, 100.0 * (a - b) / a))
    assert b < a * 0.45


# ── unit: shared Python helpers ──────────────────────────────────────────────
def _hist(n_steps=5, rlen=50, tool_len=50, user2=False):
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for k in range(1, n_steps + 1):
        if user2 and k == n_steps - 1:
            m.append({"role": "user", "content": "follow-up"})
        m.append({"role": "assistant", "content": None, "reasoning_content": "R%d " % k + "x" * rlen,
                  "tool_calls": [{"id": "c%d" % k, "type": "function",
                                  "function": {"name": "read_file", "arguments": "{}"}}]})
        m.append({"role": "tool", "tool_call_id": "c%d" % k, "content": "T%d " % k + "y" * tool_len})
    return m


def test_parse_keep_reasoning():
    assert S.parse_keep_reasoning(None) is None
    assert S.parse_keep_reasoning("") is None
    assert S.parse_keep_reasoning("all") == -1
    assert S.parse_keep_reasoning("ALL") == -1
    assert S.parse_keep_reasoning("-1") == -1
    assert S.parse_keep_reasoning("0") == 0
    assert S.parse_keep_reasoning(" 3 ") == 3
    assert S.parse_keep_reasoning("lots") is None


def test_strip_think_variants():
    assert S.strip_think("<think>a</think>b") == "b"
    assert S.strip_think("<think>a</think>\n\nb <think>c</think>d") == "b d"
    assert S.strip_think("half thought</think>answer") == "answer"
    assert S.strip_think("no tags") == "no tags"
    assert S.strip_think("<think>never closed") == "<think>never closed"
    assert S.strip_think(None) is None


def test_request_view_keep_all_is_identity_and_history_untouched():
    h = _hist()
    before = json.dumps(h)
    assert S.request_view(h, -1) is h
    v = S.request_view(h, 2)
    assert json.dumps(h) == before                      # history keeps every reasoning
    kept = [m for m in v if m.get("reasoning_content")]
    assert [m["reasoning_content"][:2] for m in kept] == ["R4", "R5"]
    assert v[2] is not h[2] and v[3] is h[3]            # untouched messages are shared, not copied


def test_request_view_current_turn_only():
    h = _hist(n_steps=5, user2=True)                    # real user turn before step 4
    v = S.request_view(h, 3)
    assert [m["reasoning_content"][:2] for m in v if m.get("reasoning_content")] == ["R4", "R5"]
    h.insert(len(h) - 2, {"role": "user", "content": "Your previous reply got stuck repeating"})
    v = S.request_view(h, 3)                            # an internal nudge is not a turn boundary
    assert [m["reasoning_content"][:2] for m in v if m.get("reasoning_content")] == ["R4", "R5"]


def test_trim_strips_old_reasoning_before_tool_output():
    h = _hist(n_steps=8, rlen=4000, tool_len=1500)
    full = sum(S._trim_msg_size(m) for m in h)
    # budget reachable by stripping old reasoning alone: no tool output is shortened
    out = S.trim_messages(h, full - 6 * 4000 + 500, keep_reasoning=2)
    assert not any("trimmed" in (m.get("content") or "") for m in out if m["role"] == "tool")
    assert [m["reasoning_content"][:2] for m in out if m.get("reasoning_content")] == ["R7", "R8"]
    assert len(out) == len(h)
    _assert_pairs(out[1:])
    # a tighter budget: reasoning is gone first, then old tool outputs are shortened
    out = S.trim_messages(h, full - 6 * 4000 - 1500, keep_reasoning=2)
    assert sum(1 for m in out if m.get("reasoning_content")) == 2
    assert any("older tool output trimmed" in (m.get("content") or "") for m in out)
    assert len(out) == len(h)


def test_trim_keep_all_is_legacy():
    h = _hist(n_steps=8, rlen=4000, tool_len=1500)
    out = S.trim_messages(h, 40000, keep_reasoning=-1)
    assert sum(1 for m in out if m.get("reasoning_content")) > 2   # reasoning never stripped


def test_trim_messages_cli_takes_keep_argument(tmp_path):
    h = _hist(n_steps=8, rlen=4000, tool_len=100)
    p = tmp_path / "m.json"
    p.write_text(json.dumps(h))
    env = {k: v for k, v in os.environ.items() if k != "INFER_KEEP_REASONING"}
    def run(arg):
        r = subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py"), "trim-messages", str(p), "20000", arg],
                           capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60)
        return json.loads(r.stdout)
    out = run("1")                       # old reasoning stripped: everything else fits
    assert sum(1 for m in out if m.get("reasoning_content")) == 1 and len(out) == len(h)
    out = run("all")                     # legacy: reasoning kept, whole units dropped instead
    asst = [m for m in out if m["role"] == "assistant"]
    assert len(out) < len(h) and all(m.get("reasoning_content") for m in asst)


def test_c_mirrors_the_turn_boundary_prefixes():
    """ai.c keeps its own copy of the internal user-message prefixes (no Python call per request)."""
    with open(os.path.join(REPO, "ai.c"), encoding="utf-8", errors="replace") as f:
        src = f.read()
    for p in S.RETENTION_INTERNAL_PREFIXES:
        assert json.dumps(p) in src, p
