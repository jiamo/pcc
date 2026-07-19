# 2026-07-08 TileLang Scheduled Loop Executable Body Fail-Closed

Track rows:

- `GPU-P1-BROADER-TILELANG-TIRX-PASSES`

Claim:

This slice hardens the TileLang/TIRx importer boundary for `T.Parallel` and
`T.vectorized`. These loops remain metadata-only for the current Metal path.
If a scheduled loop body contains ordinary executable Python assignment instead
of supported TileLang staging ops, the importer now fails closed with an
explicit diagnostic instead of accepting the body or letting a generic
unsupported-assignment error hide the boundary.

Covered behavior:

- `T.vectorized(...)` body with `C_local[0, 0] = C_local[0, 0] + 1.0` is
  rejected at import time.
- `T.Parallel(...)` body with `C_local[0, 0] = C_local[0, 0] + 1.0` is rejected
  at import time.
- Diagnostic text includes
  `executable T.Parallel/T.vectorized loop bodies are not supported`.

Gates run:

```bash
gtimeout 60s env -u LC_ALL uv run python -m py_compile pcc/kernel_ir/tilelang_import.py tests/kernel/test_tilelang_import_broader.py
```

Result: passed.

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py::test_vectorized_executable_body_fails_closed_in_importer tests/kernel/test_tilelang_import_broader.py::test_parallel_executable_body_fails_closed_in_importer
```

Result: `2 passed in 0.28s`.

```bash
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_tilelang_import_broader.py
```

Result: `46 passed in 0.24s`.

Claim scope:

This is not runtime GPU execution proof and does not broaden executable
`T.Parallel` or `T.vectorized` semantics. It proves the current importer is
honest about that unsupported boundary.
