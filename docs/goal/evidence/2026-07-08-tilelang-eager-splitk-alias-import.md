# TileLang eager split-K alias import

Date: 2026-07-08

Scope:
- Broadened the TileLang importer beyond inner `@T.prim_func` only.
- The importer now accepts the strict eager-style `@tilelang.jit` shape with
  static `A: T.Tensor(...)` declarations before exactly one `T.Kernel` region.
- The importer also evaluates statically-known outer aliases such as
  `splitK = K // split_k` and preserves split-K span provenance
  (`floor_div` vs `ceildiv`) for copy metadata.
- This is importer/TIRx/CPU-oracle/Metal-source coverage only. It is not a new
  `.metallib`, pcc1, five-GC, performance, or whole-program GPU claim.

Reference:
- The covered source shape mirrors the local TileLang split-K examples under
  `/Users/jiamo/tilelang/examples/gemm_splitk/`, which define `splitK = K //
  split_k`, use `T.ceildiv(splitK, block_K)`, and index global tiles with
  `bz * splitK + ko * block_K`.

Evidence:
- `test_splitk_atomic_outer_split_alias_survives_import_freeze_source_and_cpu_oracle`
  proves lazy outer alias preservation through Kernel IR, plain TIR freeze,
  CPU oracle, and Metal source.
- `test_splitk_atomic_outer_floor_div_alias_tail_fails_closed` proves the alias
  keeps `floor_div` provenance, so non-divisible `K/split_k` still fails closed
  in both CPU oracle and Metal finalize.
- `test_eager_splitk_atomic_alias_source_survives_import_freeze_source_and_cpu_oracle`
  proves the eager-style tensor declaration path imports A/B/C params, preserves
  split-K alias metadata, executes the CPU oracle, and emits Metal source with
  `device atomic_float* C`, `tgid.z`, `split_k0`, and atomic accumulation.

Gates:
- `gtimeout 60s env -u LC_ALL uv run python -m py_compile pcc/kernel_ir/tilelang_import.py tests/kernel/test_tilelang_import_broader.py`
  - passed
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py::test_eager_splitk_atomic_alias_source_survives_import_freeze_source_and_cpu_oracle tests/kernel/test_tilelang_import_broader.py::test_splitk_atomic_outer_split_alias_survives_import_freeze_source_and_cpu_oracle tests/kernel/test_tilelang_import_broader.py::test_splitk_atomic_outer_floor_div_alias_tail_fails_closed -rs`
  - `3 passed in 0.28s`
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py -rs`
  - `49 passed in 0.10s`
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tirx_adapter.py tests/kernel/test_tvm_oracle.py -rs`
  - `27 passed in 0.10s`
- `gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_tilelang_import_broader.py -rs`
  - `63 passed in 0.31s`

Not claimed:
- No real TileLang runtime execution.
- No `.air/.metallib` artifact or Metal command-buffer launch.
- No pcc1/no-libpython, pcc1->pcc2->pcc3 bootstrap, or five-GC proof.
- No support for arbitrary eager TileLang functions, `T.const` execution,
  non-static tensor declarations, TMA/wgmma, arbitrary executable scheduled
  loop bodies, arbitrary/non-f32 atomics, or performance.
