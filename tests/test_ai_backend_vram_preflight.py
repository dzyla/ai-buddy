"""Regression tests: the VRAM pre-flight in `ai-backend serve` must refuse to
launch llama-server when model + context cannot fit the selected GPU(s),
instead of letting it die mid-load with an opaque cudaMalloc OOM.

The original incident: Qwen3.8-27B at 256K context + MTP pinned to a single
24 GB card — ~30 GiB of weights/state/KV — and llama-server only died at
"failed to allocate buffer for rs cache", leaving no hint about the fix.

Also covers the GGUF-arch reader + footprint estimator that power the check
(hybrid SSM models, KV sized from the real head geometry, nested namespaces).

Weight sizes are faked by monkeypatching split_total_size, so no large files
are ever written (the synthetic GGUFs are ~1 MiB).
"""
import importlib.util
import os
import shutil
from pathlib import Path

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-backend")

GGUF = None
try:
    import gguf as GGUF
except Exception:
    for cand in (str(Path.home() / ".local" / "share" / "ai" / "llama.cpp" / "gguf-py"),
                 str(Path.home() / "Code" / "llama.cpp" / "gguf-py"),
                 "/home/dzyla/Code/llama.cpp/gguf-py"):
        if os.path.isdir(cand):
            import sys as _sys
            _sys.path.insert(0, cand)
            try:
                import gguf as GGUF
                break
            except Exception:
                pass
if GGUF is None:
    pytest.skip("gguf-py not importable", allow_module_level=True)


def _write_gguf(path, arch="qwen35", layers=65, kv_heads=4, head_dim=256,
                full_attn_interval=4, nextn=1, ssm=True, embed=64, ff=256,
                inner=64, groups=8, state=16, conv=4, dtime=4):
    """Tiny synthetic GGUF with the right arch fields and one small tensor.

    The single tensor is 1 MiB; the *logical* model size is controlled by
    monkeypatching ab.split_total_size in the individual tests.
    """
    import numpy as np
    w = GGUF.GGUFWriter(str(path), arch=arch)
    w.add_string("general.name", "test-model")
    w.add_uint32(f"{arch}.block_count", layers)
    w.add_uint32(f"{arch}.embedding_length", embed)
    w.add_uint32(f"{arch}.feed_forward_length", ff)
    w.add_uint32(f"{arch}.attention.head_count", 4)
    w.add_uint32(f"{arch}.attention.head_count_kv", kv_heads)
    w.add_uint32(f"{arch}.attention.key_length", head_dim)
    w.add_uint32(f"{arch}.attention.value_length", head_dim)
    w.add_uint32(f"{arch}.attention.full_attention_interval", full_attn_interval)
    w.add_uint32(f"{arch}.attention.nextn_predict_layers", nextn)
    if ssm:
        w.add_uint32(f"{arch}.ssm.state_size", state)
        w.add_uint32(f"{arch}.ssm.inner_size", inner)
        w.add_uint32(f"{arch}.ssm.group_count", groups)
        w.add_uint32(f"{arch}.ssm.conv_kernel", conv)
        w.add_uint32(f"{arch}.ssm.time_step_rank", dtime)
    w.add_tensor(name=f"{arch}.output_weight", tensor=np.zeros(262144, dtype=np.float32))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    return path


@pytest.fixture(scope="module")
def ab(tmp_path_factory):
    # ai-backend has no ".py" extension, so import it through a copied module.
    dst = tmp_path_factory.mktemp("abmod_vram") / "ai_backend_mod.py"
    shutil.copyfile(SRC, dst)
    spec = importlib.util.spec_from_file_location("ai_backend_mod_vram", dst)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def hermetic(ab, monkeypatch, tmp_path):
    """Hermetic env + a small real GGUF at a stable path for this test."""
    monkeypatch.setattr(ab, "ENV_FILE", Path(tmp_path / "env"))
    for var in ("LLAMA_MODEL_PATH", "CUDA_VISIBLE_DEVICES", "LLAMA_MTP",
                "LLAMA_CTX_SIZE", "LLAMA_CTX_SIZE_FACTOR", "LLAMA_CTX_SIZE_MAX",
                "LLAMA_N_GPU_LAYERS"):
        monkeypatch.delenv(var, raising=False)
    model = _write_gguf(tmp_path / "model.gguf")
    monkeypatch.setattr(ab, "split_total_size", lambda p: 16.0)  # 16 GiB weights
    monkeypatch.setattr(ab, "list_gpu_info",
                        lambda: [{"index": 0, "name": "RTX PRO 4000",
                                  "total_mib": 24467, "free_mib": 23900},
                                 {"index": 2, "name": "RTX PRO 4000",
                                  "total_mib": 24467, "free_mib": 23900}])
    yield model


