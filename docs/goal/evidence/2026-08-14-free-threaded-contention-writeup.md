# DOC-P2 free-threaded contention writeup evidence (2026-08-14)

## Claim

`docs/reports/shared-refcount-contention-host-pcc.md` records the shared-object
refcount benchmark at its measured claim level only: host-pcc, C runtime tier,
`PCC_WITH_THREADS=1`, macOS arm64, and individually labelled GC backends.

The report explicitly makes no pcc1, NumPy, or general free-threaded scaling
claim. It records the backend 1-4 frame-index cap, the opt-in process-lifetime
semantics of `gc.immortalize`, the call-return-root precondition, and the
invalid earlier NumPy L4/L5 evidence boundary.

## Focused gate

```text
gtimeout 300s env -u LC_ALL PCC_NO_AUTO_PCC1=1 \
  uv run pytest -q -x -n0 tests/python/test_gc_immortalize.py
..                                                                       [100%]
2 passed in 9.87s
```

This gate proves the immortal-object lifetime contract and the four-thread
correctness canary. Timing values remain recorded measurements rather than
test thresholds.

