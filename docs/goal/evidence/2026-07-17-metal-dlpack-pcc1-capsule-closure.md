# GPU-P0-DLPACK-PCC1-CAPSULE closure evidence

The C-level ABI kernel now owns a classic 64-bit `DLManagedTensor` kDLMetal
producer/consumer bridge. It uses the existing generic no-libpython
`PyCapsule_*` shim, not a compiler or framework special case. Consumption
renames `dltensor` to `used_dltensor` exactly once and projects the descriptor
into a fixed 120-byte `PccDlpackBufferHandlePacket` containing only native
handle/resource id/nbytes/shape/device/dtype POD fields.

A current pcc1 compiled and ran the producer/consumer with
`--python-libpython=off`; both pcc1 and the output executable passed dynamic
link audits. On a real Metal buffer the managed deleter moved ownership into
the shared external-resource pending queue, the buffer remained readable before
fence completion, and the driver release occurred exactly once after completion.

Gates:

- focused C helper compile with ABI static assertions — **passed**;
- incremental pcc1 runtime archive update — **passed in 3.3s**;
- strict pcc1/no-libpython real-Metal capsule + device-copy gate — **1 passed
  in 1.94s**.

No bootstrap chain, full launcher file, or GCC suite was run.

