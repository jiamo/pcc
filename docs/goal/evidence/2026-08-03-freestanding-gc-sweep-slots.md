# Freestanding GC sweep-slot evidence (2026-08-03)

## Claim boundary

Strict freestanding pcc-Python now production-owns the candidate-aware owned
slot clearing and shared container-metadata reset used by backend 0 and the
tracing GC sweep path.  Backend 0 keeps its reachability-table policy; GC1..4
keep their sweep-candidate-bit policy.  Tracing weakrefs are invalidated before
referents are cleared.

This closes only PASS-1 clearing for `LIBC-P2-FREESTANDING-GC`.  PASS-0
finalizer dispatch, resurrection recheck, PASS-2 deallocation, public tracing
collection orchestration and backend-specific scheduling remain outside this
slice, so the task remains `DONE_WEAK`.

## Ownership and closure

`freestanding_gc_sweep_slots.py` exports exactly seven raw phases:

```text
pcc_gc_tracing_is_sweep_candidate
pcc_gc_backend0_clear_slot
pcc_gc_tracing_clear_slot
pcc_gc_clear_container_metadata
pcc_gc_backend0_clear_referents
pcc_gc_tracing_clear_referents
pcc_gc_tracing_clear_unreachable
```

LLVM and self emission both leave exactly these five raw imports:

```text
pcc_gc_backend0_is_unreachable
pcc_gc_object_is_known_no_lock
pcc_gc_visit_object_slots
py_decref
py_weakref_invalidate
```

The current production archive
`2e4a76d4e3db4787e71c8010-pcc-py/libpy_runtime_pcc_py.a` reports every export
exactly once, from `freestanding_gc_sweep_slots.o`.  The former definitions
are absent from `freestanding_gc_backend0_slots.o` and `py_gc_backend.o`.

## Focused gates

```text
tests/python/test_freestanding_gc_tracing_sweep_slots.py
  5 passed in 1.71s

backend-0 slot actions + collector
  10 passed in 4.62s

tests/python/test_gc_update_referents.py
  31 passed in 0.62s

targeted generational subtract/clear contracts
  2 passed

five-backend object lifetime + weakref/finalizer + resurrection
  20 passed in 61.48s
```

The source test also pins two semantic distinctions that a mechanical merge
could lose: backend-0 clear skips children in its unreachable table, while the
tracing clear skips children carrying the `1024` sweep-candidate flag; both
clear the owner slot before any decref.

## Fresh pcc1 proof

```text
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/freestanding-gc-sweep-slots-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/freestanding-gc-sweep-slots-stage1 \
  --backend self --stage 1

PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=35406 \
  output=build/freestanding-gc-sweep-slots-stage1/pcc1
```

The profile records 34.180 seconds.  `file` reports arm64 Mach-O and `otool -L`
reports only `/usr/lib/libSystem.B.dylib`, not libpython.  That pcc1 compiled
the real strict sweep-slot source with `--python-libpython=off
--python-library`; its IR defines all seven exports and contains no `call` or
`invoke` of a `py_cpy_*` symbol.

Git HEAD while collecting evidence:
`6219a61f8f1ea84b13d9448ad66898d5ebf24a7c` (working tree intentionally
uncommitted).

## Remaining task boundary

Move the common PASS-0 finalizer/resurrection and PASS-2 deallocation/public
collection state machine, then the incremental/concurrent, generational and
relocating policies.  Only after all production GC symbols have pcc-Python
owners should the full five-GC semantic/fixed-point matrix be run once and the
long-running RSS/fragmentation/pause/throughput deltas recorded.
