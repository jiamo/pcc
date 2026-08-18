# 003 — shared traceback landings; class_gen direct blocks -17.04%

Date: 2026-09-02

## Claim boundary

Direct/no-text AArch64 functions now record traceback frames through one
shared landing block per (function, error target) instead of one `err.frame`
block per source line.  The inline error edge carries a payload index; the
emitter's per-edge cold stub stores it into an i32 entry slot and jumps to the
landing, which calls `py_exc_append_frame_indexed` with two module tables
(`.pcc.tb.lines`, `.pcc.tb.sources`).  An explicit cleanup block stores the
payload itself before branching.  The text/LLVM oracle keeps its per-line
blocks and creation order.  Flags remain opt-in.  No Stage1/Stage2/fixed-point/
five-GC/speed claim.

## Representation

```text
edge record (8 scalars): source block, trigger, condition, target,
                         source line, cleanup plan, payload (-1 = none), 0
landing table:           (landing block, i32 slot value) pairs
cold stub:               L_<fn>_<landing>.edge.<k>: mov w9,#payload ; str w9,[slot] ; b L_landing
landing block:           k = load slot; exc = py_current_exception();
                         py_exc_append_frame_indexed(exc, func, file, .pcc.tb.lines, .pcc.tb.sources, k)
```

The verifier requires a payload on every edge into a landing and forbids one
elsewhere.  Root state/liveness treat the landing like any edge target; the
slot is a non-GC i32.  The runtime entry (C + pcc-Python port) is a two-load
wrapper over `py_exc_append_frame_source`, so frame contents are unchanged.

## class_gen sizing (host worker, v8 plan AST, direct capture + emit)

```text
                          blocks  call.cont  call.err.cleanup  err.frame  edges  instructions
inline edges off          21,405      2,772             1,310      1,309      0       147,887
cleanup + try (002)       18,633          0             1,310      1,309  2,772       147,887
shared landings (this)    17,758          0             1,381        350  2,944       145,475
```

Block reduction against the direct-off baseline: **17.04%** (registered line:
15%).  The 71 extra cleanup blocks and 172 extra edges come from the dyn
subscript fix (`BUG-P0-DYN-SUBSCRIPT-SILENT-NULL`): every generic `x[k]` now
carries a post-call error check that did not exist before.  Emission
completed (`module_87.direct.s`).  Wall/RSS from these runs are not evidence
(other builds ran concurrently); the alternating A/B is the next step.

## Focused evidence (all `-x -n0`)

```text
pytest tests/python/test_llvm_capi_direct_indexed_kernel.py \
       tests/c/test_self_backend_verifier.py \
       tests/python/test_precise_stackmap_abi.py \
       tests/python/test_pcc_record_inventory_tool.py
75 passed in 49.58s

strict no-libpython --python-library --emit-llvm closure, exit 0:
  direct_indexed_kernel.py, self_backend_precise_stackmaps.py,
  self_backend_kernel.py, self_backend_aarch64_darwin_terminators.py,
  self_backend_verify.py, self_backend_emit.py

pytest tests/python/test_inline_error_edge_runtime.py -q -x -n0
1 passed in 29.70s   (text vs direct binaries, GC0/3/4: stdout, stderr with the
                      four-frame traceback and two same-function raise sites,
                      exit code all identical)

pytest tests/python/test_fallback_baseline.py::test_per_module_fallbacks_under_ratchet
1 failed in 223.61s  -- exactly the four pre-existing red modules
                      (pipeline_context 483/441, pipeline_frontend_worker_execution
                      49/19, pipeline_libpython 78/73, pipeline_frontend_parallel
                      52/0, all owned by BUG-P1-PIPELINE-MODULE-FALLBACK-RATCHET-RED);
                      no module touched here changed its count.  An earlier
                      diagnostic stats line in the worker raised its count 49 -> 56
                      and was moved into generation_lowering's timing report.
```

## Dyn subscript fix (BUG-P0-DYN-SUBSCRIPT-SILENT-NULL)

`py_obj_subscript` / `py_obj_subscript_i64` (C + port) convert the getitem
primitives' silent NULL into KeyError (with key), IndexError or TypeError
(`'<type>' object is not subscriptable`); the frontend's generic dyn subscript
load calls them and checks.  `tests/python/test_dyn_subscript_raises.py`:
13 CPython-oracle shapes, port runtime GC0..4 (1 passed, 25.15s) and C runtime
`PCC_RUNTIME_CC=cc` GC0..4 (1 passed, 12.17s).

