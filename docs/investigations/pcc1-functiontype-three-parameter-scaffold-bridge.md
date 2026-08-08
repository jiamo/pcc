# Investigation: pcc1 three-parameter FunctionType scaffold bridge

## Status

resolved

## Problem Description

The first current-source stage1 after adding the GC slot callback reached the
self-backend link and failed on
`_user_pcc_llvm_capi_ir_FunctionType___init__3`.  The callback lowering creates
`ir.FunctionType(void, [ptr, i64, ptr])`, the first three-parameter literal
function type in the compiler closure.

## Repro

```bash
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/libc-gc-object-slots-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/libc-gc-object-slots-stage1 --backend self --stage 1
```

The failed run ended after 100.791 seconds with the one undefined scaffold
symbol above.  No compiler or bootstrap child survived the bounded run.

## Proposals

- No.1 Complete the current literal FunctionType arity closure [accepted]

## No.1 Complete the current literal FunctionType arity closure

### Code Change

Add `FunctionType___init__3(return_type, arg0, arg1, arg2)` beside the existing
zero-, one- and two-parameter bridges in `pcc/llvm_capi/ir.py`.  Keep the
scaffold lowering unchanged: repository AST audit shows three is the maximum
literal function-type arity in current production sources.  Add one scaffold
symbol test and one direct LLVM-CAPI semantic test.

### Result

Accepted.  The focused tests pass and the second current-source self/no-
libpython stage1 completed its publish and exec-smoke barrier in 80.916
seconds.  That pcc1 then compiled the real strict object-slot module in 0.84
seconds; clang and nm confirmed nine definitions and the expected three raw
imports.  This failure is separate from the GC graph-contract migration: the
new callback merely exposed a pre-existing missing scaffold bridge.
