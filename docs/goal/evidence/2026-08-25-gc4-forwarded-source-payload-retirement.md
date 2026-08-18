# GC4 forwarded-source payload retirement — 2026-08-25

## Claim

C and strict pcc-Python share one source-payload retirement ABI invoked before
normal two-epoch and target-dies forwarding teardown. The shared slot visitor
heals every role, saves/nulls only OWNED source slots, detaches supported raw
payloads, makes store/remembered/card/zpage metadata invisible, frees raw bases,
then releases side-table and saved-slot ownership after unlock/world resume.
Target death excludes the already-dying target from cleanup decref.

The original strict ordinary-slab regression now reports required
`2 -> 3 -> 2 -> 1` ownership and exits zero. Normal and target-death
self-reference/last-owned-target probes and C/strict three-remap differential
all pass in the 24-node focused suite.

## Routed C-extension lifecycle boundary

The set-remove probe verifies C forwarding retirement releases a stored managed
C-extension key exactly once. Strict source retirement executes the same
saved-slot `py_decref`, but the dynamic extension object is unmanaged/unknown,
so its dealloc count remains zero. A direct strict bit62 dealloc control passes.
This is the existing `GC-P0-CEXT-STRICT-DECREF-TAG-PARITY` lifecycle blocker,
not a source-payload omission.

## Gates

- original downstream-tail regression: `1 passed in 2.73s`.
- source/closure, normal/target-death retirement and differential:
  `24 passed in 8.23s`.
- set-remove routing probe: `2 passed in 1.24s`.
- task-board validation and `git diff --check`: pass.

Durable logs:

- `build/granule-s2-gc4-payload-retirement-green.log`
- `build/gc4-relocation-mutator-quiescence.log`
- `build/gc4-set-remove-split-commit.log`

## Frozen identities

```text
4daf55aab6d59d7938e9dcab0c016a35129d52e7b8e70856963ffff9de506248  pcc/py_runtime/src/py_gc_backend.c
8f9b494389ae2c33b3cf561c1fa7d5cb346870f7315fa8cd456a3b023d8ec179  pcc/py_runtime/py/freestanding_gc_relocation_payload.py
62c11a3fc51802eec2fdef28f34db42164087359e16130c8e7774d17fbb2c82b  pcc/py_runtime/py/freestanding_gc_forwarding_retirement.py
f5bc414ad9d5da161c24decc2c2d29789680e8bdc7bec3f6b4e75166ea0fc6bf  tests/python/test_gc_granule_map.py
7c6a9798318a8787d8e4186594ba927e5eedc4342516f4ef855586a5e1cd1b33  tests/python/test_freestanding_gc_relocation_payload.py
c03c1906829879c39aad5293a6f61eb6c3cd366a507023169750aad53c7e1dfb  tests/python/test_freestanding_gc_forwarding_retirement.py
d8374f686b428e9bfdaef12476cc9ef3a7ca9f7a1013e3e0f2380c1208836a28  build/granule-s2-gc4-payload-retirement-green.log
72d9f144b0434cf6417f257f4de00a9dac078826a830604383578ed1f2d10c6c  build/gc4-relocation-mutator-quiescence.log
77e4c36f44b5a4046b75c63797906cd9364059d5a056c2a231eebab704356171  build/gc4-set-remove-split-commit.log
```

## Status

`DONE_STRONG` for built-in forwarded-source payload retirement. Strict dynamic
C-extension object lifecycle remains separately unfinished.
