# STDLIB-P2-COMPRESS-LARGE-FILE-STREAMING focused evidence — 2026-08-14

Mode: host-source bounded-reader contract; no 64 MiB current-pcc1 execution or
RSS claim.

Command/result:

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_py_stdlib_large_compression_streaming.py::test_shared_file_reader_requests_and_retains_only_bounded_fragments
# 1 passed in 0.08s
```

The shared reader requests and retains only bounded fragments.  Open: the
64 MiB+257 strict self/no-libpython codec differential and measured RSS.
