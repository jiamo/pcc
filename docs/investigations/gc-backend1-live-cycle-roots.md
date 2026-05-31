# Investigation: Backend #1 collects function-local live cycles

## Status
resolved

## Problem Description
Under `PCC_GC_BACKEND=1`, an explicit `gc.collect()` can break a cycle that is still reachable from function locals:

```python
import gc

class N:
    pass

def main() -> None:
    a = N()
    b = N()
    a.peer = b
    b.peer = a
    n = gc.collect()
    print(a.peer is b)
    print(b.peer is a)
```

The default backend preserves both dynamic `peer` attributes, but Backend #1 raises `AttributeError: peer` after collection.

## Repro
```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 240s \
  uv run pytest tests/test_gc_effectiveness.py::test_collect_does_not_break_live_cycle -q -n0
```

Expected: one passing test.

Observed: stdout is empty because the compiled probe raises `AttributeError: peer`; pytest sees `['']` instead of `['True', 'True']`.

## Test [CONFIRMED]
`tests/test_gc_effectiveness.py::test_collect_does_not_break_live_cycle` is the gate. It was run under Backend #1 and failed with the output mismatch above.

Manual rerun of pytest's generated `prog.out` confirmed the runtime exception:

```text
AttributeError: peer
```

## Proposals
- No.1 Fix Backend #1 root/referent marking for function-local live cycles [CONFIRMED]

## No.1 Fix Backend #1 root/referent marking for function-local live cycles
### Code Change
Landed changes:

- `pcc/py_runtime/src/py_gc_backend.c`: `pcc_gc_trace_referents()` now traces `PY_TYPE_CLASS` bases/MRO/method functions and the instance `inst->cls` edge.
- `pcc/py_runtime/py/py_gc_backend.py`: the pcc-Python Backend #1/#3 mirror now marks/promotes class children and instance `cls`.
- `pcc/py_frontend/codegen/layer1.py`: generated module roots now include class globals (`.class.<module>.<Name>`) as well as ordinary object globals.
- `pcc/py_runtime/src/py_obj.c` and `pcc/py_runtime/py/py_obj.py`: `pcc_gc_collect()` no longer adds mark-step work units to the Python-visible collected-object count.
- `tests/test_gc_abstraction_surface.py`: added a low-level gate that a rooted instance keeps its class child black and non-candidate under Backend #1.

Pre-edit observations:

- Emitted IR contains `pcc_gc_frame_enter` for `a.addr`, `b.addr`, and `n.addr` in `user_prog_main`.
- The normal path does not call `pcc_gc_frame_leave` before `pcc_gc_collect`; earlier leave calls are on the shared `err.exit` block.
- `py_instance_setattr` stores dynamic attributes in `inst->fields[n_fields]`, the hidden dynamic-attribute dict slot.
- Both Backend #0 (`py_obj_gc.c`) and Backend #1 (`py_gc_backend.c`) visit that dynamic dict slot when `PY_CLASS_FLAG_SLOTS_ONLY` is not set.

### CONFIRMED
The root cause was class reachability, not the dynamic-attribute dict itself. A live instance did not trace its `cls` pointer, and generated class globals were not registered as module roots. Backend #1 could therefore sweep `class N`; subsequent instance attribute operations failed because `instance_pointer_is_instance()` requires `inst->cls` to still be a valid `PY_TYPE_CLASS`.

Confirmed gates:

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 180s \
  uv run pytest tests/test_gc_effectiveness.py::test_collect_does_not_break_live_cycle -q -n0
```

Observed: `1 passed`.

```bash
env -u LC_ALL PCC_GC_BACKEND=1 /opt/homebrew/bin/timeout 360s \
  uv run pytest tests/test_gc_effectiveness.py -q -n0
```

Observed: `27 passed`.

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 300s \
  uv run pytest tests/test_gc_abstraction_surface.py -q -n0
```

Observed: `15 passed`.

## Report (only when the investigation is closing)
No.1 landed. Backend #1 now preserves function-local live cycles, keeps module-level classes alive across repeated explicit collections, and reports `gc.collect()` counts as reclaimed objects rather than tracing work. Remaining Backend #1 production gaps are tracked in `goal.md` No.6 rather than this investigation: callback remove semantics, resurrection attrs/cache cases, and performance/long-chain release.
