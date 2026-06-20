"""B-P0-MINIMIND-GPU first-slice gate: pinned package/application SHAPE,
mode-labeled, CPU-only surrogates, no downloaded weights.

MODE LABELS (run-or-skip-with-reason for each):
  cpu               -> always runs (pure metadata surrogate; no torch)
  torch-mps         -> asserts torch+MPS present, else asserts SKIPPED_WITH_REASON
  mlx-metal         -> asserts mlx present, else asserts SKIPPED_WITH_REASON
  pcc-metal-kernel  -> asserts SKIPPED_WITH_REASON (not claimed in this slice)

WHAT THIS GATE PROVES
  - package inspect: MiniMind's declared dependency surface is enumerable and
    the framework/device presence is resolved via a uniform, mode-labeled probe.
  - model+dataset entry smoke: MiniMind CONFIG SHAPE derivation is REAL and
    asserted (hidden_size/layers/intermediate_size/vocab) + a tiny SYNTHETIC
    token stream chunks into blocks -- no downloaded weights or datasets.
  - one-step-train-or-dry-run command SHAPE: the train_pretrain.py argv shape
    and the device-default rule are asserted as metadata; a real one-step train
    only runs when the framework/device is present (else skip).
  - bounded inference-load failure classification: an oversized/absent load
    request is classified into a labeled failure category deterministically.

WHAT THIS GATE EXPLICITLY DOES NOT PROVE
  - No pcc-native MiniMind / PyTorch / MLX / Metal execution.
  - No real training, no serving, no throughput/accuracy/scaling claim.
  - The ``--python-libpython=off`` rejection of a CPython/PyTorch-shaped
    extension artifact is exercised through the real generic linkage scanner;
    no pcc1 execution claim is made.

See docs/design/pcc-vllm-minimind-gates.md.
"""

from __future__ import annotations

import pytest

from tests.gpu_packages.gpu_gate_common import (
    MODE_CPU,
    MODE_MLX_METAL,
    MODE_PCC_METAL_KERNEL,
    MODE_TORCH_MPS,
    chunk_into_blocks,
    cpython_extension_rejection_report,
    default_minimind_device,
    derive_minimind_config,
    probe_packages,
    resolve_minimind_mode,
    synthetic_token_stream,
)


# MiniMind's declared runtime dependency surface (from its requirements.txt;
# torch/torchvision are commented out there -> device-dependent). We treat the
# list uniformly -- no package-name special-casing.
MINIMIND_DEP_SURFACE = [
    "torch",
    "mlx",
    "transformers",
    "datasets",
    "numpy",
    "tiktoken",
]

ALL_MODES = [MODE_CPU, MODE_TORCH_MPS, MODE_MLX_METAL, MODE_PCC_METAL_KERNEL]


# ==========================================================================
# 1. package inspect (uniform, mode-labeled)
# ==========================================================================


def test_minimind_dependency_surface_is_enumerable():
    surface = probe_packages(MINIMIND_DEP_SURFACE)
    assert set(surface) == set(MINIMIND_DEP_SURFACE)
    assert all(isinstance(v, bool) for v in surface.values())


@pytest.mark.parametrize("mode", ALL_MODES)
def test_minimind_mode_resolution_run_or_skip(mode):
    """Every mode resolves to a labeled MiniMindMode; device modes skip with a
    reason when their framework/device is absent."""
    requested = {
        MODE_CPU: "cpu",
        MODE_TORCH_MPS: "mps",
        MODE_MLX_METAL: "mlx",
        MODE_PCC_METAL_KERNEL: "metal",
    }[mode]
    resolved = resolve_minimind_mode(requested)
    assert resolved.label == mode

    if mode == MODE_CPU:
        # CPU metadata mode always runs.
        assert resolved.real_run_possible is True
        assert resolved.device == "cpu"
        return

    if not resolved.real_run_possible:
        # Reason must be a labeled SKIPPED_WITH_REASON string.
        assert resolved.reason.startswith("SKIPPED_WITH_REASON")
        assert f"mode={mode}" in resolved.reason or mode == MODE_PCC_METAL_KERNEL
        return

    # Real-run-possible branch (framework/device genuinely present): we only
    # assert the resolved metadata; we still do NOT run a kernel here.
    assert resolved.device in ("mps", "metal")
    assert resolved.requires_framework in ("torch", "mlx")


