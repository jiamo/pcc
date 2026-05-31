# Investigation: Backend 1 closure cycle gate

## Status
resolved

## Problem Description
Continue the Tier 5 backend #1 production audit from `goal.md`.  The backend #1
verdict command has one remaining xfail:
`tests/test_gc_g1_cycle_collector.py::test_cycle_closure_capture`.  The exact
thing to confirm is whether this is still a GC closure-cycle failure, or whether
the test is blocked earlier by unsupported Python function attributes.

## Repro
Run the focused xfail as a real failure:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL PCC_GC_BACKEND=1 uv run pytest \
  tests/test_gc_g1_cycle_collector.py::test_cycle_closure_capture \
  -q -n0 --runxfail
```

Expected current failure: the program should fail before GC collection with
`AttributeError: object has no attribute outer`, because `inner.outer = inner`
requires arbitrary attributes on function objects.

## Test [CONFIRMED]
The focused gate has been observed failing:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL PCC_GC_BACKEND=1 uv run pytest \
  tests/test_gc_g1_cycle_collector.py::test_cycle_closure_capture \
  -q -n0 --runxfail
# FAILED: AttributeError: object has no attribute outer
```

This confirms the remaining backend #1 xfail is not currently exercising a GC
closure-cycle miss.

## Proposals
- No.1 Replace unsupported function-attribute cycle with closure/list cycle     [CONFIRMED]
- No.2 Add explicit tracing collect sweep fallback     [CONFIRMED]

## No.1 Replace unsupported function-attribute cycle with closure/list cycle
### Code Change
The smallest semantic gate keeps the "function closure cell"
shape but avoid assigning attributes to a function object:

- create a list containing a sentinel;
- define a nested function that captures the list;
- append the nested function to the captured list to form `list -> func ->
  captures -> list`;
- run `gc.collect()` and assert the sentinel finalizer fires.

### CONFIRMED
The replacement gate reaches GC and exposes the real failure:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL PCC_GC_BACKEND=1 uv run pytest \
  tests/test_gc_g1_cycle_collector.py::test_cycle_closure_capture \
  -q -n0 --runxfail
# FAILED: AssertionError: assert '0' == '1'
```

`pcc_gc_step_trace_cycle()` can mark unreachable objects as sweep candidates,
but the tracing backend step path does not currently call the existing
`pcc_gc_sweep_unreachable()` helper for backend #1/#2.

## No.2 Add explicit tracing collect sweep fallback
### Code Change
Implemented a narrow explicit-collection fallback:

- keep `pcc_gc_step()` as a marking/progress operation so existing frame-root
  and barrier tests can still inspect mark/sweep-candidate state;
- have `pcc_gc_collect()` run the existing step loop, then call a tracing sweep
  fallback only when default `py_gc_collect()` collected nothing, no trace work
  was performed, and the collect call itself produced fresh sweep candidates;
- remove pluggable-GC object-list entries when `py_gc_collect()` deallocates
  tracked objects, preventing stale backend #1/#2 pointers after the default
  cycle collector runs first;
- mirror the same `pcc_gc_has_tracing_sweep()` /
  `pcc_gc_collect_tracing()` behavior in the pcc-Python runtime port;
- make G1 cycle xfails conditional on `PCC_GC_BACKEND != 1`, so the backend #1
  verdict command is green while the default-backend closure gap remains
  recorded.

### CONFIRMED
The backend #1 verdict command is now green:

```bash
/opt/homebrew/bin/timeout 420s env -u LC_ALL PCC_GC_BACKEND=1 uv run pytest \
  tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py \
  -q -n0 -rxX
# 15 passed in 8.75s
```

Default backend behavior remains non-regressing with the conditional xfail:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_g1_cycle_collector.py -q -n0 -rxX
# 2 passed, 1 xfailed, 5 xpassed in 4.73s
```

Runtime archives and broader GC gates pass:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" \
  make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# exit 0

/opt/homebrew/bin/timeout 180s env -u LC_ALL make -B -C pcc/py_runtime \
  libpy_runtime.a
# exit 0

/opt/homebrew/bin/timeout 420s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_incremental.py tests/test_gc_backend_concurrent.py \
  tests/test_gc_abstraction_surface.py tests/test_gc_g1_cycle_collector.py \
  -q -n0
# 20 passed, 1 xfailed, 5 xpassed in 16.84s

/opt/homebrew/bin/timeout 420s env -u LC_ALL uv run pytest \
  tests/test_gc_threading_substrate.py tests/test_gc_backend_*.py \
  tests/test_gc_effectiveness.py -q -n0
# 44 passed, 3 xfailed, 3 xpassed in 31.68s
```

## Report (only when the investigation is closing)
No.1 and No.2 landed.  The remaining backend #1 G1 xfail was first retargeted
from an unsupported function-attribute operation to an actual closure/list
cycle.  The runtime fix then added a narrow explicit-collect tracing sweep
fallback and stale-object-list cleanup after default cycle-collector frees.

This makes the `goal.md` backend #1 verdict command pass green.  It still does
not move `tasksV2.md` backend #1 to production: the default backend retains a
conditional closure-cycle xfail, and backend #1 still needs the separate
production sweep/finalizer audit described in the earlier pacer investigation.
