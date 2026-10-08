"""Runaway-generation guard (agent/llm.py RunawayGuard) and the per-step token cap.

A local reasoning model can fall into a repetition loop and generate until the request times out
(observed: ~9,600 tokens over 300 s on one final request). The Python engine aborts such a stream
(closing the HTTP connection, which makes llama-server cancel the generation), drops the partial
reply, nudges and retries once with presence_penalty >= 1.5 and reasoning effort low; a second
abort ends the step with an error the model sees. Long but legitimate outputs (a generated
300-line file in write_file arguments, long tables, JSON arrays, varying log lines) must never
trip it. Both engines send the step cap (INFER_STEP_MAX_TOKENS, default 16384) as max_tokens.
Offline: tests/mock_llm_server.py streams the scripted loops (MOCK_RUNAWAY_SEQ).
"""
import json
import os
import random
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from agent import cli, llm  # noqa: E402
from test_offline import MockServer, _sandbox_run, build_binary  # noqa: E402,F401

LOOP_UNIT = ("Wait, I should double-check the inventory file before calling the tool. "
             "Let me re-read the instructions once more.\n")      # ~120 chars, exact repeat


def feed(guard, text, channel="content", step=4):
    """Stream `text` through the guard in `step`-char events; (trip, chars consumed)."""
    for i in range(0, len(text), step):
        t = guard.observe(channel, text[i:i + step])
        if t:
            return t, i + step
    return None, len(text)


def prose(n_chars, seed=1):
    rnd = random.Random(seed)
    words = ("the model reads file then writes summary checks output counts lines because each step "
             "needs evidence from tool results before answering user question about project todo").split()
    out = []
    while sum(len(w) + 1 for w in out) < n_chars:
        out.append(rnd.choice(words))
        if rnd.random() < 0.08:
            out[-1] += ".\n"
    return " ".join(out)[:n_chars]


# ── realistic long outputs that must NOT trip the guard ─────────────────────
def python_file_300():
    lines = ['"""Generated record parsers and their tests."""', "import dataclasses", "import json", ""]
    i = 0
    while len(lines) < 300:
        lines += ["", "",
                  "@dataclasses.dataclass",
                  "class Record%d:" % i,
                  "    id: int = %d" % i,
                  "    name: str = \"item_%d\"" % i,
                  "    tags: list = dataclasses.field(default_factory=list)",
                  "",
                  "    def to_json(self):",
                  "        return json.dumps(dataclasses.asdict(self))",
                  "",
                  "",
                  "def test_record%d_roundtrip():" % i,
                  "    r = Record%d()" % i,
                  "    assert json.loads(r.to_json())[\"id\"] == %d" % i,
                  "    assert r.name.endswith(\"_%d\")" % i]
        i += 1
    lines += ["", "ZERO = [", ] + ["    [0, 0, 0, 0, 0, 0, 0, 0],"] * 8 + ["]", ""]
    return "\n".join(lines) + "\n"


def markdown_table(rows=200):
    out = ["| # | file | lines | status | notes |", "|---|------|------:|--------|-------|"]
    for i in range(rows):
        out.append("| %d | src/module_%03d.py | %d | %s | %s |" % (
            i, i, 40 + (i * 37) % 500, "ok" if i % 7 else "TODO", "-" if i % 3 else "needs review"))
    return "\n".join(out) + "\n"


def json_array(n=400):
    return json.dumps([{"id": i, "path": "data/file_%04d.txt" % i, "size": (i * 7919) % 100000,
                        "todo": i % 5 == 0} for i in range(n)], indent=2)