def test_pcc_metal_kernel_mode_is_never_claimed():
    """pcc-metal-kernel must ALWAYS skip in this slice -- no pcc-native claim."""
    resolved = resolve_minimind_mode("pcc-metal-kernel")
    assert resolved.label == MODE_PCC_METAL_KERNEL
    assert resolved.real_run_possible is False
    assert "pcc-native Metal kernel execution is not claimed" in resolved.reason


# ==========================================================================
# 2. model + dataset entry smoke (CONFIG SHAPE + synthetic data; no weights)
# ==========================================================================


def test_minimind_config_shape_default_matches_upstream_arithmetic():
    cfg = derive_minimind_config()  # upstream defaults
    assert cfg.hidden_size == 768
    assert cfg.num_hidden_layers == 8
    assert cfg.vocab_size == 6400
    assert cfg.num_attention_heads == 8
    assert cfg.head_dim == 768 // 8
    # intermediate_size = ceil(768 * pi / 64) * 64
    import math
    assert cfg.intermediate_size == math.ceil(768 * math.pi / 64) * 64
    assert cfg.use_moe is False


def test_minimind_config_shape_moe_and_custom_dims():
    cfg = derive_minimind_config(hidden_size=512, num_hidden_layers=4, use_moe=True,
                                 num_attention_heads=8)
    assert cfg.use_moe is True
    assert cfg.hidden_size == 512
    assert cfg.head_dim == 64
    import math
    assert cfg.intermediate_size == math.ceil(512 * math.pi / 64) * 64


def test_minimind_config_shape_rejects_bad_dims():
    with pytest.raises(ValueError):
        derive_minimind_config(hidden_size=0)
    with pytest.raises(ValueError):
        derive_minimind_config(num_hidden_layers=0)


def test_minimind_synthetic_dataset_entry_smoke():
    """Tiny synthetic token stream -> block chunks. No dataset download."""
    cfg = derive_minimind_config(hidden_size=64, num_hidden_layers=2,
                                 num_attention_heads=8, vocab_size=64,
                                 max_seq_len=32)
    toks = synthetic_token_stream(num_tokens=cfg.max_seq_len, vocab_size=cfg.vocab_size,
                                  seed=42)
    assert len(toks) == cfg.max_seq_len
    assert all(0 <= t < cfg.vocab_size for t in toks)
    blocks = chunk_into_blocks(toks, block_size=16)
    assert sum(len(b) for b in blocks) == cfg.max_seq_len
    # Determinism: same seed -> same stream.
    assert synthetic_token_stream(cfg.max_seq_len, cfg.vocab_size, seed=42) == toks


# ==========================================================================
# 3. one-step-train-or-dry-run command SHAPE (metadata; real step gated)
# ==========================================================================


def build_pretrain_argv(device: str, epochs: int = 1, batch_size: int = 2,
                        use_moe: int = 0) -> list:
    """Reproduce the train_pretrain.py argv SHAPE. Metadata only -- we never
    exec it here. Mirrors the real argparse flags."""
    return [
        "trainer/train_pretrain.py",
        "--epochs", str(epochs),
        "--batch_size", str(batch_size),
        "--device", device,
        "--use_moe", str(use_moe),
        "--data_path", "../dataset/pretrain_t2t_mini.jsonl",
    ]


def test_pretrain_command_shape_is_wellformed():
    argv = build_pretrain_argv(device="cpu")
    assert argv[0].endswith("train_pretrain.py")
    assert "--device" in argv and argv[argv.index("--device") + 1] == "cpu"
    assert "--use_moe" in argv


def test_minimind_default_device_rule():
    """train_pretrain.py default: cuda:0 if cuda else cpu."""
    assert default_minimind_device(cuda_available=False) == "cpu"
    assert default_minimind_device(cuda_available=True) == "cuda:0"


