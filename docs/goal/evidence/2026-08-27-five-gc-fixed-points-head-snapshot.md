# All five GC backends hold the self-host fixed point on the HEAD snapshot

Date: 2026-08-27
Tasks: `GC-P0-FIVE-GC-MATURE-RESOURCE-EFFICIENCY`, `RT-P0-SET-DROPS-ELEMENT-ON-CYCLING-PROBE`,
`PY-P0-SET-FROM-MAPPING-EMPTY`
Claim level: one backend's complete pcc1->pcc2->pcc3 fixed point on committed
source, isolated from the live worktree. Darwin arm64, mode-labeled below.

## Result

Source: `git archive HEAD` (47c9b7d7, carries the set/dict probe-budget fix and
the mapping-builtin fixes) materialized at `/tmp/pcc-gate-snapshot-head`,
physically isolated from the shared worktree's in-flight edits.

```text
PCC_GC_BACKEND=3, --backend self, --python-libpython off, cold object cache
stage1   309.863 s  rc=0   (host)
stage2  1983.835 s  rc=0   (pcc1 under GC3; ~2.3x the GC0 892 s, barrier cost)
stage3   515.498 s  rc=0   (pcc2 under GC3)
pcc2 == pcc3 RAW byte-identical — "Self-host gate passed."
```

With this, two of five backends hold a complete fixed point on the same
source identity:

```text
GC0  bootstrap-setprobe-v1     Stage2 892.439 s   pcc2==pcc3   (this morning)
GC3  snapshot gc3-chain        Stage2 1983.835 s  pcc2==pcc3   (this run)
```

## Why the snapshot, and what the earlier "failures" were

Three consecutive GC3 attempts today failed for three DIFFERENT
non-GC3 reasons, each documented in
`2026-08-27-gc3-failure-was-shared-worktree-staleness.md`:
ensure_runtime staleness triggered by live worktree edits; a mid-stage3 module
addition making pcc3 compile one more module than pcc2 (33,008-byte growth,
wrongly read as a fixed-point break); and an unbuildable worktree from an
in-flight file (which also surfaced the real frontend bug
`PY-P1-TYPEINFER-LOOP-VAR-SHADOWS-METHOD`). Gates now run in `git archive`
snapshots; probe runs pin `PCC_RUNTIME_ARCHIVE`.

## GC4 (appended after completion)

Same snapshot, `--reuse-stage1`, warm object cache from the GC3 chain:

```text
PCC_GC_BACKEND=4
stage2  225.344 s  rc=0   (emit nearly all cache hits)
stage3  243.319 s  rc=0
pcc2 == pcc3 RAW byte-identical — "Self-host gate passed."
```

Three of five backends now hold the fixed point on the same source identity.
The warm-cache stage2 also demonstrates the object cache is backend-neutral in
practice: 464 objects produced under GC3 satisfied the GC4 chain byte-for-byte.

## GC1 / GC2 (appended after completion)

Same snapshot, sequential, warm object cache:

```text
GC1: stage2 391.236 s rc=0, stage3 238.412 s rc=0, pcc2==pcc3 RAW — gate passed
GC2: stage2 236.271 s rc=0, stage3 251.270 s rc=0, pcc2==pcc3 RAW — gate passed
```

## The five-GC scoreboard, one source identity (HEAD 47c9b7d7)

```text
backend                          stage2       fixed point   where
GC0 refcount+cycle                892.439 s   pcc2==pcc3    worktree chain (morning)
GC1 incremental tricolor          391.236 s   pcc2==pcc3    snapshot, warm cache
GC2 concurrent mark-sweep         236.271 s   pcc2==pcc3    snapshot, warm cache
GC3 generational minor/major     1983.835 s   pcc2==pcc3    snapshot, cold cache
GC4 colored relocating            225.344 s   pcc2==pcc3    snapshot, warm cache
```

Every collector self-hosts to a RAW byte-identical pcc2/pcc3 on source that
carries the set/dict probe-budget fix and the mapping-builtin fixes — the two
correctness repairs this evidence chain exists to gate. The two moving
collectors (GC3, GC4), which are exactly what the restored managed reloads
serve, both close the loop.

Mode label, stated precisely: these are direct `scripts/bootstrap.sh` chains
(the same commands the pytest matrix wraps), run in an isolated `git archive
HEAD` snapshot. The pytest matrix files on the WORKTREE remain to be run once
the linker lane's in-flight work lands and the worktree builds again; wall
times across rows are not mutually comparable (cold vs warm cache, varying
host load) and are correctness receipts only.

## Open

 The five-GC pytest matrix on the WORKTREE waits until the linker
lane's in-flight work lands and the worktree builds again. The leak fix and
the No.75/76 stackmap optimizations are uncommitted worktree state and are
NOT covered by this snapshot's chains; they re-enter the gate when committed.
