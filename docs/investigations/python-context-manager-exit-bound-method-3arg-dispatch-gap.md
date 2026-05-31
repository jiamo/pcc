# Investigation: `__exit__` bound-method dispatch in `call_exit_method` / `pcc_instance_bound_method_entry` returns NULL

## Status
resolved

## Problem Description

`tests/python/data_model/test_context_manager_runtime.py::test_context_enter_exit_runtime`
and
`tests/python/data_model/test_d2_d6_compiled_acceptance.py::test_d4_context_manager_enter_exit_and_suppression_compiled`
both failed because `py_context_exit(manager, py_None, py_None, py_None)`
returned `0` even though the user-defined `__exit__(self, exc_type, exc, tb)`
returned a truthy value.

The probe in the first test:

```c
PyClassObject *cls = py_class_new("CM", NULL, 0, NULL, 0);
py_class_add_method(cls, "__enter__", (PyObject *)(uintptr_t)enter);
py_class_add_method(cls, "__exit__",  (PyObject *)(uintptr_t)exit_);
PyObject *obj = py_instance_new(cls);
py_context_enter(obj);                       // returns "entered" — OK
int64_t rc = py_context_exit(obj, py_None, py_None, py_None);
// expected 1 (truthy bit), observed 0
```

Root cause: `py_obj_getattr(obj, "__exit__")` calls
`py_instance_getattr_default` → `py_class_lookup` → `py_instance_bind_method`,
which boxes the raw function pointer into a bound `PyFunc` whose
`captures = (raw_fn, self)` and entry is `pcc_instance_bound_method_entry`.

`call_exit_method` then takes the PY_TYPE_FUNC branch and builds
`args = (self, exc_type, exc, tb)` — but the bound-method entry's
convention is "args excludes self" (the captures already hold self). With
`n_args == 4`, `pcc_instance_bound_method_entry` had no matching branch
(handlers stopped at n_args==2), so `out` stayed `NULL`. The PyFunc bound
path silently returned NULL → `py_context_exit` returned 0.

The `__enter__` path "worked" accidentally: it passed `args = (self,)`,
hit the `n_args == 1` BinaryMethod branch, and called
`meth(captures_self, args[0]) = meth(self, self)`. The redundant second
self was tolerated because `enter` only reads its first parameter.

## Repro

```bash
env -u LC_ALL uv run pytest \
  tests/python/data_model/test_context_manager_runtime.py::test_context_enter_exit_runtime \
  -q -n0
```

Pre-fix: `CalledProcessError: returned non-zero exit status 2` (the test
probe exits 2 when `py_context_exit != 1`).

## Test [CONFIRMED]

Same pytest case; pre-fix fails, post-fix passes.

## Proposals

- No.1 Add `n_args == 3` branch to `pcc_instance_bound_method_entry` and
  fix `call_exit_method` to pass `(exc_type, exc, tb)` (args excluding
  self)                                            [CONFIRMED]

## No.1 3-arg bound-method entry + drop redundant self from call_exit_method
### Code Change

`pcc/py_runtime/src/py_class_attrs.c::pcc_instance_bound_method_entry` —
add the n_args==3 branch for a 4-parameter raw method:

```c
} else if (n_args == 3) {
    PyObject *arg0 = py_tuple_get(args, 0);
    PyObject *arg1 = py_tuple_get(args, 1);
    PyObject *arg2 = py_tuple_get(args, 2);
    if (arg0 != NULL && arg1 != NULL && arg2 != NULL) {
        typedef PyObject *(*QuaternaryMethod)(
            PyObject *, PyObject *, PyObject *, PyObject *);
        QuaternaryMethod meth = (QuaternaryMethod)(uintptr_t)func;
        out = meth(self, arg0, arg1, arg2);
    }
    if (arg0 != NULL) py_decref(arg0);
    if (arg1 != NULL) py_decref(arg1);
    if (arg2 != NULL) py_decref(arg2);
}
```

`pcc/py_runtime/src/py_context.c::call_exit_method` — for PyFunc methods,
pass args without self (the captures already hold self):

```c
if (py_type_of(method) == PY_TYPE_FUNC) {
    PyObject *args = py_tuple_new(3);
    py_tuple_set_item(args, 0, exc_type);
    py_tuple_set_item(args, 1, exc);
    py_tuple_set_item(args, 2, tb);
    PyObject *out = py_func_call(method, args);
    py_decref(args);
    return out;
}
```

### CONFIRMED
- Repro probe with the patched archive now prints `context-ok` and exits 0.
- `tests/python/data_model/test_context_manager_runtime.py` passes.
- `tests/python/data_model/test_d2_d6_compiled_acceptance.py::test_d4_context_manager_enter_exit_and_suppression_compiled`
  flips from failing to passing.
- Fallback/bootstrap baselines unchanged
  (`test_fallback_baseline.py`, `test_ir_py_fallback_baseline.py`,
  `test_bootstrap_gate_baseline.py`: 17 passed, 4 skipped).
- Corpus 177 passed.

### Why this is the correct convention
Standard pcc user-code call sites already match this — `py_obj_call(meth, args, kwargs)` with a bound method calls `py_func_call(meth, args)` without injecting self. The C runtime context-manager glue was the only path that prepended self into args when calling a PyFunc, and that mismatch only surfaced on n_args == 4 (the `__exit__` shape) because the lower arities are tolerated by C ABI when the raw method ignores extra parameters.

## Report
Two related runtime helper changes (one in `py_context.c`, one in
`py_class_attrs.c`) close the dispatch gap. `call_unary_method` still
passes self in args; it is silently tolerated because `__enter__` only
reads its first parameter. A follow-up to align it with the bound-method
convention is a candidate but not required for this fix.
