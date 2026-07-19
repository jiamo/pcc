# GPU-P0-DLPACK-FRAMEWORK-MPS-ROUNDTRIP closure evidence

## Outcome

MLX 0.32.0 consumed a pcc-owned contiguous `f32[2,3]` kDLMetal tensor through
the Python DLPack protocol with `copy=False` on a real Apple Metal device. The
gate proved device `(kDLMetal, 0)`, shape, dtype, input values, and MLX device
arithmetic result equality. Exporting the MLX view back through DLPack produced
the same native `id<MTLBuffer>` pointer, so this was not a host copy.

The pcc producer is one-shot and fail-closed for versioned DLPack, device-copy,
device-remap, and non-default-stream requests. MLX's deleter drops the pcc alias
but native buffer reclamation remains pending until the explicit
`PccFenceToken` completes. The capsule destructor now recognizes a consumer's
`used_dltensor` rename and does not prematurely or twice invoke the managed
tensor deleter.

This proves the focused pcc/MLX Metal tensor interoperability and ownership
boundary. MLX owns the arithmetic execution; it is not a whole-program pcc GPU
execution claim.

## Gates

- `env -u LC_ALL uv run pytest -q -n0 tests/kernel/test_metal_dlpack_ownership.py`
  — **10 passed in 0.24s**.
- `gtimeout 300s env -u LC_ALL PCC_GPU_HARDWARE_STRICT=1 uv run --with mlx pytest -q -n0 tests/gpu_hardware/test_metal_dlpack_framework.py -rs`
  — **1 passed in 0.68s**.

No GCC, bootstrap, or five-GC matrix was run.
