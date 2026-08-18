# aarch64 frint encoding — 2026-08-25

## Claim

`pcc/backend/arm64_encode.py` now encodes `frintn`, `frintp`, `frintm` and
`frintz` for double precision, so `math.floor`, `math.ceil`, `math.trunc` and
`round` link and run correctly on the self backend.

Closes `BACKEND-P1-AARCH64-FRINT-NOT-ENCODABLE`.  Not Stage1, Stage2,
fixed-point, five-GC, or performance evidence.

## What was wrong

`pcc/backend/self_backend_aarch64_darwin_calls.py` emits all four `frint*`
mnemonics, while `arm64_encode.py` implemented `fabs fadd fcmp fcsel fcvt
fcvtzs fcvtzu fdiv fmov fmul fneg fsqrt fsub` and no `frint` at all.  Codegen
was emitting instructions its own assembler could not encode:

```text
pcc.backend.arm64_encode.EncodeError:
  mnemonic 'frintm' not in the proven subset: 'frintm d11, d9'
```

That is the `tests/py_corpus/phase4/math_floor` corpus failure.

## Derivation, checked against an oracle

The existing single-source float encoders pack `opc = (opcode << 15) | 0x4000`,
where `0x4000` is the fixed `10000` field at bits 14-10 — recovered by decoding
the `fabs`/`fneg`/`fsqrt` constants already in the table rather than assumed.
The `frint*` opcodes are `001000` (N, nearest-even), `001001` (P, +inf),
`001010` (M, -inf), `001011` (Z, toward zero), giving `0x44000`, `0x4C000`,
`0x54000`, `0x5C000`.

Ground truth from `as(1)`:

```text
frintm d11, d9   1e65412b
frintn d0, d1    1e644020
frintp d31, d0   1e64c01f
frintz d5, d17   1e65c225
```

All four match the derived encodings.

## Gates

The four shapes were added to the existing three-way differential corpus in
`tests/python/test_arm64_encode.py`, whose docstring states that the corpus *is*
the proven subset — every shape is assembled by `as(1)`, by the LLVM MC printer
in the pinned llvmlite wheel, and by pcc's encoder, and the words must match.

RED before: `EncodeError: mnemonic 'frintm' not in the proven subset`.
GREEN after: `11 passed in 1.15s`.

End-to-end against CPython on the self backend:

```text
math.floor(3.7)   3    math.ceil(3.2)   4    math.trunc(3.9)   3
math.floor(-2.3) -3    math.ceil(-2.7) -2    math.trunc(-3.9) -3
round(2.5)        2    round(-2.5)     -2    round(3.7)        4
```

Identical to CPython for all nine.  Full corpus after the fix:
`177 passed in 368.71s`.  The earlier run reported `150 passed, 1 failed`
because `-x` stopped at `math_floor`; 26 cases after it had never executed, so
the pre-fix number was a floor and not a total.

The `round(2.5) == 2` and
`round(-2.5) == -2` cases matter: banker's rounding is what `frintn`
(round-to-nearest-even) produces, so the opcode choice is validated by
behaviour and not merely by the link succeeding.

## Audit for the same class

The task asked for any other mnemonic the aarch64 lowering emits but the
encoder lacks.  One more family exists, and it is larger:

```text
self_backend_aarch64_darwin_calls.py:90-92   ldr q0, [x10...] / str q0, [x9...]
self_backend_aarch64_darwin_calls.py:103     movi v0.16b, #0
```

`arm64_encode.py` has no `movi` and no q-register operand support at all, so
the entire SIMD block copy/zero path is unencodable.  It is reached from two
live call sites (`_emit_aligned_simd_block_copy` at line 540 and
`_emit_aligned_simd_block_zero` at line 598), so a program that triggers an
aligned block copy or zero would fail to link exactly the way `math.floor` did.

Filed as `BACKEND-P1-AARCH64-SIMD-BLOCK-NOT-ENCODABLE`.

## Nonclaims

- Only double precision is encoded, matching the existing `fneg`/`fabs`/`fsqrt`
  restriction; a single-precision `frint` still raises `EncodeError`.
- `frinta`, `frintx` and `frinti` are not encoded because the lowering does not
  emit them.
- The SIMD block gap was established from source and from the absence of any
  q-register support in the encoder.  **No triggering program was constructed**,
  so its reachability in practice is argued, not demonstrated.
- No bootstrap, stage, fixed-point or five-GC gate was run.
