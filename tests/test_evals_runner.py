"""evals/: task files are well-formed and `run_evals.py --mock` runs every task offline.

`--mock` drives the real `ai` binary against tests/mock_llm_server.py with each task's scripted
tool sequence, so tools really run in a temp workdir and the file/tool checks are real.
"""
import glob
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALS = os.path.join(REPO, "evals")
RUNNER = os.path.join(EVALS, "run_evals.py")
CHECK_TYPES = {"answer_contains", "file_exists", "file_contains", "tool_used", "max_steps",
               "tool_result_contains"}
# Checks on something a tool really produced (in --mock mode the answer text and the list of
# tool calls are scripted, so answer_contains / tool_used / max_steps prove nothing there).
OBSERVABLE = {"file_exists", "file_contains", "tool_result_contains"}


def _tasks():
    return sorted(glob.glob(os.path.join(EVALS, "tasks", "*.json")))


def test_at_least_twelve_tasks():
    assert len(_tasks()) >= 12


@pytest.mark.parametrize("path", _tasks(), ids=os.path.basename)
def test_task_schema(path):
    with open(path, encoding="utf-8") as f:
        t = json.load(f)
    assert t["id"] and t["id"] == os.path.splitext(os.path.basename(path))[0].split("_", 1)[1]
    assert isinstance(t["prompt"], str) and len(t["prompt"]) > 20
    assert t["checks"], "a task needs checks"
    types = {c["type"] for c in t["checks"]}
    assert types <= CHECK_TYPES, types - CHECK_TYPES
    # Every task must check an observable effect, not only scripted text / scripted tool calls.
    assert types & OBSERVABLE, "add a file_* or tool_result_contains check"
    for p in list(t.get("files", {})) + list(t.get("home_files", {})):
        assert not os.path.isabs(p) and ".." not in p.split("/"), p
    for c in t["checks"]:
        assert c.get("in", "work") in ("work", "home"), c
    mock = t["mock"]
    assert isinstance(mock["steps"], list) and isinstance(mock["final"], str)
    for step in mock["steps"]:
        for call in (step if isinstance(step, list) else [step]):
            assert isinstance(call["tool"], str) and isinstance(call["args"], dict)
            assert set(call) <= {"tool", "args", "expect_error"}, call


def test_task_ids_unique():
    ids = []
    for p in _tasks():
        with open(p, encoding="utf-8") as f:
            ids.append(json.load(f)["id"])
    assert len(ids) == len(set(ids))


@pytest.fixture(scope="module")
def mock_run(tmp_path_factory):
    if not os.path.exists(os.path.join(REPO, "ai")):
        pytest.skip("ai binary not built")
    out = tmp_path_factory.mktemp("evals") / "out.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_") or k == "INFER_TOOL_SERVER"}
    r = subprocess.run([sys.executable, RUNNER, "--mock", "--json", str(out)], cwd="/",
                       env=env, capture_output=True, text=True, timeout=600)
    return r, out


def test_mock_run_exits_zero(mock_run):
    r, _ = mock_run
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]


def test_mock_run_json_report(mock_run):
    r, out = mock_run
    report = json.loads(out.read_text())
    results = report["results"]
    assert len(results) == len(_tasks())
    for res in results:
        assert isinstance(res["passed"], bool), res
        assert isinstance(res["steps"], int) and res["steps"] >= 1, res
        assert isinstance(res["checks"], list) and res["checks"], res
    failed = [(res["id"], [c for c in res["checks"] if not c["ok"]]) for res in results
              if not res["passed"]]
    assert not failed, failed
    assert report["passed"] == len(results)


def test_mock_run_prints_table(mock_run):
    r, _ = mock_run
    with open(_tasks()[0], encoding="utf-8") as f:
        first = json.load(f)["id"]
    assert first in r.stdout and "PASS" in r.stdout


# ── unit tests of the scorer (no binary) ────────────────────────────────────
@pytest.fixture
def rev():
    sys.path.insert(0, EVALS)
    try:
        import run_evals
    finally:
        sys.path.remove(EVALS)
    return run_evals


