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


def readme_repeated_headings():
    out = ["# rtool", "", "A small CLI for resizing and converting images in bulk.", ""]
    topics = [("resize a folder", "rtool resize --width 800 photos/", "Every image is scaled to 800 px wide."),
              ("convert to webp", "rtool convert --to webp photos/", "Originals are kept next to the output."),
              ("strip metadata", "rtool strip photos/*.jpg", "EXIF and GPS tags are removed in place."),
              ("dry run", "rtool resize --dry-run photos/", "Prints what would change without writing."),
              ("parallel jobs", "rtool convert -j 8 --to png raw/", "Eight workers share the queue.")]
    for title, cmd, note in topics:
        out += ["## Example", "", "How to %s." % title, "", "```bash", cmd, "```", "", note,
                "See the options table below for every flag.", ""]
    out += ["## Options", "", "| flag | meaning |", "|---|---|", "| -j | jobs |", "| --dry-run | no writes |", ""]
    return "\n".join(out)


def similar_test_file():
    names = ["header", "body", "footer", "empty", "unicode", "crlf", "tabs", "bom", "long_line", "nested",
             "quoted", "escaped", "trailing_space", "comment", "blank_lines", "mixed_case"]
    out = ['"""Tests for the config parser."""', "import pytest", "from app.config import parse, ParseError", ""]
    for i, n in enumerate(names):
        out += ["", "def test_parse_%s():" % n,
                "    text = load_fixture(\"%s.ini\")" % n,
                "    result = parse(text)",
                "    assert result.ok, result.errors",
                "    assert result.sections[0].name == \"%s\"" % n,
                "    assert len(result.sections) == %d" % (1 + i % 3)]
    return "\n".join(out) + "\n"


def log_summary():
    out = ["## Log summary for app.log (2,431 lines)", "",
           "- 13 ERROR, 52 WARN, 2,366 INFO lines", "- First error at 12:03:11, last at 12:45:09", "",
           "Error lines:", ""]
    msgs = ["db: connection refused (pool exhausted)", "worker-3: job 4411 timed out after 30s",
            "auth: token signature mismatch for client mobile"]
    for i in range(13):
        out.append("- 12:%02d:%02d ERROR %s" % (3 + 3 * i, (11 + 17 * i) % 60, msgs[i % 3]))
    out += ["", "Most errors come from the database pool; raising pool_size from 10 to 25 should fix them.",
            "The auth failures all come from one client version and stopped after the 12:30 deploy."]
    return "\n".join(out) + "\n"


def changelog():
    items = ["crash when the config file is empty", "wrong exit code on network errors",
             "progress bar flicker on narrow terminals", "slow startup with many plugins",
             "unicode file names on Windows", "memory leak in the watcher", "race in the job queue",
             "help text for the --jobs flag", "timezone handling in the scheduler", "duplicate log lines"]
    out = ["# Changelog", ""]
    for v in range(8):
        out += ["## [1.%d.0] - 2026-0%d-01" % (8 - v, 1 + v), "", "### Fixed"]
        out += ["- Fixed %s (#%d)" % (items[(v * 3 + j) % len(items)], 400 - v * 10 - j) for j in range(4)]
        out += ["", "### Added", "- Added option %d for the exporter" % (20 - v), ""]
    return "\n".join(out)


def service_report():
    svc = ["nginx", "postgres", "redis", "worker", "scheduler", "api-gateway", "auth", "billing", "search",
           "mailer"]
    desc = ["serves static assets and proxies the API", "primary database, nightly backups configured",
            "cache for sessions and rate limits", "runs the import queue", "cron-style scheduler for reports",
            "routes external traffic and terminates TLS", "issues tokens and handles password resets",
            "payment webhooks and invoice generation", "full-text index for the catalog",
            "sends transactional email via the SMTP relay"]
    return "Status of every service on the host:\n\n" + "".join(
        "### %s\n- Status: running\n- Restart policy: always\n- Notes: %s\n\n" % (n, d) for n, d in zip(svc, desc))


