# GC backend dispatch: measured, refactor rejected

Date: 2026-08-01

Task: `ARCH-P3-GC-DISPATCH-EVAL`

## The row's premise, corrected first

The row states that `py_gc_backend.c` consults the selected-backend global at
83 sites. Measured:

```text
pcc_gc_backend() call sites, whole runtime          21
  py_obj.c 10, py_obj_dealloc.c 3, py_obj_gc.c 2,
  pcc_threads.c 2, py_tuple.c 1, py_str_accessors.c 1, ... 
switch statements on the backend                     1
`backend == PCC_GC_KIND_*` equality tests          106
  py_obj.c 51, py_gc_backend.c 55
  (pcc_gc_store_ptr alone contains 10)
```

So the dispatch cost is not one switch consulted 83 times; it is one cheap
global read per hot function followed by an if-chain of equality tests. That
matters for the experiment: replacing "a switch" with a table is a different
change from replacing "106 predictable equality tests" with an indirect call.

## Experiment 1 — dispatch microbenchmark

A standalone benchmark in the exact shape of `pcc_gc_store_ptr` (repeated
`backend ==` tests, cheap work per arm, 40M pointer stores per backend,
`cc -O2`) compares the current if-chain against a per-backend function
pointer table:

```text
backend=0  if-chain 0.038s   table 0.036s   table/if = 0.96x   tie
backend=1  if-chain 0.094s   table 0.078s   table/if = 0.83x   table faster
backend=2  if-chain 0.094s   table 0.077s   table/if = 0.82x   table faster
backend=3  if-chain 0.078s   table 0.081s   table/if = 1.04x   slightly slower
backend=4  if-chain 0.039s   table 0.078s   table/if = 1.98x   table 2x slower
```

No consistent win. The production default and rollback reference (backend 0)
is a tie, and backend 4 — the relocating collector with the most barrier work
— is twice as slow through the table, exactly the "a predictable branch on a
global can beat an indirect call" case the row anticipated.

## Experiment 2 — real GC benchmark baseline

`benchmarks/run_gc_advantage_matrix.py --reps 2` on the current tree, median
elapsed microseconds per case per backend (raw rows archived beside this file
as `2026-08-01-gc-dispatch-baseline-rows.json`):

```text
case                                       gc0     gc1     gc2     gc3     gc4
gc0_refcount_steady_churn                 7225   12098   11315   12673   15402
gc1_incremental_explicit_churn           12296   10525   15677   12550   16173
gc2_cms_heap_under_high_collect_churn   113203   44330  162460   35610  109991
gc3_generational_high_frequency_collect 192303   64601  276922   55154  184315
gc4_colored_low_total_pause              23122   25269   26331   27980   30175
```

The spread between backends on the same case is 3-5x, and between cases on
the same backend is 20x. Dispatch is nowhere near that scale: the
microbenchmark's entire if-chain cost for 40M stores is ~0.04-0.09s, while
these cases differ by tens of milliseconds on far fewer stores. A dispatch
change that is at best 18% on two backends and 2x worse on another cannot be
detected against this variance, let alone justified by it.

## Verdict: refactor rejected at this time

Keep the if-chain. The change would touch 106 comparison sites across the C
runtime and its pcc-Python mirror, both of which must move together, and it
would put an indirect call in front of the single slot-trace contract that
five-GC production equality depends on — for no measured throughput win, a
tie on the default backend, and a 2x regression on backend 4.

What would overturn this verdict, recorded so the next attempt is not a
re-litigation: a profile showing dispatch (not barrier work) as a top cost in
a long-running workload; or a backend whose arms grow expensive enough that
the table's single indirect call replaces real work rather than three
comparisons; or a measured branch-misprediction rate on backend switching
under a mixed workload, which this repository does not currently produce.

## Not proven

- Bootstrap stage timing was not measured for this row: stage2 is dominated
  by frontend codegen and self-backend emission, so pointer-store dispatch is
  not observable there. That is an argument for irrelevance, not evidence of
  neutrality.
- The microbenchmark models `pcc_gc_store_ptr`'s shape, not every one of the
  106 sites.
