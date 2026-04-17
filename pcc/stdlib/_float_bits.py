"""IEEE 754 bit-pattern helpers used by ``pcc/llvm_capi/ir.py``
and ``pcc/stdlib/struct.py``.

Kept separate from ``struct.py`` so callers (like ir.py) that only
need the int-form conversions don't pull in struct's ``bytes`` API
which uses ``int.to_bytes`` / ``bytes(iterable)`` builtins not yet
covered by native dispatch.

Pure float / int arithmetic — no string ops, no stdlib imports.
"""
from __future__ import annotations


def _float64_to_bits(f: float) -> int:
    """Return the IEEE 754 binary64 bit pattern of ``f`` as uint64."""
    if f != f:
        return 0x7FF8000000000000
    if f == float("inf"):
        return 0x7FF0000000000000
    if f == -float("inf"):
        return 0xFFF0000000000000
    if f == 0.0:
        return 0x8000000000000000 if str(f)[0] == "-" else 0
    sign = 0
    if f < 0.0:
        sign = 1
        f = -f
    exp = 0
    while f >= 2.0:
        f = f * 0.5
        exp += 1
    while f < 1.0:
        f = f * 2.0
        exp -= 1
    mantissa_bits = int((f - 1.0) * 4503599627370496.0)
    biased_exp = exp + 1023
    if biased_exp <= 0:
        return sign << 63
    if biased_exp >= 0x7FF:
        return (sign << 63) | 0x7FF0000000000000
    return (sign << 63) | (biased_exp << 52) | mantissa_bits


def _bits_to_float64(bits: int) -> float:
    sign = (bits >> 63) & 1
    biased_exp = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    if biased_exp == 0x7FF:
        if mantissa == 0:
            return float("-inf") if sign else float("inf")
        return float("nan")
    if biased_exp == 0:
        if mantissa == 0:
            return -0.0 if sign else 0.0
        f = mantissa / 4503599627370496.0
        for _ in range(11):
            f = f * 0.0009765625
        f = f * 2.0
        return -f if sign else f
    m_frac = 1.0 + mantissa / 4503599627370496.0
    exp = biased_exp - 1023
    f = m_frac
    if exp >= 0:
        for _ in range(exp):
            f = f * 2.0
    else:
        for _ in range(-exp):
            f = f * 0.5
    return -f if sign else f


def _round_to_float32(f: float) -> float:
    if f != f or f == float("inf") or f == -float("inf") or f == 0.0:
        return f
    bits = _float64_to_bits(f)
    sign = (bits >> 63) & 1
    biased_exp = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    f32_exp = biased_exp - 1023 + 127
    bits_to_drop = 52 - 23
    keep = mantissa >> bits_to_drop
    halfway = 1 << (bits_to_drop - 1)
    remainder = mantissa & ((1 << bits_to_drop) - 1)
    if remainder > halfway:
        keep += 1
    elif remainder == halfway and (keep & 1):
        keep += 1
    if keep >= (1 << 23):
        keep = 0
        f32_exp += 1
    if f32_exp >= 255:
        return -float("inf") if sign else float("inf")
    if f32_exp <= 0:
        return -0.0 if sign else 0.0
    new_biased_exp = f32_exp - 127 + 1023
    new_mantissa = keep << bits_to_drop
    new_bits = (sign << 63) | (new_biased_exp << 52) | new_mantissa
    return _bits_to_float64(new_bits)


def _round_to_float16(f: float) -> float:
    if f != f or f == float("inf") or f == -float("inf") or f == 0.0:
        return f
    bits = _float64_to_bits(f)
    sign = (bits >> 63) & 1
    biased_exp = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    f16_exp = biased_exp - 1023 + 15
    bits_to_drop = 52 - 10
    keep = mantissa >> bits_to_drop
    halfway = 1 << (bits_to_drop - 1)
    remainder = mantissa & ((1 << bits_to_drop) - 1)
    if remainder > halfway:
        keep += 1
    elif remainder == halfway and (keep & 1):
        keep += 1
    if keep >= (1 << 10):
        keep = 0
        f16_exp += 1
    if f16_exp >= 31:
        return -float("inf") if sign else float("inf")
    if f16_exp <= 0:
        return -0.0 if sign else 0.0
    new_biased_exp = f16_exp - 15 + 1023
    new_mantissa = keep << bits_to_drop
    new_bits = (sign << 63) | (new_biased_exp << 52) | new_mantissa
    return _bits_to_float64(new_bits)
