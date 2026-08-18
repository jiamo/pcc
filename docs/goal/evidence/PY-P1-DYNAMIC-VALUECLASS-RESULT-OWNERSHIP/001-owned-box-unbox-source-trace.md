# Owned dynamic valueclass results are not consumed during unboxing

Status: source/retained-IR validated; executable lifetime regression still needed.
Task: PY-P1-DYNAMIC-VALUECLASS-RESULT-OWNERSHIP.
Date: 2026-09-05.

The v75 type-erasure regression exposed a distinct ownership path. In retained
`IndexedFunctionKernel.block_phi_fact` contextual IR, `py_obj_call` returns a
ValueBox, which is checked and read through two `py_valuebox_get_field` calls.
The call result has no release on success or the type-error path before the
native aggregate is returned; only the borrowed receiver frame is removed.

Source chain: `method_call_expression_lowering.py` marks the dynamic call value
owned; `return_lowering` coerces it; `coercion_lowering._coerce` delegates to
`type_abi_lowering._emit_object_to_valueclass_payload`, which substitutes the
aggregate without consuming the original owner. The ownership ledger is not
an automatic cleanup mechanism.

`py_valuebox_get_field` also returns an owned reference through
`py_instance_get_field` (both pcc-Python and C mirror). Scalar/nested-box
temporaries need release, while pointer-field ownership must transfer into the
aggregate's traced payload. A shared unbox helper cannot simply release every
input: borrowed controls and callers with their own cleanup must stay correct.

Minimal proposed regression: a Dyn `factory` receiver calls a method returning
an opt-in valueclass; a typed function returns that result as an aggregate.
Require correct dynamic-result consumption in IR and a finalizer-tracked pointer
payload lifetime test. Include borrowed input and wrong-return-type controls.

Restoring precise arena fields removes this path from the compiler's normal
getters, but does not fix the generic dynamic ownership defect. No runtime
lifetime or five-GC remediation claim is made from this source trace.
