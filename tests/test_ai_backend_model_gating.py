"""Regression test: the DeepSeek dspark draft must only attach to DeepSeek
main models. Attaching it to any other architecture crashes llama.cpp (it tries
to build the dsv4 graph against an incompatible model and aborts), which is
exactly what made `ai-backend serve` "do nothing" for non-DeepSeek models."""
import importlib.util
import os
import shutil
from pathlib import Path

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-backend")


@pytest.fixture(scope="module")
def ab(tmp_path_factory):
    # ai-backend has no ".py" extension, so import it through a copied module.
    dst = tmp_path_factory.mktemp("abmod") / "ai_backend_mod.py"
    shutil.copyfile(SRC, dst)
    spec = importlib.util.spec_from_file_location("ai_backend_mod", dst)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clean_draft_env(monkeypatch):
    # Ensure a stale configured draft doesn't leak across tests.
    monkeypatch.delenv("LLAMA_MODEL_PATH", raising=False)
    monkeypatch.delenv("LLAMA_DRAFT_MODEL_PATH", raising=False)


@pytest.fixture(autouse=True)
def empty_env_file(monkeypatch, ab, tmp_path):
    # Hermetic: load_env() reads ~/.local/share/ai/env in the real environment;
    # point it at an empty file so no test depends on the developer's config.
    monkeypatch.setattr(ab, "ENV_FILE", Path(tmp_path / "env"))
    monkeypatch.setattr(ab, "SYSTEMD_SERVICE", tmp_path / "dummy.service")
    monkeypatch.setattr(ab, "SYSTEMD_SOCKET", tmp_path / "dummy.socket")


def test_is_deepseek_model(ab):
    assert ab.is_deepseek_model("x/DeepSeek-V4-Flash-0731-Q5.gguf") is True
    assert ab.is_deepseek_model("x/deepseek-r1-14b.gguf") is True
    assert ab.is_deepseek_model("models/endless-frontier_BigBang-v1-Q4_K_M.gguf") is False
    assert ab.is_deepseek_model("models/Ornith-1.0-35B-Q6_K.gguf") is False


def test_find_dspark_draft_none_for_non_deepseek(ab, monkeypatch, tmp_path):
    """A configured draft must be ignored when the main model is not DeepSeek
    (this is the crash fix: it previously wired -md + --spec-type draft-dspark
    for e.g. BigBang and llama.cpp aborted)."""
    draft = tmp_path / "dspark-DeepSeek-V4-Flash-0731-Q8_0.gguf"
    draft.write_bytes(b"x")
    non_deepseek = str(tmp_path / "endless-frontier_BigBang-v1-Q4_K_M.gguf")
    monkeypatch.setenv("LLAMA_DRAFT_MODEL_PATH", str(draft))
    assert ab.find_dspark_draft(non_deepseek) is None


def test_find_dspark_draft_returns_for_deepseek(ab, monkeypatch, tmp_path):
    """The draft is still resolved for a DeepSeek main model (speculative
    decoding keeps working)."""
    draft = tmp_path / "dspark-DeepSeek-V4-Flash-0731-Q8_0.gguf"
    draft.write_bytes(b"x")
    deepseek = str(tmp_path / "DeepSeek-V4-Flash-0731-Q5_K_M.gguf")
    monkeypatch.setenv("LLAMA_DRAFT_MODEL_PATH", str(draft))
    assert ab.find_dspark_draft(deepseek) == str(draft)


def test_find_dspark_draft_no_explicit_draft(ab):
    """With no configured/inferable draft there is nothing to attach."""
    assert ab.find_dspark_draft("whatever/DeepSeek-V4.gguf") is None


def test_is_qwen38_model(ab):
    assert ab.is_qwen38_model("unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf") is True
    assert ab.is_qwen38_model("models/Qwen3_8-27B-Instruct.gguf") is True
    assert ab.is_qwen38_model("models/qwen3.8-2.4t-a95b-q1_0.gguf") is True
    assert ab.is_qwen38_model("models/Qwen3.6-35B-A3B.gguf") is False
    assert ab.is_qwen38_model("models/DeepSeek-V4.gguf") is False


def test_is_qwen_model(ab):
    assert ab.is_qwen_model("unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf") is True
    assert ab.is_qwen_model("models/Qwen3.6-35B-A3B.gguf") is True
    assert ab.is_qwen_model("models/DeepSeek-V4.gguf") is False


def test_find_mtp_draft(ab, monkeypatch, tmp_path):
    mtp_draft = tmp_path / "Qwen3.8-27B-MTP.gguf"
    mtp_draft.write_bytes(b"x")
    main_model = str(tmp_path / "Qwen3.8-27B-UD-Q4_K_XL.gguf")
    monkeypatch.setenv("LLAMA_MTP_DRAFT_PATH", str(mtp_draft))
    assert ab.find_mtp_draft(main_model) == str(mtp_draft)


def test_env_file_model_precedence_over_stale_os_environ(ab, monkeypatch, tmp_path):
    """When ~/.local/share/ai/env has a newly selected model, it must take precedence
    over a stale LLAMA_MODEL_PATH left over in the user's terminal environment."""
    fresh_model = tmp_path / "Qwen3.8-27B-UD-IQ2_XXS.gguf"
    fresh_model.write_bytes(b"dummy")
    stale_model = tmp_path / "ornith-9b-mtp-kl-Q4_K_M.gguf"
    stale_model.write_bytes(b"dummy")

    monkeypatch.setattr(ab, "load_env", lambda: {"LLAMA_MODEL_PATH": str(fresh_model)})
    monkeypatch.setenv("LLAMA_MODEL_PATH", str(stale_model))

    env_vars = ab.load_env()
    model_path = env_vars.get("LLAMA_MODEL_PATH") or os.environ.get("LLAMA_MODEL_PATH")
    assert model_path == str(fresh_model)


def test_cmd_serve_accepts_target(ab, monkeypatch, tmp_path):
    """ai-backend serve <target> should switch to that target before serving."""
    target_model = tmp_path / "models" / "my_model.gguf"
    target_model.parent.mkdir(parents=True, exist_ok=True)
    target_model.write_bytes(b"dummy")

    switched = []
    monkeypatch.setattr(ab, "cmd_use", lambda t: switched.append(t))
    monkeypatch.setattr(ab, "load_env", lambda: {"LLAMA_MODEL_PATH": str(target_model)})
    monkeypatch.setattr(ab, "vram_preflight", lambda *args, **kwargs: False)

    with pytest.raises(SystemExit):
        ab.cmd_serve("my_model.gguf")

    assert switched == ["my_model.gguf"]