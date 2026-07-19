# TileLang expanded ceildiv split-K alias runtime proof

Date: 2026-07-09

Scope:
- Broadened the TileLang/TIRx split-K importer to recognize the exact expanded
  ceildiv expression `(K + split_k - 1) // split_k` as the same split-K span
  provenance as `T.ceildiv(K, split_k)`.
- The recognized expression works both directly in source-index expressions and
  through statically evaluated outer aliases such as:

  ```python
  splitK = (K + split_k - 1) // split_k
  ...
  T.copy(A[by * block_M, bz * splitK + ko * block_K], A_shared)
  ```

- The importer records `split_k_span_mode="ceildiv"` and the evaluated span,
  so downstream CPU oracle and Metal source lowering use `min(split_k0 + span,
  K)` for non-divisible tails.
- This is a TileLang source-subset / Kernel IR / TIRx / Metal runtime-source
  proof. It is not a TileLang runtime execution, not a whole-program GPU claim,
  and not a package import claim.

Evidence:
- New importer test:
  `test_splitk_atomic_expanded_ceildiv_alias_survives_import_freeze_source_and_cpu_oracle`
  proves the expanded alias survives Kernel IR import, plain-TIR freeze, CPU
  oracle execution, and Metal source emission for `K=17, split_k=4`.
- New runtime-source test:
  `test_imported_tilelang_splitk_atomic_expanded_ceildiv_alias_runtime_source_matches_cpu_oracle`
  proves the same source shape reaches Metal runtime-source command-buffer
  execution, fence completion, device readback, and exact CPU-oracle comparison
  on this Darwin/Metal machine.

Gates:
- `gtimeout 60s env -u LC_ALL uv run python -m py_compile pcc/kernel_ir/tilelang_import.py tests/kernel/test_tilelang_import_broader.py tests/kernel/test_metal_tilelang_gemm_runtime.py`
  - passed
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py::test_splitk_atomic_expanded_ceildiv_alias_survives_import_freeze_source_and_cpu_oracle -rs`
  - `1 passed in 0.14s`
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py::test_imported_tilelang_splitk_atomic_expanded_ceildiv_alias_runtime_source_matches_cpu_oracle -rs`
  - `1 passed in 1.95s`
- `gtimeout 300s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py tests/kernel/test_tilelang_import_broader.py -rs`
  - `77 passed in 0.15s`
- `gtimeout 600s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_tilelang_gemm_runtime.py -rs`
  - `24 passed in 16.22s`

Not claimed:
- No arbitrary split-K expression support beyond the covered exact forms:
  `K // split_k`, `T.ceildiv(K, split_k)`, and `(K + split_k - 1) // split_k`.
- No TileLang/TVM runtime execution.
- No `.air/.metallib` production in this slice.
- No pcc1/no-libpython, pcc1->pcc2->pcc3 bootstrap, or five-GC proof for this
  exact expanded-expression slice.
- No performance claim.