## Source identity

HEAD `c6c78f067da5ced616ea222a7e968006a53cbfbf`, dirty worktree.  SHA-256 prefixes:

```text
c56370910f458132 pcc/llvm_capi/direct_indexed_kernel.py
4281f8fada139d9b pcc/llvm_capi/ir.py
6221857c1aafbe28 pcc/py_frontend/codegen/exception_lowering.py
0b721d1c76eb0417 pcc/py_frontend/codegen/ownership_lowering.py
b61c5f90183c1bc0 pcc/backend/self_backend_precise_stackmaps.py
ef131b13d9f0a48c pcc/backend/self_backend_kernel.py
1fca6adea03bf69d pcc/backend/self_backend_aarch64_darwin_terminators.py
005de7dd69525bed pcc/backend/self_backend_verify.py
1ef37294b5425e43 pcc/py_frontend/codegen/subscript_lowering.py
b780bb3a63bcc2e5 pcc/py_runtime/src/py_obj_ops_dispatch.c
06eab4b53242a80b pcc/py_runtime/py/py_obj_ops_dispatch.py
525d40f249e96340 pcc/py_runtime/src/py_exc_traceback.c
3d91594be8cdb73c pcc/py_runtime/py/py_exc_traceback.py
```

## Host alternating A/B (class_gen full-cost worker, run_process_tree_sample, perf lock)

Order off,on,on,off,off,on; arms 5 and 6 overlapped a 1,139-file source
snapshot copy and are discarded.  Same manifest, same host compiler, only
`PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE` differs.

```text
arm    wall_s  peak_tree_RSS_MB  capture_ms  emit_ms
off-1  10.32   490               673         6511
on-2   11.00   473               646         7169
on-3   10.94   469               636         7116
off-4  10.82   492               670         6648
median off/on: wall 0.966 (on 3.5% slower), emit 0.925 (on 8% slower),
               capture 1.04 (on faster), peak RSS 1.045 (on 4.5% smaller)
```

Host emit is slower with 17% fewer blocks: this is not the pcc1 cost model,
but it is a real signal that some per-block work is superlinear in block
size (candidate: `_exception_successor_indexed` rescans every edge of the
block from the start for each safepoint) and must be profiled, not guessed.

## Stage1 receipt (current source, for the pcc1 A/B)

`scripts/run_pcc_stage1_build.py --arm candidate --direct-indexed-emit
--self-backend-jobs 2 --gc-backend 0` on the sealed snapshot
`/private/tmp/pcc-inline-edge-source-v1` (identity 12d52b24a0a455ce), under
`run_process_tree_sample.py --max-tree-rss-bytes 8589934592`:

```text
returncode 0   wall 145.06s   tree CPU 767.81s   max RSS 4.78 GB
peak tree RSS 4.74 GB (COMPLETE, no breaker)   links_libpython False   links_llvm False
compiler sha256 e330ea5c5bd2650d...   function smoke prints 42
```

This is an operational receipt for producing a pcc1, not a Stage1 timing
claim (single arm, no control).

## pcc1 full-cost worker A/B: blocked by a pcc1-red finding

With that pcc1 the class_gen worker fails in both arms before any timing:
`codegen[pcc.py_frontend.codegen.class_gen]: KeyError: -1` while lowering the
`raise L1CodegenError(...)` at class_gen.py:4483 inside
`emit_local_class_statement_init`; the v17 pcc1 (HEAD source) compiles the
same worker item.  Host codegen of the same module is fine.  The raising
subscript fix is the most likely *revealer*: a dyn `x[-1]` in the compiler's
own code that used to return a silent NULL now raises, so the underlying
host/pcc1 lookup divergence is finally visible.  A frame-trail diagnostic was
added to `_codegen_trace_dump` and pcc1 is being rebuilt to locate the site.
(Standalone `pcc1 prog.py -o` invocations without the stage runtime bundle
environment fail identically for v17 and this build and are not evidence.)

## Bisect E1: the pcc1-red is pre-existing, the raising subscript only exposes it

