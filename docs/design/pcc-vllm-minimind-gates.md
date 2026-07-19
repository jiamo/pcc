# pcc vLLM-Metal / MiniMind GPU-Package Gates (First Slice)

Status: first-slice landed (metadata-only + skip taxonomy). No GPU / serving /
training / throughput claim anywhere.

This document is the contract for two sibling P0 gate rows:

- **B-P0-VLLM-METAL-KV** — a mode-labeled package-surface probe for the current
  vLLM Apple-Silicon path (`vllm-metal` plugin + `mlx`) plus a **local KV-block
  metadata surrogate** (paged-attention block table: block-id, refcount,
  prefix-hash, pin/unpin, eviction), all CPU-only and deterministic.
- **B-P0-MINIMIND-GPU** — a pinned MiniMind package/application gate **shape**
  with explicit device-mode labels, running CPU-only surrogates and
  run-or-skip-with-reason for every device mode.

Both gates live under `tests/gpu_packages/`:

| File | Role |
|---|---|
| `tests/gpu_packages/gpu_gate_common.py` | Shared skip taxonomy + REAL CPU-only surrogates (KV block table, MiniMind arg/mode/config resolution, synthetic corpus) |
| `tests/gpu_packages/test_vllm_metal_kv.py` | B-P0-VLLM-METAL-KV gate |
| `tests/gpu_packages/test_minimind_gpu.py` | B-P0-MINIMIND-GPU gate |

Exact command MAIN runs:

```bash
env -u LC_ALL uv run pytest tests/gpu_packages -q -n0
```

To see the skip reasons (mode labels are greppable in the output):

```bash
env -u LC_ALL uv run pytest tests/gpu_packages -q -n0 -rs
```

---

## Mode-label taxonomy

Two orthogonal axes. Every skip reason carries a `SKIPPED_WITH_REASON:` prefix
and the relevant label, so `pytest -rs` output is greppable.

### Axis 1 — execution mode (device / framework)

| Label | Device | Framework | Runs when | Otherwise |
|---|---|---|---|---|
| `cpu` | cpu | none | always (pure metadata surrogate) | — |
| `torch-mps` | mps | torch | `torch.backends.mps.is_available()` | `SKIPPED_WITH_REASON: torch ... (mode=torch-mps)` |
| `mlx-metal` | metal | mlx | `mlx` importable | `SKIPPED_WITH_REASON: mlx absent (mode=mlx-metal)` |
| `pcc-metal-kernel` | metal | pcc | **never in this slice** | `SKIPPED_WITH_REASON: pcc-native Metal kernel execution is not claimed ... (mode=pcc-metal-kernel)` |
| `cuda` (rejected) | cuda:N | torch | never on Apple hardware | `SKIPPED_WITH_REASON: cuda device not available ... (mode=cuda)` |

`pcc-metal-kernel` is a **labeled intent only**. Proving it requires a pcc1
build + a Metal backend, which the gate environment must not perform. It always
skips with a reason so the mode is honestly *not claimed*.

### Axis 2 — libpython / ABI compat (kept distinct on purpose)

| Label | Meaning |
|---|---|
| `cpython-compat` | extension built against the CPython ABI (torch / mlx / vllm-metal C-extensions) |
| `pcc-native` | extension built against the pcc-native ABI |
| `no-libpython` | the `--python-libpython=off` boundary: CPython-compat extensions must be **rejected** |

These three are never conflated. The gates feed CPython-suffixed artifacts with
a libpython edge through pcc's generic native-artifact linkage scanner and
assert fail-closed `PCC-PKG-003` / `PCC-PKG-004` diagnostics. This is real
package-boundary proof; it does not claim a compiled pcc1 imported or executed
those packages.

### Package-surface presence probe

`probe_packages(names)` sweeps a data-driven list uniformly via
`importlib.util.find_spec`. There is **no `if package == "numpy"`** (or any
package) special-casing — every dependency name is treated identically, per
AGENTS.md Package/NumPy Claim Hygiene.

---

## Test / module map (which modes run vs skip)

### `test_vllm_metal_kv.py`

