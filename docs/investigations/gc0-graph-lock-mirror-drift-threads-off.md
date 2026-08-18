# Investigation: GC0 graph lock mirror drift — pcc-Python mirror pays the full TLS+CAS lock under threads-off builds

Predecessor: [pcc1-stage2-emit-throughput-and-memory.md](pcc1-stage2-emit-throughput-and-memory.md)
(Updates No.52/No.53 closed the emit-local route and routed successor
ownership to a separately pre-registered GC0 runtime-tax row; this is that
row.)

## Status

resolved — No.1 CONFIRMED and accepted at the worker replay level on
2026-08-21; the complete-stage2 measurement is routed to its own follow-up
row.

## Problem Description

The frozen stage2 emit-worker capture
(`build/stage2-medium-worker-profile-v1/complete-v2.folded`, 16,032 samples)
attributes 1,243 leaf samples (7.75%) to `_pcc_py_gc_minor_graph_lock` (940)
plus `_pcc_py_gc_minor_graph_unlock` (303), plus a large share of
`_tlv_get_addr` (380). The lock's direct callers are flattened function
prologue frames (arena `gen_resume` 114, regex engine 86, stackprep 59,
stack-map planning 50): the volume is per-function-call frame
enter/leave registration (`pcc_gc_note_frame_enter_lifo` takes the lock at
`freestanding_gc_frame_registry.py:287`), not collector contention.

The frozen worker pcc1 is built threads-off: `pcc_threads_enabled`
disassembles to `mov x0, xzr; ret`, the archive has zero pthread references,
and the threads-off kernel (`freestanding_thread_kernel.py`) owns the default
ABI where native thread creation fails. With no second thread possible, the
graph lock's mutual exclusion is vacuous — yet the linked lock executes the
full body.

Root cause, verified by disassembly and source: the C oracle
(`pcc/py_runtime/src/py_runtime_high_substrate.c:46-68`) elides the lock at
compile time (`#if !PCC_WITH_THREADS return;`), but the pcc-Python mirror
(`pcc/py_runtime/py/freestanding_runtime_high_substrate.py:49-93`) — which is
what the self-host runtime archive actually links, under the
`@c_abi_export` symbol `pcc_py_gc_minor_graph_lock` — has no such guard and
always runs the TLS load/compare/store plus the CAS acquire and the release
store. This is a C/pcc-Python mirror behavioral drift of the class AGENTS.md
records as recurring.

## Repro

```bash
# the linked lock contains the full threads-on body although the archive is threads-off
otool -tvV -p _pcc_py_gc_minor_graph_lock build/stage2-medium-concurrency-ab-v2/input/pcc1
# shows: _tlv_get_addr call, TLS depth load/compare/store, ldaxr/stlxr CAS loop
otool -tvV -p _pcc_threads_enabled build/stage2-medium-concurrency-ab-v2/input/pcc1
# shows: mov x0, xzr ; ret   (threads-off kernel, constant 0)
```

Cost surface: every frame enter/leave of every compiled function with rooted
locals pays two TLS accesses plus stores per call, in every process built on
the pcc-Python runtime archive (stage2 frontend workers, emit workers, link
driver, and all compiled user programs).

## Test [N/A]

No failing semantic test exists — this is a performance defect with a
semantic-identity claim. Acceptance is the frozen module98 replay A/B below.

## Proposals

- No.1 Mirror-parity threads-off fast path in
  `freestanding_runtime_high_substrate.py`: both `pcc_py_gc_minor_graph_lock`
  and `pcc_py_gc_minor_graph_unlock` return immediately when
  `pcc_threads_enabled() == 0`. [CONFIRMED]

## No.1 Mirror-parity threads-off graph-lock fast path

### Code Change (pre-registered)

Both mirror functions gain one guard as their first statement:

```python
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)

@c_abi_export("pcc_py_gc_minor_graph_lock")
def pcc_py_gc_minor_graph_lock() -> None:
    if pcc_threads_enabled() == 0:
        return
    ... existing body unchanged ...
```

