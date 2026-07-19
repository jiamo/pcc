# Investigation: native re.compile pattern objects (32bfed70) vs 4 tests locking the old cpy-fallback boundary

## Status
needs-decision (root-caused 2026-06-18; not a clear-cut bug — a design conflict)

## Problem Description
Four `tests/python/test_native_re_match.py` cases fail:
`test_re_compile_local_alias_methods_stay_native_and_scoped`,
`test_re_compile_alias_value_use_keeps_fallback_boundary`,
`test_re_compile_class_attr_re_split_stores_pattern_string`,
`test_re_compile_class_attr_method_use_keeps_compile_fallback`.
They assert the OLD lowering for `re.compile(...)`: an alias-rewrite to
`@py_re_search_flags` for local method use, and a libpython cpy fallback
(`cpy.fn.compile` / `@.cpy.attr.compile`) when the compiled pattern is used as a
*value* (returned, stored, class attribute).

## Repro / evidence
The tests were last touched in `fe1de470`. `pcc/py_frontend/codegen/
native_text_modules.py` and `assignment_statement_lowering.py` were changed in
**`32bfed70`** (a later rework commit by concurrent work). `32bfed70` added a
conservative `_re_engine_subset_supported` checker and, in
`assignment_statement_lowering.py:416-425`, deliberately sets
`re_compile_alias = None` for engine-subset patterns so the variable holds a
**real `py_re_compile_obj` native pattern object** (normal assignment) instead
of the alias-rewrite. Both local and escaped patterns now lower to
`py_re_compile_obj` (verified via emitted IR for `f`/`g`) — the cpy fallback the
tests expect is gone.

**Runtime is correct.** Compiled with default and `--backend self
--python-libpython=off`, all of these match the `python3` oracle exactly:
- local: `re.compile(r"(?:[~#]|\.py[co]|\.o)$").search(t)` over foo.pyc/bar.o/baz~/qux.py
- module-level: `word = re.compile(r"b+", re.I); word.search(t)`
- escaped: `make_pat()` returns the pattern, caller does `p.search(t)`

So `32bfed70`'s native-pattern-object strategy is an objective improvement for
the tested scenarios (native, no libpython dependency).

## The conflict (why this is needs-decision, not a fix)
The four tests are *named* `..._keeps_fallback_boundary` /
`..._keeps_compile_fallback` and assert the cpy fallback for *value-use* of a
compiled pattern. That fallback was a deliberate compatibility boundary in the
old design (a real CPython `re.Pattern` for patterns that escape as opaque
values, where the native object might not cover the full `re.Pattern` API —
`.groups`, `.pattern`, being passed to other `re.*` functions, etc.).
`32bfed70` removed it. I verified `.search`/`.match`/`.split` on escaped
patterns work natively, but did **not** establish that `py_re_compile_obj`
covers the *entire* `re.Pattern` surface a value-use site might need. So the
choice is genuinely:

- **Accept the native strategy** (it's faster + no-libpython, runtime-correct
  for normal use): update the 4 tests to lock `py_re_compile_obj` (native, no
  `cpy.fn.compile`) — and verify the native pattern object's API completeness.
- **The fallback was intentional safety**: then `32bfed70` dropped it
  prematurely and the native object needs the missing `re.Pattern` surface
  before the tests can be retired.

I did NOT rewrite the four intent-bearing tests to bless `32bfed70` unilaterally
(that would either lock in a possibly-incomplete design or silently delete a
deliberate safety lock). This needs an owner's call on the re.compile design
direction. Related precedent: `python-self-backend-asm-assertions-stale.md`
(tests asserting idioms codegen moved past) — but unlike pure asm-idiom drift,
these tests encode a *behavioral* fallback boundary, not just an idiom.

## Report
Not fixed — filed as a design decision. No code changed. Recommendation: accept
the native strategy and update the tests **after** confirming `py_re_compile_obj`
covers the `re.Pattern` operations value-use sites rely on (otherwise restore the
fallback for value-escape). Sibling cpython-interop crash
`test_lambda_returning_cpython_object_stays_tagged` (pcc-self-import via
libpython, rc=139) is separately deep and deferred.
