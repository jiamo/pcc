# `py_raise(py_exc_new(...))` leaks, runtime-wide — 2026-08-25

## Status

**Measured and scoped, not fixed.**  This is a bigger and different finding
than the strict-mirror divergence I originally filed.  No code was changed in
this slice.

## The contract, read to the end this time

`py_raise_normalize` (`py_exc_tls.c`) decides ownership:

```text
exc == NULL                    -> creates one,  owned = 1
py_type_of(exc) == PY_TYPE_EXC -> returns as-is, owned = 0
user exception instance        -> returns as-is, owned = 0
anything else                  -> normalizes,    owned = 1
```

`py_raise` then does `if (exc != NULL && !exc_owned) py_incref(exc);`.

`py_exc_new` always returns a fresh `PY_TYPE_EXC`, so it always takes the
`owned = 0` branch: `py_raise` **increfs**, the caller keeps its own reference,
and the caller must release it.  Therefore

```c
py_raise(py_exc_new(PY_EXC_TYPEERROR, "..."));   /* no variable, no release */
```

orphans one exception object per raise.

## Measured, not inferred

A probe reading the refcount of the object left in the thread-local exception
slot, linked against the ordinary C runtime archive:

```text
dict_getitem (C stores then releases)   pending_rc=1
py_gen_state (inline idiom, no release) pending_rc=2
```

`py_dict_getitem` is the control: it does `py_raise(exc); if (exc)
py_decref(exc);` and leaves exactly the one reference the TLS slot holds.  The
inline idiom leaves two, and clearing the exception drops only the TLS one.

## Scope

The inline idiom is unambiguous to count — there is no variable to name:

```text
C runtime, py_raise(py_exc_new(...)) occurrences: 250
  py_bytes.c 40   py_format.c 37   py_list.c 24   py_re_engine_obj.c 21
  py_obj_ops_dispatch.c 15   py_coroutine.c 12   py_protocol.c 10
  py_class.c 9    py_int_bytes.c 8  py_class_attrs.c 8
  py_gen.c 7      pcc_threads.c 7   ... and more

Correct idiom already in use: py_raise_owned( appears 65 times in C.
```

So the right helper exists and is used in places; the leaking form dominates.

## Correction to my earlier inventory

`2026-08-25-dict-missing-key-exception-owner-parity.md` reported "twenty
unreleased strict raise sites" from a scan that looked for `py_decref(exc)`
within two lines of `py_raise(exc)`.  That scan is **variable-name sensitive
and therefore unreliable**: `py_gen.py:185` does

```python
py_raise(stop)
py_decref(stop)
```

which is correct but was counted as a leak because the variable is `stop`, not
`exc`.  The strict-side inventory must be redone with a name-agnostic method
before any strict site is touched.  The C-side count above does not have this
problem.

## Why this was not swept in this slice

For the exact pattern `py_raise(py_exc_new(...))` the fix is mechanical and
safe — `py_raise_owned(py_exc_new(...))` — because the argument is always a
fresh owned `PY_TYPE_EXC`, so the borrowed-exception hazard that makes the
general case dangerous does not apply.  But it is a 250-site diff across the
whole C runtime plus its strict mirrors, and it changes release behaviour on
every error path, so it needs the five-GC gates rather than a quick sweep.

The narrow strict-only framing of
`PY-P1-STRICT-RAISE-SITE-EXCEPTION-OWNER-PARITY` is superseded by this.

## Nonclaims

- Nothing was fixed; all 250 C sites and the strict equivalents still leak.
- Only two sites were measured (`py_dict_getitem`, `py_gen_state`).  The 250
  count is a pattern count, and while the ownership argument applies uniformly
  to `py_exc_new` results, each was not individually executed.
- Whether any caller deliberately relies on the leak was not investigated.
- No bootstrap, stage, fixed-point or five-GC gate was run.