def test_read_gguf_arch_hybrid(ab, hermetic):
    """Arch fields are read from nested namespaces (attention.*, ssm.*)."""
    arch = ab.read_gguf_arch(hermetic)
    assert arch is not None
    assert arch["arch"] == "qwen35"
    assert arch["layers"] == 65
    assert arch["kv_heads"] == 4
    assert arch["head_dim"] == 256
    assert arch["full_attn_interval"] == 4
    assert arch["nextn"] == 1
    assert arch["ssm"] == {"state": 16, "inner": 64, "groups": 8, "conv": 4, "dtime": 4}


def test_read_gguf_arch_missing_file(ab, hermetic):
    assert ab.read_gguf_arch("/nonexistent/model.gguf") is None


def test_estimate_vram_mb_hybrid(ab, hermetic):
    """Footprint scales with ctx; SSM state is a fixed, ctx-independent cost."""
    # 16 GiB weights * 1.05 + 3 GB overhead + ~1 MiB SSM state
    small = ab.estimate_vram_mb(hermetic, 1024)
    big = ab.estimate_vram_mb(hermetic, 262144)
    assert small is not None and big is not None
    base_gib = small["total_mb"] / 1024
    assert 17.0 < base_gib < 20.5
    # 256K ctx: 16 full-attention layers * 4 kv heads * 256 dim * 2 (K+V), q8_0
    # -> ~8 GiB.
    delta_gib = (big["total_mb"] - small["total_mb"]) / 1024
    assert 6.0 < delta_gib < 10.5, f"KV delta {delta_gib} GiB out of range"
    # MTP adds state on top of the base estimate.
    mtp = ab.estimate_vram_mb(hermetic, 0, use_mtp=True)
    assert mtp["total_mb"] > small["total_mb"]


def test_vram_preflight_blocks_unfittable_config(ab, hermetic, capsys):
    """16 GiB model + 256K ctx + MTP on a 24 GB card must be refused with hints."""
    ok = ab.vram_preflight(hermetic, 262144, [0], n_gpu="99", use_mtp=True)
    assert ok is False
    err = capsys.readouterr().err
    assert "will not fit in VRAM" in err
    assert "ai-backend ctx" in err
    assert "gpus all" in err


def test_vram_preflight_passes_fitting_config(ab, hermetic, capsys):
    """Same card, a small context, must pass (launch proceeds)."""
    ok = ab.vram_preflight(hermetic, 4096, [0], n_gpu="99", use_mtp=False)
    assert ok is True
    assert "will not fit" not in capsys.readouterr().err


def test_vram_preflight_passes_on_big_card(ab, hermetic, monkeypatch):
    monkeypatch.setattr(ab, "list_gpu_info",
                        lambda: [{"index": 1, "name": "RTX PRO 6000",
                                  "total_mib": 97887, "free_mib": 70000}])
    assert ab.vram_preflight(hermetic, 262144, [1], n_gpu="99", use_mtp=True) is True


def test_vram_preflight_relaxes_partial_offload(ab, hermetic, capsys):
    """-ngl below layer count spills to CPU RAM: warn, do not block."""
    ok = ab.vram_preflight(hermetic, 262144, [0], n_gpu="32", use_mtp=True)
    assert ok is True
    assert "relaxed" in capsys.readouterr().err


def test_vram_preflight_relaxes_n_cpu_moe(ab, hermetic, capsys):
    """--n-cpu-moe keeps experts in RAM: warn, do not block."""
    ok = ab.vram_preflight(hermetic, 262144, [0], n_gpu="99", use_mtp=False, n_cpu_moe=32)
    assert ok is True
    assert "relaxed" in capsys.readouterr().err


