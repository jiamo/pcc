# ds4 pin + migration inventory + external oracle (first slice)

Date: 2026-07-08

Task: `DS4-P0-INVENTORY-ORACLE`

Scope:
- Pin the ds4 reference tree and produce a checked-in migration inventory of its
  C / Metal / CUDA / ROCm / GGUF / distributed / KV surfaces plus a definition
  of external oracle vectors. Inventory + oracle metadata ONLY — no pcc compile
  of ds4, no runtime, no GPU execution, no pcc ds4 support claim.

Changed files (all new; no pcc/ source or ds4 tree touched):
- `tests/ds4_oracle/__init__.py`
- `tests/ds4_oracle/ds4_inventory.json` — checked-in machine-readable manifest.
- `tests/ds4_oracle/test_ds4_inventory.py` — gate test.
- `docs/design/pcc-ds4-inventory.md` — design doc (pinned commit + surfaces +
  oracle-vector definitions).

Pinned reference: `~/pcc_refs/antirez-ds4-depth1` HEAD
`80ebbc396aee40eedc1d829222f3362d10fa4c6c` (recorded in manifest and doc).

Inventoried surfaces:
- C/runtime core: 20 files (engine `ds4.c`, `ds4.h`, kvstore, ssd, server, cli,
  agent, bench, eval, web, help, + rax/linenoise deps) with line counts.
- GPU API surface: `ds4_gpu.h` — opaque `ds4_gpu_tensor` + 86 entrypoints.
- Metal: host `ds4_metal.m` + 19 `metal/*.metal` kernels mapped to the
  copy/bin/dense/KV/RoPE/attention/MoE/norm/softmax/repeat/sum rows.
- CUDA/ROCm: `ds4_cuda.cu`, `ds4_rocm.cu/.h`, 22 `rocm/*.cuh`, 2 `.inc` tables.
- GGUF: `gguf-tools/` (quantize + quants + subdirs).
- Distributed: `ds4_distributed.c/.h` — DS4D wire magic `0x44533444`, 9 message
  ops, 3 result kinds, 4 work flags, 14 public `ds4_dist_*` functions.
- KV/SSD: 13 kvstore + 6 ssd functions/structs.
- ds4 Makefile targets recorded for the later compile-subset slice.

External oracle vectors (defined, not run by pcc): `tests/ds4_test.c`,
`tests/test_q4k_dot.c`, `tests/cuda_long_context_smoke.c`,
`tests/ds4_agent_test.c`, `tests/test-vectors/manifest.json`.

Claim boundary (enforced + tested): manifest carries
`pcc_support_claimed: false`, `stage: "inventory-oracle-only"`,
`task_row: "DS4-P0-INVENTORY-ORACLE"`, and a reference note stating no pcc ds4
support is claimed. `test_manifest_declares_no_pcc_support` asserts these. Core
assertions read only the checked-in JSON (REPO_ROOT via walk-up to AGENTS.md);
the single live-tree cross-check `pytest.skip`s when the out-of-repo tree is
absent, so PASS never depends on `~/pcc_refs`.

Gate (owner-run): `gtimeout 120s env -u LC_ALL uv run pytest -q -n0
tests/ds4_oracle/test_ds4_inventory.py` -> `10 passed`. `git diff --check` clean.

Result: DONE_WEAK.

Claim: ds4 is pinned and its surfaces are inventoried into a checked-in manifest
with external oracle vectors defined and the no-support claim boundary enforced
by a passing gate.

Open boundary: oracle vectors are referenced/defined but their golden VALUES are
not captured into the repo (they live in the external ds4 tree); the CPU compile
subset, GPU API -> pcc HMM/Tensor/Fence mapping, and first primitive migration
are separate rows (`DS4-P1-CPU-COMPILE-SUBSET`, `DS4-P2-GPU-API-MAPPING`,
`DS4-P3-PRIMITIVE-ORACLE`) and are not started here.
