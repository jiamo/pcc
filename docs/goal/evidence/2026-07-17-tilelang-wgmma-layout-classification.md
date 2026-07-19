# TileLang WGMMA layout target classification

Date: 2026-07-17

Task: `GPU-P2-TILELANG-ADVANCED-LAYOUT-LOWERING`

## Pinned oracle

- TileLang commit: `ed00dfcd7f9c200e1150896b1be59c41ff3e8d9d`
- Path: `tilelang/layout/swizzle.py`
- SHA-256: `0389a53684dec7697bd22c8e1b30f30a6a1afc5e02980de540c277080082bb55`
- Selected helper: `make_wgmma_swizzled_layout`, explicitly introduced by
  the upstream source as a WGMMA-intrinsic layout.

## Proven slice

- The strict source importer accepts exactly one rank-2 shared-buffer
  `make_wgmma_swizzled_layout` annotation with static `continuity` and
  `k_major` settings.
- Kernel IR preserves the TileLang helper name, owner, upstream
  `tilelang.transform.LayoutInference` pass identity, pinned reference,
  required `cuda-sm90-wgmma` target, and WGMMA target marker. It deliberately
  does not relabel the buffer as pcc's ordinary Metal-compatible swizzle.
- Plain-TIR freeze for a CUDA target retains that identity as
  `tir.annotate_layout` metadata. This is representation proof, not CUDA
  codegen or execution.
- Metal TIRx freeze and Metal source emission both reject the WGMMA marker as
  a CUDA-only assumption. There is no silent downgrade to an ordinary swizzle.
- Non-boolean options, non-shared targets, and multiple advanced entries fail
  closed; existing empty and Metal-compatible swizzle annotations remain green.

## Gates

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_advanced_layout_lowering.py -rs
```

Result: `7 passed in 0.42s`.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_tilelang_import_broader.py::test_empty_annotate_layout_is_noop_metadata_for_cpu_oracle_and_metal_source \
  tests/kernel/test_tilelang_import_broader.py::test_swizzled_annotate_layout_preserves_metadata_and_metal_source_applies_layout
```

Result: `2 passed in 0.51s`.

## Claim boundary

This proves owner/pass-preserving import, CUDA-target plain-TIR metadata freeze,
and explicit Metal illegality for one upstream WGMMA layout. It does not prove
CUDA code generation, SM90 hardware execution, WGMMA descriptors or
instructions, TMA, clusters, performance, TileLang/TVM runtime ownership,
pcc1 ownership, or whole-program GPU execution.
