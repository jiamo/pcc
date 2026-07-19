# GPU-P0-DLPACK-EXTERNAL-CAPSULE-INTEROP closure evidence

## Outcome

The capsule pointer is now a real classic C-ABI `DLManagedTensor`, not
`id(tensor)` host metadata. `ctypes.Structure` definitions pin the 64-bit ABI
layout for `DLDevice`, `DLDataType`, `DLTensor`, and `DLManagedTensor`, including
the manager context and C deleter callback.

pcc-owned export fills kDLMetal device id, numeric dtype, shape/strides,
`id<MTLBuffer>` data pointer, byte offset, and deleter. Import accepts either a
pcc export or an unregistered foreign `DLManagedTensor`, validates the full
struct, renames `dltensor` to `used_dltensor`, and re-enters as POD
`PccBufferHandle` metadata. Foreign deleters are not called until the supplied
`PccFenceToken` completes. Unconsumed capsule destruction releases the pcc
alias through a completed fence; the destructor uses a raw `PyObject*` address
table and never re-wraps an object during `tp_dealloc`.

Current accepted boundary is kDLMetal, contiguous row-major, zero byte offset,
supported scalar dtype, and default stream. Non-contiguous/offset tensors and
non-default streams fail closed because the current packed-handle/stream ABI
cannot represent them.

External framework, pcc1, and five-GC device-execution claims remain separate
dependency-gated task rows. This environment has no torch, MLX, or NumPy
installation, so no framework claim is made.

## Gates

- Focused pcc/foreign ABI, destructor, and stream regressions — **4 passed in
  0.25s**.
- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_dlpack_ownership.py tests/kernel/test_hmm_fence.py`
  — **21 passed in 0.27s**.
- Module `py_compile` for `metal_dlpack.py` and the Kernel IR exports — **exit
  0**.

No compiler bootstrap or five-GC matrix was run; those are explicitly owned by
the new higher-level DLPack cards.

