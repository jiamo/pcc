# Investigation: pcc1→pcc2 stage 2 fails with LiftError AttributeError obj across unrelated modules

## Status
resolved

## Problem Description

A cold `scripts/bootstrap.sh --backend self` chain built stage 1
successfully (`rc=0`, 282 s) but stage 2 (`build/bootstrap/pcc1` compiling
`pcc/__main__.py` into `pcc2`) fails after ~130-143 s with parallel
frontend workers reporting:

```text
pcc frontend worker failed: LiftError: AttributeError: obj |
nested-stmt #11 type=_Assign line=1805 in pcc/py_frontend/pipeline.py |
top-stmt #446 line=1729 in pcc/py_frontend/pipeline.py
```

and the same `AttributeError: obj` shape on `pipeline_packages.py`,
`pipeline_self_backend_config.py`, `pipeline_profile.py`,
`package_environment.py`, `pipeline_pass_driver.py`, `pipeline_paths.py`,
`backend/self_backend_parse.py`, `pipeline_self_backend_emit.py`,
`codegen/debug_info_lowering.py`. The failure is systemic (many workers,
unrelated files), not file-specific.

Discovered 2026-08-26 while running the cold fixed-point chain owed by
`GC-P0-CONTAINER-CALLBACK-MUTATION-COMMIT`. No cold chain had been run on
this working tree before today (recorded on that row), so the break's age
is unbounded by prior evidence; other sessions modified runtime sources
(`py_obj_ops_compare.{c,py}`) the same day.

## Repro

```bash
gtimeout 3600s bash scripts/bootstrap.sh --backend self --stage 2
# PCC_BOOTSTRAP_STAGE_FAILED stage=2 rc=1 after ~130s
```

Stage-1 binary itself is healthy: a minimal function-def program compiles
and runs correctly through `pcc1 --backend self --python-libpython=off`
(smoke printed 42, exit 0).

## Test [CONFIRMED]

Failure observed personally 2026-08-26, twice:

1. With the sweep-verdict fix slice present:
   `stage=2 elapsed_ms=128907 rc=1`.
2. With that slice reverted to HEAD-equivalent (only comment residue
   differs) and stage 1 rebuilt: `stage=2 elapsed_ms=142639 rc=1`.

## Proposals

- No.1 Attribute the break to a specific uncommitted runtime/frontend
  change by bisecting the dirty working tree (candidates:
  `py_obj_ops_compare.{c,py}`, SEM-P1 instance-eq dispatch wiring in
  `py_obj*.py/c`) — each candidate reverted one at a time with a
  cache-invalidated stage-1 rebuild between attempts     [pending]
- No.2 Read the lift path raising bare `AttributeError` on name `obj`
  (`pcc/parse/py_lift.py` / `py_ast.py`) to identify which construct in
  the failing files triggers it, then minimize to a small module
  reproducing under host `pcc --backend self` directly     [pending]

## Nonclaims

- Not caused by the sweep-verdict relocation fix (measured, see Test).
- Not yet known whether host `pcc` (pcc0) lifts the same files cleanly;
  if it does, the defect is specific to stage-1-compiled frontend code.

## Update (2026-08-26, bisect): the SEM-P1 eq wiring in py_obj_ops_compare is the cause

Reverted `py_obj_ops_compare.{c,py}` (the SEM-P1
instance-`__eq__`-in-container-keys dispatch wiring, another session's
uncommitted change) to HEAD and rebuilt stage 1: stage 2 no longer fails
in lift. It ran 15m37s of real compilation work and failed LATER, in the
self-backend emit phase with different errors (`ret ptr null`,
`target-final safepoint label missing`, `managed slot address ... cannot
be resolved to one stack alloca`). Verdict:

- The `LiftError: AttributeError: obj` family is CAUSED by the py_obj_eq
  user-dispatch wiring; mechanism hypothesis: compiled frontend code's
  isinstance/attribute dispatch or dict probing depends on the previous
  fall-through equality for instance-tagged operands, and the new
  tri-state path perturbs it. Mechanism confirmation still owed.
- A second, later failure now surfaces at emit (safepoint label /
  stack-map slot resolution) — unknown whether pre-existing behind the
  lift wall or newly exposed. Do not assume either.

Files restored to the other session's state after the experiment;
reproduction requires re-reverting them for any further bisect step.

## Update (2026-08-26, mechanism and fix)

The instance-equality dispatch was not itself wrong. It made generated
dataclass `__eq__` reachable for the first time, exposing that pcc's synthetic
method compared field tuples without first checking the concrete class. An AST
dataclass with field `obj` could therefore receive an unrelated AST dataclass
and immediately raise `AttributeError: obj` (the same mechanism explains the
many unrelated worker modules).

`class_gen.py` now emits the concrete-class guard before any field read.
`tests/python/test_dataclasses_full.py::test_dataclass_eq_rejects_other_dataclass_shape`
reproduces the former cross-shape read in both directions and passes. The
instance-equality dispatch remains enabled; its reflected dispatch and
exception-preservation consumers are covered separately by the equality and
container-contract tests.

The next emit-phase failures were distinct and are closed in
[`pcc1-stage2-stackmap-block-label-hash-miss.md`](pcc1-stage2-stackmap-block-label-hash-miss.md).
On the final frozen compiler source, stage 2 completed in 1,290,895 ms, stage 3
completed in 384,764 ms, and pcc2/pcc3 were byte-identical.

## Report

The confirmed fix is the generated dataclass concrete-class guard, not a
special case in `py_obj_eq`, the lifter, or compiler-closure classes. It keeps
normal runtime instance equality active while preventing unrelated dataclass
layouts from reading each other's fields. The full strict self/no-libpython
fixed point is green; the 21m31s stage-2 time is a separate performance P0 and
is not hidden by this correctness closure.