and the symmetric guard in `pcc_py_gc_minor_graph_unlock`. No other function,
flag, barrier, or registry behavior changes. The C oracle already has the
compile-time equivalent and is not modified. Under a `PCC_WITH_THREADS=1`
build the pthread kernel module provides `pcc_threads_enabled` returning
non-zero, so the existing body runs and threaded behavior is unchanged.

### Correctness argument

Under the threads-off kernel, thread creation fails and
`pcc_threads_enabled()` is identically 0, so no concurrent collector or
mutator can exist; mutual exclusion is vacuously satisfied and the collectors
of all five backends run on the single thread, where control flow already
serializes them against explicit `gc.collect()`. Under threads-on builds the
guard is transparent. This restores mirror parity with the C oracle's
compile-time elision instead of inventing a new mechanism.

### Expected size and rejection line (pre-registered before implementation)

Expected saving on the frozen module98 worker: the 1,243 lock-pair samples
plus the lock-related share of `_tlv_get_addr` — roughly 8.5-9.5% of worker
wall, i.e. paired median speedup ~1.09-1.10x. This is above the 1.08 bar but
thin; the experiment is fail-first.

ACCEPT only if all of:

1. Runtime archive rebuilds clean after wiping
   `pcc/py_runtime/build_py/*.o` and `*.provenance.json` (the recorded
   stale-object hazard).
2. Focused gates green: GC0..4 compile smoke and program correctness, the
   threaded explicit-collect gates under a `PCC_WITH_THREADS=1` archive
   (unchanged path proof), and the existing runtime-focused test files.
3. Candidate pcc1 (stage1 rebuild over the new archive) compiles the frozen
   module98 IR to **byte-identical assembly** (the compiler's generated code
   is unchanged; only the runtime archive differs).
4. At least three balanced unsampled frozen module98 replay pairs under the
   repository performance lock: paired median wall speedup >=1.08,
   user+sys and instructions improving, max RSS/footprint <=1.02x, rc0.

DENY and revert the guard if any gate fails; no complete stage2 rebuild
follows a denial.

### What this does not prove

A single-worker replay win does not prove a complete stage2 improvement, a
stage1/stage2 ratio fix, or pcc2/pcc3 fixed-point stability. The same archive
serves the frontend workers and link driver, so the stage2-wide effect should
be larger than the worker replay alone, but that claim needs its own complete
run.

### CONFIRMED

Implemented exactly as pre-registered; no other source changed. Verification
chain, in order:

1. Runtime archive rebuilt clean after wiping all 186 `build_py/*.o` +
   provenance files (142 s make, rc=0). The emitted
   `freestanding_runtime_high_substrate.ll` shows the guard
   (`call i64 @pcc_threads_enabled()` -> `icmp eq 0` -> early-exit block).
2. Focused gates: GC0 31 passed (`test_gc_api` + `test_gc_abstraction_surface`),
   GC1..4 16 passed each, threading substrate 16 passed with two PRE-EXISTING
   HEAD failures unrelated to this change (both are source-text assertions on
   files this change does not touch:
   `test_generational_backend_step_polls_thread_safepoint_in_c_and_pcc_python_runtime`
   splits the dispatcher on `def pcc_gc_step(budget: int)` while the file says
   `budget: i64`; `test_tracing_gc_finalizer_handles_thread_objects_and_refcount_side_table`
   asserts `tag == 27` in the tracing sweep collector source).
3. Candidate pcc1 built from current source over the new archive
   (`build/gc0-lock-candidate-v1/pcc1`, stage1 318 s, rc=0). Disassembly
   proves the live guard: both lock functions now begin
   `call pcc_threads_enabled; cbz x0, <ret>`.
4. pcc1-focused gates bound to the candidate via `PCC_CURRENT_PCC1`/
   `PCC1_BINARY`: 6 passed (function-binary compile/run +
   self-backend GC-backend compile smoke).
5. Frozen module98 emit-worker replay A/B under the repository performance
   lock, fresh process per arm, `/usr/bin/time -lp`, one discarded sanity run
   per arm, then five balanced pairs ordered CB/BC/CB/BC/CB. Every run of
   every arm produced byte-identical result payloads,
   SHA256 prefix `bbd80d79d046b335` — the recorded oracle payload hash.

