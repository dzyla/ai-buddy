#!/usr/bin/env python3
"""ai-buddy harness benchmark — pass/fail job suite for the `ai` CLI.

Runs a battery of small, verifiable jobs through the real agent loop
(`ai --raw-output --no-git -y "<prompt>"`) against the configured local LLM
and scores each job by objective checks:

  * answer checks      — regex/substring on the model's final text
  * file checks        — files the job was supposed to create/edit
  * tool-use checks    — tool calls actually recorded in the session JSON
  * harness checks     — session persisted; metrics logged for backend tools

Every job runs in a scratch workspace (files are created there) and uses the
REPO checkout's ai_mcp.py (symlinked into the workdir; --no-git so nothing is
committed). Jobs are offline and deterministic: no network, no
time-sensitive answers except where computed at runtime.

Usage:
  python3 dev/bench_harness.py                 # full suite
  python3 dev/bench_harness.py --quick         # 4 fast jobs (CI-friendly)
  python3 dev/bench_harness.py --only write_file_task,math_tool
  python3 dev/bench_harness.py --json report.json

Exit code: 0 if score >= --min-pass (default 80%), 1 otherwise.
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_BIN = os.path.join(REPO, "ai")
SESSIONS_DIR = os.path.expanduser("~/.cache/ai/sessions")
METRICS_FILE = os.path.expanduser("~/.cache/ai/metrics.jsonl")
BENCH_ROOT = os.environ.get("AI_BENCH_ROOT", "/tmp/ai_bench")

# Tools handled natively in C (never reach the Python backend, so they log
# no metric).
NATIVE_TOOLS = {"think", "task_complete", "execute_command", "present_plan"}

QUICK_TASKS = {"smoke_trivia", "list_files", "write_file_task", "math_tool"}


# ─────────────────────────────────────────────────────────────────────────────
# Task definitions
# A task = (id, prompt, setup, checks, timeout)
#   setup(workdir)   -> creates fixture files
#   checks(ctx)      -> list of (name, ok, detail); ALL must pass
#   ctx keys: out (final text), workdir, session (list of messages or None),
#             tool_calls (list of tool names called)
# ─────────────────────────────────────────────────────────────────────────────

TASKS = []


def task(tid, prompt, setup=None, checks=None, timeout=180, quick=False):
    TASKS.append({"id": tid, "prompt": prompt, "setup": setup,
                  "timeout": timeout, "checks": checks, "quick": quick})


def _answer(ctx):
    return ctx.get("out") or ""


def _tools(ctx):
    return ctx.get("tool_calls") or []


def _has_tool(ctx, name):
    return name in _tools(ctx)


def _write(workdir, name, content):
    with open(os.path.join(workdir, name), "w", encoding="utf-8") as f:
        f.write(content)


def _read(workdir, name):
    with open(os.path.join(workdir, name), encoding="utf-8") as f:
        return f.read()


# ── fixtures ─────────────────────────────────────────────────────────────────

def _setup_list(w):
    for i in range(1, 6):
        _write(w, "f%d.txt" % i, "x\n")


def _setup_read(w):
    _write(w, "notes.txt",
           "Sample preparation log\n====================\nThe protein sample was "
           "prepared in 10 mM HEPES, 150 mM NaCl, pH 7.4. Buffer was prepared "
           "from 1M stock on Monday.\n")


def _setup_search(w):
    _write(w, "util_a.py", "def load_config():\n    return {}\n")
    _write(w, "util_b.py", "def compute_flux(x):\n    return x * 2\n")
    _write(w, "util_c.py", "def save_report(s):\n    pass\n")


def _setup_sq(w):
    _write(w, "data.txt", "alpha 1\nbeta 2\nalpha 3\ngamma 4\nalpha 5\n")


def _setup_edit(w):
    _write(w, "animal.txt", "cat\n")


# ── check functions ──────────────────────────────────────────────────────────

def _chk_smoke(ctx):
    a = _answer(ctx).lower()
    return [("answer has 'paris'", "paris" in a, a[:80])]


def _chk_list(ctx):
    a = _answer(ctx)
    return [("answer is 5", re.search(r"\b5\b", a) is not None, a[:80]),
            ("used list_directory", _has_tool(ctx, "list_directory"), str(_tools(ctx))[:120])]


def _chk_read(ctx):
    a = _answer(ctx)
    return [("answer has '10 mM'", bool(re.search(r"10\s*mM", a, re.I)), a[:100]),
            ("used read_file", _has_tool(ctx, "read_file"), str(_tools(ctx))[:120])]


def _chk_write_file(ctx):
    p = os.path.join(ctx["workdir"], "hello.txt")
    ok = os.path.exists(p)
    detail = "(missing)" if not ok else repr(_read(ctx["workdir"], "hello.txt")[:60])
    return [("hello.txt created", ok, detail),
            ("content is 'hello world'", ok and "hello world" in _read(ctx["workdir"], "hello.txt"), detail),
            ("used write_file", _has_tool(ctx, "write_file"), str(_tools(ctx))[:120])]


def _chk_math(ctx):
    a = _answer(ctx)
    return [("answer is 391", "391" in a, a[:80]),
            ("used execute_command", _has_tool(ctx, "execute_command"), str(_tools(ctx))[:120])]


def _chk_search(ctx):
    a = _answer(ctx)
    return [("answer names util_b.py", "util_b" in a, a[:100]),
            ("used search_files", _has_tool(ctx, "search_files"), str(_tools(ctx))[:120])]


def _chk_structured_query(ctx):
    a = _answer(ctx)
    return [("answer is 3", re.search(r"\b3\b", a) is not None, a[:80]),
            ("used structured_query", _has_tool(ctx, "structured_query"), str(_tools(ctx))[:120])]


def _chk_pool(ctx):
    a = _answer(ctx).lower()
    return [("answer mentions zebra fact", "zebra" in a, a[:120]),
            ("used append_to_context_pool", _has_tool(ctx, "append_to_context_pool"), str(_tools(ctx))[:150]),
            ("used search_context", _has_tool(ctx, "search_context"), str(_tools(ctx))[:150])]


def _chk_edit(ctx):
    try:
        content = _read(ctx["workdir"], "animal.txt")
    except Exception:
        content = ""
    return [("file now contains 'dog'", "dog" in content and "cat" not in content, repr(content[:40])),
            ("used edit_file", _has_tool(ctx, "edit_file"), str(_tools(ctx))[:150])]


def _chk_time(ctx):
    expected = datetime.date.today().strftime("%A")
    a = _answer(ctx)
    return [("answer is today's day (%s)" % expected, expected.lower() in a.lower(), a[:80]),
            ("used check_time", _has_tool(ctx, "check_time"), str(_tools(ctx))[:120])]


def _chk_todo(ctx):
    a = _answer(ctx)
    return [("answer says 1 done", re.search(r"\b1\b", a) is not None, a[:100]),
            ("used todo at least twice", _tools(ctx).count("todo") >= 2, str(_tools(ctx))[:150])]


def _chk_recovery(ctx):
    a = _answer(ctx)
    w = os.path.realpath(ctx["workdir"])
    # The model's cwd is the task workdir (where `ai` was invoked).
    ok = w in a
    return [("answered the real cwd", ok, "expected %s | got: %s" % (w, a[:120])),
            ("used execute_command", _has_tool(ctx, "execute_command"), str(_tools(ctx))[:120])]


def _chk_clarify(ctx):
    a = _answer(ctx).lower()
    return [("used clarify", _has_tool(ctx, "clarify"), str(_tools(ctx))[:120]),
            ("chose the first/recommended option (red)", "red" in a, a[:150])]


def _chk_write_verify(ctx):
    p = os.path.join(ctx["workdir"], "report.txt")
    ok = os.path.exists(p) and "BENCHMARK OK" in _read(ctx["workdir"], "report.txt")
    a = _answer(ctx)
    return [("report.txt has 'BENCHMARK OK'", ok,
             "(missing)" if not ok else repr(_read(ctx["workdir"], "report.txt")[:40])),
            ("model confirmed it", "BENCHMARK OK" in a, a[:120])]


# ── task table ───────────────────────────────────────────────────────────────

task("smoke_trivia",
     "What is the capital of France? Answer with just the city name.",
     checks=_chk_smoke, timeout=90, quick=True)

task("list_files",
     "Use the list_directory tool to look at the files in the current working "
     "directory. Answer with just the number of .txt files there (there are no "
     "subdirectories).",
     setup=_setup_list, checks=_chk_list, quick=True)

task("read_and_summarize",
     "Use read_file to read the file notes.txt in the current directory. What "
     "concentration of HEPES was used to prepare the sample? Answer briefly with the number and unit.",
     setup=_setup_read, checks=_chk_read, timeout=120)

task("write_file_task",
     "Use the write_file tool to create a file named hello.txt in the current "
     "working directory whose content is exactly: hello world",
     checks=_chk_write_file, quick=True)

task("math_tool",
     "Use execute_command to compute 17*23 (for example: python3 -c \"print(17*23)\"). "
     "Answer with just the number.",
     checks=_chk_math, quick=True)

task("search_code",
     "I have three Python files in the current directory: util_a.py, util_b.py, "
     "util_c.py. Use the search_files tool to find which one defines the function "
     "compute_flux. Answer with just the filename.",
     setup=_setup_search, checks=_chk_search, timeout=150)

task("structured_query_count",
     "The file data.txt in the current directory has 5 lines, but only some of "
     "them contain the word 'alpha'. Use the structured_query tool with target "
     "'file:data.txt', a filter_expr that matches only lines containing 'alpha', "
     "and aggregate 'count'. Answer with just the number it returns (it must be "
     "less than 5).",
     setup=_setup_sq, checks=_chk_structured_query, timeout=150)

task("context_pool_roundtrip",
     "Use append_to_context_pool to store the fact: 'zebra fact: zebras sleep only 2 hours a day'. "
     "Then use search_context with the query 'zebra' to retrieve it, and tell me what you found.",
     checks=_chk_pool, timeout=180)

task("edit_file_task",
     "The file animal.txt in the current directory contains one word. Use the edit_file tool to "
     "change the word 'cat' to 'dog'. Then read the file back and quote the exact word it "
     "contains now.",
     setup=_setup_edit, checks=_chk_edit, timeout=180)

task("check_time_day",
     "Use the check_time tool to find the current time. What day of the week is it today? "
     "Answer with just the day name.",
     checks=_chk_time, timeout=120)

task("todo_workflow",
     "Use the todo tool to create a todo list with 3 items (ids a, b, c) about preparing "
     "a lab report. Mark item a as completed (merge=True). Then read the list back with todo "
     "and tell me how many items are done.",
     checks=_chk_todo, timeout=240)

task("failure_recovery",
     "First, use execute_command to run: ls /definitely_not_here_xyz_123   "
     "(It will fail — that is expected.) Then figure out where the current working "
     "directory actually is (e.g. run pwd) and tell me the full path of the current directory.",
     checks=_chk_recovery, timeout=240)

task("clarify_noninteractive",
     "Use the clarify tool with the question 'Which color should the plot be?' and "
     "choices red, blue. In this non-interactive environment it will return a note "
     "instead of a human answer — follow the note's instruction and tell me which "
     "color you will use and why.",
     checks=_chk_clarify, timeout=180)

task("write_verify",
     "Create a file report.txt in the current directory containing exactly the line: "
     "BENCHMARK OK. Then use read_file to read it back and confirm to me that it says "
     "'BENCHMARK OK'.",
     checks=_chk_write_verify, timeout=240)


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def _session_files():
    try:
        return set(glob.glob(os.path.join(SESSIONS_DIR, "*.json")))
    except Exception:
        return set()


def _load_session(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _tool_calls_from_session(session):
    names = []
    if not isinstance(session, list):
        return names
    for m in session:
        if not isinstance(m, dict):
            continue
        for tc in (m.get("tool_calls") or []):
            try:
                names.append(tc.get("function", {}).get("name", ""))
            except Exception:
                pass
    return names


def _final_text(session, out):
    """Prefer the last assistant message content; fall back to stdout."""
    if isinstance(session, list):
        for m in reversed(session):
            if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content"):
                return str(m["content"])
    return out


def _metrics_count():
    try:
        with open(METRICS_FILE, encoding="utf-8") as f:
            return sum(1 for l in f if l.strip())
    except Exception:
        return 0


def _run_ai(cmd, workdir, timeout):
    """Run `ai` in its own session (process group) so a timeout can kill the
    WHOLE tree — the model may launch background grandchildren (conda, daemons)
    that would otherwise outlive the timeout and keep writing to our pipes."""
    import signal
    timed_out = False
    out, err = "", ""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=workdir, start_new_session=True)
        try:
            out_b, err_b = proc.communicate(timeout=timeout)
            out, err = out_b or "", err_b or ""
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                out_b, err_b = proc.communicate(timeout=10)
                out, err = out_b or "", err_b or ""
            except Exception:
                proc.kill()
                proc.wait()
    except Exception as e:
        err += "\n(bench: %r)" % e
    return timed_out, out, err


def run_task(task, verbose=True):
    tid = task["id"]
    workdir = os.path.join(BENCH_ROOT, tid)
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir)
    # The agent runs with cwd=workdir (so "the current directory" in the prompt
    # is the scratch dir, and files it writes land there). ai.c finds its tool
    # backend via ./ai_mcp.py first — symlink the REPO's backend so the
    # benchmark always tests the current repo code.
    os.symlink(os.path.join(REPO, "ai_mcp.py"), os.path.join(workdir, "ai_mcp.py"))
    if task["setup"]:
        task["setup"](workdir)

    before_sessions = _session_files()
    before_metrics = _metrics_count()
    t0 = time.time()
    cmd = [AI_BIN, "--raw-output", "--no-git", "-y",
           task["prompt"].replace("{workdir}", workdir)]
    timed_out, out, err = _run_ai(cmd, workdir, task["timeout"])
    # One retry on timeout: a busy GPU / transient server stall shouldn't fail
    # an otherwise-correct harness.
    if timed_out:
        t0 = time.time()
        timed_out, out, err = _run_ai(cmd, workdir, task["timeout"])
    dur = time.time() - t0

    # locate the session file this run produced: a session whose mtime is at or
    # after the run start (mtime-only, not "new files", so resume/overwrite of
    # last.json can't fool us).
    session, session_path = None, None
    candidates = [p for p in _session_files()
                  if os.path.getmtime(p) >= t0 - 2]
    if candidates:
        session_path = max(candidates, key=os.path.getmtime)
    else:
        all_s = sorted(_session_files(), key=os.path.getmtime, reverse=True)
        if all_s and os.path.getmtime(all_s[0]) >= t0 - 2:
            session_path = all_s[0]
    session = _load_session(session_path) if session_path else None

    final = _final_text(session, out)
    tool_calls = _tool_calls_from_session(session)
    ctx = {"out": final, "stdout": out, "stderr": err, "workdir": workdir,
           "session": session, "session_path": session_path,
           "tool_calls": tool_calls,
           "timed_out": timed_out, "duration": dur}

    checks = []
    if timed_out:
        checks.append(("finished in time", False, "timeout after %ds" % task["timeout"]))
    else:
        try:
            checks = task["checks"](ctx)
        except Exception as e:
            import traceback
            checks = [("checks raised", False, "%r: %s" % (e, traceback.format_exc().splitlines()[-1]))]
    # harness-level checks
    checks.append(("session persisted", session is not None,
                   session_path or "no session file found"))
    # metrics are only logged for tools that reach the Python backend; a
    # task answered with native tools (think/task_complete) logs none.
    backend_tools = [t for t in tool_calls if t not in NATIVE_TOOLS]
    if backend_tools:
        checks.append(("metrics logged for backend tools", _metrics_count() > before_metrics,
                       "%d -> %d (tools: %s)" % (before_metrics, _metrics_count(),
                                                 ", ".join(dict.fromkeys(backend_tools)))))
    else:
        checks.append(("metrics logged for backend tools", True,
                       "skipped — only native tools used (%s)" % ", ".join(tool_calls) or "none"))

    passed = all(ok for _, ok, _ in checks)
    return {"id": tid, "pass": passed, "duration": round(dur, 1),
            "tools": list(dict.fromkeys(tool_calls)),
            "answer": final[:300],
            "checks": [{"name": n, "ok": bool(ok), "detail": str(d)[:200]} for n, ok, d in checks]}


def main():
    ap = argparse.ArgumentParser(description="ai-buddy harness benchmark")
    ap.add_argument("--quick", action="store_true", help="run only the fast subset")
    ap.add_argument("--only", default="", help="comma-separated task ids")
    ap.add_argument("--json", default="", help="write a JSON report to this path")
    ap.add_argument("--min-pass", type=float, default=80.0,
                    help="passing score %% (default 80)")
    ap.add_argument("--keep", action="store_true", help="keep workdirs for debugging")
    args = ap.parse_args()

    if not os.path.exists(AI_BIN):
        print("ERROR: %s not found — run `make` first." % AI_BIN)
        return 2

    tasks = TASKS
    if args.only:
        wanted = set(x.strip() for x in args.only.split(",") if x.strip())
        tasks = [t for t in TASKS if t["id"] in wanted]
        unknown = wanted - set(t["id"] for t in tasks)
        if unknown:
            print("WARNING: unknown task id(s) ignored: %s" % ", ".join(sorted(unknown)))
    elif args.quick:
        tasks = [t for t in TASKS if t["id"] in QUICK_TASKS]

    print("ai-buddy harness benchmark — %d task(s), model endpoint from ~/.local/share/ai/env" % len(tasks))
    print("=" * 78)
    results = []
    for t in tasks:
        print("[RUN ] %s" % t["id"])
        res = run_task(t)
        results.append(res)
        mark = "PASS" if res["pass"] else "FAIL"
        print("  %s  (%.1fs, tools: %s)" % (mark, res["duration"],
                                            ", ".join(res["tools"]) or "none"))
        for c in res["checks"]:
            if not c["ok"]:
                print("  ✗ %s: %s" % (c["name"], c["detail"]))
        if res["pass"]:
            print("  ✓ %s" % res["answer"][:110].replace("\n", " "))

    n = len(results)
    ok = sum(1 for r in results if r["pass"])
    score = 100.0 * ok / n if n else 0.0
    print("=" * 78)
    print("SCORE: %d/%d (%.0f%%)  — passing threshold %.0f%%" % (ok, n, score, args.min_pass))
    if not args.keep:
        shutil.rmtree(BENCH_ROOT, ignore_errors=True)

    if args.json:
        report = {"timestamp": datetime.datetime.now().isoformat(),
                  "score_pct": score, "passed": ok, "total": n,
                  "results": results}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print("JSON report: %s" % args.json)

    return 0 if score >= args.min_pass else 1


if __name__ == "__main__":
    sys.exit(main())
