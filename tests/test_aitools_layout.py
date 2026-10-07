"""Layout + behaviour-preservation guards for the `aitools/` package split of ai_mcp.py.

The golden files under tests/golden/ were captured from the single-file ai_mcp.py *before*
the split, so these tests prove the split changed no observable output:
  - list_tools.json / list_tools_core.json: `ai_mcp.py list-tools` bytes (full + core profile)
  - cli_cases.json: stdout + exit code of deterministic `call-tool` calls and other subcommands

Regenerate (only when a behaviour change is intended): AITOOLS_REGEN_GOLDEN=1 pytest tests/test_aitools_layout.py
Every subprocess runs with an isolated HOME, cwd outside the repo and INFER_NO_MCP=1.
"""
import json
import os
import shutil
import stat
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.path.join(REPO, "ai_mcp.py")
GOLDEN = os.path.join(REPO, "tests", "golden")
REGEN = os.environ.get("AITOOLS_REGEN_GOLDEN") == "1"
sys.path.insert(0, REPO)

SAMPLE_MD = """# Title

Some **bold**, *italic* and `code` with $\\alpha^2 + \\beta_1$.

1. first
2. second
- bullet

```python
def f(x):
    return x + 1  # comment
```

| a | b |
|---|--:|
| 1 | 22 |
"""

# (name, argv after ai_mcp.py). Cases run in a fresh workdir holding fixture/ (see _workdir).
CLI_CASES = [
    ("think", ["call-tool", "think", "think", '{"reasoning": "x"}']),
    ("task_complete", ["call-tool", "task_complete", "task_complete", '{"summary": "done"}']),
    ("read_file", ["call-tool", "read_file", "read_file", '{"path": "fixture/hello.py"}']),
    ("read_file_range", ["call-tool", "read_file", "read_file",
                         '{"path": "fixture/hello.py", "start_line": 2, "end_line": 3}']),
    ("read_file_missing", ["call-tool", "read_file", "read_file", '{"path": "fixture/nope.txt"}']),
    ("read_file_alias_and_string_args", ["call-tool", "read_file", "read_file", '"fixture/notes.md"']),
    ("list_directory", ["call-tool", "list_directory", "list_directory", '{"path": "fixture"}']),
    ("server_name_alias", ["call-tool", "list_directory", "whatever", '{"path": "fixture"}']),
    ("unknown_tool", ["call-tool", "nope_server", "nope_tool", "{}"]),
    ("malformed_args", ["call-tool", "read_file", "read_file", '{"path": ']),
    ("repaired_args", ["call-tool", "list_directory", "list_directory", "```json\n{\"path\": \"fixture\"}\n```"]),
    ("missing_required", ["call-tool", "write_file", "write_file", '{"path": "a.txt"}']),
    ("bad_path_type", ["call-tool", "read_file", "read_file", '{"path": 12345}']),
    ("bad_query_type", ["call-tool", "web_search", "web_search", '{"query": 5}']),
    ("empty_tool_name", ["call-tool", "x", "", "{}"]),
    ("write_file", ["call-tool", "write_file", "write_file", '{"path": "out.txt", "content": "hi\\n"}']),
    ("edit_file", ["call-tool", "edit_file", "edit_file",
                   '{"path": "fixture/hello.py", "search_content": "return 1", "replace_content": "return 2"}']),
    ("edit_file_nomatch", ["call-tool", "edit_file", "edit_file",
                           '{"path": "fixture/hello.py", "search_content": "zzz", "replace_content": "y"}']),
    ("search_files", ["call-tool", "search_files", "search_files", '{"pattern": "def", "path": "fixture"}']),
    ("todo", ["call-tool", "todo", "todo", '{"todos": [{"id": "1", "content": "a", "status": "pending"}]}']),
    ("structured_query", ["call-tool", "structured_query", "structured_query",
                          '{"target": "alpha\\nbeta\\ngamma", "filter_expr": "a$", "aggregate": "count"}']),
    ("present_plan", ["call-tool", "present_plan", "present_plan", '{"plan": "p"}']),
    ("reset_context", ["call-tool", "reset_context", "reset_context", "{}"]),
    ("delegate_no_tasks", ["call-tool", "delegate_task", "delegate_task", '{"tasks": []}']),
    ("parallel_fetch_missing", ["call-tool", "parallel_fetch", "parallel_fetch", "{}"]),
    ("remote_exec_unknown", ["call-tool", "remote_exec", "remote_exec", '{"action": "bogus"}']),
    ("execute_remote_missing", ["call-tool", "execute_remote_command", "execute_remote_command",
                                '{"host": "", "command": ""}']),
    ("load_skill_none", ["call-tool", "load_skill", "load_skill", "{}"]),
    ("learn_rule_empty", ["call-tool", "learn_rule", "learn_rule", '{"rule_text": ""}']),
    ("list_scheduled_empty", ["call-tool", "list_scheduled_tasks", "list_scheduled_tasks", "{}"]),
    ("count_tokens", ["count-tokens", "local", "hello world, twelve+"]),
    ("render_markdown", ["render-markdown", SAMPLE_MD]),
    ("unknown_action", ["frobnicate"]),
    ("no_args", []),
]


