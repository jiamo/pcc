# Evidence: V-P1-VAL VP-S1 Range-Lane Re-entry

task: `V-P1-VAL`

slice: `VP-S1`

status: `DONE_STRONG` for this finite slice; parent task remains `IN_PROGRESS`.

source identity: shared local worktree on 2026-07-16; no clean-commit or
release claim.

## Proven behavior

- The bounded `range` induction counter remains a raw `i64` hot lane.
- Before the loop target becomes Python-visible, lowering calls
  `py_int_from_i64`; the raw word does not leak through an object boundary.
- The reboxed value crosses an `Any -> Any` function and a typed `int -> int`
  call through the pointer/tagged Python-int ABI, never an `i64` call ABI.
- The strict self backend/no-libpython executable prints the CPython-equivalent
  result, while the existing overflow-to-bignum regressions remain green.

No shared lowering change was needed: the implementation already satisfied the
finite contract, but the required escape/call re-entry evidence was missing.

## Gates

- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/python/test_native_typed_int_overflow.py tests/python/test_py_typed_int_unboxed.py`
  - pre-change baseline: `28 passed in 7.85s`
- `gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/python/test_py_typed_int_unboxed.py::test_for_range_raw_lane_reboxes_before_dyn_and_typed_calls tests/python/test_native_typed_int_overflow.py`
  - result: `6 passed in 2.69s`
- `gtimeout 60s env -u LC_ALL uv run black tests/python/test_py_typed_int_unboxed.py`
  - result: formatted successfully

## Claim boundary

This proves VP-S1's current bounded-range raw-lane re-entry shape on the
Darwin-arm64 self backend and the generic overflow regressions already covered
by the focused file. It does not prove VP-S2 aggregate/GC slot completion,
VP-S3 all-boundary identity escape, x86_64 self-backend overflow intrinsics, or
the parent task's closing five-GC bootstrap matrix.

## Open boundary

None for VP-S1.
