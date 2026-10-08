"""Context budget derived from the model's n_ctx (ai.c apply_context_budget / agent/cli.py Budget.apply).

The budget scales in BOTH directions: small windows get today's tightened limits, long windows get
looser ones than the 100000 / 250000 / 65536 defaults, an unknown window keeps the defaults and an
explicit INFER_TRIM_THRESHOLD / INFER_STUB_THRESHOLD / INFER_MAX_TOOL_OUTPUT always wins. Offline:
the binary-driving tests run against tests/mock_llm_server.py with an isolated HOME.
"""
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from agent import cli  # noqa: E402
from aitools import files  # noqa: E402
from test_offline import MockServer, _sandbox_run, build_binary  # noqa: E402,F401

DEFAULTS = (100000, 250000, 65536)
TOOLS = 40000


def derived(n_ctx, tools_len=TOOLS, **explicit):
    b = cli.Budget(**explicit)
    b.apply(n_ctx, tools_len)
    return b.trim, b.stub, b.max_tool


def old_tighten_only(n_ctx, tools_len):
    """The pre-change rule (never looser than the defaults), for the small-window regression."""
    ctx_chars, reserve = n_ctx * 3, min(n_ctx // 4, 8192) * 3
    budget = max(ctx_chars - tools_len - reserve, 8000)
    return (min(DEFAULTS[0], budget * 8 // 10), min(DEFAULTS[1], budget * 95 // 100),
            min(DEFAULTS[2], max(4000, budget // 4)))


# n_ctx -> (trim, stub, max_tool) with a 40000-char tool catalog
# budget = n_ctx*3 - 40000 - reserve, reserve = min(n_ctx/4, max(8192, max_tokens=32768)) tokens * 3
TABLE = {
    8192: (6400, 7600, 4000),           # budget floored at 8000
    32768: (26982, 32041, 8432),        # 98304 - 40000 - 24576 = 33728
    65536: (85964, 102083, 26864),      # 196608 - 40000 - 49152 = 107456
    131072: (203929, 242166, 63728),    # 393216 - 40000 - 98304 = 254912
    262144: (518502, 615721, 162032),   # 786432 - 40000 - 98304 = 648128
}


@pytest.mark.parametrize("n_ctx", sorted(TABLE))
def test_derived_table(n_ctx):
    assert derived(n_ctx) == TABLE[n_ctx]


def test_unknown_window_keeps_defaults():
    assert derived(0) == DEFAULTS
    assert derived(-1) == DEFAULTS


@pytest.mark.parametrize("n_ctx", [4096, 8192, 16384, 24576, 32768])
@pytest.mark.parametrize("tools_len", [0, 11676, 46268])
def test_small_windows_unchanged(n_ctx, tools_len):
    assert derived(n_ctx, tools_len) == old_tighten_only(n_ctx, tools_len)


def test_reply_reserve_follows_max_tokens():
    # at most max(8192, max_tokens) tokens, never more than a quarter of the window
    b = cli.Budget()
    b.apply(131072, TOOLS, max_tokens=8192)
    assert b.trim == (131072 * 3 - TOOLS - 8192 * 3) * 8 // 10
    b = cli.Budget()
    b.apply(262144, TOOLS, max_tokens=65536)
    assert b.trim == (262144 * 3 - TOOLS - 65536 * 3) * 8 // 10
    for n in (8192, 16384, 32768):                 # <=32K: n/4 is the smaller term either way
        assert derived(n) == old_tighten_only(n, TOOLS)


def test_long_window_loosens_and_tool_cap_is_clamped():
    assert derived(131072)[0] > 2 * DEFAULTS[0]
    trim, stub, tool = derived(262144)
    assert trim > DEFAULTS[0] and stub > DEFAULTS[1] and tool > DEFAULTS[2]
    assert derived(1 << 20)[2] == cli.Budget.TOOL_CEIL == 262144
    for n in (8192, 65536, 131072, 1 << 20):
        t, s, m = derived(n)
        assert m < s and t < s            # one result always fits under the stub threshold


def test_llama_ctx_size_is_only_an_upper_bound():
    base = {"INFER_BASE_URL": "u", "INFER_API_KEY": "k", "INFER_MODEL": "m"}
    cfg = cli.parse(["x"], {**base, "LLAMA_CTX_SIZE": "131072"})
    assert (cfg.context_window, cfg.ctx_size_hint) == (0, 131072)       # /props still queried
    cfg = cli.parse(["x"], {**base, "LLAMA_CTX_SIZE": "131072", "INFER_CONTEXT_WINDOW": "65536"})
    assert cfg.context_window == 65536


def test_explicit_values_win():
    assert derived(131072, trim=50, trim_explicit=True)[0] == 50
    assert derived(131072, stub=60, stub_explicit=True)[1] == 60
    assert derived(131072, max_tool=70, max_tool_explicit=True)[2] == 70
    env = {"INFER_BASE_URL": "u", "INFER_API_KEY": "k", "INFER_MODEL": "m",
           "INFER_TRIM_THRESHOLD": "12345", "INFER_MAX_TOOL_OUTPUT": "9999"}
    cfg = cli.parse(["x"], env)
    cfg.budget.apply(131072, TOOLS)
    assert (cfg.budget.trim, cfg.budget.stub, cfg.budget.max_tool) == (12345, TABLE[131072][1], 9999)
    # --short-ctx pins its own small values even on a long window
    cfg = cli.parse(["--short-ctx", "x"], {k: env[k] for k in ("INFER_BASE_URL", "INFER_API_KEY", "INFER_MODEL")})
    cfg.budget.apply(131072, TOOLS)
    assert (cfg.budget.trim, cfg.budget.max_tool) == (16000, 4000)


# ── read_file follows the effective cap ─────────────────────────────────────
def _notebook(path, n_lines, width=100):
    path.write_text("".join("LINE%05d " % i + "x" * (width - 11) + "\n" for i in range(1, n_lines + 1)))
    return path


def test_read_file_whole_with_long_window_cap(tmp_path, monkeypatch):
    f = _notebook(tmp_path / "nb.txt", 1200)                       # 120 KB
    monkeypatch.delenv("INFER_MAX_TOOL_OUTPUT", raising=False)
    monkeypatch.delenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", raising=False)
    assert files.read_file(str(f)).startswith("[Large file:")      # default 64 KB cap: outline
    monkeypatch.setenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", str(TABLE[262144][2]))
    out = files.read_file(str(f))
    assert out == f.read_text()


def test_ranged_read_is_cut_to_the_cap_with_continue_hint(tmp_path, monkeypatch):
    f = _notebook(tmp_path / "nb.txt", 1000)
    monkeypatch.delenv("INFER_MAX_TOOL_OUTPUT", raising=False)
    monkeypatch.setenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", "20000")   # chunk ~16000 chars = 160 lines
    out = files.read_file(str(f), start_line=1, end_line=1000)
    assert "showing 1-160" in out and "start_line=161" in out
    assert "LINE00160" in out and "LINE00161" not in out
    monkeypatch.setenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", "262144")
    out = files.read_file(str(f), start_line=1, end_line=1000)
    assert "LINE01000" in out and "start_line=" not in out


def test_ranged_read_long_line_and_past_eof(tmp_path, monkeypatch):
    monkeypatch.delenv("INFER_MAX_TOOL_OUTPUT", raising=False)
    monkeypatch.setenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", "20000")
    f = tmp_path / "wide.txt"
    f.write_text("short\n" + "W" * 50000 + "\nafter\n")
    out = files.read_file(str(f), start_line=2, end_line=3)
    assert "Line 2 is 50000 chars; showing the first 16000" in out and "cut -c" in out
    assert "start_line=3" in out and out.count("W") == 16000
    out = files.read_file(str(f), start_line=10, end_line=20)
    assert out.startswith("Error: start_line 10 is beyond end of file (3 lines)")


def test_explicit_max_tool_output_wins_for_tools(monkeypatch):
    monkeypatch.setenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", "200000")
    monkeypatch.delenv("INFER_MAX_TOOL_OUTPUT", raising=False)
    assert files.effective_max_tool_output() == 200000
    monkeypatch.setenv("INFER_MAX_TOOL_OUTPUT", "5000")
    assert files.effective_max_tool_output() == 5000
    monkeypatch.delenv("INFER_MAX_TOOL_OUTPUT")
    monkeypatch.delenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT")
    assert files.effective_max_tool_output() == 65536


def test_ai_backend_ctx_does_not_set_infer_context_window(tmp_path, monkeypatch):
    import importlib.util
    import shutil
    dst = tmp_path / "ai_backend_mod.py"
    shutil.copyfile(os.path.join(REPO, "ai-backend"), dst)
    spec = importlib.util.spec_from_file_location("ai_backend_ctx_mod", dst)
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)
    seen = []
    monkeypatch.setattr(ab, "persist_env_changes", lambda ch, auto_restart=True: seen.append(dict(ch)))
    monkeypatch.setattr(ab, "sync_ctx_to_systemd", lambda v: None)
    ab.cmd_ctx("128k", auto_restart=False)
    assert seen == [{"LLAMA_CTX_SIZE": "131072", "INFER_CONTEXT_WINDOW": None}]


# ── both engines: same numbers, explicit env wins, nothing trimmed on a long window ─────
BUDGET_RE = re.compile(r"\[ai\] context budget: n_ctx=(\d+) tools=(\d+) trim=(\d+) stub=(\d+) max_tool=(\d+)")


def _budget_line(tmp_path, n_ctx, extra=None):
    cap = str(tmp_path / ("cap_%s.jsonl" % n_ctx))
    srv_env = {"MOCK_TASK_COMPLETE": "ok"}
    if n_ctx:
        srv_env["MOCK_N_CTX"] = str(n_ctx)
    with MockServer(cap, **srv_env) as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["hi"], {"INFER_PRINT_BUDGET": "1", **(extra or {})})
    assert r.returncode == 0, r.stderr[-2000:]
    m = BUDGET_RE.search(r.stderr)
    assert m, r.stderr[-2000:]
    return tuple(int(x) for x in m.groups())


@pytest.mark.parametrize("n_ctx", [8192, 32768, 65536, 131072, 262144, 0])
def test_engines_derive_the_same_budget(tmp_path, n_ctx):
    got = {}
    for eng in ("c", "py"):
        line = _budget_line(tmp_path, n_ctx, {"INFER_ENGINE": eng, "INFER_ENGINE_STRICT": "1"})
        n, tools_len, trim, stub, tool = line
        assert n == n_ctx
        assert (trim, stub, tool) == derived(n_ctx, tools_len), eng
        got[eng] = line
    assert got["c"] == got["py"]


def test_explicit_env_wins_in_binary(tmp_path, engine):
    n, tools_len, trim, stub, tool = _budget_line(
        tmp_path, 131072, {"INFER_TRIM_THRESHOLD": "12345", "INFER_STUB_THRESHOLD": "23456",
                           "INFER_MAX_TOOL_OUTPUT": "7777"})
    assert (trim, stub, tool) == (12345, 23456, 7777)


def test_long_window_reads_160k_file_without_trimming(tmp_path, engine):
    """n_ctx=131072 from /props and a 160 KB file read in four 40 KB ranged chunks: every chunk
    must still be in the last request (with the old 100 KB trim threshold the first ones were
    dropped), and the request only ever grows."""
    f = _notebook(tmp_path / "notebook.txt", 1600)                 # 160 KB
    seq = [{"id": "r%d" % k, "type": "function",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": str(f), "start_line": 400 * k + 1,
                                                  "end_line": 400 * (k + 1)})}}
           for k in range(4)]
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(seq), MOCK_N_CTX="131072") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["read the notebook"])
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert len(reqs) >= 5
    sizes = [len(json.dumps(q["messages"])) for q in reqs]
    assert sizes == sorted(sizes), sizes
    final = json.dumps(reqs[-1]["messages"])
    missing = [i for i in range(1, 1601) if "LINE%05d" % i not in final]
    assert not missing, "trimmed/truncated: %d lines missing (first %s)" % (len(missing), missing[:3])
    assert sizes[-1] > 160000


