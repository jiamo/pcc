# The consumer-boundary ownership ledger — 2026-08-25

## Why this file exists

Three separate investigations this session found the same defect wearing
different clothes.  Each was filed on its own row and each was going to be
fixed site by site.  They are one pattern, and fixing them as one ledger is
both cheaper and safer than three scattered changes.

**The pattern.** An expression produces an owned reference.  It is handed to a
consumer that *borrows* it — a runtime call that reads and returns, or an
unpacker that produces a native value.  Nobody releases it.  One object leaks
per evaluation.

## The three instances

| consumer | shape | status |
|---|---|---|
| `print` | `py_print(obj)` borrows | fixed for the direct-subscript case only |
| scalar attribute read | `marshal_from_object` unpacks, releases nothing | open |
| single-argument builtins | `py_obj_repr(obj)` etc. borrow | open, 5 sites |

Measured for the builtins:

```text
                CPython                pcc
repr(T())       freed builtin / after  after        (never freed)
str(T())        freed str / after      after        (never freed)
```

## What the call-argument fix already covered

The `_last_call_arg_owned_temp` change landed earlier does cover **method
calls** — `method_call_lowering.py` reads the same flag:

```text
h.take(T('method'))    CPython: freed method / after method
                       pcc:     freed method / after method    identical
```

That was worth checking rather than assuming: direct calls and method calls
share the flag, builtins do not, because builtins inline
`self._emit_expr_as_pcc_object(expr.args[0])` straight into the runtime call
and never route through the argument-ABI path that owns the flag.

## Why one ledger rather than three fixes

Each instance has already produced one wrong patch:

- the print consumer got a `_gc_release_if_owned` that emitted **nothing**,
  because the classifier reports `Attr`/`BinOp`/`Subscript` as not-owned in
  that position;
- the attribute consumer got a release in a branch that is **unreachable** for
  dynamic receivers, since `expr.ty` is `DynType` there;
- both were reverted.

The common failure is guessing which classifier applies at a given consumer.  A
single ledger fixes that once: decide the ownership question **where the value
is produced**, record it, and have every borrowing consumer consult the same
record.  That is what `_last_call_arg_owned_temp` already does correctly for
call arguments, and it is why the call-argument fix worked on the first attempt
while the other two did not.

## Recommended shape

1. Extend the produced-value ownership record so an owned temporary is marked
   at its emission site, not re-derived at each consumer.
2. Give the borrowing consumers one shared helper that releases against that
   record.
3. Convert the three instances to it, each with its own before/after control —
   for every one of them a wrong classification is a double free, not a leak.

Do **not** re-apply the two reverted patches; both are recorded with the
measurement that refuted them.

## Nonclaims

- Nothing was fixed in this slice.
- Only `repr` and `str` were measured on the builtin side; the count of five
  inline sites is a pattern count, not five measured leaks.
- No bootstrap, stage, fixed-point or five-GC gate was run.
