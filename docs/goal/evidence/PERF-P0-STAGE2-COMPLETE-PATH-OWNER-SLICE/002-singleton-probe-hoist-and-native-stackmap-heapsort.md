# Slices 2 and 3: singleton-first provenance probe (DENIED) and native stack-map heapsort (accepted, v84)

Date: 2026-09-06. Status: slice 2 measured and denied (forward-removed);
slice 3 accepted through byte-identical worker replays and a source-frozen
Stage1 -> Stage2 pair after one real defect was found and fixed by reading
the output diff. Stage3 remains deferred by the human.

## Slice 2 (DENIED): immortal singletons before the granule walk

Hypothesis from evidence 001: every incref/decref runs
`pcc_gc_pointer_is_managed`, whose granule radix walk is the top pcc1 leaf,
and the four immortal singletons were only recognised inside the locked
slow chain (`_pointer_is_managed_no_lock`) after a failed walk. Both mirrors
were changed to compare `py_None`/`py_NotImplemented`/`py_True`/`py_False`
before the walk; the disjunction is order-free so every answer was
unchanged. Gates were green (new source-order ratchet, GC0..4 provenance
probes 5/5, GC0..4 production contract 169 passed on backends 2, 3, 4;
backends 0 and 1 hit `stale codegen provenance` only because slice-3
compiler edits landed while that background gate compiled).

Measurement (v83 = v82 + slice 2 + slice 3, same recipe, receipt-bound
replays, `build/singleton-heapsort-replay/`): the `cli_bootstrap` ASM
worker, which never runs the stack-map sort, went from 395.11B to 407.70B
instructions (+3.2%) and 27.11 s to 27.44 s user with byte-identical ASM.
Four extra compares on every probe cost more than the singleton calls they
saved: singleton refcount traffic is too rare on this workload. Per the
row's failure disposition the hoist is DENIED and forward-removed from both
mirrors (a comment records the measurement); its ratchet test was removed.
Do not retry the singleton hoist; the probe's call count, not its per-call
cost, is the owner.

## Slice 3 (accepted): raw-memory heapsort for final stack-map records

Owner (evidence 001): `_sort_final_stack_map_records` heapsorts four-word
records in a `CompilerIntArena` through `get_unchecked`/`set_unchecked`
method calls, 9.3% of the py_ast PCO worker.

Change: `CompilerIntArena.native_address()` exposes the storage address as
an integer (0 under the CPython list oracle). When it is nonzero the sort
runs the identical heapsort (`_sift_down_final_stack_map_records_native`,
`_swap_final_stack_map_records_native`) with `load_i64`/`store_i64` at
`int_to_ptr(address)`; the arena-method form remains the CPython path.

Defect found and fixed before acceptance: the first kernel passed the raw
pointer `base` through unannotated function parameters. The v83 py_ast PCO
replay completed with rc=0 but was not byte-identical: 23,509 of 46,625
stack-map rows differed in exactly one byte (row offset 28, bit 6), i.e.
every changed `safepoint_id` was `control | 2**38`. The frontend treats an
unannotated parameter as an object and brackets it with
`pcc_gc_pin`/`pcc_gc_unpin`; `pcc_gc_pin` writes `PY_FLAG_GC_PINNED` into
byte 12 of whatever it is handed without a provenance check, and byte 12 of
a 32-byte record is bit 38 of record zero's id word, which the heapsort then
carried around with every swap. The kernel now takes the address as an
exact `int` and converts it inside each intrinsic argument, so no raw
pointer crosses a call boundary; the emitted kernel IR pins only tagged int
locals (a no-op inside `pcc_gc_pin`). This is the same hazard class as the
module-level raw-pointer pin corruption recorded earlier in the repository.

Gates: `tests/python/test_precise_stackmap_record_sort.py` (new: list-path
order against `sorted`, empty/single/sorted inputs, and the native kernel
driven through a bytearray stand-in compared with the arena-method sort on
identical input), `test_compiler_record_spans.py`,
`test_precise_stackmap_abi.py`, `test_pcc_record_inventory_tool.py`: 68
passed. Standalone strict emission has 112 stubs versus 111 at HEAD; the
extra stub is `_sort_final_stack_map_records` because single-module
emission cannot resolve the cross-module arena class (its callee
`_swap_final_stack_map_records`, stubbed standalone at HEAD, runs natively
in pcc1); the contextual proof is the replay, which executes the sort for
every module and would fail closed on a stub.

## Receipt-bound replays, v82 control vs v84 candidate (kernel only)

Frozen `pcc-heapsort-v84` differs from v82 in
`pcc/backend/self_backend_precise_stackmaps.py`,
`pcc/backend/self_backend_value_arena.py`, comment-only changes in
`pcc/py_runtime/py/py_gc_backend.py` and `src/py_gc_backend.c`, and the
regenerated provenance JSON. Runtime archive `6b58c3b32183311dc9998a33-pcc-py`.

