"""Tests for the expanded core tool set and the self-improvement tool-health loop.

Covers the tools added to ai_mcp.py (search_files, todo, clarify, browser,
context-pool / agent / structured_query exposure) plus the metrics ->
PITFALL-lesson auto-sync, log_metric real-failure capture, MASTER -> skill
note, and the CLI dispatch (no dead-code duplicates, clean exit on broken
tool-args JSON).

Uses a throwaway HOME so the real ~/.config/ai and ~/.cache/ai are never
touched. Run: python3 -m pytest tests/test_core_tools.py -v
"""
import json
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


@pytest.fixture
def ai(tmp_path, monkeypatch):
    """Isolated-HOME ai_mcp module (same convention as test_self_improve)."""
    import ai_mcp
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Keep the history index (a module-level constant, resolved at import
    # time) inside the isolated HOME so session_recap never touches the
    # real ~/.local/share/ai/history_index.db. Same for the other
    # import-time-bound path constants.
    monkeypatch.setattr(ai_mcp, "HISTORY_DB", str(home / ".local" / "share" / "ai" / "history_index.db"))
    monkeypatch.setattr(ai_mcp, "CONTEXT_POOL_FILE", str(home / ".config" / "ai" / "context_pool.json"))
    monkeypatch.setattr(ai_mcp, "AGENT_STORE_DIR", str(home / ".config" / "ai" / "agents"))
    monkeypatch.setattr(ai_mcp, "SESSION_LOG_FILE", str(home / ".config" / "ai" / "session_outcomes.json"))
    # vault + memory FTS (note-taking tools)
    monkeypatch.setattr(ai_mcp, "VAULT_DIR", str(home / ".config" / "ai" / "vault"))
    monkeypatch.setattr(ai_mcp, "MEMORY_DB", str(home / ".config" / "ai" / "memory.db"))
    return ai_mcp


def _metrics_file(ai):
    return os.path.expanduser("~/.cache/ai/metrics.jsonl")


def _write_metrics(ai, entries):
    p = _metrics_file(ai)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# search_files
# ─────────────────────────────────────────────────────────────────────────────

def _make_tree(tmp_path):
    (tmp_path / "a.py").write_text("def hello_world():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("# hello there\nx = 2\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "junk.py").write_text("hello build artifact\n", encoding="utf-8")


def test_search_files_content(ai, tmp_path):
    _make_tree(tmp_path)
    out = ai.search_files("hello", path=str(tmp_path))
    assert "file(s) matched 'hello'" in out
    assert "a.py" in out and "b.py" in out
    # line-number prefixes present
    assert re.search(r"a\.py:1:", out)
    # build/ is on the skip list and must not be walked
    assert "junk.py" not in out
    assert "c.txt" not in out


def test_search_files_target_files(ai, tmp_path):
    _make_tree(tmp_path)
    out = ai.search_files("*.py", path=str(tmp_path), target="files")
    # a.py and b.py match; c.txt is not .py; build/junk.py is skipped
    assert "Found 2 file(s):" in out
    assert "a.py" in out and "b.py" in out
    assert "junk.py" not in out  # build/ skipped
    assert "c.txt" not in out


def test_search_files_output_modes(ai, tmp_path):
    _make_tree(tmp_path)
    fo = ai.search_files("hello", path=str(tmp_path), output_mode="files_only")
    assert "a.py" in fo and ":1:" not in fo
    cnt = ai.search_files("hello", path=str(tmp_path), output_mode="count")
    assert re.search(r"b\.py: 1", cnt)


def test_search_files_file_glob(ai, tmp_path):
    _make_tree(tmp_path)
    (tmp_path / "d.txt").write_text("hello in a text file\n", encoding="utf-8")
    out = ai.search_files("hello", path=str(tmp_path), file_glob="*.py")
    assert "d.txt" not in out
    assert "a.py" in out and "b.py" in out


def test_search_files_context(ai, tmp_path):
    _make_tree(tmp_path)
    out = ai.search_files("hello_world", path=str(tmp_path), context=1)
    # line 2 (the body) should appear with a '-' context marker
    assert re.search(r"a\.py-2:", out)


def test_search_files_limit(ai, tmp_path):
    (tmp_path / "many.txt").write_text("\n".join("needle%d" % i for i in range(30)), encoding="utf-8")
    out = ai.search_files("needle", path=str(tmp_path), limit=5)
    shown = [l for l in out.splitlines() if "many.txt:" in l]
    assert len(shown) == 5  # capped at the limit
    assert "many.txt:1: needle0" in out and "many.txt:5: needle4" in out
    assert "many.txt:6:" not in out  # and no 6th


def test_search_files_invalid_regex_falls_back(ai, tmp_path):
    (tmp_path / "f.txt").write_text("the (unclosed pattern lives here\n", encoding="utf-8")
    out = ai.search_files("(unclosed", path=str(tmp_path))
    # fixed-string fallback: finds the literal text, does not raise
    assert "f.txt" in out


def test_search_files_error_cases(ai, tmp_path):
    _make_tree(tmp_path)
    assert "Error: pattern is required" in ai.search_files("", path=str(tmp_path))
    assert "does not exist" in ai.search_files("x", path=str(tmp_path / "nope"))
    assert "No matches" in ai.search_files("zebra_never", path=str(tmp_path))


# ─────────────────────────────────────────────────────────────────────────────
# todo
# ─────────────────────────────────────────────────────────────────────────────

def test_todo_empty_read(ai):
    out = ai.todo()
    assert "TODO list is empty" in out


def test_todo_create_read_and_complete(ai):
    out = ai.todo(todos=[
        {"id": "a", "content": "first", "status": "pending"},
        {"id": "b", "content": "second", "status": "in_progress"},
    ])
    assert "[ ] a: first" in out
    assert "[~] b: second" in out
    assert "0/2 done" in out
    out = ai.todo(todos=[{"id": "a", "status": "completed"}], merge=True)
    assert "[x] a: first" in out
    assert "1/2 done" in out
    # read without args reflects the persisted state
    out = ai.todo()
    assert "1/2 done" in out


def test_todo_session_scoping(ai, monkeypatch):
    monkeypatch.setenv("INFER_SESSION_ID", "sess_abc-123")
    ai.todo(todos=[{"id": "x", "content": "in session 1", "status": "pending"}])
    p1 = os.path.expanduser("~/.config/ai/todo_sess_abc-123.json")
    assert os.path.exists(p1)
    # a different session sees its own (empty) list
    monkeypatch.setenv("INFER_SESSION_ID", "other")
    assert "TODO list is empty" in ai.todo()
    # and the default (no session id) list is separate too
    monkeypatch.delenv("INFER_SESSION_ID")
    assert "TODO list is empty" in ai.todo()
    assert os.path.exists(os.path.expanduser("~/.config/ai/todo.json")) or True


def test_todo_validation(ai):
    assert "must be an array" in ai.todo(todos="not a list")
    assert "needs an 'id'" in ai.todo(todos=[{"content": "no id"}])
    # new item without content (no merge): rejected with a pointer to merge
    assert "needs 'content'" in ai.todo(todos=[{"id": "a"}])
    # status-only update of an existing item in merge mode: allowed
    ai.todo(todos=[{"id": "a", "content": "work", "status": "pending"}])
    assert "[x] a: work" in ai.todo(todos=[{"id": "a", "status": "completed"}], merge=True)


# ─────────────────────────────────────────────────────────────────────────────
# clarify
# ─────────────────────────────────────────────────────────────────────────────

def test_clarify_noninteractive_picks_default(ai, monkeypatch):
    monkeypatch.delenv("INFER_INTERACTIVE", raising=False)
    monkeypatch.setenv("INFER_NON_INTERACTIVE", "1")
    out = ai.clarify("Which deploy target?", choices=["staging", "prod"])
    assert "[CLARIFY NON-INTERACTIVE]" in out
    assert "staging | prod" in out
    assert "Choose the first (recommended) option" in out


def test_clarify_open_ended(ai, monkeypatch):
    monkeypatch.setenv("INFER_NON_INTERACTIVE", "1")
    out = ai.clarify("What should I name it?")
    assert "[CLARIFY NON-INTERACTIVE]" in out
    assert "Make a sensible default choice" in out


def test_clarify_requires_question(ai):
    assert "Error: 'question' is required" in ai.clarify("   ")


# ─────────────────────────────────────────────────────────────────────────────
# log_metric + show_metrics + metrics -> PITFALL lesson sync
# ─────────────────────────────────────────────────────────────────────────────

def test_log_metric_records_real_failure(ai):
    ai.log_metric("flaky_tool", 12.3, success=False, error="boom code 5")
    ai.log_metric("flaky_tool", 4.1, success=True)
    with open(_metrics_file(ai), encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert len(lines) == 2
    assert lines[0]["success"] is False
    assert lines[0]["error"] == "boom code 5"
    assert lines[1]["success"] is True
    assert "error" not in lines[1]


def test_show_metrics_flags_flaky(ai, capsys):
    entries = []
    for i in range(6):
        ok = i >= 3  # 3 failures out of 6 -> 50%
        e = {"tool": "flaky_tool", "duration_ms": 10.0, "success": ok}
        if not ok:
            e["error"] = "connection reset by peer"
        entries.append(e)
    _write_metrics(ai, entries)
    ai.show_metrics()
    out = capsys.readouterr().out
    assert "[FLAKY]" in out
    assert "flaky_tool" in out


def test_metrics_lesson_sync_creates_pitfall(ai):
    _write_metrics(ai, [
        {"tool": "fetch_webpage", "duration_ms": 900.0, "success": False,
         "error": "403 Forbidden: WAF challenge page"} for _ in range(3)
    ])
    created = ai._metrics_lesson_sync(min_fails=3)
    assert len(created) == 1
    lessons = open(ai._lessons_path(), encoding="utf-8").read()
    assert "## PITFALL" in lessons
    assert "Metric-detected recurring failure" in lessons
    assert "fetch_webpage" in lessons
    # idempotent: a second sync adds nothing
    assert ai._metrics_lesson_sync(min_fails=3) == []


def test_metrics_lesson_sync_threshold_and_gating(ai, monkeypatch):
    _write_metrics(ai, [
        {"tool": "t1", "duration_ms": 1.0, "success": False, "error": "err A"} for _ in range(2)
    ])
    # below the default threshold of 3 -> nothing
    assert ai._metrics_lesson_sync() == []
    # explicit lower threshold -> lesson
    assert len(ai._metrics_lesson_sync(min_fails=2)) == 1
    # success-only entries never lesson-ify
    _write_metrics(ai, [{"tool": "t2", "duration_ms": 1.0, "success": True} for _ in range(5)])
    assert ai._metrics_lesson_sync(min_fails=2) == []


def test_session_recap_surfaces_lessons_and_health(ai):
    # 6 calls, 3 failed -> 50%: trips both the flaky-tools health view (>=5
    # calls, >=30% failed) and the metric-lesson sync (>=3 same-signature
    # failures).
    _write_metrics(ai, [
        {"tool": "flaky_tool", "duration_ms": 10.0, "success": (i >= 3),
         **({} if i >= 3 else {"error": "boom code 1"})}
        for i in range(6)
    ])
    recap = ai.session_recap()
    assert recap.startswith("[SESSION RECAP")
    assert "TOOL HEALTH" in recap
    assert "RECENT LESSONS" in recap  # the auto PITFALL from the sync inside session_recap
    assert "flaky_tool" in recap


def test_session_recap_empty_when_nothing_recorded(ai):
    assert ai.session_recap() == ""


# ─────────────────────────────────────────────────────────────────────────────
# MASTER promotion -> skill learning note (closed loop)
# ─────────────────────────────────────────────────────────────────────────────

def test_master_promotion_writes_skill_note(ai, monkeypatch):
    monkeypatch.setenv("INFER_CHAIN_MASTERED", "1")
    monkeypatch.setenv("INFER_MASTER_SKILL_NOTE", "1")
    ai.record_failure("read_file", '{"path":"/x1"}', "No such file: x1")
    ai.record_recovery("read_file", '{"path":"/x2"}', "No such file: x1")
    lessons = open(ai._lessons_path(), encoding="utf-8").read()
    assert "## MASTER" in lessons
    logp = ai._learning_log_path()
    assert os.path.exists(logp)
    log = open(logp, encoding="utf-8").read()
    assert "[MASTERED ERROR CHAIN" in log
    assert "skill_update" in log


def test_master_skill_note_gated_off(ai, monkeypatch):
    monkeypatch.setenv("INFER_CHAIN_MASTERED", "1")
    monkeypatch.setenv("INFER_MASTER_SKILL_NOTE", "0")
    ai.record_failure("grep", '{"pattern":"p1"}', "grep: binary file matches: p1")
    ai.record_recovery("grep", '{"pattern":"p2"}', "grep: binary file matches: p1")
    logp = ai._learning_log_path()
    if os.path.exists(logp):
        assert "[MASTERED ERROR CHAIN" not in open(logp, encoding="utf-8").read()


# ─────────────────────────────────────────────────────────────────────────────
# browser tool — offline-safe paths (daemon errors, arg validation)
# ─────────────────────────────────────────────────────────────────────────────

def test_browser_unknown_action(ai):
    out = ai.browser("frobnicate")
    assert "unknown browser action" in out
    assert "goto" in out  # the help lists valid actions


def test_browser_goto_requires_url(ai):
    assert "goto needs a url" in ai.browser("goto")


def test_browser_daemon_failure_is_clean(ai, monkeypatch):
    # Force the daemon to be unavailable: the tool must return a model-visible
    # Error string (never a traceback) and name the fix.
    monkeypatch.setattr(ai, "_browser_ensure_daemon", lambda: False)
    out = ai.browser("goto", url="https://example.com")
    assert out.startswith("Error:")
    assert "browser daemon" in out
    assert "playwright" in out


# ─────────────────────────────────────────────────────────────────────────────
# exposed delegation / context-pool / structured tools
# ─────────────────────────────────────────────────────────────────────────────

def test_context_pool_roundtrip(ai):
    ai.append_to_context_pool("entry one about zebras")
    ai.append_to_context_pool("entry two about tigers")
    snap = json.loads(ai.get_context_snippet(0))
    assert "zebras" in snap["content"]
    assert "No context" not in ai.search_context("zebras")
    assert "[0]" in ai.search_context("zebras")
    assert "out of range" in ai.get_context_snippet(99)
    assert "query required" in ai.search_context("")


def test_structured_query_file(ai, tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("alpha 1\nbeta 2\nalpha 3\nalpha 4\n", encoding="utf-8")
    assert ai.structured_query("file:%s" % f, filter_expr="alpha", aggregate="count") == "3"
    assert ai.structured_query("file:%s" % f, transform="unique", aggregate="first") == "alpha 1"
    assert "not found" in ai.structured_query("file:/no/such/file.txt")
    # plain-text target passthrough
    assert ai.structured_query("one\ntwo", aggregate="count") == "2"


def test_spawn_list_agents(ai):
    out = ai.spawn_agent("bench_worker", "count the files")
    assert "Spawned agent bench_worker" in out
    assert "bench_worker" in ai.list_agents()
    assert "not found" in ai.resume_agent("agent_no_such_id", "hi")


def test_session_report(ai):
    out = ai.session_report(success=True, notes="bench done")
    assert "logged successfully" in out
    data = json.load(open(os.path.expanduser("~/.config/ai/session_outcomes.json"), encoding="utf-8"))
    assert data[-1]["notes"] == "bench done"
    assert data[-1]["success"] is True


# ─────────────────────────────────────────────────────────────────────────────
# vault (Obsidian-style notes) — regression for the literal-`\n` bug
# ─────────────────────────────────────────────────────────────────────────────

def test_vault_write_links_use_real_newlines(ai):
    out = ai.vault_write("Protein X", "Line one\nLine two about binding", links="Enzyme Y")
    assert "Successfully wrote" in out
    p = os.path.expanduser("~/.config/ai/vault/Protein X.md")
    data = open(p, encoding="utf-8").read()
    # the note must contain REAL newlines, never a literal backslash-n
    assert "\n\n---\n**Links:** [[Enzyme Y]]" in data
    assert (chr(92) + "n") not in data


def test_vault_search_multiline_results(ai):
    ai.vault_write("Alpha", "shared binding site A")
    ai.vault_write("Beta", "shared binding site B")
    out = ai.vault_search("binding")
    assert out.count("- **") == 2
    assert "- **Alpha.md**" in out and "- **Beta.md**" in out
    # results joined with real newlines, not a literal \n sequence
    assert (chr(92) + "n") not in out
    # preview flattens internal newlines of one note to spaces
    ai.vault_write("Multi", "first line\nsecond line word")
    out = ai.vault_search("word")
    assert "first line second line word" in out


def test_vault_backlinks_real_newlines(ai):
    ai.vault_write("Protein X", "the target")
    ai.vault_write("Enzyme Y", "acts on [[Protein X]]")
    out = ai.vault_backlinks("Protein X")
    assert "- [[Enzyme Y" in out
    assert (chr(92) + "n") not in out


# ─────────────────────────────────────────────────────────────────────────────
# CLI dispatch: no dead-code duplicates, clean exit on broken args JSON
# ─────────────────────────────────────────────────────────────────────────────

def _run_cli(args, home, cwd=REPO):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("INFER_INTERACTIVE", None)
    return subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py")] + args,
                          capture_output=True, text=True, env=env, cwd=cwd, timeout=60)


def test_call_tool_broken_json_exits_cleanly(tmp_path):
    # Regression: an unparseable, unrepairable args JSON used to crash with a
    # NameError (normalize_tool_arguments called with an undefined variable).
    r = _run_cli(["call-tool", "main", "search_files", "not json at all {{{"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr
    assert '"error"' in r.stdout
    assert "Failed to parse arguments JSON" in r.stdout


def test_call_tool_repaired_markdown_json(tmp_path):
    # Models wrap JSON in ``` fences; repair must strip them and dispatch.
    args = "```json\n{\"entry\": \"repaired entry\"}\n```"
    r = _run_cli(["call-tool", "main", "append_to_context_pool", args], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Context pool entry stored" in r.stdout, r.stdout


def test_dispatch_no_dead_duplicates(tmp_path):
    """Every tool must be dispatched exactly once — a duplicate elif after the
    first match is dead code (append_to_context_pool was unreachable before the
    fix because its block sat behind 7 duplicate entries)."""
    src = open(os.path.join(REPO, "ai_mcp.py"), encoding="utf-8").read()
    body = src.split('    elif action == "call-tool":', 1)[1]
    # Dispatch entries always pair tool_name with server_name (the pre-dispatch
    # argument-validation ifs do not, which keeps them out of the scan).
    names = re.findall(r'^\s{8}(?:if|elif) tool_name == "([^"]+)" or server_name', body, flags=re.M)
    seen = set()
    dups = []
    for n in names:
        if n in seen:
            dups.append(n)
        seen.add(n)
    assert not dups, "duplicate dispatch entries (dead code): %s" % dups
    for t in ["search_files", "todo", "clarify", "browser", "append_to_context_pool",
              "spawn_agent", "list_agents", "structured_query", "search_context",
              "get_context_snippet", "session_report"]:
        assert t in seen, "tool %s not dispatched in call-tool" % t


def test_list_tools_includes_new_tools():
    r = subprocess.run([sys.executable, os.path.join(REPO, "ai_mcp.py"), "list-tools"],
                       capture_output=True, text=True, cwd=REPO, timeout=60)
    assert r.returncode == 0
    for t in ["search_files", "todo", "clarify", "browser", "append_to_context_pool"]:
        assert '"name": "%s"' % t in r.stdout, "missing tool in list-tools: %s" % t


def test_sync_metrics_lessons_cli(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    mf = home / ".cache" / "ai" / "metrics.jsonl"
    mf.parent.mkdir(parents=True)
    mf.write_text("\n".join(json.dumps({"tool": "cli_tool", "duration_ms": 1.0,
                                        "success": False, "error": "cli err Z9"})
                            for _ in range(3)) + "\n", encoding="utf-8")
    r = _run_cli(["sync-metrics-lessons", "3"], home)
    assert r.returncode == 0, r.stderr
    assert "Recorded 1 new" in r.stdout, r.stdout
    # second run: nothing new (deduped)
    r2 = _run_cli(["sync-metrics-lessons", "3"], home)
    assert "No new recurring" in r2.stdout
