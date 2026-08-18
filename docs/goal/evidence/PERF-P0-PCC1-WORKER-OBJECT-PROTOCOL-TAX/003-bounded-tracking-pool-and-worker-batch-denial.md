# 003 — bounded tracking-node pool retained; worker batching denied again

Date: 2026-09-04

## Tracking-node pool

The already-landed GC tracking-node freelist removed a malloc/free pair from
the instance/container lifecycle, but retained an unbounded historical
high-water until process exit. The pool is now capped at 4096 40-byte nodes
(about 160 KiB), exposes mirror-equal count/drain diagnostics, and frees nodes
above the cap. The C and freestanding pcc-Python owners use the same bound.

Retained pre/post-pool binaries provide a deterministic sizing signal:

```text
row                   pre-pool -> pooled instruction speedup (two pairs)
alloc_small_object        1.0480x / 1.0500x
call_returns_obj          1.0341x / 1.0345x
tuple_pack_unpack         1.0002x / 1.0000x
```

The bounded candidate compared with the same-wave unbounded allocation binary
adds only 0.30-0.55% instructions. Its current rows remain output-equal:

```text
alloc_small_object  4555 instructions/iteration
tuple_pack_unpack   4763 instructions/iteration
call_returns_obj    6813 instructions/iteration
```

Receipts:
`build/per-op-cost-v12-bounded-node-pool/results.json` and its guarded
process-tree receipt. The archive was rebuilt after both changed strict
objects; strict closure passed for `freestanding_gc_state.py` and
`freestanding_gc_tracking.py`.

Focused result: the new bounded/reuse/drain canary, tracking LLVM/self closure,
C/port GC0..4 differential and pthread contention were the first nine passing
nodes. The next node exposed a pre-existing test-owner bug: two globals owned
by `freestanding_gc_relocation_selector.py` were registered correctly but the
state test compared the registry only with `freestanding_gc_state.py`. The
test now aggregates both disjoint defining owners; the state file rerun is
7/7 green. No runtime ownership moved and no duplicate symbol was introduced.

## Five-module reuse recheck `[DENIED]`

The exact prior five-module manifest was rerun with the Step-10 pcc1 after the
new ownership, assembler, slab-reclaim and frontend-release work. All five
assembly hashes are exact between arms:

```text
                         five fresh       one five-module process
wall                       23.96s                 21.55s       1.112x
CPU (sum)                 ~21.38s                ~20.85s       1.025x
peak tree RSS               1.244GB                2.469GB      1.98x
```

The memory line (<=1.5x and <=2 GB) still fails, and CPU/startup removal is
only about 2.5%. The new conditions do not overturn evidence 027/028. Do not
retry a different batch size without a new mechanism that removes accumulated
live compiler state.

## Task disposition

The per-operation wave is complete at its finite boundary: multiple accepted
changes transfer through pcc1, representative workers retire about 19-23%
fewer instructions than v8, bounded pooling retains its small win, and denied
shapes are recorded. The remaining Stage wall is reassigned without relaxing
it: measured export admission proceeds in its own task, while per-instruction
runtime/provenance/root-load work remains in the frozen native-data-plane /
parallel-emit owner. The final same-resource `Stage2 <= Stage1` claim remains
open in `PERF-P0-PCC1-BOOTSTRAP-BEATS-HOST`.

