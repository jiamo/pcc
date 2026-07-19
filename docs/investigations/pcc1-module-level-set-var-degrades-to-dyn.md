# pcc1: module-level `set` variable degrades to `dyn`, breaks `set` operators

## Status

CONFIRMED and FIXED (2026-07-16). Full self-host bootstrap (stage1→stage2→stage3,
`--backend self --python-libpython=off`, pcc2==pcc3 metadata-normalized) is green
with the fix.

## Symptom

Under a **self-hosted pcc1** (not the host compiler), a module-level set-typed
variable used as an operand of a set operator lowered as **integer bitwise-or**
and produced an empty result:

```python
base = {"left"}
combined = base | set(["right"])
print(sorted(combined))   # pcc1: []   (host / CPython: ['left', 'right'])
```

pcc1 IR for the binop showed `py_int_to_i64(base)` + `or` instead of
`py_set_new` + two `py_set_update`.

## Scope (bisected under pcc1, clean end-to-end runs)

- BROKEN: module-level `set` variable as an operand of `| & - ^`.
- OK: set literal `|` set literal (`{"a"} | {"b"}`); module-level `int`, `list`,
  `dict` variables in binops; function-local `set` variables.

The discriminator is the **type representation**, not the operator: `int` /
`list` / `dict` have first-class Type classes (`IntType` / `ListType` /
`DictType`) that survive, while `set`/`frozenset` ride on
`DynType(name="set")` (py_ast has no first-class `SetType`).

## Root cause

`_is_native_set_dyn` (used by the binop set-operator lowering) is
`isinstance(ty, DynType) and ty.name in ("set", "frozenset")`. It requires the
`DynType`'s **`name` discriminator** to be `"set"`.

Instrumenting pcc1's own type inference (env-gated prints, rebuilt stage1)
showed that for a **module-global** set binding the operand reaching the binop
was `DynType(name="dyn")`, i.e. the `"set"` name discriminator was lost during
the module-global type round-trip in the self-hosted inference pass. Concrete
Type classes (`ListType`, etc.) are unaffected because their identity is the
class, not a string field. This is the same family as the pre-existing
`isinstance(TYPE_DYN, DynType)`-across-a-compiled-module-boundary hazard called
out in `_infer_assign`.

The prior uncommitted attempt patched `_infer_assign`
(`stmt.annotation is None or isinstance(ann_ty, DynType)`); that is a correct
clarification but does **not** fix this symptom — the degradation is on the
operand read path, and a fresh pcc1 still produced `[]`.

## Fix

`pcc/py_frontend/type_infer.py`: a syntax-level side table (a plain `set[str]`,
which stays reliable across the bootstrap because it never rides on a fragile
type discriminator), mirroring the existing `setdefault_none_widen_names` /
`functools_module_aliases` patterns:

- `_InferCtx.set_typed_names` — names last bound to a `set`/`frozenset` value.
- `_record_set_typed_name(...)` — populated from `_infer_assign` (add on set
  bind, discard on rebind to a non-set).
- `_restore_degraded_set_operand(...)` — in the `_infer_expr` `BinOp` branch, for
  a set operator (`| & - ^`), re-projects a **degraded** operand (a bare `Name`
  in `set_typed_names` whose inferred type collapsed to generic `dyn`) back to
  `DynType(name="set")`. It never overrides a concrete inferred kind.

Safety: the set-operator lowering requires **both** operands to be native sets
(`_is_native_set_dyn(lhs) and _is_native_set_dyn(rhs)`), so rescuing a single
operand cannot turn `n - 2` (int) into set difference — the literal `2` is never
in `set_typed_names`. Verified `n - 2 == 4` and `n | 1 == 7` still lower as
integers under both host and pcc1.

## Test [CONFIRMED]

- `tests/python/test_pcc1_python_smoke.py::test_pcc1_unannotated_set_binding_keeps_union_type`
  (existing) — now passes on a fresh pcc1 (was `[]`).
- `tests/python/test_pcc1_python_smoke.py::test_pcc1_module_set_operators_survive_and_do_not_misfire`
  (new) — all of `| & - ^` on module-level sets, plus `int` `-`/`|` no-misfire.

Both fail on a stale/pre-fix pcc1 and pass on a pcc1 built from the fixed source.

## Method note (for the next agent)

To observe pcc1's own inference you must rebuild stage1 (`scripts/bootstrap.sh
--stage 1 --out-dir build/bootstrap-dbg`, ~60-90s) with env-gated `print`s in
`type_infer.py`; the host compiler hides this class of bug entirely (host keeps
`DynType(name="set")`). Keep the probes simple — a nested `while`/multi-concat
inside the branch under test can itself be miscompiled and confound the reading.

## Out of scope (pre-existing, NOT this fix)

`tests/python/test_fallback_baseline.py` per-module ON-mode ratchet is red on
`pcc.py_frontend.codegen.class_gen` (27 vs 0) and `pcc.py_frontend.pipeline`
(8 vs 0). Verified **pre-existing at HEAD** (d4cfb078): swapping all five touched
frontend files back to their `HEAD` contents reproduces the identical counts, and
neutralizing the set fix in-process leaves them unchanged. `test_bootstrap_gate_baseline.py`
and `test_ir_py_fallback_baseline.py` are green, and the full no-libpython
bootstrap is green — consistent with a per-module standalone-resolution artifact
(see `docs/...` fallback per-module vs closure note), not a real no-libpython
regression.
