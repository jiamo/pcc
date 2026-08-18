# `compute_free_names` common/cold split — denied

Date: 2026-08-21  
Task: `PERF-P1-PCC1-PER-OP-GC-TAX`

Claim level: frozen singleton frontend-worker, Darwin arm64, GC0,
`backend=self`, `python-libpython=off`.  This is a negative performance result;
it does not prove full-stage performance, coordinator memory, GC1..4 equality,
or a pcc2/pcc3 fixed point.

## Candidate and frozen identities

The candidate mechanically promoted the stateless `compute_free_names` helper
cluster and split its recursive `walk` into ordinary and scoped paths.  It did
not change the AST wire, type inference, code-generation algorithms, runtime
barrier implementation/semantics, worker scheduling, cache-key scheme, or
output format.  The generated frame-registration count and compiler identity
did change; those were the measured variables.

```text
candidate hoist_free_names.py
  0be77e63e49dc181906cf90cba9b9f2648b214693e44f710979614e1bc2f7f84
baseline hoist_free_names.py
  4fc06cd534a109ffd0a3b7a499b7ae264a40fa803af3d65879dcf4b7bdac8e8c
candidate source manifest
  51a722f1045568f38e93284c3fb5d84d4f1a69e173a827e0b294a0a7b03787aa
baseline source manifest
  2078d2105c450d0bf85ccc592c223bbea99872d541f18888476b61cf81cb813e
candidate pcc1
  0da3fad8a480fcf3fd719715cbb3bfe2bf13db339536b20ff14a797c6b31ecf1
baseline pcc1
  b2ba3969609dd0ba2b25b5c9d99cc480b606f451af57d01517001d0afda29d47
runtime archive
  b42890eeca1e1387c7282be0297a9f7daadb1042c0efb65d533cf8b94375b3d0
worker manifest
  7fa94593c0550f2ab316360e9b00528564cd36dd2113e9ff55a35d08fca95cd6
native exports
  d6a5902b6fe741cd6e80bf2312871845703932e22c9e11a569a7c2f1e1573912
module_81 AST
  02c90f74d88d452ada1e5f137ef1dfb88ad63cba2dcb0d49bacaea7800b1f4a5
expected and every replayed 10,888,793-byte IR
  19f1c3b6d0278941f30e35c9ae7ea67a21b301b3e85c7018ae0b37ffb10030ea
final A/B receipt
  734443735665bce1febf0dab8577634c06e9070c222f4e7b7e431871457e4113
receipt path
  docs/goal/evidence/2026-08-21-compute-free-names-worker-ab-manifest.json
```

Both frozen stage1 builds completed in the same mode with no dynamic
libpython/LLVM dependency.  Their 269.73 s and 272.80 s build walls are closure
evidence only, not the optimization measurement.

## Structural and semantic gates

The candidate reduced the compiled common walker's plain frame registrations
from 33 to 14, below the pre-registered ceiling of 16.  Strict contextual
fallback remained zero; the common/cold calls were direct and introduced no
dynamic callback or owned alias.  The focused closure, comprehension, lambda,
nonlocal/global, architecture, and native-shape run completed:

```text
16 passed in 42.22s
build/hoist-walker-final-focused.log
```

The independently useful malformed empty synthetic-comprehension semantic
regression remains after the performance candidate is removed.

## Unsampled matched A/B

The controller reused the repository performance lock, process-group watchdog,
measurement environment, time parser, and paired-summary primitives from
`scripts/run_pcc_compile_ab.py`.  It used four balanced discarded warmups
(`candidate/baseline/baseline/candidate`), then four matched pairs ordered
`CB/BC/BC/CB`.  Each invocation was a fresh process and fresh output directory.

| pair | baseline wall (s) | candidate wall (s) | speedup B/C | baseline CPU (s) | candidate CPU (s) | baseline instructions | candidate instructions | baseline RSS (B) | candidate RSS (B) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 29.36 | 28.66 | 1.024424 | 28.97 | 28.35 | 343,861,527,054 | 335,653,691,055 | 2,519,416,832 | 2,519,384,064 |
| 2 | 29.30 | 28.80 | 1.017361 | 28.87 | 28.56 | 343,561,408,205 | 335,823,741,812 | 2,519,400,448 | 2,519,400,448 |
| 3 | 29.13 | 29.11 | 1.000687 | 28.92 | 28.61 | 343,489,603,269 | 335,860,942,881 | 2,519,433,216 | 2,519,384,064 |
| 4 | 29.05 | 29.20 | 0.994863 | 28.90 | 28.73 | 343,335,961,293 | 335,734,413,931 | 2,519,433,216 | 2,519,367,680 |

```text
arm-median wall speedup             1.008979x
paired-median wall speedup          1.009024x
paired wall range                   0.994863x .. 1.024424x
paired-median CPU ratio C/B         0.989271
paired-median instruction ratio C/B 0.977634
paired-median max-RSS ratio C/B     0.999984
paired-median footprint ratio C/B   0.999997
```

All eight measured processes returned zero with empty stdout/stderr, and every
IR was byte-identical to the retained stage2 artifact.

## Verdict `[DENIED]`

The pre-registered acceptance boundary required both arm-median and
paired-median wall speedup of at least 1.08x, no slower individual pair,
improving compute metrics, memory no worse than 1.02x, and byte-identical IR.
The candidate achieved only 1.009024x paired-median wall speedup and one pair
was slightly slower.  The approximately 2.24% instruction reduction is useful
attribution but cannot override the wall gate.

The common/cold split was removed by a forward working-tree patch before any
candidate full-stage rebuild.  Do not retry this exact helper-hoist or
common/cold mutual-call shape without new profile evidence that changes its
measured ceiling.

## Supported and unsupported claims

This proves only that the exact measured split is not a sufficient stage2
optimization.  It does not prove that `compute_free_names` has no other
optimizable representation, that generic GC barriers may be weakened, or that
the independently routed coordinator live-set task is resolved.
