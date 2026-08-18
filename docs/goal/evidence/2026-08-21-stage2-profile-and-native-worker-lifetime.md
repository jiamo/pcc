# Stage2 profile and native emit-worker lifetime

Date: 2026-08-21

Claim level: current-source GC0 self/no-libpython performance evidence.  This
does not close fixed point, five-GC equality, or stage2 parity with stage1.

## Frozen identities

- bootstrap source manifest:
  `91b940cdeec1e8ec267f1b4adfcf01ae74740f61b4855f322474d2007ef83815`
- pcc1:
  `aaeffa06d1a251f622aa057928644e0f41cd863cc6d2de3755a9028ef46eb4e5`
- pcc2:
  `7a80a9ec42f592c5a4b5f12df984d8ba238055d4db1d826a5136a8084a83b4f3`
- runtime archive:
  `4ffd3d021d5af696cd996d40d4d588040c9509f44f07d56c3643b2eff03e3e1b`
- mode: Darwin arm64, GC0, `backend=self`, `python-libpython=off`,
  frontend jobs 10, self-backend jobs 8, Mach-O jobs 8, frontend and object
  caches disabled, Python IR passes disabled.
- linkage: both pcc1 and pcc2 load only `/usr/lib/libSystem.B.dylib`.

## Complete stages

| metric | host pcc0 -> pcc1 | pcc1 -> pcc2 | ratio |
|---|---:|---:|---:|
| wall | 266.54 s | 875.10 s | 3.283x |
| user + system CPU | 1001.09 s | 5109.59 s | 5.104x |
| maximum RSS reported by `time -lp` | 6,273,318,912 B | 9,834,496,000 B | 1.568x |
| peak footprint reported by `time -lp` | 1,394,771,456 B | 17,995,822,208 B | diagnostic only |

The stage2 result is a successful pcc2, not a timeout.  It replaces the prior
current-source observation where stage2 exceeded 900 seconds and had emitted
only 455 of 502 planned assembly inputs.

## Phase comparison

| phase | stage1 | stage2 | ratio |
|---|---:|---:|---:|
| frontend codegen parallel | 31.348 s | 171.186 s | 5.46x |
| native emit | 94.765 s | 516.717 s | 5.45x |
| safe native emit workers | 94.417 s | 444.854 s | 4.71x |
| pcc-owned link driver | 104.113 s | 99.719 s | 0.96x |
| complete self-backend link | 204.525 s | 616.851 s | 3.02x |

Stage2 used 212 frontend chunks versus stage1's 40, while both compiled 212
modules.  Stage2 emitted seven oversized objects and 448 safe objects.  The
link driver is not slower than stage1; frontend work and native emit are the
two red owners.

## Worker-lifetime change

The implementation now gives every native safe emit item a fresh pcc process,
while the host/source stage1 emitter retains its four-item batch.  It does not
change IR, object-cache identity, item order, lane order, worker concurrency,
or the emit algorithm.

A pre-implementation real three-item medium-shard check showed:

- batch of three in one process: wall 34.49 s, max RSS 4,823,400,448 B;
- the same three items in three sequential fresh processes: summed wall
  34.47 s, maximum per-run RSS 2,548,809,728 B;
- all three assembly files were byte-identical.

In the complete stage2, all safe manifests contained exactly one item and the
profile recorded 448 safe items / 448 safe worker processes.  Periodic
synchronized process-tree RSS observations were:

- frontend/coordinator: 5.54, 8.73, 10.25, and 8.54 GB;
- early native emit: 9.63 GB;
- middle/late native emit: generally 4.30--7.05 GB;
- prior current-source emit observation: at least 15.89 GB.

Thus the full-run observed peak fell by at least about 35.5% and the old
cross-item worker growth disappeared.  The task remains red: the observed
peak and `time -lp` maximum RSS both exceed 8 GB, and stage2 is still 3.28x
stage1.  This is a retained partial improvement, not DONE_STRONG.

## CPU profile selection

The prior real stage2 self-emit flamegraph contains 11,906 samples:

- precise-stackmap namespace inclusive: 4,252 (35.71%);
- `build_stack_map_plans`: 3,659 (30.73%);
- strict GC/refcount leaves below stack-plan: 2,232 (61.0% of stack-plan);
- `CompactParsedInstrArena` iterator/getitem union: 784 (6.58% overall,
  21.43% of stack-plan);
- IRBuilder/llvm-capi call rendering: no attributable path in this profile.

