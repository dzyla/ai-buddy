"""Per-model sampling profiles in ai-backend (`use` applies them, `profile show|apply|off`).

The Qwen3.5-family (gguf arch qwen35 / qwen35moe: Qwen3.6/3.8, Ornith) thinking recipe is
temperature 1.0, top_p 0.95, top_k 20, min_p 0.0, presence_penalty 1.5 -- written to the client
(INFER_*) and the server (LLAMA_PRESENCE_PENALTY) keys. A key the user customised is never
touched: ~/.local/share/ai/profile.json records what the profile wrote, and a key whose current
value differs from that record (or that exists with a non-neutral value and no record) is
user-owned. `profile off` restores the values from before the profile, for keys still holding the
profile's value. Hermetic: the env file / sidecar live in tmp_path, nothing touches systemd, kills
processes or probes the local server port.
"""
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-backend")

QWEN = {"INFER_TEMPERATURE": "1.0", "INFER_TOP_P": "0.95", "INFER_TOP_K": "20", "INFER_MIN_P": "0.0",
        "INFER_PRESENCE_PENALTY": "1.5", "LLAMA_PRESENCE_PENALTY": "1.5"}


@pytest.fixture
def ab(tmp_path, monkeypatch):
    dst = tmp_path / "ai_backend_profiles_mod.py"
    shutil.copyfile(SRC, dst)
    spec = importlib.util.spec_from_file_location("ai_backend_profiles_mod", dst)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    data = tmp_path / "share"
    data.mkdir()
    monkeypatch.setattr(m, "DATA_DIR", data)
    monkeypatch.setattr(m, "ENV_FILE", data / "env")
    monkeypatch.setattr(m, "PROFILE_FILE", data / "profile.json")
    monkeypatch.setattr(m, "MODEL_DIR", data / "models")
    monkeypatch.setattr(m, "SYSTEMD_SERVICE", tmp_path / "none.service")
    monkeypatch.setattr(m, "SYSTEMD_SOCKET", tmp_path / "none.socket")
    m.restarts = []
    monkeypatch.setattr(m, "restart_systemd_service", lambda verbose=True: m.restarts.append(1))
    monkeypatch.setattr(m, "kill_llama_server", lambda: None)
    monkeypatch.setattr(m, "is_proxy_running", lambda *a, **k: False)
    m.archs = {}
    monkeypatch.setattr(m, "read_gguf_arch", lambda p: {"arch": m.archs[str(p)]} if str(p) in m.archs else None)
    return m


def owner_env(ab, **extra):
    """The env file as ai-backend last wrote it on the owner's box (values matching the Qwen
    recipe from an earlier `mode thinking`, neutral server penalties), plus `extra`."""
    v = {"LLAMA_CTX_SIZE": "131072", "INFER_TEMPERATURE": "1.0", "INFER_TOP_P": "0.95", "INFER_TOP_K": "20",
         "INFER_MIN_P": "0.0", "LLAMA_PRESENCE_PENALTY": "0.0", "LLAMA_REPEAT_PENALTY": "1.0",
         "INFER_REASONING_EFFORT": "medium"}
    v.update(extra)
    ab.write_env("http://localhost:8080/v1/", "llama", "/m/old.gguf", v)
    return ab.load_env()


def model(ab, tmp_path, name, arch):
    p = tmp_path / name
    p.write_bytes(b"GGUF")
    ab.archs[str(p.resolve())] = arch
    return p


def test_detects_qwen35_family(ab, tmp_path):
    assert ab.detect_sampling_profile(model(ab, tmp_path, "Qwen3.8-27B-Q4.gguf", "qwen35")) == "qwen35-thinking"
    assert ab.detect_sampling_profile(model(ab, tmp_path, "Ornith-1.5-35B-A3B.gguf", "qwen35moe")) == "qwen35-thinking"
    assert ab.detect_sampling_profile(model(ab, tmp_path, "gemma-4-12b.gguf", "gemma3")) is None
    assert ab.detect_sampling_profile(model(ab, tmp_path, "Llama-3.1-8B.gguf", "llama")) is None
    # unreadable header (no gguf module): fall back to the file name for the known family only
    nohdr = tmp_path / "Ornith-1.5-35B-A3B-Q6_K.gguf"
    nohdr.write_bytes(b"x")
    assert ab.detect_sampling_profile(nohdr) == "qwen35-thinking"
    other = tmp_path / "mystery-7b.gguf"
    other.write_bytes(b"x")
    assert ab.detect_sampling_profile(other) is None


