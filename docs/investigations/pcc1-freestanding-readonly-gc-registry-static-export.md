# Investigation: pcc1 rejects an admitted read-only GC query while rebuilding its runtime

## Status

active

## Problem Description

A current-source project-local `pcc1` rejects `py_gc_telemetry.py` while rebuilding `libpy_runtime_pcc_py.a`. The source binds `pcc_gc_relocation_set_size` with the exact no-argument `c_int64` signature, and the symbol is present in `FREESTANDING_GC_READONLY_RUNTIME_IMPORTS` and `RUNTIME_SIGNATURES`. The CPython-hosted compiler accepts the same module. The self-hosted pipeline therefore loses the finite registry relationship before emitted-IR verification.

This follows the same compiled-frontend initialization principle as the earlier `is_freestanding_gc_runtime_global` export: self-hosted pipeline code must call a statically declared owner-module function instead of importing and inspecting a managed collection initialized in another module.

## Update 2026-08-14: cross-object registry has the same owner issue

After moving read-only membership and `RUNTIME_SIGNATURES` validation into the owner helper, the rebuilt native compiler accepted `pcc_gc_relocation_set_size` and stopped at the later `pcc_gc_scheduler_root_count` call. That symbol is admitted through `FREESTANDING_GC_CROSS_OBJECT_SIGNATURES`, which `pipeline_freestanding.py` also imported and inspected directly. The same proposal therefore needs an owner-module predicate for the exact cross-object source signature; this is the next occurrence of the confirmed representation defect, not an unconditional new ABI admission.

## Repro

From the repository root with the pre-fix current-source `projects/harness/build/pcc1`:

```bash
gtimeout 120s env -u LC_ALL projects/harness/build/pcc1 \
  --python-library --emit-llvm=/tmp/py_gc_telemetry.ll \
  pcc/py_runtime/py/py_gc_telemetry.py
```

Expected exit code: `0`. Observed exit code: nonzero with `freestanding module emitted managed-runtime reference` naming the call to `pcc_gc_relocation_set_size()`.

The realistic failure is:

```bash
gtimeout 1200s env -u LC_ALL PCC1=projects/harness/build/pcc1 \
  projects/harness/build.sh
```

It stops while `make` rebuilds `py_gc_telemetry.o` with the same verifier diagnostic.

## Test [CONFIRMED]

The realistic Harness build failure was observed on 2026-08-14. Host inspection returned `True` for `pcc_gc_relocation_set_size` in both `freestanding_readonly_gc_runtime_imports(source)` and `freestanding_allowed_external_symbols(source)`, proving the registry contents and source parser are correct outside the self-hosted cross-module path.

The permanent structural regression is `tests/python/test_freestanding_module.py::test_freestanding_readonly_gc_registry_is_a_static_pcc1_import`. The final acceptance is the rebuilt current-source `pcc1` compiling `py_gc_telemetry.py`, rebuilding the runtime archive, and compiling the complete Harness with `--backend self --python-libpython off`.

## Proposals

- No.1 Export an owner-module membership predicate to the self-hosted frontend [pending]
- No.1b Export the cross-object ABI predicate from the same owner [pending]
- No.2 Add `pcc_gc_relocation_set_size` as an unconditional verifier exception [DENIED]

## No.1 Export an owner-module membership predicate to the self-hosted frontend

### Code Change

Add `is_freestanding_gc_readonly_runtime_import(symbol)` beside the finite registry in `runtime_abi.py`, declare that function in `_PCC_FRONTEND_STATIC_NATIVE_EXPORTS`, and make `pipeline_freestanding.py` call it. The source-shape and `RUNTIME_SIGNATURES` checks remain in the consumer, so admission still requires all three independent conditions.

### pending

The initial helper only owned finite-set membership; the rebuilt native compiler still rejected the module because `pipeline_freestanding.py` separately imported and read `RUNTIME_SIGNATURES`. The proposal now moves both membership and exact runtime-signature validation into the owner-module helper. A new current-source bootstrap and the native runtime-archive/Harness build remain required.

## No.1b Export the cross-object ABI predicate from the same owner

### Code Change

Add `is_freestanding_gc_cross_object_runtime_import(symbol, parameters_source, return_source)` beside `FREESTANDING_GC_CROSS_OBJECT_SIGNATURES`, declare it as a self-host static export, and leave source parsing in `pipeline_freestanding.py` while moving exact dictionary lookup and tuple-shape comparison to the owner.

### pending

The rebuilt compiler must compile the complete `py_gc_telemetry.py` after both predicates are present.

## No.2 Add `pcc_gc_relocation_set_size` as an unconditional verifier exception

### Code Change

Strip this one symbol from verifier input without checking its source binding or registered ABI.

### DENIED

That would create a second allowlist and admit the symbol independently of its exact source signature. The finite owner-module registry plus static function export preserves the existing fail-closed checks and fixes the self-hosted representation issue instead.
