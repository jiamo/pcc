# GC4 C-API borrowed item lifetime pins — 2026-08-24

## Claim

C production shim, C oracle and strict pcc-Python C-API owners now pin the live
item returned by unpaired borrowed APIs before dropping their internal owned
reference:

- `PyTuple_GetItem`;
- `PyList_GetItem`;
- `PyDict_GetItem`; and
- `PyDict_GetItemWithError`.

The pin supplies address stability for the documented borrowed lifetime; it
does not add ownership or promise validity after container mutation/destruction.
`PyList_GetItemRef` now calls the owned `py_list_get` directly, so the owned API
does not accidentally inherit a permanent pin from its borrowed sibling.  Dict
Ref APIs already used the owned getter directly.

Backend-4 direct relocation admission now rejects `PY_FLAG_GC_PINNED` in both C
add paths and the strict add owner, matching the existing normal selector rule.

## Dynamic proof

Threaded C and strict probes return exact borrowed values from list, tuple and
dict.  Each returned heap item carries PINNED and direct relocation admission
returns 0.  A separate `PyList_GetItemRef` result remains unpinned, owns one
reference, and direct relocation admission returns 1 before balanced cleanup.

This closes borrowed container items only.  `PySequence_Fast_ITEMS`, unicode/
bytes raw pointers and counted `Py_buffer`/memoryview leases remain open, as do
constructor blockers, callbacks beyond list, resurrection, stage2 performance,
fixed point and broad five-GC parity.

## Gates

- C/oracle/strict source-order contract: pass.
- strict C-API owner self/no-libpython closures: pass.
- final source + C/strict borrowed/owned matrix: `3 passed in 0.92s`.
- full source/ABI/GC abstraction neighbors: `32 passed in 31.99s`.
- task relocation payload/forwarding retirement gate:
  `24 passed in 147.38s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-capi-borrowed-item-pin-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
8ffaee5b6c6124736098cf2f8eeb010a03c4cd3c02d68b1035ca884351bdee44  pcc/py_runtime/src/py_capi_shim.c
6e3b448fc2f669785fe972c7511fa61dad1cf07ba022509fb4e3f44702168281  pcc/py_runtime/src/py_capi_shim_oracle.c
6d081fa40a2b2b260250d99365229b2e5cdb3adfe05817d1e75b1a17642e0e60  pcc/py_runtime/py/py_capi_collections_runtime.py
f5741e26905ff3a82cf2e6d30178ce8df25dc4f4e5d5a196a3c09edd78d0a591  pcc/py_runtime/py/py_capi_dict_runtime.py
e683f7c465dde8331e30366f47c7375dc88de349d96b1ae34d92a8ddf3170105  pcc/py_runtime/src/py_gc_backend.c
f9304f5251f416fce11d369dcc840812e57fa3643b2e99fff7f61a086c87c5f4  pcc/py_runtime/py/py_gc_backend.py
d032ce2f5137bbf17bde4bf7e62722138209ae96d66f58c6c5e99b178119ca93  tests/python/test_gc_threading_substrate.py
bf3a4d6bde6a1958fb9e4467b4ac24a3aea21548165af55e9a64d59f0681e53b  build/gc4-capi-borrowed-item-pin-final.log
d3da9d49af22ea24fbb82943ba361004351281ac9264b9773b0ba3a41a5d9f6e  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.13a borrowed list/tuple/dict item lifetime pins.
The GC4 parent remains `IN_PROGRESS` for other raw views and leases.