SERVICES = ["auth", "billing", "search", "mailer", "gateway", "cache", "queue", "indexer", "scheduler", "exporter"]


MODULES = ["parser", "lexer", "config", "cli", "server", "client", "cache", "models", "views", "utils", "auth",
           "export"]
ACTIONS = ["install the dependencies", "build the wheel", "run the unit tests", "lint the sources",
           "type-check the package", "build the docs", "start the dev server", "run the smoke test",
           "tag the release", "upload to the index", "update the changelog", "clean the workspace"]


def irregular_report(odd=None, extra=None):
    """10-item status report where one item differs ("degraded") and/or has an extra line."""
    return "".join("### Service %s\nStatus: %s\nLatency within budget\n%sNo errors in logs\n\n"
                   % (n, "degraded" if i == odd else "OK and healthy",
                      "Restarted once last night after deploy\n" if i == extra else "")
                   for i, n in enumerate(SERVICES))


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
    # recurrence detector: recurring lines in legitimate documents
    "readme_repeated_headings": readme_repeated_headings,
    "test_file_unfenced": similar_test_file,
    "test_file_fenced": lambda: "```python\n" + similar_test_file() + "```\n",
    "log_summary": log_summary,
    "changelog": changelog,
    "service_report": service_report,
    # review: >= 6-item reports with a >= 3-line boilerplate template (single-token item headers)
    "service_template": lambda: "".join("### Service %s\nStatus: OK and healthy\nLatency within budget\n"
                                        "No errors in logs\n\n" % n for n in SERVICES),
    # items named by words (ruling G-1: items that differ ONLY by a number are a counting loop)
    "module_template": lambda: "".join("**%s.py**\n- Lint: passed cleanly\n- Tests: all passing\n"
                                       "- Types: no errors found\n\n" % m for m in MODULES),
    "step_template": lambda: "".join("Step %d: %s\nRan the command successfully\nOutput matched expectation\n\n"
                                     % (i + 1, a) for i, a in enumerate(ACTIONS)),
    "service_report_degraded": lambda: irregular_report(odd=4),
    "service_report_extra_line": lambda: irregular_report(extra=6),
    "service_report_both": lambda: irregular_report(odd=2, extra=7),
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


# ── recurrence: a drifting loop in a final answer (recorded from Ornith-1.5-35B-A3B) ──
# The model answered correctly, then kept re-answering for 113 K chars: "[Source: ...]" pairs,
# "## Sources / - app.log / - count.txt" blocks and reworded "The task is complete" / "I'll end my
# turn now" paragraphs -- never one exact unit back to back, so only recurrence catches it.
ORNITH = os.path.join(HERE, "fixtures", "ornith_final_answer_loop.tokens")   # content chunks, \x1f-separated
ORNITH_LOOP_START = 664      # the first "[Source: ...]" pair ends here; everything after it re-answers


def ornith_chunks():
    with open(ORNITH, encoding="utf-8") as f:
        return f.read().split("\x1f")


def test_recorded_final_answer_loop_trips_early_with_a_cut_after_the_answer():
    chunks = ornith_chunks()
    text = "".join(chunks)
    assert len(text) > 100000
    g = llm.RunawayGuard(max_tokens=0)
    used = 0
    for c in chunks:                         # the recorded token boundaries
        used += len(c)
        trip = g.observe("content", c)
        if trip:
            break
    assert trip and trip["kind"] == "repetition" and trip["detector"] == "recurrence", trip
    assert used - ORNITH_LOOP_START <= 4096, used        # within ~1 KB of the loop in practice
    assert trip["cut"] == ORNITH_LOOP_START
    kept = text[:trip["cut"]]
    assert "13 ERROR lines" in kept and "count.txt" in kept and kept.count("[Source:") == 2


def test_recorded_loop_without_the_source_lines_still_trips():
    """The same stream minus its exact "[Source: ...]" lines: only the reworded paragraphs and the
    recurring "## Sources" block remain."""
    text = "".join(ln for ln in "".join(ornith_chunks()).splitlines(True) if not ln.startswith("[Source"))
    g = llm.RunawayGuard(max_tokens=0)
    trip, used = feed(g, text, "content", step=3)
    assert trip and trip["detector"] == "recurrence", trip
    assert used <= 8192, used
    kept = text[:trip["cut"]]
    assert "13" in kept and "count.txt" in kept and kept.count("## Sources") == 1


