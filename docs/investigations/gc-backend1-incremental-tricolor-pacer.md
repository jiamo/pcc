# Investigation: Backend 1 incremental tricolor pacer

## Status
resolved

## Problem Description
Implement the first Tier 5 GC backend productionization slice from `goal.md`:
backend #1 already has tricolor flags, frame roots, write barriers, bounded
mark work, and sweep candidates, but it lacks a Lua-style allocation debt
pacer.  The required first slice is debt accounting in `pcc_gc_note_alloc`,
`gcpause` / `gcstepmul` tuning through runtime configuration, and telemetry
that can gate bounded pauses.

## Repro
Run the smallest new gate:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_incremental.py -q -n0
```

Expected before the fix: the new gate fails because `PCC_GC_BACKEND` is not
honored as the runtime default, backend #1 still does an allocation-time
`pcc_gc_step(0)` telemetry tick instead of debt-paced real work, and
`pcc_gc_telemetry(7)` is not a max-pause metric.

## Test [CONFIRMED]
`tests/test_gc_backend_incremental.py` is the focused gate for this slice.
The failing baseline was observed with:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_incremental.py -q -n0
# 2 failed in 1.58s
```

Observed failures:

- runtime default backend stayed `0` under `PCC_GC_BACKEND=1`;
- `pcc_gc_telemetry(7)` returned an invalid/negative value instead of a
  max-pause counter;
- allocation pressure under backend #1 did not produce debt-triggered work.

## Proposals
- No.1 Add backend #1 allocation debt pacer and pause telemetry     [CONFIRMED]

## No.1 Add backend #1 allocation debt pacer and pause telemetry
### Code Change
The landed slice:

- add backend runtime configuration from `PCC_GC_BACKEND`, `PCC_GC_PAUSE`,
  `PCC_GC_STEPMUL` / `PCC_GC_STEP_MUL`, and `PCC_GC_DEBT_THRESHOLD`;
- track backend #1 allocation debt in `pcc_gc_note_alloc(size)`;
- trigger real bounded `pcc_gc_step()` work only after debt crosses the
  threshold;
- remove the allocation-time `pcc_gc_step(0)` fake tick from `pcc_gc_alloc`;
- expose `PCC_GC_COUNTER_DEBT_BYTES` and `PCC_GC_COUNTER_MAX_PAUSE_US`.

Touched files:

- `pcc/py_runtime/src/py_gc_backend.c`
- `pcc/py_runtime/src/py_obj.c`
- `pcc/py_runtime/include/py_runtime.h`
- `pcc/py_runtime/py/py_gc_backend.py`
- `pcc/py_runtime/py/py_obj.py`
- `pcc/py_runtime/py/py_substrate.py`
- `tests/test_gc_backend_incremental.py`
- `tests/test_gc_abstraction_surface.py`

### CONFIRMED
The focused backend #1 pacer gate now passes:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_incremental.py -q -n0
# 2 passed in 24.99s
```

The updated GC abstraction surface also passes:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_incremental.py tests/test_gc_abstraction_surface.py -q -n0
# 16 passed in 28.91s
```

The pcc-Python runtime mirror still rebuilds:

```bash
/opt/homebrew/bin/timeout 420s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" \
  make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# success
```

The C runtime archive rebuilds:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL \
  make -B -C pcc/py_runtime libpy_runtime.a
# success; existing unused-function warnings remain for tracing helpers
```

The existing threading/GC substrate gate passes:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_threading_substrate.py -q -n0
# 12 passed in 2.47s
```

The `goal.md` backend #1 verdict command exits successfully, but it is not a
claim that backend #1 is fully production:

```bash
/opt/homebrew/bin/timeout 420s env -u LC_ALL PCC_GC_BACKEND=1 uv run pytest \
  -n0 tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q
# 9 passed, 1 xfailed, 5 xpassed in 8.73s
```

## Report (only when the investigation is closing)
No.1 landed.  Backend #1 now has the first Lua-style pacer slice: runtime env
selection, allocation debt, configurable debt threshold / pause / step
multiplier, bounded automatic work, and max-pause telemetry.  This does not
move `tasksV2.md` backend #1 from `partial` to `production`; one G1 xfail still
remains in the verdict command, and the collector still needs a separate
production sweep/finalizer audit before that row can honestly change.
