# Investigation: Python mixed integer/float comparisons lose integer precision

## Status
active — issue193; current-source failure reproduced, exact comparison pending

## Problem Description
Both typed Python lowering and generic boxed numeric comparisons convert an
integer to binary64 before comparing it with a float. This rounds integers
beyond 2**53 and can overflow larger values. Python numeric comparisons must
compare the original mathematical values; C's usual arithmetic conversions
remain a different language contract and are outside this repair.

## Repro
`build/correctness-20260906-a/array-numeric-current-01/observation.json`
contains current-source host-pcc -> self/no-libpython/C-runtime evidence:
`9007199254740993 == 9007199254740992.0` is True and boxed greater-than is
False. CPython returns False and True respectively.

## Test [CONFIRMED]
The retained program exercises typed and boxed paths with a CPython control.
The focused extension must cover 2**53, int64 boundaries, fractions, signed
zero, infinities and supported bignums without substituting a float for an int.

## Proposals
- No.1 Compare finite binary64 values against exact integer magnitude [pending]

## No.1 Compare finite binary64 values against exact integer magnitude
### Code Change
Use the generic numeric runtime owner and integer representation/bit-length
contracts. Keep a float-only fast path, preserve explicit unordered NaN
results, and route unproven mixed Python comparisons to the exact owner.
The preceding finite boxed-float dispatch investigation remains historical:
`boxed-float-dyntype-sub-mul-compare-wrong.md` did not cover large-integer
precision or NaN semantics.

### Pending
Implement only after the NaN predicate/value boundary has focused execution
evidence. No array-specific rounding, representation workaround, or C
arithmetic-conversion change is permitted.

## Update — exact captured values and provenance (2026-09-07)

The retained current-source program uses an integer `9007199254740993`
(`2**53 + 1`) and an exactly representable float `9007199254740992.0`
(`float(2**53)`). No parsing or decimal-repr approximation is needed to explain
the comparison mismatch:

| Captured path | CPython | Native C-runtime result |
|---|---|---|
| boxed integer == boxed float | False | True |
| boxed integer > boxed float | True | False |
| typed integer == float literal | False | True |

The source, producing script and receipts are under
`build/correctness-20260906-a/array-numeric-current-01/`. Readback confirmed
process `COMPLETE`/return code 0, empty program stderr, C-runtime selection,
`sources_unchanged=true` at capture, and matching probe/executable hashes.
The executable SHA256 is
`5e976dc80554205c805d22d18ae86b30460e62cc73b09a905574ef4f9317536c`.
The observation contains the full captured source hashes; subsequent edits
must not be assessed through this pre-repair binary.

The earlier capability/helper receipts under `array-numeric-capability-01`
and `array-numeric-helper-02` explicitly use the older frozen
`source-2gdr4ie9/stage1/pcc1` and pcc-Python runtime. They are useful capability
data, not current-source execution or exact-comparison qualification.

## Update — semantic boundary and required controls

The [Python numeric comparison contract](https://docs.python.org/3.15/reference/expressions.html#value-comparisons)
requires mathematically correct mixed numeric comparisons without discarding
integer precision. This differs from explicitly converting an integer with
`float(i)`, where rounding is part of the requested conversion. Do not change
ordinary float conversion to repair a comparison.

At the captured boundary, typed comparison routes mixed operands through a
double conversion; boxed comparison converts integer payloads before comparing
to the float. The shared exact numeric owner must instead preserve the integer
value, report NaN as unordered, and handle signs, fractions, infinities and
integer magnitude without unchecked float-to-i64 overflow.

C remains a separate language contract. `c_codegen.py` performs C's usual
integer/floating conversions before its comparisons; a C mixed comparison may
therefore intentionally differ from Python near 2**53. Do not route C operators
through the proposed Python exact-comparison helper or change C integer width,
signedness or overflow semantics. The existing C NaN and unordered-backend
controls remain regression guards.

Before implementation verdict, require both operand orders and all six
comparison operators around 2**53, signed int64 boundaries, supported positive
and negative bignums, fractional doubles, signed zero, infinities and NaNs.
Maintain bool/int equality and numeric hash/container-key agreement. Separate
ordinary equal-value controls from the
[identity-aware NaN/container boundary](python-boxed-nan-value-comparison.md).

The finite boxed-float dispatch, closed-world float conversion, raw-integer
literal scaling and float-formatting histories were read end to end. In
particular, the recorded failure of digit-count-only formatting and repeated
floating scaling is not a reason to round displayed output or compare decimal
strings here. Those mechanisms have different owners and evidence.

No.1 remains pending. No exact-comparison fix, pcc-Python runtime result,
fresh pcc1 execution or bootstrap closure is claimed by this documentation
handoff.
