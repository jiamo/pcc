# No.98 shared vthread analysis `[DENIED]`

## Claim and implementation

`compute_vthread_may_park_callables` and the immediately following
`classify_vthread_park_boundaries` each rebuilt the same post-hoist function/
method lists, scope nodes, lexical bindings, attribute calls, class hints,
class tables and import aliases. No.98 made compute publish one identity-bound,
one-shot analysis handoff inside `vthread_effect_analysis`; classify consumed
and cleared it. `PCC_FORCE_UNSHARED_VTHREAD_ANALYSIS=1` retained the independent
control path in the same binary.

Focused gates proved shared and independent effect/rejection results equal,
reduced `_function_scope_nodes` from twice to once per callable, rejected a
cache for a different module/export identity, passed the 29-node vthread
packet, emitted real strict bodies, kept host worker IR exact, and kept item311
assembly `ff943e10...`.

The first candidate build is excluded from performance evidence. A new
cross-module combined-analysis entry caused the compiled generation method to
contain `py_cpy_from_pcc_obj(self)`, so strict lowering replaced the whole
method with an unavailable stub. The corrected v2 restored
`generation_lowering.py` byte-for-byte to No.89 and kept the handoff inside the
existing vthread compute/classify APIs. Both shared and unshared v2 pcc1 worker
canaries completed with exact `065100ba...` output.

## Three alternating pairs

One corrected pcc1 binary, identical frozen input, GC0, threads off, timing
disabled, shared performance lock, `/usr/bin/time -lp`, process-tree sampling.
Unshared is the control (U), shared the candidate (S).

| pair/order | wall U/S | CPU U/S | instructions S/U | footprint S/U | tree RSS S/U |
|---|---:|---:|---:|---:|---:|
| 1 U/S | 0.78589 | 0.93873 | 1.00091 | 0.99999 | 1.00786 |
| 2 S/U | 1.01119 | 1.00563 | 1.00000 | 1.00000 | 0.98353 |
| 3 U/S | 1.51387 | 1.07975 | 0.99874 | 1.00001 | 1.02081 |
| median | **1.01119** | **1.00563** | **1.0000009** | **1.00000** | **1.00786** |

All six outputs are the exact 19,279,474-byte `065100ba...` oracle. Pair 1's
shared arm and pair 3's unshared arm contain opposite off-CPU delays; both stay
in the record. Pair 2 and the median CPU/instruction counters show the actual
work is flat.

## Verdict

`[DENIED]`. No.98 misses the registered 1.025 wall/CPU and 0.98 instruction
lines. Sharing removes a logically duplicate traversal but the cache/handoff
materialization costs essentially the same amount under pcc1. No Stage2,
Stage3 or GC1--4 ran.

The vthread production change is forward-removed. Two generic dataclass
correctness fixes discovered during No.97 remain separately covered. After two
consecutive frontend metadata projections failed, the next work must zoom out
to a whole-Stage2 owner of at least 25%; another 3--7% per-pass proposal is not
authorized.

Artifacts:

- `build/no98-shared-vthread-stage1-candidate-315-v2/`
- `build/no98-vthread-v2-canary-{shared,unshared}/`
- `build/no98-vthread-v2-ab-pair{1,2,3}-{shared,unshared}/`
- `build/no98-vthread-focused-gate.log`

