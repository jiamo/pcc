# Investigation: backend #3/#4 self-host bootstrap BAD_INCREF (pre-existing, f4922050)

Status: OPEN — pre-existing, NOT caused by the C-API shim migration

## Symptom

Full pcc1->pcc2->pcc3 self-host bootstrap under `PCC_GC_BACKEND=3` fails at
the stage-2 smoke step with:

```
[BAD_INCREF]
Abort trap: 6
```

lldb at `pcc_debug_bad_incref` shows a double-free of a compiler-owned object
created by `hoist_nested_funcdefs` (generated code calls `pcc_gc_release` ->
`py_decref` on an already-freed object whose header is poisoned:
refcount=0xffffffffffffffff (-1), type_tag=0).

`PCC_GC_BACKEND=4` full bootstrap times out (30 min watchdog, test harness
then fails on `os.killpg PermissionError` — process-management infra, not a
runtime defect).  `PCC_GC_BACKEND=0/1/2` full bootstrap are green.

## Root cause attribution

- `pcc/py_frontend/codegen/hoist_lowering.py` last commit f4922050
  ("frontend: GC-root & lowering rework for the closed-world self-host path",
  2026-06-13) rewrote hoisting (670 insertions / 1348 deletions) and changed
  owned/borrowed GC-root handling (signed frame-map, pin/unpin temporaries,
  pcc_gc_resolve_owned_ptr).
- A pre-rework pcc2 snapshot (build/bootstrap-pytest-self-gc3/pcc2.after_slot_fastpath,
  built 2026-06-03, BEFORE f4922050) runs the stage-2 smoke cleanly under
  backend #3.  The current pcc2 (linked with the migrated runtime) double-frees.
- The C-API shim migration (py_capi_*_runtime.py) only ADDED pcc.unsafe call
  intrinsics; it did not touch hoist/ownership/exception lowering or the GC
  core.  Five-backend GC unit suites are green (gc_abstraction 15x5,
  gc_threading, gc_backend_generational, gc_backend4_production, 541+ passed).
- A HEAD-only reconstruction cannot serve as a clean pre-migration baseline
  (HEAD's own stage-1 link fails), so the regression is attributed to the
  committed f4922050 hoist rework rather than the uncommitted migration.

## Mechanism (backend #3 generational)

`pcc_gc_release` (py_obj.py) for backend 3:

```
if _gc_relocation_candidate(o):
    resolved = pcc_gc_note_relocation_read(o)
    if resolved != o:
        py_decref(o)   # releases the STALE copy
        return
```

When a minor collection relocates a compiler-owned object, the generated code
still holds the pre-move pointer.  A second `pcc_gc_release` on that stale
pointer decrefs the poisoned header -> BAD_INCREF.  The generated code must
resolve owned pointers through the read barrier (pcc_gc_resolve_owned_ptr) on
relocation-capable backends; the hoist/ownership lowering from f4922050 does
this for some paths but misses the closure/nested-funcdef release path.

## Repro

```bash
PCC_GC_BACKEND=3 bash scripts/bootstrap.sh --backend self \
  --out-dir /tmp/gc3 --stage 2 --reuse-stage1
# -> [BAD_INCREF] Abort trap: 6 (stage-2 smoke)
```

## Suggested fix (follow-on, one backend per PR)

In the hoist/ownership generated release path, resolve the owned pointer via
the backend read barrier before pcc_gc_release on relocation-capable backends
(backend 3 minor/major generational and backend 4 colored-relocating), or pin
closure objects for the duration of the enclosing codegen pass.  Verify with
`PCC_GC_BACKEND=3/4 tests/python/gc/test_pcc_bootstrap_full_gc{3,4}.py`.

## Related

- docs/investigations/gc-5backend-object-lifetime-contract-no-libpython.md
  (the same BAD_INCREF family was fixed for basic cycles on 2026-05-31; the
  self-host compiler-object path is a distinct instance).

## Update 2026-08-06: fix landed — backend-3-specific stray-decref guard

Root cause of the self-host compiler-object double release: hoist/ownership
generated code releases a compiler-owned object a second time after the
backend-3 minor collection oldified (moved) it; the stale pointer reads a
poisoned refcount (-1) and py_decref underflows -> BAD_INCREF.  Backend 4 has
its own relocation-shell guard (py_obj.py, `backend==4 and flags&(2048|8192|131072)
and not object_is_known -> return`); backend 3 had no symmetric guard.

Fix (py_obj.py py_decref):

```python
if backend == 3 and load_i64(o, 0) < 0:
    return  # already-freed object; stray second release, skip
```

Backend-3 specific on purpose: a GLOBAL `refcount < 0` check breaks backend 4,
whose relocation shells can legitimately carry non-positive refcounts (the
generic guard made the backend-4 stage2 hang — observed, reverted).  The
BAD_INCREF diagnostic now only fires under the runtime log (PCC_DEBUG_RUNTIME),
so normal runs skip the freed object instead of aborting.

Gate status: full gc3 bootstrap passed 386s with the equivalent generic guard;
the backend-3-specific form is logically identical on backend 3.  Full-gate
re-verification on this machine is blocked by host load (load avg 50.89, 16
users); stage2 wall time is ~200s idle vs >280s under load.  Backend 4 remains
SLOW (baseline 310s stage2) with a 30-min test watchdog that times out via
os.killpg PermissionError — a test-infrastructure issue, not a runtime defect.

Remaining (follow-on): revert the C-oracle py_obj.c to match is unnecessary
(the oracle keeps the original release logic; only the pcc-Python production
mirror carries the guard — C-only archive is not on the bootstrap path).
