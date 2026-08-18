# Investigation: inline post-call error edges instead of one CFG triplet per call

## Status

active

## Problem Description

Current pcc1 Stage2 is predicted at about 1022 seconds versus a 149.15-second
host Stage1.  Emit-local order storage has been exhausted by two measured
denials.  The remaining representation multiplier begins in the frontend:
every raise-capable call splits the normal path into a `call.cont` block and,
when ownership cleanup is needed, adds `call.err.cleanup`; traceback recording
adds an `err.frame` block keyed by source line.  The AArch64 cold-path planner
can remove a final success jump, but verifier, stack preparation, precise
stack maps and direct finalization have already paid for all CFG nodes.

Predecessors:

- `pcc1-stage2-emit-throughput-and-memory.md` (post-call cache and cold-path
  history);
- `pcc1-frontend-direct-indexed-kernel-plane.md` (current full-cost profile and
  exhausted order-storage candidates);
- `emission-site-err-check-audit.md` (missing checks remain correctness work,
  never candidates for deletion).

## Repro

The retained v8 `class_gen` AST was compiled by host pcc with direct capture
disabled so canonical LLVM CFG remains visible:

```text
build/classgen-cleanup-sizing-v1/out/module_87.ll
bytes                    14,943,795
blocks                       24,274
instructions                180,641
call.cont blocks              2,773
call.err.cleanup blocks       1,310 / 8,880 instructions
err.frame blocks              1,309 / 7,855 instructions
```

The three generated families are 5,392 blocks (22.2% of the function CFG
corpus).  Cleanup plus traceback alone are 9.26% of instructions; continuation
blocks additionally fragment all normal code and multiply per-block analysis.

The corresponding v15 full-cost pcc1 profile is
`build/classgen-full-cost-profile-v17/emit.folded`: direct finalization is
31.36%, stack-map planning 17.89%, verification 10.95%, and the emitter 41.0%.
All consume the expanded CFG.

## Test [CONFIRMED]

The representation failure above is confirmed on the exact retained AST and
current canonical LLVM output.  Existing focused cold-path tests prove only
final AArch64 fallthrough; they do not change the block counts above.

The first implementation tests must prove a no-cleanup raise-capable call can
publish one inline error edge in direct/no-text mode while the text/LLVM oracle
retains its historical branch+continuation CFG.  Direct and text arms must
emit byte-identical AArch64 and execute identically before cleanup state is
added.

## Proposals

- No.1 Direct inline error-edge record plus shared function error exits
  `[pending]`

## No.1 Direct inline error-edge record plus shared function error exits

### Code Change

Add a direct-kernel error-edge plane attached to the raise-capable call (or its
`py_err_occurred` check): error target, source-location ID and cleanup-plan ID.
The normal successor stays in the same logical instruction stream, so no
`call.cont` block is created on the direct/no-text self path.  Text/LLVM modes
keep the existing CFG as the differential oracle.

Start with cleanup-plan ID zero: no owned/pinned/rooted temporaries, one shared
function error/traceback exit.  Extend only after direct/text output and root
state agree.  Nonzero cleanup plans use fixed function-local state slots and a
shared cleanup dispatcher; they must preserve evaluation order, pin/unpin,
owned releases, LIFO root leave, finalizer reentrancy, try-handler targets and
source traceback lines.  Unsupported shapes retain the old explicit blocks
and increment a visible fallback counter.

The precise-stackmap analysis consumes the inline edge as a real exceptional
successor at the safepoint; it may not erase the edge or assume root state from
the normal path.  AArch64 emission generates the same error branch and cold
exit as today.  Archive/link order and ordinary Python exception semantics do
not change.

### pending

Pre-registered gates: focused direct/text error-edge differential, try/raise/
traceback and ownership cleanup tests, precise-stackmap join/root equality,
GC0/3/4 execution, strict no-libpython closure, exact class_gen assembly and a
source-shape counter.  Before Stage1, the host full-cost worker must improve at
least 1.15x or reduce block count by at least 15% with no CPU/RSS regression.
The pcc1 full-cost worker must improve wall/CPU at least 1.20x and keep RSS
within 1.02x.  No Stage2 until a refreshed v8-manifest prediction fits 600
seconds.  Any semantic mismatch disables the inline plane by forward patch;
never remove an error edge to obtain a number.