The precise-stackmap-private cursor hypothesis was then implemented and
fail-first measured.  After removing an independent `typing.cast` ownership
error, two real oversized-shard comparisons were correct and byte-identical
but only about 1.04x faster.  On the most stackmap-dense retained shard the
measured pair was 36.78 s candidate versus 38.25 s baseline; CPU was 5.9%
lower, instructions 6.9% lower, and max RSS 0.6% lower.  This misses the
pre-registered 1.08x wall threshold, so the cursor implementation is
`[DENIED]` and removed.  Do not retry generator/view removal alone.  The next
bounded hypothesis must address the measured 61.0% stack-plan GC/refcount
traffic or another larger structural owner.

### Exact dense-shard GC-tax attribution

The retained baseline pcc1 then replayed the same dense shard under the
existing CPU flamegraph tool.  The 16,595-sample capture is retained at
`build/stage2-gc-tax-profile-v1/dense-shard.{folded,svg}`.  It attributes
3,717 samples (22.40%) to stack-map planning and 3,643 (21.95%) to the
per-function planner.  Within the latter,
`pcc_gc_managed_pointer_find_slot` is only 331 samples (2.00% of the complete
capture), so even its complete removal has a 1.02035x Amdahl ceiling.  Minor
graph locking, object-graph locking, retain/release, roots/frames, allocation
and zeroing are separate peers; the profile does not justify deleting any GC
barrier or optimizing only the index query.

`pcc_flamegraph.py peak/heap` was also exercised against the same binary with
malloc stack logging enabled.  macOS supplied no usable high-water/allocation
history for this freestanding allocator, so those modes produced no live-set
claim and the tool was left unchanged.

An eight-field ordinary-class versus eight-item-tuple native discriminator ran
2,000,000 construction/consumption iterations in one binary.  Tuple pairs were
5.02/4.99 s versus class pairs 5.42/5.46 s, with about 13.8% lower RSS.  The
paired-median micro speedup is 1.0869x, but the measured delta extrapolates to
only about 0.0082 s for the real shard's 37,545 stack-map records.  Record
representation is therefore not the stage2 owner and no production
class-to-tuple change was made.  Both this shape and a `find_slot`-only edit
are rejected; the next candidate must cover the whole lifecycle/dataflow group
or move to the independent frontend/coordinator owner.

### Frontend singleton-worker attribution

The accepted baseline pcc1 replayed the retained V4 singleton worker for
`pcc.py_frontend.codegen.class_gen` (assigned index 81) using the original AST
and native-export wires.  Only result/IR destinations were redirected into
`build/stage2-frontend-worker-profile-v1`.  The process returned zero, stdout
was empty, and its 10,888,793-byte IR matched the retained stage2 module
byte-for-byte at SHA256
`19f1c3b6d0278941f30e35c9ae7ea67a21b301b3e85c7018ae0b37ffb10030ea`.

The diagnostic run measured 27.73 s wall, 26.82 s user+system, 2.519 GB max
RSS and 2.474 GB peak footprint.  Worker timing separated 0.618 s AST-wire
read, 1.089 s inference and 24.601 s codegen.  The existing CPU flamegraph
captured 10,785 samples (`worker.cpu.folded` SHA256
`c48b17388e1d2d6c555535b67b411af3d25f4d07b2932cb677cece0b6ab16616`).

`IRBuilder_call*` is a meaningful 18.24% inclusive path, but not a unique root
cause: GC/refcount leaves are 59.54% and distributed across hoist analysis,
IRBuilder, vthread effects, class-model lookup, rendering and ordinary
lowering.  The narrowest newly measured owner is
`compute_free_names.__nested_walk`: 1,653 samples (15.33%), including 1,051
direct GC/ref leaves.  Its preserved generated IR has 81 frame-enter sites:
33 plain common-entry registrations plus 48 path-specific LIFO registrations.
Rare comprehension/lambda branch locals share the same large function.  This
justifies one fail-first common/cold control-flow split, not a global barrier
reduction.  It remains diagnostic evidence until an unsampled, byte-identical
matched A/B clears the 1.08x worker wall gate.

## Verification and limits

- focused native/source batching gates: 12 passed;
- host source control explicitly locks 10 items into 4/4/2 manifests;
- an attempted whole `test_py_multi_file_bootstrap_shim.py` run stopped at an
  unrelated pre-existing native-extension alias assertion, as required by
  `-x`; it is not reported as green evidence;
- pcc2/pcc3 equality, semantic parity, and GC1..4 remain unrun;
- `time -lp` peak footprint is not a synchronized process-tree estimator, so
  it is retained as diagnostic data rather than substituted for sampled RSS.
