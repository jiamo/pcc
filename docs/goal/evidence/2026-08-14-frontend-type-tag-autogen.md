# Frontend runtime type-tag autogen — 2026-08-14

## Claim

Frontend lowering no longer copies numeric public object tags when dispatching
on `py_obj_type_tag`, `pcc_py_type_of`, or `py_builtin_type_for_tag`.
`scripts/gen_freestanding_stdio_abi.py` now emits compiler-facing `PY_TYPE_*`
aliases over the existing generated `ABI_CONSTANTS` projection, whose values
ultimately come from the C runtime headers through
`scripts/gen_port_abi_constants.py`.

The migrated boundary includes builtin type objects and `isinstance`, dynamic
int/string comparisons, list/dict/set/bytes/bytearray method guards, generator
recognition, and the guarded-loop bytes check.  Exception-class numeric tags
and unrelated integer constants are explicitly outside this slice.

## Focused evidence

- `tests/python/test_port_abi_constants.py -k 'compiler_type_tag or frontend_type_tag'`
  — 3 passed.  This proves complete generated alias coverage, exact dispatch
  tables, and an AST fail-closed ratchet against copied numeric tag compares.
- `tests/python/test_fallback_baseline.py::test_contextual_frontend_type_tag_aliases_remain_native`
  — 1 passed in 19.70s.  All 13 affected compiler modules remain zero-fallback
  under strict contextual scaffold-on lowering.
- Dynamic list dispatch IR, type-guarded dynamic string equality IR, and the
  guarded-loop plan — 3 passed in 0.38s.
- Generator `--check`, targeted `py_compile`, and `git diff --check` passed.

## Remaining boundary

One combined self/no-libpython executable command completed its first three
lightweight nodes but returned no final pytest summary while the separate
HARNESS session was compiling.  It is not counted as green evidence.  Run the
dynamic builtin/isinstance/list/dict/set/bytes/bytearray executable cluster and
the final deliberate current-pcc1 strict no-libpython check after the shared
source queue stabilizes.
