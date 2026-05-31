# Investigation: Class(args) for a class without an explicit `__init__` synthesises a phantom user_<module>_<class>___init__ symbol → link error

## Status
resolved

## Problem Description

When a user class has no `__init__` body method (e.g. `class MAError(Exception):
pass`) and is instantiated with arguments — directly as `Cls(args)` or via
`raise Cls(args)` — pcc's class lowering emits a direct call to a function
symbol named `@user_<current_module>_<class>___init__`. The symbol is never
defined anywhere (no `__init__` exists to lower), producing a hard link error:

```
ld: Undefined symbols:
  _user_numpy_ma_mrecords_MAError___init__,
    referenced from: _user_numpy_ma_mrecords_MaskedRecords___new__
      in self_backend_expanded_63.ll.o
  _user_numpy_testing__private_utils__Dummy___init__,
    referenced from: __pcc_py_module_top_numpy_testing__private_utils
      in self_backend_expanded_90.ll.o
```

This is the **next blocker that surfaced after** closing the sibling-generator
owned-flag cap
([python-generator-owned-flag-cache-leaks-across-sibling-generators.md](python-generator-owned-flag-cache-leaks-across-sibling-generators.md)).
With the owned-flag fix, all 149 numpy IR modules emit and the compile reaches
the link stage, where these phantom init symbols are exposed.

Note: the symbol is mangled with the CALLER's module (`mrecords`) via
`_method_symbol` (`class_gen.py:2715` uses `self.parent.ast_module.name`,
the *current* module being lowered), even though `MAError` is defined in
`numpy.ma.core`. That secondary mangling-quirk is part of the same root issue:
the call has no real init to bind to.

## Repro

```python
class MyError(Exception):
    pass

class Marker:
    pass

def main() -> None:
    m = Marker()                    # no-Exception class, no __init__, no args
    try:
        raise MyError("bang")       # Exception subclass, no __init__, with args
    except MyError:
        print("caught")
    print("done")

if __name__ == "__main__":
    main()
```

Before the fix, this compiled but produced an undefined-symbol link error for
the `MyError` call site. After the fix, the program runs and prints
`caught / done`.

The numpy ground truth (auto-mode diagnostic on a real numpy install) showed
exactly this error after the owned-flag cap was closed; with this fix in
place, both `MAError___init__` and `_Dummy___init__` undefined-symbol errors
are gone. (`dict_append` undefined remains, separate bug — a top-level
`def dict_append(d, **kws):` in `numpy.distutils.misc_util` apparently does
not emit a native symbol; `**kwargs` lowering / function emission, not
covered by this fix.)

## Test [CONFIRMED]

`tests/python/test_py_exceptions.py
::PyExceptionTests::test_class_without_explicit_init_does_not_emit_phantom_call`
— mixes a no-Exception no-init class instantiated without args and an
Exception subclass with no `__init__` raised with a message. Confirmed
failing before the fix (link-stage undefined symbol), passing after.

## Proposals

- No.1 Skip the init call at emission sites when no init was found  [CONFIRMED]
- No.2 Route through a runtime helper for exception subclasses  [DENIED — bigger, not minimal for link fix]

## No.1 Skip the init call at emission sites when no init was found

### Code Change

Two sites synthesise the phantom call. Both follow the same pattern:
`init_fn` is None after the MRO walk + symbol lookup, but `should_call_init`
is True (because args were passed), so a phantom `init_fn` is built from the
symbol name and called.

`pcc/py_frontend/codegen/class_gen.py:5566` (general user-class constructor /
`raise UserCls(args)` path):
```python
if init_fn is None:
    # Skip: see investigation. Args are owned by their slots; do not
    # synthesise @user_<module>_<class>___init__ that nobody emitted.
    pass
else:
    _classgen_emit_discarded_call(builder, init_fn, ...)
```

`pcc/py_frontend/codegen/native_modules.py:2063` (`_emit_native_class_instantiate`
path used by some `Cls(args)` lowering for native-tracked classes): same skip.

### CONFIRMED

