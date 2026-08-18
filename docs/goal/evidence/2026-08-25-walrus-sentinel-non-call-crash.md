# Walrus-sentinel predicate crash — 2026-08-25

## Claim

`_is_walrus_sentinel` is now total over expressions, so an annotated
assignment with a boxed-int target and a non-`Call` right-hand side no longer
crashes the Python frontend.

This closes `PY-P1-WALRUS-SENTINEL-NON-CALL-CRASH`.  It is not Stage1, Stage2,
fixed-point, five-GC, or performance evidence.

## What was wrong

`_is_walrus_sentinel(self, expr: Call)` read `expr.func` directly.  The
annotation documented a precondition that only half its callers honoured:

```text
guarded with isinstance(..., Call) first
  stmt_misc_lowering.py:332, 380, 401, 417
unguarded
  assignment_statement_lowering.py:726
  class_gen.py:2769, 2844, 3401
```

So `x: int = a[0]` inside a function reached the predicate with a `Subscript`:

```text
error: PCC-PY-COMPILE-001: [python-frontend]
       'Subscript' object has no attribute 'func'
  note: exception_type=AttributeError
```

The CLI surfaced only the bare `AttributeError`.  The traceback came from
calling `compile_python` directly:

```text
assignment_statement_lowering.py:726 in _emit_assign
    elif boxed_int_target and self._is_walrus_sentinel(stmt.value):
stmt_misc_lowering.py:368 in _is_walrus_sentinel
    isinstance(expr.func, Name)
```

## The fix, and why it went in the predicate

Four call sites needed the same guard.  Making the predicate answer for any
node is one change instead of four and cannot regress when a fifth caller is
added; the `isinstance(expr, Call)` test simply moved inside and the annotation
widened.  The four already-guarded call sites are now redundant but harmless
and were left alone — narrowing them is a separate readability change, not part
of this fix.

## Regression

`tests/python/test_walrus_sentinel_non_call_value.py` compiles and runs
annotated int assignment from a subscript, an attribute, a name and a call,
under `--backend self --python-libpython=off`.

RED before: the build failed with the `'Subscript' object has no attribute
'func'` diagnostic.
GREEN after: `1 passed in 28.30s`, output `11 / 7 / 13 / 17`.

## Neighbors

```text
gtimeout 590s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_py_chained_assignment.py \
  tests/python/test_py_list_unpack_assignment.py \
  tests/python/test_py_module_augassign.py \
  tests/python/test_py_slice_augassign.py \
  tests/python/test_native_typed_int_overflow.py
```

Result: `10 passed in 29.53s`.

## Follow-on datum for the print-consumer boundary

With the crash gone, the previously unreachable shape could be emitted, and it
sharpens the open print-consumer question recorded in
`2026-08-25-print-consumer-ownership-investigation.md`:

```python
def f(a: list) -> None:
    x: int = a[0]     # bound to a local
    print(x)
```

Here the subscript result is stored in the rooted local `x`, and the IR does
contain `pcc_gc_release(%x.release.current...)` on both the normal and cleanup
paths — `py_print` borrows from a properly owned local.  **This shape does not
leak.**

So the leak is specific to a NEW reference passed *directly* to a borrowing
consumer with no owning local in between — `print(a[0])` and `print(o.n)` —
which is exactly the pair captured in that investigation.  A successor fixing
the print ledger can use this contrast as its control: the bound-to-a-local
form must keep exactly one release, and the direct form must gain one.

## Nonclaims

- No print-consumer leak was fixed; both remain open.
- No bootstrap, stage, fixed-point or five-GC gate was run.
