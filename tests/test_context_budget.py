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
TABLE = {
    8192: (6400, 7600, 4000),           # budget floored at 8000
    32768: (26982, 32041, 8432),        # 98304 - 40000 - 24576 = 33728
    65536: (105625, 125430, 33008),     # 132032: trim already looser than the 100000 default
    131072: (262912, 312208, 82160),    # 328640
    262144: (577484, 685763, 180464),   # 721856
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


def test_long_window_loosens_and_tool_cap_is_clamped():
    trim, stub, tool = derived(131072)
    assert trim > DEFAULTS[0] and stub > DEFAULTS[1] and tool > DEFAULTS[2]
    assert derived(1 << 20)[2] == cli.Budget.TOOL_CEIL == 262144
    for n in (8192, 65536, 131072, 1 << 20):
        t, s, m = derived(n)
        assert m < s and t < s            # one result always fits under the stub threshold


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
    monkeypatch.setenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", "20000")   # chunk ~16000 chars = 160 lines
    out = files.read_file(str(f), start_line=1, end_line=1000)
    assert "showing 1-160" in out and "start_line=161" in out
    assert "LINE00160" in out and "LINE00161" not in out
    monkeypatch.setenv("INFER_EFFECTIVE_MAX_TOOL_OUTPUT", "262144")
    out = files.read_file(str(f), start_line=1, end_line=1000)
    assert "LINE01000" in out and "start_line=" not in out


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


def test_long_window_reads_200k_file_without_trimming(tmp_path, engine):
    """n_ctx=131072 from /props and a ~200 KB file read in four 50 KB ranged chunks: every chunk
    must still be in the last request (with the old 100 KB trim threshold the first ones were
    dropped), and the request only ever grows."""
    f = _notebook(tmp_path / "notebook.txt", 2000)                 # 200 KB
    seq = [{"id": "r%d" % k, "type": "function",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": str(f), "start_line": 500 * k + 1,
                                                  "end_line": 500 * (k + 1)})}}
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
    missing = [i for i in range(1, 2001) if "LINE%05d" % i not in final]
    assert not missing, "trimmed/truncated: %d lines missing (first %s)" % (len(missing), missing[:3])
    assert sizes[-1] > 200000
