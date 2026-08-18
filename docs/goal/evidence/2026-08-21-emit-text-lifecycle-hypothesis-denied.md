# Emit-text lifecycle hypothesis DENIED (frozen capture mining)

Date: 2026-08-21

Claim level: focused GC0, self-backend, no-libpython module98 native emit-worker
diagnostic, mined from the already-frozen corrected capture. This is not a
complete stage2 timing, not a stage1/stage2 comparison, and not pcc2/pcc3
fixed-point evidence.

## Question

Pre-registered direction for this step: verify whether per-instruction
assembly generation repeatedly creating `list[str]`, concatenations and
temporary text objects constitutes one wholesale >=8% eliminable lifecycle
inside the AArch64 function-emission path. Write a candidate only if
verified; otherwise pivot to the structural IR text parse/rebuild roundtrip.

## Method

No new sampling and no source change. Mined the existing frozen capture
`build/stage2-medium-worker-profile-v1/complete-v2.folded`
(16,032 samples, SHA256 `b426cb2c7e16e925ba810ab8f6c04fed20242ee6c70946a27ee668b438269ff8`,
produced by the corrected capture-first `pcc_flamegraph.py`; target exited
normally and the result payload was byte-identical to the assembly oracle).
Leaf frames were aggregated per inclusive subtree scope. Sample-based leaf
shares carry normal sampling overhead and are attribution evidence, not an
acceptance wall measurement.

## Result

Category shares of leaf samples per scope:

| scope | samples | gc/refcount | text/memops | misc | dict | libc |
|---|---:|---:|---:|---:|---:|---:|
| whole worker | 16,032 | 10,618 (66.2%) | 964 (6.0%) | 3,525 | 319 | 606 |
| function emit (incl.) | 4,602 | ~67% | ~6% | 21% | ~2% | ~3% |
| instruction emit (incl.) | 2,644 | 1,812 (68.5%) | 148 (5.6%) | 544 | 49 | 91 |
| parse module (incl.) | 3,309 | 2,138 (64.6%) | 228 (6.9%) | 713 | 76 | 154 |

Global top leaves are almost entirely GC/refcount primitives:
`_pcc_gc_managed_pointer_find_slot` 1,818 (11.3% of the whole worker),
`_pcc_py_gc_minor_graph_lock` 940 (5.9%), `_pcc_gc_store_root` 476,
`_pcc_gc_load_ptr` 447, `_py_decref` 416, `pointer_is_managed_no_lock` 411,
`_py_incref` 387, `_tlv_get_addr` 380, `_pcc_gc_pointer_is_managed` 370,
graph_unlock 303, `_pcc_gc_unpin` 261, `_pcc_gc_index_py_find_slot` 256,
`_pcc_gc_pin` 236, `_pcc_gc_managed_pointer_index_contains` 223. No
list/str/join/format leaf appears in the top 20.

The hypothesized code shape does exist:
`_emit_function` (`pcc/backend/self_backend_aarch64_darwin.py:1353`) builds
per-instruction `list[str]` through emit callbacks and
`_emit_prepared_aarch64_darwin_module` extends them into the module list
(`:266`). The pattern is real; it is just not where the time goes.

## Verdict

DENIED. Per-instruction text-object churn is ~5.6% of instruction emit and
~6.0% of the whole worker, below the 8% bar, and the dominant emit cost is
the GC/refcount barrier tax on general object churn — uniform across parse
(64.6%), instruction emit (68.5%) and the whole worker (66.2%). Restructuring
text building alone cannot wholesale-eliminate that tax. Per the
pre-registered rule, no emit-text candidate is written.

## Stackprep candidate removal state

The denied stackprep dense-projection candidate is fully removed: no
`dense_projection`/`sparse_used_value_projection` symbols remain under
`pcc/backend/`, the working tree is clean, and both retained guard tests pass
on the clean tree
(`tests/c/test_self_backend.py::test_self_backend_stackprep_dense_projection_preserves_result_types_without_public_views`,
`..._sparse_used_value_projection_covers_instruction_abi`; 2 passed, 0.27s).
The denial numbers remain as reported: paired median 1.056x (bar 1.08),
instructions -5.2%, RSS -1.3%, store-roots 99/100 -> 121.

## Consequence and next direction

1. Pivot to the structural IR text parse/rebuild roundtrip (parse module is
   20.64% of the worker, and 64.6% of it is GC/refcount churn from rebuilding
   object graphs — eliminating the roundtrip removes whole object graphs, so
   it attacks the dominant tax, not just text work).
2. A separate, larger cross-phase lever is visible in the same capture: the
   GC0 managed-pointer index and barrier tax (`find_slot` 11.3% of the whole
   worker, graph lock/unlock ~7.7%, store_root/load_ptr/pin/unpin/index
   probes). It would move parse, stackmap planning and emit simultaneously,
   but it touches the GC0 reference backend and requires its own
   pre-registered row before any source change. Do not bundle it with the
   roundtrip route.
3. Even a perfect `_emit_function` cannot alone close the stage2 875.10s vs
   stage1 266.54s gap; the roundtrip, the barrier tax and the multi-worker
   live set must fall together.
