# Last-decref resurrection metadata restore — 2026-08-25

## Claim

C and strict pcc-Python terminal decref now delay instance/user-class GC
metadata removal through dealloc dispatch. `py_instance_dealloc` runs `__del__`
while the object remains indexed/managed:

- on resurrection, the compatibility `py_gc_track` is idempotent, GC3/GC4
  metadata is validated, then DEALLOCATING is cleared;
- without resurrection, fields/dynamic attrs are released, then the instance
  deallocator performs the delayed freeing-note/untrack/free;
- Backend-4 zpage objects preserve the established free-object-memory-before-
  freeing-note order so page recycling cannot precede type cleanup.

No guessed size/origin reconstruction and no flag-clear-only path is used.
Other type tags retain the generic terminal-decref order.

## Dynamic proof

C and strict probes under GC3 and GC4 last-drop a native instance whose
`__del__` retains self. All four observe refcount one, DEALLOCATING clear,
managed provenance and object-index presence. GC3 retains a valid YOUNG/OLD
generation state; GC4 admits the live instance to relocation selection. A later
last drop does not call `__del__` again and removes the object index exactly
once.

## Gates

- source contract + C/strict GC3/GC4 probes: `5 passed in 152.22s`.
- existing default resurrection neighbor: `1 passed in 27.28s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 10.68s`.
- relocation payload/forwarding retirement: `24 passed in 159.54s`.
- bootstrap baseline: `2 passed, 2 deselected in 0.73s`.
- C syntax, strict py_obj/py_class closures and `git diff --check`: pass.

Durable logs:

- `build/gc-last-decref-resurrection-metadata.log`
- `build/gc-last-decref-resurrection-default-neighbor.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
c73a2dded1568b1318f180587b800941660c094fcb4147f87e639589da2c001f  pcc/py_runtime/src/py_obj.c
6307c3b3fac2c2ba08c4365e3eef0c53a170e542b34fbf9ffbfb7b4889019b93  pcc/py_runtime/src/py_class.c
a226efb1d37481278d36ae17936ea2928180ef4c0968ea7aa55a4fd0eb589aa3  pcc/py_runtime/py/py_obj.py
ed67eda6b685ec7ff55b0dc824f598911069e412ec607f35a381b49cf4c09fc2  pcc/py_runtime/py/py_class.py
88c593617dff274c56a5e430cd6ea528c56c3a0625744a63a5cfe2c29876b8fc  tests/python/test_gc_last_decref_resurrection_metadata.py
9092b47639443889b3fc9a040654323689f82d085aaa6921f42cee126ca46fbd  build/gc-last-decref-resurrection-metadata.log
f0b879bc636f85c22ebc51e53cddda5b6be9b7b659125d3ba145bee79b1c4f7c  build/gc-last-decref-resurrection-default-neighbor.log
ff4a5ebba1c891825f8b401bf9a9203888e4418c14e978cb58d243c73527710d  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for GC3/GC4 last-decref instance resurrection metadata.
