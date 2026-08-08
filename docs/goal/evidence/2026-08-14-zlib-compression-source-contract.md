# Zlib/compression source-contract evidence — 2026-08-14

Mode: host-side bounded API and fail-closed source contract only.

Command:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 -m 'not integration' tests/python/test_py_stdlib_compression_closure.py
```

Result: 2 passed, one strict self/no-libpython integration node deselected.

This proves covered invalid parameters stop before native dispatch and the
unowned incremental compressor/file-writer APIs fail closed. It does not prove
the native compression ABI, cross-decoder artifact bytes, current pcc1, or the
pcc1 -> pcc2 -> pcc3 fixed point.
