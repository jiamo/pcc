# Metal DLPack PyCapsule Interop

Date: 2026-07-06

Task: `GPU-P0-DLPACK-EXTERNAL-CAPSULE-INTEROP`

## What Changed

- Extended `pcc/kernel_ir/metal_dlpack.py` with a real CPython `PyCapsule`
  boundary for the current pcc-owned Metal DLPack-shaped tensor owner.
- Added `export_metal_dlpack_py_capsule(...)`.
- Added `import_metal_dlpack_py_capsule(...)`.
- Added capsule result records:
  - `MetalDlpackCapsuleExport`
  - `ImportedMetalDlpackCapsule`
- Exported the new API from `pcc.kernel_ir`.
- Added regression coverage to `tests/kernel/test_metal_dlpack_ownership.py`.

## Contract

The new bridge uses CPython's real `PyCapsule_*` C API through `ctypes.pythonapi`.

It proves:

- exported capsules are named `dltensor`;
- import consumes the capsule once and renames it to `used_dltensor`;
- a second import is rejected;
- imported tensors re-enter pcc as `PccBufferHandle` metadata;
- descriptors still report `descriptor_contains_pyobject=false`;
- deleter/reclaim still requires a `PccFenceToken`;
- non-default stream values are rejected with an explicit diagnostic instead of
  silently claiming stream synchronization.

## Validation

```text
env -u LC_ALL uv run python -m py_compile \
  pcc/kernel_ir/metal_dlpack.py \
  pcc/kernel_ir/__init__.py \
  tests/kernel/test_metal_dlpack_ownership.py
passed

env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_dlpack_ownership.py \
  tests/kernel/test_hmm_fence.py
18 passed in 0.22s

env -u LC_ALL uv run pytest -q -n0 \
  tests/kernel/test_metal_dlpack_ownership.py \
  tests/kernel/test_hmm_fence.py \
  tests/kernel/test_metal_buffer.py \
  tests/kernel/test_metal_tensor.py
30 passed in 0.31s
```

## Remaining Boundary

This is `DONE_WEAK`.

Open work:

- No torch/MLX/MPS round-trip has been run.
- Non-default stream synchronization is explicitly rejected.
- The capsule pointer is pcc-owned host metadata for the current ownership
  state machine, not a full external `DLManagedTensor` C struct interchange.
- No pcc1 no-libpython DLPack capsule proof yet.
- No five-GC runtime process executes real GPU work through this capsule path.
