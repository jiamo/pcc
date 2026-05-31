# Investigation: bytearray repr + mutable methods unsupported (no-libpython cluster)

## Status
active (scoped cluster; not started — bytearray repr is bounded, methods are several)

## Problem Description
`bytearray` is largely unsupported under strict no-libpython:
- `print(bytearray(b"Hello"))` -> `<object tag=18>` (no bytearray repr formatter;
  CPython: `bytearray(b'Hello')`).
- `ba.append(33)` -> `AttributeError: append` (and likely extend/insert/pop/
  remove/etc. — no bytearray method dispatch).
Found 2026-05-30 by real26. bytearray construction + indexing + `bytes(ba)`
already work (real26 bisect: `bytearray(b"hi")`, `bytearray(...).hex()` OK).

## Repro
```bash
printf 'def main():\n    ba=bytearray(b"hi")\n    print(ba)\n    ba.append(33)\n    print(ba)\nmain()\n' > /tmp/ba.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/ba.py -o /tmp/ba_bin
/tmp/ba_bin            # <object tag=18>  then AttributeError: append
python3 /tmp/ba.py     # bytearray(b'hi')  bytearray(b'hi!')
```

## Root cause (CONFIRMED by read)
- **repr** (tag 18 = PY_TYPE_BYTEARRAY): py_print_fmt.c `py_format` / `py_format_repr`
  have no PY_TYPE_BYTEARRAY case -> falls to the `<object tag=N>` default. Need a
  bytearray formatter `bytearray(b'...')` (reuse the bytes \xNN-escape logic from
  #49, wrapped in `bytearray(` + `)`). Three paths like #49: py_format_bytes-style
  in py_print_fmt.c (+ port py_print_fmt.py) for print, and a _format_bytearray_str
  in the port py_obj_stubs.py `_format_builtin_str` for repr/str.
- **methods**: no bytearray method dispatch in the frontend (no `_DYN_BYTEARRAY_METHOD`
  / `_maybe_emit_bytearray_method`). append/extend/insert/pop/remove/etc. Some
  runtime helpers may exist (check py_runtime.h for py_bytearray_*); add a frontend
  dispatch (like list_method_lowering) and any missing C helpers (C-only-helper
  pattern if the port would awkwardly reimplement).

## Proposals
- No.1 bytearray repr formatter   [CONFIRMED #51]
- No.2 bytearray mutable methods (append/extend/insert/pop/remove/...)   [pending — cluster]

## pending
Deferred from the 2026-05-30 session (15 fixes #36-#50 already landed). bytearray
is moderately niche (binary buffers). No.1 (repr) is a clean bounded fix mirroring
#49 (bytes-repr escaping); No.2 (methods) is a cluster. Do No.1 first; No.2 in a
focused session. Check existing py_bytearray_* runtime helpers before adding.

## Update 2026-05-30 — No.1 (repr) DONE (#51)
bytearray repr landed + bootstrap-passed. py_format_bytearray = 'bytearray(' +
py_format_bytes + ')' (bytes/bytearray share layout: byte_len@16, data@24) in
py_print_fmt.c + port py_print_fmt.py (print path); _format_bytearray_str
(py_str_concat wrap of _format_bytes_str) in py_obj_stubs.py (repr/str path).
test_native_bytearray_repr.py 1 passed. REMAINING (No.2 + a new sub-item):
- `bytearray()` (no-arg / empty constructor) -> libpython fallback (bytearray(b'...')
  works; the 0-arg form doesn't). Bisected from real26.
- bytearray mutable methods (append/extend/insert/pop/remove/...) -> AttributeError.
Both still pending — a focused bytearray-construction+methods session.
