# GC0 graph-lock mirror parity ACCEPTED at the worker replay level

Date: 2026-08-21

Investigation: `docs/investigations/gc0-graph-lock-mirror-drift-threads-off.md`

## Change

`pcc/py_runtime/py/freestanding_runtime_high_substrate.py`: both
`pcc_py_gc_minor_graph_lock` and `pcc_py_gc_minor_graph_unlock` now return
immediately when `pcc_threads_enabled() == 0`, mirroring the C oracle's
compile-time elision (`py_runtime_high_substrate.c`
`#if !PCC_WITH_THREADS return;`). No other function or build changed; under
`PCC_WITH_THREADS=1` the pthread kernel returns non-zero and the full body
runs exactly as before.

## Why

The frozen stage2 emit-worker capture attributes 1,243 leaf samples (7.75%)
to the lock pair plus a large share of `_tlv_get_addr`, driven by per-function
frame enter/leave registration. The production self-host archive is built
threads-off (`pcc_threads_enabled` ≡ 0, zero pthread references), so the
lock's mutual exclusion was vacuous while every compiled function call paid
TLS+CAS for it. The linked lock was the pcc-Python mirror, which lacked the
C oracle's elision — a mirror drift.

## Acceptance evidence (all pre-registered gates)

1. Archive rebuilt clean after wiping all 186 stale `build_py` objects;
   guard visible in the emitted IR.
2. Focused gates: GC0 31 passed; GC1..4 16 passed each; threading substrate
   16 passed (two PRE-EXISTING HEAD failures in source-text assertions on
   untouched files, recorded in the investigation).
3. Candidate pcc1 `build/gc0-lock-candidate-v1/pcc1` (stage1 318 s, rc=0);
   disassembly shows both locks begin `call pcc_threads_enabled; cbz x0, <ret>`.
4. pcc1-focused gates bound to the candidate: 6 passed.
5. Frozen module98 emit-worker replay A/B under `build/.pcc-performance.lock`,
   five balanced pairs CB/BC/CB/BC/CB, fresh process per arm:

```text
paired median wall speedup   1.1023x   (bar >=1.08)   pairs 1.0628-1.1086x
paired median cpu ratio      1.0824    (improving)
paired median rss ratio      0.9855    (bar <=1.02x)
assembly                     byte-identical across all arms,
                             SHA256 prefix bbd80d79d046b335 (oracle payload)
```

## Claim level

One frozen module98 emit-worker replay plus focused correctness gates. This
is not a complete stage2 result, not a stage1/stage2 ratio fix, and not
pcc2/pcc3 fixed-point evidence. The complete-stage2 measurement is routed to
`PERF-P1-STAGE2-FULL-RUN-GC0-LOCK-PARITY`.
