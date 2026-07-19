# pcc-owned ds4 GPU lifetime mapping

Status: `DS4-P2-GPU-API-MAPPING` complete at lifecycle-mapping level only.

The pinned `ds4_gpu.h` `GPU Tensor and Command Lifetime` section contains 21
function declarations. `pcc.kernel_ir.ds4_gpu_mapping` extracts that bounded
section and requires an exact mapping for every declaration; a new or removed
upstream API fails closed.

The ownership translation is:

```text
ds4_gpu_tensor allocation -> PccBufferHandle storage owner
ds4_gpu_tensor view       -> bounded PccTensorSlice alias
begin/flush/end           -> pcc command lifetime + PccFenceToken
selected readback event   -> event-gated PccTensorReadbackWindow
last tensor alias free    -> PccDeferredFreeQueue behind the last fence
```

The executable adapter is a CPU-only state machine. It proves that base/view
aliases share one pcc buffer, that selected readback is rejected before event
completion, that byte windows are checked, and that the final alias cannot
reclaim storage until its fence completes. Device-only raw host access and
release of an unsubmitted command operand are rejected.

This does **not** import, link, or call ds4; submit a Metal/CUDA/ROCm command;
map ds4 model/cache/operator semantics; produce a device result; or establish
ds4 support. Primitive execution begins in the separate
`DS4-P3-PRIMITIVE-ORACLE` row.