```text
py_ast PCO (exact cb81f6c2...)   wall     user     instructions
control-1                        9.72 s   9.33 s   136,565,267,001
candidate-1                      8.74 s   8.51 s   124,352,665,054
control-2                        9.64 s   9.35 s   136,447,867,627
candidate-2                      8.71 s   8.50 s   124,399,774,381
cli_bootstrap ASM (exact 04b55bb2...)
control-1                       28.11 s  27.10 s   395,052,107,422
candidate-1                     27.85 s  26.80 s   394,667,046,362
```

py_ast worker CPU 1.10x, instructions -8.9%, RSS flat; ASM lane unchanged.
Artifacts: `build/heapsort-v84-replay/`.

## Source-frozen Stage1 -> Stage2 (v84)

```text
                       v82        v84
Stage1 wall            170.76 s   187.33 s   (v83 reruns of one snapshot: 253.06 s under host load 13.4, then 183.34 s)
Stage1 CPU             689.41 s   747.63 s
Stage2 wall            474.406 s  464.379 s
Stage2 compile CPU     1437.6 s   1368.4 s   (-4.8%)
Stage2 sys CPU         149.6 s    151.5 s
Stage2 tree peak       8.029 GB   8.024 GB
  coordinator          124.1 s    126.9 s
  frontend workers      94.5 s     95.4 s
  ASM emit             116.5 s    112.6 s
  PCO emit              65.2 s     56.9 s   (-12.7%)
  pcc-owned link        63.5 s     62.2 s
pcc2                   38863e8f   1bed60ee   (libSystem only)
```

Receipts: `build/heapsort-stage1-v84/`, `build/heapsort-v84-build-guard/`,
`build/heapsort-stage2-v84/`. Stage1 wall varies 171-187 s across the
v82/v83/v84 builds of the same recipe, so single Stage1 walls are
observations; the Stage2 compile CPU and phase receipts are the verdict.

## Fallback ratchets on the v84 source

`tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py`
with `-vv -x -n0 --durations=10`: 45 passed in 619.08s (0:10:19)
(`build/heapsort-v84-fallback-ratchets.log`). The changed compiler modules
add no fallback edge to the closure.

## Supported claim

On GC0, self backend, no libpython: the native heapsort produces
byte-identical stack maps and native objects, cuts the py_ast PCO worker
by 8.9% instructions and the Stage2 PCO phase by 12.7%, with unchanged peak
memory. The singleton hoist is a measured negative and stays out.

## Not proven / open

Stage2 is about 2.5-2.7x Stage1 depending on the Stage1 wall used. The
refcount provenance probe (about 11% of every pcc1 phase by the diagnostic
ceiling in evidence 001) remains the largest measured owner and needs a
human design decision. The compiled `int` local protocol (int locals live in
GC slots, are reloaded through `pcc_gc_load_borrowed_ptr` and pinned as
objects, as the kernel IR shows) is the deeper cross-cutting owner behind
the GC family share. Stage3 is deferred by the human.

## Bounding the int-local owner (same day)

The heapsort kernel IR boxes `root`/`child`/offset locals (`py_int_from_i64`,
GC frame slots, `py_int_mul`/`py_int_add`, pin/unpin) while `limit`,
`address` and loaded words stay exact i64: locals defined by unproven
arithmetic take the object lane. The `py_int_*` runtime functions
themselves are small in the worker profiles: 4.3% self on the py_ast PCO
worker, 2.0% on the cli_bootstrap ASM worker, 2.2% on the frontend worker
(`_py_int_from_i64` about 1% each). The arithmetic calls are therefore not
a >=10% owner; the cost of boxed int locals is the GC slot/root/pin
protocol they share with every object local, and that int-specific share
is unmeasured. A diagnostic that quantifies it (for example counting
frame-slot registrations and pins by static local type on one worker) must
precede any value-projection slice; the recorded INT-P0 design (tagged
`*` fast path, unboxed-lane admission tightening) bounds at about 1-2% here.

Static proxy on two real module emissions (host `--python-library` IR of
`self_backend_precise_stackmaps.py` and `py_stdlib/struct.py`): `pcc_gc_pin`
sites whose operand is a boxed int (`py_int_from_i64` result) are 105 of 759
(14%) and 71 of 314 (23%); boxing sites number 576 and 230 per module against
295 and 108 frame registrations. Boxed-int traffic is therefore a mid-size
share of the object protocol, not its majority; the remaining pins sit on
allocation results, call results and slot loads of real objects. A dynamic
count on one worker is still required before selecting a value-projection
slice.
