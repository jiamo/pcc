# pcc ds4 first primitive: f32 tensor copy

Status: `DS4-P3-PRIMITIVE-ORACLE` complete for one bounded primitive.

The selected external oracle is `kernel_cpy_f32_f32` from pinned
`metal/cpy.metal`. pcc does not compile or call that source. It validates the
pin and migrates the operation as its own `pcc_ds4_copy_f32` Kernel IR:

```text
pinned ds4 f32->f32 copy semantics (oracle only)
  -> pcc Kernel IR parallel + copy
  -> canonical plain TIRx freeze
  -> pcc-owned Metal source
  -> pcc-metal runtime-source command buffer
  -> fence completion + f32 readback
  -> independent CPU copy oracle
```

The real device test uses a 3x4 matrix containing positive, negative, zero,
fractional, and larger finite values. Readback matches exactly with maximum
absolute error 0.0; the pcc owner manifest records requested backend and actual
backend as `pcc-metal`, `fallback_used=false`, and releases its allocations.

This proves only the selected contiguous row-major f32 copy primitive on the
host-Python runtime-source Metal route. It does not prove ds4 source execution,
f32/f16 conversion, strided copies, fill, model/KV/cache operators, whole-ds4
execution, pcc1 launcher support, five-GC device parity, or performance parity.
