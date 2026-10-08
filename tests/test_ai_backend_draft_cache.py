"""ai-backend: speculative-draft precedence (MTP / DFlash) and the prompt-cache RAM cap.

Live failure: with LLAMA_MTP=1, LLAMA_SPEC_TYPE=draft-dflash and LLAMA_MTP_DRAFT_PATH=<...DFlash2...gguf>
in the env file, `serve` attached the sibling full model "Qwen3.8-27B-GSQ-RCO-IQ3_XXS-mtp.gguf" as the
draft (the explicit path was only honoured when its name contained "mtp") and llama-server aborted
("DFlash model has no target_layer_ids"). Separately llama-server was SIGKILLed while its host-RAM
prompt cache (default --cache-ram 8192) evicted a 3.6 GB entry at ~94K tokens.
Offline: fake GGUF files (sizes only) in tmp dirs, cmd_serve stopped at Popen.
"""
import importlib.util
import os
import shutil

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-backend")
MB = 1 << 20


@pytest.fixture(scope="module")
def ab(tmp_path_factory):
    dst = tmp_path_factory.mktemp("abmod_draft") / "ai_backend_mod.py"
    shutil.copyfile(SRC, dst)
    spec = importlib.util.spec_from_file_location("ai_backend_mod_draft", dst)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gguf(path, mb):
    with open(path, "wb") as f:
        f.truncate(int(mb * MB))
    return str(path)


@pytest.fixture
def env(ab, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".cache" / "ai").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    models = tmp_path / "models"
    models.mkdir()
    env_file = tmp_path / "env"
    env_file.write_text('export INFER_BASE_URL="http://127.0.0.1:9/v1/"\nexport INFER_MODEL="llama"\n')
    monkeypatch.setattr(ab, "ENV_FILE", env_file)
    monkeypatch.setattr(ab, "MODEL_DIR", tmp_path / "other_models")
    monkeypatch.setattr(ab, "restart_systemd_service", lambda *a, **k: None)
    monkeypatch.setattr(ab, "is_proxy_running", lambda *a, **k: False)
    monkeypatch.setattr(ab, "kill_llama_server", lambda: None)
    for var in ("LLAMA_MODEL_PATH", "LLAMA_MTP", "LLAMA_CTX_SIZE", "LLAMA_DRAFT_MODEL_PATH", "LLAMA_MTP_DRAFT_PATH",
                "LLAMA_SPEC_TYPE", "LLAMA_SPEC_DRAFT_N_MAX", "LLAMA_N_PARALLEL", "LLAMA_CACHE_RAM",
                "LLAMA_N_GPU_LAYERS", "CUDA_VISIBLE_DEVICES", "LLAMA_TENSOR_SPLIT"):
        monkeypatch.delenv(var, raising=False)
    main = _gguf(models / "Qwen3.8-27B-Q4_K_M.gguf", 30)
    # a full model that merely has MTP heads: as big as the main model, "mtp" in its name
    full_mtp = _gguf(models / "Qwen3.8-27B-GSQ-RCO-IQ3_XXS-mtp.gguf", 24)
    dflash = _gguf(models / "Qwen3.8-27B-DFlash2-Q4_K_M.gguf", 2)
    return {"ab": ab, "env": env_file, "models": models, "main": main, "full_mtp": full_mtp, "dflash": dflash}


def _set(ab, **kv):
    ab.persist_env_changes(kv, auto_restart=False)


# ── draft resolution ────────────────────────────────────────────────────────
def test_explicit_dflash_draft_wins_over_an_mtp_sibling(env):
    ab = env["ab"]
    _set(ab, LLAMA_MODEL_PATH=env["main"], LLAMA_MTP="1", LLAMA_SPEC_TYPE="draft-dflash",
         LLAMA_MTP_DRAFT_PATH=env["dflash"])
    assert ab.find_mtp_draft(env["main"]) == env["dflash"]


def test_explicit_draft_wins_even_for_draft_mtp_without_mtp_in_the_name(env):
    ab = env["ab"]
    small = _gguf(env["models"] / "Qwen3.8-27B-MTP-draft.gguf", 1)      # an auto-detectable alternative
    _set(ab, LLAMA_MODEL_PATH=env["main"], LLAMA_SPEC_TYPE="draft-mtp", LLAMA_MTP_DRAFT_PATH=env["dflash"])
    assert ab.find_mtp_draft(env["main"]) == env["dflash"]
    _set(ab, LLAMA_MTP_DRAFT_PATH=None)
    assert ab.find_mtp_draft(env["main"]) == small


def test_dflash_never_auto_picks_an_mtp_file(env):
    ab = env["ab"]
    os.remove(env["dflash"])
    _gguf(env["models"] / "Qwen3.8-27B-MTP-draft.gguf", 1)             # small, a real MTP draft
    _set(ab, LLAMA_MODEL_PATH=env["main"], LLAMA_MTP="1", LLAMA_SPEC_TYPE="draft-dflash")
    assert ab.find_mtp_draft(env["main"]) is None


