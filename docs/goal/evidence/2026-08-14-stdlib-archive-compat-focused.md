# STDLIB-P2-ARCHIVE-COMPAT-SUITE focused evidence — 2026-08-14

Mode: host CPython corpus/model checks, serial fail-fast; the strict
self/no-libpython integration node was deselected.

Command/result:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 -m 'not integration' tests/python/test_py_stdlib_archive_compat.py tests/python/test_py_stdlib_archive_closure.py
# 25 passed, 1 deselected in 0.19s
```

Supported archive shapes and named fail-closed ZIP64/ZipCrypto/multi-disk,
ZIP-LZMA, sparse/link/special-file boundaries are green at the corpus layer.
Open: strict current-pcc1 no-libpython archive differential.
