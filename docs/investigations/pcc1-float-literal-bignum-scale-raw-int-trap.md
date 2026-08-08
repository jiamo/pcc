# Investigation: pcc1 traps while scaling finite float literals through raw integers

## Status

active

## Problem Description

After the current compiler passed the typed aggregate regression, rebuilding the PCC-native Harness runtime stopped at `freestanding_libc_numeric.py` with `Trace/BPT trap: 5`. LLDB localized the trap to `_parse_float_literal_lift` while lifting a numeric expression. That parser constructs an exact decimal numerator or denominator with Python arbitrary-precision integers before converting it to binary64.

Compiler modules currently use the raw-int scaffold, so `_pow10i_lift(293)` is lowered as checked native integer multiplication rather than Python bignum multiplication. Finite source literals such as `8.98846567431158e307` and `2.004168360008973e-292` therefore trap inside the compiler even though both are valid binary64 values.

This is a new representation failure in the function previously investigated for ownership in [pcc1-self-host-parse-float-literal-uaf.md](pcc1-self-host-parse-float-literal-uaf.md). The earlier stale-reference symptom and this checked-integer overflow have different causes and tests.

## Repro

```bash
gtimeout 240s env -u LC_ALL \
  PCC_CURRENT_PCC1=projects/harness/build/pcc1 \
  uv run pytest -q -x -n0 \
  tests/python/test_native_float_literal_precision.py::test_current_pcc1_parses_large_and_tiny_finite_float_literals
```

Expected: exit code `0` and the two correctly rounded binary64 constants in emitted IR.

Observed 2026-08-14: the compiler subprocess exits from signal 5 with no diagnostic. LLDB stops at `user_pcc_parse_py_lift__parse_float_literal_lift + 724`; the caller chain is `Lifter__e_Num -> Lifter__e_BinOp -> Lifter__s_Assign`.

The realistic failure is:

```bash
gtimeout 1800s env -u LC_ALL projects/harness/build.sh
```

It reaches `freestanding_libc_numeric.py` after rebuilding earlier runtime modules, then `make` reports `Trace/BPT trap: 5`.

## Test [CONFIRMED]

`tests/python/test_native_float_literal_precision.py::test_current_pcc1_parses_large_and_tiny_finite_float_literals` failed deterministically with subprocess return code `-5` before the parser changed.

The CPython binary64 conversions are `0x7FE0000000000000` and `0x0360000000000000` for the two minimized literals. The test derives both bit patterns with `struct` rather than duplicating their spelling.

## Proposals

- No.1 Parse literal text through the runtime string-to-binary64 operation [pending]
- No.2 Reduce the exact-integer threshold and retain repeated float scaling [DENIED]

## No.1 Parse literal text through the runtime string-to-binary64 operation

### Code Change

Make both parser mirrors remove source underscores and call `float(text)` directly. CPython owns the host result; the no-libpython compiler lowers runtime string conversion through its native `py_float_value_of` implementation, whose platform conversion is correctly rounded for supported targets. Remove the now-unused repeated-power and exact-bignum helpers so the two parser mirrors cannot diverge back to raw integer scaling.

### pending

The host precision differential, rebuilt current-pcc1 minimized test, direct `freestanding_libc_numeric.py` compile, and native Harness build remain required.

## No.2 Reduce the exact-integer threshold and retain repeated float scaling

### Code Change

Use exact integer scaling only while the value fits i64 and fall back to repeated multiplication or division for larger exponents.

### DENIED

The old repeated-scaling path already produced measurable decimal-to-binary64 errors such as `1e100 -> 1.0000000000000006e+100`. Avoiding a compiler trap by restoring known incorrect literal semantics would violate the existing precision oracle.

## Update 2026-08-14: native conversion and emitted bits agree

After rebuilding `pcc1`, the minimized source compiled instead of trapping. The initial post-fix assertion incorrectly expected the tiny value's next binary64 neighbor. LLDB stopped at `py_float_value_of` and observed `0x0360000000000000` on return for `2.004168360008973e-292`; the emitted LLVM constant was identical. CPython conversion and an exact `Decimal` distance comparison both select that value over `0x0360000000000001`.

The regression now derives the expected bits from CPython's binary64 conversion and compares their padded LLVM spelling. This update corrects the test oracle; it does not weaken the requirement for exact binary64 agreement.
