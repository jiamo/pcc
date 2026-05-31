# Investigation: Layer1 host helper extraction needs contextual host type

## Status
active

## Problem Description

`pcc/py_frontend/codegen/layer1.py` is still large. A natural next split is to
move the remaining `isinstance` / `issubclass` lowering bodies into a separate
module while leaving `L1CodeGen` as the composition class.

## Repro

Attempted extraction:

1. Add `pcc/py_frontend/codegen/isinstance_lowering.py`.
2. Move `L1CodeGen._compile_time_isinstance`,
   `_emit_builtin_runtime_isinstance`, `_ir_scaffold_class_symbol`,
   `_emit_ir_scaffold_isinstance`, `_maybe_emit_issubclass_builtin`,
   `_class_is_subclass`, and `_emit_isinstance_call` bodies into plain helper
   functions.
3. Keep thin wrapper methods on `L1CodeGen` so the concrete method table still
   has the same names.
4. Run strict pcc1 compile:

```bash
env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 240 \
  uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc1_isinstance_split_probe
```

Observed failure:

```text
Python pipeline requires libpython fallback for multi-file compile
(modules: pcc.py_frontend.codegen.isinstance_lowering)
```

## Root Cause

The extracted helper functions receive the `L1CodeGen` instance as a normal
`host` parameter. Current type inference does not assign that parameter the
real closed-world `L1CodeGen` type. As a result, calls such as:

```python
host._emit_as_object(expr)
host.builder.call(...)
host._fresh("name")
host._resolve_class_alias(name)
```

lower as dynamic Python attribute lookups/calls:

```text
py_cpy_getattr
py_cpy_call1
py_cpy_call_kw
```

This is a real self-host violation, not a per-module reporting artifact.
The method bodies work when they stay directly on `L1CodeGen` because the
frontend sees `self` as the concrete class during closed-world compilation.

## Confirmed Non-Solution

Moving these bodies to a mixin or plain helper function without a contextual
host/self type is not safe. The wrapper method preserves Python method-table
shape but does not preserve static type information inside the extracted
function body.

The failed extraction was removed after confirming the root cause. The
`layer1.py` comment above the `isinstance` methods now records this boundary.

## Correct Direction

Implement a contextual host type mechanism before extracting this class of
code:

1. `self`/`host` overlay for helper modules that operate on `L1CodeGen`.
2. A typed `L1CodeGen` host protocol or equivalent interface in type
   inference.
3. Direct/native lowering for calls through that interface instead of dynamic
   `py_cpy_*` dispatch.

After that exists, the remaining host-method groups in `layer1.py` can move
out without reintroducing libpython fallback.

## Validation After Reverting Failed Extraction

```bash
env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 240 \
  uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc1_isinstance_restore_probe
```

Result:

```text
exit code 0
```

## Update 2026-05-14: safe cleanup boundary

After the failed helper extraction, `layer1.py` was still reduced safely by
removing obsolete local AST aliases and unused imports left behind by earlier
splits. This does not move any `L1CodeGen` host-dependent method body across
a module boundary.

Current size after that cleanup:

```text
pcc/py_frontend/codegen/layer1.py  957 lines
```

Validation:

```text
direct strict pcc1 compile
  exit code 0

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 56.25s
```

The remaining `layer1.py` split candidates are now mostly host-dependent
methods or composition glue. Further extraction should wait for contextual
host/self typing and direct host-interface call lowering rather than moving
those methods as plain helpers.

## Update 2026-05-14: first type-inference slice for contextual host params

The type-inference layer now has an opt-in hook:

```python
infer_module(
    module,
    contextual_host_params={"helper_name": ("host",)},
)
```

For marked helper functions, an unannotated `host` parameter is typed as a
synthetic `pcc.py_frontend.codegen.layer1.L1CodeGen` host. The synthetic type
exposes the host contract attrs as `dyn` fields and the host contract methods
as callable fields. Known return types are preserved for the first critical
methods, for example:

```text
host._fresh("probe") -> str
host._ir_scaffold_enabled() -> bool
host._class_is_subclass(a, b) -> bool
```

Focused regression:

```text
tests/python/test_py_class_schema_type_infer.py::test_contextual_l1_codegen_host_param_types_host_methods
  1 passed in 0.18s
```

Strict direct pcc1 compile after this type-inference-only slice:

```text
env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 240 \
  uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
  pcc/__main__.py -o /tmp/pcc1_host_overlay_probe

exit code 0
```

