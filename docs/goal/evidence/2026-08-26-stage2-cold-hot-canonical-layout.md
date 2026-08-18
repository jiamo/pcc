# Stage2 cold/hot localization and canonical-error layout optimization

Date: 2026-08-26  
Task: `PERF-P0-STAGE2-COLD-CACHE-REGRESSION`  
Platform/mode: Darwin arm64, GC0, `--backend self`,
`--python-libpython=off`, `--ir-scaffold=on`, ten frontend/self-backend
workers.

## Claim

One current-source cold Stage2 profile localized 66.2% of wall time to native
object emission, not cache retention.  Replacing the AArch64 canonical-error
layout pass's repeated remaining-block scan and list shifts with a text-keyed
index plus integer linked-list layout reduced the matched cold Stage2 from
1356.194 s to 1109.920 s (246.274 s, 18.16%) and preserved an exact
`pcc2 == pcc3` fixed point.

This is an accepted optimization slice, not closure of the P0.  The task's
600 s cold threshold and three alternating full-build pairs are not met.

## Frozen baseline and cache localization

Baseline source was `eab5407f64bce65451e9a2dc2216d8d48636f886` and its
ordinary-bootstrap pcc1 was
`0338405442cc6da75fe743f159e694b31423c3d0d3a40b32b58e44e925735986`.
The run used a new empty cache at
`build/stage2-cold-cache-profile-eab5407f-v1/cache`; no source path was
changed.  A same-source hot replay used that same populated directory.

| measure | cold | hot |
|---|---:|---:|
| Stage2 wall | 1356.194 s | 183.429 s |
| frontend IR cache | 0 hit / 1 miss | 1 hit / 0 miss |
| frontend actions | 0 hit / 212 miss | skipped on bundle hit |
| native objects | 0 hit / 463 miss | 463 hit / 0 miss |
| native emit | 897.916 s | no workers |
| frontend codegen | 195.522 s | 1.327 s cache-load path |
| link driver | 139.083 s | 33.730 s |
| cache publish/retention | 2.430 s | 0.243 s |

Cold and hot produced the same pcc2 SHA-256,
`2d66c9647d02b1df31873ad1660f0f491020e148e62e5bf889b796136f750f04`.
The isolated cache reproduced the cold regression while retention consumed
only 0.18% of wall, denying the hypothesis that maintenance of the previous
89 GB shared cache caused the 21-minute build.

After the measurement and after checking that the performance lock was free,
no bootstrap process was active, and no live cache lease existed, the human
explicitly authorized deletion of
`build/bootstrap-pytest-object-cache`.  That exact 89 GB directory was
removed; it is recoverable only by recompilation.  The isolated measurement
caches remain.

## Profile and proposal

The largest current `pcc.cli_bootstrap` shard was 4,495,046 bytes, 89,448
lines, and one function.  Baseline pcc1 emitted it in 47.27 s with 512.66
billion instructions and a 4.60 GB peak physical footprint.  Host cProfile on
the same shard attributed 4.495 s of 30.044 s to
`plan_aarch64_canonical_error_fallthroughs`, including 4,507,467
`text_key_names_equal` calls.

The old pass linearly searched the rest of a mutable block list for every
canonical success target, then used `pop`/`insert`.  The replacement builds a
block-name-to-original-index mapping, maintains current order in integer
`previous`/`next` arrays, marks processed nodes, and materializes the final
list once.  The recognizer and edge ordering are unchanged, and the existing
text-key lookup retains the native inconsistent-hash recovery contract.

A deterministic 240-block regression compares the new pass with the exact
slow reference algorithm.  It requires identical block layout and edge lists,
observes more than 6,000 oracle scan comparisons, and caps the new recognizer
at two text comparisons per canonical edge.

## Exact-shard A/B

The receipt-bound baseline and candidate pcc1 builds differed in exactly
`pcc/backend/self_backend_aarch64_darwin_flow.py`.  Three alternating pairs on
the frozen largest shard produced byte-identical assembly in every run:

