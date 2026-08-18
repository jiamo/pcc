# Investigation: process-tree sampler aborts a healthy Stage2 on transient `ps` timeout

## Status

resolved

## Problem Description

The receipt-bound v34 GC0 Stage2 compile was active at 151 seconds with 26
processes and a previously observed 21,987,524,608-byte tree-RSS peak.  The
compiler had emitted no failure, but `scripts/run_process_tree_sample.py`
aborted the whole process group because one `ps -Ao ...` telemetry subprocess
exceeded its fixed five-second timeout.  The sampler raised raw
`subprocess.TimeoutExpired` and left `stage2-process.result.json` at
`status: RUNNING`, so the compiler run produced neither a usable Stage2 result
nor an honest terminal sampler receipt.

This is a measurement-harness failure, not a pcc1 compile failure.  Repeating
Stage2 without repairing the sampler would risk wasting the same cold compile
again.

## Repro

The confirmed production artifact is
`build/no105-summary-pco-stage2-v34/stage2-process.result.json`.  The invoking
runner was `run_pcc_stage_ab.py::_run_stage2` with a 600-second compiler
watchdog and 0.25-second RSS interval.  At 151 seconds it raised:

```text
subprocess.TimeoutExpired: Command '['ps', '-Ao',
'pid=,ppid=,rss=,comm=']' timed out after 5 seconds
```

No `bootstrap.sh`, pcc1, pcc2, or codegen-worker process survived the sampler's
exception cleanup, and no pcc2 was produced.

## Test [CONFIRMED]

The failure was observed directly in the source-frozen v34 Stage2 run on
2026-08-31.  A focused regression must inject one `TimeoutExpired` from the
first process-table read, prove a bounded second attempt succeeds, and prove
the completed receipt records the retry count.  Persistent failure must still
fail closed and terminate the owned process group.

## Proposals

- No.1 Bounded process-table retry with receipt accounting [CONFIRMED]
- No.2 Ignore missing RSS samples indefinitely [DENIED]

## No.1 Bounded process-table retry with receipt accounting

### Code Change

Keep the ordinary five-second `ps` watchdog, retry one timed-out sample with a
twenty-second watchdog, and record every retry in the final receipt.  If the
retry also times out, raise a sampler-owned error and retain existing
process-group cleanup.  This tolerates one load/VM-pressure spike without
turning telemetry into an unbounded command or silently claiming complete RSS
coverage.

### pending

Awaiting focused tool tests and a resumed source-frozen Stage2.

## No.2 Ignore missing RSS samples indefinitely

### Code Change

Catch every `ps` failure, reuse the previous process table, and continue the
target without a retry limit.

### DENIED

That could under-report the actual RSS peak, lose newly spawned children from
the termination set, and still label the receipt complete.  A claim-grade
sampler must either obtain the table within a bounded retry or fail closed.

## Update — 2026-08-31 focused implementation

No.1 is CONFIRMED at focused-tool scope. The sampler now retries 5s→20s once,
persists `SAMPLER_ERROR` after a second timeout, flushes every RSS row
incrementally and records retry counts. The adjacent host-safety incident also
added a hard aggregate-RSS `MEMORY_LIMIT` terminal path with complete process
group cleanup. Stage runners default to two workers and an 8 GiB cap.

The combined sampler/stage-runner gate passes 15 tests in 0.96s. A real Stage2
was deliberately not rerun: human-supplied Jetsam/panic evidence showed that
the prior unbounded pcc1 fan-out exhausted compressor segments and swap and
rebooted the host. This investigation remains active until one jobs<=2,
8-GiB-capped Stage2 completes without a sampler retry/error; a memory-limit
trip is a compiler-memory failure to optimize, not permission to raise the cap.

## Update — 2026-08-31 incident attribution and safety-mode correction

The human's Jetsam follow-up corrects the earlier wording: this was one
Stage2 coalition with an older pcc1 coordinator/parent plus ten pcc1 workers,
not independent benchmark processes. Retained V4 manifests tie the tree to
coordinator PID 28786 and identify the first ten codegen owners; three workers
reached 26.7--36.0 GiB after direct `.pco` publication moved backend emit and
assembly into the frontend worker while leaving its width at ten.

The measurement retry and a host-safety circuit breaker now have different
contracts. An uncapped observation may retain the bounded 5s->20s retry. A run
with `--max-tree-rss-bytes` is safety-critical: its process-table read has one
one-second deadline, and failure immediately terminates the complete owned tree
rather than leaving a potentially growing compiler unobserved for another
twenty seconds. `ps -ww ... command=` retains full argv; terminal and
largest-process records include extracted worker manifest paths. Ordinary
bootstrap now enters this guard itself unless an outer receipt runner declares
that it already owns the guard.

## Update — 2026-09-04 lean safety-table closure

The safety-mode one-second deadline exposed a second harness failure on a
healthy Stage1: asking `ps` for full argv of roughly 700 system processes could
miss the deadline even when the compiler tree itself was below its cap. The
sampler now reads only `pid,ppid,rss` for every safety sample, queries the
largest owned PID's command separately with a bounded 250 ms lookup, and
caches successful command text for terminal evidence. RSS accounting and the
known process set never depend on argv availability. Receipts count command
lookup failures explicitly.

The complete sampler tool packet passes 13 tests. Current guarded transfers
then completed without a process-table retry or command lookup failure,
including Stage1 v14 (4.984GB peak) and both v14 checkpoints (6.997GB and
7.293GB peaks). More importantly, the current-source GC0 Stage2 safety closure
completed rc0 below the 8 GiB cap at 7,812,333,568 bytes with
`process_table_retry_count=0`; it produced a runnable libSystem-only pcc2.

## Report

No sampler failure is ignored. Uncapped diagnostic mode retains the bounded
retry, while capped safety mode uses the lean one-second table and fails closed
if that essential RSS observation is unavailable. Command enrichment is
best-effort and separately counted. This closes the original healthy-build
abort without weakening the memory circuit breaker.