def log_lines(n=500):
    return "".join("2026-10-07 12:%02d:%02d INFO worker-%d processed batch %d (%d items) in %d ms\n"
                   % (i // 60 % 60, i % 60, i % 4, i, 50 + i % 13, 3 + i % 17) for i in range(n))


LEGIT = {
    "python_file": python_file_300,
    "markdown_table": markdown_table,
    "json_array": json_array,
    "log_lines": log_lines,
    "banners": lambda: "".join("=" * 120 + "\n" + prose(600, seed=k) + "\n" + "-" * 300 + "\n" for k in range(6)),
    # review round 1: data-like and templated repeats that a real answer contains
    "csv_zero_rows": lambda: "date,open,close\n" + "2026-10-01,0.00,0.00\n" * 40,
    "tbd_table": lambda: "| item | owner | state |\n|---|---|---|\n" + "| TBD | TBD | pending |\n" * 30,
    "todo_list": lambda: "## Checklist\n" + "- [ ] TODO: fill in later\n" * 12,
    "same_asserts": lambda: "Add these checks:\n\n" + "    assert result == expected_value\n" * 10,
    "zero_matrix": lambda: "M = [\n" + "    [0, 0, 0, 0, 0, 0, 0, 0],\n" * 24 + "]\n",
    "templated_paragraph": lambda: ("Section notes: this part of the report is intentionally left as a placeholder "
                                    "until the measurements are in; see the appendix for the method.\n\n") * 4,
    "html_td_rows": lambda: "```html\n<table>\n" + "  <tr><td>0</td><td>pending</td></tr>\n" * 300 + "</table>\n```\n",
}


@pytest.mark.parametrize("name", sorted(LEGIT))
@pytest.mark.parametrize("channel", ["content", "reasoning"])
def test_long_legit_output_does_not_trip(name, channel):
    text = LEGIT[name]()
    g = llm.RunawayGuard(max_tokens=0)
    body = "```\n%s```\n" % text if name == "python_file" else text
    trip, _ = feed(g, prose(1500) + "\n" + body + "\n" + prose(800), channel)
    assert trip is None, (name, trip)


def test_generated_file_in_write_file_arguments_does_not_trip():
    code = python_file_300()
    assert code.count("\n") >= 300
    args = json.dumps({"path": "gen/records.py", "content": code})
    g = llm.RunawayGuard(max_tokens=0)
    trip, _ = feed(g, args, "args0", step=3)
    assert trip is None, trip


ZERO_CSV = "0,0,0,0\n" * 800                       # 6.4 KB
LEVEL_MAP = "".join("#" + "." * 60 + "#\n" for _ in range(80))
B64_ZEROS = "A" * 8000


@pytest.mark.parametrize("content", [ZERO_CSV, LEVEL_MAP, B64_ZEROS, "all work and no play. " * 400],
                         ids=["zero_csv", "level_map", "base64_zeros", "looping_text"])
def test_tool_arguments_are_exempt_from_the_repetition_detector(content):
    """Only the step cap applies to tool arguments: data files are legitimately repetitive."""
    assert len(json.dumps(content)) >= 5000
    g = llm.RunawayGuard(max_tokens=0)
    trip, _ = feed(g, json.dumps({"path": "data.csv", "content": content}), "args0", step=3)
    assert trip is None, trip
    g = llm.RunawayGuard(max_tokens=500)                     # the cap still applies
    trip, _ = feed(g, json.dumps({"path": "data.csv", "content": content}), "args0", step=4)
    assert trip and trip["kind"] == "step_cap"


TOK_PER_S = 50     # a slow local decode rate: "within seconds" is checked against it


def test_reasoning_loop_trips_within_the_loop_window():
    g = llm.RunawayGuard(max_tokens=0)
    head = prose(2000)
    trip, used = feed(g, head + LOOP_UNIT * 200, "reasoning")
    assert trip and trip["kind"] == "repetition" and trip["channel"] == "reasoning"
    # caught after ~8 repeats of the loop (+ the check interval), not after the 9,600-token timeout
    loop_chars = used - len(head)
    assert loop_chars <= 9 * len(LOOP_UNIT) + 256, loop_chars
    assert loop_chars / 4 / TOK_PER_S < 10                 # ~5 s of decoding at 50 tok/s


def test_counter_loop_trips_on_digit_normalised_text():
    """A loop whose only change is a counter ("item 17", "item 18", ...) is caught too."""
    g = llm.RunawayGuard(max_tokens=0)
    head = prose(1000)
    loop = "".join("Let me check item %d again before I continue with the next step of the plan.\n" % (100 + i)
                   for i in range(200))
    trip, used = feed(g, head + "\n" + loop, "reasoning")
    assert trip and trip["kind"] == "repetition", trip
    assert (used - len(head)) / 4 / TOK_PER_S < 10


def test_short_identical_lines_trip():
    g = llm.RunawayGuard(max_tokens=0)
    trip, used = feed(g, prose(500) + "\n" + "I will call task_complete now.\n" * 50, "content")
    assert trip and trip["kind"] == "repetition"
    assert used <= 500 + 1 + 17 * 31, used


def test_reasoning_cap_from_the_reasoning_budget():
    g = llm.RunawayGuard(max_tokens=0, reasoning_max_tokens=100, repetition=False)
    assert feed(g, prose(5000), "content")[0] is None        # content does not count
    trip, used = feed(g, prose(5000), "reasoning")
    assert trip and trip["kind"] == "reasoning_cap" and used == 101 * 4


def test_step_cap_trips_on_varying_text():
    g = llm.RunawayGuard(max_tokens=100, repetition=False)
    trip, used = feed(g, prose(5000), "reasoning", step=4)
    assert trip and trip["kind"] == "step_cap"
    assert used == 101 * 4


def test_step_cap_counts_chars_when_events_are_large():
    g = llm.RunawayGuard(max_tokens=100, repetition=False)
    trip, used = feed(g, prose(5000), "content", step=200)      # ~50 tokens per event
    assert trip and trip["kind"] == "step_cap" and used <= 600


def test_repetition_guard_can_be_disabled():
    g = llm.RunawayGuard(max_tokens=0, repetition=False)
    assert feed(g, LOOP_UNIT * 100, "reasoning")[0] is None


# ── options: the step cap and penalties ─────────────────────────────────────
BASE = {"INFER_BASE_URL": "u", "INFER_API_KEY": "k", "INFER_MODEL": "m"}


def _payload(env, args=("x",)):
    cfg = cli.parse(list(args), {**BASE, **env})
    return cfg, llm.build_payload("m", [], None, cfg.opts)


def test_step_cap_default_and_explicit_values():
    cfg, p = _payload({})
    assert cfg.opts.step_max_tokens == 32768 and p["max_tokens"] == 32768      # ruling H-1
    _, p = _payload({"INFER_STEP_MAX_TOKENS": "2048"})
    assert p["max_tokens"] == 2048
    # an explicit INFER_MAX_TOKENS wins over the new default cap (explicit settings win)
    cfg, p = _payload({"INFER_MAX_TOKENS": "65536"})
    assert p["max_tokens"] == 65536 and cfg.opts.step_max_tokens == 65536
    _, p = _payload({"INFER_MAX_TOKENS": "32768", "INFER_STEP_MAX_TOKENS": "8000"})
    assert p["max_tokens"] == 8000
    _, p = _payload({"INFER_MAX_TOKENS": "4096"})
    assert p["max_tokens"] == 4096


def test_server_profile_presence_penalty_is_sent():
    _, p = _payload({"LLAMA_PRESENCE_PENALTY": "1.5"})
    assert p["presence_penalty"] == 1.5
    _, p = _payload({"LLAMA_PRESENCE_PENALTY": "1.5", "INFER_PRESENCE_PENALTY": "0.3"})
    assert p["presence_penalty"] == 0.3                       # explicit client value wins
    _, p = _payload({"LLAMA_PRESENCE_PENALTY": "0.0"})
    assert p["presence_penalty"] == 0.05                      # neutral server value: client default
    _, p = _payload({"LLAMA_FREQUENCY_PENALTY": "0.4"})
    assert p["frequency_penalty"] == 0.4


# ── through the binary (Python engine): abort, nudge, retry ─────────────────
def _run(tmp_path, runaway, extra=None, srv_extra=None, engine="py"):
    cap = str(tmp_path / "cap.jsonl")
    disc = str(tmp_path / "disc.jsonl")
    srv_env = {"MOCK_RUNAWAY_SEQ": json.dumps(runaway), "MOCK_DISCONNECTS": disc, "MOCK_TASK_COMPLETE": "done ok",
               **(srv_extra or {})}
    env = {"INFER_ENGINE": engine, "INFER_ENGINE_STRICT": "1", "LLAMA_PRESENCE_PENALTY": "",
           "LLAMA_FREQUENCY_PENALTY": "", **(extra or {})}
    with MockServer(cap, **srv_env) as srv:
        t0 = time.monotonic()
        r = _sandbox_run(tmp_path, srv.base_url, ["count the todos"], env, timeout=120)
        elapsed = time.monotonic() - t0
        time.sleep(0.3)
        reqs = srv.requests()
    discs = [json.loads(x) for x in open(disc)] if os.path.exists(disc) else []
    return r, reqs, discs, elapsed


LOOP = {"channel": "reasoning", "prefix": "Let me look at the results.\n", "unit": LOOP_UNIT, "count": 2000,
        "delay": 0.0005}


def test_repetition_loop_aborted_nudged_and_retried(tmp_path):
    r, reqs, discs, elapsed = _run(tmp_path, [LOOP])
    assert r.returncode == 0, r.stderr[-3000:]
    assert len(reqs) == 2, [q["messages"][-1] for q in reqs]
    # the stream was cut early and the server saw the disconnect (llama-server cancels on it)
    assert discs and discs[0]["closed"] is True, discs
    assert discs[0]["sent_chars"] < 20 * len(LOOP_UNIT) + 65536, discs
    assert elapsed < 60
    retry = reqs[1]
    last = retry["messages"][-1]
    assert last["role"] == "user" and "stuck repeating itself" in last["content"]
    assert not any(LOOP_UNIT[:40] in json.dumps(m) for m in retry["messages"]), "partial reply kept"
    assert retry["presence_penalty"] >= 1.5 and retry["reasoning_effort"] == "low"
    assert reqs[0].get("presence_penalty", 0) < 1.5
    assert "repetition" in r.stderr and "retrying" in r.stderr
    # the event is logged to metrics
    metrics = tmp_path / "home" / ".cache" / "ai" / "metrics.jsonl"
    rows = [json.loads(x) for x in metrics.read_text().splitlines()] if metrics.exists() else []
    assert any(row.get("tool") == "generation_guard" for row in rows), rows


def test_second_abort_ends_the_step_with_an_error_the_model_sees(tmp_path):
    r, reqs, discs, elapsed = _run(tmp_path, [LOOP, LOOP])
    assert r.returncode == 0, r.stderr[-3000:]
    assert len(reqs) == 3
    assert len(discs) == 2 and all(d["closed"] for d in discs)
    last = reqs[2]["messages"][-1]
    assert last["role"] == "user" and "aborted" in last["content"].lower()
    # the third (normal) request goes back to the configured sampling
    assert reqs[2].get("presence_penalty", 0) < 1.5
    assert elapsed < 90


def test_step_cap_aborts_a_server_that_ignores_max_tokens(tmp_path):
    varying = {"channel": "content", "prefix": prose(60000), "unit": "", "count": 0, "delay": 0}
    r, reqs, discs, _ = _run(tmp_path, [varying], {"INFER_STEP_MAX_TOKENS": "600"})
    assert r.returncode == 0, r.stderr[-3000:]
    assert reqs[0]["max_tokens"] == 600
    assert len(reqs) == 2 and discs[0]["closed"]
    assert discs[0]["sent_chars"] < 600 * 4 + 65536 * 2
    assert "token cap" in r.stderr
    nudge = reqs[1]["messages"][-1]["content"]
    assert "token limit" in nudge and "repeating" not in nudge         # its own nudge


def test_legit_long_stream_is_not_aborted(tmp_path):
    table = {"channel": "content", "prefix": markdown_table(150), "unit": "", "count": 0}
    r, reqs, discs, _ = _run(tmp_path, [table])
    # finished stream (finish_reason length) -> no guard abort, no disconnect
    assert discs and discs[0]["closed"] is False, discs
    assert "repetition" not in r.stderr
    assert not any("stuck repeating" in json.dumps(q["messages"]) for q in reqs)


@pytest.mark.parametrize("eng", ["c", "py"])
def test_both_engines_send_the_step_cap(tmp_path, eng):
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TASK_COMPLETE="ok") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["hi"], {"INFER_ENGINE": eng, "INFER_ENGINE_STRICT": "1"})
        r2 = _sandbox_run(tmp_path, srv.base_url, ["hi"], {"INFER_ENGINE": eng, "INFER_ENGINE_STRICT": "1",
                                                           "INFER_STEP_MAX_TOKENS": "3000"})
        reqs = srv.requests()
    assert r.returncode == 0 and r2.returncode == 0, r.stderr[-2000:] + r2.stderr[-2000:]
    assert reqs[0]["max_tokens"] == 32768
    assert reqs[-1]["max_tokens"] == 3000


