# Investigation: for v in [int list] stores i64 into a boxed (ptr) slot — link error / empty loop

## Status
resolved

## Problem Description

Iterating a list with `int` elements (`for v in xs:` where `xs` is
`list[int]`) is miscompiled when the loop variable's storage slot is a boxed
`PyObject*` (which it is whenever int-boxing is active for the enclosing
scope). The native-i64 list fast path stores a raw `i64` element into the
pointer slot:

- under the **llvm backend**: a hard link/verify error
  `'%for.elem.i64.N' defined with type 'i64' but expected 'ptr'`
  (`store ptr %for.elem.i64.N, ptr %item.addr`);
- under **`--backend self`**: silently miscompiles to a **zero-iteration**
  loop — the program runs and prints nothing.

This is a very common construct (`for v in [10, 20, 30]:`), so the silent
self-backend variant is especially dangerous. Found while testing the
generator enumerate fix
([python-generator-enumerate-counter-not-persisted.md](python-generator-enumerate-counter-not-persisted.md));
initially and **wrongly** attributed to a concurrent agent's uncommitted edits
— `git diff` showed `call_expression_lowering.py` / `py_class.py` clean, and
the failing function `_emit_for_list_index` is at committed HEAD (untouched by
this session's edits). The bug is in committed code; the trigger is the
int-boxing slot-type condition, not any uncommitted edit.

## Repro

```python
def main() -> None:
    for v in [10, 20]:
        print(v)

if __name__ == "__main__":
    main()
```

`env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on
/tmp/g.py -o /tmp/g.out && /tmp/g.out` → prints nothing (expected `10` then
`20`). Under the llvm backend the same source fails to link with the i64/ptr
error.

## Test [CONFIRMED]

Already-present tests gate it (they were failing before the fix, pass after):
- `tests/python/test_python_iteration_parity.py::test_for_over_list`
  (`for v in [10, 20, 30]` summed)
- `tests/python/test_py_for_generic_iterable.py::test_for_loop_falls_back_to_object_iterator_for_pointer_typed_iterable`
  (`xs` optional `None | list[int]`, falls into the list path with a boxed
  slot).

Confirmed: both failing before (i64/ptr link error), both passing after.

## Proposals

- No.1 Gate the native-i64 list fast path on a native-i64 target slot  [CONFIRMED]

## No.1 Gate the native-i64 list fast path on a native-i64 target slot

### Code Change

`pcc/py_frontend/codegen/for_loop_lowering.py::_emit_for_list_index`, the
element-fetch branch:

```python
if (
    isinstance(iter_ty, ListType)
    and isinstance(elem_ty, IntType)
    and not isinstance(target_ir_ty, ir.PointerType)
):
    native_val = self.builder.call(
        self.runtime["py_list_get_i64_nonnegative"], [iter_obj, cur], ...)
    self.builder.store(native_val, target_alloca)
else:
    ...existing boxed / marshal path...
```

### CONFIRMED

Root cause: `_emit_for_list_index` allocates the loop-variable slot via
`self._storage_ir_type(elem_ty)` (line ~201). For `IntType` under int-boxing,
`_storage_ir_type` returns `_CSTR` (a `PyObject*`) — see
`type_abi_lowering.py::_storage_ir_type` /`_int_exprs_are_boxed`. But the
element-fetch fast path keyed only on `isinstance(iter_ty, ListType) and
isinstance(elem_ty, IntType)` always called `py_list_get_i64_nonnegative`
(returns raw `i64`) and stored it into that pointer slot — an i64-into-ptr
store. The `else` branch already does the correct thing for a boxed slot: it
calls `py_list_get` (returns the `PyObject*` element) and stores the object
(line ~240, `isinstance(target_ir_ty, ir.PointerType)` → store `elem_obj`). The
fix simply makes the fast path require a genuinely native-i64 slot; the boxed
case now flows through the object path.

Conservative: when the slot is native i64 (boxing off), behavior is unchanged
(unboxed fast path). Only the previously-miscompiled boxed case is rerouted.

Evidence:
- Repro `for v in [10, 20]` → `10 / 20` ✓ (`--backend self`).
- `test_python_iteration_parity.py test_py_for_generic_iterable.py
  test_python_generator_parity.py -q -n0` → 21 passed (the two
  previously-failing list tests now pass; no regressions).
- Side benefit: int-element enumerate generators
  (`for i, v in enumerate([ints]): yield ...`) now also work — they were only
  failing because their inner `for v in [ints]` hit this same bug.
- Mandatory self-host gate `test_pcc_bootstrap_full.py
  ::test_full_three_stage_bootstrap_self` → 1 passed (stage1→stage2→stage3).

## Report

Landed No.1, a one-condition gate on the native-i64 list fast path. Corrects
the earlier mis-attribution: this is a committed-HEAD bug triggered by the
int-boxing slot-type condition, not a concurrent agent's uncommitted edit
(those files are clean). The earlier evidence-ledger entries that named a
"concurrent stopped agent" for these two failures are inaccurate and have been
corrected in `docs/current-goal-state.md`.
