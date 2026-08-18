# GC4 A3b GC3 remembered-owner detached finish

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b holder sub-boundary confirmed; parent task remains
`IN_PROGRESS`.

## Claim boundary

Backend-3 remembered-owner nodes in the C transition root and strict
pcc-Python runtime are now structurally detached while the object graph lock is
held and physically freed only after the outer caller unlocks. This covers both
normal budgeted drain and overflow/telemetry whole-list clear.

The strict scheduler also moved its final processed-work safepoint after graph
unlock, matching the C root. Periodic safepoints inside remembered overflow,
normal drain and young promotion remain under the graph lock. TLS exception
oldification still has a cleanup decref under the lock, and extension/caller
root visitors remain callback-capable holders. This slice does not close those
boundaries and does not authorize A3c.

## Genuine RED

`test_remembered_owner_node_retirement_finishes_after_graph_unlock` was added
before implementation and failed because the detached-finish owner did not
exist:

```text
AssertionError: assert 'pcc_gc_backend3_finish_detached_remembered_owners' in ...
1 failed in 0.09s
```

## Implementation

- C `pcc_gc_backend3_remembered_owners_clear_unlocked` now returns the detached
  list. Normal drain prepends consumed nodes to one caller-owned finish chain.
- C telemetry reset and generational promotion free that chain only after
  `pcc_gc_graph_unlock()`.
- The strict remembered-owner object exposes matching detach, drain-with-output
  and finish ABIs. The scheduler allocates only its output slot before locking,
  drains under lock, unlocks, finishes nodes and only then safepoints.
- Managed telemetry reset follows the same detach/unlock/finish sequence.
- Runtime ABI signatures and strict source-owner/undefined-symbol inventories
  were updated exactly; no freestanding verifier rule was widened.

The first strict scheduler closure correctly failed because a trailing comma
after the third multiline `extern(...)` argument made the module-scope scanner
reject that binding from the exact cross-object allowlist. Removing that source
shape ambiguity made the existing signature-exact allowlist recognize
`(c_int64,c_ptr)->c_int64`; no diagnostic or boundary was weakened.

## Focused evidence

All pytest commands stopped at the first failure.

1. Source ordering, strict LLVM/self closure, overflow/budget preservation and
   the scheduler retry contract passed 10/10 in 2.14 seconds.

2. A C/strict runtime differential proves a remembered old owner promotes its
   young child and clears remembered state after the drain. A second
   differential proves cross-domain remembered-slot rewriting in both roots.
   The final packet was:

   ```text
   gtimeout 180s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short <14 focused node ids> 2>&1 | tee build/gc3-remembered-owner-detached-final.log'
   14 passed in 134.64s
   ```

3. The remembered-owner, generational-scheduler and managed runtime strict
   modules compiled directly with `--backend self --python-libpython=off
   --ir-scaffold=on --python-library`. LLVM receipt hashes are in
   `build/gc3-remembered-owner-detached-closures-final.log`.

4. Python syntax, C syntax under `PCC_WITH_THREADS=0/1`, and
   `git diff --check` passed. C emitted only the same unrelated pre-existing
   unused-function warnings.

## Frozen identities

```text
65a70a21f23c5baf2cf5af9d6fefcbc71f8b018f878d989f6629d9495c4e8773  pcc/py_runtime/src/py_gc_backend.c
220fa50d526caf8be02529142a89883623c3d6043fd32a6210f98fab1699d039  pcc/py_runtime/py/freestanding_gc_generational_remembered_owners.py
1998d02ab7024df9d18400699877e0479fa72d383bbf8bba6e1e3ff8c77e11a5  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
cff7896cda856a65af4c6c70cbd84144ef5e1832560ec3fff71e3fb6987b38d8  pcc/py_runtime/py/py_gc_backend.py
9fc8f9f66ec53940367d6b8ab6488811068c2386dcc67b3ad8a164012688ec0c  pcc/py_frontend/codegen/runtime_abi.py
dad1125ddcc3035cd696abd4394f8e0c40823071c924ce78473bebdd65a84355  tests/python/test_freestanding_gc_generational_remembered_owners.py
18f7f14f898d315e038882ce5bccb428e9efa8843f5ace1738f1ccbe67404a58  tests/python/test_freestanding_gc_generational_scheduler.py
0f112c1496af64bda34692f501e314d3385b5a941059590991856731500e5366  build/gc3-remembered-owner-detached-final.log
98d22366d32e2960b3ba90ad45ef6feb92584e39d2504e746a5ce1cc729e3e7e  build/gc3-remembered-owner-detached-closures-final.log
0cbd21f3f46ecfb95d394c2faf775d486a7b00b6cc30b76da4c490d9a769df6f  build/gc3-remembered-owner-detached-source-identity.txt
```

## Next boundary

Do not connect A3c. Split the periodic safepoints and promotion/TLS cleanup
decref out of the Backend-3 graph-lock tenure with an explicit bounded
snapshot/revalidation design. Extension-module and caller/runtime-root
callbacks remain a separate following boundary.
