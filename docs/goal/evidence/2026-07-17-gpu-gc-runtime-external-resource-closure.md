# GPU GC Runtime External Resource Closure

Date: 2026-07-17

Task: `GPU-P0-GC-EXTERNAL-RESOURCE-SEAM`

## Closed Claim

GPU buffers and fences now use one production runtime registry shared by GC
backends 0..4 and by the C-authored and pcc-Python-authored runtimes.

- `pcc_gc_external_resource.c` owns opaque native handles, retain counts,
  completion state, and release callbacks in the C-level kernel.
- Its records contain no `PyObject` fields. GPU/device memory is not scanned,
  traced, or rewritten by any collector.
- Final host release and fence completion are both required before release.
- Ready records are detached under the registry lock, then the foreign driver
  callback runs outside the lock and at most once.
- The zero-ready poll path is one atomic load. C `py_obj.c` and pcc-Python
  `py_obj.py` poll after runtime safepoints and after collection, with tracing
  callbacks delayed until after the world resumes.
- `pcc_gc_external_metal_buffer_register(...)` installs the real
  `pcc_metal_buffer_runtime_release_prebuilt(...)` driver adapter. The focused
  probe loads a fake Metal driver library through that production `dlopen` /
  `dlsym` path and observes its distinct return code.
- The Makefile links the same C-kernel object into the C, pcc-emitted C, and
  pcc-Python runtime archives. The changed pcc-Python `py_obj.py` module also
  compiles independently under `--python-library`.

Existing pcc1/real-Metal lifetime scope remains owned by the dedicated
`GPU-P0-METAL-PCC1-LAUNCH-REAL` and `GPU-P0-METAL-5GC-LIFETIME-REAL` rows; this
row claims the generic runtime seam, not arbitrary GPU kernels or whole-program
GPU execution.

## Validation

```text
gtimeout 240s env -u LC_ALL uv run pytest -q -n0 \
  tests/gpu_gc/test_runtime_external_resource.py
2 passed in 6.98s

gtimeout 120s env -u LC_ALL uv run pcc --python-library \
  --emit-llvm=/tmp/pcc_py_obj_external_resource.ll \
  pcc/py_runtime/py/py_obj.py
exit 0 in 0.49s

gtimeout 240s env -u LC_ALL uv run pytest -q -n0 tests/gpu_gc
67 passed in 7.18s

gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_gpu_external_resource_gc0.py \
  tests/python/gc/test_gpu_external_resource_gc1.py \
  tests/python/gc/test_gpu_external_resource_gc2.py \
  tests/python/gc/test_gpu_external_resource_gc3.py \
  tests/python/gc/test_gpu_external_resource_gc4.py
5 passed in 0.10s
```

