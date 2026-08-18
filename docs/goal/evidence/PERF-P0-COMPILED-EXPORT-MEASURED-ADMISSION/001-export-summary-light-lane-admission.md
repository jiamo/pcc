# 001 — measured compiled export/summary light-lane admission

Date: 2026-09-04

## Claim

Compiled export and summary workers now have a measured, fail-closed admission
class separate from compiled codegen workers. With an unknown budget or the
production 8 GiB budget, both short-lived one-module lanes retain width two.
With the explicitly measured 16 GiB shared envelope, the common formula admits
the seven frontend jobs available on this host while compiled codegen remains
at width two.

This closes only the export/summary checkpoint slice. It does not prove a
complete Stage2 improvement, Stage2 <= Stage1, a fixed point, or GC1--4.

## Implementation

- `pipeline_frontend_workers.py` charges export and summary workers 512 MiB
  each after a 7 GiB coordinator/headroom reserve, caps each lane at ten, and
  falls back to the prior width two when the budget is absent or invalid.
- `pipeline_frontend_parallel.py` uses the measured summary policy only for a
  native single-executable worker prefix. Source-Python workers retain the
  existing host width, and an explicit `PCC_PY_FRONTEND_SUMMARY_JOBS` remains
  an authoritative human override.
- The static native export table exposes both policy functions to pcc1.
- `compiled_native_auto_jobs` and the 3 GiB codegen risk class are unchanged.

## Focused correctness and closure

```text
tests/python/test_pipeline_frontend_workers.py
tests/python/test_pipeline_frontend_worker_owners.py
tests/python/test_py_frontend_worker_export.py
tests/python/test_vthread_effect_summary_wire.py
39 passed in 0.41s
```

The policy owner and caller both compile under strict self/no-libpython
`--python-library --emit-llvm`. Function-level IR inspection confirms a real
definition of `compiled_native_summary_jobs` and a direct static call from
`_summary_worker_parallelism`. Four pre-existing whole-module stubs belong to
unrelated action-cache/serialization functions; neither changed function is a
stub and neither calls `py_cpy_*`.

One attempted 193-node combined multi-file packet reached 53 passing nodes and
then its 240-second watchdog expired without a pytest summary. It left no
children and is deliberately not counted as green evidence; this finite lane
slice uses the focused packet plus the stronger real compiled checkpoint below.

## Frozen compiler

Source snapshot:
`/private/tmp/pcc-export-summary-source-v14.esoXh6`

```text
bootstrap source SHA-256  0d057c2567dad023d4591c0c943b77340c1de401ea2588e361492f8d71373d49
pcc1 SHA-256              ece23795411db5c44cd195024e5ed1d2a60a7d30940c9c7c176633d07005cef8
Stage1 wall / tree CPU    203.30s / 772.48s
Stage1 tree peak          4,983,603,200 bytes
linkage                   libSystem only
function canary           42
```

Artifacts are under `build/export-summary-stage1-v14/` and its external
process-tree receipt. This single Stage1 is correctness transfer evidence,
not an unpaired Stage1 speed claim.

## Same-source compiled checkpoint

Both arms use the pcc1 above, its immutable source/runtime bundle, private
caches, cache-off frontend/object settings, deferred codegen plan, and the same
17,179,869,184-byte outer process-tree limit. Only the internal shared worker
budget changes from 8 GiB to 16 GiB; `PCC_PY_FRONTEND_JOBS=auto` and the
absence of a summary override are identical.

```text
metric                         width-2 control      width-7 candidate   ratio
frontend jobs                  3                    7
export / summary / codegen     2 / 2 / 2            7 / 7 / 2
checkpoint total               116.018s             88.334s             1.313x
export+summary owner            69.069s             38.838s             1.778x
tree peak                       6,996,705,280 B      7,292,813,312 B      1.042x
outer-cap headroom                                    9,887,055,872 B
sampler command failures        0                    0
```

The 1.778x owner result crosses the registered 1.75x threshold. The candidate
also has much more than the required 1 GiB cap headroom.

`native_exports.json` is byte-identical between arms at SHA-256
`3885505122933faf928935f9be3c4ef1569216705f0d7b3495b375e273d62147`.
All 224 AST sidecars compare byte-for-byte by relative filename. Both plans
record 4,988 summary nodes, 8,264 edges, seven oversized codegen chunks, 217
safe chunks, and unchanged codegen width two.

Artifacts:

- `build/export-summary-checkpoint-v14-control-auto*`
- `build/export-summary-checkpoint-v14*`

## Invalid arm retained as harness evidence

An earlier v14 control incorrectly set numeric
`PCC_PY_FRONTEND_JOBS=3`. A numeric override intentionally disables the
automatic/deferred source-lane route, so that arm entered full frontend
codegen and the external guard terminated it at 17,224,646,656 bytes. It is
not a compiler failure and is not used in the comparison. The receipt is
retained as `build/export-summary-checkpoint-v14-control-process.result.json`;
no pcc/pytest/bootstrap child survived. Replacing the numeric value with the
same `auto` mode as the candidate produced the valid control above.

## Verdict

`[CONFIRMED]` for the finite measured light-lane admission claim. Retain the
policy. Do not extrapolate the 27.7-second checkpoint saving into a complete
Stage2 number; native emit and owned linking remain separate owners and require
their own guarded receipts.