def test_guard_nudges_are_internal_for_resume_and_trim():
    from agent import loop
    from aitools import sessions
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "count the todos"},
            {"role": "user", "content": loop.RUNAWAY_NUDGE},
            {"role": "user", "content": loop.RUNAWAY_FAILED % "repetition in reasoning"},
            {"role": "assistant", "content": "3 todos"}]
    assert [m["content"] for m in sessions.session_transcript(msgs)] == ["count the todos", "3 todos"]
    for text in (loop.RUNAWAY_NUDGE, loop.RUNAWAY_CAP_NUDGE, loop.RUNAWAY_FAILED):
        assert text.startswith(sessions._TRIM_INTERNAL_USER_PREFIXES)


# ── finish_reason "length": a cut-off tool call is never executed (both engines) ──
def test_truncated_tool_call_is_not_executed(tmp_path, engine):
    work = tmp_path / "work"
    work.mkdir()
    (work / "big.txt").write_text("ORIGINAL CONTENT\n")
    ok_call = {"id": "c1", "type": "function",
               "function": {"name": "write_file", "arguments": json.dumps({"path": "small.txt", "content": "fine\n"})}}
    broken = {"id": "c2", "type": "function",          # invalid JSON that repair_json would "fix"
              "function": {"name": "write_file", "arguments": '{"path": "other.txt", "content": "x\n'}}
    cut = {"id": "c3", "type": "function",
           "function": {"name": "write_file", "arguments": '{"path": "big.txt", "content": "line 1\\nline 2\\nli'}}
    seq = [{"calls": [ok_call, broken, cut], "finish_reason": "length"}]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq)) as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["write the files"])
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-3000:]
    assert (work / "big.txt").read_text() == "ORIGINAL CONTENT\n"      # untouched, not truncated
    assert not (work / "other.txt").exists()
    assert (work / "small.txt").read_text() == "fine\n"                # complete earlier call ran
    tool_msgs = {m["tool_call_id"]: m["content"] for m in reqs[1]["messages"] if m.get("role") == "tool"}
    assert set(tool_msgs) == {"c1", "c2", "c3"}                         # every call answered
    for cid in ("c2", "c3"):
        assert "cut off at the token limit and was NOT executed" in tool_msgs[cid], tool_msgs[cid]
    assert "NOT executed" not in tool_msgs["c1"]
    assert "cut off" in r.stderr
