# Investigation: default GC should collect closure capture cycles

## Status
resolved

## Problem Description
The only remaining `tests/test_gc_g1_cycle_collector.py` xfail under the
default backend is a closure/list/function cycle:

```text
list -> function -> captures -> list
```

The default cycle collector should reclaim the cycle and run the sentinel
finalizer. The current run leaves the sentinel alive and prints `0`.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_g1_cycle_collector.py::test_cycle_closure_capture -q -n0 --runxfail
```

Expected current failure before the fix:

```text
AssertionError: assert '0' == '1'
```

## Test [CONFIRMED]
The failing baseline was observed with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_g1_cycle_collector.py::test_cycle_closure_capture -q -n0 --runxfail
```

Observed result:

```text
stdout was 0 instead of 1
```

## Proposals
- No.1 Release owned temporaries in native list append     [CONFIRMED]

## No.1 Release owned temporaries in native list append
### Code Change
`py_obj_gc.c` already traverses `PY_TYPE_FUNC -> captures`, and capture tuples
are tracked when they contain cycle-capable objects. The failure is in
codegen: `payload[0].append(inner)` creates an owned `py_obj_getitem()` result
for the receiver and an owned native function value for `inner`, but the native
`list.append` method path does not release either temporary after the list has
retained what it needs.

Patch the native `list.append` lowering to release owned receiver temporaries
and owned argument temporaries after `py_list_append()`.
### CONFIRMED
Implemented in `pcc/py_frontend/codegen/layer1.py`.

The patch extends owned-object detection to user function values and releases:

- the boxed append argument when it is an owned temporary, such as a freshly
  materialized native function value;
- the append receiver when it came from an owned expression, such as
  `payload[0]`.

After that, the only refs left in the repro are the cycle's internal refs, so
the default cycle collector can reclaim the closure/list/function graph.

Verification:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_g1_cycle_collector.py::test_cycle_closure_capture -q -n0 --runxfail
env -u LC_ALL /opt/homebrew/bin/timeout 240s uv run pytest tests/test_gc_g1_cycle_collector.py -q -n0 -rxX
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0 -rxX
```

Observed results:

```text
1 passed
8 passed
162 passed, 23 xfailed
```

## Report
No.1 landed. The default backend now collects the closure/list/function cycle
covered by `tests/test_gc_g1_cycle_collector.py::test_cycle_closure_capture`.

This was a codegen ownership bug, not a missing default-GC traverse edge:
`py_obj_gc.c` already traversed function captures, but extra owned temporary
references kept the cycle externally reachable.
