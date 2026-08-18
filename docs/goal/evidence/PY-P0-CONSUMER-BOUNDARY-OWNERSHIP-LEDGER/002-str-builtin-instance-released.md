# PY-P0-CONSUMER-BOUNDARY-OWNERSHIP-LEDGER: str() instance released

## State on arrival (vs the 2026-08-25 ledger evidence)

Re-measured 2026-09-03. Of the three instances the ledger evidence named, two
were already addressed since: `print` (test_print_consumes_owned_argument.py +
test_temporary_call_argument_released.py, 3 passed) and the single-argument
builtins `repr`/`ascii`/`hash` (each already calls
`self._gc_release_if_owned(arg_obj, expr.args[0])` in call_expression_lowering).
Two instances remained: the `str()` builtin and scalar attribute reads.

## Fix (str instance)

`_emit_str_builtin` (builtin_type_attr_lowering.py) generic path calls
`py_obj_str(boxed)` (which BORROWS its operand) and returned without releasing
`boxed`. `str(T())` therefore leaked the owned instance the argument produced,
while CPython frees it when the borrow ends. Added the same shared release the
other single-argument builtins use:

```python
result = self.builder.call(self.runtime["py_obj_str"], [boxed], ...)
self._gc_release_if_owned(boxed, arg)   # NEW: py_obj_str borrows; release owned temp once
return self.builder.call(self.runtime["pcc_gc_resolve_owned_ptr"], [result], ...)
```

`_gc_release_if_owned` consults the value ledger first and the AST classifier
otherwise, and leaves a borrowed operand (a plain name) alone, so `str(name)`
gains no release (a double free would be worse than the leak).

## Gate (test-first, IR contrast)

`tests/python/test_str_builtin_consumes_owned_argument.py`: `direct()` returning
`str(T())` must release the `py_obj_str` operand; `bound(x)` returning `str(x)`
(a borrowed parameter) must NOT. RED before the fix (no release of the owned
`%inst.T` operand); GREEN after. Regression: the print + temporary-call-argument
gates stay green (4 passed together).

## Remaining (row stays IN_PROGRESS)

Scalar attribute reads: `_unbox_scalar_attr_result` and the inline
`py_obj_getattr` + `marshal.marshal_from_object(result, scalar_ty)` sites in
attr_load_lowering.py unbox an owned boxed getattr result to a native scalar
and never release the boxed object. This one needs the value-ledger discipline
(mark the owned getattr result at production, release-if-owned at the unbox
consumer) because `_unbox_scalar_attr_result` is shared with borrowed fast-path
field reads where a blind release would double-free. Left for a focused
follow-up. The row's `test_py_corpus.py` (1500s) close gate is also pending.

## Update — scalar-attr repro is subtler than the inline sites (2026-09-03)

Probing the scalar-attr instance found the reproducer is not the obvious one.
A classmethod `return cls.COUNT` (declared `-> int`) does NOT take the
`py_obj_getattr` + `marshal_from_object(scalar)` unbox path at
attr_load_lowering.py:1540/1734; it emits `py_obj_getattr` -> `pcc_gc_retain`
and returns the object (return-type coercion), so the unbox-and-leak only fires
when the attribute is CONSUMED as a scalar in-expression (e.g. `cls.COUNT + 1`),
not merely returned.

Site classification for the eventual fix:
- 1540, 1734: `result` is a DIRECT `py_obj_getattr` (unambiguously owned) then a
  scalar unbox -> safe to release `result` after the unbox once a repro that
  actually reaches them is built.
- 758: `result` is a PHI of a fast-path field value (possibly borrowed) and a
  `py_obj_getattr` fallback (owned) -> a blind release double-frees the
  fast-path edge; this needs the value-ledger PHI normalization
  (`_note_owned_object_value` on the owned edge only).
- `_unbox_scalar_attr_result` (1812): shared by borrowed fast-path field reads,
  so it must not release unconditionally either.

So the scalar-attr instance is genuinely the value-ledger part of this row
(mark owned at production including PHI edges, release-if-owned at the unbox
consumer), not a blanket release. It carries the row's double-free risk and is
left for a focused follow-up with a scalar-in-expression repro; the `str()`
instance above is landed and safe.

## Update 2 — the documented marshal-unbox scalar-attr path is NOT hit by current codegen (2026-09-03)

Instrumented three scalar-attr-in-expression shapes on current source and
emitted IR:

```text
cls.COUNT + 1  (classmethod)   py_obj_getattr present, py_int_to_i64 ABSENT
t.n + 1        (dyn instance)  py_obj_getattr present, py_int_to_i64 ABSENT
return t.n     (dyn instance)  py_obj_getattr present, py_int_to_i64 ABSENT; result RETAINED for return
```

None reach `marshal.marshal_from_object(result, IntType)` — current codegen
keeps the attribute as a PyObject and does object arithmetic / retain-for-return
rather than unboxing to i64 at the attr-load site. So the 2026-08-25 evidence's
"scalar attribute read via marshal_from_object which releases nothing" path is
not exercised by these shapes on current source (codegen has evolved in the 9
days since). Any remaining leak here is in the object-arithmetic / return-
coercion consumers, not the marshal unbox, and confirming whether one exists
requires a refcount-DIFFERENTIAL measurement (CPython-oracle finalizer /
counted refs), not IR pattern-matching — a wrong release here is a double free.

Consequence: the scalar-attr instance is NOT a marshal-unbox fix. It needs a
dedicated refcount-differential investigation to (a) confirm whether these
shapes still leak and (b) locate the exact consumer, before any release is
added. The `str()` instance (above) remains the landed, safe slice of this row;
the scalar-attr instance is re-scoped to that investigation plus the row's
`test_py_corpus.py` (1500s) close gate.