A scratch snapshot with `subscript_lowering.py` reverted to HEAD (everything
else current) produced a pcc1 that compiles the class_gen worker (`OK`), yet
the handler-stack probe still reads `type=dict len=401` on two of eighteen
calls.  The dict read therefore predates this work; with the silent-NULL
`py_obj_getitem` the bad read produced NULL, `isinstance(entry, tuple)`
rejected it and compilation continued on corrupted state.  Filed as
`BUG-P0-PCC1-HANDLER-STACK-ATTRIBUTE-CORRUPTION` (P0, in progress); the
pcc1 full-cost A/B stays blocked on it.

## Root cause of the pcc1-red and its fix

`docs/investigations/pcc1-host-mixin-state-outside-contract-aliases-slots.md`:
`DebugInfoLoweringMixin` (HEAD c6c78f06) writes four `self._di_*` fields that
are not in `L1_CODEGEN_HOST_ATTRS`; under pcc1 such writes use the mixin's own
field index and land on host slots 0-3, so `_active_handler_excs` (slot 2)
read `_di_scope`/`_di_subprograms`.  Five older mixins had 9 more uncontracted
attributes.  All 13 are now in the contract and
`test_l1_codegen_host_contract_covers_every_mixin_self_state` ratchets it
(fails on the pre-fix contract with exactly those names).

## pcc1 confirmation blocked by external memory pressure

The v8 Stage1 rebuild (contract fix included) was refused by
`run_process_tree_sample.py`'s Darwin preflight: "swap is already pressured;
refusing guarded process tree".  At that moment `vm.swapusage` showed
8217/9216 MB used and the largest process was an unrelated user process
(`~/mydb/zdb/target/release/zdb`, about 48 GB RSS); no pcc/pytest children
were alive.  The refusal is the harness working as intended.  The pcc1
class_gen confirmation and the pcc1 off/on A/B resume once memory pressure
clears; they need no source change.

## Host emit regression: owner found and fixed

cProfile off/on attributes the ON arm's extra host time to
`self_backend_stackprep.assign_stack_slots`: a linear scan of the block-local
active-slot list per operand use (`maybe_free_local_value` 150k -> 658k
calls) and a per-block name -> ID re-lookup (`kernel.value_id` 19k -> 529k
calls), both quadratic in block length once `call.cont` blocks are gone.
Replaced by an O(1) position table with swap-remove and per-type free-slot
buckets; the class_gen off-arm `module_87.direct.s` is byte-identical before
and after (slot reuse order preserved).

Host alternating A/B after the fix (three pairs, order off,on,on,off,off,on;
same harness; the machine still carried the external swap pressure, so treat
the absolute numbers as operational and the ratios as indicative):

```text
arm    wall_s  peak_tree_RSS_MB  capture_ms  emit_ms
off-1  11.11   489               710         6703
on-2   10.47   472               663         6366
on-3   10.52   473               665         6402
off-4  10.82   491               698         6553
off-5  11.18   492               714         6950
on-6   10.89   470               679         6580
median off/on: wall 1.056, emit 1.047, capture 1.068, peak RSS 1.042
```

The ON arm is now faster on every axis (it was 3.5% slower before the
stackprep fix); 1.056x is below the alternative 1.15x host line, but the
row's registered class_gen block line (>=15%) is the one met.

## Third defect surfaced: exc.args on a tagged value

pcc1's trace dump segfaulted right after printing a `KeyError(-1)`; a
host-built probe reproduces it: `exc.args` crashes when the exception value
is a tagged small int because the port's `args` getter read the message type
tag with a raw header load (the C mirror uses the tag-aware `py_type_of`).
Fixed in `py_obj_ops_dispatch.py` (`_type_of`), covered by
`test_dyn_subscript_raises.py` printing `repr(exc.args)` for every KeyError:
2 passed (port + `PCC_RUNTIME_CC=cc`) on GC0..4.  Filed and closed as
`BUG-P1-EXC-ARGS-TAGGED-VALUE-SEGFAULT`.

## pcc1 v10: contract fix confirmed, then a second pcc1-only gap in the ON arm

With swap still tripping the guard (used 3052/4096 MB, free 1044 MB < 4 GiB)
and the human's go-ahead, pcc1 v10 was built with `run_pcc_stage1_build.py`
directly (own 520 s timeout and smoke, no external 8 GiB tree cap; receipt
max RSS 4.78 GB, wall 151.1 s, tree CPU 805 s, libSystem-only, sha
ee46f5ca9f35aab6).  Its class_gen worker replay compiles item 87 (`OK`) with
the raising dyn subscript in place: the host-contract fix for the mixin
state aliasing is confirmed on pcc1 (`BUG-P0-PCC1-HANDLER-STACK-ATTRIBUTE-CORRUPTION` closed).

