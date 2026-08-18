# GC4 container constructor publication — 2026-08-24

## Claim

C and strict pcc-Python now share one internal
`pcc_gc_publish_initialized(obj)` ABI.  For Backend 4, allocation of list,
dict, set and tuple sets `PY_FLAG_GC_FRESH_ALLOC`; direct relocation admission
and normal selectors reject that flag.  Publication clears it only while the
graph/no-park lock is held.

The four container contracts are explicit:

- list publishes after its items array and owner payload span exist;
- dict publishes after indices/entries and entries span exist;
- set publishes after its entries table/span exists; and
- empty tuple publishes at constructor completion, while a non-empty tuple
  remains fresh until `py_tuple_set_item` observes every inline slot non-NULL.

Only these four tags are marked fresh in this slice.  Other relocation-supported
tags retain their previous behavior and remain in No.12b+; they are not left
permanently ineligible by an over-broad allocator change.

## Dynamic proof

In threaded C and strict runtime archives under GC4:

- a raw list allocation carries FRESH and direct relocation-set add returns 0;
- explicit publication clears FRESH and admission returns 1;
- a two-slot tuple is rejected before fill and after its first slot, then its
  second slot publishes it and admission succeeds; and
- completed empty list/dict/set/tuple objects have no FRESH flag and each is
  directly admissible.

Existing concurrent object/page selector tests remain green in both runtimes.
No production pause or test-only publication hook was added.

This closes container constructors only.  Property/descriptors, function/
iterator/frame/task/class/instance/scalar and other supported constructors,
C-API raw leases, callbacks beyond list, resurrection, stage2 performance,
fixed point and broad five-GC parity remain open.

## Gates

- C syntax threads off/on for allocator and five container owners; strict
  `py_compile` and self/no-libpython closures: pass (pre-existing pointer
  warnings only).
- source/ABI/chunk contract: `3 passed in 0.41s`.
- final C/strict publication plus concurrent object/page selectors:
  `6 passed in 2.12s`.
- full source/ABI/GC abstraction neighbors: `32 passed in 31.39s`.
- task relocation payload/forwarding retirement gate:
  `24 passed in 140.24s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-container-constructor-publication-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
d9fd52dba495a2c9d3f1cf15694d297fe5d5ada9ab23a5c6ba7d1e786095bf12  pcc/py_runtime/src/py_obj.c
9db3ba6b7faaf33692416eff405113815901e426d1642349f0579d92e658fbfb  pcc/py_runtime/py/py_obj.py
2f599f574a2dfa6addab6e4d4644f23082738467ca52b6e361ab237d85a7dadd  pcc/py_runtime/src/py_list.c
5acbb12cf2fe0f6e12f20aeb144962b8571b77fc70fff22951110ba1eb39b5ad  pcc/py_runtime/src/py_dict.c
d9b1f31ec68e0f501b2ae9d0d1c043cac861f4d446f6adeeae7a9e39115c999f  pcc/py_runtime/src/py_set.c
11635d7b12d8707922a4eb1c31fa377592267e15fcf390e962387368292c78f5  pcc/py_runtime/src/py_tuple.c
4c0e74c0a665905f487aeb3e62267e4206502aee96018298043957101de28488  pcc/py_runtime/py/py_list.py
005d04813c273e2210a7b58ed4802563edf45a36a171bf9fd4d7ec596823878e  pcc/py_runtime/py/py_dict.py
cbb1a28daef206890419cd70b59d2cf795d0f92c9ed852a0406a6c4edc0d63da  pcc/py_runtime/py/py_set.py
133dee5b79718fdbb81a639f6cef0b398ac2ce0b2b9f5dbf5bd353984e5d82dd  pcc/py_runtime/py/py_tuple.py
f3f98979a551dab387e267f7cfd8e1a80febb6555244ddc22fc92656fe379f5e  pcc/py_runtime/src/py_internal.h
39017b9032539bd1ef2458d140e871ad8817c462e8bbc0cae499a2cf643dcfae  pcc/py_frontend/codegen/runtime_abi.py
a316d072d1eb63ecdb5beb351701de61b811b5788ea03c96400724af1a608c41  tests/python/test_gc_threading_substrate.py
d687f0d1eb17d9cc6d782b31e413900c0b42541932c06a58d5a208e983902f19  build/gc4-container-constructor-publication-final.log
08732e3742121afa8dabd0f2082fa938ac4ecf2598c8abee6de40e1b6a5452f6  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.12a list/dict/set/tuple constructor publication.
The GC4 parent remains `IN_PROGRESS` for all other supported tags.
