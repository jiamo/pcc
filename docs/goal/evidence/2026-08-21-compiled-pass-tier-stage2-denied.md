# Turning the compiled default pass tier on for stage2 is DENIED

Date: 2026-08-21

Claim level: focused GC0, Darwin arm64, single-module pcc1 compile A/B plus
IR-shape audit. Not a complete stage2 timing; no source changed.

## Question

Stage2 runs with `PCC_PYTHON_IR_PASSES=off` everywhere. The compiled
mem2reg+sroa default tier (PERF-P2-PASS-WIRE wiring, PERF-P3-PASS-CLOSURE
in-pcc1 implementation) is present and green. Does enabling it pay at stage2
scale?

## Measurements (current-tree pcc1, stage1 rc=0, health checks green)

Three interleaved pairs, fresh salted 400-function input per run so the object
cache cannot hit, program output verified equal:

```
pair0  off 61.5 s | on 75.1 s
pair1  off 61.5 s | on 75.1 s
pair2  off 61.7 s | on 76.4 s
min    off 61.5 s | on 75.1 s   -> on/off = 1.221  (22.1% slower)
```

Controls that explain the number:

```
--emit-llvm off vs default    IR byte-identical (15,510,343 bytes both,
                              alloca 6,803 / load 4,406 / store 11,207 both)
pass telemetry                none written by the compiled tier
```

So the tier ran (the -o wall moved), found nothing, and its cost is pure
overhead: ~13.6 s per module-set to parse/walk 15.5 MB of IR under pcc1's
execution tax, for zero transformations.

## Why mem2reg finds nothing, structurally

Every object local's `alloca` is a GC root slot (`%t.addr.*`,
`%exact.int.*.tmp.root.*`); the function registers those addresses into the
root registry (4,403 `pcc_gc_frame_enter` calls, 2,402 `gc.frame.slots`
references in the same module). An alloca whose address escapes into the frame
registry is not legally promotable, and that is *by construction* every rooted
local. The bounded same-block tier promoted 0 of 6,803 allocas.

## Verdict

`[DENIED]` for stage2 use. The self default stays `off` (it already is); the
explicit versioned tier remains available by env for LLVM-path callers.

Reopen condition, sharpened: the tier can only become profitable after the
structural work that removes root registration for provably non-escaping
locals (the value-lane/dead-barrier track named by investigation Update
No.53). Until locals exist whose addresses do not escape, mem2reg has an empty
legal domain on pcc-generated IR, and any pass-tier cost is a pure loss.

## Also repaired in the same session (pre-existing HEAD test staleness)

`tests/python/test_py_frontend_ir_pass_pipeline.py`: four stale tests updated
to pin committed semantics (8-worker default with the 52.8% cap-denial cited;
bounded large-IR concurrency instead of any-large->1; the fake export worker
now writes per-module AST wires like production; the shard+skip contract test
routed through the memory transport it guards, since the compiled tier
in-process path never shards by design). File now 85 passed.

## Follow-up sizing: function-level allocation-point analysis is also an empty domain

The cheapest slice of the "no allocation point -> no GC point -> no root"
direction is function granularity: a function whose only calls are
non-allocating GC bookkeeping could skip frame enter/leave and every root
store. Measured on the same representative module (804 functions, 401 with
`pcc_gc_frame_enter`, 12,807 enter/leave and 11,602 store_root sites):

```
functions with frame_enter and zero non-bookkeeping calls:  0  (0.0%)
```

Every rooted function calls at least one potentially-allocating runtime helper,
so the function-level predicate never fires. The direction survives only at
**window granularity** — proving no allocation point between a root store and
the value's last use, per store, across control flow. That is a real dataflow
analysis inside codegen, not a probe: it needs its own pre-registered row, a
conservative allocating-call whitelist as the proof obligation, and the full
five-GC equality gates, because a wrongly elided root is a use-after-free under
exactly the backends that move or collect earliest.

Two empty-domain results in one session (mem2reg: rooted alloca addresses all
escape; function-level root elision: no call-free rooted function) point at the
same fact from both sides: **pcc IR pays rooting on paths that allocate
everywhere**, so the only levers left are (a) window-level elision with real
proofs, or (b) making common helpers provably non-allocating (value-lane) so
the windows appear. Neither is a session-sized probe.

## Sizing gate for S-P1-ALLOCATION-POINT-ROOT-ELISION: the domain is large

Two scan corrections mattered and are recorded so the next reader does not
re-derive them:

1. Keyed on the rooted VALUE the scan reads 100% elidable — vacuously, because
   root-based codegen reloads from the SLOT after the store (that is what a
   root is for), so the value often has zero textual uses after `store_root`.
   The window must be keyed on the slot's reloads.
2. Keyed on the SLOT, block-local scanning is structurally blind (every call is
   followed by a `py_err_occurred` branch, so all 5,601 windows cross blocks).

Generous cross-block upper bound (store-block remainder to its last slot read,
plus each reading block's prefix to its last read; intermediate blocks ignored;
allocating-call whitelist = GC bookkeeping + err check only):

```
store_root sites                         5,601
slots never reloaded (dead roots)            0   (existing root hygiene is good)
windows with no allocating call (UB)     4,001   = 71.4%
```

71.4% is an upper bound — intermediate blocks on the reload paths may contain
allocating calls that this scan ignores — but it is far above the 5% rejection
line, so the row proceeds to real design: a sound cross-block window analysis
on the precise-stackmap CFG, which already reconciles managed-root state at
every join. The lower bound and the per-backend safety argument belong to that
design, not to this scan.