@pytest.mark.parametrize("llama_ctx,props,explicit,want", [
    ("131072", "32768", None, 32768),      # per-slot /props below the server total (--parallel 4)
    ("16384", "131072", None, 16384),      # stale/smaller LLAMA_CTX_SIZE still caps
    ("131072", None, None, 131072),        # no /props: the env value
    ("131072", "32768", "65536", 65536),   # INFER_CONTEXT_WINDOW is an explicit override
])
def test_n_ctx_source(tmp_path, engine, llama_ctx, props, explicit, want):
    cap = str(tmp_path / "cap.jsonl")
    srv_env = {"MOCK_TASK_COMPLETE": "ok", "MOCK_N_CTX": props}
    extra = {"INFER_PRINT_BUDGET": "1", "LLAMA_CTX_SIZE": llama_ctx}
    if explicit:
        extra["INFER_CONTEXT_WINDOW"] = explicit
    with MockServer(cap, **srv_env) as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["hi"], extra)
    assert r.returncode == 0, r.stderr[-2000:]
    m = BUDGET_RE.search(r.stderr)
    assert m and int(m.group(1)) == want, r.stderr[-2000:]


def _reads(f, n, lines_per=200):
    return [{"id": "c%d" % k, "type": "function",
             "function": {"name": "read_file",
                          "arguments": json.dumps({"path": str(f), "start_line": lines_per * k + 1,
                                                   "end_line": lines_per * (k + 1)})}}
            for k in range(n)]