def test_recurrence_in_reasoning_trips_too():
    g = llm.RunawayGuard(max_tokens=0)
    trip, used = feed(g, "".join(ornith_chunks()), "reasoning", step=5)
    assert trip and trip["channel"] == "reasoning" and used <= 8192


def test_recurrence_ignores_fenced_code_and_tool_arguments():
    text = "".join(ornith_chunks())[:20000]
    g = llm.RunawayGuard(max_tokens=0)
    assert feed(g, "```text\n" + text + "\n```\n", "content", step=5)[0] is None
    g = llm.RunawayGuard(max_tokens=0)
    assert feed(g, json.dumps({"path": "out.md", "content": text}), "args0", step=5)[0] is None


@pytest.mark.parametrize("name", ["service_template", "module_template", "step_template",
                                  "service_report_degraded", "service_report_extra_line", "service_report_both"])
def test_templated_final_answer_is_not_cut(name):
    """Templated reports alone as the whole answer (no prose around them), both channels."""
    for channel in ("content", "reasoning"):
        g = llm.RunawayGuard(max_tokens=0)
        assert feed(g, LEGIT[name](), channel)[0] is None, (name, channel)


# ruling G-1 (reviewer's adversarial cases): loops that differ only by a counter, exactly
# periodic and multi-line, must trip well before the step cap -- they are not templates.
COUNTER_LOOPS = {
    "attempt_3line": lambda i: "Attempt %d\nI'll end my turn now.\nThe task is complete.\n\n" % i,
    "attempt_noblank": lambda i: "Attempt %d\nI'll end my turn now.\nThe task is complete.\n" % i,
    "attempt_colon": lambda i: "Attempt %d:\nI'll end my turn now.\nThe task is complete.\n\n" % i,
    "bold_attempt": lambda i: "**Attempt %d**\nI'll end my turn now.\nThe task is complete.\nCalling task_complete.\n\n" % i,
    "wait_short": lambda i: "Wait %d.\nActually the answer is 13.\n" % i,
    "iter_2line": lambda i: "Iteration %d\nChecking the value again now.\n" % i,
    "iter_3line": lambda i: "Iteration %d\nChecking the value again now.\nStill the same result here.\n" % i,
    "numbered_para": lambda i: ("%d. Let me re-check the output file once more.\nThe output looks correct to me.\n"
                                "So the count is 13.\n\n" % i),
    "count_inline3": lambda i: "Now count %d.\nThat makes %d items.\nNext one please.\n" % (i, i),
    "try_header_prose": lambda i: ("Try #%d - recount the lines.\nI count the lines again and get 13.\n"
                                   "So it must be 13 then.\n" % i),
    "numbered_steps_only": lambda i: "Step %d\nRan the command successfully\nOutput matched expectation\n\n" % i,
}


@pytest.mark.parametrize("name", sorted(COUNTER_LOOPS))
@pytest.mark.parametrize("channel", ["content", "reasoning"])
def test_counter_loops_are_not_templates(name, channel):
    loop = "".join(COUNTER_LOOPS[name](i) for i in range(1, 400))
    for head in ("", prose(1500) + "\n\n"):
        g = llm.RunawayGuard(max_tokens=0)
        trip, used = feed(g, head + loop, channel, step=5)
        assert trip and trip["kind"] == "repetition", (name, bool(head))
        assert used - len(head) <= 2048, (name, used - len(head))


def test_template_exemption_ends_at_25_items():
    names = ["%s-%s" % (a, b) for a in ("north", "south", "east", "west", "core", "edge")
             for b in ("alpha", "beta", "gamma", "delta", "omega")]
    report = "".join("### Service %s\nStatus: OK and healthy\nLatency within budget\nNo errors in logs\n\n" % n
                     for n in names)
    g = llm.RunawayGuard(max_tokens=0)
    trip, used = feed(g, report, "content")
    assert trip and trip["detector"] == "recurrence", trip
    # the 25th item ends the exemption; recurrence is checked every 256 chars (~3 items here)
    assert 25 <= report[:used].count("No errors in logs\n") <= 28, used


