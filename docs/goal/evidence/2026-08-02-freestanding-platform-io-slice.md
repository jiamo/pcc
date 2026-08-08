# Freestanding platform IO wrapper slice — 2026-08-02

## Claim boundary

This partial `LIBC-P2-THIN-WRAPPERS` slice owns read, write, close and getpid
behind a pcc-Python ABI object.  Darwin uses explicitly named libSystem calls;
Linux x86_64 emits raw syscalls.  It does not claim the full wrapper-family
task: filesystem metadata, env, time, process/spawn and sockets remain open.

## Implementation

- `pcc/py_runtime/py/freestanding_platform_io.py` exports
  `pcc_platform_read`, `pcc_platform_write`, `pcc_platform_close` and
  `pcc_platform_getpid` from strict freestanding pcc-Python.
- `pcc.unsafe` gained target-labeled read/write/close/getpid lowering. Darwin
  declares the named ABI; Linux self IR uses syscall numbers 0, 1, 3 and 39.
- `py_os_substrate.py`, `py_process_substrate.py` and `py_print_sys.py` consume
  the shared `pcc_platform_*` ABI instead of directly owning those platform
  calls.
- The default pcc-Python runtime archive now includes
  `freestanding_platform_io.o`.

## Focused evidence

```text
tests/python/test_freestanding_platform_io.py
4 passed in 1.33s (object, LLVM/self C ABI, Linux raw-IR, archive plan)

compiled sys.stdout.write runtime smoke
1 passed in 0.69s

default runtime self-backend sys.stdout.write + os.getpid
1 passed in 42.66s (cold content-addressed archive rebuild)

platform IO + allocator + page-provider suites
15 passed in 7.01s

unsafe syscall/atomic/freestanding regressions
36 passed in 3.81s
```

The generated Darwin object has exactly `_read`, `_write`, `_close` and
`_getpid` undefined.  The monkeypatched Linux x86_64 IR and self assembly have
four raw syscall sites and no declarations for those functions.  Pipe
roundtrips and PID parity pass through LLVM and self objects.

## Remaining boundary

Continue with access/stat/getcwd/realpath/mkdtemp/env/uname/sysconf; then time,
sleep, process/spawn/wait/kill/exit/abort and socket/connect/send/recv plus the
explicit resolver subset.  Remaining C helper modules that still call these
symbols directly must migrate before the Darwin import ratchet can tighten for
the family as a whole.