def test_vram_preflight_aggregates_multi_gpu(ab, hermetic, monkeypatch):
    """Two 24 GB cards can carry what one cannot."""
    monkeypatch.setattr(ab, "split_total_size", lambda p: 30.0)  # 30 GiB weights
    assert ab.vram_preflight(hermetic, 65536, [0], n_gpu="99", use_mtp=False) is False
    assert ab.vram_preflight(hermetic, 65536, [0, 2], n_gpu="99", use_mtp=False) is True


def test_vram_preflight_skips_without_gpu_info(ab, hermetic, monkeypatch, capsys):
    """No nvidia-smi data -> skip the check (launch proceeds)."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: [])
    assert ab.vram_preflight(hermetic, 262144, [0], n_gpu="99", use_mtp=True) is True
    assert "skipping pre-flight" in capsys.readouterr().err


def test_vram_preflight_unreadable_gguf_skips(ab, hermetic, tmp_path, capsys):
    """Not a GGUF header -> estimate None -> skip, never crash the launcher."""
    p = tmp_path / "junk.gguf"
    p.write_bytes(b"not a gguf at all")
    assert ab.vram_preflight(str(p), 262144, [0], n_gpu="99", use_mtp=False) is True
    assert "skipping pre-flight" in capsys.readouterr().err


def test_auto_ctx_full_offload_never_exceeds_gpu_budget(ab, hermetic, monkeypatch):
    """All layers on GPU: base + KV for the chosen ctx must fit the card."""
    monkeypatch.setattr(ab, "split_total_size", lambda p: 4.0)  # 4 GiB weights
    ctx = ab.calculate_auto_ctx(hermetic, vram_free=23900, n_gpu_layers="99",
                                gpu_indices=[0], use_mtp=False)
    assert 4096 <= ctx <= 262144
    est = ab.estimate_vram_mb(hermetic, 0, use_mtp=False)
    assert est["total_mb"] + est["kv_per_tok_mib"] * ctx <= 23900 + 2048


def test_auto_ctx_partial_offload_extends_with_ram(ab, hermetic, monkeypatch):
    """Partial offload may extend ctx via CPU RAM KV (legacy behaviour kept)."""
    monkeypatch.setattr(ab, "split_total_size", lambda p: 4.0)
    ctx = ab.calculate_auto_ctx(hermetic, vram_free=23900, n_gpu_layers="32",
                                gpu_indices=[0], use_mtp=False)
    assert ctx >= 4096


# --- biggest-card default ----------------------------------------------------
# The "serve on the biggest card by default" behaviour. A stale CUDA_VISIBLE_DEVICES
# pin (env file / systemd unit / process env) used to force a specific card so the
# auto-selection never ran; the default must now resolve to the largest-VRAM card.

BIG_CARDS = [
    {"index": 0, "name": "RTX PRO 4000", "total_mib": 24467, "free_mib": 23900},
    {"index": 1, "name": "RTX PRO 6000", "total_mib": 97887, "free_mib": 70000},
]


def test_best_single_gpu_idx_picks_largest(ab, hermetic, monkeypatch):
    """best_single_gpu_idx returns the max-total-VRAM card's index."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    assert ab.best_single_gpu_idx() == "1"


def test_default_gpu_selection_biggest_when_fits(ab, hermetic, monkeypatch):
    """Active model fits the largest single card -> that card alone."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    monkeypatch.setattr(ab, "split_total_size", lambda p: 16.0)  # 16 GiB fits 97 GB
    monkeypatch.setattr(ab, "load_env", lambda: {"LLAMA_MODEL_PATH": hermetic})
    assert ab.default_gpu_selection() == "1"


def test_default_gpu_selection_all_when_no_card_fits(ab, hermetic, monkeypatch):
    """Model too big for one card -> every detected card (tensor-split)."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    monkeypatch.setattr(ab, "split_total_size", lambda p: 100.0)  # > 97 GB free
    monkeypatch.setattr(ab, "load_env", lambda: {"LLAMA_MODEL_PATH": hermetic})
    assert ab.default_gpu_selection() == "0,1"


