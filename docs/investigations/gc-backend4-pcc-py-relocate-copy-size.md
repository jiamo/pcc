# Investigation: Backend #4 pcc-Python relocate copy size validation

## Status
resolved

## Problem Description
pcc-Python runtime mirror `pcc_gc_relocate_copy()` should reject copy sizes
larger than the recorded source-object allocation, matching the C runtime.
The current mirror tracks only object pointers in `pcc_gc_object_head`, so an
oversized relocation copy can proceed instead of returning NULL.

## Repro
Run:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_runtime_substrate_spike.py::test_pcc_python_relocate_copy_rejects_oversized_copy' -q -n0
```

Expected current failure before the fix:

```text
selected=1
oversize_null=0
```

The correct behavior is:

```text
selected=1
oversize_null=1
```

## Test [CONFIRMED]
`tests/test_runtime_substrate_spike.py::test_pcc_python_relocate_copy_rejects_oversized_copy`
failed before the fix with:

```bash
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_runtime_substrate_spike.py::test_pcc_python_relocate_copy_rejects_oversized_copy' -q -n0
```

Observed result:

```text
selected=1
oversize_null=0
```

## Proposals
- No.1 Add allocation size to pcc-Python object nodes     [CONFIRMED]

## No.1 Add allocation size to pcc-Python object nodes
### Code Change
Extend the pcc-Python mirror object-list node from `(obj, next)` to
`(obj, size, next)`, use the stored size for live-byte accounting on free, and
reject `pcc_gc_relocate_copy(from, size)` when `size` exceeds the recorded
allocation size.
### CONFIRMED
Implemented in `pcc/py_runtime/py/py_gc_backend.py`.

The pcc-Python mirror now stores object-list nodes as:

```text
offset 0:  PyObject *obj
offset 8:  int64_t size
offset 16: PccGcObjectNode *next
```

All object-list traversal uses helpers for the new `next` offset, freeing uses
the per-object recorded size for live-byte subtraction, and
`pcc_gc_relocate_copy()` rejects unknown or oversized source copies before
allocating the destination object.

Confirmed with:

```bash
PATH="$PWD/.venv/bin:$PATH" env -u LC_ALL /opt/homebrew/bin/timeout 300s make -B -C pcc/py_runtime libpy_runtime_pcc_py.a
env -u LC_ALL /opt/homebrew/bin/timeout 120s uv run pytest 'tests/test_runtime_substrate_spike.py::test_pcc_python_relocate_copy_rejects_oversized_copy' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_runtime_substrate_spike.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest 'tests/test_py_multi_file_bootstrap_shim.py::MultiFileBootstrapShimTests::test_py_gc_backend_runtime_file_compiles_without_libpython_fallback' -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_backend_relocating.py tests/test_gc_abstraction_surface.py -q -n0
PCC_GC_BACKEND=4 env -u LC_ALL /opt/homebrew/bin/timeout 180s uv run pytest tests/test_gc_g1_cycle_collector.py tests/test_gc_g2_finalizers.py -q -n0
env -u LC_ALL /opt/homebrew/bin/timeout 300s uv run pytest tests/test_gc_*.py -q -n0
```

Observed results:

```text
libpy_runtime_pcc_py.a rebuilt
1 passed in 0.49s
30 passed in 7.62s
1 passed in 1.43s
19 passed in 8.32s
9 passed, 1 xfailed, 5 xpassed in 8.75s
142 passed, 25 xfailed, 15 xpassed in 151.47s
```

Formatting note: `env -u LC_ALL uv run black pcc/py_runtime/py/py_gc_backend.py tests/test_runtime_substrate_spike.py`
could not run because this environment does not expose `black` in the active
pyenv/uv path.

## Report (only when the investigation is closing)
No.1 landed. The change preserves the existing object-list side table while
adding the allocation-size metadata needed for pcc-Python parity with the C
runtime. This is smaller than changing the public allocation ABI and directly
guards the only unsafe behavior observed in the repro: copying more bytes than
the source allocation recorded.

Backend #4 is still a research relocating backend. This patch only makes the
restricted copy primitive size-safe in the pcc-Python mirror; it does not add
page evacuation, container/reference rewriting, or fragmentation control.
