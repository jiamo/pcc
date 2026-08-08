# Investigation: freestanding GC telemetry counter ABI drift

## Status
resolved

## Problem Description

`LIBC-P2-FREESTANDING-GC` is moving the production telemetry dispatcher from
the C oracle into strict freestanding pcc-Python.  During the cross-object ABI
audit, the pcc-Python dispatcher was found to use the current pause counters at
32..37 and then reuse 32..37 for scheduler and backend-4 counters.  The public
`py_runtime.h` ABI assigns scheduler roots to 38 and continues through the
evacuation-page counter at 115.  Consequently the duplicate branches are
unreachable and most backend-4 metrics are shifted or missing.

This is separate from whether the strict module compiles: a zero-fallback
object can still implement the wrong public metric ABI.

## Repro

Run the focused C-oracle differential after its tracer test is added:

```bash
gtimeout 120s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/test_freestanding_gc_telemetry.py \
  -k counter_abi_matches_c_oracle
```

Expected pre-fix result: the first mismatch is metric 38, where the C oracle
returns the scheduler-root provider value and the pcc-Python dispatcher returns
the old evacuation-candidate provider value.

## Test [CONFIRMED]

`tests/python/test_freestanding_gc_telemetry.py` extracts the C dispatcher,
links both implementations against identical deterministic provider/state
stubs, and compares every metric from -1 through 116.  Before the fix the
first mismatch was deterministic:

```text
metric 38: expected 10082, got 10003
```

After the fix the differential passes through both the LLVM emitter and the
self emitter.  The same test file also proves the exact cross-object undefined
set and the five-symbol public export set, then links the production runtime
archive and executes its telemetry surface under GC0..4.

## Proposals

- No.1 Align every pcc-Python branch with the public C counter enum [CONFIRMED]
- No.2 Preserve the old duplicate numeric mapping for compatibility [DENIED]

## No.1 Align every pcc-Python branch with the public C counter enum

### Code Change

The dispatcher now uses the public values 38..115 for scheduler/backend-4
metrics.  The fix changes the literal branch values directly rather than
adding a translation offset, so the pcc-Python implementation and C oracle
consume the same ABI.  The module is also strict freestanding pcc-Python and
uses ordering-explicit atomic loads.

### CONFIRMED

The complete focused file passes with both emitters and the production archive:

```text
4 passed in 51.49s
```

A fresh no-libpython/self pcc1 compiled the module to LLVM IR; lowering that IR
to an object produced exactly the five public exports and only its declared raw
GC provider/state imports.  There were no managed exception, libpython, or
libc object dependencies.

## No.2 Preserve the old duplicate numeric mapping for compatibility

### DENIED

The public ABI is the enum in `pcc/py_runtime/include/py_runtime.h`, and the C
production oracle already implements it.  Keeping duplicate 32..37 branches
would retain unreachable code and make a production-ownership switch change
observable metric meanings.

## Report

The production telemetry dispatcher is now owned by strict freestanding
pcc-Python and matches the public C oracle for all valid and invalid metric
values covered by the ABI.  This closes telemetry dispatch only; it does not
claim that `py_obj_gc.py` or the remaining collector implementation has already
migrated out of the C/managed-runtime closure.
