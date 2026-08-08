# Evidence: shared-refcount contention benchmark + gc.immortalize (2026-08-07)

Claim (mode-labeled): host-pcc-compiled binaries, C runtime tier
(PCC_RUNTIME_CC=cc), PCC_WITH_THREADS=1 threaded archive, macOS arm64.
No pcc1/pcc2 claim; no `import numpy` claim.

- New benchmarks: benchmarks/python/shared_refcount_contention{,_serial,_immortal}.py
  (4 threads x 5M iterations of a borrowed-param retain on ONE shared
  instance — the free-threaded-CPython/NumPy bottleneck shape).
- Matrix (speedup = serial/parallel, min of 2 runs):
  backend 0: 0.33x contended -> 4.20x with gc.immortalize (0.38s wall,
  2.3x faster than CPython 3.14 GIL best of 0.86s; CPython parallel 0.95x).
  Backends 1-4: ~0.8x -> ~1.0x; residual cap owned by
  gc-frame-index-entry-pool-perf.md (frame-index bookkeeping, not refcount).
- New API: gc.immortalize(obj) = pcc-native PyUnstable_Object_SetImmortal
  (C py_obj.c + port py_obj.py mirror + runtime_abi + native_gc lowering).
- Gates run: tests/python/test_gc_immortalize.py (2 passed, 1.11s, -n0);
  tests/python/test_gc_api.py (16 passed, -n0).
- Full narrative: docs/investigations/shared-refcount-contention-thread-scaling.md.
- Blocking bug found en route (separate file, fix verified but not landed):
  docs/investigations/py-frontend-call-ret-root-alloca-loop-stack-overflow.md
  (call.ret.root alloca in loop bodies burns 16B stack/iteration; any rooted
  hot loop dies at ~500K iterations).
