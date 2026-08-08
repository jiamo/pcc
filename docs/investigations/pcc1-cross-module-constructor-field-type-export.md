# Investigation: constructor-initialized class fields lose cross-module types

## Status

resolved

## Problem Description

After fixing the unannotated return ABI and rebuilding current pcc1, the native Harness durable Session path still resolved `sessionStore` as `None`. Disassembly of `HarnessRuntime.resolve_session_store()` showed that `self.kernel.context` was lowered through `py_obj_getattr`, and the following `context.get("sessionStore")` was incorrectly specialized as `py_dict_get_default`.

`PluginKernel.context` is initialized with `PluginContext("harness")` in `PluginKernel.__init__`. Local type inference recognizes constructor calls when building a class field schema. The closed-world export pass only records a field type from an explicit annotation or an annotated constructor parameter, so the imported `PluginKernel` schema exported `context` as dynamic. A downstream nested field access consequently lost the receiver class and allowed the generic `get` name to select the dictionary builtin.

## Repro

```bash
gtimeout 300s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_py_cross_module_class_inference.py \
  -k constructor_initialized_cross_module_field
```

The reduced program exports a `Kernel` whose unannotated `context` field is initialized by a same-module `Context` constructor. An importing `Runtime` calls `self.kernel.context.get(...)`. Before the fix the binary prints `None` or misuses that value instead of returning the registered `Store`.

## Test [CONFIRMED]

The regression must print the Store path, contain pointer-returning native method definitions, and avoid `py_dict_get_default` for the user-defined `Context.get` call. The rebuilt Harness durable Session integration remains the product-level gate.

## Proposals

- No.1 Export same-module constructor field types [implemented]

## No.1 Export same-module constructor field types

### Code Change

Collect all top-level class names before building class exports. When an unannotated `self.field` assignment in `__init__` calls one of those classes, record that class reference in the exported field type table. This brings the closed-world export schema into parity with local `_class_fields_from_def` inference without guessing arbitrary method-call results.

### Validation

The reduced regression failed with `AttributeError: path` before the change and passed afterward; the emitted call no longer uses `py_dict_get_default`. The broader return/export group passed with 8 tests. Current pcc1 self-bootstrap, both Harness self-checks, and the 2-test Cordis plus durable Session integration also passed.
