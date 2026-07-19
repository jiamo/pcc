# pcc1 dynamic class descriptor fixed-layout state

Resolved locally 2026-07-17.  A current-source pcc1 compiled a runtime class
function replacement (`Child.label = replacement`) as a direct class-attribute
load, so instance lookup returned the unbound function and `m.__self__` raised
`AttributeError`.  Host stage0 remained correct, and both the prior hoist-split
pcc1 and a newly built pcc1 reproduced the failure, ruling out the adjacent
floor-division consolidation.

## First boundary and root cause

The first divergent boundary was generated IR: host stage0 emitted
`py_obj_getattr(obj, "label")`, while pcc1 emitted a direct load from the
`Child.label` class-attribute global.  Runtime mutation tracking depended on
`L1CodeGen._class_attr_runtime_state` and
`_class_attr_mutation_in_loop_depth`, but these fields were created lazily.
That works for the host Python object and is not a valid fixed-layout contract
for compiled `L1CodeGen`.  Initializing both fields in `Layer1InitMixin` and
declaring them in `host_contract.py` restores the dynamic lookup and descriptor
binding path.

## Stacked pcc2 failure

The first fixed-point attempt then exposed a separate stage2 failure:
`AttributeError: body` while hoisting a nested closure beside a `try/except`.
The compiled stage could not reliably project direct `ExceptHandler.body`,
`.name`, and `.exc_type` accesses.  All hoist passes now use their existing
cross-stage `_dataclass_field_value` boundary for those fields.  A 10-line
nested-closure/try reproducer failed with the old pcc2 and prints `rootx` with
the rebuilt pcc2.

The outer `RuntimeError: no active exception to reraise` seen during this
failure is a separate bare-reraise gap: it masked the original exception but
did not cause either descriptor or handler-projection bug.

That separate gap was resolved later on 2026-07-17. `_active_handler_excs` was
another lazily created `L1CodeGen` field that pcc1's fixed object layout could
not preserve across handler lowering. It is now initialized constructor state
and part of the closed-world host contract; see
`docs/goal/evidence/2026-07-17-pcc1-bare-reraise-active-exception.md`.

## Verification

- current pcc1 descriptor, no-host artifact, pcc2/pcc3 descriptor, and pcc2
  nested-closure/try regression: 4 passed;
- descriptor/classmethod neighborhood: 12 passed;
- pcc2/pcc3 are byte-identical after Mach-O signature/UUID normalization;
- fallback/IR baseline is recorded in the task evidence after its final gate.
