#!/usr/bin/env python3
"""Agent eval set runner for the `ai` CLI.

Each task in evals/tasks/*.json runs the real `ai` binary (`ai -y "<prompt>"`) in a fresh temp
workdir with an isolated HOME, then is scored by the task's checks:

  answer_contains      {"text": "..."} or {"any": [...]}   case-insensitive, on the final answer
  file_exists          {"path": "rel/path"}                 "in": "home" = under the eval HOME
  file_contains        {"path": "rel/path", "text": "..."}  (also takes "in": "home")
  tool_used            {"name": "read_file", "min": 1}      tool calls recorded in the session
  tool_result_contains {"tool": "read_file", "text": "..."} what a tool really returned
  max_steps            {"value": N}                         model turns (assistant messages)
Any check takes "not": true to invert it.

Task file fields: id, prompt, checks, optional files {relpath: content} (workdir), home_files
{relpath: content} (eval HOME), env {VAR: value}, timeout (s), and mock {"steps": [...],
"final": "..."}. A mock step is {"tool", "args"[, "expect_error": true]} or a list of them
(parallel calls in one turn); "final" becomes the task_complete summary.

In --mock mode the answer text and the tool calls are scripted, so the runner also fails a task
when a scripted tool call returns an error, unless that step has "expect_error": true; such a
step must then really error (recovery tasks prove their failure is real).

Modes:
  --mock    start tests/mock_llm_server.py per task, scripted by the task's `mock` field. Offline
            and deterministic (CI): the tools really run, so file/tool checks are real. Exit 1 if
            any task fails, since a scripted run can only fail through a harness regression.
  default   a real OpenAI-compatible endpoint from --base-url/--model/--api-key, else INFER_* from
            the environment, else ~/.local/share/ai/env. Exit 0 unless the pass rate is below
            --min-pass (default 0).

Usage:
  python3 evals/run_evals.py --mock [--json out.json]
  python3 evals/run_evals.py --engine py --base-url http://127.0.0.1:8080/v1/ --model M --json out.json
  python3 evals/run_evals.py --only edit_config_value,fix_failing_test
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TASKS_DIR = os.path.join(HERE, "tasks")
AI_BIN = os.path.join(REPO, "ai")
MOCK_SERVER = os.path.join(REPO, "tests", "mock_llm_server.py")
CHECK_TYPES = ("answer_contains", "file_exists", "file_contains", "tool_used", "max_steps",
               "tool_result_contains")
_STATE_RE = re.compile(r"^\[CURRENT STATE step \d+\] \S+ -> (\w+)")
# Variables that must not leak from the developer's shell into an eval run.
SCRUB = ("CUDA_VISIBLE_DEVICES", "CUDA_PATH", "LLAMA_CTX_SIZE", "LLAMA_N_GPU_LAYERS",
         "LLAMA_MODEL_PATH", "PYTHONPATH")


# ── task loading ─────────────────────────────────────────────────────────────
def load_tasks(only=None, tasks_dir=TASKS_DIR):
    tasks = []
    for path in sorted(glob.glob(os.path.join(tasks_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
        t["_file"] = os.path.basename(path)
        tasks.append(t)
    if only:
        wanted = set(only)
        unknown = wanted - {t["id"] for t in tasks}
        if unknown:
            print("WARNING: unknown task id(s) ignored: %s" % ", ".join(sorted(unknown)),
                  file=sys.stderr)
        tasks = [t for t in tasks if t["id"] in wanted]
    return tasks


def _mock_calls(task):
    """Task `mock.steps` -> (MOCK_TOOL_SEQ items, {call id: expect_error})."""
    seq, expect, n = [], {}, 0

    def call(c):
        nonlocal n
        n += 1
        cid = "call_eval_%d" % n
        expect[cid] = bool(c.get("expect_error"))
        return {"id": cid, "type": "function",
                "function": {"name": c["tool"], "arguments": json.dumps(c["args"])}}

    for step in task.get("mock", {}).get("steps", []):
        seq.append([call(c) for c in step] if isinstance(step, list) else call(step))
    return seq, expect


def mock_tool_seq(task):
    """Task `mock.steps` -> MOCK_TOOL_SEQ items (OpenAI tool_call dicts, string arguments)."""
    return _mock_calls(task)[0]


def tool_result_is_error(content):
    """True for a failed tool result: the C loop's "[CURRENT STATE step N] tool -> error" header,
    "[Command Failed ...]", or an "Error..." result from the Python backend."""
    text = str(content or "").lstrip()
    m = _STATE_RE.match(text)
    if m:
        return m.group(1) == "error"
    if text.startswith("[Command Failed"):
        return True
    if text.startswith("[Command Success"):
        return False
    return text.startswith("Error")


# ── environment ──────────────────────────────────────────────────────────────
def agent_env(home, base_url, model, api_key, engine=None, extra=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("INFER_", "MOCK_")) and k not in SCRUB}
    env.update({
        "HOME": home,
        "INFER_BASE_URL": base_url,
        "INFER_MODEL": model,
        "INFER_API_KEY": api_key,
        "INFER_AUTO_APPROVE": "1",
        "INFER_QUIET": "1",
        "INFER_NO_MCP": "1",
        "BROWSER": ":", "NO_BROWSER": "1",
        # An edit within the same second as the first run, with the same file size, would
        # otherwise reuse a stale __pycache__ entry (fix_failing_test: a - b -> a + b).
        "PYTHONDONTWRITEBYTECODE": "1",
        # The binary's rpath for libremote_harness.so is "." (cwd) and the build user's
        # ~/.local/lib; runs happen in a temp cwd, so point the loader at the checkout.
        "LD_LIBRARY_PATH": REPO + (os.pathsep + os.environ["LD_LIBRARY_PATH"]
                                   if os.environ.get("LD_LIBRARY_PATH") else ""),
    })
    if engine:
        env["INFER_ENGINE"] = engine
    env.update(extra or {})
    return env


def read_env_file(path):
    """`export KEY="value"` lines of ~/.local/share/ai/env -> dict (INFER_* only)."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if "=" not in line or line.startswith("#"):
                    continue
                k, v = line.split("=", 1)
                if k.startswith("INFER_"):
                    out[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def resolve_endpoint(args):
    file_env = read_env_file(os.path.expanduser("~/.local/share/ai/env"))

    def pick(cli, var):
        return cli or os.environ.get(var) or file_env.get(var)

    return (pick(args.base_url, "INFER_BASE_URL"), pick(args.model, "INFER_MODEL"),
            pick(args.api_key, "INFER_API_KEY") or "none")


# ── mock server ──────────────────────────────────────────────────────────────
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockServer:
    def __init__(self, task, capture):
        self.task, self.capture, self.proc = task, capture, None
        self.port = _free_port()

    def __enter__(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MOCK_")}
        env.update(MOCK_CAPTURE=self.capture,
                   MOCK_TOOL_SEQ=json.dumps(mock_tool_seq(self.task)),
                   MOCK_TASK_COMPLETE=self.task.get("mock", {}).get("final", "Done"))
        self.proc = subprocess.Popen([sys.executable, MOCK_SERVER, str(self.port)], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    @property
    def base_url(self):
        return "http://127.0.0.1:%d/v1/" % self.port


# ── running one task ─────────────────────────────────────────────────────────
def _run_ai(cmd, cwd, env, timeout):
    """Run `ai` in its own process group so a timeout kills the whole tree."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        return False, proc.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        out, err = proc.communicate()
        return True, None, out or "", err or ""


def _session(home):
    try:
        with open(os.path.join(home, ".cache", "ai", "sessions", "last.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else None
    except (OSError, ValueError):
        return None


def _tool_names(session):
    names = []
    for m in session or []:
        if isinstance(m, dict) and m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                names.append((tc.get("function") or {}).get("name", ""))
    return names


def _tool_results(session):
    """[(call id, tool name, content)] for every tool message, in order."""
    names, out = {}, []
    for m in session or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                names[tc.get("id")] = (tc.get("function") or {}).get("name", "")
        elif m.get("role") == "tool":
            cid = m.get("tool_call_id")
            out.append((cid, names.get(cid, ""), str(m.get("content") or "")))
    return out


def _mock_error_checks(task, results):
    """--mock only: every scripted call ran, and errored exactly when marked expect_error."""
    _, expect = _mock_calls(task)
    by_id = {cid: (name, content) for cid, name, content in results}
    checks = []
    for cid, want_err in expect.items():
        if cid not in by_id:
            checks.append((False, "scripted tool call %s ran" % cid, "no tool result"))
            continue
        name, content = by_id[cid]
        is_err = tool_result_is_error(content)
        snippet = content[-200:].replace("\n", " | ")
        if is_err and not want_err:
            checks.append((False, "unexpected tool error: %s (%s)" % (name, cid), snippet))
        elif want_err and not is_err:
            checks.append((False, "expected error did not happen: %s (%s)" % (name, cid), snippet))
    if not checks:
        checks.append((True, "scripted tool calls errored only where expected", ""))
    return checks


def _final_answer(session, stdout):
    """The last task_complete summary, else the last assistant text, else stdout."""
    for m in reversed(session or []):
        if not (isinstance(m, dict) and m.get("role") == "assistant"):
            continue
        for tc in reversed(m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            if fn.get("name") == "task_complete":
                try:
                    return str(json.loads(fn.get("arguments") or "{}").get("summary", ""))
                except ValueError:
                    return fn.get("arguments") or ""
        if m.get("content"):
            return str(m["content"])
    return stdout


def _check_path(check, ctx):
    base = ctx["home"] if check.get("in") == "home" else ctx["workdir"]
    return os.path.join(base, check["path"])


def _check_label(check):
    return ("~/" if check.get("in") == "home" else "") + check["path"]


def run_check(check, ctx):
    """-> (ok, label, detail). Raises ValueError for an unknown check type."""
    kind = check.get("type")
    if kind == "answer_contains":
        options = check["any"] if "any" in check else [check["text"]]
        ans = ctx["answer"].lower()
        ok = any(o.lower() in ans for o in options)
        label = "answer contains %s" % " | ".join(repr(o) for o in options)
        detail = ctx["answer"][:160]
    elif kind == "file_exists":
        ok = os.path.exists(_check_path(check, ctx))
        label, detail = "file %s exists" % _check_label(check), ""
    elif kind == "file_contains":
        p = _check_path(check, ctx)
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            content = None
        ok = content is not None and check["text"] in content
        label = "%s contains %r" % (_check_label(check), check["text"])
        detail = "(missing)" if content is None else repr(content[:120])
    elif kind == "tool_used":
        n = ctx["tools"].count(check["name"])
        ok = n >= int(check.get("min", 1))
        label = "%s used >= %d" % (check["name"], int(check.get("min", 1)))
        detail = ", ".join(ctx["tools"])[:160]
    elif kind == "tool_result_contains":
        hits = [c for name, c in ctx.get("tool_results", []) if name == check["tool"]]
        ok = any(check["text"] in c for c in hits)
        label = "%s result contains %r" % (check["tool"], check["text"])
        detail = " || ".join(c[-120:].replace("\n", " | ") for c in hits)[:240] or "(no such tool result)"
    elif kind == "max_steps":
        ok = ctx["steps"] <= int(check["value"])
        label, detail = "steps <= %d" % int(check["value"]), "steps=%d" % ctx["steps"]
    else:
        raise ValueError("unknown check type %r" % kind)
    if check.get("not"):
        ok, label = not ok, "NOT " + label
    return ok, label, detail


def run_task(task, endpoint, engine, keep=False):
    """endpoint: (base_url, model, api_key) or None for --mock."""
    root = tempfile.mkdtemp(prefix="ai_eval_%s_" % task["id"])
    work, home = os.path.join(root, "work"), os.path.join(root, "home")
    os.makedirs(work)
    # ai.c finds its tool backend as ./ai_mcp.py, else ~/.local/bin/ai_mcp.py: link the checkout's
    # shim into the isolated HOME so the workdir holds only the task's own files.
    os.makedirs(os.path.join(home, ".local", "bin"))
    os.symlink(os.path.join(REPO, "ai_mcp.py"), os.path.join(home, ".local", "bin", "ai_mcp.py"))
    for base, files in ((work, task.get("files", {})), (home, task.get("home_files", {}))):
        for rel, content in files.items():
            p = os.path.join(base, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            if rel.endswith(".sh"):
                os.chmod(p, 0o755)
    timeout = int(task.get("timeout", 300 if endpoint else 60))
    cmd = [AI_BIN, "-y", task["prompt"]]
    t0 = time.time()
    capture = os.path.join(root, "requests.jsonl")
    try:
        if endpoint is None:
            with MockServer(task, capture) as srv:
                env = agent_env(home, srv.base_url, "mock", "mock", engine, task.get("env"))
                env["INFER_TOOL_CHOICE"] = "auto"
                timed_out, rc, out, err = _run_ai(cmd, work, env, timeout)
        else:
            env = agent_env(home, *endpoint, engine=engine, extra=task.get("env"))
            timed_out, rc, out, err = _run_ai(cmd, work, env, timeout)
        session = _session(home)
        tools = _tool_names(session)
        steps = sum(1 for m in session or [] if isinstance(m, dict) and m.get("role") == "assistant")
        results = _tool_results(session)
        ctx = {"answer": _final_answer(session, out), "workdir": work, "home": home,
               "tools": tools, "steps": steps,
               "tool_results": [(name, content) for _, name, content in results]}
        checks = []
        if timed_out:
            checks.append((False, "finished within %ds" % timeout, "timeout"))
        if session is None:
            checks.append((False, "session persisted", "exit=%s stderr=%s" % (rc, err[-300:])))
        for c in task["checks"]:
            try:
                checks.append(run_check(c, ctx))
            except (KeyError, ValueError) as e:
                checks.append((False, "check %r" % c.get("type"), "bad check: %r" % e))
        if endpoint is None and session is not None:
            checks.extend(_mock_error_checks(task, results))
        passed = bool(checks) and all(ok for ok, _, _ in checks)
        return {"id": task["id"], "passed": passed, "steps": steps,
                "duration_s": round(time.time() - t0, 2), "tools": tools,
                "tool_errors": [c[-300:] for _, _, c in results if tool_result_is_error(c)],
                "answer": ctx["answer"][:500],
                "checks": [{"ok": bool(ok), "check": label, "detail": str(detail)}
                           for ok, label, detail in checks]}
    finally:
        if keep:
            print("kept %s: %s" % (task["id"], root), file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)


def python_engine_available():
    """True once the checkout has the Python engine (`import agent` works from the repo)."""
    if not os.path.isdir(os.path.join(REPO, "agent")):
        return False
    r = subprocess.run([sys.executable, "-c", "import agent"], cwd=REPO,
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0


# ── reporting ────────────────────────────────────────────────────────────────
def print_table(results):
    w = max([len("task")] + [len(r["id"]) for r in results])
    print("%-*s  %-4s  %5s  %7s  %s" % (w, "task", "res", "steps", "time", "failed checks"))
    print("-" * (w + 40))
    for r in results:
        bad = "; ".join("%s (%s)" % (c["check"], c["detail"][:60])
                        for c in r["checks"] if not c["ok"])
        print("%-*s  %-4s  %5d  %6.1fs  %s" % (w, r["id"], "PASS" if r["passed"] else "FAIL",
                                              r["steps"], r["duration_s"], bad))
    n = sum(r["passed"] for r in results)
    print("-" * (w + 40))
    print("%d/%d passed (%.0f%%)" % (n, len(results), 100.0 * n / max(1, len(results))))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the ai agent eval set.")
    ap.add_argument("--mock", action="store_true",
                    help="offline: scripted mock LLM per task (deterministic, used by CI)")
    ap.add_argument("--engine", choices=("c", "py"), default=None,
                    help="agent engine (sets INFER_ENGINE; default: the binary's default)")
    ap.add_argument("--base-url", help="OpenAI-compatible base URL ending in /v1/")
    ap.add_argument("--model", help="model name")
    ap.add_argument("--api-key", help="API key (default: INFER_API_KEY or 'none')")
    ap.add_argument("--json", help="write the full report to this file")
    ap.add_argument("--only", default="", help="comma-separated task ids")
    ap.add_argument("--tasks-dir", default=TASKS_DIR, help="directory of task *.json files")
    ap.add_argument("--jobs", type=int, default=None,
                    help="tasks run in parallel (default: 4 with --mock, 1 otherwise)")
    ap.add_argument("--min-pass", type=float, default=None,
                    help="exit 1 below this pass %% (default: 100 with --mock, 0 otherwise)")
    ap.add_argument("--keep", action="store_true", help="keep the temp workdirs")
    args = ap.parse_args(argv)

    if not os.access(AI_BIN, os.X_OK):
        print("ERROR: %s not found; run `make` first." % AI_BIN, file=sys.stderr)
        return 2
    if args.engine == "py" and not python_engine_available():
        print("ERROR: --engine py: the Python engine (agent/ package, Task 8) is not in this "
              "checkout yet; use --engine c or omit --engine.", file=sys.stderr)
        return 2
    endpoint = None
    if not args.mock:
        endpoint = resolve_endpoint(args)
        if not endpoint[0] or not endpoint[1]:
            print("ERROR: no endpoint: pass --base-url and --model (or set INFER_BASE_URL/"
                  "INFER_MODEL), or use --mock.", file=sys.stderr)
            return 2
    tasks = load_tasks([x.strip() for x in args.only.split(",") if x.strip()], args.tasks_dir)
    if not tasks:
        print("ERROR: no tasks selected", file=sys.stderr)
        return 2
    jobs = args.jobs or (4 if args.mock else 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(lambda t: run_task(t, endpoint, args.engine, args.keep), tasks))

    print_table(results)
    n = sum(r["passed"] for r in results)
    report = {"mode": "mock" if args.mock else "live", "engine": args.engine or "default",
              "model": "mock" if args.mock else endpoint[1], "passed": n, "total": len(results),
              "results": results}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
            f.write("\n")
    min_pass = args.min_pass if args.min_pass is not None else (100.0 if args.mock else 0.0)
    return 0 if 100.0 * n / len(results) >= min_pass else 1


if __name__ == "__main__":
    sys.exit(main())
