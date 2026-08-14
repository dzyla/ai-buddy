"""Tests for the new `ai-backend` sampling/YaRN controls and the env persistence
delete fix:

* `ai-backend yarn <on|off|<scale>>`  — YaRN RoPE-scaling state + serve flag emit.
* `ai-backend mode <xhigh|normal|low|instruct>` — reasoning-effort presets.
* `write_env(authoritative=True)` / `persist_env_changes` — deleting a key must
  not resurrect it (regression: the old code re-loaded the on-disk file and
  `.update()`d on top, so every `None`-deletion silently re-appeared).
"""
import importlib.util
import os
import shutil
from pathlib import Path

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-backend")


@pytest.fixture(scope="module")
def ab(tmp_path_factory):
    # ai-backend has no ".py" extension; import through a copied module.
    dst = tmp_path_factory.mktemp("abmod_yarn") / "ai_backend_mod.py"
    shutil.copyfile(SRC, dst)
    spec = importlib.util.spec_from_file_location("ai_backend_mod_yarn", dst)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def hermetic(ab, monkeypatch, tmp_path):
    """Hermetic env file + a real model path so serve's existence check passes."""
    env_file = tmp_path / "env"
    env_file.write_text(
        '# ai backend config\n'
        'export CUDA_VISIBLE_DEVICES="auto"\n'
        'export INFER_BASE_URL="http://localhost:8080/v1/"\n'
        'export INFER_API_KEY="not-needed"\n'
        'export INFER_MODEL="llama"\n'
        'export INFER_TOOL_CHOICE=auto\n'
        'export LLAMA_CTX_SIZE="131072"\n'
        'export LLAMA_N_GPU_LAYERS="99"\n'
        'export INFER_TEMPERATURE="1.0"\n'
        'export INFER_TOP_P="0.95"\n'
        'export INFER_REASONING_EFFORT="xhigh"\n'
    )
    monkeypatch.setattr(ab, "ENV_FILE", env_file)
    for var in ("LLAMA_MODEL_PATH", "CUDA_VISIBLE_DEVICES", "LLAMA_MTP", "LLAMA_CTX_SIZE",
                "LLAMA_CTX_SIZE_FACTOR", "LLAMA_CTX_SIZE_MAX", "LLAMA_N_GPU_LAYERS",
                "LLAMA_DRAFT_MODEL_PATH", "LLAMA_MTP_DRAFT_PATH",
                "LLAMA_ROPE_SCALING", "LLAMA_ROPE_SCALE", "LLAMA_YARN_ORIG_CTX"):
        monkeypatch.delenv(var, raising=False)
    model = tmp_path / "Qwen3.8-27B-test.gguf"
    model.write_bytes(b"fake")
    monkeypatch.setattr(ab, "MODEL_DIR", tmp_path / "models")
    (tmp_path / "models").mkdir()
    yield {"env": env_file, "model": model}


# ---------------------------------------------------------------------------
# env persistence delete regression
# ---------------------------------------------------------------------------

def test_persist_delete_not_resurrected(ab, hermetic):
    """Deleting a key via persist_env_changes must stay deleted (was: the
    write_env re-load resurrected it)."""
    ab.persist_env_changes({"LLAMA_YARN_ORIG_CTX": "262144",
                            "LLAMA_ROPE_SCALE": "4.0",
                            "LLAMA_ROPE_SCALING": "yarn"})
    assert "LLAMA_ROPE_SCALE" in ab.load_env()
    # Now delete all three.
    ab.persist_env_changes({"LLAMA_YARN_ORIG_CTX": None,
                            "LLAMA_ROPE_SCALE": None,
                            "LLAMA_ROPE_SCALING": None})
    env = ab.load_env()
    for k in ("LLAMA_YARN_ORIG_CTX", "LLAMA_ROPE_SCALE", "LLAMA_ROPE_SCALING"):
        assert k not in env, f"{k} was resurrected after deletion"
    # Unrelated keys must survive.
    assert env.get("INFER_TEMPERATURE") == "1.0"
    assert env.get("INFER_REASONING_EFFORT") == "xhigh"


def test_patch_env_key_delete_not_resurrected(ab, hermetic):
    ab.patch_env_key("LLAMA_TENSOR_SPLIT", "1,1")
    assert "LLAMA_TENSOR_SPLIT" in ab.load_env()
    ab.patch_env_key("LLAMA_TENSOR_SPLIT", None)
    assert "LLAMA_TENSOR_SPLIT" not in ab.load_env()
    # Non-deletion still writes.
    ab.patch_env_key("LLAMA_TENSOR_SPLIT", "2,1")
    assert ab.load_env().get("LLAMA_TENSOR_SPLIT") == "2,1"


# ---------------------------------------------------------------------------
# ai-backend yarn
# ---------------------------------------------------------------------------

def test_yarn_query_off(ab, hermetic, capsys):
    ab.cmd_yarn()
    out = capsys.readouterr().out
    assert "off" in out
    assert "LLAMA_ROPE_SCALING=(unset)" in out


def test_yarn_on_writes_env(ab, hermetic, capsys):
    ab.cmd_yarn("on")
    env = ab.load_env()
    assert env.get("LLAMA_ROPE_SCALING") == "yarn"
    assert env.get("LLAMA_ROPE_SCALE") == "4.0"
    assert env.get("LLAMA_YARN_ORIG_CTX") == "262144"


