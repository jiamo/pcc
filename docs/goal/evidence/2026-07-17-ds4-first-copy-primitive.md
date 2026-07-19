# DS4-P3 first primitive evidence — 2026-07-17

Modes:

- external oracle: pinned source inspection only;
- semantic owner/codegen/runtime owner: pcc Kernel IR / pcc-metal;
- execution: host-Python runtime-source Metal, no fallback;
- result: real command buffer + completed fence + device readback.

Pinned oracle:

- commit: `80ebbc396aee40eedc1d829222f3362d10fa4c6c`
- path: `metal/cpy.metal`
- SHA-256:
  `c55ac67377adf3f38b5e40f0dee3008e56901854c41f97640c4b1712bf33f77c`
- selected symbol: `kernel_cpy_f32_f32`

Proven slice:

- pcc-owned `pcc_ds4_copy_f32` Kernel IR contains bounded parallel + copy;
- TIRx freeze contains `tir.parallel_for` + `tir.copy_loop`;
- emitted pcc Metal source contains the typed f32 copy kernel and does not copy
  the ds4 symbol/source;
- real 3x4 device readback equals the independent CPU oracle exactly
  (`max_abs_error=0.0`), fence completes, allocations release, and the owner
  manifest records `pcc-metal -> pcc-metal`, no fallback.

Required gate:

```text
gtimeout 420s env -u LC_ALL uv run pytest -q -n0 tests/ds4/test_ds4_primitive_oracle.py tests/kernel/test_metal_source_runtime.py -rs
14 passed in 1.75s
```

There were no skips. This does not prove ds4 execution or support beyond this
contiguous f32 copy primitive, nor pcc1/five-GC/performance parity.
