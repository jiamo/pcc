# Investigation: generated type-tag alias loads crash current pcc1 during frontend module initialization

## Status

active

## Problem Description

A current-source project-local `pcc1` exits with `SIGSEGV` while compiling any Harness module, including the previously stable `projects/harness/model_runtime.py`. The CPython-hosted current compiler accepts both new persistence modules. The failure therefore occurs in the native compiler before it reaches Harness-specific lowering.

## Repro

From the repository root:

```bash
gtimeout 30s projects/harness/build/pcc1 --backend self --python-libpython off --ir-scaffold on --emit-llvm=/tmp/harness-model.ll projects/harness/model_runtime.py
```

Expected: exit 0 and a complete LLVM IR file. Observed on 2026-08-14: exit 139. LLDB stops in `pcc_gc_pin` with address `0x1e`, called from `_pcc_py_module_top_pcc_py_frontend_codegen_compare_membership_lowering`.

## Test [CONFIRMED]

`tests/python/test_port_abi_constants.py::test_generated_compiler_type_tag_aliases_are_static_integer_constants` requires every generated compiler `PY_TYPE_*` alias to be a literal integer equal to `freestanding_abi_spec.ABI_SPEC`. Before the fix it fails with an empty actual assignment map because all 39 aliases are dynamic `ABI_CONSTANTS[...]` lookups.

The realistic acceptance is a newly built current-source `projects/harness/build/pcc1` compiling the old `model_runtime.py` control and the complete Harness application with `--backend self --python-libpython off`.

## Proposals

- No.1 Emit static aliases from the same ABI generator input [pending]
- No.2 Disable generated aliases in self-hosted frontend modules [DENIED]

## No.1 Emit static aliases from the same ABI generator input

### Code Change

`scripts/gen_freestanding_stdio_abi.py` emits each `PY_TYPE_*` alias as a literal integer read from `ABI_SPEC`. `ABI_CONSTANTS` and the aliases remain generated from the same source; the change removes runtime dictionary reads from native compiler module initialization.

### Pending

Regenerate the compiler ABI module, pass the structural regression, rebuild the current-source `pcc1`, and rerun both the control and Harness build.

## No.2 Disable generated aliases in self-hosted frontend modules

### DENIED

Restoring handwritten tags or conditional self-host behavior would recreate a second ABI source of truth and make the native compiler differ from the host compiler. The generated aliases are valid compiler inputs; only their module-initialization representation must be static.
