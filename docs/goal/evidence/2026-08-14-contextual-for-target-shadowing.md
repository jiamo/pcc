# Contextual compiler cleanup-target shadowing

Mode: host pcc frontend, contextual per-module closure compilation.  This is
not a runtime CPython-to-pcc representation-union claim.

## Root cause and fix

Four compiler helper modules reused a source local name first bound to a
CPython-domain IR-builder result as a later pcc-native cleanup-loop target.
The frontend correctly rejected that zero/nonzero-iteration representation
join.  The cleanup collections are independent and the earlier values are dead,
so the fix gives each cleanup loop a distinct source binding in:

- `lambda_callback_lowering.py`
- `lambda_helpers_lowering.py`
- `native_virtual_thread.py`
- `numeric_builtin_lowering.py`

A proposed universal `py_cpy_to_pcc_obj` join was rejected and removed because
unknown CPython objects can degrade to numeric or repr-string projections.  A
genuinely live cross-domain target therefore remains fail-closed.

## Evidence

- RED before the renames: the four targeted contextual OFF counts were all
  `-1` (codegen failure).
- `gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_fallback_baseline.py::test_contextual_for_target_domain_join_cleanup_names_compile`
  -> `1 passed in 28.63s`; OFF counts all compile and strict ON counts are all
  zero.
- `gtimeout 30s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_py_for_target_representation_join.py::test_live_cpython_native_target_join_remains_fail_closed`
  -> `1 passed in 0.10s`.
- Adjacent lightweight for-target coverage completed as `10 passed` plus
  `3 passed`; the one heavy executable node was already the recorded evidence
  for the prerequisite `PY-P0-FOR-TARGET-REPRESENTATION-JOIN` and is not
  re-claimed here.
- `py_compile`, `git diff --check`, and task-board validation passed.

Global closure ratchets and final bootstrap evidence remain owned by
`S-P1-SELF-LINK-LINK-ARG-HONESTY`; this narrow card neither raises a baseline
nor changes fallback policy.
