# Investigation: native file handles are not closed when dropped in strict no-libpython mode

## Status
resolved 2026-05-31 — fixed by giving `PY_TYPE_FILE` a type-specific
deallocator and allocating file wrappers through `pcc_gc_alloc` so backend #4
also treats them as known runtime objects.

## Problem Description
The strict no-libpython file-object path wraps a native `FILE *`. When a write
handle is dropped without an explicit `.close()`, the wrapper must close the
native handle before freeing the object so buffered writes are flushed and the
native resource is released.

Minimal shape:

```python
import gc

PATH = "/tmp/pcc-native-file.txt"

f = open(PATH, "w", encoding="utf-8")
f.write("alpha")
f = None
gc.collect()

with open(PATH, "r", encoding="utf-8") as r:
    print(r.read())
```

Before the fix the program printed an empty line on all five backends. The
wrapper memory was freed, but the underlying `FILE *` was never closed/flushed.

## Root Cause
`py_file_open` allocated `PyFileObject` with raw `malloc`, initialized a
`PY_TYPE_FILE` header, and relied on the generic deallocator. That generic
deallocator only freed wrapper memory. It did not call `py_file_close`, so a
dropped file object leaked the native handle.

After adding a file deallocator, backends #0/#1/#2/#3 passed but backend #4
still failed. `py_decref` intentionally ignores unknown raw pointers under the
relocating collector. Because raw-malloc file wrappers were not registered with
the GC object index, backend #4 skipped the decref/deallocator path entirely.

## Fix
`py_file_open` now allocates file wrappers through `pcc_gc_alloc(...,
PY_TYPE_FILE, 0)` in both the C runtime and pcc-Python mirror. This makes the
wrapper a known runtime object for every backend while preserving backend flags
instead of overwriting the object header manually.

This does not claim native-handle relocation support: `PY_TYPE_FILE` remains
outside the backend #4 relocation-supported tag set. The contract here is
reachability, close/flush lifetime, and safe deallocation of the native handle
wrapper.

`PY_TYPE_FILE` now dispatches to `py_dealloc_file`, which calls
`py_file_close(o)` and then frees wrapper memory via `pcc_gc_free_object_memory`.
The pcc-Python deallocator mirror implements the same dispatch.

The same change also moves binary-mode detection before dropping the temporary
mode string, avoiding a use-after-free in the default-mode path.

## Regression Test
`tests/python/gc_production_contract/test_native_handle_lifetime.py`

The test compiles one strict no-libpython program with the self backend and runs
it under `PCC_GC_BACKEND=0..4`. It checks both:

- dropped unclosed write handle closes/flushes before the file is reopened;
- live write handle survives `gc.collect()`, can write again, and then closes
  explicitly.

## Evidence
```bash
env -u LC_ALL uv run pytest tests/python/gc_production_contract/test_native_handle_lifetime.py -q -n0
# 5 passed

env -u LC_ALL uv run pytest tests/python/gc_production_contract -q -n0
# 100 passed

env -u LC_ALL uv run pytest tests/python/test_native_file_open.py -q -n0
# 3 passed

env -u LC_ALL uv run pytest tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self -q -n0
# 1 passed
```
