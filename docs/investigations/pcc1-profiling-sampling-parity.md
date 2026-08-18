# Investigation: pcc1-native profiling.sampling parity with Python 3.15

## Status

active

## Problem Description

The user requires pcc1 to provide the Python 3.15 PEP 799
`profiling.sampling` capability in pcc1's own execution model.  Host CPython's
Tachyon implementation is an oracle, not an execution owner.  The supported
surface must be extensible to all Python rather than permanently limited to the
current compiler subset.

Public parity includes run/attach/dump/replay, statistical sampling, thread and
execution-state filtering, exception/GC modes, subprocess recursion, logical
async stacks, source/semantic-operation attribution, and the standard useful
output families.  Physical implementation differs deliberately: pcc publishes
its own remote-unwinder ABI and maps native PCs to Python semantic frames,
vthreads, GC0--4 phases, and lowered operation IDs.  No libpython, CPython
runtime-layout dependency, or host profiler subprocess may own pcc1 profiling.

## Repro

After the 3.15 host migration, run the current-source pcc1 in strict self/
no-libpython mode with:

```text
pcc1 -m profiling.sampling --help
```

Expected current boundary: the module/CLI is unavailable.  Preserve the exact
diagnostic before implementation.  Completion requires compiling, running,
attaching to, and replaying a pcc-native target rather than merely importing a
host stdlib module.

## Test [pending]

Add a capability matrix against CPython 3.15.0rc1 plus pcc-specific native-PC,
vthread, five-GC, child-process and no-host ownership gates.  Mark confirmed
only after the current pcc1 failure is observed.

## Proposals

- No.1 Publish a versioned pcc remote profiling ABI and PEP 799 frontend [pending]

## No.1 Publish a versioned pcc remote profiling ABI and PEP 799 frontend

### Code Change

Design a versioned, read-only process descriptor exposing thread/carrier/task
state, stack/root epochs, code identity, PC-to-source/semantic-op tables,
exception state, and GC phase.  Implement an out-of-process unwinder and the
`profiling.sampling` CLI/output layer in pcc-Python.  Keep ordinary Python frame
semantics independent of their current native representation and fail closed
on incompatible descriptor versions.

## Update — CPython 3.15 host oracle profile

The host migration supplied a real Stage1 oracle before any pcc1 profiler
implementation.  A dedicated 3.15 executable with the documented macOS
`com.apple.security.cs.debugger` entitlement ran Tachyon at 1kHz in CPU/opcode/
native/subprocess mode.  It produced 227 per-process flamegraphs and a complete
pcc1 build receipt.  `scripts/pcc_tachyon_aggregate.py` combines the HTML into
one deterministic JSON report.

Across 988,071 self samples, the largest pcc-owned files were
`arm64_encode.py` 12.26%, `self_backend_parse.py` 8.13%,
`arm64_asm_driver.py` 7.83%, `self_backend_kernel.py` 4.36%, and
`self_backend_analysis.py` 4.22%.  Process import startup was 15.59% and GC
5.71%.  The leading pcc function was `_stable_text_bucket_key` at 3.49%.

This proves immediate diagnostic value for Stage1.  Per explicit human order,
full pcc1 PEP 799 parity remains required but does not preempt the
Stage2<=Stage1 performance spine; only a minimal native tracer bullet may move
earlier when it directly shortens Stage2 diagnosis.
