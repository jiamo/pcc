# Gateway nonblocking socket/waitset — focused evidence

Date: 2026-08-14

Task: `GATEWAY-P2-NONBLOCKING-SOCKET-WAITSET`

## Fixes made while running the focused gate

- The loopback connect probes now wait through the compiler-owned readiness
  observation API before accepting on a nonblocking listener. A legal initial
  `EAGAIN` is no longer treated as a connection failure.
- The pcc-Python waitset's kqueue/epoll capability and skip-reason exports now
  use the authoritative C header's `int` ABI through typed `i32` exports.
  Virtual-thread extern declarations use `c_int32`, and the C probe relies on
  `py_io_waitset.h` instead of conflicting hand-written `int64_t` prototypes.

## Focused gates

```text
gtimeout 40s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 \
  tests/python/test_py_io_waitset.py::test_production_epoll_surface_owns_live_syscalls_and_generation

1 passed in 0.09s
```

```text
gtimeout 300s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 \
  tests/python/test_py_io_waitset.py::test_production_archive_exports_backend_labels_and_live_epoll

1 passed in 257.30s
```

The second command performed the one required cold, content-addressed
pcc-Python runtime build for the changed source and then linked and executed
the C ABI probe.

```text
gtimeout 240s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -q -x -n0 \
  -m "not integration" \
  tests/python/test_gateway_nonblocking_socket.py \
  tests/python/test_py_io_waitset.py

13 passed, 1 deselected in 3.62s
```

The edited Python sources and focused test also passed `python -m py_compile`.

## Claim boundary

This proves the current host compiler, current pcc-Python runtime archive,
nonblocking socket probes and Darwin/Linux waitset ABI at the focused gate. It
does not prove the source-current pcc1 self/no-libpython TCP echo, the GC0..4
integration matrix, Linux AArch64 syscall lowering or long-running wake-race
behavior. Those remain the open boundary, so the task is `DONE_WEAK`.