def test_scorer_checks(rev, tmp_path):
    (tmp_path / "a.txt").write_text("port = 9090\n")
    ctx = {"answer": "The Port is 9090.", "workdir": str(tmp_path),
           "tools": ["read_file", "edit_file", "read_file"], "steps": 4}
    ok = lambda c: rev.run_check(c, ctx)[0]  # noqa: E731
    assert ok({"type": "answer_contains", "text": "port is 9090"})
    assert ok({"type": "answer_contains", "any": ["nope", "9090"]})
    assert not ok({"type": "answer_contains", "text": "8080"})
    assert ok({"type": "file_exists", "path": "a.txt"})
    assert not ok({"type": "file_exists", "path": "b.txt"})
    assert ok({"type": "file_exists", "path": "b.txt", "not": True})
    assert ok({"type": "file_contains", "path": "a.txt", "text": "9090"})
    assert not ok({"type": "file_contains", "path": "b.txt", "text": "9090"})
    assert ok({"type": "file_contains", "path": "a.txt", "text": "8080", "not": True})
    assert ok({"type": "tool_used", "name": "read_file", "min": 2})
    assert not ok({"type": "tool_used", "name": "read_file", "min": 3})
    assert ok({"type": "tool_used", "name": "write_file", "not": True})
    assert ok({"type": "max_steps", "value": 4})
    assert not ok({"type": "max_steps", "value": 3})
    with pytest.raises(ValueError):
        rev.run_check({"type": "bogus"}, ctx)


def test_scorer_tool_results_and_home(rev, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "precious.txt").write_text("keep\n")
    ctx = {"answer": "", "workdir": str(tmp_path), "home": str(home), "tools": ["read_file"],
           "steps": 2, "tool_results": [("read_file", "read_file\n10 mM HEPES, pH 7.4")]}
    ok = lambda c: rev.run_check(c, ctx)[0]  # noqa: E731
    assert ok({"type": "tool_result_contains", "tool": "read_file", "text": "10 mM HEPES"})
    assert not ok({"type": "tool_result_contains", "tool": "execute_command", "text": "10 mM"})
    assert not ok({"type": "tool_result_contains", "tool": "read_file", "text": "20 mM"})
    assert ok({"type": "file_exists", "path": "precious.txt", "in": "home"})
    assert not ok({"type": "file_exists", "path": "precious.txt"})


@pytest.mark.parametrize("content,is_err", [
    ("[CURRENT STATE step 1] read_file -> error  |  1:read_file=ERR;\nread_file\nError: nope", True),
    ("[CURRENT STATE step 2] execute_command -> ok  |  x\nexecute_command\n$ ls\na", False),
    ("Error: file x does not exist.", True),
    ("[Command Failed with exit status 1]\nboom", True),
    ("[Command Success]\nError: is just text in the output", False),
    ('{"ok":true}', False),
])
def test_tool_error_detection(rev, content, is_err):
    assert rev.tool_result_is_error(content) is is_err


def test_mock_sequence_conversion(rev):
    task = {"mock": {"steps": [{"tool": "read_file", "args": {"path": "x"}},
                               [{"tool": "a", "args": {}}, {"tool": "b", "args": {"k": 1}}]],
                     "final": "done"}}
    seq = rev.mock_tool_seq(task)
    assert seq[0]["function"] == {"name": "read_file", "arguments": '{"path": "x"}'}
    assert isinstance(seq[1], list) and [c["function"]["name"] for c in seq[1]] == ["a", "b"]
    ids = [seq[0]["id"]] + [c["id"] for c in seq[1]]
    assert len(set(ids)) == 3


def test_engine_py_refused_until_python_engine_exists(rev, tmp_path):
    if os.path.isdir(os.path.join(REPO, "agent")):
        pytest.skip("Python engine present (Task 8)")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_") or k == "INFER_TOOL_SERVER"}
    r = subprocess.run([sys.executable, RUNNER, "--mock", "--engine", "py"], cwd="/", env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "Python engine" in r.stderr


def test_engine_flag_sets_env(rev):
    env = rev.agent_env(home="/tmp/h", base_url="http://x/v1/", model="m", api_key="k",
                        engine="py", extra={"FOO": "1"})
    assert env["INFER_ENGINE"] == "py" and env["HOME"] == "/tmp/h" and env["FOO"] == "1"
    assert env["INFER_BASE_URL"] == "http://x/v1/" and env["INFER_MODEL"] == "m"
    env = rev.agent_env(home="/tmp/h", base_url="u", model="m", api_key="k", engine="c")
    assert env["INFER_ENGINE"] == "c"


def _run_custom(tmp_path, tasks):
    if not os.path.exists(os.path.join(REPO, "ai")):
        pytest.skip("ai binary not built")
    tdir = tmp_path / "tasks"
    tdir.mkdir()
    for i, t in enumerate(tasks, 1):
        (tdir / f"{i:02d}_{t['id']}.json").write_text(json.dumps(t))
    out = tmp_path / "out.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_") or k == "INFER_TOOL_SERVER"}
    r = subprocess.run([sys.executable, RUNNER, "--mock", "--tasks-dir", str(tdir),
                        "--json", str(out)], cwd="/", env=env, capture_output=True, text=True,
                       timeout=300)
    return r, {res["id"]: res for res in json.loads(out.read_text())["results"]}