| Test | Mode | Runs vs skips |
|---|---|---|
| `test_vllm_metal_package_surface_taxonomy` | surface probe | runs the probe always; skips the *present-branch* assertion when `vllm-metal` absent (SKIPPED_WITH_REASON) |
| `test_kv_block_alloc_and_free_refcount` | cpu | always runs |
| `test_kv_block_double_free_raises` | cpu | always runs |
| `test_kv_block_prefix_hash_reuse_touches_same_block` | cpu | always runs |
| `test_kv_block_prefix_chain_distinct_hashes` | cpu | always runs |
| `test_kv_block_pin_prevents_eviction` | cpu | always runs |
| `test_kv_block_unpin_non_pinned_raises` | cpu | always runs |
| `test_kv_block_eviction_invalidates_hash_index` | cpu | always runs |
| `test_kv_block_oom_surrogate_raises_not_wraps` | cpu | always runs |
| `test_kv_block_cache_hit_rate_is_metadata_only` | cpu | always runs |
| `test_kv_block_free_queue_tail_lru_order` | cpu | always runs |
| `test_libpython_off_rejects_cpython_extension_surface` | no-libpython | always runs the real generic artifact/linkage rejection path; no pcc1 build |

### `test_minimind_gpu.py`

| Test | Mode | Runs vs skips |
|---|---|---|
| `test_minimind_dependency_surface_is_enumerable` | package inspect | always runs |
| `test_minimind_mode_resolution_run_or_skip[cpu]` | cpu | always runs |
| `test_minimind_mode_resolution_run_or_skip[torch-mps]` | torch-mps | runs metadata; skips if torch/MPS absent |
| `test_minimind_mode_resolution_run_or_skip[mlx-metal]` | mlx-metal | runs metadata; skips if mlx absent |
| `test_minimind_mode_resolution_run_or_skip[pcc-metal-kernel]` | pcc-metal-kernel | always skips (not claimed) |
| `test_pcc_metal_kernel_mode_is_never_claimed` | pcc-metal-kernel | always runs (asserts the skip contract) |
| `test_minimind_config_shape_default_matches_upstream_arithmetic` | cpu | always runs |
| `test_minimind_config_shape_moe_and_custom_dims` | cpu | always runs |
| `test_minimind_config_shape_rejects_bad_dims` | cpu | always runs |
| `test_minimind_synthetic_dataset_entry_smoke` | cpu | always runs |
| `test_pretrain_command_shape_is_wellformed` | cpu | always runs |
| `test_minimind_default_device_rule` | cpu | always runs |
| `test_one_step_train_dry_run_or_skip[torch-mps]` | torch-mps | asserts SHAPE; skips real step (framework absent → skip; present → still skipped, no training claim) |
| `test_one_step_train_dry_run_or_skip[mlx-metal]` | mlx-metal | same as above for mlx |
| `test_one_step_train_dry_run_or_skip[pcc-metal-kernel]` | pcc-metal-kernel | always skips (not claimed) |
| `test_cpu_mode_dry_run_shape_runs` | cpu | always runs (metadata dry-run: steps-per-epoch) |
| `test_inference_load_failure_classification` | cpu | always runs |
| `test_libpython_off_rejects_pytorch_extension_surface` | no-libpython | always runs the real generic artifact/linkage rejection path; no pcc1 build |

On a typical CPU-only macOS host (no torch/mlx installed), the CPU-mode and two
generic no-libpython linkage tests **run and pass**; the `torch-mps` /
`mlx-metal` / `pcc-metal-kernel` tests **skip with reasons**.

---

## What each gate proves vs explicitly does NOT prove

### B-P0-VLLM-METAL-KV

**Proves:**

- The vLLM Apple-Silicon package surface (`vllm-metal`, `vllm`, `mlx`) is
  probed with a uniform, mode-labeled skip taxonomy (present → placeholder
  assertion; absent → `SKIPPED_WITH_REASON`).
- A **real** paged-attention KV-block metadata contract holds on CPU:
  - stable block-ids allocated from / returned to a free queue;
  - refcount discipline (touch/incref/free), double-free rejected (not wrapped);
  - content-addressed prefix hashing `hash(parent_hash + block_tokens)` with
    reuse "touch" semantics;
  - pin/unpin keeping a block resident at refcount 0;
  - LRU eviction of the oldest-freed **cached** block, invalidating its
    content-hash mapping;
  - OOM surrogate raises rather than silently wrapping.

**Does NOT prove:**

- No pcc-native vLLM / MLX / Metal execution.
- No attention math, no KV **tensor** data movement, no GPU residency.
- No throughput / scaling / serving / cache-hit-rate-in-production claim
  (`cache_hit_rate` is a deterministic metadata count over the hash index, not
  a serving metric).
- The generic `--python-libpython=off` artifact/linkage boundary is proven; a
  pcc1 import or vLLM/MLX runtime execution is not.

### B-P0-MINIMIND-GPU

**Proves:**

- MiniMind's declared dependency surface is enumerable and its device modes are
  resolved via a uniform, mode-labeled probe.
