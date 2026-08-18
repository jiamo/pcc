# Investigation: pcc1 exact-string concat chains allocate one object per edge

## Status

resolved

## Problem Description

The accepted No.89 GC0/AArch64 Stage2 is 598.629 seconds against a 275.13
second same-source Stage1.  The source-frozen profiles do not contain another
single algorithmic leaf large enough to close that gap: GC/refcount/object
lifecycle is distributed across the frontend and native-emitter workers.

This investigation reduces that distributed tax to one representation family.
During the first 60 seconds of a real No.89 frontend worker for
`pcc.cli_bootstrap_pytest`, the existing runtime allocation log recorded
969,826 allocation requests before the diagnostic watchdog stopped the run:

```text
type                    requests    share       bytes    byte share
str (tag 4)              664,852    68.55%  47,046,861       77.21%
list (tag 5)             180,569    18.62%   7,222,760       11.85%
dict (tag 6)              77,016     7.94%   4,312,896        7.08%
tuple (tag 7)             40,595     4.19%   1,772,592        2.91%
```

The string distribution is dominated by payload lengths 1--12.  Static AST
sizing finds 1,231 maximal 3+-operand `+` chains in the pcc Python source; 1,182
(96.0%) have 3--8 leaves.  `pcc/llvm_capi/ir.py` owns 116 and
`pcc/cli_bootstrap.py` owns 117.  Current lowering evaluates a left-associated
exact-string chain through `py_str_concat` once per edge, allocating and then
releasing every prefix object.

This is distinct from the callee-signature cache, fixed-arity call renderer,
`_text_lines`, and per-type text-cache proposals already measured in
[`pcc1-stage2-emit-throughput-and-memory.md`](pcc1-stage2-emit-throughput-and-memory.md).
Those changed one IRBuilder lookup/render site.  The present candidate is a
generic exact-`StrType` lowering rule that removes a whole immutable prefix
lifecycle across every admitted source chain.  It also follows the structural
boundary in
[`pcc1-native-vs-cpython-per-operation-cost.md`](pcc1-native-vs-cpython-per-operation-cost.md):
short-lived strings are the largest remaining value-projection opportunity.

Two alternate explanations have already been controlled:

- a same-source LLVM-built pcc1 and No.89 self-built pcc1 emitted
  byte-identical item311 assembly; LLVM saved only 4.2% instructions and about
  2% wall;
- on the exact frontend worker the LLVM control saved 2.2% instructions and
  5.7% cycles.  The one wall pair was scheduler-contaminated and is not used as
  a speed claim.  Self-backend machine-code quality therefore lacks a 25%
  ceiling on both representative paths;
- replacing a five-part chain with `"".join([parts])` is `[DENIED]`: the
  native runtime retired 30.474B instructions versus 23.152B for the chain
  (1.316x worse), with equal output.  A Python list/join projection is not the
  proposed implementation.

## Repro

The allocation-type observation is reproducible from the frozen No.89 worker
manifest by selecting module 7 and enabling the existing allocation log:

```text
PCC_GC_BACKEND=0 PCC_PYTHON_IR_PASSES=off PYTHONHASHSEED=0 \
PCC_LOG=alloc PCC_LOG_FORMAT=json PCC_LOG_FILE=- \
build/no89-call-span-stage1-candidate-315-v1/pcc1 \
  --pcc-python-multi-codegen-worker \
  build/no99-alloc-type-module7/worker.manifest
```

The diagnostic watchdog is intentionally 60 seconds because per-event JSON
logging is prohibitively expensive.  Expected diagnostic result is timeout,
not a green worker: the durable partial log must contain allocation request
and object pairs and the type distribution above.  It must never be used as a
timing result.

The no-logging accepted worker and emit controls remain:

```text
build/no89-frontend-worker0-replay-v1/profile/worker.manifest
build/stage2-current-object-inputs-no62-v1/item_311.ll
```

## Test [CONFIRMED]

The allocation diagnostic was observed under the command above: watchdog
status `TIMEOUT`, no surviving child, 1,939,651 complete/partial JSON lines,
969,826 complete `alloc_request` records, and the distribution in the problem
statement.  The ordinary No.89 frontend replay is independently green with
exact IR SHA-256 `065100ba25f24b5ef5d423b4ed6246058e5d1f4fe7f1d152c1f4176f574a77a7`.

