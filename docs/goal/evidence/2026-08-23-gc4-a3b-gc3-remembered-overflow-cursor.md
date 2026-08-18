# GC4 A3b GC3 remembered-overflow cursor

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b holder sub-boundary confirmed; parent task remains
`IN_PROGRESS`.

## Claim boundary

Backend-3 remembered-owner overflow fallback in C and strict pcc-Python now
examines at most 16 tracked-object nodes per generational step, including
inactive and nonmatching nodes. The scan returns examined work, so a full
nonmatching batch remains schedulable instead of falsely returning zero.

The retained scan cursor is unlink-safe: object-node unlink advances it before
the node can enter the recycle pool. Every object-list head link/clear and
non-head unlink advances a revision; a revision mismatch restarts the next
batch from the authoritative head. A newly remembered owner whose queue-node
allocation fails also requests a head restart, so it cannot be skipped behind
an older cursor.

This does not close the broader graph-lock holder inventory. TLS exception
oldification still cleanup-decrefs under the lock. Registered frame/scheduler
roots, extension-module roots, and owner referent visitors remain potentially
unbounded or callback-capable. A3c remains disconnected.

## Genuine RED

The new source regression failed before production edits on the absent strict
allocation seam/cursor:

```text
assert "pcc_gc_backend3_remembered_owner_allocation_limit" in strict_remember
1 failed in 0.10s
```

## Implementation

- Added a default-inactive remembered-owner allocation diagnostic:
  `-1` is normal allocation, `0` forces overflow, and a positive value permits
  that many queue-node allocations. C and strict expose the same public ABI.
- Added object-list revision, remembered-scan revision and remembered-scan
  cursor state to C and the strict freestanding state/runtime ABI.
- Centralized strict head mutation through the existing list-head owner and
  taught both unlink implementations to move the retained cursor before node
  retirement.
- Reworked overflow drain to detach queued remembered nodes once, retain the
  overflow bit across incomplete batches, return examined nodes as work, and
  clear overflow only when the cursor reaches the end.
- Full object-list teardown clears the retained cursor before freeing nodes.

## Deterministic runtime probe

The same probe runs against `libpy_runtime.a` and
`libpy_runtime_pcc_py.a`. It:

1. promotes one rooted list owner to old;
2. links 31 nonmatching objects and one young child ahead of that owner;
3. forces remembered-owner queue allocation failure;
4. runs one 16-node overflow batch;
5. links another object between batches to invalidate the revision;
6. observes three `16 + owner still remembered` batches, followed by a
   bounded fourth batch that clears the owner flag.

Both runtime arms passed. The pcc-Python cold/current-source build completed
in 123.87 seconds.

## Focused evidence

- Python syntax passed for all touched Python sources and tests.
- C syntax passed with `PCC_WITH_THREADS=0` and `PCC_WITH_THREADS=1`.
- `git diff --check` passed.
- Direct `--backend self --python-libpython=off --ir-scaffold=on
  --python-library --emit-llvm` closure passed for state, object-node and
  remembered-owner strict roots.
- Source/ABI packet: 16 passed, 5 archive tests deliberately deselected.
- Final focused packet: 12/12 passed in 2.72 seconds. It includes LLVM/self
  exact strict closures, C/strict overflow probes, state ABI typing and the
  existing C/strict remembered-owner referent-rewrite neighbor.

## Frozen identities

```text
bab0362bb13d99ab1fce2c00a18e2600cfe65b79bb6345aba1e32ff9631ec37a  pcc/py_runtime/src/py_gc_backend.c
137538e52808f20442fe05b506672dbbb9f0798f3259c8ef8acbfbcc32d364e3  pcc/py_runtime/include/py_runtime.h
9cb753ceaba5ac32cadac09509d6cf9543b37052e2bdde1bb0941c9b8c2098e3  pcc/py_runtime/py/freestanding_gc_state.py
39d23f4d84f25342586af3e9750e66966501f287214b6e59009700d4f30fc56a  pcc/py_runtime/py/freestanding_gc_object_nodes.py
e08b45fcfebc7be744abda784f6bc0a2063f76c0964458b2d1a89f6c65d4c524  pcc/py_runtime/py/freestanding_gc_generational_remembered_owners.py
9ee1a1b514c71f7b4b1f3ac892fc9b77a0fdeb58f2eb7b61a65cbfc043c19383  pcc/py_runtime/py/py_gc_backend.py
f93282edf73d7666b565c3b58618771d1c141b62d344d3f54d49ff15209cdd22  pcc/py_frontend/codegen/runtime_abi.py
6cac3fb866013e28cc589e6246912c73cd13af8d00e457f659734283c9a0e209  tests/python/test_freestanding_gc_generational_remembered_owners.py
868a771f455cb2a26d027c0c570b832070503e40b9c71dd452a9fdffa667cc8f  tests/python/test_freestanding_gc_object_nodes.py
86deb979520005e81abc9bb0083113342d47d6d592e6b96203786fbb0596f31e  tests/python/test_freestanding_gc_state.py
58fe9f27bed8ead508dfcfaeed8227e48c98c276e0f389fca9e794ec7ca502c6  tests/python/test_gc_backend_generational.py
9c7bf55ef50e6bc32b3eb576459b0fa2ccbe310f4a9a3566919b38dc9b1d88b8  build/gc3-remembered-overflow-source.log
175d9c89f6783bf838bff6c534389b4b22a69758b74c2ec3d21fbb2dbc062f4a  build/gc3-remembered-overflow-c-runtime.log
55a24f634e45146f3d546655b252111fdc0b53ee940e7a00ad963ec36309ca6c  build/gc3-remembered-overflow-pcc-py-runtime.log
c0cac05031864b7df8f7fc30be39d7d7329fd3ddea5be0cda9d9d53e269a635e  build/gc3-remembered-overflow-final.log
b083fc1712c2bbd810e521ee0d9f47a044716a0162e47fba145713c24d056571  build/gc3-remembered-overflow-source-identity.txt
```

## Next boundary

Do not connect A3c. Split TLS exception oldification so saved-reference
cleanup decref occurs after graph unlock. Then inventory and split registered
root, extension-module and caller/runtime-root callback holders.
