"""Privacy guards: the pre-commit scanner and the binary's git auto-commit.

Auto-commit used to run `git add -A` in the cwd after every successful command
and could not be turned off (a shadowing `static` in ai.c meant --no-git and
--private never reached ai_git.c). That swept untracked personal files into a
public repo. It is now opt-in, stages tracked files only, and restores the
index when a commit is skipped or blocked.
"""
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_offline import AI_BIN, REPO, MockServer  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "scripts"))
import privacy_check  # noqa: E402


# ── scanner unit tests ────────────────────────────────────────────────────────
@pytest.mark.parametrize("line", [
    'api_key = "abcd1234abcd1234abcd"',  # privacy-ok
    "ssh me@lab.university.edu",  # privacy-ok
    'URL = "http://93.184.216.34:8080"',  # privacy-ok
    'open("/home/alice/data.txt")',  # privacy-ok
    "-----BEGIN RSA PRIVATE KEY-----",  # privacy-ok
])
def test_scanner_flags_personal_data(line):
    assert privacy_check.scan_line(line, [])


@pytest.mark.parametrize("line", [
    "sender = 'bot@example.com'",
    "server = 'http://127.0.0.1:8080'",
    "lan = '192.168.1.5'",
    "ua = 'Chrome/124.0.0.0'",
    'api_key = os.environ.get("PUBMED_API_KEY", "")',
    'path = "/home/user/project"',
    'ip = "93.184.216.34"  # privacy-ok',
])
def test_scanner_allows_safe_lines(line):
    assert privacy_check.scan_line(line, []) == []


def test_scanner_personal_patterns_and_data_files():
    import re
    pats = [("personal pattern", re.compile("secretlab", re.I))]
    assert privacy_check.scan_line("ssh secretLab-gpu", pats)
    assert privacy_check.check_name("results/Viral titers.xlsx")
    assert privacy_check.check_name("gcal_token.json")
    assert privacy_check.check_name("ai.c") == []


# ── binary auto-commit behaviour ──────────────────────────────────────────────
def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(str(r), "init", "-q")
    _git(str(r), "config", "user.email", "t@example.com")
    _git(str(r), "config", "user.name", "t")
    _git(str(r), "config", "core.hooksPath", "/dev/null")
    (r / "tracked.txt").write_text("v1\n")
    _git(str(r), "add", "tracked.txt")
    _git(str(r), "commit", "-qm", "init")
    return str(r)


def _run_cmd_in(repo, tmp_path, args, extra_env=None):
    seq = [{"id": "c1", "function": {"name": "execute_command", "arguments": json.dumps(
        {"command": "echo v2 >> tracked.txt && echo secret > untracked_notes.txt"})}}]
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TOOL_SEQ=json.dumps(seq),
                    MOCK_TASK_COMPLETE="done") as srv:
        env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_") or k in ("INFER_TOOL_SERVER", "INFER_ENGINE", "INFER_ENGINE_STRICT")}
        env.update(HOME=str(home), INFER_BASE_URL=srv.base_url, INFER_API_KEY="t",
                   INFER_MODEL="mock", INFER_TOOL_CHOICE="auto", INFER_AUTO_APPROVE="1")
        env.update(extra_env or {})
        r = subprocess.run([AI_BIN, *args, "do it"], cwd=repo, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=90)
    assert os.path.exists(os.path.join(repo, "untracked_notes.txt")), r.stdout + r.stderr
    return r


def _commits(repo):
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


def test_no_auto_commit_by_default(repo, tmp_path, engine):
    _run_cmd_in(repo, tmp_path, [])
    assert _commits(repo) == 1
    assert _git(repo, "diff", "--cached", "--name-only").strip() == ""


def test_opt_in_commits_tracked_files_only(repo, tmp_path, engine):
    _run_cmd_in(repo, tmp_path, ["--git"])
    assert _commits(repo) == 2
    files = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert files == ["tracked.txt"]
    assert "untracked_notes.txt" in _git(repo, "status", "--porcelain")


def test_env_opt_in_and_no_git_override(repo, tmp_path, engine):
    _run_cmd_in(repo, tmp_path, ["--no-git"], {"INFER_AUTO_COMMIT": "1"})
    assert _commits(repo) == 1


def test_blocked_commit_restores_index(repo, tmp_path, engine):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    os.chmod(hooks / "pre-commit", 0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    _run_cmd_in(repo, tmp_path, ["--git"])
    assert _commits(repo) == 1
    assert _git(repo, "diff", "--cached", "--name-only").strip() == ""


# ── CI canary: the checker can't pass vacuously ──────────────────────────────
def test_self_test_flags_every_canary():
    assert privacy_check.self_test() == []
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "privacy_check.py"), "--self-test"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_self_test_fails_when_the_checker_is_broken(monkeypatch):
    monkeypatch.setattr(privacy_check, "GENERIC", [])
    monkeypatch.setattr(privacy_check, "IPV4", re.compile(r"(?!)"))
    missed = privacy_check.self_test()
    assert "private key" in missed and "public IP" in missed and "home path" in missed
    assert privacy_check.main(["--self-test"]) == 1


def test_all_refuses_when_no_files_are_tracked(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "privacy_check.py"), "--all"],
                       cwd=str(tmp_path), capture_output=True, text=True,
                       env={**os.environ, "AI_PRIVATE_PATTERNS": os.devnull})
    assert r.returncode == 2 and "no tracked files" in r.stderr


def test_ci_runs_the_canary_before_the_scan():
    ci = open(os.path.join(REPO, ".github", "workflows", "ci.yml"), encoding="utf-8").read()
    assert ci.index("privacy_check.py --self-test") < ci.index("privacy_check.py --all")
