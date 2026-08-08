# LIBC-P2-THIN-WRAPPERS — clock and sleep slice

Date: 2026-08-02

## Source identity

This evidence describes the current dirty shared worktree. It is not a clean
commit or release claim.

## Changed behavior

- Added strict freestanding `freestanding_platform_time.py`, which owns wall
  microseconds, monotonic microseconds and nanosecond sleep behind one shared
  ABI.
- Darwin lowers to named libSystem `clock_gettime` and `nanosleep`. Linux
  x86_64 lowers both operations to raw syscalls.
- Python `time.time`, `time.monotonic` and `time.perf_counter`, GC pause timing,
  thread deadlines, runtime logging and subprocess timeout polling now consume
  that ABI instead of independently consulting libc clocks.
- The retained C wrappers preserve host-C oracle behavior when
  `PCC_USE_FREESTANDING_PLATFORM_TIME` is unset and route to pcc-Python in the
  production archive when it is set.
- Added explicit unchecked unsigned division/remainder intrinsics for the
  low-level, proven-nonzero constant divisors. The strict freestanding validator
  remains fail-closed; ordinary Python division was rejected because it emitted
  managed `ZeroDivisionError` machinery.

## Focused gates

```text
gtimeout 150s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_platform_time.py -vv
6 passed in 38.26s (cold current-source runtime archive)

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_time_module.py \
  tests/python/test_subprocess_timeout_runtime.py::test_native_subprocess_timeout_kills_child_process_group
2 passed in 39.42s

gtimeout 90s make build/pcc_runtime_log.o build/pcc_threads.o \
  build/py_process_timeout.o
completed successfully with PCC_USE_FREESTANDING_PLATFORM_TIME unset

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_libc_import_baseline.py::test_pcc1_libc_imports_stay_within_baseline \
  tests/python/test_libc_import_baseline.py::test_linux_baseline_is_platform_labeled_and_distinct_from_darwin
2 passed in 0.26s
```

The platform suite exercises real LLVM/self C ABI harnesses, wall-clock
proximity, monotonic ordering and real sleep duration, the exact Darwin object
boundary, Linux raw-syscall IR/self assembly, archive selection, and retained
C-helper undefined symbols. The runtime consumers prove both Python time calls
and timeout process-group cleanup.

## Ratchet evidence

A fresh Darwin-arm64 stage1 reduced the undefined-symbol set from 60 to 58:

```text
removed: gettimeofday, time
added:   none
remaining time boundary: clock_gettime, nanosleep
```

Both Darwin baseline files are tightened by two symbols and retain the same
six-symbol pthread-only delta. Linux declares none of the four time symbols.

## Supported claim

Wall/monotonic timing and sleep for the default pcc-Python production archive
are owned behind a freestanding pcc-Python ABI. Linux x86_64 uses raw syscalls;
Darwin uses only the two named libSystem calls above.

## Not proven

This does not add general `time.sleep` frontend lowering or migrate calendar
formatting. Process lifecycle, child environment propagation, sockets and
resolver families remain. This is not Linux container execution, the full
five-GC matrix, or the pcc1->pcc2->pcc3 fixed point.
