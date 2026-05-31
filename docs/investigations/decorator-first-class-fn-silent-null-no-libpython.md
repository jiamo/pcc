# Investigation: function decorators silently return null under --python-libpython=off (first-class-function limitation)

## Status
active

## Problem Description
Under strict no-libpython (`--backend self --python-libpython=off`, DEFAULT
ports), a function decorated with a wrapper-returning decorator COMPILES but the
decorated function returns `<null>` at runtime instead of working OR hard-erroring.

```python
def trace(fn):
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper
@trace
def add(a, b):
    return a + b
def main():
    print(add(3, 4))   # pcc: <null> (then TypeError on the null);  CPython: 7
main()
```

This is the known first-class-function limitation (see memory
`feedback_no_libpython_first_class_functions`: "no-libpython pcc has NO general
first-class fn boxing; `f=add` falls back"). A decorator is exactly this: `add =
trace(add)` passes `add` as a function VALUE to `trace`, and `trace` returns a
`wrapper` closure that is then bound to the name `add` and called. The closure-
as-value + the splat call through it do not work natively.

Two distinct concerns:
1. **Functionality** (big/deferred): general first-class function values
   (passing functions as args, returning closures, binding them to names,
   calling them) — a major subsystem, not a bounded fix.
2. **Honesty** (obligation 1): under `=off` the decorator COMPILES and SILENTLY
   returns null rather than either working or raising the
   `PCC-PY-COMPILE-001 ... requires libpython fallback` hard error. A silent
   wrong result is worse than a clean rejection. A bounded sub-fix would be to
   DETECT the unsupported decorator / first-class-fn pattern and emit the
   no-libpython hard error (fail loudly) until the functionality lands.

## Repro
```
env -u LC_ALL uv run pcc --backend self --python-libpython=off --ir-scaffold=on /tmp/deco.py -o /tmp/deco_bin
/tmp/deco_bin            # prints <null> then TypeError
python3 /tmp/deco.py     # prints 7
```

## Test [CONFIRMED]
2026-05-30: `@trace`-decorated `add(3,4)` -> `<null>` under =off (compiles, no
fallback error). Isolated: the SAME program without the decorator is
diff-IDENTICAL (real9b.py — context managers / with / fib recursion+memo /
keyword-only args / nested-with all work). So the decorator is the sole cause.
Splat-forwarding `inner(*args, **kwargs)` and recursion+memo each work in
isolation; only the decorator (function-value + returned closure) fails.

## Proposals
- No.1 (honesty, bounded-ish): detect the unsupported first-class-fn / decorator
  pattern in the frontend and emit the `=off` hard error instead of compiling to
  null. Makes the limitation loud, not silent. [pending]
- No.2 (functionality, big/deferred): general first-class function values —
  boxing a function (incl. closures) as a callable PyObject, passing/returning/
  binding/calling them natively. Major subsystem. [pending — deferred]

## Notes
Found by real9.py via the realistic-program CPython-diff methodology. Decorators
are very common, so the functionality (No.2) is high-value — but it is the big
deferred first-class-function subsystem, not a bounded idiom slice. Related:
docs/investigations/python-pcc1-list-of-functions-syntactic-fallback.md; memory
`feedback_no_libpython_first_class_functions`. This is a natural boundary of the
bounded-idiom-slice phase: the remaining high-value no-libpython gaps
(first-class functions/decorators, typed-int overflow, regex) are all big
deferred subsystems, not bounded slices.

## Update (2026-05-30): root cause pinpointed — the @-decorator path boxes a closure + py_obj_call
Discriminating probe: the EXPLICIT form `add2 = trace(add); add2(3,4)` correctly HARD-ERRORS under =off
(PCC-PY-COMPILE-001 requires libpython), but the `@trace` SYNTAX silently returns null. So the @-decorator
lowering is a SEPARATE path from the explicit assignment. It lives in user_function_lowering.py (~lines
1678-1718): it builds a function value via `_emit_native_func_value`, applies each decorator via
`_emit_direct_user_function_call(dec, [temp_holding_fn_obj])` (i.e. `fn_obj = decorator(fn_obj)`), then calls
the result via `py_obj_call(fn_obj, args, kwargs)`. For a decorator that returns a CLOSURE capturing the fn
param (the `@trace`/`def wrapper(*a): return fn(*a)` shape), the closure can't be boxed/called natively, so
`py_obj_call` returns null -> silent wrong result. This is the first-class-function / closure-boxing
limitation (NOT a clean isolated 'unknown decorator' branch). There is no bounded honesty sub-fix without
the detection that is itself part of the big first-class-fn subsystem. CONCLUSION: this is the big deferred
first-class-function subsystem; the @-decorator path should EITHER box closures properly (functionality) OR,
as a future honesty pass, route an unboxable-closure decorator to the same =off hard-error the explicit form
already emits.
