# GC4 list clear split-decref transaction — 2026-08-24

## Claim

For backends 1-4, C and strict pcc-Python list clear now:

1. keep the list owner in an updateable root;
2. allocate and initialize every split store/decref packet before graph tenure;
3. reload and revalidate the current length after locking;
4. detach every owned slot and publish length zero while graph/no-park is held;
5. unlock; and only then
6. finish decref packets, including finalizer, weakref and deallocation tails.

Backend 0 retains its direct length-zero/slot-clear/decref loop and incurs no
plan allocation or graph-lock overhead.

## Same-list finalizer re-entry proof

A native pcc class instance is left with its terminal owned reference in the
list.  Its real `__del__` callback observes length zero, explicitly relocates
the same GC4 list, appends tagged integer `777`, and returns.  C and strict
runtimes preserve that append and return the scheduler-root count to the one
deliberate persistent root.

An initial dynamic C-extension deallocator version passed C but exposed a
distinct strict C-API ownership gap: its tag is registry-proven while the
object is unmanaged/unknown and append does not retain it.  A tag-only strict
decref exemption was tested and denied, then removed.  That lifecycle is
tracked in `strict-cext-dynamic-tag-decref-parity.md`; the list transaction uses
the native finalizer surface supported by both runtime owners.

This evidence closes clear only.  Delete-slice bound conversion, delete
compaction, replacement-bearing set-slice, sort callbacks, stage2 performance,
bootstrap fixed point and broad five-GC parity remain open.

## Gates

- C syntax with threads off/on: pass (one pre-existing unrelated pointer
  warning).
- strict `py_compile` and self-backend/no-libpython `py_list.py` closure: pass.
- clear split-plan/source and neighboring contracts: `14 passed in 1.59s`.
- C/strict native finalizer relocation/re-entry: `2 passed in 144.49s`.
- ordinary list semantics including clear/reuse and all list neighbors:
  `14 passed in 8.28s`; the independently exposed typed-mutator None result is
  closed in `2026-08-24-typed-list-mutator-none-results.md`.
- task-card relocation payload/forwarding retirement gate:
  `24 passed in 152.43s`.
- `git diff --check`: pass.

Durable logs:

- `build/gc4-list-clear-finalizer-final.log`
- `build/gc4-relocation-mutator-quiescence.log`

## Frozen identities

```text
dbb8bcc6333a3051441ef07c19ff02b5c62467af8ee446b98f1c34cb12bebf14  pcc/py_runtime/src/py_list.c
0679032c783e0159059369bcae154c6cb5e9dfa5797b96ad9d7f988a4e6f181f  pcc/py_runtime/py/py_list.py
4bd21f2f6161e0ceb7f3f9c755298466834fc09cbaeb7b0828fb05ddc907eb9b  tests/python/test_gc_codegen_write_barrier.py
4623cde173106051eb94d63cca3c68ef00b74b5b7fe65302332f381a68cde1b5  tests/python/test_gc_threading_substrate.py
5053558063744856d10aaf4cec3ac2d1d42483b3464177485ba3d644cb157761  tests/python/test_python_list_methods_parity.py
f16517c6c5557339d3c22c1db1524bc650933e8a61d4f9c117a35e48b2151884  build/gc4-list-clear-finalizer-final.log
5efb77c2c3a6716c5ea9aa8d010d04fff21114553cca7a5f7294da13ea87536c  build/gc4-relocation-mutator-quiescence.log
```

## Status

`DONE_STRONG` for Proposal No.11b.1a list clear.  The GC4 parent remains
`IN_PROGRESS` at delete-slice conversion/compaction.
