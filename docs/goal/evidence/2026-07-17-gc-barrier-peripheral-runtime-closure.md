# GC pointer-slot audit — peripheral runtime closure

Date: 2026-07-17

Task: `AUD-P0-GC-BARRIER-WRITE-AUDIT`

## Outcome

The finite pointer-slot audit is closed.  The earlier evidence classified the
core containers and major instance/class/tuple/exception/generator/coroutine
families.  This slice followed the remaining branches of
`py_obj_visit_slots()` through every peripheral owner-slot family:

- `PY_TYPE_FUNC`: C-API self/module/weakref metadata, captures, bound self, and
  attrs are visited; constructors and mutations use `pcc_gc_store_ptr`; null
  and teardown stores are not value stores.
- `PY_TYPE_ITER` and enumerate: the iterator sequence is an owned visited slot,
  initialized only on a fresh object and read through `pcc_gc_load_ptr`;
  enumerate owns no additional inline object slot.
- module attrs and function-code/class caches: module attrs mutate through the
  barriered dict API; external cache nodes store only pinned objects.
- weakrefs: callback is owned and barriered; target is explicitly
  borrowed-update-only and repaired during relocation.
- property/classmethod/staticmethod, memoryview, thread, and virtual-thread
  fields: all owned fields appear in the fixed visitor and use store/load
  helpers; waiter/scheduler nodes register external roots.
- continuation stack slots and C-extension module state use the existing slot
  visitor/root-registration contracts.
- the C-API shim has exactly three internal static extension types.  All three
  are now explicitly traced and managed-deallocated.

The sweep found four real peripheral gaps and fixed them:

1. `PySeqIter_New`'s owned `seq` field had no `tp_traverse` or managed
   `tp_dealloc`, and iteration read it raw.  It now uses the common C-extension
   slot visitor, managed release, store barrier, and relocation load.
2. the shim `ContextVar`'s owned `def`/`value` fields had the same trace/release
   omission.  Construction, get, set, and reset now preserve its ownership
   transfer semantics while notifying slot writes and healing forwarded reads.
3. the shim slice object's owned `start`/`stop`/`step` fields had the same
   omission.  They now trace, release, barrier-store, and relocation-load.
4. the TLS current-exception reference was healed only when another exception
   was raised.  Direct `py_current_exception()` and `py_clear_exception()`
   could therefore expose or release an old backend-4 address.  C and
   pcc-Python now share a resolve-and-rewrite helper; raise, current, clear,
   traceback formatting, and context-exit stashing enter through it.

No other unclassified or suspected peripheral owner-slot omission remains.
The C-API shim has no pcc-Python mirror; the TLS exception change is mirrored
in `pcc/py_runtime/py/py_exc_tls.py` and `py_exc_traceback.py`.

## Gates

Task-board write-barrier gate, including the new C-API and TLS source/mirror
contracts:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_codegen_write_barrier.py
8 passed in 0.64s
```

Task-board backend-4 mutation gate:

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_backend4_production.py::test_backend4_list_mutations_load_forwarded_item_slots
1 passed in 6.81s
```

New load-bearing backend-4 probes:

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_backend4_production.py::test_backend4_capi_internal_owner_slots_trace_and_load_forwarded_values
1 passed in 7.23s

gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_backend4_production.py::test_backend4_tls_exception_accessors_heal_forwarded_reference
1 passed in 7.38s
```

The first probe verifies the exact `1/2/3` owned-slot traverse surface for
sequence iterator / ContextVar / slice and then relocates the iterator sequence
and ContextVar default.  The second relocates pending exceptions and verifies
both direct-current healing and direct-clear behavior.

Focused regression for the refactored raise path plus actual compilation and
execution of the pcc-Python exception port in self/no-libpython mode:

```text
gtimeout 300s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_gc_backend4_production.py::test_backend4_raise_context_chaining_resolves_forwarded_current_exception \
  'tests/python/test_native_exception_context_chaining.py::test_exception_context_chaining_matches_cpython[port]'
2 passed in 6.92s
```

No full GCC suite and no five-GC compiler-bootstrap matrix was run.

## Claim boundary

This proves the exact C and pcc-Python runtime pointer-slot surface reachable
through the shared object visitor plus registered external roots is classified,
and the concrete peripheral omissions found by that enumeration are fixed and
behavior-tested.  It does not claim arbitrary third-party C extensions declare
correct `tp_traverse` functions; the runtime can only consume the slots an
extension exposes through that contract.