The pcc1 off/on A/B then failed in every ON arm with
`NameError: name 'ir' is not defined` at the first function: the IR scaffold
static export table (`layer1_support.py`) lacked
`IRBuilder_declare_inline_error_landing` and still described
`IRBuilder_try_inline_error_edge` with five parameters (the payload made it
six), so the compiled frontend fell back to a dynamic `ir` lookup that has no
runtime object.  Both exports are added; pcc1 v11 rebuild and the A/B follow.

v11 still failed the ON arm with the same NameError.  The real cause is the
CALL FORM: `exception_lowering.py` invoked the inline-edge helpers as
`ir.IRBuilder_try_inline_error_edge(...)` (attribute on the scaffold `ir`
object), and under pcc1 a module-level `pcc.llvm_capi.ir` helper resolves only
when it is IMPORTED BY NAME (like every other `IRBuilder_*` in `class_gen.py`,
e.g. `IRBuilder_publish_direct_raw_call`, which has no static export yet
works).  Fixed by importing the three helpers by name in
`exception_lowering.py`; `test_frontend_ir_module_helpers_have_matching_static_exports`
now forbids any `ir.IRBuilder_*` attribute call in the frontend and checks
arity for the exported ones.  The static export additions are kept (harmless).
pcc1 v12 rebuild + A/B follow.

## pcc1 full-cost off/on A/B: 17% fewer blocks does NOT speed pcc1

pcc1 v12 (sha fbf80636582ce2be, name-import fix; direct-built, no external tree
cap, receipt libSystem-only), six alternating class_gen full-cost worker arms
off,on,on,off,off,on, same worker manifest, only
`PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE` differs:

```text
arm    wall_s  peak_RSS_MB  capture_ms  emit_ms   direct-kernel blocks / edges
off-1  41.27   3580         5424        18470     21,806 / 0
on-2   41.77   3540         5556        18232     17,758 / 2,944
on-3   38.67   4002         5219        17767
off-4  38.88   4115         5366        17490
off-5  39.29   4121         5268        17470
on-6   39.19   3992         5330        18230
median off/on: wall 1.003, emit 0.959 (ON 4% SLOWER), capture 1.007,
               codegen 0.996, peak RSS 1.031 (ON 3% smaller)
```

**Honest verdict: this is a structural/correctness closure, not a pcc1 speed
win.**  The registered pcc1 threshold (wall/CPU >=1.20x, RSS <=1.02x) is NOT
met: wall is flat (1.003x) and emit is 4% slower on the ON arm, offset by 3%
smaller peak RSS.  Why: the 18.5% block reduction (21,806 -> 17,758) barely
moves instruction count (147,887 -> 145,475, 1.6% fewer) and adds 2,944 edge
records plus per-edge cold stubs; pcc1 emit is instruction-bound, not
block-bound, so removing CFG nodes does not shorten it.  Per the convergence
guardrail "match optimization scale to the goal gap" / "a structural closure
must not be presented as the performance solution", the inline-error-edge
plane is landed and correct but must NOT be enabled by default as a speed
measure, and it does not advance the Stage2 <=600s goal on its own.  A real
pcc1 emit speedup needs per-instruction work reduction (the native data-plane
/ emit-throughput track), not block-count reduction.

What IS delivered and proven: the concept closure (zero `call.cont`, per-line
`err.frame` 1,309 -> 350 shared landings), 17.04% class_gen block reduction
(meets the >=15% structural line), byte-identical output, text-vs-direct
runtime differential identical on GC0/3/4, all exception/traceback/cleanup
semantics preserved, and three real correctness bugs found and fixed on the
way (dyn subscript silent NULL, host-mixin state slot aliasing, exc.args
tagged-value segfault).

## Open boundary

Host alternating A/B (off/on, 3 pairs, quiet machine) and one Stage1 build
for the pcc1 full-cost A/B are next; default enablement and the Stage2
prediction refresh wait on them.  The shared cleanup dispatcher stays a
measured tradeoff.  Two pre-existing CPython divergences seen by the
differential are not this slice's: temporaries passed to a raising call are
freed before the handler body runs (CPython frees them after), and an uncaught
exception exits without running module-global finalizers.
