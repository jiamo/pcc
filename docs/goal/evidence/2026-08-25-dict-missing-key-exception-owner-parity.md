# Dict missing-key exception owner parity — 2026-08-25

## Claim

The strict pcc-Python dict mirror now releases the exception it creates on the
missing-key paths, matching the C runtime.  `py_dict_getitem`, `py_dict_pop`
and `py_dict_popitem` in `pcc/py_runtime/py/py_dict.py` call `py_raise_owned`
instead of a bare `py_raise`.

This closes one of the six sub-gaps listed on
`PY-P0-EXACT-CONTAINER-SUBSCRIPT-FULL-OWNERSHIP`: "the pcc-Python py_dict
missing-key path raises a fresh exception without balancing its caller owner
unlike the C runtime."  The other five sub-gaps of that row are untouched.  It
is not Stage1, Stage2, fixed-point, five-GC, or performance evidence.

## Contract, read rather than assumed

`py_raise` in `pcc/py_runtime/src/py_exc_tls.c:128` normalizes the exception
and then does `if (exc != NULL && !exc_owned) py_incref(exc);` before
`py_tls_exc_set(exc)`.  It **increfs**; it does not steal.  A caller that
allocated the exception with `py_exc_new_with_value` therefore still owns its
own reference and must release it.  `py_raise_owned` is the runtime's existing
one-call form of raise-then-release, already used throughout
`pcc/py_runtime/py/py_file.py`.

Every C counterpart already did this — `py_raise(exc); if (exc) py_decref(exc);`
in `py_dict_getitem`, `py_dict_pop` and `py_dict_popitem`.  Only the strict
mirror was missing it, so this is a mirror-parity defect, not a design
question.

## Measured differential (RED then GREEN)

The regression is `tests/python/test_dict_missing_key_exception_owner_parity.py`.
It raises through each of the three paths and reads the refcount of the object
left in the thread-local exception slot: after a correct raise, exactly one
reference remains — the TLS slot's.

RED, before the fix:

```text
[c]          passed
[pcc_python] exception refcounts: getitem=2 pop=2 popitem=2 (expected 1 1 1)
```

GREEN, after the fix:

```text
gtimeout 600s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_dict_missing_key_exception_owner_parity.py
2 passed in 145.19s
```

The `[c]` arm passing before *and* after is the control: it shows the probe
measures the intended property rather than an artifact of the probe itself.

## Neighbors

```text
gtimeout 900s env -u LC_ALL uv run pytest -q -x -n0 \
  tests/python/test_python_dict_methods_parity.py \
  tests/python/test_gc_threading_substrate.py -k "dict or set"
```

Result: `38 passed, 170 deselected in 39.51s`.

## Wider class found, inventoried and routed

The same scan across every strict runtime port shows this is not confined to
dict.  `py_capi_exc_runtime.py` and `py_exc_tls.py` release correctly, which
confirms the intended contract; twenty other sites do not:

```text
py_capi_import_runtime.py   2   lines 96, 149
py_class.py                 2   lines 1978, 2002
py_coroutine.py             4   lines 110, 115, 566, 587
py_gen.py                  10   lines 100, 104, 108, 197, 203, 222, 234, 240, 246, 272
py_list.py                  2   lines 2012, 2076
py_func.py                  0
```

Those twenty are filed as `PY-P1-STRICT-RAISE-SITE-EXCEPTION-OWNER-PARITY`
rather than bulk-patched here.  Each needs its own C counterpart checked before
being changed: a site whose exception came from a borrowed source, or that
already releases further away than the two-line window this scan inspected,
must not gain a second release.  A blind sweep would convert a leak into a
double free.

## Nonclaims

- Only the three dict sites were changed and measured.  The twenty routed sites
  are unverified in either direction.
- The remaining five sub-gaps of `PY-P0-EXACT-CONTAINER-SUBSCRIPT-FULL-OWNERSHIP`
  (operand rooting across hash calls, getitem error-path cleanup, late
  owned-local err.exit registration, exact-int print consumption, bool-unbox
  post-call error check) are untouched.
- No bootstrap, stage, fixed-point or five-GC gate was run.  That row's final
  gate is the five-GC bootstrap matrix, which is a long run and has not been
  started.
