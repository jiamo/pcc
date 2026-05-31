# Investigation: pcc2 py_ast closed-world field export becomes empty

## Symptom

`tests/python/test_pcc_bootstrap_full.py` regresses in the self-backend
bootstrap path. The minimized reproducer is a stage2 compiler compiling
`pcc/parse/py_lift.py`:

```bash
timeout 180s env -u LC_ALL ./build/bootstrap-pytest-self-debug-freshN/pcc2 \
  --ir-scaffold=on --backend self --python-libpython off \
  pcc/parse/py_lift.py -o /tmp/pcc_py_lift_single
```

Observed failure:

```text
error: PCC-PY-COMPILE-001: [python-frontend] type_infer[pcc.parse.py_lift]: op
```

With temporary `PCC_DEBUG_BOOTSTRAP_TRACE=1` instrumentation, the failing
object is a `UnaryOp` seen by `type_infer`; `type_infer` tries to read
`expr.op`, but the stage2 object has no valid `op` field.

## Facts established

- The original broad `build_closed_world_context` exception reporting hid the
  true layer. Narrow profiling reduced the failure from
  `build_closed_world_context: name` to `type_infer[pcc.parse.py_lift]: op`.
- `pcc1` compiling `pcc/parse/py_lift.py` directly exports the expected
  `pcc.py_frontend.py_ast` fields:

  ```text
  Expr fields=span,ty
  BinOp fields=span,ty,op,lhs,rhs
  UnaryOp fields=span,ty,op,operand
  BoolExpr fields=span,ty,op,left,right
  Attr fields=span,ty,obj,name
  ```

- `pcc2` compiling the same file exports empty field lists for the same
  classes:

  ```text
  Expr fields=
  BinOp fields=
  UnaryOp fields=
  BoolExpr fields=
  Attr fields=
  ```

- Therefore the immediate bug is not that `py_lift._s_Assert` or
  `py_lift._e_UnaryOp` forgot to pass `op`. It is that the stage2
  closed-world class/export context loses the `py_ast` dataclass field schema,
  so downstream static field access is compiled against an empty class shape.
- Changing `py_lift` to use positional `pa.*(...)` constructors is still
  useful as a hardening step, but it did not by itself fix the stage2
  export loss.
- The current narrow workaround under test is to restore `field_names` for
  `pcc.py_frontend.py_ast` from a static py_ast contract when the stage2
  export path produces an empty schema.

## Why this is hard

This is a self-host consistency failure, not a normal host-Python unit
failure. Stage0/CPython and pcc1 can both see valid AST/dataclass fields.
The generated pcc2 binary then re-runs the same semantic pipeline but loses
field metadata while rebuilding the closed-world export table. That means
ordinary CPython inspection of `parse_and_lift("py_ast.py")` is not enough;
the failure only appears after generated stage code is used as the compiler.

The bad state also presents as a tiny `AttributeError("op")`, so without
stage-specific tracing it looks like a local expression-lowering bug in
`py_lift` or `type_infer`. The distinguishing signal is the pcc1/pcc2
comparison of `build_closed_world_context` export fields.

## Valhalla note

There is no direct evidence yet that the Valhalla value model caused this.
The failure is in ordinary `py_ast` dataclass/class metadata (`Expr`,
`UnaryOp`, `BinOp`, `Attr`), before valueclass payload semantics are involved.
Valhalla may be adjacent because recent work touched class/type export,
`ClassType` shape, and dataclass-heavy frontend code, but the proved symptom
is broader: pcc2 cannot faithfully re-export `py_ast` field names.

## Open questions

1. Why does pcc2's `build_closed_world_context` recognize the top-level
   `ClassDef` nodes but not recover their dataclass field assignments?
2. Is `stmt.name` / target `ident` in pcc2 a non-host-string object whose
   equality/hash breaks dict lookup and membership tests?
3. Should the final fix be a general closed-world shape-based AST accessor,
   or a py_ast-specific bootstrap contract table?

## Current next step

Rebuild pcc1/pcc2 after changing the py_ast field override lookup to use
`str(stmt.name)`, then rerun the minimized `pcc2 pcc/parse/py_lift.py`
compile. If it passes, remove temporary debug logs, keep the smallest
production-safe contract fix, and add a regression that validates stage2
exports non-empty `py_ast` field names.

## Update 2026-05-23: stage2/stage3 fixed point restored

The original `py_lift.py` symptom was the first visible failure, not the full
bug. After restoring enough `py_ast` schema to get past the initial
`UnaryOp.op` read, the bootstrap kept exposing new pcc2/pcc3 differences in
later modules. The useful diagnostic loop was:

1. run the focused full bootstrap test under a hard timeout
2. compare the first pcc2/pcc3 LLVM IR size/hash mismatch
3. dump the corresponding stage2 and stage3 module IR
4. diff the first user function that diverged
5. add only the missing static contract needed for that divergence

The final root class was closed-world schema drift across generated stages:
pcc2 could compile the same source but rebuild a weaker export/type fixed
point than stage3 for imported frontend classes. That affected both
`pcc.py_frontend.py_ast` and `pcc.llvm_capi.ir`.

Concrete fixes landed in this slice:

- `type_infer` now falls back to a static `py_ast` contract for imported AST
  class fields and base classes when the generated export omits them. This
  covers expression, statement, type, function, class, and import nodes that
  showed up in the pcc2/pcc3 trace.
- `type_infer` also has a static `pcc.llvm_capi.ir` contract for the small
  IR object model used by the self-backend path, including `Module`,
  `Function`, `Block`, `IRBuilder`, `Value`, `GlobalVariable`, and the common
  type records.
- `pipeline` normalizes bare builtin `dict` annotations to `DictType`, matching
  the existing handling for bare `list` and `tuple`. Before this, exported
  `dict` could become a user `ClassType("dict")`, which made
  `module.globals.get(name)` fall back dynamically in later stages.
- `_expr_type_name()` now returns the statically known `str` field directly
  instead of wrapping it in `str(...)`. The wrapper created a stage-dependent
  lowering difference: one stage returned the field, the other emitted
  `py_obj_str()`.
- `native_threading` now reads `Import.names` through a small typed helper.
  This avoids a stage-dependent positive-`isinstance` narrowing difference
  where one stage used dynamic `py_obj_getattr(stmt, "names")` and the next
  used the concrete instance field.

One tempting path was adding an `IRBuilder_call2` helper after seeing a call
shape in the trace. That was the wrong direction: `pcc/llvm_capi/ir.py`
already has `IRBuilder_call0` through `IRBuilder_call7` plus
`IRBuilder_call_dyn`. The real mismatch was not call arity support; it was
that the pcc2 closed-world view of the imported IR/AST classes was weaker
than the pcc3 view.

Evidence after the fix:

```bash
env -u LC_ALL uv run pytest tests/python/test_py_class_schema_type_infer.py \
  tests/python/test_value_model_valhalla.py -q -n0
# 18 passed

/opt/homebrew/bin/timeout 900s env -u LC_ALL uv run pytest \
  tests/python/test_pcc_bootstrap_full.py -q -n0
# 1 passed in 99.73s

/opt/homebrew/bin/timeout 300s env -u LC_ALL uv run pytest \
  tests/python/test_bootstrap_gate_baseline.py -q -n0
# 4 skipped
```

The fallback ratchet gate is no longer hidden by a crash after fixing a
separate `profile` NameError in `compile_contextual_per_module_fallback_counts`,
but it is not green yet:

```text
tests/python/test_fallback_baseline.py tests/python/test_ir_py_fallback_baseline.py
2 failed, 15 passed

test_per_module_fallbacks_under_ratchet:
  pcc.py_frontend.codegen.class_gen: 298 vs baseline 216
  pcc.py_frontend.type_infer: 911 vs baseline 846

test_on_mode_per_module_fallbacks_under_ratchet:
  pcc.py_frontend.codegen.class_gen: 298 vs ON baseline 216
  pcc.py_frontend.codegen.marshal: 38 vs ON baseline 29
  pcc.py_frontend.type_infer: 911 vs ON baseline 846
```

This remaining fallback count drift is a separate follow-up from the original
full-bootstrap failure. Do not update the baseline without first explaining
which new dynamic fallback sites are intentional.

Why this took longer than expected: the first symptoms were tiny local-looking
attribute/call differences, but each one was downstream of a generated-stage
type/export fixed point. `py_ast.py` existed and was valid; the bug was that
the stage compiler did not preserve or consume its class schema consistently.
Valhalla remains an adjacent stressor, not a proven direct cause.

Recommended diagnostic component: add a bootstrap divergence reporter that
captures, for the first pcc2/pcc3 mismatch, (1) stage module hash/size, (2)
first differing user function in LLVM IR, (3) closed-world export snapshots for
`pcc.py_frontend.py_ast` and `pcc.llvm_capi.ir`, and (4) a summary of new
dynamic `py_obj_getattr`/`py_obj_call` sites. That would have pointed at schema
drift directly instead of forcing manual IR archaeology.
