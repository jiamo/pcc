# Investigation: Backend #4 needs a copy relocation primitive

## Status
resolved

## Problem Description
Backend #4 now has forwarding, stable IDs, and an object-level relocation set,
but it still has no movement primitive.  `goal.md` calls out true movement:
copy an object to new storage and leave forwarding from the old address.  The
safe next slice is a restricted copy relocation primitive for simple objects
with no child references; page selection and container pointer fixing remain
separate follow-ups.

## Repro
Run the focused backend #4 copy-relocation gate:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py::test_colored_relocating_copy_moves_selected_simple_object -q -n0
```

Expected before the fix: the probe fails to link because
`pcc_gc_relocate_copy` is not provided by the runtime.

## Test [CONFIRMED]
`tests/test_gc_backend_relocating.py::test_colored_relocating_copy_moves_selected_simple_object`

Observed before the fix on 2026-05-07:

```text
ld: Undefined symbols:
  _pcc_gc_relocate_copy, referenced from:
      _user_probe_main in probe-dffb8b.o
pcc.py_frontend.pipeline.PyPipelineError: clang link failed (exit 1)
```

## Proposals
- No.1 Add restricted copy relocation for selected simple objects     [CONFIRMED]

## No.1 Add restricted copy relocation for selected simple objects
### Code Change
Add `pcc_gc_relocate_copy(obj, size)` for backend #4.  It should require the
object to be known, unpinned, already in the relocation set, and of a simple
no-child-reference type.  It copies the bytes to a new GC allocation, resets
the new object's refcount, preserves the old stable ID on the new object by
installing forwarding, and lets the existing load barrier heal slots to the
new address.  Mirror the ABI in the pcc-Python runtime port.
### CONFIRMED
Implemented `pcc_gc_relocate_copy(obj, size)` in
`pcc/py_runtime/src/py_gc_backend.c` and mirrored it in
`pcc/py_runtime/py/py_gc_backend.py`.  The helper is deliberately restricted:
backend #4 only, selected relocation-set members only, unpinned objects only,
and simple no-child-reference type tags only.  It copies bytes to a new GC
allocation, resets the new refcount, clears the candidate bit on the copy, and
uses `pcc_gc_install_forwarding()` so the existing load barrier resolves old
slots to the copied object while preserving stable object ID.

Observed verification:

```bash
/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py::test_colored_relocating_copy_moves_selected_simple_object -q -n0
# 1 passed in 0.85s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py -q -n0
# 19 passed in 8.20s

/opt/homebrew/bin/timeout 180s env -u LC_ALL make -B -C pcc/py_runtime libpy_runtime.a
# passed; existing unused-function warning in py_gc_backend.c remains

/opt/homebrew/bin/timeout 420s env -u LC_ALL PATH="$PWD/.venv/bin:$PATH" make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
# passed

/opt/homebrew/bin/timeout 120s env -u LC_ALL uv run pytest tests/test_gc_threading_substrate.py::test_tracing_gc_finalizer_handles_thread_objects_and_refcount_side_table -q -n0
# 1 passed in 0.16s

/opt/homebrew/bin/timeout 420s env -u LC_ALL uv run pytest tests/test_gc_threading_substrate.py tests/test_gc_backend_*.py tests/test_gc_effectiveness.py -q -n0
# 49 passed, 3 xfailed, 3 xpassed in 42.02s

/opt/homebrew/bin/timeout 420s env -u LC_ALL PCC_GC_BACKEND=4 uv run pytest tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0 -rxX
# 9 passed, 1 xfailed, 5 xpassed in 8.91s
```

`nm` confirms both runtime archives export `pcc_gc_relocate_copy`.  Black was
attempted but unavailable in the active environment: `pyenv: black: command
not found`.

## Report (only when the investigation is closing)
No.1 landed.  Backend #4 now has a minimal movement primitive: selected simple
objects can be copied to new storage, the old object receives a forwarding
entry, stable ID follows the copy, and `pcc_gc_load_ptr()` heals an old slot to
the copied object.

This is still not a full ZGC relocate phase.  The helper does not move
containers or update arbitrary interior references, does not allocate from
page-specific relocation storage, and does not choose evacuation pages by
fragmentation.  `tasksV2.md` backend #4 remains `research partial`.
