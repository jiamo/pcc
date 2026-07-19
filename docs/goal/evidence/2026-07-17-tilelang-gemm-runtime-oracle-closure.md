# GPU-P0-TILELANG-GEMM-RUNTIME-ORACLE closure evidence

Finite claim: pcc imports its strict TileLang/TIRx GEMM subset, executes the
lowered Metal workload, and compares device output with the CPU oracle. This
does not claim arbitrary TileLang syntax/passes, arbitrary layouts or atomics,
framework support, or performance.

Current importer and Metal GEMM runtime gate:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import.py tests/kernel/test_metal_tilelang_gemm_runtime.py
42 passed in 17.30s
```

The same source state also passed the CPU-reference/simdgroup tests (84 tests
in 25.90s) and Metal source-runtime/package verification (35 tests in 1.00s).
Historical strict evidence extends this bounded owner path through pcc1
no-libpython prebuilt-metallib execution and GC0..4 fence-deferred lifetime.

No full bootstrap or full GCC suite was run.
