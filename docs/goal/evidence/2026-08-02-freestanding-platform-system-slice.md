# LIBC-P2-THIN-WRAPPERS — uname and CPU-count slice

Date: 2026-08-02

## Source identity

This evidence describes the current dirty shared worktree. It is not a clean
commit or release claim.

## Changed behavior

- Added strict freestanding `freestanding_platform_system.py` with the shared
  `pcc_platform_uname`, `pcc_platform_uname_field`, and
  `pcc_platform_cpu_count` ABI.
- Added `py_os_system.py` as the sole production owner of `py_os_uname` and
  `py_os_cpu_count`. Python tuple/string/int construction no longer lives in
  the retained C helper.
- Darwin obtains utsname data through named libSystem `uname` and queries only
  `hw.logicalcpu` through named `sysctlbyname`.
- Linux x86_64 lowers uname and sched-getaffinity to raw syscalls. CPU affinity
  mask popcount is authored in freestanding pcc-Python; the object declares no
  uname/sysconf/sysctl symbol.
- The host-C oracle still compiles the old two functions. The production
  pcc-Python archive defines `PCC_USE_FREESTANDING_PLATFORM_SYSTEM`, excludes
  those C definitions, and has exactly one Python-port owner for both ABI
  symbols.

## Focused gates

```text
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_platform_system.py \
  tests/python/test_native_os_uname.py
8 passed in 2.87s

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_os_uname.py -vv
1 passed in 44.34s (cold current-source runtime archive)

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_libc_import_baseline.py::test_pcc1_libc_imports_stay_within_baseline \
  tests/python/test_libc_import_baseline.py::test_linux_baseline_is_platform_labeled_and_distinct_from_darwin
2 passed in 0.32s
```

The system suite exercises real C ABI harnesses with LLVM and self objects,
all five uname fields and the CPU count against the host, the exact Darwin
undefined-symbol boundary, Linux raw-syscall IR/self assembly, archive plan and
symbol ownership, and a default self/no-libpython runtime consumer.

## Ratchet evidence

A fresh Darwin-arm64 stage1 stayed at 60 undefined symbols with the exact
argued ABI lowering:

```text
removed: sysconf
added:   sysctlbyname
```

The added call is restricted to the literal `hw.logicalcpu` query. Both Darwin
baseline files record the equal-count trade and preserve the same six-symbol
pthread-only delta. Linux uses raw syscalls and adds neither symbol.

## Supported claim

`os.uname()` and `os.cpu_count()` Python semantics are authored in pcc-Python
and focused-green in the default self/no-libpython production archive. Linux
x86_64 uses no libc for this slice. Darwin uses the explicitly named libSystem
ABI boundary stated above.

## Not proven

This is not a claim that Darwin is system-library-free. Time, process
lifecycle, child environment propagation, sockets and resolver families remain
open. This is not Linux container execution, the full five-GC matrix, or the
pcc1->pcc2->pcc3 fixed point.
