# PY-P0-CONSUMER-BOUNDARY-OWNERSHIP-LEDGER: return path now consults the value ledger

## Measured leak (CPython-oracle finalizer differential, 2026-09-03)

Instrumenting the getattr-result consumer shapes on current source:

```text
shape                         CPython            pcc (before)
return t.n                    fin fires          fin NEVER fires   <-- LEAK
t.n   (bare statement)        fin fires          fin fires         (correct)
len(t.n.tag) + 0 (binop)      fin fires          fin fires         (correct)
```

`return obj.attr` leaked one reference to the attribute object per call — the
most common getter shape in real code. The bare-statement and binop consumers
were already correct.

## Root cause (precise)

The attribute emitter already applies the value ledger: the generic
`py_obj_getattr` site (attr_load_lowering.py ~1783) calls
`_note_owned_dynamic_call_value(result)` because the AST-shape classifier
(`_attr_expr_returns_owned_object`) answers "not owned" for an `Attr` on a
DynType receiver. Every borrowing consumer honours that ledger through
`_gc_release_if_owned` (ledger first, AST classifier second) — which is why the
statement and binop shapes were correct.

The return path was the one consumer that did not. `_return_value_needs_retain`
(return_lowering.py) re-derived ownership from the AST alone
(`_expr_returns_owned_object(expr)` -> `_attr_expr_returns_owned_object` ->
False for a DynType receiver), decided the value was borrowed, emitted
`pcc_gc_retain` and returned that second reference, while the original owned
getattr result was never released. The 2026-08-25 ledger evidence's
"scalar attribute read via marshal_from_object releases nothing" instance turned
out to be already fixed on current source (the scalar-unbox branch releases the
boxed getattr result); the live leak was this return-coercion consumer.

## Fix (return_lowering.py, `_return_value_needs_retain`)

After the pointer/CPython-bridge checks and before any AST-shape reasoning:

```python
if self._value_is_owned_object(value):
    return False   # ledger says one owner already: transfer it, do not retain
```

Suppressing the spurious retain routes ledger-owned values onto the existing,
already-correct owned-return path (the one `return T()` takes): the value goes
through the return cleanup root (store/load, no release) and is transferred to
the caller as one owned reference, matching the "function calls return owned"
contract. No release is added anywhere, so this change cannot introduce a
double free; it removes a leaked retain. A `return name` still follows the Name
rules (a loaded local is a fresh SSA value, never the noted production value).
The same path also fixes `return obj.method()` on a DynType receiver, whose
`py_obj_call` result is ledger-noted by the same mechanism.

## Gates

- Test-first regression `tests/python/test_return_transfers_ledger_owned_attr_result.py`
  (CPython-oracle finalizer order, with the statement/binop shapes as controls):
  RED before (pcc output lacked `fin ret`), GREEN after.
- Focused ownership/getattr/return gates with the fix: print-consumes-owned,
  temporary-call-argument-released, str-builtin-consumes-owned,
  hasattr/getattr probe, self-host getattr default -> 12 passed, 1 xfailed
  (the pre-existing, separately tracked 2-arg getattr AttributeError-escape
  strict xfail).
- Broader codegen-change gates: commit-level bootstrap-gate + fallback ratchets
  and the python-frontend multi-file/bootstrap-shim gate passed (no F/E);
  GC finalizer/resurrection/trashcan regression (test_gc_last_decref_resurrection_metadata.py +
  test_gc_trashcan.py): 17 passed in 256.30s. Full three-group run exit 0.

## Remaining for the row

The row's `test_py_corpus.py` (1500 s) close gate has not been run (long run,
needs explicit authorization). The scalar-attr marshal-unbox instance from the
2026-08-25 evidence is confirmed already fixed on current source (evidence 002
Update 2); with `str()` (002) and the return path (003) landed, the remaining
unmeasured consumer families are the object-arithmetic/binop operands, which
the finalizer differential shows correct for the probed shape.