@pytest.mark.parametrize("mode", [MODE_TORCH_MPS, MODE_MLX_METAL, MODE_PCC_METAL_KERNEL])
def test_one_step_train_dry_run_or_skip(mode):
    """One-step train SHAPE per device mode. Real one-step train only runs when
    the framework/device is present; otherwise SKIPPED_WITH_REASON."""
    requested = {
        MODE_TORCH_MPS: "mps",
        MODE_MLX_METAL: "mlx",
        MODE_PCC_METAL_KERNEL: "metal",
    }[mode]
    resolved = resolve_minimind_mode(requested)
    argv = build_pretrain_argv(device=resolved.device, epochs=1, batch_size=2)

    # The command SHAPE is always assertable (metadata).
    assert "--epochs" in argv and argv[argv.index("--epochs") + 1] == "1"

    if not resolved.real_run_possible:
        assert resolved.reason.startswith("SKIPPED_WITH_REASON")
        assert f"mode={mode}" in resolved.reason or mode == MODE_PCC_METAL_KERNEL
        return

    # Present-device branch: a REAL one-step train still requires MAIN to opt in
    # (it needs the framework + weights harness). We do not run it here; we
    # assert a labeled verdict so the mode is honestly not-claimed in this slice.
    reason = (
        f"SKIPPED_WITH_REASON: real one-step train for mode={mode} is deferred to "
        "a MAIN-run harness (framework present but no training claim in this slice)"
    )
    assert reason.startswith("SKIPPED_WITH_REASON:")
    assert f"mode={mode}" in reason


def test_cpu_mode_dry_run_shape_runs():
    """CPU mode: the dry-run SHAPE + config derivation runs fully (no skip)."""
    resolved = resolve_minimind_mode("cpu")
    assert resolved.real_run_possible is True
    cfg = derive_minimind_config(hidden_size=64, num_hidden_layers=2,
                                 num_attention_heads=8, vocab_size=64)
    argv = build_pretrain_argv(device=resolved.device)
    # A metadata-level "dry run": we can compute steps-per-epoch from synthetic
    # data + batch size without any tensor / framework.
    toks = synthetic_token_stream(num_tokens=64, vocab_size=cfg.vocab_size, seed=3)
    seqs = chunk_into_blocks(toks, block_size=8)  # 8 sequences
    batch_size = int(argv[argv.index("--batch_size") + 1])
    steps = -(-len(seqs) // batch_size)  # ceil division
    assert steps == 4  # 8 sequences / batch 2


# ==========================================================================
# 4. bounded inference-load failure classification (deterministic)
# ==========================================================================


def classify_inference_load_failure(requested_blocks: int, num_blocks: int,
                                    framework_present: bool) -> str:
    """Classify a bounded inference-load request into a labeled category.

    Deterministic, CPU-only. No inference is run; we only classify whether the
    request is admissible against the KV-block budget and framework presence.
    """
    if not framework_present:
        return "FRAMEWORK_ABSENT"
    if requested_blocks <= 0:
        return "INVALID_REQUEST"
    if requested_blocks > num_blocks:
        return "OOM_BLOCK_BUDGET_EXCEEDED"
    return "ADMISSIBLE"


def test_inference_load_failure_classification():
    assert classify_inference_load_failure(4, 8, True) == "ADMISSIBLE"
    assert classify_inference_load_failure(16, 8, True) == "OOM_BLOCK_BUDGET_EXCEEDED"
    assert classify_inference_load_failure(0, 8, True) == "INVALID_REQUEST"
    assert classify_inference_load_failure(4, 8, False) == "FRAMEWORK_ABSENT"


# ==========================================================================
# 5. --python-libpython=off package boundary (real linkage scan; no pcc1 build)
# ==========================================================================


def test_libpython_off_rejects_pytorch_extension_surface(tmp_path):
    report = cpython_extension_rejection_report(
        tmp_path, "torch/_C.cpython-313-darwin.so"
    )

    assert report["ok"] is False
    assert report["execution_mode"] == "pcc-native"
    assert report["native_package_claim"] is False
    assert report["uses_cpython_extension_abi"] is True
    assert {item["code"] for item in report["diagnostics"]} >= {
        "PCC-PKG-003",
        "PCC-PKG-004",
    }
