# LIBC-P2-THIN-WRAPPERS — owned child-environment snapshot slice

Date: 2026-08-02

## Source identity

This evidence describes the current dirty shared worktree. It is not a clean
commit or release claim.

## Changed behavior

- The freestanding pcc-Python environment owner now exports a deep-copied,
  NULL-terminated `char **` snapshot plus a matching release operation.
- Snapshot construction holds the environment lock, copies every string, and
  cleans up partial allocation failure. Callers never borrow the mutable live
  table after releasing the lock.
- The transitional subprocess-timeout helper in the production pcc-Python
  archive passes that snapshot to `posix_spawnp`; the host-C oracle build keeps
  its original host-`environ` behavior.
- A child-process regression proves that a value written only through native
  `os.environ` is visible to `/bin/sh`. Before this slice the child received
  host `environ` and exited 1.

## Red/green evidence

Before implementation the two new regressions failed independently:

```text
undefined symbol: pcc_platform_env_snapshot
CalledProcessError: /bin/sh environment assertion exited 1
```

After implementation:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_platform_env.py::test_platform_env_owns_copy_set_overwrite_and_unset_semantics \
  tests/python/test_subprocess_timeout_runtime.py::test_native_subprocess_timeout_passes_owned_environment_to_child
2 passed in 5.82s

gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_platform_env.py \
  tests/python/test_subprocess_timeout_runtime.py \
  -k 'not pcc1_bootstrap_wrapper_enforces_timeout'
10 passed, 1 deselected in 47.24s
```

The wider focused gate covers LLVM and self-backend objects, real environment
mutation and snapshot isolation, Linux boundary inspection, production archive
selection and routing, timeout process-group cleanup, and child propagation.

## Supported claim

The production pcc-Python environment table is now the source for the existing
timed subprocess spawn path. Its snapshot is owned and remains stable across
later `setenv`/`unsetenv` calls.

## Not proven

The spawn/wait/kill implementation is still transitional C and Darwin still
imports the named libSystem process functions. Non-timeout `system`, captured
stdio/popen, sockets, resolver behavior, the full five-GC matrix, and the
pcc1->pcc2->pcc3 fixed point remain open.