- MiniMind **config-shape** derivation is real and asserted:
  `intermediate_size = ceil(hidden_size * pi / 64) * 64`,
  `head_dim = hidden_size // num_attention_heads`, `vocab_size` default 6400,
  MoE flag — mirroring `model/model_minimind.py` **without importing torch**.
- The device-default rule `cuda:0 if cuda else cpu` (from
  `trainer/train_pretrain.py`) is asserted as pure metadata.
- The `train_pretrain.py` argv **shape** is well-formed; a CPU metadata
  "dry-run" computes steps-per-epoch from a tiny **synthetic** token stream
  (no downloaded weights/datasets).
- Bounded inference-load requests are classified deterministically into labeled
  failure categories (`ADMISSIBLE` / `OOM_BLOCK_BUDGET_EXCEEDED` /
  `INVALID_REQUEST` / `FRAMEWORK_ABSENT`).

**Does NOT prove:**

- No pcc-native MiniMind / PyTorch / MLX / Metal execution.
- No real training step, no serving, no throughput / accuracy / scaling claim.
- The `torch-mps` / `mlx-metal` / `pcc-metal-kernel` modes are **not** claimed
  to run here (they skip with reasons even when the framework is present — a
  real one-step train is deferred to a MAIN-run harness).
- The generic `--python-libpython=off` artifact/linkage boundary is proven; a
  pcc1 import or MiniMind/PyTorch runtime execution is not.

---

## Claim-boundary lines (for the goal doc)

Copy these verbatim into the goal doc rows. They must NOT be strengthened
without a real device/framework/pcc1 run.

- **B-P0-VLLM-METAL-KV (first slice):** landed a mode-labeled `vllm-metal`/`mlx`
  package-surface probe (present/absent → `SKIPPED_WITH_REASON`) and a
  deterministic CPU-only paged-attention KV-block metadata surrogate (block-id /
  refcount / prefix-hash / pin-unpin / eviction). **Proves the metadata
  contract only. Does NOT claim pcc-native vLLM / MLX / Metal execution, KV
  tensor movement, serving, or any throughput/scaling number.** The
  `--python-libpython=off` rejection of `cpython-compat` artifacts is proven by
  the generic linkage scanner; this is not a pcc1 import/execution claim.
- **B-P0-MINIMIND-GPU (first slice):** landed a pinned MiniMind package/
  application gate **shape** with device-mode labels `cpu` / `torch-mps` /
  `mlx-metal` / `pcc-metal-kernel`, each run-or-skip-with-reason; CPU-only
  surrogates for arg/mode resolution and config-shape derivation over tiny
  synthetic data. **Proves the command/config shape only. Does NOT claim
  pcc-native MiniMind / PyTorch / MLX / Metal training, inference, serving, or
  any throughput/accuracy/scaling number; no weights or datasets are
  downloaded.** The `--python-libpython=off` rejection of CPython/PyTorch-shaped
  artifacts is proven by the generic linkage scanner; this is not a pcc1
  import/execution claim.
- **Mode distinctness:** `host pcc != pcc1`; `cpython-compat != pcc-native`;
  `libpython != no-libpython`. These gates operate at the host-Python metadata
  level and make no pcc1 / no-libpython / self-backend claim.

---

## Risk notes

- **Skip-inflation risk.** On a CPU-only host most device-mode tests skip. That
  is intended (honest not-claimed), but it means the gate's *positive* signal is
  the CPU surrogate + config-shape assertions. If someone reads "N passed, M
  skipped" as "GPU works", that is a misread — the design doc labels are the
  guard.
- **Surrogate ≠ implementation.** The KV block table mirrors vLLM's *bookkeeping
  vocabulary* (touch/free/evict/prefix-hash), not vLLM's actual allocator. It is
  a contract fixture for a future GPU/Metal collector, not evidence one exists.
- **Config-shape drift.** `derive_minimind_config` hard-codes upstream defaults
  and the `ceil(h*pi/64)*64` arithmetic from `model/model_minimind.py`. If
  MiniMind changes that formula, the config-shape test will (correctly) fail and
  must be re-synced against the pinned upstream commit.
- **Linkage-probe scope.** The two `no-libpython` tests exercise the real generic
  artifact scanner, including both libpython-edge and CPython-ABI diagnostics.
  They must not be described as pcc1 import or framework execution evidence.
- **No pcc1 here by construction.** These gates deliberately avoid `uv run pcc`
  / bootstrap so they stay cheap and runnable in any host. The genuine
  no-libpython and pcc-native-ABI claims must be proven by the existing
  bootstrap gates (`tests/python/test_fallback_baseline.py`,
  `tests/python/test_bootstrap_gate_baseline.py`), which MAIN owns.
```
