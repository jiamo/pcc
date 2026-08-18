# Stage2 block-local last-use analysis reuse

Date: 2026-08-26  
Task: `PERF-P0-STAGE2-COLD-CACHE-REGRESSION`  
Mode: Darwin arm64, GC0, self backend, no libpython, IR scaffold on, ten
frontend/self-backend workers.

## Claim

`assign_stack_slots` and AArch64 register allocation performed the same pure
`collect_block_local_last_uses` whole-function analysis back-to-back.  Caching
the stackprep result on `ParsedFunction` and retaining a compute-on-miss path
for direct regalloc callers improves an exact current pcc1 safe-shard emit by
about 3% with byte-identical assembly.  A normal current-source cold Stage2
also succeeded in 1076.793 s, 33.127 s (2.98%) below the immediately preceding
1109.920 s accepted source, and the pcc2/pcc3 fixed point remained
byte-identical.

The end-to-end 33.127 s delta is real for the source pair but is not attributed
entirely to the removed analysis: the compiler-source IR shape changed by one
safe object and phase wall moved between frontend and native lanes.  The exact
shard A/B is the mechanism proof.

This slice does not close the P0.  Cold Stage2 remains 476.793 s above the
600 s target, and three alternating full cold/hot build pairs remain open.

## Safe-shard localization

After the canonical-error layout optimization, the largest IR object below
the 2,000,000-byte safe/oversized boundary was 1,973,250 bytes, with 19
functions and 3,596 blocks:

```text
input SHA256  6d52e8b9f335c050d4c58ef50e72cd32eee428ed80211f329b6ead5f6f8aff54
pcc1 SHA256   e984196bd53a5e081cdc62d5d1971e2a65069fb6e02afd823ea01a649fa3cb9d
wall          18.30s
instructions  197,179,961,966
peak footprint 2,257,930,280 bytes
```

A complete 12-second pcc1 call graph contained 9,962 samples.  Inclusive
owners were function emission 43.56%, precise stack-map construction 34.38%,
module preparation 7.98%, global emission 5.17%, and the adjacent-memory
target pass 4.77%.  The largest leaf,
`pcc_gc_granule_is_object_start` at 16.65%, was distributed across many owners
and did not identify a safe edit.

Host cProfile on the identical IR exposed the structural duplicate:

```text
collect_block_local_last_uses    38 calls / 0.760s cumulative
  assign_stack_slots             19 calls / 0.386s
  allocate_aarch64_block_registers
                                 19 calls / 0.374s
```

The analysis reads parsed blocks, phis, instruction kind/data and terminators;
the slot/type fields changed between the calls are not inputs.  The candidate
publishes the mapping during stackprep and regalloc reuses it.  A function that
did not run stackprep still computes the mapping locally.

With host cProfile the assembly SHA-256 stayed
`8f50906c97c688f4d8bd7c8eddd304d7f893cf10b89442a9899188ab2f6da276`,
calls fell from 27.99M to 25.88M, total profiled time fell 6.342 s -> 5.867 s,
and the collector fell from 38 calls to 19.

## Receipt-bound pcc1 A/B

Matched baseline and candidate pcc1 builds used runtime archive
`0f9b409ad71d4474add9e8da8dadc3b96ffba0dbd9bd596ac6394495526b2fa6`
and identical host Python/external-tool receipts.  Their source manifests
differed only in:

- `pcc/backend/self_backend_ir.py`
- `pcc/backend/self_backend_stackprep.py`
- `pcc/backend/self_backend_aarch64_darwin_regalloc.py`

The three alternating pairs all emitted the same assembly:

| pair | baseline wall | candidate wall |
|---|---:|---:|
| 1 | 16.01 s | 15.53 s |
| 2 | 15.98 s | 15.53 s |
| 3 | 16.10 s | 15.64 s |

Median results:

```text
wall speedup                 1.02941x
CPU speedup                  1.03143x
candidate/base instructions  0.97633
candidate/base cycles        0.97073
candidate/base footprint     0.97105
```

Manifest:
`build/stage2-last-use-emit-ab-v1/manifest.json`.

## Receipt-build correctness confounder

The receipt-built candidate pcc1 passed the function-definition native-binary
test but failed the GC0 compile smoke with an empty `PCC-PY-COMPILE-001`.
This was not candidate causality: the matched receipt-built baseline pcc1
failed the exact same input in 0.51 s with the same error.  The ordinary
current-source bootstrap pcc1
`31d6ac3bc4f9b217ac9a15336c99e7f0e1f6f1e7d54e7159ed14becad13a0393`
compiled and ran that input, printing `2016`, `8128`, and `True`.

Receipt-built pcc1 remains suitable for direct emit A/Bs but not for integrated
compile/bootstrap correctness gates until its build-protocol difference is
fixed.  Correctness acceptance below uses the ordinary pcc1.

## Full cold Stage2 and fixed point

The ordinary pcc1 was copied into a new output root.  Stage2 used a verified
empty isolated cache.  It completed in 1076.793 s and produced pcc2
`1c86b82ddeb18872441ac691b6b6676778a9b588b411d58134fc39f59a904787`.

Compared with the immediately preceding accepted No.60 source:

| phase | No.60 | No.62 | delta |
|---|---:|---:|---:|
| total Stage2 wall | 1109.920 s | 1076.793 s | -33.127 s |
| compiler profile | 1105.537 s | 1072.209 s | -33.328 s |
| frontend codegen parallel | 201.571 s | 168.235 s | -33.336 s |
| native emit | 708.549 s | 711.927 s | +3.378 s |
| oversized workers | 95.553 s | 91.261 s | -4.292 s |
| safe workers | 591.968 s | 600.185 s | +8.217 s |
| link driver | 129.470 s | 132.223 s | +2.753 s |

Both runs compiled 212 frontend modules and split 48 modules.  No.62 generated
464 native objects rather than 463: seven oversized and 457 safe, versus seven
and 456.  This IR-shape difference is why the full wall delta is not used as
the proof that last-use reuse accelerated the safe lane.  The single-input
receipt-bound A/B above isolates that mechanism.

Stage3 completed in 336.860 s with 464/464 object-cache hits.  Its pcc3 SHA-256
is the same as pcc2, so `cmp` passed byte-for-byte.

## Gates

```text
three strict no-libpython module closure emits
  rc=0

self backend + precise stack maps + focused layouts
  355 passed in 5.08s

ordinary pcc1 GC0..4 compile smoke
  5 passed in 101.16s

bootstrap baseline + IR fallback
  10 passed, 2 deselected in 1.78s

fallback baseline
  32 passed in 558.89s

ordinary pcc1 -> pcc2 -> pcc3
  cold Stage2 1076.793s; Stage3 336.860s
  pcc2/pcc3 byte-identical
```

## Open boundary

No.62 proves a small per-input native emit win but did not reduce the complete
safe-worker critical-path wall in this one cold run.  The next investigation
must identify the actual critical safe batch/item rather than assume the
largest sub-threshold IR file determines the lane.  Add or use bounded
per-batch/per-item timing receipts, profile the real longest safe worker, and
register one structural proposal.  Do not count frontend parallel variance as
native-emitter attribution, and do not rerun another full cold Stage2 until an
exact critical-path A/B clears its declared floor.
