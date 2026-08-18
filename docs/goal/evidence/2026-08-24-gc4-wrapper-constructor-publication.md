# GC4 wrapper constructor publication — 2026-08-24

## Claim

Backend 4 C and strict allocators now mark property, classmethod and weakref
objects FRESH, and each unique constructor publishes only after its complete
pointer topology is installed:

- classmethod after the wrapped function store and GC tracking;
- property after optional getter/setter/deleter stores and tracking; and
- weakref after target/callback fields, intrusive weakref-list links and
  constructor logging.

Direct relocation admission rejects all three while fresh.  The same internal
graph-locked publication ABI from the container slice clears the flag.

`PY_TYPE_STATICMETHOD` is deliberately not added: the runtime layout/visitor
supports the tag, but source documents that static methods lower directly to
their callable and there is no public runtime constructor.  Marking the tag
fresh without a publication owner would make any future object permanently
ineligible; adding a constructor or removing the unreachable tag remains later
inventory.

## Dynamic proof

Threaded C and strict probes show raw property/classmethod/weakref allocations
carry FRESH and reject direct relocation add; explicit publication makes them
admissible.  Real `py_property_new`, `py_classmethod_new` and `py_weakref_new`
results have FRESH cleared and are directly admissible.  Container constructor
and concurrent object/page selector neighbors remain green.

This closes only these three wrappers.  Function/iterator/frame/task/class/
instance/scalar constructors, staticmethod disposition, C-API raw leases,
callbacks beyond list, resurrection, stage2 performance, fixed point and broad
five-GC parity remain open.

## Gates

- C syntax threads off/on and strict `py_compile`/self-no-libpython closure for
  allocator/class/weakref owners: pass.
- wrapper source contract: pass.
- final C/strict container+wrapper+selector matrix: `8 passed in 2.46s`.
- full source/ABI/GC abstraction neighbors: `32 passed in 30.80s`.
- task relocation payload/forwarding retirement gate:
  `24 passed in 140.58s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-wrapper-constructor-publication-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
577af6fe96aab39160f97eb727a33658751247226bc8f9a3d2bcb6d606de86e4  pcc/py_runtime/src/py_obj.c
d081ef4d75252c8dcd98073a1965977bb4ebb638b807092fe2965d2f737c2a13  pcc/py_runtime/py/py_obj.py
d1320f5c7ea74cdbee8c2dd3b0ab8996477ad2ff3cb5db7c487e98c0eb70ea0e  pcc/py_runtime/src/py_class_attrs.c
f4b878d75fd1f29e86c192db825a9a3d45a88fcfb7c3131e3e0104d06a9b258a  pcc/py_runtime/py/py_class.py
d68f253e3ca84d2eb0761547dec263cf05badc72ab26f741713dd21ea3c82908  pcc/py_runtime/src/py_weakref.c
ff1d0b8509060deaf6a224365e4a7594aa2b4f432cd15e538241773839ab50eb  pcc/py_runtime/py/py_weakref.py
75c017e916cdf53c58ce1f554a4e9aa2ea181a8ef1f44fccad1f08a27872d581  tests/python/test_gc_threading_substrate.py
4a6c2164880aaf9c99bb72fd5731a30f7ef26dd73a809f19e6155b80b0770676  build/gc4-wrapper-constructor-publication-final.log
f7b2a5c820f8c83e3d18f366709a014534fe899dddc47666608adb79dc6d3789  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.12b property/classmethod/weakref constructor
publication.  The GC4 parent remains `IN_PROGRESS` for other supported tags.
