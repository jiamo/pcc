# GC3 cpy-handle oldification ownership

Date: 2026-08-23

Task: `GC-P0-GC3-CPY-HANDLE-OLDIFY-OWNERSHIP`

Status: `DONE_STRONG` for the scoped Backend-3 ownership defect.

## Claim boundary

C and strict pcc-Python Backend 3 now use one exclusive-transfer contract when
copy-oldifying `PY_TYPE_CPY_HANDLE`: the bytewise target receives the one owned
foreign reference before forwarding publication, the source handle is cleared,
and forwarding-install failure moves ownership back before raw target cleanup.
Retiring/decrefing the forwarding source therefore cannot call the foreign
release hook while the oldified target is live. The target continues to return
the foreign pointer and its terminal drop calls the hook exactly once.

This is a pcc-native/no-libpython runtime ownership proof using a synthetic
foreign pointer and release callback. It does not prove execution of a real
CPython extension, libpython bridge acceptance, Backend-4 cpy-handle movement,
or the parent GC4 mutator-quiescence task.

## Genuine RED and diagnosis

Before the production edit, the new source/ordering regression failed because
strict oldification did not support cpy handles:

```text
assert "PY_TYPE_CPY_HANDLE" in strict
1 failed in 0.09s
```

The first helper name, `py_cpy_handle_move_owned_ref`, also made the direct
strict no-libpython closure fail: the frontend intentionally classifies every
`py_cpy_*` call as a CPython bridge and replaced the oldifier with a
`py_exc_new` fail-closed stub. That proposal was denied. The accepted ABI is
`pcc_cpy_handle_move_owned_ref`, accurately naming a pcc-runtime ownership
primitive with no CPython operation.

## Implementation

- `pcc_cpy_handle_move_owned_ref(from, to)` exists in both the C runtime and
  the pcc-Python `py_obj_dealloc` owner. It accepts the bytewise-copy state
  (`to` already contains the same pointer) and the rollback state (`to` is
  NULL), clears the source, and leaves exactly one owner.
- C and strict GC3 oldifiers invoke the move after payload preparation and
  object-index publication but before `pcc_gc_install_forwarding_unlocked`.
  The failure branch performs the reverse move before removing/freeing the
  unpublished target.
- Strict GC3 now admits `PY_TYPE_CPY_HANDLE` only with that transfer operation.
  The exact cross-object ABI is `(c_ptr, c_ptr) -> c_void`; no ordinary
  libpython/runtime fallback signature was added.
- The public header documents move rather than retain semantics. No foreign
  retain hook, package special case, or Backend-4 policy change was introduced.

## Runtime evidence

One true-pthread probe is linked separately against the C runtime and the
strict pcc-Python production runtime. It stores a young cpy handle in the TLS
exception root and runs GC3 promotion. Both modes prove:

1. TLS now points at a distinct OLD target;
2. `py_cpy_handle_get(target)` still returns the original foreign pointer;
3. source cleanup has made zero release calls;
4. after clearing TLS and terminally decrefing the target, the release hook has
   run exactly once; and
5. the hook can wake and join a contender that acquires the graph lock, proving
   the callback occurs after the locked promotion holder.

The current-source packet passed 2/2 in 126.31 seconds and is preserved at
`build/gc3-cpy-handle-oldify-final-current.log`.

## Gates

- Direct self-backend, strict no-libpython closures passed for
  `freestanding_gc_generational_oldification.py` and `py_obj_dealloc.py`.
- Required oldification source/ABI/archive-owner file: 6/6 passed in 1.74
  seconds from a successful current-source cache. The cold owner node was
  measured separately at 123.21 seconds because the task-card 120-second
  watchdog cannot finish a cold production archive build.
- Current-source C/strict dynamic ownership packet: 2/2 passed in 126.31
  seconds.
- Current-source strict symbol-owner and baseline exactly-once deallocation
  neighbors: 2/2 passed in 1.06 seconds.
- Runtime tripwire/source ABI packet: 2/2 passed in 0.07 seconds.
- Bootstrap baseline: 2/2 passed, 2 deselected in 0.60 seconds.
- Python syntax, C syntax with threads off/on, and `git diff --check` passed.

The optional combined fallback/IR baseline command emitted 15 progress dots
but reached its 120-second watchdog without a final summary; it is
inconclusive and is not reported as green. The broader runtime-ABI chunking
test also remains non-green in the current shared tree (`max chunk 57 > 50`);
the task-specific exact cross-object signature assertion is green, and this
slice did not expand an ordinary runtime-signature chunk.

## Frozen identities

```text
45b1bae91957ad265bfbc52ca9dd8f5510b56527f9811c5a8c374952b5f963bd  pcc/py_runtime/include/py_runtime.h
69908a1907a7c17f0c6f8e038b121e5e1a2ccd450d6d166d62f20c2c52c2e025  pcc/py_runtime/src/py_cpy_handle.c
a1849e802ec2668ed596969c7a05e01ba6c3656c831dcdf29c1a7c37f481a58c  pcc/py_runtime/src/py_gc_backend.c
b77960eba368f4b4c8af9d1cce97b5ec4421afb7980d2069d1f8e92a76dafb45  pcc/py_runtime/py/py_obj_dealloc.py
a105a0c29d27bc816b5a57708b5346812f77ec12190213c1ba780ed1460b6ccf  pcc/py_runtime/py/freestanding_gc_generational_oldification.py
23d776b1d0af1950d37993429b8de6ae988e78d5e3950421eb6d1edddb65c987  pcc/py_frontend/codegen/runtime_abi.py
594e490c3dcf7d82cf089c9c621e1234b91718b5f8a7cb73fa95fafb364890a3  tests/python/test_freestanding_gc_generational_oldification.py
a03d32e9115f6e9d51b5ee8b4fc597eb60b2c8f096fc16d7981164cc6a62df3c  tests/python/test_gc_backend_generational.py
a7f68bdc6c5fc993af911a52e1aa7576e85d9d24f8440fe18921f1d84f358710  tests/python/test_freestanding_runtime_no_c_closure.py
6756e12b4dee6e8efc57da1bca7fe41758a6c7ccbb66618c16d6009439822171  /tmp/gc3_cpy_handle_oldification.ll
cbb2bc1da9ff17e21a02ef61ec4fffb27807a9ca5ec7a79bef8afc54cfae4abf  /tmp/gc3_cpy_handle_obj_dealloc.ll
ee7632e04f1274a288a3463b5863e3b1752ccea23f33794cf746078d25d6bdd6  build/gc3-cpy-handle-oldify-source-final.log
6cf928e49afa748b652e8c73b5fd002525d602cdbd17d376aceef1ed0ccb8c0b  build/gc3-cpy-handle-oldify-final-current.log
dfb949ae5a8440bed26c849b9c7497fa5b891948fd76c006b8b19667b326d093  build/gc3-cpy-handle-owner-current.log
b9fb5817654cf2b53505627054e57925fadcbee9f2733642fe9c6215fb6903b9  build/gc3-cpy-handle-oldify-archive-owner.log
```

## Open boundary

None for this scoped GC3 defect. The parent task still owns remaining locked
callbacks and unbounded root/referent holders before A3c may be connected.
