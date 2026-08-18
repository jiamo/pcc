# 002 — cleanup/try inline edges, class_gen sizing, and three stack-map defects

Date: 2026-09-02

## Claim boundary

Direct/no-text AArch64 functions now publish every post-call error check as
an inline edge: function-exit, try-handler and owned/pinned/rooted/LIFO
cleanup shapes.  Cleanup keeps its explicit `call.err.cleanup` block (its
operands are call-specific SSA values); the edge reaches that block directly,
so no `call.cont` block exists in direct mode.  The text/LLVM oracle keeps its
block creation order.  This is a dirty-worktree focused result: no Stage1,
Stage2, fixed-point, five-GC, or speed claim.  The flags remain opt-in.

## Correctness defects found and fixed on the way

Sizing the real `class_gen` module and running a real differential program
exposed four defects, all now covered by focused tests:

1. **Edge trigger drift** (`direct_indexed_kernel.py`): the trigger was a
   record-index snapshot taken at publish time; the frontend later inserts
   allocas/hoists ahead of it (`position_at_start`, `position_before`), so a
   real `__del__` method failed `ssa-dominance`.  The trigger is now resolved
   at finalization from the condition's definition, and a block's edges are
   published sorted by trigger.  Test: `test_direct_inline_error_edge_trigger_follows_records_inserted_ahead`.
2. **Edge-only blocks had no stack maps** (`self_backend_precise_stackmaps.py`):
   `_native_root_states` reused the previous block's edge span in its
   worklist, so blocks reachable only through inline edges (every cleanup
   block, and `err.frame.*` in the committed plan-zero tracer) received no
   entry root state and safepoint planning silently skipped them.  Fixed by
   the per-block reset plus a fail-closed ratchet: every CFG-reachable block
   must own an entry root state.  Test: `test_frontend_edge_only_cleanup_blocks_keep_safepoint_records`.
3. **Entry hoists landed after the first edge** (`ownership_lowering.py`,
   `generator_lowering.py`, `exception_lowering.py`): root frame enters and
   slot initializers are hoisted "before the entry terminator"; the text
   oracle's entry ends at the first err-check `cbranch`, a direct entry does
   not.  `_position_at_entry_hoist_point` now anchors on the first inline
   edge's condition record (`Value._instr` is attached for icmp results),
   so every exceptional path sees the same registered roots.  Repro:
   `for ... try ...` (`loop_probe`), `err.exit` leaving an unentered
   `for.obj.iter.root`.
4. **Edge live-in was placed at block end** (`_native_managed_liveness`):
   the per-safepoint backward scan seeded from `cfg_successor` live-out, so a
   cleanup block's unpin operand looked live past a later `frame_leave` and
   class_gen's `_resolve_metaclass_expr_name` reported a stale managed value.
   The scan now seeds from terminator successors and injects each edge
   target's live-in exactly at its trigger.

Diagnostics gained block/callee/slot context for slot leaves and safepoint
block/index context for stale values.

## Focused evidence

All pytest commands used `-x -n0`.

```text
pytest tests/python/test_llvm_capi_direct_indexed_kernel.py -q -x -n0
14 passed in 53.00s

pytest tests/python/test_llvm_capi_direct_indexed_kernel.py \
       tests/c/test_self_backend_verifier.py \
       tests/python/test_precise_stackmap_abi.py \
       tests/python/test_pcc_record_inventory_tool.py -q -x -n0
74 passed in 55.78s   (before the two new direct tests were added)
```

Runtime differential (host pcc, `--backend self --python-libpython=off
--ir-scaffold=on`, program with nested raising calls, uncaught traceback,
try/except in a loop, re-raise, owned temporaries as call arguments,
`__del__` canaries):

```text
flags off vs flags on (CAPTURE=1 EMIT=1 INLINE_ERROR_EDGE_CAPTURE=1 FUSE_USES=1)
PCC_GC_BACKEND=0: stdout, stderr, exit code identical
PCC_GC_BACKEND=3: identical
PCC_GC_BACKEND=4: identical
```

Strict no-libpython `--python-library --emit-llvm` closure: `direct_indexed_kernel.py`,
`self_backend_precise_stackmaps.py`, `pipeline_frontend_worker_execution.py`
exit 0.  `exception_lowering.py` / `ownership_lowering.py` cannot use that
harness ("python_library mode only supports a single Python source"); their
closure evidence is the per-module fallback ratchet run recorded below.

## class_gen sizing (host worker, v8 plan AST, direct capture + emit)

`direct_indexed_module_cfg_stats` (new, printed under
`PCC_PY_FRONTEND_WORKER_TIMING=1`) on the same direct-kernel accounting:

```text
                         blocks  call.cont  call.err.cleanup  err.frame  edges  instructions
inline edges off         21,405      2,772             1,310      1,309      0       147,887
plan-zero only (001)     20,315      1,682             1,310      1,309  1,090       147,887
cleanup + try (this)     18,633          0             1,310      1,309  2,772       147,887
```

Block reduction 12.95% against the direct-off baseline (text baseline
24,274 counts unreachable blocks and terminators differently and is not
comparable).  Emission completed (`module_87.direct.s`).  Wall/RSS from these
runs are not evidence: other builds ran concurrently.

## Source identity

HEAD `c6c78f067da5ced616ea222a7e968006a53cbfbf`, dirty worktree.  SHA-256 prefixes:

```text
direct_indexed_kernel.py            9b251ba0610c83cc
ir.py                               0471e903385ebcc8
exception_lowering.py               6a6b0fa5ee27d9f9
ownership_lowering.py               0b721d1c76eb0417
self_backend_precise_stackmaps.py   b61c5f90183c1bc0
test_llvm_capi_direct_indexed_kernel.py c1932af2af0b1fdb
```

## Open boundary

12.95% is below the registered 15% class_gen block line; the remaining
families are 1,310 `call.err.cleanup` and 1,309 per-line `err.frame` blocks.
A shared cleanup dispatcher would trade hot-path state stores for cold-block
count and is recorded as a measured tradeoff, not implemented.  Next: merge
per-line frame blocks into one landing per (function, target), then host
alternating A/B on a quiet machine, then a pcc1 build for the full-cost A/B.
A pre-existing runtime bug found by the differential (dyn `x[k]` returns a
silent NULL instead of KeyError/IndexError/TypeError) is filed as
`BUG-P0-DYN-SUBSCRIPT-SILENT-NULL`.
