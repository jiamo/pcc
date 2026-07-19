# GPU-P0-SIMDGROUP-TENSORCORE-GEMM closure evidence

Finite claim: pcc has an explicit opt-in Metal simdgroup GEMM lowering while
the scalar tiled GEMM remains the semantic fallback. This is not a claim of
arbitrary TileLang tiling, arbitrary atomics/split-K expressions, universal
tensorcore coverage, or performance.

Current focused lowering and CPU-oracle gate:

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_kernel_cpu_reference.py tests/kernel/test_metal_simdgroup_gemm.py
84 passed in 25.90s
```

The current Metal finalize/package/runtime suite also passed (49 tests in
12.18s). Historical strict hardware evidence already covers prebuilt metallib
execution from pcc1 no-libpython binaries and GC0..4 lifetime for the bounded
simdgroup slices through the 32-simdgroup transpose/split-K/edge-tail case.
Those deeper examples strengthen the finite lowering claim; they do not turn
unbounded future variants or performance into its exit condition.

No full bootstrap or full GCC suite was run.
