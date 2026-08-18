# GC4 A3b remaining locked fatal-log routing

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite log-site classification/routing boundary confirmed; parent
remains `IN_PROGRESS`.

## Claim boundary

Seven locked-context C sites in `py_gc_backend.c` now defer their fatal
report to outer unlock instead of entering the runtime log/abort sink while
the graph lock is held: target-death and normal payload-retirement failure
paths, both source-side-table commit failures, granule retirement, the
zombie-page retention check, and the forwarding-count underflow check. The
five relocation-read-barrier validations route through a mixed helper:
immediate when unlocked, deferred with a safe bail (no heal, no forward
count) when the caller owns the lock; the lost-span violation heals to the
validated forwarding target because returning the source risks a crash
before the report fires. `pcc_cpy_handle_move_owned_ref` defers via one new
cross-TU seam `pcc_gc_tripwire_defer_or_fail` (reachable under lock through
GC3 oldification). The strict port gained the mirror mechanism: substrate
TLS slots plus exported defer/finish owners, unlock finishing after physical
release on both exits, and all five strict fatal sites routed.

Classified as needing no routing: young-promotion drain report,
continuation-register pre-lock check, scheduler-link helper (callers release
first), `pcc_gc_visit_runtime_roots` immediate calls (outside tenures),
vthread-channel dealloc, cpy-handle new/dealloc, `py_obj_gc.c` reachability
(STW owner, not graph-lock holder), `pcc_decref_finish` (no known in-lock
caller after A3b finish tails).

## RED and gates

Every added assertion was shown failing against `git show HEAD:` text before
implementation (C defer-missing x7, read-barrier RT/MIXED shape, cpy direct
RT under lock). Final packet:

```text
11 passed in 15.43s  tests/python/test_runtime_tripwires.py
  - threaded armed probe prints LOCK-HOLDER-CONTINUED before TRIPWIRE
    (deferral proven, not just post-hoc abort)
  - unlocked armed probe keeps immediate abort
3 passed in 139.34s  tests/python/test_freestanding_gc_production_link_map.py
6 passed             tests/python/test_freestanding_gc_generational_oldification.py
24 passed in 15.01s  task-card payload/retirement pair (log below)
```

Strict-closure corrections found by gates and fixed: freestanding modules
require every function exported (`pcc_py_gc_finish_deferred_tripwire`
export added); `pcc_py_gc_defer_tripwire` registered in the exact
cross-object ABI registry; both raw-import inventories swapped from
`pcc_runtime_tripwire_fail`; substrate symbol allowlist extended by review.
Owner-referent promotion worklist design recorded as Proposal No.2 in the
investigation; implementation remains open.

## Frozen identities

```text
4483a7a00bab91597ddac3aa308b5d0cad309b81784290d779f734dfc8a99cbe  pcc/py_runtime/src/py_gc_backend.c
9b87b659f42e11521c2e01de1de400a75b5954346297a3d4b93012a457913a6e  pcc/py_runtime/src/py_cpy_handle.c
d403bfa3ccad2a325bc1a8136bf9aae3d57355c98bf697e24673f6d08dbafdfc  pcc/py_runtime/src/py_internal.h
cbcbe6b05bfdb258fde71bd848ecc09e5865ab1149ab086430245059d3abb099  pcc/py_runtime/py/freestanding_runtime_high_substrate.py
6ac899f65920c9ccac5459835d1a7987a05ad02ae51fb657b4ac1ff815e395da  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
8f9b494389ae2c33b3cf561c1fa7d5cb346870f7315fa8cd456a3b023d8ec179  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
87200964b3350b7fd5147daa21c0d000cbcb9f2b9826c0896fa4a101aa578b3c  pcc/py_runtime/py/py_gc_backend.py
3d62c60dda3f1166798c971f6fe7e394c0764c44485f1eb37dfdbddd88a2e0ae  pcc/py_frontend/codegen/runtime_abi.py
1e6465563bc0836fbb82f3a8344631b3378bd077e5c9fee14234f76f066b36e7  tests/python/test_runtime_tripwires.py
26adbaa9a87768edf0f6852cb0a00ea9cf4c253fc737464e96918aeb5e0c8135  tests/python/test_freestanding_gc_production_link_map.py
7c6a9798318a8787d8e4186594ba927e5eedc4342516f4ef855586a5e1cd1b33  tests/python/test_freestanding_gc_relocation_payload.py
31cb2b0b92a09b2e93e9ad0298ba94cfd2a806e13c713d8f54cfa0bdec30c269  tests/python/test_freestanding_gc_forwarding_retirement.py
b1b22b3923c69b1b422778ff97bee8de603593719d5213d566cc8eb8c610d061  build/gc4-relocation-mutator-quiescence.log
```

## Nonclaims

No tripwire-clean claim for unarmed builds; no A3c no-park connection; no
raw-access transaction, collector STW phase, or physical relocation proof;
no pause/throughput acceptance; strict parity limited to the five routed
sites plus source contracts (strict has no read-barrier checks to mirror).
