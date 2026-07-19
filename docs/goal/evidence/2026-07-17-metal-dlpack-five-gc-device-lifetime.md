# GPU-P0-DLPACK-5GC-DEVICE-LIFETIME closure evidence

The same pcc1/no-libpython classic DLPack capsule workload ran with
`PCC_GC_BACKEND=0`, `1`, `2`, `3`, and `4`. Each runtime process verified its
own backend marker, consumed and renamed the capsule once, projected the
descriptor into the POD packed handle, launched a real prebuilt Metal copy
kernel with that native handle, and matched all six f32 device-result bits.

For every backend, the managed deleter left the source MTLBuffer live/readable
while its pcc fence was incomplete. Fence completion plus the common registry
poll then invoked the Metal driver release exactly once and restored active and
pending counts to baseline. The produced executables linked no libpython.

Gate:

- strict focused GC0..4 pcc1 DLPack/Metal workload — **1 passed in 8.84s**.

No bootstrap chain, full GPU hardware suite, or GCC suite was run.

