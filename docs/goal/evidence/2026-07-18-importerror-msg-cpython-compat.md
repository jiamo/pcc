# ImportError/ModuleNotFoundError `.msg` — CPython-compat runtime attribute

## Claim

pcc builtin `ImportError` and `ModuleNotFoundError` now expose the `.msg`
attribute (== args[0]) that CPython exposes, scoped so other builtin exceptions
(RuntimeError, ValueError, ...) still have no `.msg` — byte-identical to CPython.
This is a generic runtime CPython-compat fix, not a numpy special case. numpy's
`_core/__init__.py` re-init opt-out branches on `exc.msg == "..."`; without `.msg`
that branch could not evaluate faithfully.

## Changes

- `pcc/py_runtime/src/py_obj_ops_dispatch.c`: `py_obj_getattr` `PY_TYPE_EXC`
  branch gains a `msg` case, gated by
  `py_isinstance(o, py_exc_builtin_class(PY_EXC_IMPORTERROR))` so only
  ImportError and its ModuleNotFoundError subclass return the message; all other
  builtin exceptions fall through to `py_obj_missing_attr`.
- `pcc/py_runtime/py/py_obj_ops_dispatch.py` (port mirror): same branch, plus a
  `_cstr_is_msg` byte-compare helper and a `py_exc_builtin_class` extern.

## Verification [CONFIRMED]

Repro (`getattr(e, "msg", "NOMSG")` across exception types), CPython vs pcc:

| exception | CPython | pcc (default/port) | pcc (`PCC_RUNTIME_CC=cc`) |
|---|---|---|---|
| `ImportError("boom")` | `boom` | `boom` | `boom` |
| `ModuleNotFoundError("gone")` | `gone` | `gone` | `gone` |
| `RuntimeError("x")` | `NOMSG` | `NOMSG` | `NOMSG` |
| `ValueError("y")` | `NOMSG` | `NOMSG` | `NOMSG` |

Both runtime tiers compiled with `pcc --backend self` and run; results identical
to CPython in all four rows and both tiers.

## Boundary

- This fix is CPython-faithful and complete on its own, but it does NOT turn the
  numpy head-truth loader-probe gate green — numpy intentionally re-raises on the
  message this enables it to read. See
  [docs/investigations/numpy-loader-probe-cext-reimport-load-once.md](../../investigations/numpy-loader-probe-cext-reimport-load-once.md).
- Touches the shared `py_obj_getattr` runtime path, so a self-host
  stage1->2->3 bootstrap gate is required before promoting any dependent task to
  DONE_STRONG. Not yet run in this slice.
- `.name` / `.path` (CPython also exposes them on ImportError, default None) are
  not added yet — no consumer needs them; deferred until one does.
