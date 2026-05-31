# Investigation: User iterator __next__ advances state twice

## Status
resolved

## Problem Description
`tests/test_python_class_features_parity.py::test_class_user_iter` is still
xfailed.  The minimized class implements `__iter__` returning `self` and
`__next__` returning `self.i` before incrementing it.  The compiled program
should print `0`, `1`, `2`, but it prints odd values, showing that the user
iterator path runs with incorrect state/update semantics.

## Repro
Run the xfailed test as a real assertion:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest \
  'tests/test_python_class_features_parity.py::test_class_user_iter' \
  -q -n0 --runxfail -vv
```

Expected current failure:

```text
AssertionError: assert ['1', '3', '5'] == ['0', '1', '2']
```

## Test [CONFIRMED]
The repro above was run on 2026-05-08 and failed with output `['1', '3', '5']`
instead of `['0', '1', '2']`.

## Proposals
- No.1 Preserve user __next__ return value before state update     [CONFIRMED]

## No.1 Preserve user __next__ return value before state update
### Code Change
Use boxed-int storage for native iterator loop targets when ordinary Python
modules are compiling ints as PyObject* values.  The previous
`_emit_for_native_iterator` path used `_map_type(IntType)` and allocated an
`i64` target, then stored the boxed `__next__` return pointer into it.

### CONFIRMED
Generated IR showed only one `Range3.__next__` call per loop iteration, but the
loop target slot was:

```llvm
%v.addr = alloca i64
...
store ptr %Range3.__next__.ret, ptr %v.addr
```

The output `1`, `3`, `5` was the tagged-int pointer representation of `0`,
`1`, `2` interpreted as raw `i64`.  After changing the loop target storage to
`_CSTR` for boxed-int mode, the focused test passes and the class parity file
is all green.

Observed results:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest \
  'tests/test_python_class_features_parity.py::test_class_user_iter' \
  -q -n0 -vv
# 1 passed in 0.94s

env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest \
  tests/test_python_class_features_parity.py -q -n0 -rxX
# 10 passed in 5.61s
```

## Report (only when the investigation is closing)
No.1 landed.  The issue was not double invocation of `__next__`; it was an
IR storage mismatch at the loop target.  This closes the class parity xfail for
user-defined `__iter__` / `__next__`.  Broader B4 protocol polish such as
additional `__hash__` / `__str__` edge coverage remains tracked in the roadmap.
