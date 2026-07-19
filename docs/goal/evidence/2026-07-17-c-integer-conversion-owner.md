# C integer usual-conversion decision owner

Date: 2026-07-17

Task: `AUD-P1-C-SIGNEDNESS-SOURCE-OF-TRUTH`

## Selected family

This slice selected the integer usual-arithmetic-conversion decision after
integer promotion: which operand's rank/width supplies the common type, and
whether that common integer is signed or unsigned. It did not change floating
conversion, shifts, pointer arithmetic, ABI layout, libc signatures, or the
storage mechanism for declaration/binding metadata.

The same decision had independent implementations in four active consumers:

- runtime integer expression lowering;
- `sizeof`/expression IR-type inference;
- C11 `_Generic` expression type-key inference;
- enum and initializer constant-expression evaluation.

## Shared decision record

`IntegerConversionDecision` records the winning normalized rank/width,
signedness, and source operand. `_decide_usual_integer_conversion` is the one
pure decision owner. Semantic type-key callers pass C rank; lowered IR and
constant callers pass normalized bit width. Each consumer retains its own
projection mechanics while consuming the same winner.

The focused source guard requires all four consumers to call that owner. Its
parity program covers:

- same-rank signed/unsigned, where unsigned wins;
- a wider signed type that can represent the narrower unsigned type;
- unsigned wrap in constant and runtime arithmetic;
- `sizeof` result width for both combinations;
- `_Generic` selection for both combinations.

## Stacked regression found by the cross-path test

The first parity program exposed an existing second failure: after comparing a
signed `long` with an `unsigned long`, a later comparison of the same SSA value
with `unsigned int` incorrectly treated the signed value as unsigned. Reversing
the comparison order or explicitly recasting the second use made the failure
disappear.

Root cause: `_convert_int_value` returned the original LLVM value for a
same-width signedness conversion, then mutated that value's `_is_unsigned`
metadata. The conversion result and the declared-source value were therefore
the same mutable metadata carrier.

The fix creates an identity-preserving `or value, 0` projection only when the
LLVM width is unchanged but semantic signedness changes. The projection gets
the target metadata; the original SSA value retains its declared signedness.
No arithmetic flags or overflow assumptions are added.

## Gates

Focused owner and cross-path parity:

```bash
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_c_integer_conversion_owner.py
```

Result: `3 passed in 0.37s`.

Unsigned propagation and same-width cast neighbors:

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_unsigned_loads.py tests/c/test_param_array_decay.py \
  -k 'unsigned or signed_cast'
```

Result: `17 passed, 1 deselected in 1.70s`. Before the final `_Generic`
extension, the five directly selected owner/history checks also passed in
`0.65s`.

Required task gate:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_float_semantics.py tests/c/test_clang_compat.py
```

Result: `95 passed in 8.81s`. No GCC project suite, bootstrap, or GC matrix was
run or claimed.

## Claim boundary

This proves one shared rank/width/signedness decision for promoted integer
usual conversions across runtime, `sizeof`, `_Generic`, and constant
evaluation, plus isolation of same-width conversion metadata. It does not
replace the broader ad hoc value/binding metadata model, prove every C integer
rank on every target ABI, or consolidate shifts, casts, pointer conversions,
bitfields, SSA type construction, floating conversions, or ABI layout.
