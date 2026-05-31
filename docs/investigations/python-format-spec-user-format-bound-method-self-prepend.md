# Investigation: `__format__(self, spec)` receives self as `spec` when method is a bound PyFunc

## Status
resolved

## Problem Description

`tests/python/data_model/test_d2_d6_compiled_acceptance.py::test_d6_format_spec_and_user_format_compiled`
failed with the user body:

```python
class F:
    def __format__(self, spec):
        return "fmt:" + spec

print(format(F(), "abc"))
```

raising `TypeError: unsupported operand type(s) for +`.

Root cause: same convention bug as
[python-context-manager-exit-bound-method-3arg-dispatch-gap.md](python-context-manager-exit-bound-method-3arg-dispatch-gap.md).

`py_obj_format(o, spec)` calls `py_obj_getattr(o, "__format__")` which
returns a bound `PyFunc` (captures = (raw_fn, self), entry =
`pcc_instance_bound_method_entry`). `call_format_method` then built
`args = (self, spec)` and called `py_func_call`. With n_args == 2 the
bound-method entry dispatched as
`meth(captures_self, args[0], args[1]) = meth(self, self, spec)`.

The raw `__format__(self, spec)` is declared with 2 parameters. The C
ABI passes 3 actual args and the callee reads only its first 2:
`self = captures_self`, `spec = self_redundant`. The user body's
`"fmt:" + spec` therefore became `"fmt:" + <F-instance>`, triggering
`TypeError`.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_d2_d6_compiled_acceptance.py::test_d6_format_spec_and_user_format_compiled \
  -q -n0
```

Pre-fix: `AssertionError` with stderr `TypeError: unsupported operand
type(s) for +`.

## Test [CONFIRMED]

Same pytest case; pre-fix fails, post-fix passes.

## Proposals

- No.1 Drop the redundant self from args for the PyFunc bound path  [CONFIRMED]

## No.1 Drop redundant self in `call_format_method`'s PyFunc path
### Code Change

`pcc/py_runtime/src/py_format.c::call_format_method`:

```c
if (py_type_of(method) == PY_TYPE_FUNC) {
    PyObject *args = py_tuple_new(1);
    py_tuple_set_item(args, 0, spec);
    PyObject *out = py_func_call(method, args);
    py_decref(args);
    return out;
}
```

### CONFIRMED
- Targeted test now passes.
- All `tests/python/data_model/` is now 80 / 81 (was 77 / 81 at session
  start; the remaining failure `test_t1_metaclass_type_enum_abcmeta_compiled`
  is unrelated metaclass-init lowering work).
- Baselines + corpus: 194 passed, 4 skipped (unchanged).

### Why this is the correct convention
The bound-method dispatch convention is "args excludes self" — captures
already hold `self`, and `pcc_instance_bound_method_entry` injects
`self` from captures when calling the raw fn. The same convention is
already followed by `py_obj_call` for normal user-code call sites.
`call_format_method` is one of a small set of C runtime glue helpers
that historically prepended `self` into args; for unary callees
(`__enter__`, `__len__`, `__bool__`) this was silently tolerated, but
for 2-parameter `__format__` the redundant self shifts the real spec
into a dropped C-ABI slot.

## Report
Landed. Mirrors the fix in
`python-context-manager-exit-bound-method-3arg-dispatch-gap.md` for
`py_context.c::call_exit_method`. Other glue helpers
(`call_unary_method`, `py_class.c::class_call_*_method`,
`py_protocol.c::call_unary/binary/ternary`) carry the same shape but
are usually fed a raw class-lookup function pointer (no PyObject
header), so their PyFunc branch is dead code in practice; aligning them
with the same convention is a follow-up but not required for the
current closure.