Root cause: in `class_gen.py:5263-5306`, the constructor lowering tries in
order to bind `init_fn`: `info.init_fn` → `info.methods.get("__init__")` →
module global lookup of `_method_symbol(info.name, "__init__")` → MRO walk of
declared bases (only pcc-known classes). Bases that are builtins (`Exception`,
`BaseException`, ...) are not in `self.classes`, so the MRO walk doesn't
reach them. If the body has no `__init__`, all four lookups return None.

Then the `should_call_init` heuristic (line 5302-5306) forces a call when
args were passed:

```python
should_call_init = init_fn is not None
if not should_call_init and init_ast_fd is not None:
    should_call_init = True
if not should_call_init and len(arg_exprs) > 0:
    should_call_init = True  # <-- forces a call with no real target
```

The third clause sets `should_call_init = True` even with no `init_fn` in
hand. The subsequent `if init_fn is None:` block synthesised a phantom
`ir.Value` whose name was `"@" + _method_symbol(info.name, "__init__")` —
which mangles under the CURRENT module, never gets emitted, and never has
a definition.

The fix skips the call when no `init_fn` was bound. The args were emitted
from already-owned slots whose lifecycle is managed by their slot ownership
(the slot's owned-flag drops on scope exit), so this is safe. The lost
behavior is `Exception.args` storage for the no-body-`__init__` case (a
follow-up best handled via `py_exc_new_with_class(cls, msg)` runtime, which
already exists — see `py_runtime.h:961`).

Evidence:
- Minimal repros e1 (`class MyError(Exception): pass; raise MyError("bang")`),
  e2 (class with explicit `__init__`, regression), e3 (`class Marker: pass;
  Marker()`) all ✓ under `--backend self --python-libpython=off`.
- Focused class/exception suites
  (`test_py_codegen_class_model.py test_py_class_constructor_attr_args.py
  test_py_class_kwargs_no_init.py test_exception_chaining_wiring.py
  test_dataclasses_full.py -q -n0`) → 20 passed, no regression.
- New regression
  `test_py_exceptions.py::test_class_without_explicit_init_does_not_emit_phantom_call`
  → 1 passed.
- Mandatory self-host gate
  `tests/python/test_pcc_bootstrap_full.py::test_full_three_stage_bootstrap_self`
  → 1 passed in 50.36s.
- Numpy auto-mode compile (real numpy site): both
  `MAError___init__` and `_Dummy___init__` undefined-symbol errors are gone.
  Remaining link error is `dict_append` (separate bug — top-level `def
  dict_append(d, **kws):` not emitted; `**kwargs` lowering).

## No.2 Route through a runtime helper for exception subclasses

### DENIED — bigger, not minimal for link fix

Would require detecting that the class's MRO ends in a builtin exception base,
then emitting a call to `py_exc_new_with_class(cls, msg_cstr)` or
`py_exc_set_args(inst, args_tuple)` (the latter doesn't exist yet and would
need adding). This preserves `Exception.args` semantics for the
no-body-`__init__` case. But the immediate goal is to close the link error;
the dropped-args behavior is acceptable as a follow-up and does not block
further numpy progress, since most call sites that care about `args` would
declare an explicit `__init__`. No.1 is the minimum correct fix.

## Report

Landed No.1, a 2-site skip mirroring the same condition at both emission
points. Closes the post-`.owned.N` link-stage cap that exposed phantom-init
calls for `numpy.ma.mrecords.MAError` and
`numpy.testing._private.utils._Dummy`.

**Newly-exposed downstream blocker** (NOT this change; next iteration's
target): `_user_numpy_distutils_misc_util_dict_append` undefined symbol.
The function is `def dict_append(d, **kws):` at
`numpy/distutils/misc_util.py:2287` — a top-level function with `**kwargs`.
Plain calls to it from `Configuration.add_extension` in the same module
expect a native symbol that pcc apparently never emits (likely because
`**kwargs` lowering bails out and the function is skipped). This is the
NEXT link-stage gap on the numpy generator path. Progress order:
... → (closed) sibling-generator owned-flag leak → (closed, this)
phantom-init link error → (NEW) `**kwargs` function emission /
dict_append.
