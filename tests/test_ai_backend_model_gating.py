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