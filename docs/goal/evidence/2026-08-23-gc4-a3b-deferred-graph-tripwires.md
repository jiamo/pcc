# GC4 A3b deferred graph tripwires

Date: 2026-08-23

Task: `GC-P0-GC4-RELOCATION-MUTATOR-QUIESCENCE`

Status: finite A3b fatal-log holder sub-boundary confirmed; parent remains
`IN_PROGRESS`.

## Claim boundary

The C graph-lock implementation now has one thread-local, first-failure
tripwire slot. A locked invariant can record its static message/file/line
without entering runtime logging. The innermost recursive unlock does nothing;
the outermost unlock releases the physical graph lock, finishes any pending
CMS flush, clears the deferred slot, and only then calls the original fatal
`pcc_runtime_tripwire_fail` sink.

Three confirmed locked sites use this mechanism:

- GC3 young promotion rejects a simultaneous YOUNG+OLD invariant before
  unlinking or changing generation state;
- registered scheduler-root visitation skips a corrupt null slot; and
- GC3 remembered-owner drain detaches but skips a corrupt null owner.

Normal non-tripwire builds still do not evaluate the conditions. Strict
pcc-Python has no corresponding tripwire calls on these paths, so no mirror
change was required.

This is not a repository-wide tripwire-clean claim. Other C/strict fatal-log
sites remain classified separately.

## RED

The source contract was genuinely RED because outer unlock had no deferred
sink and all three helpers still contained direct `PCC_RT_TRIPWIRE` calls:

```text
tests/python/test_runtime_tripwires.py::
test_graph_locked_tripwires_defer_until_outer_unlock_source

missing pcc_gc_finish_deferred_tripwire()
1 failed in 0.09s
```

## Gates

C syntax passed in threads-off mode and armed threaded mode. Final packet:

```text
gtimeout 240s sh -c 'env -u LC_ALL uv run pytest -vv -x -n0 --tb=short \
  tests/python/test_runtime_tripwires.py::test_graph_locked_tripwires_defer_until_outer_unlock_source \
  tests/python/test_runtime_tripwires.py::test_tripwire_source_covers_named_runtime_boundaries \
  tests/python/test_runtime_tripwires.py::test_armed_tripwires_accept_valid_roots_zpage_forwarding_and_native_handle \
  tests/python/test_runtime_tripwires.py::test_armed_deferred_graph_tripwire_aborts_after_outer_unlock \
  2>&1 | tee build/gc-deferred-tripwire-final.log'

4 passed in 7.73s
```

The fault probe creates a non-minor-arena GC3 list with inconsistent
YOUNG+OLD flags. The locked path records and returns; outer unlock releases the
lock, and the unchanged armed runtime sink logs `TRIPWIRE` plus the generation
message and aborts. The valid armed integration continues to cover scheduler
and continuation roots, ZPage forwarding and native-handle release.
`git diff --check` exited zero.

## Frozen identities

```text
219047e5a7254720227296d2c6f7e77715bbb7cfb5e6940a15e07fb737f51b7d  pcc/py_runtime/src/py_gc_backend.c
9f9193352c77b79fcfbe185992730da1a7261c2018dec7e16e33756dc0aec9ad  tests/python/test_runtime_tripwires.py
af2a9649aaf3a44047443d962fcc1827019b9272eddbae4d571042827bae7511  build/gc-deferred-tripwire-final.log
```

## Next boundary

Do not connect A3c. Inventory and defer or prove unreachable every remaining
locked fatal-log site; separately design owner-referent promotion as a
resumable remembered-slot worklist.
