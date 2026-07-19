# GPU GC External Resource Seam

Date: 2026-07-06

Task: `GPU-P0-GC-EXTERNAL-RESOURCE-SEAM`

## What Changed

- Added `pcc/gpu_gc/external_resource.py`.
- Exported the seam from `pcc.gpu_gc`.
- Added `tests/gpu_gc/test_external_resource.py`.
- Added explicit five-backend entry tests:
  - `tests/python/gc/test_gpu_external_resource_gc0.py`
  - `tests/python/gc/test_gpu_external_resource_gc1.py`
  - `tests/python/gc/test_gpu_external_resource_gc2.py`
  - `tests/python/gc/test_gpu_external_resource_gc3.py`
  - `tests/python/gc/test_gpu_external_resource_gc4.py`

## Contract

The seam is CPU-only but production-shaped:

- external resources are registered under a selected GC backend `0..4`;
- resource records carry `PccBufferHandle` metadata, a native handle integer,
  and a release callback;
- records reject PyObject payloads at the device frontier;
- retain/release is backend-neutral;
- final release moves the resource to `PENDING_RELEASE` and records a
  `PccFenceToken`;
- `poll()` calls the native release callback only after the fence completes;
- released resources mark the `PccBufferHandle` as `FREED`;
- all records report `whole_program_gpu=false`.

## Validation

```text
env -u LC_ALL uv run python -m py_compile \
  pcc/gpu_gc/external_resource.py \
  pcc/gpu_gc/__init__.py \
  tests/gpu_gc/test_external_resource.py \
  tests/python/gc/gpu_external_resource_contract.py \
  tests/python/gc/test_gpu_external_resource_gc0.py \
  tests/python/gc/test_gpu_external_resource_gc1.py \
  tests/python/gc/test_gpu_external_resource_gc2.py \
  tests/python/gc/test_gpu_external_resource_gc3.py \
  tests/python/gc/test_gpu_external_resource_gc4.py
passed

env -u LC_ALL uv run pytest -q -n0 tests/gpu_gc/test_external_resource.py
4 passed in 0.07s

env -u LC_ALL uv run pytest -q -n0 \
  tests/python/gc/test_gpu_external_resource_gc0.py \
  tests/python/gc/test_gpu_external_resource_gc1.py \
  tests/python/gc/test_gpu_external_resource_gc2.py \
  tests/python/gc/test_gpu_external_resource_gc3.py \
  tests/python/gc/test_gpu_external_resource_gc4.py
5 passed in 0.09s

env -u LC_ALL uv run pytest -q -n0 tests/gpu_gc
65 passed in 0.25s
```

## Remaining Boundary

This is `DONE_WEAK`.

Open work:

- The seam is not wired into the C runtime or pcc-Python runtime GC backends.
- No real Metal/CUDA/ROCm driver callback is registered by production runtime
  code yet.
- No GPU kernel scans object graphs.
- No pcc1 no-libpython GPU lifetime proof yet.
- No full `PCC_GC_BACKEND=0..4` runtime process executing GPU work yet.
