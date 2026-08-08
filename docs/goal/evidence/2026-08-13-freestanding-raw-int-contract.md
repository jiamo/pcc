# PY-P1 freestanding raw-integer contract — 2026-08-13

## Claim boundary

This slice makes fixed-width integer intent explicit in strict freestanding
modules without changing ordinary non-freestanding Python `int` semantics.
`pcc.i64` and `pcc.u64` are compile-time annotations; raw arithmetic,
comparison, shifts, division and ABI calls stay unmanaged.  Ordinary Python
`int` operations that could depend on arbitrary precision are rejected before
IR publication.  A remaining ordinary integer literal outside signed i64 is
also rejected before code generation, while explicitly typed in-range `u64`
constants (including the maximum value) remain valid.

All 60 strict pcc-Python runtime modules now import and use the explicit raw
annotation rather than relying on the former blanket freestanding `int` bypass.
The target triple is carried into unsafe lowering, so Linux syscall code is not
selected from the Darwin build host by accident.

## Focused evidence

The minimized out-of-range ordinary-literal regression was first observed RED:
it emitted `py_int_from_cstr` and failed only in the freestanding IR validator.
After the type-inference validation was added, the exact regression passed.
The explicit `u64` maximum default regression was also first RED, exposing that
raw annotations had not contextualized parameter defaults, and then passed
after that boundary was fixed.

Observed current-source commands:

```text
gtimeout 180s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_freestanding_module.py
48 passed in 5.22s

gtimeout 90s env -u LC_ALL uv run pytest -q -x -n0 tests/python/test_py_typed_int_unboxed.py -k '<focused exact-int/static selection>'
16 passed, 29 deselected in 3.29s

current-source emit-only loop over every pcc/py_runtime/py module containing
__pcc_freestanding__ = True
FREESTANDING_EMIT_OK 60

current-source AST contract probe
FREESTANDING_RAW_ANNOTATIONS_OK modules=60 raw_annotations=2313

python -m py_compile over the changed frontend/backend files and focused tests
PASS
```

## Honest remaining boundary

This is `DONE_WEAK`, not `DONE_STRONG`.  A previous quiet full
`test_py_typed_int_unboxed.py` attempt reached 28 progress dots and was killed
by its 180-second watchdog without a pytest summary; it is not green evidence
and was not repeated as a diagnostic loop.  The current pcc-Python runtime
archive is content-stale, so the production closure and bootstrap-baseline
gates would trigger a cold rebuild.  They remain for the final current-source
build/test phase, where the full typed-int file must run once with `-vv -x` and
produce a final summary, followed by the listed closure/bootstrap gates.