```text
pair 1 (CB): cand 16.36s | base 17.48s | 1.0682x
pair 2 (BC): cand 16.19s | base 17.95s | 1.1086x
pair 3 (CB): cand 16.41s | base 17.44s | 1.0628x
pair 4 (BC): cand 16.93s | base 18.66s | 1.1023x
pair 5 (CB): cand 16.25s | base 17.99s | 1.1073x

paired median wall speedup   1.1023x   (bar >=1.08)
paired median cpu ratio      1.0824    (improving)
paired median rss ratio      0.9855    (bar <=1.02)
```

All five pairs favor the candidate; user+sys improves in every pair. ACCEPT.

## Report

No.1 landed: the pcc-Python runtime mirror now matches the C oracle's
threads-off graph-lock elision. The accepted artifacts are the substrate
guard, the rebuilt archive, and candidate pcc1
`build/gc0-lock-candidate-v1/pcc1`. This is the first accepted stage2-side
optimization after the No.42-No.53 denial streak, won by mirror repair rather
than a new algorithmic shape.

Scope honesty: the 1.10x is one frozen module98 emit-worker replay, not a
complete stage2 result. The same archive serves frontend workers and the link
driver, so the stage2-wide effect should be larger in absolute seconds, but
that requires a complete same-source stage2 run against a pre-change archive
and is routed to a separate follow-up row rather than claimed here.

Follow-up candidates recorded, not attempted: the same drift pattern may
exist in other py-mirror functions whose C oracle carries `#if !PCC_WITH_THREADS`
elisions; auditing those is a separate mirror-parity sweep.
## Update — complete-stage2 measurement and honest attribution correction

Three complete stage2 runs on this machine today, all `--backend self --stage
2`, all rc=0 with a working pcc2 (`print(7)` -> 7):

The retained no-guard control's own stage1 receipt is also important:
`stage1.result.json` records stage wall 63.908s and compile wall 60.217s.
Older hot-stage1 history is 71-90s. A later reported granule-source stage1 of
133.6s has no phase receipt and is not comparable; if reproduced, it is a
stage1 regression to diagnose, not a denominator that may relax the stage2
target.

```text
guard pcc1,   run 1   stage2 397.6s   (total wall 460.2s)
guard pcc1,   run 2   stage2 351.0s   (total wall 413.0s, user+sys 1257s)
control pcc1, run 3   stage2 357.685s (stage2.result.json wall_ms=357685;
                                      total outer wall 422.54s,
                                      user+sys 1255s, max RSS 9.03GB)
                                      -- NO-GUARD binary
```

Run 3 used the frozen pre-guard pcc1 (`b2ba3969...`, built 12:00 today)
swapped into `build/bootstrap/pcc1`, back-to-back under the same lock with
equalized cache state. Guard versus no-guard is inside run-to-run noise: at
complete-stage2 scale the lock fix contributes single-digit seconds, exactly
the diluted prediction of its worker-level 1.10x, and NOT the 875->400s move.
The worker-replay ACCEPT (1.1023x) stands unchanged; any stage2-level claim
for this fix is withdrawn.

The reproduced fact is different and larger: complete stage2 on this source
runs ~351-398s where this morning's retained record said 875.10s, with control
RSS 9.03GB. The 422.54s figure is the outer harness and must never be labelled
stage2. The roughly 2x movement is NOT attributable to the graph lock.
Candidate causes, unranked and unverified: the exact-container
ownership fix and other changes landed between the No.46 measurement build
(`aaeffa06...`) and the 12:00 build (`b2ba3969...`); cache-state differences
against the No.46 run; ambient machine state during No.46. Attributing the
2x would require a receipt-bound bisect across those historical builds, but
the runtime source has since moved to the granule slice. Do not rerun or select
this stale graph-lock experiment as the active route. Current ownership is
`ARCH-P0-PROVENANCE-GRANULE-MAP`; after its focused gates freeze one source,
the overarching bootstrap-performance route must recapture same-knob stage1,
stage2 and host-CPython controls. The historical full-stage attribution remains
unresolved and is not a green performance claim.
