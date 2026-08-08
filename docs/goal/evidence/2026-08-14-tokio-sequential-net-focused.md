# Tokio-inspired sequential TCP focused evidence — 2026-08-14

Mode: host-pcc compilation, no-libpython runtime, Darwin kqueue, explicit
verified pcc-Python production runtime archive.

Focused commands were run fail-fast and serially:

```text
gtimeout 60s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_virtual_thread_network.py::test_darwin_sequential_tcp_contract
gtimeout 180s env -u LC_ALL PCC_RUNTIME_ARCHIVE=/Users/jiamo/my/pcc/pcc/py_runtime/libpy_runtime_pcc_py.a uv run pytest -q -x -n0 tests/python/test_virtual_thread_network.py::test_sequential_tcp_gc_matrix
```

Result: both nodes passed. The first verifies the raw LLVM module as well as
the public lowering and lock-through-syscall source contracts. The second
compiled one executable and passed real loopback echo, zero-deadline error,
parked cancellation, close plus exact fd reuse, stale-generation rejection,
and scheduler/timer/I/O-root return-to-baseline assertions under GC0..4.

The first focused run originally exposed invalid LLVM dominance: a hidden
generator frame-slot root cast was emitted in one retry arm and reused by
sibling cleanup/retry blocks. The cast is now emitted before the generator
function entry terminator, and raw-module verification prevents regression.

This remains weak evidence because the source-current pcc1/self/no-libpython
integration node has not run. The externally owned HARNESS build is producing
that pcc1 and must not be raced or replaced. Linux epoll and broader Tokio
compatibility are outside this Darwin-first claim.
