# pcc-compiled C runtime archive: full three-stage chain proven

Date: 2026-08-01

Task: `LIBC-P1-PCC-RUNTIME-ARCHIVE` (closure slice; slices 1-2 in
`2026-07-31-py-list-todo-raise-placeholders.md` /
`2026-07-31-cross-module-none-return-abi.md` and the investigation)

## Source identity

Base commit `98e62890963c60515d6f8ddc8c31996b04500f95` plus this session's
slices. Final changed behavior beyond slices 1-2:

- `pcc/py_frontend/pipeline.py`: export schema carries a per-method/function
  `"returns_none"` bool via `_export_returns_none` — True ONLY for an
  explicit `-> None` annotation (`_closed_world_is_node(ret, NoneType)`).
  Unannotated definitions export dyn as before: the definition lowers the
  post-inference type, so declaring them void had turned every unannotated
  cross-module method call's result into py_None and collapsed FuncDef
  resolution wholesale (56 workers) — root-caused by a six-cycle
  instrumented bisect recorded in the investigation.
- `pcc/py_frontend/codegen/class_gen.py`: the extern-class declaration plan
  consumes the schema bool (fallback to decoded-NoneType for older
  schemas), lowers explicit `-> None` methods to `void` cross-module
  (async keeps PTR), uses indexed plan access, and `_find_method_def` uses
  ``in`` + subscript instead of ``dict.get`` (defensive, prescription-
  conformant).
- `pcc/py_frontend/codegen/marshal.py`: void SSA values materialize
  `py_None` in the DynType branch.
- The temporary store-site trap in `py_obj.c` is fully removed (its
  round-2 leftover caused one false-positive chain failure, documented).

## The chain (the row's core gate)

```text
PCC_BOOTSTRAP_RUNTIME_HIGH=c PCC_BOOTSTRAP_RUNTIME_CC=pcc \
  scripts/bootstrap.sh  (out-dir build/bootstrap-pcc-c-runtime, staged)

stage1: host -> pcc1 linked against libpy_runtime_pcc.a
        (pcc-emitted all-C runtime; publish-barrier smoke compile+run pass)
stage2: that pcc1 -> pcc2 in 275.5s (0 worker failures)
stage3: pcc2 -> pcc3 in 60.9s
verify: pcc2 and pcc3 metadata-normalized byte-identical (standard verdict)
```

`libpy_runtime_pcc.a` builds from current source (84/86 objects emitted by
pcc; the documented cc-only exceptions remain py_os_rss/py_os_heap and the
Metal/dlpack/gc-external/waitset files per the Makefile).

## Gates on the final tree

```text
tests/python/test_bootstrap_gate_baseline.py     4 deselected (integration-marked;
  the default-chain fixed point was re-proven directly: stage2/stage3 green,
  pcc2/pcc3 metadata-normalized byte-identical)
ABI + multi-file + class-schema + list-pop gates  45 passed
fallback ratchets + bootstrap shim               (final summaries recorded in
  the session log: 27 passed ratchets; 93 passed shim)
```

## Supported claim

pcc compiles its own C runtime end to end: the pcc-emitted all-C runtime
archive links a stage1 pcc1 that completes a real pcc1→pcc2→pcc3 chain to
the normalized fixed point, with no libpython, on darwin-arm64. Mode
labels: `PCC_RUNTIME_CC=pcc PCC_RUNTIME_HIGH=c`, self backend, GC0
default; the production default (`HIGH=py`) and its fixed point stay green
on the same tree.

## Not proven

- Five-GC matrix under the all-C archive (the production-equality rule
  keeps GC gates on the default archive; an all-C five-GC pass would be a
  separate deliberate gate).
- The cc-only kernel files (py_os_rss/py_os_heap etc.) remain the
  documented two-file-plus-peripherals exception — LIBC-P2-SDK-STRUCT-HELPERS.
- encode_type's dyn degradation under pcc1 (schema types collapse to
  ("dyn",)) is still open as its own card material; the returns_none bool
  bypasses it for the return-ABI only.