The implementation gate must add focused regressions for:

- exact `StrType` chains of lengths 3 through 8, with exact output and one
  fused runtime call;
- strict left-to-right operand evaluation and exception cleanup;
- a dynamic operand, user-class operands, bytes, and two-part strings staying
  on their existing paths;
- empty strings, non-ASCII strings, overflow/OOM fail-closed behavior, and the
  GC0 moving-input-independent path;
- C/pcc-Python runtime mirror shape.  GC1--4 execution remains deliberately
  deferred until the user-ordered post-performance five-GC transfer.

## Proposals

- No.1 Fuse exact `StrType` chains through one bounded concat ABI [pending]

## No.1 Fuse exact `StrType` chains through one bounded concat ABI

### Code Change

Add one bounded runtime ABI accepting 3--8 already-evaluated exact strings,
allocating the output once and copying each payload once.  The moving-GC arm
must copy inputs to raw temporary storage before the output allocation; the
non-moving arm may allocate the output first.  Mirror the ABI in the C oracle
and freestanding pcc-Python runtime.

At expression lowering, flatten only a maximal `+` tree with 3--8 leaves when
every leaf's inferred type is `StrType`.  Evaluate leaves left-to-right, keep
each native pointer pinned/root-cleanup-safe while later leaves execute, call
the bounded ABI once, and release the original owners exactly once.  A Dyn
leaf, class/dunder receiver, unsupported length, CPython value, or uncertain
ownership keeps the current nested slow path.  Do not globally intern strings,
change `is`, weaken arbitrary Python class semantics, or use list/join.

Pre-registered performance disposition:

- first require a source-frozen candidate pcc1 and exact item311 assembly.
  The frontend worker compiles pcc source containing admitted chains, so its
  candidate IR is expected to differ: two candidate repetitions must instead
  be byte-identical to each other, and a structural diff against No.89 must be
  confined to the bounded concat call plus its corresponding operand
  root/pin/release lifetime.  Runnable pcc2 and the later fixed point, not a
  contradictory baseline byte-compare, are the semantic gate;
- accept only if three alternating frontend-worker pairs show median wall and
  CPU speedup at least 1.15x, candidate instructions at most 0.90x, and
  footprint/tree RSS at most 1.02x; a first stable pair below 1.08x may stop
  and deny;
- if accepted at the worker boundary, run a cache-off GC0 Stage2 only after
  the implementation is source-frozen; Stage3/fixed point follows only after
  Stage2 is no slower than the non-regressed Stage1 target;
- any semantic, ownership, output, or allocation-count regression is an
  immediate denial regardless of speed.  GC1--4 are not run or claimed before
  the user-ordered performance milestone.

### DENIED

The candidate was correct on its focused GC0 boundary, built a source-frozen
pcc1, retained exact item311 assembly, and improved a five-part runtime micro
about 1.19x.  It did not transfer to the real frontend worker: the first
balanced pair measured only 1.01078x wall and 1.00509x CPU, with instructions
0.97712x and sampled tree RSS 1.04724x.  This crossed the pre-registered early
denial line, so no more pairs or Stage2 ran.

The real worker replaced 251 binary concat references with 85 bounded calls,
but retired only 2.3% fewer instructions.  Its dominant short strings come
from the larger two-part/conversion/slice/name producer family, not 3--8-leaf
prefix chains.  All candidate production and test source was removed by
forward patch and the production files compare byte-for-byte with No.89.
Complete receipts are in
[`020-no99-exact-str-chain-denied.md`](../goal/evidence/PERF-P0-PROVEN-NATIVE-DATA-PLANE-PARALLEL-EMIT/020-no99-exact-str-chain-denied.md).

## Report

No proposal landed.  No.1 proved that one-allocation concatenation is useful
when it actually owns the workload, but its source-wide admitted domain is too
small to be a Stage2 lever.  The successor boundary is an end-to-end
compiler-text value/arena projection across producers and consumers, not more
concat arities, ownership tweaks, IRBuilder caches, or rendering helpers.
