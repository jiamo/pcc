# Freestanding common GC mark-cycle evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns the common TRACE slot action,
referent graying, root seeding, transitive gray drain, mark-cycle begin and STW
mark-termination cut used by GC backends 1 through 4.  It consumes the existing
single object-slot contract and strict root operations; no object geometry,
root set, sweep, finalizer, deallocation or backend scheduling policy was
duplicated or changed.

This closes one common mark-cycle slice of `LIBC-P2-FREESTANDING-GC`; it does
not complete the task.  Sweep/finalizer/deallocation, incremental/concurrent
scheduling, generational promotion/oldification and relocating policy/remap
remain in `py_gc_backend.py`.

## Ownership and closure

`freestanding_gc_common_mark_cycle.py` exports exactly seven raw phases:

```text
pcc_gc_trace_mark_gray_if_known
pcc_gc_trace_slot
pcc_gc_trace_referents
pcc_gc_seed_roots
pcc_gc_drain_all_gray_unlocked
pcc_gc_begin_mark_cycle
pcc_gc_finish_tracing_cycle
```

LLVM and self emission have the same exact finite undefined-symbol closure.
The current production archive
`9368d824de10bf977f88beff-pcc-py/libpy_runtime_pcc_py.a` reports all seven
definitions uniquely from `freestanding_gc_common_mark_cycle.o`; none is
defined by `py_gc_backend.o`.

The first implementation incorrectly reused root re-graying for normal child
tracing.  The backend-1 resurrection test timed out after 60 seconds and a
macOS sample placed the hot loop in
`pcc_gc_drain_all_gray_unlocked -> pcc_gc_trace_slot ->
pcc_gc_mark_root_gray_if_known`.  Root graying may intentionally re-gray black
objects at the termination cut, while referent tracing must not.  The final
strict `pcc_gc_trace_mark_gray_if_known` preserves the original black-object
guard; a structural regression test prohibits using root-gray in TRACE slots.

## Focused gates

```text
tests/python/test_freestanding_gc_common_mark_cycle.py
  6 passed in 58.37s

common mark/root strict modules
  15 passed in 4.55s

tests/python/test_gc_update_referents.py
  31 passed in 0.93s

tests/python/test_gc_abstraction_surface.py
  15 passed in 3.87s

targeted generational/backend4 source ownership gates
  6 passed

pcc-Python incremental explicit-collect gate
  1 passed in 57.08s

five-backend object lifetime + finalizer resurrection
  10 passed in 1.41s

five-backend root graph + slot graph + weakref/finalizer
  35 passed in 4.71s
```

The direct regression changed from
`test_resurrected_object_survives_gc[1]` timing out after 60 seconds to passing
in 55.07 seconds after restoring the black-object guard.  The later cached
five-backend run is the mode-labeled semantic proof, not a performance claim.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-common-mark-cycle-final-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-common-mark-cycle-final-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=32458 \
  output=build/freestanding-gc-common-mark-cycle-final-stage1/pcc1
```

The profile records 31.276 seconds.  `file` reports arm64 Mach-O and `otool -L`
reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That pcc1 compiled the
real strict mark-cycle source with `--python-libpython=off --python-library`;
the IR defines all seven exports and contains no `call` or `invoke` of a
`py_cpy_*` symbol.

## Scoped hashes

```text
baaf8640aa43b249ee3ab81bfc7b1e4be55d23178d75c4307844ce76b85f27e8  pcc/py_runtime/py/freestanding_gc_common_mark_cycle.py
ea3a0d7ba3162984f55c6f6d51d2483b439101b79d742e533f077a538333ab5e  pcc/py_runtime/py/py_gc_backend.py
68fcda51a99c0e31324669293d7b3dee29c79b6411cfd4e3685c25562e282393  pcc/py_frontend/codegen/runtime_abi.py
784adef35cb26048abeb776cb760861c89dcca7ceeb07f8deed7303ac3d273fb  pcc/py_runtime/Makefile
d512632a890543f172f24b3209d764549d5c7ed66384e95c043932e2b9101171  tests/python/test_freestanding_gc_common_mark_cycle.py
```

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Migrate the common tracing sweep/finalizer/deallocation state machine, then the
backend-specific incremental/concurrent, generational and relocating policies.
After all production GC symbols have pcc-Python owners, prove no GC C object is
linked, run the full five-GC semantic/fixed-point matrix once, and record
long-running RSS/fragmentation/pause/throughput deltas.
