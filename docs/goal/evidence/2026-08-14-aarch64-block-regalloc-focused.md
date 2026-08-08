# AArch64 block-local regalloc focused evidence (2026-08-14)

Mode: host-pcc self-backend unit/LLVM-oracle parity on the local Darwin target.
No bootstrap stage or stage2 timing measurement was run.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/c/test_self_backend_aarch64_regalloc.py \
  tests/c/test_llvm_self_vector_parity.py::test_llvm_self_block_local_regalloc_result_matches
11 passed in 0.67s
```

The finite allocator reuses expired registers, reduces hot-block slot traffic,
spills at calls/PHIs/cross-block uses/pressure, preserves float/vector spill
lanes, and matches the LLVM execution oracle. Pinned before/after traffic,
stage2 wall time and final bootstrap remain open.
