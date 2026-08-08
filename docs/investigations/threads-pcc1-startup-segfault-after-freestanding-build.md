# Investigation: threads-on pcc1 segfaults at the publication barrier

## Status
resolved (invalid reproduction shape; no production code change)

## Problem Description

After one retained isolated threads runtime archive built and linked
successfully, its fresh stage1 pcc1 crashed with `SIGSEGV` in both publication
checks.  LLDB proved an infinite recursion between `pcc_gc_note_frame_enter`
and `pcc_gc_frame_enter`.

This was an invalid reproduction shape, not a production/test regression.  The
real test names its copied runtime root `py_runtime`, which intentionally makes
`_is_py_runtime_library_source()` suppress implicit GC roots while compiling
runtime ABI functions.  The retained experiment instead used a root named
`libc-ratchet-threads-runtime-v1`; that bypassed the runtime-source classifier
and injected a frame root into the frame-enter implementation itself.

Predecessor (resolved first failure):
[`threads-isolated-runtime-missing-python-port-members.md`](threads-isolated-runtime-missing-python-port-members.md).

## Repro

```text
gtimeout 360s env -u LC_ALL \
  PCC_WITH_THREADS=1 \
  PCC_RUNTIME_DIR=build/libc-ratchet-threads-runtime-v1 \
  PCC_BOOTSTRAP_OUT_DIR=build/libc-ratchet-threads-v3 \
  bash scripts/bootstrap.sh --stage 1
```

Observed on 2026-08-03: the binary linked after 82.383 seconds, then both
publication commands exited through `Segmentation fault: 11`; stage result was
139.

## Test [CONFIRMED]

The retained invalid-shape executable is
`build/libc-ratchet-threads-v3/pcc1`.  Batch LLDB showed alternating
`pcc_gc_note_frame_enter` / `pcc_gc_frame_enter` frames until stack exhaustion.
The faithful focused test, which creates `.../py_runtime/py`, passes its pcc1
publication and libc ratchet in 72.55 seconds.  Its `nm -u` set is exactly the
52-symbol threads baseline; the threads-off pcc1 is exactly the 46-symbol
baseline.

## Proposals

- No.1 Capture the first native crash stack and owning runtime function [CONFIRMED]
- No.2 Repair the demonstrated threads-on startup contract with a focused regression [REJECTED]

## No.1 Capture the first native crash stack and owning runtime function

### Experiment

Run the retained signed pcc1 under LLDB in batch mode with `--help`; inspect the
first non-system frame and its arguments/globals.  Compare against the green
threads-off pcc1 only after the crash location is known.

### CONFIRMED

LLDB stopped at `pcc_gc_note_frame_enter + 20`; the next 79 frames alternated
with `pcc_gc_frame_enter + 56`.  The copied `py_gc_backend.ll` contained two
entry calls to `pcc_gc_frame_enter`, while the normal runtime-source build did
not.  Comparing paths identified the classifier mismatch.

## No.2 Repair the demonstrated threads-on startup contract with a focused regression

### Code Change

None.  Do not broaden production path heuristics to accommodate a diagnostic
directory that failed to reproduce the test's documented `py_runtime` shape.

### REJECTED

The exact original test is green after the predecessor's freestanding
safepoint fix.  A code change for this invalid experiment would weaken or
complicate runtime-source classification without fixing a reachable boundary.
