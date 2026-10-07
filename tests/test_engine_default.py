"""Engine selection: the Python engine (agent/) is the default; INFER_ENGINE=c keeps the legacy C loop
for one release with a one-line deprecation notice on stderr (never stdout: pipes and -q parse
stdout); a missing agent/ falls back to C with a warning unless INFER_ENGINE_STRICT=1."""
import json
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
from test_offline import AI_BIN, MockServer  # noqa: E402

DEPRECATION = "INFER_ENGINE=c is deprecated"


def _env(home, base_url, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INFER_") or k == "INFER_TOOL_SERVER"}
    env.update(HOME=str(home), INFER_BASE_URL=base_url, INFER_API_KEY="t", INFER_MODEL="mock",
               INFER_TOOL_CHOICE="auto")
    env.update(extra)
    return env


def _run(binary, args, env, cwd):
    return subprocess.run([binary] + args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=90)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_default_is_python_engine(tmp_path, home):
    """No INFER_ENGINE: the front-end starts the Python engine. With INFER_ENGINE_PATH at a fake
    engine that records its argv, the fake runs (the C loop would never exec it)."""
    fake = tmp_path / "fake_agent"
    fake.mkdir()
    marker = tmp_path / "ran.json"
    (fake / "__main__.py").write_text(
        "import json, os, sys\n"
        "json.dump(sys.argv[1:], open(%r, 'w'))\n"
        "os.write(3, b'{\"ev\": \"done\", \"code\": 0}\\n')\n" % str(marker))
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TASK_COMPLETE="UNUSED") as srv:
        env = _env(home, srv.base_url, INFER_ENGINE_PATH=str(fake), INFER_ENGINE_STRICT="1")
        r = _run(AI_BIN, ["-q", "hello"], env, str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert json.loads(marker.read_text())[:2] == ["--frontend-fd", "3"]
    assert DEPRECATION not in r.stderr


def test_default_answers_through_python_engine(tmp_path, home):
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TASK_COMPLETE="PY_DEFAULT_OK") as srv:
        r = _run(AI_BIN, ["-q", "hello"], _env(home, srv.base_url, INFER_ENGINE_STRICT="1"), str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "PY_DEFAULT_OK" in r.stdout
    assert DEPRECATION not in r.stderr and "deprecated" not in r.stdout


@pytest.mark.parametrize("args", [["hello"], ["-q", "hello"]])
def test_c_engine_prints_one_deprecation_line_on_stderr_only(tmp_path, home, args):
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TASK_COMPLETE="C_ENGINE_OK") as srv:
        r = _run(AI_BIN, args, _env(home, srv.base_url, INFER_ENGINE="c"), str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "C_ENGINE_OK" in r.stdout
    assert "deprecated" not in r.stdout
    lines = [ln for ln in r.stderr.splitlines() if DEPRECATION in ln]
    assert len(lines) == 1, r.stderr


def test_c_engine_pipe_output_is_clean(tmp_path, home):
    """`ai -q … | next-tool`: stdout carries only the answer under the legacy engine too."""
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TASK_COMPLETE="PIPED_ANSWER") as srv:
        env = _env(home, srv.base_url, INFER_ENGINE="c")
        r = subprocess.run("echo some input | %s -q 'summarise' | cat" % AI_BIN, shell=True, cwd=str(tmp_path),
                           env=env, capture_output=True, text=True, timeout=90)
    assert "PIPED_ANSWER" in r.stdout
    assert "INFER_ENGINE" not in r.stdout and "deprecated" not in r.stdout


@pytest.fixture
def lone_binary(tmp_path):
    """The binary without an agent/ next to it (and none installed under the isolated HOME)."""
    d = tmp_path / "bin"
    d.mkdir()
    shutil.copy2(AI_BIN, d / "ai")
    shutil.copy2(os.path.join(REPO, "libremote_harness.so"), d / "libremote_harness.so")  # rpath "."
    return d


def test_missing_engine_falls_back_to_c_with_warning(tmp_path, home, lone_binary):
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TASK_COMPLETE="FALLBACK_OK") as srv:
        r = _run(str(lone_binary / "ai"), ["-q", "hello"], _env(home, srv.base_url), str(lone_binary))
    assert r.returncode == 0, r.stderr
    assert "FALLBACK_OK" in r.stdout
    assert "Python engine (agent/) was not found; using the legacy C engine" in r.stderr
    assert "not found" not in r.stdout


def test_missing_engine_strict_fails(tmp_path, home, lone_binary):
    with MockServer(str(tmp_path / "cap.jsonl"), MOCK_TASK_COMPLETE="NEVER") as srv:
        r = _run(str(lone_binary / "ai"), ["-q", "hello"],
                 _env(home, srv.base_url, INFER_ENGINE_STRICT="1"), str(lone_binary))
        reqs = srv.requests()
    assert r.returncode == 1
    assert "Python engine (agent/) was not found" in r.stderr
    assert "NEVER" not in r.stdout and reqs == []
