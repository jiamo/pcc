# GC4 A3b retained-page release finish

Date: 2026-08-23

## Claim

For Backend 4's existing two-remap-epoch page quarantine, both the C
transition oracle and the strict freestanding pcc-Python production runtime
now detach an eligible oldest-generation retained ZPage while holding the GC
graph lock, but physically free its backing span and page descriptor only
after the outer object-drain, page-drain or idle-step caller releases that
lock.

The remap helper now returns a caller-owned list of eligible pages. Pages whose
object, pending-allocation or pending-forwarding counts remain nonzero stay
quarantined. Remap still partitions the old retained generation before moving
the parked generation into retention, so no page parked by the current remap
can be returned to the finish owner in that same epoch.

This is a retained-page physical-release claim only. Forwarding-node free,
target decref, identity/object-node retirement and source-payload retirement
still occur inside remap's graph-lock tenure and remain separate open owners.

## Frozen source identity

```text
c6626f74e9a3298a3f12db3604e928f582cc3ee9d16c837674d916a1458f11c2  pcc/py_runtime/src/py_gc_backend.c
5e1ed1fa96da4473a6acb984fed7d91a2418537a5a88261926b67aeef27ba1d1  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
ddff7e466f2ca2a5b33fee01bb4fa8434bd48f9261035d923d9c184838cff151  pcc/py_runtime/py/freestanding_gc_relocation_drain.py
76d05529b92aa19022bbb707c0217175e68d0c89cacd65c5cd9606c1ad3a0df0  pcc/py_runtime/py/freestanding_gc_barrier_dispatcher.py
c412de897ecae066ba9cc0fc1290a4630a43d06818c8a544474be7f6242e6c02  pcc/py_runtime/py/py_gc_backend.py
e1251099e73dea03ad939ee004e88d45a6feefbb241e0a005e651ecb3944ce4f  pcc/py_frontend/codegen/runtime_abi.py
797262e2723e4bda3aa2d32648dea867b57888acb445e4fe87773625ff67cc15  tests/python/test_freestanding_gc_forwarding_retirement.py
bad38344b8517ac4151f99da78d9af3ba5c5ce1d976bd05b68a3c6d4697fb482  tests/python/test_freestanding_gc_relocation_drain.py
a5a1b03830cf840c093b55596332f1013f0720657cf1ea3be0c2b8c542c719a5  tests/python/test_freestanding_gc_barrier_dispatcher.py
076cd8df23caba95949cbfbbf8677fe934fdf14a217fe331a832135be2fdaf1e  tests/python/test_gc_update_referents.py
```

These hashes remained unchanged through the final dynamic packets.

## RED and implementation

The new source/ABI/order regression was first run against the old retained-page
release body and failed because `free(span)` remained inside the strict locked
partition (`1 failed in 0.10s`). The final regression covers all three outer
caller families in both mirrors, both public/cross-object ABI tables, and the
absence of physical frees in the locked partition.

The implementation returns a detached eligible-page list from remap and adds
one finish owner that clears/frees the span and page after graph unlock. No
extra epoch or page-state transition was introduced.

The first strict closure packet exposed one local implementation error:
`freestanding_gc_relocation_drain.py` used the new `null()` initialization
without importing the intrinsic. The LLVM closure failed closed on a
`py_module_attr_get` reference. Adding the missing `pcc.unsafe.null` import made
the exact original node and the complete six-emitter closure packet green; no
managed-runtime fallback was added.

## Focused gates

LLVM/self strict closure for forwarding retirement, object/page drain and the
barrier dispatcher:

```text
6 passed in 3.05s
```

Log: `build/gc4-a3b-retained-page-finish-closure.log`, SHA-256
`183bf6fd2fd84c042a45a4560146bb8fecf303fea95edf5d16dd561f1302b72c`.

Complete source, ABI, archive-owner and C/strict differential packet for the
three directly affected modules:

```text
gtimeout 360s sh -c 'set -o pipefail; env -u LC_ALL uv run pytest \
  -vv -x -n0 --tb=short \
  tests/python/test_freestanding_gc_forwarding_retirement.py \
  tests/python/test_freestanding_gc_relocation_drain.py \
  tests/python/test_freestanding_gc_barrier_dispatcher.py \
  2>&1 | tee build/gc4-a3b-retained-page-finish-source-owner.log'

24 passed in 135.66s
```

Log SHA-256:
`47bfa3bd27f9a2ac92315dafbaa9a36bd131e9f05cc3c14ee09c681183d1ebcb`.

All fourteen type-specific raw-payload cases in both runtime roots plus the
strict relocation-payload ownership neighbors:

```text
21 passed in 7.39s
```

Log: `build/gc4-a3b-retained-page-finish-behavior-final.log`, SHA-256
`b223afec8fccb3fa8a635e7ef09a3bb103e71b7640a2d399ae4ef2538fc989c8`.

Fragmentation, stable-ID and GC3 oldification compatibility:

```text
7 passed in 22.84s
```

Log: `build/gc4-a3b-retained-page-finish-compatibility-final.log`, SHA-256
`73eb5490c56cdadbae5e125e37d965fd8ec1617f4c98525fb9524e3cea7649ea`.

One initial 28-node combined behavior command reached 78% but its 150-second
watchdog expired during the stable-ID node and produced no summary. It is not
green evidence. Immediate process inspection found no surviving pytest,
pcc1/pcc2/pcc3 or bootstrap child; the two complete sharded summaries above
replace it.

The four root-slot source contracts affected by the remap return signature
passed 4/4. `python -m py_compile` passed for all affected strict/ABI/test
files. C syntax passed with `PCC_WITH_THREADS=0` and `=1`; both configurations
reported only the same five pre-existing unused-static-helper warnings.
`git diff --check` was clean.

## Open boundary

This slice does not establish general mutator quiescence. Remap still heals
roots and referents and retires forwarding state while holding the graph lock.
Its forwarding retirement can unlink/free forwarding nodes, decref targets,
retire identity/object metadata and invoke source-payload retirement with
allocation/free/decref behavior. Those owners require their own ordering and
reentry proof before moving across the lock.

The pre-existing stale-candidate/fairness stress remains red as recorded by
the preceding forwarding-plan evidence; this slice neither changes nor
weakens its assertion. Public-copy rehash loops, raw byte copy, source/page
lifetime across planning, lock-free readers, allocator failure, concurrent or
nested drains, ABA/backend switching, remembered roots, target death,
callbacks/raw leases, resurrection, physical movement, A3c, broad parity,
performance and fixed point also remain open. No bootstrap chain, broad
default suite, performance gate or five-GC matrix was run for this finite
correctness slice. The parent task remains `IN_PROGRESS`.
