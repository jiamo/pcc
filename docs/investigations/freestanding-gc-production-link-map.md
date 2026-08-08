# Investigation: production GC link-map ownership

## Status

resolved

## Problem Description

`LIBC-P2-FREESTANDING-GC` requires every production collector symbol family to
come from freestanding pcc-Python and no retained C collector implementation
to enter the production archive. The audit must distinguish collector policy
from the explicitly retained machine-boundary lock/TLS and C-extension ABI
kernel rather than treating every symbol containing `gc` as equivalent.

## Repro

Inspect `libpy_runtime_pcc_py.a` with `ar -t` and `nm -A -g`, map each object
member to `pcc/py_runtime/py/<stem>.py` or `src/<stem>.c`, and classify public
`pcc_gc_*` / `py_gc_*`, internal `pcc_py_gc_*`, and C-API `PyObject_GC_*`
definitions separately.

## Test [CONFIRMED]

The archive contains 633 public collector definitions. All map to pcc-Python
source members. Ten graph-lock/TLS definitions map to
`py_runtime_high_substrate.c`; four `PyObject_GC_*` entries map to the no-
libpython C-API shim. The new fail-closed test records these exact boundaries.

## Proposals

- No.1 Add exact production source-attribution ratchets [CONFIRMED]

## No.1 Add exact production source-attribution ratchets

### Code Change

Add a production-archive link-map test that rejects any public collector
definition without a same-stem pcc-Python source, admits only the exact ten
machine-boundary C kernel symbols, identifies the four C-API shim entries, and
rejects the two C-only oracle member names.

### CONFIRMED

The three focused link-map tests pass against the content-addressed production
archive. More than 600 public collector definitions all map to pcc-Python
sources. The exact C machine-boundary allowance is six callable lock/TLS
helpers plus four private TLS storage symbols in
`py_runtime_high_substrate.o`; any addition fails the test. Four
`PyObject_GC_*` C-extension ABI entries remain independently identified in
`py_capi_shim.o`. Neither C-only oracle member enters the production archive.

## Report

Proposal No.1 landed. No production collector policy symbol is C-owned. The
remaining C names are explicitly classified machine-boundary kernel storage /
synchronization and C-extension ABI, consistent with the repository runtime
layering contract rather than hidden collector implementations.
