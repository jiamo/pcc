# Valueclass aggregate arity projection

Mode: host pcc frontend plus contextual strict scaffold compilation.  Current
pcc1 executable evidence is deliberately deferred to the final sequential
build.

The valueclass ABI mirrors previously enumerated field counts one through
seven and returned `None` at field eight.  That silently selected the object
ABI and emitted `py_instance_new` for an otherwise valid opt-in valueclass.
Both mirrors now pass the runtime-built field-type list to the existing
`LiteralStructType_dyn` scaffold route.  Unsupported field types retain their
existing explicit projection boundary; field count alone no longer changes
the ABI.

Evidence:

- RED: the new eight-field regression found `call ... @py_instance_new`.
- GREEN: `test_eight_field_valueclass_keeps_aggregate_projection` passed and
  observes the eight-i64 aggregate plus field extraction.
- `test_contextual_valueclass_arity_projection_remains_native` passed in
  16.71s with exact zero fallback for `class_gen` and `type_abi_lowering`.
- Existing two-field and six/seven-field aggregate regressions passed 2/2.
- Static search found no second valueclass field-count cap; field construction,
  extraction, nested traversal and pointer-root discovery iterate the complete
  declared field sequence.

One existing nested self-backend executable node exceeded a separate 60-second
diagnostic budget and produced no final summary, so it is not green evidence.
Current-pcc1 strict self/no-libpython execution remains the only boundary.
