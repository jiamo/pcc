# 2026-07-16 production virtual-thread one-million evidence

Task: `T-P0-VTHREAD-1M-GATE`

Machine-readable result:
`docs/goal/evidence/2026-07-16-vthread-1m-results.json`

## Result

The manual `real-runtime` gate now builds an isolated, no-libpython
`libpy_runtime.a`, links a C benchmark directly to the production
`py_virtual_thread_*` scheduler, and runs every GC backend in its own process.
The build is cached by a SHA-256 over the benchmark runner plus the runtime
Makefile, headers, and C sources. The manifest records that source digest, the
runtime archive digest, compiler, platform, architecture, Python driver version,
UTC timestamp, workload shape, and complete per-backend metrics.

Each process simultaneously schedules one million virtual-thread objects:

- 899,000 enter through the ready queue;
- 100,000 park in the production timer heap and wake through the timer poller;
- 1,000 park on one real pipe through the production poll waitset and wake from
  live readability (this measures vthread waiters, not one million fds);
- one explicit GC runs while the scheduler roots are the only owners;
- all one million are dequeued, completed, and released;
- scheduler roots and ready/timer/IO counts must all finish at zero.

Mean latencies are amortized phase measurements, not p50/p99 distributions.
RSS is reported by the current machine's Mach task APIs. GC pauses come from the
runtime's real `PCC_GC_COUNTER_PAUSE_*` telemetry, not the old logical
threshold surrogate.

## Current-machine GC0..4 result

Command:

```text
gtimeout 300s env -u LC_ALL PCC_VTHREAD_1M=1 uv run python \
  scripts/run_vthread_1m_gate.py --backend-timeout 120 \
  --output /tmp/pcc-vthread-1m-final-source.json
```

Source SHA-256:
`6c5e7e58e2733bc74db2bdeeb19c4b07285b19c68856f3d4d100bc29e062714c`

Runtime archive SHA-256:
`d175032b9670cea5650f14f9bbd508a2a30e077de933e17df6917048b2ecd59b`

Persisted JSON SHA-256:
`614e4c6fde2019926eb256aa55b31184dfc3c6d9cdb3ef483887aec33a0e4994`

| GC | total | throughput/s | peak RSS | enqueue mean | resume mean | timer park/wake mean | IO park/wake mean | max pause | pause sum |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 refcount/cycle | 0.670 s | 1,493,625 | 264.1 MB | 49 ns | 52 ns | 123 / 192 ns | 10,570 / 70 ns | 103.956 ms | 103.956 ms |
| 1 incremental | 0.846 s | 1,182,725 | 456.8 MB | 59 ns | 59 ns | 138 / 240 ns | 11,068 / 121 ns | 97.311 ms | 176.656 ms |
| 2 concurrent MS | 0.857 s | 1,166,402 | 455.3 MB | 54 ns | 59 ns | 139 / 238 ns | 10,348 / 166 ns | 94.102 ms | 170.378 ms |
| 3 generational | 28.180 s | 35,486 | 456.1 MB | 57 ns | 62 ns | 142 / 206 ns | 9,550 / 71 ns | 162.509 ms | 27,518.740 ms |
| 4 relocating | 1.321 s | 757,049 | 688.2 MB | 61 ns | 85 ns | 148 / 233 ns | 10,995 / 101 ns | 96.032 ms | 185.586 ms |

All five results report `completed=1_000_000` and zero final scheduler roots,
ready entries, timers, and IO waiters. The fixed 4096-event diagnostic buffer
reports dropped events at this scale by design; transition correctness is
proved separately by the no-drop focused production-event gate. The 1M result
does not mislabel bounded diagnostic retention as full telemetry retention.

## GC3 release regression found by the gate

The first million run completed GC0..2 but GC3 stayed below its first 100,000
completion marker for over 80 seconds at approximately 100% CPU and 446 MB RSS.
A native sample put every sampled stack under
`py_decref -> pcc_gc_free_object_memory`.

The cause was a redundant ownership search after the freeing hook had already
removed a copy-oldified heap object from the object index. Each deallocation
then fell back to a full object-list scan and a linear minor-block address scan,
making the million-object completion phase quadratic. The final C runtime uses
the existing allocation-origin law: GC3 arena objects carry
`PY_FLAG_GC_MINOR_ARENA`; live non-arena objects are normal heap objects and
take O(1) index cleanup/direct free. No arena safety or flag semantics were
weakened. Details:
`docs/investigations/gc-backend3-vthread-million-release-quadratic.md`.

Final focused semantics and small real-runtime matrix:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/benchmarks/vthread/test_vthread_real_runtime.py \
  tests/python/test_gc_backend_generational.py::test_generational_backend_c_runtime_uses_minor_bump_arena \
  tests/python/test_gc_backend_generational.py::test_generational_backend_c_runtime_frees_minor_object_by_index_when_flag_clobbered \
  tests/python/test_gc_backend_generational.py::test_generational_backend_minor_refill_oldifies_copy_for_remembered_child \
  tests/python/test_gc_backend_generational.py::test_generational_backend_release_of_forwarded_source_consumes_source_ref \
  tests/python/test_gc_backend4_production.py::test_backend4_skips_zpage_and_graph_for_leaf_objects

8 passed in 41.73s
```

Two pcc-Python GC3 oldify parity probes remain independently red after all
mirror experiments were removed. They are recorded as
`G-P1-GC3-PCC-PY-OLDIFY-REGRESSION`; they do not change the mode label of this
C production-runtime result.

## Claim boundary

This proves that the current macOS arm64 no-libpython C runtime can hold,
collect, wake, complete, and release one million production virtual-thread
objects under each of GC0..4, with real RSS, amortized operation latency,
throughput, and GC-pause telemetry bound to the measured source/archive. It
does not prove one million simultaneous file descriptors, percentile latency,
an unbounded event trace, Linux epoll, arbitrary native-stack suspension, or a
fast GC3 collector. In particular, GC3's 27.5-second aggregate pause is a
measured efficiency deficit for the next long-running five-GC performance
work, not a hidden success.
