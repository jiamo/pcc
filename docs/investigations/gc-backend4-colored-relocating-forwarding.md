# Investigation: Backend 4 colored relocating forwarding

## Status
resolved

## Problem Description
Implement the next Tier 5 GC backend productionization slice from `goal.md`:
Backend #4 has read-barrier counters and relocation-candidate clearing, but no
forwarding table, relocation set, real movement, or stable identity plan.  The
first safe slice should prove the read barrier can follow a forwarding entry and
heal the loaded slot without claiming full ZGC-style object movement.

## Repro
Run the focused backend #4 gate:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_relocating.py -q -n0
```

Expected current failure: the compiled no-libpython probe cannot link or pass
because backend #4 has no forwarding-table install/read-barrier forwarding
surface.

## Test [CONFIRMED]
The focused gate has been observed failing:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_relocating.py -q -n0
# 2 failed in 1.07s
```

Observed failure:

- both no-libpython probes fail during clang link with
  `_pcc_gc_install_forwarding` undefined, proving backend #4 has no
  forwarding-table install/read-barrier forwarding surface yet.

## Proposals
- No.1 Add side-table forwarding and read-barrier slot healing     [CONFIRMED]

## No.1 Add side-table forwarding and read-barrier slot healing
### Code Change
The landed slice:

- add backend #4 forwarding telemetry for forwarding installs, read-barrier
  forwards, and pinned-object forwarding rejections;
- add a side-table forwarding map from old object pointer to replacement object
  pointer;
- reject forwarding installation for pinned objects;
- make `pcc_gc_load_ptr()` resolve forwarded objects under backend #4 and heal
  the loaded slot with correct reference ownership;
- remove forwarding entries when the old object is freed or when switching away
  from backend #4;
- mirror the forwarding table and counters in the pcc-Python runtime port.

Touched files:

- `pcc/py_runtime/src/py_gc_backend.c`
- `pcc/py_runtime/src/py_obj.c`
- `pcc/py_runtime/src/py_internal.h`
- `pcc/py_runtime/include/py_runtime.h`
- `pcc/py_runtime/py/py_gc_backend.py`
- `pcc/py_runtime/py/py_obj.py`
- `pcc/py_runtime/py/py_substrate.py`
- `tests/test_gc_backend_relocating.py`
- `tests/test_gc_abstraction_surface.py`

### CONFIRMED
The focused backend #4 forwarding gate now passes:

```bash
/opt/homebrew/bin/timeout 240s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_relocating.py -q -n0
# 2 passed in 1.49s
```

Both runtime archives rebuild:

```bash
/opt/homebrew/bin/timeout 420s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" \
  make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# success

/opt/homebrew/bin/timeout 180s env -u LC_ALL \
  make -B -C pcc/py_runtime libpy_runtime.a
# success; existing unused-function warnings remain for tracing helpers
```

The forwarding gate and public GC-counter surface pass together:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py \
  -q -n0
# 16 passed in 6.86s
```

Existing backend #4 tracing compatibility checks pass:

```bash
/opt/homebrew/bin/timeout 180s env -u LC_ALL uv run pytest \
  'tests/test_gc_effectiveness.py::test_non_default_backends_collect_list_cycle[4]' \
  'tests/test_gc_effectiveness.py::test_non_default_backends_collect_cross_type_cycle[4]' \
  -q -n0
# 2 passed in 1.44s

/opt/homebrew/bin/timeout 240s env -u LC_ALL PCC_GC_BACKEND=4 uv run pytest \
  tests/test_gc_backend_relocating.py \
  tests/test_gc_backend_incremental.py \
  tests/test_gc_backend_generational.py \
  -q -n0
# 6 passed in 4.85s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/test_gc_backend_*.py -q -n0
# 10 passed in 7.36s
```

## Report (only when the investigation is closing)
No.1 landed.  Backend #4 now has a concrete forwarding/read-barrier surface:
`pcc_gc_install_forwarding(old, new)` records a side-table forwarding entry,
pinned objects reject forwarding, `pcc_gc_load_ptr()` follows the forwarding
entry under backend #4, heals the slot with correct refcount ownership, and
reports forwarding telemetry.

This is still not full ZGC-style relocation.  Objects are not copied by the
collector, there is no page/relocation-set selection, and stable `id()`
indirection is still unresolved.  Therefore `tasksV2.md` backend #4 must remain
`research partial`.
