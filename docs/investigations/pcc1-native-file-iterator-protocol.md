# Investigation: native file objects do not implement the iterator protocol

## Status

resolved

## Problem Description

The first native Harness process now persists its complete Session JSONL log. The second process fails while loading it at `for line in stream` with `TypeError`.

The frontend correctly lowers a dynamic for-loop through `py_obj_iter` and `py_obj_next`, and the native file runtime already implements `readline()`. Both pcc-Python and C iterator runtimes omit `PY_TYPE_FILE`, so `iter(file)` falls through to user-dunder dispatch and raises `TypeError` even though Python file objects are self-iterators.

## Repro

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_native_file_readline_seek_tell.py \
  -k file_iteration
```

The product-level reproduction is the second process in:

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_harness_session_composition.py::test_current_pcc1_persist_resume_and_fork \
  -m integration
```

## Test [CONFIRMED]

Compile and run text and binary file iteration without libpython, compare output with Python 3, and assert `iter(file) is file`. Exhaustion must end the loop by raising and consuming `StopIteration`; iteration of a closed file must retain the existing `ValueError` from `readline()`.

## Proposals

- No.1 Route file iteration through native readline [implemented]

## No.1 Route file iteration through native readline

### Code Change

Teach `py_obj_iter` that `PY_TYPE_FILE` is its own iterator. Teach `py_obj_next` to call `py_file_readline(file, -1)`, return non-empty text or bytes lines, and translate the empty EOF value to `StopIteration`. Keep the pcc-Python source runtime and its C mirror behaviorally aligned.

### Validation

The text/binary parity regression passed once with the default C runtime owner and once with `PCC_RUNTIME_HIGH=py`; each run compared the native binary with Python 3. Current pcc1 then self-bootstrapped from the revised pcc-Python runtime. The rebuilt Harness passed both self-checks and the 2-test Cordis plus durable Session integration, including three separate processes for persist, resume, and fork.
