# Investigation: reflected arithmetic operators __radd__/__rmul__/... (no-libpython)

## Status
active — gap CONFIRMED; a FRONTEND static-dispatch fix was ATTEMPTED and
REVERTED 2026-05-30 (it dispatched the reflected dunder but the result-type
propagation / repr was wrong, and it missed no-__init__ classes). Needs a
type-flow-aware retry.

## Problem Description
`scalar <op> instance` where the instance defines a reflected dunder fails:
```python
class V:
    def __init__(self, x): self.x = x
    def __radd__(self, o): return V(o + self.x)
    def __rmul__(self, o): return self.x * o
1 + V(5)   # TypeError: unsupported operand type(s) for +   (CPython: V6)
3 * V(5)   # TypeError                                       (CPython: 15)
```
Found 2026-05-30 by the real33 batch probe (which otherwise was IDENTICAL for
__add__ forward, container protocol __getitem__/__len__, [*a,*b]/{*a,*b}/{**a,**b}
unpacking, __call__, +=, f"{x!r:>6}", yield from, multiple-inheritance super()).

## Root cause
`a <op> b` with a builtin/scalar `a` and a user-instance `b`: the frontend
routes the mixed binop to the generic runtime helper (py_obj_add / py_obj_sub /
py_obj_mul), which does NOT try the reflected dunder — it raises
"unsupported operand type(s)". CPython, after `a.__op__(b)` returns
NotImplemented (always so for a builtin LHS + user RHS), calls
`b.__r<op>__(a)`.

## Repro
```bash
printf 'class V:\n    def __init__(s,x): s.x=x\n    def __rmul__(s,o): return s.x*o\ndef main():\n    print(3*V(5))\nmain()\n' > /tmp/r.py
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/r.py -o /tmp/r_bin
/tmp/r_bin   # TypeError ; python3 -> 15
```

## Proposals
- No.1 FRONTEND static reflected dispatch (rhs.__r<op>__(lhs))  [ATTEMPTED, REVERTED]

## No.1 FRONTEND static reflected dispatch [REVERTED]
### Code Change (reverted from binary_op_lowering.py)
`_maybe_emit_reflected_binop(op, lhs, lhs_ty, rhs, rhs_ty)`: gated to a
non-ClassType LHS + ClassType RHS whose class has the reflected dunder
(_REFLECTED_DUNDER map: +->__radd__, *->__rmul__, ...); dispatch
`rhs.__r<op>__(lhs)` via `_emit_direct_method_value_call` (the static dunder
resolution the `<` operator / key= paths use). Injected before the
py_obj_add/etc. routing.
### Why reverted — type-flow / repr complications (not clean)
- For `1 + V(5)` (with __init__) the dispatch DID fire and `__radd__` ran, but
  the result printed as `<object tag=104>` instead of `V6`: the binop result is
  typed DynType, and the V instance returned by the dunder did not route through
  __repr__ on the print path (a DynType-value-holding-an-instance repr gap, or a
  result-type-propagation gap — the dunder's actual return type isn't carried).
- For a class with NO __init__ (`class V: def __rmul__...`), `V()` was not typed
  as ClassType, so the gate skipped it -> still TypeError.
- Net: partial dispatch + wrong/crashy results. Per AGENTS.md §9 (don't stack
  unverified edits in shared codegen; reduce when the first attempt doesn't
  clearly improve the repro), reverted. Frontend-only, so no bootstrap was at
  risk; sanity probe (forward __add__ + builtins) IDENTICAL after revert.

## Next step
A correct fix must propagate the reflected dunder's RETURN type so downstream
print/repr/arithmetic handles the result, and must type `V()` (no-__init__
class) as ClassType. Options: (a) make the binop return value carry the dunder's
declared return type (so `_emit_binop_value`'s result is typed like the dunder
ret, not DynType); (b) wrap the result so the print/repr path dispatches
__repr__ for a DynType-held instance (verify whether print(DynType-instance)
dispatches __repr__ generally — if not, that's a separate prerequisite gap).
This is part of the harder **user-dunder-dispatch-in-dynamic-context** class
(with custom __eq__ in dict/set keys, see
custom-obj-eq-dict-set-key-no-libpython.md) — these are not clean /loop slices;
they need focused type-flow + runtime work. The clean-idiom no-libpython phase
is largely complete (real29-real33 batches were nearly all IDENTICAL).
