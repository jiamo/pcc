# libc import ratchet: linux and threads twins land

Date: 2026-08-01

Task: `LIBC-P1-IMPORT-RATCHET`

## What the row still needed

The darwin-arm64 threads-off ratchet has been enforcing since 2026-07-24. The
row's open boundary named two missing twins: a linux-x86_64 baseline behind a
docker gate, and a `PCC_WITH_THREADS` variant. Both now exist.

## Threads variant (darwin-arm64)

`tests/libc_import_baseline_threads.json`, **70 symbols**, and the gate
asserts the delta over the threads-off baseline is pthread-only:

```text
delta = pthread_cond_broadcast, pthread_cond_destroy, pthread_cond_wait,
        pthread_mutex_destroy, pthread_mutex_lock, pthread_mutex_unlock
```

Two isolation problems had to be fixed for this baseline to mean anything:

- The threads build is done through a **private copy of the runtime tree**
  (`PCC_RUNTIME_DIR`). Without that, `PCC_WITH_THREADS=1` rebuilds the shared
  in-tree archives and every later threads-off binary inherits the pthread
  imports — which is exactly what happened on the first attempt and made the
  threads-off ratchet fail.
- The private copy must include `vendor/`. Omitting it silently drops the
  vendored musl objects, and the build re-imports memcpy/strlen/atoi/strtod/
  pow from libSystem: the first regenerated baseline recorded 83 symbols with
  a 19-entry "pthread" delta that was mostly not pthread at all. With
  `vendor/` copied the count is 70 and the delta is the six above.

Both are recorded in the test as comments, because both produce a *passing*
baseline that measures the wrong binary.

## Linux twin (x86_64, docker gate)

`tests/libc_import_baseline_linux.json`, **109 symbols**, captured from a real
ELF stage1 pcc1 built inside `docker/self-backend-linux-x86_64.Dockerfile`.
Getting there needed four distinct fixes, none of which was a pcc defect
except by exclusion:

1. `clang-14` (bookworm's default) rejects pcc's emitted IR with
   `expected type` — it predates LLVM's opaque `ptr`. The Dockerfile now adds
   LLVM's apt repo and pins **clang-16**.
2. The link then failed on `py_*` symbols: the container was reusing the
   **arm64** archives the macOS host had built in the shared mounted tree.
3. Building a native archive in `/tmp` did not survive, because the harness
   runs `docker run --rm` — each invocation is a fresh container.
4. Building it under `/workspace/build/linux_rt` (mounted) and passing
   `PCC_RUNTIME_ARCHIVE`/`PCC_RUNTIME_DIR` produced a working
   33 MB `ELF 64-bit LSB pie executable, x86-64`.

The two platform baselines are deliberately not comparable symbol-for-symbol,
and a test asserts they cannot be confused: linux carries ELF/TLS/ifunc
machinery (`__libc_start_main`, `__errno_location`, `__tls_get_addr`,
`_ITM_*`, `__gmon_start__`) that mach-o has no equivalent for, while darwin
carries `__stderrp`, `_tlv_bootstrap`, `__chkstk_darwin`, `__stack_chk_guard`.
The linux count is also higher because that build links the C runtime
(`HIGH=c`), not the pcc-Python port archive.

## Commands and results

```text
tests/python/test_libc_import_baseline.py (non-integration)   2 passed
tests/python/test_libc_import_baseline.py -m integration      2 passed
  - linux ratchet: builds via the docker harness when needed, then holds the
    ELF's undefined symbols to the 109-symbol baseline
  - threads ratchet: 70 symbols, pthread-only delta
  - platform-labelling test: the two baselines cannot be mistaken for each
    other
darwin threads-off ratchet (unchanged this slice)            64 symbols
```

## Supported claim

All three ratchets exist and enforce: darwin-arm64 threads-off (64),
darwin-arm64 threads-on (70, pthread-only delta), and linux-x86_64 (109,
docker-gated). Growth fails on every platform; shrinkage is recorded by
deliberate regeneration.

## Not proven

- The linux baseline is from an `llvm`-backend, `HIGH=c` build. A self-backend
  linux stage1 is `LINK-P3-ELF-LINUX`'s subject, not this row's.
- The linux number is a first capture, not a tightened one: nothing on that
  platform has been migrated to pcc-owned implementations yet.
