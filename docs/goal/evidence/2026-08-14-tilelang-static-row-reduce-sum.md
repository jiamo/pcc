# TileLang static row reduce_sum focused evidence

Mode-labeled claim: host pcc imports the finite static rank-2 last-dimension
`T.reduce_sum` shape through pcc Kernel IR/TIRx and the pcc-owned Metal runtime
produces the expected real device result. This is Level 4 evidence only; it is
not current-pcc1 or five-GC evidence.

Fail-fast gates on current source:

- `gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 tests/kernel/test_tilelang_reduce_sum.py`
  — `18 passed in 0.23s`.
- `gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run pytest -q -x -n0 -rs tests/gpu_hardware/test_metal_tilelang_reduce_sum_runtime.py`
  — `1 passed in 1.73s`.

The focused run also corrected a test-only regex bug: the expected literal
output shape `(3, 1)` now escapes parentheses. Production already emitted the
correct fail-closed diagnostic.

Remaining boundary: build the same launcher with a current-source pcc1 under
strict self/no-libpython and prove Level 5 owner/fallback metadata, then execute
that workload under GC0 through GC4 with fence-deferred buffer lifetime for
Level 6.