def _env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_")}
    env.update(HOME=str(home), INFER_NO_MCP="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def _workdir(tmp_path):
    work = tmp_path / "work"
    fx = work / "fixture"
    (fx / "sub").mkdir(parents=True)
    (fx / "hello.py").write_text("def f():\n    return 1\n\n\nclass A:\n    pass\n")
    (fx / "notes.md").write_text("# notes\nline two\n")
    home = tmp_path / "home"
    home.mkdir()
    return work, home


def _run(argv, cwd, home, script=SHIM):
    r = subprocess.run([sys.executable, script] + argv, cwd=cwd, env=_env(home),
                       capture_output=True, text=True, timeout=120)
    return r


def _normalise(text, tmp_path):
    return text.replace(str(tmp_path), "<TMP>")


def _golden_bytes(name, data):
    path = os.path.join(GOLDEN, name)
    if REGEN or not os.path.exists(path):
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("profile,golden", [("", "list_tools.json"), ("core", "list_tools_core.json")])
def test_list_tools_byte_identical(tmp_path, profile, golden):
    work, home = _workdir(tmp_path)
    env = _env(home)
    if profile:
        env["INFER_TOOL_PROFILE"] = profile
    r = subprocess.run([sys.executable, SHIM, "list-tools"], cwd=work, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout == _golden_bytes(golden, r.stdout)


def test_cli_outputs_match_golden(tmp_path):
    got = {}
    for name, argv in CLI_CASES:
        case_dir = tmp_path / name
        work, home = _workdir(case_dir)
        r = _run(argv, work, home)
        got[name] = {"stdout": _normalise(r.stdout, case_dir), "returncode": r.returncode}
    blob = json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    want = json.loads(_golden_bytes("cli_cases.json", blob))
    for name, _ in CLI_CASES:
        assert got[name] == want[name], name


def test_shim_is_small():
    with open(SHIM, encoding="utf-8") as f:
        assert len(f.read().splitlines()) < 80


def test_no_aitools_module_too_big():
    pkg = os.path.join(REPO, "aitools")
    assert os.path.isdir(pkg), "aitools/ package missing"
    sizes = {}
    for fn in os.listdir(pkg):
        if fn.endswith(".py"):
            with open(os.path.join(pkg, fn), encoding="utf-8") as f:
                sizes[fn] = len(f.read().splitlines())
    too_big = {k: v for k, v in sizes.items() if v > 1500}
    assert sizes and not too_big, too_big


def test_monkeypatch_memory_path_reaches_moved_code(tmp_path, monkeypatch):
    import ai_mcp
    p = tmp_path / "mem.txt"
    monkeypatch.setattr(ai_mcp, "MEMORY_PATH", str(p))
    ai_mcp.save_memory("x")
    assert p.read_text().strip() == "x"


def test_registry_interface(tmp_path, monkeypatch):
    """TOOL_SCHEMAS is the native list-tools catalog; dispatch returns call-tool's stdout."""
    from aitools import registry
    want = json.loads(open(os.path.join(GOLDEN, "list_tools.json"), encoding="utf-8").read())
    assert registry.TOOL_SCHEMAS == want
    assert registry.dispatch("think", "think", {"reasoning": "x"}) == '{"ok": true}\n'
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    (work / "f.txt").write_text("abc\n")
    assert registry.dispatch("list_directory", "list_directory", {"path": "."}) == "      f.txt (4 B)\n"
    out = registry.dispatch("write_file", "write_file", {"path": "x"})
    assert out == '{"error": "Missing required argument(s): content"}\n'


def test_monkeypatch_reaches_dispatch(monkeypatch):
    """Patching a tool function on the ai_mcp shim must change what dispatch runs."""
    import ai_mcp
    from aitools import registry
    monkeypatch.setattr(ai_mcp, "fetch_smart", lambda url: "PATCHED:" + url)
    monkeypatch.delenv("INFER_FETCH_BASIC", raising=False)
    assert registry.dispatch("fetch_webpage", "fetch_webpage", {"url": "http://x"}) == "PATCHED:http://x\n"


def _fake_bin(dirpath, names):
    dirpath.mkdir()
    for n in names:
        p = dirpath / n
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)


def test_install_layout(tmp_path):
    """install.sh into an isolated HOME: the installed ~/.local/bin/ai_mcp.py finds the
    aitools package in ~/.local/share/ai/lib and serves list-tools from cwd=/."""
    if not os.path.exists(os.path.join(REPO, "ai")):
        pytest.skip("ai binary not built")
    home = tmp_path / "home"
    home.mkdir()
    fake = tmp_path / "fakebin"
    # No rebuild, no pip, no apt, no systemd, no git config: only the copy steps run for real.
    _fake_bin(fake, ["make", "pip", "sudo", "systemctl", "git", "apt-get"])
    env = _env(home)
    env["PATH"] = f"{fake}:{env.get('PATH', '')}"
    r = subprocess.run(["bash", os.path.join(REPO, "install.sh")], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    lib = home / ".local" / "share" / "ai" / "lib" / "aitools"
    assert (lib / "__init__.py").exists() and (lib / "registry.py").exists()
    assert not list(lib.glob("__pycache__"))
    installed = home / ".local" / "bin" / "ai_mcp.py"
    r = subprocess.run([sys.executable, str(installed), "list-tools"], cwd="/", env=_env(home),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    with open(os.path.join(GOLDEN, "list_tools.json"), encoding="utf-8") as f:
        assert r.stdout == f.read()
    # The package came from the installed lib, not from this checkout.
    probe = ("import sys; sys.argv=['x']; import ai_mcp, aitools; print(aitools.__file__)")
    r = subprocess.run([sys.executable, "-c", probe], cwd="/", env=dict(_env(home), PYTHONPATH=str(installed.parent)),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().startswith(str(lib)), r.stdout
    shutil.rmtree(home, ignore_errors=True)
