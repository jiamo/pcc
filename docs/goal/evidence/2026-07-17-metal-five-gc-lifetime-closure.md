# GPU-P0-METAL-5GC-LIFETIME-REAL closure evidence

The finite claim is real Metal buffer/fence lifetime parity across
`PCC_GC_BACKEND=0..4`, not arbitrary TileLang program coverage. Existing
Level-6 evidence covers many runtime-source and prebuilt-metallib workloads.
The current canonical strict rerun used the pcc1 no-libpython static-roller
metallib workload: every runtime process verified its backend marker, launched
real Metal buffers, matched the CPU result, and released buffers only after the
synchronous fence completed.

The newly shared opaque external-resource registry independently exercises the
same buffer/fence retain, completion, and exact-once release seam under all five
backends. External framework interoperability and individual TileLang lowering
features remain separate task claims; they do not make this finite lifetime
claim weak.

Gates:

- strict pcc1/Metal canonical five-GC workload — **1 passed in 2.27s**;
- external-resource GC0..4 files — **5 passed in 0.10s** (recorded in
  `2026-07-17-gpu-gc-runtime-external-resource-closure.md`).

No full bootstrap or full hardware file was run.

