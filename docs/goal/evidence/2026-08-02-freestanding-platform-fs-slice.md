# LIBC-P2-THIN-WRAPPERS — freestanding filesystem slice

Date: 2026-08-02

## Source identity

This evidence describes the current dirty shared worktree. It is not a clean
commit or release claim.

## Changed behavior

- Added `freestanding_platform_fs.py` to the default pcc-Python production
  archive.
- The shared `pcc_platform_*` ABI now owns `access`, `getcwd`, stat kind/mtime,
  `realpath`, and `mkdtemp` behavior.
- Darwin lowers only to the explicitly named libSystem ABI boundary
  `access/getcwd/getpid/mkdir/readlink/stat`.
- Linux x86_64 lowers all six operations to raw syscalls and declares none of
  those libc symbols.
- `realpath` semantics are authored in freestanding pcc-Python: component
  normalization plus relative/absolute symlink expansion with a 40-link
  fail-closed bound. It does not call libc `realpath`.
- `mkdtemp` semantics are authored in freestanding pcc-Python: validates the
  six-X template, uses an atomic unique sequence, and creates mode-0700
  directories through the platform `mkdir` primitive. It does not call libc
  `mkdtemp`.
- `py_os_substrate.py` and `py_process_substrate.py` consume the shared ABI;
  compiled `os` path queries and `tempfile.TemporaryDirectory` exercise the
  production archive under self/no-libpython.

## Focused gates

```text
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_platform_fs.py \
  tests/python/test_freestanding_platform_io.py
12 passed in 9.33s

gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_unsafe_syscall6.py \
  tests/python/test_unsafe_atomics.py \
  tests/python/test_unsafe_pages.py
23 passed in 3.45s

gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_libc_import_baseline.py::test_pcc1_libc_imports_stay_within_baseline
1 passed in 0.32s
```

The filesystem suite includes LLVM and self objects, real C ABI harnesses,
relative-symlink `realpath` differential behavior, two unique mode-0700
temporary directories, Linux raw-syscall IR/self-assembly inspection, archive
selection, and a default-runtime self/no-libpython program covering
`getcwd/access/isfile/isdir/getmtime/realpath/TemporaryDirectory`.

## Ratchet evidence

A fresh Darwin-arm64 stage1 `nm -u` remained at 62 symbols. The exact delta
against the prior baseline was:

```text
added lower platform ABI: mkdir, readlink
removed semantic libc ABI: mkdtemp, realpath
```

`tests/libc_import_baseline.json` records this argued equal-count Darwin ABI
lowering. The threads baseline mirrors the same trade; its six-symbol pthread
delta is unchanged. Linux does not add the replacement symbols because its
object path emits raw syscalls.

## Supported claim

The access/stat/getcwd/realpath/mkdtemp portion of
`LIBC-P2-THIN-WRAPPERS` is implemented and focused-green in host-compiled
pcc-Python, LLVM and self object modes, with a default self/no-libpython runtime
consumer on Darwin and raw-syscall source/object evidence for Linux x86_64.

## Not proven

This does not close `LIBC-P2-THIN-WRAPPERS`: environment/uname/sysconf, time,
process lifecycle, sockets and resolver work remain. It is not Linux container
execution, the full five-GC matrix, or the pcc1->pcc2->pcc3 fixed point.
