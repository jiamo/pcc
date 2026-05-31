# Investigation: shadowed cross-module class method dispatch

## Status
resolved

## Problem Description
`tests/test_industrial_cross_module_abi.py::test_shadowing_repro` failed after
the bootstrap fallback fix. Two sibling modules both define `class W`; the
main module constructs `mod_x.W(1)` and `mod_y.W(2)`, then calls `.work()` on
each. The second call was dispatched to `mod_x.W.work`.

Observed output:

```text
11 12
```

Expected output:

```text
11 200
```

## Repro
```bash
env -u LC_ALL uv run pytest tests/test_industrial_cross_module_abi.py::test_shadowing_repro -q -n0
```

## Test [CONFIRMED]
```bash
env -u LC_ALL uv run pytest tests/test_industrial_cross_module_abi.py -q -n0
env -u LC_ALL uv run pytest \
  tests/test_native_cross_module_class_annotation_dispatch.py \
  tests/test_typed_class_field_access.py \
  -q -n0
```

Observed after the fix:

```text
tests/test_industrial_cross_module_abi.py: 2 passed
cross-module annotation + typed class gates: 4 passed
```

## Proposals
- No.1 Preserve qualified extern-class registry keys for shadowed sibling classes [CONFIRMED]

## No.1 Preserve qualified extern-class registry keys

### Code Change
`ClassLowering.declare_extern_class()` no longer overwrites an existing short
class key when a different sibling class with the same leaf name is registered;
the colliding class remains keyed by `module.Class`.

`L1CodeGen._class_hint_for_expr()` now registers a cross-module class
constructor hint before selecting the method target for chained calls like
`mod_y.W(2).work()`. `_ensure_class_type_registered()` returns the actual
registry key produced by `declare_extern_class()` instead of always returning
the short class name.

### CONFIRMED
The generated IR for the repro now dispatches the second chained call to
`user_mod_y_W_work` rather than `user_mod_x_W_work`. The industrial ABI test
and existing cross-module class gates pass.

## Report
No.1 landed. The important distinction is that a short class name is only safe
when it denotes one sibling class in the current native registry. On collision,
the qualified `module.Class` key must flow through constructor hints and method
dispatch. The fix keeps the existing short-name path for non-shadowed classes
while preserving qualified keys for shadowed siblings.
