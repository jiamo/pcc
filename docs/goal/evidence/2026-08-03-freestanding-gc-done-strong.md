# Freestanding five-GC production closure

Date: 2026-08-03

Task: `LIBC-P2-FREESTANDING-GC`

Status: `DONE_STRONG`

## Closed claim

All known production collector policy for GC0 through GC4 is authored in
strict freestanding pcc-Python and compiled into the production runtime
archive. The production link map contains no C-owned collector definition.
The retained low-level C surface is the explicitly permitted machine and ABI
boundary, not a second collector implementation:

- six callable minor-collection TLS/lock helpers in
  `py_runtime_high_substrate.o`;
- four private TLS storage symbols backing those helpers;
- four independent C-extension compatibility entries in `py_capi_shim.o`:
  `PyObject_GC_New`, `PyObject_GC_Del`, `PyObject_GC_Track`, and
  `PyObject_GC_UnTrack`.

The complete-symbol link-map proof and exact allowance are recorded in
`2026-08-03-freestanding-gc-production-link-map.md`. C collector sources remain
only as explicit oracle/test inputs.

## Focused semantic evidence

The current production archive passed the focused closure summarized in
`2026-08-03-freestanding-gc-semantic-closure.md`:

```text
five-backend no-libpython/self production contract: 168 tests
C-extension/referent/root gates:                  31 tests
suspended-frame/scheduler-root differentials:      4 tests
pcc-Python GC4 relocation selection:               8 tests
threaded frame/mapped-root differentials:           2 tests
strict C-extension slot delegation:                 1 test
```

These cover the finite weakref, finalizer, resurrection, suspended-frame,
scheduler-root, C-extension-root, relocation, slot-update, and synchronization
claim boundary. The production link-map suite itself passed 3 tests.

## Long-running evidence

`2026-08-03-freestanding-gc-longrun.md` records the current-source strict
no-libpython/self 100,000-round workload for all five backends. Every backend
completed 6,400,000 operations with zero steady-tail RSS drift; peak RSS,
fragmentation, pause histogram, and throughput are all pinned in
`2026-08-03-freestanding-gc-longrun-summary.json`.

GC4 throughput was 40,047 ops/s and is materially below both the older
different-source 392,542 ops/s artifact and the current GC1-3 range. This is
not hidden or treated as a correctness failure. The finite follow-up is
`PERF-P1-GC4-FREESTANDING-LONGRUN`; it may not restore C collector policy or
weaken semantic gates.

## Current-source five-GC fixed point

Before the final matrix, the resource scheduler gate passed 8 tests, the
current bootstrap source hash was measured, and a fresh stage1 completed in
72.250 seconds. The matrix was then run once:

```text
gtimeout 1800s env -u LC_ALL uv run pytest -q -m integration \
  tests/python/gc/test_pcc_bootstrap_full_gc0.py \
  tests/python/gc/test_pcc_bootstrap_full_gc1.py \
  tests/python/gc/test_pcc_bootstrap_full_gc2.py \
  tests/python/gc/test_pcc_bootstrap_full_gc3.py \
  tests/python/gc/test_pcc_bootstrap_full_gc4.py

5 passed in 729.07s (0:12:09)
```

All five success manifests record the same current-source identity:

```text
bootstrap source sha256: 146f36fc6fec189abd1b280f12444a4ee444aa9e6269ab019ec5f8d1e0b9b3e1
runtime archive sha256:  f5f979410eb925ef3bb71029e5dea806abdcd75f1612f46ed124eb6d66048c7f
shared pcc1 sha256:      eeb1debbe2557cf49b70342e27bae4633969430d7e031b2b14fbc4a431ea9099
backend:                  self
python_libpython:         off
runtime_cc/runtime_high:  pcc/py
links_libpython:          false (GC0..4)
normalized pcc2 == pcc3: true  (GC0..4)
```

The success-manifest SHA-256 values for GC0 through GC4 are respectively:

```text
753665c237dd6303f8d940957baa8c76343ade17074d72350965be4b655cafbe
7ab3f86007660f284779f59e722bbc06c2467d30b8ff10bc77cdf2ca081e2015
cd6a54e8f0b52435d6ccb9c4f33267184a5b976181571eb7c811e07ac2fe2a49
9609a56d17981b3c8d65afa981a74202e65c3afceaa57877ce5bb5b2505aae1d
56bcf5dcc1f64b1bd2c22f950f734a2bed2be383cb8ceeab5ff826c66b1a30fd
```

No pytest, bootstrap, long-run, pcc, pcc1, pcc2, or pcc3 child remained after
the bounded gates.

## Claim boundary

This proves the finite task claim: pcc-Python owns the five production
collector families, the production link map has no C collector object, the
focused semantic surface is green, the long-running axes are recorded, and
all five current-source no-libpython/self pcc1-to-pcc2-to-pcc3 chains reach a
normalized fixed point. It does not claim zero low-level C machine/extension
ABI code, universal collector performance, or closure of the separate GC4
throughput regression.
