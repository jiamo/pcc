# Investigation: Backend #3 cross-domain remembered slot rewrite

## Status
resolved

## Problem Description
Backend #3 had many focused remembered-slot rewrite gates, but the remaining
production checklist still called out cross-domain remembered-set sharing.  The
missing evidence was a case where one native mutator thread allocates a young
minor object, another thread stores that object into an old remembered owner,
and the collecting thread promotes the cross-domain young child and rewrites
the old owner's slot.

## Repro
Run the focused C-runtime gate:

```bash
env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_backend_generational.py::test_generational_backend_cross_domain_remembered_slot_rewrite \
  -q -n0
```

Run the pcc-Python runtime-high mirror gate:

```bash
env -u LC_ALL -u LC_CTYPE uv run pytest \
  tests/python/test_gc_backend_generational.py::test_generational_backend_pcc_python_runtime_cross_domain_remembered_slot_rewrite \
  -q -n0
```

## Test
The regression builds a `PCC_WITH_THREADS=1` runtime archive and verifies:

- the child object is allocated by a worker thread with a different thread id;
- the child is a young minor-arena object;
- the main-thread old owner is marked remembered after the store;
- a generational promotion step oldifies the child into non-minor old storage;
- the old owner slot is rewritten to the forwarded copy;
- the forwarded minor source rejects a second forwarding install;
- `pcc_gc_load_ptr()` keeps returning the forwarded copy.

The same helper runs against `libpy_runtime.a` and
`libpy_runtime_pcc_py.a`.

## Result
Confirmed focused gates:

```text
test_generational_backend_cross_domain_remembered_slot_rewrite: 1 passed in 3.67s
test_generational_backend_pcc_python_runtime_cross_domain_remembered_slot_rewrite: 1 passed in 26.51s
```

## Notes
While adding the gate, the first draft released the worker-owned child reference
before checking the forwarding table.  That was a harness bug: after the owner
slot is rewritten, the source object's refcount can drop to zero and deallocation
correctly removes the forwarding entry.  The final regression keeps the worker
reference alive until all forwarding checks finish.

This closes the explicit cross-domain remembered-slot evidence gap for Backend
#3's current object model.  It does not claim a full OCaml domain runtime; the
collector still uses pcc's shared object registry and thread-local minor blocks.
