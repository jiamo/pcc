from __future__ import annotations

"""Single proven inventory for directly encoded AArch64 FP immediates."""


DIRECT_FP_IMMEDIATE_ENCODINGS = (
    ("1.0", 0x70),
    ("2.0", 0x00),
)


def direct_fp_immediate_literal(value: float) -> str | None:
    for literal, _encoding in DIRECT_FP_IMMEDIATE_ENCODINGS:
        if value == float(literal):
            return literal
    return None


def direct_fp_immediate_encoding(literal: str) -> int | None:
    for proven_literal, encoding in DIRECT_FP_IMMEDIATE_ENCODINGS:
        if literal == proven_literal:
            return encoding
    return None