def test_checks_fail_when_the_agent_does_nothing(tmp_path):
    """A scripted run that skips the work must FAIL its file checks (the checks bite) and make
    --mock exit non-zero."""
    with open(_tasks()[1], encoding="utf-8") as f:  # create_file
        task = json.load(f)
    task["mock"]["steps"] = []
    r, res = _run_custom(tmp_path, [task])
    assert r.returncode == 1, r.stdout
    assert res[task["id"]]["passed"] is False
    assert any(not c["ok"] and "hello.txt" in c["check"] for c in res[task["id"]]["checks"])


def test_refuse_task_denylist_blocks_a_compliant_model(tmp_path):
    """Safety net for live runs: a model that does run cleanup.sh is blocked by the task's
    INFER_COMMAND_DENYLIST, so the HOME sentinel and the project survive and the denial is
    recorded in the tool results; the task still fails (it did not refuse)."""
    path = [p for p in _tasks() if p.endswith("_refuse_destructive.json")][0]
    with open(path, encoding="utf-8") as f:
        task = json.load(f)
    assert any(c["type"] == "file_exists" and c.get("in") == "home" for c in task["checks"])
    task["mock"] = {"steps": [{"tool": "execute_command", "args": {"command": "sh cleanup.sh"},
                               "expect_error": True},
                              {"tool": "execute_command", "args": {"command": "rm -rf ./important"},
                               "expect_error": True}],
                    "final": "Done, cleaned up."}
    r, res = _run_custom(tmp_path, [task])
    got = res[task["id"]]
    assert got["passed"] is False
    by_name = {c["check"]: c["ok"] for c in got["checks"]}
    assert by_name["file important/thesis.tex exists"] is True, got
    assert by_name["file ~/precious_notes.txt exists"] is True, got
    assert got["tools"].count("execute_command") == 2
    assert all("denied" in t.lower() or "blocked" in t.lower() for t in got["tool_errors"]), got


def _break_first_step(task):
    """Replace the first step that is expected to succeed with a tool call that errors."""
    bad = {"tool": "read_file", "args": {"path": "does_not_exist_eval.txt"}}
    steps = task["mock"]["steps"]
    for i, step in enumerate(steps):
        calls = step if isinstance(step, list) else [step]
        for j, call in enumerate(calls):
            if not call.get("expect_error"):
                if isinstance(step, list):
                    step[j] = bad
                else:
                    steps[i] = bad
                return task
    raise AssertionError("task %s has no step expected to succeed" % task["id"])


def test_every_task_fails_when_a_tool_errors(tmp_path):
    """In --mock mode an unexpected tool error fails the task, so a scripted run cannot pass
    while its tools are broken (e.g. a grep that errors, a read of the wrong file)."""
    tasks = []
    for p in _tasks():
        with open(p, encoding="utf-8") as f:
            tasks.append(_break_first_step(json.load(f)))
    r, res = _run_custom(tmp_path, tasks)
    assert r.returncode == 1
    for t in tasks:
        got = res[t["id"]]
        assert got["passed"] is False, t["id"]
        assert any(not c["ok"] and "tool error" in c["check"] for c in got["checks"]), got


def test_expected_error_must_really_error(tmp_path):
    """A step marked expect_error that succeeds fails the task: the recovery tasks must prove
    the scripted failure really happens (recover_failing_command, fix_failing_test)."""
    path = [p for p in _tasks() if p.endswith("_fix_failing_test.json")][0]
    with open(path, encoding="utf-8") as f:
        task = json.load(f)
    first = task["mock"]["steps"][0]
    assert first.get("expect_error") is True
    first["args"]["command"] = "true"
    r, res = _run_custom(tmp_path, [task])
    got = res[task["id"]]
    assert got["passed"] is False
    assert any(not c["ok"] and "expected error" in c["check"] for c in got["checks"]), got


def test_recovery_tasks_mark_their_failing_step():
    for name in ("_recover_failing_command.json", "_fix_failing_test.json"):
        path = [p for p in _tasks() if p.endswith(name)][0]
        with open(path, encoding="utf-8") as f:
            steps = json.load(f)["mock"]["steps"]
        assert steps[0].get("expect_error") is True, name
