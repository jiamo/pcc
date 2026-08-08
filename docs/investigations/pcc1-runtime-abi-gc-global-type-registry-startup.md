# Investigation: pcc1 startup fails after raw GC globals enter runtime ABI type registry

## Status
resolved

## Problem Description

A fresh self/no-libpython `pcc0 -> pcc1` build for the freestanding GC
telemetry slice produced a signed executable, but the mandatory stage publish
barrier failed.  The first `pcc1 --help` execution crashes while importing
`pcc/py_frontend/codegen/runtime_abi.py`:

```text
File "pcc/llvm_capi/ir.py", line 286, in __new__
File "pcc/llvm_capi/ir.py", line 286, in __new__
File "pcc/py_frontend/codegen/runtime_abi.py", line 1133, in <module>
AttributeError: _instance
```

The directly preceding compiler change added all 130 raw GC storage symbols to
`RUNTIME_GLOBALS` with live `ir.IntType` / `ir.PointerType` values.  These raw
symbols are only needed by the strict freestanding validator; unsafe
`global_addr` lowering already declares its own raw external storage and never
calls `declare_runtime_global`.

This failure is separate from the resolved telemetry counter-number drift.

## Repro

```bash
gtimeout 360s env -u LC_ALL \
  PCC_BOOTSTRAP_PROFILE_DIR=build/libc-gc-telemetry-stage1-profile \
  bash scripts/bootstrap.sh \
  --out-dir build/libc-gc-telemetry-stage1 --backend self --stage 1
```

Observed: compile succeeds in 60.856 seconds, codesign verification succeeds,
but `publish_barrier_returncode=1`; direct `pcc1 --help` reproduces the
`AttributeError: _instance` traceback above.

## Test [CONFIRMED]

The fresh stage1 command above is the compiled-stage reproducer.  A focused
source-contract regression will require raw freestanding GC storage to live in
a string-only storage-kind registry rather than the managed `ir.Type` registry,
then the same fresh stage1 and `--help` path must pass.

## Proposals

- No.1 Separate raw GC storage kinds from managed runtime LLVM type objects [DENIED as startup fix; retained]
- No.2 Disable the stage publish/exec-smoke barrier [DENIED]
- No.3 Patch `_SingletonType` inheritance without isolating the triggering registry [DENIED]
- No.4 Defer pipeline's runtime ABI import until strict validation executes [CONFIRMED]

## No.1 Separate raw GC storage kinds from managed runtime LLVM type objects

### Code Change

Keep `RUNTIME_GLOBALS` for globals consumed by
`declare_runtime_global`.  Add exact string-only i32/pointer sets for strict
freestanding GC validation, verify them against `freestanding_gc_state.py`, and
make the pipeline admit only that finite set.

### DENIED as the startup fix; retained as the raw-storage contract

The exact source-contract tests passed, but a fresh v2 stage1 still failed its
publish barrier after 69.663 seconds of successful compilation.  Direct
`pcc1 --help` reproduced the same `_SingletonType.__new__` / `_instance`
failure.  The separation remains correct because unsafe raw storage is not a
managed `declare_runtime_global` surface, but it is not sufficient startup
causality evidence.

## No.2 Disable the stage publish/exec-smoke barrier

### DENIED

The barrier correctly caught a deterministic startup regression after the
compiler had already emitted and signed `pcc1`.  Removing it would publish a
binary that cannot execute even `--help`.

## No.3 Patch `_SingletonType` inheritance without isolating the triggering registry

### DENIED

The singleton implementation predates this regression and existing compiler
code depends on its identity behavior.  The new raw storage symbols do not need
live LLVM type objects at all, so changing the object model first would be a
broader speculative fix without a reduced causality test.

## No.4 Defer pipeline's runtime ABI import until strict validation executes

### Code Change

Remove the new module-scope `pipeline -> runtime_abi -> llvm_capi.ir` import.
Import the exact function/global registries locally only inside the two strict
freestanding validation helpers.  A fresh-process regression asserts that
importing `pcc.py_frontend.pipeline` no longer initializes `runtime_abi`.

### CONFIRMED

The focused fresh-process import regression passes, and the current-source v3
stage1 command completed its full publish/exec-smoke barrier:

```text
PCC_BOOTSTRAP_STAGE_RESULT stage=1 elapsed_ms=74884 \
  output=build/libc-gc-telemetry-stage1-v3/pcc1
```

The resulting binary executes `--help` and compiles
`py_gc_telemetry.py` with `--backend self --python-libpython off
--python-library`.  The only behavioral difference from the two failed stage1
builds is that importing `pipeline` no longer initializes `runtime_abi` before
strict freestanding validation actually needs it.  This confirms the eager
module-initialization edge as the startup trigger.

## Report

Raw GC state names remain in a finite string-only registry, separate from the
managed LLVM-type registry.  Pipeline imports that registry lazily inside the
two strict validators.  This preserves the exact freestanding contract without
changing `_SingletonType`, weakening the pcc1 exec-smoke barrier, or eagerly
materializing the runtime ABI during pipeline startup.