def test_resolve_serve_gpu_defaults_to_biggest_card(ab, hermetic, monkeypatch):
    """No pin anywhere -> the single biggest card (the whole point of the change)."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    monkeypatch.setattr(ab, "split_total_size", lambda p: 16.0)
    monkeypatch.setattr(ab, "load_env", lambda: {"LLAMA_MODEL_PATH": hermetic})
    assert ab.resolve_serve_gpu() == "1"


def test_resolve_serve_gpu_auto_sentinel_is_default(ab, hermetic, monkeypatch):
    """The 'auto' sentinel stored in the env file means 'pick the biggest card'."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    monkeypatch.setattr(ab, "split_total_size", lambda p: 16.0)
    monkeypatch.setattr(ab, "load_env",
                        lambda: {"CUDA_VISIBLE_DEVICES": "auto",
                                 "LLAMA_MODEL_PATH": hermetic})
    assert ab.resolve_serve_gpu() == "1"


def test_resolve_serve_gpu_honors_env_file_pin(ab, hermetic, monkeypatch):
    """A concrete env-file pin is honored verbatim — the default never overrides it."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    monkeypatch.setattr(ab, "load_env", lambda: {"CUDA_VISIBLE_DEVICES": "0"})
    assert ab.resolve_serve_gpu() == "0"


def test_resolve_serve_gpu_process_env_pin_wins(ab, hermetic, monkeypatch):
    """An explicit process-env pin wins over the env file / default."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    monkeypatch.setattr(ab, "load_env", lambda: {"CUDA_VISIBLE_DEVICES": "0"})
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert ab.resolve_serve_gpu() == "1"


def test_resolve_serve_gpu_process_env_auto_falls_back(ab, hermetic, monkeypatch):
    """'auto' in the process env is also treated as the biggest-card default."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    monkeypatch.setattr(ab, "split_total_size", lambda p: 16.0)
    monkeypatch.setattr(ab, "load_env", lambda: {"LLAMA_MODEL_PATH": hermetic})
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "auto")
    assert ab.resolve_serve_gpu() == "1"


def test_resolve_serve_gpu_single_card_forces_biggest(ab, hermetic, monkeypatch):
    """Draft co-location: no pin + single card required -> biggest even if it's big."""
    monkeypatch.setattr(ab, "list_gpu_info", lambda: BIG_CARDS)
    monkeypatch.setattr(ab, "split_total_size", lambda p: 100.0)  # wouldn't fit alone
    monkeypatch.setattr(ab, "load_env", lambda: {"LLAMA_MODEL_PATH": hermetic})
    assert ab.resolve_serve_gpu(need_single_card=True) == "1"


def test_sync_gpu_to_systemd_auto_clears_pin(ab, hermetic, monkeypatch, tmp_path):
    """'auto' removes the CUDA_VISIBLE_DEVICES pin from the unit file."""
    unit = tmp_path / "llama-server.service"
    unit.write_text(
        "[Unit]\n[Service]\n"
        "Environment=CUDA_VISIBLE_DEVICES=1\n"
        "Environment=LLAMA_CTX_SIZE=131072\n"
        "ExecStart=/bin/true\n"
    )
    monkeypatch.setattr(ab, "SYSTEMD_SERVICE", unit)
    monkeypatch.setattr(ab.subprocess, "run", lambda *a, **k: None)  # no real systemctl
    ab.sync_gpu_to_systemd("auto")
    text = unit.read_text()
    assert "CUDA_VISIBLE_DEVICES" not in text
    assert "LLAMA_CTX_SIZE=131072" in text


def test_sync_gpu_to_systemd_writes_concrete_pin(ab, hermetic, monkeypatch, tmp_path):
    """A concrete pin is written into the unit file."""
    unit = tmp_path / "llama-server.service"
    unit.write_text("[Unit]\n[Service]\nEnvironment=LLAMA_CTX_SIZE=131072\nExecStart=/bin/true\n")
    monkeypatch.setattr(ab, "SYSTEMD_SERVICE", unit)
    monkeypatch.setattr(ab.subprocess, "run", lambda *a, **k: None)
    ab.sync_gpu_to_systemd("0")
    assert "Environment=CUDA_VISIBLE_DEVICES=0" in unit.read_text()
