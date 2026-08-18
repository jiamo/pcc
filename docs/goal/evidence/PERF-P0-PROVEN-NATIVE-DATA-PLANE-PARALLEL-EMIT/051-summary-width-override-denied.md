# Existing summary-width override under 8 GiB `[DENIED]`

The source-frozen v58 Stage2 coordinator used compiled export/summary width
two and reported 117.593s total / 74.577s export+summary.  A same-pcc1 direct
checkpoint set `PCC_PY_FRONTEND_SUMMARY_JOBS=7` under the unchanged 8 GiB
external breaker.  The existing API correctly clamps the request to the
coordinator's `max_parallel=3`, so actual summary width changed only 2->3.

```text
metric                         width-2 control     requested-7/actual-3
checkpoint profile total      117.593s            121.302s
export+summary                  74.577s             73.422s
process-tree peak                7.731GB             7.689GB
```

The owner improves only 1.5%, and total time does not.  No source change was
made.  The independently proven width-seven policy remains scoped to a shared
16 GiB envelope; this result provides no authority to weaken the 8 GiB safety
reserve or bypass `max_parallel`.