def test_yarn_on_query_shows_enabled(ab, hermetic, capsys):
    ab.cmd_yarn("on")
    ab.cmd_yarn()
    out = capsys.readouterr().out
    assert "yarn scale=4.0" in out
    assert "1,048,576" in out  # ~1M tokens


def test_yarn_custom_scale(ab, hermetic, capsys):
    ab.cmd_yarn("2")
    env = ab.load_env()
    assert env.get("LLAMA_ROPE_SCALE") == "2"
    ab.cmd_yarn()
    out = capsys.readouterr().out
    assert "524,288" in out  # 262144 * 2


def test_yarn_off_clears_env(ab, hermetic, capsys):
    ab.cmd_yarn("on")
    ab.cmd_yarn("off")
    env = ab.load_env()
    for k in ("LLAMA_ROPE_SCALING", "LLAMA_ROPE_SCALE", "LLAMA_YARN_ORIG_CTX"):
        assert k not in env, f"{k} should be cleared"
    ab.cmd_yarn()
    out = capsys.readouterr().out
    assert "off" in out


def test_yarn_bad_value_exits(ab, hermetic):
    with pytest.raises(SystemExit):
        ab.cmd_yarn("not-a-number")


# ---------------------------------------------------------------------------
# ai-backend mode presets
# ---------------------------------------------------------------------------

def test_mode_thinking_is_xhigh(ab, hermetic, capsys):
    ab.cmd_mode("thinking")
    assert ab.load_env().get("INFER_REASONING_EFFORT") == "xhigh"


def test_mode_normal_is_medium(ab, hermetic, capsys):
    ab.cmd_mode("normal")
    assert ab.load_env().get("INFER_REASONING_EFFORT") == "medium"


def test_mode_medium_alias(ab, hermetic, capsys):
    ab.cmd_mode("medium")
    assert ab.load_env().get("INFER_REASONING_EFFORT") == "medium"


def test_mode_low(ab, hermetic, capsys):
    ab.cmd_mode("low")
    assert ab.load_env().get("INFER_REASONING_EFFORT") == "low"


def test_mode_instruct(ab, hermetic, capsys):
    ab.cmd_mode("instruct")
    env = ab.load_env()
    assert env.get("INFER_REASONING_EFFORT") == "none"
    assert env.get("INFER_TEMPERATURE") == "0.7"
    assert env.get("INFER_TOP_P") == "0.80"


def test_mode_bad_exits(ab, hermetic):
    with pytest.raises(SystemExit):
        ab.cmd_mode("bogus")


# ---------------------------------------------------------------------------
# serve flag assembly (YaRN on/off)
# ---------------------------------------------------------------------------

@pytest.fixture
def serve_stubs(ab, hermetic, monkeypatch):
    """Stub the GPU/VRAM/network bits so cmd_serve reaches Popen with a
    synthetic model and no real subprocess is launched."""
    monkeypatch.setattr(ab, "find_dspark_draft", lambda *a, **k: None)
    monkeypatch.setattr(ab, "find_mtp_draft", lambda *a, **k: None)
    monkeypatch.setattr(ab, "resolve_serve_gpu", lambda *a, **k: "1")
    monkeypatch.setattr(ab, "list_gpu_info",
                        lambda: [{"index": 1, "name": "RTX PRO 6000",
                                  "total_mib": 97000, "free_mib": 90000}])
    monkeypatch.setattr(ab, "vram_preflight", lambda *a, **k: True)
    monkeypatch.setenv("LLAMA_MODEL_PATH", str(hermetic["model"]))
    captured = {}

    def fake_popen(cmd, *a, **k):
        captured["cmd"] = cmd
        raise SystemExit("CAPTURED")
    monkeypatch.setattr(ab.subprocess, "Popen", fake_popen)
    return captured


def test_serve_emits_yarn_flags_when_on(ab, serve_stubs, monkeypatch, capsys):
    monkeypatch.delenv("LLAMA_CTX_SIZE", raising=False)
    ab.persist_env_changes({"LLAMA_ROPE_SCALING": "yarn",
                            "LLAMA_ROPE_SCALE": "4.0",
                            "LLAMA_YARN_ORIG_CTX": "262144"})
    monkeypatch.setenv("LLAMA_CTX_SIZE", "262144")
    with pytest.raises(SystemExit):
        ab.cmd_serve()
    cmd = serve_stubs.get("cmd") or []
    assert "--rope-scaling" in cmd and cmd[cmd.index("--rope-scaling") + 1] == "yarn"
    assert "--rope-scale" in cmd and cmd[cmd.index("--rope-scale") + 1] == "4.0"
    assert "--yarn-orig-ctx" in cmd and cmd[cmd.index("--yarn-orig-ctx") + 1] == "262144"


def test_serve_omits_yarn_flags_when_off(ab, serve_stubs, monkeypatch, capsys):
    # Ensure no YaRN vars in env file or process env.
    ab.persist_env_changes({"LLAMA_ROPE_SCALING": None,
                            "LLAMA_ROPE_SCALE": None,
                            "LLAMA_YARN_ORIG_CTX": None})
    for k in ("LLAMA_ROPE_SCALING", "LLAMA_ROPE_SCALE", "LLAMA_YARN_ORIG_CTX"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLAMA_CTX_SIZE", "262144")
    with pytest.raises(SystemExit):
        ab.cmd_serve()
    cmd = serve_stubs.get("cmd") or []
    for f in ("--rope-scaling", "--rope-scale", "--yarn-orig-ctx"):
        assert f not in cmd, f"{f} should not be present when YaRN is off"
