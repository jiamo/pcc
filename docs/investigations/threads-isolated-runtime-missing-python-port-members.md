# Investigation: threads-on isolated runtime misses pcc-Python archive members

## Status
resolved

## Problem Description

The default fresh pcc1 and its exact libc-import ratchet are green after the
freestanding stdio migration.  The separate threads-on ratchet fails earlier,
while linking its stage1 pcc1 against a private copy of `py_runtime`: Darwin
`ld` reports undefined pcc runtime symbols including `py_tuple_slice` (and an
adjacent truncated symbol) even though the repository runtime archive normally
defines those symbols.

This is a separate failure from the resolved C-ABI variadic export issue.  It
was localized before treating the threads-on libc-import JSON as measured
evidence, because the failing run never produced a pcc1 binary for `nm -u`.

Successor (separate second failure):
[`threads-pcc1-startup-segfault-after-freestanding-build.md`](threads-pcc1-startup-segfault-after-freestanding-build.md).

## Repro

```text
gtimeout 900s env -u LC_ALL uv run pytest -q -n0 -m integration \
  tests/python/test_libc_import_baseline.py::test_threads_pcc1_libc_imports_stay_within_baseline
```

Observed on 2026-08-03: stage1 link failure after 186.97 seconds; no `nm`
comparison was reached.  The pytest assertion currently preserves only the
last 1500 bytes of stdout and stderr, so the complete missing-symbol set was
not retained.

## Test [CONFIRMED]

The focused integration test deterministically reached the isolated runtime
build and failed at stage1 link.  The repository archive contains members
`py_tuple_slice.o` and `py_list.o`, and `nm` reports definitions for both
`py_tuple_slice` and `py_list_slice`; therefore the next experiment must retain
the copied runtime directory and inspect the exact archive and link command
used by the failing build.

The required green boundary is stricter than a successful link: the produced
threads-on pcc1 must complete `nm -u`, match
`tests/libc_import_baseline_threads.json`, and differ from the threads-off
baseline only by the named pthread ABI.

## Proposals

- No.1 Reproduce with a retained isolated runtime and inspect the archive/link input [CONFIRMED]
- No.2 Repair archive selection or construction at the demonstrated boundary [CONFIRMED]

## No.1 Reproduce with a retained isolated runtime and inspect the archive/link input

### Experiment

Copy only `src`, `py`, `include`, `Makefile`, and `vendor` into a unique
directory under `build/`, run the same stage1 bootstrap with
`PCC_WITH_THREADS=1` and `PCC_RUNTIME_DIR` pointing to that copy, then inspect
the retained archive using `ar -t` and `nm`.  Enable the existing runtime debug
log so the selected archive path and make target are explicit.

### CONFIRMED

The retained run selected the intended private
`libpy_runtime_pcc_py.a`.  The archive build failed before publication because
`PCC_WITH_THREADS=1` injected a `pcc_thread_safepoint` call into
`freestanding_mem_str.py`; the pipeline then warned and continued to link with
no archive, which explained the otherwise misleading all-runtime-symbol
undefined list.

## No.2 Repair archive selection or construction at the demonstrated boundary

### Code Change

Freestanding modules now explicitly disable compiler-injected thread
safepoints.  They are the runtime dependency root and must remain callable
before the managed threading/GC substrate exists.  The normal function-entry
safepoint remains enabled for non-freestanding modules under
`PCC_WITH_THREADS=1`.

### CONFIRMED

The new regression first failed with an out-of-closure
`pcc_thread_safepoint` call, then passed after the fix.  The full
`test_freestanding_module.py` file reports 17 passed; a real threads-on compile
of `freestanding_mem_str.py` succeeds; and the retained private runtime make
completes and publishes an archive containing its pcc-Python members.  A fresh
threads-on stage1 then linked pcc1, exposing the separate startup segfault
tracked by the successor investigation.
