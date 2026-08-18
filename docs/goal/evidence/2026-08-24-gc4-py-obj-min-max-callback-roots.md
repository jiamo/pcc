# GC4 py_obj_min_max callback roots — 2026-08-24

## Claim

C oracle and strict pcc-Python `py_obj_min_max` now retain iterator, current
best and candidate element in updateable scheduler-root slots on Backend 3/4.
They reload iterator/best after `py_obj_next`, reload best/element after
`py_obj_lt`, and swap rooted slot/handle pairs when a candidate becomes the new
best.  Empty, error, discard and success paths detach every handle before the
corresponding decref or return.

This closes the callback-root mechanics of `py_obj_min_max` under the runtime's
current comparison semantics.  It does not claim callable-iterator internals,
`enumerate`, tuple scans or dict/set hash/equality loops.

## Dynamic proof

C and strict runtime probes use heap strings so iterator, best and element all
take the moving-root path.  Both min/max results match `a`/`c`, and the public
scheduler-root count returns to zero after each pair of calls.

A strict no-libpython program runs Backend 4 with a movable custom iterator
whose `__next__` executes `gc.collect()` on every element.  `min` and `max`
continue after callback re-entry and return `1` and `3`.  The existing custom
class, primitive, iterator and string semantic neighbors remain 5/5 green.

## Denied comparison-callback probes

- A custom iterator returning ordinary user instances does not compile through
  the self backend: the frontend joins the generic min result as i64 where the
  call expects `void *`.
- Returning strings compiles but produces `0/0`, confirming the same generic
  custom-iterator path is currently an i64 accumulator.
- A direct runtime user class with native `__lt__` produces pointer-order
  `3/2` and reports zero callback hits.  The current `py_obj_lt` runtime path
  does not dispatch ordinary user-instance `__lt__`; the callback-capable
  C-extension comparison path operates on nonmoving C-extension objects.

These are frontend/runtime comparison capability boundaries, not evidence that
the new roots failed.  The temporary failing comparison probe was removed; the
verdict is retained here and in the investigation so it is not re-derived.

## Gates

- static C/strict root/reload/swap contract: `1 passed in 0.39s`.
- C/strict heap-object results and balanced roots: `2 passed in 0.93s`.
- Backend-4 custom iterator `__next__ -> gc.collect()`: `1 passed in 3.64s`.
- existing min/max semantic neighbors: `5 passed in 14.15s`.
- strict no-libpython source closure: pass.
- strict archive owner: `1 passed in 144.05s`.
- runtime ABI chunk plus GC abstraction: `17 passed in 12.49s`.
- task relocation payload/forwarding retirement gate: `24 passed in 7.34s`.
- C syntax and `git diff --check`: pass.

Durable logs:

- `build/gc4-py-obj-min-max-source-contract.log`
- `build/gc4-py-obj-min-max-root-balance.log`
- `build/gc4-py-obj-min-max-iterator-reentry.log`
- `build/gc4-py-obj-min-max-semantics.log`
- `build/gc4-py-obj-min-max-archive-owner.log`
- `build/gc4-py-obj-min-max-abi-gc.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
a2e4ce1d91633a8cfd470e902182a1725bdbc62ea81e8dd5742c399d45eb8b70  pcc/py_runtime/src/py_obj_min_max.c
2d74376e98af332546b235a30f6663844becee0bb97512b975a1f19cbfa3a8c3  pcc/py_runtime/py/py_obj_ops_compare.py
068d17d3d56bd23cbbf725884fa39e63ec70ec4b8841ad6ee87665a7b6adb69d  tests/python/test_gc_threading_substrate.py
afef9e54b40079d4b71d520573c3e4caf02f9e6b1ac10d644d7969f0ff93f9e7  tests/python/test_native_min_max_custom_lt.py
036bb9390fb408c39eb19a61d865bc4044aa29eeea8139e62a988a5a83930a4d  build/gc4-py-obj-min-max-source-contract.log
fc3e6b5a778f0b65df52498c72918711956bc618e3ad698ec5fc135811b6a1a1  build/gc4-py-obj-min-max-root-balance.log
742298cf4bd9b3f71e7225a6f48f78d15c96a631c9d73a7b521278d2facdbcc3  build/gc4-py-obj-min-max-iterator-reentry.log
35282e43bf980adb55284c6896bbafe76795e2e049b9bc11b2eab75aa4abaf4d  build/gc4-py-obj-min-max-semantics.log
0580a279cff4d51ec7bec817e9deee6a2588a3658dfe64d63c183a4fecd29d19  build/gc4-py-obj-min-max-archive-owner.log
38760d8a99d565ecf28087e7030cd0fe3612679422fec963885310448dd8e892  build/gc4-py-obj-min-max-abi-gc.log
1f9a9e887e12f230c0d1a5b54d660b7f3952b04d36aaabbbc28edb32bfcedbf9  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.15 under the current runtime callback surface.
The explicitly denied comparison capabilities and the GC4 parent remain open
under their own boundaries.
