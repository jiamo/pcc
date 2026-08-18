# GC4 A3b GC3 TLS cleanup after graph unlock

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b holder sub-boundary confirmed; parent task remains
`IN_PROGRESS`.

## Claim boundary

Backend-3 TLS exception copy-oldification in C and strict pcc-Python no longer
decrefs the replaced TLS object while holding the GC graph lock. The locked
helper publishes and promotes the replacement, transfers the old owned
reference through one `cleanup_out`, and the generational-step owner decrefs it
only after graph unlock and detached remembered-node finish.

This closes one cleanup tail, not the broader holder inventory. Registered
frame/scheduler root walks, extension-module roots, owner referent visitors and
caller/runtime callbacks remain potentially unbounded or callback-capable.
A3c remains disconnected.

## Genuine RED

The new C/strict ordering regression failed before production edits on the
absent strict cleanup owner:

```text
assert "cleanup_out: c_ptr" in strict_tls
1 failed in 0.09s
```

## Implementation

- C `pcc_gc_promote_tls_exception_root` now accepts `PyObject **cleanup_out`;
  strict `pcc_gc_generational_promote_tls_exception_root` accepts a `c_ptr`
  out slot.
- Copy success retains and publishes the oldified object, promotes its
  referents, and transfers the replaced TLS-owned reference without decref.
- The outer scheduler unlocks, finishes detached remembered-owner nodes, then
  performs the terminal decref.
- The exact strict cross-object ABI changed from `()` to `(c_ptr)`; no verifier
  relaxation or duplicate implementation was added.

## Runtime evidence

Existing C and strict scalar TLS probes both confirm that a young exception is
still promoted to an old TLS root.

The C runtime additionally has a copy-supported `PY_TYPE_CPY_HANDLE`, whose
foreign release hook is callback-capable. A true-pthread probe makes that hook
wake and join a contender whose next operation acquires the graph lock via
`pcc_gc_object_is_known`. The probe completes only when the terminal decref is
outside the graph lock; it passed under the 20-second subprocess watchdog.

Strict GC3 intentionally supports only int/float/str/complex/bytes/bytearray
copy-oldification, so it has no callback-capable tag for an equivalent dynamic
hook. Its cleanup ordering is instead proven by exact LLVM/self source closure
and the scalar runtime probe. An attempted strict cpy-handle arm returned rc=6
because strict correctly did not copy that tag; adding support would be unsafe
without a foreign retain/transfer contract.

That attempt exposed a distinct C ownership issue: C shallow-copies an owned
cpy handle, then source cleanup calls its foreign release hook while the copied
target remains in TLS. It is recorded separately as
`GC-P0-GC3-CPY-HANDLE-OLDIFY-OWNERSHIP`; no ownership fix is claimed here.

## Focused evidence

- Python syntax, C syntax with threads off/on, and `git diff --check` passed.
- Direct strict self/no-libpython closure emitted
  `/tmp/gc3_tls_cleanup_scheduler.ll`.
- Scheduler source/closure packet: 5/5 passed.
- C/strict scalar TLS plus C pthread callback packet: 3/3 passed.
- Final focused packet: 10/10 passed in 2.04 seconds, including the preceding
  C/strict remembered-overflow neighbor.

## Frozen identities

```text
214cc328961516a2e80f25b6253feebbb1a981b7a1602bd0a624a02949844952  pcc/py_runtime/src/py_gc_backend.c
0a89befd7bb653c87316cf5be16a333ad3d04a9bfa22f36b2b4247bca356438b  pcc/py_runtime/py/freestanding_gc_generational_scheduler.py
65000b32d8676f8829c828d1d314c8b5da5446db0462632f6cd74c033f6a6c57  pcc/py_frontend/codegen/runtime_abi.py
042c08ab0eb5f6af3d7499fe8a756006f09601155b0909547f532b1a6625e45f  tests/python/test_freestanding_gc_generational_scheduler.py
193c80a8dc80a398825331b8321490aa6fa753b76b52f49c9110fda647645790  tests/python/test_gc_backend_generational.py
471f507a754325c9d0eb0b6941c95cf8aa9137565ab0c32f50be7c0a8d9350b5  /tmp/gc3_tls_cleanup_scheduler.ll
0062deab1b69a42ae01e17a4b7e6f8daeb82e89825cdaecd3794d9a87b9e8a67  build/gc3-tls-cleanup-source.log
46a09a42c6257e8cb06573c93b35b71de4ba8d94c66e7affa880aecc04e34760  build/gc3-tls-cleanup-c-pthread.log
8bb569e2c369561186cce696996b661c4c9196b096adddd2d4d52150b7aa191b  build/gc3-tls-cleanup-runtime.log
61d43fbcbab26e125bcbb66b0110adacb8765836598e86b0de4d85cbb9106ac5  build/gc3-tls-cleanup-final.log
6bd11dff7aae7635ec47908f67ce1d5ab8f4bd55301ffcf10f7faa9257fc92e1  build/gc3-tls-cleanup-source-identity.txt
```

## Next boundary

Do not connect A3c. Inventory and split registered frame/scheduler roots,
extension-module roots, remembered-owner referent visits, and caller/runtime
callbacks or other unbounded graph-lock holders.
