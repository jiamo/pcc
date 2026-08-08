# LIBC-P2-THIN-WRAPPERS — process primitive slice

Date: 2026-08-02

## Source identity

This evidence describes the current dirty shared worktree. It is not a clean
commit or release claim.

## Changed behavior

- Added strict freestanding `freestanding_platform_process.py` as the owner of
  EINTR-aware wait, signal delivery, POSIX wait-status normalization, immediate
  process exit, and abort-by-SIGABRT.
- Darwin lowers to the explicitly named libSystem boundary
  `waitpid/kill/__error/getpid/_exit`. Linux x86_64 lowers to raw
  `wait4/kill/getpid/exit_group` syscalls and declares none of those libc
  process functions.
- The timeout helper retains its process-group TERM/grace/KILL policy but calls
  the pcc-Python environment/spawn/wait/signal ABI. Its argv copying, deadline
  loop, TERM/grace/KILL policy, and exit-status normalization are now authored
  in `py_process_timeout.py`; the retained C file is a host-oracle source and
  is absent from the production pcc-Python archive.
- Native `sys.exit`, runtime fatal logging, fortify failures, and the two GC
  fatal paths now consume the pcc-Python exit/abort ABI. Their host-C oracle
  variants retain host-libc behavior when the production define is absent.
- The freestanding IR validator now derives its allowed extern closure from
  the `pcc.unsafe` intrinsics actually imported by each module. Direct
  `extern("malloc", ...)` remains rejected; adding one platform intrinsic does
  not create a global extern whitelist.

## Red/green evidence

The wait/signal test first failed because the module did not exist. After the
initial lowering it was stopped by the strict extern-closure validator until
the exact process boundary was modeled. Exit/abort then failed with the two
expected undefined pcc ABI symbols before their implementation.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_platform_process.py
7 passed in 2.43s (48.06s on the cold archive build)

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_module.py \
  tests/python/test_freestanding_platform_process.py \
  tests/python/test_unsafe_syscall6.py \
  tests/python/test_subprocess_timeout_runtime.py \
  tests/python/test_native_subprocess_check_output.py \
  -k 'not pcc1_bootstrap_wrapper_enforces_timeout'
39 passed, 1 deselected in 55.19s

gtimeout 180s env -u LC_ALL bash scripts/bootstrap.sh --stage 1
stage 1 passed in 30.974s

gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_libc_import_baseline.py::test_pcc1_libc_imports_stay_within_baseline \
  tests/python/test_libc_import_baseline.py::test_linux_baseline_is_platform_labeled_and_distinct_from_darwin
2 passed in 0.31s
```

The process harness uses real child processes to prove exit 7, explicit kill
to SIGTERM, exit 23, abort to SIGABRT, wait-status decoding, and both LLVM and
self-backend objects. The default runtime smoke proves `sys.exit(23)` under
self/no-libpython. Archive inspection proves the production timeout/log/
fortify/GC/process objects reference the pcc platform ABI instead of direct
`waitpid/kill/abort/exit`.

The timeout-port red test first returned zero after one second. Object
inspection separated two stacked failures: a stale same-name C object was
still present after the Makefile ownership change, then the actual Python
object read `_TIMEOUT_RC` from uninitialized module-global storage. Runtime
library modules are linked without executing their Python module initializer,
so timeout return, poll, and grace constants now remain literals on the C-ABI
export path.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_subprocess_timeout_runtime.py \
  tests/python/test_freestanding_platform_process.py
12 passed in 87.63s

nm -A -g pcc/py_runtime/libpy_runtime_pcc_py.a
py_process_timeout.o: T _py_subprocess_run_timeout
py_process_timeout.o: U _pcc_platform_spawnp
freestanding_platform_process.o: T _pcc_platform_spawnp

otool -rv pcc/py_runtime/build_py/py_process_timeout.o
no _calloc or _posix_spawnp imports; _malloc and _pcc_platform_spawnp present
```

## Ratchet evidence

A fresh Darwin-arm64 stage1 produced this exact change:

```text
removed: abort, exit
added:   _exit
count:   58 -> 57
```

