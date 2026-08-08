# LIBC-P2-THIN-WRAPPERS — freestanding environment slice

Date: 2026-08-02

## Source identity

This evidence describes the current dirty shared worktree. It is not a clean
commit or release claim.

## Changed behavior

- Added `freestanding_platform_env.py` to the default pcc-Python production
  archive. It copies the initial `envp` into one owned, growable table and
  implements lookup, overwrite-aware set, and unset without libc environment
  mutation calls.
- Darwin reads the initial pointer through the explicitly named `environ`
  libSystem boundary. Linux x86_64 owns `pcc_initial_envp`; the future static
  `_start` slice supplies that pointer directly and the emitted object declares
  none of `environ/getenv/setenv/unsetenv`.
- All pcc-Python runtime consumers now call the shared `pcc_platform_*`
  environment ABI.
- Transitional C helpers retained in `libpy_runtime_pcc_py.a` are compiled with
  `PCC_USE_FREESTANDING_PLATFORM_ENV` and consume the same table. The host-C
  oracle archive leaves that define unset and still compiles against host libc.
- Added native `del os.environ[key]` lowering. It composes the raising mapping
  getitem with unsetenv, preserving `KeyError` for a missing key and evaluating
  the key only once.

## Focused gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_platform_env.py \
  tests/python/test_native_os_environ.py
16 passed in 4.39s

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_os_environ_mapping.py::test_os_environ_mapping_matches_cpython
1 passed in 43.89s

gtimeout 600s env -u LC_ALL uv run pytest -q -n0 -s \
  tests/python/test_libc_import_baseline.py::test_pcc1_libc_imports_stay_within_baseline
1 passed in 32.41s

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_libc_import_baseline.py::test_pcc1_libc_imports_stay_within_baseline \
  tests/python/test_libc_import_baseline.py::test_linux_baseline_is_platform_labeled_and_distinct_from_darwin
2 passed in 0.30s

gtimeout 90s make build/py_os_native.o build/pcc_threads.o \
  build/pcc_runtime_log.o build/py_extension_loader.o
completed successfully with PCC_USE_FREESTANDING_PLATFORM_ENV unset
```

The environment suite includes LLVM and self ABI harnesses, owned-copy and
mutation behavior, invalid-name handling, Linux IR/self-assembly inspection,
archive selection and retained-helper symbol inspection, plus a default
self/no-libpython program covering inherited lookup, set/get, membership and
delete. The existing mapping suite remains differential-green against CPython.

## Ratchet evidence

A fresh Darwin-arm64 stage1 reduced the production undefined-symbol set from
62 to 60. The exact delta was:

```text
removed: getenv, setenv
added:   none
remaining environment boundary: environ
```

`unsetenv` was already absent from the prior baseline. Both Darwin baseline
files are tightened by two symbols and retain the same six-symbol pthread-only
delta. Linux object evidence declares none of the four environment symbols.

## Supported claim

The process-local environment table and Python `os.getenv`/`os.environ`
read-write-delete semantics are owned by freestanding pcc-Python in the
default self/no-libpython runtime. Retained production C helpers no longer
consult a second libc environment table.

## Not proven

Child-process propagation of mutations is not claimed here. The process/spawn
slice must pass an exported snapshot of the owned table to new children. This
is not Linux container execution, the full five-GC matrix, or the
pcc1->pcc2->pcc3 fixed point.