## Update 2026-09-01 — cleanup-plan-zero tracer is end-to-end in the direct kernel

### Code Change

The first opt-in tracer now removes the normal continuation block rather than
merely mirroring an explicit conditional terminator. A packed six-scalar edge
and per-block spans are consumed by direct reachability/finalization, verifier
CFG/type/dominance checks, fused/non-fused use indexing, exact trigger-time root
state, managed liveness, exceptional stack-map discovery and both indexed
AArch64 emit loops. The frontend selects it only for no-cleanup function-exit
edges; try handlers and all owned/pinned/rooted cleanup shapes remain explicit.
The text/default path retains its previous block creation order.

The construction owner uses native head/tail/next arenas rather than a Python
list per block. A real frontend `return int(value)` canary now publishes an
inline edge, reduces `call.cont`, and reaches AArch64 emission. The minimized
fused-use canary has two direct logical blocks (`entry`, `error`) versus three
oracle blocks (`entry`, `error`, `normal`) and emits `cbnz` directly to the
error target.

### Test [CONFIRMED]

Evidence:
`docs/goal/evidence/PERF-P0-INLINE-ERROR-EDGE-DATA-PLANE/001-cleanup-zero-inline-edge-tracer.md`.

Focused results are 12 direct-kernel tests, 61 verifier/precise-stackmap/
inventory tests, three explicit-cleanup ownership tests, and one strict
no-libpython self closure check, all green. One llvmlite probe hit the existing
`FunctionAttributes._attrs` incompatibility before this boundary and is not
claimed.

### Measurement correction

The 149.15-second Stage1 and roughly 1022-second Stage2 prediction use
different external resource envelopes. They remain useful operational
receipts but do not prove a pcc1/host speed ratio. Task
`PERF-P0-STAGE-RESOURCE-ENVELOPE-PARITY` now requires the eventual pair to use
the same 8 GiB process-tree cap and CPU availability, with dynamic live-RSS
admission inside that common cap.

### Status

`[CONFIRMED]` for the opt-in cleanup-plan-zero representation and its direct
kernel consumers. The investigation remains active: no class-gen sizing or
host/pcc1 full-cost measurement has been run; the flag remains off by default;
runtime traceback/GC0/3/4 evidence, shared per-line frame exits, try edges and
nonzero cleanup plans remain open. No Stage1 or Stage2 run is authorized by
this update.

## Update 2026-09-02 — cleanup/try edges land; class_gen exposes three stack-map defects

### Code Change

`_emit_post_call_err_check` now inlines every direct-mode check: try-handler
targets (`try.err`/`err.exit` carry no PHIs, so the target contract holds) and
cleanup shapes, whose `call.err.cleanup` block is reached by the edge instead
of a `cbranch`.  The cleanup body moved to `_emit_post_call_error_cleanup` and
is shared by both routes; the text path keeps its creation order.

Sizing the real class_gen worker and a real differential program then failed
four times, each a genuine defect in the committed tracer or in the frontend's
assumptions about the text CFG (see evidence 002 for the list): trigger index
drift, edge-only blocks silently unplanned by the stack-map worklist, entry
root hoists landing after the first edge, and edge live-in placed at block end.
All four are fixed with focused tests; two new diagnostics name the block,
callee, slot and safepoint.

### Test [CONFIRMED]

Evidence:
`docs/goal/evidence/PERF-P0-INLINE-ERROR-EDGE-DATA-PLANE/002-cleanup-try-inline-edges-stackmap-fixes.md`.
14 direct-kernel tests, 61 verifier/stackmap/inventory tests, GC0/3/4
byte-identical runtime differential, class_gen direct emission OK.

### Measurement

class_gen direct kernel: 21,405 -> 18,633 blocks (-12.95%), zero `call.cont`,
2,772 inline edges; `call.err.cleanup` 1,310 and `err.frame` 1,309 remain.
Timing from these runs is not evidence (concurrent builds).

### [DENIED] as a performance solution: shared cleanup dispatcher without measurement