| pair | baseline wall | candidate wall |
|---|---:|---:|
| 1 | 41.60 s | 30.29 s |
| 2 | 41.57 s | 30.26 s |
| 3 | 41.45 s | 30.34 s |

Median wall speedup was 1.37339x, CPU speedup 1.37450x, candidate/baseline
instructions 0.78354, cycles 0.72904, and peak physical footprint 0.99997.
The A/B manifest is
`build/stage2-canonical-error-layout-emit-ab-v2/manifest.json`.

## Receipt-built pcc1 control

The first full candidate Stage2 failed with an empty generic frontend error.
It was not valid attribution: the candidate pcc1 built through
`run_pcc_stage1_build.py` failed at 206.460 s, but an otherwise matched
baseline-source pcc1 built through the same tool failed at 163.404 s with the
same error.  Their source manifests differed only in the proposed flow file.
Every one of the candidate's 463 emitted shards and 48 original split inputs
also compiled successfully in isolation.

This paired control assigns that red result to an unresolved difference in the
receipt-bound Stage1 build protocol, not to the optimization or the cache
directory.  Acceptance therefore used the repository's ordinary bootstrap
path.

## Ordinary bootstrap result

Candidate flow source SHA-256 was
`b4d991c22ee94d644b4884de99d0c1cd15a991375352e8f25bfed572c934ad57`.
Ordinary pcc0 -> pcc1 completed in 298.027 s and produced pcc1
`e984196bd53a5e081cdc62d5d1971e2a65069fb6e02afd823ea01a649fa3cb9d`.
Its Stage2 used another verified-empty cache at
`build/stage2-canonical-error-layout-bootstrap-candidate-normal-v1/cache-stage2-fresh`.

Cold Stage2 succeeded in 1109.920 s and produced pcc2
`d5cfbb0415659d365f32afc57485a913e70854e5358ea2ec850dfd5bc2a1436f`.

| phase | baseline | candidate | delta |
|---|---:|---:|---:|
| compile total | 1351.473 s | 1105.537 s | -245.936 s |
| backend IR texts | 1037.970 s | 838.457 s | -199.513 s |
| native emit | 897.916 s | 708.549 s | -189.367 s |
| oversized workers | 289.843 s | 95.553 s | -194.290 s |
| safe workers | 586.496 s | 591.968 s | +5.472 s |
| frontend codegen | 195.522 s | 201.571 s | +6.049 s |
| link driver | 139.083 s | 129.470 s | -9.613 s |

The win is therefore localized: the seven oversized native objects improved
3.03x; the 456 safe objects did not improve.  Counters were otherwise matched:
212 frontend modules/actions, 463 object misses, seven oversized objects, and
48 split modules in both cold runs.

Stage3 completed in 302.363 s with all 463 object-cache hits.  It produced
pcc3 with the same SHA-256 as pcc2, so `cmp` passed byte-for-byte.

## Gates

```text
tests/c/test_self_backend_aarch64_cold_paths.py
  3 passed in 0.13s

tests/python/test_bootstrap_gate_baseline.py
  2 passed, 2 deselected in 0.56s

tests/python/test_ir_py_fallback_baseline.py
  8 passed in 0.99s

tests/python/test_fallback_baseline.py
  32 passed in 488.25s

strict no-libpython flow-module closure emit
  rc=0

ordinary pcc0 -> pcc1 -> pcc2 -> pcc3
  stage1 298.027s; cold stage2 1109.920s; stage3 302.363s
  pcc2/pcc3 byte-identical
```

Durable pytest logs are under
`build/stage2-canonical-error-layout-bootstrap-candidate-normal-v1/`.

## Open boundary

The 600 s target is still missed by 509.920 s, and only one full cold candidate
run exists.  The next measured owner is the 591.968 s safe-worker lane, not
cache retention and no longer the oversized canonical-layout scan.  Continue
with a representative safe-shard caller profile and one proposal at a time;
then run matched cold/hot pairs.  Do not describe this slice as overall Stage2
closure, three-pair proof, five-GC equality, or general Python performance.
