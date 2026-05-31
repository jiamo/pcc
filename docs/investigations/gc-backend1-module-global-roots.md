# Investigation: Backend 1 must trace module global roots

## Status
resolved

## Problem Description
Continue the Backend #1 production root audit from `goal.md`.  Backend #1
currently traces registered function frame roots, but module-level object
globals are plain LLVM global slots and are not added to the tracing root set.
Programs that keep objects alive through globals such as `live_root = []`,
`gc.callbacks`, or resurrection caches can therefore lose reachable objects
under `PCC_GC_BACKEND=1`.

## Repro
Run the focused existing gate:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_gc_effectiveness.py::test_collect_preserves_root_reachable_subgraph \
  -q -n0
```

Expected pre-fix failure: the program prints `False` for
`live_root[0] is keep`, showing that the global `live_root` was not treated as
a tracing root.

## Test [CONFIRMED]
The focused gate fails before the fix:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_gc_effectiveness.py::test_collect_preserves_root_reachable_subgraph \
  -q -n0
# FAILED: assert ['False', 'True'] == ['True', 'True']
```

## Proposals
- No.1 Register module object global slots as long-lived GC roots     [CONFIRMED]

## No.1 Register module object global slots as long-lived GC roots
### Code Change
Emit `pcc_gc_frame_enter()` calls for object module-global storage slots during
module top-level initialization and entry-module `main` setup.  The slots
themselves stay alive for the module lifetime and are cleared by existing
module teardown.
### CONFIRMED
The focused failing gate now passes under Backend #1 and the default backend:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_gc_effectiveness.py::test_collect_preserves_root_reachable_subgraph \
  -q -n0
# 1 passed in 0.75s

env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest \
  tests/test_gc_effectiveness.py::test_collect_preserves_root_reachable_subgraph \
  -q -n0
# 1 passed in 0.62s
```

Codegen/root and bootstrap-adjacent gates:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s \
  uv run python -m py_compile pcc/py_frontend/codegen/layer1.py
# success

env -u LC_ALL /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_gc_root_precision.py -q -n0
# 3 passed in 0.92s

env -u LC_ALL /opt/homebrew/bin/timeout 420s \
  uv run pytest tests/test_py_multi_file_compile.py \
  tests/test_py_multi_file_bootstrap_shim.py -q -n0
# 70 passed in 131.88s
```

Related Backend #1 failures remain separate: `gc.callbacks.remove` still
leaves callback observations at four events, function-local live cycles can
still be swept, and resurrection caches can still contain objects whose
attributes were cleared before resurrection.

## Report (only when the investigation is closing)
Proposal No.1 landed.  Module object-global storage slots now participate in
the tracing root set, so objects reachable only through module globals survive
Backend #1 explicit collection.

This closes one global-root failure in No.6, but it does not complete Backend
#1 production.  The remaining failures above need their own reductions.
