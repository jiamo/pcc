# C scalar and pointer ABI layout owner

Date: 2026-07-17

Task: `AUD-P1-C-ABI-LAYOUT-SOURCE-OF-TRUTH`

## Selected family

This finite slice selected fundamental scalar and pointer size/alignment facts
for pcc's supported LP64 execution targets:

- integer widths used by `_Bool`, char, short, int, long, and long long;
- float and double;
- `wchar_t`;
- data pointers.

Array recursion and struct/union member placement, padding, bitfield storage,
custom layouts, and target data-layout generalization remain outside this
slice.

## Shared contract

`pcc/c_abi_layout.py` now owns immutable `CAbiScalarLayout` facts through:

- `integer_scalar_layout`;
- `floating_scalar_layout`;
- `pointer_scalar_layout`;
- `builtin_scalar_layout` for C AST spellings.

Both static and instance C IR size/alignment helpers consume the IR-facing
facts. The SSA builder consumes the builtin spelling and pointer facts for
both `sizeof` and alignment recursion. Aggregate consumers keep their existing
recursive algorithms but no longer repeat leaf scalar/pointer constants.

The source guard mechanically checks every active leaf consumer. A fact-parity
test compares static C IR, instance C IR, and SSA results for char/short/int/
long/float/double and pointer. A native `cc` probe returns the combined
`sizeof` + `_Alignof` checksum for the same family; pcc and native both return
the LP64 checksum `86`.

## Gates

Focused owner and native-compiler parity:

```bash
gtimeout 60s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_c_scalar_layout_owner.py
```

Result: `3 passed in 0.34s`.

SSA `sizeof` neighbors:

```bash
gtimeout 90s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_ssa_builder.py tests/c/test_ssa_lowering.py -k sizeof
```

Result: `5 passed, 136 deselected in 1.17s`.

Required task gate:

```bash
gtimeout 180s env -u LC_ALL uv run pytest -q -n0 \
  tests/c/test_clang_compat.py tests/c/test_sizeof.py \
  tests/c/test_struct.py tests/c/test_bitfields.py
```

Result: `113 passed in 9.86s`. No GCC project suite, bootstrap, or GC matrix
was run or claimed.

## Claim boundary

This proves one LP64 scalar/pointer size-and-alignment source of truth shared
by C IR construction/type inference and SSA construction, with direct native
compiler parity. It does not prove a configurable cross-target ABI, LLP64 or
ILP32, long-double lowering, vector layout, aggregate/union/bitfield padding,
packed/aligned attributes, or LLVM target-data equivalence.