def _ornith_run(tmp_path, toolcall_first=False):
    spec = {"channel": "content", "chunks_file": ORNITH, "toolcall_first": toolcall_first, "delay": 0,
            "reasoning": "The count is 13, and count.txt contains 13. Task done.\n"}
    return _run(tmp_path, [spec])


def drifting_loop(n=600, seed=3):
    """A reworded loop: one recurring line at irregular gaps among a few rotating phrasings."""
    rnd = random.Random(seed)
    pool = ["The value looks fine, so the next file is the one to check.",
            "Nothing wrong there either; moving on to the next file now.",
            "That file is fine as well, I will continue with the others.",
            "No issue found in this one, continuing with the remaining files."]
    out = []
    for _ in range(n):
        out.append("Checking the configuration file for the service again.")
        out += [rnd.choice(pool) for _ in range(rnd.randint(1, 3))]
    return "\n".join(out) + "\n"


# a loop with no real answer before it is not a final answer: abort -> nudge -> retry
NON_ANSWERS = {
    "lead_in_then_line_loop": {"channel": "content", "prefix": "I'll write the config now:\n",
                               "unit": "The config sets the port and the host for the web service.\n",
                               "count": 2000, "delay": 0.0005},
    "short_intro_then_recurrence": {"channel": "content", "prefix": "Let me check each file.\n" + drifting_loop(),
                                    "unit": "", "count": 0, "delay": 0.0005},
}


@pytest.mark.parametrize("name", sorted(NON_ANSWERS))
def test_loop_without_a_real_answer_is_retried_not_salvaged(tmp_path, name):
    r, reqs, discs, _ = _run(tmp_path, [NON_ANSWERS[name]])
    assert r.returncode == 0, r.stderr[-3000:]
    assert discs and discs[0]["closed"], discs
    assert len(reqs) == 2, [q["messages"][-1] for q in reqs]
    assert "stuck repeating itself" in reqs[1]["messages"][-1]["content"]
    assert "kept the answer" not in r.stderr


def test_looping_final_answer_is_salvaged_not_retried(tmp_path):
    r, reqs, discs, elapsed = _ornith_run(tmp_path)
    assert r.returncode == 0, r.stderr[-3000:]
    assert len(reqs) == 1, [q["messages"][-1] for q in reqs]          # no nudge, no retry
    # cut mid-stream (the guard trips at ~1.4 KB; socket buffers let the server write more first)
    assert discs and discs[0]["closed"] is True and discs[0]["sent_chars"] < 100000, discs
    assert "kept the answer before the loop" in r.stderr
    assert "retrying" not in r.stderr
    sess = json.loads((tmp_path / "home" / ".cache" / "ai" / "sessions" / "last.json").read_text())
    final = [m for m in sess if m.get("role") == "assistant"][-1]["content"]
    assert "13" in final and "count.txt" in final and final.count("[Source:") == 2, final
    assert len(final) <= ORNITH_LOOP_START
    assert not any("stuck repeating" in json.dumps(m) for m in sess if isinstance(m, dict))
    metrics = tmp_path / "home" / ".cache" / "ai" / "metrics.jsonl"
    rows = [json.loads(x) for x in metrics.read_text().splitlines()]
    row = [x for x in rows if x.get("tool") == "generation_guard"][-1]
    assert row["action"] == "salvaged" and row["detector"] == "recurrence" and row["success"] is True, row


def test_looping_reply_with_a_tool_call_is_still_retried(tmp_path):
    r, reqs, discs, _ = _ornith_run(tmp_path, toolcall_first=True)
    assert r.returncode == 0, r.stderr[-3000:]
    assert len(reqs) == 2 and discs[0]["closed"]
    assert "stuck repeating itself" in reqs[1]["messages"][-1]["content"]
    assert "kept the answer" not in r.stderr
