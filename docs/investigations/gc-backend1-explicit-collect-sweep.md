# Investigation: Backend 1 explicit collect must sweep with live roots

## Status
resolved

## Problem Description
Continue the Backend #1 production sweep/finalizer audit from `goal.md`.
Backend #1 can mark unreachable objects as sweep candidates, but explicit
`pcc_gc_collect()` should not leave those candidates pending merely because the
same collection also traced at least one live root.

## Repro
Run the focused gate:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_gc_backend_incremental.py::test_incremental_backend_explicit_collect_sweeps_with_live_roots \
  -q -n0
```

Expected pre-fix failure: the probe prints `True` for collection progress and
`1` from `pcc_gc_has_tracing_sweep()`, showing that explicit collect processed
the live root but left the dead object as a pending sweep candidate.

## Test [CONFIRMED]
The focused gate fails before the fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_gc_backend_incremental.py::test_incremental_backend_explicit_collect_sweeps_with_live_roots \
  -q -n0
# FAILED: assert ['True', '1'] == ['True', '0']
```

The first line proves explicit collection made tracing progress.  The second
line proves the dead object remained a pending tracing sweep candidate after
that explicit collection returned.

## Proposals
- No.1 Sweep tracing candidates after explicit collect reaches mark termination     [CONFIRMED]

## No.1 Sweep tracing candidates after explicit collect reaches mark termination
### Code Change
The landed slice:

- updates C-runtime `pcc_gc_collect()` to run `pcc_gc_collect_tracing()` after
  the explicit tracing step loop whenever sweep candidates are present;
- mirrors the same behavior in the pcc-Python runtime-high `py_obj.py`;
- adds C-runtime and pcc-Python runtime-high gates for a collection containing
  both a live registered frame root and an otherwise-dead object.
- roots the temporary argument tuple used by `print(a, b, ...)` while later
  arguments are evaluated, because a later argument may explicitly call
  `gc.collect()` while earlier argument objects are reachable only from that
  tuple.

Allocation-time automatic steps stay mark-only; this change is limited to
explicit collection.
### CONFIRMED
The focused gates now pass:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 360s \
  uv run pytest \
  tests/test_gc_backend_incremental.py::test_incremental_backend_explicit_collect_sweeps_with_live_roots \
  tests/test_gc_backend_incremental.py::test_incremental_backend_pcc_python_explicit_collect_sweeps_with_live_roots \
  -q -n0
# 2 passed in 1.40s
```

The full backend #1 incremental gate passes after the temporary-root fix:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_gc_backend_incremental.py -q -n0
# 6 passed in 4.73s
```

Related gates:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_gc_backend_incremental.py \
  tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py \
  -q -n0 -rxX
# 21 passed in 13.16s

env -u LC_ALL /opt/homebrew/bin/timeout 800s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
# 197 passed in 156.36s
```

Runtime and bootstrap-adjacent gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  make -B -C pcc/py_runtime libpy_runtime.a
# success; existing py_gc_backend.c unused-helper warning remains

env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  make -B -C pcc/py_runtime PCC='uv run pcc' \
    PYTHON=/Users/jiamo/my/pcc/.venv/bin/python3 libpy_runtime_pcc_py.a
# success

env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_llvm_capi_ir_parity.py \
  tests/test_llvm_capi_end_to_end.py -q -n0
# 23 passed in 0.13s

env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_fallback_baseline.py \
  tests/test_ir_py_fallback_baseline.py -q -n0
# 11 passed in 57.16s

env -u LC_ALL /opt/homebrew/bin/timeout 520s \
  uv run pytest tests/test_py_multi_file_compile.py \
  tests/test_py_multi_file_bootstrap_shim.py -q -n0
# 70 passed in 155.33s
```

The whole GC suite under `PCC_GC_BACKEND=1` is not green yet:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 900s \
  uv run pytest tests/test_gc_*.py -q -n0 -rxX
# 16 failed, 181 passed in 411.66s
```

The remaining failures are outside this explicit-sweep slice: one test assumes
initial backend `0` despite the env override, several live-cycle/global-root and
resurrection/finalizer cases still fail under Backend #1, and two performance
ratchets exceed their Backend #1 budgets.  `test_million_entry_list_release`
also hung under Backend #1 until its compiled probe was killed.

## Report (only when the investigation is closing)
Proposal No.1 landed.  Explicit `pcc_gc_collect()` for tracing backends now
sweeps pending tracing candidates after mark termination instead of leaving
them for a later call.  The same behavior is mirrored in the pcc-Python runtime
port.

The implementation also roots the `print(a, b, ...)` temporary argument tuple
while later arguments are evaluated.  That root is necessary because a later
argument can call `gc.collect()` while earlier argument objects are reachable
only through that tuple.

This closes one Backend #1 production sweep audit gap, but it does not complete
`goal.md` No.6.  The full `PCC_GC_BACKEND=1` GC suite still has the failures
listed above.

## Update 2026-05-14: explicit collect must age fresh allocations

The backend #1 production gate regressed again with a different signature:

```text
PCC_GC_BACKEND=1 tests/python/test_gc_backend_incremental.py
tests/python/test_gc_g1_cycle_collector.py
tests/python/test_gc_g2_finalizers.py

10 failed, 11 passed
```

The focused explicit-collect probes printed:

```text
False
0
```

and every G1 cycle/finalizer test printed zero finalizer calls or a zero
collection count. The root cause was the `PY_FLAG_GC_FRESH_ALLOC` protection
added for incremental/concurrent safety. Automatic backend #1/#2 steps should
keep fresh allocations black for one cycle so allocation-time tracing does not
race unregistered temporaries. Explicit `gc.collect()` is different: it owns a
stop-the-world boundary and has a stable root set, so fresh unreachable objects
must participate in the collection.

The current fix adds an explicit tracing-collect mode:

- `pcc_gc_begin_explicit_tracing_collect()` sets the tracing request bit and
  marks the current thread as an explicit collector.
- `pcc_gc_end_explicit_tracing_collect()` clears that mode.
- root seeding treats fresh objects as white during explicit collection and
  still grays the real roots before mark/drain/cut.
- automatic backend #1/#2 incremental work keeps the old fresh-object
  protection.
- the pcc-Python runtime mirror has the same mode bit and seeding behavior.

Validation:

```text
tests/python/test_gc_backend_incremental.py::test_incremental_backend_explicit_collect_sweeps_with_live_roots
tests/python/test_gc_backend_incremental.py::test_incremental_backend_pcc_python_explicit_collect_sweeps_with_live_roots
2 passed in 5.63s

PCC_GC_BACKEND=1
tests/python/test_gc_backend_incremental.py
tests/python/test_gc_g1_cycle_collector.py
tests/python/test_gc_g2_finalizers.py
21 passed in 13.07s
```