Not implemented: sharing cleanup exits needs the released/unpinned operands in
fixed function-local state slots, i.e. hot-path stores at 1,310 call sites,
to remove 1,310 cold blocks.  It is a tradeoff to be measured on pcc1, not a
free block reduction; record it as a candidate only.

### Status

`[CONFIRMED]` for cleanup/try inline edges and the four fixes.  Active: the
15% class_gen line needs the per-line `err.frame` merge; no A/B or Stage
run has happened.

## Update 2026-09-02 (2) — shared traceback landings: class_gen -17.04%

### Code Change

Per-line `err.frame` blocks are replaced in direct mode by one landing per
(function, error target).  Edge records grew to eight scalars (payload index);
the kernel carries a (landing block, i32 slot) table; the AArch64 emitter
branches an edge into a landing through a per-edge cold stub (`mov`/`str`/`b`)
appended after the function's blocks; explicit cleanup blocks store the payload
themselves.  The landing calls the new `py_exc_append_frame_indexed` (C and
port) with two module constant tables sized at the end of codegen.  The
verifier enforces payload/landing consistency.

### Test [CONFIRMED]

Evidence:
`docs/goal/evidence/PERF-P0-INLINE-ERROR-EDGE-DATA-PLANE/003-shared-frame-landings-classgen-17pct.md`.
class_gen direct kernel 21,405 -> 17,758 blocks (-17.04%), 350 landings for
1,309 former frame blocks; 75 focused tests; six changed backend files pass
the strict closure emit; the text-vs-direct runtime differential is
byte-identical on GC0/3/4 including a four-frame traceback.

### [DENIED] lazily reading source files at traceback print time

Considered to avoid the source-text table: it changes output whenever the
source is absent or moved at run time, which the deployed-binary traceback
contract forbids.  The (line, source) pair stays baked in, indexed.

### Status

`[CONFIRMED]` for the shared landing representation.  Active: alternating
host A/B, then one Stage1 build for the pcc1 full-cost A/B, then default
enablement and the Stage2 prediction refresh.

## Update 2026-09-02 (3) — host emit regression attributed and fixed in stackprep

### Measurement

Host alternating A/B (two clean pairs, `run_process_tree_sample.py`, same
compiler, only `PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE` differs): the ON arm was
3.5% slower wall and 8% slower emit despite 17% fewer blocks.  cProfile
off/on on the same class_gen worker (relative attribution only; the machine
was under external swap pressure) names the owner:

```text
d_tottime  calls_off  calls_on  function
  +0.513    150,447    657,913  self_backend_stackprep.py:maybe_free_local_value
  +0.422     19,030    528,978  self_backend_kernel.py:value_id
  +0.279    226,528    707,451  self_backend_analysis.py:_stable_text_bucket_key
  +0.255        389        389  self_backend_stackprep.py:assign_stack_slots
  +0.202    226,528    707,451  self_backend_ir.py:_dot_numeric_text_key_id
  +0.145    222,393    729,026  self_backend_kernel.py:last_use
```

`assign_stack_slots` scanned `active_local_value_ids` linearly on every
operand use and, at block end, re-looked every active value up by NAME
(`kernel.value_id`, a text-key hash).  Both are linear in the number of
block-local values still holding a slot, which grows with block length once
`call.cont` blocks are gone: quadratic per block.

### Code Change `[CONFIRMED]`

`active_position` (value ID -> index in the active lists, swap-remove) makes
a use O(1); free slots are bucketed per type so an allocation takes the
earliest freed slot of its type without scanning (the same slot the linear
scan chose); block-end frees sort by name as before but map names back to
the IDs already at hand.  The class_gen off-arm `module_87.direct.s` is
byte-identical before/after, so slot assignment order is unchanged.

### [DENIED] `import traceback` / `pcc.extern` in a frontend module for the frame-trail diagnostic

Either import adds a module to the stage1 closure (`test_direct_publication_uses_exact_static_abi_in_stage1_context`
pins 224): `pcc.py_stdlib.traceback` via the stdlib walker, `pcc.extern` via
the same-package walker, which does not treat scaffold modules as
compile-time-only.  The diagnostic keeps the context line before `str(exc)`
and documents the temporary recipe instead.
