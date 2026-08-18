# 001 — cleanup-plan-zero inline error-edge tracer

Date: 2026-09-01

## Claim boundary

This slice proves that the AArch64 direct/no-text frontend can keep normal
post-call execution in the current logical block and publish a real
exceptional successor.  The text/LLVM path and every unsupported shape retain
the historical explicit CFG.  This is a dirty-worktree focused result, not a
Stage1, Stage2, fixed-point, five-GC, runtime-output, or speed claim.

The implementation remains opt-in behind all three environment flags:

```text
PCC_DIRECT_INDEXED_KERNEL_CAPTURE=1
PCC_DIRECT_INDEXED_KERNEL_EMIT=1
PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE=1
```

## Implemented boundary

- `IndexedFunctionSeed`/`IndexedFunctionKernel` own one six-scalar record per
  edge: source block, trigger instruction, condition value, error block,
  source line and cleanup-plan ID, plus one packed start/count span per block.
- `DirectIndexedFunctionBuilder` records the construction form in native
  arenas and maintains native head/tail/next indices, so finalization does not
  materialize a Python list per source block.
- Direct finalization includes edge-only error blocks in reachability, resolves
  record positions to final instruction IDs, and accounts for the edge
  condition in fused and non-fused use/last-use data.
- The verifier validates packed span coverage, source/trigger/target/condition
  IDs, cleanup plan zero, `i1` type/dominance, and the initial no-PHI target
  contract; its CFG includes the exceptional successor.
- AArch64 precise root-state propagation publishes the state at the exact
  trigger rather than the source block's final state. Managed liveness includes
  the error successor, and stack-map exceptional-successor discovery consumes
  the inline condition.
- The dense and callback AArch64 emitters materialize the condition and emit a
  true-is-error `cbnz` while normal execution falls through in the same block.
- `_emit_post_call_err_check` selects this representation only for
  cleanup-plan-zero function-exit edges. Try handlers, owned/pinned/rooted
  cleanup and every flag-disabled path retain the explicit `call.cont` /
  cleanup CFG. The default text block creation order remains unchanged.
- Static exports cover both new `IRBuilder` helpers, so the pcc1 closure does
  not introduce a dynamic method fallback.

## Focused evidence

All pytest commands used `-x -n0`.

```text
pytest tests/python/test_llvm_capi_direct_indexed_kernel.py -q -x -n0
12 passed in 43.98s

pytest tests/c/test_self_backend_verifier.py \
       tests/python/test_precise_stackmap_abi.py \
       tests/python/test_pcc_record_inventory_tool.py -q -x -n0
61 passed in 2.24s

pytest tests/python/test_cpy_call_argument_ownership.py::test_arglist_pcc_and_mapping_errors_unwind_fresh_callable_and_list \
       tests/python/test_cpy_call_argument_ownership.py::test_arglist_allocation_error_uses_pcc_try_cleanup \
       tests/python/test_cpy_call_argument_ownership.py::test_splat_tuple_releases_each_owned_list_get_result -q -x -n0
3 passed in 0.70s

pcc --backend self --python-libpython=off --ir-scaffold=on \
    --python-library --emit-llvm=/tmp/pcc-inline-edge-kernel-check.ll \
    pcc/backend/self_backend_kernel.py
exit 0
```

The minimized direct test uses fused use publication and proves:

```text
direct blocks: [entry, error]
text blocks:   entry, error, normal
direct edge:   [source=0, trigger=0, condition, target=1, line=42, plan=0]
condition last use: trigger + 1
AArch64: cbnz w9, L_probe_error
```

The real frontend test lowers `return int(value)`: direct/no-text publishes at
least one inline edge and fewer `call.cont` blocks, while the text oracle keeps
its explicit continuation CFG and both routes complete AArch64 emission.

One attempted `PCC_USE_LLVMLITE_PY=1` ownership node failed before reaching
this code in the pre-existing `runtime_abi.py` assumption that llvmlite
`FunctionAttributes` has `_attrs`; it is not counted as evidence for or against
this slice. The inline helper call is guarded by the combined direct-mode flag,
so the default llvmlite path does not evaluate the native-only API.

## Source identity

Observed HEAD was `a492bf2b08b81ba6c878a5df172e09669dd3bb1c`; the worktree was dirty.
Representative final file SHA-256 values:

```text
self_backend_kernel.py                  1121580d8307e4bb4c3ddb2107d8137df3f93f8fcdd010b962b91413ad83b5d5
self_backend_precise_stackmaps.py       b27c01a63d7725f2fd8a1fe730d9ef7ba19059b165f3b28a68072da3422bf35e
direct_indexed_kernel.py                a5a2aae8e2ede625a99186a4c3f968d3d8e9be6c484d16e8f0134e587fdf7d57
exception_lowering.py                   2b45d35bcbc2a3ae0001912c0498029f86973f79f789622449733e1a7d8d2adc
test_llvm_capi_direct_indexed_kernel.py 9e7cbf8a20c5c9f49b453c877410a6869f6fe6331441c15966456863b9351fe8
```

## Open boundary

No class-gen inventory or full-cost host/pcc1 measurement has been run, the
flag is not enabled by default, and runtime traceback/output plus GC0/3/4 have
not yet been exercised. Cleanup-plan-zero still targets the existing
per-source-line `err.frame` blocks, so this slice removes eligible normal
continuations but not the larger shared-frame/cleanup family. Try-handler and
all nonzero cleanup plans deliberately remain explicit. The next slice must
first count the current class-gen reduction, then implement shared traceback /
cleanup exits before any Stage1 or Stage2 run.