This does not yet make `isinstance` helper extraction safe. It only prevents
the extracted helper body from immediately becoming `DynType` at inference
time. The next required slice is codegen support for direct calls through the
synthetic host interface, so `host._fresh(...)` lowers to the same native path
as `self._fresh(...)` instead of becoming a generic object/CPython method
call.

## Update 2026-05-14: automatic host-param detection for codegen helpers

The pipeline now automatically enables `contextual_host_params` for top-level
helper functions in `pcc.py_frontend.codegen.*` modules when the first
parameter is named `host`.

Scope:

```text
pcc.py_frontend.codegen.*:
  def helper(host, ...): ...
    -> contextual_host_params={"helper": ("host",)}

other modules:
  no automatic host typing
```

This rule is intentionally narrow. The current tree has no such helper yet,
so existing bootstrap behavior is unchanged, but future host-dependent helper
extractions get the right type-inference context without custom call-site
plumbing.

A manual probe using the full bootstrap closed-world export table showed that
`host._fresh("probe")` can already generate IR with zero `py_cpy_*` calls when
the helper module is inferred with this contextual host type. The earlier
two-file probe failed because it did not include `layer1.py` and its mixin
bases in the native export table.

Focused and bootstrap gates:

```text
tests/python/test_py_class_schema_type_infer.py
  9 passed in 0.16s

direct strict pcc1 compile
  exit code 0

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 54.01s
```

## Update 2026-05-14: first host-dependent helper extraction is native

`isinstance` / `issubclass` helper bodies have been moved into
`pcc.py_frontend.codegen.isinstance_lowering` while preserving thin
host-owned wrapper methods on `L1CodeGen`.

The first attempt after type-inference wiring still failed direct strict
pcc1 compile:

```text
Python pipeline requires libpython fallback for multi-file compile
(modules: pcc.py_frontend.codegen.isinstance_lowering)
```

The root cause was not the baseline. The contextual `host` parameter was
typed as `L1CodeGen`, but two host-dependent call shapes were still outside
the native lowering model:

```text
host.builder.call(...)
host.builder.icmp_signed(...)
host.builder.or_(...)
host.class_lowering.emit_isinstance(...)
```

`host.builder.*` was missing from the IR scaffold syntactic recognizer, which
only accepted `self.builder`, `parent.builder`, `self.parent.builder`, and
local `builder` aliases. `host.class_lowering` was still typed as `DynType`,
so `emit_isinstance` lowered as a generic CPython method call.

Fix:

```text
pcc/py_frontend/codegen/ir_scaffold_lowering.py
  accepts host.builder.* as an IR scaffold receiver

pcc/py_frontend/type_infer.py
  types synthetic host.class_lowering as ClassLowering

pcc/py_frontend/codegen/host_contract.py
pcc/py_frontend/pipeline.py
  classify isinstance_lowering as contextual-mixin

tests/python/test_fallback_baseline.py
  adds explicit ON-mode contextual fallback-zero regression
```

Focused evidence:

```text
compile_contextual_per_module_fallback_counts(
  pcc.py_frontend.codegen.isinstance_lowering,
  ir_scaffold_mode="on",
)
  -> 0

tests/python/test_fallback_baseline.py::test_on_mode_isinstance_helper_contextual_fallback_zero
  1 passed in 45.00s

tests/python/test_fallback_baseline.py::test_contextual_per_module_fallbacks_under_ratchet
  1 passed in 45.50s

tests/python/test_py_class_schema_type_infer.py
  9 passed in 0.03s
```

Strict pcc1 evidence:

```text
direct strict pcc1 compile
  env -u LC_ALL perl -e 'alarm shift; exec @ARGV' 240 \
    uv run pcc --backend self --python-libpython=off --ir-scaffold=on \
    pcc/__main__.py -o /tmp/pcc1_isinstance_context_probe
  exit code 0

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 74.40s
```

## Update 2026-05-14: layer1 constant cleanup after helper extraction

After `isinstance_lowering` became a real contextual-host helper, the
remaining `layer1.py` file still carried old top-level constants that were no
longer referenced by the module. Those were removed instead of moved to a new
module.

Result:

```text
pcc/py_frontend/codegen/layer1.py
  746 lines -> 603 lines
```

Validation:

```text
direct strict pcc1 compile
  exit code 0

tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self
  1 passed in 53.20s
```
