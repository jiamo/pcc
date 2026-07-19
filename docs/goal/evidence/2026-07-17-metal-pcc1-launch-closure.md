# GPU-P0-METAL-PCC1-LAUNCH-REAL closure evidence

A current pcc1 binary that links no libpython compiled a focused launcher
program with `--python-libpython=off`, loaded a real prebuilt Metal library,
created native MTLBuffers, dispatched the copy kernel, waited for completion,
read exact f32 output, and released the buffers. The strict real-hardware gate
passed in **2.77s**.

The same pcc1 then ran the canonical metallib workload under GC0..4 in **2.27s**.
This closes the finite title claim: pcc1/no-libpython owns the launcher path.
It does not claim arbitrary TileLang lowering, arbitrary tensorcore coverage,
performance, or whole-program GPU execution; those are separate feature cards.

No pcc1 rebuild, full launcher file, bootstrap chain, or GCC suite was run.