def test_dflash_auto_picks_a_small_dflash_sibling(env):
    ab = env["ab"]
    _set(ab, LLAMA_MODEL_PATH=env["main"], LLAMA_SPEC_TYPE="draft-dflash")
    assert ab.find_mtp_draft(env["main"]) == env["dflash"]


def test_full_model_with_mtp_in_its_name_is_not_a_draft(env):
    ab = env["ab"]
    _set(ab, LLAMA_MODEL_PATH=env["main"], LLAMA_SPEC_TYPE="draft-mtp")
    assert ab.find_mtp_draft(env["main"]) is None                       # only the 24 MB "-mtp" model
    small = _gguf(env["models"] / "Qwen3.8-27B-mtp-head-Q8_0.gguf", 3)  # < 1/3 of 30 MB
    assert ab.find_mtp_draft(env["main"]) == small


def test_other_model_family_mtp_file_is_not_picked(env):
    ab = env["ab"]
    _gguf(env["models"] / "Gemma4-mtp-draft.gguf", 1)
    _set(ab, LLAMA_MODEL_PATH=env["main"], LLAMA_SPEC_TYPE="draft-mtp")
    assert ab.find_mtp_draft(env["main"]) is None


def test_missing_explicit_draft_disables_auto_pick(env, capsys):
    ab = env["ab"]
    _set(ab, LLAMA_MODEL_PATH=env["main"], LLAMA_SPEC_TYPE="draft-dflash",
         LLAMA_MTP_DRAFT_PATH=str(env["models"] / "gone-DFlash.gguf"))
    assert ab.find_mtp_draft(env["main"]) is None
    assert "does not exist" in capsys.readouterr().err


def test_spec_dflash_command_and_draft_routing(env, capsys):
    ab = env["ab"]
    _set(ab, LLAMA_MODEL_PATH=env["main"])
    ab.cmd_mtp("dflash", env["dflash"], auto_restart=False)
    e = ab.load_env()
    assert e["LLAMA_MTP"] == "1" and e["LLAMA_SPEC_TYPE"] == "draft-dflash"
    assert e["LLAMA_MTP_DRAFT_PATH"] == os.path.realpath(env["dflash"])
    ab.cmd_mtp("off", auto_restart=False)
    assert "LLAMA_MTP_DRAFT_PATH" not in ab.load_env()
    ab.cmd_draft(env["dflash"])                     # `ai-backend draft <DFlash file>` -> DFlash, not dspark
    e = ab.load_env()
    assert e["LLAMA_SPEC_TYPE"] == "draft-dflash" and "LLAMA_DRAFT_MODEL_PATH" not in e
    with pytest.raises(SystemExit):
        ab.cmd_mtp("dflash", str(env["models"] / "nope.gguf"), auto_restart=False)


# ── serve argv ──────────────────────────────────────────────────────────────
@pytest.fixture
def serve(env, monkeypatch):
    ab = env["ab"]
    monkeypatch.setattr(ab, "resolve_serve_gpu", lambda *a, **k: "0")
    monkeypatch.setattr(ab, "list_gpu_info", lambda: [{"index": 0, "name": "GPU", "total_mib": 97000,
                                                       "free_mib": 90000}])
    monkeypatch.setattr(ab, "vram_preflight", lambda *a, **k: True)
    monkeypatch.setattr(ab, "get_serve_port", lambda: 18081)
    captured = {}

    def fake_popen(cmd, *a, **k):
        captured["cmd"] = cmd
        raise SystemExit("CAPTURED")
    monkeypatch.setattr(ab.subprocess, "Popen", fake_popen)

    def run(**kv):
        _set(ab, LLAMA_MODEL_PATH=env["main"], **kv)
        with pytest.raises(SystemExit):
            ab.cmd_serve()
        return captured["cmd"]
    return run


def _after(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def test_serve_live_config_attaches_the_dflash_draft(serve, env, capsys):
    cmd = serve(LLAMA_MTP="1", LLAMA_SPEC_TYPE="draft-dflash", LLAMA_MTP_DRAFT_PATH=env["dflash"],
                LLAMA_CTX_SIZE="32768")
    assert _after(cmd, "-md") == env["dflash"] and _after(cmd, "--spec-type") == "draft-dflash"
    assert env["full_mtp"] not in cmd
    assert "DFlash2" in capsys.readouterr().err


def test_serve_cache_ram_default_for_large_ctx(serve):
    assert _after(serve(LLAMA_CTX_SIZE="131072"), "--cache-ram") == "4096"


def test_serve_cache_ram_absent_for_small_ctx(serve):
    assert "--cache-ram" not in serve(LLAMA_CTX_SIZE="32768")


@pytest.mark.parametrize("value", ["2048", "-1", "0", "16384"])
def test_serve_cache_ram_explicit_value_wins(serve, value):
    assert _after(serve(LLAMA_CTX_SIZE="131072", LLAMA_CACHE_RAM=value), "--cache-ram") == value
    assert _after(serve(LLAMA_CTX_SIZE="8192", LLAMA_CACHE_RAM=value), "--cache-ram") == value