def test_use_applies_profile_on_owner_env(ab, tmp_path):
    before = owner_env(ab)
    ab.apply_model_path(model(ab, tmp_path, "Qwen3.8-27B-Q4.gguf", "qwen35"))
    env = ab.load_env()
    for k, v in QWEN.items():
        assert float(env[k]) == float(v), k
    # nothing else changed (apart from the model the `use` switched to)
    for k in ("LLAMA_CTX_SIZE", "LLAMA_REPEAT_PENALTY", "INFER_REASONING_EFFORT"):
        assert env[k] == before[k]
    assert env["LLAMA_MODEL_PATH"].endswith("Qwen3.8-27B-Q4.gguf")
    side = json.loads(ab.PROFILE_FILE.read_text())
    assert side["profile"] == "qwen35-thinking"
    assert side["previous"]["LLAMA_PRESENCE_PENALTY"] == "0.0"
    assert side["previous"]["INFER_PRESENCE_PENALTY"] is None


def test_user_customised_keys_are_untouched(ab, tmp_path):
    owner_env(ab, INFER_TEMPERATURE="0.7", INFER_TOP_K="40", LLAMA_PRESENCE_PENALTY="0.6")
    ab.apply_model_path(model(ab, tmp_path, "Qwen3.8-27B-Q4.gguf", "qwen35"))
    env = ab.load_env()
    assert (env["INFER_TEMPERATURE"], env["INFER_TOP_K"], env["LLAMA_PRESENCE_PENALTY"]) == ("0.7", "40", "0.6")
    assert env["INFER_PRESENCE_PENALTY"] == "1.5" and env["INFER_TOP_P"] == "0.95"
    side = json.loads(ab.PROFILE_FILE.read_text())
    assert "INFER_TEMPERATURE" not in side["written"] and "LLAMA_PRESENCE_PENALTY" not in side["written"]


def test_key_edited_after_apply_becomes_user_owned(ab, tmp_path):
    owner_env(ab)
    q = model(ab, tmp_path, "Qwen3.8-27B-Q4.gguf", "qwen35")
    ab.apply_model_path(q)
    ab.persist_env_changes({"INFER_PRESENCE_PENALTY": "1.0", "INFER_TEMPERATURE": "0.6"}, auto_restart=False)
    ab.apply_model_path(q)                                   # re-`use`: the user's edits stay
    env = ab.load_env()
    assert env["INFER_PRESENCE_PENALTY"] == "1.0" and env["INFER_TEMPERATURE"] == "0.6"
    ab.cmd_profile("off")                                    # and `off` leaves them alone too
    env = ab.load_env()
    assert env["INFER_PRESENCE_PENALTY"] == "1.0" and env["INFER_TEMPERATURE"] == "0.6"
    assert env["LLAMA_PRESENCE_PENALTY"] == "0.0"            # profile-owned: restored


def test_profile_off_restores_and_disables(ab, tmp_path):
    before = owner_env(ab)
    q = model(ab, tmp_path, "Qwen3.8-27B-Q4.gguf", "qwen35")
    ab.apply_model_path(q)
    ab.cmd_profile("off")
    env = ab.load_env()
    assert "INFER_PRESENCE_PENALTY" not in env
    assert env["LLAMA_PRESENCE_PENALTY"] == "0.0"
    for k in ("INFER_TEMPERATURE", "INFER_TOP_P", "INFER_TOP_K", "INFER_MIN_P", "LLAMA_CTX_SIZE"):
        assert env[k] == before[k], k
    assert ab.restarts                                       # server penalty changed -> restart
    # disabled: a later `use` does not re-apply ...
    ab.apply_model_path(q)
    assert "INFER_PRESENCE_PENALTY" not in ab.load_env()
    # ... until `profile apply`
    ab.cmd_profile("apply")
    env = ab.load_env()
    assert env["INFER_PRESENCE_PENALTY"] == "1.5" and env["LLAMA_PRESENCE_PENALTY"] == "1.5"


def test_unknown_model_leaves_env_as_is(ab, tmp_path):
    before = owner_env(ab)
    ab.apply_model_path(model(ab, tmp_path, "gemma-4-12b.gguf", "gemma3"))
    env = ab.load_env()
    env.pop("LLAMA_MODEL_PATH")
    before.pop("LLAMA_MODEL_PATH")
    assert env == before
    assert not ab.PROFILE_FILE.exists()


def test_profile_show_reports_ownership(ab, tmp_path, capsys):
    owner_env(ab, INFER_TEMPERATURE="0.7")
    ab.apply_model_path(model(ab, tmp_path, "Qwen3.8-27B-Q4.gguf", "qwen35"))
    capsys.readouterr()
    ab.cmd_profile("show")
    out = capsys.readouterr().out
    assert "qwen35-thinking" in out
    line = [ln for ln in out.splitlines() if "INFER_TEMPERATURE" in ln][0]
    assert "0.7" in line and "user" in line
    line = [ln for ln in out.splitlines() if "LLAMA_PRESENCE_PENALTY" in ln][0]
    assert "1.5" in line and "profile" in line


def test_main_dispatches_profile(ab, tmp_path, monkeypatch, capsys):
    owner_env(ab)
    monkeypatch.setattr(ab.sys, "argv", ["ai-backend", "profile"])
    ab.main()
    assert "profile" in capsys.readouterr().out.lower()
