# Investigation: boxed NaN operators inherit container identity semantics

## Status
active — issue193; current-source failure reproduced, focused repair pending

## Problem Description
Direct boxed numeric operators use `py_obj_eq`, whose identity shortcut is
also required by container membership/equality and RichCompareBool. This
makes a boxed NaN equal to itself. Numeric three-way comparison also returns
equal for unordered NaN pairs, making <= and >= true and allowing later
list/tuple elements to decide an ordering that should be unordered.

## Repro
`build/correctness-20260906-a/array-numeric-current-01/observation.json`
separately reproduced boxed self-NaN equality on current host-pcc ->
self/no-libpython/C-runtime source. It is not inferred from the earlier
receipt-bound pcc1 capability program.

## Test [CONFIRMED]
The capability program prints True for boxed self-NaN equality; CPython
prints False. `tests/python/test_python_numeric_comparison_contract.py`
adds direct equality/order and identity-aware list/tuple/set/dict controls.

## Proposals
- No.1 Separate direct-value equality from identity-or-equality [pending]
- No.2 Propagate an explicit unordered numeric result [pending]

## No.1 Separate direct-value equality from identity-or-equality
### Code Change
Keep container identity policy explicit in the existing comparison owner;
direct Python operators and direct C-API RichCompare need value semantics.
RichCompareBool and container callbacks retain identity-or-equality. Do not
remove the shared shortcut from every container caller indiscriminately.

### Pending
Focused runtime and frontend implementation, followed by real execution.

## No.2 Propagate an explicit unordered numeric result
### Code Change
Audit all three-way consumers: direct relations, recursive list/tuple
comparison, merge sorting and its insertion-sort fallback. Unordered must
not become equal or greater. Identical container elements retain their
identity shortcut before recursive value ordering.

### Pending
Focused implementation and relation/container/sorting controls.

## Update — observed boundary versus comparison-policy inventory (2026-09-07)

The current baseline is the retained
`array-numeric-current-01/{probe.py,run.py,observation.json,process.result.json}`.
Readback verified process `COMPLETE`/return code 0, C-runtime selection
(`PCC_RUNTIME_CC=cc`, `PCC_RUNTIME_HIGH=c`), the unchanged-source capture flag,
and executable SHA256
`5e976dc80554205c805d22d18ae86b30460e62cc73b09a905574ef4f9317536c`.
Its line `nan-self False False True` means typed equality is correctly False,
typed inequality is incorrectly False, and boxed self-equality is incorrectly
True. CPython prints `nan-self False True False` for the same source.

Only boxed self-equality is the measured failure belonging to this document.
The initial notes about unordered ordering, later sequence elements and sorting
are code-review targets, not additional results from this baseline program.
The baseline does not call either RichCompare API or exercise container NaNs.
Those boundaries need their own focused execution controls before a verdict.

The old `array-numeric-capability-01` receipt uses the older frozen
`source-2gdr4ie9/stage1/pcc1` plus its pcc-Python runtime bundle. It must not be
merged with this current-source C-runtime observation or used as evidence that
a new equality implementation has executed under current pcc1.

## Update — direct comparison and identity-aware contracts

The [Python 3.15 C-API contract](https://docs.python.org/3.15/c-api/object.html#c.PyObject_RichCompareBool)
distinguishes the APIs: RichCompare returns the ordinary comparison result as
a new reference; RichCompareBool returns an integer truth/error result and
requires identical operands to yield 1 for EQ and 0 for NE. The
[language comparison contract](https://docs.python.org/3.15/reference/expressions.html#value-comparisons)
also preserves identity handling in builtin containers, even for nonreflexive
values such as NaN.

Required controls for one stored NaN `n` are therefore:

| Boundary | Expected result |
|---|---|
| direct `n == n`, `n != n` | False, True |
| RichCompare(n, n, EQ/NE) | False/True result objects |
| RichCompareBool(n, n, EQ/NE) | 1 / 0 |
| `n in [n]`, `n in (n,)`, `n in {n}` | True |
| `[n] == [n]`, `(n,) == (n,)`, `{'v': n} == {'v': n}` | True |
| list/tuple count or index of the same `n` | find the retained element |

At the baseline, `py_obj_eq` in both `py_obj_ops_compare.c` and its Python
mirror returns equality before inspecting numeric payloads when pointers are
identical. List membership/count/index, tuple methods, recursive list/dict
equality and container-key lookup depend on identity-aware equality. Their GC
root reloads, callback error propagation and commit checks must remain intact.
The C-API Python owner and C oracle currently make RichCompare call
RichCompareBool, conflating the two contracts. The existing extension-loader
`tuple_dict_attr_helpers` smoke compares equal strings and cannot establish the
same-NaN distinction or arbitrary rich-comparison result behavior.

The histories
[custom key equality](custom-obj-eq-dict-set-key-no-libpython.md) and
[instance equality dispatch](py-instance-eq-ignored-in-container-keys.md) were
read end to end. The former records standalone success followed by a failed
bootstrap, and a recursion-guard attempt that did not help; the later resolved
history records the actual instance-tag dispatch repair. Neither permits
removing container identity handling or exempting compiler classes from Python
semantics. Preserve these histories rather than treating their dated status
text as the current implementation inventory.

Both proposals remain pending. A direct-value owner must have focused numeric,
C-API and container controls, matched C/pcc-Python behavior and fresh native
qualification before this investigation can close. The
[typed predicate's local green step](python-nan-ordered-predicates.md) does not
close this mechanism.
