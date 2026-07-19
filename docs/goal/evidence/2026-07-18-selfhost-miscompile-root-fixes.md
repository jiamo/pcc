# AUD-P1 self-host miscompile root fixes — DONE_STRONG

## Root cause and fix

The self-host-only type loss was not a set operator defect. A typed receiver
attribute load used this incorrect precedence:

```text
same-named class annotation slot -> instance field
```

For `_InferCtx.globals`, the class annotation global was null, while
`__init__` had correctly populated the instance field. pcc1 therefore passed
`None` as the inference scope; module-level bindings degraded to `DynType`,
which caused the historical cross-module `isinstance`/FuncType and set
discriminator failures.

`attr_load_lowering.py` now follows Python lookup precedence for the static
fast path: data descriptor, instance field, then non-data/class attribute.
The focused minimized runtime regression is
`test_instance_field_precedes_same_named_annotated_class_slot`.

The list-of-functions syntax workaround was removed from
`literal_lowering.py`; the original pcc1 indirect-call test passes through the
real `ListType[FuncType]` path. Set/frozenset now have first-class `SetType`
through inference, export metadata, AST wire encoding, codegen, and marshal/
GC type classification. The old `set_typed_names` and
`_restore_degraded_set_operand` side table is absent.

## Five-class reproducer/classification table

| class | minimal pcc1 gate | classification |
|---|---|---|
| cross-module `isinstance` / FuncType | `test_pcc1_runs_test_list_via_indirect_calls` with the syntax fallback removed | codegen object-model field precedence; fixed at root |
| generator projection | `test_pcc1_smoke_generator` and `test_pcc1_generator_expression_fstring_join` | generator/codegen projection; current focused behavior green |
| set construction/members | `test_pcc1_unannotated_set_binding_keeps_union_type` | inference state corrupted by the same instance/class-field precedence; fixed |
| module data constants | `test_pcc1_cross_module_method_reads_provider_data_constant` | compiled module-global/codegen path; real tuple data remains visible |
| `DynType(name=...)` discriminator | `test_pcc1_module_set_operators_survive_and_do_not_misfire` | fragile type representation; replaced by `SetType` plus boundary canonicalization |

## Gates

```text
focused host class/set gates: 15 passed in 34.85s
pcc1 root reproducer subset: 3 passed in 1.70s
full pcc1 smoke: 56 passed, 1 deselected in 44.48s
five-GC pcc1 -> pcc2 -> pcc3 matrix: 5 passed in 2.43s
```

The matrix result includes normalized pcc2/pcc3 fixed-point comparison and
no-libpython checks for all five backends. The earlier parallel attempt that
ended after three dots without a pytest summary is explicitly excluded.

Open boundary: empty for this row. This evidence does not claim every future
self-host miscompile shares the same cause.