This is an argued narrowing, not an unlabelled addition: ordinary libc `exit`
and libc `abort` semantics are now pcc-Python-owned; Darwin retains only the
immediate `_exit` machine boundary. The threads-on baseline mirrors the change
at 63 and preserves the exact six-symbol pthread-only delta.

The following ordinary-system slice removes the shell-launch convenience
boundary as well. `py_process_substrate.py` now constructs `/bin/sh -c` argv,
deep-copies the owned environment, spawns through `pcc_platform_spawnp`, waits
through `pcc_platform_waitpid`, and decodes the raw status through the shared
Python owner. This route is used by ordinary `subprocess.run` and native
temporary-directory cleanup.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_native_subprocess_check_output.py \
  tests/python/test_native_tempfile_tempdir.py
10 passed in 2.80s

gtimeout 180s env -u LC_ALL bash scripts/bootstrap.sh --stage 1
stage 1 passed in 25.506s

fresh stage1 import change:
removed: system, posix_spawnp
added:   posix_spawn
count:   57 -> 56

gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_libc_import_baseline.py::test_pcc1_libc_imports_stay_within_baseline \
  tests/python/test_libc_import_baseline.py::test_linux_baseline_is_platform_labeled_and_distinct_from_darwin
2 passed in 0.27s
```

The `posix_spawnp -> posix_spawn` trade is deliberate: PATH search is now
authored in pcc-Python, so Darwin receives an already resolved path. Linux
continues to declare neither symbol and lowers the path to raw process
syscalls. The threads-on ratchet mirrors the net reduction at 62 while keeping
the exact six pthread-only symbols.

## Socket and resolver closure

`freestanding_platform_socket.py` now owns the finite network boundary:
numeric IPv4 and compressed/uncompressed IPv6 parsing, `/etc/hosts` alias
lookup, sockaddr layout selection, TCP connect/listen, and socket send/recv.
Unknown names fail closed; there is no DNS, NSS, subprocess, or libpython
fallback. Darwin uses the named libSystem socket/file ABI; Linux x86_64 lowers
the same source to raw syscalls.

The retained `py_http.c` and `py_asyncio_io.c` production helpers consume this
ABI. Their production objects no longer import `getaddrinfo`, `freeaddrinfo`,
`socket`, `connect`, `send`, or `recv`; their C-oracle builds keep the host
calls when the production define is absent. A Darwin AArch64-specific failure
was caught before closure: `fcntl` had been emitted with a fixed three-argument
prototype, but Darwin's variadic ABI passes the third argument differently.
Declaring the LLVM function as truly variadic restored `O_NONBLOCK`, closing
the initial accept hang and both downstream relay timeouts.

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_platform_socket.py \
  tests/python/test_native_asyncio_stdlib_no_libpython.py \
  tests/python/test_package_network_acquisition.py::test_self_backend_transport_and_sha256_kernel_primitives
19 passed in 65.94s

gtimeout 180s env -u LC_ALL bash scripts/bootstrap.sh --stage 1
stage 1 passed in 94.785s

fresh stage1 import change:
removed: freeaddrinfo, getaddrinfo
added:   open, read
count:   56 -> 56

gtimeout 30s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_libc_import_baseline.py::test_pcc1_libc_imports_stay_within_baseline \
  tests/python/test_libc_import_baseline.py::test_linux_baseline_is_platform_labeled_and_distinct_from_darwin
2 passed in 16.48s
```

The equal-count trade is lower-level and finite: `open/read/close` read only
the hosts file, while libc's policy-rich `getaddrinfo/freeaddrinfo` resolver is
gone. The 94.785-second cold stage1 is a measured bootstrap-performance cost,
not a performance acceptance claim; it must be profiled before the final
five-GC matrix.

## Supported claim

The finite thin-wrapper task boundary—IO, filesystem, environment, system,
time, process/spawn/timeout, numeric+hosts resolution, and TCP socket
primitives—is authored in pcc-Python. Linux x86_64 uses raw syscalls; Darwin
uses the explicitly named libSystem platform ABI recorded by the ratchet.

## Not proven

Darwin still imports its named platform ABI; this slice does not claim DNS/NSS
resolution or general POSIX completeness. Captured `popen/pclose` belongs to
the separate stdio task. The profiled cold-stage regression, full five-GC
matrix, and pcc1->pcc2->pcc3 fixed point remain open.
