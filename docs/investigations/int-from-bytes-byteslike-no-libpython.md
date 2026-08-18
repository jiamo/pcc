# Investigation: native `int.from_bytes` rejects bytearray and memoryview

## Status

active

## Problem Description

CPython accepts any bytes-like object in `int.from_bytes`, including `bytes`,
`bytearray` and contiguous `memoryview`. Both pcc runtime implementations only
accept the exact `PY_TYPE_BYTES` tag and raise `TypeError` for bytearray and
memoryview.

The gap became a self-host blocker after the pcc1 AArch64 assembler correctly
reached compact-unwind normalization: it slices a mutable `bytearray` payload
and calls `int.from_bytes`, causing the Stage1 v25 strong canary to fail with
`from_bytes expects a bytes object`.

## Repro

`tests/python/test_int_bytes_builtin.py::test_int_bytes_forms_match_cpython`
now includes:

```python
mutable = bytearray(b"\x01\x00")
print(int.from_bytes(mutable, "big"))
print(int.from_bytes(memoryview(mutable), "little"))
```

CPython prints `256` then `1`. Before the fix, the pcc executable prints all
earlier exact-bytes results correctly, then raises at the bytearray call.

## Test [CONFIRMED]

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_int_bytes_builtin.py::test_int_bytes_forms_match_cpython

1 failed in 2.62s
```

## Proposals

- No.1 Share a bytes-like base/payload contract in both runtime mirrors [pending]

## No.1 Share a bytes-like base/payload contract in both runtime mirrors

### Code Change

Teach C `py_int_from_bytes` and its freestanding pcc-Python mirror to accept
bytes and bytearray's common `{byte_len, data[]}` layout. For memoryview, follow
its `base` slot through `pcc_gc_load_ptr` until reaching bytes/bytearray, then
read the same payload. Reject null, tagged or non-bytes-like bases with the
existing TypeError. Signedness/byteorder/bignum behavior is unchanged.
