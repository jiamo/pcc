# Current-source stage2 phase/RSS localization

Date: 2026-08-21

Claim level: diagnostic GC0, Darwin arm64, `backend=self`,
`python-libpython=off`, frontend jobs 10, self-backend jobs 8, Mach-O jobs 8,
frontend/object caches off and Python IR passes off.  The run used LLDB,
250 ms process-tree sampling and a 20-second CPU flamegraph, so its wall time is
not an unsampled performance acceptance result.

## Frozen inputs and successful compiler output

- source closure: 1,132 files, manifest digest
  `1155c41392eeb94d00dbc6272a0b38afc15f1e3e3d33c5f631f6de1814213d96`;
- pcc1: `b2ba3969609dd0ba2b25b5c9d99cc480b606f451af57d01517001d0afda29d47`;
- matching runtime archive:
  `b42890eeca1e1387c7282be0297a9f7daadb1042c0efb65d533cf8b94375b3d0`;
- matching host-pcc stage1: 272.80 s wall, 1,012.99 s user+system,
  5,681,725,440 B max RSS;
- produced pcc2:
  `8b8b570d4b367d99a4bc605de3fe83fd2f559fb8d53ef428c74b29f50fbca7a5`,
  167,112,264 bytes;
- compiler target return code 0; pcc2 `--help` return code 0; pcc2 links only
  `/usr/lib/libSystem.B.dylib`;
- 10/10 export results, 212/212 codegen results and 212/212 module IR files.

Durable diagnostic artifacts are under
`build/stage2-coordinator-live-v1/`.  The run manifest deliberately remains
`ERROR` because the observer missed its requested 212th `read_worker_ir`
one-shot marker.  This does not change the target return code or pcc2
evidence, but it prevents claiming complete six-boundary allocator coverage.
All getter-based allocator readings were zero and are not used as evidence.

## Synchronized RSS attribution

| phase | process-tree peak | coordinator peak |
|---|---:|---:|
| startup/closure | 2.577 GB | 2.379 GB |
| export/AST/vthread handoff | 6.835 GB | 6.755 GB |
| frontend codegen workers | 8.824 GB | 0.188 GB after handoff |
| IR result reread | 4.278 GB | 4.179 GB |
| self split | 3.761 GB | 3.256 GB |
| oversized emit | 12.057 GB | 0.189 GB |
| medium emit | **13.439 GB** | 0.189 GB |
| small emit | 7.315 GB | 0.371 GB |
| link | 6.236 GB | 0.189 GB |

First-observed lifecycle checkpoints were:

- first export read: 0.526 GB coordinator;
- first AST read: 1.710 GB;
- vthread annotation entry: 5.164 GB;
- native-export serialization entry: 6.276 GB;
- first codegen manifest after the shared-export helper returned: 6.362 GB;
- first IR result read: 0.498 GB.

The coordinator's all-AST/vthread/export lifetime is real, but its measured
6.755 GB maximum is below the existing 8 GB local limit and is not the full
stage peak.  Once that helper returns, the coordinator falls below 0.2 GB.
The largest synchronized owner is the already-fresh-process-per-item native
emit path, specifically the medium lane at 13.439 GB.  Whole IR result reread
also cannot explain that peak.

## CPU attribution

`scripts/pcc_flamegraph.py` captured 16,406 samples in the child-free vthread
annotation window.  The highest self paths include `py_incref`, raw allocator
`mmap`, managed-pointer index probes and minor graph locking beneath
`annotate_closed_world_vthread_effects`.  This confirms a real frontend
GC/allocation tax but does not override the larger synchronized emit peak or
justify weakening any GC barrier.

## Next finite boundary

Do not repeat the already retained batch4-to-batch1 worker-lifetime change.
Stage2 currently uses one safe item per pcc worker.  First measure only the
medium-lane concurrency variable on a frozen, balanced 32-item IR suite:
8 workers versus 4, identical pcc1/IR/toolchain/environment and byte-identical
assembly.  Retain a four-worker cap only if paired wall is no more than 3%
slower, user+system/instructions/cycles are each no more than 5% worse, and
synchronized aggregate RSS falls at least 40% to at most 8 GB.  Otherwise
deny the cap and profile one batch1 medium worker's complete lifecycle before
changing its algorithm.

This evidence does not close unsampled stage2 performance, pcc2/pcc3 fixed
point, or GC1--GC4 equality.