def test_context_overflow_400_trims_and_retries_once(tmp_path, engine):
    f = _notebook(tmp_path / "nb.txt", 1600)
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(_reads(f, 8)), MOCK_CTX_ERROR_AT="9",
                    MOCK_TASK_COMPLETE="RETRIED_OK") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["read it"], {"INFER_TRIM_THRESHOLD": "1000000"})
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert "retrying once" in r.stderr
    assert "RETRIED_OK" in r.stdout
    sizes = [len(json.dumps(q["messages"])) for q in reqs]
    assert len(reqs) == 10 and sizes[9] <= sizes[8] * 0.6, sizes     # request 10 = the trimmed retry
    assert any("CURRENT" in json.dumps(m) or m.get("role") == "user" for m in reqs[9]["messages"])


def test_context_overflow_400_twice_ends_the_task(tmp_path, engine):
    """Only one retry per overflow: a second 400 in a row is reported, not looped on."""
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_CTX_ERROR_AT="1,2,3", MOCK_TASK_COMPLETE="x") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["hi"])
        reqs = srv.requests()
    assert "retrying once" in r.stderr and len(reqs) == 2, (len(reqs), r.stderr[-1500:])
    assert "exceeds the available context size" in r.stderr


def test_high_prompt_tokens_force_a_trim(tmp_path, engine):
    """The server counts 1 char/token (dense content, far below the assumed 3): once
    usage.prompt_tokens passes 80% of n_ctx the history is trimmed before the next request by the
    measured ratio, although it is still under trim_threshold (~199K chars at 128K)."""
    f = _notebook(tmp_path / "nb.txt", 1600)
    cap = str(tmp_path / "cap.jsonl")
    with MockServer(cap, MOCK_TOOL_SEQ=json.dumps(_reads(f, 8)), MOCK_N_CTX="131072",
                    MOCK_CHARS_PER_TOKEN="1") as srv:
        r = _sandbox_run(tmp_path, srv.base_url, ["read it"])
        reqs = srv.requests()
    assert r.returncode == 0, r.stderr[-2000:]
    assert "context tokens; trimming history" in r.stderr
    body = [len(json.dumps(q)) for q in reqs]                 # what the mock counts as tokens
    assert any(b < a for a, b in zip(body, body[1:])), body
    # never far past the window: one 20 KB read can land on top of an 80% prompt, no more
    assert max(body) < 131072 * 0.8 + 25000, body
