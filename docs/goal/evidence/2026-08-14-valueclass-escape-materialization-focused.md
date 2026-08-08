# PERF-P2-ESCAPE-SCALAR-MATERIALIZATION focused evidence — 2026-08-14

Mode: current host frontend plus self/no-libpython focused executable; no
cross-GC benchmark or fixed-point claim.

Command/result:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_py_value_class_unboxed.py tests/python/data_model/test_value_class_source_shape.py
# 66 passed in 25.66s
```

The suite covers zero-allocation value projection, exact valuebox
materialization boundaries, identity/weakref rejection, nested and
pointer-bearing payloads, function/container/global/mutation/control-flow and
exception/format boundaries, plus the executable hot-loop allocation oracle.

Open: GC0..4 ownership/root matrix, LLVM/self cross-owner parity, measured
RSS/pause/throughput, and sequential fixed point.
